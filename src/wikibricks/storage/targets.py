"""Pure local/remote storage target classification."""

from pathlib import Path


def is_postgres_target(target: str | Path | None) -> bool:
    return isinstance(target, str) and (
        target.startswith(("postgresql://", "postgres://"))
        or ("dbname=" in target and ("host=" in target or "user=" in target))
    )


__all__ = ["is_postgres_target"]
