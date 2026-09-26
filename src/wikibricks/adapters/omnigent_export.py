"""Translate an Omnigent server JSONL export into a shared session."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from wikibricks.models import SessionEvent, SessionRecord


def _timestamp(value: int | float | str | None) -> str | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, str):
        return value
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _message_text(data: dict[str, Any]) -> str:
    content = data.get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(part.get("text") or "") if isinstance(part, dict) else str(part)
        for part in content
    ).strip()


def _event(data: dict[str, Any]) -> SessionEvent | None:
    item_type = data.get("type")
    metadata: dict[str, Any] = {}
    if item_type == "message":
        role = data.get("role")
        if role not in {"user", "assistant"}:
            return None
        kind = role
        content = _message_text(data)
    elif item_type == "function_call":
        kind = "tool_call"
        arguments = data.get("arguments", "")
        content = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        metadata = {"tool_name": data.get("name", "?"), "call_id": data.get("call_id")}
    elif item_type == "function_call_output":
        kind = "tool_result"
        output = data.get("output", "")
        content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, sort_keys=True)
        metadata = {"call_id": data.get("call_id")}
    elif item_type == "error":
        kind = "error"
        content = str(data.get("message") or data.get("code") or "error")
    elif item_type == "resource_event":
        kind = "lifecycle"
        content = json.dumps(data, ensure_ascii=False, sort_keys=True)
    else:
        return None
    return SessionEvent(
        external_id=str(data.get("id") or ""),
        kind=kind,
        content=content,
        created_at=_timestamp(data.get("created_at")),
        metadata={key: value for key, value in metadata.items() if value is not None},
    )


def export_to_session(
    lines: Iterable[str],
    *,
    user_id: str,
    server: str,
) -> SessionRecord | None:
    """Convert one export, or return ``None`` for a non-capturable session."""
    metadata: dict[str, Any] | None = None
    items: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            continue
        if value.get("record_type") == "session_meta":
            metadata = value
        elif value.get("record_type") == "item":
            items.append(value)

    if metadata is None or metadata.get("archived"):
        return None
    if metadata.get("parent_session_id") is not None or metadata.get("sub_agent_name") is not None:
        return None

    events = [event for item in items if isinstance(item, dict) and (event := _event(item)) is not None]
    if not any(event.kind == "user" and event.content.strip() for event in events):
        return None

    return SessionRecord(
        harness="omnigent",
        external_id=str(metadata.get("id") or ""),
        user_id=user_id,
        agent=metadata.get("agent_name"),
        workspace=metadata.get("workspace"),
        started_at=_timestamp(metadata.get("created_at")),
        updated_at=_timestamp(metadata.get("updated_at")),
        events=events,
        metadata={
            "title": metadata.get("title"),
            "omnigent_harness": metadata.get("harness"),
            "omnigent_server": server,
        },
    )
