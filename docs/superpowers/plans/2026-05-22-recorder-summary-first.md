# Recorder Summary-First Write Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the recorder generate a dense structured summary at session flush via one Claude Haiku 4.5 call, and write that summary as the page's `content_text` (the column Vector Search embeds). Retrieval surfaces the right past session by intent + outcome instead of by raw-transcript noise.

**Architecture:** New `src/wikibricks_recorder/auto_summary.py` module mirrors the existing `auto_title.py` / `auto_tag.py` contract (synchronous call at flush time, silent fall-back on any error, opt-in via TOML). One additive library change: a kwarg `content_text_override` on `WikiClient.write_page` so the recorder can write its dense summary into the embedded column while keeping the raw transcript in `content.body` for human reads and `fn_wiki_read`. No proactive chunking in v1 — the existing nightly `segregate` job still handles oversize pages.

**Tech Stack:**
- Databricks SDK `serving_endpoints.query` against `databricks-claude-haiku-4-5`
- Existing `segregate_logic` primitives (only referenced — not modified)
- Structured-output JSON envelope `{summary_markdown}` via Haiku 4.5 (GA on Anthropic platform Feb 2026; on Databricks FMAPI we get the same model + can prompt-shape JSON output)
- `~/.wikibricks-recorder.toml` `[auto_summary]` block for opt-in config

---

## Research summary (informing the design)

Cited from the web-research pass; see `~/code/wikibricks/dev/docs/research/2026-05-22-summary-first-research.md` for full notes (Task 0 saves it).

| Finding | Source | How it shapes the plan |
|---|---|---|
| MemGPT keeps **raw chunks in external storage + summary in active context** — nothing destroyed | arXiv:2310.08560 | Keep `content.body` = raw transcript; `content_text` = dense summary |
| RAPTOR recursively summarizes + embeds at every level | arXiv:2401.18059 | Validates summary-as-embedded-unit; we apply at session granularity only (v1) |
| Dense X / Proposition Retrieval — atomic propositions beat passages across 5 datasets | arXiv:2312.06648 | Structure summary as bullet propositions, not flowing prose |
| Headers + brief per-section summaries lift retrieval quality | AWS RAG best practices | Use `## Intent / ## Approach / ## Outcome / ## Artifacts` headers |
| Chunk-size empirics: 64–128 tokens for factoid recall, 512–1024 for reasoning | arXiv:2505.21700 | Target 150–300 tokens for the structured summary |
| Haiku 4.5 pricing: $1/$5 per 1M input/output (batch $0.50/$2.50) | platform.claude.com pricing | One call ≈ $0.02/session standard; $0.01 with batch — negligible |
| LangMem episodic memory = situation + thought process + outcome | LangMem conceptual guide | The 4-section schema (Intent / Approach / Outcome / Artifacts) maps directly |
| Skip summarization under ~2K tokens / 4 user turns ("Keep-It-All") | Memory Optimization Strategies survey | Short-session short-circuit at MIN_TOKENS_FOR_SUMMARY = 2000 |
| Entity-coverage validation cheaply catches hallucinations | arXiv:2207.02263 | Defer to v2 — v1 relies on strict "every claim must trace to a span" system prompt |

---

## File Structure

**New:**
- `src/wikibricks_recorder/auto_summary.py` — LLM-driven dense summary, opt-in
- `tests/test_recorder_auto_summary.py` — unit tests for `auto_summary` module
- `docs/research/2026-05-22-summary-first-research.md` — research notes (Task 0)

**Modified:**
- `src/wikibricks/client.py` — add `content_text_override` kwarg to `write_page`
- `src/wikibricks/ops.py` — `write_page_sql_statements` accepts override
- `src/wikibricks_recorder/config.py` — add `load_auto_summary_config()` (mirror of `load_auto_title_config`)
- `src/wikibricks_recorder/page_builder.py` — `session_content` accepts optional dense summary
- `src/wikibricks_recorder/hooks.py` — `_flush` calls `auto_summary.generate_summary`, passes override
- `tests/test_wiki_ops.py` — assert override path emits correct SQL
- `tests/test_client.py` — assert `WikiClient.write_page` plumbs the override
- `tests/test_recorder_config.py` — assert `[auto_summary]` block loads
- `tests/test_recorder_page_builder.py` — assert dense summary flows through `session_content`
- `tests/test_recorder_hooks.py` — assert `_flush` wires it end-to-end
- `CHANGELOG.md` — new `[0.7.8]` section
- `pyproject.toml` — version bump 0.7.7 → 0.7.8
- `plugin/.claude-plugin/plugin.json` — version bump
- `CLAUDE.md` (`AGENTS.md`) — telemetry table (`summary_ok` / `summary_fail` op_types)
- `README.md` — test count + brief note on opt-in summary feature

**Untouched:** `segregate_logic.py`, the curate/segregate notebooks, the VS index spec, every UC function. The library change is one kwarg + one SQL branch.

---

## Hard rules to honor (from `AGENTS.md`)

1. **No LLM calls inside `src/wikibricks/`** — the summary call lives in `src/wikibricks_recorder/auto_summary.py` only.
2. **No REST API calls** — use `workspace_client.serving_endpoints.query` with `ChatMessage` (same pattern as `auto_title.py`).
3. **Adding a method to WikiClient is a minor version bump** — we're adding a kwarg, not a method, but bump 0.7.7 → 0.7.8 anyway because public behavior changes.
4. **TDD: pre-commit hook enforces lint + tests** — write failing test first every time; never `--amend` / `--no-verify`.
5. **Update every notebook's `%pip install wikibricks-*.whl`** at version bump (`grep -rn "wikibricks-.*\.whl" notebooks/`).
6. **Add new op_types to the telemetry table** in `AGENTS.md` + `README.md`.

---

## Tasks

### Task 0: Discovery + research notes

**Files:**
- Create: `docs/research/2026-05-22-summary-first-research.md`
- Read: `src/wikibricks/ops.py`, `src/wikibricks/client.py`, `src/wikibricks_recorder/auto_title.py`, `src/wikibricks_recorder/config.py`

- [ ] **Step 1: Save the research summary**

Save the research findings (cited above in this plan) as a standalone reference doc so future maintainers see the evidence.

```bash
mkdir -p docs/research
```

