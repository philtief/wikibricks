"""Convert 2WikiMultiHopQA dev JSON into WikiBricks seed files.

Input: data/twowiki/raw/dev.json + id_aliases.json
Output: src/wikibricks/seeds/twowiki/{pages.jsonl, links.jsonl, queries.jsonl}

One page per unique Wikipedia title seen in dev contexts (dedup). Typed links
from Wikidata `evidences_id` triples, resolved via surface form + id_aliases.
Queries preserve every field the official eval v1.1 needs.

Usage:
  .venv/bin/python scripts/build_twowiki_seed.py          # full dev
  .venv/bin/python scripts/build_twowiki_seed.py --sample 500
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

DEV_PATH = Path("data/twowiki/raw/dev.json")
ALIASES_PATH = Path("data/twowiki/raw/id_aliases.json")
OUT_DIR = Path("src/wikibricks/seeds/twowiki")


def _slugify(title: str) -> str:
    s = title.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")[:120] or "untitled"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().strip())


def _norm_stripped(s: str) -> str:
    """Lower, strip parens content (e.g. 'Foo (film)' -> 'foo')."""
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
    return _norm(s)


def _page_id(title: str) -> str:
    return hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]


def load_aliases(path: Path) -> dict[str, set[str]]:
    """Qid -> {surface forms}. id_aliases.json is JSONL."""
    out: dict[str, set[str]] = {}
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            out[r["Q_id"]] = set(r.get("aliases", [])) | set(r.get("demonyms", []))
    return out


def build_title_index(titles: set[str]) -> dict[str, str]:
    """Index of normalized surface forms -> canonical title for fast lookup."""
    idx: dict[str, str] = {}
    for t in titles:
        idx.setdefault(_norm(t), t)
        idx.setdefault(_norm_stripped(t), t)
    return idx


def resolve_title(surface: str, qid: str, title_idx: dict[str, str],
                  aliases: dict[str, set[str]]) -> str | None:
    """Try normalized surface match, fall back to Qid aliases."""
    hit = title_idx.get(_norm(surface)) or title_idx.get(_norm_stripped(surface))
    if hit:
        return hit
    for alias in aliases.get(qid, ()):
        hit = title_idx.get(_norm(alias)) or title_idx.get(_norm_stripped(alias))
        if hit:
            return hit
    return None


def _pred_slug(pred: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", pred.lower()).strip("_") or "related"


def convert(raw: list[dict], aliases: dict[str, set[str]]
           ) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Return (pages, links, queries, stats)."""
    pages_by_title: dict[str, dict] = {}
    queries: list[dict] = []

    # First pass: collect every unique context title -> page row.
    for q in raw:
        for title, sentences in q["context"]:
            if title in pages_by_title:
                continue
            body = " ".join(sentences)
            pages_by_title[title] = {
                "page_id": _page_id(title),
                "path": _slugify(title),
                "title": title,
                "page_type": "entity",
                "content": {
                    "summary": sentences[0] if sentences else title,
                    "body": body,
                    "paragraphs": [
                        {"paragraph_id": i, "text": s} for i, s in enumerate(sentences)
                    ],
                },
                "created_by": "twowiki-import",
                "tags": [],
            }

    title_to_path = {t: p["path"] for t, p in pages_by_title.items()}
    title_idx = build_title_index(set(pages_by_title.keys()))

    # Second pass: build typed links from evidences_id.
    link_set: set[tuple[str, str, str]] = set()
    stats = {"evidences_total": 0, "evidences_resolved": 0,
             "questions_with_evidence": 0, "questions_no_evidence": 0}

    for q in raw:
        evi_id = q.get("evidences_id") or []
        evi_surf = q.get("evidences") or []
        if not evi_id:
            stats["questions_no_evidence"] += 1
            continue
        stats["questions_with_evidence"] += 1
        for (sub_q, rel, obj_q), (sub_s, _, obj_s) in zip(evi_id, evi_surf):
            stats["evidences_total"] += 1
            sub_title = resolve_title(sub_s, sub_q, title_idx, aliases)
            obj_title = resolve_title(obj_s, obj_q, title_idx, aliases)
            if sub_title and obj_title:
                stats["evidences_resolved"] += 1
                link_set.add((title_to_path[sub_title],
                              title_to_path[obj_title],
                              _pred_slug(rel)))

    links = [
        {"source_path": s, "target_path": t, "link_type": lt}
        for (s, t, lt) in sorted(link_set)
    ]

    # Queries: preserve everything the eval + generation step need.
    for q in raw:
        queries.append({
            "id": q["_id"],
            "type": q.get("type"),
            "question": q["question"],
            "answer": q["answer"],
            "answer_id": q.get("answer_id"),
            "supporting_facts": q.get("supporting_facts", []),
            "evidences": q.get("evidences", []),
            "evidences_id": q.get("evidences_id", []),
            "entity_ids": q.get("entity_ids"),
            # Helper: the 10 candidate context titles (for distractor-mode comparison
            # if we ever run that setting; open-retrieval doesn't use these).
            "context_titles": [t for t, _ in q["context"]],
        })

    return list(pages_by_title.values()), links, queries, stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="N=0 → full dev")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    if not DEV_PATH.exists():
        print(f"missing {DEV_PATH} — run scripts/fetch_twowiki.py first",
              file=sys.stderr)
        sys.exit(1)

    print(f"loading {DEV_PATH}...")
    with open(DEV_PATH) as f:
        raw = json.load(f)
    if args.sample and args.sample < len(raw):
        import random
        random.seed(42)
        raw = random.sample(raw, args.sample)
        print(f"  sampled {args.sample} questions (seed=42)")
    print(f"  {len(raw):,} questions")

    print(f"loading {ALIASES_PATH}...")
    aliases = load_aliases(ALIASES_PATH)
    print(f"  {len(aliases):,} Qid aliases")

    print("converting...")
    pages, links, queries, stats = convert(raw, aliases)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    pages_path = args.out_dir / "pages.jsonl"
    links_path = args.out_dir / "links.jsonl"
    queries_path = args.out_dir / "queries.jsonl"

    with open(pages_path, "w") as f:
        for p in pages:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(links_path, "w") as f:
        for link in links:
            f.write(json.dumps(link, ensure_ascii=False) + "\n")
    with open(queries_path, "w") as f:
        for q in queries:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    print()
    print(f"pages:   {len(pages):,} → {pages_path}")
    print(f"links:   {len(links):,} → {links_path}")
    print(f"queries: {len(queries):,} → {queries_path}")
    print()
    print(f"stats: {stats}")
    if stats["evidences_total"]:
        pct = 100 * stats["evidences_resolved"] / stats["evidences_total"]
        print(f"  evidence resolution rate: {pct:.1f}% "
              f"({stats['evidences_resolved']:,}/{stats['evidences_total']:,})")


if __name__ == "__main__":
    main()
