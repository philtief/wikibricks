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
    "database": {"url": None},
    "search": {"default_results": None, "maximum_results": None},
    "maintenance": {"prune_archived_sessions_after_days": None},
    "sync": {"batch_size": None, "apply_policy": None},
}


@dataclass(frozen=True, slots=True)
class WikiBricksConfig:
    database_url: str
    search_default_results: int
    search_maximum_results: int
    prune_archived_sessions_after_days: int | None
    sync_batch_size: int
    sync_apply_policy: Literal["safe", "all"]


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


def _environment_overlay(environ: Mapping[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    mappings = {
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
    }
    for name, (section, key, convert) in mappings.items():
        if name not in environ:
            continue
        raw = environ[name]
        try:
            value = convert(raw)
        except ValueError as exc:
            raise ValueError(f"{name} has an invalid value") from exc
        result.setdefault(section, {})[key] = value
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

    user_path = Path(home) if home is not None else Path.home()
    user_path = user_path / ".wikibricks" / "config.yml"
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
    database_url = value["database"]["url"]
    if not isinstance(database_url, str) or not database_url.strip():
        raise ValueError("database.url must be a non-empty string")
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
    batch_size = _integer(
        value["sync"]["batch_size"],
        "sync.batch_size",
        minimum=1,
        maximum=100000,
    )
    policy = value["sync"]["apply_policy"]
    if policy not in {"safe", "all"}:
        raise ValueError("sync.apply_policy must be safe or all")
    return WikiBricksConfig(
        database_url=database_url,
        search_default_results=default_results,
        search_maximum_results=maximum_results,
        prune_archived_sessions_after_days=retention,
        sync_batch_size=batch_size,
        sync_apply_policy=policy,
    )


__all__ = ["WikiBricksConfig", "load_config"]
