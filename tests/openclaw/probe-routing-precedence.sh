#!/usr/bin/env bash
# probe-routing-precedence.sh — close the remaining Phase 3 routing gaps (WOR-514).
#
# Probe A  PRECEDENCE: openclaw.json vs agents/main/agent/models.json — if both define
#          provider "openai" with DIFFERENT baseUrls, which one routes? (The design
#          rewrites openclaw.json; if models.json wins, the design has a hole.)
# Probe B  SECRETREF LIVE: apiKey as a SecretRef {source:env}; confirm rewriting baseUrl
#          still redirects (source scan said yes — prove it).
# Probe C  MULTI-AGENT: a second agent with its own provider table — does it route via
#          the top-level config or its own models.json?
# Probe D  WHAT REACHES THE PROXY: capture the Authorization header at the redirect
#          target — confirm shard-A (a fake/inert value), not a real key, is what arrives.
#
# Run:  bash tests/openclaw/probe-routing-precedence.sh
# Needs Docker. Self-cleaning. Reads hits via docker exec (no curl).
set -uo pipefail

IMG="${OPENCLAW_IMG:-ghcr.io/openclaw/openclaw:2026.5.3-1}"
NET=wor-rp-net
MOCKA=wor-rp-mockA
MOCKB=wor-rp-mockB
OC=wor-rp-oc
MOCK_IMG=wor-probe-mock
HERE="$(cd "$(dirname "$0")" && pwd)"

