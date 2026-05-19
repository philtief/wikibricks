"""CLI: export a wikibricks store to a Karpathy-style markdown folder.

Usage:
    uv run python -m wikibricks.export_karpathy ./out/ \\
        --profile fe-vm-agent-marketplace \\
        --catalog mycat --schema myschema \\
        --warehouse-id 41754a8563a43a49

Walks every page in the wiki, writes one `.md` per page under the target
directory. Frontmatter carries title, tags, memory_class, page_type, path.
Outgoing currently-valid edges (`valid_until IS NULL`) become a `## Related`
section with `[[wikilinks]]` or `link_type::[[wikilinks]]` (LLM Wiki v2
typed-edge syntax).

The output round-trips with `python -m wikibricks.import_karpathy` — you
can export the whole wiki, edit in Obsidian / Foam / Dendron / vim, and
re-import the modified folder.

This is the answer to "what about lock-in?" — wikibricks owns nothing,
the data is yours, as a folder of plain markdown.
"""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

from wikibricks.karpathy_logic import map_wiki_path_to_file, render_page_markdown


def fetch_pages_and_edges(wiki) -> tuple[list[dict], list[dict]]:
    """Pull all pages and all currently-valid edges (joined to source/target paths).

    `wiki` is a WikiClient. Uses two SQL statements: list_pages (already a
    method) and a join over links + pages to resolve page_ids to paths.
    """
    from wikibricks.ops import LINKS_TABLE, PAGES_TABLE

    pages = wiki.list_pages()

    # Pull currently-valid edges with src + tgt paths resolved.
    resp = wiki._exec(  # noqa: SLF001
        f"SELECT s.path AS source_path, t.path AS target_path, l.link_type "
        f"FROM {LINKS_TABLE} l "
        f"JOIN {PAGES_TABLE} s ON s.page_id = l.source_page_id "
        f"JOIN {PAGES_TABLE} t ON t.page_id = l.target_page_id "
        f"WHERE l.valid_until IS NULL"
    )
    rows = resp.result.data_array if resp.result else []
    cols = [c.name for c in wiki._manifest_columns(resp.manifest)] if rows else []
    edges = [dict(zip(cols, r)) for r in rows]
    return pages, edges


def write_pages(target_dir: Path, pages: list[dict], edges: list[dict]) -> int:
    """Render and write every page as a markdown file under `target_dir`.

    Returns the number of files written. Creates any missing parent dirs.
    Overwrites existing files (idempotent re-export).
    """
    edges_by_src: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        edges_by_src[e["source_path"]].append(e)

    written = 0
    for p in pages:
        wiki_path = p.get("path")
        if not wiki_path:
            continue
        rel = map_wiki_path_to_file(wiki_path)
        out_path = target_dir / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md = render_page_markdown(p, edges_by_src.get(wiki_path, []))
        out_path.write_text(md, encoding="utf-8")
        written += 1
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("target_dir", help="where to write the markdown tree")
    p.add_argument("--profile", help="Databricks CLI profile")
    p.add_argument("--catalog")
    p.add_argument("--schema")
    p.add_argument("--warehouse-id")
    p.add_argument("--limit", type=int, default=None,
                   help="cap number of pages exported (testing)")
    args = p.parse_args()

    if args.catalog:
        os.environ["WIKIBRICKS_CATALOG"] = args.catalog
    if args.schema:
        os.environ["WIKIBRICKS_SCHEMA"] = args.schema

    from databricks.sdk import WorkspaceClient

    from wikibricks import WikiClient

    ws = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    wiki = WikiClient(
        warehouse_id=args.warehouse_id or os.environ.get("WIKIBRICKS_WAREHOUSE_ID", ""),
        workspace_client=ws,
    )

    pages, edges = fetch_pages_and_edges(wiki)
    if args.limit is not None:
        pages = pages[: args.limit]
    print(f"exporting {len(pages)} pages, {len(edges)} edges to {args.target_dir}")

    target = Path(args.target_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    written = write_pages(target, pages, edges)
    print(f"wrote {written} markdown files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
