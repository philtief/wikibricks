"""Bounded archive selection and immutable curation-manifest publication."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from wikibricks.curation import build_manifest, create_patch, publish_manifest
from wikibricks.postgres_store import PostgresStore
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
_PROPOSAL_REQUIRED_FIELDS = {
    "group",
    "operation",
    "path",
    "title",
    "page_type",
    "summary",
    "body",
    "tags",
    "source_ids",
    "target_path",
    "evidence_ids",
    "reason",
    "risk_class",
}
_PROPOSAL_FIELDS = _PROPOSAL_REQUIRED_FIELDS | {"link_type"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


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
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT event_id, local_sequence, entity_kind, entity_id, version_id, "
        "payload_hash, payload "
        "FROM archive_events WHERE replica_id = %s AND local_sequence > %s "
        "AND local_sequence <= %s ORDER BY local_sequence LIMIT %s",
        (replica_id, after, through, policy.max_events_per_replica),
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
        encoded = _canonical_json(item)
        if used + len(encoded) > policy.max_input_chars:
            if evidence:
                break
            item["payload"] = {
                "truncated": True,
                "excerpt": encoded[: max(256, policy.max_input_chars // 2)],
            }
            encoded = _canonical_json(item)
        evidence.append(item)
        used += len(encoded)
    return evidence


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
        payload = dict(raw_payload)
        pages.append(
            {
                "evidence_id": f"archive-event:{event_id}",
                "path": payload["path"],
                "title": payload["title"],
                "page_type": payload["page_type"],
                "content": payload["content"],
                "tags": list(payload.get("tags") or []),
                "source_ids": list(payload.get("source_ids") or []),
                "base_version_id": str(version_id),
                "base_content_hash": content_hash,
            }
        )
    return pages


def _page_proposal(raw: dict[str, Any]) -> dict[str, Any]:
    summary = str(raw["summary"]).strip()
    body = str(raw["body"]).strip()
    return {
        "title": str(raw["title"]).strip(),
        "page_type": str(raw["page_type"]),
        "content": {"summary": summary, "body": body},
        "content_text": f"{summary}\n\n{body}".strip(),
        "tags": list(raw["tags"]),
        "source_ids": list(raw["source_ids"]),
        "parent_id": None,
        "chunk_index": None,
    }


def build_patches(
    raw_result: dict[str, Any],
    *,
    run_id: UUID,
    pages: list[dict[str, Any]],
    evidence_ids: set[str],
    policy: RemotePolicy,
) -> list[dict[str, Any]]:
    if set(raw_result) != {"proposals"} or not isinstance(raw_result["proposals"], list):
        raise ValueError("curator output must contain only a proposals array")
    proposals = raw_result["proposals"]
    if len(proposals) > policy.max_proposals_per_replica:
        raise ValueError("curator output exceeds the proposal limit")
    current = {page["path"]: page for page in pages}
    known_evidence = evidence_ids | {page["evidence_id"] for page in pages}
    group_positions: dict[str, int] = {}
    patches = []
    for raw in proposals:
        if (
            not isinstance(raw, dict)
            or not _PROPOSAL_REQUIRED_FIELDS <= set(raw)
            or not set(raw) <= _PROPOSAL_FIELDS
        ):
            raise ValueError("curation proposal fields do not match the schema")
        if not all(
            isinstance(raw[field], str) and raw[field].strip()
            for field in ("group", "operation", "path", "reason", "risk_class")
        ):
            raise ValueError("curation proposal identifiers and reason must be non-empty")
        if not all(
            isinstance(raw[field], list)
            and all(isinstance(value, str) and value for value in raw[field])
            for field in ("tags", "source_ids", "evidence_ids")
        ):
            raise ValueError("curation proposal lists must contain strings")
        operation = str(raw["operation"])
        path = str(raw["path"])
        if operation not in policy.allowed_operations:
            raise ValueError(f"operation is disabled by remote policy: {operation}")
        cited = list(raw["evidence_ids"])
        if not cited or not set(cited) <= known_evidence:
            raise ValueError("curation proposal cites unknown evidence")
        existing = current.get(path)
        link_type = raw.get("link_type")
        if operation == "add_link":
            if not isinstance(link_type, str) or link_type not in policy.allowed_link_types:
                raise ValueError(f"unsupported remote link type: {link_type}")
        elif link_type is not None:
            raise ValueError(f"link type is only valid for add_link: {operation}")
        if operation in {"create_page", "update_page"}:
            if not all(
                isinstance(raw[field], str) and raw[field].strip()
                for field in ("title", "page_type", "summary", "body")
            ):
                raise ValueError("page proposals require title, type, summary, and body")
            if raw["page_type"] not in {"entity", "concept", "synthesis", "comparison"}:
                raise ValueError(f"unsupported page type: {raw['page_type']}")
        if operation == "create_page":
            if existing or raw["target_path"] is not None:
                raise ValueError(f"create_page path already exists: {path}")
            proposal = _page_proposal(raw)
            base_version_id = None
            base_content_hash = None
        elif operation == "update_page":
            if not existing or raw["target_path"] is not None:
                raise ValueError(f"update_page path does not exist: {path}")
            proposal = _page_proposal(raw)
            base_version_id = existing["base_version_id"]
            base_content_hash = existing["base_content_hash"]
        elif operation == "add_link":
            target_path = raw["target_path"]
            if not existing or target_path not in current:
                raise ValueError(f"link paths do not exist: {path} -> {target_path}")
            proposal = {"target_path": target_path, "link_type": link_type}
            base_version_id = existing["base_version_id"]
            base_content_hash = existing["base_content_hash"]
        else:
            target_path = raw["target_path"]
            if not existing or target_path not in current:
                raise ValueError(f"cleanup paths do not exist: {path} -> {target_path}")
            proposal = {"target_path": target_path}
            base_version_id = existing["base_version_id"]
            base_content_hash = existing["base_content_hash"]
        group = str(raw["group"])
        position = group_positions.get(group, 0)
        group_positions[group] = position + 1
        group_id = uuid5(run_id, f"group:{group}")
        patch_id = uuid5(group_id, f"patch:{position}:{_canonical_json(raw)}")
        patches.append(
            create_patch(
                operation=operation,
                path=path,
                proposal=proposal,
                evidence_ids=cited,
                reason=str(raw["reason"]),
                base_version_id=base_version_id,
                base_content_hash=base_content_hash,
                patch_id=patch_id,
                group_id=group_id,
                position=position,
                risk_class=str(raw["risk_class"]),
            )
        )
    return patches


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
        with store.connection() as conn:
            evidence = _bounded_evidence(
                conn,
                replica_id=replica_id,
                after=previous_watermark,
                through=available_watermark,
                policy=policy,
            )
            if not evidence:
                continue
            watermark = int(evidence[-1]["sequence"])
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
        raw_result = proposer(load_prompt(), request, load_schema())
        patches = build_patches(
            raw_result,
            run_id=run_id,
            pages=pages,
            evidence_ids={item["evidence_id"] for item in evidence},
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
        result["replicas"] += 1
        result["proposals"] += len(patches)
        result["replica_ids"].append(str(replica_id))
    if not result["replicas"]:
        result["status"] = "idle"
    return result


__all__ = ["CandidateProvider", "build_patches", "run_maintenance"]
