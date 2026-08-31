"""Claude Code hook adapter that records sessions to local PostgreSQL."""

from __future__ import annotations

import getpass
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from wikibricks.adapters import claude_code_buffer
from wikibricks.adapters.claude_code import state_to_session


def _read_payload() -> dict[str, Any]:
    try:
        value = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_id() -> str:
    configured = os.environ.get("WIKIBRICKS_USER_ID") or os.environ.get(
        "WIKIBRICKS_RECORDER_USER_ID"
    )
    if configured:
        return configured
    try:
        email = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        email = ""
    return (email or getpass.getuser()).replace("@", "-at-")


def should_skip(state: dict[str, Any]) -> bool:
    cwd = str(state.get("cwd") or "").rstrip("/")
    if cwd in {"/tmp", "/private/tmp", "/var/tmp"}:
        return True
    if cwd.startswith(
        (
            "/tmp/",
            "/private/tmp/",
            "/private/var/folders/",
            "/var/tmp/",
            "/var/folders/",
        )
    ):
        return True
    try:
        minimum = int(os.environ.get("WIKIBRICKS_RECORDER_MIN_EVENTS", "2"))
    except ValueError:
        minimum = 2
    events = state.get("events") or []
    if not events or len(events) < minimum:
        return True
    prompts = [event for event in events if event.get("kind") == "prompt"]
    first = str(state.get("first_prompt") or "").strip().lower()
    utility_prefixes = ("you are ", "summarize ", "read the transcript", "extract ")
    return len(prompts) <= 1 and first.startswith(utility_prefixes)


def on_session_start() -> None:
    payload = _read_payload()
    session_id = payload.get("session_id")
    if not session_id:
        return
    state = claude_code_buffer.load(session_id)
    state["started_at"] = state.get("started_at") or _now_iso()
    state["cwd"] = state.get("cwd") or payload.get("cwd") or os.getcwd()
    state["model"] = state.get("model") or payload.get("model")
    claude_code_buffer.save(state)


def on_user_prompt_submit() -> None:
    payload = _read_payload()
    session_id = payload.get("session_id")
    if not session_id:
        return
    prompt = str(payload.get("prompt") or "")
    state = claude_code_buffer.load(session_id)
    state["first_prompt"] = state.get("first_prompt") or prompt
    state["events"].append({"kind": "prompt", "prompt": prompt, "ts": _now_iso()})
    claude_code_buffer.save(state)


def on_post_tool_use() -> None:
    payload = _read_payload()
    session_id = payload.get("session_id")
    if not session_id:
        return
    state = claude_code_buffer.load(session_id)
    tool_name = str(payload.get("tool_name") or "?")
    state["events"].append(
        {
            "kind": "tool",
            "tool_name": tool_name,
            "tool_input": payload.get("tool_input"),
            "ts": _now_iso(),
        }
    )
    response = payload.get("tool_response")
    if response is not None:
        content = (
            response
            if isinstance(response, str)
            else json.dumps(response, default=str)
        )
        state["events"].append(
            {
                "kind": "tool_result",
                "tool_name": tool_name,
                "output": content,
                "ts": _now_iso(),
            }
        )
    claude_code_buffer.save(state)


def _flush(state: dict[str, Any]) -> None:
    if should_skip(state):
        return
    from wikibricks import WikiClient

    WikiClient().ingest_session(state_to_session(state, user_id=_user_id()))


def on_stop() -> None:
    payload = _read_payload()
    session_id = payload.get("session_id")
    if session_id:
        _flush(claude_code_buffer.load(session_id))


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
    try:
        handler = handlers.get(event_name)
        if handler:
            handler()
    except Exception as exc:
        print(f"wikibricks[{event_name}]: {exc}", file=sys.stderr)


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    sys.stdin = io.StringIO(raw)
    dispatch(str(payload.get("hook_event_name") or ""))


if __name__ == "__main__":
    main()
