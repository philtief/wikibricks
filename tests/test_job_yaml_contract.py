"""YAML contract tests for `resources/wiki_curate_job.yml`.

Locks in the bundle wiring so a silent YAML edit (or merge conflict) can't
disconnect a task from its dependencies or strip a critical parameter.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

JOB_YML = Path(__file__).parent.parent / "resources" / "wiki_curate_job.yml"


def _load_job() -> dict:
    return yaml.safe_load(JOB_YML.read_text())["resources"]["jobs"]["wikibricks_curate"]


def _task(job: dict, key: str) -> dict:
    for t in job["tasks"]:
        if t["task_key"] == key:
            return t
    raise AssertionError(f"task {key!r} missing from job")


class TestJobTaskDag:
    def test_four_tasks_present(self):
        job = _load_job()
        keys = sorted(t["task_key"] for t in job["tasks"])
        assert keys == ["curate", "promote", "segregate", "tag"]

    def test_segregate_depends_on_curate(self):
        job = _load_job()
        seg = _task(job, "segregate")
        deps = [d["task_key"] for d in seg.get("depends_on", [])]
        assert deps == ["curate"]

    def test_tag_depends_on_curate(self):
        job = _load_job()
        tag = _task(job, "tag")
        deps = [d["task_key"] for d in tag.get("depends_on", [])]
        assert deps == ["curate"]

    def test_promote_depends_on_curate(self):
        job = _load_job()
        prom = _task(job, "promote")
        deps = [d["task_key"] for d in prom.get("depends_on", [])]
        assert deps == ["curate"]


class TestTagTaskParameters:
    def test_tag_uses_wiki_tag_notebook(self):
        job = _load_job()
        tag = _task(job, "tag")
        assert tag["notebook_task"]["notebook_path"].endswith("wiki_tag.py")

    def test_tag_passes_required_params(self):
        job = _load_job()
        params = _task(job, "tag")["notebook_task"]["base_parameters"]
        for key in ("catalog", "schema", "warehouse_id", "tag_endpoint",
                    "max_pages_per_run", "tag_concurrency",
                    "approve_threshold", "max_tags_per_page"):
            assert key in params, f"tag task missing {key}"


class TestPromoteWiredToTracesView:
    def test_promote_traces_table_points_at_agent_traces_v(self):
        # Phase A1 contract: promote must consume the citation-tracking view,
        # not the default <catalog>.<schema>.agent_traces table.
        job = _load_job()
        params = _task(job, "promote")["notebook_task"]["base_parameters"]
        assert "traces_table" in params, (
            "promote task missing traces_table parameter (Phase A1)"
        )
        assert "agent_traces_v" in params["traces_table"], (
            f"traces_table must point at agent_traces_v, got: {params['traces_table']}"
        )


class TestServerlessLibrariesContract:
    """Bug 2 contract — wikibricks wheel installs via serverless env, not %pip in notebooks.

    Regression: prior to the fix, every notebook had a literal
    `%pip install /Volumes/<catalog>/<schema>/wheels/...` line. The bundle
    deploys notebooks verbatim, so the placeholders broke fresh deploys until
    someone hand-substituted them in the workspace. The fix moved wheel
    install into `environments[].spec.dependencies` so it's bundle-templated.
    """

    def test_serverless_env_installs_wikibricks_wheel(self):
        job = _load_job()
        envs = {e["environment_key"]: e for e in job["environments"]}
        assert "serverless" in envs
        deps = envs["serverless"]["spec"].get("dependencies", [])
        assert any("wikibricks" in d and ".whl" in d for d in deps), (
            f"serverless env must declare a wikibricks wheel dependency; got: {deps}"
        )

    def test_serverless_dep_path_uses_bundle_variables(self):
        job = _load_job()
        envs = {e["environment_key"]: e for e in job["environments"]}
        deps = envs["serverless"]["spec"]["dependencies"]
        wheel_dep = next(d for d in deps if "wikibricks" in d and ".whl" in d)
        # Must reference variables, not hardcoded catalog/schema. Otherwise the
        # bundle isn't portable across wikis.
        assert "${var.catalog}" in wheel_dep
        assert "${var.schema}" in wheel_dep
        assert "${var.version}" in wheel_dep, (
            "wheel path must reference ${var.version} so a release bump in "
            "databricks.yml propagates without hand-editing the resource yml"
        )

    def test_no_notebook_has_volumes_pip_install_directive(self):
        """No notebook may include `# MAGIC %pip install /Volumes/...whl`.

        That pattern was the source of Bug 2: the bundle deploys it verbatim,
        the placeholders never substitute, and fresh deploys break.
        """
        from pathlib import Path
        nb_dir = Path(__file__).parent.parent / "notebooks"
        offenders = []
        for nb in nb_dir.glob("*.py"):
            for line in nb.read_text().split("\n"):
                if (line.startswith("# MAGIC %pip install")
                        and "/Volumes/" in line):
                    offenders.append(f"{nb.name}: {line[:80]}")
        assert not offenders, (
            "Found %pip install /Volumes/... directives in notebook source. "
            "Use serverless env dependencies instead. Offenders:\n  "
            + "\n  ".join(offenders)
        )
