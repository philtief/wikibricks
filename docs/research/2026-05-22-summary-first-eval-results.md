# A/B retrieval eval — 0.7.8 summary-first

**Date:** 2026-05-22
**Topics:** 10  **Queries:** 20  **Decision:** **KEEP OPT-IN, iterate on prompt**

## Aggregate

| Metric | concat (control) | summary (treatment) | Δ |
|---|---|---|---|
| recall@1 | 55% | 35% | -20% |
| recall@3 | 90% | 80% | -10% |
| recall@5 | 90% | 95% | +5% |
| recall@10 | 95% | 95% | +0% |
| mean rank | 2.10 | 2.40 | +0.30 |

**Wins / Ties / Losses:** 8 / 1 / 11  (win_rate 40%)

## Per-query ranks

| Topic | Q | rank_concat | rank_summary | winner |
|---|---|---|---|---|
| stripe-webhooks | 1 | 1 | 4 | concat |
| stripe-webhooks | 2 | 1 | 4 | concat |
| lakeflow-backfill | 1 | 1 | 2 | concat |
| lakeflow-backfill | 2 | 2 | 1 | summary |
| react-rerender | 1 | 1 | 2 | concat |
| react-rerender | 2 | 2 | 1 | summary |
| databricks-obo | 1 | 2 | 1 | summary |
| databricks-obo | 2 | 11 | 11 | tie |
| slow-orders-query | 1 | 1 | 2 | concat |
| slow-orders-query | 2 | 2 | 1 | summary |
| snowflake-uc-migration | 1 | 1 | 2 | concat |
| snowflake-uc-migration | 2 | 6 | 4 | summary |
| vs-hybrid-notebook | 1 | 1 | 2 | concat |
| vs-hybrid-notebook | 2 | 2 | 1 | summary |
| uv-wheel-publish | 1 | 1 | 2 | concat |
| uv-wheel-publish | 2 | 1 | 2 | concat |
| lakebase-pool-exhaustion | 1 | 1 | 2 | concat |
| lakebase-pool-exhaustion | 2 | 1 | 2 | concat |
| genie-streamlit-dashboard | 1 | 2 | 1 | summary |
| genie-streamlit-dashboard | 2 | 2 | 1 | summary |

## Decision rule

Per `2026-05-22-summary-first-eval-plan.md`: flip the default to ON
in v0.7.9 if recall@5 lifts ≥ 10pp AND win_rate ≥ 70%. Otherwise
leave opt-in and iterate on the system prompt.

**Outcome:** recall@5 lifted +5pp (below 10pp) and win_rate is 40%
(below 70%) — **keep opt-in.**

## Analysis

The mechanism works (smoke test confirmed end-to-end), but at our
embedding + retrieval mix it doesn't move the bulk metric. Three
observations from the per-query table that direct what to fix next:

### 1. Summary hurts top-1 precision (-20pp recall@1)

Concat wins 11 of 20 queries; on most of those it pulls the page to
rank 1 while summary lands at 2. The pattern: queries that include a
specific named artifact (file path, library name, version number,
table name) match the raw transcript more strongly than the
distilled summary, because HYBRID retrieval rewards keyword density.

Example: `stripe-webhooks` q1+q2 — both arms contain "Stripe" + "webhook"
+ "signatures" in the prompt, but the concat arm's content_text *also*
contains `STRIPE_WEBHOOK_SECRET`, `stripe.Webhook.construct_event`,
and `payments/webhook.py` verbatim. Those tokens carry semantic weight
in HYBRID's keyword leg that the summary loses.

### 2. Summary wins on abstract / outcome queries

The 8 summary wins are concentrated on queries where the matching page
would otherwise have ambiguous ranking. Two `genie-streamlit-dashboard`
queries, the `databricks-obo` "how do we set up" intent query,
`react-rerender` q2 — all cases where the dense summary's "## Approach"
or "## Outcome" sections captured the right framing.

### 3. HYBRID mode amplifies the keyword effect

Re-running with `mode="ANN"` (pure semantic) would likely shift the
balance toward summary — the keyword leg of HYBRID is what specifically
rewards concat. Worth a follow-up arm.

## Recommended v0.7.9 work (ordered)

1. **Tune the system prompt to preserve named entities verbatim.**
   Currently `## Artifacts` says "bullet list of created/modified files,
   URLs, IDs" — but the model summarizes ("modified the payments module
   file") instead of quoting paths. Tighten to: "## Artifacts MUST quote
   every file path, library/module name, function/class name, table/
   column name, version number, environment variable, and ID exactly as
   it appears in the transcript." This alone may close the recall@1 gap.

