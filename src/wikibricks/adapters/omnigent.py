"""Translate read-only Omnigent conversation rows into shared sessions."""

from __future__ import annotations

import json
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
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(part.get("text") or part.get("output_text") or "")
        if isinstance(part, dict)
        else str(part)
        for part in content
    ).strip()


def _event(position: int, item_type: int, data: dict[str, Any]) -> SessionEvent | None:
    external_id = str(data.get("id") or position)
    metadata: dict[str, Any] = {}
    if item_type == 1:
        role = data.get("role")
        if role not in {"user", "assistant"}:
            return None
        kind = role
        content = _message_text(data)
    elif item_type == 2:
        kind = "tool_call"
        arguments = data.get("arguments", "")
        content = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        metadata = {"tool_name": data.get("name", "?"), "call_id": data.get("call_id")}
    elif item_type == 3:
        kind = "tool_result"
        output = data.get("output", "")
        content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, sort_keys=True)
        metadata = {"call_id": data.get("call_id")}
    elif item_type == 5:
        kind = "error"
        content = str(data.get("message") or data.get("code") or "error")
    elif item_type == 8:
        kind = "lifecycle"
        content = json.dumps(data, ensure_ascii=False, sort_keys=True)
    else:
        return None
    return SessionEvent(
        external_id=external_id,
        kind=kind,
        content=content,
        created_at=_timestamp(data.get("created_at")),
        metadata={key: value for key, value in metadata.items() if value is not None},
    )


def conversation_to_session(conversation: dict[str, Any], *, user_id: str) -> SessionRecord:
    events = [
        event
        for position, (item_type, data) in enumerate(conversation.get("items") or [])
        if isinstance(data, dict) and (event := _event(position, item_type, data)) is not None
    ]
    return SessionRecord(
        harness="omnigent",
        external_id=str(conversation.get("conversation_id") or ""),
        user_id=user_id,
        agent=conversation.get("agent_name"),
        workspace=conversation.get("workspace"),
        started_at=_timestamp(conversation.get("created_at")),
        updated_at=_timestamp(conversation.get("updated_at")),
        events=events,
        metadata={
            "title": conversation.get("title"),
            "archived": bool(conversation.get("archived")),
        },
    )
