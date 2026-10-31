from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml
from jsonschema import Draft202012Validator
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from wikibricks.maintenance import initialize_database
from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.postgres_store import PostgresStore
from wikibricks.remote.lakebase import sync_to_archive

ROOT = Path(__file__).parents[1]


def test_model_json_parser_accepts_trailing_commentary():
    from wikibricks_remote.main import _json_content

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"proposals": []}\nThe wiki is already clean.'
                )
            )
        ]
    )

    assert _json_content(response) == {"proposals": []}


def _database_url(base_url: str, database: str) -> str:
    params = conninfo_to_dict(base_url)
    params["dbname"] = database
    return make_conninfo(**params)


@pytest.fixture(scope="module")
def maintenance_url(postgres_url: str) -> str:
    target = _database_url(postgres_url, "wikibricks_remote_maintenance_test")
    initialize_database(target)
    return target


def _archive_page(local: PostgresStore, remote_url: str, suffix: str) -> None:
    local.migrate()
    local.clear_all()
    remote = PostgresStore(remote_url)
    remote.migrate()
    remote.clear_all()
    local.write_page(
        f"topics/{suffix}",
        suffix.title(),
        {"summary": "archived evidence", "body": f"Evidence for {suffix}."},
    )
    assert sync_to_archive(local, remote_url)["acknowledged"] == 1


def test_remote_resources_are_readable_and_schema_validates_proposals():
    from wikibricks_remote.resources import load_policy, load_prompt, load_schema

    policy = load_policy()
    prompt = load_prompt()
    schema = load_schema()
    Draft202012Validator.check_schema(schema)

    assert policy.max_events_per_replica == 500
    assert policy.max_batches_per_replica == 24
    assert policy.max_event_chars == 2000
    assert policy.allowed_operations == (
        "create_page",
        "update_page",
        "add_link",
        "retarget_links",
        "add_alias",
        "supersede_page",
    )
    assert policy.allowed_link_types == (
        "related",
        "supports",
        "contradicts",
        "depends_on",
    )
    assert policy.max_search_chunk_chars == 12000
    assert policy.max_index_pages == 500
    assert policy.embedding_dimension == 1024
    assert policy.max_embedding_documents == 500
    assert policy.embedding_batch_size == 32
    assert policy.max_query_documents == 50
    assert policy.pages_per_query == 10
    assert "immutable manifest" in prompt


def test_remote_link_proposals_are_policy_bounded():
    from wikibricks_remote.maintenance import build_patches
    from wikibricks_remote.resources import load_policy

    source_id = uuid4()
    target_id = uuid4()
    pages = [
        {
            "evidence_id": "archive-event:source",
            "path": "topics/source",
            "title": "Source",
            "page_type": "concept",
            "content": {"summary": "Source", "body": "One"},
            "tags": [],
            "source_ids": [],
            "base_version_id": str(source_id),
            "base_content_hash": "a" * 64,
        },
        {
            "evidence_id": "archive-event:target",
            "path": "topics/target",
            "title": "Target",
            "page_type": "concept",
            "content": {"summary": "Target", "body": "Two"},
            "tags": [],
            "source_ids": [],
            "base_version_id": str(target_id),
            "base_content_hash": "b" * 64,
        },
    ]
    raw = {
        "proposals": [
            {
                "group": "relationship",
                "operation": "add_link",
                "path": "topics/source",
                "title": None,
                "page_type": None,
                "summary": None,
                "body": None,
                "tags": [],
                "source_ids": [],
                "target_path": "topics/target",
                "link_type": "related",
                "evidence_ids": [
                    "archive-event:source",
                    "archive-event:target",
                ],
                "reason": "The pages have a relationship.",
                "risk_class": "low",
            }
        ]
    }

    patches = build_patches(
        raw,
        run_id=uuid4(),
        pages=pages,
        evidence_ids=set(),
        policy=load_policy(),
    )

    assert patches[0]["proposal"] == {
        "target_path": "topics/target",
        "link_type": "related",
    }

    raw["proposals"][0]["link_type"] = "duplicate"
    with pytest.raises(ValueError, match="link type"):
        build_patches(
            raw,
            run_id=uuid4(),
            pages=pages,
            evidence_ids=set(),
            policy=load_policy(),
        )


