"""Bounded Omnigent server access using only the Python standard library."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any


def list_sessions(
    server: str,
    token: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[dict[str, Any]]:
    """List all accessible sessions, following the server's opaque cursor."""
    sessions: list[dict[str, Any]] = []
    after: str | None = None
    for _ in range(1000):
        parameters: dict[str, str] = {"limit": "100"}
        if after is not None:
            parameters["after"] = after
        request = urllib.request.Request(
            f"{server.rstrip('/')}/v1/sessions?{urllib.parse.urlencode(parameters)}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with opener(request) as response:
            payload = json.load(response)
        sessions.extend(item for item in payload.get("data", []) if isinstance(item, dict))
        if not payload.get("has_more"):
            return sessions
        last_id = payload.get("last_id")
        if not last_id or last_id == after:
            break
        after = str(last_id)
    return sessions


def export_session(
    server: str,
    session_id: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> list[str]:
    """Run the Omnigent CLI export and return its JSONL lines."""
    with tempfile.NamedTemporaryFile(prefix="wikibricks-omnigent-", suffix=".jsonl") as handle:
        output = Path(handle.name)
        result = runner(
            [
                "omnigent",
                "session",
                "export",
                "--id",
                session_id,
                "--server",
                server,
                "-o",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if result is None:
            raise RuntimeError("omnigent session export returned no result")
        return output.read_text(encoding="utf-8").splitlines()


def resolve_token(profile: str) -> str:
    """Resolve the explicit token from the environment or the Databricks CLI."""
    token = os.environ.get("WIKIBRICKS_OMNIGENT_TOKEN")
    if token:
        return token
    result = subprocess.run(
        ["databricks", "auth", "token", "--profile", profile],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    token = payload.get("access_token")
    if not token:
        raise ValueError("Databricks auth token response did not contain access_token")
    return str(token)
