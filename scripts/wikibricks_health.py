"""CLI wrapper around `wikibricks.health` for live probing of a deployed wiki.

Usage:
    uv run python scripts/wikibricks_health.py \\
        --profile fe-vm-agent-marketplace \\
        --catalog agent_marketplace_catalog \\
        --schema  wikibricks_personal_philipp \\
        --warehouse-id 41754a8563a43a49

Exits non-zero if any of the six v0.5.0 health checks fail.
"""

import argparse
import math
import sys

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

from wikibricks.health import (
    all_passing,
    default_checks,
    format_report,
    run_health_checks,
)


def _make_sql_runner(ws: WorkspaceClient, warehouse_id: str):
    def runner(sql: str) -> float:
        resp = ws.statement_execution.execute_statement(
            warehouse_id=warehouse_id,
            statement=sql.strip(),
            wait_timeout="30s",
        )
        if resp.status.state != StatementState.SUCCEEDED:
            return math.nan
        data = resp.result.data_array if resp.result else None
        if not data or not data[0]:
            return math.nan
        cell = data[0][0]
        if cell is None:
            return math.nan
        try:
            return float(cell)
        except (TypeError, ValueError):
            return math.nan

    return runner


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True, help="Databricks CLI profile name")
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--warehouse-id", required=True)
    args = p.parse_args()

    ws = WorkspaceClient(profile=args.profile)
    runner = _make_sql_runner(ws, args.warehouse_id)
    results = run_health_checks(runner, default_checks(args.catalog, args.schema))

    print(format_report(results))
    return 0 if all_passing(results) else 1


if __name__ == "__main__":
    sys.exit(main())
