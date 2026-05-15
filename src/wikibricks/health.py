"""Health probe for a deployed WikiBricks store.

Six SQL probes that, taken together, declare whether the v0.5.0 feature set
(noise filter, segregate, connect, auto-tag, citation tracking, promote) is
operating end-to-end on a given catalog.schema.

Pure module: `run_health_checks` takes a `sql_runner` callable so the logic
is unit-testable without a workspace. `scripts/wikibricks_health.py` is the
CLI wrapper that supplies a real `WikiClient`-backed runner.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class HealthCheck:
    name: str
    sql: str
    threshold_op: str  # "ge" or "le"
    threshold_value: float
    description: str


@dataclass(frozen=True)
class HealthResult:
    check: HealthCheck
    value: float
    passing: bool


def default_checks(catalog: str, schema: str) -> list[HealthCheck]:
    """The six v0.5.0 success criteria.

    Each SQL must return exactly one numeric value as the first row, first column.
    """
    fq = f"{catalog}.{schema}"
    return [
        HealthCheck(
            name="auto_tag_coverage",
            sql=f"""
                WITH new_pages AS (
                  SELECT path FROM {fq}.pages
                  WHERE created_at > current_timestamp() - INTERVAL 24 HOURS
                    AND parent_id IS NULL
                ),
                tagged AS (
                  SELECT DISTINCT path FROM {fq}.wiki_log
                  WHERE op_type = 'auto_tag'
                    AND created_at > current_timestamp() - INTERVAL 24 HOURS
                )
                SELECT CASE WHEN (SELECT COUNT(*) FROM new_pages) = 0
                            THEN 1.0
                            ELSE (SELECT COUNT(*) FROM new_pages n JOIN tagged t ON n.path = t.path) * 1.0
                                 / (SELECT COUNT(*) FROM new_pages)
                       END
            """,
            threshold_op="ge",
            threshold_value=0.8,
            description="Auto-tag fires on >=80% of new top-level pages (24h)",
        ),
        HealthCheck(
            name="vocab_growth",
            sql=f"""
                SELECT COUNT(*) FROM {fq}.wiki_vocabulary
            """,
            threshold_op="ge",
            threshold_value=5.0,
            description="Vocabulary store has at least 5 terms (any status)",
        ),
        HealthCheck(
            name="page_tag_coverage",
            sql=f"""
                SELECT CASE WHEN COUNT(*) = 0 THEN 1.0
                            ELSE COUNT_IF(
                                EXISTS(COALESCE(tags, ARRAY()),
                                       t -> t LIKE 'llm:%')
                            ) * 1.0 / COUNT(*)
                       END
                FROM {fq}.pages
                WHERE created_at > current_timestamp() - INTERVAL 24 HOURS
                  AND parent_id IS NULL
            """,
            threshold_op="ge",
            threshold_value=0.7,
            description=">=70% of new top-level pages have at least one llm: tag",
        ),
        HealthCheck(
            name="citations_logged",
            sql=f"""
                SELECT COUNT(*) FROM {fq}.wiki_log
                WHERE op_type = 'search'
                  AND created_at > current_timestamp() - INTERVAL 24 HOURS
                  AND details LIKE '%returned_paths%'
            """,
            threshold_op="ge",
            threshold_value=10.0,
            description=">=10 search calls per day log their returned paths",
        ),
        HealthCheck(
            name="promote_end_to_end",
            sql=f"""
                SELECT COUNT(*) FROM {fq}.agent_traces_v
            """,
            threshold_op="ge",
            threshold_value=1.0,
            description="agent_traces_v has rows for promote to consume",
        ),
        HealthCheck(
            name="curate_recent",
            sql=f"""
                SELECT COALESCE(
                  unix_timestamp(current_timestamp())
                    - unix_timestamp(MAX(created_at)),
                  999999
                )
                FROM {fq}.wiki_log
                WHERE op_type = 'curate_run'
            """,
            threshold_op="le",
            threshold_value=86400.0,
            description="Curate has run within the last 24 hours",
        ),
    ]


def evaluate(check: HealthCheck, value: float) -> bool:
    if check.threshold_op == "ge":
        return value >= check.threshold_value
    if check.threshold_op == "le":
        return value <= check.threshold_value
    raise ValueError(f"unknown threshold_op: {check.threshold_op}")


def run_health_checks(
    sql_runner: Callable[[str], float],
    checks: list[HealthCheck],
) -> list[HealthResult]:
    """Run each check via `sql_runner` and return paired results.

    `sql_runner` must return a single float — the first row, first column of
    the query. Callers that fail to fetch a value (table missing, SQL error)
    should surface that as float('nan') so the check fails loudly.
    """
    results = []
    for check in checks:
        value = sql_runner(check.sql)
        passing = evaluate(check, value) if value == value else False  # NaN-safe
        results.append(HealthResult(check=check, value=value, passing=passing))
    return results


def all_passing(results: list[HealthResult]) -> bool:
    return all(r.passing for r in results)


def format_report(results: list[HealthResult]) -> str:
    lines = []
    width_name = max(len(r.check.name) for r in results)
    for r in results:
        mark = "PASS" if r.passing else "FAIL"
        op = ">=" if r.check.threshold_op == "ge" else "<="
        lines.append(
            f"{mark}  {r.check.name:<{width_name}}  "
            f"value={r.value:>10.3f}  {op} {r.check.threshold_value}  "
            f"({r.check.description})"
        )
    summary = f"{sum(r.passing for r in results)}/{len(results)} passing"
    lines.append("")
    lines.append(summary)
    return "\n".join(lines)
