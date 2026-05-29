#!/usr/bin/env bash
#
# probe-uid-gate.sh — live proof that worthless lock exits 87 on Docker UID mismatch
#
# What it proves:
#   When openclaw.json is owned by a different OS user (the Docker two-UID topology),
#   `worthless lock` aborts before touching anything and exits 87.
#
# How it works:
#   Runs entirely inside a Docker Linux container, where UID isolation is real
#   (no macOS VirtioFS UID remapping). Inside the container:
#     1. Creates openclaw.json owned by UID 999 (the "openclaw daemon user")
#     2. Runs `worthless lock` as UID 500 (the "host developer user")
#     3. Verifies: exit code is 87, openclaw.json is byte-for-byte unchanged
#
# Usage:
#   ./tests/openclaw/probe-uid-gate.sh
#
# Requirements:
#   - Docker available and running
#   - Run from the repo root or any subdirectory

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[1;34m'
NC='\033[0m'

step() { echo -e "\n${BLUE}── $*${NC}"; }

# ── Verify Docker ─────────────────────────────────────────────────────────────
step "0. Verify Docker is running"
if ! docker info &>/dev/null; then
    echo -e "   ${RED}✗${NC}  Docker is not running — start Docker Desktop and retry"
    exit 1
fi
echo -e "   ${GREEN}✓${NC}  Docker running"

# ── Run the entire probe inside a single Docker container ─────────────────────
step "1–7. Running probe inside Docker (Python 3.12, Linux UID isolation)"
echo "   Repo mounted at /repo inside container"
echo "   Config owner: UID 999  |  worthless runner: UID 500"
echo ""

docker run --rm \
    --user root \
    -v "$REPO_ROOT:/repo" \
    -e UV_PROJECT_ENVIRONMENT=/tmp/.venv \
    -e UV_CACHE_DIR=/tmp/uv-cache \
    python:3.12-slim \
    bash -c '
set -euo pipefail

GREEN="\033[0;32m"
RED="\033[0;31m"
BLUE="\033[1;34m"
NC="\033[0m"

pass() { echo -e "   ${GREEN}✓${NC}  $*"; }
fail() { echo -e "   ${RED}✗${NC}  $*"; exit 1; }
step() { echo -e "\n${BLUE}── $*${NC}"; }

# Install uv
step "1. Install uv"
pip install uv --quiet
pass "uv installed"

# Create users
step "2. Create UID 999 (openclaw-user) and UID 500 (dev-user)"
useradd -u 999 -m openclaw-user 2>/dev/null || true
useradd -u 500 -m dev-user 2>/dev/null || true
pass "users created"

# Create openclaw config dir and file, owned by UID 999
step "3. Create openclaw.json owned by UID 999"
mkdir -p /home/dev-user/.openclaw
cat > /home/dev-user/.openclaw/openclaw.json <<'"'"'EOFJSON'"'"'
{
  "version": "1",
  "providers": {
    "anthropic": {
      "type": "api",
      "apiKey": "ANTHROPIC_API_KEY",
      "models": {}
    },
    "openai": {
      "type": "api",
      "apiKey": "OPENAI_API_KEY",
      "models": {}
    }
  }
}
EOFJSON
chown 999:999 /home/dev-user/.openclaw/openclaw.json
chown 500:500 /home/dev-user/.openclaw
chmod 755 /home/dev-user/.openclaw

SHA_BEFORE=$(sha256sum /home/dev-user/.openclaw/openclaw.json | awk "{print \$1}")
OWNER=$(stat -c "%u" /home/dev-user/.openclaw/openclaw.json)
echo "   config owner UID: $OWNER"
echo "   runner UID:       500"
echo "   config SHA:       $SHA_BEFORE"
pass "openclaw.json created, owned by UID 999"

# Create .env
step "4. Create test.env (fake format-correct key)"
cat > /home/dev-user/test.env <<'"'"'EOFENV'"'"'
ANTHROPIC_API_KEY=sk-ant-api03-probetestnotreal0000000000000000000000000000000000000000000000000000000000000000AA
EOFENV
chown 500:500 /home/dev-user/test.env
pass ".env written"

# Run worthless lock as UID 500
step "5. Run worthless lock as UID 500 (expect exit 87)"
echo "   HOME=/home/dev-user uv run worthless lock test.env"
echo ""

set +e
su dev-user -c "cd /home/dev-user && HOME=/home/dev-user uv run --project /repo worthless lock test.env 2>&1"
EXIT_CODE=$?
set -e

echo ""
echo "   exit code: $EXIT_CODE"

if [ "$EXIT_CODE" -eq 87 ]; then
    pass "exit 87 — UID gate fired correctly"
else
    fail "expected exit 87, got $EXIT_CODE — UID gate did NOT fire (bug)"
fi

# Verify SHA unchanged
step "6. Verify openclaw.json is byte-for-byte unchanged"
SHA_AFTER=$(sha256sum /home/dev-user/.openclaw/openclaw.json | awk "{print \$1}")
echo "   SHA before: $SHA_BEFORE"
echo "   SHA after:  $SHA_AFTER"

if [ "$SHA_BEFORE" = "$SHA_AFTER" ]; then
    pass "openclaw.json untouched — lock aborted before any write"
else
    fail "openclaw.json was MODIFIED — UID gate failed to protect the config (critical bug)"
fi

# Check error message
step "7. Check error message mentions the cause"
set +e
ERROR_MSG=$(su dev-user -c "cd /home/dev-user && HOME=/home/dev-user uv run --project /repo worthless lock test.env 2>&1")
set -e

if echo "$ERROR_MSG" | grep -qi "uid\|user\|docker\|owner\|permission\|unreadable\|87\|different"; then
    pass "error message references cause — user knows what to fix"
    echo "   → $(echo "$ERROR_MSG" | grep -v "^$" | head -2)"
else
    echo -e "   ${RED}WARNING${NC}: error message may be unclear to users"
    echo "   → $ERROR_MSG"
fi

echo ""
echo -e "   ${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "   ${GREEN}RESULT: Docker UID gate works correctly.${NC}"
echo "   worthless lock exits 87 and leaves openclaw.json"
echo "   untouched when the config is owned by a different user."
echo "   WOR-516 AC — live proof."
echo -e "   ${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
'
