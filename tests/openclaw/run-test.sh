#!/usr/bin/env bash
#
# OpenClaw integration test — manual one-shot validation.
#
# Builds a 2-container stack (mock-upstream + worthless-proxy),
# enrolls a test key, triggers a completion through the proxy,
# and verifies the real key (not shard-A) reached the mock upstream.
#
# Usage:
#   ./tests/openclaw/run-test.sh
#
# Exit codes:
#   0 — PASS
#   1 — FAIL
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
PROJECT="openclaw-manual-$$"
ALIAS="openai-octest1"

cleanup() {
    echo "Tearing down..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down -v --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT

echo "=== OpenClaw Integration Test ==="
echo ""

# 1. Generate fake key
echo "Generating fake key..."
FAKE_KEY=$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from tests.helpers import fake_openai_key
print(fake_openai_key())
")
echo "  Fake key: ${FAKE_KEY:0:12}...${FAKE_KEY: -4}"

# 2. Build and start the stack
echo "Building and starting Docker stack..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" up -d --build

# 3. Wait for worthless-proxy
echo "Waiting for services..."
PROXY_CONTAINER="${PROJECT}-worthless-proxy-1"
MOCK_CONTAINER="${PROJECT}-mock-upstream-1"

for i in $(seq 1 45); do
    STATUS=$(docker inspect --format '{{.State.Health.Status}}' "$PROXY_CONTAINER" 2>/dev/null || echo "unknown")
    if [ "$STATUS" = "healthy" ]; then
        echo "  worthless-proxy is healthy"
        break
    fi
    if [ "$i" -eq 45 ]; then
        echo "FAIL: worthless-proxy did not become healthy (status: $STATUS)"
        docker logs "$PROXY_CONTAINER" 2>&1 | tail -20
        exit 1
    fi
    sleep 2
done

# 4. Discover dynamic ports
PROXY_PORT=$(docker port "$PROXY_CONTAINER" 8787 | head -1 | cut -d: -f2)
MOCK_PORT=$(docker port "$MOCK_CONTAINER" 9999 | head -1 | cut -d: -f2)
echo "  Proxy port: $PROXY_PORT"
echo "  Mock port:  $MOCK_PORT"

# 5. Enroll the fake key
echo "Enrolling test key..."
echo -n "$FAKE_KEY" | docker exec -i "$PROXY_CONTAINER" \
    worthless enroll --alias "$ALIAS" --key-stdin --provider openai

# 6. Clear captured headers
python3 -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:$MOCK_PORT/captured-headers', method='DELETE'))" 2>/dev/null || true

# 7. Send a request through the proxy (alias inference)
echo "Sending request through proxy (alias inference)..."
RESPONSE=$(python3 -c "
import json, urllib.request
data = json.dumps({'model': 'gpt-4o', 'messages': [{'role': 'user', 'content': 'test'}]}).encode()
req = urllib.request.Request(
    'http://127.0.0.1:$PROXY_PORT/v1/chat/completions',
    data=data,
    headers={'Content-Type': 'application/json'},
)
resp = urllib.request.urlopen(req)
print(resp.status)
")
echo "  Proxy response status: $RESPONSE"

# 8. Check what the mock upstream received
echo "Checking upstream Authorization header..."
UPSTREAM_AUTH=$(python3 -c "
import json, urllib.request
resp = urllib.request.urlopen('http://127.0.0.1:$MOCK_PORT/captured-headers')
data = json.loads(resp.read())
if data['headers']:
    print(data['headers'][-1]['authorization'])
else:
    print('NO_HEADERS_CAPTURED')
")

echo ""
echo "=== Results ==="
echo "  Real key:      Bearer ${FAKE_KEY:0:12}...${FAKE_KEY: -4}"
echo "  Upstream got:  ${UPSTREAM_AUTH:0:19}...${UPSTREAM_AUTH: -4}"

# 9. Assert
if [ "$UPSTREAM_AUTH" = "Bearer $FAKE_KEY" ]; then
    echo ""
    echo "PASS: Mock upstream received the REAL key (reconstructed from shards)"
    exit 0
else
    echo ""
    echo "FAIL: Upstream did not receive the expected key"
    exit 1
fi
