"""The reader-facing page layout: a short summary card, then key points."""

from __future__ import annotations

import json
import re
from typing import Any

SUMMARY_MAX_CHARS = 280
# A shorter body is read in one glance and needs no key points.
KEY_POINTS_MIN_BODY_CHARS = 1000
KEY_POINTS_HEADING = "## Key points"
# The heading, then its bullets, indented continuation lines, and blank lines. Any other
# line ends the section, so a rewrite never removes body text.
_KEY_POINTS_SECTION = re.compile(
    r"^## Key points[ \t]*\n(?:(?:[ \t]*[-*+] [^\n]*|[ \t]+\S[^\n]*)?\n)*", re.M
)
_LEADING_TITLE = re.compile(r"\A\s*# [^\n]*\n")


def _split_title(body: str) -> tuple[str, str]:
    """Split off a leading `# Title` line, which stays above the key points."""
    match = _LEADING_TITLE.match(body)
    if match is None:
        return "", body
    return match.group(0).strip() + "\n\n", body[match.end():]


def digest_problems(summary: str, body: str) -> list[str]:
    problems = []
    if len(summary) > SUMMARY_MAX_CHARS:
        problems.append("summary_too_long")
    opening = _split_title(body)[1].lstrip()
    if len(body) >= KEY_POINTS_MIN_BODY_CHARS and not opening.startswith(KEY_POINTS_HEADING):
        problems.append("no_key_points")
    return problems


def with_key_points(body: str, points: list[str]) -> str:
    """Open the body with the key points, replacing an earlier key-points section."""
    title, rest = _split_title(body)
    rest = _KEY_POINTS_SECTION.sub("", rest, count=1).lstrip("\n")
    lines = "\n".join(f"- {point}" for point in points)
    return f"{title}{KEY_POINTS_HEADING}\n\n{lines}\n\n{rest}"


def undigested_pages(conn: Any) -> list[dict[str, Any]]:
    """Active curated pages that break the layout, most recently updated first."""
    rows = conn.execute(
        "SELECT p.path, v.content FROM pages p "
        "JOIN page_versions v ON v.version_id = p.current_version_id "
        "WHERE p.status = 'active' AND p.path NOT LIKE '_meta/%' "
        "AND v.page_type NOT IN ('session', 'archive') ORDER BY p.updated_at DESC, p.path"
    ).fetchall()
    result = []
    for path, raw in rows:
        content = json.loads(raw) if isinstance(raw, str) else raw
        problems = digest_problems(str(content.get("summary") or ""), str(content.get("body") or ""))
        if problems:
            result.append({"path": path, "problems": problems})
    return result


__all__ = [
    "KEY_POINTS_HEADING",
    "KEY_POINTS_MIN_BODY_CHARS",
    "SUMMARY_MAX_CHARS",
    "digest_problems",
    "undigested_pages",
    "with_key_points",
]