cleanup() {
  docker rm -f "$OC" "$MOCKA" "$MOCKB" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
say() { printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }

hits() { docker exec "$1" python -c \
  "import urllib.request,json;print(len(json.load(urllib.request.urlopen('http://localhost:9999/captured-headers'))['headers']))" \
  2>/dev/null || echo ERR; }
last_auth() { docker exec "$1" python -c \
  "import urllib.request,json;h=json.load(urllib.request.urlopen('http://localhost:9999/captured-headers'))['headers'];print((h[-1].get('authorization') or h[-1].get('x-api-key') or '?') if h else 'NONE')" \
  2>/dev/null || echo ERR; }
clear_hits() { docker exec "$1" python -c \
  "import urllib.request;urllib.request.urlopen(urllib.request.Request('http://localhost:9999/captured-headers',method='DELETE'))" \
  >/dev/null 2>&1 || true; }
oc() { docker exec "$OC" node openclaw.mjs "$@"; }
ocq() { docker exec "$OC" node openclaw.mjs "$@" >/dev/null 2>&1 || true; }

say "BUILD + START stack"
docker build -t "$MOCK_IMG" "$HERE/mock-upstream" >/dev/null
docker network create "$NET" >/dev/null
docker run -d --name "$MOCKA" --network "$NET" "$MOCK_IMG" >/dev/null
docker run -d --name "$MOCKB" --network "$NET" "$MOCK_IMG" >/dev/null
docker run -d --name "$OC" --network "$NET" -e OPENCLAW_ACCEPT_TERMS=yes --user node "$IMG" >/dev/null
for i in $(seq 1 30); do oc config get gateway >/dev/null 2>&1 && break; sleep 2; done

URLA="http://$MOCKA:9999/openai/v1"
URLB="http://$MOCKB:9999/openai/v1"

# ---------------------------------------------------------------------------
say "PROBE A — PRECEDENCE: openclaw.json(openai->mockA) vs models.json(openai->mockB)"
# openclaw.json provider -> mockA
ocq config set models.providers.openai '{"baseUrl":"'"$URLA"'","api":"openai-completions","models":[]}' --strict-json
ocq config set models.providers.openai.apiKey "sk-OPENCLAWJSON-aaaaaaaaaaaaaaaaaaaa"
# Locate / seed the per-agent models.json with a CONFLICTING openai -> mockB
echo "--- locating models.json ---"
docker exec "$OC" sh -c 'find /home/node/.openclaw -name models.json 2>/dev/null' || echo "(none yet)"
# Write a conflicting models.json provider table directly
docker exec "$OC" sh -c 'AG=/home/node/.openclaw/agents/main/agent; mkdir -p "$AG"; cat > "$AG/models.json" <<JSON
{ "providers": { "openai": { "baseUrl": "'"$URLB"'", "api": "openai-completions", "apiKey": "sk-MODELSJSON-bbbbbbbbbbbbbbbbbbbb", "models": [] } } }
JSON
echo "wrote $AG/models.json"'
docker restart "$OC" >/dev/null; for i in $(seq 1 30); do oc config get gateway >/dev/null 2>&1 && break; sleep 2; done
clear_hits "$MOCKA"; clear_hits "$MOCKB"
echo "--- infer ---"; oc infer model run --model openai/gpt-4o --prompt hi --local --json 2>&1 | tail -3 || true
A_a=$(hits "$MOCKA"); A_b=$(hits "$MOCKB")
echo "PRECEDENCE result: openclaw.json/mockA=$A_a   models.json/mockB=$A_b"
echo "  mockA wins -> openclaw.json is authoritative for routing (design safe)."
echo "  mockB wins -> models.json overrides; design MUST also rewrite models.json."

# ---------------------------------------------------------------------------
say "PROBE B — SECRETREF LIVE: apiKey as {source:env}; rewrite baseUrl; still redirects?"
# clean slate: remove the conflicting models.json so only openclaw.json drives
docker exec "$OC" sh -c 'rm -f /home/node/.openclaw/agents/main/agent/models.json'
ocq config set models.providers.openai '{"baseUrl":"'"$URLA"'","api":"openai-completions","apiKey":{"source":"env","id":"OPENAI_PROBE_KEY"},"models":[]}' --strict-json
docker restart "$OC" >/dev/null; for i in $(seq 1 30); do oc config get gateway >/dev/null 2>&1 && break; sleep 2; done
clear_hits "$MOCKA"; clear_hits "$MOCKB"
echo "--- infer with SecretRef apiKey, baseUrl=mockA ---"
docker exec -e OPENAI_PROBE_KEY="sk-SECRETREF-cccccccccccccccccccc" "$OC" \
  node openclaw.mjs infer model run --model openai/gpt-4o --prompt hi --local --json 2>&1 | tail -3 || true
echo "secretref baseline: mockA=$(hits "$MOCKA")  mockB=$(hits "$MOCKB")"
# rewrite baseUrl -> mockB, keep SecretRef apiKey
ocq config set models.providers.openai.baseUrl "$URLB"
docker restart "$OC" >/dev/null; for i in $(seq 1 30); do oc config get gateway >/dev/null 2>&1 && break; sleep 2; done
clear_hits "$MOCKA"; clear_hits "$MOCKB"
echo "--- infer with SecretRef apiKey, baseUrl rewritten to mockB ---"
docker exec -e OPENAI_PROBE_KEY="sk-SECRETREF-cccccccccccccccccccc" "$OC" \
  node openclaw.mjs infer model run --model openai/gpt-4o --prompt hi --local --json 2>&1 | tail -3 || true
B_a=$(hits "$MOCKA"); B_b=$(hits "$MOCKB")
echo "SECRETREF redirect result: mockA=$B_a  mockB=$B_b   (mockB hit => SecretRef honors baseUrl)"
echo "auth header seen at mockB: $(last_auth "$MOCKB")  (should be the RESOLVED secret, proving cred is independent of routing)"

# ---------------------------------------------------------------------------
say "PROBE C — MULTI-AGENT: does a second agent route via top-level openclaw.json?"
echo "--- list agents ---"; oc agents list 2>&1 | tail -15 || true
echo "(If a second agent exists with its own provider table, repeat infer with --agent <id> and compare.)"
echo "--- agent turn on default agent, baseUrl currently mockB ---"
clear_hits "$MOCKA"; clear_hits "$MOCKB"
oc agent --local --session-id rp-sess --message hi --json 2>&1 | tail -4 || true
echo "agent-path routing: mockA=$(hits "$MOCKA")  mockB=$(hits "$MOCKB")"

# ---------------------------------------------------------------------------
say "PROBE D — WHAT REACHES THE PROXY (shard-A vs real key)"
# Simulate the lock rewrite: baseUrl=proxy(mockB), apiKey=shard-A-like inert string
ocq config set models.providers.openai '{"baseUrl":"'"$URLB"'","api":"openai-completions","apiKey":"WORTHLESS-SHARDA-deadbeefdeadbeefdeadbeef","models":[]}' --strict-json
docker restart "$OC" >/dev/null; for i in $(seq 1 30); do oc config get gateway >/dev/null 2>&1 && break; sleep 2; done
clear_hits "$MOCKB"
oc infer model run --model openai/gpt-4o --prompt hi --local --json >/dev/null 2>&1 || true
echo "auth header arriving at proxy(mockB): $(last_auth "$MOCKB")"
echo "  -> should be the shard-A value 'WORTHLESS-SHARDA-...', NOT a real sk- key."

say "DONE — summary"
echo "A precedence:  openclaw.json/mockA=$A_a  models.json/mockB=$A_b"
echo "B secretref :  baseUrl-rewrite mockA=$B_a  mockB=$B_b"
echo "D proxy sees:  $(last_auth "$MOCKB")"