Write `docs/research/2026-05-22-summary-first-research.md` with the table above plus full inline source URLs (copy from the plan's Research summary section).

- [ ] **Step 2: Re-confirm content_text construction**

Run:
```bash
grep -n "content_text *= *concat" src/wikibricks/ops.py src/wikibricks/client.py
```

Expected: matches in both files (`ops.py` for the canonical DDL, `client.py` for `write_page` / `bulk_write_pages` MERGE SQL builders). Note the exact line numbers — Task 2 will branch on them.

- [ ] **Step 3: Baseline test suite green**

Run:
```bash
uv run pytest -x -q
uv run ruff check src tests scripts
```

Expected: all pass. If anything fails, stop and fix before continuing.

- [ ] **Step 4: Commit research note**

```bash
git add docs/research/2026-05-22-summary-first-research.md
git commit -m "docs: research notes for recorder summary-first write path"
```

---

### Task 1: Library — add `content_text_override` to `ops.write_page_sql_statements`

**Files:**
- Modify: `src/wikibricks/ops.py`
- Test: `tests/test_wiki_ops.py`

The MERGE SQL in `ops.py` currently builds `content_text = concat(summary, body)` unconditionally. We add an optional override: when set, `content_text` is a literal string (the dense summary). When absent, behavior is unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_wiki_ops.py`:

```python
def test_write_page_sql_statements_uses_content_text_override():
    from wikibricks.ops import write_page_sql_statements

    archive_sql, merge_sql = write_page_sql_statements(
        path="sessions/u/2026/05/22/abc",
        title="Test session",
        content_json={"summary": "raw first prompt", "body": "huge raw events..."},
        tags=["session"],
        page_type="session",
        created_by="user@example.com",
        content_text_override="## Intent\n- short dense summary",
    )

    # Override literal must appear inside both INSERT and UPDATE branches
    assert "## Intent" in merge_sql
    # The default concat path must NOT appear when override is set
    assert "concat(\n            PARSE_JSON" not in merge_sql
    assert "concat(\n                PARSE_JSON" not in merge_sql


def test_write_page_sql_statements_default_concats_summary_and_body():
    from wikibricks.ops import write_page_sql_statements

    _, merge_sql = write_page_sql_statements(
        path="x", title="y",
        content_json={"summary": "s", "body": "b"},
        tags=[], page_type="entity", created_by="u",
    )
    # Default behavior unchanged: concat appears
    assert "concat(" in merge_sql
    assert ":summary::STRING" in merge_sql
    assert ":body::STRING" in merge_sql
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_wiki_ops.py::test_write_page_sql_statements_uses_content_text_override -v
```

Expected: FAIL — `TypeError: write_page_sql_statements() got an unexpected keyword argument 'content_text_override'`.

- [ ] **Step 3: Implement the override branch**

In `src/wikibricks/ops.py`, change the function signature and SQL builder. Current signature (around line 165):

```python
def write_page_sql_statements(
    path, title, content_json, tags, page_type, created_by
):
```

New signature:

```python
def write_page_sql_statements(
    path,
    title,
    content_json,
    tags,
    page_type,
    created_by,
    *,
    content_text_override: str | None = None,
):
```

Inside the function, replace both `concat(...)` expressions with a single computed `content_text_expr`:

```python
    if content_text_override is None:
        content_text_expr = (
            f"concat("
            f"PARSE_JSON('{content_escaped}'):summary::STRING, ' ', "
            f"PARSE_JSON('{content_escaped}'):body::STRING)"
        )
    else:
        override_escaped = content_text_override.replace("'", "\\'")
        content_text_expr = f"'{override_escaped}'"
```

Then use `content_text_expr` in both the `WHEN MATCHED THEN UPDATE SET content_text = ...` clause and the `WHEN NOT MATCHED THEN INSERT VALUES (..., content_text_expr, ...)` clause.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_wiki_ops.py -v
```

Expected: both new tests PASS, existing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wikibricks/ops.py tests/test_wiki_ops.py
git commit -m "feat(ops): write_page_sql_statements accepts content_text_override

Override lets callers (recorder) write a dense summary into the
VS-embedded column without changing the human-readable body. Default
behavior unchanged."
```

---

### Task 2: Library — propagate `content_text_override` through `WikiClient.write_page`

**Files:**
- Modify: `src/wikibricks/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_client.py` (find the existing `class TestWritePage` or similar block):

```python
def test_write_page_passes_content_text_override_to_sql_builder(monkeypatch):
    from wikibricks.client import WikiClient
    from wikibricks import ops

    captured = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return ["SELECT 1", "SELECT 2"]

    monkeypatch.setattr(ops, "write_page_sql_statements", fake_builder)

    client = WikiClient(warehouse_id="w", workspace_client=MagicMock())
    # _execute_sql also needs a stub — use the existing fixture pattern from
    # the file. The minimal stub:
    client._execute_sql = MagicMock(return_value=None)  # type: ignore[method-assign]
    client._log = MagicMock(return_value=None)  # type: ignore[method-assign]

    client.write_page(
        path="x", title="y",
        content_json={"summary": "s", "body": "b"},
        tags=["t"],
        content_text_override="## Intent\n- dense",
    )

    assert captured["content_text_override"] == "## Intent\n- dense"


def test_write_page_default_omits_content_text_override(monkeypatch):
    from wikibricks.client import WikiClient
    from wikibricks import ops

    captured = {}
    monkeypatch.setattr(ops, "write_page_sql_statements",
                        lambda **kw: captured.update(kw) or ["", ""])
    client = WikiClient(warehouse_id="w", workspace_client=MagicMock())
    client._execute_sql = MagicMock(return_value=None)  # type: ignore[method-assign]
    client._log = MagicMock(return_value=None)  # type: ignore[method-assign]

    client.write_page(path="x", title="y",
                      content_json={"summary": "s", "body": "b"}, tags=[])

    assert captured.get("content_text_override") is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_client.py::test_write_page_passes_content_text_override_to_sql_builder -v
```

Expected: FAIL — either the kwarg doesn't exist on `write_page`, or the builder doesn't receive it.

- [ ] **Step 3: Implement**

In `src/wikibricks/client.py`, find the `write_page` definition (around line 83) and change its signature + builder call:

```python
def write_page(
    self,
    path: str,
    title: str,
    *,
    content_json: dict | None = None,
    tags: list[str] | None = None,
    page_type: str = "entity",
    content_text_override: str | None = None,
) -> None:
```

Inside the method, find the call to `ops.write_page_sql_statements(...)` and pass the override through:

```python
    archive_sql, merge_sql = ops.write_page_sql_statements(
        path=path,
        title=title,
        content_json=content_json or {},
        tags=tags or [],
        page_type=page_type,
        created_by=self._created_by,
        content_text_override=content_text_override,
    )
```

Leave the inline MERGE SQL (the duplicated one starting around line 142 in client.py — verified by Task 0 Step 2) **unchanged for now**. That's a separate `write_pages` / `bulk_write_pages` code path; the override only ships on the single-page write_page in v1. Add a TODO comment pointing to a follow-up.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_client.py -v -k "write_page"
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wikibricks/client.py tests/test_client.py
git commit -m "feat(client): write_page accepts content_text_override kwarg

Plumbs through to ops.write_page_sql_statements. bulk_write_pages
path unchanged — separate follow-up."
```

---

### Task 3: Recorder — add `auto_summary.py` (no LLM call yet, pure helpers)

**Files:**
- Create: `src/wikibricks_recorder/auto_summary.py`
- Create: `tests/test_recorder_auto_summary.py`

We split this from the full LLM-call task so we can TDD the prompt assembly, sampling, and output cleaning in isolation.

- [ ] **Step 1: Write the failing tests for pure helpers**

Create `tests/test_recorder_auto_summary.py`:

```python
"""Unit tests for the auto_summary module — pure helpers only.

The LLM call is tested in a separate test that monkeypatches
workspace_client.serving_endpoints.query.
"""
from __future__ import annotations

import pytest

from wikibricks_recorder import auto_summary


def test_is_enabled_default_false():
    assert auto_summary.is_enabled({}) is False
    assert auto_summary.is_enabled({"enabled": False}) is False
    assert auto_summary.is_enabled({"enabled": True}) is True


def test_sample_transcript_includes_first_prompt_and_recent_events():
    state = {
        "first_prompt": "build a thing",
        "events": [
            {"kind": "prompt", "prompt": "now refine it", "ts": "2026-05-22T10:00:00Z"},
            {"kind": "tool", "tool_name": "Read", "ts": "2026-05-22T10:01:00Z"},
            {"kind": "tool", "tool_name": "Edit", "ts": "2026-05-22T10:02:00Z"},
        ],
    }
    sample = auto_summary._sample_transcript(state, max_chars=2000)
    assert "build a thing" in sample
    assert "now refine it" in sample
    assert "Read" in sample
    assert "Edit" in sample


def test_sample_transcript_truncates_to_max_chars():
    state = {"first_prompt": "x" * 5000, "events": []}
    sample = auto_summary._sample_transcript(state, max_chars=100)
    assert len(sample) <= 100


def test_sample_transcript_returns_empty_for_empty_state():
    assert auto_summary._sample_transcript({"events": []}, max_chars=100) == ""


def test_clean_summary_strips_code_fences():
    raw = "```markdown\n## Intent\n- build\n```"
    assert auto_summary._clean_summary(raw) == "## Intent\n- build"


def test_clean_summary_strips_leading_whitespace_and_returns_none_for_empty():
    assert auto_summary._clean_summary("   \n  \n") is None
    assert auto_summary._clean_summary("") is None
    assert auto_summary._clean_summary(None) is None


def test_clean_summary_caps_length():
    raw = "## Intent\n" + ("x" * 10_000)
    cleaned = auto_summary._clean_summary(raw)
    assert cleaned is not None
    assert len(cleaned) <= auto_summary._SUMMARY_MAX_CHARS


def test_should_summarize_short_session_returns_false():
    state = {"first_prompt": "hi", "events": [{"kind": "prompt", "prompt": "hi"}]}
    assert auto_summary._should_summarize(state) is False


def test_should_summarize_long_session_returns_true():
    long_prompt = "x" * 9000  # ~2250 tokens at 4 chars/token
    state = {
        "first_prompt": long_prompt,
        "events": [
            {"kind": "prompt", "prompt": long_prompt},
            {"kind": "tool", "tool_name": "Read"},
            {"kind": "tool", "tool_name": "Edit"},
            {"kind": "prompt", "prompt": "follow up"},
        ],
    }
    assert auto_summary._should_summarize(state) is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_recorder_auto_summary.py -v
```

Expected: FAIL — module does not exist.

- [ ] **Step 3: Create the module with pure helpers**

Create `src/wikibricks_recorder/auto_summary.py`:

```python
"""LLM-generated dense session summary, opt-in.

At flush time, ask a Databricks Foundation Model serving endpoint to
produce a structured Markdown summary (Intent / Approach / Outcome /
Artifacts) from the session's first prompt + tool histogram + final
assistant turn. The summary becomes the VS-embedded ``content_text``
via ``WikiClient.write_page(..., content_text_override=...)``; the raw
transcript stays in ``content.body`` for ``fn_wiki_read`` reads.

**Privacy.** This module sends a sample of the session transcript to a
Databricks serving endpoint. It is OFF by default. Enable via the
``[auto_summary]`` section in ``~/.wikibricks-recorder.toml``::

    [auto_summary]
    enabled = true
    endpoint = "databricks-claude-haiku-4-5"
    max_input_chars = 12000
    min_chars_for_summary = 2000

Failures are silent: any error returns ``None`` and the caller falls
back to the deterministic ``content.summary = first_prompt[:200]``
behavior (no override, VS embeds the default concat).
"""

from __future__ import annotations

from typing import Any

DEFAULT_ENDPOINT = "databricks-claude-haiku-4-5"
DEFAULT_MAX_INPUT_CHARS = 12_000
DEFAULT_MIN_CHARS_FOR_SUMMARY = 2_000
_MAX_OUTPUT_TOKENS = 400
_SUMMARY_MAX_CHARS = 2_000

_SYSTEM_PROMPT = (
    "You compress a Claude Code work session into a dense retrieval-friendly "
    "summary. Output strict Markdown with exactly four sections:\n"
    "## Intent\n- 1-3 bullet propositions stating what the user asked for "
    "and why (cite verbatim quotes when possible).\n"
    "## Approach\n- 1-3 bullets naming the files, tools, or strategies used.\n"
    "## Outcome\n- 1-3 bullets stating what changed and whether it worked. "
    "If it didn't finish, say so.\n"
    "## Artifacts\n- bullet list of created/modified files, URLs, IDs.\n\n"
    "Every claim must trace to a verbatim span in the transcript. If unsure, "
    "omit. No preamble, no closing. Output Markdown only."
)


def is_enabled(cfg: dict[str, Any]) -> bool:
    """True if auto-summary is enabled. Default: False (opt-in)."""
    return bool(cfg.get("enabled", False))


def _should_summarize(state: dict[str, Any]) -> bool:
    """Skip very short sessions — "Keep-It-All" pattern from memory survey."""
    text_len = len((state.get("first_prompt") or ""))
    for e in state.get("events", []):
        if e.get("kind") == "prompt":
            text_len += len(e.get("prompt") or "")
        elif e.get("kind") == "tool":
            text_len += 50  # rough cost of a tool-call line in the body
    return text_len >= DEFAULT_MIN_CHARS_FOR_SUMMARY


def _sample_transcript(state: dict[str, Any], max_chars: int) -> str:
    """Return a compact transcript sample for the LLM prompt.

    Order: first_prompt → user prompts in order → tool histogram. Tool
    output bodies are NOT included (they live in body, not summary).
    """
    parts: list[str] = []
    fp = (state.get("first_prompt") or "").strip()
    if fp:
        parts.append(f"FIRST PROMPT:\n{fp}\n")
    later_prompts = []
    tool_counts: dict[str, int] = {}
    for e in state.get("events", []):
        kind = e.get("kind")
        if kind == "prompt":
            txt = (e.get("prompt") or "").strip()
            if txt and txt != fp:
                later_prompts.append(txt)
        elif kind == "tool":
            name = e.get("tool_name") or "?"
            tool_counts[name] = tool_counts.get(name, 0) + 1
    if later_prompts:
        parts.append("LATER PROMPTS:\n" + "\n---\n".join(later_prompts))
    if tool_counts:
        hist = ", ".join(f"{k}={v}" for k, v in sorted(tool_counts.items()))
        parts.append(f"TOOL HISTOGRAM: {hist}")
    joined = "\n\n".join(parts)
    if len(joined) > max_chars:
        joined = joined[:max_chars]
    return joined


def _clean_summary(raw: str | None) -> str | None:
    """Strip code fences, whitespace; cap length. Return None if empty."""
    if not raw:
        return None
    s = raw.strip()
    # Strip ```markdown / ``` code fences
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl > 0:
            s = s[first_nl + 1:]
    if s.endswith("```"):
        s = s[: -3].rstrip()
    s = s.strip()
    if not s:
        return None
    return s[:_SUMMARY_MAX_CHARS]


def generate_summary(
    state: dict[str, Any],
    cfg: dict[str, Any],
    workspace_client: Any,
) -> str | None:
    """Call the configured serving endpoint and return a cleaned summary.

    Returns None when disabled, when the session is too short, when the
    endpoint errors, or when the model returns nothing useful. Callers
    treat None as "fall back to default concat-of-summary-and-body".
    """
    if not is_enabled(cfg):
        return None
    if not _should_summarize(state):
        return None
    max_chars = int(cfg.get("max_input_chars", DEFAULT_MAX_INPUT_CHARS))
    sample = _sample_transcript(state, max_chars=max_chars)
    if not sample:
        return None
    endpoint = cfg.get("endpoint", DEFAULT_ENDPOINT)
    try:
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

        response = workspace_client.serving_endpoints.query(
            name=endpoint,
            messages=[
                ChatMessage(role=ChatMessageRole.SYSTEM, content=_SYSTEM_PROMPT),
                ChatMessage(role=ChatMessageRole.USER, content=sample),
            ],
            max_tokens=_MAX_OUTPUT_TOKENS,
        )
    except Exception:
        return None
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError):
        return None
    return _clean_summary(content)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_recorder_auto_summary.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wikibricks_recorder/auto_summary.py tests/test_recorder_auto_summary.py
