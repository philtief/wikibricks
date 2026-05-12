"""Translate accumulated session state into a WikiBricks page payload.

Pure functions, no IO — fully unit-testable. Output shape matches what
`WikiClient.write_page` expects: a path, a title, a {summary, body} content
dict, and a tag list.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any

TITLE_MAX = 120
SUMMARY_MAX = 200

# Prompt text starting with one of these prefixes is almost always a
# skill-supplied system prompt (memory consolidation, daily summarisation,
# code review templates). Skill invocations create their own Claude Code
# sessions, and recording every one of them turns the wiki index into noise.
_SYSTEM_PROMPT_PREFIXES = (
    "You are ",
    "Apply maximum ",
    "Apply minimum ",
    "Apply non-destructive ",
)


def _looks_like_system_prompt(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    return any(stripped.startswith(p) for p in _SYSTEM_PROMPT_PREFIXES)


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
    # Prefer the first prompt that is not a skill system-prompt template.
    # Falls back to the original behaviour if every prompt looks like one.
    for e in state.get("events", []):
        if e.get("kind") != "prompt":
            continue
        text = (e.get("prompt") or "").strip()
        if text and not _looks_like_system_prompt(text):
            return text.split("\n", 1)[0][:TITLE_MAX]
    fp = state.get("first_prompt")
    if fp:
        first_line = fp.strip().split("\n", 1)[0]
        return first_line[:TITLE_MAX]
    return f"Session {state['session_id'][:8]}"


def session_tags(state: dict[str, Any]) -> list[str]:
    tags = ["session"]
    cwd = state.get("cwd")
    if cwd:
        tags.append(f"cwd:{PurePath(cwd).name}")
    model = state.get("model")
    if model:
        tags.append(f"model:{model}")
    return tags


def session_content(state: dict[str, Any]) -> dict[str, str]:
    """Build {'summary', 'body'} for the wiki page's VARIANT content column."""
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
