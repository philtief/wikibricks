"""Parse [wb:<path>] citation markers from a Claude Code transcript.

At Stop time, ``hooks.on_stop`` reads the transcript at ``payload["transcript_path"]``,
walks to the most recent assistant message, and extracts every ``[wb:<path>]``
marker the agent emitted in its reply. Those paths get logged as ``op_type='cited'``
rows so the search reranker can later bias hits toward pages the agent actually used.

All failures (missing file, malformed JSON lines, no assistant turn) return an empty
set — the hook must never crash the host.
"""

from __future__ import annotations

import json
import re

_CITATION_RE = re.compile(r"\[wb:([^\[\]\s]+)\]")


def extract_cited_paths(transcript_path: str | None) -> set[str]:
    """Return the set of distinct paths cited as ``[wb:<path>]`` in the
    transcript's most recent assistant message. Empty set if the file is
    missing, unreadable, or contains no assistant turn.
    """
    if not transcript_path:
        return set()
    last_assistant_text: list[str] = []
    try:
        with open(transcript_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                content = (rec.get("message") or {}).get("content") or []
                texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                if texts:
                    last_assistant_text = texts
    except OSError:
        return set()
    if not last_assistant_text:
        return set()
    cited: set[str] = set()
    for t in last_assistant_text:
        cited.update(m.group(1) for m in _CITATION_RE.finditer(t))
    return cited
