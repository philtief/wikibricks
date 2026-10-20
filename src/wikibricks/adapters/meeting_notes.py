"""Meeting notes dropped as files: one note file is one session."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wikibricks.models import SessionEvent, SessionRecord

NOTES_HARNESS = "meeting-notes"
NOTE_SUFFIXES = (".md", ".txt", ".vtt")
_DATE_PREFIX = re.compile(r"(\d{4}-\d{2}-\d{2})")
# Teams and Graph transcripts wrap each turn in a voice span.
_VOICE = re.compile(r"^<v ([^>]+)>(.*)</v>$", re.DOTALL)
_SIDECAR_KEYS = ("platform", "attendees", "source_uri")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def iter_note_files(directory: Path) -> Iterator[Path]:
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in NOTE_SUFFIXES:
            yield path


def note_to_session(path: Path, *, user_id: str, root: Path | None = None) -> SessionRecord:
    """Map one note or transcript file to a session the curator can route."""
    sidecar = _sidecar(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    events = _cue_events(text) if path.suffix.lower() == ".vtt" else [SessionEvent("0", "user", text)]
    if not any(event.content.strip() for event in events):
        raise ValueError(f"note holds no text: {path}")
    started_at = _started_at(sidecar, path)
    metadata: dict[str, Any] = {"title": _title(sidecar, text, path)}
    metadata.update({key: sidecar[key] for key in _SIDECAR_KEYS if key in sidecar})
    return SessionRecord(
        harness=NOTES_HARNESS,
        external_id=_external_id(sidecar, path, root),
        user_id=user_id,
        events=events,
        started_at=started_at,
        updated_at=started_at,
        metadata=metadata,
    )


def _sidecar(path: Path) -> dict[str, Any]:
    sidecar = path.with_suffix(".json")
    if not sidecar.is_file():
        return {}
    value = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"note sidecar must be an object: {sidecar}")
    return value


def _cue_events(text: str) -> list[SessionEvent]:
    events: list[SessionEvent] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line for line in block.strip().splitlines() if line.strip()]
        timing = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        body = "\n".join(lines[timing + 1 :]).strip()
        if not body:
            continue
        start, _, end = (part.strip() for part in lines[timing].partition("-->"))
        voice = _VOICE.fullmatch(body)
        metadata: dict[str, Any] = {"start": start, "end": end}
        if voice:
            metadata["speaker"] = voice.group(1)
            body = f"{voice.group(1)}: {voice.group(2).strip()}"
        events.append(SessionEvent(str(len(events)), "user", body, metadata=metadata))
    return events


def _external_id(sidecar: dict[str, Any], path: Path, root: Path | None) -> str:
    configured = sidecar.get("external_id")
    if configured:
        # The id also becomes a page path segment, so keep it path safe.
        return _UNSAFE.sub("-", str(configured)).strip("-")
    relative = path.relative_to(root) if root else Path(path.name)
    return relative.with_suffix("").as_posix()


def _started_at(sidecar: dict[str, Any], path: Path) -> str:
    configured = sidecar.get("started_at")
    if configured:
        return str(configured)
    dated = _DATE_PREFIX.match(path.name)
    if dated:
        return f"{dated.group(1)}T00:00:00+00:00"
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _title(sidecar: dict[str, Any], text: str, path: Path) -> str:
    configured = sidecar.get("title")
    if configured:
        return str(configured)
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("# ").strip()
    return path.stem