2. **Try a hybrid override: dense summary + entity tail.** Instead of
   `content_text_override = summary`, set
   `content_text_override = summary + "\n\nEntities: " + extracted_entities`.
   Gives HYBRID both the abstract framing AND the keyword payload.
   Worth an A/B/C arm.

3. **Re-run the eval with `mode="ANN"`.** Confirms whether the keyword-
   density effect is dominant.

4. **Larger N.** 20 queries is enough for direction; 50 would tighten
   the confidence interval and let us split intent vs artifact queries
   cleanly.

## Cost of this run

10 Haiku 4.5 calls × ~$0.02 = **$0.20** total. The eval is cheap to
re-run after each prompt iteration.

## Reproduce

```bash
DATABRICKS_CONFIG_PROFILE=<profile> \
  WIKIBRICKS_CATALOG=<catalog> \
  WIKIBRICKS_SCHEMA=<schema> \
  WIKIBRICKS_WAREHOUSE_ID=<warehouse_id> \
  uv run python scripts/eval_summary_first_recall.py
```

Pass `--skip-writes` to re-query without re-generating summaries
(useful when iterating on retrieval params or the prompt).

## Generated summaries (one per topic)

### stripe-webhooks

```markdown
## Intent
- User requested refactoring `payments/webhook.py` to replace custom HMAC implementation with `stripe.Webhook.construct_event` using `STRIPE_WEBHOOK_SECRET` from environment
- Add unit tests covering "replay attacks (same timestamp, same payload)" and "regression test for the legacy v1 payload shape we still receive from grandfathered customers"
- Ensure "the existing webhook_logged_at metric still increments" after refactor
- User then asked to "ship the change" and confirmed "did you remember the metric?"

## Approach
- Read existing `payments/webhook.py` to understand current custom HMAC implementation
- Replace with Stripe's official `stripe.Webhook.construct_event()` method
- Modified test suite to add replay attack and v1 payload regression coverage
- Verified metric increment logic preserved in refactored code

## Outcome
- Refactored webhook signature verification from custom HMAC to `stripe.Webhook.construct_event()`
- Added unit tests for replay attack scenarios and legacy v1 payload compatibility
- Confirmed `webhook_logged_at` metric still increments in refactored code
- Changes marked ready to ship; metric preservation was explicitly verified per follow-up question

## Artifacts
- `payments/webhook.py` (modified: replaced custom HMAC with `stripe.Webhook.construct_event`)
- `payments/tests/test_webhook.py` (modified: added replay attack test and v1 payload regression test)
```

### lakeflow-backfill

```markdown
## Intent
- User requested a Lakeflow Job that "backfills Q1 invoices into the finance.bronze.invoices_raw table" from "s3://acme-finance-archive/q1/" with date-partitioned JSON files.
- Job must be "idempotent (re-runs don't duplicate rows)" and "use Auto Loader with schema evolution."
- Job should be "Schedule it as a one-shot and tag the run with backfill=q1_2026 so the platform team can find it" in "dabs/finance/" bundle.
- User then requested validation and deployment to dev.

## Approach
- Created Lakeflow job configuration with Auto Loader source pointing to S3 path with schema evolution enabled.
- Configured idempotency via checkpoint location and merge logic to prevent duplicate rows on re-runs.
- Set one-shot schedule with tags metadata (backfill=q1_2026) for platform team discovery.
- Used Bash to validate bundle and deploy to dev environment.

## Outcome
- Bundle created in dabs/finance/ with job configuration, validated successfully, and deployed to dev.
- Auto Loader with schema evolution and idempotent write pattern implemented.
- One-shot schedule and backfill=q1_2026 tag applied for tracking.

## Artifacts
- dabs/finance/resources/jobs/q1_invoices_backfill.yml (Lakeflow job definition)
- dabs/finance/databricks.yml (bundle configuration)
- Bundle validation and deployment completed via Bash commands
```

### react-rerender

```markdown
## Intent
- User requested fixing a React hook (`useFilter.tsx`) that "re-renders on every keystroke in the search box" due to spreading "the entire filter state into a useEffect dependency array"
- Goal: "Refactor to use useMemo for the derived filter and useDeferredValue for the search term"
- Verification requirement: "Confirm the network panel shows one query per debounce window, not per keystroke"
- Later request: "also add a test"

## Approach
- Read the existing hook file at `apps/dashboard/src/hooks/useFilter.tsx`
- Refactored using `useMemo` to memoize derived filter object and `useDeferredValue` to defer search term updates
- Added test file to validate the debounce behavior and query reduction

## Outcome
- Modified `useFilter.tsx` to wrap filter derivation in `useMemo` and search term in `useDeferredValue`, eliminating unnecessary re-renders and GraphQL queries on each keystroke
- Added test suite confirming one query fires per debounce window rather than per keystroke
- Changes complete; network panel behavior validated through test assertions

## Artifacts
- `apps/dashboard/src/hooks/useFilter.tsx` (modified)
- `apps/dashboard/src/hooks/useFilter.test.tsx` (created)
```

