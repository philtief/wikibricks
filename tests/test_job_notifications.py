"""Every job YAML under resources/ must configure on_failure email alerts.

Cron jobs that fail silently are the worst kind of operational debt: the
Monday-only `wikibricks_autoeval` and `wikibricks_deploy` jobs ran red for
five consecutive weeks (2026-04-27 through 2026-05-25) before anyone
noticed, because no notification was wired up. This test fails if any job
loses its failure alert.
"""

from __future__ import annotations

from pathlib import Path

import yaml

RESOURCES = Path(__file__).parent.parent / "resources"
JOB_YAMLS = sorted(RESOURCES.glob("*_job.yml"))


def test_resources_dir_has_job_files() -> None:
    assert JOB_YAMLS, f"no *_job.yml found under {RESOURCES}"


def test_every_job_has_on_failure_email() -> None:
    failures: list[str] = []
    for path in JOB_YAMLS:
        with path.open() as f:
            doc = yaml.safe_load(f)
        jobs = ((doc or {}).get("resources") or {}).get("jobs") or {}
        for job_key, job in jobs.items():
            recipients = (
                (job.get("email_notifications") or {}).get("on_failure") or []
            )
            if not recipients:
                failures.append(f"{path.name}::{job_key}")
    assert not failures, (
        "jobs missing email_notifications.on_failure (silent cron risk): "
        + ", ".join(failures)
    )