git commit -m "feat(recorder): auto_summary module (pure helpers + scaffolding)

Mirrors auto_title contract: opt-in, silent failures, sync flush call.
LLM call wiring tested separately in Task 4."
```

---

### Task 4: Recorder — LLM-call test for `auto_summary.generate_summary`

**Files:**
- Modify: `tests/test_recorder_auto_summary.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recorder_auto_summary.py`:

```python
from unittest.mock import MagicMock


def _mock_ws(content: str | None):
    """Build a workspace_client mock whose serving_endpoints.query returns
    a fake response with the given content (or simulates failure)."""
    ws = MagicMock()
    if content is None:
        ws.serving_endpoints.query.side_effect = RuntimeError("endpoint down")
    else:
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content=content))]
        ws.serving_endpoints.query.return_value = resp
    return ws


def _long_state():
    """A state long enough to trip the min-chars threshold."""
    return {
        "first_prompt": "Refactor the payment module" + ("x" * 3000),
        "events": [
            {"kind": "prompt", "prompt": "also add a test"},
            {"kind": "tool", "tool_name": "Read"},
            {"kind": "tool", "tool_name": "Edit"},
        ],
    }


def test_generate_summary_returns_none_when_disabled():
    result = auto_summary.generate_summary(_long_state(), {"enabled": False}, _mock_ws("x"))
    assert result is None


