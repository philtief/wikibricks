"""2WikiMultiHopQA — step 2: create VS index on wiki_2wiki.pages and wait for READY."""

import os
import sys
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.vectorsearch import (
    DeltaSyncVectorIndexSpecRequest,
    EmbeddingSourceColumn,
    PipelineType,
    VectorIndexType,
)

os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", "fe-vm-agent-marketplace")

CATALOG = "agent_marketplace_catalog"
SCHEMA = "wiki_2wiki"
VS_ENDPOINT = "wiki-vs-endpoint"
VS_INDEX = f"{CATALOG}.{SCHEMA}.pages_index"
PAGES_TABLE = f"{CATALOG}.{SCHEMA}.pages"
EMBEDDING_MODEL = "databricks-bge-large-en"

w = WorkspaceClient()

print(f"=== VS index: {VS_INDEX} ===")
try:
    existing = w.vector_search_indexes.get_index(VS_INDEX)
    print(f"  exists, status={existing.status}")
except Exception:
    print("  not found, creating...")
    w.vector_search_indexes.create_index(
        name=VS_INDEX,
        endpoint_name=VS_ENDPOINT,
        primary_key="page_id",
        index_type=VectorIndexType.DELTA_SYNC,
        delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
            source_table=PAGES_TABLE,
            pipeline_type=PipelineType.TRIGGERED,
            embedding_source_columns=[
                EmbeddingSourceColumn(
                    name="content_text",
                    embedding_model_endpoint_name=EMBEDDING_MODEL,
                )
            ],
            columns_to_sync=[
                "page_id", "path", "title", "page_type", "content_text", "tags", "version",
            ],
        ),
    )
    print("  create request sent")

print("=== triggering sync ===")
try:
    w.vector_search_indexes.sync_index(VS_INDEX)
    print("  sync triggered")
except Exception as e:
    print(f"  sync error (may be normal on first create): {e}")

print("=== waiting for READY... (poll every 60s, up to 2h) ===")
t0 = time.time()
MAX_WAIT = 7200
while time.time() - t0 < MAX_WAIT:
    idx = w.vector_search_indexes.get_index(VS_INDEX)
    st = idx.status
    ready = getattr(st, "ready", None)
    state = getattr(st, "detailed_state", None) or getattr(st, "index_status", None) or "?"
    msg = getattr(st, "message", "")
    indexed_rows = getattr(st, "indexed_row_count", None)
    dt = int(time.time() - t0)
    print(f"  [{dt // 60:3d}m{dt % 60:02d}s] ready={ready} state={state} "
          f"indexed={indexed_rows} {msg[:80]}")
    if ready:
        break
    time.sleep(60)
else:
    print("TIMEOUT after 2h", file=sys.stderr)
    sys.exit(1)

dt = int(time.time() - t0)
print(f"\n=== VS index READY after {dt // 60}m{dt % 60}s ===")
