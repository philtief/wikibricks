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

_SYSTEM_PROMPT_TEMPLATE = (
    "You compress a Claude Code work session into a structured retrieval-friendly envelope."
    " Output strict JSON with exactly these top-level keys:\n"
    "\n"
    "{{\n"
    '  "summary_markdown": "<dense Markdown with sections ## Intent, ## Approach, ## Outcome,'
    ' ## Artifacts. Quote file paths, library names, IDs verbatim>",\n'
    '  "entities": [{{"name": "<verbatim identifier>",'
    ' "type": "<file|library|table|service|customer|concept>"}}],\n'
    '  "tags": ["customer:<slug>", "topic:<slug>", "domain:<slug>"],\n'
    '  "edges": [{{\n'
    '    "target_path": "<MUST be one of the candidate paths listed below>",\n'
    '    "link_type": "<one of: related, cites, extends, contradicts, supersedes>",\n'
    '    "evidence": "<short verbatim quote from the transcript supporting this edge>"\n'
    "  }}]\n"
    "}}\n"
    "\n"
    "Constraints:\n"
    "- Every claim in summary_markdown must trace to a verbatim transcript span.\n"
    "- entities: list every file/library/table/service/customer/concept mentioned, max 20.\n"
    "- tags: 1-5 slugs of the form `<prefix>:<kebab-case-slug>`."
    " Prefix from: customer, topic, domain.\n"
    "- edges: only propose to candidates from this list. NEVER invent a target_path."
    " Empty list is fine if no edge is well-supported.\n"
    "  {candidates_block}\n"
    "\n"
    "No preamble, no closing. Output ONLY the JSON object."
)


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

    def _list(key: str) -> list:
        v = data.get(key)
        return v if isinstance(v, list) else []

    return {
        "summary_markdown": str(data.get("summary_markdown", "")).strip(),
        "entities": _list("entities"),
        "tags": _list("tags"),
        "edges": _list("edges"),
    }


def filter_edges_to_candidates(
    edges: list[dict[str, Any]] | None,
    candidate_paths: list[str] | None,
) -> list[dict[str, Any]]:
    """Drop edges whose target isn't in the candidate set or whose evidence
    is empty. Normalize unknown link_types to 'related' (safe default).

    Target-path matching is case-insensitive and whitespace-tolerant so an
    LLM that returns ``"Topics/Foo"`` or ``"topics/foo "`` still matches
    a ``topics/foo`` candidate. The kept edge stores the *canonical*
    candidate string (not the LLM's variant) so downstream
    ``edges_proposed`` rows match exactly what's in ``pages.path``.
    """
    edges = edges or []
    candidate_paths = candidate_paths or []
    candidate_lookup = {p.lower().strip(): p for p in candidate_paths}
    kept: list[dict[str, Any]] = []
    for e in edges:
        target_raw = e.get("target_path")
        if not isinstance(target_raw, str):
            continue
        target_norm = target_raw.lower().strip()
        canonical = candidate_lookup.get(target_norm)
        if not canonical:
            continue
        evidence = (e.get("evidence") or "").strip()
        if not evidence:
            continue
        link_type = e.get("link_type", "related")
        if link_type not in ALLOWED_LINK_TYPES:
            link_type = "related"
        kept.append({
            "target_path": canonical,
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
