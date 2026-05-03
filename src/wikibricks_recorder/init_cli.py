"""`wiki-init` — interactive setup for personal or team WikiBricks recorder.

Three flows:

  wiki-init                # interactive: personal-or-team, then mode-specific
  wiki-init --join PATH    # joiner: import a teammate's wikibricks-team.toml,
                           # supply own profile + user_id

Outputs:
  ~/.wikibricks-recorder.toml   — always (the runtime config)
  ./wikibricks-team.toml        — only on team-create (sharable, non-secret)

The team-config holds {catalog, schema, warehouse_id, host} only — no
profile names, no tokens. Joiners supply their own per-machine profile +
user_id; the team owner runs the printed GRANT SQL to give them access.

Pure stdin/stdout — no extra deps. All prompts and writers are factored
into testable functions; `main()` is the thin CLI entrypoint.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Callable, TextIO

from wikibricks_recorder import config as recorder_config
from wikibricks_recorder.config import CONFIG_FILE

TEAM_CONFIG_FILENAME = "wikibricks-team.toml"

# Schema name validation: lowercase + digits + underscore only; UC convention.
_SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TEAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def _prompt(reader: Callable[[str], str], label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        raw = reader(f"{label}{suffix}: ").strip()
        if raw:
            return raw
        if default:
            return default
        print("  (required, please enter a value)", file=sys.stderr)


def _prompt_choice(
    reader: Callable[[str], str], label: str, options: list[str], default: str
) -> str:
    """Single-letter choice; default is the capitalized one in `[P/t]`."""
    pretty = "/".join(o.upper() if o == default else o for o in options)
    while True:
        raw = reader(f"{label} [{pretty}]: ").strip().lower()
        if not raw:
            return default
        if raw in options:
            return raw
        first = raw[0]
        for o in options:
            if o.startswith(first):
                return o
        print(f"  (please answer one of: {', '.join(options)})", file=sys.stderr)


def _email_local_part(email: str) -> str:
    """Default schema suffix for personal mode — local part of git email,
    sanitized to UC schema rules."""
    local = email.split("@", 1)[0]
    return re.sub(r"[^a-z0-9_]", "_", local.lower()) or "me"


def _sanitize_user_id(email: str) -> str:
    return email.replace("@", "-at-").strip()


def _git_email() -> str | None:
    import shutil
    import subprocess

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
    return (result.stdout.strip() or None)


_RECORDER_HEADER = (
    "# wikibricks-recorder runtime config — written by `wiki-init`.\n"
    "# Per-machine. Edit by hand any time, or re-run `wiki-init`.\n"
    "# Switch the active wiki with: wiki-target <name>\n\n"
)


def _format_wiki_section(name: str, cfg: dict[str, str]) -> str:
    """Render one [wikis.<name>] table with the five required keys."""
    return (
        f"[wikis.{name}]\n"
        f'catalog = "{cfg["catalog"]}"\n'
        f'schema = "{cfg["schema"]}"\n'
        f'warehouse_id = "{cfg["warehouse_id"]}"\n'
        f'profile = "{cfg["profile"]}"\n'
        f'user_id = "{cfg["user_id"]}"\n'
    )


def _write_wiki_config(path: Path, name: str, cfg: dict[str, str]) -> None:
    """Insert/replace [wikis.<name>]. Preserves other [wikis.*] sections;
    drops legacy [recorder] (multi-wiki supersedes single-section configs)."""
    existing: dict[str, dict[str, str]] = {}
    if path.exists():
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
            wikis_section = data.get("wikis") or {}
            if isinstance(wikis_section, dict):
                for n, sec in wikis_section.items():
                    if isinstance(sec, dict):
                        existing[n] = {k: str(v) for k, v in sec.items()}
        except tomllib.TOMLDecodeError:
            pass
    existing[name] = dict(cfg)
    body = _RECORDER_HEADER + "\n".join(
        _format_wiki_section(n, c) for n, c in sorted(existing.items())
    )
    path.write_text(body)


def _format_team_toml(team: dict[str, str]) -> str:
    """Render a sharable [team] table — non-secret workspace coordinates."""
    return (
        "# wikibricks-team config — share this file with teammates.\n"
        "# Joiners run: wiki-init --join wikibricks-team.toml\n"
        "# Contains no secrets — just the workspace coordinates.\n\n"
        "[team]\n"
        f'name = "{team["name"]}"\n'
        f'host = "{team["host"]}"\n'
        f'catalog = "{team["catalog"]}"\n'
        f'schema = "{team["schema"]}"\n'
        f'warehouse_id = "{team["warehouse_id"]}"\n'
    )


def _grants_sql(catalog: str, schema: str, warehouse_id: str, principal: str) -> str:
    return (
        f"GRANT USE CATALOG ON CATALOG {catalog} TO `{principal}`;\n"
        f"GRANT USE SCHEMA, SELECT, MODIFY ON SCHEMA {catalog}.{schema} TO `{principal}`;\n"
        f"GRANT CAN_USE ON WAREHOUSE {warehouse_id} TO `{principal}`;"
    )


def _confirm_overwrite(reader: Callable[[str], str], path: Path) -> bool:
    if not path.exists():
        return True
    answer = reader(f"{path} already exists. Overwrite? [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def run_personal(
    reader: Callable[[str], str], out: TextIO, *, config_path: Path
) -> int:
    email = _git_email() or ""
    default_user = _sanitize_user_id(email) if email else None
    default_schema_suffix = _email_local_part(email) if email else "me"

    profile = _prompt(reader, "Databricks CLI profile (from ~/.databrickscfg)")
    catalog = _prompt(reader, "Catalog", default="main")
    schema = _prompt(
        reader, "Schema", default=f"wikibricks_personal_{default_schema_suffix}"
    )
    if not _SCHEMA_RE.match(schema):
        print(f"error: schema '{schema}' must be lowercase a-z, 0-9, _", file=out)
        return 2
    warehouse_id = _prompt(reader, "SQL warehouse ID")
    user_id = _prompt(reader, "Your user ID", default=default_user)

    _write_wiki_config(
        config_path,
        "personal",
        {
            "catalog": catalog,
            "schema": schema,
            "warehouse_id": warehouse_id,
            "profile": profile,
            "user_id": user_id,
        },
    )
    recorder_config.set_active_target("personal")
    print(f"\nWrote [wikis.personal] to {config_path}", file=out)
    print("Active target: personal", file=out)
    print(
        "\nNext steps:\n"
        "  cd <your wikibricks-dev clone> && uv sync --extra recorder\n"
        "  claude mcp add wiki --scope user -- uvx --from "
        "<your wikibricks-dev clone> '.[recorder]' wikibricks-mcp",
        file=out,
    )
    return 0


def run_team_create(
    reader: Callable[[str], str],
    out: TextIO,
    *,
    config_path: Path,
    team_config_path: Path,
) -> int:
    email = _git_email() or ""
    default_user = _sanitize_user_id(email) if email else None

    team_name = _prompt(reader, "Team name (lowercase, no spaces)")
    if not _TEAM_NAME_RE.match(team_name):
        print(
            f"error: team name '{team_name}' must be lowercase a-z, 0-9, _, -",
            file=out,
        )
        return 2

    profile = _prompt(reader, "Databricks CLI profile (from ~/.databrickscfg)")
    catalog = _prompt(reader, "Catalog", default="main")
    schema = _prompt(reader, "Schema", default=f"wikibricks_team_{team_name}")
    if not _SCHEMA_RE.match(schema):
        print(f"error: schema '{schema}' must be lowercase a-z, 0-9, _", file=out)
        return 2
    warehouse_id = _prompt(reader, "SQL warehouse ID")
    host = _prompt(
        reader, "Workspace host (e.g. https://acme.cloud.databricks.com)"
    )
    user_id = _prompt(reader, "Your user ID", default=default_user)

    if team_config_path.exists() and not _confirm_overwrite(reader, team_config_path):
        print("aborted — no changes written.", file=out)
        return 1

    wiki_name = f"team-{team_name}"
    _write_wiki_config(
        config_path,
        wiki_name,
        {
            "catalog": catalog,
            "schema": schema,
            "warehouse_id": warehouse_id,
            "profile": profile,
            "user_id": user_id,
        },
    )
    recorder_config.set_active_target(wiki_name)
    team_config_path.write_text(
        _format_team_toml(
            {
                "name": team_name,
                "host": host,
                "catalog": catalog,
                "schema": schema,
                "warehouse_id": warehouse_id,
            }
        )
    )
    print(f"\nWrote [wikis.{wiki_name}] to {config_path}", file=out)
    print(f"Active target: {wiki_name}", file=out)
    print(f"Wrote {team_config_path} — share this with teammates.", file=out)
    print(
        f"\nFor each teammate `<email>`, run in your workspace:\n\n"
        f"{_grants_sql(catalog, schema, warehouse_id, '<email>')}\n",
        file=out,
    )
    print(
        "Teammates then run:\n"
        f"  wiki-init --join {team_config_path}",
        file=out,
    )
    return 0


def run_team_join(
    reader: Callable[[str], str],
    out: TextIO,
    *,
    team_config_path: Path,
    config_path: Path,
) -> int:
    if not team_config_path.exists():
        print(f"error: team config not found at {team_config_path}", file=out)
        return 2
    try:
        with team_config_path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        print(f"error: invalid TOML at {team_config_path}: {exc}", file=out)
        return 2
    team = data.get("team")
    if not isinstance(team, dict):
        print(f"error: missing [team] section in {team_config_path}", file=out)
        return 2
    for key in ("catalog", "schema", "warehouse_id"):
        if key not in team:
            print(f"error: [team] is missing key '{key}'", file=out)
            return 2

    print(
        f"Joining team '{team.get('name', '?')}' on {team.get('host', '?')}\n"
        f"  catalog={team['catalog']} schema={team['schema']} "
        f"warehouse_id={team['warehouse_id']}\n",
        file=out,
    )

    email = _git_email() or ""
    default_user = _sanitize_user_id(email) if email else None
    profile = _prompt(reader, "Your Databricks CLI profile")
    user_id = _prompt(reader, "Your user ID", default=default_user)

    wiki_name = f"team-{team.get('name', 'unknown')}"
    _write_wiki_config(
        config_path,
        wiki_name,
        {
            "catalog": team["catalog"],
            "schema": team["schema"],
            "warehouse_id": team["warehouse_id"],
            "profile": profile,
            "user_id": user_id,
        },
    )
    recorder_config.set_active_target(wiki_name)
    print(f"\nWrote [wikis.{wiki_name}] to {config_path}", file=out)
    print(f"Active target: {wiki_name}", file=out)
    print(
        "\nIf the workspace returns 401/403 on first write, ask the team\n"
        "owner to run the GRANT statements they got from `wiki-init` for\n"
        f"your principal `{user_id.replace('-at-', '@')}`.",
        file=out,
    )
    return 0


def run(
    args: list[str] | None = None,
    *,
    reader: Callable[[str], str] | None = None,
    out: TextIO | None = None,
    config_path: Path | None = None,
    cwd: Path | None = None,
) -> int:
    """Pure-functional entrypoint — everything is injectable for tests."""
    parser = argparse.ArgumentParser(prog="wiki-init")
    parser.add_argument(
        "--join",
        metavar="TEAM_CONFIG",
        help="Join an existing team — path to a wikibricks-team.toml.",
    )
    ns = parser.parse_args(args)

    reader = reader or input
    out = out or sys.stdout
    config_path = config_path or CONFIG_FILE
    cwd = cwd or Path.cwd()

    if ns.join:
        return run_team_join(
            reader,
            out,
            team_config_path=Path(ns.join),
            config_path=config_path,
        )

    mode = _prompt_choice(reader, "Personal or team wiki?", ["p", "t"], "p")
    if mode == "p":
        return run_personal(reader, out, config_path=config_path)
    sub = _prompt_choice(
        reader, "Are you creating a new team or joining one?", ["c", "j"], "c"
    )
    if sub == "c":
        return run_team_create(
            reader,
            out,
            config_path=config_path,
            team_config_path=cwd / TEAM_CONFIG_FILENAME,
        )
    join_path_str = _prompt(
        reader, "Path to the team's wikibricks-team.toml"
    )
    return run_team_join(
        reader,
        out,
        team_config_path=Path(join_path_str),
        config_path=config_path,
    )


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
