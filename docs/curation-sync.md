# Curation sync protocol

Local PostgreSQL owns the active WikiBricks pages. Lakebase stores immutable
history and publishes curation proposals. A remote process cannot update a
local page directly.

This boundary keeps recording, search, reads, writes, and local curation
available without Databricks or a network connection. It also preserves the
[LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f):
sessions are evidence, while agents maintain a smaller set of linked pages.

## Data flow

```text
local page and session versions
            |
            | explicit archive push
            v
Lakebase immutable archive
            |
            | bounded weekly analysis
            v
immutable curation manifest
            |
            | explicit pull
            v
local durable inbox
            |
            | plan, compare, apply
            v
new local page versions and receipts
            |
            | normal archive outbox
            v
Lakebase acknowledgement history
```

Only `wikibricks sync lakebase` contacts Databricks. Planning, application,
conflict review, and resolution use local PostgreSQL.

## Stored state

Migrations `0003_curation_patches.sql` through
`0005_remote_maintenance.sql` add the following records:

| Record | Purpose |
|---|---|
| `sync_replicas` | Stores one stable identifier for the local database. |
| `curation_runs` | Stores each immutable manifest and its archive watermark. |
| `curation_patches` | Stores normalized operations grouped by transaction. |
| `curation_receipts` | Records the local result for every processed patch. |
| `curation_conflicts` | Stores base, local, and remote values for a failed group. |
| `page_aliases` | Resolves a superseded path to its canonical page. |
| `remote_maintenance_runs` | Records processed archive watermarks, including runs with no proposals. |

Pages remain stable entities with immutable versions. A page is either
`active` or `superseded`. Superseded pages retain their versions but do not
appear in the default index, search results, duplicate reports, or orphan
reports.

## Manifest contract

A curator publishes a complete manifest in one PostgreSQL transaction. The
manifest contains:

- `schema_version`
- `run_id`
- target `replica_id`
- `input_watermark`
- an ordered list of patches
- `manifest_hash`

Every patch has a stable patch ID and group ID, its position inside the group,
an allowlisted operation, a path, base version ID, base content hash, proposed
value and hash, evidence IDs, reason, and risk class. Create operations have no
base. All other operations require both the base version ID and content hash.

The publisher hashes canonical JSON. The local pull recalculates the manifest
and proposal hashes before inserting anything. A run ID cannot be reused with
different content.

The first schema supports five operations:

| Operation | Local effect |
|---|---|
| `create_page` | Creates a page only when the path is absent. |
| `update_page` | Writes a new version after an exact base match. |
| `retarget_links` | Moves incoming and outgoing links to a canonical page. |
| `add_alias` | Preserves an old path. |
| `supersede_page` | Hides a duplicate and points it at the canonical entity. |

Patches contain data, never SQL or executable code.

## Pull, plan, and apply

The pull copies matching manifests into the local inbox. It does not change a
page:

```bash
wikibricks sync lakebase \
  --profile PROFILE \
  --project PROJECT \
  --branch production \
  --endpoint primary \
  --database wikibricks \
  --pull-patches
```

Inspect the stable local replica ID when configuring the remote publisher:

```bash
wikibricks sync replica
```

Plan a downloaded run without a remote connection:

```bash
wikibricks sync plan RUN_ID --policy safe
```

The safe policy processes low-risk groups. Any group containing a medium- or
high-risk patch remains `review_required`. After review, apply such a group
with the all policy:

```bash
wikibricks backup backups/before-curation.dump
wikibricks sync apply RUN_ID --policy all
wikibricks curate
wikibricks check
```

Application takes a PostgreSQL advisory transaction lock. It then locks every
participating page row and repeats the precondition checks. Each group commits
as one transaction. A failure rolls back page versions, search rows, aliases,
links, status changes, receipts, operation records, and outbox events together.

The resulting page version records `created_by=remote-curator` and the source
patch ID. A receipt enters the normal archive outbox. Archive history therefore
records both the page version and the local decision, which prevents the next
weekly run from proposing the same work.

## Conflict rules

| Proposal | Local state | Result |
|---|---|---|
| Create | Path absent | Apply. |
| Create | Proposed hash already present | Record `already_applied`. |
| Create | Different page exists | Conflict. |
| Update | Version ID and content hash match the base | Write a new version. |
| Update | Proposed hash already present | Record `already_applied`. |
| Update | Local page differs from base and proposal | Conflict; keep local active. |
| Cleanup group | Every page and target matches | Apply the whole group. |
| Cleanup group | Any participant changed | Apply none of the group. |
| Supersede | Page already points to the same canonical target | Record `already_applied`. |

WikiBricks does not use timestamps or last-write-wins. Exact IDs and hashes
decide whether the remote proposal still applies.

List conflicts locally:

```bash
wikibricks sync conflicts
```

Each conflict stores the common base identifiers, current local page, proposed
remote value, evidence, and reason. Four decisions are available:

```bash
wikibricks sync resolve RUN_ID GROUP_ID --action keep_local
wikibricks sync resolve RUN_ID GROUP_ID --action accept_remote
wikibricks sync resolve RUN_ID GROUP_ID --action defer
wikibricks sync resolve RUN_ID GROUP_ID --action merged
```

`accept_remote` writes another local version and preserves the divergent
version in history. For `merged`, edit the page through the ordinary MCP or
Python API first, then record the resolution. `defer` leaves the conflict open.

## Duplicate cleanup

A duplicate merge is one patch group:

1. Update the canonical page with the consolidated text and evidence.
2. Retarget the duplicate's graph links.
3. Add the duplicate path as an alias.
4. Mark the duplicate page as superseded.

All participating page hashes must match the archive snapshot. A local edit to
either page rejects the entire group. WikiBricks never hard-deletes a page
during remote curation.

Default reads resolve the old path through its alias. Search and local
maintenance inspect active pages only. Raw sessions remain searchable evidence
but stay outside the curated `_meta/index` page.

## Local and remote maintenance schedule

Local hygiene runs without Databricks:

- The write path prevents identical versions and updates GIN search chunks.
- The active MCP client searches before writing and updates existing pages.
- A daily `wikibricks curate` repairs search metadata and reports active
  duplicates and orphans.
- Retention removes a session only after every immutable event version has a
  committed archive acknowledgement.

The weekly remote job uses a fixed archive watermark and bounded policy values
from `src/wikibricks_remote/resources/remote-policy.yml`. It validates model
output against the proposal contract and publishes one immutable manifest per
replica. A run with no proposals records its watermark, so the next run does
not analyze the same evidence again. Remote analysis never connects to the
local database.

Change Data Feed and a Lakehouse history layer are optional later additions.
The weekly job does not require either one.

## Recovery properties

- Pull is idempotent by run ID and manifest hash.
- Apply is idempotent by patch receipt and proposed content hash.
- Concurrent apply commands serialize through an advisory lock.
- A crash before group commit leaves no partial page or cleanup state.
- A lost connection after the remote archive commit retries the existing
  archive batch by immutable event ID.
- Remote unavailability does not affect local reads or writes.

The first remote deployment should use an isolated Lakebase Autoscaling branch.
Run the publisher against copied archive data, pull its manifest into a staging
local database, and compare page hashes, aliases, links, search results, and
receipts before enabling the production weekly schedule. Keep the current
FEVM tables unchanged as rollback evidence during migration.

## References

1. [Andrej Karpathy, LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
2. [Databricks, Lakebase Change Data Feed](https://docs.databricks.com/aws/en/oltp/projects/quickstart-lakebase-cdf)
3. [PostgreSQL, TOAST storage](https://www.postgresql.org/docs/current/storage-toast.html)
