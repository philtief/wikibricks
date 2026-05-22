# Retrieval-quality eval plan — recorder summary-first (Task 11)

**Status:** Deferred to v0.7.9 decision window. 0.7.8 ships opt-in either
way; this eval decides whether to flip the default to enabled.

## Why deferred

The smoke test (`2026-05-22-summary-first-smoke-test.md`) confirmed the
mechanism end-to-end: LLM output is structured + citation-faithful, the
content_text override applies correctly, body is preserved. Default is
OFF in v0.7.8, so no production risk while the eval runs.

A proper A/B retrieval-quality eval needs:

1. A controlled set of paired pages — same content, two paths, one
   with override, one without
2. VS sync to land both pages in the index (TRIGGERED pipeline, ~30s
   per `sync_index()`)
3. Queries that reasonably match the intent (paraphrased, not verbatim)
4. Stable scoring window — VS scores fluctuate between syncs

That's a focused 1–2 hour session, better done deliberately than
shoehorned into the v0.7.8 ship.

## Eval design (when run)

**Seed corpus.** 10 synthetic Claude Code session states across distinct
topics:

1. "refactor payments to verify Stripe webhook signatures"
2. "add a Lakeflow Job that backfills Q1 invoices"
3. "fix the React hook that re-renders on every keystroke"
4. "set up auth via Databricks OBO for a FastAPI app"
5. "investigate slow queries on the orders fact table"
6. "migrate Snowflake stored procs to UC SQL functions"
7. "wire up Vector Search HYBRID retrieval in a notebook"
8. "package and publish a Python wheel from a uv project"
9. "diagnose Lakebase psycopg2 connection-pool exhaustion"
10. "build a Streamlit dashboard backed by a Genie Space"

For each, ~3000 chars of plausible transcript + tool histogram.

**A/B writes.** Each session is written twice:

- **Arm A (control):** `eval/concat/<topic>` — default
  `content_text = concat(summary, body)` path
- **Arm B (treatment):** `eval/summary/<topic>` — same content,
  `content_text_override = generate_summary(state)` dense summary

`sync_index()` after the batch.

**Queries.** For each topic, generate two paraphrased queries via Haiku
4.5: one intent-focused ("how did we verify Stripe signatures?") and one
artifact-focused ("which file did we change for the Stripe webhook?").
Run `fn_wiki_search(query, num_results=10)` for each.

**Metrics.**

- recall@1, recall@3, recall@5, recall@10 per arm
- Mean rank of the matching page per arm
- Wins / ties / losses (count of topics where Arm B beats Arm A on rank)
- Per-topic ranks logged in a CSV so failure modes are inspectable

**Decision rule.**

- **Flip default to ON in v0.7.9:** recall@5 improvement ≥ 10pp AND
  Arm B wins on ≥ 70% of topics
- **Keep opt-in, iterate prompt:** anything less. Document where Arm B
  loses and tighten the system prompt accordingly. Likely candidates:
  add a few-shot example; tighten the propositions constraint; surface
  more tool-output detail in the summary.

## Implementation sketch

`scripts/eval_summary_first_recall.py`:

```python
def main():
    seed_topics = load_yaml("docs/research/eval_seeds.yaml")
    for topic in seed_topics:
        state = build_state(topic)
        summary = generate_summary(state, {"enabled": True}, ws)
        write_page(f"eval/concat/{topic.slug}", ..., content_text_override=None)
        write_page(f"eval/summary/{topic.slug}", ..., content_text_override=summary)
    client.sync_index()
    time.sleep(30)
    for topic in seed_topics:
        for query in topic.paraphrases:
            hits = client.search(query, num_results=10)
            rank_a = rank_of(hits, f"eval/concat/{topic.slug}")
            rank_b = rank_of(hits, f"eval/summary/{topic.slug}")
            write_csv_row(topic.slug, query, rank_a, rank_b)
    print_summary_metrics()
```

Total runtime estimate: ~5 min (10 topics × 2 writes × ~5s each + 30s
VS sync + 20 queries).

## Owner / next step

When the user has a free 1–2 hour window for v0.7.9 prep, run
`scripts/eval_summary_first_recall.py --output docs/research/<date>-eval.csv`
and append numbers to this file. Then either:

- Open PR to flip the `is_enabled` default in `auto_summary.py`, or
- Open PR to update the system prompt with the eval-suggested
  improvements.
