"""Full-text and trigram search across pages, sessions, and archives."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wikibricks.storage.store import PostgresStore


class SearchRepository:
    def __init__(self, store: PostgresStore) -> None:
        self.store = store

    def query(self, query: str, *, num_results: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        pattern = f"%{query}%"
        statement = """
            WITH q AS (SELECT websearch_to_tsquery('simple', %s) AS value),
            page_hits AS (
                SELECT p.page_id AS id, p.path, v.title, v.page_type,
                       v.content_text, v.tags, v.version,
                       GREATEST(
                           CASE WHEN p.path = %s THEN 10.0 ELSE 0.0 END,
                           similarity(p.path, %s) * 3.0,
                           similarity(v.title, %s) * 2.0,
                           COALESCE(max(ts_rank_cd(c.search_vector, q.value)), 0.0)
                       ) AS score
                FROM pages p
                JOIN page_versions v ON v.version_id = p.current_version_id
                LEFT JOIN page_search_chunks c ON c.version_id = v.version_id
                CROSS JOIN q
                WHERE p.status = 'active'
                  AND (
                    p.path ILIKE %s
                    OR v.title ILIKE %s
                    OR c.search_vector @@ q.value
                  )
                GROUP BY p.page_id, p.path, v.title, v.page_type,
                         v.content_text, v.tags, v.version
            ),
            session_hits AS (
                SELECT s.session_id AS id, s.page_path AS path, s.title,
                       'session'::text AS page_type, ''::text AS content_text,
                       ARRAY['session', 'harness:' || s.harness]::text[] AS tags,
                       1 AS version,
                       GREATEST(
                           CASE WHEN s.page_path = %s THEN 10.0 ELSE 0.0 END,
                           similarity(s.page_path, %s) * 3.0,
                           similarity(s.title, %s) * 2.0,
                           COALESCE(max(ts_rank_cd(c.search_vector, q.value)), 0.0)
                       ) AS score
                FROM sessions s
                JOIN session_events e
                  ON e.session_id = s.session_id AND e.active
                JOIN session_event_versions v
                  ON v.version_id = e.current_version_id
                LEFT JOIN session_search_chunks c
                  ON c.version_id = v.version_id
                CROSS JOIN q
                WHERE s.page_path ILIKE %s
                   OR s.title ILIKE %s
                   OR c.search_vector @@ q.value
                GROUP BY s.session_id, s.page_path, s.title, s.harness
            ),
            archive_hits AS (
                SELECT NULL::uuid AS id, a.path, a.title,
                       'archive'::text AS page_type, a.content_text, a.tags,
                       a.snapshot_version AS version,
                       GREATEST(
                           CASE WHEN a.path = %s THEN 9.0 ELSE 0.0 END,
                           similarity(a.path, %s) * 2.5,
                           similarity(a.title, %s) * 1.5,
                           CASE
                             WHEN to_tsvector('simple', a.content_text) @@ q.value
                             THEN ts_rank_cd(
                               to_tsvector('simple', a.content_text),
                               q.value
                             ) - 0.25
                             ELSE 0.0
                           END
                       ) AS score
                FROM archive_pages a CROSS JOIN q
                WHERE a.path ILIKE %s
                   OR a.title ILIKE %s
                   OR to_tsvector('simple', a.content_text) @@ q.value
            )
            SELECT * FROM (
                SELECT * FROM page_hits
                UNION ALL SELECT * FROM session_hits
                UNION ALL SELECT * FROM archive_hits
            ) hits
            ORDER BY score DESC, path
            LIMIT %s
        """
        params = (
            query,
            query,
            query,
            query,
            pattern,
            pattern,
            query,
            query,
            query,
            pattern,
            pattern,
            query,
            query,
            query,
            pattern,
            pattern,
            num_results,
        )
        with self.store.connection() as conn:
            rows = conn.execute(statement, params).fetchall()
        return [
            {
                "page_id": str(row[0]) if row[0] is not None else None,
                "path": row[1],
                "title": row[2],
                "page_type": row[3],
                "content_text": row[4],
                "tags": list(row[5] or []),
                "version": row[6],
                "score": float(row[7]),
            }
            for row in rows
        ]
