"""Workspace client resolution — dual mode (Databricks Apps remote vs local dev).

Detects the runtime via DATABRICKS_APP_NAME. Remote → SP-injected creds via
WorkspaceClient(); local → profile from DATABRICKS_PROFILE env var.
"""

from __future__ import annotations

import os
from functools import lru_cache

from databricks.sdk import WorkspaceClient

IS_DATABRICKS_APP = bool(os.environ.get("DATABRICKS_APP_NAME"))


def get_workspace_client() -> WorkspaceClient:
    """Return an authenticated WorkspaceClient.

    Remote (in Databricks Apps): uses the app's service-principal creds
    injected by the runtime. Local dev: uses the named profile from
    ~/.databrickscfg via DATABRICKS_PROFILE env var.
    """
    if IS_DATABRICKS_APP:
        return WorkspaceClient()
    profile = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
    return WorkspaceClient(profile=profile)


@lru_cache(maxsize=1)
def get_app_config() -> dict[str, str]:
    """Read WIKIBRICKS_* env vars once per process."""
    required = ("WIKIBRICKS_CATALOG", "WIKIBRICKS_SCHEMA", "WIKIBRICKS_WAREHOUSE_ID")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing required env vars: {missing}")
    return {
        "catalog": os.environ["WIKIBRICKS_CATALOG"],
        "schema": os.environ["WIKIBRICKS_SCHEMA"],
        "warehouse_id": os.environ["WIKIBRICKS_WAREHOUSE_ID"],
    }
