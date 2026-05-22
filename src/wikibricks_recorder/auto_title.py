"""LLM-generated session titles, opt-in.

At flush time, ask a Databricks Foundation Model serving endpoint to
produce a clean human-readable title from the session's first prompt.
Falls back to ``page_builder.session_title`` (boilerplate-skip heuristic)
on any error or when disabled.

**Privacy.** This module sends a sample of your first prompt text to a
Databricks serving endpoint. It is OFF by default. Enable via the
``[auto_title]`` section in ``~/.wikibricks-recorder.toml``::

    [auto_title]
    enabled = true
    endpoint = "databricks-claude-haiku-4-5"
    max_input_tokens = 600

Failures are silent: any error returns ``None`` and the caller is expected
to fall back to the deterministic title heuristic.
"""

from __future__ import annotations

from typing import Any

DEFAULT_ENDPOINT = "databricks-claude-haiku-4-5"
DEFAULT_MAX_INPUT_TOKENS = 600
_MAX_OUTPUT_TOKENS = 40
_TITLE_MAX = 120  # mirrors page_builder.TITLE_MAX

_SYSTEM_PROMPT = (
    "You generate a short human-readable title for a Claude Code work "
    "session. Read the user's first prompt and output a single line "
    "(≤80 characters) that names what the session is about. No quotes, "
    "no trailing period, no preamble. Just the title text."
)


def is_enabled(cfg: dict[str, Any]) -> bool:
    """True if auto-title is enabled. Default: False (opt-in)."""
    return bool(cfg.get("enabled", False))


def _clean_title(raw: str) -> str | None:
    """Strip whitespace, surrounding quotes, trailing period. Return None
    if the cleaned result is empty.
    """
    if not raw:
        return None
    s = raw.strip()
    # Strip surrounding double or single quotes (LLMs love quoting titles)
    if len(s) >= 2 and s[0] in ('"', "'") and s[-1] == s[0]:
        s = s[1:-1].strip()
    # Strip a trailing period (almost never desired in a title)
    if s.endswith(".") and not s.endswith(".."):
        s = s[:-1].rstrip()
    # Use only the first non-empty line — LLMs sometimes add an
    # explanation after a blank line despite the prompt.
    first = next((line for line in s.splitlines() if line.strip()), "")
    first = first.strip()
    if not first:
        return None
    return first[:_TITLE_MAX]


def _sample_prompt(state: dict[str, Any], max_chars: int) -> str:
    """Return up to ``max_chars`` of the session's first prompt content."""
    fp = (state.get("first_prompt") or "").strip()
    if fp:
        return fp[:max_chars]
    for event in state.get("events", []):
        if event.get("kind") != "prompt":
            continue
        text = (event.get("prompt") or "").strip()
        if text:
            return text[:max_chars]
    return ""


def generate_title(
    state: dict[str, Any],
    cfg: dict[str, Any],
    workspace_client: Any,
) -> str | None:
    """Call the configured serving endpoint and return a cleaned title.

    Returns ``None`` when auto-title is disabled, no prompt content is
    available, any endpoint error occurs, or the model returns nothing
    useful. Callers should treat ``None`` as "fall back to the
    deterministic heuristic".
    """
    if not is_enabled(cfg):
        return None
    max_in_tokens = int(cfg.get("max_input_tokens", DEFAULT_MAX_INPUT_TOKENS))
    sample = _sample_prompt(state, max_chars=max_in_tokens * 4)
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
    return _clean_title(content)