def test_remote_maintenance_publishes_once_for_one_archive_watermark(
    postgres_url: str,
    maintenance_url: str,
):
    from wikibricks_remote.maintenance import run_maintenance
    from wikibricks_remote.resources import load_policy

    local = PostgresStore(postgres_url)
    remote = PostgresStore(maintenance_url)
    _archive_page(local, maintenance_url, "weekly-source")
    calls = 0

    def propose(_system, request, _schema):
        nonlocal calls
        calls += 1
        evidence_id = request["evidence"][0]["evidence_id"]
        return {
            "proposals": [
                {
                    "group": "weekly-source",
                    "operation": "create_page",
                    "path": "synthesis/weekly-source",
                    "title": "Weekly source synthesis",
                    "page_type": "synthesis",
                    "summary": "A curated weekly summary.",
                    "body": "The archived source supports this synthesis.",
                    "tags": ["weekly"],
                    "source_ids": ["session:weekly-source"],
                    "target_path": None,
                    "evidence_ids": [evidence_id],
                    "reason": "Preserve a reusable conclusion.",
                    "risk_class": "low",
                }
            ]
        }

    policy = replace(load_policy(), max_replicas_per_run=1)
    first = run_maintenance(remote, policy=policy, proposer=propose)
    repeated = run_maintenance(remote, policy=policy, proposer=propose)

    assert {key: value for key, value in first.items() if key != "replica_ids"} == {
        "status": "completed",
        "replicas": 1,
        "published_manifests": 1,
        "proposals": 1,
        "no_changes": 0,
        "dropped_groups": 0,
        "batches": 1,
        "pending_sequences": 0,
    }
    assert repeated == {
        "status": "idle",
        "replicas": 0,
        "published_manifests": 0,
        "proposals": 0,
        "no_changes": 0,
        "dropped_groups": 0,
        "batches": 0,
        "pending_sequences": 0,
        "replica_ids": [],
    }
    assert calls == 1
    with remote.connection() as conn:
        manifest = conn.execute("SELECT manifest FROM curation_runs").fetchone()[0]
        run = conn.execute(
            "SELECT status, input_watermark FROM remote_maintenance_runs"
        ).fetchone()
    assert manifest["replica_id"] == first["replica_ids"][0]
    assert manifest["patches"][0]["path"] == "synthesis/weekly-source"
    assert manifest["patches"][0]["proposal"]["source_ids"] == [
        "session:weekly-source"
    ]
    assert run == ("published", manifest["input_watermark"])


def _create_page(group: str, path: str, evidence_id: str) -> dict:
    return {
        "group": group,
        "operation": "create_page",
        "path": path,
        "title": path.rsplit("/", 1)[-1].title(),
        "page_type": "synthesis",
        "summary": "A curated summary.",
        "body": "The archived source supports this synthesis.",
        "tags": [],
        "source_ids": [],
        "target_path": None,
        "evidence_ids": [evidence_id],
        "reason": "Preserve a reusable conclusion.",
        "risk_class": "low",
    }


def test_remote_model_sees_only_citable_ids(postgres_url: str, maintenance_url: str):
    # Archive events also carry entity, version and hash IDs; the model cited one as evidence.
    from wikibricks_remote.maintenance import run_maintenance
    from wikibricks_remote.resources import load_policy

    _archive_page(PostgresStore(postgres_url), maintenance_url, "citable")
    seen: dict = {}

    def propose(_system, request, _schema):
        seen["request"] = request
        return {"proposals": []}

    run_maintenance(
        PostgresStore(maintenance_url),
        policy=replace(load_policy(), max_replicas_per_run=1),
        proposer=propose,
    )

    request = seen["request"]
    assert "replica_id" not in request
    assert {key for item in request["evidence"] for key in item} == {
        "evidence_id",
        "sequence",
        "entity_kind",
        "payload",
    }
    assert request["current_pages"]
    assert not any(
        {"base_version_id", "base_content_hash"} & set(page) for page in request["current_pages"]
    )


