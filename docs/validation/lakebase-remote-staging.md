# Lakebase remote maintenance staging validation

Date: 2026-08-31

Branch: `feat/lakebase-remote-maintenance`

## Result

The staging job publishes an immutable curation manifest from a bounded
Lakebase archive and becomes idle after advancing its watermark. Local
PostgreSQL remains authoritative. The job does not write directly to a local
WikiBricks database.

This validation changed only the staging branch of the `wikibricks` Lakebase
project and the bundle-managed staging job. Production, the legacy jobs, the
existing app, Delta data, Vector Search, and the public repository were not
changed.

## Resources

| Resource | Observed state |
|---|---|
| Workspace | `https://fevm-agent-marketplace.cloud.databricks.com` |
| Lakebase project | `projects/wikibricks` |
| Lakebase branch | `projects/wikibricks/branches/staging`, ready |
| PostgreSQL | 17.11 |
| Capacity | 0.5 to 2 CU |
| Suspend timeout | 300 seconds |
| Database | `wikibricks` |
| Job | `222630699179712` |
| Schedule | Sunday at 04:00 UTC, paused |
| Task | one serverless Python wheel task, environment client 4 |
| Run bound | one concurrent run, 60-minute timeout, one retry |

The production Lakebase branch is protected. The staging branch was created
from production and is not protected. The bundle summary contains one managed
resource, the staging maintenance job.

Five database migrations are recorded. `pg_trgm` 1.6 is installed. `vector`
0.8.0 is available but is not installed because remote maintenance does not
need embedding storage.

## Manifest and watermark

The archive contained one event for replica
`655f603e-965e-439b-933e-f02aeb1d849c` at local sequence 1. Run
`913825562725687` published one manifest. Lakebase then contained one archive
event, one curation run, and one remote maintenance run.

| Field | Value |
|---|---|
| Input watermark | `1` |
| Manifest hash | `21e8482d165254f624693620e649c7335d44c4f0c2229a04a157528732dabff1` |
| Patch count | `1` |
| Patch path | `synthesis/local-first-remote-optional` |
| Evidence | `archive-event:88fc6270-ae38-4542-a88a-8031534cb7ad` |
| Maintenance status | `published` |

The manifest body, curation row, and maintenance row have matching manifest
hashes, replica IDs, and input watermarks. The published proposal had no
semantic `source_ids`; its `evidence_ids` reference the supplied archive
event.

The immediate rerun `863652020451418` returned `idle` with zero replicas,
manifests, proposals, and no-change records. The model was not called again.
The three driver-validation runs listed below also returned `idle`, so the
database counts remained at one manifest and one maintenance run.

## Failures found during staging

Run `837794106184935` exposed two independent faults. Attempt 0 aborted with
SIGABRT while importing `psycopg-binary` 3.3.4. The automatic retry reached
curation, then rejected the model output because the validator treated
semantic `source_ids` as archive evidence IDs.

Commit `25cbffa` removed that invalid `source_ids` allowlist. A regression test
uses a semantic source ID and separately verifies the allowlisted evidence ID.
Unknown `evidence_ids` remain rejected.

Run `913825562725687` reproduced the same native import abort on attempt 0.
Its retry succeeded and published the manifest. The repeated failure localized
the abort to the psycopg native-loading boundary in a fresh serverless Python
3.12 environment. Commit `cee59e7` pins `psycopg` and `psycopg-binary` to
3.2.13 instead of resolving the unconstrained 3.3.4 release.

| Pinned-driver run | Attempt | Result | Maintenance result |
|---|---:|---|---|
| `281962062656158` | 0 | success | idle |
| `809325429921327` | 0 | success | idle |
| `239700301049184` | 0 | success | idle |

All three runs completed without a retry or kernel abort. This validates the
pin against the observed cold-start failure. It does not identify the native
library defect inside psycopg 3.3.4.

## Local and bundle gates

| Check | Result |
|---|---|
| `uv run --extra dev ruff check src tests scripts` | passed |
| `uv run --extra dev pytest` | 89 passed |
| `UV_OFFLINE=1 uv run --extra dev pytest` | 89 passed |
| `uv build --wheel` | passed |
| Staging bundle validation with `--strict` | passed |
| Personal bundle validation with `--strict` | passed |

The built wheel is 82,611 bytes with SHA-256
`1293acff1410038bfa21e5ab7d11fd41028f0dccf3a5548474cd31c1b0c04a94`.
Its metadata requires `psycopg[binary]==3.2.13`. The overnight-dev pre-commit
hook ran Ruff and the full suite for both repair commits.

## Autosuspend observation

The staging endpoint reported `IDLE` at 21:50:13 CEST after the final SQL
verification connection closed. Its configured suspend timeout remained 300
seconds. Control-plane status checks did not reconnect to PostgreSQL.

## Production gate

No production target was deployed. Production migration still requires a
separate non-destructive plan for the existing remote wiki, Delta history, and
legacy resources. Enabling the production schedule also requires an explicit
deployment of the `personal` target; its declared schedule is unpaused.
