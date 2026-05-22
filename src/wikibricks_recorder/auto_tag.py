"""LLM-based topic-slug extraction for session pages.

At flush time, sample the session's prompts and ask a Databricks Foundation
Model API serving endpoint to extract 1-3 topic slugs. Slugs persist in
``wiki_vocabulary`` and accumulate counts; the active set (count >=
threshold AND ``last_seen`` within window) tags subsequent sessions via
``WikiClient.list_active_vocabulary()``.

**Privacy.** This module sends a sample of your prompt text to a Databricks
serving endpoint. It is OFF by default. Enable via the ``[auto_tag]``
section in ``~/.wikibricks-recorder.toml``::

    [auto_tag]
    enabled = true
    endpoint = "databricks-claude-haiku-4-5"
    max_input_tokens = 1000

Failures are silent: any error returns an empty slug list, the session
still flushes normally without customer tags.
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_ENDPOINT = "databricks-claude-haiku-4-5"
DEFAULT_MAX_INPUT_TOKENS = 1000
_MAX_OUTPUT_TOKENS = 100

_EXTRACTION_SYSTEM_PROMPT = (
    "You extract topic slugs from a coding / work session for memory indexing.\n"
    "Return ONLY a JSON array of 1-3 short slug strings.\n"
    "Slugs: lowercase, hyphen-separated, alphanumeric only, no punctuation.\n"
    "Pick specific entities: customer names, project codes, named technologies.\n"
    "Do NOT pick generic terms like 'python', 'debugging', 'agent'.\n"
    "If nothing specific stands out, return []."
)


def is_enabled(cfg: dict[str, Any]) -> bool:
    """True if auto-tagging is enabled. Default: False (opt-in)."""
    return bool(cfg.get("enabled", False))


def _sample_prompts(state: dict[str, Any], max_chars: int = 4000) -> str:
    """Concatenate user-prompt text from events up to ``max_chars``."""
    parts: list[str] = []
    total = 0
    for event in state.get("events", []):
        if event.get("kind") != "prompt":
            continue
        text = (event.get("prompt") or "").strip()
        if not text:
            continue
        room = max_chars - total
        if room <= 0:
            break
        if len(text) > room:
            parts.append(text[:room])
            break
        parts.append(text)
        total += len(text)
    return "\n\n".join(parts)


def _parse_response(text: str) -> list[str]:
    """Parse the LLM response as a JSON array of slug strings. Returns [] on any error."""
    if not text:
        return []
    s = text.strip()
    if s.startswith("```"):
        inner = s[3:]
        if inner.startswith("json"):
            inner = inner[4:]
        if "```" in inner:
            inner = inner.split("```", 1)[0]
        s = inner.strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, str) and item.strip()]


def extract_topic_slugs(
    state: dict[str, Any],
    cfg: dict[str, Any],
    workspace_client: Any,
) -> list[str]:
    """Call the configured serving endpoint and return up to 3 topic slugs.

    No-op (returns ``[]``) when auto-tag is disabled, no prompts to sample,
    or any endpoint error. Slugs returned here are NOT normalized — the
    caller (``WikiClient.upsert_vocabulary_slugs``) normalizes them.
    """
    if not is_enabled(cfg):
        return []
    max_in_tokens = int(cfg.get("max_input_tokens", DEFAULT_MAX_INPUT_TOKENS))
    sample = _sample_prompts(state, max_chars=max_in_tokens * 4)
    if not sample.strip():
        return []
    endpoint = cfg.get("endpoint", DEFAULT_ENDPOINT)
    try:
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

        response = workspace_client.serving_endpoints.query(
            name=endpoint,
            messages=[
                ChatMessage(role=ChatMessageRole.SYSTEM, content=_EXTRACTION_SYSTEM_PROMPT),
                ChatMessage(role=ChatMessageRole.USER, content=sample),
            ],
            max_tokens=_MAX_OUTPUT_TOKENS,
        )
    except Exception:
        return []
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError):
        return []
    return _parse_response(content)[:3]
