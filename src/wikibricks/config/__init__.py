"""Validated YAML configuration for local WikiBricks."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import yaml

_ALLOWED = {
    "version": None,
    "database": {"path": None, "url": None},
    "search": {"default_results": None, "maximum_results": None},
    "maintenance": {"prune_archived_sessions_after_days": None},
    "automation": {
        "enabled": None,
        "poll_seconds": None,
        "local_maintenance_hours": None,
        "omnigent": {"database": None},
    },
    "sync": {
        "batch_size": None,
        "apply_policy": None,
        "interval_hours": None,
        "profile": None,
        "project": None,
        "branch": None,
        "endpoint": None,
        "database": None,
    },
}


@dataclass(frozen=True, slots=True)
class WikiBricksConfig:
    database_path: Path
    database_url: str | None
    search_default_results: int
    search_maximum_results: int
    prune_archived_sessions_after_days: int | None
    automation_enabled: bool
    automation_poll_seconds: int
    automation_local_maintenance_hours: int
    automation_omnigent_database: Path
    sync_batch_size: int
    sync_apply_policy: Literal["safe", "all"]
    sync_interval_hours: int
    sync_profile: str | None
    sync_project: str | None
    sync_branch: str
    sync_endpoint: str
    sync_database: str


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"configuration in {path} must be a mapping")
    return value


def _merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _validate_keys(
    value: Mapping[str, Any],
    allowed: Mapping[str, Any],
    prefix: str = "",
) -> None:
    for key, nested in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if key not in allowed:
            raise ValueError(f"unknown configuration key: {path}")
        child_allowed = allowed[key]
        if child_allowed is not None:
            if not isinstance(nested, Mapping):
                raise ValueError(f"{path} must be a mapping")
            _validate_keys(nested, child_allowed, path)


def _integer(value: Any, path: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{path} must be between {minimum} and {maximum}")
    return value


def _boolean(value: Any, path: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError(f"{path} must be true or false")


def _string(value: Any, path: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        suffix = " or null" if optional else ""
        raise ValueError(f"{path} must be a non-empty string{suffix}")
    return value.strip()


def _environment_overlay(environ: Mapping[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    mappings = {
        "WIKIBRICKS_DATABASE_PATH": ("database", "path", str),
        "WIKIBRICKS_DATABASE_URL": ("database", "url", str),
        "WIKIBRICKS_SEARCH_DEFAULT_RESULTS": ("search", "default_results", int),
        "WIKIBRICKS_SEARCH_MAXIMUM_RESULTS": ("search", "maximum_results", int),
        "WIKIBRICKS_PRUNE_ARCHIVED_SESSIONS_AFTER_DAYS": (
            "maintenance",
            "prune_archived_sessions_after_days",
            int,
        ),
        "WIKIBRICKS_SYNC_BATCH_SIZE": ("sync", "batch_size", int),
        "WIKIBRICKS_SYNC_APPLY_POLICY": ("sync", "apply_policy", str),
        "WIKIBRICKS_AUTOMATION_ENABLED": ("automation", "enabled", str),
        "WIKIBRICKS_AUTOMATION_POLL_SECONDS": ("automation", "poll_seconds", int),
        "WIKIBRICKS_OMNIGENT_DATABASE": ("automation", "omnigent.database", str),
        "WIKIBRICKS_SYNC_INTERVAL_HOURS": ("sync", "interval_hours", int),
        "WIKIBRICKS_SYNC_PROFILE": ("sync", "profile", str),
        "WIKIBRICKS_SYNC_PROJECT": ("sync", "project", str),
        "WIKIBRICKS_SYNC_BRANCH": ("sync", "branch", str),
        "WIKIBRICKS_SYNC_ENDPOINT": ("sync", "endpoint", str),
        "WIKIBRICKS_SYNC_DATABASE": ("sync", "database", str),
    }
    for name, (section, key, convert) in mappings.items():
        if name not in environ:
            continue
        raw = environ[name]
        try:
            value = convert(raw)
        except ValueError as exc:
            raise ValueError(f"{name} has an invalid value") from exc
        target = result.setdefault(section, {})
        if "." in key:
            parent, child = key.split(".", 1)
            target.setdefault(parent, {})[child] = value
        else:
            target[key] = value
    return result


def load_config(
    path: str | Path | None = None,
    *,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> WikiBricksConfig:
    active_environment = os.environ if environ is None else environ
    defaults = files("wikibricks.config").joinpath("defaults.yml")
    value = yaml.safe_load(defaults.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("packaged defaults.yml must contain a mapping")

    user_home = Path(home) if home is not None else Path.home()
    user_path = user_home / ".wikibricks" / "config.yml"
    if user_path.exists():
        _merge(value, _read_yaml(user_path))

    environment_path = active_environment.get("WIKIBRICKS_CONFIG")
    if environment_path:
        _merge(value, _read_yaml(Path(environment_path).expanduser()))
    if path is not None:
        _merge(value, _read_yaml(Path(path).expanduser()))
    _merge(value, _environment_overlay(active_environment))
    _validate_keys(value, _ALLOWED)

    if value.get("version") != 1:
        raise ValueError("version must be 1")
    database_path = _string(value["database"]["path"], "database.path")
    assert database_path is not None
    if database_path.startswith("~/"):
        resolved_database_path = user_home / database_path[2:]
    else:
        resolved_database_path = Path(database_path).expanduser()
    database_url = _string(
        value["database"].get("url"),
        "database.url",
        optional=True,
    )
    default_results = _integer(
        value["search"]["default_results"],
        "search.default_results",
        minimum=1,
        maximum=1000,
    )
    maximum_results = _integer(
        value["search"]["maximum_results"],
        "search.maximum_results",
        minimum=1,
        maximum=1000,
    )
    if default_results > maximum_results:
        raise ValueError("search.default_results must not exceed search.maximum_results")
    retention = value["maintenance"]["prune_archived_sessions_after_days"]
    if retention is not None:
        retention = _integer(
            retention,
            "maintenance.prune_archived_sessions_after_days",
            minimum=1,
            maximum=36500,
        )
    automation_enabled = _boolean(
        value["automation"]["enabled"],
        "automation.enabled",
    )
    poll_seconds = _integer(
        value["automation"]["poll_seconds"],
        "automation.poll_seconds",
        minimum=1,
        maximum=86400,
    )
    local_maintenance_hours = _integer(
        value["automation"]["local_maintenance_hours"],
        "automation.local_maintenance_hours",
        minimum=1,
        maximum=8760,
    )
    omnigent_database = _string(
        value["automation"]["omnigent"]["database"],
        "automation.omnigent.database",
    )
    assert omnigent_database is not None
    if omnigent_database.startswith("~/"):
        omnigent_path = user_home / omnigent_database[2:]
    else:
        omnigent_path = Path(omnigent_database).expanduser()
    batch_size = _integer(
        value["sync"]["batch_size"],
        "sync.batch_size",
        minimum=1,
        maximum=100000,
    )
    policy = value["sync"]["apply_policy"]
    if policy not in {"safe", "all"}:
        raise ValueError("sync.apply_policy must be safe or all")
    sync_interval_hours = _integer(
        value["sync"]["interval_hours"],
        "sync.interval_hours",
        minimum=1,
        maximum=8760,
    )
    sync_profile = _string(value["sync"]["profile"], "sync.profile", optional=True)
    sync_project = _string(value["sync"]["project"], "sync.project", optional=True)
    sync_branch = _string(value["sync"]["branch"], "sync.branch")
    sync_endpoint = _string(value["sync"]["endpoint"], "sync.endpoint")
    sync_database = _string(value["sync"]["database"], "sync.database")
    assert sync_branch is not None
    assert sync_endpoint is not None
    assert sync_database is not None
    return WikiBricksConfig(
        database_path=resolved_database_path,
        database_url=database_url,
        search_default_results=default_results,
        search_maximum_results=maximum_results,
        prune_archived_sessions_after_days=retention,
        automation_enabled=automation_enabled,
        automation_poll_seconds=poll_seconds,
        automation_local_maintenance_hours=local_maintenance_hours,
        automation_omnigent_database=omnigent_path,
        sync_batch_size=batch_size,
        sync_apply_policy=policy,
        sync_interval_hours=sync_interval_hours,
        sync_profile=sync_profile,
        sync_project=sync_project,
        sync_branch=sync_branch,
        sync_endpoint=sync_endpoint,
        sync_database=sync_database,
    )


__all__ = ["WikiBricksConfig", "load_config"]
