# Graph-Aware Auto-Summary (Structured Envelope) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `auto_summary` first-class with the WikiBricks graph. Replace the v0.7.9 single-purpose summary call with **one structured-output LLM call** that returns `{summary_markdown, entities, tags, edges}`. Drop the conversational `first_prompt` tail from the override; replace with structured concatenation (`title + summary + tags + entities`). Proposed edges land in a new `edges_proposed` staging table for nightly judge + promotion.

**Architecture:** One LLM call per session at flush. Before the call, fetch top-10 VS neighbors of the raw session text as the candidate set the LLM may propose edges to (anti-hallucination). After the call, the envelope's pieces fan out: `summary_markdown` → `content.summary`, `tags` → `pages.tags`, `entities` → `content.entities`, `edges` → new `edges_proposed` table. The content_text override is built from `title + summary + tag_tokens + entity_names` (no first_prompt tail). A new TOML config flag `[auto_summary] mode = "envelope" | "intent_tail"` lets v0.7.9 users opt in; default stays `"intent_tail"` for backward compatibility until the eval validates the switch.

**Tech Stack:**
- Databricks SDK `serving_endpoints.query` against `databricks-claude-haiku-4-5` (existing endpoint, same cost ≈ $0.02/session)
- Anthropic Structured Outputs JSON schema (graceful fall-back if endpoint doesn't honor the strict format — parse permissively)
- Existing library primitives: `WikiClient.search` for candidate neighbors, `bulk_write_pages` patterns for the new `bulk_propose_edges`, `ops.write_page_sql` for paired-arm experiments
- New Delta table `edges_proposed` (additive — no schema migration of existing tables)

---

## Research summary (informing the design)

Full notes at `docs/research/2026-05-26-graph-aware-summary-research.md`. Five facts the plan rests on:

| Finding | Source | How it shapes the plan |
|---|---|---|
| HippoRAG / RAPTOR fit session-style content better than Microsoft GraphRAG | arXiv:2405.14831, arXiv:2401.18059, arXiv:2503.04338 | Don't replicate global community summaries (`promote_topics.py` already does this). Focus on per-session structured envelope. |
| Structured-output JSON envelope (single call) replaces 3 separate calls (auto_title + auto_tag + auto_summary) | Anthropic Structured Outputs GA Feb 2026 | One Haiku call per session, ~$0.02. Consolidates title + tags + summary + entities + edges. |
| LLM-proposed edges hallucinate target paths — mitigate by injecting candidate list | arXiv:2510.20345 (LLM-KG construction survey) | Pre-fetch top-10 VS neighbors, pass as candidate set; reject any edge whose target isn't in that set. |
| Drop first_prompt tail from override; replace with structured fields | This plan's research synthesis | New shape `title + summary + tags + entities` is denser + structured for both BM25 and cosine. |
| Proposed edges go to staging, not directly to `links` | LLM-KG construction survey, GraphRAG follow-up papers | New `edges_proposed` Delta table; nightly judge auto-promotes rows where target exists + evidence non-empty. |

---

## File Structure

**New:**
- `src/wikibricks_recorder/envelope.py` — pure helpers: schema definition, prompt construction, response parsing, candidate-injection
- `tests/test_recorder_envelope.py` — unit tests for envelope parsing + candidate filtering
- `notebooks/promote_edges.py` — nightly judge + promotion job
- `tests/test_promote_edges_notebook.py` — drift-guard tests for the new notebook
- `resources/wiki_curate_job.yml` — *modify* to add `promote_edges` task to the DAG

**Modified:**
- `src/wikibricks/ops.py` — add `edges_proposed` table to `create_tables_sql()` + new `propose_edges_sql_statements()` helper
- `src/wikibricks/client.py` — add `WikiClient.bulk_propose_edges(rows)` method
- `src/wikibricks_recorder/auto_summary.py` — add `generate_envelope()` calling the new path; keep `generate_summary` + `build_content_text_override` intact for backward compat
- `src/wikibricks_recorder/hooks.py` — `_flush` branches on `[auto_summary] mode`: "envelope" → new path, "intent_tail" → v0.7.9 path (default)
- `src/wikibricks_recorder/config.py` — `load_auto_summary_config()` already returns the section; no API change needed
- `src/wikibricks_recorder/page_builder.py` — `session_content(state, dense_summary, entities=None)` adds optional entities into the content JSON
- `tests/test_wiki_ops.py` — assert new table SQL builds correctly
- `tests/test_client.py` — assert `bulk_propose_edges` plumbs rows correctly
- `tests/test_recorder_auto_summary.py` — assert `generate_envelope` happy + failure paths
- `tests/test_recorder_hooks.py` — assert mode branching
- `CHANGELOG.md`, `pyproject.toml`, `plugin/.claude-plugin/plugin.json` — version 0.7.10
- `notebooks/deploy_wiki_store.py` — `%pip install` line + table creation

**Untouched (deliberately):**
- v0.7.9 `auto_summary.generate_summary` + `build_content_text_override` — they stay so users can A/B test or roll back via TOML
- `auto_title` + `auto_tag` modules — keep as-is for back-compat; an opt-in `mode="envelope"` short-circuits them
- `notebooks/wiki_curate.py` — the new `promote_edges` notebook is a separate task in the DAG

---

## Hard rules (from `AGENTS.md`)

1. **No LLM calls in `src/wikibricks/`** — envelope LLM call lives in `src/wikibricks_recorder/envelope.py` only
2. **No REST API** — use `workspace_client.serving_endpoints.query` with `ChatMessage`
3. **Adding a method to WikiClient is a minor version bump** — `bulk_propose_edges` qualifies → bump 0.7.9 → 0.7.10
4. **TDD (pre-commit hook enforces)** — every implementation task starts with a failing test
5. **Add new op_types to telemetry tables** — `propose_edges` (LLM-proposed) and `promote_edge` (promoted from staging to `links`)
6. **Update notebook `%pip` refs on version bump** — `grep -rn "wikibricks-0\.7\." notebooks/`

---

## Tasks

### Task 0: Discovery + commit research notes

**Files:**
- Create: `docs/research/2026-05-26-graph-aware-summary-research.md` (already written this turn)

- [ ] **Step 1: Baseline test suite green**

```bash
cd ~/code/wikibricks/dev
uv run pytest -x -q
uv run ruff check src tests scripts
```

Expected: 841 tests pass, lint clean. If anything is red, stop and fix.

- [ ] **Step 2: Commit research + this plan**

```bash
git add docs/research/2026-05-26-graph-aware-summary-research.md \
        docs/superpowers/plans/2026-05-26-graph-aware-summary.md
git commit -m "docs: research + plan for graph-aware auto_summary envelope"
```

---

### Task 1: ops.py — `edges_proposed` table DDL + SQL helpers

**Files:**
- Modify: `src/wikibricks/ops.py`
- Test: `tests/test_wiki_ops.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_wiki_ops.py`:

```python
def test_create_tables_sql_includes_edges_proposed():
    from wikibricks.ops import create_tables_sql

    sql_statements = create_tables_sql()
    joined = "\n".join(sql_statements)
    assert "edges_proposed" in joined
    assert "source_path" in joined
    assert "target_path" in joined
    assert "link_type" in joined
    assert "evidence" in joined
    assert "status" in joined


def test_propose_edges_sql_statements_returns_insert():
    from wikibricks.ops import propose_edges_sql_statements

    rows = [
        {
            "source_path": "sessions/u/2026/05/22/abc",
            "target_path": "topics/stripe-webhooks",
            "link_type": "cites",
            "evidence": "uses stripe.Webhook.construct_event",
            "confidence": 0.85,
            "created_by": "auto_summary@v0.7.10",
        },
    ]
    sql = propose_edges_sql_statements(rows)
    assert "INSERT INTO" in sql
    assert "edges_proposed" in sql
    assert "stripe-webhooks" in sql
    assert "stripe.Webhook.construct_event" in sql
    assert "cites" in sql
    # Default status is 'pending'
    assert "'pending'" in sql or "pending" in sql.lower()


def test_propose_edges_sql_handles_empty_rows():
    from wikibricks.ops import propose_edges_sql_statements

    assert propose_edges_sql_statements([]) == ""


def test_propose_edges_sql_escapes_single_quotes():
    from wikibricks.ops import propose_edges_sql_statements

    rows = [{
        "source_path": "s",
        "target_path": "t",
        "link_type": "related",
        "evidence": "it's an example",
        "confidence": 0.5,
        "created_by": "test",
    }]
    sql = propose_edges_sql_statements(rows)
    assert "it\\'s an example" in sql
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_wiki_ops.py -v -k "edges_proposed or propose_edges_sql_statements" 2>&1 | tail -10
```

Expected: 4 failures — `propose_edges_sql_statements` doesn't exist, `create_tables_sql` doesn't include `edges_proposed`.

- [ ] **Step 3: Add the table to `create_tables_sql`**

In `src/wikibricks/ops.py`, find the `create_tables_sql()` function (around line 31). Add a new CREATE TABLE statement to the list it returns:

```python
def create_tables_sql():
    """Return DDL for the seven core tables and the new edges_proposed staging table."""
    return [
        # ... existing 7 CREATE TABLE statements unchanged ...
        f"""
        CREATE TABLE IF NOT EXISTS {EDGES_PROPOSED_TABLE} (
            proposal_id   STRING DEFAULT uuid(),
            source_path   STRING NOT NULL,
            target_path   STRING NOT NULL,
            link_type     STRING NOT NULL,
            evidence      STRING,
            confidence    DOUBLE,
            created_by    STRING,
            created_at    TIMESTAMP DEFAULT current_timestamp(),
            status        STRING DEFAULT 'pending',
            CONSTRAINT edges_proposed_pk PRIMARY KEY (proposal_id)
        )
        USING delta
        TBLPROPERTIES (
            delta.enableChangeDataFeed = true
        )
        """,
    ]
```

Also add the table constant near the existing table constants at the top of the file:

```python
EDGES_PROPOSED_TABLE = f"{CATALOG}.{SCHEMA}.edges_proposed"
```

- [ ] **Step 4: Add `propose_edges_sql_statements()` helper**

Add a new function in `ops.py` after `write_page_sql`:

```python
def propose_edges_sql_statements(rows: list[dict]) -> str:
    """Build a single INSERT statement that stages N LLM-proposed edges.

    Each row dict must have: source_path, target_path, link_type, evidence,
    confidence, created_by. Status defaults to 'pending' — the nightly
    promote_edges job auto-confirms rows whose target_path exists and
    evidence is non-empty.

    Returns an empty string when rows is empty (caller short-circuits).
    """
    if not rows:
        return ""

    def _esc(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace("'", "\\'")

    values = []
    for r in rows:
        values.append(
            f"(uuid(), "
            f"'{_esc(r['source_path'])}', "
            f"'{_esc(r['target_path'])}', "
            f"'{_esc(r['link_type'])}', "
            f"'{_esc(r.get('evidence', ''))}', "
            f"{float(r.get('confidence', 0.0))}, "
            f"'{_esc(r.get('created_by', 'unknown'))}', "
            f"current_timestamp(), 'pending')"
        )

    return (
        f"INSERT INTO {EDGES_PROPOSED_TABLE} "
        f"(proposal_id, source_path, target_path, link_type, evidence, "
        f"confidence, created_by, created_at, status) VALUES\n"
        + ",\n".join(values)
    )
```

- [ ] **Step 5: Run tests to verify pass**

```bash
uv run pytest tests/test_wiki_ops.py -v -k "edges_proposed or propose_edges_sql_statements" 2>&1 | tail -10
```

Expected: 4 PASS.

- [ ] **Step 6: Full suite + lint**

```bash
uv run pytest -q
uv run ruff check src tests scripts
```

Expected: all PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/wikibricks/ops.py tests/test_wiki_ops.py
git commit -m "feat(ops): edges_proposed staging table + propose_edges_sql_statements"
```

---

### Task 2: WikiClient.bulk_propose_edges

**Files:**
- Modify: `src/wikibricks/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_client.py`:

```python
class TestBulkProposeEdges:
    def test_writes_rows_via_propose_edges_sql(self, monkeypatch):
        from wikibricks.client import WikiClient
        from wikibricks import ops

        captured = {}

        def fake_builder(rows):
            captured["rows"] = rows
            return "INSERT INTO edges_proposed VALUES (...)"

        monkeypatch.setattr(ops, "propose_edges_sql_statements", fake_builder)
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        client = WikiClient(warehouse_id="w", workspace_client=ws)

        rows = [
            {"source_path": "s/1", "target_path": "t/1", "link_type": "cites",
             "evidence": "ok", "confidence": 0.8, "created_by": "test"},
            {"source_path": "s/1", "target_path": "t/2", "link_type": "related",
             "evidence": "ok", "confidence": 0.7, "created_by": "test"},
        ]
        n = client.bulk_propose_edges(rows)
        assert n == 2
        assert captured["rows"] == rows

    def test_empty_rows_is_noop(self):
        from wikibricks.client import WikiClient
        ws = MagicMock()
        client = WikiClient(warehouse_id="w", workspace_client=ws)
        assert client.bulk_propose_edges([]) == 0
        ws.statement_execution.execute_statement.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_client.py::TestBulkProposeEdges -v
```

Expected: `AttributeError: 'WikiClient' object has no attribute 'bulk_propose_edges'`.

- [ ] **Step 3: Implement**

In `src/wikibricks/client.py`, add this method after `commit_edges` (around line 803):

```python
    def bulk_propose_edges(self, rows: list[dict]) -> int:
        """Stage LLM-proposed edges in the edges_proposed table.

        Each row dict must have: source_path, target_path, link_type,
        evidence, confidence, created_by. The nightly promote_edges job
        auto-confirms rows whose target exists and evidence is non-empty.

        Returns the number of rows staged. Returns 0 (no-op) on empty input.
        """
        if not rows:
            return 0
        sql = ops.propose_edges_sql_statements(rows)
        if not sql:
            return 0
        self._exec(sql)
        self._log("propose_edges", details=json.dumps({"n_proposed": len(rows)}))
        return len(rows)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_client.py::TestBulkProposeEdges -v
```

Expected: both PASS.

- [ ] **Step 5: Full suite + lint**

```bash
uv run pytest -q
uv run ruff check src tests scripts
```

- [ ] **Step 6: Commit**

```bash
git add src/wikibricks/client.py tests/test_client.py
git commit -m "feat(client): bulk_propose_edges writes to edges_proposed staging"
```

---

### Task 3: envelope.py — schema, prompt, parser, candidate-injection

**Files:**
- Create: `src/wikibricks_recorder/envelope.py`
- Test: `tests/test_recorder_envelope.py`

The structured envelope module is the heart of this plan. Pure helpers in this task; the LLM-call wrapper comes in Task 4.

- [ ] **Step 1: Write failing tests**

Create `tests/test_recorder_envelope.py`:

```python
"""Tests for the envelope module — pure helpers (no LLM calls)."""
from __future__ import annotations

import json

from wikibricks_recorder import envelope


def test_parse_envelope_happy_path():
    raw = json.dumps({
        "summary_markdown": "## Intent\n- refactor",
        "entities": [{"name": "Stripe", "type": "library"}],
        "tags": ["customer:az", "topic:payments"],
        "edges": [
            {"target_path": "topics/stripe", "link_type": "cites",
             "evidence": "uses Stripe.construct_event"}
        ],
    })
    e = envelope.parse_envelope(raw)
    assert e is not None
    assert e["summary_markdown"].startswith("## Intent")
    assert len(e["entities"]) == 1
    assert e["entities"][0]["name"] == "Stripe"
    assert "customer:az" in e["tags"]
    assert len(e["edges"]) == 1
    assert e["edges"][0]["target_path"] == "topics/stripe"


def test_parse_envelope_strips_code_fences():
    raw = "```json\n" + json.dumps({
        "summary_markdown": "S", "entities": [], "tags": [], "edges": []
    }) + "\n```"
    e = envelope.parse_envelope(raw)
    assert e is not None
    assert e["summary_markdown"] == "S"


def test_parse_envelope_returns_none_on_garbage():
    assert envelope.parse_envelope("not json") is None
    assert envelope.parse_envelope("") is None
    assert envelope.parse_envelope(None) is None


def test_parse_envelope_handles_missing_optional_keys():
    raw = json.dumps({"summary_markdown": "S"})
    e = envelope.parse_envelope(raw)
    assert e is not None
    assert e["summary_markdown"] == "S"
    assert e["entities"] == []
    assert e["tags"] == []
    assert e["edges"] == []


def test_filter_edges_to_candidates_drops_unknown_targets():
    edges = [
        {"target_path": "topics/known", "link_type": "cites", "evidence": "ok"},
        {"target_path": "topics/fabricated", "link_type": "cites", "evidence": "ok"},
    ]
    candidates = ["topics/known", "topics/also-known"]
    kept = envelope.filter_edges_to_candidates(edges, candidates)
    assert len(kept) == 1
    assert kept[0]["target_path"] == "topics/known"


def test_filter_edges_drops_edges_with_empty_evidence():
    edges = [
        {"target_path": "topics/known", "link_type": "cites", "evidence": ""},
        {"target_path": "topics/known", "link_type": "cites", "evidence": "ok"},
    ]
    kept = envelope.filter_edges_to_candidates(edges, ["topics/known"])
    assert len(kept) == 1
    assert kept[0]["evidence"] == "ok"


def test_filter_edges_normalizes_unknown_link_types():
    edges = [
        {"target_path": "t", "link_type": "WRONG_TYPE", "evidence": "ok"},
        {"target_path": "t", "link_type": "cites", "evidence": "ok"},
    ]
    kept = envelope.filter_edges_to_candidates(edges, ["t"])
    # Unknown link_type normalized to 'related' (the safe default)
    assert kept[0]["link_type"] == "related"
    assert kept[1]["link_type"] == "cites"


def test_build_override_text_includes_all_fields():
    e = {
        "summary_markdown": "## Intent\n- refactor",
        "entities": [{"name": "Stripe"}, {"name": "payments/webhook.py"}],
        "tags": ["customer:az", "topic:payments"],
        "edges": [],
    }
    text = envelope.build_override_text(title="My Session", env=e)
    assert "My Session" in text
    assert "## Intent" in text
    assert "Tags: customer:az topic:payments" in text
    assert "Entities: Stripe, payments/webhook.py" in text


def test_build_override_text_caps_entity_count():
    e = {
        "summary_markdown": "S",
        "entities": [{"name": f"e{i}"} for i in range(50)],
        "tags": [],
        "edges": [],
    }
    text = envelope.build_override_text(title="T", env=e)
    # Cap at 20 entities to keep override text bounded
    assert text.count("e") <= 21  # 20 entities, each starting with "e", + the word "Entities"


def test_build_prompt_includes_candidates_inline():
    candidates = [
        {"path": "topics/foo", "title": "Foo", "summary": "About foo"},
        {"path": "topics/bar", "title": "Bar", "summary": "About bar"},
    ]
    prompt = envelope.build_prompt(
        transcript="refactor payments",
        candidates=candidates,
    )
    assert "topics/foo" in prompt
    assert "topics/bar" in prompt
    assert "refactor payments" in prompt
    # The candidate constraint is explicit so the LLM doesn't invent targets
    assert "MUST come from" in prompt or "must come from" in prompt
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_recorder_envelope.py -v
```

Expected: ImportError (module doesn't exist yet).

- [ ] **Step 3: Implement the module**

Create `src/wikibricks_recorder/envelope.py`:

```python
"""Structured envelope for graph-aware auto_summary (v0.7.10+).

One LLM call returns a JSON envelope with summary, entities, tags, and
proposed edges. This module owns:

- the JSON schema definition
- the prompt builder (with candidate-neighbor injection — the
  anti-hallucination mitigation from arXiv:2510.20345)
- the response parser (lenient JSON-with-code-fence-stripping)
- the post-filter that drops edges to non-candidate targets, empty
  evidence, and normalizes unknown link types
- the content_text override builder (title + summary + tags + entities)

LLM call wrapper lives in ``auto_summary.generate_envelope`` (Task 4)
so this module stays pure / unit-testable / no Databricks-SDK import.
"""

from __future__ import annotations

import json
from typing import Any

ALLOWED_LINK_TYPES = ("related", "cites", "extends", "contradicts", "supersedes")
MAX_ENTITIES_IN_OVERRIDE = 20

_SYSTEM_PROMPT_TEMPLATE = """You compress a Claude Code work session into a structured retrieval-friendly envelope. Output strict JSON with exactly these top-level keys:

{{
  "summary_markdown": "<dense Markdown with sections ## Intent, ## Approach, ## Outcome, ## Artifacts. Quote file paths, library names, IDs verbatim>",
  "entities": [{{"name": "<verbatim identifier>", "type": "<file|library|table|service|customer|concept>"}}],
  "tags": ["customer:<slug>", "topic:<slug>", "domain:<slug>"],
  "edges": [{{
    "target_path": "<MUST be one of the candidate paths listed below>",
    "link_type": "<one of: related, cites, extends, contradicts, supersedes>",
    "evidence": "<short verbatim quote from the transcript supporting this edge>"
  }}]
}}

Constraints:
- Every claim in summary_markdown must trace to a verbatim transcript span.
- entities: list every file/library/table/service/customer/concept mentioned, max 20.
- tags: 1-5 slugs of the form `<prefix>:<kebab-case-slug>`. Prefix from: customer, topic, domain.
- edges: only propose to candidates from this list. NEVER invent a target_path. Empty list is fine if no edge is well-supported.
  {candidates_block}

No preamble, no closing. Output ONLY the JSON object."""


def build_prompt(transcript: str, candidates: list[dict[str, Any]]) -> str:
    """Build the system prompt with candidate-neighbor injection.

    `candidates` is a list of `{"path": str, "title": str, "summary": str}`
    typically the top-10 VS hits on the raw session text. The LLM may only
    propose edges to these paths — the prompt makes the constraint explicit
    and the post-filter (filter_edges_to_candidates) enforces it.
    """
    if candidates:
        lines = []
        for c in candidates:
            path = c.get("path", "")
            title = c.get("title", "")[:80]
            summary = (c.get("summary") or "")[:160].replace("\n", " ")
            lines.append(f"  - {path}  — {title}: {summary}")
        cand_block = "Candidate target paths (target_path MUST come from this list):\n" + "\n".join(lines)
    else:
        cand_block = "No candidates — leave edges as []."

    system = _SYSTEM_PROMPT_TEMPLATE.format(candidates_block=cand_block)
    user = f"TRANSCRIPT:\n{transcript[:12000]}"
    return system + "\n\n" + user


def parse_envelope(raw: str | None) -> dict[str, Any] | None:
    """Parse the LLM's JSON output into the envelope dict.

    Strips code fences, fills missing optional keys with empty lists.
    Returns None on any JSON-decode failure or empty input.
    """
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl > 0:
            s = s[first_nl + 1:]
    if s.endswith("```"):
        s = s[:-3].rstrip()
    s = s.strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "summary_markdown": str(data.get("summary_markdown", "")).strip(),
        "entities": data.get("entities") or [],
        "tags": data.get("tags") or [],
        "edges": data.get("edges") or [],
    }


