"""Claude Code hook entry points → wikibricks_recorder state + WikiBricks writes.

Five hook events from Claude Code (each fires with a JSON payload on stdin):
- SessionStart: stamp started_at + cwd
- UserPromptSubmit: append prompt event, capture first_prompt
- PostToolUse: append tool event
- Stop / SessionEnd: synchronously flush the session as one WikiBricks page

Hooks must never crash the host — all exceptions are swallowed and logged to
stderr. Recorder dir is configurable via `WIKIBRICKS_RECORDER_DIR`.

Workspace target (catalog/schema/warehouse/profile) and user attribution
are resolved at flush time via `config.load_config()` — env var, then
~/.wikibricks-recorder.toml, then raise. No hardcoded defaults — the same
code serves a personal wiki (one user_id) or a team wiki (many user_ids
sharing one schema).
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

from wikibricks_recorder import config, page_builder, session


def _read_payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_error(where: str, exc: BaseException) -> None:
    print(f"wikibricks_recorder[{where}]: {exc}", file=sys.stderr)


def on_session_start() -> None:
    try:
        payload = _read_payload()
        sid = payload.get("session_id")
        if not sid:
            return
        state = session.load(sid)
        if state.get("started_at") is None:
            state["started_at"] = _now_iso()
        if state.get("cwd") is None:
            state["cwd"] = payload.get("cwd") or os.getcwd()
        if state.get("model") is None and payload.get("model"):
            state["model"] = payload["model"]
        session.save(state)
    except Exception as e:
        _log_error("on_session_start", e)


def on_user_prompt_submit() -> None:
    try:
        payload = _read_payload()
        sid = payload.get("session_id")
        if not sid:
            return
        prompt = payload.get("prompt", "") or ""
        state = session.load(sid)
        if state.get("first_prompt") is None and prompt:
            state["first_prompt"] = prompt
        state["events"].append({"kind": "prompt", "ts": _now_iso(), "prompt": prompt})
        session.save(state)
        _emit_relevant_context(sid, prompt)
    except Exception as e:
        _log_error("on_user_prompt_submit", e)


_INJECT_MIN_PROMPT_LEN = 10
_INJECT_MAX_HITS = 3
_INJECT_SNIPPET_LEN = 200


def _emit_relevant_context(session_id: str, prompt: str) -> None:
    """If WIKIBRICKS_INJECT_CONTEXT=1 and the prompt is substantive, search the
    wiki and emit a UserPromptSubmit additionalContext JSON response on stdout.
    All exceptions swallowed — must never break the user's session.
    """
    if os.environ.get("WIKIBRICKS_INJECT_CONTEXT") != "1":
        return
    if len(prompt.strip()) < _INJECT_MIN_PROMPT_LEN:
        return
    try:
        cfg = config.load_config()
        client = _build_wiki_client(cfg)
        hits = client.search(prompt, mode="HYBRID", num_results=_INJECT_MAX_HITS + 2)
        relevant = [h for h in (hits or []) if session_id not in (h.get("path") or "")]
        if not relevant:
            return
        lines = ["Wikibricks — relevant prior pages:"]
        for h in relevant[:_INJECT_MAX_HITS]:
            title = (h.get("title") or "").strip()[:80]
            path = h.get("path") or ""
            snippet = (h.get("content_text") or "").replace("\n", " ").strip()[:_INJECT_SNIPPET_LEN]
            lines.append(f"- [{path}] {title}: {snippet}")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "\n".join(lines),
            }
        }))
        # User-visible summary on stderr so Claude Code surfaces it.
        used = relevant[:_INJECT_MAX_HITS]
        print(f"wikibricks: injected {len(used)} pages", file=sys.stderr)
        for h in used:
            print(f"  - {h.get('path', '')}", file=sys.stderr)
    except Exception:
        # Never break the user's session because of a failed wiki call.
        pass


def on_post_tool_use() -> None:
    try:
        payload = _read_payload()
        sid = payload.get("session_id")
        if not sid:
            return
        session.append_event(sid, {
            "kind": "tool",
            "ts": _now_iso(),
            "tool_name": payload.get("tool_name", "?"),
        })
    except Exception as e:
        _log_error("on_post_tool_use", e)


def _is_utility_session(state: dict[str, Any]) -> bool:
    """True for skill / sub-agent sessions that should not be recorded."""
    cwd = state.get("cwd") or ""
    if cwd in ("/tmp", "/private/tmp") or cwd.startswith(
        ("/private/var/folders/", "/var/folders/", "/tmp/", "/private/tmp/")
    ):
        return True
    prompts = [e for e in state.get("events", []) if e.get("kind") == "prompt"]
    first = (state.get("first_prompt") or "").strip()
    return len(prompts) <= 1 and page_builder._looks_like_system_prompt(first)


def _build_wiki_client(cfg: dict[str, str]):
    """Construct a WikiClient from resolved config.

    Set env vars BEFORE importing wikibricks so its module-level CATALOG/SCHEMA
    constants resolve to this recorder's target schema.
    """
    os.environ.setdefault("WIKIBRICKS_CATALOG", cfg["catalog"])
    os.environ.setdefault("WIKIBRICKS_SCHEMA", cfg["schema"])
    from databricks.sdk import WorkspaceClient

    from wikibricks.client import WikiClient
    ws = WorkspaceClient(profile=cfg["profile"])
    return WikiClient(warehouse_id=cfg["warehouse_id"], workspace_client=ws)


def _flush(state: dict[str, Any]) -> None:
    if not state.get("events"):
        return
    if _is_utility_session(state):
        return
    cfg = config.load_config()
    client = _build_wiki_client(cfg)
    path = page_builder.session_path(
        cfg["user_id"], state["session_id"], state.get("started_at")
    )
    tags = page_builder.session_tags(state, topic_keywords=config.load_topic_keywords())
    tags.append(f"user:{cfg['user_id']}")
    client.write_page(
        path,
        title=page_builder.session_title(state),
        content_json=page_builder.session_content(state),
        tags=tags,
    )


def on_stop() -> None:
    try:
        payload = _read_payload()
        sid = payload.get("session_id")
        if not sid:
            return
        state = session.load(sid)
        _flush(state)
    except Exception as e:
        _log_error("on_stop", e)


def on_session_end() -> None:
    on_stop()


def dispatch(event_name: str) -> None:
    handlers = {
        "SessionStart": on_session_start,
        "UserPromptSubmit": on_user_prompt_submit,
        "PostToolUse": on_post_tool_use,
        "Stop": on_stop,
        "SessionEnd": on_session_end,
    }
    handler = handlers.get(event_name)
    if handler is None:
        return
    try:
        handler()
    except Exception as e:
        _log_error(f"dispatch({event_name})", e)


def main() -> None:
    """CLI entry: read stdin once, route by `hook_event_name`, restore stdin."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    event_name = payload.get("hook_event_name", "")
    sys.stdin = io.StringIO(raw)
    dispatch(event_name)


if __name__ == "__main__":
    main()
