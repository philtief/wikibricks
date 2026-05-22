# Research notes — recorder summary-first write path

**Date:** 2026-05-22
**Plan:** `docs/superpowers/plans/2026-05-22-recorder-summary-first.md`
**Question:** What is the best contract for compressing a Claude Code session into an embedding-friendly summary so Vector Search retrieves the right past session by intent and outcome instead of by raw-transcript noise?

## Findings table

| # | Finding | Source | How it shapes the plan |
|---|---|---|---|
| 1 | MemGPT keeps **raw chunks in external storage + summary in active context** — nothing is destroyed | [arXiv:2310.08560](https://arxiv.org/pdf/2310.08560) | Keep `content.body` = raw transcript; `content_text` = dense summary |
| 2 | RAPTOR recursively clusters + summarizes text into a tree, retrieving from any level. +20% QuALITY when paired with GPT-4. Base chunks ~100 tokens | [arXiv:2401.18059](https://arxiv.org/pdf/2401.18059) | Validates summary-as-embedded-unit; v1 applies this at session granularity only |
| 3 | Dense X / Proposition Retrieval — atomic self-contained propositions beat passages and sentences across 5 datasets / 6 retrievers | [arXiv:2312.06648](https://arxiv.org/abs/2312.06648) | Summary should be bullet propositions, not flowing prose |
| 4 | Headers + brief per-section summaries lift retrieval quality and reduce hallucinations | [AWS RAG best practices](https://docs.aws.amazon.com/prescriptive-guidance/latest/writing-best-practices-rag/best-practices.html) | Use `## Intent / ## Approach / ## Outcome / ## Artifacts` headers |
| 5 | Chunk-size empirics: 64–128 tokens win for factoid recall, 512–1024 for reasoning. NAACL 2025 fixed 200-word chunks match semantic chunking | [arXiv:2505.21700](https://arxiv.org/pdf/2505.21700) | Target 150–300 tokens for the structured summary |
| 6 | Anthropic memory tool (`memory_20250818`) is a file-directory contract; recommends progress log + feature checklist pattern. Compaction "compact-2026-01-12" summarizes older context — "high-level facts central to the task usually survive; obscure specifics typically don't" | [Memory tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool), [Compaction cookbook](https://platform.claude.com/cookbook/tool-use-automatic-context-compaction) | Confirms write-time summarization is the right granularity |
| 7 | LangMem distinguishes semantic (facts), **episodic** (full interaction + outcome + why it worked), and procedural (prompt updates) memory | [LangMem concepts](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/) | Session pages are *episodic* — summary schema maps directly to Intent + Approach + Outcome |
| 8 | Mem0 turns conversations into ADD-only facts then hash-dedups — lossy by design | [Mem0 docs](https://docs.mem0.ai/core-concepts/memory-evaluation) | Useful contrast — verbatim+summary (our plan) is more recoverable than extract-only |
| 9 | Zep keeps episode nodes (raw) + semantic entity nodes + edges in a temporal KG. Practical: "include the last 4-6 messages when calling your LLM" | [Zep docs](https://help.getzep.com/v2/memory), [Zep KG paper arXiv:2501.13956](https://arxiv.org/pdf/2501.13956) | Summary = long-term, recent raw events = short-term — fits the parent/body split we already have |
| 10 | Karpathy LLM Wiki gist is deliberately abstract — mentions `index.md` (one-line summaries) and `log.md` (append-only). No prescribed page schema | [gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | Our four-section structured-summary schema is original to WikiBricks |
| 11 | Haiku 4.5 pricing: $1 / $5 per 1M input/output tokens. Batch: $0.50 / $2.50. Released Oct 15, 2025, 200K context | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing), [Haiku 4.5 announcement](https://www.anthropic.com/news/claude-haiku-4-5) | One 30k-input / 500-output call = $0.0325/session standard, $0.016 batch — negligible |
| 12 | Typical Claude Code session: turn-1 ~15K tokens, turn-15 ~100K, turn-30 ~167K then compaction. Avg session 30–80K tokens | [Shipyard Claude Code tokens](https://shipyard.build/blog/claude-code-tokens/) | Justifies the 12K-char input sample cap |
| 13 | Claude Haiku 4.5 supports **GA structured outputs** as of Feb 4, 2026 with strict JSON schema enforcement via constrained decoding | [Anthropic structured outputs](https://claude.com/blog/structured-outputs-on-the-claude-developer-platform), [Platform docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) | Phase-2 follow-up: consolidate title + tags + summary into one structured call. Phase-1 (this plan) keeps three independent calls. |
| 14 | Entity-coverage validation cheaply catches hallucinations: NER on summary, set-diff against transcript NER, regenerate if non-zero | [arXiv:2207.02263](https://arxiv.org/pdf/2207.02263) | Defer to v2; v1 relies on strict system prompt ("every claim must trace to a verbatim span") |
| 15 | Skip summarization under ~2K tokens / 4 user turns — "Keep-It-All" pattern: "perfect recall without summarization … zero added latency, no summary drift" | [Memory Optimization Strategies survey](https://medium.com/@nirdiamant21/memory-optimization-strategies-in-ai-agents-1f75f8180d54) | `MIN_CHARS_FOR_SUMMARY = 2000` short-circuit in `_should_summarize` |

## Concrete contract derived from the research

```
SessionPage {
  title: str (≤120 chars, already done by auto_title.py)
  tags: list[str] (3-7, already done by auto_tag.py)
  content.summary: str (NEW — dense Markdown, 150–300 tokens, Intent/Approach/Outcome/Artifacts)
  content.body: str (unchanged — raw transcript timeline)
  content_text_override: str (NEW — same as content.summary; this is what VS embeds)
}
```

VS embeds `content_text_override`. The agent calls `fn_wiki_read` (or its parent) to retrieve title + summary + body when it needs the raw events. The page itself remains human-readable in the Streamlit app via `content.body`.

## Why v1 keeps three independent LLM calls

The plan ships three separate calls (`auto_title`, `auto_tag`, `auto_summary`) instead of consolidating into one structured-output call. Reasons:

1. Each module has a small, focused contract; failures are isolated. If the summary call hangs or rate-limits, title + tags still happen.
2. Cost is already negligible — three Haiku 4.5 calls per session ≈ $0.05; not worth the rewrite.
3. The structured-output GA is recent (Feb 4 2026); not yet exercised on Databricks Foundation Model serving. Consolidation can wait until we confirm the Databricks-hosted Haiku endpoint honors the same `output_config.format` contract as the direct Anthropic platform.

A Phase-2 follow-up will consolidate to one call once Phase-1 retrieval-quality numbers are in.

## What v2 should add

- **Entity-coverage validation** (finding 14): cheap post-validation; flag pages whose summary references entities not in the transcript.
- **Phase-2 consolidated structured-output call** (finding 13): one Haiku call returns `{title, tags, summary_markdown}`, drops the three-module separation.
- **Proactive segregate for huge sessions** (finding 1): for sessions > some threshold, write a parent page + chunk children at flush time so the body field itself stays compact. The existing nightly segregate task handles this reactively today; doing it at write time saves a curate-job pass.
- **Cross-encoder reranker** at search time (finding 5 indirectly): blend `databricks-bge-reranker` over the top-N VS hits.

## Sources

1. [Anthropic Memory tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)
2. [Anthropic context compaction cookbook](https://platform.claude.com/cookbook/tool-use-automatic-context-compaction)
3. [MemGPT paper (arXiv:2310.08560)](https://arxiv.org/pdf/2310.08560)
4. [Mem0 docs](https://docs.mem0.ai/core-concepts/memory-evaluation)
5. [LangMem conceptual guide](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)
6. [Zep KG paper (arXiv:2501.13956)](https://arxiv.org/pdf/2501.13956)
7. [Karpathy LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
8. [RAPTOR (arXiv:2401.18059)](https://arxiv.org/pdf/2401.18059)
9. [Dense X / Proposition Retrieval (arXiv:2312.06648)](https://arxiv.org/abs/2312.06648)
10. [Rethinking Chunk Size (arXiv:2505.21700)](https://arxiv.org/pdf/2505.21700)
11. [Claude Haiku 4.5 pricing](https://platform.claude.com/docs/en/about-claude/pricing)
12. [Haiku 4.5 announcement](https://www.anthropic.com/news/claude-haiku-4-5)
13. [Structured outputs on Claude Developer Platform](https://claude.com/blog/structured-outputs-on-the-claude-developer-platform)
14. [Entity Coverage Control (arXiv:2207.02263)](https://arxiv.org/pdf/2207.02263)
15. [Shipyard — Claude Code token usage](https://shipyard.build/blog/claude-code-tokens/)
16. [Memory Optimization Strategies in AI Agents](https://medium.com/@nirdiamant21/memory-optimization-strategies-in-ai-agents-1f75f8180d54)
