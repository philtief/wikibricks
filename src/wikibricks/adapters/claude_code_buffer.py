"""Durable file buffer for Claude Code hook events."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _state_dir() -> Path:
    return Path(
        os.environ.get(
            "WIKIBRICKS_RECORDER_DIR",
            str(Path.home() / ".wikibricks_recorder"),
        )
    )


def state_path(session_id: str) -> Path:
    return _state_dir() / "sessions" / f"{session_id}.json"


def _empty(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "events": [],
        "started_at": None,
        "cwd": None,
        "first_prompt": None,
        "model": None,
    }


def load(session_id: str) -> dict[str, Any]:
    path = state_path(session_id)
    if not path.exists():
        return _empty(session_id)
    try:
        value = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return _empty(session_id)
    return value if isinstance(value, dict) else _empty(session_id)


def save(state: dict[str, Any]) -> None:
    path = state_path(state["session_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, default=str))


def append_event(session_id: str, event: dict[str, Any]) -> dict[str, Any]:
    state = load(session_id)
    state["events"].append(event)
    save(state)
    return state
