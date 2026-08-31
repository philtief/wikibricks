"""Pure construction and validation for immutable curation manifests."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import UUID, uuid4

from wikibricks.storage.content import page_content_hash

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


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_path(path: str) -> None:
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or len(parts) < 2
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError(f"invalid wiki page path: {path!r}")


def _proposal_hash(operation: str, proposal: dict[str, Any]) -> str:
    if operation in {"create_page", "update_page"}:
        if set(proposal) != _PAGE_FIELDS:
            missing = sorted(_PAGE_FIELDS - set(proposal))
            extra = sorted(set(proposal) - _PAGE_FIELDS)
            raise ValueError(
                "page proposal fields do not match contract; "
                f"missing={missing}, extra={extra}"
            )
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
    return content_hash(proposal)


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
    elif (
        base_version_id is None
        or not base_content_hash
        or not _HASH.fullmatch(base_content_hash)
    ):
        raise ValueError(
            f"{operation} requires a base version ID and content hash"
        )
    if base_version_id is not None:
        UUID(str(base_version_id))
    proposal_copy = json.loads(canonical_json(proposal))
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
        if (
            base_id is None
            or not isinstance(base_hash, str)
            or not _HASH.fullmatch(base_hash)
        ):
            raise ValueError(
                f"{operation} requires a base version ID and content hash"
            )
        UUID(str(base_id))
    calculated = _proposal_hash(operation, patch["proposal"])
    if patch.get("proposed_hash") != calculated:
        raise ValueError(
            f"proposed hash mismatch for patch {patch['patch_id']}"
        )


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
    positions = [
        (patch["group_id"], patch["position"])
        for patch in patches
    ]
    if len(set(patch_ids)) != len(patch_ids):
        raise ValueError("curation manifest contains duplicate patch IDs")
    if len(set(positions)) != len(positions):
        raise ValueError("curation manifest contains duplicate group positions")
    body = {
        "schema_version": CURATION_SCHEMA_VERSION,
        "run_id": str(run_id or uuid4()),
        "replica_id": str(replica_id),
        "input_watermark": input_watermark,
        "patches": json.loads(canonical_json(patches)),
    }
    body["manifest_hash"] = content_hash(body)
    return body


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    copy = json.loads(canonical_json(manifest))
    supplied_hash = copy.pop("manifest_hash", None)
    if (
        not isinstance(supplied_hash, str)
        or supplied_hash != content_hash(copy)
    ):
        raise ValueError("curation manifest hash mismatch")
    if copy.get("schema_version") != CURATION_SCHEMA_VERSION:
        raise ValueError(
            "unsupported curation schema version: "
            f"{copy.get('schema_version')}"
        )
    UUID(str(copy["run_id"]))
    UUID(str(copy["replica_id"]))
    if int(copy["input_watermark"]) < 0 or not copy.get("patches"):
        raise ValueError("invalid curation manifest watermark or patch list")
    for patch in copy["patches"]:
        _validate_patch(patch)
    patch_ids = {patch["patch_id"] for patch in copy["patches"]}
    if len(patch_ids) != len(copy["patches"]):
        raise ValueError("curation manifest contains duplicate patch IDs")
    positions = {
        (patch["group_id"], patch["position"])
        for patch in copy["patches"]
    }
    if len(positions) != len(copy["patches"]):
        raise ValueError("curation manifest contains duplicate group positions")
    copy["manifest_hash"] = supplied_hash
    return copy
