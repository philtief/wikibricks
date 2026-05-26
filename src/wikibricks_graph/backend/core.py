"""Workspace client resolution — dual mode (Databricks Apps remote vs local dev).

Detects the runtime via DATABRICKS_APP_NAME. Remote → SP-injected creds via
WorkspaceClient(); local → profile from DATABRICKS_PROFILE env var.
"""

from __future__ import annotations

import os
from functools import lru_cache

from databricks.sdk import WorkspaceClient

from backend.services.graph_cache import GraphCache

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


_graph_cache: GraphCache | None = None


def get_user_ws():
    """FastAPI dependency for the user-OBO WorkspaceClient.

    Tests override via `app.dependency_overrides[get_user_ws] = lambda: fake_ws`.
    In production (Databricks Apps) this resolves to a per-request OBO
    client; we use the same factory here for simplicity. Future hardening
    can switch to true per-request OBO via x-forwarded-access-token.
    """
    return get_workspace_client()


def get_graph_cache() -> GraphCache:
    """Process-wide singleton TTLCache. Tests can patch _graph_cache."""
    global _graph_cache
    if _graph_cache is None:
        _graph_cache = GraphCache(ttl_seconds=600)
    return _graph_cache
