#!/usr/bin/env python3
"""Run an isolated WikiBricks acceptance scenario against staging Lakebase."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from wikibricks.automation import run_remote_cycle
from wikibricks.config import load_config
from wikibricks.curation import (
    build_manifest,
    create_patch,
    get_or_create_replica_id,
    list_conflicts,
    publish_manifest,
)
from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.postgres_store import PostgresStore
from wikibricks.remote.lakebase import LakebaseTarget, sync_to_archive
from wikibricks.storage.sqlite_store import SQLiteStore
from wikibricks_remote.search import LakebaseHybridSearch

_TABLES = (
    "archive_batch_events",
    "archive_batches",
    "archive_events",
    "curation_patches",
    "curation_runs",
    "remote_maintenance_runs",
    "remote_search_documents",
)
_TOPICS = (
    "OAuth token renewal",
    "Lakeflow checkpoint recovery",
    "Lakebase connection pooling",
    "Unity Catalog permission diagnosis",
    "agent tool timeout handling",
    "incremental ingestion watermarking",
    "vector index maintenance",
    "MCP server lifecycle",
    "session transcript compaction",
    "remote curation conflict handling",
)


@dataclass(frozen=True, slots=True)
class PageFixture:
    path: str
    title: str
    content: dict[str, str]
    tags: tuple[str, ...]
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcceptanceCorpus:
    pages: tuple[PageFixture, ...]
    session: SessionRecord
    expected_partners: dict[str, str]


def _long_text(prefix: str, minimum: int) -> str:
    sentence = (
        f"{prefix}. Preserve the exact evidence, use bounded chunks, and keep "
        "the active local page authoritative when remote maintenance is unavailable. "
    )
    return (sentence * ((minimum // len(sentence)) + 1))[:minimum]


def build_corpus(
    run_label: str,
    *,
    page_count: int = 100,
    pair_count: int = 10,
    long_event_chars: int = 24_000,
) -> AcceptanceCorpus:
    """Build stable labeled pairs followed by noisy distractor pages."""
    if page_count < pair_count * 2 or pair_count < 1:
        raise ValueError("page count must accommodate every labeled pair")
    if long_event_chars < 12_000:
        raise ValueError("long session events must exercise remote chunking")
    safe_label = re.sub(r"[^a-z0-9-]", "-", run_label.lower()).strip("-")
    if not safe_label:
        raise ValueError("run label must contain a letter or number")

    pages: list[PageFixture] = []
    partners: dict[str, str] = {}
    for number in range(pair_count):
        topic = _TOPICS[number % len(_TOPICS)]
        marker = f"wbpair{number:02d}"
        relation = "duplicate" if number < 2 else "related"
        left = f"acceptance/{safe_label}/pair-{number:02d}-a"
        right = f"acceptance/{safe_label}/pair-{number:02d}-b"
        partners[left] = right
        partners[right] = left
        for side, path in (("A", left), ("B", right)):
            role = (
                "an equivalent copy of the same operational guidance"
                if relation == "duplicate"
                else f"the {side} perspective with distinct operational ownership"
            )
            pages.append(
                PageFixture(
                    path=path,
                    title=f"{topic} {side}",
                    content={
                        "summary": f"{topic} retrieval benchmark {marker}.",
                        "body": (
                            f"This page is {role}. The shared diagnostic marker is "
                            f"{marker}. Validate symptoms, preserve evidence, and record "
                            "the recovery decision for future agent harnesses."
                        ),
                    },
                    tags=("acceptance", relation, marker),
                    source_ids=(f"acceptance:{safe_label}:pair:{number:02d}",),
                )
            )

    subjects = (
        "deployment rollback",
        "schema migration",
        "query optimization",
        "credential rotation",
        "stream recovery",
        "tool registration",
        "cache invalidation",
        "session import",
    )
    for number in range(page_count - len(pages)):
        subject = subjects[number % len(subjects)]
        pages.append(
            PageFixture(
                path=f"acceptance/{safe_label}/noise-{number:03d}",
                title=f"{subject.title()} note {number:03d}",
                content={
                    "summary": f"Independent {subject} note {number:03d}.",
                    "body": (
                        "This distractor shares operational terms such as evidence, "
                        "recovery, agent, local memory, and maintenance, but it owns a "
                        f"different decision. Noise identifier wbnoise{number:03d}."
                    ),
                },
                tags=("acceptance", "noise", subject.replace(" ", "-")),
                source_ids=(f"acceptance:{safe_label}:noise:{number:03d}",),
            )
        )

    session = SessionRecord(
        harness="omnigent",
        external_id=f"staging-acceptance-{safe_label}",
        user_id="staging-acceptance",
        agent="codex",
        workspace="wikibricks-staging",
        metadata={"title": "WikiBricks staging acceptance long session"},
        events=[
            SessionEvent(
                "user-0",
                "user",
                _long_text("Long user session evidence", long_event_chars),
            ),
            SessionEvent(
                "assistant-0",
                "assistant",
                _long_text("Long assistant session evidence", long_event_chars),
            ),
        ],
    )
    return AcceptanceCorpus(tuple(pages), session, partners)


def retrieval_metrics(
    similarity: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    evidence_to_path: dict[str, str],
    expected_partners: dict[str, str],
    maximum_rank: int,
) -> dict[str, int | float]:
    """Calculate labeled page-pair recall from hybrid candidate groups."""
    if maximum_rank < 1:
        raise ValueError("maximum rank must be positive")
    evaluated = hybrid_hits = vector_hits = keyword_hits = 0
    for group in similarity:
        source = evidence_to_path.get(str(group["query_evidence_id"]))
        if source not in expected_partners:
            continue
        evaluated += 1
        expected = expected_partners[source]
        candidates = list(group.get("candidates") or [])[:maximum_rank]
        match = next((item for item in candidates if item.get("path") == expected), None)
        if match is None:
            continue
        hybrid_hits += 1
        vector_rank = match.get("vector_rank")
        keyword_rank = match.get("keyword_rank")
        vector_hits += isinstance(vector_rank, int) and vector_rank <= maximum_rank
        keyword_hits += isinstance(keyword_rank, int) and keyword_rank <= maximum_rank
    if not evaluated:
        raise AssertionError("the hybrid result contained no labeled queries")

    def recall(hits: int) -> float:
        return round(hits / evaluated, 3)

    return {
        "evaluated_queries": evaluated,
        f"hybrid_recall_at_{maximum_rank}": recall(hybrid_hits),
        f"vector_recall_at_{maximum_rank}": recall(vector_hits),
        f"keyword_recall_at_{maximum_rank}": recall(keyword_hits),
    }


def seed_corpus(local: SQLiteStore, corpus: AcceptanceCorpus) -> None:
    """Place long evidence inside the bounded window without moving labeled queries."""
    priority_pages = len(corpus.expected_partners)
    for page in corpus.pages[:priority_pages]:
        local.write_page(
            page.path,
            page.title,
            page.content,
            tags=list(page.tags),
            source_ids=list(page.source_ids),
        )
    local.ingest_session(corpus.session)
    for page in corpus.pages[priority_pages:]:
        local.write_page(
            page.path,
            page.title,
            page.content,
            tags=list(page.tags),
            source_ids=list(page.source_ids),
        )


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _assert_staging_job(workspace: Any, job_id: int) -> dict[str, Any]:
    job = workspace.jobs.get(job_id=job_id)
    settings = job.settings
    if settings is None or settings.schedule is None:
        raise AssertionError("the staging job has no schedule")
    if _enum_value(settings.schedule.pause_status) != "PAUSED":
        raise AssertionError("the staging job schedule must remain paused")
    tasks = list(settings.tasks or [])
    if len(tasks) != 1 or tasks[0].python_wheel_task is None:
        raise AssertionError("the staging job must contain one wheel task")
    parameters = dict(tasks[0].python_wheel_task.named_parameters or {})
    if parameters.get("branch") != "staging":
        raise AssertionError("the selected job does not target the staging branch")
    return {
        "job_id": job_id,
        "name": settings.name,
        "schedule": {
            "cron": settings.schedule.quartz_cron_expression,
            "timezone": settings.schedule.timezone_id,
            "state": _enum_value(settings.schedule.pause_status),
        },
    }


def _run_job(workspace: Any, job_id: int, timeout_minutes: int) -> int:
    waiter = workspace.jobs.run_now(job_id=job_id, idempotency_token=str(uuid4()))
    run = waiter.result(timeout=timedelta(minutes=timeout_minutes))
    state = run.state
    result = _enum_value(state.result_state) if state is not None else ""
    if result != "SUCCESS":
        raise AssertionError(f"staging job did not succeed: {result or 'unknown'}")
    if run.run_id is None:
        raise AssertionError("staging job returned no run ID")
    return int(run.run_id)


def _global_counts(remote: PostgresStore) -> dict[str, int]:
    with remote.connection() as conn:
        return {
            table: int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in _TABLES
        }


def _replica_counts(remote: PostgresStore, replica_id: UUID) -> dict[str, int]:
    statements = {
        "archive_batch_events": (
            "SELECT count(*) FROM archive_batch_events b JOIN archive_events e "
            "ON e.event_id = b.event_id WHERE e.replica_id = %s"
        ),
        "archive_batches": "SELECT count(*) FROM archive_batches WHERE replica_id = %s",
        "archive_events": "SELECT count(*) FROM archive_events WHERE replica_id = %s",
        "curation_patches": (
            "SELECT count(*) FROM curation_patches p JOIN curation_runs r "
            "ON r.run_id = p.run_id WHERE r.replica_id = %s"
        ),
        "curation_runs": "SELECT count(*) FROM curation_runs WHERE replica_id = %s",
        "remote_maintenance_runs": (
            "SELECT count(*) FROM remote_maintenance_runs WHERE replica_id = %s"
        ),
        "remote_search_documents": (
            "SELECT count(*) FROM remote_search_documents WHERE replica_id = %s"
        ),
    }
    with remote.connection() as conn:
        return {
            table: int(conn.execute(sql, (replica_id,)).fetchone()[0])
            for table, sql in statements.items()
        }


def _search_state(remote: PostgresStore, replica_id: UUID) -> dict[str, int]:
    with remote.connection() as conn:
        row = conn.execute(
            "SELECT count(*), count(embedding), "
            "coalesce(max(vector_dims(embedding)), 0) "
            "FROM remote_search_documents WHERE replica_id = %s",
            (replica_id,),
        ).fetchone()
    return {
        "documents": int(row[0]),
        "embedded_documents": int(row[1]),
        "embedding_dimensions": int(row[2]),
    }


def _archive_evidence(
    remote: PostgresStore,
    replica_id: UUID,
) -> tuple[int, list[dict[str, Any]], dict[str, str]]:
    with remote.connection() as conn:
        rows = conn.execute(
            "SELECT event_id, local_sequence, entity_kind, entity_id, version_id, "
            "payload_hash, payload FROM archive_events WHERE replica_id = %s "
            "ORDER BY local_sequence",
            (replica_id,),
        ).fetchall()
    evidence = []
    paths = {}
    for row in rows:
        item = {
            "event_id": str(row[0]),
            "evidence_id": f"archive-event:{row[0]}",
            "sequence": int(row[1]),
            "entity_kind": str(row[2]),
            "entity_id": str(row[3]),
            "version_id": str(row[4]),
            "payload_hash": str(row[5]),
            "payload": dict(row[6]),
        }
        evidence.append(item)
        if item["entity_kind"] == "page_version":
            paths[item["evidence_id"]] = str(item["payload"]["path"])
    watermark = max((int(item["sequence"]) for item in evidence), default=0)
    return watermark, evidence, paths


def _publish_keep_local_probe(
    local: SQLiteStore,
    remote: PostgresStore,
    replica_id: UUID,
    watermark: int,
    evidence_id: str,
    path: str,
) -> str:
    state = local.current_page_state(path)
    if state is None:
        raise AssertionError(f"missing local probe page: {path}")
    patch = create_patch(
        operation="update_page",
        path=path,
        base_version_id=state["version_id"],
        base_content_hash=state["content_hash"],
        proposal={
            "title": state["title"],
            "page_type": state["page_type"],
            "content": {
                "summary": "Remote staging proposal.",
                "body": "This content must not replace a newer local decision.",
            },
            "content_text": (
                "Remote staging proposal. This content must not replace a newer "
                "local decision."
            ),
            "tags": list(state["tags"]),
            "source_ids": list(state["source_ids"] or []),
            "parent_id": state["parent_id"],
            "chunk_index": state["chunk_index"],
        },
        evidence_ids=[evidence_id],
        reason="Verify automatic keep-local conflict resolution in staging.",
    )
    manifest = build_manifest(
        replica_id=replica_id,
        input_watermark=watermark,
        patches=[patch],
    )
    publish_manifest(remote, manifest)
    return str(manifest["run_id"])


def _cleanup_replica(remote: PostgresStore, replica_id: UUID) -> dict[str, int]:
    deleted = {}
    statements = (
        ("remote_search_documents", "DELETE FROM remote_search_documents WHERE replica_id = %s"),
        ("curation_runs", "DELETE FROM curation_runs WHERE replica_id = %s"),
        (
            "remote_maintenance_runs",
            "DELETE FROM remote_maintenance_runs WHERE replica_id = %s",
        ),
        ("archive_batches", "DELETE FROM archive_batches WHERE replica_id = %s"),
        ("archive_events", "DELETE FROM archive_events WHERE replica_id = %s"),
    )
    with remote.connection() as conn, conn.transaction():
        for table, sql in statements:
            deleted[table] = int(conn.execute(sql, (replica_id,)).rowcount)
    return deleted


def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    if args.branch != "staging":
        raise ValueError("this acceptance runner may target only the staging branch")
    from databricks.sdk import WorkspaceClient

    workspace = WorkspaceClient(profile=args.profile)
    job = _assert_staging_job(workspace, args.job_id)
    target = LakebaseTarget(
        project=args.project,
        branch=args.branch,
        endpoint=args.endpoint,
        database=args.database,
        profile=args.profile,
    )
    remote = PostgresStore(target.fresh_database_url())
    remote.migrate()
    baseline = _global_counts(remote)
    run_label = uuid4().hex[:12]
    corpus = build_corpus(
        run_label,
        page_count=args.pages,
        pair_count=args.pairs,
        long_event_chars=args.long_event_chars,
    )
    replica_id: UUID | None = None
    report: dict[str, Any] = {"job": job, "run_label": run_label}
    try:
        with tempfile.TemporaryDirectory(prefix="wikibricks-staging-") as directory:
            database_path = Path(directory) / "wikibricks.db"
            local = SQLiteStore(database_path)
            local.migrate()
            seed_corpus(local, corpus)
            replica_id = get_or_create_replica_id(local)
            if any(_replica_counts(remote, replica_id).values()):
                raise AssertionError("new acceptance replica already exists in staging")

            archive = sync_to_archive(
                local,
                target.fresh_database_url(),
                limit=200,
                drain=True,
            )
            expected_events = len(corpus.pages) + len(corpus.session.events)
            if archive["acknowledged"] != expected_events or local.outbox_count() != 0:
                raise AssertionError("the acceptance corpus was not fully archived")

            first_run = _run_job(workspace, args.job_id, args.timeout_minutes)
            watermark, evidence, evidence_to_path = _archive_evidence(remote, replica_id)
            search_state = _search_state(remote, replica_id)
            if search_state["documents"] < expected_events:
                raise AssertionError("remote search did not project the full corpus")
            if search_state["embedded_documents"] != search_state["documents"]:
                raise AssertionError("remote search left acceptance embeddings incomplete")
            if search_state["embedding_dimensions"] != args.embedding_dimensions:
                raise AssertionError("remote search produced the wrong embedding dimension")

            search = LakebaseHybridSearch(
                remote,
                embedding_model=args.embedding_endpoint,
                embedding_dimension=args.embedding_dimensions,
            )
            selection = search.candidates(
                replica_id,
                watermark,
                evidence,
                maximum_queries=50,
                pages_per_query=10,
            )
            metrics = retrieval_metrics(
                selection.similarity_candidates,
                evidence_to_path=evidence_to_path,
                expected_partners=corpus.expected_partners,
                maximum_rank=10,
            )
            thresholds = {
                "hybrid_recall_at_10": args.minimum_hybrid_recall,
                "vector_recall_at_10": args.minimum_vector_recall,
                "keyword_recall_at_10": args.minimum_keyword_recall,
            }
            for metric, minimum in thresholds.items():
                if float(metrics[metric]) < minimum:
                    raise AssertionError(
                        f"{metric}={metrics[metric]} is below the threshold {minimum}"
                    )

            before_repeat = _replica_counts(remote, replica_id)
            second_run = _run_job(workspace, args.job_id, args.timeout_minutes)
            after_repeat = _replica_counts(remote, replica_id)
            if after_repeat != before_repeat:
                raise AssertionError("the repeated job changed an already processed replica")

            probe_path = next(iter(corpus.expected_partners))
            probe_evidence = next(
                evidence_id
                for evidence_id, path in evidence_to_path.items()
                if path == probe_path
            )
            keep_local_run = _publish_keep_local_probe(
                local,
                remote,
                replica_id,
                watermark,
                probe_evidence,
                probe_path,
            )
            local.write_page(
                probe_path,
                "Local staging decision",
                {
                    "summary": "Local decision wins.",
                    "body": "This newer local content must survive remote maintenance.",
                },
                tags=["acceptance", "local"],
                source_ids=[f"acceptance:{run_label}:local"],
            )
            config = replace(
                load_config(home=directory, environ={}),
                database_path=database_path,
                sync_profile=args.profile,
                sync_project=args.project,
                sync_branch=args.branch,
                sync_endpoint=args.endpoint,
                sync_database=args.database,
                sync_apply_policy="safe",
                sync_batch_size=200,
            )
            cycle = run_remote_cycle(
                local,
                config,
                remote_url_factory=lambda _target: target.fresh_database_url(),
            )
            page = local.read_page(probe_path)
            if keep_local_run not in cycle["kept_local_runs"]:
                raise AssertionError("the divergent local page was not recorded as keep_local")
            if page is None or page["title"] != "Local staging decision":
                raise AssertionError("remote maintenance replaced the divergent local page")
            if list_conflicts(local):
                raise AssertionError("the keep-local conflict remained unresolved")

            report.update(
                replica_id=str(replica_id),
                corpus={
                    "pages": len(corpus.pages),
                    "labeled_pairs": len(corpus.expected_partners) // 2,
                    "session_events": len(corpus.session.events),
                    "long_event_chars": args.long_event_chars,
                },
                archive=archive,
                first_job_run=first_run,
                repeated_job_run=second_run,
                search=search_state,
                retrieval=metrics,
                idempotent_repeat=True,
                keep_local_run=keep_local_run,
                keep_local_verified=True,
            )
    finally:
        if replica_id is not None:
            report["deleted"] = _cleanup_replica(remote, replica_id)
            remaining = _replica_counts(remote, replica_id)
            if any(remaining.values()):
                raise AssertionError(f"acceptance cleanup left replica rows: {remaining}")
            after_cleanup = _global_counts(remote)
            if after_cleanup != baseline:
                raise AssertionError(
                    f"staging baseline changed: before={baseline}, after={after_cleanup}"
                )
            report["cleanup_verified"] = True
            report["staging_baseline"] = after_cleanup
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--job-id", required=True, type=int)
    parser.add_argument("--project", default="wikibricks")
    parser.add_argument("--branch", default="staging")
    parser.add_argument("--endpoint", default="primary")
    parser.add_argument("--database", default="wikibricks")
    parser.add_argument("--embedding-endpoint", default="databricks-gte-large-en")
    parser.add_argument("--embedding-dimensions", default=1024, type=int)
    parser.add_argument("--pages", default=100, type=int)
    parser.add_argument("--pairs", default=10, type=int)
    parser.add_argument("--long-event-chars", default=24_000, type=int)
    parser.add_argument("--minimum-hybrid-recall", default=0.9, type=float)
    parser.add_argument("--minimum-vector-recall", default=0.5, type=float)
    parser.add_argument("--minimum-keyword-recall", default=0.8, type=float)
    parser.add_argument("--timeout-minutes", default=30, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    report = run_acceptance(build_parser().parse_args(argv))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
