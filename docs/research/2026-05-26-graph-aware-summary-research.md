# Research notes — graph-aware auto_summary

**Date:** 2026-05-26
**Question:** How should `auto_summary` evolve so it works with WikiBricks' edges + nodes + Databricks primitives, not just standalone retrieval?

## Headline findings

### 1. Pattern selection: HippoRAG / RAPTOR > GraphRAG

| Pattern | Fit for session-style content | Notes |
|---|---|---|
| Microsoft GraphRAG ([arXiv:2404.16130](https://arxiv.org/abs/2404.16130)) | Overkill at our scale | Brutal extraction cost; only pays off above ~1M tokens of corpus with broad sensemaking queries |
| HippoRAG ([arXiv:2405.14831](https://arxiv.org/abs/2405.14831)) | **Strongest match** | Passage-entity graph, query-time Personalized PageRank from matched entities. HippoRAG 2 beats GraphRAG by ~20% on multi-hop at a fraction of indexing cost |
| RAPTOR ([arXiv:2401.18059](https://arxiv.org/abs/2401.18059)) | Already partially live | `promote_topics.py` does community-aware abstractive summaries — RAPTOR's tier-2 |
| LightRAG ([arXiv:2410.05779](https://arxiv.org/abs/2410.05779)) | Useful for incremental story | Dual-level (entity-low + topic-high), good incremental-update semantics |

Survey verdict ([arXiv:2503.04338](https://arxiv.org/pdf/2503.04338), [arXiv:2506.05690](https://arxiv.org/html/2506.05690v3)): *"for query-focused summarization on a per-doc basis, vanilla RAG + RAPTOR-style hierarchy is the cost-efficient sweet spot."* Don't replicate GraphRAG.

### 2. Structured-envelope summary (the key design move)

One LLM call returns prose + structured fields. With Anthropic Structured Outputs GA (Feb 2026, see [structured outputs docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)) the JSON schema is enforced — no parsing roulette.

Target envelope:

```json
{
  "summary_markdown": "## Intent ...",
  "entities": [
    {"name": "Allianz CH", "type": "customer"},
    {"name": "stripe.Webhook.construct_event", "type": "identifier"}
  ],
  "tags": ["customer:azch", "topic:webhook-signing", "domain:fintech"],
  "edges": [
    {
      "target_path": "topics/stripe-webhooks",
      "link_type": "cites",
      "evidence": "uses stripe.Webhook.construct_event from the prior page"
    }
  ]
}
```

### 3. Anti-hallucination: candidate-neighbor injection

The canonical edge-extraction failure mode is the LLM inventing target paths that don't exist (LLM-KG construction survey [arXiv:2510.20345](https://arxiv.org/html/2510.20345v1)). Mitigation that works:

- **Before the LLM call**, query VS for the top-10 most similar existing pages to the raw session text.
- **Inject those candidates** into the prompt: `target_path` MUST come from this list.
- **Reject any edge** whose target isn't in the candidate set (post-validation).

### 4. content_text shape — what VS should embed

Currently (v0.7.9): `content_text = summary_markdown + "\n\n## Raw intent\n" + first_prompt[:2000]`.

The first_prompt tail restored keyword density but is conversational noise (user thinking out loud). Better: structured concatenation that VS cosine + BM25 both benefit from.

Target shape (v0.7.11):

```
{title}

{summary_markdown}

Tags: customer:azch topic:webhook-signing domain:fintech
Entities: Allianz CH, stripe.Webhook.construct_event, payments/webhook.py
```

Same total length as v0.7.9 (~3-4k chars), denser keyword payload, structured for both legs of HYBRID.

### 5. Edge staging, not direct writes

LLM-proposed edges land in a **new `edges_proposed` Delta table** with columns `(source_path, target_path, link_type, evidence, confidence, created_by, created_at, status)`. Nightly job promotes `status='confirmed'` rows to `links`. Keeps the LLM's hallucinations quarantined.

Existing `client.propose_edges` writes to `links` directly with low confidence — this is different: it stages edges with the explicit purpose of LLM-emit-then-judge-then-promote.

### 6. Tags as a separate column (Databricks-specific)

Databricks Vector Search supports **only one `embedding_source_columns`** per index, so tags can't be embedded separately. But VS DOES support metadata filters on any Delta column at query time ([docs](https://docs.databricks.com/aws/en/vector-search/vector-search)). So tags belong in:
- `pages.tags` (existing ARRAY column — already filtered today)
- The structured envelope emits tags → existing append-tags path

For Genie integration ([Genie best practices](https://docs.databricks.com/aws/en/genie/best-practices)), keep tags + entities as STRUCTURED columns / JSON keys so Genie can filter on them in NL queries.

### 7. Hierarchical summaries — keep the existing division of labor

| Tier | Writer | Status |
|---|---|---|
| Page-level (one summary per session) | `auto_summary` at flush | This plan extends this tier |
| Topic-level (community synthesis) | `promote_topics.py` nightly | Already live since 0.7.6 |
| Domain-level rollups | quarterly job | Not built; not needed at current scale |

The handoff is the typed edges emitted at flush time — they're what lets Leiden cluster sessions meaningfully for `promote_topics.py`.

### 8. Graph-aware retrieval (deferred — separate plan)

The research recommends:
- **PPR rerank** seeded at query-matched entities (the single biggest signal — HippoRAG)
- **1-hop expansion** on `cites` / `extends` edges
- **Mixed scoring**: `final = α·cosine + β·PPR_from_query + γ·log(global_PR)`

These are retrieval-side changes. They benefit from this plan's structured-envelope output but don't require it. Keep them out of v0.7.11 scope — they're a separate v0.7.12+ workstream.

### 9. Concrete recommendations for v0.7.11

| Component | Change |
|---|---|
| `auto_summary` LLM call | Single structured-output call returning the envelope |
| `auto_title` + `auto_tag` modules | **Consolidate into the envelope** — title becomes `summary_markdown` first line, tags come from the envelope's `tags` field. Title module + tag module become thin wrappers that call the envelope path |
| `build_content_text_override` | Drop `first_prompt[:2000]`. New shape: `title + summary + tags + entities` |
| `pages.content` JSON | Add `entities` key |
| New table `edges_proposed` | Staging for LLM-proposed edges |
| `WikiClient.bulk_propose_edges` | New method (writes to staging) |
| Nightly promotion job | Promotes `status='confirmed'` (auto-approve if target exists + evidence non-empty) — extends `wiki_curate.py` |
| `[auto_summary] mode` TOML key | "envelope" (new) vs "intent_tail" (v0.7.9, default for backward compat) |

## Sources

| Source | Citation |
|---|---|
| Microsoft GraphRAG | Edge et al., [arXiv:2404.16130](https://arxiv.org/abs/2404.16130) |
| HippoRAG | Jiménez Gutiérrez et al., NeurIPS'24, [arXiv:2405.14831](https://arxiv.org/abs/2405.14831) |
| RAPTOR | Sarthi et al., ICLR'24, [arXiv:2401.18059](https://arxiv.org/abs/2401.18059) |
| LightRAG | Guo et al., [arXiv:2410.05779](https://arxiv.org/abs/2410.05779) |
| RAG vs GraphRAG evaluation | [arXiv:2502.11371](https://arxiv.org/abs/2502.11371) |
| Unbiased GraphRAG evaluation | [arXiv:2506.06331](https://arxiv.org/html/2506.06331v1) |
| Unified Graph-RAG framework | [arXiv:2503.04338](https://arxiv.org/pdf/2503.04338) |
| When to use Graphs in RAG | [arXiv:2506.05690](https://arxiv.org/html/2506.05690v3) |
| Mixture-of-PageRanks | [arXiv:2412.06078](https://arxiv.org/html/2412.06078v1) |
| LLM-KG construction survey | [arXiv:2510.20345](https://arxiv.org/html/2510.20345v1) |
| Efficient KG construction for RAG | [arXiv:2507.03226](https://arxiv.org/html/2507.03226v2) |
| Personalize Before Retrieve | [arXiv:2510.08935](https://arxiv.org/abs/2510.08935) |
| Anthropic Structured Outputs | [platform.claude.com docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) |
| Databricks Vector Search | [docs.databricks.com](https://docs.databricks.com/aws/en/vector-search/vector-search) |
| Genie best practices | [docs.databricks.com/aws/en/genie/best-practices](https://docs.databricks.com/aws/en/genie/best-practices) |
