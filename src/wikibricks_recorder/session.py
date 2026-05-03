"""On-disk JSON state for a single Claude Code session.

Hooks fire often (UserPromptSubmit on every turn, PostToolUse on every tool
call). Each fire is a fast file read + append + write. Network writes to
WikiBricks happen only on Stop / SessionEnd.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _state_dir() -> Path:
    """Resolved at call time so tests can monkeypatch the env var."""
    return Path(os.environ.get(
        "WIKIBRICKS_RECORDER_DIR",
        str(Path.home() / ".wikibricks_recorder"),
    ))


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
    p = state_path(session_id)
    if not p.exists():
        return _empty(session_id)
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return _empty(session_id)


def save(state: dict[str, Any]) -> None:
    p = state_path(state["session_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, default=str))


def append_event(session_id: str, event: dict[str, Any]) -> dict[str, Any]:
    state = load(session_id)
    state["events"].append(event)
    save(state)
    return state