def test_a_group_citing_unknown_evidence_is_dropped_and_counted(
    postgres_url: str,
    maintenance_url: str,
):
    from wikibricks_remote.maintenance import run_maintenance
    from wikibricks_remote.resources import load_policy

    remote = PostgresStore(maintenance_url)
    _archive_page(PostgresStore(postgres_url), maintenance_url, "mixed")
    with remote.connection() as conn:
        entity_id = conn.execute("SELECT entity_id FROM archive_events").fetchone()[0]

    def propose(_system, request, _schema):
        return {
            "proposals": [
                _create_page("kept", "synthesis/kept", request["evidence"][0]["evidence_id"]),
                # The production failure: an entity ID cited as if it were an archive event.
                _create_page("miscited", "synthesis/miscited", f"archive-event:{entity_id}"),
            ]
        }

    result = run_maintenance(
        remote,
        policy=replace(load_policy(), max_replicas_per_run=1),
        proposer=propose,
    )

    assert result["published_manifests"] == 1
    assert result["proposals"] == 1
    assert result["dropped_groups"] == 1
    with remote.connection() as conn:
        manifest = conn.execute("SELECT manifest FROM curation_runs").fetchone()[0]
    assert [patch["path"] for patch in manifest["patches"]] == ["synthesis/kept"]


def test_no_change_run_advances_the_remote_watermark(
    postgres_url: str,
    maintenance_url: str,
):
    from wikibricks_remote.maintenance import run_maintenance
    from wikibricks_remote.resources import load_policy

    local = PostgresStore(postgres_url)
    remote = PostgresStore(maintenance_url)
    _archive_page(local, maintenance_url, "already-clean")
    calls = 0

    def no_changes(_system, _request, _schema):
        nonlocal calls
        calls += 1
        return {"proposals": []}

    policy = replace(load_policy(), max_replicas_per_run=1)
    first = run_maintenance(remote, policy=policy, proposer=no_changes)
    repeated = run_maintenance(remote, policy=policy, proposer=no_changes)

    assert first["no_changes"] == 1
    assert repeated["status"] == "idle"
    assert calls == 1


def _archive_session(local: PostgresStore, remote_url: str, events: list[SessionEvent]) -> int:
    local.migrate()
    local.clear_all()
    remote = PostgresStore(remote_url)
    remote.migrate()
    remote.clear_all()
    for summary in ("first", "second"):
        local.write_page("topics/catch-up", "Catch up", {"summary": summary, "body": summary})
    local.ingest_session(
        SessionRecord(harness="omnigent", external_id="long-session", user_id="u", events=events)
    )
    sync_to_archive(local, remote_url)
    with remote.connection() as conn:
        return int(conn.execute("SELECT max(local_sequence) FROM archive_events").fetchone()[0])


