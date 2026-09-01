"""Translate read-only Omnigent conversation rows into shared sessions."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wikibricks.models import SessionEvent, SessionRecord

_SUBAGENT_TITLE_PREFIXES = ("general-purpose:", "polly:", "debby:")
_SUBAGENT_TITLE_SUFFIXES = ("-native-ui",)


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


def is_syncable_conversation(conversation: dict[str, Any]) -> bool:
    if conversation.get("archived"):
        return False
    title = str(conversation.get("title") or "").strip()
    if title.startswith(_SUBAGENT_TITLE_PREFIXES) or title.endswith(_SUBAGENT_TITLE_SUFFIXES):
        return False
    record = conversation_to_session(conversation, user_id="filter")
    return any(event.kind == "user" and event.content.strip() for event in record.events)


def load_conversations(
    db_path: Path,
    *,
    cursor: tuple[int, str] | None = None,
    since_epoch: int | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Read Omnigent's SQLite store without acquiring write access."""
    connection = sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=2,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        where = ["c.archived = 0"]
        params: list[Any] = []
        if since_epoch is not None:
            where.append("c.updated_at >= ?")
            params.append(since_epoch)
        if cursor is not None:
            where.append(
                "(c.updated_at > ? OR (c.updated_at = ? AND lower(hex(c.id)) > ?))"
            )
            params.extend([cursor[0], cursor[0], cursor[1]])
        query = (
            "SELECT lower(hex(c.id)) AS cid, c.title, c.created_at, c.updated_at, "
            "c.workspace_id, a.name AS agent_name "
            "FROM conversations c "
            "LEFT JOIN agents a ON a.id = c.agent_id AND a.workspace_id = c.workspace_id "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY c.updated_at ASC, cid ASC"
        )
        if limit:
            query += f" LIMIT {int(limit)}"
        rows = connection.execute(query, params).fetchall()
        conversations: list[dict[str, Any]] = []
        for row in rows:
            item_rows = connection.execute(
                "SELECT type, data FROM conversation_items "
                "WHERE conversation_id = ? AND workspace_id = ? ORDER BY position ASC",
                (bytes.fromhex(row["cid"]), row["workspace_id"]),
            ).fetchall()
            items: list[tuple[int, dict[str, Any]]] = []
            for item in item_rows:
                try:
                    data = json.loads(item["data"])
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if isinstance(data, dict):
                    items.append((int(item["type"]), data))
            conversations.append(
                {
                    "conversation_id": row["cid"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "agent_name": row["agent_name"],
                    "workspace": None,
                    "archived": False,
                    "items": items,
                }
            )
        return conversations
    finally:
        connection.close()
