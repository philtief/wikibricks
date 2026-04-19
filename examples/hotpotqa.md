# HotpotQA Benchmark

How to reproduce the WikiBricks HotpotQA numbers.

## 1. Fetch and transform the corpus (local, build-time)

```bash
python scripts/fetch_hotpot.py
python scripts/build_hotpot_seed.py
```

Produces:
- `src/wikibricks/seeds/hotpot/pages.jsonl` — one page per Wikipedia article referenced by the dev set (~25 k pages)
- `src/wikibricks/seeds/hotpot/links.jsonl` — `supports` links between co-referenced pages
- `src/wikibricks/seeds/hotpot/queries.jsonl` — 7,405 dev questions with relevant-page labels

## 2. Deploy a HotpotQA WikiBricks instance

```bash
databricks bundle deploy --target dev \
  --var="seed_domain=hotpot" \
  --var="schema=wiki_hotpot"

databricks bundle run deploy_wiki_store --target dev
```

Then, from a Databricks notebook:

```python
from wikibricks import WikiClient
wiki = WikiClient(warehouse_id="41754a8563a43a49")
wiki.bulk_write_pages(
    "/Workspace/Shared/wikibricks/hotpot/pages.jsonl",
    source_tag="hotpot-dev-distractor",
)
```

Trigger VS sync; wait for READY (~20–40 min for 25 k pages).

## 3. Run the benchmark

`notebooks/benchmark_hotpot.py` — computes recall@2, recall@10, MRR, and supporting-fact F1 across HYBRID, ANN, and FULL_TEXT. The link-graph ablation follows `supports` edges from the first retrieved page to measure the cross-reference uplift.

Acceptance: HYBRID `recall@10 ≥ 0.70`, `MRR ≥ 0.35`. Any competent dense retriever should clear this; HotpotQA's BM25 baseline sits near this line.

## 4. Where results land

- `/Workspace/Shared/wikibricks/hotpot/benchmark_results.json`
- Republished in the README's benchmark table.
