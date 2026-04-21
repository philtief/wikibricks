# 2WikiMultiHopQA - WikiBricks Evaluation Report

**Status:** preliminary ablation on a fixed 350-query subset of the 12,576-question
dev set. **Not a leaderboard submission.** Joint F1 sits around the 2020 paper's own
open-retrieval baseline (~20); modern 2024-2025 open-retrieval SOTA is 50-65. This
report documents what a zero-shot, off-the-shelf retrieval + generation pipeline on
top of WikiBricks gets you - nothing more. WikiBricks' value is the infrastructure
(MERGE into Delta, DELTA_SYNC Vector Search, typed link graph); this evaluation is a
sanity check that the retrieval layer produces valid, non-trivial predictions.

## Setup

- Fixed qid set: **350 queries** (same across variants), drawn from the dev set.
- Corpus: **54,957 Wikipedia paragraphs** - union of 2Wiki dev contexts.
- Index: single Delta + VS DELTA_SYNC on `databricks-bge-large-en` (`wiki-vs-endpoint`).
- Generation: zero-shot, temperature 0. No fine-tuning, no reranker, no iterative retrieval.
- Scored by the official v1.1 evaluator (vendored) on a gold subset matching the
  350 predicted qids.
- Links table: 9,354 typed Wikidata-derived edges present, not yet exploited by any
  variant.

## Full 8-variant results

| Variant            | Retrieval | Model      |  K | Prompt | Ans F1 | Sup F1 | Evi F1 | **Joint F1** |
|--------------------|-----------|------------|---:|--------|-------:|-------:|-------:|-------------:|
| A_baseline         | HYBRID    | haiku-4-5  |  5 | base   |  44.1  |  53.1  |  26.9  |     10.4     |
| B_k10              | HYBRID    | haiku-4-5  | 10 | base   |  51.0  |  59.5  |  29.4  |     13.8     |
| C_sonnet           | HYBRID    | sonnet-4-6 |  5 | base   |  65.7  |  64.3  |  39.1  |     19.3     |
| D_cevi             | HYBRID    | haiku-4-5  |  5 | cevi   |  44.1  |  58.7  |  29.9  |      9.3     |
| E_ann              | ANN       | haiku-4-5  |  5 | base   |  48.8  |  56.2  |  26.8  |     11.7     |
| **F_sonnet_k10**   | **HYBRID** | **sonnet-4-6** | **10** | **base** | **66.7** | **68.4** | **40.9** | **21.2** |
| G_sonnet_ann       | ANN       | sonnet-4-6 |  5 | base   |  65.8  |  66.5  |  40.0  |     19.5     |
| H_sonnet_ann_k10   | ANN       | sonnet-4-6 | 10 | base   |  64.7  |  68.8  |  41.5  |     20.4     |

Ordered by Joint F1 (top 4 Sonnet variants within ~2 points of each other):

1. **F (Sonnet + HYBRID + K=10)** - 21.2
2. H (Sonnet + ANN + K=10) - 20.4
3. G (Sonnet + ANN + K=5) - 19.5
4. C (Sonnet + HYBRID + K=5) - 19.3

## Findings

### 1. Triple-stack (Sonnet + ANN + K=10) did not produce the best result
- Expected: stacking the three positive levers (model, retrieval mode, K) would
  additively improve.
- Actual: H_sonnet_ann_k10 = 20.4 Joint F1, **0.8 below F_sonnet_k10 (21.2)**.
- Interpretation: with Sonnet, the model dominates; retrieval-mode differences that
  mattered at Haiku level (ANN +1.3 over HYBRID) disappear or slightly reverse.
  Sonnet is strong enough to make good use of HYBRID passages; ANN's specific
  neighborhood is not meaningfully more useful.

