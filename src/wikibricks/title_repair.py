"""Pure-function helpers for retroactive page-title repair.

Used by `scripts/repair_titles.py` to derive a meaningful title from a
session page body when the original title is a skill / sub-agent system
prompt template. LLM-free per the library's hard rules.
"""

from __future__ import annotations

import re

_SYSTEM_PROMPT_PREFIXES = ("You are ", "Apply maximum ")
_PROMPT_LINE_RE = re.compile(r"^### prompt @ .*?\n> (.+)$", re.MULTILINE)
TITLE_MAX = 120


def looks_like_system_prompt(text: str) -> bool:
    """True when ``text`` starts with a known skill / sub-agent prefix."""
    return bool(text) and text.strip().startswith(_SYSTEM_PROMPT_PREFIXES)


def extract_repaired_title(body: str) -> str | None:
    """Walk a session page body and return the first prompt that is not a
    system-prompt template, truncated to ``TITLE_MAX`` chars. Returns None
    if no suitable prompt is found.
    """
    for match in _PROMPT_LINE_RE.finditer(body):
        text = match.group(1).strip()
        if text and not looks_like_system_prompt(text):
            return text.split("\n", 1)[0][:TITLE_MAX]
    return None