### databricks-obo

```markdown
## Intent
- User requested setup of Databricks On-Behalf-Of (OBO) authentication for a FastAPI app deployed on Databricks Apps to "call the SQL warehouse and Unity Catalog as the end user, not as the app's service principal."
- Requires wiring OBO token resolution in `app/auth.py` by reading `X-Forwarded-Access-Token` from proxy, validating audience claim, and passing token to Databricks SDK `WorkspaceClient`.
- Needs documentation of workspace configuration (workspace setting + app resource role) for OBO to function.
- Later request: "confirm it works locally."

## Approach
- Modified `app/auth.py` to extract and validate OBO tokens from `X-Forwarded-Access-Token` header, including audience claim validation.
- Integrated token passing into Databricks SDK `WorkspaceClient` initialization.
- Created workspace configuration documentation outlining required settings and app resource role setup.
- Tested locally to verify OBO token flow.

## Outcome
- OBO authentication layer implemented in `app/auth.py` with token extraction, validation, and SDK integration.
- Workspace configuration guide documented with required settings.
- Local testing confirmed token resolution and validation logic functions as expected.

## Artifacts
- `app/auth.py` (modified) — OBO token extraction, audience validation, WorkspaceClient initialization
- Workspace configuration documentation (created) — OBO setup requirements and app resource role configuration
```

### slow-orders-query

```markdown
## Intent
- Investigate slow queries on `warehouse.gold.orders_fact` (12M rows) where "A specific aggregation rolling up monthly revenue by region takes 90 seconds"
- "EXPLAIN shows a full table scan + a broadcast hash join with the customer dim"
- Recommend "Z-ORDER BY order_date, region_id and OPTIMIZE the table; consider adding a materialized view for the monthly aggregate"
- "Measure before/after with %sql ANALYZE TABLE compute statistics"
- Run optimization on dev environment first

## Approach
- Execute ANALYZE TABLE statistics on `warehouse.gold.orders_fact` before optimization to establish baseline
- Apply Z-ORDER clustering by order_date and region_id columns
- Run OPTIMIZE command on dev environment
- Create materialized view for monthly revenue by region aggregation
- Re-run ANALYZE TABLE and query performance test after optimization

## Outcome
- Optimization workflow initiated on dev environment; before/after statistics collection planned but not yet completed in transcript
- Z-ORDER and OPTIMIZE commands staged for execution
- Materialized view design prepared but implementation status unclear

## Artifacts
- `warehouse.gold.orders_fact` (target table for Z-ORDER and OPTIMIZE)
- Materialized view for monthly revenue aggregation (proposed, not yet created)
- ANALYZE TABLE baseline and post-optimization statistics (planned)
```

### snowflake-uc-migration

```markdown
## Intent
- Migrate Snowflake stored procedure `SP_CALCULATE_RISK` (takes `portfolio_id` and `as_of_date`, returns struct with `var_99`, `cvar_99`, `max_drawdown`) to a Unity Catalog SQL function in `risk.functions` schema
- Update three downstream dashboards that call the procedure to use the new function
- Validate one dashboard end-to-end

## Approach
- Read existing `SP_CALCULATE_RISK` procedure definition to extract CTE logic over `positions` and `price_history` tables
- Create UC SQL UDF with identical logic in `risk.functions` schema
- Locate and update three dashboard definitions (queries/configurations) to call new function instead of stored procedure
- Execute end-to-end validation on one dashboard

## Outcome
- Stored procedure migrated to UC SQL UDF with struct return type preserved
- Three dashboard call sites updated from `SP_CALCULATE_RISK()` to new function reference
- One dashboard validated end-to-end (query execution and output structure confirmed)
- Migration complete; all downstream consumers now use Unity Catalog function

## Artifacts
- `risk.functions.CALCULATE_RISK` (UC SQL UDF, replaces `SP_CALCULATE_RISK`)
- Three dashboard definition files (updated query references)
- Validation test output for end-to-end dashboard check
```

### vs-hybrid-notebook

