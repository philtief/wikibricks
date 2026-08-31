"""Immutable remote curation patches with guarded local application."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from wikibricks.postgres_store import PostgresStore, page_content_hash

CURATION_SCHEMA_VERSION = 1
_HASH = re.compile(r"^[0-9a-f]{64}$")
_OPERATIONS = {
    "create_page",
    "update_page",
    "retarget_links",
    "add_alias",
    "supersede_page",
}
_CLEANUP_OPERATIONS = {"retarget_links", "add_alias", "supersede_page"}
_RISK_CLASSES = {"low", "medium", "high"}
_PAGE_FIELDS = {
    "title",
    "page_type",
    "content",
    "content_text",
    "tags",
    "source_ids",
    "parent_id",
    "chunk_index",
}
_SYNC_LOCK_KEY = "wikibricks:curation-sync"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _validate_path(path: str) -> None:
    parts = path.split("/")
    if not path or path.startswith("/") or len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid wiki page path: {path!r}")


def _proposal_hash(operation: str, proposal: dict[str, Any]) -> str:
    if operation in {"create_page", "update_page"}:
        if set(proposal) != _PAGE_FIELDS:
            missing = sorted(_PAGE_FIELDS - set(proposal))
            extra = sorted(set(proposal) - _PAGE_FIELDS)
            raise ValueError(f"page proposal fields do not match contract; missing={missing}, extra={extra}")
        if not isinstance(proposal["content"], dict):
            raise ValueError("page proposal content must be an object")
        return page_content_hash(
            title=proposal["title"],
            page_type=proposal["page_type"],
            content=proposal["content"],
            content_text=proposal["content_text"],
            tags=proposal["tags"],
            source_ids=proposal["source_ids"],
            parent_id=proposal["parent_id"],
            chunk_index=proposal["chunk_index"],
        )
    if set(proposal) != {"target_path"}:
        raise ValueError(f"{operation} proposal must contain only target_path")
    _validate_path(str(proposal["target_path"]))
    return _content_hash(proposal)


def create_patch(
    *,
    operation: str,
    path: str,
    proposal: dict[str, Any],
    evidence_ids: list[str],
    reason: str,
    base_version_id: str | UUID | None = None,
    base_content_hash: str | None = None,
    patch_id: UUID | None = None,
    group_id: UUID | None = None,
    position: int = 0,
    risk_class: str = "low",
) -> dict[str, Any]:
    if operation not in _OPERATIONS:
        raise ValueError(f"unsupported curation operation: {operation}")
    _validate_path(path)
    if position < 0:
        raise ValueError("patch position cannot be negative")
    if risk_class not in _RISK_CLASSES:
        raise ValueError(f"unsupported risk class: {risk_class}")
    if operation in _CLEANUP_OPERATIONS and risk_class != "high":
        raise ValueError(f"{operation} must be classified as high risk")
    if not evidence_ids or any(not value for value in evidence_ids):
        raise ValueError("curation patch requires evidence IDs")
    if not reason.strip():
        raise ValueError("curation patch requires a reason")
    if operation == "create_page":
        if base_version_id is not None or base_content_hash is not None:
            raise ValueError("create_page cannot have a base version")
    elif base_version_id is None or not base_content_hash or not _HASH.fullmatch(base_content_hash):
        raise ValueError(f"{operation} requires a base version ID and content hash")
    if base_version_id is not None:
        UUID(str(base_version_id))
    proposal_copy = json.loads(_canonical_json(proposal))
    return {
        "patch_id": str(patch_id or uuid4()),
        "group_id": str(group_id or uuid4()),
        "position": position,
        "operation": operation,
        "path": path,
        "base_version_id": str(base_version_id) if base_version_id else None,
        "base_content_hash": base_content_hash,
        "proposal": proposal_copy,
        "proposed_hash": _proposal_hash(operation, proposal_copy),
        "evidence_ids": list(evidence_ids),
        "reason": reason.strip(),
        "risk_class": risk_class,
    }


def _validate_patch(patch: dict[str, Any]) -> None:
    UUID(str(patch["patch_id"]))
    UUID(str(patch["group_id"]))
    operation = str(patch["operation"])
    if operation not in _OPERATIONS:
        raise ValueError(f"unsupported curation operation: {operation}")
    _validate_path(str(patch["path"]))
    if int(patch["position"]) < 0:
        raise ValueError("patch position cannot be negative")
    if patch["risk_class"] not in _RISK_CLASSES:
        raise ValueError(f"unsupported risk class: {patch['risk_class']}")
    if operation in _CLEANUP_OPERATIONS and patch["risk_class"] != "high":
        raise ValueError(f"{operation} must be classified as high risk")
    if not patch["evidence_ids"] or not str(patch["reason"]).strip():
        raise ValueError("curation patch requires evidence and a reason")
    base_id = patch.get("base_version_id")
    base_hash = patch.get("base_content_hash")
    if operation == "create_page":
        if base_id is not None or base_hash is not None:
            raise ValueError("create_page cannot have a base version")
    else:
        if base_id is None or not isinstance(base_hash, str) or not _HASH.fullmatch(base_hash):
            raise ValueError(f"{operation} requires a base version ID and content hash")
        UUID(str(base_id))
    calculated = _proposal_hash(operation, patch["proposal"])
    if patch.get("proposed_hash") != calculated:
        raise ValueError(f"proposed hash mismatch for patch {patch['patch_id']}")


def build_manifest(
    *,
    replica_id: UUID,
    input_watermark: int,
    patches: list[dict[str, Any]],
    run_id: UUID | None = None,
) -> dict[str, Any]:
    if input_watermark < 0:
        raise ValueError("input watermark cannot be negative")
    if not patches:
        raise ValueError("curation manifest requires at least one patch")
    for patch in patches:
        _validate_patch(patch)
    patch_ids = [patch["patch_id"] for patch in patches]
    positions = [(patch["group_id"], patch["position"]) for patch in patches]
    if len(set(patch_ids)) != len(patch_ids):
        raise ValueError("curation manifest contains duplicate patch IDs")
    if len(set(positions)) != len(positions):
        raise ValueError("curation manifest contains duplicate group positions")
    body = {
        "schema_version": CURATION_SCHEMA_VERSION,
        "run_id": str(run_id or uuid4()),
        "replica_id": str(replica_id),
        "input_watermark": input_watermark,
        "patches": json.loads(_canonical_json(patches)),
    }
    body["manifest_hash"] = _content_hash(body)
    return body


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    copy = json.loads(_canonical_json(manifest))
    supplied_hash = copy.pop("manifest_hash", None)
    if not isinstance(supplied_hash, str) or supplied_hash != _content_hash(copy):
        raise ValueError("curation manifest hash mismatch")
    if copy.get("schema_version") != CURATION_SCHEMA_VERSION:
        raise ValueError(f"unsupported curation schema version: {copy.get('schema_version')}")
    UUID(str(copy["run_id"]))
    UUID(str(copy["replica_id"]))
    if int(copy["input_watermark"]) < 0 or not copy.get("patches"):
        raise ValueError("invalid curation manifest watermark or patch list")
    for patch in copy["patches"]:
        _validate_patch(patch)
    if len({patch["patch_id"] for patch in copy["patches"]}) != len(copy["patches"]):
        raise ValueError("curation manifest contains duplicate patch IDs")
    positions = {(patch["group_id"], patch["position"]) for patch in copy["patches"]}
    if len(positions) != len(copy["patches"]):
        raise ValueError("curation manifest contains duplicate group positions")
    copy["manifest_hash"] = supplied_hash
    return copy


def get_or_create_replica_id(store: PostgresStore) -> UUID:
    store.migrate()
    candidate = uuid4()
    with store.connection() as conn, conn.transaction():
        conn.execute(
            "INSERT INTO sync_replicas (singleton, replica_id) VALUES (true, %s) "
            "ON CONFLICT (singleton) DO NOTHING",
            (candidate,),
        )
        return UUID(str(conn.execute("SELECT replica_id FROM sync_replicas WHERE singleton").fetchone()[0]))


def _insert_manifest(conn: Connection, manifest: dict[str, Any], *, received: bool) -> bool:
    existing = conn.execute(
        "SELECT manifest_hash FROM curation_runs WHERE run_id = %s",
        (UUID(manifest["run_id"]),),
    ).fetchone()
    if existing:
        if existing[0] != manifest["manifest_hash"]:
            raise RuntimeError(f"curation run hash conflict: {manifest['run_id']}")
        return False
    conn.execute(
        "INSERT INTO curation_runs "
        "(run_id, replica_id, input_watermark, schema_version, manifest_hash, manifest, received_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END)",
        (
            UUID(manifest["run_id"]),
            UUID(manifest["replica_id"]),
            manifest["input_watermark"],
            manifest["schema_version"],
            manifest["manifest_hash"],
            Jsonb(manifest),
            received,
        ),
    )
    for patch in manifest["patches"]:
        conn.execute(
            "INSERT INTO curation_patches "
            "(patch_id, run_id, group_id, position, operation, path, base_version_id, "
            "base_content_hash, proposed_hash, proposal, evidence_ids, reason, risk_class) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                UUID(patch["patch_id"]),
                UUID(manifest["run_id"]),
                UUID(patch["group_id"]),
                patch["position"],
                patch["operation"],
                patch["path"],
                UUID(patch["base_version_id"]) if patch["base_version_id"] else None,
                patch["base_content_hash"],
                patch["proposed_hash"],
                Jsonb(patch["proposal"]),
                patch["evidence_ids"],
                patch["reason"],
                patch["risk_class"],
            ),
        )
    return True


def publish_manifest(remote: PostgresStore, manifest: dict[str, Any]) -> bool:
    remote.migrate()
    checked = validate_manifest(manifest)
    with remote.connection() as conn, conn.transaction():
        return _insert_manifest(conn, checked, received=False)


def pull_manifests(
    local: PostgresStore,
    remote: PostgresStore,
    *,
    replica_id: UUID,
) -> dict[str, int]:
    local.migrate()
    remote.migrate()
    with remote.connection() as conn:
        rows = conn.execute(
            "SELECT manifest FROM curation_runs WHERE replica_id = %s "
            "ORDER BY input_watermark, published_at, run_id",
            (replica_id,),
        ).fetchall()
    received_runs = 0
    received_patches = 0
    for row in rows:
        manifest = validate_manifest(dict(row[0]))
        with local.connection() as conn, conn.transaction():
            if _insert_manifest(conn, manifest, received=True):
                received_runs += 1
                received_patches += len(manifest["patches"])
    return {"received_runs": received_runs, "received_patches": received_patches}


def _patches_for_run(conn: Connection, run_id: UUID) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT patch_id, group_id, position, operation, path, base_version_id, "
        "base_content_hash, proposed_hash, proposal, evidence_ids, reason, risk_class "
        "FROM curation_patches WHERE run_id = %s ORDER BY group_id, position",
        (run_id,),
    ).fetchall()
    if not rows:
        raise KeyError(f"unknown curation run: {run_id}")
    keys = (
        "patch_id",
        "group_id",
        "position",
        "operation",
        "path",
        "base_version_id",
        "base_content_hash",
        "proposed_hash",
        "proposal",
        "evidence_ids",
        "reason",
        "risk_class",
    )
    patches = []
    for row in rows:
        patch = dict(zip(keys, row))
        patch["patch_id"] = str(patch["patch_id"])
        patch["group_id"] = str(patch["group_id"])
        patch["base_version_id"] = str(patch["base_version_id"]) if patch["base_version_id"] else None
        patch["proposal"] = dict(patch["proposal"])
        patch["evidence_ids"] = list(patch["evidence_ids"])
        patches.append(patch)
    return patches


def _grouped(patches: Iterable[dict[str, Any]]) -> list[tuple[UUID, list[dict[str, Any]]]]:
    groups: dict[UUID, list[dict[str, Any]]] = {}
    for patch in patches:
        groups.setdefault(UUID(patch["group_id"]), []).append(patch)
    return [(group_id, sorted(items, key=lambda item: item["position"])) for group_id, items in groups.items()]


def _page_state(conn: Connection, path: str, *, lock: bool) -> dict[str, Any] | None:
    suffix = " FOR UPDATE OF p" if lock else ""
    row = conn.execute(
        "SELECT p.page_id, p.path, p.status, p.superseded_by_page_id, v.version_id, "
        "v.version, v.title, v.page_type, v.content, v.content_text, v.tags, v.source_ids, "
        "v.parent_id, v.chunk_index, v.content_hash "
        "FROM pages p JOIN page_versions v ON v.version_id = p.current_version_id "
        "WHERE p.path = %s" + suffix,
        (path,),
    ).fetchone()
    if not row:
        return None
    return {
        "page_id": str(row[0]),
        "path": row[1],
        "status": row[2],
        "superseded_by_page_id": str(row[3]) if row[3] else None,
        "version_id": str(row[4]),
        "version": row[5],
        "title": row[6],
        "page_type": row[7],
        "content": row[8],
        "content_text": row[9],
        "tags": list(row[10] or []),
        "source_ids": list(row[11]) if row[11] is not None else None,
        "parent_id": str(row[12]) if row[12] else None,
        "chunk_index": row[13],
        "content_hash": row[14],
    }


def _target_page(conn: Connection, target_path: str, *, lock: bool) -> dict[str, Any] | None:
    state = _page_state(conn, target_path, lock=lock)
    return state if state and state["status"] == "active" else None


def _conflict_detail(patch: dict[str, Any], local: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    return {
        "patch_id": patch["patch_id"],
        "operation": patch["operation"],
        "path": patch["path"],
        "base_version_id": patch["base_version_id"],
        "base_content_hash": patch["base_content_hash"],
        "local": local,
        "remote": patch["proposal"],
        "reason": reason,
    }


def _preflight_group(
    conn: Connection,
    patches: list[dict[str, Any]],
    *,
    lock: bool,
    force: bool = False,
) -> tuple[list[str], list[dict[str, Any]]]:
    statuses = []
    conflicts = []
    supersedes = {
        patch["path"]: patch["proposal"]["target_path"]
        for patch in patches
        if patch["operation"] == "supersede_page"
    }
    for patch in patches:
        operation = patch["operation"]
        local = _page_state(conn, patch["path"], lock=lock)
        if operation == "create_page":
            if local is None:
                statuses.append("applicable")
            elif local["status"] == "active" and local["content_hash"] == patch["proposed_hash"]:
                statuses.append("already_applied")
            elif force and local["status"] == "active":
                statuses.append("applicable")
            else:
                statuses.append("conflict")
                conflicts.append(_conflict_detail(patch, local, "path already exists"))
            continue
        target_path = patch["proposal"].get("target_path")
        target = _target_page(conn, target_path, lock=lock) if target_path else None
        if operation == "supersede_page" and local and local["status"] == "superseded":
            if target and local["superseded_by_page_id"] == target["page_id"]:
                statuses.append("already_applied")
            else:
                statuses.append("conflict")
                conflicts.append(_conflict_detail(patch, local, "page is already superseded elsewhere"))
            continue
        if operation == "add_alias":
            alias = conn.execute(
                "SELECT target_page_id FROM page_aliases WHERE alias_path = %s",
                (patch["path"],),
            ).fetchone()
            if alias and target and str(alias[0]) == target["page_id"]:
                statuses.append("already_applied")
                continue
            if alias:
                statuses.append("conflict")
                conflicts.append(_conflict_detail(patch, local, "alias points to another page"))
                continue
        if local is None:
            statuses.append("conflict")
            conflicts.append(_conflict_detail(patch, None, "base page is missing"))
            continue
        base_matches = (
            local["version_id"] == patch["base_version_id"]
            and local["content_hash"] == patch["base_content_hash"]
        )
        if operation == "update_page" and local["content_hash"] == patch["proposed_hash"]:
            statuses.append("already_applied")
        elif not force and (local["status"] != "active" or not base_matches):
            statuses.append("conflict")
            conflicts.append(_conflict_detail(patch, local, "local page changed after the remote base"))
        elif operation in {"retarget_links", "add_alias", "supersede_page"} and not target:
            statuses.append("conflict")
            conflicts.append(_conflict_detail(patch, local, "canonical target is missing or inactive"))
        elif operation == "add_alias" and local["status"] == "active" and supersedes.get(patch["path"]) != target_path:
            statuses.append("conflict")
            conflicts.append(_conflict_detail(patch, local, "active page is not superseded in this group"))
        else:
            statuses.append("applicable")
    return statuses, conflicts


def _receipt_count(conn: Connection, patches: list[dict[str, Any]]) -> int:
    ids = [UUID(patch["patch_id"]) for patch in patches]
    return int(conn.execute("SELECT count(*) FROM curation_receipts WHERE patch_id = ANY(%s)", (ids,)).fetchone()[0])


def plan_run(store: PostgresStore, run_id: UUID, *, policy: str = "safe") -> dict[str, Any]:
    if policy not in {"safe", "all"}:
        raise ValueError("curation policy must be 'safe' or 'all'")
    store.migrate()
    groups = []
    with store.connection() as conn:
        patches = _patches_for_run(conn, run_id)
        for group_id, items in _grouped(patches):
            receipt_count = _receipt_count(conn, items)
            if receipt_count == len(items):
                status = "already_processed"
                details = []
            elif receipt_count:
                status = "conflict"
                details = [{"reason": "patch group has partial receipts"}]
            elif policy == "safe" and any(item["risk_class"] != "low" for item in items):
                status = "review_required"
                details = []
            else:
                patch_statuses, details = _preflight_group(conn, items, lock=False)
                if details:
                    status = "conflict"
                elif all(value == "already_applied" for value in patch_statuses):
                    status = "already_applied"
                else:
                    status = "applicable"
            groups.append({"group_id": str(group_id), "status": status, "details": details})
    return {"run_id": str(run_id), "groups": groups, "counts": dict(Counter(item["status"] for item in groups))}


def _retarget_links(conn: Connection, source: dict[str, Any], target: dict[str, Any]) -> None:
    source_id = UUID(source["page_id"])
    target_id = UUID(target["page_id"])
    incoming = conn.execute(
        "SELECT link_id, source_page_id, link_type, origin, metadata FROM links WHERE target_page_id = %s",
        (source_id,),
    ).fetchall()
    for link_id, from_id, link_type, origin, metadata in incoming:
        if from_id != target_id:
            conn.execute(
                "INSERT INTO links (link_id, source_page_id, target_page_id, link_type, origin, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (source_page_id, target_page_id, link_type) DO NOTHING",
                (uuid4(), from_id, target_id, link_type, origin, Jsonb(metadata)),
            )
        conn.execute("DELETE FROM links WHERE link_id = %s", (link_id,))
    outgoing = conn.execute(
        "SELECT link_id, target_page_id, link_type, origin, metadata FROM links WHERE source_page_id = %s",
        (source_id,),
    ).fetchall()
    for link_id, to_id, link_type, origin, metadata in outgoing:
        if to_id not in {source_id, target_id}:
            conn.execute(
                "INSERT INTO links (link_id, source_page_id, target_page_id, link_type, origin, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (source_page_id, target_page_id, link_type) DO NOTHING",
                (uuid4(), target_id, to_id, link_type, origin, Jsonb(metadata)),
            )
        conn.execute("DELETE FROM links WHERE link_id = %s", (link_id,))


def _apply_patch(
    store: PostgresStore,
    conn: Connection,
    patch: dict[str, Any],
    *,
    force: bool,
) -> UUID | None:
    operation = patch["operation"]
    proposal = patch["proposal"]
    patch_id = UUID(patch["patch_id"])
    if operation in {"create_page", "update_page"}:
        kwargs: dict[str, Any] = {}
        if not force:
            kwargs["expected_base_content_hash"] = (
                None if operation == "create_page" else patch["base_content_hash"]
            )
        _message, version_id = store.pages.write_in_connection(
            conn,
            patch["path"],
            proposal["title"],
            proposal["content"],
            page_type=proposal["page_type"],
            created_by="remote-curator",
            tags=proposal["tags"],
            source_ids=proposal["source_ids"],
            parent_id=proposal["parent_id"],
            chunk_index=proposal["chunk_index"],
            content_text_override=proposal["content_text"],
            curation_patch_id=patch_id,
            preserve_llm_tags=False,
            **kwargs,
        )
        return version_id
    source = _page_state(conn, patch["path"], lock=True)
    target = _target_page(conn, proposal["target_path"], lock=True)
    if not source or not target:
        raise RuntimeError(f"curation cleanup precondition disappeared: {patch['path']}")
    if operation == "retarget_links":
        _retarget_links(conn, source, target)
    elif operation == "add_alias":
        conn.execute(
            "INSERT INTO page_aliases (alias_path, target_page_id, curation_patch_id) "
            "VALUES (%s, %s, %s) ON CONFLICT (alias_path) DO NOTHING",
            (patch["path"], UUID(target["page_id"]), patch_id),
        )
    elif operation == "supersede_page":
        conn.execute(
            "UPDATE pages SET status = 'superseded', superseded_by_page_id = %s, updated_at = now() "
            "WHERE page_id = %s",
            (UUID(target["page_id"]), UUID(source["page_id"])),
        )
    conn.execute(
        "INSERT INTO operations (operation_id, op_type, path, details) VALUES (%s, %s, %s, %s)",
        (uuid4(), operation, patch["path"], Jsonb({"curation_patch_id": patch["patch_id"]})),
    )
    return None


def _insert_receipt(
    conn: Connection,
    run_id: UUID,
    patch: dict[str, Any],
    *,
    status: str,
    result_version_id: UUID | None,
) -> None:
    local = _page_state(conn, patch["path"], lock=False)
    local_hash = local["content_hash"] if local else None
    payload = {
        "run_id": str(run_id),
        "patch_id": patch["patch_id"],
        "status": status,
        "result_version_id": str(result_version_id) if result_version_id else None,
        "local_content_hash": local_hash,
    }
    conn.execute(
        "INSERT INTO curation_receipts "
        "(patch_id, run_id, status, result_version_id, local_content_hash, details) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (patch_id) DO NOTHING",
        (
            UUID(patch["patch_id"]),
            run_id,
            status,
            result_version_id,
            local_hash,
            Jsonb({}),
        ),
    )
    conn.execute(
        "INSERT INTO sync_outbox (event_id, entity_kind, entity_id, version_id, payload_hash) "
        "VALUES (%s, 'curation_receipt', %s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
        (
            uuid5(NAMESPACE_URL, f"wikibricks:curation-receipt:{patch['patch_id']}"),
            run_id,
            UUID(patch["patch_id"]),
            _content_hash(payload),
        ),
    )


def _mark_run_applied(conn: Connection, run_id: UUID) -> None:
    conn.execute(
        "UPDATE curation_runs SET applied_at = now() WHERE run_id = %s "
        "AND NOT EXISTS ("
        "SELECT 1 FROM curation_patches p LEFT JOIN curation_receipts r USING (patch_id) "
        "WHERE p.run_id = %s AND r.patch_id IS NULL)",
        (run_id, run_id),
    )


def _apply_group(
    store: PostgresStore,
    conn: Connection,
    run_id: UUID,
    group_id: UUID,
    patches: list[dict[str, Any]],
    *,
    force: bool,
    failpoint: Callable[[str], None],
) -> tuple[str, list[dict[str, Any]]]:
    receipt_count = _receipt_count(conn, patches)
    if receipt_count == len(patches):
        return "already_processed", []
    if receipt_count:
        raise RuntimeError(f"curation group has partial receipts: {group_id}")
    statuses, conflicts = _preflight_group(conn, patches, lock=True, force=force)
    if conflicts:
        conn.execute(
            "INSERT INTO curation_conflicts (conflict_id, run_id, group_id, details) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (run_id, group_id) DO UPDATE SET details = excluded.details "
            "WHERE curation_conflicts.status = 'pending'",
            (uuid4(), run_id, group_id, Jsonb(conflicts)),
        )
        return "conflict", conflicts
    for patch, patch_status in zip(patches, statuses):
        if patch_status == "already_applied":
            state = _page_state(conn, patch["path"], lock=False)
            result_id = UUID(state["version_id"]) if state else None
            _insert_receipt(
                conn,
                run_id,
                patch,
                status="already_applied",
                result_version_id=result_id,
            )
        else:
            result_id = _apply_patch(store, conn, patch, force=force)
            _insert_receipt(conn, run_id, patch, status="applied", result_version_id=result_id)
    failpoint("before_group_commit")
    return "applied", []


def apply_run(
    store: PostgresStore,
    run_id: UUID,
    *,
    policy: str = "safe",
    failpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if policy not in {"safe", "all"}:
        raise ValueError("curation policy must be 'safe' or 'all'")
    store.migrate()
    trigger = failpoint or (lambda _stage: None)
    with store.connection() as conn:
        patches = _patches_for_run(conn, run_id)
    results = []
    for group_id, items in _grouped(patches):
        if policy == "safe" and any(item["risk_class"] != "low" for item in items):
            results.append({"group_id": str(group_id), "status": "review_required"})
            continue
        with store.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_SYNC_LOCK_KEY,))
            status, details = _apply_group(
                store,
                conn,
                run_id,
                group_id,
                items,
                force=False,
                failpoint=trigger,
            )
            _mark_run_applied(conn, run_id)
        results.append({"group_id": str(group_id), "status": status, "details": details})
    return {"run_id": str(run_id), "groups": results, "counts": dict(Counter(item["status"] for item in results))}


def list_conflicts(store: PostgresStore) -> list[dict[str, Any]]:
    store.migrate()
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT conflict_id, run_id, group_id, details, created_at "
            "FROM curation_conflicts WHERE status = 'pending' ORDER BY created_at, conflict_id"
        ).fetchall()
    return [
        {
            "conflict_id": str(row[0]),
            "run_id": str(row[1]),
            "group_id": str(row[2]),
            "details": list(row[3]),
            "created_at": row[4],
        }
        for row in rows
    ]


def resolve_conflict(
    store: PostgresStore,
    run_id: UUID,
    group_id: UUID,
    *,
    action: str,
) -> dict[str, Any]:
    if action == "defer":
        return {"run_id": str(run_id), "group_id": str(group_id), "resolution": "deferred"}
    if action not in {"keep_local", "accept_remote", "merged"}:
        raise ValueError("resolution must be keep_local, accept_remote, merged, or defer")
    store.migrate()
    with store.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_SYNC_LOCK_KEY,))
        conflict = conn.execute(
            "SELECT conflict_id FROM curation_conflicts "
            "WHERE run_id = %s AND group_id = %s AND status = 'pending' FOR UPDATE",
            (run_id, group_id),
        ).fetchone()
        if not conflict:
            raise KeyError(f"pending curation conflict not found: {run_id}/{group_id}")
        patches = [
            patch
            for patch in _patches_for_run(conn, run_id)
            if UUID(patch["group_id"]) == group_id
        ]
        if action == "accept_remote":
            status, details = _apply_group(
                store,
                conn,
                run_id,
                group_id,
                patches,
                force=True,
                failpoint=lambda _stage: None,
            )
            if status == "conflict":
                raise RuntimeError(f"remote proposal no longer has a valid target: {details}")
        else:
            receipt_status = "kept_local" if action == "keep_local" else "merged"
            for patch in patches:
                state = _page_state(conn, patch["path"], lock=True)
                result_id = UUID(state["version_id"]) if state else None
                _insert_receipt(
                    conn,
                    run_id,
                    patch,
                    status=receipt_status,
                    result_version_id=result_id,
                )
        conn.execute(
            "UPDATE curation_conflicts SET status = 'resolved', resolution = %s, resolved_at = now() "
            "WHERE conflict_id = %s",
            (action, conflict[0]),
        )
        _mark_run_applied(conn, run_id)
    return {"run_id": str(run_id), "group_id": str(group_id), "resolution": action}
