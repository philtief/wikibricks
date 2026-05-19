# One wiki, many agents — the team pattern

WikiBricks does not assume a single user. One workspace, one catalog,
one schema, *N* agents writing into the same `pages` and `links` tables.
The substrate (Delta + Unity Catalog) handles concurrency, audit, and
permissions. The agents don't need to know about each other.

This example walks through the setup. It assumes you've already deployed
the wiki store per the [root README](../../README.md).

## The architecture

```
                          ┌─────────────────────────────┐
                          │  Databricks workspace        │
                          │                              │
   Claude Code on   ──────┼──┐                           │
   Alice's laptop         │  │                           │
                          │  ▼                           │
   Claude Code on   ──────┼──→  agent_team_catalog.      │
   Bob's laptop           │     team_wiki                │
                          │     ├─ pages                 │
   A Genie space    ──────┼──→  ├─ links (bi-temporal)   │
   (read-only)            │     ├─ wiki_log              │
                          │     ├─ wiki_vocabulary       │
   Codex CLI on     ──────┼──→  └─ agent_traces_v        │
   Carol's box            │                              │
                          │     UC governs: who reads,   │
                          │     who writes, all logged   │
                          └─────────────────────────────┘
```

Three real writers (one per developer machine), one read-only reader
(the Genie space), all hitting the same tables. UC handles auth, audit,
lineage, and row-level security. The recorder installs are per-machine;
the wiki is one shared resource.

## Setup (one-time, by the wiki owner)

### 1. Deploy the wiki store

Per the root README — one `databricks bundle deploy`. The catalog/schema
you pick becomes the team's shared wiki.

```yaml
# databricks.override.yml
targets:
  dev:
    variables:
      catalog: agent_team_catalog
      schema:  team_wiki
      warehouse_id: <your-sql-warehouse-id>
```

### 2. Grant team access via UC

```sql
-- Read access for everyone who searches the wiki
GRANT USAGE ON CATALOG agent_team_catalog TO `alice@example.com`;
GRANT USAGE ON CATALOG agent_team_catalog TO `bob@example.com`;
GRANT USAGE ON CATALOG agent_team_catalog TO `carol@example.com`;
GRANT USAGE ON SCHEMA agent_team_catalog.team_wiki TO `alice@example.com`;
GRANT SELECT ON SCHEMA agent_team_catalog.team_wiki TO `alice@example.com`;
-- ... repeat for bob, carol

-- Write access — only for the recorder accounts (could be a group)
GRANT MODIFY ON SCHEMA agent_team_catalog.team_wiki TO `team-recorders@example.com`;

-- The curate job's service principal needs both
GRANT USAGE, SELECT, MODIFY ON SCHEMA agent_team_catalog.team_wiki TO `<job-sp>`;
```

The Databricks-managed MCP endpoint at
`https://<workspace>/api/2.0/mcp/functions/agent_team_catalog/team_wiki`
respects these GRANTs — Alice's agent can only call functions Alice
has `EXECUTE` on, only read tables she has `SELECT` on. No application
permission logic to maintain.

## Per-developer setup (Alice, Bob, Carol)

Each developer installs the recorder plugin once and joins the team
wiki:

```bash
/plugin marketplace add https://github.com/philtief/wikibricks.git
/plugin install wikibricks-recorder@wikibricks
```

Then once per machine:

```bash
uvx --from "git+https://github.com/philtief/wikibricks.git@v0.7.1" \
    wiki-init team-join

# Prompts for the host, profile, catalog (agent_team_catalog),
# schema (team_wiki), warehouse_id.
```

That's it. The next Claude Code session writes to
`agent_team_catalog.team_wiki.pages` under
`sessions/<alice@example.com>/2026/05/15/<session-id>`. UC stamps each
row's `created_by` with the user's identity. Curate, tag, promote,
graph-analytics all run on the shared corpus.

## What changes (and what doesn't)

| Mechanism | Single-user wiki | Team wiki |
|---|---|---|
| Storage | Delta tables you own | Delta tables shared via UC GRANT |
| Auth | Your PAT / OAuth | Each user's OAuth + UC policies |
| Recorder install | Per-machine | Per-machine (each user) |
| Page paths | `sessions/<you>/...` | `sessions/<user>/...` (path identifies who) |
| Curate job | Runs on your behalf | Runs on a service principal; touches everyone's pages |
| MCP endpoint | Your `/api/2.0/mcp/functions/<c>/<s>` | Same endpoint, UC GRANTs filter what each user sees |
| Audit | `wiki_log.created_by` | `wiki_log.created_by` + UC system tables |

Zero application code change. Everything is UC + Delta primitives. That
is what "agent-built knowledge for teams" looks like on Databricks.

## Demoing it

`scripts/simulate_team_activity.py` writes a small set of pretend
sessions under three different fake user paths and runs a few searches,
so you can see how a shared wiki looks under load without setting up
multiple machines.

```bash
uv run python examples/team_wiki/simulate_team_activity.py \
    --profile <profile> \
    --catalog agent_team_catalog --schema team_wiki \
    --warehouse-id <wh>
```

It writes ~9 pages across 3 fake users and shows the activity in
`wiki_log` grouped by `created_by`.

## Why this matters

Mem0 and Letta optimise for *single-user* memory — `user_id` is
required, every fact is keyed to one human or one agent identity. They
can serve a chatbot remembering its user, but the architecture is bias-
ed toward single-tenant deployment.

Wikibricks's path-based identity model (`sessions/<user>/...`,
`topics/...`, `promoted/...`) treats users and agents as orthogonal
dimensions of the wiki, not as the primary key. The same wiki can be
read by Genie, written by Claude Code, audited by SQL, and governed by
UC — all without rewriting the data model. That's the team pattern.
