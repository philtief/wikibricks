"""Direct SDK redeploy of the WikiBricks Asset Bundle (no Terraform).

Equivalent to `databricks bundle deploy --target dev` + the
`deploy_wiki_store` notebook run, but driven by the Databricks SDK so it
sidesteps Terraform. Useful when `databricks bundle deploy` fails with
``openpgp: key expired`` (a Terraform-fetcher GPG-key issue in some CLI
versions) or when you want a thin, scriptable redeploy in CI.

Steps (all idempotent):

1. ``CREATE SCHEMA IF NOT EXISTS``
2. Create the seven Delta tables (``pages``, ``pages_history``, ``links``,
   ``sources``, ``wiki_log``, ``pages_vs_source``)
3. Ensure a managed volume ``<catalog>.<schema>.wheels`` exists and upload
   the most recent ``dist/wikibricks-*.whl`` into it
4. Refresh the UC function surface — drop any deployed ``fn_wiki_*``
   outside the enabled set, then ``CREATE OR REPLACE`` the enabled set
5. Verify the final function list

Configuration is fully env-var driven so the script is workspace-agnostic.

Run::

    DATABRICKS_CONFIG_PROFILE=<your-profile> \\
      WIKIBRICKS_CATALOG=<your-catalog> \\
      WIKIBRICKS_SCHEMA=<your-schema> \\
      WIKIBRICKS_WAREHOUSE_ID=<your-warehouse-id> \\
      uv run python scripts/sdk_redeploy.py

Optional ``WIKIBRICKS_ENABLED_UC_FUNCTIONS`` (comma-separated subset) mirrors
the bundle variable of the same name. Empty / unset deploys all eight
functions; pass e.g. ``fn_wiki_search,fn_wiki_read_full,fn_wiki_index`` for
a read-only tool surface.

Run ``uv build`` first so a wheel exists in ``dist/``.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required env var: {name}")
    return value


CATALOG = _require_env("WIKIBRICKS_CATALOG")
SCHEMA = _require_env("WIKIBRICKS_SCHEMA")
WAREHOUSE_ID = _require_env("WIKIBRICKS_WAREHOUSE_ID")

_enabled_raw = os.environ.get("WIKIBRICKS_ENABLED_UC_FUNCTIONS", "").strip()
ENABLED: list[str] | None = (
    [name.strip() for name in _enabled_raw.split(",") if name.strip()]
    if _enabled_raw
    else None
)

WHEEL_DIR_VOLUME = f"/Volumes/{CATALOG}/{SCHEMA}/wheels"

from databricks.sdk import WorkspaceClient  # noqa: E402

from wikibricks.ops import (  # noqa: E402
    create_schema_sql,
    create_tables_sql,
    create_uc_functions_sql,
    drop_uc_functions_sql,
    migrate_tables_sql,
)


def _exec(w: WorkspaceClient, sql: str) -> str:
    r = w.statement_execution.execute_statement(warehouse_id=WAREHOUSE_ID, statement=sql)
    state = r.status.state
    return state.value if hasattr(state, "value") else str(state)


def _ensure_volume(w: WorkspaceClient) -> None:
    try:
        w.volumes.read(f"{CATALOG}.{SCHEMA}.wheels")
        print(f"Volume {CATALOG}.{SCHEMA}.wheels exists")
    except Exception:
        from databricks.sdk.service.catalog import VolumeType

        w.volumes.create(
            catalog_name=CATALOG,
            schema_name=SCHEMA,
            name="wheels",
            volume_type=VolumeType.MANAGED,
        )
        print(f"Volume {CATALOG}.{SCHEMA}.wheels created")


def _upload_wheel(w: WorkspaceClient) -> None:
    wheels = sorted(
        Path("dist").glob("wikibricks-*.whl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not wheels:
        raise SystemExit("no wheel in dist/ — run `uv build` first")
    src = wheels[0]
    dst = f"{WHEEL_DIR_VOLUME}/{src.name}"
    with open(src, "rb") as f:
        w.files.upload(dst, f, overwrite=True)
    print(f"Uploaded {src.name} -> {dst}")


def main() -> None:
    w = WorkspaceClient()

    print(f"\n=== schema ({CATALOG}.{SCHEMA}) ===")
    print(_exec(w, create_schema_sql()))

    print("\n=== tables ===")
    for stmt in create_tables_sql():
        head = stmt.strip().split("\n")[0][:80]
        print(f"  {_exec(w, stmt)} :: {head}")

    print("\n=== migrations (idempotent ALTERs for schema drift) ===")
    for stmt in migrate_tables_sql():
        head = stmt[:80]
        print(f"  {_exec(w, stmt)} :: {head}")

    print("\n=== volume + wheel ===")
    _ensure_volume(w)
    _upload_wheel(w)

    print("\n=== drop UC functions outside enabled set ===")
    for stmt in drop_uc_functions_sql(enabled=ENABLED):
        name = stmt.strip().split()[-1]
        print(f"  {_exec(w, stmt)} :: drop {name}")

    print("\n=== create UC functions in enabled set ===")
    for stmt in create_uc_functions_sql(warehouse_id=WAREHOUSE_ID, enabled=ENABLED):
        head = stmt.strip().split("(", 1)[0].split()[-1]
        print(f"  {_exec(w, stmt)} :: create {head}")
        time.sleep(0.5)  # warehouse rate-limit safety

    print("\n=== verify deployed function set ===")
    rows = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=(
            f"SELECT routine_name FROM {CATALOG}.information_schema.routines "
            f"WHERE specific_schema = '{SCHEMA}' AND routine_name LIKE 'fn_wiki_%' "
            "ORDER BY routine_name"
        ),
    )
    if rows.result and rows.result.data_array:
        for row in rows.result.data_array:
            print(f"  {row[0]}")
    else:
        print("  (no rows returned)")

    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
