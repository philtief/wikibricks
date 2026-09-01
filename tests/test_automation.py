from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from wikibricks.automation import run_background_cycle, run_remote_cycle
from wikibricks.config import load_config
from wikibricks.curation import (
    build_manifest,
    create_patch,
    get_or_create_replica_id,
    list_conflicts,
    publish_manifest,
)
from wikibricks.maintenance import initialize_database
from wikibricks.postgres_store import PostgresStore
from wikibricks.storage.sqlite_store import SQLiteStore


def test_local_background_maintenance_uses_sqlite_lease(tmp_path: Path):
    config = load_config(
        home=tmp_path,
        environ={"WIKIBRICKS_DATABASE_PATH": str(tmp_path / "memory.db")},
    )

    first = run_background_cycle(config, now=700_000)
    second = run_background_cycle(config, now=700_060)

    assert first["maintenance"]["ok"] is True
    assert "maintenance" not in second
    assert SQLiteStore(config.database_path).read_page("_meta/index") is not None


def _database_url(base_url: str, database: str) -> str:
    params = conninfo_to_dict(base_url)
    params["dbname"] = database
    return make_conninfo(**params)


@pytest.fixture(scope="module")
def automation_remote_url(postgres_url: str) -> str:
    target = _database_url(postgres_url, "wikibricks_automation_remote_test")
    initialize_database(target)
    return target


def _proposal(title: str, body: str) -> dict:
    return {
        "title": title,
        "page_type": "concept",
        "content": {"summary": title, "body": body},
        "content_text": f"{title} {body}",
        "tags": ["automation"],
        "source_ids": ["session:automation"],
        "parent_id": None,
        "chunk_index": None,
    }


def _publish_update(
    local: PostgresStore,
    remote: PostgresStore,
    *,
    title: str,
    body: str,
    watermark: int,
) -> UUID:
    state = local.current_page_state("topics/background")
    patch = create_patch(
        operation="update_page",
        path="topics/background",
        base_version_id=state["version_id"],
        base_content_hash=state["content_hash"],
        proposal=_proposal(title, body),
        evidence_ids=["session:automation"],
        reason="Keep the local page current without a user command.",
    )
    manifest = build_manifest(
        replica_id=get_or_create_replica_id(local),
        input_watermark=watermark,
        patches=[patch],
    )
    publish_manifest(remote, manifest)
    return UUID(manifest["run_id"])


def test_remote_cycle_applies_exact_base_and_keeps_divergent_local_edit(
    postgres_url: str,
    automation_remote_url: str,
    tmp_path: Path,
):
    local = PostgresStore(postgres_url)
    remote = PostgresStore(automation_remote_url)
    local.migrate()
    local.clear_all()
    remote.clear_all()
    local.write_page(
        "topics/background",
        "Original",
        {"summary": "Original", "body": "base"},
        source_ids=["session:automation"],
    )
    config = replace(
        load_config(home=tmp_path, environ={"WIKIBRICKS_DATABASE_URL": postgres_url}),
        sync_profile="test",
        sync_project="wikibricks",
    )

    first_run = _publish_update(
        local,
        remote,
        title="Remote clean",
        body="applied automatically",
        watermark=1,
    )
    first = run_remote_cycle(
        local,
        config,
        remote_url_factory=lambda _target: automation_remote_url,
    )

    assert local.read_page("topics/background")["title"] == "Remote clean"
    assert str(first_run) in {item["run_id"] for item in first["applications"]}

    second_run = _publish_update(
        local,
        remote,
        title="Remote second",
        body="must not replace a newer local edit",
        watermark=2,
    )
    local.write_page(
        "topics/background",
        "Local wins",
        {"summary": "Local wins", "body": "newer local decision"},
        source_ids=["session:automation"],
    )
    second = run_remote_cycle(
        local,
        config,
        remote_url_factory=lambda _target: automation_remote_url,
    )

    assert local.read_page("topics/background")["title"] == "Local wins"
    assert list_conflicts(local) == []
    assert str(second_run) in second["kept_local_runs"]


def test_remote_failure_is_rate_limited_and_does_not_skip_local_maintenance(
    postgres_url: str,
    tmp_path: Path,
    monkeypatch,
):
    store = PostgresStore(postgres_url)
    store.migrate()
    store.clear_all()
    config = replace(
        load_config(home=tmp_path, environ={"WIKIBRICKS_DATABASE_URL": postgres_url}),
        sync_profile="unavailable",
        sync_project="wikibricks",
    )
    attempts = 0

    def fail_remote(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("remote unavailable")

    monkeypatch.setattr("wikibricks.automation.run_remote_cycle", fail_remote)

    first = run_background_cycle(config, now=700_000)
    second = run_background_cycle(config, now=700_060)

    assert first["remote_error"] == "remote unavailable"
    assert first["maintenance"]["ok"] is True
    assert "remote_error" not in second
    assert attempts == 1


def _omnigent_db(path: Path) -> tuple[str, sqlite3.Connection]:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute(
        "CREATE TABLE conversations (id BLOB, created_at INT, updated_at INT, "
        "title TEXT, archived INT, agent_id BLOB, workspace_id INT)"
    )
    connection.execute("CREATE TABLE agents (id BLOB, name TEXT, workspace_id INT)")
    connection.execute(
        "CREATE TABLE conversation_items (conversation_id BLOB, workspace_id INT, "
        "position INT, type INT, data TEXT)"
    )
    conversation_id = bytes.fromhex("cc" * 16)
    agent_id = bytes.fromhex("dd" * 16)
    connection.execute("INSERT INTO agents VALUES (?, ?, ?)", (agent_id, "codex-native-ui", 0))
    connection.execute(
        "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?)",
        (conversation_id, 1788084000, 1788087600, "Automatic memory", 0, agent_id, 0),
    )
    connection.execute(
        "INSERT INTO conversation_items VALUES (?, ?, ?, ?, ?)",
        (
            conversation_id,
            0,
            0,
            1,
            json.dumps({"role": "user", "content": "background capture marker"}),
        ),
    )
    connection.commit()
    return "cc" * 16, connection


def test_background_cycle_does_not_scrape_omnigent_database(tmp_path: Path):
    database_path = tmp_path / "wikibricks.db"
    store = SQLiteStore(database_path)
    store.migrate()
    chat_db = tmp_path / "chat.db"
    conversation_id, writer = _omnigent_db(chat_db)
    config = load_config(
        home=tmp_path,
        environ={
            "WIKIBRICKS_DATABASE_PATH": str(database_path),
            "WIKIBRICKS_OMNIGENT_DATABASE": str(chat_db),
        },
    )
    try:
        result = run_background_cycle(config, now=700_000)
    finally:
        writer.close()

    assert "omnigent" not in result
    assert store.read_page(f"omnigent-sessions/u/2026/08/30/{conversation_id}") is None
