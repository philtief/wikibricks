"""CLI: import a Karpathy-style markdown wiki folder into WikiBricks.

Usage:
    uv run python -m wikibricks.import_karpathy ./my-notes/ \\
        --profile fe-vm-agent-marketplace \\
        --warehouse-id 41754a8563a43a49 \\
        --catalog mycat --schema myschema [--dry-run]

The importer walks every `.md` file under the source directory, parses
frontmatter, extracts `[[wikilinks]]` and `relationship::[[target]]` typed
edges, and writes pages + edges via `WikiClient.bulk_write_pages` +
`WikiClient.commit_edges`. Edges with a `link_type` not in the library's
`VALID_LINK_TYPES` are downgraded to `related` and the original type is
preserved as a tag.

Idempotent: re-running overwrites existing pages (`write_page` archives
the previous version) and the v0.6.0 bi-temporal `commit_edges` adds a
new edge version while closing the previous one.
"""

import argparse
import json
import sys
from pathlib import Path

from wikibricks.karpathy_logic import (
    extract_typed_edges,
    extract_wikilinks,
    parse_frontmatter,
    wiki_path_for,
)

VALID_LINK_TYPES = ("related", "contradicts", "extends", "supersedes", "cites")


def _collect_files(source_dir: Path) -> list[Path]:
    return sorted(p for p in source_dir.rglob("*.md") if p.is_file())


def _read_page_record(file: Path, base_dir: Path) -> dict:
    """Parse one markdown file into a WikiBricks page record (no edges yet)."""
    text = file.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    path = wiki_path_for(str(file), base_dir=str(base_dir), frontmatter=meta)
    title = meta.get("title")
    if not title:
        # First H1 in body, or filename slug as fallback.
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        if not title:
            title = file.stem.replace("-", " ").title()

    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tags = [str(t) for t in tags]

    summary = ""
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            summary = s[:200]
            break

    return {
        "path": path,
        "title": str(title),
        "content": {"summary": summary, "body": body},
        "page_type": "concept",
        "created_by": "import-karpathy",
        "tags": tags,
        "_body": body,  # internal — for edge extraction in build_edges
    }


def build_pages_and_edges(
    source_dir: Path,
) -> tuple[list[dict], list[dict]]:
    """Two-pass: read all files, then resolve wikilink/typed-edge targets to paths.

    Returns `(pages, edges)`:
    - `pages` is a list of dicts ready for `bulk_write_pages` (the `_body`
      field is internal and gets stripped before writing).
    - `edges` is a list of `{source_path, target_path, link_type}` dicts;
      the caller resolves these to page_ids after writing.
    """
    files = _collect_files(source_dir)
    records: list[dict] = [_read_page_record(f, source_dir) for f in files]

    # Index by title (case-insensitive) and path for wikilink resolution.
    by_title: dict[str, str] = {}
    by_path: dict[str, str] = {}
    for r in records:
        by_title[r["title"].lower()] = r["path"]
        by_path[r["path"]] = r["path"]

    edges: list[dict] = []
    for r in records:
        body = r["_body"]
        # Typed edges go first; collect their targets so we don't double-count.
        typed = extract_typed_edges(body)
        typed_targets = {t for _, t in typed}
        for relationship, target in typed:
            resolved = by_title.get(target.lower()) or by_path.get(target)
            if not resolved:
                continue
            link_type = relationship if relationship in VALID_LINK_TYPES else "related"
            edges.append({
                "source_path": r["path"],
                "target_path": resolved,
                "link_type": link_type,
            })

        for plain in extract_wikilinks(body):
            if plain in typed_targets:
                continue
            resolved = by_title.get(plain.lower()) or by_path.get(plain)
            if not resolved:
                continue
            edges.append({
                "source_path": r["path"],
                "target_path": resolved,
                "link_type": "related",
            })

    # Strip internal field
    for r in records:
        r.pop("_body", None)
    return records, edges


def _resolve_edges_to_ids(
    edges: list[dict], path_to_id: dict[str, str],
) -> list[dict]:
    out: list[dict] = []
    for e in edges:
        src = path_to_id.get(e["source_path"])
        tgt = path_to_id.get(e["target_path"])
        if not src or not tgt:
            continue
        out.append({
            "source_page_id": src,
            "target_page_id": tgt,
            "link_type": e["link_type"],
            "confidence": 1.0,
            "origin": "manual",
        })
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source_dir", help="path to the markdown wiki root")
    p.add_argument("--database-url", help="PostgreSQL connection URL")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be imported without writing")
    args = p.parse_args()

    source = Path(args.source_dir).resolve()
    if not source.is_dir():
        print(f"error: {source} is not a directory", file=sys.stderr)
        return 2

    pages, edges = build_pages_and_edges(source)
    print(f"discovered {len(pages)} pages, {len(edges)} edges from {source}")
    if not pages:
        return 0

    if args.dry_run:
        print(json.dumps({
            "pages": [{"path": p["path"], "title": p["title"],
                       "tags": p["tags"]} for p in pages[:10]],
            "edges": edges[:20],
            "totals": {"pages": len(pages), "edges": len(edges)},
        }, indent=2))
        return 0

    from wikibricks import WikiClient

    wiki = WikiClient(args.database_url)

    written = wiki.write_pages(pages)
    print(f"wrote {written} pages")

    # Resolve paths back to page_ids for commit_edges.
    listed = wiki.list_pages()
    path_to_id = {p["path"]: p["page_id"] for p in listed if p.get("path")}
    resolved = _resolve_edges_to_ids(edges, path_to_id)
    committed = wiki.commit_edges(resolved)
    print(f"committed {committed} edges (of {len(resolved)} resolved)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
