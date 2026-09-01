# Lakebase remote maintenance validation

Date: 2026-09-01

Branch: `feat/lakebase-remote-maintenance`

## Result

The staging job published one immutable curation manifest from a bounded
Lakebase archive. Its immediate rerun was idle after the archive watermark
advanced. Local PostgreSQL remained authoritative throughout the test.

The job publishes proposals only. It cannot connect to or update a local
WikiBricks database.

## Validated resources

| Resource | Value |
|---|---|
| Workspace | `https://fevm-agent-marketplace.cloud.databricks.com` |
| Lakebase project | `projects/wikibricks` |
| Branch | `projects/wikibricks/branches/staging` |
| Endpoint | `primary`, 0.5 to 2 CU, 300-second suspend timeout |
| PostgreSQL | 17.11 |
| Database | `wikibricks` |
| Lakeflow Job | `222630699179712` |
| Schedule | Sunday at 04:00 UTC, paused |
| Task | one bounded serverless Python wheel task |
| Run limit | one concurrent run, 60-minute timeout, one retry |

The bundle now keeps the schedule paused in every target. Enabling it requires
an explicit deployment override.

## Manifest evidence

Run `913825562725687` processed archive watermark 1 and published one
manifest for replica `655f603e-965e-439b-933e-f02aeb1d849c`.

| Field | Value |
|---|---|
| Manifest hash | `21e8482d165254f624693620e649c7335d44c4f0c2229a04a157528732dabff1` |
| Patch count | 1 |
| Patch path | `synthesis/local-first-remote-optional` |
| Evidence | `archive-event:88fc6270-ae38-4542-a88a-8031534cb7ad` |
| Maintenance status | `published` |

Run `863652020451418` immediately returned `idle` with no new manifest or
proposal. Three later driver-validation runs also returned `idle`, so the
database remained at one manifest and one maintenance run.

The manifest, curation row, and maintenance row had matching hashes, replica
IDs, and watermarks.

## Driver validation

Fresh serverless runs exposed a native import failure in
`psycopg-binary` 3.3.4. WikiBricks pins `psycopg` and
`psycopg-binary` to 3.2.13.

| Run | Attempt | Result |
|---|---:|---|
| `281962062656158` | 0 | success, idle |
| `809325429921327` | 0 | success, idle |
| `239700301049184` | 0 | success, idle |

All three completed without a retry or process abort.

## Local verification

The 2026-09-01 cleanup passed Ruff, 89 tests, the 89-test offline suite, wheel
build, and a clean installed-wheel MCP smoke test. The bundle resource test
also confirms that every target is paused by default. Strict bundle validation
passed for both `dev` and `staging` with the `fe-vm-agent-marketplace` profile.

No Databricks resource or Lakebase data was changed during this cleanup. A
future production run should first deploy the paused job, inspect a staging
manifest locally, and enable the schedule only through the explicit
`schedule_pause_status=UNPAUSED` override.
