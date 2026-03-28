#!/usr/bin/env bash
# smoke-test.sh — Self-contained lock→unlock round-trip (WOR-30)
#
# Runs the full worthless CLI lifecycle against a fake .env in a temp dir:
#   1. worthless lock        — expect exit 0, 2 keys enrolled
#   2. cat .env              — API keys changed, DATABASE_URL unchanged
#   3. worthless status      — expect exit 0, 2 protected keys
#   4. worthless status --json — valid JSON with key count
#   5. worthless scan .      — exit 0, no unprotected keys
#   6. worthless unlock      — exit 0
#   7. cat .env              — SHA256 matches original (perfect round-trip)
#
# No real API keys, no network calls.  Exit 0/1.  macOS + Linux.
set -euo pipefail

# ── colour helpers (NO_COLOR + pipe detection) ───────────────────
if [ -n "${NO_COLOR:-}" ] || [ ! -t 1 ]; then
  OK="ok"; FAIL="FAIL"; BOLD=""; RESET=""
else
  OK='\033[0;32m✓\033[0m'; FAIL='\033[0;31m✗\033[0m'; BOLD='\033[1m'; RESET='\033[0m'
fi

STEP=0
PASSED=0
TOTAL=7

run_step() {
  STEP=$((STEP + 1))
  local label="$1"; shift
  if "$@" ; then
    printf " ${OK} %s\n" "$label"
    PASSED=$((PASSED + 1))
    return 0
  else
    printf " ${FAIL} %s\n" "$label"
    printf "smoke-test: FAILED at step %d (%s)\n" "$STEP" "$label"
    exit 1
  fi
}

# ── portable SHA-256 (macOS shasum vs Linux sha256sum) ───────────
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# ── temp dir + cleanup via trap ──────────────────────────────────
TMPDIR_SMOKE="$(mktemp -d "${TMPDIR:-/tmp}/worthless-smoke.XXXXXX")"
FAKE_HOME="$TMPDIR_SMOKE/dot-worthless"

cleanup() {
  rm -rf "$TMPDIR_SMOKE"
}
trap cleanup EXIT

# ── resolve the worthless command ────────────────────────────────
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if command -v uv >/dev/null 2>&1; then
  WORTHLESS="uv run --project $REPO_ROOT worthless"
else
  WORTHLESS="worthless"
fi

# Point state at temp dir, not real ~/.worthless/
export WORTHLESS_HOME="$FAKE_HOME"

# ── seed a fake .env ─────────────────────────────────────────────
WORK_DIR="$TMPDIR_SMOKE/project"
mkdir -p "$WORK_DIR"
ENV_FILE="$WORK_DIR/.env"
cat > "$ENV_FILE" <<'DOTENV'
OPENAI_API_KEY=sk-proj-abc123fakekeynotreal456789012345678901234567
ANTHROPIC_API_KEY=sk-ant-api03-fakekeyT3st1ng0nly9x8w7v6u5t4s3r2q1p0o9n8m7L6k5j
DATABASE_URL=postgres://localhost/mydb
DOTENV

HASH_BEFORE="$(sha256 "$ENV_FILE")"
DB_URL_BEFORE="$(grep '^DATABASE_URL=' "$ENV_FILE")"

printf "${BOLD}worthless smoke test${RESET}\n"

# ── step 1: lock ─────────────────────────────────────────────────
assert_lock() {
  local output
  output="$($WORTHLESS lock --env "$ENV_FILE" 2>&1)"
  # Check that 2 keys were enrolled
  echo "$output" | grep -qiE '2 key'
}
run_step "lock: 2 keys enrolled" assert_lock

# ── step 2: .env rewritten correctly ─────────────────────────────
assert_env_rewritten() {
  # API key values must have changed
  ! grep -q 'sk-proj-abc123fakekeynotreal' "$ENV_FILE" &&
  ! grep -q 'sk-ant-api03-fakekeyT3st1ng0nly' "$ENV_FILE" &&
  # DATABASE_URL must be unchanged
  grep -q "^${DB_URL_BEFORE}$" "$ENV_FILE"
}
run_step "lock: API keys replaced, DATABASE_URL unchanged" assert_env_rewritten

# ── step 3: status ───────────────────────────────────────────────
assert_status() {
  $WORTHLESS status 2>&1 | grep -qiE '(protected|enrolled)'
}
run_step "status: 2 protected keys reported" assert_status

# ── step 4: status --json ────────────────────────────────────────
assert_status_json() {
  local json_out
  json_out="$($WORTHLESS --json status 2>/dev/null)"
  # Must be valid JSON with a keys array
  echo "$json_out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
keys = d.get('keys', [])
assert len(keys) == 2, f'expected 2 keys, got {len(keys)}'
"
}
run_step "status --json: valid JSON, 2 keys" assert_status_json

# ── step 5: scan ─────────────────────────────────────────────────
assert_scan() {
  # scan the project dir — should find 0 unprotected keys (exit 0)
  (cd "$WORK_DIR" && $WORTHLESS scan . 2>&1)
}
run_step "scan: no unprotected keys" assert_scan

# ── step 6: unlock ───────────────────────────────────────────────
assert_unlock() {
  $WORTHLESS unlock --env "$ENV_FILE" 2>&1
}
run_step "unlock: keys restored" assert_unlock

# ── step 7: round-trip integrity ─────────────────────────────────
assert_roundtrip() {
  local hash_after
  hash_after="$(sha256 "$ENV_FILE")"
  [ "$HASH_BEFORE" = "$hash_after" ]
}
run_step "round-trip: .env SHA256 matches original" assert_roundtrip

# ── summary ──────────────────────────────────────────────────────
printf "smoke-test: %d/%d passed\n" "$PASSED" "$TOTAL"
exit 0
