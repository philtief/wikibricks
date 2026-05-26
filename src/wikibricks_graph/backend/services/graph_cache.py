"""In-process graph snapshot cache + ETag.

Single TTLCache holds one graph snapshot per (catalog, schema) key.
Snapshots auto-expire after `ttl_seconds`; manual `invalidate` forces a
re-fetch on next access (used by a future "rebuild" endpoint).

ETag is `blake2b(json.dumps(graph, sort_keys=True))[:16]` — stable for
identical content, fast for 1-2 MB payloads.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from cachetools import TTLCache


class GraphCache:
    def __init__(self, ttl_seconds: int = 600) -> None:
        self._cache: TTLCache = TTLCache(maxsize=8, ttl=ttl_seconds)

    @staticmethod
    def _compute_etag(payload: dict[str, Any]) -> str:
        serialized = json.dumps(
            {"nodes": payload.get("nodes", []), "edges": payload.get("edges", [])},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.blake2b(serialized.encode(), digest_size=8).hexdigest()

    def get_or_fetch(
        self,
        *,
        key: tuple[str, ...],
        fetcher: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        raw = fetcher()
        snapshot = {
            "nodes": raw.get("nodes", []),
            "edges": raw.get("edges", []),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "etag": self._compute_etag(raw),
        }
        self._cache[key] = snapshot
        return snapshot

    def invalidate(self, *, key: tuple[str, ...]) -> None:
        self._cache.pop(key, None)
