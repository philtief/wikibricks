"""Convert the HotpotQA dev JSON into WikiBricks seed files (pages.jsonl + links.jsonl).

Input: .cache/hotpot/hotpot_dev_distractor_v1.json (produced by fetch_hotpot.py).
Output: src/wikibricks/seeds/hotpot/pages.jsonl, src/wikibricks/seeds/hotpot/links.jsonl,
        src/wikibricks/seeds/hotpot/queries.jsonl.

Each HotpotQA article becomes one WikiBricks page. Each question produces two `supports`
links (one per supporting-fact page).
"""

import json
import re
import sys
from pathlib import Path

CACHE = Path(".cache/hotpot/hotpot_dev_distractor_v1.json")
OUT_DIR = Path("src/wikibricks/seeds/hotpot")


def _slugify(title: str) -> str:
    s = title.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")[:120] or "untitled"


def convert(raw: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (pages, links, queries). Dedupes pages across questions."""
    pages_by_path: dict[str, dict] = {}
    links: list[dict] = []
    queries: list[dict] = []

    for q in raw:
        qid = q["_id"]
        question = q["question"]
        supporting_paths: list[str] = []

        for title, paragraphs in q["context"]:
            path = _slugify(title)
            if path not in pages_by_path:
                paragraph_sections = [
                    {"paragraph_id": i, "text": sent}
                    for i, sent in enumerate(paragraphs)
                ]
                body = " ".join(paragraphs)
                pages_by_path[path] = {
                    "path": path,
                    "title": title,
                    "page_type": "entity",
                    "content": {
                        "summary": paragraphs[0] if paragraphs else title,
                        "body": body,
                        "paragraphs": paragraph_sections,
                    },
                    "created_by": "hotpot-import",
                    "tags": [],
                }

        supp_titles = {title for title, _ in q.get("supporting_facts", [])}
        for title in supp_titles:
            supporting_paths.append(_slugify(title))

        queries.append({
            "id": qid,
            "question": question,
            "relevant_paths": sorted(set(supporting_paths)),
        })

        # Cross-link supporting pages to each other with link_type='supports'.
        for src in supporting_paths:
            for tgt in supporting_paths:
                if src != tgt:
                    links.append({
                        "source_path": src,
                        "target_path": tgt,
                        "link_type": "supports",
                    })

    # Dedupe links.
    seen = set()
    unique_links: list[dict] = []
    for link in links:
        key = (link["source_path"], link["target_path"], link["link_type"])
        if key not in seen:
            seen.add(key)
            unique_links.append(link)

    return list(pages_by_path.values()), unique_links, queries


def main(cache_path: Path = CACHE, out_dir: Path = OUT_DIR) -> None:
    if not cache_path.exists():
        print(f"missing {cache_path} - run scripts/fetch_hotpot.py first", file=sys.stderr)
        sys.exit(1)

    with open(cache_path) as f:
        raw = json.load(f)
    pages, links, queries = convert(raw)

    out_dir.mkdir(parents=True, exist_ok=True)
    pages_path = out_dir / "pages.jsonl"
    links_path = out_dir / "links.jsonl"
    queries_path = out_dir / "queries.jsonl"

    with open(pages_path, "w") as f:
        for p in pages:
            f.write(json.dumps(p) + "\n")
    with open(links_path, "w") as f:
        for link in links:
            f.write(json.dumps(link) + "\n")
    with open(queries_path, "w") as f:
        for q in queries:
            f.write(json.dumps(q) + "\n")

    print(f"pages: {len(pages):,} → {pages_path}")
    print(f"links: {len(links):,} → {links_path}")
    print(f"queries: {len(queries):,} → {queries_path}")


if __name__ == "__main__":
    main()