```markdown
## Intent
- User requested switching the support_kb notebook from "pure ANN over the support_articles Delta table" to "HYBRID via vector_search() TVF with query_type='HYBRID'" to prevent keyword matches on product names ('Snowsight', 'Genie Space') from being "drowned by semantic neighbours."
- Validation requirement: "top result for 'how do I share a Genie Space' actually links to the sharing doc, not to a generic permissions article."

## Approach
- Read the existing support_kb notebook to understand current ANN retrieval implementation.
- Edit the retrieval logic to replace ANN with `vector_search()` TVF call using `query_type='HYBRID'` parameter.
- Test the modified query against the validation case ('how do I share a Genie Space').

## Outcome
- The notebook retrieval mechanism was modified to use HYBRID search mode, combining keyword and semantic matching.
- Validation test executed to confirm top result correctly returns the sharing documentation rather than a generic permissions article.
- Switch completed; hybrid retrieval is now active in the support_kb notebook.

## Artifacts
- `support_kb` notebook (modified retrieval cell with `vector_search()` TVF and `query_type='HYBRID'`)
```

### uv-wheel-publish

```markdown
## Intent
- User requested to "Package and publish the latest version of the acme-analytics Python wheel from a uv project" with version bump from 1.4.2 to 1.5.0.
- Tasks included: run `uv build`, smoke-test in fresh venv, publish to internal PyPI at `pypi.acme.internal via uv publish`, update README.md install instructions, and tag `v1.5.0` in git.
- Follow-up question: "any breaking changes?"

## Approach
- Used Bash (2 invocations) for build, test, and publish commands.
- Used Edit (1 invocation) to modify pyproject.toml version and README.md.
- Used Read (1 invocation) to inspect files.

## Outcome
- Version bumped and wheel built via `uv build`.
- Smoke-test executed in fresh virtual environment.
- Package published to internal PyPI.
- README.md updated with new version reference.
- Git tag `v1.5.0` created.
- Follow-up question about breaking changes was addressed but no breaking changes were identified in the transcript.

## Artifacts
- `pyproject.toml` (version bumped to 1.5.0)
- `README.md` (install instructions updated to reference 1.5.0)
- Git tag: `v1.5.0`
- Built wheel artifact (published to `pypi.acme.internal`)
```

### lakebase-pool-exhaustion

```markdown
## Intent
- User diagnosed that the Streamlit Lakebase dashboard exhausts psycopg2 connections under load because "every Streamlit script rerun grabs a fresh connection without releasing the prior one" with a pool of max_size=5.
- Request to "Switch to a scoped connection pattern via contextlib.contextmanager and ensure connections are released on rerun."
- Validate via "stress-testing with 50 concurrent sessions."
- Later: "add a connection-leak test."

## Approach
- Refactored connection management using `contextlib.contextmanager` to scope connection lifecycle to query execution, ensuring automatic release on context exit.
- Modified Streamlit app to use the scoped connection pattern instead of holding connections across reruns.
- Created stress-test script simulating 50 concurrent sessions and connection-leak detection test.

## Outcome
- Replaced global connection-holding pattern with context-managed connections that release immediately after use, eliminating per-rerun connection exhaustion.
- Stress test validated the pool handles 50 concurrent sessions without pool depletion.
- Connection-leak test added to detect unreleased connections and verify cleanup on rerun cycles.
- Solution completed and validated.

## Artifacts
- Modified connection pool module with `@contextmanager` decorator for scoped connections
- Updated Streamlit dashboard app to use `with get_connection():` pattern
- Stress-test script for 50 concurrent session simulation
- Connection-leak detection test validating no connections remain after rerun cycles
```

### genie-streamlit-dashboard

```markdown
## Intent
- Build a Streamlit dashboard for sales-ops team to ask natural-language questions about pipeline (e.g., "which deals slipped from Q1 to Q2?")
- Backend integration with Genie Space using `/api/2.0/genie/spaces/<space-id>/conversations` endpoint
- Render Genie responses inline with SQL + table results using `st.dataframe`, manage `conversation_id` across reruns, and deploy as Databricks App
- Wire in OBO (on-behalf-of) authentication

## Approach
- Streamlit framework with session state to persist `conversation_id` across reruns
- Genie Space API integration via `/api/2.0/genie/spaces/<space-id>/conversations` endpoint
- Databricks App deployment target
- OBO authentication mechanism added to API calls

## Outcome
- Dashboard structure created with natural-language query input and response rendering
- Conversation state management implemented to maintain context across Streamlit reruns
- SQL query and results table display configured with `st.dataframe`
- OBO authentication wired into API request headers
- Deployment configuration prepared for Databricks App

## Artifacts
- `app.py` – main Streamlit dashboard application with Genie Space integration and OBO auth
- Databricks App deployment manifest/configuration
- Session state management for `conversation_id` persistence
```
