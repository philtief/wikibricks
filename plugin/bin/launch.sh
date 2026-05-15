#!/usr/bin/env bash
# Idempotent launcher for the wikibricks-recorder plugin.
#
# First call (per CLAUDE_PLUGIN_DATA + WIKIBRICKS_PLUGIN_REF): installs
# wikibricks[recorder] from a Git URL into the plugin's persistent data dir.
# Subsequent calls: exec the requested console-script binary directly.
#
# Usage: launch.sh <binary-name> [args...]
#   e.g. launch.sh wikibricks-recorder-hook
#        launch.sh wikibricks-mcp

set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "launch.sh: usage: launch.sh <binary> [args...]" >&2
  exit 64
fi

REF="${WIKIBRICKS_PLUGIN_REF:-v0.5.1}"
GIT_URL="${WIKIBRICKS_PLUGIN_GIT:-https://github.com/philtief/wikibricks.git}"
DATA_DIR="${CLAUDE_PLUGIN_DATA:-${HOME}/.claude/plugins/data/wikibricks-recorder}"
TOOL_DIR="${DATA_DIR}/uv-tools"
BIN_DIR="${DATA_DIR}/bin"
SAFE_REF="${REF//\//_}"
MARKER="${DATA_DIR}/installed-${SAFE_REF}"
BINARY="${BIN_DIR}/$1"

if [ ! -f "$MARKER" ] || [ ! -x "$BINARY" ]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "wikibricks-recorder: 'uv' is not on PATH. Install it first:" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    echo "Then restart your Claude Code session." >&2
    exit 127
  fi
  mkdir -p "$TOOL_DIR" "$BIN_DIR"
  UV_TOOL_DIR="$TOOL_DIR" UV_TOOL_BIN_DIR="$BIN_DIR" \
    uv tool install --force \
      "wikibricks[recorder] @ git+${GIT_URL}@${REF}" >&2
  touch "$MARKER"
fi

if [ ! -x "$BINARY" ]; then
  echo "wikibricks-recorder: binary not found at $BINARY after install." >&2
  echo "Reinstall with: rm -rf $DATA_DIR && restart Claude Code session." >&2
  exit 1
fi

exec "$BINARY" "${@:2}"
