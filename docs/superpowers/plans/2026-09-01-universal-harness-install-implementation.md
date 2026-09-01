# Universal Harness Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `wikibricks install` connect standalone clients and every Omnigent 0.11 harness to one local SQLite memory without a separate agent or source modification.

**Architecture:** A universal installer detects Omnigent and local client executables, validates all files before mutation, then applies native MCP entries or the smallest client adapter required. Pi receives a user extension that forwards the five fixed tools over MCP. OpenCode and Hermes receive Omnigent command wrappers because those runtimes isolate their configuration during managed sessions.

**Tech Stack:** Python 3.10+, argparse, JSON, PyYAML, stdio MCP, CommonJS, pytest, Ruff, uv

**Spec:** `docs/superpowers/specs/2026-09-01-universal-harness-install-design.md`

## Global Constraints

- The default active store is SQLite at `~/.wikibricks/wikibricks.db`.
- Local operation must not require Databricks, Lakebase, PostgreSQL, credentials, or a network connection.
- The MCP surface contains exactly `wiki_search`, `wiki_read_full`, `wiki_index`, `wiki_write_page`, and `wiki_promote_answer`.
- Omnigent support requires version 0.11.0 or newer.
- Do not patch Omnigent source or install a separate WikiBricks agent.
- Preserve unrelated user settings and validate every affected file before mutation.
- Use atomic writes and keep `wikibricks install omnigent` as a compatibility alias.
- If Omnigent is absent, create configuration only for detected clients.
- Use failing behavior tests before production changes.

---

## File map

- `src/wikibricks/omnigent_install.py`: detection, preflight, native config merges, skills, ownership manifest, and legacy-agent removal.
- `src/wikibricks/install_io.py`: shared JSON/YAML validation and atomic file writes.
- `src/wikibricks/harness_launchers.py`: pure OpenCode/Hermes environment preparation and final process execution.
- `src/wikibricks/resources/pi-mcp-extension.js`: Pi's MCP transport adapter.
- `src/wikibricks/cli.py`: optional install target and universal command dispatch.
- `tests/test_omnigent_install.py`: universal installer behavior, preservation, idempotency, and failure atomicity.
- `tests/test_harness_launchers.py`: isolated runtime adapters and real Pi extension behavior.
- `README.md`: one-command installation, automatic detection, Omnigent matrix, and standalone Codex/Claude use.
- `AGENTS.md`: product boundary and updated installer responsibility.

### Task 1: Universal detection and native configuration

**Files:**
- Modify: `tests/test_omnigent_install.py`
- Create: `src/wikibricks/install_io.py`
- Modify: `src/wikibricks/omnigent_install.py`

**Interfaces:**
- Produces: `install_integrations(*, home=None, require_omnigent=False, command="omnigent", run=subprocess.run, which=shutil.which, environ=None) -> dict[str, Any]`
- Preserves: `install_omnigent(...) -> dict[str, Any]` as the strict compatibility entry point.

- [ ] **Step 1: Replace repeated narrow coverage with one failing full-matrix behavior test**

Create isolated existing JSON/YAML files containing unrelated settings. Supply a deterministic `which` mapping for `wikibricks-mcp`, installer wrappers, Omnigent, and all external clients. Assert these literal outcomes:

```python
assert result["mode"] == "omnigent"
assert set(result["harnesses"]) == {
    "claude", "codex", "debby", "goose", "hermes", "kimi",
    "kiro", "opencode", "pi", "polly", "qwen",
}
assert json.loads(kimi_path.read_text())["mcpServers"]["wikibricks"] == {
    "command": str(mcp_command)
}
assert yaml.safe_load(goose_path.read_text())["extensions"]["wikibricks"]["cmd"] == str(mcp_command)
assert yaml.safe_load(hermes_path.read_text())["mcp_servers"]["wikibricks"] == {
    "command": str(mcp_command), "args": []
}
assert json.loads(kiro_path.read_text())["mcpServers"]["wikibricks"] == {
    "command": str(mcp_command)
}
assert json.loads(qwen_path.read_text())["mcpServers"]["wikibricks"] == {
    "command": str(mcp_command)
}
assert json.loads(opencode_path.read_text())["mcp"]["wikibricks"] == {
    "type": "local", "command": [str(mcp_command)], "enabled": True
}
```

