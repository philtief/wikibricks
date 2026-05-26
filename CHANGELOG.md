# Changelog

All notable changes to WikiBricks are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.12] - 2026-05-26

### Added

- **`src/wikibricks_graph/`** — new Databricks App that visualizes the
  wiki as an interactive force-directed graph. Replaces the Streamlit
  `wikibricks-app` (same `/Workspace/.../wikibricks-app` slot).
- **Backend (FastAPI)**: `/api/graph` (with ETag), `/api/graph/refresh`,
  `/api/pages/{path:path}`, `/api/edges/proposed` + approve/reject.
  Hides chunks by default. In-process `cachetools.TTLCache` snapshot
  + `blake2b` ETag. Dependencies: `fastapi`, `uvicorn`, `pydantic`,
  `databricks-sdk`, `cachetools`. 33 backend tests.
- **Frontend (Vite + React + TypeScript)**: `@xyflow/react@^12` +
  `d3-force@^3` force-directed graph; Zustand for state; URL search
  params (`?focus=&depth=`) drive ego-network deep links; `FilterSidebar`
  for community / page_type / typed-edges-only / chunks / search;
  `PageDetailDrawer` with title + tags + 1-hop neighbor chips + body;
  `/queue` route for approving / rejecting LLM-proposed edges. 25 unit
  tests for pure logic (store, ego BFS, palette, force layout).
- **Deployed** at https://wikibricks-app-7474653189849615.aws.databricksapps.com
  (env vars set on the Databricks Apps side: `WIKIBRICKS_CATALOG`,
  `WIKIBRICKS_SCHEMA`, `WIKIBRICKS_WAREHOUSE_ID`).
- **Plugin manifest + launcher REF** bumped to `v0.7.12`. Notebook
  `%pip` lines + README test count refreshed.

### Pivoted from APX → manual scaffold

The original plan specified `apx init` for scaffolding. APX install
(curl-pipe-to-sh from an external domain) was blocked by auto-mode
without explicit user authorization. Pivoted to the FE-blessed manual
scaffold pattern from the `fe-databricks-tools:databricks-apps` skill
— same tech stack (FastAPI + Vite + React + Tailwind + Zustand), with
hand-written fetch wrappers in place of Orval auto-typed-client.

### Why

The previous Streamlit `wikibricks-app` was a list-and-edit UI that
made the wiki's graph structure invisible. With v0.7.10 envelope mode
now staging LLM-proposed typed edges, the graph is the most valuable
view — clusters, citations, hub pages, and orphans are immediate at a
glance. The new app makes the WikiBricks graph a first-class surface.

### Research + plan

- `docs/research/2026-05-26-graph-interface-research.md`
- `docs/superpowers/plans/2026-05-26-graph-interface.md`

### To browse

Visit https://wikibricks-app-7474653189849615.aws.databricksapps.com
(authenticate via Databricks SSO). Click any node to deep-link into
its 2-hop neighborhood. Use the sidebar to filter by community / page
type / search. The `/queue` route lists proposed edges from envelope
mode for review.

## [0.7.11] - 2026-05-26

### Fixed (hotfix on 0.7.10 — caught by final code-quality review)

- **`notebooks/promote_edges.py`: `link_type` was interpolated into
  the duplicate-check SQL without escaping.** Now uses a `_esc` helper
  applied to all SQL interpolations (target_path, source_path,
  link_type, proposal_id, orphan ids). [Important]
- **`notebooks/promote_edges.py`: promotion loop only validated
  `target_path` existence, not `source_path`.** Under Delta visibility
  races (write committed but not yet visible to other warehouse
  connections), the final `INSERT INTO links` INNER JOIN would silently
  drop rows while `status='confirmed'` was already set — silent edge
  loss. Now adds an explicit source-exists check (`rejected:
  source_missing`) PLUS a compensating LEFT-JOIN check after the
  INSERT to catch any orphan rows and mark them `rejected:
  source_join_orphan`. [Important]
- **`notebooks/promote_edges.py`: rejection-update loop double-
  processed orphan rows.** The compensating UPDATE marked orphans
  rejected and appended `[rejected: source_join_orphan]` to evidence;
  the subsequent rejection loop then re-appended the same marker. Now
  tracks orphan ids in a set and skips them in the rejection loop.
  [Important]
- **`notebooks/promote_edges.py`: `rejected_reasons` telemetry dict
  hard-coded three reasons.** Now uses `Counter(reason for _, reason
  in rejected)` so new reasons (including the two added in this
  release) are counted automatically. [Minor]

### Coverage

5 new drift-guard tests in `tests/test_promote_edges_notebook.py`
(suite 874 → 879).

### Why

The v0.7.10 release introduced the nightly `promote_edges.py` notebook
that promotes LLM-proposed edges from `edges_proposed` into the
canonical `links` table. Per-task code review during 0.7.10 missed
these four findings — they surfaced only in the final
v0.7.9..v0.7.10 cross-cutting review. Hotfix-released as 0.7.11
because Important #2 is a silent-data-loss path that could affect
any user who enables `[auto_summary] mode = "envelope"`.

The auto_summary library + envelope-mode write path are unchanged.

## [0.7.10] - 2026-05-26

### Added

- **`auto_summary.generate_envelope`** — single structured-output LLM
  call returning `{summary_markdown, entities, tags, edges}`. Replaces
  the v0.7.9 pure-summary call when `[auto_summary] mode = "envelope"`.
- **`wikibricks_recorder.envelope` module** — schema, prompt builder
  (with candidate-neighbor injection per arXiv:2510.20345), parser,
  edge filter (case-insensitive path normalization to defeat LLM case-
  shifting), content_text override builder. 12 unit tests.
- **`WikiClient.bulk_propose_edges`** — stages LLM-proposed edges in
  the new `edges_proposed` Delta table. Uses `INSERT INTO ... SELECT
  ... UNION ALL ...` form (the SQL-warehouse-safe pattern).
- **`edges_proposed` Delta table** — staging area for LLM-emitted typed
  edges with provenance (source_path, target_path, link_type,
  evidence, confidence, status). DDL in `ops.create_tables_sql`.
- **`notebooks/promote_edges.py` + task in `wiki_curate_job.yml`** —
  nightly auto-confirms staged edges where target exists + evidence
  non-empty + no duplicate. Joins `edges_proposed` paths to
  `pages.page_id` and inserts into `links` with
  `origin='auto_summary_envelope'`.
- **`hooks._flush` mode branching** — `[auto_summary] mode` selects
  `"envelope"` (new) or `"intent_tail"` (v0.7.9 default, unchanged).
  Envelope mode fetches top-10 VS candidates → calls generate_envelope
  → builds override → stages proposed edges.
- **`propose_edges` and `promote_edge` `wiki_log` op_types** for the
  new flow. Telemetry table in `AGENTS.md` updated.
- **33 new tests** (suite 841 → 874).

### Changed

- **Envelope-mode `content_text` override drops the `first_prompt`
  tail** — replaced with `title + summary + tags + entities`. Denser,
  keyword-rich, no conversational noise. v0.7.9 intent_tail mode
  unchanged.

### Why

Anthropic Structured Outputs (GA Feb 2026) makes a single
multi-purpose JSON call reliable. WikiBricks' graph (typed edges,
PageRank, communities) is most valuable when summaries are AWARE of
their neighbors — proposing typed edges at write time so subsequent
retrieval + community synthesis have richer signal. See
`docs/research/2026-05-26-graph-aware-summary-research.md` for the
HippoRAG / RAPTOR / LightRAG references.

