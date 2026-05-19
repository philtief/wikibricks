"""Pure helpers for `notebooks/wiki_tag.py`.

LLM-free. The notebook supplies LLM-returned strings; this module turns them
into well-formed slugs, deduplicates against the existing vocabulary, and
shapes the `wiki_log` event payload.

Storage convention (one decision, made here):
- Auto-tags written to `pages.tags` carry an `llm:` prefix so they can be
  distinguished from mechanical recorder tags (`session`, `cwd:...`, etc.).
- Slugs in `wiki_vocabulary` are stored WITHOUT the prefix.
"""

import json
import re

LLM_TAG_PREFIX = "llm:"
MAX_SLUG_LEN = 60
DEFAULT_APPROVE_THRESHOLD = 3


def normalize_slug(text: str) -> str:
    """Map a free-form phrase to a stable, kebab-case slug.

    - Lowercase
    - Non-alphanumerics collapsed to single hyphens
    - Stripped of leading/trailing hyphens
    - Truncated to MAX_SLUG_LEN

    Empty input returns empty string; caller filters.
    """
    if not text:
        return ""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:MAX_SLUG_LEN]


def parse_tag_response(raw: str) -> list[str]:
    """Extract a list of normalized slugs from an LLM response.

    Accepts:
        {"tags": ["row level security", "delta-lake-acl"]}
        ```json
        {"tags": [...]}
        ```
        ["row-level-security", "delta-lake"]

    Returns an empty list on any parse failure. Order is preserved.
    """
    if not raw:
        return []
    # Strip markdown code fences if present
    stripped = raw.strip()
    if stripped.startswith("```"):
        # Drop opening fence, optional language tag, and closing fence
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return []

    if isinstance(parsed, dict):
        items = parsed.get("tags", [])
    elif isinstance(parsed, list):
        items = parsed
    else:
        return []

    if not isinstance(items, list):
        return []

    result = []
    for item in items:
        if not isinstance(item, str):
            continue
        slug = normalize_slug(item)
        if slug:
            result.append(slug)
    return result


def dedupe_against_vocabulary(
    proposed: list[str],
    existing: list[str],
) -> list[str]:
    """Filter proposed slugs that already exist (case-insensitive).

    Existing slugs win — caller increments the vocabulary count by writing the
    same slug back, but does not produce a new term entry. Order of `proposed`
    is preserved.
    """
    existing_lc = {s.lower() for s in existing if s}
    return [s for s in proposed if s.lower() not in existing_lc]


def should_approve(count: int, threshold: int = DEFAULT_APPROVE_THRESHOLD) -> bool:
    """A slug becomes `approved` once it has been observed at least `threshold` times.

    Below threshold the row remains `pending`. Threshold is configurable so
    sparse personal wikis can drop to 2 and busy team wikis can raise to 5.
    """
    return count >= threshold


def prefix_llm(tags: list[str]) -> list[str]:
    """Prepend the LLM-tag prefix to each slug for storage in `pages.tags`."""
    return [f"{LLM_TAG_PREFIX}{t}" for t in tags if t]


def build_tag_event(
    path: str,
    proposed: list[str],
    committed: list[str],
    deduped: list[str],
    model: str,
    raw: str,
) -> dict:
    """Build the `wiki_log` details JSON for an `auto_tag` op.

    Keep `raw` truncated — wiki_log details is human-debuggable, not a model
    audit log.
    """
    return {
        "path": path,
        "model": model,
        "proposed": proposed,
        "committed": committed,
        "deduped_against_vocab": deduped,
        "raw_truncated": (raw or "")[:300],
    }
