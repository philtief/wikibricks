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
            tags = {tags_sql},
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

    def search(self, query: str, mode: str = "HYBRID", num_results: int = 5) -> list[dict]:
        """Search wiki pages via Vector Search.

        Args:
            query: Search query text.
            mode: One of ANN, FULL_TEXT, HYBRID.
            num_results: Max results to return.
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
            return []
        # Vector Search API returns columns directly on manifest, not under .schema
        cols = [c.name for c in resp.manifest.columns]
        self._log("search", query=query)
        return [dict(zip(cols, row)) for row in resp.result.data_array]

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

        written = 0
        for page in pages:
            content = page["content"]
            if isinstance(content, dict):
                content = json.dumps(content)
            self.write_page(
                path=page["path"],
                title=page["title"],
                content_json=content,
                page_type=page.get("page_type", "concept"),
                created_by=page.get("created_by", "bulk-import"),
                tags=page.get("tags") or [],
            )
            written += 1

        details = f"bulk_import count={written} source={source_tag or 'unset'}"
        self._log("bulk_import", details=details)
        return {"written": written, "would_write": len(pages), "source_tag": source_tag}

    def propose_edges(
        self,
        path: str,
        num_candidates: int = 10,
        min_similarity: float = 0.7,
    ) -> list[dict]:
        """Propose candidate edges for a page without calling an LLM.

        Combines two deterministic signals:
        - Vector Search nearest neighbors on ``content_text`` (origin ``auto-vs``).
          ``confidence`` = VS similarity score, kept only if >= ``min_similarity``.
        - Exact-title substring match: other wiki page titles that appear verbatim
          in this page's body (origin ``auto-title``, confidence 1.0).

        Edges are proposed only — nothing is written. The agent or curate job
        decides which to commit via :meth:`commit_edges`.

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
        """Batch-MERGE edges into the links table in a single statement.

        Each edge dict must contain: source_page_id, target_page_id, link_type,
        confidence, origin. Invalid rows are skipped silently.

        Collapses N edges into ONE MERGE with a multi-row VALUES source — at
        scale (60 edges/page × 66 pages/run) this reduces 3960 round-trips to
        66, which is the difference between a 3-hour run and a 3-minute run.
        """
        valid: list[tuple[str, str, str, float, str]] = []
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
            valid.append((src, tgt, lt, conf, origin))

        if not valid:
            return 0

        rows_sql = ", ".join(
            f"('{src}', '{tgt}', '{lt}', CAST({conf} AS FLOAT), '{origin}')"
            for src, tgt, lt, conf, origin in valid
        )
        try:
            self._exec(
                f"MERGE INTO {LINKS_TABLE} AS t "
                f"USING (SELECT * FROM (VALUES {rows_sql}) "
                f"AS v(src, tgt, lt, conf, org)) AS s "
                f"ON t.source_page_id = s.src AND t.target_page_id = s.tgt "
                f"AND t.link_type = s.lt "
                f"WHEN MATCHED THEN UPDATE SET confidence = s.conf, origin = s.org "
                f"WHEN NOT MATCHED THEN INSERT "
                f"(source_page_id, target_page_id, link_type, confidence, origin) "
                f"VALUES (s.src, s.tgt, s.lt, s.conf, s.org)"
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