Edges go to a staging table — never directly to `links` — so
hallucinated targets are quarantined (mitigation pattern from
arXiv:2510.20345). Path normalization (lower + strip) on the
candidate-filter further hardens against LLM case-shift bypasses.

### To enable

```toml
# ~/.wikibricks-recorder.toml
[auto_summary]
enabled = true
mode = "envelope"
endpoint = "databricks-claude-haiku-4-5"
```

Default mode is `"intent_tail"` (v0.7.9 behavior) until the
larger-N eval validates `"envelope"`. See plan at
`docs/superpowers/plans/2026-05-26-graph-aware-summary.md`.

## [0.7.9] - 2026-05-22

### Changed

- **`hooks._flush` now builds `content_text_override` as
  `dense_summary + "\n\n## Raw intent\n" + first_prompt[:2000]`** instead
  of passing the dense summary verbatim. Recorder users who already
  opted into `[auto_summary]` automatically get the better override
  composition.
- **`auto_summary._SYSTEM_PROMPT` reverted** to the v0.7.8 wording.
  The brief "v2" tightening (one-commit lifetime, never released) that
  demanded strict identifier backtick-quoting hurt recall@1 from 35%
  to 5% by shifting output toward bag-of-identifiers and away from
  coherent prose that natural-language queries match against.

### Added

- **`auto_summary.build_content_text_override(state, summary)`** —
  composes the dense LLM summary with a capped raw-intent tail. Pure
  function, no LLM call. Tested with 5 new tests.
- **`scripts/eval_summary_first_recall.py` extended to A/B/C × HYBRID/ANN**:
  third arm `intent_tail` and `--mode {HYBRID,ANN,BOTH}` flag. The
  v2 results that motivated this release are at
  `docs/research/2026-05-22-summary-first-eval-v2.md`.

### Eval numbers (HYBRID, N=20 paired queries)

| Arm | recall@1 | recall@5 | mean_rank | wins |
|---|---|---|---|---|
| concat (control) | 40% | 90% | 2.65 | 8 |
| pure summary | 5% | 100% | 2.80 | 2 |
| **intent_tail (shipped default)** | **50%** | 95% | **2.10** | **10** |

`intent_tail` beats concat on every metric — recall@1 (+10pp),
recall@5 (+5pp), mean_rank (−0.55), wins (10 vs 8). It's the only
arm that does. `is_enabled` stays opt-in until N is larger; the
composition change applies automatically when a user enables it.

## [0.7.8] - 2026-05-22

### Added

- **`wikibricks_recorder/auto_summary.py`** — opt-in dense LLM summary at
  session flush. One Haiku 4.5 call produces a structured Markdown summary
  (Intent / Approach / Outcome / Artifacts) that becomes the VS-embedded
  `content_text` via a new `write_page(..., content_text_override=...)`
  kwarg. The raw transcript stays in `content.body` for `fn_wiki_read`.
- **`WikiClient.write_page(content_text_override=...)`** — optional kwarg
  on the library write path (both `ops.write_page_sql` and the inline
  MERGE SQL in `WikiClient.write_page`). When set, the literal string
  is written into `content_text` instead of `concat(summary, body)`.
  Default behavior unchanged for every existing caller.
- **`config.load_auto_summary_config()`** — reads the `[auto_summary]`
  section; returns `{}` when absent (default OFF).
- **`page_builder.session_content(dense_summary=...)`** — when a non-empty
  dense summary is provided, it replaces the truncated-first-prompt
  default in `content.summary`. Empty-string falls through to the
  legacy default — guards against accidentally embedding a blank LLM
  response.
- **`summary_ok` / `summary_fail` `wiki_log` op_types** — operators can
  grep `wiki_log` for LLM-summary success rate. Emitted only when
  `auto_summary` is enabled (opt-out users keep a clean log).
- **15 new tests** across `test_recorder_auto_summary.py` (19 total),
  `test_recorder_config.py` (3 new), `test_recorder_hooks.py` (7 new),
  `test_recorder_page_builder.py` (3 new), `test_wiki_ops.py` (3 new),
  `test_client.py` (3 new). Suite grew from 798 → 836.
- Plugin manifest bumped to v0.7.8.

### Why

Vector Search embeds `concat(content.summary, content.body)`. Before 0.7.8
that meant retrieval embeddings were dominated by raw tool output and
bash logs — the recorder's own session transcripts. With `auto_summary`
enabled, VS embeds a 150–300-token structured summary whose every claim
traces to a verbatim transcript span (strict system prompt). Raw events
stay accessible via `fn_wiki_read` and the body field.

The four-section schema (Intent / Approach / Outcome / Artifacts) is
the LangMem episodic-memory pattern recast as bullet propositions
(per the Dense X / Proposition Retrieval finding that atomic
self-contained propositions beat passages across 5 retrieval datasets).

### Research

`docs/research/2026-05-22-summary-first-research.md` cites the MemGPT
external-storage pattern, RAPTOR summary-as-embedded-unit, LangMem
episodic schema, Dense X proposition retrieval, and the Anthropic
memory-tool compaction contract. Plan at
`docs/superpowers/plans/2026-05-22-recorder-summary-first.md`.

### To enable

```toml
# ~/.wikibricks-recorder.toml
[auto_summary]
enabled = true
endpoint = "databricks-claude-haiku-4-5"
max_input_chars = 12000
```

Cost ≈ $0.02/session on Haiku 4.5 (~3k input + ~400 output). At 5
sessions/day this is ~$36/year — negligible.

## [0.7.7] - 2026-05-19

### Added

- **`wikibricks_recorder/auto_title.py`** — LLM-generated session titles,
  opt-in via the `[auto_title]` TOML block. Mirrors the `auto_tag.py`
  contract: synchronous call at flush time, ≤40 output tokens,
  ChatMessage pattern against a Foundation Model serving endpoint
  (default `databricks-claude-haiku-4-5`), silent fall-back to
  `page_builder.session_title` on any error.
- **`config.load_auto_title_config()`** — reads the `[auto_title]`
  section; returns `{}` when absent (default OFF).
- **Wired into `hooks._flush`** — try `auto_title.generate_title`,
  fall through to the deterministic boilerplate-skip heuristic on
  None / failure. Failures logged via `_log_error`.
- **`tests/test_auto_title.py`** — 11 tests covering enable/disable,
  clean output, surrounding-quote stripping, truncation, runaway-response
  rejection, custom endpoint, empty-prompt short-circuit, endpoint-error
  swallowing.
- Plugin manifest bumped to v0.7.7.

### To enable

```toml
# ~/.wikibricks-recorder.toml
[auto_title]
enabled = true
endpoint = "databricks-claude-haiku-4-5"
```

Cost ≈ $0.0005/session (Haiku, ~600 input tokens, ~40 output). At 5
sessions/day this is ~$1/year.

## [0.7.6] - 2026-05-19

### Added

- **`topic_clustering.cluster_pages_by_community(pages)`** — groups pages
  by the `community_id` written nightly by `graph_analytics` (Leiden over
  the currently-valid edge graph). Drops null-community + sub-threshold
  clusters; sorts within-cluster by `hub_score` desc so synthesis picks
  authoritative members first.
- **`topic_clustering.topic_slug_from_titles(titles, community_id=None)`**
  — deterministic slug derivation from cross-title word frequency, with
  stop-word filtering and a `community-<id>` fallback.
