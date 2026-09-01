"""Stable identities, hashes, and paths for normalized session ingestion."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from wikibricks.models import SessionRecord


def session_identity(record: SessionRecord) -> UUID:
    """Return the storage identity, which is independent of the rendered path."""
    return uuid5(
        NAMESPACE_URL,
        f"https://wikibricks.dev/session/{record.harness}/{record.external_id}",
    )


def session_content_hash(record: SessionRecord) -> str:
    """Hash canonical source content for idempotent re-imports."""
    payload = record.to_dict()
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def session_page_path(record: SessionRecord) -> str:
    """Build a browse path while keeping source identity separate."""
    if record.started_at:
        started = datetime.fromisoformat(record.started_at.replace("Z", "+00:00"))
    else:
        started = datetime(1970, 1, 1, tzinfo=timezone.utc)
    prefix = "sessions" if record.harness == "claude-code" else f"{record.harness}-sessions"
    return f"{prefix}/{record.user_id}/{started:%Y/%m/%d}/{record.external_id}"


def session_tags(record: SessionRecord) -> list[str]:
    tags = ["session", f"harness:{record.harness}"]
    if record.agent:
        tags.append(f"agent:{record.agent}")
    tags.append(f"user:{record.user_id}")
    return tags
