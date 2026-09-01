from __future__ import annotations

from pathlib import Path

import pytest

from wikibricks import WikiClient


def test_wiki_client_uses_a_local_sqlite_file(tmp_path: Path):
    database_path = tmp_path / "memory.db"
    client = WikiClient(database_path)
    client.write_page(
        "topics/local",
        "Local",
        {"summary": "local", "body": "sqlite"},
    )

    assert isinstance(client, WikiClient)
    assert client.database_path == database_path
    assert client.read_page("topics/local")["content"]["body"] == "sqlite"


def test_wiki_client_rejects_removed_sql_warehouse_arguments():
    with pytest.raises(TypeError):
        WikiClient(warehouse_id="warehouse", workspace_client=object())
