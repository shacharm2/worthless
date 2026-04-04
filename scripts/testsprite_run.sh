#!/usr/bin/env bash
# Wrapper for TestSprite CLI that sources API_KEY from .env
# The MCP server gets API_KEY via .mcp.json env config, but when it
# delegates to the CLI subprocess, that process only inherits the
# shell's env. This script bridges the gap.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Export all vars from .env (API_KEY, etc.)
set -a
# shellcheck source=/dev/null
source "$REPO_ROOT/.env"
set +a

exec npx -y @testsprite/mcp-server generateCodeAndExecute "$@"
