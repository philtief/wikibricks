# WikiBricks agent schema

The wiki is a persistent, compounding artifact. Raw sources and recorded
sessions are evidence. Curated wiki pages are the maintained knowledge layer.
Recording a session does not by itself update the curated wiki.

The agent owns the wiki layer. It incrementally creates and revises pages,
maintains cross-references, records contradictions, and files useful answers
back into the wiki. The human chooses sources and directs the analysis.

At the start of every task that may have relevant history, call `wiki_search`
automatically with the user, account, project, or topic. Read the best relevant
pages before answering. Do this without asking the user to run a command or
manage WikiBricks. If no useful result exists, continue normally.

## Knowledge layers

1. **Raw sources and sessions:** immutable source material and ordered session
   events. Do not rewrite source evidence.
2. **Wiki pages:** summaries, entities, concepts, comparisons, guides, and
   syntheses that integrate knowledge from the source layer.
3. **This schema:** the conventions and workflows that keep the wiki useful.

## Page types

| Type | Use |
|---|---|
| `entity` | One product, person, service, organization, or component |
| `concept` | A pattern, mechanism, or principle |
| `synthesis` | An answer or conclusion drawn from several pages or sources |
| `comparison` | Alternatives with evidence and trade-offs |

## Paths and content

Every page path contains a slash. Use lowercase, hyphen-separated names and a
maximum depth of four levels.

| Prefix | Use |
|---|---|
| `topics/` | Entities and concepts |
| `guides/` | Procedures and runbooks |
| `comparisons/` | Side-by-side analysis |
| `synthesis/` | Answers filed from agent work |
| `_meta/` | Generated index and maintenance pages |

Each page has a `page_type`, a short `summary`, and a Markdown `body`. Keep
claims specific. Preserve source paths or source IDs when available. Reuse
existing tags and use lowercase, hyphen-separated tag names.

## Cross-references

Use typed links when the relationship is known:

| Link type | Meaning |
|---|---|
| `related` | The pages cover connected subjects |
| `extends` | The source page adds detail to the target |
| `contradicts` | The pages contain conflicting claims |
| `supersedes` | The source replaces an older target |
| `cites` | The source page derives a claim from the target |

Do not create a link only because two pages share a keyword. The relationship
must help a later reader navigate or evaluate the knowledge.

## Ingest workflow

When new source material matters:

1. Search the existing wiki before writing.
2. Read the relevant pages and the source evidence.
3. Create or update the smallest set of wiki pages that integrates the new
   facts.
4. Add source provenance and useful cross-references.
5. Update a summary when the evidence changes its conclusion.
6. Mark a contradiction instead of silently choosing one claim.

One source can update several pages. Do not save only a source summary if the
source changes an existing entity, concept, comparison, or synthesis.

## Query workflow

Search the wiki first, then read the best pages and their linked evidence.
Answer with page-path citations when possible. If the answer adds a reusable
comparison, connection, or synthesis, file it with `wiki_promote_answer` or
`wiki_write_page`. Link the new page to its sources.

Do not promote routine chat, temporary debugging output, or an answer that
only repeats one existing page.

## Lint workflow

Periodically inspect the wiki for:

- contradictory claims without a `contradicts` link;
- stale pages that newer evidence supersedes;
- orphan pages and missing cross-references;
- duplicate pages or overlapping syntheses;
- important concepts that have no page;
- claims without source provenance;
- unanswered questions that need new sources.

Fix deterministic problems locally. Keep uncertain changes as explicit agent
proposals or review tasks. Materialize `_meta/index` after a large ingest or
maintenance pass so a person can browse the current wiki. The index lists
curated wiki pages only. Raw sessions and remote archives remain evidence.
