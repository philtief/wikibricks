"""Zero-touch local capture, maintenance, and optional archive synchronization."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator
from uuid import UUID

from wikibricks.config import WikiBricksConfig, load_config
from wikibricks.storage.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from wikibricks.postgres_store import PostgresStore

_LOGGER = logging.getLogger(__name__)
_LOCK_NAME = "wikibricks:background-automation"
_LOCAL_CURSOR = "automation:local-maintenance"
_REMOTE_CURSOR = "automation:lakebase-sync"


def _due(store: Any, target: str, interval_hours: int, now: float) -> bool:
    cursor = store.get_sync_cursor(target)
    latest = max(
        float(cursor.get("attempted_at", 0)),
        float(cursor.get("completed_at", 0)),
    )
    return now - latest >= interval_hours * 3600


def _mark_attempt(store: Any, target: str, now: float) -> None:
    store.set_sync_cursor(target, {"attempted_at": now})


def _mark_complete(store: Any, target: str, now: float) -> None:
    store.set_sync_cursor(target, {"attempted_at": now, "completed_at": now})


@contextmanager
def _single_runner(store: Any) -> Iterator[bool]:
    if isinstance(store, SQLiteStore):
        owner = f"{os.getpid()}:{id(store)}"
        yield store.acquire_lease(_LOCK_NAME, owner, 600)
        return
    with store.connection() as conn:
        acquired = bool(
            conn.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s))",
                (_LOCK_NAME,),
            ).fetchone()[0]
        )
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(
                    "SELECT pg_advisory_unlock(hashtext(%s))",
                    (_LOCK_NAME,),
                )


def _pending_run_ids(store: PostgresStore) -> list[Any]:
    with store.connection() as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT run_id FROM curation_runs "
                "WHERE received_at IS NOT NULL AND applied_at IS NULL "
                "ORDER BY input_watermark, published_at, run_id"
            ).fetchall()
        ]


def run_remote_cycle(
    local: PostgresStore | SQLiteStore,
    config: WikiBricksConfig,
    *,
    remote_url_factory: Callable[[Any], str] | None = None,
) -> dict[str, Any]:
    """Push, pull, and safely apply remote work without a user command."""
    if not config.sync_profile or not config.sync_project:
        return {"status": "disabled"}

    from wikibricks.curation import apply_run, resolve_conflict
    from wikibricks.remote.lakebase import (
        LakebaseTarget,
        pull_curation_patches,
        sync_to_archive,
    )

    target = LakebaseTarget(
        project=config.sync_project,
        branch=config.sync_branch,
        endpoint=config.sync_endpoint,
        database=config.sync_database,
        profile=config.sync_profile,
    )
    database_url = (
        remote_url_factory(target)
        if remote_url_factory is not None
        else target.fresh_database_url()
    )
    archive = sync_to_archive(
        local,
        database_url,
        limit=config.sync_batch_size,
        drain=True,
    )
    database_url = (
        remote_url_factory(target)
        if remote_url_factory is not None
        else target.fresh_database_url()
    )
    pulled = pull_curation_patches(local, database_url)
    applications = []
    kept_local_runs: set[str] = set()
    for run_id in _pending_run_ids(local):
        result = apply_run(local, run_id, policy=config.sync_apply_policy)
        applications.append(result)
        for group in result["groups"]:
            if group["status"] != "conflict":
                continue
            resolve_conflict(
                local,
                run_id,
                group_id=UUID(group["group_id"]),
                action="keep_local",
            )
            kept_local_runs.add(str(run_id))
    return {
        "status": "complete",
        "archive": archive,
        "pulled": pulled,
        "applications": applications,
        "kept_local_runs": sorted(kept_local_runs),
    }


def run_background_cycle(
    config: WikiBricksConfig | None = None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Run due work once. Each subsystem fails independently."""
    active = config or load_config()
    if not active.automation_enabled:
        return {"status": "disabled"}
    current_time = time.time() if now is None else now
    database_target: str | Any = active.database_url or active.database_path
    store = (
        SQLiteStore(active.database_path)
    )
    if active.database_url:
        from wikibricks.postgres_store import PostgresStore

        store = PostgresStore(active.database_url)
    store.migrate()
    result: dict[str, Any] = {"status": "complete"}
    with _single_runner(store) as acquired:
        if not acquired:
            return {"status": "busy"}

        if (
            active.sync_profile
            and active.sync_project
            and _due(store, _REMOTE_CURSOR, active.sync_interval_hours, current_time)
        ):
            try:
                _mark_attempt(store, _REMOTE_CURSOR, current_time)
                result["remote"] = run_remote_cycle(store, active)
                _mark_complete(store, _REMOTE_CURSOR, current_time)
            except Exception as exc:
                _LOGGER.warning("WikiBricks remote sync failed: %s", exc)
                result["remote_error"] = str(exc)

        if _due(
            store,
            _LOCAL_CURSOR,
            active.automation_local_maintenance_hours,
            current_time,
        ):
            try:
                from wikibricks.maintenance import curate_database

                result["maintenance"] = curate_database(database_target)
                _mark_complete(store, _LOCAL_CURSOR, current_time)
            except Exception as exc:
                _LOGGER.warning("WikiBricks local maintenance failed: %s", exc)
                result["maintenance_error"] = str(exc)
    return result


async def run_background_loop() -> None:
    """Run automation beside an MCP server without delaying tool calls."""
    while True:
        delay = 300
        try:
            config = load_config()
            delay = config.automation_poll_seconds
            if config.automation_enabled:
                await asyncio.to_thread(run_background_cycle, config)
        except Exception as exc:
            _LOGGER.warning("WikiBricks background cycle failed: %s", exc)
        await asyncio.sleep(delay)


def run_background_worker(stop: threading.Event) -> None:
    """Run maintenance for a native host until its lifecycle ends."""
    while not stop.is_set():
        delay = 300
        try:
            config = load_config()
            delay = config.automation_poll_seconds
            if config.automation_enabled:
                run_background_cycle(config)
        except Exception as exc:
            _LOGGER.warning("WikiBricks background cycle failed: %s", exc)
        stop.wait(delay)


__all__ = [
    "run_background_cycle",
    "run_background_loop",
    "run_background_worker",
    "run_remote_cycle",
]