def test_remote_maintenance_reads_messages_in_batches_until_caught_up(
    postgres_url: str,
    maintenance_url: str,
):
    # Tool output is most of the archive; one bounded call a week never reached recent work.
    from wikibricks_remote.maintenance import run_maintenance
    from wikibricks_remote.resources import load_policy

    events = []
    for index in range(6):
        events.append(SessionEvent(f"u{index}", "user", f"question {index} " + "x" * 3000))
        events.append(SessionEvent(f"t{index}", "tool_result", "tool output " * 500))
    events.append(SessionEvent("tail", "tool_call", "trailing tool call"))
    archived = _archive_session(PostgresStore(postgres_url), maintenance_url, events)
    remote = PostgresStore(maintenance_url)
    seen: list[list[dict]] = []

    def propose(_system, request, _schema):
        seen.append(request["evidence"])
        return {"proposals": []}

    policy = replace(
        load_policy(),
        max_replicas_per_run=1,
        max_input_chars=3000,
        max_event_chars=1000,
        max_batches_per_replica=2,
    )
    first = run_maintenance(remote, policy=policy, proposer=propose)
    second = run_maintenance(remote, policy=policy, proposer=propose)

    assert (first["batches"], second["batches"]) == (2, 2)
    assert first["pending_sequences"] > 0
    assert second["pending_sequences"] == 0
    assert run_maintenance(remote, policy=policy, proposer=propose)["status"] == "idle"
    items = [item for batch in seen for item in batch]
    pages = [item["payload"] for item in items if item["entity_kind"] == "page_version"]
    messages = [item["payload"] for item in items if item["entity_kind"] != "page_version"]
    assert [page["content"]["summary"] for page in pages] == ["second"]
    assert [message["kind"] for message in messages] == ["user"] * 6
    assert all(len(message["content"]) == 1000 and message["content_truncated"] for message in messages)
    assert {key for message in messages for key in message} <= {
        "kind", "content", "content_truncated", "harness", "workspace",
        "session_external_id", "source_created_at", "metadata",
    }
    with remote.connection() as conn:
        watermark = conn.execute("SELECT max(input_watermark) FROM remote_maintenance_runs").fetchone()[0]
    assert watermark == archived


def test_a_window_without_messages_advances_without_a_model_call(
    postgres_url: str,
    maintenance_url: str,
):
    from wikibricks_remote.maintenance import run_maintenance
    from wikibricks_remote.resources import load_policy

    remote = PostgresStore(maintenance_url)
    _archive_page(PostgresStore(postgres_url), maintenance_url, "tools-first")
    policy = replace(load_policy(), max_replicas_per_run=1)
    run_maintenance(remote, policy=policy, proposer=lambda *_: {"proposals": []})
    local = PostgresStore(postgres_url)
    local.ingest_session(
        SessionRecord(
            harness="omnigent",
            external_id="tools-only",
            user_id="u",
            events=[SessionEvent("t", "tool_result", "output")],
        )
    )
    sync_to_archive(local, maintenance_url)

    def fail(*_):
        raise AssertionError("no message to curate")

    result = run_maintenance(remote, policy=policy, proposer=fail)

    assert (result["batches"], result["no_changes"], result["pending_sequences"]) == (1, 1, 0)
    assert run_maintenance(remote, policy=policy, proposer=fail)["status"] == "idle"


def test_remote_maintenance_curates_from_hybrid_candidates(
    postgres_url: str,
    maintenance_url: str,
):
    from wikibricks_remote.maintenance import run_maintenance
    from wikibricks_remote.resources import load_policy
    from wikibricks_remote.search import CandidateSelection

    local = PostgresStore(postgres_url)
    remote = PostgresStore(maintenance_url)
    local.migrate()
    remote.migrate()
    local.clear_all()
    remote.clear_all()
    for path in ("topics/source", "topics/target"):
        local.write_page(
            path,
            path.rsplit("/", 1)[1].title(),
            {"summary": path, "body": f"Evidence for {path}."},
        )
    assert sync_to_archive(local, maintenance_url, drain=True)["acknowledged"] == 2
    captured = {}

    def candidates(_replica_id, _watermark, evidence):
        pages = []
        for item in evidence:
            payload = item["payload"]
            pages.append(
                {
                    "evidence_id": item["evidence_id"],
                    "path": payload["path"],
                    "title": payload["title"],
                    "page_type": payload["page_type"],
                    "content": payload["content"],
                    "tags": payload["tags"],
                    "source_ids": payload["source_ids"],
                    "base_version_id": item["version_id"],
                    "base_content_hash": item["payload_hash"],
                }
            )
        similarity = (
            {
                "query_evidence_id": pages[0]["evidence_id"],
                "query_document_id": str(uuid4()),
                "candidates": [
                    {
                        "path": pages[1]["path"],
                        "evidence_id": pages[1]["evidence_id"],
                        "vector_rank": 1,
                        "keyword_rank": 2,
                        "rrf_score": 0.03,
                    }
                ],
            },
        )
        return CandidateSelection("available", tuple(pages), similarity, 1, 1, 1)

    def propose(_system, request, _schema):
        captured.update(request)
        pages = {page["path"]: page for page in request["current_pages"]}
        return {
            "proposals": [
                {
                    "group": "relationship",
                    "operation": "add_link",
                    "path": "topics/source",
                    "title": None,
                    "page_type": None,
                    "summary": None,
                    "body": None,
                    "tags": [],
                    "source_ids": [],
                    "target_path": "topics/target",
                    "link_type": "related",
                    "evidence_ids": [
                        pages["topics/source"]["evidence_id"],
                        pages["topics/target"]["evidence_id"],
                    ],
                    "reason": "The archived pages describe related concepts.",
                    "risk_class": "low",
                }
            ]
        }

    result = run_maintenance(
        remote,
        policy=replace(load_policy(), max_replicas_per_run=1),
        proposer=propose,
        candidate_provider=candidates,
    )

    assert result["search_status"] == "available"
    assert len(captured["similarity_candidates"]) == 1
    with remote.connection() as conn:
        operation = conn.execute("SELECT operation FROM curation_patches").fetchone()[0]
    assert operation == "add_link"


