"""Model proposal validation and deterministic curation-patch planning."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid5

from wikibricks.curation import create_patch
from wikibricks_remote.resources import RemotePolicy

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
