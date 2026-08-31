from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_databricks_sdk_is_optional_not_a_base_dependency():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    assert not any(item.startswith("databricks-sdk") for item in project["dependencies"])
    assert any(
        item.startswith("databricks-sdk")
        for item in project["optional-dependencies"]["databricks"]
    )


def test_base_package_excludes_incompatible_mcp_major_version():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]

    assert "mcp>=1.0,<2" in dependencies


def test_base_modules_import_when_databricks_is_blocked():
    code = """
import importlib.abc
import sys

class BlockDatabricks(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'databricks' or fullname.startswith('databricks.'):
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockDatabricks())
import wikibricks
import wikibricks.client
import wikibricks.ops
import wikibricks.postgres_store
import wikibricks_databricks.lakebase_sync
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
