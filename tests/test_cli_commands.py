from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from wikibricks.cli import import_omnigent_server, main
from wikibricks.curation.backlog import unrouted_sessions
from wikibricks.models import SessionRecord
from wikibricks.storage.sqlite_store import SQLiteStore


def run_cli(database_path: Path, *arguments: str) -> int:
    return main(["--database-path", str(database_path), *arguments])


@pytest.mark.parametrize(
    ("command", "json_keys"),
    [
        ("init", None),
        ("search query", []),
        (
            "check",
            ["ok", "integrity", "broken_page_pointers", "capture"],
        ),
        (
            "curate",
            ["ok", "index", "repaired_page_search_versions", "pending_outbox"],
        ),
        ("vacuum", None),
    ],
)
def test_local_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str, json_keys):
    database = tmp_path / "wikibricks.db"
    exit_code = run_cli(database, *command.split())
    output = capsys.readouterr().out

    assert exit_code == 0
    if json_keys is not None:
        assert set(json_keys).issubset(json.loads(output))
    else:
        assert output.strip()


def test_backup_restore_vacuum_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    restored = tmp_path / "restored.db"

    assert run_cli(source, "init") == 0
    assert run_cli(source, "backup", str(backup)) == 0
    assert backup.is_file()
    assert run_cli(restored, "restore", str(backup)) == 0
    assert run_cli(restored, "vacuum") == 0

    captured = capsys.readouterr().out
    assert str(backup) in captured
    assert "backup restored" in captured
    assert "vacuum complete" in captured


def test_import_jsonl_reports_contract_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    database = tmp_path / "wikibricks.db"
    source = tmp_path / "sessions.jsonl"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session": {
                    "harness": "cli-test",
                    "external_id": "one",
                    "user_id": "user",
                    "events": [{"external_id": "0", "kind": "user", "content": "hello"}],
                },
            }
        )
        + "\n",
    )

    exit_code = run_cli(database, "import", "jsonl", str(source))
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output == {"scanned": 1, "imported": 1, "errors": 0}


def test_import_notes_maps_meeting_files_and_stays_unrouted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    database = tmp_path / "wikibricks.db"
    notes = tmp_path / "meetings"
    notes.mkdir()
    (notes / "2026-10-19--allianz-pricing.md").write_text(
        "# Allianz pricing follow-up\n\nAgreed to send the serverless estimate.\n"
    )
    (notes / "2026-10-19--allianz-pricing.json").write_text(
        json.dumps(
            {
                "platform": "gmeet",
                "attendees": ["philipp@databricks.com"],
                "source_uri": "https://meet.google.com/abc-defg-hij",
            }
        )
    )
    (notes / "2026-10-20--rwe-sap.vtt").write_text(
        "WEBVTT\n\n"
        "00:00:01.500 --> 00:00:04.000\n<v Philipp>SAP ingestion is next.</v>\n\n"
        "00:00:04.000 --> 00:00:07.000\n<v Anna>We need Lakeflow Connect.</v>\n"
    )

    assert run_cli(database, "import", "notes", str(notes), "--user-id", "user") == 0
    report = json.loads(capsys.readouterr().out)
    assert run_cli(database, "import", "notes", str(notes), "--user-id", "user") == 0
    repeat = json.loads(capsys.readouterr().out)

    assert report == {"scanned": 2, "imported": 2, "errors": 0}
    assert repeat == report
    store = SQLiteStore(database)
    meeting = store.read_page("meeting-notes-sessions/user/2026/10/19/2026-10-19--allianz-pricing")
    transcript = store.read_page("meeting-notes-sessions/user/2026/10/20/2026-10-20--rwe-sap")

    assert meeting["title"] == "Allianz pricing follow-up"
    assert "serverless estimate" in meeting["content_text"]
    assert [event["content"] for event in transcript["events"]] == [
        "Philipp: SAP ingestion is next.",
        "Anna: We need Lakeflow Connect.",
    ]
    with store.connection() as conn:
        # A meeting has no workspace, so the curator routes it by content.
        assert len(unrouted_sessions(conn, since="2026-10-01", generic=(), home=tmp_path)) == 2
        assert conn.execute("SELECT COUNT(*) FROM session_event_versions").fetchone()[0] == 3


