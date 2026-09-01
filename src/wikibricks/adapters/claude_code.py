"""Translate Claude Code recorder state into the shared session contract."""

from __future__ import annotations

import json
from typing import Any

from wikibricks.models import SessionEvent, SessionRecord

_KIND_MAP = {
    "prompt": "user",
    "response": "assistant",
    "tool": "tool_call",
    "tool_result": "tool_result",
    "error": "error",
    "lifecycle": "lifecycle",
}


def _event_content(event: dict[str, Any], normalized_kind: str) -> str:
    for key in ("prompt", "content", "summary", "output", "message"):
        value = event.get(key)
        if isinstance(value, str):
            return value
    if normalized_kind == "tool_call" and event.get("tool_input") is not None:
        return json.dumps(event["tool_input"], ensure_ascii=False, sort_keys=True)
    return ""


def state_to_session(state: dict[str, Any], *, user_id: str) -> SessionRecord:
    events: list[SessionEvent] = []
    for position, event in enumerate(state.get("events") or []):
        source_kind = str(event.get("kind") or "")
        normalized_kind = _KIND_MAP.get(source_kind)
        if normalized_kind is None:
            continue
        metadata = {
            key: value
            for key, value in event.items()
            if key not in {"kind", "prompt", "content", "summary", "output", "message", "ts"}
        }
        if event.get("tool_name"):
            metadata["tool_name"] = event["tool_name"]
        events.append(
            SessionEvent(
                external_id=str(event.get("event_id") or position),
                kind=normalized_kind,
                content=_event_content(event, normalized_kind),
                created_at=event.get("ts"),
                metadata=metadata,
            )
        )
    return SessionRecord(
        harness="claude-code",
        external_id=str(state.get("session_id") or ""),
        user_id=user_id,
        agent=state.get("model"),
        workspace=state.get("cwd"),
        started_at=state.get("started_at"),
        updated_at=state.get("updated_at"),
        events=events,
        metadata={"first_prompt": state.get("first_prompt")},
    )
