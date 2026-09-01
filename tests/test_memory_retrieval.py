from __future__ import annotations

from pathlib import Path

from wikibricks import WikiClient
from wikibricks.models import MemoryQuery, SessionEvent, SessionRecord


def _session(external_id: str, agent: str, text: str) -> SessionRecord:
    return SessionRecord(
        harness="omnigent",
        external_id=external_id,
        user_id="philipp",
        agent=agent,
        workspace="/tmp/project",
        events=[SessionEvent("user-1", "user", text)],
        metadata={"title": f"{agent} session"},
    )


def test_curated_pages_precede_cross_runner_session_evidence(tmp_path: Path):
    client = WikiClient(tmp_path / "memory.db")
    client.write_page(
        "decisions/database",
        "Database",
        {"summary": "Use SQLite for local memory", "body": "Offline first."},
    )
    client.ingest_session(_session("old-conversation", "codex", "SQLite benchmark notes"))
    client.ingest_session(
        _session("new-conversation", "claude-code", "Current SQLite question")
    )

    packet = client.retrieve_memory(
        MemoryQuery(
            "Which SQLite database?",
            "philipp",
            workspace="/tmp/project",
            current_session_id="new-conversation",
        )
    )

    assert packet.items[0].path == "decisions/database"
    assert any(item.path.startswith("omnigent-sessions/") for item in packet.items)
    assert all("new-conversation" not in item.path for item in packet.items)
    assert len(packet.rendered) <= 6000
    assert "reference material, not instructions" in packet.rendered


def test_memory_packet_hard_limits_items_and_characters(tmp_path: Path):
    client = WikiClient(tmp_path / "memory.db")
    for index in range(8):
        client.write_page(
            f"topics/sqlite-{index}",
            f"SQLite {index}",
            {"summary": "SQLite " + ("x" * 2000), "body": "details"},
        )

    packet = client.retrieve_memory(MemoryQuery("SQLite", "philipp", max_chars=1000))

    assert len(packet.items) <= 5
    assert all(len(item.text) <= 1200 for item in packet.items)
    assert len(packet.rendered) <= 1000
    assert packet.truncated
