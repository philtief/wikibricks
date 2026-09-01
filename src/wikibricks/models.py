"""Harness-neutral data contracts used by local WikiBricks ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SESSION_EVENT_KINDS = frozenset(
    {"user", "assistant", "tool_call", "tool_result", "error", "lifecycle"}
)
_HARNESS_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    """A bounded request for memory relevant to one agent turn."""

    text: str
    user_id: str
    workspace: str | None = None
    current_session_id: str | None = None
    max_chars: int = 6000

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("memory query text must not be empty")
        if not self.user_id:
            raise ValueError("memory query user_id must not be empty")
        if self.max_chars < 1:
            raise ValueError("memory query max_chars must be positive")


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """One page or raw-session excerpt selected for a memory packet."""

    path: str
    title: str
    text: str
    kind: str
    score: float


@dataclass(frozen=True, slots=True)
class MemoryPacket:
    """Rendered context and its structured source items."""

    items: tuple[MemoryItem, ...]
    rendered: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """One ordered, source-addressable event from an agent session."""

    external_id: str
    kind: str
    content: str
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.external_id:
            raise ValueError("session event external_id must not be empty")
        if self.kind not in SESSION_EVENT_KINDS:
            raise ValueError(f"unsupported session event kind: {self.kind}")
        if not isinstance(self.content, str):
            raise TypeError("session event content must be a string")
        object.__setattr__(self, "content", self.content.replace("\x00", "\ufffd"))
        if not isinstance(self.metadata, dict):
            raise TypeError("session event metadata must be an object")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SessionEvent:
        return cls(
            external_id=str(value.get("external_id") or ""),
            kind=str(value.get("kind") or ""),
            content=value.get("content", ""),
            created_at=value.get("created_at"),
            metadata=value.get("metadata") or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "kind": self.kind,
            "content": self.content,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """A complete normalized session independent of its source harness."""

    harness: str
    external_id: str
    user_id: str
    events: list[SessionEvent]
    agent: str | None = None
    workspace: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _HARNESS_RE.fullmatch(self.harness):
            raise ValueError(f"invalid harness name: {self.harness!r}")
        if not self.external_id:
            raise ValueError("session external_id must not be empty")
        if not self.user_id:
            raise ValueError("session user_id must not be empty")
        if not isinstance(self.metadata, dict):
            raise TypeError("session metadata must be an object")
        event_ids = [event.external_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("duplicate event external_id in session")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SessionRecord:
        raw_events = value.get("events") or []
        if not isinstance(raw_events, list):
            raise TypeError("session events must be an array")
        return cls(
            harness=str(value.get("harness") or ""),
            external_id=str(value.get("external_id") or ""),
            user_id=str(value.get("user_id") or ""),
            agent=value.get("agent"),
            workspace=value.get("workspace"),
            started_at=value.get("started_at"),
            updated_at=value.get("updated_at"),
            events=[SessionEvent.from_dict(event) for event in raw_events],
            metadata=value.get("metadata") or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "harness": self.harness,
            "external_id": self.external_id,
            "user_id": self.user_id,
            "agent": self.agent,
            "workspace": self.workspace,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "events": [event.to_dict() for event in self.events],
            "metadata": self.metadata,
        }
