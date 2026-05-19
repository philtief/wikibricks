# Your AI agent has been forgetting your past — until now

*Bi-temporal memory on Delta gives agents an audit trail their substrate can't lie about.*

Last week, a colleague asked me where my brother lived. I said Berlin.
She was confused — she remembered he lived in London. Both true, at
different times. She had pre-2024 memory; I had post-2025 memory.

LLM agents do this constantly, and they do it worse than people. Once a
fact is overwritten in a typical agent-memory store — Mem0, vanilla
RAG, even most knowledge graphs — the past is gone. *Mem0 doesn't know
he ever lived in London. Your agent will confidently say Berlin, and
when your colleague pushes back, it has no idea what she's talking about.*

There is a clean, well-known answer to this problem from database
research: bi-temporal data. wikibricks v0.7.x ships it on Delta + Unity
Catalog.

## Two timelines, not one

Every edge in wikibricks's `links` table carries three timestamps:

```sql
CREATE TABLE links (
    source_page_id  STRING,
    target_page_id  STRING,
    link_type       STRING,
    ...
    valid_from      TIMESTAMP,  -- when the fact became true (event time)
    valid_until     TIMESTAMP,  -- when it stopped being true; NULL = current
    created_at      TIMESTAMP   -- when the system recorded it (transaction time)
)
```

These are independent. Recording today (2026-05-15) that Philipp lived
in London from 2022-04 to 2025-09 is one statement:

```python
wiki.commit_edges([{
    "source_page_id": philipp_id, "target_page_id": london_id,
    "link_type":  "related",
    "valid_from": "2022-04-15T00:00:00",   # when it became true
    "valid_until":"2025-09-01T00:00:00",   # when it stopped
    # created_at auto = 2026-05-15T... (now)
}])
```

The system records a fact today about an event in the past. That's the
"bi" in bi-temporal: the two timelines move independently.

## What this lets you ask

Three queries no single-temporal store can answer correctly:

```python
# Where does Philipp live now?
wiki.graph_neighbors("philipp")
# → [Berlin]  (only currently-valid edges)

# Where did my agent think Philipp lived on 2022-06-01?
wiki.graph_neighbors_at("philipp", at_timestamp="2022-06-01T00:00:00")
# → [London]  (Munich edge had closed; Berlin hadn't started)

# Full timeline.
wiki.link_history("philipp", "munich") + ... + wiki.link_history("philipp", "berlin")
# 2020-01-01 → 2022-04-15  Munich
# 2022-04-15 → 2025-09-01  London
# 2025-09-01 → NULL        Berlin
```

The third query is the audit trail. When a regulator, a teammate, or a
suspicious user asks *"why did your agent say London in mid-2024?"*,
you can point at a row in `links` with `valid_from <= 2024-06-01 AND
(valid_until IS NULL OR valid_until > 2024-06-01)`. That's not a log
file — it's the data the agent was reasoning over.

## Why the substrate matters

You could implement bi-temporal edges on top of Postgres. You could
implement them on Neo4j (Graphiti does this). What you get on
Delta + Unity Catalog that you don't get elsewhere:

- **Permissions are governed by UC, not application code.** A teammate
  who can't see your wiki can't see its history either. Lineage and
  audit logs come for free from system tables.
- **The closed rows are first-class current state, not time-travel
  artifacts.** When `commit_edges` supersedes an edge, it `UPDATE`s the
  old row to set `valid_until` and `INSERT`s the new one. Both rows
  live in the table indefinitely — survive `OPTIMIZE`, survive
  `VACUUM`, queryable via plain `SELECT`. Delta time travel is not
  involved; you can prove this with `grep -E "VERSION AS OF|TIMESTAMP AS OF"`
  over the codebase and get zero results.
- **Cross-cluster reads.** Genie spaces, SQL warehouses, Spark jobs all
  see the same temporal-aware data. No graph-DB connector to
  configure, no separate audit pipeline.

## Why a wiki, not just a fact log

The bi-temporal model in wikibricks isn't unique to its choice of
substrate — Graphiti exists, Zep exists. What's distinctive is that
wikibricks wraps it in the [Karpathy LLM Wiki pattern][karpathy] (16M
views, April 2026). Edges connect **pages**, not raw facts. Each page
has stable identity (`topics/databricks`, `promoted/why-we-moved`),
typed outgoing edges, and a markdown body the agent can read or rewrite.

That means when an agent answers a question by walking
`philipp → lives_in → munich`, the citation isn't a fact-id, it's a
URL to a page another human can read. The audit trail is human-
inspectable.

## Try it

`uv pip install wikibricks` (or use the [Claude Code plugin][plugin]
for the 5-minute install).

```bash
git clone https://github.com/philtief/wikibricks.git
cd wikibricks
uv run python examples/audit_demo/audit_demo.py \
    --profile <databricks-profile> \
    --catalog <c> --schema <s> --warehouse-id <wh>
```

The demo writes a four-page graph, walks it forward through three
event windows, and queries it from three different points in time.
Code on [GitHub][repo]; ~80 lines, all real Databricks calls.

## What's actually new

| | Mem0 | Letta | Graphiti / Zep | wikibricks v0.7.x |
|---|---|---|---|---|
| Unit of memory | atomic fact | OS-style tier | edge | wiki page + edge |
| Bi-temporal | ✗ | ✗ | ✓ | ✓ |
| Audit trail via SQL | ✗ | ✗ | partial (Neo4j) | ✓ (Delta + UC) |
| Karpathy wiki pattern | ✗ | ✗ | ✗ | ✓ |
| Permission model | app-level | app-level | DB-level | **UC** |
| Substrate | vector DB | Postgres | Neo4j | Delta |

The strategic point isn't "wikibricks invented bi-temporal memory" —
the academic literature on bitemporal databases is decades old, and
Graphiti got there first in the agent space. The point is that this
specific bet — bi-temporal edges, wiki-style pages, UC-governed audit —
lives one rung up in the stack you already trust. No new database to
operate, no opaque graph engine to debug, no app-level ACL code to
maintain. Just SQL.

If you're already on Databricks, you already have everything wikibricks
needs. The only thing to add is your agent's writes.

[karpathy]: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
[plugin]: https://github.com/philtief/wikibricks#5-minute-install-the-personal-recorder
[repo]:   https://github.com/philtief/wikibricks
