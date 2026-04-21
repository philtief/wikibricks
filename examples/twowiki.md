# 2WikiMultiHopQA Benchmark

How to reproduce the WikiBricks 2WikiMultiHopQA numbers end-to-end, using the official
eval v1.1 script against the full 12,576-question dev set.

## 0. Prerequisites

- A Databricks workspace with a SQL warehouse, a VS endpoint (reused: `wiki-vs-endpoint`),
  and access to Foundation Model Serving (Claude family).
- Databricks SDK on the client: `uv pip install -e .` from the repo root.
- Profile `fe-vm-agent-marketplace` in `~/.databrickscfg` (adjust to your workspace).

## 1. Fetch official assets

```bash
.venv/bin/python scripts/fetch_twowiki.py
```

Downloads:
- `data/twowiki/raw/dev.json` — 12,576 dev questions (with gold answer, supporting_facts, evidences, answer_id, evidences_id).
- `data/twowiki/raw/train.json` — 167,454 train questions (kept for potential future fine-tuning, not used by eval).
- `data/twowiki/raw/id_aliases.json` — Qid→alias map required by the eval's answer/evidence scoring.
- `vendor/2wikimultihop_evaluate_v1.1.py` — official evaluator, used verbatim.

## 2. Build WikiBricks seeds

```bash
.venv/bin/python scripts/build_twowiki_seed.py              # full
# or
.venv/bin/python scripts/build_twowiki_seed.py --sample 500  # smoke test
```

Produces:
- `src/wikibricks/seeds/twowiki/pages.jsonl` — one page per unique Wikipedia title seen in dev contexts (~55k pages).
- `src/wikibricks/seeds/twowiki/links.jsonl` — typed edges from Wikidata `evidences_id` triples (director, mother, spouse, country_of_citizenship, …).
- `src/wikibricks/seeds/twowiki/queries.jsonl` — one row per dev question with `_id`, `question`, `answer`, `answer_id`, `supporting_facts`, `evidences`, `evidences_id`, `type`.

## 3. Ingest + index

```bash
.venv/bin/python scripts/twowiki_01_setup.py      # schema + tables + MERGE
.venv/bin/python scripts/twowiki_02_vs_index.py   # VS DELTA_SYNC; waits for READY
```

Creates `agent_marketplace_catalog.wiki_2wiki.{pages,links,pages_index}`. Initial sync
for 55k paragraphs on `wiki-vs-endpoint` with `databricks-bge-large-en` takes ~20–30 min.

## 4. Retrieve + generate + evaluate

```bash
# Smoke test on 100 queries first
TWOWIKI_SAMPLE=100 .venv/bin/python scripts/twowiki_03_retrieve.py
TWOWIKI_SAMPLE=100 .venv/bin/python scripts/twowiki_04_generate.py
.venv/bin/python scripts/twowiki_05_evaluate.py

# Full run (12,576 queries × 3 modes)
.venv/bin/python scripts/twowiki_03_retrieve.py      # retrieval
.venv/bin/python scripts/twowiki_04_generate.py      # LLM answer/sp/evidence
.venv/bin/python scripts/twowiki_05_evaluate.py      # official evaluator
.venv/bin/python scripts/twowiki_06_render.py        # → twowiki_results.html
```

Env vars:
- `TWOWIKI_MODES=ANN` — restrict to a single mode (default: HYBRID,ANN,FULL_TEXT).
- `TWOWIKI_CONTEXT_K=5` — passages shown to the LLM (default 5).
- `TWOWIKI_MODEL=databricks-claude-sonnet-4-6` — swap to opus / gpt-5 for comparison.
- `TWOWIKI_WORKERS=10` — generation parallelism (FMAPI rate-limited).

## 5. Where results land

- `data/twowiki/metrics.json` — six official metrics per mode.
- `twowiki_results.html` — rendered view.
- `docs/twowiki_evaluation.md` — deep-dive analysis.

## Notes on methodology

- **Open-retrieval setting.** We index the union of all dev paragraphs (~55k unique
  titles) in one VS index. Each query retrieves from the full index — we do NOT use
  the distractor-setting's 10 candidate paragraphs per question. This is the harder
  and more realistic setting for WikiBricks' role as a wiki-store retriever.
- **Official eval v1.1.** Predictions are written in the exact schema the script
  expects: `{"answer": {qid: str}, "sp": {qid: [[title, idx], ...]}, "evidence": {qid: [[s, p, o], ...]}}`.
  No re-scoring, no custom metrics.
- **No training data touched.** WikiBricks uses an off-the-shelf encoder
  (`databricks-bge-large-en`). Numbers reflect what a Databricks user gets out of the
  box, not what fine-tuning on 2Wiki train would achieve.
