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

# Ephemeral CWDs mark a programmatic Claude Code sub-invocation (another
# agent / a skill / a memory-consolidation run using ``claude`` as a
# subprocess) rather than real interactive work. This mirrors the write-time
# skip signal in ``wikibricks_recorder.page_builder.is_ephemeral``. Anchored
# to the ``- CWD:`` metadata line so an ephemeral path merely *mentioned* in
# prose does not condemn a real session.
_EPHEMERAL_CWD_RE = re.compile(
    r"^- CWD:\s*(/tmp|/private/tmp|/var/tmp|/var/folders|/private/var/folders)(/|\s|$)",
    re.MULTILINE,
)


def looks_like_system_prompt(text: str) -> bool:
    """True when ``text`` starts with a known skill / sub-agent prefix."""
    return bool(text) and text.strip().startswith(_SYSTEM_PROMPT_PREFIXES)


def body_has_ephemeral_cwd(body: str | None) -> bool:
    """True when a session page body records an ephemeral (``/tmp``-ish) CWD.

    Reads the persisted ``- CWD: <path>`` metadata line. Real summarized
    sessions store a dense summary + ToC with no metadata block, so they
    return False — the safe default that keeps genuine work.
    """
    return bool(body) and _EPHEMERAL_CWD_RE.search(body) is not None


def is_noise_page(title: str | None, body: str | None) -> bool:
    """True when a persisted session page is recorder noise, safe to purge.

    Two independent signals, either sufficient:
    1. Body records an ephemeral CWD (programmatic /tmp sub-invocation).
    2. Title is a raw system-prompt template (legacy title-based path).

    A ``[stub]`` title alone is NOT sufficient — real sessions can fall back
    to a stub title while carrying a genuine summary body.
    """
    return body_has_ephemeral_cwd(body) or looks_like_system_prompt(title or "")


def strip_boilerplate_prefix(title: str | None) -> str | None:
    """Recover a chunk title whose boilerplate parent prefix leaked in.

    ``segregate`` builds chunk titles as ``"<parent> - <chunk>"`` (see
    ``segregate_logic.child_title``). When the parent title was leaked
    system-prompt boilerplate, the real chunk title is the text after the
    first ``" - "``. Returns that suffix, or None when the prefix is not
    boilerplate, there is no separator, or the suffix is empty (nothing to
    recover — leave the title untouched).
    """
    if not title or " - " not in title:
        return None
    prefix, suffix = title.split(" - ", 1)
    if not looks_like_system_prompt(prefix):
        return None
    suffix = suffix.strip()
    return suffix[:TITLE_MAX] if suffix else None


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
