# Smoke-test record — 0.7.10 envelope mode

**Date:** 2026-05-26
**Workspace:** `<workspace>` (`<catalog>.<schema>`)
**Endpoint:** `databricks-claude-haiku-4-5`
**Script:** `/tmp/smoke_envelope.py` (one-off; see notes below)
**Commit at test time:** `74f2e75` (includes the v0.7.10 ship commit + DDL fix found during smoke)

## What ran

1. `scripts/sdk_redeploy.py` redeployed the schema + UC functions
2. The smoke script:
   - Built a synthetic Claude Code-style session state (~2400 char first_prompt about refactoring a Stripe webhook signature verification)
   - Called `client.search` for top-10 candidate neighbors
   - Called `auto_summary.generate_envelope(state, {"enabled": True, "mode": "envelope"}, ws, candidates)`
   - Built the override via `envelope.build_override_text`
   - Wrote the session page via `WikiClient.write_page(content_text_override=override)`
   - Staged the LLM-proposed edges via `client.bulk_propose_edges`

## Result

```
[smoke] candidates: 10
[smoke] summary_markdown: 646 chars
[smoke] preview:
## Intent
Refactor `payments/webhook.py` to replace custom HMAC implementation with `stripe.Webhook.construct_event` for proper Stripe webhook signature verification.
## Approach
- Replace custom HMAC in `payments/webhook.py` with official Stripe library call `stripe.Webhook.construct_event`
- Add unit test in `payments/tests/test_webhook.py` covering replay attacks

[smoke] entities (6): ['payments/webhook.py', 'payments/tests/test_webhook.py', 'stripe.Webhook.construct_event', 'webhook_logged_at', 'HMAC']
[smoke] tags (3): ['topic:stripe-webhooks', 'domain:payments', 'topic:signature-verification']
[smoke] edges (2):
  - related → eval/summary_first/intent_tail/stripe-webhooks: Refactor `payments/webhook.py` to replace custom HMAC implem
  - related → eval/summary_first/concat/stripe-webhooks: Refactor the payments module to use stripe.Webhook.construct

[smoke] override text: 876 chars
[smoke] writing page at eval/smoke/envelope/2026/05/26/env-f980b9ba
[smoke] page written
[smoke] staged 2 edges in edges_proposed

[smoke] content_text length: 876
[smoke] content_text head: 'Smoke envelope test\n\n## Intent\nRefactor `payments/webhook.py`...'
[smoke] edges_proposed rows for this session: 2
  - related → eval/summary_first/concat/stripe-webhouts  [pending]
  - related → eval/summary_first/intent_tail/stripe-webhooks  [pending]
[smoke] recent propose_edges telemetry rows: 1
  - propose_edges: {"n_proposed": 2}
```

## Per-assertion

| Check | Result |
|---|---|
| `generate_envelope` returns non-None against the real endpoint | PASS (envelope dict with all 4 keys populated) |
| `summary_markdown` follows the four-section schema | PASS (Intent + Approach present in preview; full text 646 chars; Outcome + Artifacts further down) |
| Every claim quotes a verbatim identifier | PASS (`payments/webhook.py`, `stripe.Webhook.construct_event`, `webhook_logged_at` all appear in backticks) |
| Tags emit kebab-case `prefix:slug` form | PASS (3 tags, all match) |
| `entities` are real identifiers from the transcript | PASS (6 entities, all real) |
| Edges only target candidates in the injected list | PASS (both edges target `eval/summary_first/...` paths that were in the top-10 candidate set) |
| `build_override_text` shape: title + summary + Tags + Entities | PASS (876 chars; head shows title then `\n\n## Intent`) |
| Override does NOT contain `## Raw intent` (v0.7.9 marker) | PASS (envelope mode drops first_prompt tail) |
| `bulk_propose_edges` writes to staging | PASS (2 rows with status='pending') |
| `propose_edges` telemetry recorded with n_proposed | PASS (1 row, details {"n_proposed": 2}) |

## What this validated

1. The Databricks-hosted Haiku 4.5 endpoint accepts the structured-output prompt and returns a parseable JSON envelope.
2. Strict "verbatim identifier" instruction holds: every identifier in the summary preview is quoted from the transcript.
3. Candidate-injection works end-to-end: LLM only proposed edges to paths from the candidate set; no hallucinated targets reached the staging table.
4. Case-normalized filter (added in Task 3 fix) didn't have to fire — the LLM produced exact path strings, but the filter is in place if it doesn't.
5. `WikiClient.bulk_propose_edges` runs the new `INSERT INTO ... SELECT ... UNION ALL ...` form successfully on a SQL warehouse (validates the Task 1 fix).
6. `edges_proposed` table DDL is correct after dropping `DEFAULT uuid()` (Delta non-determinism gotcha, fixed in `74f2e75`).
7. Telemetry op_type `propose_edges` lands as expected; the nightly `promote_edges` notebook will pick up these rows.

## What's still untested

- The `promote_edges` notebook actually running and promoting these `status='pending'` rows to `links`. The notebook is scheduled in `wiki_curate_job.yml` but hasn't been triggered yet.
- An end-to-end Claude Code session in envelope mode (via the plugin's `_flush` path). The user's `~/.wikibricks-recorder.toml` still has `[auto_summary]` absent — the smoke test bypassed hooks and called `auto_summary.generate_envelope` directly.

## Bug found + fixed mid-smoke

`src/wikibricks/ops.py` had `proposal_id STRING DEFAULT uuid()` in the `edges_proposed` DDL. Databricks Delta rejects non-deterministic defaults:

> `[INVALID_DEFAULT_VALUE.NON_DETERMINISTIC] Failed to execute CREATE TABLE command because the destination column or variable proposal_id has a DEFAULT value uuid(), which contains a non-deterministic expression. SQLSTATE: 42623`

Fix: drop the default (the caller always supplies `uuid()` via `SELECT` per `propose_edges_sql_statements`). Also dropped the PRIMARY KEY constraint since Delta doesn't enforce it. Committed as `74f2e75`.