- **`notebooks/promote_topics.py` is now live**, not scaffolding. The
  three-step pipeline (cluster → synthesise → judge) is fully wired
  against `databricks-claude-sonnet-4-5` (synth) +
  `databricks-claude-haiku-4-5` (judge). Writes pass the 1–5 judge
  threshold (default 4.0); rejects log `op_type='promote_topic_reject'`.
- **`promote_topics` task** in `wiki_curate_job.yml`. Depends on
  `graph_analytics`. Capped by `max_topics_per_run` (default 20).
- **`tests/test_topic_clustering_community.py`** — 12 tests covering
  empty input, null-community drop, singleton drop, hub_score ordering,
  slug determinism, fallback.
- **`tests/test_promote_topics_notebook.py`** — 12 drift-guard assertions
  on the notebook contract.
- Plugin manifest bumped to v0.7.6.

## [0.7.5] - 2026-05-19

### Changed

- **PageRank RRF rerank is now the DEFAULT in `WikiClient.search`.** The
  `graph_analytics` task computes `hub_score` nightly; not using it was
  waste. Opt out per-call with `rerank_with_pagerank=False` or globally
  via the new `WIKIBRICKS_DISABLE_PAGERANK_RERANK=1` env var.
- **`fn_wiki_search` UC function blends VS rank + PageRank rank via RRF
  (k=60) in pure SQL.** Managed-MCP callers (Databricks managed MCP at
  `/api/2.0/mcp/functions/<catalog>/<schema>`) now get the same ranking
  quality as Python `WikiClient.search`. Implemented with two window
  functions over the over-fetched VS hits joined to `pages.hub_score`;
  pages without a hub_score still appear via VS rank only.
- Plugin manifest bumped to v0.7.5.

## [0.7.4] - 2026-05-19

### Changed

- **`WikiClient.list_pages` and `WikiClient.search` exclude pages tagged
  `ephemeral:stub` by default.** Pass `include_ephemeral=True` to surface
  them. `search` overfetches by 3× to keep `num_results` honest when stubs
  are mixed in.
- **`fn_wiki_search` UC function** (managed-MCP read surface) inherits the
  same filter. Inner `vector_search()` bumped from `num_results => 20` to
  `40` to account for the post-filter.
- **Default chunk size raised from 8 000 → 30 000 characters** via the new
  `segregate_logic.DEFAULT_MAX_CHARS_PER_CHUNK` constant. Cuts per-page chunk
  count ~3× on typical session bodies without measurable retrieval-quality
  loss. The notebook widget `max_chars_per_chunk` still wins when set.
- **`WikiClient.search(rerank_with_pagerank=True)`** opt-in flag composes
  with the existing `rerank_by_citations` flag. RRF (k=60) blends VS rank
  with PageRank rank from `pages.hub_score`; pages without a hub_score
  contribute 0 but stay visible via their VS rank.

### Added

- **`WikiClient.update_graph_scores(scores)`** — batch MERGE of
  `hub_score` + `community_id` for the graph_analytics task.
- **`pages.hub_score`, `pages.community_id`, `pages.memory_class`** columns
  in the canonical DDL (`ops.create_tables_sql`).
- **`links.valid_from`, `links.valid_until`** bi-temporal columns.
- **graph_analytics + tag tasks** in `wiki_curate_job.yml`. Five-task DAG
  now: curate → segregate, graph_analytics, tag, promote. `igraph` added
  to the serverless environment.
- **Plugin manifest bumped to v0.7.4.**
- **`llm:`-prefixed tags preserved across `write_page` and
  `bulk_write_pages` MERGEs** — recorder writes no longer wipe the
  auto-tag task's contributions.

## [0.7.3] - 2026-05-19

### Fixed

- **Recorder titles no longer mirror LLM system prompts.**
  `page_builder.session_title` skips boilerplate lines (`"You are…"`,
  `"Apply maximum compression. Rules:"`, `"Summarize…"`, lone `Rules:` /
  `Instructions:` headers, and bullet-list items) and picks the first
  informative line of the prompt. Sessions whose every line is scaffolding
  fall back to `Session <short-id>`.
- **Ephemeral 1-prompt `/tmp` sessions are no longer written as pages.**
  New `page_builder.is_ephemeral(state)` returns True when `cwd` is
  `/tmp`, `/private/tmp`, or `/var/tmp`, or when the session has fewer
  than `WIKIBRICKS_RECORDER_MIN_EVENTS` events (default 2). `_flush`
  short-circuits — no page write, no chunks, no curate cost.

### Compatibility

- `_looks_like_system_prompt` kept as a back-compat shim forwarding to the
  new `_is_boilerplate` detector. Whitespace-only input returns False.

## [0.7.2] - 2026-05-19

### Added

- **`examples/team_wiki/`** — multi-agent team-wiki walkthrough plus
  `simulate_team_activity.py` sample-data generator.
- **`examples/audit_demo/`** — bi-temporal audit demo (`audit_demo.py`
  writes a four-page graph through three event windows; `post.md` is a
  Medium-ready essay).

## [0.7.1] - 2026-05-19

### Added

- **Karpathy export** — `python -m wikibricks.export_karpathy <dir>` walks
  every page, writes one `.md` per page with YAML frontmatter and a
  `## Related` section carrying outgoing currently-valid edges as
  `[[wikilinks]]` (plain) or `link_type::[[wikilinks]]` (typed).
- **`graph_logic`, `health`, `tag_logic`, `*_karpathy`** modules — see the
  v0.7.4 commit message for the per-file decision log of the reconciliation
  with remote v0.7.0 (citation parsing, customer-tag vocab, provenance,
  MCP stderr — all preserved).

## [0.7.0] - 2026-05-13

### Added

- **Outcome tracking via citation parsing.** At ``Stop`` time, the
  recorder reads the transcript JSONL at ``payload["transcript_path"]``,
  walks to the agent's most recent assistant message, and extracts every
  ``[wb:<path>]`` marker. One ``op_type='cited'`` row per unique cited
  path goes to ``wiki_log``, with ``details={"session_id": "<sid>"}``.
  Visible stderr summary mirrors the other injection paths::

      wikibricks: cited 2 pages from this session
        - sessions/2026/05/04/abc
        - topics/solvd

  Parser lives in ``src/wikibricks_recorder/citations.py`` (pure
  function, no LLM, no SDK calls). Logging lives in
  ``hooks.py::_log_citations``. All failures (missing transcript, bad
  JSON, log errors) are swallowed silently so the host is never crashed.
- **Citation-aware search reranker.**
  ``WikiClient.search(rerank_by_citations=True)`` now joins each
  candidate hit with its ``wiki_log`` citation count and re-orders by
  ``1/(rank+1) + 0.5 * log(1 + cited_count)``. Pages with ≥ 5 prior
  citations consistently move ahead of un-cited rank-0 hits — the wiki
  *learns* which pages have proven useful and surfaces them first. The
  recorder's MCP search and per-prompt injection both pick up reranking
  automatically when ``WIKIBRICKS_RERANK_BY_CITATIONS=1`` is set; the
  argument flips the default per-call when callers want to override.
  Lives in ``WikiClient._rerank_by_citations`` and
  ``WikiClient._fetch_citation_counts``.
- New ``wiki_log`` op_type: ``cited`` (documented in
  ``CLAUDE.md`` op_type table).

### Changed

- Plugin launcher's ``WIKIBRICKS_PLUGIN_REF`` default bumped from
  ``v0.6.0`` to ``v0.7.0``.
