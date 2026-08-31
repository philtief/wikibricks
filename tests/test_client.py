from __future__ import annotations

import pytest

from wikibricks import WikiClient


def test_wiki_client_is_the_local_client(postgres_url: str):
    client = WikiClient(postgres_url)
    client.write_page(
        "topics/local",
        "Local",
        {"summary": "local", "body": "postgres"},
    )

    assert isinstance(client, WikiClient)
    assert client.read_page("topics/local")["content"]["body"] == "postgres"


def test_wiki_client_rejects_removed_sql_warehouse_arguments():
    with pytest.raises(TypeError):
        WikiClient(warehouse_id="warehouse", workspace_client=object())
