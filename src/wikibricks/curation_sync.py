"""Compatibility exports for the local curation API."""

from wikibricks.curation import (
    apply_run,
    build_manifest,
    create_patch,
    get_or_create_replica_id,
    list_conflicts,
    plan_run,
    publish_manifest,
    pull_manifests,
    resolve_conflict,
    validate_manifest,
)

__all__ = [
    "apply_run",
    "build_manifest",
    "create_patch",
    "get_or_create_replica_id",
    "list_conflicts",
    "plan_run",
    "publish_manifest",
    "pull_manifests",
    "resolve_conflict",
    "validate_manifest",
]