- ``_flush`` now returns the constructed ``WikiClient`` (or ``None``
  when the session was skipped as empty/utility) so callers can reuse
  it. Used by ``on_stop`` to call ``_log_citations`` on the same client
  without re-resolving config.

Test count: 609 → 637 (11 parser + 7 logging + 10 rerank). Ruff clean.

## [0.6.0] - 2026-05-13

### Added

- **Provenance citations on injected context.** Every
  ``additionalContext`` block emitted by the recorder (both the
  ``SessionStart`` prelude and the per-prompt ``UserPromptSubmit``
  injection) now ends with a one-line directive instructing the agent
  to cite any page it used inline as ``[wb:<path>]``::

      When you use information from any page above, cite the path
      inline as [wb:<path>] so the user can trace the source.

  The marker format is stable and machine-parseable. It lets users
  trace which prior page the agent's answer drew from, and sets up
  outcome tracking in 0.7.0 (citations → ``helpful_score`` column →
  search reranker). Lives in
  ``src/wikibricks_recorder/hooks.py::_CITATION_DIRECTIVE`` and is
  appended to both injection paths. No behavior change when
  ``WIKIBRICKS_INJECT_CONTEXT`` is unset.

### Changed

- Plugin launcher's ``WIKIBRICKS_PLUGIN_REF`` default bumped from
  ``v0.5.0`` to ``v0.6.0``.

Test count: 607 → 609 (2 citation-directive assertions). Ruff clean.

## [0.5.0] - 2026-05-13

### Added

- **SessionStart prelude — "where you left off."** When
  ``WIKIBRICKS_INJECT_CONTEXT=1`` (same env-var gate as Stage 2
  per-prompt injection), the recorder's ``on_session_start`` hook now
  queries wikibricks for the most recent 3 session pages tagged with
  the current ``cwd:<basename>`` and emits them as a one-shot
  ``SessionStart`` ``additionalContext`` JSON. The agent sees prior
  sessions from this directory the moment the new session opens — no
  search, no prompt needed. User-visible stderr summary mirrors the
  per-prompt path:

      wikibricks: prelude - 3 prior sessions in 'wikibricks'

  New ``WikiClient.list_recent_by_cwd_tag(cwd_basename, limit=3)``
  exposes the underlying SQL (filter by ``array_contains(tags,
  'cwd:X')``, order by ``updated_at`` DESC). LLM-free per the library
  hard rules. Hook lives in
  ``src/wikibricks_recorder/hooks.py::_emit_cwd_prelude``.
- **Search-visibility stderr in the MCP server.** Every successful
  ``wiki_search``, ``wiki_read_full``, ``wiki_write_page``,
  ``wiki_promote_answer``, and ``wiki_index`` MCP tool call now prints
  a single terse line to stderr. Examples:

      wikibricks: search "AGI roadmap" -> 3 hits
      wikibricks: read sessions/2026/05/04/abc
      wikibricks: wrote topics/solvd

  Closes the trust gap from the other side: you already saw automatic
  injection via Stage 2; now you also see every agent-initiated wiki
  call. Always-on, no env-var gate, terse format (one line, ≤120
  chars). Hook lives in
  ``src/wikibricks_recorder/wiki_mcp.py::_log_tool_call``.

### Changed

- Plugin launcher's ``WIKIBRICKS_PLUGIN_REF`` default bumped from
  ``v0.4.1`` to ``v0.5.0``.

Test count: 591 → 607 (7 cwd-tag + 5 prelude + 4 stderr-summary).
Ruff clean.

## [0.4.1] - 2026-05-13

### Fixed

- **`WikiClient.list_active_vocabulary`** no longer crashes on an empty
  result set. The Databricks SDK returns ``resp.result.data_array =
  None`` for 0-row SELECTs, not ``[]``; the helper now coerces to ``[]``
  before iterating.