### 2. The real levers are model and context size, not retrieval mode
- Sonnet → +8.9 Joint F1 over Haiku baseline.
- K=5 → K=10 → +3.4 Joint F1 with Haiku, +1.9 Joint F1 with Sonnet.
- ANN vs. HYBRID with Sonnet: **noise level** (−0.2 to +1.7 depending on K).
- Constrained-evidence prompt (D): **net negative** (−1.1 Joint F1). Drop.

### 3. Answer F1 and Support F1 are respectable; Evidence F1 is the ceiling
- Joint F1 = Ans × Sup × Evi (multiplicative). Weak Evi drags Joint down.
- Zero-shot LLMs cannot reliably hit exact Wikidata surface forms in
  `(subject, predicate, object)` triples. This is a structural limitation, not a
  retrieval one. A task-tuned evidence head would materially move Joint F1 -
  outside the "WikiBricks out of the box" framing.

## Honest benchmark positioning

| Benchmark (Joint F1, dev, official v1.1 eval)       | Setting            | Joint F1 |
|------------------------------------------------------|--------------------|---------:|
| Ho et al. 2020 (original paper)                      | open-retrieval     | ~20      |
| **F_sonnet_k10 (this work)**                         | **open-retrieval** | **21.2** |
| 2024-2025 RAG SOTA (HyperRAG, A2RAG, FutureMind)     | open-retrieval     | 50-65    |
| Ho et al. 2020                                       | distractor         | ~40      |
| FE2H (2023, task-tuned)                              | distractor         | ~65      |
| 2024-2025 full SOTA                                  | distractor         | 70-75    |

We sit at a five-year-old open-retrieval baseline. Modern methods are 2.5-3× better
on Joint F1. They get there by combining:

- Task-tuned retrievers (MDR, ColBERT-Hop trained on HotpotQA/2Wiki).
- Iterative multi-hop retrieval (retrieve → rewrite → retrieve).
- Cross-encoder rerankers.
- Fine-tuned answer + evidence heads.

None of those four are in scope for WikiBricks-as-a-product. WikiBricks' proposition
is a queryable, auditable wiki over Delta + VS, not a multi-hop QA system.

## What this evaluation is / is not

**Is:**
- Sanity check that the pipeline produces valid v1.1-schema predictions.
- A ranking of cheap levers (model, K, retrieval mode, prompt) for anyone layering
  a Q&A system on top of a WikiBricks wiki.
- A defensible ballpark on what Sonnet + VS DELTA_SYNC gets you zero-shot.

**Is not:**
- A leaderboard submission. Any public table placing our Joint F1 next to
  2024-2025 SOTA must carry the caveats above inline.
- A statement about WikiBricks' production value. That story is the infrastructure:
  MERGE into Delta, DELTA_SYNC VS index in ~20 min, 9,354 typed edges in a links
  table, resumable checkpoints.

## Recommended configuration (if running the full dev set)

- **Model:** `databricks-claude-sonnet-4-6`
- **Retrieval:** HYBRID (marginal over ANN with Sonnet)
- **Context K:** 10
- **Prompt:** base (not constrained-evidence)
- **Batch size:** 250, Delta-table checkpointed
- **Wall clock:** ~6 h for 12,576 queries at ~0.6 q/s steady with Sonnet
- **Expected Joint F1:** 20-23 (within noise of the 350-qid 21.2)

## Threats to validity

- **Corpus restricted to dev contexts.** We ingested only titles seen in dev
  contexts, not all of Wikipedia. 2Wiki's gold passages are always in our index by
  construction - the harder test ("retrieve the right page from all of Wikipedia") is
  not covered here. A production WikiBricks deployment would index the domain corpus
  fully and retrieve from it.
- **LLM variance.** Claude is deterministic at temperature 0, but identical prompts
  across two runs can drift slightly as Databricks FMAPI model versions change.
  Numbers are reproducible within a release but not across major model versions.
- **Evidence prediction is the hardest task.** The LLM must emit triples in
  `(subj, pred, obj)` form matching Wikidata surface conventions. It is the floor
  of the Joint F1 number.