def test_generate_summary_returns_none_for_short_session():
    short = {"first_prompt": "hi", "events": []}
    result = auto_summary.generate_summary(short, {"enabled": True}, _mock_ws("## Intent\n- x"))
    assert result is None


def test_generate_summary_happy_path():
    ws = _mock_ws("## Intent\n- refactor payment\n## Approach\n- edit foo.py")
    result = auto_summary.generate_summary(
        _long_state(), {"enabled": True}, ws,
    )
    assert result == "## Intent\n- refactor payment\n## Approach\n- edit foo.py"
    # Verify the SDK was called with the expected endpoint default
    call = ws.serving_endpoints.query.call_args
    assert call.kwargs["name"] == "databricks-claude-haiku-4-5"
    # Two messages: system + user
    assert len(call.kwargs["messages"]) == 2


def test_generate_summary_uses_custom_endpoint():
    ws = _mock_ws("## Intent\n- x")
    auto_summary.generate_summary(
        _long_state(),
        {"enabled": True, "endpoint": "my-haiku"},
        ws,
    )
    assert ws.serving_endpoints.query.call_args.kwargs["name"] == "my-haiku"


def test_generate_summary_swallows_endpoint_errors():
    ws = _mock_ws(None)  # raises
    result = auto_summary.generate_summary(_long_state(), {"enabled": True}, ws)
    assert result is None


def test_generate_summary_swallows_malformed_response():
    ws = MagicMock()
    ws.serving_endpoints.query.return_value = MagicMock(choices=[])
    result = auto_summary.generate_summary(_long_state(), {"enabled": True}, ws)
    assert result is None


def test_generate_summary_strips_code_fences():
    ws = _mock_ws("```markdown\n## Intent\n- x\n```")
    result = auto_summary.generate_summary(_long_state(), {"enabled": True}, ws)
    assert result == "## Intent\n- x"


