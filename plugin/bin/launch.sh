#!/usr/bin/env bash
# Idempotent launcher for the WikiBricks Claude Code plugin.
#
# The first call installs WikiBricks from the marketplace checkout into the
# plugin's persistent data directory. Later calls reuse that installation.
# Subsequent calls: exec the requested console-script binary directly.
#
# Usage: launch.sh <binary-name> [args...]
#   e.g. launch.sh wikibricks-hook
#        launch.sh wikibricks-mcp

set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "launch.sh: usage: launch.sh <binary> [args...]" >&2
  exit 64
fi

PACKAGE_ROOT="$(cd "${CLAUDE_PLUGIN_ROOT}/.." && pwd -P)"
DATA_DIR="${CLAUDE_PLUGIN_DATA:-${HOME}/.claude/plugins/data/wikibricks}"
TOOL_DIR="${DATA_DIR}/uv-tools"
BIN_DIR="${DATA_DIR}/bin"
MARKER="${DATA_DIR}/installed-0.9.0"
BINARY="${BIN_DIR}/$1"

if [ ! -f "$MARKER" ] || [ ! -x "$BINARY" ]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "wikibricks: 'uv' is not on PATH. Install it first:" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    echo "Then restart your Claude Code session." >&2
    exit 127
  fi
  mkdir -p "$TOOL_DIR" "$BIN_DIR"
  UV_TOOL_DIR="$TOOL_DIR" UV_TOOL_BIN_DIR="$BIN_DIR" \
    uv tool install --force \
      "${PACKAGE_ROOT}" >&2
  touch "$MARKER"
fi

if [ ! -x "$BINARY" ]; then
  echo "wikibricks: binary not found at $BINARY after install." >&2
  echo "Reinstall with: rm -rf $DATA_DIR && restart Claude Code session." >&2
  exit 1
fi

exec "$BINARY" "${@:2}"