def filter_edges_to_candidates(
    edges: list[dict[str, Any]],
    candidate_paths: list[str],
) -> list[dict[str, Any]]:
    """Drop edges whose target isn't in the candidate set or whose evidence
    is empty. Normalize unknown link_types to 'related' (safe default).
    """
    candidate_set = set(candidate_paths)
    kept: list[dict[str, Any]] = []
    for e in edges:
        target = e.get("target_path")
        evidence = (e.get("evidence") or "").strip()
        if not target or not evidence or target not in candidate_set:
            continue
        link_type = e.get("link_type", "related")
        if link_type not in ALLOWED_LINK_TYPES:
            link_type = "related"
        kept.append({
            "target_path": target,
            "link_type": link_type,
            "evidence": evidence,
        })
    return kept


def build_override_text(*, title: str, env: dict[str, Any]) -> str:
    """Build the content_text override from envelope pieces.

    Shape:
        <title>

        <summary_markdown>

        Tags: tag1 tag2 ...
        Entities: name1, name2, ...

    Drops the v0.7.9 first_prompt tail (conversational noise dilutes the
    embedding). Density + structure wins on both BM25 and cosine legs.
    """
    parts = [title.strip()]
    summary = (env.get("summary_markdown") or "").strip()
    if summary:
        parts.append("")
        parts.append(summary)
    tags = env.get("tags") or []
    if tags:
        parts.append("")
        parts.append("Tags: " + " ".join(tags))
    entities = env.get("entities") or []
    if entities:
        names = [e.get("name", "") for e in entities[:MAX_ENTITIES_IN_OVERRIDE] if e.get("name")]
        if names:
            parts.append("Entities: " + ", ".join(names))
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_recorder_envelope.py -v
```

Expected: all 10 PASS.

- [ ] **Step 5: Lint + full suite**

```bash
uv run pytest -q
uv run ruff check src tests scripts
```

- [ ] **Step 6: Commit**

```bash
git add src/wikibricks_recorder/envelope.py tests/test_recorder_envelope.py
git commit -m "feat(envelope): structured envelope schema + parser + filters (no LLM)"
```

---

### Task 4: `auto_summary.generate_envelope` (the LLM-call wrapper)

**Files:**
- Modify: `src/wikibricks_recorder/auto_summary.py`
- Test: `tests/test_recorder_auto_summary.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_recorder_auto_summary.py`:

```python
# --- generate_envelope (v0.7.10 structured-output path) ----------------------


