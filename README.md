# WikiBricks

A **domain-agnostic memory pattern** for AI agents on Databricks. A structured wiki store backed by Delta tables, Vector Search, and Unity Catalog functions — designed to act as long-term memory for agent systems.

## What it is

WikiBricks gives agents a persistent, queryable, versioned knowledge store:

- **Delta + Vector Search** — five Delta tables (`pages`, `pages_history`, `links`, `sources`, `wiki_log`) plus a Vector Search index over `pages` for semantic retrieval
- **Managed MCP via UC functions** — seven Unity Catalog functions (`fn_wiki_search`, `fn_wiki_read`, `fn_wiki_history`, `fn_wiki_log`, `fn_wiki_index`, `fn_wiki_schema`, `fn_wiki_write_help`) are automatically exposed as MCP tools at `/api/2.0/mcp/functions/<catalog>/<schema>`. No FastMCP, no extra servers
- **Auto-promote** — Streamlit chat judges every answer on a 1–5 scale; score ≥ 4 writes a synthesis page to `promoted/<slug>` with `cites` links to source pages
- **Version history** — every write archives the previous version; `fn_wiki_history` returns the full lineage
- **Three search modes** — HYBRID (default), ANN, FULL_TEXT

## Quick start

Prerequisites: a Databricks workspace with Unity Catalog, a SQL warehouse, and a Vector Search endpoint.

```bash
git clone https://github.com/ptiefenbacher/wikibricks.git
cd wikibricks
databricks bundle deploy --target dev
databricks bundle run deploy_wiki_store --target dev
```

Override the defaults per target:

```bash
databricks bundle deploy --target dev \
  --var="catalog=my_catalog" \
  --var="schema=wiki" \
  --var="warehouse_id=abc123" \
  --var="seed_domain=sample"
```

## Seed domains

| Domain | Contents |
|--------|----------|
| `sample` (default) | 5 meta-pages describing WikiBricks itself — for smoke-tests and the baseline AutoEval |
| `hotpot` | HotpotQA benchmark corpus (Phase 4 — see `examples/hotpotqa.md`) |
| `custom` | Empty. Point `WIKIBRICKS_CUSTOM_PAGES` at your own JSONL |
| `none` | No seed |

## Development

```bash
uv sync
uv run pytest -q        # 226 tests
uv run ruff check src/ app/ tests/
```

## License

Apache 2.0 — see `LICENSE`.
