"""Translate accumulated session state into a WikiBricks page payload.

Pure functions, no IO — fully unit-testable. Output shape matches what
`WikiClient.write_page` expects: a path, a title, a {summary, body} content
dict, and a tag list.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any

TITLE_MAX = 120
SUMMARY_MAX = 200
DEFAULT_MIN_EVENTS = 2

# Lines matching these patterns are LLM system-prompt boilerplate, not
# user-meaningful titles. Anchored at start-of-line so "Apply this patch"
# still passes through. (v0.7.3 — replaces the legacy startswith prefix
# test that mis-titled 92 % of summarizer-driven sessions.)
_BOILERPLATE_PATTERNS = [
    re.compile(r"^you are\b", re.IGNORECASE),
    re.compile(r"^apply\b.*\b(rules?|compression|prompts?|instructions?|patch|fixes?)\b", re.IGNORECASE),
    re.compile(r"^apply (the |maximum |non-?destructive )", re.IGNORECASE),
    re.compile(r"^(please )?summari[sz]e\b", re.IGNORECASE),
    re.compile(r"^read the (conversation|transcript|session|message|extract)", re.IGNORECASE),
    re.compile(r"^extract\b.*\bfrom (the |this )?(conversation|transcript|session)", re.IGNORECASE),
    re.compile(r"^write\b.*\b(memory|summary|entry|log)\b", re.IGNORECASE),
    re.compile(r"^generate (a |an |the )?(summary|entry|log)\b", re.IGNORECASE),
    re.compile(r"^output (only |just )?(the |a |an )?(summary|entry|log|json)\b", re.IGNORECASE),
    re.compile(r"^rules:\s*$", re.IGNORECASE),
    re.compile(r"^instructions?:\s*$", re.IGNORECASE),
    re.compile(r"^system( prompt)?:\s*$", re.IGNORECASE),
]
# Bullet lines are almost always rule items, not titles.
_BULLET_LINE = re.compile(r"^[\-\*•]\s")
_MIN_TITLE_CHARS = 4

_EPHEMERAL_CWD_PREFIXES = ("/tmp/", "/private/tmp/", "/var/tmp/")
_EPHEMERAL_CWD_EXACT = ("/tmp", "/private/tmp", "/var/tmp")


# Back-compat shim — the legacy name is still imported by older tests
# and downstream tools. Forwards to the v0.7.3 boilerplate detector.
# Whitespace-only input returns False (no actual content to classify).
def _looks_like_system_prompt(text: str) -> bool:
    if not text or not text.strip():
        return False
    first = text.strip().split("\n", 1)[0]
    return _is_boilerplate(first)


def _is_boilerplate(line: str) -> bool:
    s = line.strip()
    # Recorder bodies sometimes render prompts as `> <text>` blockquotes
    # and that prefix can leak into first_prompt itself; strip greedily.
    while s.startswith("> "):
        s = s[2:].lstrip()
    if len(s) < _MIN_TITLE_CHARS:
        return True
    if _BULLET_LINE.match(s):
        return True
    return any(p.search(s) for p in _BOILERPLATE_PATTERNS)


def _started_dt(started_at: str | None) -> datetime:
    if not started_at:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(started_at.replace("Z", "+00:00"))


def session_path(user_id: str, session_id: str, started_at: str | None) -> str:
    """`sessions/<user_id>/YYYY/MM/DD/<session_id>` — partitions by user
    so the same schema serves a single user (personal wiki) or a team
    sharing one schema. Date-bucketed under user for natural browsing.
    """
    d = _started_dt(started_at)
    return f"sessions/{user_id}/{d:%Y/%m/%d}/{session_id}"


def session_title(state: dict[str, Any]) -> str:
    """Pick the first informative line of `first_prompt` as the page title.

    Many sessions feed Claude a multi-line prompt that opens with LLM
    instruction boilerplate ("You are summarizing...", "Rules:") and then
    states the actual ask further down. Naïve first-line extraction made
    every such session indistinguishable. We scan for the first line that
    isn't boilerplate; if every line looks like instruction scaffolding,
    we fall back to the session-id stub.
    """
    fp = state.get("first_prompt")
    if fp:
        for line in fp.splitlines():
            if not _is_boilerplate(line):
                clean = line.strip()
                while clean.startswith("> "):
                    clean = clean[2:].lstrip()
                return clean[:TITLE_MAX]
    # Fall back to events (legacy path) — same boilerplate filter.
    for e in state.get("events", []):
        if e.get("kind") != "prompt":
            continue
        text = (e.get("prompt") or "").strip()
        for line in text.splitlines():
            if not _is_boilerplate(line):
                return line.strip()[:TITLE_MAX]
    return f"Session {state['session_id'][:8]}"


def is_ephemeral(state: dict[str, Any]) -> bool:
    """Skip writes for sessions that aren't real interactive work.

    Two signals: (a) cwd is a tmp-ish path, so the session is almost
    certainly a programmatic Claude Code invocation (e.g. another agent
    using ``claude`` as a sub-process); (b) event count below threshold,
    so there isn't enough content to be worth a page. Threshold is
    ``WIKIBRICKS_RECORDER_MIN_EVENTS`` (default 2).
    """
    cwd = (state.get("cwd") or "").rstrip("/")
    if cwd in _EPHEMERAL_CWD_EXACT or any(cwd.startswith(p) for p in _EPHEMERAL_CWD_PREFIXES):
        return True
    try:
        min_events = int(os.environ.get("WIKIBRICKS_RECORDER_MIN_EVENTS", DEFAULT_MIN_EVENTS))
    except ValueError:
        min_events = DEFAULT_MIN_EVENTS
    if len(state.get("events", [])) < min_events:
        return True
    return False


def session_tags(
    state: dict[str, Any],
    topic_keywords: dict[str, list[str]] | None = None,
) -> list[str]:
    tags = ["session"]
    cwd = state.get("cwd")
    if cwd:
        tags.append(f"cwd:{PurePath(cwd).name}")
    model = state.get("model")
    if model:
        tags.append(f"model:{model}")
    if topic_keywords:
        text = (state.get("first_prompt") or "")
        for e in state.get("events", []):
            if e.get("kind") == "prompt":
                text += " " + (e.get("prompt") or "")
        text_lower = text.lower()
        for slug, terms in topic_keywords.items():
            if any(t.lower() in text_lower for t in terms):
                tags.append(f"customer:{slug}")
    return tags


def session_content(
    state: dict[str, Any],
    *,
    dense_summary: str | None = None,
) -> dict[str, str]:
    """Build {'summary', 'body'} for the wiki page's VARIANT content column.

    If ``dense_summary`` is a non-empty string, it replaces the default
    truncated-first-prompt summary. The raw transcript body is built the
    same way either path. Empty-string dense_summary falls through to
    the legacy default — guards against accidentally embedding a blank
    LLM response.
    """
    if dense_summary:
        summary = dense_summary
    else:
        summary = (state.get("first_prompt") or "").strip().replace("\n", " ")
        summary = summary[:SUMMARY_MAX] if summary else f"Session {state['session_id'][:8]}"

    body_lines = [
        f"# Session {state['session_id']}",
        "",
        f"- Started: {state.get('started_at') or '?'}",
        f"- CWD: {state.get('cwd') or '?'}",
        f"- Model: {state.get('model') or '?'}",
        f"- Events: {len(state.get('events', []))}",
        "",
        "## Timeline",
    ]
    for e in state.get("events", []):
        kind = e.get("kind", "?")
        ts = e.get("ts", "")
        if kind == "prompt":
            body_lines.append(f"### prompt @ {ts}")
            body_lines.append(f"> {e.get('prompt', '')}")
        elif kind == "tool":
            body_lines.append(f"### {e.get('tool_name', '?')} @ {ts}")
            extra = e.get("summary")
            if extra:
                body_lines.append(extra)
        else:
            body_lines.append(f"### {kind} @ {ts}")
        body_lines.append("")

    return {"summary": summary, "body": "\n".join(body_lines)}
