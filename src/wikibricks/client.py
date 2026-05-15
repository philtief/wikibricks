"""WikiClient: high-level API for reading and writing wiki pages on Databricks."""

import json
import re

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

from wikibricks.ops import (
    HISTORY_TABLE,
    LINKS_TABLE,
    LOG_TABLE,
    PAGES_TABLE,
    PAGES_VS_SOURCE_TABLE,
    SOURCES_TABLE,
    VALID_LINK_ORIGINS,
    VALID_LINK_TYPES,
    VOCABULARY_TABLE,
    VS_INDEX,
    delete_broken_links_sql,
    graph_neighbors_sql,
)


class WikiClient:
    """Databricks-native wiki client for AI agents.

    Usage::

        from wikibricks import WikiClient

        wiki = WikiClient(warehouse_id="abc123")
        wiki.write_page("topics/my-topic", "My Topic", content_json)
        page = wiki.read_page("topics/my-topic")
        results = wiki.search("search query")
    """

    def __init__(self, warehouse_id: str, workspace_client: WorkspaceClient | None = None):
        self.ws = workspace_client or WorkspaceClient()
        self.warehouse_id = warehouse_id

    def _exec(self, sql: str):
        """Execute SQL and return the response. Raises on failure."""
        resp = self.ws.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id,
            statement=sql.strip(),
            wait_timeout="30s",
        )
        if resp.status.state != StatementState.SUCCEEDED:
            error = resp.status.error
            raise RuntimeError(f"SQL execution failed: {error}")
        return resp

    def _escape(self, value: str) -> str:
        """Escape backslashes and single quotes for Databricks SQL string literals."""
        return value.replace("\\", "\\\\").replace("'", "\\'")

    @staticmethod
    def _manifest_columns(manifest):
        """Return [col, ...] from a SQL ResultManifest (manifest.schema.columns in databricks-sdk)."""
        return manifest.schema.columns

    def _log(self, op_type, path=None, query=None, details=None):
        """Log an operation to the wiki_log table. Failures are silently ignored.

        Uses `INSERT ... SELECT` rather than `VALUES` because `uuid()` is
        rejected in VALUES clauses on SQL warehouses with
        INVALID_INLINE_TABLE.CANNOT_EVALUATE_EXPRESSION_IN_INLINE_TABLE.
        """
        path_sql = f"'{self._escape(path)}'" if path else "NULL"
        query_sql = f"'{self._escape(query)}'" if query else "NULL"
        details_sql = f"'{self._escape(details)}'" if details else "NULL"
        try:
            self._exec(
                f"INSERT INTO {LOG_TABLE} (log_id, op_type, path, query, details) "
                f"SELECT uuid(), '{op_type}', {path_sql}, {query_sql}, {details_sql}"
            )
        except Exception:
            pass

    def write_page(
        self,
        path: str,
        title: str,
        content_json: str | dict,
        page_type: str = "concept",
        created_by: str = "agent",
        tags: list[str] | None = None,
        source_ids: list[str] | None = None,
        parent_id: str | None = None,
        chunk_index: int | None = None,
    ) -> str:
        """Create or update a wiki page. Archives previous version to history.

        Args:
            path: Wiki page path, e.g. 'topics/my-topic'.
            title: Page title.
            content_json: Content as JSON string or dict with 'summary' and 'body' fields.
            page_type: One of: entity, concept, synthesis, comparison.
            created_by: Who created this version.
            tags: Optional list of tags.
            source_ids: Optional list of source IDs to link provenance.
            parent_id: For chunk children, the parent page's `page_id`.
            chunk_index: For chunk children, 1-based position. `0` is also valid.

        Returns:
            Confirmation message.
        """
        if isinstance(content_json, dict):
            content_json = json.dumps(content_json)

        title_esc = self._escape(title)
        content_esc = self._escape(content_json)
        tags_sql = f"ARRAY({','.join(repr(t) for t in tags)})" if tags else "ARRAY()"
        src_sql = f"ARRAY({','.join(repr(s) for s in source_ids)})" if source_ids else "NULL"
        parent_sql = f"'{self._escape(parent_id)}'" if parent_id else "NULL"
        chunk_sql = str(chunk_index) if chunk_index is not None else "NULL"

        archive_sql = f"""
        INSERT INTO {HISTORY_TABLE}
        (page_id, path, title, page_type, content, content_text, tags,
         created_by, created_at, version)
        SELECT page_id, path, title, page_type, content, content_text,
               tags, created_by, created_at, version
        FROM {PAGES_TABLE} WHERE path = '{path}'
        """

        # Preserve `llm:`-prefixed tags across writes. Recorder owns mechanical
        # tags (session, cwd:..., model:..., user:...); the auto-tag task owns
        # `llm:*` tags via append_page_tags. Without this preservation, every
        # write_page wiped llm: tags (Bug 3).
        merge_sql = f"""
        MERGE INTO {PAGES_TABLE} AS target
        USING (SELECT '{path}' AS path) AS source
        ON target.path = source.path
        WHEN MATCHED THEN UPDATE SET
            title = '{title_esc}',
            page_type = '{page_type}',
            content = PARSE_JSON('{content_esc}'),
            content_text = concat(
                PARSE_JSON('{content_esc}'):summary::STRING, ' ',
                PARSE_JSON('{content_esc}'):body::STRING),
            tags = array_distinct(array_union(
                {tags_sql},
                filter(COALESCE(target.tags, array()), t -> t LIKE 'llm:%')
            )),
            source_ids = {src_sql},
            parent_id = {parent_sql},
            chunk_index = {chunk_sql},
            created_by = '{created_by}',
            updated_at = current_timestamp(),
            version = target.version + 1
        WHEN NOT MATCHED THEN INSERT
            (page_id, path, title, page_type, content, content_text, tags,
             source_ids, parent_id, chunk_index, created_by, version)
        VALUES (uuid(), '{path}', '{title_esc}', '{page_type}',
                PARSE_JSON('{content_esc}'),
                concat(
                    PARSE_JSON('{content_esc}'):summary::STRING, ' ',
                    PARSE_JSON('{content_esc}'):body::STRING),
                {tags_sql}, {src_sql}, {parent_sql}, {chunk_sql},
                '{created_by}', 1)
        """

        self._exec(archive_sql)
        self._exec(merge_sql)
        self._sync_vs_source(path)
        self._log("write", path=path)
        return f"Wrote wiki page: {path}"

    def write_pages(self, pages: list[dict]) -> int:
        """Batched write_page — collapses N pages into 4 SQL statements total.

        For each page in `pages`, the dict supports the same fields as
        `write_page` keyword args: path, title, content (str or dict with
        summary+body), page_type, created_by, tags, source_ids, parent_id,
        chunk_index. Falls back to the same defaults as `write_page` for
        missing fields.

        Statement plan (vs. N × 4 = 4N statements when looping write_page):
          1. INSERT INTO pages_history ... SELECT ... FROM pages WHERE path IN (...)
          2. MERGE INTO pages USING (UNION ALL of N source rows)
          3. MERGE INTO pages_vs_source FROM pages WHERE path IN (...)
          4. INSERT INTO wiki_log (UNION ALL of N rows)

        Returns the number of pages written.

        Used by segregate (parent + N chunk children per oversize page) and
        bulk_write_pages. For the agent-marketplace wiki at 27 oversize pages
        × ~5 chunks each, this is the difference between ~33 min of write
        round-trips and ~1 min.
        """
        if not pages:
            return 0

        rows: list[dict] = []
        for p in pages:
            content = p.get("content") if "content" in p else p.get("content_json")
            if isinstance(content, dict):
                content = json.dumps(content)
            if not p.get("path") or not p.get("title") or content is None:
                continue
            rows.append({
                "path": p["path"],
                "title": p["title"],
                "page_type": p.get("page_type", "concept"),
                "content": content,
                "tags": p.get("tags") or [],
                "source_ids": p.get("source_ids"),
                "parent_id": p.get("parent_id"),
                "chunk_index": p.get("chunk_index"),
                "created_by": p.get("created_by", "agent"),
            })
        if not rows:
            return 0

        def _row_select(r: dict, with_aliases: bool) -> str:
            path = self._escape(r["path"])
            title = self._escape(r["title"])
            content = self._escape(r["content"])
            page_type = r["page_type"]
            tags = r["tags"]
            tags_sql = (
                f"ARRAY({','.join(repr(t) for t in tags)})"
                if tags else "CAST(ARRAY() AS ARRAY<STRING>)"
            )
            sids = r["source_ids"]
            sids_sql = (
                f"ARRAY({','.join(repr(s) for s in sids)})"
                if sids else "CAST(NULL AS ARRAY<STRING>)"
            )
            parent_sql = (
                f"'{self._escape(r['parent_id'])}'"
                if r["parent_id"] else "CAST(NULL AS STRING)"
            )
            chunk_sql = (
                str(r["chunk_index"])
                if r["chunk_index"] is not None else "CAST(NULL AS INT)"
            )
            created_by = self._escape(r["created_by"])
            cols = (
                f"'{path}', '{title}', '{page_type}', '{content}', "
                f"{tags_sql}, {sids_sql}, {parent_sql}, {chunk_sql}, '{created_by}'"
            )
            if with_aliases:
                return (
                    f"SELECT '{path}' AS path, '{title}' AS title, "
                    f"'{page_type}' AS page_type, '{content}' AS content_json, "
                    f"{tags_sql} AS tags, {sids_sql} AS source_ids, "
                    f"{parent_sql} AS parent_id, {chunk_sql} AS chunk_index, "
                    f"'{created_by}' AS created_by"
                )
            return f"SELECT {cols}"

        source_sql = " UNION ALL ".join(
            _row_select(r, with_aliases=(i == 0)) for i, r in enumerate(rows)
        )
        paths_in = ", ".join(f"'{self._escape(r['path'])}'" for r in rows)

        # 1. Archive existing rows for these paths into history.
        self._exec(
            f"INSERT INTO {HISTORY_TABLE} "
            f"(page_id, path, title, page_type, content, content_text, tags, "
            f"created_by, created_at, version) "
            f"SELECT page_id, path, title, page_type, content, content_text, tags, "
            f"created_by, created_at, version "
            f"FROM {PAGES_TABLE} WHERE path IN ({paths_in})"
        )

        # 2. MERGE INTO pages — one statement covering all rows.
        self._exec(
            f"MERGE INTO {PAGES_TABLE} AS target "
            f"USING ({source_sql}) AS source "
            f"ON target.path = source.path "
            f"WHEN MATCHED THEN UPDATE SET "
            f"  title = source.title, "
            f"  page_type = source.page_type, "
            f"  content = PARSE_JSON(source.content_json), "
            f"  content_text = concat("
            f"    PARSE_JSON(source.content_json):summary::STRING, ' ', "
            f"    PARSE_JSON(source.content_json):body::STRING), "
            f"  tags = array_distinct(array_union("
            f"    source.tags, "
            f"    filter(COALESCE(target.tags, array()), t -> t LIKE 'llm:%')"
            f"  )), "
            f"  source_ids = source.source_ids, "
            f"  parent_id = source.parent_id, "
            f"  chunk_index = source.chunk_index, "
            f"  created_by = source.created_by, "
            f"  updated_at = current_timestamp(), "
            f"  version = target.version + 1 "
            f"WHEN NOT MATCHED THEN INSERT "
            f"  (page_id, path, title, page_type, content, content_text, tags, "
            f"   source_ids, parent_id, chunk_index, created_by, version) "
            f"VALUES (uuid(), source.path, source.title, source.page_type, "
            f"  PARSE_JSON(source.content_json), "
            f"  concat("
            f"    PARSE_JSON(source.content_json):summary::STRING, ' ', "
            f"    PARSE_JSON(source.content_json):body::STRING), "
            f"  source.tags, source.source_ids, source.parent_id, "
            f"  source.chunk_index, source.created_by, 1)"
        )

        # 3. Sync pages_vs_source for all touched paths in one MERGE.
        self._exec(
            f"MERGE INTO {PAGES_VS_SOURCE_TABLE} AS target "
            f"USING (SELECT page_id, path, title, page_type, content_text, tags, version "
            f"FROM {PAGES_TABLE} WHERE path IN ({paths_in})) AS source "
            f"ON target.path = source.path "
            f"WHEN MATCHED THEN UPDATE SET * "
            f"WHEN NOT MATCHED THEN INSERT *"
        )

        # 4. Single multi-row INSERT INTO wiki_log (uuid() must live in SELECT,
        # not VALUES, on a SQL warehouse — see _log() docstring).
        log_select = " UNION ALL ".join(
            (f"SELECT uuid() AS log_id, 'write' AS op_type, "
             f"'{self._escape(r['path'])}' AS path") if i == 0 else
            f"SELECT uuid(), 'write', '{self._escape(r['path'])}'"
            for i, r in enumerate(rows)
        )
        try:
            self._exec(
                f"INSERT INTO {LOG_TABLE} (log_id, op_type, path) {log_select}"
            )
        except Exception:
            pass

        return len(rows)

    def _sync_vs_source(self, path: str) -> None:
        """Mirror a single page into the VS-source projection table.

        The VS DELTA_SYNC pipeline cannot tolerate VARIANT columns on the source
        table (CDF dedup uses `lead(col, ...)` which requires an ordering type).
        We maintain a parallel table without the VARIANT `content` column that
        the index points at.
        """
        self._exec(
            f"MERGE INTO {PAGES_VS_SOURCE_TABLE} AS target "
            f"USING (SELECT page_id, path, title, page_type, content_text, tags, version "
            f"FROM {PAGES_TABLE} WHERE path = '{path}') AS source "
            f"ON target.path = source.path "
            f"WHEN MATCHED THEN UPDATE SET * "
            f"WHEN NOT MATCHED THEN INSERT *"
        )

    def sync_index(self) -> None:
        """Trigger the DELTA_SYNC VS index so recent writes become searchable.

        The index is TRIGGERED (not CONTINUOUS), so `write_page` alone leaves
        new pages invisible to `search()` until the next explicit sync. Call
        this after a batch of writes (promote pipeline, ingest job).
        Swallows errors so callers don't have to wrap — sync is best-effort.
        """
        try:
            self.ws.vector_search_indexes.sync_index(index_name=VS_INDEX)
            self._log("vs_sync", details=VS_INDEX)
        except Exception as e:
            self._log("vs_sync_fail", details=f"{type(e).__name__}: {e}")

    def list_pages(self, path_prefix: str | None = None) -> list[dict]:
        """List wiki pages for navigation. Returns page_id, path, title, page_type, version."""
        prefix_esc = self._escape(path_prefix) if path_prefix else None
        where = f"WHERE path LIKE '{prefix_esc}%'" if prefix_esc else ""
        resp = self._exec(
            f"SELECT page_id, path, title, page_type, version "
            f"FROM {PAGES_TABLE} {where} ORDER BY path"
        )
        rows = resp.result.data_array if resp.result else []
        if not rows:
            return []
        cols = [c.name for c in self._manifest_columns(resp.manifest)]
        return [dict(zip(cols, row)) for row in rows]

    def read_page(self, path: str) -> dict | None:
        """Read a wiki page by path. Returns page dict or None if not found."""
        resp = self._exec(
            f"SELECT page_id, path, title, page_type, content_text, tags, "
            f"created_by, created_at, updated_at, version "
            f"FROM {PAGES_TABLE} WHERE path = '{path}'"
        )
        rows = resp.result.data_array if resp.result else []
        if not rows:
            return None
        cols = [c.name for c in self._manifest_columns(resp.manifest)]
        self._log("read", path=path)
        return dict(zip(cols, rows[0]))

    def search(
        self,
        query: str,
        mode: str = "HYBRID",
        num_results: int = 5,
        rerank_with_pagerank: bool = False,
    ) -> list[dict]:
        """Search wiki pages via Vector Search.

        Args:
            query: Search query text.
            mode: One of ANN, FULL_TEXT, HYBRID.
            num_results: Max results to return.
            rerank_with_pagerank: When True, fetch each result's `hub_score`
                from `pages` and reorder via Reciprocal Rank Fusion (k=60)
                across vector-search rank and PageRank rank. Defaults to
                False — backward-compatible with v0.6.x callers. Pages
                without a hub_score (newly written, not yet analytics-scored)
                contribute 0 to the PageRank ranker but still appear via
                their VS rank.
        """
        kwargs = {
            "index_name": VS_INDEX,
            "columns": ["page_id", "path", "title", "page_type", "content_text", "tags", "version"],
            "query_text": query,
            "num_results": num_results,
        }
        if mode != "ANN":
            kwargs["query_type"] = mode

        resp = self.ws.vector_search_indexes.query_index(**kwargs)
        if not resp.result or not resp.result.data_array:
            self._log("search", query=query, details=json.dumps({
                "returned_paths": [], "k": num_results, "mode": mode,
            }))
            return []
        cols = [c.name for c in resp.manifest.columns]
        results = [dict(zip(cols, row)) for row in resp.result.data_array]

        if rerank_with_pagerank and results:
            results = self._rerank_by_rrf(results)

        # Citation tracking: log which paths were returned so the promote view
        # over wiki_log can mine real agent traces (v0.5.0 — see health probe
        # `citations_logged`).
        self._log("search", query=query, details=json.dumps({
            "returned_paths": [r.get("path") for r in results if r.get("path")],
            "k": num_results,
            "mode": mode,
            "reranked": rerank_with_pagerank,
        }))
        return results

    def _rerank_by_rrf(self, results: list[dict]) -> list[dict]:
        """Reorder VS results via Reciprocal Rank Fusion with PageRank.

        Pulls `hub_score` for each result's `page_id`, builds two rankings
        (VS rank + PageRank rank), fuses with RRF (k=60). Pages with NULL
        hub_score sit at the bottom of the PageRank ranking. Returns the
        same list of dicts re-ordered.
        """
        from wikibricks.graph_logic import rrf_fuse

        page_ids = [r["page_id"] for r in results if r.get("page_id")]
        if not page_ids:
            return results

        # VS ranking (already best-first from the query response)
        vs_ranking = page_ids

        # PageRank ranking — pull current hub_scores for these page_ids
        ids_sql = ", ".join(f"'{self._escape(pid)}'" for pid in page_ids)
        resp = self._exec(
            f"SELECT page_id, COALESCE(hub_score, 0.0) AS hub_score "
            f"FROM {PAGES_TABLE} "
            f"WHERE page_id IN ({ids_sql})"
        )
        rows = resp.result.data_array if resp.result else []
        hub_by_id = {r[0]: float(r[1]) for r in rows}
        pr_ranking = sorted(page_ids, key=lambda pid: -hub_by_id.get(pid, 0.0))

        fused = rrf_fuse([vs_ranking, pr_ranking], k=60)
        return sorted(
            results,
            key=lambda r: -fused.get(r.get("page_id"), 0.0),
        )

    def update_graph_scores(self, scores: list[dict]) -> int:
        """Batch-MERGE PageRank hub_scores and community_ids into pages.

        Each dict: `{"page_id": str, "hub_score": float | None,
                     "community_id": int | None}`. NULL-safe — missing keys
        become NULL via COALESCE. Returns the number of rows processed.

        Called by `notebooks/wiki_graph_analytics.py` after each curate run.
        """
        if not scores:
            return 0

        rows = []
        for s in scores:
            pid = self._escape(s["page_id"])
            hub = s.get("hub_score")
            cid = s.get("community_id")
            hub_sql = f"CAST({float(hub)} AS DOUBLE)" if hub is not None else "NULL"
            cid_sql = f"CAST({int(cid)} AS INT)" if cid is not None else "NULL"
            rows.append(f"('{pid}', {hub_sql}, {cid_sql})")
        union = ", ".join(rows)

        self._exec(
            f"MERGE INTO {PAGES_TABLE} AS t "
            f"USING (SELECT * FROM (VALUES {union}) "
            f"AS v(page_id, hub_score, community_id)) AS s "
            f"ON t.page_id = s.page_id "
            f"WHEN MATCHED THEN UPDATE SET "
            f"  hub_score = s.hub_score, "
            f"  community_id = s.community_id, "
            f"  updated_at = current_timestamp()"
        )
        return len(scores)

    def history(self, path: str) -> list[dict]:
        """Get version history for a wiki page."""
        resp = self._exec(
            f"SELECT version, created_by, created_at, content:summary::STRING AS summary "
            f"FROM {HISTORY_TABLE} WHERE path = '{path}' ORDER BY version DESC"
        )
        rows = resp.result.data_array if resp.result else []
        if not rows:
            return []
        cols = [c.name for c in self._manifest_columns(resp.manifest)]
        return [dict(zip(cols, row)) for row in rows]

    def ingest_source(
        self, uri: str, title: str | None = None,
        content_text: str | None = None, source_type: str = "manual",
    ) -> str:
        """Ingest a source document into the sources table.

        Returns:
            Confirmation message with the URI.
        """
        title_sql = f"'{self._escape(title)}'" if title else "NULL"
        content_sql = f"'{self._escape(content_text)}'" if content_text else "NULL"
        self._exec(
            f"INSERT INTO {SOURCES_TABLE} (uri, title, content_text, source_type) "
            f"VALUES ('{self._escape(uri)}', {title_sql}, {content_sql}, '{source_type}')"
        )
        self._log("ingest", details=uri)
        return f"Ingested source: {uri}"

    def promote_answer(
        self, query: str, answer: str, source_pages: list[dict],
        created_by: str = "chat",
    ) -> str:
        """Promote a chat answer to a wiki page.

        Creates a synthesis page from the answer, links to source pages.

        Args:
            query: The original user question.
            answer: The generated answer text.
            source_pages: List of page dicts (must have 'page_id') used as sources.
            created_by: Attribution for the page.

        Returns:
            The path of the created wiki page.
        """
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:60]
        path = f"promoted/{slug}"
        content = {"summary": query, "body": answer}
        tags = ["promoted", "auto-generated"]

        self.write_page(path, query[:120], content, page_type="synthesis",
                        created_by=created_by, tags=tags)

        for page in source_pages:
            page_id = page.get("page_id")
            if not page_id:
                continue
            promoted = self.read_page(path)
            if promoted and promoted.get("page_id"):
                try:
                    self._exec(
                        f"MERGE INTO {LINKS_TABLE} AS t "
                        f"USING (SELECT '{promoted['page_id']}' AS src, "
                        f"'{page_id}' AS tgt, 'cites' AS lt) AS s "
                        f"ON t.source_page_id = s.src AND t.target_page_id = s.tgt "
                        f"AND t.link_type = s.lt "
                        f"WHEN NOT MATCHED THEN INSERT "
                        f"(source_page_id, target_page_id, link_type) "
                        f"VALUES (s.src, s.tgt, s.lt)"
                    )
                except Exception:
                    pass

        self._log("promote", path=path, query=query)
        return path

    def upsert_vocabulary(
        self,
        observations: list[dict],
        approve_threshold: int = 3,
    ) -> int:
        """Increment vocabulary counts for a batch of slug observations.

        Each observation is `{"slug": str, "source": str}`. Existing slugs
        have their `count` incremented and `last_seen` refreshed; new slugs
        are inserted with `count=1` and `status='pending'`. Status flips to
        `approved` once `count >= approve_threshold`.

        Returns the number of observations processed.
        """
        if not observations:
            return 0
        rows = []
        for obs in observations:
            slug = self._escape(obs["slug"])
            source = self._escape(obs.get("source") or "auto_tag")
            rows.append(f"SELECT '{slug}' AS slug, '{source}' AS source")
        union = " UNION ALL ".join(rows)
        threshold = int(approve_threshold)
        # Pre-aggregate by slug so MERGE sees one source row per target — Delta
        # rejects multi-source-to-single-target matches as ambiguous. Without
        # GROUP BY here, batches with repeated slugs (the LLM proposes the same
        # tag across multiple pages) fail with
        # DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE.
        self._exec(
            f"MERGE INTO {VOCABULARY_TABLE} AS t "
            f"USING ("
            f"  SELECT slug, MAX(source) AS source, COUNT(*) AS occurrences "
            f"  FROM ({union}) GROUP BY slug"
            f") AS s "
            f"ON t.slug = s.slug "
            f"WHEN MATCHED THEN UPDATE SET "
            f"  count = t.count + s.occurrences, "
            f"  last_seen = current_timestamp(), "
            f"  status = CASE WHEN t.count + s.occurrences >= {threshold} "
            f"               THEN 'approved' ELSE t.status END "
            f"WHEN NOT MATCHED THEN INSERT "
            f"(slug, source, count, first_seen, last_seen, status) "
            f"VALUES (s.slug, s.source, s.occurrences, "
            f"        current_timestamp(), current_timestamp(), "
            f"        CASE WHEN s.occurrences >= {threshold} THEN 'approved' ELSE 'pending' END)"
        )
        return len(observations)

    def append_page_tags(self, path: str, tags: list[str]) -> None:
        """Append tags to `pages.tags`, deduped via array_distinct. Idempotent.

        No-op when `tags` is empty.
        """
        if not tags:
            return
        path_esc = self._escape(path)
        arr = ", ".join(f"'{self._escape(t)}'" for t in tags)
        self._exec(
            f"UPDATE {PAGES_TABLE} "
            f"SET tags = array_distinct("
            f"  array_union(COALESCE(tags, array()), array({arr}))"
            f"), updated_at = current_timestamp() "
            f"WHERE path = '{path_esc}'"
        )

    def bulk_write_pages(
        self,
        jsonl_path: str,
        source_tag: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Bulk-import pages from a JSONL file. Each line is a page dict matching seed_pages format.

        Args:
            jsonl_path: Path to a JSONL file with one page per line.
            source_tag: Optional label recorded in the wiki_log summary row.
            dry_run: If True, parse the file but skip writes.

        Returns:
            Summary dict with keys: written (int), would_write (int), source_tag (str|None).
        """
        with open(jsonl_path) as f:
            pages = [json.loads(line) for line in f if line.strip()]

        if dry_run:
            return {"written": 0, "would_write": len(pages), "source_tag": source_tag}

        normalized = [
            {
                "path": p["path"],
                "title": p["title"],
                "content": p["content"],
                "page_type": p.get("page_type", "concept"),
                "created_by": p.get("created_by", "bulk-import"),
                "tags": p.get("tags") or [],
            }
            for p in pages
        ]
        written = self.write_pages(normalized)

        details = f"bulk_import count={written} source={source_tag or 'unset'}"
        self._log("bulk_import", details=details)
        return {"written": written, "would_write": len(pages), "source_tag": source_tag}

    def propose_edges(
        self,
        path: str,
        num_candidates: int = 10,
        min_similarity: float = 0.7,
        other_pages: list[dict] | None = None,
    ) -> list[dict]:
        """Propose candidate edges for a page without calling an LLM.

        Combines two deterministic signals:
        - Vector Search nearest neighbors on ``content_text`` (origin ``auto-vs``).
          ``confidence`` = VS similarity score, kept only if >= ``min_similarity``.
        - Exact-title substring match: other wiki page titles that appear verbatim
          in this page's body (origin ``auto-title``, confidence 1.0).

        Edges are proposed only — nothing is written. The agent or curate job
        decides which to commit via :meth:`commit_edges`.

        ``other_pages`` is the candidate set for the title-substring match.
        When ``None`` (default), :meth:`list_pages` is called once. Batch
        callers (e.g. the curate notebook) should pre-fetch ``list_pages()``
        once and pass it in to avoid an O(N) round-trip per page in the loop.

        Returns a list of dicts::

            {"source_page_id": ..., "target_page_id": ...,
             "target_path": ..., "target_title": ...,
             "link_type": "related", "confidence": float, "origin": str}
        """
        page = self.read_page(path)
        if not page:
            return []
        source_id = page.get("page_id")
        content = page.get("content_text") or ""
        if not source_id or not content:
            return []

        candidates: dict[tuple[str, str], dict] = {}

        try:
            hits = self.search(content[:500], mode="HYBRID", num_results=num_candidates + 1)
        except Exception:
            hits = []
        for h in hits:
            tgt_id = h.get("page_id")
            if not tgt_id or tgt_id == source_id:
                continue
            score = float(h.get("score", 0.0) or 0.0)
            if score < min_similarity:
                continue
            key = (tgt_id, "related")
            candidates[key] = {
                "source_page_id": source_id,
                "target_page_id": tgt_id,
                "target_path": h.get("path"),
                "target_title": h.get("title"),
                "link_type": "related",
                "confidence": round(score, 4),
                "origin": "auto-vs",
            }

        # Title-substring match. list_pages() now returns page_id, so we
        # avoid the per-match SELECT that previously dominated propose_edges
        # latency on wikis with many pages (one round-trip per matching title
        # × N pages = the prior 55s/page was almost entirely this loop).
        # Batch callers can pre-fetch and pass `other_pages` to skip this SQL.
        if other_pages is None:
            other_pages = self.list_pages()
        content_lower = content.lower()
        for p in other_pages:
            title = (p.get("title") or "").strip()
            if not title or len(title) < 3:
                continue
            if p.get("path") == path:
                continue
            if title.lower() not in content_lower:
                continue
            tgt_id = p.get("page_id")
            if not tgt_id or tgt_id == source_id:
                continue
            key = (tgt_id, "related")
            candidates[key] = {
                "source_page_id": source_id,
                "target_page_id": tgt_id,
                "target_path": p.get("path"),
                "target_title": title,
                "link_type": "related",
                "confidence": 1.0,
                "origin": "auto-title",
            }

        return list(candidates.values())

    def commit_edges(self, edges: list[dict]) -> int:
        """Batch-write edges with bi-temporal semantics.

        Each edge dict must contain: ``source_page_id``, ``target_page_id``,
        ``link_type``, ``confidence``, ``origin``. Optional bi-temporal fields:
        ``valid_from`` (ISO 8601 string — when the fact became true) and
        ``valid_until`` (when it stopped). Both default to "open-ended now":
        ``valid_from = current_timestamp()``, ``valid_until = NULL``.

        Append-only write pattern. For each new edge:
        1. UPDATE any currently-open row with the same
           ``(source_page_id, target_page_id, link_type)`` to set
           ``valid_until`` = the new edge's ``valid_from`` (or
           ``current_timestamp()`` if unspecified). This produces continuous
           validity intervals — no gap, no overlap — and preserves the
           closed row as ordinary table state.
        2. INSERT the new row with the caller's (or default) ``valid_from``
           and ``valid_until``.

        For backward-compatible "the fact starts now" callers, no API change
        is required. For real bi-temporal recording (a fact learned today
        about a past event), supply ``valid_from``/``valid_until`` per-edge.

        Edges are grouped by ``valid_from`` so the batched UPDATE+INSERT
        pattern still collapses N edges to at most 2*K SQL round-trips
        (where K = number of distinct valid_from values supplied).
        Single-group (no caller-supplied valid_from) is the common case
        and remains exactly two statements.
        """
        from collections import defaultdict

        valid: list[tuple[str, str, str, float, str, str | None, str | None]] = []
        for e in edges:
            lt = e.get("link_type", "related")
            origin = e.get("origin", "manual")
            conf = float(e.get("confidence", 1.0))
            src = e.get("source_page_id")
            tgt = e.get("target_page_id")
            if not src or not tgt or lt not in VALID_LINK_TYPES or origin not in VALID_LINK_ORIGINS:
                continue
            if not 0.0 <= conf <= 1.0:
                continue
            valid.append((src, tgt, lt, conf, origin,
                          e.get("valid_from"), e.get("valid_until")))

        if not valid:
            return 0

        # Group by (valid_from, valid_until) so a single backdated batch can
        # still close the prior open rows with the right cutoff. Most calls
        # have all edges in one group (the default-now case).
        groups: dict[tuple[str | None, str | None], list[tuple]] = defaultdict(list)
        for row in valid:
            groups[(row[5], row[6])].append(row)

        for (vf, vu), group in groups.items():
            # Close-out timestamp for the supersede UPDATE: the new edge's
            # valid_from (continuous intervals), or current_timestamp().
            close_sql = f"TIMESTAMP '{self._escape(vf)}'" if vf else "current_timestamp()"
            keys_sql = ", ".join(
                f"('{r[0]}', '{r[1]}', '{r[2]}')" for r in group
            )
            try:
                self._exec(
                    f"UPDATE {LINKS_TABLE} "
                    f"SET valid_until = {close_sql} "
                    f"WHERE valid_until IS NULL "
                    f"  AND (source_page_id, target_page_id, link_type) IN ({keys_sql})"
                )
            except Exception:
                return 0

            vf_sql = f"TIMESTAMP '{self._escape(vf)}'" if vf else "current_timestamp()"
            vu_sql = f"TIMESTAMP '{self._escape(vu)}'" if vu else "NULL"
            rows_sql = ", ".join(
                f"('{r[0]}', '{r[1]}', '{r[2]}', CAST({r[3]} AS FLOAT), '{r[4]}', "
                f"{vf_sql}, {vu_sql})"
                for r in group
            )
            try:
                self._exec(
                    f"INSERT INTO {LINKS_TABLE} "
                    f"(source_page_id, target_page_id, link_type, confidence, origin, "
                    f" valid_from, valid_until) VALUES {rows_sql}"
                )
            except Exception:
                return 0

        self._log("connect", details=f"committed={len(valid)}")
        return len(valid)

    def graph_neighbors(
        self,
        path: str,
        depth: int = 1,
        link_types: list[str] | None = None,
    ) -> list[dict]:
        """Return outgoing neighbors of a page up to ``depth`` hops.

        Pure graph traversal over the links table — no LLM, no embeddings.
        """
        page = self.read_page(path)
        if not page or not page.get("page_id"):
            return []
        sql = graph_neighbors_sql(page["page_id"], depth=depth, link_types=link_types)
        resp = self._exec(sql)
        rows = resp.result.data_array if resp.result else []
        if not rows:
            return []
        cols = [c.name for c in self._manifest_columns(resp.manifest)]
        return [dict(zip(cols, row)) for row in rows]

    def graph_neighbors_at(
        self,
        path: str,
        at_timestamp: str,
        depth: int = 1,
        link_types: list[str] | None = None,
    ) -> list[dict]:
        """Return outgoing neighbors that were valid at ``at_timestamp``.

        Bi-temporal point query (v0.6.0). Useful for reproducing the agent's
        view of the world at a specific moment, debugging why an answer used
        a particular set of edges, or auditing historical reasoning paths.
        """
        page = self.read_page(path)
        if not page or not page.get("page_id"):
            return []
        sql = graph_neighbors_sql(
            page["page_id"], depth=depth, link_types=link_types,
            at_timestamp=at_timestamp,
        )
        resp = self._exec(sql)
        rows = resp.result.data_array if resp.result else []
        if not rows:
            return []
        cols = [c.name for c in self._manifest_columns(resp.manifest)]
        return [dict(zip(cols, row)) for row in rows]

    def link_history(self, src_path: str, dst_path: str) -> list[dict]:
        """Return the full chronological version trace of edges between two pages.

        Yields one row per edge version, oldest first. Each row carries the
        ``link_type``, ``confidence``, ``origin``, ``valid_from``, and
        ``valid_until`` (None = currently valid).
        """
        resp = self._exec(
            f"SELECT l.link_type, l.confidence, l.origin, l.valid_from, l.valid_until "
            f"FROM {LINKS_TABLE} l "
            f"JOIN {PAGES_TABLE} s ON s.page_id = l.source_page_id "
            f"JOIN {PAGES_TABLE} d ON d.page_id = l.target_page_id "
            f"WHERE s.path = '{self._escape(src_path)}' "
            f"  AND d.path = '{self._escape(dst_path)}' "
            f"ORDER BY valid_from"
        )
        rows = resp.result.data_array if resp.result else []
        if not rows:
            return []
        cols = [c.name for c in self._manifest_columns(resp.manifest)]
        return [dict(zip(cols, row)) for row in rows]

    def fix_broken_links(self) -> int:
        """Delete link rows whose endpoint page no longer exists. Returns rows deleted."""
        before = self._exec(f"SELECT COUNT(*) FROM {LINKS_TABLE}")
        before_rows = before.result.data_array if before.result else [[0]]
        before_count = int(before_rows[0][0]) if before_rows else 0
        self._exec(delete_broken_links_sql())
        after = self._exec(f"SELECT COUNT(*) FROM {LINKS_TABLE}")
        after_rows = after.result.data_array if after.result else [[0]]
        after_count = int(after_rows[0][0]) if after_rows else 0
        deleted = max(before_count - after_count, 0)
        if deleted:
            self._log("lint", details=f"fix_broken_links deleted={deleted}")
        return deleted

    def materialize_index(self) -> str:
        """Materialize the wiki index as a page at _meta/index.

        Queries all pages and writes a summary page listing them.

        Returns:
            Confirmation message.
        """
        resp = self._exec(
            f"SELECT path, title, page_type, content:summary::STRING AS summary "
            f"FROM {PAGES_TABLE} WHERE path NOT LIKE '_meta/%' ORDER BY path"
        )
        rows = resp.result.data_array if resp.result else []
        cols = [c.name for c in self._manifest_columns(resp.manifest)] if rows else []

        entries = [dict(zip(cols, row)) for row in rows]
        body_lines = [
            f"- [{e.get('title', '?')}]({e.get('path', '?')}) "
            f"({e.get('page_type', '?')}): {e.get('summary', '')}"
            for e in entries
        ]
        body = "\n".join(body_lines) if body_lines else "No pages yet."

        content = {
            "summary": f"Wiki index: {len(entries)} pages",
            "body": body,
        }
        self.write_page("_meta/index", "Wiki Index", content,
                        page_type="synthesis", created_by="maintenance",
                        tags=["meta", "index", "auto-generated"])
        return f"Materialized index with {len(entries)} pages"
