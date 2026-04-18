# WikiBricks Schema

Living schema definition for the WikiBricks wiki memory system.
Agents and maintenance jobs read this file to understand wiki conventions.

## Page Types

| Type | When to Use |
|------|-------------|
| `entity` | A single, well-defined thing: a product, a service, a person, a system component. |
| `concept` | An idea, pattern, or principle that explains *how* or *why* something works. |
| `synthesis` | A combined answer that draws on multiple sources or pages. Auto-promoted chat answers use this type. |
| `comparison` | Side-by-side analysis of two or more alternatives, with trade-offs. |

## Path Conventions

Paths use forward slashes to form a hierarchy. Every page must have at least one slash.

| Prefix | Purpose |
|--------|---------|
| `topics/` | Core knowledge pages. Use sub-paths for depth: `topics/area/sub-topic`. |
| `guides/` | How-to and step-by-step instructions. |
| `comparisons/` | Comparison pages. |
| `promoted/` | Auto-promoted chat answers. Path is slugified from the original query. |
| `_meta/` | System-managed pages (index, maintenance log). Never edit manually. |

Rules:
- Lowercase, hyphen-separated: `topics/delta-lake`, not `Topics/Delta Lake`.
- No trailing slashes.
- Max depth: 4 levels.

## Content Structure

Every page has a `content` VARIANT column with two required fields:

```json
{
  "summary": "One-sentence description of what this page covers.",
  "body": "Full content. Can be multi-paragraph plain text or markdown."
}
```

- `summary` powers search result snippets and the wiki index.
- `body` holds the detailed knowledge. Keep it factual and concise.
- `content_text` is auto-generated as `summary + ' ' + body` for Vector Search embedding.

## Tag Taxonomy

Tags are freeform but should follow these guidelines:

- Use lowercase, hyphen-separated tags: `vector-search`, `getting-started`.
- Prefer existing tags over inventing new ones. Check the wiki index first.
- Auto-generated pages add `auto-generated` and `promoted` tags.
- Limit to 3-7 tags per page.

## Link Types

Cross-references between pages use the `links` table with typed relationships:

| Type | Meaning |
|------|---------|
| `related` | General relationship. Default link type. |
| `extends` | Target page builds on or deepens the source page's topic. |
| `contradicts` | Pages present conflicting information. Maintenance should resolve. |
| `supersedes` | Source page replaces the target page. Target may be archived. |
| `cites` | Source page was derived from the target page (used by promoted answers). |

## Source Provenance

Pages can track where their knowledge came from via `source_ids` (array of source IDs from the `sources` table). Sources represent raw ingested material: URLs, documents, API responses.

## Operation Log

All wiki operations (write, read, search, ingest, promote, lint) are logged to the `wiki_log` table for auditability and maintenance.

## Maintenance

A scheduled job performs autonomous maintenance:
1. Detect changes via Change Data Feed.
2. Find and merge near-duplicate pages.
3. Suggest cross-reference links for related content.
4. Detect orphan pages (no incoming links).
5. Quality-check recently promoted pages.
6. Materialize the wiki index at `_meta/index`.
