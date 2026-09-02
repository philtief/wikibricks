# Lakebase remote maintenance validation

## Hybrid search implementation, 2026-09-02

Branch: `feat/lakebase-hybrid-curation`

Commit tested: `6b12264`

The local and non-mutating staging gates pass for the remote-only hybrid search
implementation.

| Check | Command | Result |
|---|---|---|
| Ruff | `uv run --no-sync ruff check src tests` | pass |
| Full suite | `uv run --no-sync pytest -q` | 124 passed |
| Offline suite | `UV_OFFLINE=1 uv run --no-sync pytest -q` | 124 passed |
| Offline build | `UV_OFFLINE=1 uv build` | wheel and source archive built |
| Installed wheel | `tests/wheel_smoke.py` in an isolated virtual environment | MCP smoke and packaged search SQL passed |
| Bundle | `databricks bundle validate --strict -t staging --profile pt` | pass |

Artifacts:

| File | SHA-256 |
|---|---|
| `wikibricks-0.11.0-py3-none-any.whl` | `107c46ff5a3dbf1f408cb8699d2a72a0f8864b29ff20f333c6d44e314f8de4e4` |
| `wikibricks-0.11.0.tar.gz` | `f9a8ff6c9f58ec00d5e7092c35bf97785798178522ff15e587c25ce9f1fad83d` |

The first online build attempt could not reach PyPI for Hatchling. Repeating
the locked build with `UV_OFFLINE=1` used the existing cache and passed. The
isolated wheel environment used the machine's installed MCP dependencies
because MCP was absent from the `uv` offline cache; WikiBricks itself came only
from the built wheel.

The read-only staging check used
`projects/wikibricks/branches/staging/endpoints/primary` with profile `pt`.
PostgreSQL reported version `17.11 (32e7196)`. Both Lakebase Search extensions
appear in `pg_available_extensions`:

| Extension | Installed version |
|---|---|
| `lakebase_text` | not installed |
| `lakebase_vector` | not installed |

No bundle was deployed, no extension was installed, no preview setting was
changed, and no Lakebase data was written. A live ANN/BM25 staging run remains
pending authorization to install the two extensions and deploy the paused job.

Date: 2026-09-01

Branch: `feat/lakebase-remote-maintenance`

## Result

The staging job published one immutable curation manifest from a bounded
Lakebase archive. Its immediate rerun was idle after the archive watermark
advanced. The local store remained authoritative throughout the test.

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