- **`auto_tag.extract_topic_slugs`** now passes typed
  ``databricks.sdk.service.serving.ChatMessage`` objects to
  ``serving_endpoints.query`` instead of plain dicts. The dict form was
  silently rejected by the SDK (``'dict' object has no attribute
  as_dict'``), so the call never made it to FMAPI in 0.4.0. End-to-end
  test now extracts real slugs against the live endpoint in ~2s.

## [0.4.0] - 2026-05-13

### Added

- **Evolving customer-tag vocabulary via LLM (opt-in).** New
  ``wiki_vocabulary`` Delta table accumulates topic slugs over time;
  the recorder asks a configurable Databricks Foundation Model API
  serving endpoint to extract 1-3 slugs per session at flush time,
  upserts them, and tags the session with ``customer:<slug>``. Replaces
  the static ``[topic_keywords]`` design from 0.3.4 with one that grows
  naturally as the corpus expands. **Off by default**; enable via
  ``[auto_tag]`` section in ``~/.wikibricks-recorder.toml``::

      [auto_tag]
      enabled = true
      endpoint = "databricks-claude-haiku-4-5"
      max_input_tokens = 1000

  Slugs persist with ``source ∈ {llm, manual, seed}`` and ``status ∈
  {candidate, active, archived}``; a slug crosses to ``active`` once
  ``count >= 3``. ``WikiClient`` gains
  ``upsert_vocabulary_slugs(slugs, source)`` and
  ``list_active_vocabulary()`` (both LLM-free per the library's hard
  rules — the LLM call lives in ``src/wikibricks_recorder/auto_tag.py``).
- ``config.load_auto_tag_config()`` reads the new TOML section.

### Schema

- New table ``{catalog}.{schema}.wiki_vocabulary`` created by
  ``create_tables_sql()``. Run the ``deploy_wiki_store`` notebook or
  ``databricks bundle deploy`` to add it to existing wikis.

### Privacy

- The auto-tag path sends a sample of prompt text to your configured
  Databricks serving endpoint for entity extraction. Default endpoint
  ``databricks-claude-haiku-4-5`` stays within your workspace tenant.
  No data leaves Databricks. Disable by omitting the ``[auto_tag]``
  section or setting ``enabled = false``.

### Changed

- Plugin launcher's ``WIKIBRICKS_PLUGIN_REF`` default bumped from
  ``v0.3.4`` to ``v0.4.0``.

Test count: 564 → 591 (added 8 vocabulary + 16 auto_tag + 2 hook + 1 ops).
Ruff clean.

## [0.3.4] - 2026-05-13

### Added

- **Visible context injection.** When
  `WIKIBRICKS_INJECT_CONTEXT=1` causes hits to be injected, the recorder
  now also writes a one-line summary to stderr so Claude Code surfaces
  it to the user above the agent's reply:
  ``wikibricks: injected 2 pages\n  - sessions/2026/05/08/abc\n  - …``
  Silent when no hits, env var off, or any exception. The stdout
  ``additionalContext`` JSON for the model is unchanged.
- **Auto-tag sessions by customer-keyword.** New
  ``[topic_keywords]`` section in ``~/.wikibricks-recorder.toml`` adds
  ``customer:<slug>`` tags to every flushed session whose prompts
  match the keyword terms (case-insensitive substring). Backward
  compatible: no section ⇒ no auto-tagging.

  ```toml
  [topic_keywords]
  solvd = ["solvd", "controlexpert"]
  allianz-italy = ["allianz italy", "az italy"]
  ```

  ``page_builder.session_tags(state, topic_keywords=...)`` gained the
  optional kwarg; ``config.load_topic_keywords()`` exposes the
  parsed map.

### Changed

- Plugin launcher's ``WIKIBRICKS_PLUGIN_REF`` default bumped from
  ``v0.3.3`` to ``v0.3.4``.

Test count: 556 → 564. Ruff clean.

## [0.3.3] - 2026-05-13

### Fixed

- **Recorder utility-session filter now catches `/private/tmp`.** macOS
  resolves `/tmp` to `/private/tmp` at the kernel level; Claude Code
  reports the resolved path. The cwd-prefix list previously had
  `/tmp/` but not `/private/tmp/`, so skill / sub-agent sessions
  spawned from `/private/tmp` slipped past the cwd-prefix path
  (they were still caught by the system-prompt-text path as a
  fallback, but this closes the gap). Exact-equality cases (`/tmp`,
  `/private/tmp` with no trailing slash) are now caught too.

### Changed

- Plugin launcher's `WIKIBRICKS_PLUGIN_REF` default bumped from
  `v0.3.2` to `v0.3.3`.

## [0.3.2] - 2026-05-13

### Fixed

- **Recorder no longer records skill / sub-agent sessions.** `_flush`
  skips any session whose `cwd` is rooted in a tmp directory
  (`/private/var/folders/`, `/var/folders/`, `/tmp/`) or whose single
  prompt matches a known system-prompt prefix (`"You are "`,
  `"Apply maximum "`). Filter lives in `hooks.py::_is_utility_session`.
- **Session titles prefer the first non-templated user prompt.**
  `page_builder.session_title` walks events for a prompt that does not
  match `_looks_like_system_prompt`, falling back to the original
  `first_prompt` if every prompt is templated.

### Added

- **Proactive context injection (opt-in).** With
  `WIKIBRICKS_INJECT_CONTEXT=1`, the recorder's `on_user_prompt_submit`
  hook searches the wiki and emits up to 3 relevant prior pages as a
  `UserPromptSubmit` `additionalContext` JSON response. Hits from the
  current session are filtered out; short prompts (<10 chars) and
  search failures are silent. Default off. See
  `hooks.py::_emit_relevant_context`.
- **`scripts/purge_noise.py` + `src/wikibricks/title_repair.py`.**
  One-shot cleanup tool that deletes session pages with system-prompt
  template titles. Used to take a personal wiki from 1620 pages down
  to 44 after the recorder filter shipped. Dry-run by default;
  `--apply` to mutate. Logs `op_type='purge_noise'` to `wiki_log`.
- **Stage 1 scaffolding for cross-session topic synthesis.** New
  `src/wikibricks/topic_clustering.py` with `cluster_pages_by_keyword`
  (pure function, LLM-free per the library's hard rules) groups
  session pages into topic buckets by case-insensitive title match.
  New `notebooks/promote_topics.py` is a dry-run-only stub: enforces a
  corpus-size guard (default 80 pages), clusters via the keyword map,
  prints eligible topics, and leaves the LLM-synthesis + judge step as
  a clearly-marked TODO. No bundle resource entry added by default —
  the notebook is opt-in.

### Changed

- Plugin launcher's `WIKIBRICKS_PLUGIN_REF` default bumped from
  `v0.3.1` to `v0.3.2`.

Test count: 491 → 553. Ruff clean.

## [0.3.1] - 2026-05-05

### Added

- **`wikibricks.curate_logic.run_connect_phase`** — pure helper that fans
  `propose_fn` across paths via `ThreadPoolExecutor` and batches one
  `commit_fn` call at the end. Used by `notebooks/wiki_curate.py`.
- **`wiki_curate` notebook** gains a `propose_concurrency` widget
  (default 8). The bundle resource sets the same default for scheduled
  runs.

### Performance

- **`notebooks/wiki_curate.py` connect phase** now runs `propose_edges`
  in parallel up to `propose_concurrency` workers and commits all
  high-confidence edges in a single MERGE INTO links instead of one
  MERGE per page. On the personal philipp wiki (~92 candidate pages
  per run on serverless) this drops the connect phase from ~9 min to
  ~1-2 min wall time.

### Fixed

- **`notebooks/wiki_curate.py` connect filter** restricts the recent-
  pages window to `parent_id IS NULL` and
  `created_by NOT IN ('segregate', 'promote')`. Segregate-produced
  chunk children dominated the prior 48h lookback after a single big
  segregate run (984 of 1074 "recent" pages on 2026-05-05); the loop
  was processing stale chunks instead of new agent writes.
- **`WikiClient.propose_edges`** accepts an optional `other_pages`
  argument. Batch callers can pre-fetch `list_pages()` once and pass it
  in, collapsing N list_pages SQL round-trips into 1. Default behavior
  unchanged.

## [0.3.0] - 2026-05-04

### Fixed

- **Curate / segregate / promote notebooks** set `WIKIBRICKS_CATALOG`
  and `WIKIBRICKS_SCHEMA` from job widgets before importing
  `wikibricks.ops` — `ops` reads them at module-load time and was
  silently resolving every table to `main.wiki` (latent since the
  recorder shipped). `resources/wiki_curate_job.yml` now passes
  `catalog` / `schema` widgets to all three task `base_parameters`.
- **Phase 4 health check** in the curate notebook used the wrong
  column names (`id`, `body`) — corrected to `page_id` / `content_text`
  via SQL aliases so `classify_page_health` / `find_duplicate_paths`
  keep working unchanged.
- **`run_sql` in segregate** used `wait_timeout="60s"`, which the
  Databricks Statements API rejects — capped at 50s, lowered to 30s
  for consistency with curate.

### Performance

- **`WikiClient.commit_edges`** now batches into a single MERGE
  (multi-row VALUES source) instead of one MERGE per edge. At scale
  (60 edges per session page × 66 pages updated in 48h) this turns 3960
  round-trips into 66, dropping a ~3-hour curate phase to ~3 minutes.
- **`WikiClient.propose_edges`** drops the N+1 `SELECT page_id FROM
  pages WHERE path = ...` per matching title — `list_pages()` now
  returns `page_id` (additive change, no breaking callers) and
  `propose_edges` reads it directly. The shipped curate job lowers
  `max_pages_per_run` default from 500 → 100 to keep cold-start
  serverless runs inside the 30-min task budget; pages beyond the cap
  roll forward into the next nightly window.
- **New `WikiClient.write_pages(pages: list[dict])`** does real batched
  writes — exactly four SQL statements regardless of N (history INSERT,
  pages MERGE, pages_vs_source MERGE, wiki_log INSERT). `bulk_write_pages`
  delegates to it. `notebooks/wiki_segregate.py` collects parent + chunk
  children into one `wiki.write_pages(...)` call per oversize page,
  collapsing 6× round-trips per page into 1×. End-to-end: a curate run
  that previously timed out at 30 min now completes all three tasks
  in ~32 min on cold serverless including the full segregate workload.

### Changed

- Plugin launcher's `WIKIBRICKS_PLUGIN_REF` default switched from `main`
  to `v0.3.0` so installs are reproducible by default. Override to
  `main` (or any other ref) for bleeding-edge.
- `plugin/README.md` rewritten with a two-half install (workspace bundle
  deploy first, plugin install second), corrected
  `WIKIBRICKS_RECORDER_DIR` default (`~/.wikibricks_recorder/`, not
  `~/.wikibricks/sessions/`), and added the missing `WIKIBRICKS_TARGET`
  row to the env-var table.
- Root `README.md` restructured to lead with the personal recorder as
  the 5-minute on-ramp (was buried 65% through the document). Trimmed
  448 → 203 lines: dropped redundant "Why" section, compressed the
  maintenance-loop description, cut deploy-customization sub-sections
  that moved one link away to `databricks.yml`, cut the team-shared
  `.mcp.json` snippet (superseded by the plugin's own auto-registering
  `.mcp.json`), and replaced pre-plugin install instructions
  (`uv pip install -e ".[recorder]"` + `claude mcp add`) with the
  marketplace install path. Test count 453 → 491; wheel filename
  0.2.0 → 0.3.0.

### Added

- **`wikibricks-recorder` Claude Code plugin** at `plugin/`. Users install
  via marketplace flow instead of hand-editing `~/.claude/settings.json`:
  ```
  /plugin marketplace add https://github.com/philtief/wikibricks-dev.git
  /plugin install wikibricks-recorder@wikibricks
  ```
  Plugin ships:
  - `.claude-plugin/plugin.json` — full manifest (name, description,
    version, homepage, repository, license, keywords, author).
  - `hooks/hooks.json` — 5 events (SessionStart 60s, UserPromptSubmit 5s,
    PostToolUse 5s, Stop 30s, SessionEnd 30s) routed through `bin/launch.sh`.
  - `.mcp.json` — `wiki` stdio MCP server, auto-registers without
    `claude mcp add`. Tools surfaced as
    `mcp__plugin_wikibricks-recorder_wiki__*`.
  - `bin/launch.sh` — idempotent `uv tool install` from Git URL into
    `${CLAUDE_PLUGIN_DATA}` on first call (~5s cold), exec's cached binary
    thereafter (~70ms warm). Override Git ref / URL via
    `WIKIBRICKS_PLUGIN_REF` / `WIKIBRICKS_PLUGIN_GIT`.
- **Repo-root `.claude-plugin/marketplace.json`** registers the plugin in
  the `wikibricks` marketplace so a single `claude plugin marketplace add`
  picks up future plugins from the same repo.
- **`wikibricks-recorder-hook` console script** wired to
  `wikibricks_recorder.hooks:main`. Lets the plugin launcher exec a
  binary instead of `python -m wikibricks_recorder.hooks`.
- **`wikibricks_recorder.wiki_mcp.format_tool_response()`** — extracted
  the MCP `call_tool` error-wrapping path into a sync helper. Unknown
  tools, raising tools, and bad kwargs all return `{"error": "..."}` JSON
  instead of crashing the stdio loop. Five new robustness tests in
  `tests/test_recorder_wiki_mcp.py::TestFormatToolResponse`.
- **`tests/test_plugin_manifest.py`** — 16 manifest tests covering plugin
  fields, version sync with `pyproject.toml`, hook events + timeouts,
  MCP server entry, launcher executability, and marketplace consistency.
- README "Team-shared MCP via `.mcp.json`" section — show how a team commits
  one `.mcp.json` at the repo root that pins the recorder to a Git ref, so
  every contributor's Claude Code session registers the same `wiki` server
  without each developer running `claude mcp add`. Documents the file://
  portability caveat and the per-machine nature of hooks. (Largely
  superseded by the plugin's own `.mcp.json` from 0.3.0; kept for
  non-plugin / multi-server team setups.)
- `wiki-init --install-hooks` — auto-merge the five recorder hooks into
  `~/.claude/settings.json` (existing entries preserved, file backed up
  first). Replaces the manual `sed examples/claude-settings.json` step.
  Honors `--python` and `--settings` for non-default paths. Marked
  legacy in 0.3.0 — recommended install path is now the plugin.
- `wiki-init --install-hooks --scope {user,project,local}` — match the
  `claude mcp add` UX. `user` (default) writes to `~/.claude/settings.json`;
  `project` writes to `./.claude/settings.json` (team-shared, commit to
  git); `local` writes to `./.claude/settings.local.json` (personal-per-
  project, gitignored). `--scope` and `--settings` are mutually exclusive.
- `wiki-init --uninstall-hooks` — inverse of `--install-hooks`. Matches
  recorder entries by exact command string, leaves any non-recorder hooks
  untouched, drops empty event arrays, and backs up before writing.
  Honors the same `--scope` / `--settings` / `--python` flags.
- README "Recorder" section now lists every MCP tool's required and
  optional arguments, sourced from `wiki_mcp.py::get_tool_schemas()`.

### Fixed

- `wiki-init` personal-flow Next-steps message: replaced the broken
  `uvx --from . '.[recorder]'` invocation with the correct
  `uvx --from "wikibricks[recorder] @ file://$(pwd)"` form, and pointed
  users at the new `--install-hooks` flag.
- `wiki_mcp.py` module docstring: updated the stale
  `claude mcp add wiki -- uvx --from . wikibricks-mcp` example to the
  working PEP 508 form (`--scope user`, absolute `file://` URL).

## [0.2.0] - 2026-05-03

### Added

- **`wikibricks_recorder` package** — optional Claude Code → wiki bridge
  shipped alongside the library. Install with `pip install wikibricks[recorder]`.
  Three console scripts are wired in `pyproject.toml`:
  - `wiki-init` — interactive setup that writes `~/.wikibricks-recorder.toml`
    with a `[wikis.<name>]` section per wiki. Three flows: personal,
    team-create (emits a non-secret `wikibricks-team.toml` for sharing +
    GRANT SQL for the owner), team-join (consumes the shared toml + your
    own CLI profile).
  - `wiki-target` — switch which configured wiki the hooks write to.
    Persists the choice in `~/.wikibricks/active-target`. `WIKIBRICKS_TARGET`
    env var beats the file for one-shot overrides.
  - `wikibricks-mcp` — stdio MCP server registered with Claude Code via
    `claude mcp add`. Five tools (3 read, 2 write) talking directly to
    `WikiClient`. The library's UC functions stay deployed for managed-MCP
    consumers; this is a separate consumer-side surface.
- **Multi-wiki TOML format.** `~/.wikibricks-recorder.toml` now supports
  multiple `[wikis.<name>]` sections (e.g. `[wikis.personal]` next to
  `[wikis.team-platform]`). The legacy `[recorder]` single-section format
  is still read for back-compat.
- **`examples/claude-settings.json`** — copy-pasteable hook template with
  a `/PATH/TO/wikibricks-recorder` placeholder so new users `sed` it to
  their checkout and merge into `~/.claude/settings.json` instead of
  hand-crafting five identical hook entries.
- **`create_uc_functions_sql(..., enabled=...)`** — opt-in subset deploy
  for the eight UC functions. `enabled=None` (the default) keeps the
  existing behavior (deploy all eight); pass a set or list of names to
  deploy only those, e.g. `{"fn_wiki_search", "fn_wiki_read_full"}`.
  Unknown names raise `ValueError` so a typo can't silently produce a
  partial deploy. The library still ships every function — this is
  purely about which surface the managed-MCP endpoint exposes.
- **`UC_FUNCTION_NAMES`** — public tuple of the eight names, re-exported
  from `wikibricks` so callers can reference them without string typos.
- **`enabled_uc_functions` widget on `deploy_wiki_store`** — comma-separated
  list (default empty = all eight). Lets a deployment narrow the MCP tool
  surface without forking the notebook.
- **`scripts/sdk_redeploy.py`** — direct-SDK redeploy that bypasses
  Terraform, an escape hatch for `databricks bundle deploy` failing with
  `openpgp: key expired` on some CLI versions. Workspace-agnostic via
  required `WIKIBRICKS_CATALOG` / `WIKIBRICKS_SCHEMA` /
  `WIKIBRICKS_WAREHOUSE_ID` env vars; optional
  `WIKIBRICKS_ENABLED_UC_FUNCTIONS` mirrors the bundle variable of the
  same name. Idempotent: schema → seven tables → managed `wheels` volume
  + wheel upload → drop UC functions outside enabled set →
  `CREATE OR REPLACE` enabled set → verify.

### Changed

- **AGENTS.md hard rules 1 + 2 scoped to the library.** "No LLM in `src/`"
  and "no bespoke MCP server" now explicitly bind only the `src/wikibricks/`
  package. `src/wikibricks_recorder/` is consumer-side tooling and ships its
  own stdio MCP server because UC functions cannot do DML.

### Fixed

- **`fn_wiki_search` SQL UDF compatible with current `vector_search()`
  TVF.** Two runtime errors blocked the function from being created:
  `AI_SEARCH_HYBRID_QUERY_PARAM_DEPRECATION_ERROR` (HYBRID mode now requires
  `query_text =>` instead of `query =>`) and `NON_FOLDABLE_ARGUMENT` (UDF
  parameters can't be passed straight through to `num_results =>`). The
  inner `num_results` is now fixed at 20; the outer query trims to the
  caller's K via `ROW_NUMBER()`.
- **`deploy_wiki_store` notebook honors catalog/schema widgets.** Two real
  bugs surfaced when running against a non-default catalog/schema:
  `wikibricks.ops` reads `WIKIBRICKS_CATALOG` / `WIKIBRICKS_SCHEMA` at
  import time, so the notebook now sets `os.environ` from widgets BEFORE
  importing. `table_names` extended to all seven tables (was 5; missed
  `pages_vs_source` and `promote_checkpoint`, raising IndexError on the
  6th iteration).

## [0.1.5] - 2026-04-29

### Added

- **Page segregation for oversize pages.** Long pages now have a first-class
  parent/child split path. `wiki.pages` and `wiki.pages_history` gain
  `parent_id`, `chunk_index`, `health_status`, `health_score`, and
  `last_health_check` columns. The curate job's new Phase 4 health check
  classifies each page as `ok` / `empty` / `oversize` (default threshold
  50KB) and writes the verdict back via one batched UPDATE per status
  bucket. The new opt-in `wiki_segregate` notebook reads pages flagged
  `oversize`, asks the chat endpoint for a 1-2 sentence summary plus one
  title per chunk, then writes a parent (summary + Markdown ToC) and N
  chunk children joined by `parent_id`/`chunk_index`. Deterministic
  chunking and ToC construction live in `wikibricks.segregate_logic` and
  are unit-tested; the LLM call lives in the notebook only, per the
  AGENTS.md library-LLM-free rule.
- **`fn_wiki_read_full` UC function.** Reassembles a parent page with its
  chunks in `chunk_index` order, returning a single document. Exposed via
  managed MCP so agents reading a segregated page see the same content as
  before splitting.
- **`WikiClient.write_page(parent_id=..., chunk_index=...)`.** Two new
  optional kwargs let callers (and the segregate notebook) write chunk
  children that link to their parent and order deterministically.
- **`wikibricks.curate_logic.classify_page_health` /
  `find_duplicate_paths` / `build_health_summary`** — pure helpers for the
  curate health phase, with 15 new unit tests.
- **`wikibricks.segregate_logic.chunk_at_boundaries` / `child_path` /
  `child_title` / `build_parent_body`** — pure helpers for the split flow,
  with 14 new unit tests.
- **`wikibricks.make_agent_tools(warehouse_id)`** — factory that returns
  plain Python callables for the two write operations UC functions cannot
  perform: `wiki_write_page` and `wiki_promote_answer`. Register with any
  agent framework (Databricks Agent Framework, LangChain, LlamaIndex, a
  custom MCP server) to give agents direct promote-to-memory capability
  without routing through the curate job's trace-driven promote path.
- **`segregate` / `segregate_skip` `wiki_log` op_types.** Each split run
  appends a `segregate` row per parent (with chunk count + chunk titles)
  and a `segregate_skip` row when the chunker can't split a single
  oversize paragraph.

### Changed

- **`fn_wiki_search` now uses Vector Search.** The UC function previously
  did SQL `LIKE` substring matching over `content_text` / `title`, which
  meant the managed-MCP search surface was keyword-only while the Python
  `WikiClient.search` path was semantic. `fn_wiki_search` now calls the
  `vector_search()` SQL TVF with `query_type => 'HYBRID'` against
  `pages_index`, returning top-K pages ranked by semantic + lexical
  relevance with their full `content_text`. Signature changed from
  `(question, mode)` to `(question, num_results INT DEFAULT 5)` — agents
  that hard-coded `mode='HYBRID'` must drop the argument.
- **`wikibricks.ops.CATALOG` / `SCHEMA` are env-var driven.**
  `WIKIBRICKS_CATALOG` and `WIKIBRICKS_SCHEMA` retarget the library
  defaults from `main.wiki` without editing source — useful for forks and
  per-workspace deployments.

## [0.1.4] - 2026-04-23

### Added

- **`WikiClient.sync_index()`** — triggers the DELTA_SYNC Vector Search
  index and logs `vs_sync` / `vs_sync_fail` to `wiki_log`. Called
  automatically from `promote_from_traces` after successful promotions and
  from the Streamlit app after chat-mode auto-promote, so freshly written
  pages are searchable within one sync cycle.
- **Parse-fail discrimination in promote.** `promote_from_traces.py` now
  distinguishes *judge returned non-numeric text* (`promote_parse_fail`)
  from *legitimate low score* (`promote_reject`), so operators querying
  `wiki_log` can spot prompt drift independently of quality failures.
- **`judge_response_is_numeric`** helper in `wikibricks.promote_logic`.
- **Cross-task DAG integration test** (`tests/test_job_dag.py`) — executes
  `wiki_curate.py` and `promote_from_traces.py` in the real job DAG order
  against a `spec_set=WikiClient` mock, so method drift between the two
  notebooks fails loudly instead of silently.
- **`scripts/diagnose_traces.py`** — standalone diagnostic reporting
  trace volume, query-length percentiles, exact-match cluster eligibility,
  and `wiki_log` event counts. Run before trusting a scheduled promote
  window.
- **Env-var configuration for the Streamlit app**
  (`WIKIBRICKS_WAREHOUSE_ID`, `WIKIBRICKS_VS_INDEX`, `WIKIBRICKS_LLM_MODEL`).
  Wired from bundle vars in `resources/app.yml`. Warehouse ID and VS index
  are required — the app fails fast with a clear error if unset.
- **Browse-mode AppTest coverage** (`tests/test_app.py::TestBrowseMode`) —
  six in-process Streamlit tests covering the tree-button → session-state
  round-trip.
- **`databricks.override.example.yml`** — template for per-developer
  workspace host / profile / warehouse_id overrides.
  `databricks.override.yml` is gitignored.

### Changed

- **`judge_threshold` default lowered 4.5 → 4.0.** The judge prompt asks
  for a single digit 1–5, so 4.5 rejected every integer score; 4.0 admits
  4 and 5 as intended.
- **Portable bundle variable defaults.** `catalog` default is `main`;
  `warehouse_id` has no default and must be supplied per target. Target
  `workspace.host` / `profile` are provided via `DATABRICKS_CONFIG_PROFILE`
  env var or the new override file.
- **README refresh** — added operational-telemetry table, env-var config
  table, updated test count (223 → 305) and wheel version (0.1.3 → 0.1.4).

### Fixed

- **`_log` telemetry writes on SQL warehouse.** Rewrote the insert from
  `INSERT INTO ... VALUES (uuid(), ...)` to `INSERT INTO ... SELECT uuid(),
  ...` so `wiki_log` rows persist from the warehouse execution path.

## [0.1.3] - 2026-04-22

### Added

- **LLM-free graph primitives on `WikiClient`**: `propose_edges` (VS nearest-
  neighbor + exact-title entity match with per-edge `confidence` + `origin`),
  `commit_edges` (batch MERGE), `graph_neighbors` (1–3 hop traversal), and
  `fix_broken_links` (deterministic endpoint cleanup). No model calls inside
  WikiBricks — the calling agent stays the only LLM in the loop.
- **Default curate pipeline** (`notebooks/wiki_curate.py` +
  `resources/wiki_curate_job.yml`). One shipped Lakeflow Job with two tasks:
  (1) `curate` — deterministic connect + lint + repair (no LLM, library
  contract); (2) `promote` — optional trace-driven LLM synthesis that depends
  on `curate`. Drop the `promote` task block to run LLM-free.
- `confidence FLOAT` and `origin STRING` columns on the `links` table, with
  allowed origins `manual | auto-vs | auto-title | auto-cite`.

### Changed

- `add_link_sql` now writes `confidence` + `origin` and raises `ValueError` on
  invalid origin or out-of-range confidence.
- Legacy `notebooks/wiki_lint.py` fixed: the `wiki_log` INSERT now matches the
  real schema (`log_id, op_type, path, query, details, created_by`).
- Removed `resources/wiki_lint_job.yml` and `resources/promotion_pipeline.yml`;
  both superseded by the single two-task `wiki_curate_job.yml`. Promote task
  uses `databricks-claude-sonnet-4-5` by default (override via `llm_model`
  bundle var).

## [0.1.0] - 2026-04-21

Initial public release. A Delta + Vector Search wiki store for AI agents on
Databricks.

### Added

- **`WikiClient` Python API** (`src/wikibricks/client.py`) with `write_page`,
  `read_page`, `search`, `history`, `ingest_source`, `promote_answer`,
  `bulk_write_pages`, and `materialize_index`.
- **Five Delta tables** (`pages`, `pages_history`, `links`, `sources`, `log`)
  created by the `deploy_wiki_store` notebook. CDF enabled on `pages`.
- **Vector Search DELTA_SYNC index** (`pages_index`) over `pages.content_text`
  using `databricks-bge-large-en`. Three search modes: HYBRID (default), ANN,
  FULL_TEXT.
- **Seven UC functions** auto-exposed as MCP tools at
  `/api/2.0/mcp/functions/<catalog>/<schema>`: `fn_wiki_search`, `fn_wiki_read`,
  `fn_wiki_history`, `fn_wiki_log`, `fn_wiki_index`, `fn_wiki_schema`,
  `fn_wiki_write_help`. No FastMCP; Databricks managed MCP surfaces UC
  functions natively.
- **Versioned writes.** Every `write_page` archives the previous version to
  `pages_history`; `history(path)` returns the full lineage.
- **Typed links** between pages (`cites`, `related`, `supports`, `depends_on`,
  …) - cross-reference graph queryable in plain SQL.
- **Domain-agnostic seed loaders** (`src/wikibricks/seeds/`): `sample`
  (5 meta-pages), `hotpot` (HotpotQA, ~66k pages), `custom` (JSONL), `none`.
- **Databricks Asset Bundle** (`databricks.yml` + `resources/`) with
  `dev` / `staging` / `prod` targets. One-command deploy:
  `databricks bundle deploy --target dev`.
- **Reference Streamlit app** (`app/app.py`) with chat, Write, and Browse modes.
  Auto-promotes judged answers (score ≥ 4 on a 1-5 scale) into synthesis pages
  with `cites` edges back to source pages.
- **Batch promotion pipeline** (`resources/promotion_pipeline.yml`) -
  scheduled job that promotes offline-judged answers.
- **Nightly lint job** (`resources/wiki_lint_job.yml`) - scans for orphans,
  stale pages, duplicates, and broken links; writes issues to `log`.
- **Observability dashboard** (`resources/observability_dashboard.yml`) -
  pages, writes, reads, and lint findings over time.
- **Evaluation harness**:
  - HotpotQA fetch + seed + retrieval benchmark (`scripts/hotpot_*.py`,
    `notebooks/benchmark_hotpot.py`). Produces `benchmark_results.json` and
    `hotpotqa_results.html`.
  - 2WikiMultiHopQA fetch + seed + retrieval + generation + eval
    (`scripts/twowiki_*.py`), including an 8-variant cheap-lever ablation
    (`scripts/twowiki_variants.py`) and a Delta-checkpointed batch loop
    (`scripts/twowiki_batch_loop.sh`). Vendored official v1.1 evaluator.
- **220 unit tests** (`tests/`), no Databricks connectivity required.
- **Documentation**: `README.md`, `examples/hotpotqa.md`,
  `examples/twowiki.md`, `docs/hotpotqa_evaluation.md`,
  `docs/twowiki_evaluation.md`, `docs/img/architecture.{mmd,svg,png}`.

### Benchmarks

- **HotpotQA retrieval pilot** - 500-query HYBRID recall@10 ≈ 89% on a
  66,569-page corpus. Retrieval-only, not a HotpotQA leaderboard metric.
- **2WikiMultiHopQA open-retrieval** - preliminary 350-query ablation. Best
  variant (Sonnet 4.6 + HYBRID + K=10) reaches **Joint F1 21.2** under the
  official v1.1 evaluator. This matches the 2020 paper's own open-retrieval
  baseline (~20); modern 2024-2025 open-retrieval SOTA is 50-65 (task-tuned
  retrievers, iterative multi-hop, rerankers, fine-tuned heads - all outside
  WikiBricks' scope). See [`docs/twowiki_evaluation.md`](docs/twowiki_evaluation.md)
  for full framing.

### Limitations

- Off-the-shelf embeddings only (`databricks-bge-large-en`); no task-tuned
  retriever.
- Single-shot retrieval; no iterative multi-hop.
- No cross-encoder reranker.
- Evaluation harness uses a vendored copy of
  `2wikimultihop_evaluate_v1.py`; vendored assets are gitignored and fetched
  on demand by `scripts/fetch_twowiki.py`.

[Unreleased]: https://github.com/philtief/wikibricks/compare/v0.7.12...HEAD
[0.7.12]: https://github.com/philtief/wikibricks/compare/v0.7.11...v0.7.12
[0.7.11]: https://github.com/philtief/wikibricks/compare/v0.7.10...v0.7.11
[0.7.10]: https://github.com/philtief/wikibricks/compare/v0.7.9...v0.7.10
[0.7.0]: https://github.com/philtief/wikibricks/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/philtief/wikibricks/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/philtief/wikibricks/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/philtief/wikibricks/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/philtief/wikibricks/compare/v0.3.4...v0.4.0
[0.3.4]: https://github.com/philtief/wikibricks/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/philtief/wikibricks/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/philtief/wikibricks/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/philtief/wikibricks/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/philtief/wikibricks/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/philtief/wikibricks/compare/v0.1.5...v0.2.0
[0.1.5]: https://github.com/philtief/wikibricks/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/philtief/wikibricks/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/philtief/wikibricks/compare/v0.1.0...v0.1.3
[0.1.0]: https://github.com/philtief/wikibricks/releases/tag/v0.1.0
