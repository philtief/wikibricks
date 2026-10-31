# Reading UI and page digests design

Status: proposal, 31 October 2026. Extends #5 (local browser) and #6 (remote UI).

## Decision

People read WikiBricks in two layers. Every page opens with a short card and a
few key points, and a generated digest says what changed this week. A local
read-only browser, `wikibricks browse`, shows the digest, search, pages, and the
review queue over the live SQLite store. The Databricks App from #6 follows only
if the wiki must be read away from the laptop.

## Current state

Measured on the local store, 31 October 2026:

| Measure | Value |
|---|---|
| Curated pages | 130: 71 `topics/`, 53 `synthesis/`, 3 `projects/`, 2 `guides/`, 1 `_meta/` |
| `summary` length | median 298 characters, 90th percentile 518, maximum 815 |
| `body` length | median 3,202 characters, 90th percentile 6,851, maximum 16,780 |
| Bodies with headings | 102 of 130; the most common first heading is "Current state" (15) |

Every page has a summary, but the median one is 298 characters, closer to a
paragraph than a card, and the body has no fixed order. A reader must open the body to find the decision, the date,
or the owner. Agents read pages through MCP. People have `wikibricks search`,
`_meta/index`, and the Markdown export, none of which shows what changed or what
waits for review.

## Part 1: digestible pages

### Page contract

| Layer | Field | Rule | Written by |
|---|---|---|---|
| Card | `summary` | At most 280 characters, two sentences: what the page covers and its current state or decision | The curator or agent that writes the page |
| Key points | First body section, `## Key points` | Three to seven bullets, one fact each: a decision, number, date, owner, or next step | Same |
| Details | The rest of the body | Unchanged prose, then `## Open questions` when any exist, then `## Sources` | Same |

The card tells a reader whether to open the page. The key points give what they
need to know in under a minute. The details stay for readers who need them and
for agents.

The contract goes into the three prompts that write pages:
`src/wikibricks/resources/agent-instructions.md` (agents through MCP),
`src/wikibricks_curator/resources/local-curator.md` (the daily local curator),
and `src/wikibricks_remote/resources/curation.md` (the weekly remote job).

### Hygiene report

`wikibricks curate` reports pages that break the contract: a summary over 280
characters, or a body without `## Key points`. The report is deterministic and
changes nothing, because the library never calls a model.

### Backfill

The curator rewrites the reported pages through the existing guarded
`update_page` path: base version and content hash must match, and the old
version stays in history. At 130 pages of roughly 3,200 characters each, that
is a few curator calls. The run happens once, after the prompt change ships.

### Generated digests

Two deterministic pages, built by `wikibricks curate` the way `_meta/index` is:

- `_meta/digest` lists pages created or changed in the last seven days, with
  their cards, grouped by top-level hub; the meetings imported; and the remote
  curation groups that wait for review.
- A hub is a `topics/` page with three or more outgoing links. Its digest
  section lists each linked page with its card. The curator keeps hubs current;
  the digest only arranges what the hubs already link.

Neither page needs a model call, so `wikibricks curate` rebuilds both every day.

## Part 2: browse UI

### Options

| Option | What it is | Effort | Gives | Lacks |
|---|---|---|---|---|
| A. Obsidian vault | The daily job runs the existing Markdown export into a vault folder | About an hour | Graph, backlinks, search, mobile through Obsidian sync | Read-only snapshot: an edit in Obsidian is overwritten next day. No review queue. A second copy on disk, which #5 rejected as a synchronized mirror |
| B. `wikibricks browse` | Local read-only web UI over the live SQLite store (#5) | Two to three days with tests | Live data, digest home, review queue, session drill-down | Laptop only |
| C. Databricks App | App over the Lakebase archive (#6) | About a week: APX scaffold, authentication, Lakebase reads, deployment | Any device | Remote state lags the laptop; app compute cost; more surface to secure |

Choose B. A is a same-day stopgap if the wiki must be readable before B ships;
it needs one extra daily-job step and no code. C reuses B's views later, and
only if reading away from the laptop matters.

### B: `wikibricks browse`

```bash
wikibricks browse            # opens http://127.0.0.1:8765
wikibricks browse --port 9000 --bind 127.0.0.1
```

The server is the standard library's `ThreadingHTTPServer` with server-rendered
HTML, one stylesheet, and no JavaScript framework. It binds to `127.0.0.1`
unless told otherwise.

It reads through `WikiClient` (`list_pages`, `read_page`, `history`, `search`,
`graph_neighbors`) over a read-only SQLite connection (`mode=ro`). WAL lets it
read while the MCP server and the daily job write.

`markdown-it-py` renders page text with raw HTML disabled, so a page cannot
inject markup. It ships as the optional extra `wikibricks[browse]`, and the core
dependencies stay `mcp` and `PyYAML`.

| Screen | Content |
|---|---|
| Home | The `_meta/digest` view: changed this week as cards with a new or updated badge, waiting for review, hubs |
| Search | Full-text search over pages; filters for page type, tag, and path prefix; sessions hidden unless toggled |
| Page | Card, key points, rendered body; type, tags, version, updated time, writer; sources linking to sessions; typed outgoing links and backlinks, one hop; version history with a diff |
| Session | A recorded session's messages, with tool output collapsed |
| Review | Each pending remote group: reason, evidence, and a before-and-after diff, with the `wikibricks sync apply` or `sync resolve` command to run |

The first release is read-only. Applying a review from the browser waits for a
write path that checks the base version and content hash, as #5 requires.

### Tests

- Reads succeed while another connection writes the same database.
- Sessions stay out of default search and navigation.
- A `<script>` tag in a page body renders as text.
- A backlink opens the linking page.
- The home view lists a page changed today and a pending review group.

## Milestones

1. Page contract in the three prompts, the hygiene report, and `_meta/digest`.
2. Backfill run over the reported pages.
3. `wikibricks browse`: home, search, and page views.
4. Review screen and version diffs.
5. Optional: the Databricks App from #6, reusing the views.

Milestones 1 and 2 make pages digestible in every surface, including MCP and
the Markdown export, before any UI exists.
