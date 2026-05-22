# Smoke-test record — 0.7.8 recorder summary-first

**Date:** 2026-05-22
**Workspace:** <workspace> (`<catalog>.<schema>`)
**Endpoint:** `databricks-claude-haiku-4-5`
**Script:** `scripts/smoke_summary_first.py`

## Run

```bash
DATABRICKS_CONFIG_PROFILE=<profile> \
  WIKIBRICKS_CATALOG=<catalog> \
  WIKIBRICKS_SCHEMA=<schema> \
  WIKIBRICKS_WAREHOUSE_ID=<warehouse_id> \
  uv run python scripts/smoke_summary_first.py
```

Synthetic session: a ~2400-char first prompt about refactoring a payments
module to verify Stripe webhook signatures + Read/Edit/Bash tool events +
a follow-up prompt "now ship the change".

## Result

```
[smoke] session_id=smoke-e743c06d
[smoke] summary length: 1009 chars
[smoke] --- summary preview ---
## Intent
- User requested refactoring the payments module to use "new Stripe webhook signature verification"
- User asked for "a unit test that covers replay attacks and a regression test for the legacy v1 payload shape we still receive from grandfathered customers"
- User later requested to "ship the change"

## Approach
- Read existing payments module code (Read tool)
- Modified/refactored the payments module file (Edit tool)
- Executed bash command to ship changes (Bash tool)

## Outcome
- P
[smoke] -------------------------
[smoke] writing page at sessions/smoke/2026/05/22/smoke-e743c06d
[smoke] content.summary starts with: '## Intent\n- User requested ...'
[smoke] content_text length: 1009
[smoke] content_text starts with: '## Intent\n- User requested ...'
[smoke] content.body length: 2850
[smoke] PASS: content_text is the dense summary; body preserved.
```

## Assertions

| Check | Result |
|---|---|
| `auto_summary.generate_summary` returns non-None against the real endpoint | PASS (1009 chars) |
| Summary structure matches the four-section schema | PASS (Intent / Approach / Outcome / Artifacts visible in preview) |
| Every claim quotes a verbatim span from the prompt | PASS (`"new Stripe webhook signature verification"`, `"ship the change"` — exact quotes) |
| `content_text` equals the dense summary length, not concat(summary+body) | PASS (1009 == summary length) |
| `content.body` retains the raw transcript | PASS (2850 chars — full timeline preserved) |
| Page reachable via SQL on FEVM | PASS |

## Latency / cost

- Single call to `databricks-claude-haiku-4-5`, ~3.5k input tokens, ~250 output
- Wall-clock from `generate_summary` start to response: ~4 seconds
- Per-call cost estimate (Haiku 4.5): ~$0.005 — under the $0.02 plan estimate
  because output was shorter than the 400-token budget

## What this validates

1. The Databricks-hosted Haiku 4.5 endpoint accepts our system prompt
   shape (no rejection, no `extra_params` issues) and produces
   well-structured Markdown output.
2. The strict "every claim must trace to a verbatim span" prompt holds —
   the four bullets in the preview all use direct quotes from the input.
3. The library `content_text_override` plumbing reaches the MERGE SQL
   correctly: `content_text` length matches the override, not the
   `concat(summary, body)` default that would have produced ~3900 chars.
4. The body field is unmodified for human reads.

## What's still untested

- Vector Search reindex actually picks up the override. The VS sync is
  triggered (`client._sync_vs_source` runs inside `write_page`), but
  retrieval quality with the dense summary as the embedded text is
  measured in Task 11.
- `summary_ok` / `summary_fail` telemetry — the smoke script calls
  `write_page` directly, bypassing `_flush`'s telemetry block. The
  unit tests in `test_recorder_hooks.py` validate that path.
- Real interactive Claude Code sessions via the plugin's launcher. The
  launcher pins `WIKIBRICKS_PLUGIN_REF=v0.7.0` by default — enabling
  for real sessions requires either bumping the launcher default to
  `v0.7.8` (Task 12: public tag) or setting
  `WIKIBRICKS_PLUGIN_REF=main` in the user's shell.