Also assert that existing keys remain, the Pi/shared/native skills exist, the ownership manifest names only WikiBricks-owned keys/files, and a second call leaves byte-identical files.

- [ ] **Step 2: Run the focused test and confirm the missing universal installer failure**

Run: `uv run --no-sync pytest tests/test_omnigent_install.py -q`

Expected: FAIL because `install_integrations` and the new harness configurations do not exist.

- [ ] **Step 3: Implement reusable preflight and atomic merge helpers**

Add helpers to `install_io.py` with these signatures:

```python
def _load_json_object(path: Path, *, parent: str) -> dict[str, Any]: ...
def _load_yaml_mapping(path: Path, *, parent: str) -> dict[str, Any]: ...
def _write_json_atomic(path: Path, value: dict[str, Any]) -> None: ...
def _write_yaml_atomic(path: Path, value: dict[str, Any]) -> None: ...
def _write_text_atomic(path: Path, text: str, *, executable: bool = False) -> None: ...
```

Each loader accepts a missing file as `{}`, rejects a non-object top level, and rejects a present non-object parent. Each writer uses a same-directory temporary file and `os.replace`.

- [ ] **Step 4: Implement detection and native client merges**

Use one declarative executable map and prepare all file-based clients only when Omnigent is present or their executable is detected. Merge these WikiBricks-owned entries:

```python
stdio = {"command": mcp_command}
hermes = {"command": mcp_command, "args": []}
opencode = {"type": "local", "command": [mcp_command], "enabled": True}
goose = {
    "name": "wikibricks", "type": "stdio", "enabled": True,
    "cmd": mcp_command, "args": [], "timeout": 300,
}
```

Use native Codex and Claude commands only when those executables exist. Mark Debby and Polly as using Claude's user MCP settings. Install shared/Codex/Claude/Pi skill copies only for the selected mode and clients.

- [ ] **Step 5: Write the ownership manifest and keep legacy cleanup signature-gated**

Write `~/.wikibricks/omnigent-install.json` with schema version `1`, installer version, mode, MCP path, status mapping, owned files, owned settings, and launcher downstream paths. Convert a scalar Omnigent `harness` value to `harness.default` before adding wrapper overrides. Remove only the parsed legacy agent signature and its matching `default_agent` value.

- [ ] **Step 6: Run focused tests and refactor only after green**

Run: `uv run --no-sync pytest tests/test_omnigent_install.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the native installer slice**

```bash
git add src/wikibricks/install_io.py src/wikibricks/omnigent_install.py tests/test_omnigent_install.py
git commit -m "feat: install wikibricks across native harnesses"
```

### Task 2: Pi MCP extension

**Files:**
- Create: `src/wikibricks/resources/pi-mcp-extension.js`
- Create: `tests/test_harness_launchers.py`
- Modify: `src/wikibricks/omnigent_install.py`

**Interfaces:**
- Consumes: `get_tool_schemas() -> list[dict[str, Any]]` and the resolved `wikibricks-mcp` path.
- Produces: `~/.pi/agent/extensions/wikibricks-mcp.js` plus `~/.wikibricks/pi-mcp.json`.

- [ ] **Step 1: Write a failing real-extension test**

Create a temporary newline-delimited JSON-RPC server executable that replies to `initialize` and `tools/call`. Load the installed extension with Node and a small Pi API object that records `registerTool` and `on` calls. Execute `wiki_search` and assert the returned text contains the fake server's literal result and that all five tool names were registered.

- [ ] **Step 2: Confirm the missing extension failure**

Run: `uv run --no-sync pytest tests/test_harness_launchers.py::test_pi_extension_forwards_all_tools_over_mcp -q`

Expected: FAIL because the extension resource and installed files do not exist.

- [ ] **Step 3: Implement the CommonJS MCP adapter**

The resource must synchronously register schemas from `~/.wikibricks/pi-mcp.json`, lazily spawn the MCP server on the first call, perform `initialize` followed by `notifications/initialized`, correlate JSON-RPC response IDs, return MCP content unchanged, and kill the child on Pi's `session_shutdown` event. Server errors reject the matching tool call.

- [ ] **Step 4: Install the extension and config atomically**

Add `pi-mcp-extension.js` to the installed resource package. Write its config with:

```json
{
  "mcpCommand": "/absolute/path/to/wikibricks-mcp",
  "tools": ["the five packaged MCP schemas"]
}
```

The installed source receives the absolute config path as its only generated value.

- [ ] **Step 5: Run focused tests**

Run: `uv run --no-sync pytest tests/test_harness_launchers.py tests/test_omnigent_install.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the Pi adapter**

