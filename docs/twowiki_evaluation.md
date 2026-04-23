# 2WikiMultiHopQA - WikiBricks Evaluation Report

This report documents a zero-shot, off-the-shelf retrieval + generation pipeline on
top of WikiBricks, evaluated on a fixed 350-query subset of the 12,576-question
2WikiMultiHopQA dev set. The evaluation exercises WikiBricks as infrastructure —
MERGE into Delta, DELTA_SYNC Vector Search, typed link graph — and confirms that
the retrieval layer produces valid, non-trivial predictions under the official v1.1
evaluator.

## Setup

- Fixed qid set: **350 queries** (same across variants), drawn from the dev set.
- Corpus: **54,957 Wikipedia paragraphs** - union of 2Wiki dev contexts.
- Index: single Delta + VS DELTA_SYNC on `databricks-bge-large-en` (`wiki-vs-endpoint`).
- Generation: zero-shot, temperature 0. No fine-tuning, no reranker, no iterative retrieval.
- Scored by the official v1.1 evaluator (vendored) on a gold subset matching the
  350 predicted qids.
- Links table: 9,354 typed Wikidata-derived edges available for downstream graph
  expansion.

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

### 1. Model and context size dominate; retrieval mode is near-noise
- Sonnet → +8.9 Joint F1 over the Haiku baseline.
- K=5 → K=10 → +3.4 Joint F1 with Haiku, +1.9 Joint F1 with Sonnet.
- ANN vs. HYBRID with Sonnet: within noise (−0.2 to +1.7 depending on K).
- The constrained-evidence prompt (variant D) is not a win at this setting.

### 2. Stacked levers are not additive at the top
The top four Sonnet variants land within ~2 Joint F1 points. HYBRID + K=10 edges
out ANN + K=10 by 0.8; retrieval-mode differences that matter at Haiku level
compress once the generator is strong enough.

### 3. Answer and Support F1 are the easy wins; Evidence F1 is the ceiling
Joint F1 is multiplicative across the three sub-metrics. Exact-triple evidence
matching against Wikidata surface conventions is the binding constraint for any
zero-shot pipeline, independent of the retrieval layer.

## Scope

This evaluation measures a zero-shot, off-the-shelf pipeline — no fine-tuning, no
reranker, no iterative retrieval, no task-tuned heads. It is a ranking of cheap
levers (model, K, retrieval mode, prompt) for anyone layering a Q&A system on top
of a WikiBricks wiki, not a comparison against task-tuned SOTA systems that
combine fine-tuned retrievers, cross-encoder rerankers, and multi-hop rewriting.

WikiBricks' proposition is a queryable, auditable wiki over Delta + Vector Search
with a typed link graph — the infrastructure underneath a Q&A system, not the Q&A
system itself.

## Recommended configuration

- **Model:** `databricks-claude-sonnet-4-6`
- **Retrieval:** HYBRID (marginal over ANN with Sonnet)
- **Context K:** 10
- **Prompt:** base (not constrained-evidence)
- **Batch size:** 250, Delta-table checkpointed

## Methodology notes

- **Corpus.** The index covers the union of titles referenced by the dev set
  contexts, aligned with the official evaluator's retrieval setting.
- **LLM determinism.** Temperature 0; numbers are reproducible within a model
  version.
- **Evidence metric.** Evidence F1 requires exact triple matches against
  Wikidata surface conventions and sets the floor for Joint F1 under any
  zero-shot pipeline.
