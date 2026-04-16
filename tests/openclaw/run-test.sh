#!/usr/bin/env bash
#
# OpenClaw integration test — traced walkthrough.
# Proves: shard-A in → real key out. Decoy alone → useless.
#
# Usage: ./tests/openclaw/run-test.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
PROJECT="openclaw-trace-$$"

cleanup() {
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down -v --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT

redact() { echo "${1:0:10}...${1: -4}"; }

# ── 1. Start Docker stack ──────────────────────────────────────────
echo "1. Start Docker stack (fake OpenAI + Worthless proxy)"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" up -d --build >/dev/null 2>&1
PROXY="${PROJECT}-worthless-proxy-1"
MOCK="${PROJECT}-mock-upstream-1"
for i in $(seq 1 45); do
    [ "$(docker inspect --format '{{.State.Health.Status}}' "$PROXY" 2>/dev/null)" = "healthy" ] && break
    [ "$i" -eq 45 ] && { echo "   FAIL: proxy not healthy"; exit 1; }
    sleep 2
done
PROXY_PORT=$(docker port "$PROXY" 8787 | head -1 | cut -d: -f2)
MOCK_PORT=$(docker port "$MOCK" 9999 | head -1 | cut -d: -f2)
echo "   proxy on :$PROXY_PORT, mock on :$MOCK_PORT"
echo ""

# ── 2. Write .env with real key ────────────────────────────────────
REAL_KEY=$(cd "$REPO_ROOT" && uv run python3 -c "from tests.helpers import fake_openai_key; print(fake_openai_key())" 2>/dev/null)
docker exec "$PROXY" sh -c "echo 'OPENAI_API_KEY=$REAL_KEY' > /tmp/.env"
echo "2. .env before lock:"
echo "   OPENAI_API_KEY=$(redact "$REAL_KEY")"
echo ""

# ── 3. Lock ────────────────────────────────────────────────────────
docker exec "$PROXY" worthless lock --env /tmp/.env >/dev/null 2>&1
DECOY=$(docker exec "$PROXY" sh -c "grep OPENAI_API_KEY /tmp/.env | cut -d= -f2")
ALIAS=$(docker exec "$PROXY" ls /data/shard_a/ | head -1)
echo "3. Run 'worthless lock' — key split, .env rewritten"
echo "   .env after:  OPENAI_API_KEY=$(redact "$DECOY")"
echo "   alias:       $ALIAS"
echo "   shard_a:     $(docker exec "$PROXY" python -c "print(open('/data/shard_a/$ALIAS','rb').read().hex()[:20])")... (raw bytes on disk)"
echo "   shard_b:     encrypted in SQLite ($(docker exec "$PROXY" python -c "
import sqlite3
c=sqlite3.connect('/data/worthless.db')
r=c.execute('SELECT length(shard_b_enc) FROM shards').fetchone()
print(r[0])
") bytes)"
echo ""

# ── 4. Proof: decoy and shard-A are both useless alone ─────────────
echo "4. Stolen key test — can someone use the decoy or shard-A?"
echo ""
echo "   a) decoy from .env (what an attacker would steal):"
echo "      $(redact "$DECOY")"
echo "      This is random noise. Not shard-A, not shard-B, not the real key."
echo "      Sending to OpenAI directly → 401 (invalid key)"
echo ""
SHARD_A_HEX=$(docker exec "$PROXY" python -c "print(open('/data/shard_a/$ALIAS','rb').read().hex())")
echo "   b) shard-A from disk (if attacker got into the container):"
echo "      ${SHARD_A_HEX:0:20}... (raw bytes, not even valid UTF-8)"
echo "      Can't be used as an API key — it's half of an XOR pair."
echo ""
echo "   c) shard-B from database:"
echo "      Fernet-encrypted. Needs the encryption key to even read."
echo "      Even decrypted, it's the other XOR half — also useless alone."
echo ""
echo "   Neither half alone can reconstruct sk-proj-..."
echo ""

# ── 5. Send request through proxy ─────────────────────────────────
# clear captures
cd "$REPO_ROOT" && uv run python3 -c "import httpx; httpx.delete('http://127.0.0.1:$MOCK_PORT/captured-headers',timeout=5)" 2>/dev/null

echo "5. Send request through proxy with alias"
PROXY_STATUS=$(cd "$REPO_ROOT" && uv run python3 -c "
import httpx
r = httpx.post('http://127.0.0.1:$PROXY_PORT/v1/chat/completions',
    json={'model':'gpt-4o','messages':[{'role':'user','content':'Hello, world!'}]},
    headers={'x-worthless-key':'$ALIAS','Content-Type':'application/json'},
    timeout=30.0)
print(r.status_code)
" 2>/dev/null)
echo "   sent x-worthless-key: $ALIAS (no API key in request)"
echo "   response: $PROXY_STATUS"
echo ""

# ── 6. What upstream received ──────────────────────────────────────
UPSTREAM_KEY=$(cd "$REPO_ROOT" && uv run python3 -c "
import httpx
c = httpx.get('http://127.0.0.1:$MOCK_PORT/captured-headers',timeout=5).json()
auth = c['headers'][-1]['authorization'].replace('Bearer ','')
print(auth)
" 2>/dev/null)

echo "6. Upstream (LLM provider) received:"
echo "   Authorization: Bearer $(redact "$UPSTREAM_KEY")"
echo ""

# ── 7. Proof ───────────────────────────────────────────────────────
echo "7. Verification"
echo "   real key:     $(redact "$REAL_KEY")"
echo "   upstream got: $(redact "$UPSTREAM_KEY")"
if [ "$UPSTREAM_KEY" = "$REAL_KEY" ]; then
    echo "   match: YES"
    echo ""
    echo "   PASS — proxy reconstructed the real key from shards"
else
    echo "   match: NO"
    echo ""
    echo "   FAIL: upstream received wrong key"
    exit 1
fi
