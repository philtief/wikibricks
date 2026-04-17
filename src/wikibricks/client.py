"""WikiClient: high-level API for reading and writing wiki pages on Databricks."""

import json

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

from wikibricks.ops import (
    HISTORY_TABLE,
    PAGES_TABLE,
    VS_INDEX,
)


class WikiClient:
    """Databricks-native wiki client for AI agents.

    Usage::

        from wikibricks import WikiClient

        wiki = WikiClient(warehouse_id="abc123")
        wiki.write_page("claims/fraud/patterns", "Fraud Patterns", content_json)
        page = wiki.read_page("claims/fraud/patterns")
        results = wiki.search("fraud detection")
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

    def write_page(
        self,
        path: str,
        title: str,
        content_json: str | dict,
        page_type: str = "concept",
        created_by: str = "agent",
        tags: list[str] | None = None,
    ) -> str:
        """Create or update a wiki page. Archives previous version to history.

        Args:
            path: Wiki page path, e.g. 'claims/fraud/patterns'.
            title: Page title.
            content_json: Content as JSON string or dict with 'summary' and 'body' fields.
            page_type: One of: entity, concept, synthesis, comparison.
            created_by: Who created this version.
            tags: Optional list of tags.

        Returns:
            Confirmation message.
        """
        if isinstance(content_json, dict):
            content_json = json.dumps(content_json)

        title_esc = self._escape(title)
        content_esc = self._escape(content_json)
        tags_sql = f"ARRAY({','.join(repr(t) for t in tags)})" if tags else "ARRAY()"

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
            created_by = '{created_by}',
            updated_at = current_timestamp(),
            version = target.version + 1
        WHEN NOT MATCHED THEN INSERT
            (page_id, path, title, page_type, content, content_text, tags,
             created_by, version)
        VALUES (uuid(), '{path}', '{title_esc}', '{page_type}',
                PARSE_JSON('{content_esc}'),
                concat(
                    PARSE_JSON('{content_esc}'):summary::STRING, ' ',
                    PARSE_JSON('{content_esc}'):body::STRING),
                {tags_sql}, '{created_by}', 1)
        """

        self._exec(archive_sql)
        self._exec(merge_sql)
        return f"Wrote wiki page: {path}"

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
        cols = [c.name for c in resp.manifest.columns]
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
        cols = [c.name for c in resp.manifest.columns]
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
        cols = [c.name for c in resp.manifest.columns]
        return [dict(zip(cols, row)) for row in rows]