```bash
git add src/wikibricks/resources/pi-mcp-extension.js src/wikibricks/omnigent_install.py tests/test_harness_launchers.py
git commit -m "feat: connect pi to wikibricks over mcp"
```

### Task 3: Omnigent isolated-runtime launchers

**Files:**
- Create: `src/wikibricks/harness_launchers.py`
- Modify: `src/wikibricks/omnigent_install.py`
- Modify: `tests/test_harness_launchers.py`

**Interfaces:**
- Produces: `prepare_opencode_environment(environ, mcp_command) -> dict[str, str]`.
- Produces: `prepare_hermes_home(environ, mcp_command) -> Path | None`.
- Produces: `main(argv=None, environ=None) -> NoReturn` for installed wrapper scripts.

- [ ] **Step 1: Write failing launcher behavior tests**

For OpenCode, begin with literal inline JSON containing an unrelated MCP entry. Assert the result preserves that entry and adds only:

```json
{"wikibricks": {"type": "local", "command": ["/bin/wikibricks-mcp"], "enabled": true}}
```

For Hermes, create a temporary `HERMES_HOME/config.yaml` with Omnigent's MCP entry and policy hook. Assert both remain after WikiBricks adds `mcp_servers.wikibricks`. Run the wrapper against a fake downstream executable and assert argument and exit-code passthrough.

- [ ] **Step 2: Confirm both launcher tests fail for missing functions**

Run: `uv run --no-sync pytest tests/test_harness_launchers.py -q`

Expected: FAIL because `wikibricks.harness_launchers` is missing.

- [ ] **Step 3: Implement the two launch preparations and process handoff**

OpenCode rejects malformed `OPENCODE_CONFIG_CONTENT` instead of discarding it. Hermes validates YAML before replacement and writes through the shared atomic helper. `main` loads downstream paths from the ownership manifest, prevents self-recursion, and uses `os.execvpe` with unchanged arguments.

- [ ] **Step 4: Install thin executable wrappers and Omnigent overrides**

Write `~/.wikibricks/bin/opencode` and `~/.wikibricks/bin/hermes`. Set `harness.opencode-native.command` and `harness.hermes-native.command` only when the downstream client exists. Preserve existing per-harness `args` and record any prior command value in the ownership manifest.

- [ ] **Step 5: Run focused tests**

Run: `uv run --no-sync pytest tests/test_harness_launchers.py tests/test_omnigent_install.py -q`

Expected: PASS.

- [ ] **Step 6: Commit isolated-runtime support**

```bash
git add src/wikibricks/harness_launchers.py src/wikibricks/omnigent_install.py tests/test_harness_launchers.py
git commit -m "feat: bridge isolated omnigent harness configs"
```

### Task 4: One-command CLI and documentation

