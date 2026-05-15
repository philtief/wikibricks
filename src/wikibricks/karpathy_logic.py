"""Pure helpers for importing a Karpathy-style markdown wiki into WikiBricks.

The Karpathy LLM-Wiki pattern (gist 442a6bf, April 2026, 16M views) is a folder
of markdown files where:
- `wiki/` holds curated entity pages (one per concept)
- `raw/` holds source documents
- `index.md` is the table of contents
- YAML frontmatter carries `title`, `tags`, optional `path`
- `[[wikilinks]]` connect pages
- `relationship::[[Target]]` typed edges (LLM Wiki v2 convention)

This module is LLM-free. The CLI in `wikibricks.import_karpathy` walks a
directory and calls these helpers to convert markdown into WikiBricks
`bulk_write_pages` + `commit_edges` payloads.
"""

import re

VALID_RELATIONSHIP = re.compile(r"^[a-z][a-z0-9_]*$")
WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
TYPED_EDGE = re.compile(r"([a-z][a-z0-9_]*)::\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML-ish frontmatter from the head of a markdown string.

    Recognises the simple two-cases the Karpathy/Obsidian/Foam ecosystems
    settled on:
    - `key: value` pairs (one per line)
    - YAML list form for `tags`:
        ```
        tags:
          - foo
          - bar
        ```

    Returns `({}, original_text)` if there's no closing `---` — keeps the body
    intact so the caller can still write the file. Avoids pulling in PyYAML
    so the importer has zero runtime deps beyond stdlib.
    """
    if not text.startswith("---\n"):
        return {}, text
    closing = text.find("\n---", 4)
    if closing == -1:
        return {}, text
    block = text[4:closing]
    body = text[closing + len("\n---"):].lstrip("\n").rstrip()

    meta: dict[str, object] = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if ":" not in line:
            i += 1
            continue
        key, sep, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest == "":
            # YAML list form: gather following indented "- " lines
            items: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].startswith(("  -", "\t-")):
                items.append(lines[j].lstrip(" \t-").strip())
                j += 1
            meta[key] = items
            i = j
        else:
            meta[key] = rest
            i += 1
    return meta, body


def extract_wikilinks(body: str) -> list[str]:
    """Return ordered unique `[[target]]` targets in `body`.

    Strips Obsidian display aliases (`[[target|display]]` → `target`).
    Empty brackets (`[[]]`) are skipped. Order of first appearance is preserved.
    """
    seen: dict[str, None] = {}
    for m in WIKILINK.finditer(body):
        target = m.group(1).strip()
        if target:
            seen.setdefault(target, None)
    return list(seen.keys())


def extract_typed_edges(body: str) -> list[tuple[str, str]]:
    """Return `[(link_type, target), ...]` for `relationship::[[Target]]` edges.

    `link_type` must match `[a-z][a-z0-9_]*` (kebab-friendly identifier).
    Caller decides whether to map the link_type into the library's
    `VALID_LINK_TYPES` set or pass it as a free-form tag.
    """
    edges: list[tuple[str, str]] = []
    for m in TYPED_EDGE.finditer(body):
        relationship = m.group(1)
        target = m.group(2).strip()
        if VALID_RELATIONSHIP.match(relationship) and target:
            edges.append((relationship, target))
    return edges


def _slugify(text: str) -> str:
    """Lower-case, replace non-alphanumerics with hyphens, strip edges."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9/]+", "-", text)
    return text.strip("-")


def wiki_path_for(
    local_path: str,
    base_dir: str,
    frontmatter: dict | None = None,
) -> str:
    """Map a local markdown file path to a WikiBricks page path.

    Rules:
    - frontmatter `path:` wins if present.
    - `<base>/wiki/X/Y.md` → `topics/x/y`
    - `<base>/raw/X.md` → `sources/x`
    - else `notes/<slug>`.

    Always slug-cases the leaf; subfolders are slugged too.
    """
    if frontmatter and "path" in frontmatter and isinstance(frontmatter["path"], str):
        return frontmatter["path"]

    base = base_dir.rstrip("/")
    rel = local_path[len(base):].lstrip("/") if local_path.startswith(base) else local_path
    if rel.endswith(".md"):
        rel = rel[: -len(".md")]

    parts = rel.split("/")
    top = parts[0] if parts else ""

    if top == "wiki":
        prefix = "topics"
        tail = parts[1:]
    elif top == "raw":
        prefix = "sources"
        tail = parts[1:]
    else:
        prefix = "notes"
        tail = parts

    slugged = [_slugify(p) for p in tail if p]
    return f"{prefix}/" + "/".join(slugged)
