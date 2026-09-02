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

    assert policy.max_events_per_replica == 200
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
    }
    assert repeated == {
        "status": "idle",
        "replicas": 0,
        "published_manifests": 0,
        "proposals": 0,
        "no_changes": 0,
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
        },
    }
    assert job["environments"] == [
        {
            "environment_key": "remote",
            "spec": {
                "client": "4",
                "dependencies": ["../dist/*.whl", "databricks-sdk>=0.85.0"],
            },
        }
    ]
    assert json.loads(
        (
            ROOT
            / "src/wikibricks_remote/resources/curation-proposals.schema.json"
        ).read_text()
    )["type"] == "object"
