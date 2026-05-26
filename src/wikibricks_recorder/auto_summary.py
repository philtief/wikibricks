"""LLM-generated dense session summary, opt-in.

At flush time, ask a Databricks Foundation Model serving endpoint to
produce a structured Markdown summary (Intent / Approach / Outcome /
Artifacts) from the session transcript. The recorder passes the summary
to ``WikiClient.write_page(..., content_text_override=...)`` so Vector
Search embeds the dense summary rather than ``concat(summary, body)``.
The raw transcript still lives in ``content.body`` for ``fn_wiki_read``.

**Privacy.** This module sends a sample of the session transcript to a
Databricks serving endpoint. It is OFF by default. Enable via the
``[auto_summary]`` section in ``~/.wikibricks-recorder.toml``::

    [auto_summary]
    enabled = true
    endpoint = "databricks-claude-haiku-4-5"
    max_input_chars = 12000

Failures are silent: any error returns ``None`` and the caller falls
back to the default ``content_text = concat(summary, body)`` path.

Cost on Claude Haiku 4.5: ~$0.02 per session (~3k input + ~400 output).
"""

from __future__ import annotations

from typing import Any

from wikibricks_recorder import envelope as env_module

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

# Cap on how much of the raw first_prompt to append as the "intent tail"
# when building content_text_override. The dense summary captures the
# narrative; the tail adds keyword density (entities + verbatim phrasing)
# that HYBRID retrieval's BM25 leg rewards. Empirically (eval v2 vs v1)
# this lifts recall@1 by +15pp over pure-summary while preserving the
# +5pp recall@5 gain from the dense framing.
_INTENT_TAIL_MAX_CHARS = 2000


def build_content_text_override(state: dict[str, Any], summary: str) -> str:
    """Combine the dense LLM summary with a capped raw-intent tail.

    Returned string is what callers pass as ``content_text_override`` to
    ``WikiClient.write_page``. The shape is::

        <dense summary>

        ## Raw intent
        <first_prompt[:_INTENT_TAIL_MAX_CHARS]>

    See ``docs/research/2026-05-22-summary-first-eval-v2.md`` for the
    A/B/C numbers that motivated this composition.
    """
    if not summary:
        return ""
    fp = (state.get("first_prompt") or "").strip()
    if not fp:
        return summary
    return summary + "\n\n## Raw intent\n" + fp[:_INTENT_TAIL_MAX_CHARS]


def is_enabled(cfg: dict[str, Any]) -> bool:
    """True if auto-summary is enabled. Default: False (opt-in)."""
    return bool(cfg.get("enabled", False))


def _should_summarize(state: dict[str, Any]) -> bool:
    """Skip very short sessions — "Keep-It-All" pattern from the agent-memory
    literature: under ~2k characters, the raw transcript is already cheap
    to embed and recall is perfect without summarization."""
    text_len = len((state.get("first_prompt") or ""))
    for e in state.get("events", []):
        if e.get("kind") == "prompt":
            text_len += len(e.get("prompt") or "")
        elif e.get("kind") == "tool":
            text_len += 50
    return text_len >= DEFAULT_MIN_CHARS_FOR_SUMMARY


def _sample_transcript(state: dict[str, Any], max_chars: int) -> str:
    """Return a compact transcript sample for the LLM prompt.

    Order: first_prompt → later user prompts (deduped against first) → tool
    histogram. Tool output bodies are NOT included — those live in
    ``content.body``, not the summary input.
    """
    parts: list[str] = []
    fp = (state.get("first_prompt") or "").strip()
    if fp:
        parts.append(f"FIRST PROMPT:\n{fp}\n")
    later_prompts: list[str] = []
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
    """Strip code fences, whitespace; cap length. Return ``None`` if empty."""
    if not raw:
        return None
    s = raw.strip()
    # Strip ``` or ```markdown openers
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl > 0:
            s = s[first_nl + 1:]
    if s.endswith("```"):
        s = s[:-3].rstrip()
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

    Returns ``None`` when disabled, when the session is too short, when the
    endpoint errors, or when the model returns nothing useful. Callers
    should treat ``None`` as "fall back to default concat-of-summary-and-body".
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


def generate_envelope(
    state: dict[str, Any],
    cfg: dict[str, Any],
    workspace_client: Any,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Call the LLM with a structured-output prompt and return the
    parsed envelope dict.

    The envelope is::

        {
          "summary_markdown": str,
          "entities": list[{"name", "type"}],
          "tags": list[str],
          "edges": list[{"target_path", "link_type", "evidence"}]
        }

    ``candidates`` is the list of existing wiki pages the LLM may propose
    edges to (typically top-10 VS hits on the raw session text). Returns
    ``None`` when disabled, when the session is too short, or on any
    endpoint / parsing failure.

    Edges in the returned envelope are post-filtered against the
    candidates list and against the allowed link_type vocabulary.
    """
    if not is_enabled(cfg):
        return None
    if not _should_summarize(state):
        return None
    sample = _sample_transcript(
        state, max_chars=int(cfg.get("max_input_chars", DEFAULT_MAX_INPUT_CHARS))
    )
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
