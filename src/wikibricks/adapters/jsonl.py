"""Versioned JSON Lines interchange format for agent session imports."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator

from wikibricks.models import SessionRecord

JSONL_SCHEMA_VERSION = 1


def iter_jsonl_sessions(lines: Iterable[str]) -> Iterator[SessionRecord]:
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on line {line_number}: {exc.msg}") from exc
        if not isinstance(envelope, dict):
            raise ValueError(f"session envelope on line {line_number} must be an object")
        version = envelope.get("schema_version")
        if version != JSONL_SCHEMA_VERSION:
            raise ValueError(f"unsupported WikiBricks session schema version: {version}")
        session = envelope.get("session")
        if not isinstance(session, dict):
            raise ValueError(f"session on line {line_number} must be an object")
        yield SessionRecord.from_dict(session)
