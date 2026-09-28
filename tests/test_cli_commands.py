from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from wikibricks.cli import import_omnigent_server, main
from wikibricks.models import SessionRecord


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
