# Universal Harness Installation Design

## Goal

`wikibricks install` connects every supported local agent client to one SQLite
database without requiring Omnigent, Databricks, or a separate memory agent.
When Omnigent 0.11.0 or newer is installed, the same command prepares all of
its bundled harnesses. A user who has only Codex or Claude Code gets the same
one-command setup without extra configuration files for absent clients.

## Product boundary

WikiBricks owns the memory service and its client integration. Omnigent owns
session management and harness launch. The installer must use public client and
Omnigent configuration interfaces. It must not patch Omnigent source, select a
default Omnigent agent, or add a resident WikiBricks process.

Every client calls the same `wikibricks-mcp` executable and sees exactly five
tools:

- `wiki_search`
- `wiki_read_full`
- `wiki_index`
- `wiki_write_page`
- `wiki_promote_answer`

All processes use `~/.wikibricks/wikibricks.db` unless the user sets
`WIKIBRICKS_DATABASE_PATH`. SQLite WAL and the existing busy timeout provide
safe concurrent access. Remote archival and curation remain optional.

## Command contract

The normal installation is:

```bash
uv tool install wikibricks
wikibricks install
```

`wikibricks install omnigent` remains as a compatibility alias. The alias
requires Omnigent 0.11.0 or newer. It is not the primary documented command.

The automatic command follows these rules:

1. It initializes the local database and installs the shared memory skill.
2. It detects supported executables on `PATH`.
3. If Omnigent is absent, it configures only detected clients.
4. If Omnigent is present, it validates version 0.11.0 or newer and prepares
   the complete Omnigent harness set, including file-based configuration for a
   client whose executable is not installed yet.
5. If no MCP client is detected, it leaves the database and shared skill ready
   and reports that no client was configured.

Re-running the command produces the same configuration and does not duplicate
entries.

## Harness integration matrix

| Omnigent command | Client integration | User-level location or interface |
|---|---|---|
| `claude` | Native stdio MCP registration | `claude mcp add --scope user` |
| `codex` | Native stdio MCP registration | `codex mcp add` |
| `debby` | Reuses Claude SDK user MCP settings | Claude user MCP configuration |
| `goose` | Native Goose extension entry | `~/.config/goose/config.yaml` |
| `hermes` | Native Hermes MCP entry plus Omnigent launcher shim | `~/.hermes/config.yaml` and Omnigent config |
| `kimi` | Native Kimi MCP entry | `~/.kimi/mcp.json` or `KIMI_SHARE_DIR/mcp.json` |
| `kiro` | Native Kiro MCP entry | `~/.kiro/settings/mcp.json` |
| `opencode` | Native OpenCode MCP entry plus Omnigent launcher shim | `~/.config/opencode/opencode.json` and Omnigent config |
| `pi` | First-party Pi user extension that acts as an MCP client | `~/.pi/agent/extensions/wikibricks-mcp.js` |
| `polly` | Reuses Claude SDK user MCP settings | Claude user MCP configuration |
| `qwen` | Native Qwen MCP entry | `~/.qwen/settings.json` |

The shared `wikibricks-memory` skill is written to `~/.agents/skills`. It is
also copied to the native Codex and Claude Code skill directories when those
clients are configured, and to Pi's user skill directory when Pi is prepared.

## File-based configuration

The installer parses every existing JSON or YAML file before changing any
file. A malformed file stops installation with its original bytes untouched.
The installer validates the parent object used by each client and replaces
only the `wikibricks` entry. Unrelated providers, MCP servers, extensions,
models, authentication settings, and Omnigent defaults remain unchanged.

JSON and YAML writes use a temporary file in the destination directory followed
by `os.replace`. Installed skills, extensions, and launcher files use the same
atomic pattern. Wrapper scripts are executable only after their content is
complete.

Client entries use the absolute path resolved for `wikibricks-mcp`. No client
receives a database URL or Databricks credential.

## Pi adapter

Pi has no built-in MCP client. WikiBricks installs one CommonJS extension using
Pi's supported user-extension mechanism. The extension:

1. starts the configured `wikibricks-mcp` child process;
2. completes the MCP initialize handshake over newline-delimited JSON-RPC;
3. registers the five fixed WikiBricks schemas with `pi.registerTool`;
4. forwards each tool call through MCP and returns the server's content;
5. terminates the child process when Pi shuts down.

The extension contains no memory implementation and never calls a WikiBricks
memory CLI command. It is only an MCP transport adapter.

## OpenCode adapter

Standalone OpenCode reads the user `mcp.wikibricks` entry. Omnigent launches
OpenCode with an isolated `XDG_CONFIG_HOME`, so that user entry is not visible
inside an Omnigent session. WikiBricks installs an executable launcher under
`~/.wikibricks/bin` and sets `harness.opencode-native.command` in the Omnigent
user configuration.

The launcher merges a `wikibricks` local MCP entry into
`OPENCODE_CONFIG_CONTENT`, then executes the original OpenCode binary with the
same arguments and exit behavior. Inline caller configuration is preserved,
and WikiBricks replaces only its own MCP key. The installation manifest records
the original executable so a second installation does not wrap the wrapper.

## Hermes adapter

Standalone Hermes reads the user `mcp_servers.wikibricks` entry. Omnigent sets a
per-session `HERMES_HOME` and copies provider settings into it, which hides the
user MCP entry. WikiBricks therefore installs a Hermes launcher under
`~/.wikibricks/bin` and sets `harness.hermes-native.command` in the Omnigent
user configuration.

When `HERMES_HOME` is present, the launcher atomically merges WikiBricks into
that directory's `config.yaml` before executing the original Hermes binary. It
does not change the Omnigent MCP entry or policy hook already written there.
Without `HERMES_HOME`, it executes Hermes unchanged because the normal user
configuration is already visible.

## Ownership manifest

The installer writes `~/.wikibricks/omnigent-install.json`. The JSON document
records the installer version, resolved MCP command, configured harnesses,
files created by WikiBricks, configuration keys owned by WikiBricks, and the
original OpenCode and Hermes executables when launchers are installed.

The manifest does not claim ownership of an entire user configuration file.
It identifies only WikiBricks files and keys. This supports safe upgrades and a
future uninstall command without treating user settings as disposable.

## Failure behavior

Preflight happens before mutation. It resolves `wikibricks-mcp`, validates an
installed Omnigent version, parses each file that will be changed, and confirms
that each launcher has a real downstream executable. A preflight error
leaves all files and client registrations unchanged.

After preflight, any native CLI registration failure stops the command and
reports the client and command output. File writes already completed remain
valid and the command can be rerun. The installer never deletes an unrelated
configuration or executable.

The previous WikiBricks Omnigent agent file is removed only when its parsed
content matches the legacy WikiBricks MCP agent signature. Its
`default_agent` setting is cleared only when it points to that matching legacy
file.

## Verification

Lean behavior tests use temporary home directories and real file parsing. One
end-to-end installer test covers the complete Omnigent matrix, preservation,
the ownership manifest, wrapper chaining, skill installation, and idempotent
reruns. Narrow tests cover standalone detection, malformed preflight input,
the compatibility alias, and the Pi MCP bridge.

Release validation runs Ruff, the full suite, the full suite with
`UV_OFFLINE=1`, package build, and a clean `uv tool` installation. Installed
package smoke tests initialize a temporary database, run `wikibricks install`
against isolated client fixtures, start `wikibricks-mcp`, complete MCP
initialization, and verify that `tools/list` returns exactly the five tool
names.
