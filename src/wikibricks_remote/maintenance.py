"""Bounded archive selection and immutable curation-manifest publication."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from wikibricks.curation import build_manifest, publish_manifest
from wikibricks.postgres_store import PostgresStore
from wikibricks_remote.proposals import _canonical_json, archive_page, build_patches, drop_miscited_groups
from wikibricks_remote.resources import (
    RemotePolicy,
    load_prompt,
    load_schema,
)
from wikibricks_remote.search import CandidateSelection

Proposer = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]
CandidateProvider = Callable[
    [UUID, int, list[dict[str, Any]]],
    CandidateSelection,
]
_UNKNOWN_REPLICA = UUID(int=0)


def _last_watermark(conn: Connection, replica_id: UUID) -> int:
    row = conn.execute(
        "SELECT max(input_watermark) FROM ("
        "SELECT input_watermark FROM curation_runs WHERE replica_id = %s "
        "UNION ALL SELECT input_watermark FROM remote_maintenance_runs "
        "WHERE replica_id = %s) processed",
        (replica_id, replica_id),
    ).fetchone()
    return int(row[0] or 0)


def _candidate_replicas(
    conn: Connection,
    *,
    maximum: int,
) -> list[tuple[UUID, int, int]]:
    rows = conn.execute(
        "SELECT replica_id, max(local_sequence) FROM archive_events "
        "WHERE replica_id <> %s GROUP BY replica_id ORDER BY replica_id",
        (_UNKNOWN_REPLICA,),
    ).fetchall()
    candidates = []
    for raw_replica_id, raw_watermark in rows:
        replica_id = UUID(str(raw_replica_id))
        last = _last_watermark(conn, replica_id)
        available = int(raw_watermark)
        if available > last:
            candidates.append((replica_id, last, available))
        if len(candidates) == maximum:
            break
    return candidates


def _bounded_evidence(
    conn: Connection,
    *,
    replica_id: UUID,
    after: int,
    through: int,
    policy: RemotePolicy,
) -> tuple[list[dict[str, Any]], int]:
    """Return the next batch of messages and newest page versions, and the watermark it reaches.

    Tool calls, tool output, lifecycle events and receipts are most of the archive and carry
    no curated knowledge. The batch skips them, and so does the watermark.
    """
    rows = conn.execute(
        "SELECT event_id, local_sequence, entity_kind, entity_id, version_id, "
        "payload_hash, payload "
        "FROM archive_events e WHERE replica_id = %s AND local_sequence > %s "
        "AND local_sequence <= %s AND ("
        "(entity_kind = 'session_event_version' AND payload->>'kind' = ANY(%s)) "
        "OR (entity_kind = 'page_version' AND NOT EXISTS (SELECT 1 FROM archive_events newer "
        "WHERE newer.replica_id = e.replica_id AND newer.entity_kind = 'page_version' "
        "AND newer.payload->>'path' = e.payload->>'path' "
        "AND newer.local_sequence > e.local_sequence AND newer.local_sequence <= %s))) "
        "ORDER BY local_sequence LIMIT %s",
        (replica_id, after, through, list(_MESSAGE_KINDS), through, policy.max_events_per_replica),
    ).fetchall()
    evidence: list[dict[str, Any]] = []
    used = 0
    for row in rows:
        item = {
            "evidence_id": f"archive-event:{row[0]}",
            "sequence": int(row[1]),
            "entity_kind": row[2],
            "entity_id": str(row[3]),
            "version_id": str(row[4]),
            "payload_hash": row[5],
            "payload": dict(row[6]),
        }
        encoded = _canonical_json(_model_evidence(item, policy.max_event_chars))
        if used + len(encoded) > policy.max_input_chars:
            if evidence:
                return evidence, evidence[-1]["sequence"]
            item["payload"] = {
                "truncated": True,
                "excerpt": encoded[: max(256, policy.max_input_chars // 2)],
            }
            encoded = _canonical_json(item)
        evidence.append(item)
        used += len(encoded)
    if len(rows) == policy.max_events_per_replica:
        return evidence, int(rows[-1][1])
    return evidence, through


def _current_pages(
    conn: Connection,
    *,
    replica_id: UUID,
    watermark: int,
    maximum: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "WITH ranked AS (SELECT event_id, version_id, payload_hash, payload, "
        "row_number() OVER (PARTITION BY payload->>'path' ORDER BY local_sequence DESC) AS rank "
        "FROM archive_events WHERE replica_id = %s AND entity_kind = 'page_version' "
        "AND local_sequence <= %s) SELECT event_id, version_id, payload_hash, payload "
        "FROM ranked WHERE rank = 1 ORDER BY payload->>'path' LIMIT %s",
        (replica_id, watermark, maximum),
    ).fetchall()
    pages = []
    for event_id, version_id, content_hash, raw_payload in rows:
        pages.append(
            archive_page(event_id, version_id, content_hash, raw_payload)
        )
    return pages


def _record_run(
    store: PostgresStore,
    *,
    run_id: UUID,
    replica_id: UUID,
    watermark: int,
    input_digest: str,
    status: str,
    proposal_count: int,
    manifest_hash: str | None,
    report: dict[str, Any] | None = None,
) -> None:
    report = {"proposal_count": proposal_count, **(report or {})}
    with store.connection() as conn, conn.transaction():
        conn.execute(
            "INSERT INTO remote_maintenance_runs "
            "(run_id, replica_id, input_watermark, input_digest, status, proposal_count, "
            "manifest_hash, report) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (replica_id, input_watermark) DO NOTHING",
            (
                run_id,
                replica_id,
                watermark,
                input_digest,
                status,
                proposal_count,
                manifest_hash,
                Jsonb(report),
            ),
        )
        stored = conn.execute(
            "SELECT input_digest, status, proposal_count, manifest_hash "
            "FROM remote_maintenance_runs WHERE replica_id = %s AND input_watermark = %s",
            (replica_id, watermark),
        ).fetchone()
        if stored != (input_digest, status, proposal_count, manifest_hash):
            raise RuntimeError("remote maintenance run conflicts with its archive watermark")


# The model cites evidence by ID. Any other UUID beside it (an event's entity or version,
# a page's base version, a search document) invites it to cite the wrong field.
_UNCITABLE_EVIDENCE = frozenset({"entity_id", "version_id", "payload_hash"})
_UNCITABLE_PAGE = frozenset({"base_version_id", "base_content_hash"})
_MESSAGE_KINDS = ("user", "assistant")
# A message as the model reads it: its text and where it came from.
_MODEL_MESSAGE_FIELDS = frozenset(
    {"kind", "content", "harness", "workspace", "session_external_id", "source_created_at", "metadata"}
)


def _model_evidence(item: dict[str, Any], max_event_chars: int) -> dict[str, Any]:
    view = {key: value for key, value in item.items() if key not in _UNCITABLE_EVIDENCE}
    if item["entity_kind"] == "session_event_version":
        payload = {key: value for key, value in item["payload"].items() if key in _MODEL_MESSAGE_FIELDS}
        content = str(payload.get("content") or "")
        if len(content) > max_event_chars:
            payload.update(content=content[:max_event_chars], content_truncated=True)
        view["payload"] = payload
    return view


def _model_view(request: dict[str, Any], max_event_chars: int) -> dict[str, Any]:
    """Return the request as the model sees it: only IDs it may cite."""
    view = {key: value for key, value in request.items() if key != "replica_id"}
    view["evidence"] = [_model_evidence(item, max_event_chars) for item in request["evidence"]]
    view["current_pages"] = [
        {key: value for key, value in page.items() if key not in _UNCITABLE_PAGE}
        for page in request["current_pages"]
    ]
    view["similarity_candidates"] = [
        {key: value for key, value in group.items() if key != "query_document_id"}
        for group in request["similarity_candidates"]
    ]
    return view


def run_maintenance(
    store: PostgresStore,
    *,
    policy: RemotePolicy,
    proposer: Proposer,
    candidate_provider: CandidateProvider | None = None,
) -> dict[str, Any]:
    store.migrate()
    with store.connection() as conn:
        candidates = _candidate_replicas(conn, maximum=policy.max_replicas_per_run)
    result: dict[str, Any] = {
        "status": "idle" if not candidates else "completed",
        "replicas": 0,
        "published_manifests": 0,
        "proposals": 0,
        "no_changes": 0,
        "dropped_groups": 0,
        "batches": 0,
        "pending_sequences": 0,
        "replica_ids": [],
    }
    if candidate_provider is not None:
        result.update(
            search_status="unavailable",
            projected_documents=0,
            embedded_documents=0,
            search_queries=0,
            vector_matches=0,
            keyword_matches=0,
        )
    for replica_id, previous_watermark, available_watermark in candidates:
        for _ in range(policy.max_batches_per_replica):
            with store.connection() as conn:
                evidence, watermark = _bounded_evidence(
                    conn,
                    replica_id=replica_id,
                    after=previous_watermark,
                    through=available_watermark,
                    policy=policy,
                )
                pages = _current_pages(
                    conn,
                    replica_id=replica_id,
                    watermark=watermark,
                    maximum=policy.max_current_pages,
                )
            similarity_candidates: list[dict[str, Any]] = []
            search_report: dict[str, Any] = {"search_status": "disabled"}
            if candidate_provider is not None:
                selection = candidate_provider(replica_id, watermark, evidence)
                if not isinstance(selection, CandidateSelection):
                    raise TypeError("candidate provider must return CandidateSelection")
                if selection.search_status not in {"available", "unavailable"}:
                    raise ValueError("candidate provider returned an invalid search status")
                search_report = {
                    "search_status": selection.search_status,
                    "projected_documents": selection.projected_documents,
                    "embedded_documents": selection.embedded_documents,
                    "search_queries": selection.query_count,
                    "vector_matches": selection.vector_matches,
                    "keyword_matches": selection.keyword_matches,
                }
                if selection.search_status == "available":
                    pages = list(selection.pages)
                    similarity_candidates = list(selection.similarity_candidates)
                    result["search_status"] = "available"
                for field in (
                    "projected_documents",
                    "embedded_documents",
                    "search_queries",
                    "vector_matches",
                    "keyword_matches",
                ):
                    result[field] += int(search_report[field])
            request = {
                "replica_id": str(replica_id),
                "previous_watermark": previous_watermark,
                "input_watermark": watermark,
                "current_pages": pages,
                "evidence": evidence,
                "similarity_candidates": similarity_candidates,
            }
            input_digest = hashlib.sha256(_canonical_json(request).encode()).hexdigest()
            run_id = uuid5(
                replica_id,
                f"wikibricks:remote-maintenance:{watermark}:{input_digest}",
            )
            # A window of tool output alone still moves the watermark, without a model call.
            raw_result = (
                proposer(load_prompt(), _model_view(request, policy.max_event_chars), load_schema())
                if evidence
                else {"proposals": []}
            )
            evidence_ids = {item["evidence_id"] for item in evidence}
            result["dropped_groups"] += drop_miscited_groups(
                raw_result,
                pages=pages,
                evidence_ids=evidence_ids,
            )
            patches = build_patches(
                raw_result,
                run_id=run_id,
                pages=pages,
                evidence_ids=evidence_ids,
                policy=policy,
            )
            manifest_hash = None
            status = "no_changes"
            if patches:
                manifest = build_manifest(
                    replica_id=replica_id,
                    input_watermark=watermark,
                    patches=patches,
                    run_id=run_id,
                )
                publish_manifest(store, manifest)
                manifest_hash = manifest["manifest_hash"]
                status = "published"
                result["published_manifests"] += 1
            else:
                result["no_changes"] += 1
            _record_run(
                store,
                run_id=run_id,
                replica_id=replica_id,
                watermark=watermark,
                input_digest=input_digest,
                status=status,
                proposal_count=len(patches),
                manifest_hash=manifest_hash,
                report=search_report,
            )
            result["batches"] += 1
            result["proposals"] += len(patches)
            previous_watermark = watermark
            if watermark >= available_watermark:
                break
        result["pending_sequences"] += available_watermark - previous_watermark
        result["replicas"] += 1
        result["replica_ids"].append(str(replica_id))
    if not result["replicas"]:
        result["status"] = "idle"
    return result


__all__ = ["CandidateProvider", "build_patches", "run_maintenance"]
