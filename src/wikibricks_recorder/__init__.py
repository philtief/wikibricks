"""wikibricks_recorder: Claude Code session → WikiBricks page bridge.

Runs locally on the user's Mac, called by Claude Code hooks. Hooks accumulate
session state on disk; on Stop / SessionEnd the recorder synchronously writes
one page per session to a configured WikiBricks deployment.

The same code path serves either:
  * **personal wiki** — one user_id writing to a private schema, or
  * **team wiki** — many user_ids sharing one schema, partitioned in the
    page path by user.

Config (catalog/schema/warehouse/profile/user_id) is resolved at runtime by
`wikibricks_recorder.config.load_config()` from env vars or
`~/.wikibricks-recorder.toml`. No hardcoded workspace defaults.
"""