def test_search_failure_does_not_advance_the_maintenance_watermark(
    postgres_url: str,
    maintenance_url: str,
):
    from wikibricks_remote.maintenance import run_maintenance
    from wikibricks_remote.resources import load_policy

    local = PostgresStore(postgres_url)
    remote = PostgresStore(maintenance_url)
    _archive_page(local, maintenance_url, "search-failure")

    def fail_search(_replica_id, _watermark, _evidence):
        raise RuntimeError("embedding endpoint failed")

    with pytest.raises(RuntimeError, match="embedding endpoint failed"):
        run_maintenance(
            remote,
            policy=replace(load_policy(), max_replicas_per_run=1),
            proposer=lambda *_args: {"proposals": []},
            candidate_provider=fail_search,
        )
    with remote.connection() as conn:
        runs = conn.execute("SELECT count(*) FROM remote_maintenance_runs").fetchone()[0]
        manifests = conn.execute("SELECT count(*) FROM curation_runs").fetchone()[0]
    assert (runs, manifests) == (0, 0)


def test_bundle_defines_one_bounded_weekly_serverless_wheel_job():
    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text())
    job_file = ROOT / "resources" / "wikibricks_remote.job.yml"
    job = yaml.safe_load(job_file.read_text())["resources"]["jobs"][
        "wikibricks_remote"
    ]

    assert bundle["variables"]["schedule_pause_status"]["default"] == "PAUSED"
    assert bundle["targets"]["personal"]["variables"][
        "schedule_pause_status"
    ] == "PAUSED"
    assert job["schedule"] == {
        "quartz_cron_expression": "0 0 4 ? * SUN",
        "timezone_id": "UTC",
        "pause_status": "${var.schedule_pause_status}",
    }
    assert job["max_concurrent_runs"] == 1
    assert job["timeout_seconds"] == 3600
    assert len(job["tasks"]) == 1
    task = job["tasks"][0]
    assert task["max_retries"] == 1
    assert "libraries" not in task
    assert task["python_wheel_task"] == {
        "package_name": "wikibricks",
        "entry_point": "wikibricks-remote-maintenance",
        "named_parameters": {
            "project": "${var.lakebase_project}",
            "branch": "${var.lakebase_branch}",
            "endpoint": "${var.lakebase_endpoint}",
            "database": "${var.lakebase_database}",
            "model-endpoint": "${var.model_endpoint}",
            "embedding-endpoint": "${var.embedding_endpoint}",
        },
    }
    assert job["environments"] == [
        {
            "environment_key": "remote",
            "spec": {
                "client": "4",
                "dependencies": [
                    "../dist/*.whl",
                    "databricks-sdk>=0.85.0",
                    "psycopg[binary]==3.2.13",
                ],
            },
        }
    ]
    assert json.loads(
        (
            ROOT
            / "src/wikibricks_remote/resources/curation-proposals.schema.json"
        ).read_text()
    )["type"] == "object"