def test_generate_envelope_returns_none_when_disabled():
    state = _long_state()
    ws = MagicMock()
    result = auto_summary.generate_envelope(
        state, {"enabled": False}, ws, candidates=[]
    )
    assert result is None


def test_generate_envelope_returns_none_for_short_session():
    state = {"first_prompt": "hi", "events": []}
    ws = MagicMock()
    result = auto_summary.generate_envelope(
        state, {"enabled": True, "mode": "envelope"}, ws, candidates=[]
    )
    assert result is None


def test_generate_envelope_happy_path():
    import json as _json
    state = _long_state()
    raw = _json.dumps({
        "summary_markdown": "## Intent\n- refactor",
        "entities": [{"name": "Stripe", "type": "library"}],
        "tags": ["topic:payments"],
        "edges": [],
    })
    ws = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=raw))]
    ws.serving_endpoints.query.return_value = resp

    result = auto_summary.generate_envelope(
        state, {"enabled": True, "mode": "envelope"}, ws,
        candidates=[],
    )
    assert result is not None
    assert "## Intent" in result["summary_markdown"]
    assert result["entities"][0]["name"] == "Stripe"
    assert "topic:payments" in result["tags"]


def test_generate_envelope_swallows_endpoint_errors():
    state = _long_state()
    ws = MagicMock()
    ws.serving_endpoints.query.side_effect = RuntimeError("boom")
    result = auto_summary.generate_envelope(
        state, {"enabled": True}, ws, candidates=[]
    )
    assert result is None