def test_generate_summary_returns_none_for_blank_response():
    ws = _mock_ws("   \n\n   ")
    result = auto_summary.generate_summary(_long_state(), {"enabled": True}, ws)
    assert result is None
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/test_recorder_auto_summary.py -v
```

Expected: all PASS (no production changes needed — `generate_summary` is already implemented and these tests validate it).

- [ ] **Step 3: Commit**

```bash
git add tests/test_recorder_auto_summary.py
git commit -m "test(recorder): auto_summary LLM-call path covers enable/disable/failure modes"
```

---

### Task 5: Recorder — `config.load_auto_summary_config()`

**Files:**
- Modify: `src/wikibricks_recorder/config.py`
- Test: `tests/test_recorder_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_recorder_config.py`:

```python
def test_load_auto_summary_config_reads_section(tmp_path, monkeypatch):
    from wikibricks_recorder import config

    toml = tmp_path / ".wikibricks-recorder.toml"
    toml.write_text(
        '[recorder]\n'
        'catalog = "c"\nschema = "s"\nwarehouse_id = "w"\nprofile = "p"\nuser_id = "u"\n'
        '\n'
        '[auto_summary]\n'
        'enabled = true\n'
        'endpoint = "my-haiku"\n'
        'max_input_chars = 5000\n'
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    cfg = config.load_auto_summary_config()
    assert cfg == {
        "enabled": True,
        "endpoint": "my-haiku",
        "max_input_chars": 5000,
    }


def test_load_auto_summary_config_returns_empty_when_section_absent(tmp_path, monkeypatch):
    from wikibricks_recorder import config

    toml = tmp_path / ".wikibricks-recorder.toml"
    toml.write_text(
        '[recorder]\n'
        'catalog = "c"\nschema = "s"\nwarehouse_id = "w"\nprofile = "p"\nuser_id = "u"\n'
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    assert config.load_auto_summary_config() == {}
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_recorder_config.py -v -k auto_summary
```

Expected: FAIL — `AttributeError: module 'wikibricks_recorder.config' has no attribute 'load_auto_summary_config'`.

- [ ] **Step 3: Implement**

In `src/wikibricks_recorder/config.py`, find the existing `load_auto_title_config()` function and add a sibling immediately after it. Use the same parsing pattern:

```python
def load_auto_summary_config() -> dict[str, Any]:
    """Return the ``[auto_summary]`` TOML section or ``{}`` when absent.

    Defaults are filled in by ``auto_summary.generate_summary``, so this
    function returns the raw section verbatim.
    """
    return _load_section("auto_summary")
```

If `_load_section` does not already exist in `config.py`, refactor: extract the TOML-loading boilerplate from `load_auto_title_config` into `_load_section(name)` and have both `load_auto_title_config` and the new `load_auto_summary_config` delegate to it. Keep the public signatures identical to today's.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_recorder_config.py -v
```

Expected: all PASS, no regressions in existing config tests.

- [ ] **Step 5: Commit**

```bash
git add src/wikibricks_recorder/config.py tests/test_recorder_config.py
git commit -m "feat(recorder): config.load_auto_summary_config reads [auto_summary] block"
```

---

### Task 6: Recorder — `page_builder.session_content` accepts optional dense summary

**Files:**
- Modify: `src/wikibricks_recorder/page_builder.py`
- Test: `tests/test_recorder_page_builder.py`

When the recorder has a dense summary, we want it in `content.summary` (replacing the truncated first_prompt). The raw transcript still goes in `content.body`. The override-into-`content_text` happens at the `write_page` call site (Task 7).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_recorder_page_builder.py`:

```python
def test_session_content_uses_dense_summary_when_provided():
    from wikibricks_recorder import page_builder

    state = {
        "session_id": "abc12345-...",
        "first_prompt": "ignore me — should not appear in summary",
        "events": [{"kind": "prompt", "ts": "2026-05-22T10:00:00Z", "prompt": "x"}],
    }
    dense = "## Intent\n- refactor payment module\n## Outcome\n- done"

    content = page_builder.session_content(state, dense_summary=dense)
    assert content["summary"] == dense
    # Body still contains the raw events for human reads
    assert "## Timeline" in content["body"]
    assert "prompt @" in content["body"]


def test_session_content_falls_back_to_default_summary_when_no_dense():
    from wikibricks_recorder import page_builder

    state = {
        "session_id": "abc12345-...",
        "first_prompt": "Refactor payments",
        "events": [{"kind": "prompt", "ts": "2026-05-22T10:00:00Z", "prompt": "x"}],
    }
    content = page_builder.session_content(state)
    # Default path unchanged
    assert content["summary"].startswith("Refactor payments")
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_recorder_page_builder.py::test_session_content_uses_dense_summary_when_provided -v
```

Expected: FAIL — `TypeError: session_content() got an unexpected keyword argument 'dense_summary'`.

- [ ] **Step 3: Implement**

In `src/wikibricks_recorder/page_builder.py`, change the `session_content` signature (line 157):

```python
def session_content(
    state: dict[str, Any],
    *,
    dense_summary: str | None = None,
) -> dict[str, str]:
    """Build {'summary', 'body'} for the wiki page's VARIANT content column.

    If ``dense_summary`` is provided, it replaces the default truncated-
    first-prompt summary. Body is unchanged either way.
    """
    if dense_summary:
        summary = dense_summary
    else:
        summary = (state.get("first_prompt") or "").strip().replace("\n", " ")
        summary = summary[:SUMMARY_MAX] if summary else f"Session {state['session_id'][:8]}"
    # ... rest of the function unchanged (body construction)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_recorder_page_builder.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wikibricks_recorder/page_builder.py tests/test_recorder_page_builder.py
git commit -m "feat(page_builder): session_content accepts dense_summary kwarg"
```

---

### Task 7: Recorder — wire `auto_summary` into `hooks._flush`

**Files:**
- Modify: `src/wikibricks_recorder/hooks.py`
- Test: `tests/test_recorder_hooks.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_recorder_hooks.py` (use the existing fixtures for monkey-patching `config.load_config` + `WikiClient`):

```python
def test_flush_passes_dense_summary_to_write_page(monkeypatch, tmp_path):
    """When auto_summary is enabled and returns a summary, _flush passes it
    as content_text_override to write_page."""
    from wikibricks_recorder import hooks, config as recorder_config

    state = {
        "session_id": "abc12345",
        "first_prompt": "Refactor payment module" + ("x" * 3000),
        "events": [
            {"kind": "prompt", "prompt": "do it", "ts": "2026-05-22T10:00:00Z"},
            {"kind": "tool", "tool_name": "Read", "ts": "2026-05-22T10:01:00Z"},
        ],
        "started_at": "2026-05-22T10:00:00Z",
        "cwd": "/home/u/proj",
    }

    monkeypatch.setattr(recorder_config, "load_config",
                        lambda: {"catalog": "c", "schema": "s",
                                 "warehouse_id": "w", "profile": "p",
                                 "user_id": "u"})
    monkeypatch.setattr(recorder_config, "load_topic_keywords", lambda: {})
    monkeypatch.setattr(recorder_config, "load_auto_tag_config", lambda: {})
    monkeypatch.setattr(recorder_config, "load_auto_title_config", lambda: {})
    monkeypatch.setattr(recorder_config, "load_auto_summary_config",
                        lambda: {"enabled": True})

    # Stub out the LLM call to return a fixed summary
    from wikibricks_recorder import auto_summary
    monkeypatch.setattr(auto_summary, "generate_summary",
                        lambda state, cfg, ws: "## Intent\n- refactor")

    # Capture write_page kwargs
    captured = {}

    class FakeClient:
        ws = MagicMock()
        def write_page(self, path, **kwargs):
            captured["path"] = path
            captured.update(kwargs)
        def upsert_vocabulary_slugs(self, *a, **kw): pass
        def _normalize_slug(self, s): return s

    monkeypatch.setattr(hooks, "_build_wiki_client", lambda cfg: FakeClient())

    hooks._flush(state)

    assert captured.get("content_text_override") == "## Intent\n- refactor"
    # Sanity: the page content still carries body + dense summary
    assert captured["content_json"]["summary"] == "## Intent\n- refactor"
    assert "## Timeline" in captured["content_json"]["body"]


def test_flush_falls_back_when_summary_returns_none(monkeypatch):
    """When auto_summary returns None (disabled / failure), no override is
    passed and write_page uses the default concat path."""
    from wikibricks_recorder import hooks, config as recorder_config
    from wikibricks_recorder import auto_summary

    state = {
        "session_id": "abc12345",
        "first_prompt": "Refactor payment",
        "events": [{"kind": "prompt", "prompt": "x", "ts": "2026-05-22T10:00:00Z"}],
        "started_at": "2026-05-22T10:00:00Z",
        "cwd": "/home/u/proj",
    }

    monkeypatch.setattr(recorder_config, "load_config",
                        lambda: {"catalog": "c", "schema": "s",
                                 "warehouse_id": "w", "profile": "p",
                                 "user_id": "u"})
    monkeypatch.setattr(recorder_config, "load_topic_keywords", lambda: {})
    monkeypatch.setattr(recorder_config, "load_auto_tag_config", lambda: {})
    monkeypatch.setattr(recorder_config, "load_auto_title_config", lambda: {})
    monkeypatch.setattr(recorder_config, "load_auto_summary_config", lambda: {})
    monkeypatch.setattr(auto_summary, "generate_summary", lambda *a, **kw: None)

    captured = {}

    class FakeClient:
        ws = MagicMock()
        def write_page(self, path, **kwargs):
            captured.update(kwargs)
        def upsert_vocabulary_slugs(self, *a, **kw): pass
        def _normalize_slug(self, s): return s

    monkeypatch.setattr(hooks, "_build_wiki_client", lambda cfg: FakeClient())

    hooks._flush(state)
    assert captured.get("content_text_override") is None
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_recorder_hooks.py -v -k flush_passes_dense_summary
```

Expected: FAIL — `_flush` doesn't call `auto_summary` yet.

- [ ] **Step 3: Implement**

In `src/wikibricks_recorder/hooks.py`, find `_flush` (line 225) and modify it. After the `auto_title` block and before the `client.write_page(...)` call, insert:

```python
    # v0.7.8: dense LLM summary, opt-in via [auto_summary] config block.
    # When present, becomes both content.summary AND the VS-embedded
    # content_text via the new write_page override kwarg. Failures
    # (disabled / endpoint error / too short) return None and fall
    # back to the default concat-of-summary-and-body path.
    dense_summary = None
    summary_cfg = config.load_auto_summary_config()
    if auto_summary.is_enabled(summary_cfg):
        try:
            dense_summary = auto_summary.generate_summary(
                state, summary_cfg, client.ws
            )
        except Exception as e:
            _log_error("auto_summary.generate_summary", e)
            dense_summary = None

    client.write_page(
        path,
        title=title,
        content_json=page_builder.session_content(state, dense_summary=dense_summary),
        tags=tags,
        content_text_override=dense_summary,
    )
```

Also add `auto_summary` to the imports at the top of the file:

```python
from wikibricks_recorder import auto_summary, auto_tag, auto_title, citations, config, page_builder, session
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_recorder_hooks.py -v
```

Expected: all PASS, no regressions.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest -x -q
```

Expected: all PASS (target: same count as baseline plus the new tests added by Tasks 1–7).

- [ ] **Step 6: Commit**

```bash
git add src/wikibricks_recorder/hooks.py tests/test_recorder_hooks.py
git commit -m "feat(hooks): generate dense summary at flush and pass as content_text_override

Honors [auto_summary] TOML block. Silent fall-back to the default
concat path on disable, short session, or endpoint error."
```

---

### Task 8: Telemetry — log summary outcomes via `wiki_log`

**Files:**
- Modify: `src/wikibricks_recorder/hooks.py`
- Modify: `CLAUDE.md` (symlink-shared with `AGENTS.md`)
- Modify: `README.md`
- Test: `tests/test_recorder_hooks.py`

We want operators to see "what fraction of sessions got an LLM summary vs fell back." Add two op_types: `summary_ok` (summary written) and `summary_fail` (summary disabled or failed).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_recorder_hooks.py`:

```python
def test_flush_logs_summary_ok_when_dense_summary_present(monkeypatch):
    from wikibricks_recorder import hooks, config as recorder_config, auto_summary

    state = {
        "session_id": "abc12345",
        "first_prompt": "x" * 3000,
        "events": [{"kind": "prompt", "prompt": "x", "ts": "2026-05-22T10:00:00Z"}],
        "started_at": "2026-05-22T10:00:00Z",
        "cwd": "/home/u/proj",
    }
    monkeypatch.setattr(recorder_config, "load_config",
                        lambda: {"catalog": "c", "schema": "s", "warehouse_id": "w",
                                 "profile": "p", "user_id": "u"})
    monkeypatch.setattr(recorder_config, "load_topic_keywords", lambda: {})
    monkeypatch.setattr(recorder_config, "load_auto_tag_config", lambda: {})
    monkeypatch.setattr(recorder_config, "load_auto_title_config", lambda: {})
    monkeypatch.setattr(recorder_config, "load_auto_summary_config",
                        lambda: {"enabled": True})
    monkeypatch.setattr(auto_summary, "generate_summary",
                        lambda *a, **kw: "## Intent\n- x")

    log_calls = []

    class FakeClient:
        ws = MagicMock()
        def write_page(self, *a, **kw): pass
        def upsert_vocabulary_slugs(self, *a, **kw): pass
        def _normalize_slug(self, s): return s
        def _log(self, op, **kw): log_calls.append((op, kw))

    monkeypatch.setattr(hooks, "_build_wiki_client", lambda cfg: FakeClient())

    hooks._flush(state)
    ops = [c[0] for c in log_calls]
    assert "summary_ok" in ops


def test_flush_logs_summary_fail_when_enabled_but_returned_none(monkeypatch):
    from wikibricks_recorder import hooks, config as recorder_config, auto_summary

    state = {
        "session_id": "abc12345",
        "first_prompt": "x" * 3000,
        "events": [{"kind": "prompt", "prompt": "x", "ts": "2026-05-22T10:00:00Z"}],
        "started_at": "2026-05-22T10:00:00Z",
        "cwd": "/home/u/proj",
    }
    monkeypatch.setattr(recorder_config, "load_config",
                        lambda: {"catalog": "c", "schema": "s", "warehouse_id": "w",
                                 "profile": "p", "user_id": "u"})
    monkeypatch.setattr(recorder_config, "load_topic_keywords", lambda: {})
    monkeypatch.setattr(recorder_config, "load_auto_tag_config", lambda: {})
    monkeypatch.setattr(recorder_config, "load_auto_title_config", lambda: {})
    monkeypatch.setattr(recorder_config, "load_auto_summary_config",
                        lambda: {"enabled": True})
    monkeypatch.setattr(auto_summary, "generate_summary", lambda *a, **kw: None)

    log_calls = []

    class FakeClient:
        ws = MagicMock()
        def write_page(self, *a, **kw): pass
        def upsert_vocabulary_slugs(self, *a, **kw): pass
        def _normalize_slug(self, s): return s
        def _log(self, op, **kw): log_calls.append((op, kw))

    monkeypatch.setattr(hooks, "_build_wiki_client", lambda cfg: FakeClient())

    hooks._flush(state)
    assert "summary_fail" in [c[0] for c in log_calls]
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_recorder_hooks.py -v -k summary_ok
```

Expected: FAIL — no `summary_ok` log call yet.

- [ ] **Step 3: Implement the logging**

In `_flush`, right after the dense_summary block:

```python
    if auto_summary.is_enabled(summary_cfg):
        try:
            if dense_summary:
                client._log("summary_ok", path=path,
                            details=json.dumps({"chars": len(dense_summary)}))
            else:
                client._log("summary_fail", path=path,
                            details=json.dumps({"reason": "none_returned"}))
        except Exception as e:
            _log_error("summary log", e)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_recorder_hooks.py -v
```

Expected: all PASS.

- [ ] **Step 5: Update the telemetry table in `AGENTS.md`**

In `AGENTS.md` find the `## Telemetry — wiki_log op_types` section and add two rows under the existing table:

```markdown
| `summary_ok` | Recorder wrote a dense LLM summary into content_text_override (auto_summary enabled, model returned text) |
| `summary_fail` | Recorder had auto_summary enabled but the model returned nothing or errored — falls back to default concat |
```

- [ ] **Step 6: Mirror in `README.md`**

Find the corresponding `wiki_log` op_types table in `README.md` and add the same two rows. Search:

```bash
grep -n "^| .op_type." README.md
```

Apply the same two-row addition there.

- [ ] **Step 7: Commit**

```bash
git add src/wikibricks_recorder/hooks.py tests/test_recorder_hooks.py AGENTS.md README.md
git commit -m "feat(telemetry): log summary_ok / summary_fail op_types from recorder

Operators can grep wiki_log for failure rate; documented in AGENTS.md
and README.md telemetry tables."
```

---

### Task 9: Versioning + CHANGELOG

**Files:**
- Modify: `pyproject.toml`
- Modify: `plugin/.claude-plugin/plugin.json`
- Modify: `CHANGELOG.md`
- Modify: all `notebooks/*.py` files that reference `wikibricks-0.7.7-*.whl`
- Modify: `README.md` (test count, if it moved)
- Test: `tests/test_plugin_manifest.py` (asserts plugin version matches pyproject)

- [ ] **Step 1: Bump library version**

Edit `pyproject.toml`:

```toml
version = "0.7.8"
```

- [ ] **Step 2: Bump plugin manifest version**

Edit `plugin/.claude-plugin/plugin.json` → `"version": "0.7.8"`.

Also check `.claude-plugin/marketplace.json` if it pins a version — bump there too.

- [ ] **Step 3: Bump every notebook's wheel reference**

```bash
grep -rln "wikibricks-0\.7\.7" notebooks/
```

For each match, replace `0.7.7` → `0.7.8` (use `sed` carefully or edit by hand):

```bash
grep -rl "wikibricks-0\.7\.7" notebooks/ | xargs sed -i '' 's/wikibricks-0\.7\.7/wikibricks-0.7.8/g'
```

Verify nothing else was hit:

```bash
git diff notebooks/
```

- [ ] **Step 4: Add CHANGELOG entry**

In `CHANGELOG.md`, immediately after the `## [Unreleased]` line and before `## [0.7.7]`:

```markdown
## [0.7.8] - 2026-05-22

### Added

- **`wikibricks_recorder/auto_summary.py`** — opt-in dense LLM summary at
  session flush. One Haiku 4.5 call produces a structured Markdown summary
  (Intent / Approach / Outcome / Artifacts) that becomes the VS-embedded
  `content_text`. Raw transcript stays in `content.body` for `fn_wiki_read`.
- **`WikiClient.write_page(content_text_override=...)`** — optional kwarg
  lets callers override the VS-embedded text without changing the readable
  body. Default behavior unchanged.
- **`config.load_auto_summary_config()`** — reads the `[auto_summary]`
  section; returns `{}` when absent (default OFF).
- **`page_builder.session_content(dense_summary=...)`** — when provided,
  the dense summary replaces the truncated-first-prompt default in
  `content.summary`.
- **`summary_ok` / `summary_fail` `wiki_log` op_types** — operators can
  grep for LLM-summary success rate.
- **Tests** — 15+ new tests across `test_recorder_auto_summary.py`,
  `test_recorder_config.py`, `test_recorder_hooks.py`,
  `test_recorder_page_builder.py`, `test_wiki_ops.py`, `test_client.py`.

### To enable

```toml
# ~/.wikibricks-recorder.toml
[auto_summary]
enabled = true
endpoint = "databricks-claude-haiku-4-5"
max_input_chars = 12000
```

Cost ≈ $0.02/session (Haiku 4.5, ~3k input + ~400 output). At 5
sessions/day this is ~$36/year.

### Why

VS embeds `concat(content.summary, content.body)`. Before 0.7.8, that
meant retrieval embeddings were dominated by raw tool output and bash
logs. With auto_summary enabled, VS embeds a 150–300-token structured
summary whose every claim traces to a verbatim transcript span. Raw
events stay accessible via `fn_wiki_read` and the body field.

### Research

See `docs/research/2026-05-22-summary-first-research.md` — applies the
MemGPT external-storage pattern (raw verbatim) + LangMem episodic
schema (Intent / Approach / Outcome) + Proposition-Retrieval
indexing (bullet propositions, not prose).
```

Update the bottom-of-file compare links: change `[Unreleased]` to `...HEAD`, add `[0.7.8]: .../compare/v0.7.7...v0.7.8`.

- [ ] **Step 5: Update README test count if moved**

```bash
uv run pytest --collect-only -q | tail -3
```

If the count changed (which it will — we added 15+ tests), update `README.md`:

```bash
grep -n "tests, no workspace" README.md
```

Change the count line to match the new total.

- [ ] **Step 6: Build the wheel**

```bash
uv build
ls -la dist/wikibricks-0.7.8-*.whl
```

Expected: a new wheel `dist/wikibricks-0.7.8-py3-none-any.whl`.

- [ ] **Step 7: Run full suite + lint**

```bash
uv run pytest -x -q
uv run ruff check src tests scripts
```

Expected: all PASS.

- [ ] **Step 8: Commit the release prep**

```bash
git add pyproject.toml plugin/.claude-plugin/plugin.json .claude-plugin/marketplace.json \
        notebooks/ CHANGELOG.md README.md dist/wikibricks-0.7.8-py3-none-any.whl
git commit -m "chore(release): 0.7.8 — recorder summary-first write path

See CHANGELOG.md for the full entry."
```

---

### Task 10: Deploy + smoke test against FEVM agent-marketplace

**Files:**
- No code changes — deployment only

This is the validation step. We enable `auto_summary` in our personal recorder TOML, run a synthetic session, and assert the page was written with a dense summary and that VS retrieves it on a related query.

- [ ] **Step 1: Confirm the wheel is on the FEVM**

The notebooks `%pip install` the wheel from the workspace path. The bundle deploy uploads `dist/wikibricks-0.7.8-*.whl`:

```bash
databricks bundle validate --target dev
databricks bundle deploy --target dev
```

Expected: deploy succeeds. If a Terraform GPG-key error appears (known: see project_personal_wikibricks memory), use the SDK fallback instead:

```bash
uv run python scripts/sdk_redeploy.py
```

- [ ] **Step 2: Enable `auto_summary` locally**

Edit `~/.wikibricks-recorder.toml` and add:

```toml
[auto_summary]
enabled = true
endpoint = "databricks-claude-haiku-4-5"
max_input_chars = 12000
```

The recorder picks this up on the next session flush — no restart needed.

- [ ] **Step 3: Run a synthetic session**

Open a new Claude Code session in a non-`/tmp` directory and run a meaningful interaction (e.g., "refactor the foo module to use bar"). Make sure the session generates >2000 characters of prompt text (otherwise it short-circuits per `_should_summarize`).

End the session cleanly so the Stop hook fires.

- [ ] **Step 4: Verify the page was written with a dense summary**

```bash
databricks --profile <profile> api post /api/2.0/sql/statements \
  -- --json '{
    "warehouse_id": "<warehouse_id>",
    "statement": "SELECT path, title, content:summary::STRING AS summary, length(content_text) AS ct_len FROM <catalog>.<schema>.pages ORDER BY updated_at DESC LIMIT 3"
  }'
```

Expected: top row's `summary` starts with `## Intent`, and `ct_len` is in the 500–2000 range (dense summary length, not raw transcript length).

- [ ] **Step 5: Verify `summary_ok` was logged**

```bash
databricks --profile <profile> api post /api/2.0/sql/statements \
  -- --json '{
    "warehouse_id": "<warehouse_id>",
    "statement": "SELECT op_type, path, details, ts FROM <catalog>.<schema>.wiki_log WHERE op_type IN (\"summary_ok\", \"summary_fail\") ORDER BY ts DESC LIMIT 5"
  }'
```

Expected: most recent row is `summary_ok` with the page path from Step 4.

- [ ] **Step 6: Verify retrieval surfaces the new page**

```bash
databricks --profile <profile> api post /api/2.0/sql/statements \
  -- --json '{
    "warehouse_id": "<warehouse_id>",
    "statement": "SELECT * FROM <catalog>.<schema>.fn_wiki_search(question => \"refactor foo to use bar\", num_results => 3)"
  }'
```

Expected: the synthetic session page appears in the top-3 with a search_score that beats prior-day session pages on the same query.

- [ ] **Step 7: Negative test — disabled path**

Edit TOML to `enabled = false`. Run a second synthetic session. Verify the latest `wiki_log` row is **not** `summary_ok` / `summary_fail` (the log call is gated on `is_enabled`), and that `content:summary::STRING` starts with raw text (truncated first prompt), not `## Intent`.

- [ ] **Step 8: Commit the smoke-test record**

Save a brief log of what you observed at `docs/research/2026-05-22-summary-first-smoke-test.md` with the query outputs and dates.

```bash
git add docs/research/2026-05-22-summary-first-smoke-test.md
git commit -m "docs: 0.7.8 smoke-test record on <workspace>"
```

---

### Task 11: Quantify retrieval-quality delta

**Files:**
- Create: `scripts/eval_summary_first_recall.py`
- Create: `docs/research/2026-05-22-summary-first-eval.md`

We use a small recorder-style eval: take 20 recent personal-recorder sessions, generate 1 query per session that paraphrases the user's intent (manually or via Haiku), then measure recall@5 with `fn_wiki_search` (a) against pages written WITHOUT auto_summary (read from `pages_history` if available, otherwise sample older pages) and (b) with auto_summary enabled.

If `pages_history` doesn't go back far enough, use a controlled A/B: take 20 recent sessions, rebuild each twice (once with auto_summary, once without) into two distinct paths under `eval/with_summary/...` and `eval/without_summary/...`, then run the eval.

- [ ] **Step 1: Write the eval script**

Create `scripts/eval_summary_first_recall.py` that:

1. Connects via `WorkspaceClient(profile="<profile>")`
2. Lists sessions from the last 30 days
3. For each, generates 1 paraphrased query using Haiku 4.5 (one-shot prompt: "Restate this session's user intent in one sentence, as someone might ask later")
4. Runs `fn_wiki_search(query, 10)` and records whether the source session is in the top-K (K = 1, 3, 5, 10)
5. Outputs CSV: `session_id,query,rank_with_summary,rank_without_summary`

The script is OK to put under `scripts/` (operational, ships to public per `AGENTS.md`).

- [ ] **Step 2: Run the eval**

```bash
uv run python scripts/eval_summary_first_recall.py \
  --profile <profile> \
  --output docs/research/2026-05-22-summary-first-eval.csv \
  --n-sessions 20
```

Expected: writes a CSV of 20 rows.

- [ ] **Step 3: Compute and write up the numbers**

In `docs/research/2026-05-22-summary-first-eval.md`, capture:

- Recall@1, @3, @5, @10 for both arms
- Mean rank improvement
- Wins / ties / losses

A meaningful result is **recall@5 improvement ≥ 10 percentage points**. If it's smaller, document and note that the summary prompt might need tuning before broader rollout.

- [ ] **Step 4: Decide rollout**

Two outcomes:
- **Good (≥10pp improvement at recall@5):** Flip the default in `auto_summary.is_enabled` to `True` in v0.7.9 (a follow-up — not this plan). Cite the eval in CHANGELOG.
- **Marginal / mixed:** Leave it opt-in; iterate on the system prompt (e.g., add a few-shot example, tighten the propositions constraint).

- [ ] **Step 5: Commit the eval**

```bash
git add scripts/eval_summary_first_recall.py \
        docs/research/2026-05-22-summary-first-eval.csv \
        docs/research/2026-05-22-summary-first-eval.md
git commit -m "eval(0.7.8): quantify recorder retrieval-quality delta from summary-first

See docs/research/2026-05-22-summary-first-eval.md for numbers."
```

---

### Task 12: Sync to public + tag release

Run the **Dev → public sync checklist** from `AGENTS.md` (Sections 1–6 in the "Two repos" block). Recap of the critical steps:

- [ ] **Step 1: Cherry-pick library + recorder changes into `~/code/wikibricks/public/`**

The plan touches only files allowed in public per `AGENTS.md`:
- `src/wikibricks/ops.py`, `src/wikibricks/client.py`
- `src/wikibricks_recorder/auto_summary.py` (new)
- `src/wikibricks_recorder/config.py`, `page_builder.py`, `hooks.py`
- `tests/test_recorder_*.py`, `tests/test_wiki_ops.py`, `tests/test_client.py`
- `CHANGELOG.md`, `README.md`, `AGENTS.md`, `pyproject.toml`, `plugin/.claude-plugin/plugin.json`
- `docs/research/2026-05-22-summary-first-research.md`

Do NOT publish `docs/research/2026-05-22-summary-first-eval.*` if the eval pulled real personal-session content — review first; redact if needed.

- [ ] **Step 2: Flip install-source URLs back to public**

Per `AGENTS.md` section 2, edit:
- `plugin/bin/launch.sh` — `WIKIBRICKS_PLUGIN_REF` to `v0.7.8`
- `plugin/.claude-plugin/plugin.json` — `homepage` + `repository`
- `.claude-plugin/marketplace.json` — same
- `plugin/README.md` — install URL + uvx example
- `CHANGELOG.md` — install command in the 0.7.8 section

- [ ] **Step 3: Tag the public mirror**

```bash
cd ~/code/wikibricks/public
git tag -a v0.7.8 -m "release v0.7.8 — recorder summary-first write path"
```

Push only after user confirms — per `AGENTS.md` hard rule 5.

- [ ] **Step 4: Smoke-test the install path**

In a new Claude Code session:

```
/plugin marketplace add https://github.com/philtief/wikibricks.git
/plugin install wikibricks-recorder@wikibricks
```

Confirm a `wikibricks==0.7.8` lands under `${CLAUDE_PLUGIN_DATA}/uv-tools/` and MCP tools register as `mcp__plugin_wikibricks-recorder_wiki__*`.

- [ ] **Step 5: Push (after user OK)**

```bash
git push origin main
git push origin v0.7.8
```

---

## Self-review checklist (run before handoff)

| Check | Status |
|---|---|
| Every hard rule from AGENTS.md honored (no LLM in `src/wikibricks/`, no REST API, SDK only, no hardcoded workspace IDs) | ✓ |
| Every spec requirement has a task — research-informed contract, structured summary, override path, opt-in TOML, telemetry, eval | ✓ |
| No "TBD" / "Add appropriate handling" / "Similar to Task N" placeholders | ✓ |
| Type consistency — `content_text_override: str \| None` everywhere, `dense_summary: str \| None`, `is_enabled(cfg: dict) -> bool` | ✓ |
| Every code step has a complete code block | ✓ |
| TDD — every implementation step preceded by a failing test step | ✓ |
| Commits are small (1 task = 1 commit) and end with green tests | ✓ |
| Version bump + notebook wheel refs + plugin manifest + CHANGELOG all in Task 9 | ✓ |
| Telemetry table updated in both `AGENTS.md` and `README.md` (Task 8) | ✓ |
| Dev → public sync checklist explicitly run as Task 12 | ✓ |
| Validation is end-to-end against a real workspace (Task 10) + quantified (Task 11) | ✓ |

---

## Risk register

| Risk | Mitigation |
|---|---|
| Haiku 4.5 hallucinates facts not in transcript | Strict system prompt ("every claim must trace to a verbatim span"); v2 will add entity-coverage check (arXiv:2207.02263) |
| Cost spike on a very chatty user (50 sessions/day) | At $0.02/session, 50/day = $1/day = $365/year. Still negligible. Per-user cap can be added via `max_summaries_per_day` if needed (defer until observed). |
| Endpoint outage | All failures swallowed; falls back to default summary. Logged as `summary_fail`. |
| Summary becomes the only thing embedded — what if the user later wants substring search on the body? | `mode="FULL_TEXT"` still scans `content_text`. The body is preserved on the page (in `content.body`) and reachable via `fn_wiki_read`. Substring search on body specifically would need a new code path — defer until requested. |
| Bulk-write callers (e.g. Confluence connector in a future task) want override too | Documented as a v0.7.9 follow-up: extend `bulk_write_pages` and `write_pages` similarly. |
