"""Validated, atomic file operations used by harness installers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


def load_json_object(path: Path, *, parent: str | None = None) -> dict[str, Any]:
    """Load a JSON object and validate an optional object-valued parent."""
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot update JSON config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Cannot update JSON config {path}: top level must be an object")
    if parent is not None and parent in value and not isinstance(value[parent], dict):
        raise RuntimeError(f"Cannot update JSON config {path}: {parent} must be an object")
    return value


def load_yaml_mapping(path: Path, *, parent: str | None = None) -> dict[str, Any]:
    """Load a YAML mapping and validate an optional mapping-valued parent."""
    if not path.exists():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Cannot update YAML config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Cannot update YAML config {path}: top level must be a mapping")
    if parent is not None and parent in value and not isinstance(value[parent], dict):
        raise RuntimeError(f"Cannot update YAML config {path}: {parent} must be a mapping")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    """Write one JSON object by replacing a same-directory temporary file."""
    write_text_atomic(path, json.dumps(value, indent=2) + "\n")


def write_yaml_atomic(path: Path, value: dict[str, Any]) -> None:
    """Write one YAML mapping by replacing a same-directory temporary file."""
    write_text_atomic(path, yaml.safe_dump(value, sort_keys=False))


def write_text_atomic(path: Path, text: str, *, executable: bool = False) -> None:
    """Atomically write text and optionally set user executable permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        if executable:
            temporary.chmod(0o755)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


__all__ = [
    "load_json_object",
    "load_yaml_mapping",
    "write_json_atomic",
    "write_text_atomic",
    "write_yaml_atomic",
]