**Files:**
- Modify: `tests/test_omnigent_install.py`
- Modify: `src/wikibricks/cli.py`
- Modify: `README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: `install_integrations(require_omnigent=False)` and `install_omnigent()`.
- Produces: `wikibricks install [omnigent]` with an optional compatibility target.

- [ ] **Step 1: Write a failing parser and standalone detection test**

Assert that `build_parser().parse_args(["install"])` selects the install handler. With only Codex detected, assert the installer invokes Codex MCP registration, writes the shared and Codex skills, and does not create `.claude`, `.kimi`, `.qwen`, `.hermes`, `.kiro`, `.pi`, `.config/goose`, or `.config/opencode`.

- [ ] **Step 2: Confirm current required-subcommand behavior fails**

Run: `uv run --no-sync pytest tests/test_omnigent_install.py -q`

Expected: FAIL because `wikibricks install` currently requires `omnigent`.

- [ ] **Step 3: Make the target optional and dispatch the approved modes**

Replace the required install subparser with an optional positional whose only compatibility value is `omnigent`. Initialize the configured database first, then call universal mode for no target and strict mode for the alias.

- [ ] **Step 4: Rewrite installation documentation, then apply the docs editing pass**

Lead README installation with:

```bash
uv tool install wikibricks
wikibricks install
```

Explain automatic detection, list all eleven Omnigent harness commands, state that standalone Codex and Claude Code use the same setup, and retain the explicit GitHub install command only as the unreleased-main alternative. Remove claims that only Codex, Claude Code, and Kimi are covered. Update `AGENTS.md` so the installer responsibility matches the implementation.

- [ ] **Step 5: Run focused tests, Ruff, and the full suite**

Run: `uv run --no-sync pytest tests/test_omnigent_install.py tests/test_harness_launchers.py -q`

Run: `uv run --no-sync ruff check src tests`

Run: `uv run --no-sync pytest -q`

Expected: all commands exit 0.

- [ ] **Step 6: Commit CLI and docs**

```bash
git add src/wikibricks/cli.py tests/test_omnigent_install.py README.md AGENTS.md
git commit -m "docs: simplify universal wikibricks setup"
```

### Task 5: Release validation and local installation

**Files:**
- Modify only files required by failures found during validation.

**Interfaces:**
- Verifies the built wheel, source distribution, installed console scripts, SQLite initialization, installer output, and MCP contract.

- [ ] **Step 1: Run the full online and offline quality gates**

Run: `uv run --no-sync ruff check src tests`

Run: `uv run --no-sync pytest -q`

Run: `UV_OFFLINE=1 uv run --no-sync pytest -q`

Expected: all commands exit 0 with no warnings or failures.

- [ ] **Step 2: Build both package artifacts**

Run: `uv build`

Expected: one wheel and one source archive for version 0.11.0.

- [ ] **Step 3: Test a clean isolated installation**

Create a temporary uv tool directory and bin directory. Install the built wheel
there, then point `HOME` and `WIKIBRICKS_DATABASE_PATH` at temporary paths:

```bash
validation_root="$(mktemp -d)"
UV_TOOL_DIR="$validation_root/tools" UV_TOOL_BIN_DIR="$validation_root/bin" \
  uv tool install dist/wikibricks-0.11.0-py3-none-any.whl
HOME="$validation_root/home" WIKIBRICKS_DATABASE_PATH="$validation_root/home/memory.db" \
  "$validation_root/bin/wikibricks" install
HOME="$validation_root/home" WIKIBRICKS_DATABASE_PATH="$validation_root/home/memory.db" \
  "$validation_root/bin/wikibricks" check
```

Expected: installation reports no MCP client when no client executable is on
the isolated `PATH`, and `wikibricks check` reports `"ok": true`.

- [ ] **Step 4: Verify the installed MCP server over its real stdio protocol**

Run the existing protocol test against the built installation environment:

```bash
UV_OFFLINE=1 uv run --no-sync pytest tests/test_mcp_end_to_end.py -q
```

Then start the installed `wikibricks-mcp`, send `initialize`,
`notifications/initialized`, and `tools/list` with the same helper used by that
test, and assert the returned tool-name set is exactly the five names in Global
Constraints.

- [ ] **Step 5: Reinstall the built wheel in the user's uv tool environment**

Run: `uv tool install --force dist/wikibricks-0.11.0-py3-none-any.whl`

Run: `wikibricks install`

Run: `wikibricks check`

Expected: Omnigent 0.11.0 is detected, installed Codex and Claude Code are configured, the local database is valid, and rerunning installation is idempotent.

- [ ] **Step 6: Inspect the final diff and commit any validation fixes**

Run: `git diff --check`

Run: `git status --short`

If validation produced fixes, commit them with a conventional message after the overnight hook passes.

- [ ] **Step 7: Push the verified main branch**

Run: `git push origin main`

Expected: the remote `main` ref advances to the locally verified commit.