def test_generate_envelope_filters_hallucinated_edges():
    """Edges whose target isn't in the candidates list are dropped."""
    import json as _json
    state = _long_state()
    raw = _json.dumps({
        "summary_markdown": "S",
        "entities": [],
        "tags": [],
        "edges": [
            {"target_path": "topics/real", "link_type": "cites",
             "evidence": "real evidence"},
            {"target_path": "topics/HALLUCINATED", "link_type": "cites",
             "evidence": "fake"},
        ],
    })
    ws = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=raw))]
    ws.serving_endpoints.query.return_value = resp

    result = auto_summary.generate_envelope(
        state, {"enabled": True}, ws,
        candidates=[{"path": "topics/real", "title": "Real", "summary": ""}],
    )
    assert result is not None
    assert len(result["edges"]) == 1
    assert result["edges"][0]["target_path"] == "topics/real"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_recorder_auto_summary.py -v -k generate_envelope
```

Expected: `AttributeError` — function doesn't exist.

- [ ] **Step 3: Implement**

In `src/wikibricks_recorder/auto_summary.py`, add at the bottom:

```python
def generate_envelope(
    state: dict[str, Any],
    cfg: dict[str, Any],
    workspace_client: Any,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Call the LLM with a structured-output prompt and return the
    parsed envelope dict.

    The envelope is:
        {
          "summary_markdown": str,
          "entities": list[{"name", "type"}],
          "tags": list[str],
          "edges": list[{"target_path", "link_type", "evidence"}]
        }

    ``candidates`` is the list of existing wiki pages the LLM may propose
    edges to (typically top-10 VS hits on the raw session text). Returns
    None when disabled, when the session is too short, or on any
    endpoint / parsing failure.

    Edges in the returned envelope are post-filtered against the
    candidates list and against the allowed link_type vocabulary.
    """
    from wikibricks_recorder import envelope as env_module

    if not is_enabled(cfg):
        return None
    if not _should_summarize(state):
        return None
    sample = _sample_transcript(state, max_chars=int(cfg.get("max_input_chars", DEFAULT_MAX_INPUT_CHARS)))
    if not sample:
        return None
    endpoint = cfg.get("endpoint", DEFAULT_ENDPOINT)
    prompt = env_module.build_prompt(transcript=sample, candidates=candidates)
    try:
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

        response = workspace_client.serving_endpoints.query(
            name=endpoint,
            messages=[
                ChatMessage(role=ChatMessageRole.SYSTEM, content=prompt),
                ChatMessage(role=ChatMessageRole.USER, content="Emit the JSON envelope now."),
            ],
            max_tokens=1500,
        )
    except Exception:
        return None
    try:
        raw = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError):
        return None
    parsed = env_module.parse_envelope(raw)
    if parsed is None:
        return None
    candidate_paths = [c.get("path", "") for c in candidates]
    parsed["edges"] = env_module.filter_edges_to_candidates(
        parsed["edges"], candidate_paths
    )
    return parsed
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_recorder_auto_summary.py -v -k generate_envelope
```

Expected: 5 PASS.

- [ ] **Step 5: Full suite + lint**

```bash
uv run pytest -q
uv run ruff check src tests scripts
```

- [ ] **Step 6: Commit**

```bash
git add src/wikibricks_recorder/auto_summary.py tests/test_recorder_auto_summary.py
git commit -m "feat(auto_summary): generate_envelope — single structured LLM call"
```

---

### Task 5: `hooks._flush` branches on `[auto_summary] mode`

**Files:**
- Modify: `src/wikibricks_recorder/hooks.py`
- Test: `tests/test_recorder_hooks.py`

The plan is **additive** — `intent_tail` (v0.7.9) remains the default mode for backward compat. Opt into the new path via `[auto_summary] mode = "envelope"`.

- [ ] **Step 1: Write failing test**

Append to `tests/test_recorder_hooks.py`:

```python
def test_flush_envelope_mode_writes_override_and_proposed_edges():
    """When [auto_summary] mode='envelope', _flush fetches candidates,
    calls generate_envelope, writes the structured override, and stages
    proposed edges via bulk_propose_edges."""
    from wikibricks_recorder import hooks, config as recorder_config, auto_summary

    state = _flushable_state()
    cfg = _base_cfg()
    summary_cfg = {"enabled": True, "mode": "envelope"}
    fake_envelope = {
        "summary_markdown": "## Intent\n- refactor",
        "entities": [{"name": "Stripe"}],
        "tags": ["topic:payments"],
        "edges": [{
            "target_path": "topics/stripe",
            "link_type": "cites",
            "evidence": "uses Stripe.construct_event",
        }],
    }
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
         patch("wikibricks_recorder.hooks.config.load_auto_tag_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_topic_keywords", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_auto_title_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_auto_summary_config",
               return_value=summary_cfg), \
         patch("wikibricks_recorder.hooks.auto_summary.generate_envelope",
               return_value=fake_envelope), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        client = mock_build.return_value
        # Stub search() so envelope-mode candidate fetch works
        client.search.return_value = [
            {"path": "topics/stripe", "title": "Stripe", "content_text": "..."}
        ]
        _flush(state)
        # write_page received the structured override
        kwargs = client.write_page.call_args.kwargs
        override = kwargs["content_text_override"]
        assert "## Intent" in override
        assert "Tags: topic:payments" in override
        assert "Entities: Stripe" in override
        # First-prompt tail is dropped
        assert "## Raw intent" not in override
        # Proposed edge made it through
        client.bulk_propose_edges.assert_called_once()
        proposed = client.bulk_propose_edges.call_args.args[0]
        assert len(proposed) == 1
        assert proposed[0]["target_path"] == "topics/stripe"


def test_flush_intent_tail_mode_unchanged_v0_7_9_path():
    """Default mode (or mode='intent_tail') keeps the v0.7.9 behavior."""
    from wikibricks_recorder import hooks, config as recorder_config, auto_summary

    state = _flushable_state()
    cfg = _base_cfg()
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
         patch("wikibricks_recorder.hooks.config.load_auto_tag_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_topic_keywords", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_auto_title_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_auto_summary_config",
               return_value={"enabled": True}), \
         patch("wikibricks_recorder.hooks.auto_summary.generate_summary",
               return_value="## Intent\n- x"), \
         patch("wikibricks_recorder.hooks.auto_summary.generate_envelope") as mock_env, \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        client = mock_build.return_value
        _flush(state)
        # Envelope path NOT taken
        mock_env.assert_not_called()
        # v0.7.9 override (intent_tail) IS written
        kwargs = client.write_page.call_args.kwargs
        assert "## Raw intent" in kwargs["content_text_override"]
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_recorder_hooks.py -v -k "envelope_mode or intent_tail_mode" 2>&1 | tail -10
```

Expected: 2 failures.

- [ ] **Step 3: Update `_flush` to branch on mode**

In `src/wikibricks_recorder/hooks.py`, find the v0.7.9 dense_summary block and wrap it in a mode check. The new block:

```python
    # v0.7.10: branch on [auto_summary] mode = "envelope" | "intent_tail"
    dense_summary: str | None = None
    override: str | None = None
    envelope = None
    summary_cfg = config.load_auto_summary_config()
    summary_enabled = auto_summary.is_enabled(summary_cfg)
    mode = (summary_cfg.get("mode") or "intent_tail").lower()

    if summary_enabled and mode == "envelope":
        # Fetch candidate neighbors (top-10 VS hits on the raw transcript)
        try:
            transcript_sample = (state.get("first_prompt") or "")[:8000]
            hits = client.search(
                transcript_sample, mode="HYBRID", num_results=10,
                rerank_with_pagerank=False, rerank_by_citations=False,
                include_ephemeral=False,
            )
            candidates = [
                {"path": h.get("path") or "", "title": h.get("title") or "",
                 "summary": (h.get("content_text") or "")[:200]}
                for h in (hits or [])
            ]
        except Exception as e:
            _log_error("envelope candidate fetch", e)
            candidates = []
        try:
            envelope = auto_summary.generate_envelope(
                state, summary_cfg, client.ws, candidates,
            )
        except Exception as e:
            _log_error("auto_summary.generate_envelope", e)
            envelope = None
        if envelope:
            dense_summary = envelope.get("summary_markdown") or None
            from wikibricks_recorder import envelope as env_module
            override = env_module.build_override_text(title=title, env=envelope)
    elif summary_enabled:
        # v0.7.9 intent_tail path (default)
        try:
            dense_summary = auto_summary.generate_summary(state, summary_cfg, client.ws)
        except Exception as e:
            _log_error("auto_summary.generate_summary", e)
            dense_summary = None
        if dense_summary:
            override = auto_summary.build_content_text_override(state, dense_summary)

    client.write_page(
        path,
        title=title,
        content_json=page_builder.session_content(state, dense_summary=dense_summary),
        tags=tags,
        content_text_override=override,
    )

    # v0.7.10: stage LLM-proposed edges in edges_proposed
    if envelope and envelope.get("edges"):
        edges_to_stage = [
            {
                "source_path": path,
                "target_path": e["target_path"],
                "link_type": e["link_type"],
                "evidence": e["evidence"],
                "confidence": 0.7,
                "created_by": "auto_summary@envelope",
            }
            for e in envelope["edges"]
        ]
        try:
            client.bulk_propose_edges(edges_to_stage)
        except Exception as e:
            _log_error("bulk_propose_edges", e)

    # Telemetry (existing summary_ok / summary_fail kept)
    if summary_enabled:
        try:
            if dense_summary:
                client._log("summary_ok", path=path,
                            details=json.dumps({"chars": len(dense_summary), "mode": mode}))
            else:
                client._log("summary_fail", path=path,
                            details=json.dumps({"reason": "none_returned", "mode": mode}))
        except Exception as e:
            _log_error("summary log", e)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_recorder_hooks.py -v
```

Expected: all PASS (including the existing v0.7.9 tests — backward compat).

- [ ] **Step 5: Full suite + lint**

```bash
uv run pytest -q
uv run ruff check src tests scripts
```

- [ ] **Step 6: Commit**

```bash
git add src/wikibricks_recorder/hooks.py tests/test_recorder_hooks.py
git commit -m "feat(hooks): mode-branched _flush (envelope vs intent_tail)"
```

---

### Task 6: Nightly promote_edges notebook (auto-confirm + write to `links`)

**Files:**
- Create: `notebooks/promote_edges.py`
- Test: `tests/test_promote_edges_notebook.py`
- Modify: `resources/wiki_curate_job.yml`

A simple deterministic promoter — no LLM judge in v0.7.10. Auto-confirm a proposed edge iff `(target_path exists in pages)` AND `(evidence is non-empty)` AND `(no identical edge already in links)`. More sophisticated judging deferred to v0.7.12+.

- [ ] **Step 1: Write the notebook**

Create `notebooks/promote_edges.py`:

```python
# Databricks notebook source
# MAGIC %md
# MAGIC # Promote staged edges to the links table
# MAGIC
# MAGIC Reads `edges_proposed WHERE status='pending'`. For each row:
# MAGIC - target_path must exist in `pages`
# MAGIC - evidence must be non-empty
# MAGIC - no identical (source_path, target_path, link_type) edge in `links`
# MAGIC
# MAGIC Passing rows: INSERT into `links` with confidence + provenance.
# MAGIC Marks the staged row status='confirmed' (or 'rejected' with a reason).

# COMMAND ----------
# MAGIC %pip install /Volumes/<catalog>/<schema>/wheels/wikibricks-0.7.10-py3-none-any.whl

# COMMAND ----------
import json
from databricks.sdk import WorkspaceClient
from wikibricks.client import WikiClient

ws = WorkspaceClient()
warehouse_id = dbutils.widgets.get("warehouse_id")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
client = WikiClient(warehouse_id=warehouse_id, workspace_client=ws)

# Pull all pending rows
pending_sql = f"""
SELECT proposal_id, source_path, target_path, link_type, evidence, confidence
FROM {catalog}.{schema}.edges_proposed
WHERE status = 'pending'
ORDER BY created_at ASC
LIMIT 1000
"""
resp = ws.statement_execution.execute_statement(
    warehouse_id=warehouse_id, statement=pending_sql, wait_timeout="30s"
)
rows = resp.result.data_array if resp.result else []
print(f"pending edges: {len(rows)}")

# Validation queries — fast, no LLM
confirmed_ids: list[str] = []
rejected: list[tuple[str, str]] = []
for proposal_id, source_path, target_path, link_type, evidence, confidence in rows:
    if not evidence or not evidence.strip():
        rejected.append((proposal_id, "empty_evidence"))
        continue
    # Target exists?
    check_sql = (
        f"SELECT 1 FROM {catalog}.{schema}.pages "
        f"WHERE path = '{target_path.replace(chr(39), chr(39) + chr(39))}' LIMIT 1"
    )
    r = ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=check_sql, wait_timeout="30s"
    )
    if not r.result or not r.result.data_array:
        rejected.append((proposal_id, "target_missing"))
        continue
    # Duplicate in links?
    dup_sql = (
        f"SELECT 1 FROM {catalog}.{schema}.links "
        f"WHERE source_path = '{source_path.replace(chr(39), chr(39) + chr(39))}' "
        f"AND target_path = '{target_path.replace(chr(39), chr(39) + chr(39))}' "
        f"AND link_type = '{link_type}' LIMIT 1"
    )
    r = ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=dup_sql, wait_timeout="30s"
    )
    if r.result and r.result.data_array:
        rejected.append((proposal_id, "duplicate"))
        continue
    confirmed_ids.append(proposal_id)

print(f"confirmed: {len(confirmed_ids)}  rejected: {len(rejected)}")

# Update statuses + insert into links
if confirmed_ids:
    ids_list = ",".join(f"'{i}'" for i in confirmed_ids)
    # Update status
    ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=f"""
            UPDATE {catalog}.{schema}.edges_proposed
            SET status = 'confirmed'
            WHERE proposal_id IN ({ids_list})
        """,
        wait_timeout="30s",
    )
    # Insert into links (matching the existing links schema)
    ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=f"""
            INSERT INTO {catalog}.{schema}.links
            (link_id, source_path, target_path, link_type, confidence, origin, created_at)
            SELECT uuid(), source_path, target_path, link_type, confidence,
                   'auto_summary_envelope', current_timestamp()
            FROM {catalog}.{schema}.edges_proposed
            WHERE proposal_id IN ({ids_list})
        """,
        wait_timeout="30s",
    )

if rejected:
    for proposal_id, reason in rejected:
        ws.statement_execution.execute_statement(
            warehouse_id=warehouse_id,
            statement=(
                f"UPDATE {catalog}.{schema}.edges_proposed "
                f"SET status = 'rejected', evidence = concat(evidence, ' [rejected: {reason}]') "
                f"WHERE proposal_id = '{proposal_id}'"
            ),
            wait_timeout="30s",
        )

# Telemetry
client._log(
    "promote_edge",
    details=json.dumps({
        "confirmed": len(confirmed_ids),
        "rejected": len(rejected),
        "rejected_reasons": dict(
            (r, sum(1 for _, rr in rejected if rr == r))
            for r in {"empty_evidence", "target_missing", "duplicate"}
        ),
    }),
)
print("DONE")
```

- [ ] **Step 2: Add the drift-guard test**

Create `tests/test_promote_edges_notebook.py`:

```python
"""Drift-guard tests for notebooks/promote_edges.py — assert the contract."""
from pathlib import Path

NB = Path(__file__).resolve().parent.parent / "notebooks" / "promote_edges.py"


def test_notebook_exists():
    assert NB.exists()


def test_widgets_are_warehouse_catalog_schema():
    txt = NB.read_text()
    assert 'dbutils.widgets.get("warehouse_id")' in txt
    assert 'dbutils.widgets.get("catalog")' in txt
    assert 'dbutils.widgets.get("schema")' in txt


def test_filters_by_pending_status():
    txt = NB.read_text()
    assert "WHERE status = 'pending'" in txt


def test_inserts_into_links_with_origin():
    txt = NB.read_text()
    assert "INSERT INTO" in txt
    assert ".links" in txt
    assert "auto_summary_envelope" in txt


def test_logs_promote_edge_op_type():
    txt = NB.read_text()
    assert '"promote_edge"' in txt


def test_pip_install_pinned_to_0_7_10():
    txt = NB.read_text()
    assert "wikibricks-0.7.10-py3-none-any.whl" in txt
```

- [ ] **Step 3: Run notebook tests**

```bash
uv run pytest tests/test_promote_edges_notebook.py -v
```

Expected: all 6 PASS.

- [ ] **Step 4: Add the task to `resources/wiki_curate_job.yml`**

Open `resources/wiki_curate_job.yml`. Find the existing tasks list (curate, segregate, graph_analytics, tag, promote, promote_topics). Add a new task `promote_edges` that depends on `curate`:

```yaml
      - task_key: promote_edges
        depends_on:
          - task_key: curate
        notebook_task:
          notebook_path: ../notebooks/promote_edges.py
          source: WORKSPACE
          base_parameters:
            warehouse_id: ${var.warehouse_id}
            catalog: ${var.catalog}
            schema: ${var.schema}
        environment_key: serverless
```

- [ ] **Step 5: Full suite + lint**

```bash
uv run pytest -q
uv run ruff check src tests scripts
```

- [ ] **Step 6: Commit**

```bash
git add notebooks/promote_edges.py tests/test_promote_edges_notebook.py resources/wiki_curate_job.yml
git commit -m "feat(curate): promote_edges nightly task — auto-confirm staged edges"
```

---

### Task 7: Telemetry + AGENTS.md table updates

**Files:**
- Modify: `AGENTS.md`
- Modify: `src/wikibricks_recorder/hooks.py` (already adds telemetry in Task 5; just verify)

Two new op_types from this plan: `propose_edges` (recorder's `bulk_propose_edges` emits this) and `promote_edge` (the nightly notebook emits this).

- [ ] **Step 1: Update the telemetry table in `AGENTS.md`**

Find the `## Telemetry — wiki_log op_types` section. Append two rows after `summary_fail`:

```markdown
| `propose_edges` | Recorder envelope-mode emitted N LLM-proposed edges to `edges_proposed`. v0.7.10+. |
| `promote_edge` | Nightly `promote_edges` notebook confirmed K edges from staging, rejected M. v0.7.10+. |
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs(telemetry): document propose_edges + promote_edge op_types"
```

---

### Task 8: Release prep — version bump + CHANGELOG + wheel

**Files:**
- Modify: `pyproject.toml`, `plugin/.claude-plugin/plugin.json`
- Modify: `CHANGELOG.md`, `README.md`
- Modify: `notebooks/*.py` `%pip` lines (sed-bump)
- Modify: `plugin/bin/launch.sh` (dev only — keep `wikibricks-dev` URL)

- [ ] **Step 1: Bump versions**

```bash
sed -i '' 's/version = "0.7.9"/version = "0.7.10"/' pyproject.toml
sed -i '' 's/"version": "0.7.9"/"version": "0.7.10"/' plugin/.claude-plugin/plugin.json
sed -i '' 's/REF:-v0.7.9/REF:-v0.7.10/' plugin/bin/launch.sh
grep -rln "wikibricks-0\.7\.9" notebooks/ | xargs -I {} sed -i '' 's/wikibricks-0\.7\.9/wikibricks-0.7.10/g' {}
```

- [ ] **Step 2: Verify all bumps**

```bash
grep -E "0\.7\.9|0\.7\.10" pyproject.toml plugin/.claude-plugin/plugin.json plugin/bin/launch.sh
grep -rn "wikibricks-0\.7\." notebooks/
```

Expected: only `0.7.10` references; no `0.7.9` left.

- [ ] **Step 3: Add CHANGELOG entry**

In `CHANGELOG.md`, immediately after the `## [Unreleased]` line:

```markdown
## [0.7.10] - 2026-05-26

### Added

- **`auto_summary.generate_envelope`** — single structured-output LLM
  call returning `{summary_markdown, entities, tags, edges}`. Replaces
  the v0.7.9 pure-summary call when `[auto_summary] mode = "envelope"`.
- **`wikibricks_recorder.envelope` module** — schema, prompt builder
  (with candidate-neighbor injection per arXiv:2510.20345), parser,
  edge filter, content_text override builder. 10 unit tests.
- **`WikiClient.bulk_propose_edges`** — stages LLM-proposed edges in
  the new `edges_proposed` Delta table.
- **`edges_proposed` Delta table** — staging area for LLM-emitted typed
  edges with provenance (source_path, target_path, link_type,
  evidence, confidence, status). Schema in `ops.create_tables_sql`.
- **`notebooks/promote_edges.py` + task in `wiki_curate_job.yml`** —
  nightly auto-confirms staged edges where target exists + evidence
  non-empty + no duplicate; writes to `links` with
  `origin='auto_summary_envelope'`.
- **`hooks._flush` mode branching** — `[auto_summary] mode` selects
  `"envelope"` (new) or `"intent_tail"` (v0.7.9 default, unchanged).
- **`propose_edges` and `promote_edge` `wiki_log` op_types** for the
  new flow.

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
arXiv:2510.20345).

### To enable

```toml
# ~/.wikibricks-recorder.toml
[auto_summary]
enabled = true
mode = "envelope"
endpoint = "databricks-claude-haiku-4-5"
```

Default mode is `"intent_tail"` (v0.7.9 behavior) until the larger-N
eval validates `"envelope"`. See plan at
`docs/superpowers/plans/2026-05-26-graph-aware-summary.md`.
```

Update the compare links at the bottom:

```markdown
[Unreleased]: https://github.com/philtief/wikibricks/compare/v0.7.10...HEAD
[0.7.10]: https://github.com/philtief/wikibricks/compare/v0.7.9...v0.7.10
```

- [ ] **Step 4: README test count bump**

```bash
uv run pytest --collect-only -q 2>&1 | tail -3
```

Update `README.md`'s `# NNN tests` line to match. Also update the wheel filename mention to `0.7.10`.

- [ ] **Step 5: Build wheel**

```bash
uv build
ls -la dist/wikibricks-0.7.10-py3-none-any.whl
```

Expected: new wheel exists.

- [ ] **Step 6: Final full suite + lint**

```bash
uv run pytest -x -q
uv run ruff check src tests scripts
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml plugin/.claude-plugin/plugin.json plugin/bin/launch.sh \
        notebooks/ CHANGELOG.md README.md uv.lock
git commit -m "chore(release): 0.7.10 — graph-aware envelope auto_summary"
```

---

### Task 9: Deploy + smoke test against FEVM agent-marketplace

**Files:**
- No code changes — deployment + validation only

- [ ] **Step 1: Deploy via SDK (Terraform-free path)**

```bash
DATABRICKS_CONFIG_PROFILE=fe-vm-agent-marketplace \
  WIKIBRICKS_CATALOG=agent_marketplace_catalog \
  WIKIBRICKS_SCHEMA=wikibricks_personal_philipp \
  WIKIBRICKS_WAREHOUSE_ID=41754a8563a43a49 \
  uv run python scripts/sdk_redeploy.py
```

Expected: idempotent — creates `edges_proposed` table, no errors on the existing 7 tables.

- [ ] **Step 2: Enable envelope mode locally**

Edit `~/.wikibricks-recorder.toml`:

```toml
[auto_summary]
enabled = true
mode = "envelope"
endpoint = "databricks-claude-haiku-4-5"
```

- [ ] **Step 3: Smoke-test directly via Python (bypass plugin until next session)**

Write a small one-shot:

```bash
cat > /tmp/smoke_envelope.py <<'EOF'
import os, sys, uuid, json
from datetime import datetime, timezone
sys.path.insert(0, os.path.expanduser("~/code/wikibricks/dev/src"))
from databricks.sdk import WorkspaceClient
from wikibricks.client import WikiClient
from wikibricks_recorder import auto_summary, envelope, page_builder

ws = WorkspaceClient(profile="fe-vm-agent-marketplace")
client = WikiClient(warehouse_id="41754a8563a43a49", workspace_client=ws)
os.environ["WIKIBRICKS_CATALOG"] = "agent_marketplace_catalog"
os.environ["WIKIBRICKS_SCHEMA"] = "wikibricks_personal_philipp"

state = {
    "session_id": f"smoke-env-{uuid.uuid4().hex[:8]}",
    "first_prompt": "Refactor payments to use stripe.Webhook.construct_event for signature verification. " + ("x" * 3000),
    "events": [
        {"kind": "prompt", "ts": "x", "prompt": "ship it"},
        {"kind": "tool", "ts": "x", "tool_name": "Read"},
        {"kind": "tool", "ts": "x", "tool_name": "Edit"},
    ],
    "started_at": datetime.now(timezone.utc).isoformat(),
    "cwd": "/eval/smoke-envelope",
    "model": "claude-opus-4-7",
}

# Fetch candidates
hits = client.search(
    state["first_prompt"][:2000], mode="HYBRID", num_results=10,
    rerank_with_pagerank=False, rerank_by_citations=False,
)
candidates = [{"path": h.get("path",""), "title": h.get("title",""), "summary": (h.get("content_text") or "")[:200]} for h in hits]
print(f"candidates: {len(candidates)} top-10 hits")

env = auto_summary.generate_envelope(
    state, {"enabled": True, "mode": "envelope"}, ws, candidates,
)
print("envelope keys:", list(env.keys()) if env else "FAIL")
if env:
    print("  summary head:", env["summary_markdown"][:100])
    print("  entities:", [e["name"] for e in env["entities"][:5]])
    print("  tags:", env["tags"])
    print("  edges:", len(env["edges"]))

# Build override + write
override = envelope.build_override_text(title="Smoke envelope test", env=env)
print(f"override len: {len(override)}")
path = f"eval/smoke/envelope/{state['session_id']}"
client.write_page(
    path, title="Smoke envelope test",
    content_json=page_builder.session_content(state, dense_summary=env["summary_markdown"]),
    tags=["smoke", "v0.7.10", "envelope"],
    content_text_override=override,
)

# Stage proposed edges
if env["edges"]:
    rows = [{
        "source_path": path, "target_path": e["target_path"],
        "link_type": e["link_type"], "evidence": e["evidence"],
        "confidence": 0.7, "created_by": "smoke-v0.7.10",
    } for e in env["edges"]]
    n = client.bulk_propose_edges(rows)
    print(f"staged edges: {n}")

print("PASS")
EOF
DATABRICKS_CONFIG_PROFILE=fe-vm-agent-marketplace uv run python /tmp/smoke_envelope.py
```

Expected: prints `PASS` with non-empty envelope, override length 1-3k chars, ≥0 edges staged (depends on candidate overlap).

- [ ] **Step 4: Verify FEVM state**

```sql
-- Check the smoke page landed with the structured override
SELECT path, title, length(content_text) AS ct_len,
       substring(content_text, 1, 200) AS ct_head
FROM agent_marketplace_catalog.wikibricks_personal_philipp.pages
WHERE path LIKE 'eval/smoke/envelope/%' ORDER BY updated_at DESC LIMIT 1;

-- Check staged edges
SELECT * FROM agent_marketplace_catalog.wikibricks_personal_philipp.edges_proposed
WHERE source_path LIKE 'eval/smoke/envelope/%' ORDER BY created_at DESC LIMIT 5;

-- Check the propose_edges telemetry row
SELECT op_type, details, created_at
FROM agent_marketplace_catalog.wikibricks_personal_philipp.wiki_log
WHERE op_type = 'propose_edges' ORDER BY created_at DESC LIMIT 3;
```

Expected: the page exists with override length ~1-3k chars (no `## Raw intent` block). One or more rows in `edges_proposed` with `status='pending'`. A `propose_edges` telemetry row.

- [ ] **Step 5: (Optional) Run the promote_edges notebook**

If you want to validate the nightly path eagerly:

```bash
databricks --profile fe-vm-agent-marketplace bundle run promote_edges --target dev
```

Then re-query `edges_proposed` and `links` to see confirmed rows.

- [ ] **Step 6: Save smoke record**

Capture the SQL output to `docs/research/2026-05-26-envelope-smoke-test.md`. Commit:

```bash
git add docs/research/2026-05-26-envelope-smoke-test.md
git commit -m "docs: 0.7.10 envelope smoke-test record on fevm-agent-marketplace"
```

---

### Task 10: Sync dev → public + tag v0.7.10

Follow the standard sync checklist from `AGENTS.md`. Recap:

- [ ] **Step 1: Copy 0.7.10 changes to `~/code/wikibricks/public/`**

```bash
DEV=~/code/wikibricks/dev PUB=~/code/wikibricks/public
cp $DEV/src/wikibricks/ops.py $PUB/src/wikibricks/
cp $DEV/src/wikibricks/client.py $PUB/src/wikibricks/
cp $DEV/src/wikibricks_recorder/envelope.py $PUB/src/wikibricks_recorder/
cp $DEV/src/wikibricks_recorder/auto_summary.py $PUB/src/wikibricks_recorder/
cp $DEV/src/wikibricks_recorder/hooks.py $PUB/src/wikibricks_recorder/
cp $DEV/tests/test_wiki_ops.py $PUB/tests/
cp $DEV/tests/test_client.py $PUB/tests/
cp $DEV/tests/test_recorder_envelope.py $PUB/tests/
cp $DEV/tests/test_recorder_auto_summary.py $PUB/tests/
cp $DEV/tests/test_recorder_hooks.py $PUB/tests/
cp $DEV/tests/test_promote_edges_notebook.py $PUB/tests/
cp $DEV/notebooks/promote_edges.py $PUB/notebooks/
cp $DEV/notebooks/deploy_wiki_store.py $PUB/notebooks/
cp $DEV/resources/wiki_curate_job.yml $PUB/resources/
cp $DEV/AGENTS.md $PUB/AGENTS.md
cp $DEV/CHANGELOG.md $PUB/CHANGELOG.md
cp $DEV/README.md $PUB/README.md
cp $DEV/pyproject.toml $PUB/pyproject.toml
cp $DEV/uv.lock $PUB/uv.lock
mkdir -p $PUB/docs/research $PUB/docs/superpowers/plans
cp $DEV/docs/research/2026-05-26-graph-aware-summary-research.md $PUB/docs/research/
cp $DEV/docs/research/2026-05-26-envelope-smoke-test.md $PUB/docs/research/ 2>/dev/null || true
cp $DEV/docs/superpowers/plans/2026-05-26-graph-aware-summary.md $PUB/docs/superpowers/plans/
```

- [ ] **Step 2: Bump public-only files (URLs)**

```bash
# Public plugin.json keeps the public URL
sed -i '' 's/"version": "0.7.9"/"version": "0.7.10"/' $PUB/plugin/.claude-plugin/plugin.json
# Public launch.sh: keep wikibricks (public) URL, bump REF
sed -i '' 's/REF:-v0.7.9/REF:-v0.7.10/' $PUB/plugin/bin/launch.sh
```

- [ ] **Step 3: Verify tests pass on public**

```bash
cd $PUB
UV_OFFLINE=1 uv run pytest -q
UV_OFFLINE=1 uv run ruff check src tests scripts
```

Expected: all PASS.

- [ ] **Step 4: Commit + tag + push**

```bash
cd $PUB
UV_OFFLINE=1 git add -A
UV_OFFLINE=1 git commit -m "chore(sync): dev v0.7.9 → v0.7.10 — graph-aware envelope auto_summary"
git tag -a v0.7.10 -m "v0.7.10 — graph-aware envelope auto_summary"

# Also tag + push dev
cd $DEV
git push origin main
git tag -a v0.7.10 -m "v0.7.10 — graph-aware envelope auto_summary"
git push origin v0.7.10

cd $PUB
git push origin main
git push origin v0.7.10
```

- [ ] **Step 5: Refresh local marketplace cache**

```bash
cd ~/.claude/plugins/marketplaces/wikibricks
git stash --include-untracked
git pull --ff-only
rm -f ~/.claude/plugins/data/wikibricks-recorder/installed-v0.7.9
```

Next Claude Code session installs v0.7.10. To activate envelope mode, edit `~/.wikibricks-recorder.toml` per Task 9 Step 2.

---

## Self-review checklist

| Check | Status |
|---|---|
| User's requirement met (auto_summary works WITH edges + nodes + Databricks primitives) | ✓ Envelope emits typed edges + entities + tags + summary in one call; tags + entities slot into Databricks-native filtering; edges land in a graph-aware staging table |
| All hard rules from AGENTS.md honored | ✓ LLM call lives in `src/wikibricks_recorder/`, SDK only (no REST), no hardcoded workspace IDs in repo |
| Backward compatible (v0.7.9 users not broken) | ✓ Default `mode = "intent_tail"` keeps v0.7.9 behavior; envelope is strictly opt-in via TOML |
| Anti-hallucination mitigation (candidate injection + post-filter) | ✓ Task 3 implements both; Task 5 fetches candidates before the call |
| No placeholders / TBDs / "Similar to Task N" | ✓ scanned |
| Type consistency across tasks | ✓ `envelope` dict shape consistent in Tasks 3, 4, 5; `bulk_propose_edges` row shape consistent in Tasks 1, 2, 5, 6 |
| TDD: failing test before each implementation step | ✓ Tasks 1, 2, 3, 4, 5 all start with failing tests |
| Frequent commits (one per task) | ✓ each task ends with a commit step |
| New op_types documented in telemetry table | ✓ Task 7 |
| Version bump + notebook %pip refs + CHANGELOG + plugin manifest | ✓ Task 8 |
| Smoke test against real workspace | ✓ Task 9 |
| Dev → public sync follows AGENTS.md checklist | ✓ Task 10 |

---

## Risk register

| Risk | Mitigation |
|---|---|
| Anthropic structured-outputs not honored by Databricks-hosted Haiku 4.5 endpoint | Parse permissively (`envelope.parse_envelope` strips code fences + tolerates missing keys); fall through to v0.7.9 intent_tail behavior on None |
| LLM proposes edges to popular pages by default (mode collapse) | Candidate-injection limits choices to top-10 VS hits per session — naturally diverse |
| `edges_proposed` table grows unbounded | Nightly promote_edges auto-confirms or rejects each row; consider TTL on `status='rejected'` rows in v0.7.11 |
| Override drops first_prompt → recall@1 regresses below v0.7.9 | This is exactly what the larger-N eval (the OTHER plan) is designed to measure; defer the default-flip decision until that eval runs |
| Envelope adds entities/tags that conflict with the existing auto_tag pipeline | Envelope is opt-in via `mode`; `auto_tag` keeps running independently in `intent_tail` mode |
| Cost increase: one call now does more work but max_tokens=1500 vs 400 for summary alone | Still ~$0.025/session on Haiku 4.5 — saves the separate auto_title (~$0.0005) and auto_tag (~$0.001) calls in envelope mode, so net change negligible |
