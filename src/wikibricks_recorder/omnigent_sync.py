"""Pull Omnigent's own session store into WikiBricks — no Omnigent changes.

The Claude Code recorder (`hooks.py`) writes a wiki page per Claude Code
session via SessionStart/Stop hooks. Omnigent has no such hook surface and its
scheduled-task MCP endpoint is not served by every daemon build, so this module
takes the harness-agnostic route instead: read Omnigent's *own* local
conversation store (`~/.omnigent/chat.db`) read-only and write each real
conversation as a wiki page through the existing `WikiClient`.

This file is PURE (no SQLite, no WorkspaceClient). The CLI wrapper in
`scripts/omnigent_sync_cli.py` owns all IO and calls `conversation_page()` per
conversation. Everything here is unit-testable from plain dicts.

A conversation dict (produced by the CLI from chat.db rows) looks like::

    {
      "conversation_id": "<hex>",       # conversations.id, hex-encoded
      "title": "<first user prompt>",   # conversations.title
      "created_at": <unix seconds>,
      "updated_at": <unix seconds>,
      "agent_name": "claude-native-ui", # bound agent (via agents table)
      "workspace": "/abs/path" | None,  # session workspace, if any
      "archived": False,
      "items": [(type:int, data:dict), ...],  # conversation_items, ordered
    }

`items` types are Omnigent's `conversation_items.type` SMALLINT, verified
empirically against a live store: 1=message (role user/assistant), 2=tool
call, 3=tool output, 5=error, 8=lifecycle event, 10=command, 11=raw input.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from wikibricks_recorder import page_builder

# Distinct from the Claude Code recorder's ``sessions/`` prefix so Omnigent
# pages never collide with recorder pages or the purge/backfill tooling that
# scans ``sessions/``.
OMNIGENT_PATH_PREFIX = "omnigent-sessions/"

# conversation_items.type values (see module docstring).
_TYPE_MESSAGE = 1
_TYPE_TOOL_CALL = 2
_TYPE_TOOL_OUTPUT = 3
_TYPE_ERROR = 5

# Titles Omnigent auto-assigns to spawned sub-agent / native-UI child
# conversations — these are orchestration plumbing, not real work.
_SUBAGENT_TITLE_PREFIXES = ("general-purpose:", "polly:", "debby:")
_SUBAGENT_TITLE_SUFFIXES = ("-native-ui",)


def _ts_iso(unix_seconds: int | float | None) -> str | None:
    """Omnigent stores Unix *seconds*; render ISO-8601 UTC for the body."""
    if not unix_seconds:
        return None
    return datetime.fromtimestamp(int(unix_seconds), tz=timezone.utc).isoformat()


def _message_text(data: dict[str, Any]) -> str:
    """Flatten an Omnigent message item's content parts into plain text."""
    content = data.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                txt = part.get("text") or part.get("output_text") or ""
                if txt:
                    parts.append(str(txt))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts).strip()
    return ""


def _decode_item(item_type: int, data: dict[str, Any]) -> dict[str, Any] | None:
    """Map one (type, data) row to a recorder-style event, or None to skip.

    Event shapes mirror what ``page_builder.session_content`` renders:
    ``{"kind": "prompt", "prompt": ...}``, ``{"kind": "tool", "tool_name",
    "summary"}``, ``{"kind": "response", ...}``, ``{"kind": "error", ...}``.
    Unknown types return None so a new Omnigent item kind never crashes sync.
    """
    if not isinstance(data, dict):
        return None
    if item_type == _TYPE_MESSAGE:
        role = data.get("role")
        text = _message_text(data)
        if not text:
            return None
        if role == "user":
            return {"kind": "prompt", "prompt": text}
        if role == "assistant":
            return {"kind": "response", "summary": text[:500]}
        return None
    if item_type == _TYPE_TOOL_CALL:
        return {"kind": "tool", "tool_name": data.get("name", "?")}
    if item_type == _TYPE_TOOL_OUTPUT:
        return None  # output is implied by its call; keep the timeline compact
    if item_type == _TYPE_ERROR:
        msg = data.get("message") or data.get("code") or "error"
        return {"kind": "error", "summary": str(msg)[:200]}
    return None


def build_state(conversation: dict[str, Any]) -> dict[str, Any]:
    """Convert an Omnigent conversation dict into the recorder `state` shape.

    The returned dict is exactly what ``page_builder`` functions consume, so
    title / content / ephemeral logic is shared with the Claude Code recorder
    rather than reimplemented.
    """
    events: list[dict[str, Any]] = []
    first_prompt: str | None = None
    for item_type, data in conversation.get("items", []):
        ev = _decode_item(item_type, data)
        if ev is None:
            continue
        events.append(ev)
        if first_prompt is None and ev["kind"] == "prompt":
            first_prompt = ev["prompt"]

    return {
        "session_id": conversation["conversation_id"],
        "events": events,
        "started_at": _ts_iso(conversation.get("created_at")),
        "cwd": conversation.get("workspace"),
        "first_prompt": first_prompt,
        "model": conversation.get("agent_name"),
    }


def is_syncable(conversation: dict[str, Any]) -> bool:
    """True when a conversation is real work worth a wiki page.

    Filters out: archived conversations, Omnigent sub-agent/native-UI child
    conversations (title plumbing), conversations with no user prompt, and
    single-prompt boilerplate/utility runs (same rule as the recorder's
    ``_is_utility_session`` + ``is_ephemeral``).
    """
    if conversation.get("archived"):
        return False
    title = (conversation.get("title") or "").strip()
    if title.startswith(_SUBAGENT_TITLE_PREFIXES) or title.endswith(_SUBAGENT_TITLE_SUFFIXES):
        return False

    state = build_state(conversation)
    prompts = [e for e in state["events"] if e["kind"] == "prompt"]
    if not prompts:
        return False
    # One-shot utility/summarizer invocations: single prompt that is itself
    # system-prompt boilerplate.
    if len(prompts) <= 1 and page_builder._is_boilerplate(state.get("first_prompt") or ""):
        return False
    return True


def conversation_page(conversation: dict[str, Any], *, user_id: str) -> dict[str, Any]:
    """Build the `WikiClient.write_page` payload for one Omnigent conversation.

    Returns ``{path, title, content, tags, content_text_override}``. Path is
    ``omnigent-sessions/<user>/YYYY/MM/DD/<conversation_id>`` — the conversation
    id as the leaf makes re-sync idempotent (MERGE-by-path overwrites).
    """
    state = build_state(conversation)
    inner = page_builder.session_path(
        user_id, state["session_id"], state.get("started_at")
    ).removeprefix("sessions/")
    path = OMNIGENT_PATH_PREFIX + inner

    title = page_builder.session_title(state)
    content = page_builder.session_content(state)

    tags = ["session", "harness:omnigent", f"user:{user_id}"]
    agent = conversation.get("agent_name")
    if agent:
        tags.append(f"agent:{agent}")

    # content_text feeds the VS index — keep it the (keyword-rich) first prompt,
    # not the full transcript, mirroring the recorder's dense-summary intent.
    override = (state.get("first_prompt") or title).strip()

    return {
        "path": path,
        "title": title,
        "content": content,
        "tags": tags,
        "content_text_override": override,
    }