def test_import_notes_reads_frontmatter_and_skips_dot_folders(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    database = tmp_path / "wikibricks.db"
    vault = tmp_path / "vault"
    (vault / ".trash").mkdir(parents=True)
    (vault / "2026-10-21--rwe-sap-review.md").write_text(
        "---\n"
        "title: RWE SAP ingestion review\n"
        "platform: teams\n"
        "attendees: [philipp@databricks.com, anna@rwe.com]\n"
        "started_at: 2026-10-21T15:00:00+00:00\n"
        "---\n\n"
        "Lakeflow Connect covers the SAP extractors.\n"
    )
    # Obsidian moves a deleted note into `.trash` inside the vault.
    (vault / ".trash" / "2026-10-20--deleted.md").write_text("# Deleted\n\nGone.\n")

    assert run_cli(database, "import", "notes", str(vault), "--user-id", "user") == 0
    report = json.loads(capsys.readouterr().out)

    store = SQLiteStore(database)
    page = store.read_page("meeting-notes-sessions/user/2026/10/21/2026-10-21--rwe-sap-review")
    with store.connection() as conn:
        row = conn.execute("SELECT metadata, started_at FROM sessions").fetchone()
    metadata = json.loads(row[0])

    assert report == {"scanned": 1, "imported": 1, "errors": 0}
    assert page["title"] == "RWE SAP ingestion review"
    assert page["events"][0]["content"] == "Lakeflow Connect covers the SAP extractors.\n"
    assert metadata["platform"] == "teams"
    assert metadata["attendees"] == ["philipp@databricks.com", "anna@rwe.com"]
    # YAML types a timestamp as a datetime, which still has to reach the store as ISO text.
    assert row[1].startswith("2026-10-21 15:00:00")


def test_sync_replica_and_conflicts(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    database = tmp_path / "wikibricks.db"

    assert run_cli(database, "sync", "replica") == 0
    replica = json.loads(capsys.readouterr().out)
    assert run_cli(database, "sync", "conflicts") == 0
    conflicts = json.loads(capsys.readouterr().out)

    assert list(replica) == ["replica_id"]
    assert conflicts == []


def test_sync_lakebase_uses_a_fresh_remote_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    database = tmp_path / "wikibricks.db"
    remote_calls = []

    def fake_sync(local, remote_url, *, limit, drain, max_batches):
        remote_calls.append(remote_url)
        return {"status": "idle", "acknowledged": 0}

    monkeypatch.setattr(
        "wikibricks.remote.lakebase.LakebaseTarget.fresh_database_url",
        lambda self: str(tmp_path / "remote.db"),
    )
    monkeypatch.setattr(
        "wikibricks.remote.lakebase.sync_to_archive",
        fake_sync,
    )

    assert run_cli(
        database,
        "sync",
        "lakebase",
        "--profile",
        "profile",
        "--project",
        "memory",
        "--limit",
        "1",
        "--max-batches",
        "2",
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert remote_calls == [str(tmp_path / "remote.db")]
    assert result == {"status": "idle", "acknowledged": 0}


def test_sync_lakebase_apply_patches_applies_low_risk_and_lists_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    from uuid import uuid4

    from wikibricks.curation import build_manifest, create_patch, get_or_create_replica_id, store_manifest

    database = tmp_path / "wikibricks.db"
    store = SQLiteStore(database)
    store.migrate()
    store.write_page("topics/contoso", "Contoso", {"summary": "s", "body": "base"}, source_ids=["session:a"])
    state = store.current_page_state("topics/contoso")

    def proposal(title: str) -> dict:
        return {
            "title": title,
            "page_type": "concept",
            "content": {"summary": title, "body": title},
            "content_text": title,
            "tags": [],
            "source_ids": ["session:a"],
            "parent_id": None,
            "chunk_index": None,
        }

    low = create_patch(
        operation="update_page",
        path="topics/contoso",
        base_version_id=state["version_id"],
        base_content_hash=state["content_hash"],
        proposal=proposal("Contoso, updated remotely"),
        evidence_ids=["session:a"],
        reason="low risk",
        group_id=uuid4(),
    )
    high = create_patch(
        operation="create_page",
        path="topics/contoso-merged",
        proposal=proposal("Merged"),
        evidence_ids=["session:a"],
        reason="needs review",
        group_id=uuid4(),
        risk_class="high",
    )
    manifest = build_manifest(
        replica_id=get_or_create_replica_id(store),
        input_watermark=0,
        patches=[low, high],
    )
    store_manifest(store, manifest)
    monkeypatch.setattr(
        "wikibricks.remote.lakebase.LakebaseTarget.fresh_database_url",
        lambda self: "remote",
    )
    monkeypatch.setattr(
        "wikibricks.remote.lakebase.sync_to_archive",
        lambda local, url, *, limit, drain, max_batches: {"status": "idle"},
    )
    monkeypatch.setattr(
        "wikibricks.remote.lakebase.pull_curation_patches",
        lambda local, url: {"received_runs": 0, "received_patches": 0},
    )

    arguments = ("sync", "lakebase", "--profile", "p", "--project", "m", "--apply-patches")
    assert run_cli(database, *arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert run_cli(database, *arguments) == 0
    second = json.loads(capsys.readouterr().out)

    assert store.read_page("topics/contoso")["title"] == "Contoso, updated remotely"
    assert store.read_page("topics/contoso-merged") is None
    assert [run["run_id"] for run in first["applied"]["runs"]] == [manifest["run_id"]]
    assert first["applied"]["runs"][0]["counts"]["review_required"] == 1
    # A run with a group waiting for review stays pending, so every pass reports it again.
    assert second["applied"]["runs"] == [
        {"run_id": manifest["run_id"], "counts": {"already_processed": 1, "review_required": 1}}
    ]


def _fake_list_sessions(response):
    calls = []

    def fake_list_sessions(server, token):
        calls.append(token)
        if len(calls) == 1 and isinstance(response, HTTPError):
            raise response
        return [{"id": "session-one", "updated_at": 2}]

    return fake_list_sessions, calls


def test_import_omnigent_server_retries_forbidden_once(tmp_path, monkeypatch):
    database = tmp_path / "wikibricks.db"
    store = import_omnigent_server.__globals__["_store"](database)
    store.migrate()
    fake_list_sessions, calls = _fake_list_sessions(
        HTTPError("url", 403, "Forbidden", None, None)
    )
    monkeypatch.setattr(
        "wikibricks.cli.resolve_token",
        lambda profile: f"token-{len(calls) + 1}",
    )
    monkeypatch.setattr("wikibricks.cli.list_sessions", fake_list_sessions)
    monkeypatch.setattr(
        "wikibricks.cli.export_session",
        lambda server, session_id: {"id": session_id},
    )
    monkeypatch.setattr(
        "wikibricks.cli.export_to_session",
        lambda *args, **kwargs: SessionRecord(
            harness="omnigent",
            external_id="session-one",
            user_id="user",
            events=[],
        ),
    )

    result = import_omnigent_server(
        database_url=database,
        server="example",
        profile="profile",
        user_id="user",
    )

    assert calls == ["token-1", "token-2"]
    assert result["scanned"] == 1
    assert store.get_sync_cursor("omnigent-server:example")


def test_import_omnigent_server_second_forbidden_propagates(
    tmp_path,
    monkeypatch,
):
    error = HTTPError("url", 403, "Forbidden", None, None)

    def fake_list_sessions(server, token):
        calls.append(token)
        raise error

    calls = []
    monkeypatch.setattr("wikibricks.cli.resolve_token", lambda profile: "token")
    monkeypatch.setattr("wikibricks.cli.list_sessions", fake_list_sessions)

    with pytest.raises(HTTPError):
        import_omnigent_server(
            database_url=tmp_path / "wikibricks.db",
            server="example",
            profile="profile",
            user_id="user",
        )

    assert calls == ["token", "token"]


def test_import_omnigent_server_server_error_does_not_retry(tmp_path, monkeypatch):
    error = HTTPError("url", 500, "Error", None, None)
    fake_list_sessions, calls = _fake_list_sessions(error)
    monkeypatch.setattr("wikibricks.cli.resolve_token", lambda profile: "token")
    monkeypatch.setattr("wikibricks.cli.list_sessions", fake_list_sessions)

    with pytest.raises(HTTPError):
        import_omnigent_server(
            database_url=tmp_path / "wikibricks.db",
            server="example",
            profile="profile",
            user_id="user",
        )

    assert calls == ["token"]


@pytest.mark.parametrize("install_target", [(), ("omnigent",)])
def test_install_uses_selected_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    install_target: tuple[str, ...],
):
    calls = []

    def fake_install() -> dict[str, str]:
        calls.append("default" if not install_target else "omnigent")
        return {"configured": "clients"}

    monkeypatch.setattr(
        "wikibricks.omnigent_install.install_integrations",
        fake_install,
    )
    monkeypatch.setattr(
        "wikibricks.omnigent_install.install_omnigent",
        fake_install,
    )

    assert run_cli(tmp_path / "wikibricks.db", "install", *install_target) == 0
    result = json.loads(capsys.readouterr().out)

    assert len(calls) == 1
    assert calls[0] == ("default" if not install_target else "omnigent")
    assert result == {"configured": "clients"}
