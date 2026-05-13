"""Resolve recorder runtime config — env vars → TOML file → raise.

Five required values: catalog, schema, warehouse_id, profile, user_id.

TOML supports two shapes:

  Single (legacy):
    [recorder]
    catalog = "..."
    schema = "..."
    warehouse_id = "..."
    profile = "..."
    user_id = "..."

  Multi (per-wiki named sections — required for personal+team on one machine):
    [wikis.personal]
    catalog = "..."
    ...
    [wikis.team-platform]
    catalog = "..."
    ...

In multi-wiki mode the active wiki is selected by, in order:

  1. `WIKIBRICKS_TARGET=<name>` env var (per-session override)
  2. `~/.wikibricks/active-target` file (set by `wiki-target <name>`)
  3. If exactly one wiki is configured, that one.
  4. Otherwise raise with a hint to run `wiki-target <name>`.

Per-key overrides via `WIKIBRICKS_RECORDER_<KEY>` (preferred) or
`WIKIBRICKS_RECORDER_<KEY>` (legacy) env vars take precedence over both shapes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

CONFIG_FILE = Path.home() / ".wikibricks-recorder.toml"
ACTIVE_TARGET_FILE = Path.home() / ".wikibricks" / "active-target"

REQUIRED_KEYS = ("catalog", "schema", "warehouse_id", "profile", "user_id")


def _load_full_toml() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        with CONFIG_FILE.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _load_recorder_section() -> dict[str, Any]:
    section = _load_full_toml().get("recorder") or {}
    return section if isinstance(section, dict) else {}


def list_wikis() -> dict[str, dict[str, str]]:
    """Return named [wikis.<name>] sections as a dict of dicts."""
    wikis = _load_full_toml().get("wikis") or {}
    if not isinstance(wikis, dict):
        return {}
    return {
        name: {k: str(v) for k, v in cfg.items()}
        for name, cfg in wikis.items()
        if isinstance(cfg, dict)
    }


def load_auto_tag_config() -> dict[str, Any]:
    """Return the ``[auto_tag]`` section from the recorder config, or empty.

    Used by the recorder to opt into LLM-based topic-slug extraction.
    Default behaviour (no section) is OFF.
    """
    section = _load_full_toml().get("auto_tag") or {}
    if not isinstance(section, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in section.items():
        out[str(k)] = v
    return out


def load_topic_keywords() -> dict[str, list[str]]:
    """Return the ``[topic_keywords]`` section as ``{slug: [terms, ...]}``.
    Empty dict if the section is absent or malformed. Used by the recorder
    to auto-tag sessions with ``customer:<slug>`` at flush time.
    """
    section = _load_full_toml().get("topic_keywords") or {}
    if not isinstance(section, dict):
        return {}
    out: dict[str, list[str]] = {}
    for slug, terms in section.items():
        if isinstance(terms, list):
            out[str(slug)] = [str(t) for t in terms if isinstance(t, (str, int))]
    return out


def get_active_target() -> str | None:
    if not ACTIVE_TARGET_FILE.exists():
        return None
    try:
        return ACTIVE_TARGET_FILE.read_text().strip() or None
    except OSError:
        return None


def set_active_target(name: str) -> None:
    ACTIVE_TARGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_TARGET_FILE.write_text(name + "\n")


def clear_active_target() -> None:
    if ACTIVE_TARGET_FILE.exists():
        ACTIVE_TARGET_FILE.unlink()


def _git_email() -> str | None:
    if not shutil.which("git"):
        return None
    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    email = result.stdout.strip()
    return email or None


def _sanitize_user_id(raw: str) -> str:
    return raw.replace("@", "-at-").strip()


def _env_override(key: str) -> str | None:
    new_env = os.environ.get(f"WIKIBRICKS_RECORDER_{key.upper()}")
    if new_env:
        return new_env
    legacy_env = os.environ.get(f"WIKIBRICKS_RECORDER_{key.upper()}")
    if legacy_env:
        return legacy_env
    return None


def _pick_wiki(wikis: dict[str, dict[str, str]]) -> dict[str, str]:
    """Choose the active wiki. Raises RuntimeError with a setup hint."""
    if len(wikis) == 1:
        return next(iter(wikis.values()))
    target = os.environ.get("WIKIBRICKS_TARGET") or get_active_target()
    if target is None:
        names = ", ".join(sorted(wikis))
        raise RuntimeError(
            f"wikibricks_recorder: multiple wikis configured ({names}) but "
            f"no active target selected.\n\n"
            f"Pick one with:\n  wiki-target <name>\n\n"
            f"Or set WIKIBRICKS_TARGET=<name> for this session."
        )
    if target not in wikis:
        names = ", ".join(sorted(wikis))
        raise RuntimeError(
            f"wikibricks_recorder: unknown wiki target '{target}'. "
            f"Configured: {names}"
        )
    return wikis[target]


def load_config() -> dict[str, str]:
    """Return {catalog, schema, warehouse_id, profile, user_id} or raise."""
    toml = _load_full_toml()
    recorder = toml.get("recorder") or {}
    if not isinstance(recorder, dict):
        recorder = {}
    base: dict[str, str] = {}
    if recorder:
        base = {k: str(v) for k, v in recorder.items() if isinstance(v, (str, int))}
    else:
        wikis = list_wikis()
        if wikis:
            base = _pick_wiki(wikis)

    resolved: dict[str, str] = {}
    missing: list[str] = []

    for key in REQUIRED_KEYS:
        value = _env_override(key) or base.get(key)
        if value is None and key == "user_id":
            email = _git_email()
            if email:
                value = _sanitize_user_id(email)
        if value is None:
            missing.append(key)
        else:
            resolved[key] = value

    if missing:
        raise RuntimeError(
            "wikibricks_recorder: missing config keys: "
            + ", ".join(missing)
            + ".\n\nFix one of:\n"
            + "  1. export WIKIBRICKS_RECORDER_<KEY>=... in your shell\n"
            + f"  2. write {CONFIG_FILE} with [wikis.<name>] sections "
            + "(use `wiki-init`):\n\n"
            + "       [wikis.personal]\n"
            + '       catalog = "main"\n'
            + '       schema = "wikibricks_personal_<you>"\n'
            + '       warehouse_id = "<sql-warehouse-id>"\n'
            + '       profile = "<databricks-cli-profile>"\n'
            + '       user_id = "<your-email-or-handle>"\n'
        )

    return resolved
