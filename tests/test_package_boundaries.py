from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import wikibricks

ROOT = Path(__file__).resolve().parents[1]


def test_public_package_exposes_only_local_runtime():
    assert wikibricks.__all__ == ["SQLiteStore", "WikiClient", "make_agent_tools"]


def test_databricks_sdk_is_optional_not_a_base_dependency():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    assert not any(item.startswith("databricks-sdk") for item in project["dependencies"])
    assert any(
        item.startswith("databricks-sdk")
        for item in project["optional-dependencies"]["lakebase"]
    )


def test_base_package_excludes_incompatible_mcp_major_version():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]

    assert "mcp>=1.0,<2" in dependencies


def test_postgres_driver_is_optional():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    assert not any(item.startswith("psycopg") for item in project["dependencies"])
    assert "psycopg[binary]==3.2.13" in project["optional-dependencies"]["lakebase"]
    assert "psycopg[binary]==3.2.13" in project["optional-dependencies"][
        "postgres-migration"
    ]


def test_public_ci_uses_public_packages_and_installs_postgres():
    lockfile = (ROOT / "uv.lock").read_text()

    assert "pypi-proxy.cloud.databricks.com" not in lockfile
    for name in ("ci.yml", "release.yml"):
        workflow = (ROOT / ".github" / "workflows" / name).read_text()
        assert "Install PostgreSQL" in workflow
        assert "uv sync --locked --all-extras --dev" in workflow
        assert "uv run --locked pytest -q" in workflow


def test_base_modules_import_when_postgres_and_databricks_are_blocked():
    code = """
import importlib.abc
import sys

class BlockDatabricks(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'databricks', 'psycopg'} or fullname.startswith(('databricks.', 'psycopg.')):
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockDatabricks())
import wikibricks
import wikibricks.client
import wikibricks.automation
import wikibricks.cli
import wikibricks.curation
import wikibricks.maintenance
import wikibricks.remote.lakebase
print('ok')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_distribution_has_no_claude_only_recorder():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        scripts = tomllib.load(handle)["project"]["scripts"]

    assert "wikibricks-hook" not in scripts
    assert not (ROOT / "plugin").exists()
    assert not (ROOT / ".claude-plugin").exists()
    assert not (ROOT / "src/wikibricks/adapters/claude_code_hook.py").exists()
    assert not (ROOT / "src/wikibricks/adapters/claude_code_buffer.py").exists()
