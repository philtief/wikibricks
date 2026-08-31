from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from wikibricks.maintenance import initialize_database
from wikibricks.postgres_store import PostgresStore
from wikibricks.remote.lakebase import sync_to_archive

ROOT = Path(__file__).parents[1]


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
        "retarget_links",
        "add_alias",
        "supersede_page",
    )
    assert "immutable manifest" in prompt


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


def test_bundle_defines_one_bounded_weekly_serverless_wheel_job():
    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text())
    job_file = ROOT / "resources" / "wikibricks_remote.job.yml"
    job = yaml.safe_load(job_file.read_text())["resources"]["jobs"][
        "wikibricks_remote"
    ]

    assert bundle["variables"]["schedule_pause_status"]["default"] == "PAUSED"
    assert bundle["targets"]["personal"]["variables"][
        "schedule_pause_status"
    ] == "UNPAUSED"
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
