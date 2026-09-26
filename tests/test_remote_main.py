from __future__ import annotations

import argparse
import json
from types import SimpleNamespace
from typing import Any

import pytest
from psycopg.conninfo import conninfo_to_dict

from wikibricks_remote import main as remote_main
from wikibricks_remote.main import _candidate_provider, _database_url, _embedder, _json_content, build_parser, main
from wikibricks_remote.resources import load_policy
from wikibricks_remote.search import CandidateSelection


def test_build_parser_uses_contract_defaults():
    args = build_parser().parse_args(
        ["--project", "memory", "--model-endpoint", "curator"]
    )

    assert args == argparse.Namespace(
        project="memory",
        branch="production",
        endpoint="primary",
        database="wikibricks",
        model_endpoint="curator",
        embedding_endpoint="databricks-gte-large-en",
        policy=None,
    )


def test_database_url_combines_endpoint_credential_and_user():
    endpoint_calls: list[str] = []
    credential_calls: list[str] = []
    endpoint = SimpleNamespace(
        status=SimpleNamespace(hosts=SimpleNamespace(host="lakebase.example"))
    )
    workspace = SimpleNamespace(
        postgres=SimpleNamespace(
            get_endpoint=lambda endpoint_name: endpoint_calls.append(endpoint_name)
            or endpoint,
            generate_database_credential=lambda endpoint_name: credential_calls.append(
                endpoint_name
            )
            or SimpleNamespace(token="token"),
        ),
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name="user@example.com")
        ),
    )
    args = build_parser().parse_args(
        [
            "--project",
            "memory",
            "--branch",
            "staging",
            "--endpoint",
            "east",
            "--database",
            "wiki",
            "--model-endpoint",
            "curator",
        ]
    )

    parsed = conninfo_to_dict(_database_url(workspace, args))

    endpoint_name = "projects/memory/branches/staging/endpoints/east"
    assert endpoint_calls == [endpoint_name]
    assert credential_calls == [endpoint_name]
    assert parsed == {
        "dbname": "wiki",
        "host": "lakebase.example",
        "password": "token",
        "port": "5432",
        "sslmode": "require",
        "user": "user@example.com",
    }


def test_main_resolves_endpoints_and_rejects_a_non_object_model_response(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    endpoint_calls: list[str] = []
    queries: list[dict[str, Any]] = []
    store_urls: list[str] = []
    search_arguments: list[dict[str, Any]] = []
    endpoint = SimpleNamespace(
        status=SimpleNamespace(hosts=SimpleNamespace(host="lakebase.example"))
    )

    class FakeWorkspace:
        def __init__(self):
            def model_response(**request):
                queries.append(request)
                if "messages" in request:
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))]
                    )
                return SimpleNamespace(
                    data=[
                        SimpleNamespace(index=1, embedding=[4.0]),
                        SimpleNamespace(index=0, embedding=[2.0]),
                    ]
                )

            self.postgres = SimpleNamespace(
                get_endpoint=lambda endpoint_name: endpoint_calls.append(endpoint_name)
                or endpoint,
                generate_database_credential=lambda _: SimpleNamespace(token="token"),
            )
            self.current_user = SimpleNamespace(
                me=lambda: SimpleNamespace(user_name="user@example.com")
            )
            self.serving_endpoints = SimpleNamespace(
                query=model_response,
            )

    class FakeStore:
        def __init__(self, database_url: str):
            store_urls.append(database_url)

    class FakeSearch:
        def __init__(self, store, **kwargs):
            search_arguments.append({"store": store, **kwargs})

        def available(self):
            return True

        def project(self, replica_id, watermark, evidence, *, max_pages):
            return max_pages

        def embed_missing(self, embedder, *, maximum, batch_size):
            return embedder(["text"])

        def candidates(
            self, replica_id, watermark, evidence, *, maximum_queries, pages_per_query
        ):
            return CandidateSelection(
                "ready", (), (), maximum_queries, pages_per_query, 0
            )

    monkeypatch.setattr("databricks.sdk.WorkspaceClient", FakeWorkspace)
    monkeypatch.setattr(remote_main, "PostgresStore", FakeStore)
    monkeypatch.setattr(remote_main, "LakebaseHybridSearch", FakeSearch)

    def run_maintenance(store, *, policy, proposer, candidate_provider):
        with pytest.raises(
            RuntimeError, match="curation model output must be a JSON object"
        ):
            proposer("system prompt", {"request": "value"}, {"type": "object"})
        selection = candidate_provider("replica", 3, ("evidence",))
        return {
            "database": store_urls[-1],
            "selection_source": selection.search_status,
            "projected": selection.projected_documents,
            "temperature": policy.temperature,
        }

    monkeypatch.setattr(remote_main, "run_maintenance", run_maintenance)

    exit_code = main(
        [
            "--project",
            "memory",
            "--branch",
            "staging",
            "--endpoint",
            "east",
            "--database",
            "wiki",
            "--model-endpoint",
            "curator",
            "--embedding-endpoint",
            "embedder",
        ]
    )

    policy = load_policy()
    parsed = conninfo_to_dict(store_urls[0])
    selection = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert endpoint_calls == ["projects/memory/branches/staging/endpoints/east"]
    assert len(queries) == 2
    assert queries[0]["name"] == "curator"
    assert queries[0]["temperature"] == policy.temperature
    assert queries[0]["max_tokens"] == policy.max_output_tokens
    assert queries[1]["name"] == "embedder"
    assert queries[1]["input"] == ["text"]
    assert parsed["host"] == "lakebase.example"
    assert parsed["dbname"] == "wiki"
    assert parsed["password"] == "token"
    assert parsed["user"] == "user@example.com"
    assert search_arguments[0]["embedding_model"] == "embedder"
    assert selection == {
        "database": store_urls[0],
        "selection_source": "ready",
        "projected": policy.max_index_pages,
        "temperature": policy.temperature,
    }


def test_json_content_rejects_empty_and_non_object_responses():
    no_message = SimpleNamespace(choices=[])
    empty = SimpleNamespace(choices=[SimpleNamespace(message=None)])
    empty_content = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="  "))]
    )
    non_object = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))]
    )
    fenced = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content="```json\n{\"ok\": true}\n```"))
        ]
    )

    with pytest.raises(RuntimeError, match="curation model returned no message"):
        _json_content(no_message)
    with pytest.raises(RuntimeError, match="curation model returned no message"):
        _json_content(empty)
    with pytest.raises(RuntimeError, match="curation model returned empty content"):
        _json_content(empty_content)
    with pytest.raises(RuntimeError, match="must be a JSON object"):
        _json_content(non_object)

    assert _json_content(fenced) == {"ok": True}


def test_embedder_orders_embeddings_and_rejects_missing_data():
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            query=lambda **_: SimpleNamespace(
                data=[
                    SimpleNamespace(index=1, embedding=[4.0]),
                    SimpleNamespace(index=0, embedding=[2.0]),
                ]
            )
        )
    )
    missing = SimpleNamespace(
        serving_endpoints=SimpleNamespace(query=lambda **_: SimpleNamespace(data=None))
    )

    assert _embedder(workspace, "embedding")(["text"]) == [[2.0], [4.0]]
    with pytest.raises(RuntimeError, match="embedding model returned no data"):
        _embedder(missing, "embedding")(["text"])


def test_candidate_provider_returns_unavailable_search():
    class UnavailableSearch:
        def available(self):
            return False

    selection = _candidate_provider(
        UnavailableSearch(),
        lambda texts: [],
        load_policy(),
    )("replica", 1, ())

    assert selection.search_status == "unavailable"
    assert selection.pages == ()
