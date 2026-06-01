#!/usr/bin/env bash
# probe-populated-modelsjson.sh — THE decisive cell (Karen's residual #1), WOR-621/WOR-514.
#
# The earlier probes used an agent models.json with "models": [] (empty), derived from an
# empty-model openclaw.json. Cursor's objection + OpenClaw's docs ("non-empty baseUrl in the
# agent models.json wins") + the mergeWithExistingProviderSecrets source say a POPULATED agent
# models.json with a divergent baseUrl may win over openclaw.json. That cell was never run.
#
# This constructs the REAL incident state and tests the GATEWAY path (Ido's path):
#   openclaw.json openai.baseUrl = mockB  (as if lock already rewrote it to the proxy)
#   agent models.json openai     = POPULATED (real model rows) + baseUrl = mockA (stale/original)
# Then: does a gateway agent turn hit mockA (Cursor RIGHT: lock MUST rewrite models.json too)
#       or mockB (openclaw.json wins even with a populated stale models.json)?
set -uo pipefail
IMG="${OPENCLAW_IMG:-ghcr.io/openclaw/openclaw:2026.5.3-1}"
NET=wor-pm-net; MOCKA=wor-pm-mockA; MOCKB=wor-pm-mockB; OC=wor-pm-oc; MOCK_IMG=wor-probe-mock
HERE="$(cd "$(dirname "$0")" && pwd)"
cleanup(){ docker rm -f "$OC" "$MOCKA" "$MOCKB" >/dev/null 2>&1||true; docker network rm "$NET" >/dev/null 2>&1||true; }
trap cleanup EXIT; cleanup
say(){ printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }
hits(){ docker exec "$1" python -c "import urllib.request,json;print(len(json.load(urllib.request.urlopen('http://localhost:9999/captured-headers'))['headers']))" 2>/dev/null||echo ERR; }
clr(){ docker exec "$1" python -c "import urllib.request;urllib.request.urlopen(urllib.request.Request('http://localhost:9999/captured-headers',method='DELETE'))" >/dev/null 2>&1||true; }
oc(){ docker exec "$OC" node openclaw.mjs "$@"; }
ocq(){ docker exec "$OC" node openclaw.mjs "$@" >/dev/null 2>&1||true; }
wait_oc(){ for i in $(seq 1 30); do oc config get gateway >/dev/null 2>&1 && return; sleep 2; done; }

say "BUILD + START"
docker build -t "$MOCK_IMG" "$HERE/mock-upstream" >/dev/null
docker network create "$NET" >/dev/null
docker run -d --name "$MOCKA" --network "$NET" "$MOCK_IMG" >/dev/null
docker run -d --name "$MOCKB" --network "$NET" "$MOCK_IMG" >/dev/null
docker run -d --name "$OC" --network "$NET" -e OPENCLAW_ACCEPT_TERMS=yes --user node "$IMG" >/dev/null
wait_oc
URLA="http://$MOCKA:9999/openai/v1"; URLB="http://$MOCKB:9999/openai/v1"

say "STEP 1 — seed openclaw.json openai -> mockB (POST-LOCK: proxy); restart; let agent models.json generate"
ocq config set models.providers.openai '{"baseUrl":"'"$URLB"'","api":"openai-completions","models":[]}' --strict-json
ocq config set models.providers.openai.apiKey "sk-SEED-bbbbbbbbbbbbbbbbbbbbbbbb"
ocq config set agents.defaults.model.primary openai/gpt-4o
docker restart "$OC" >/dev/null; wait_oc
oc infer model run --model openai/gpt-4o --prompt warmup --local --json >/dev/null 2>&1 || true
docker restart "$OC" >/dev/null; wait_oc

say "STEP 2 — OVERWRITE agent models.json with a POPULATED table + DIVERGENT baseUrl (mockA)"
# Hand-write directly (bypasses --strict-json) to construct the real incident state.
docker exec "$OC" sh -c 'AG=/home/node/.openclaw/agents/main/agent; mkdir -p "$AG"; cat > "$AG/models.json" <<JSON
{ "providers": { "openai": {
    "baseUrl": "'"$URLA"'",
    "api": "openai-completions",
    "apiKey": "sk-STALE-aaaaaaaaaaaaaaaaaaaaaaaa",
    "models": [ { "id": "gpt-4o", "name": "gpt-4o" }, { "id": "gpt-4o-mini", "name": "gpt-4o-mini" } ]
} } }
JSON
echo "wrote populated agent models.json:"; cat "$AG/models.json"'
docker restart "$OC" >/dev/null; wait_oc
echo "--- post-restart: where does each file point? ---"
echo -n "openclaw.json openai.baseUrl: "; docker exec "$OC" sh -c 'cat /home/node/.openclaw/openclaw.json' | python3 -c "import sys,json;print(json.load(sys.stdin).get('models',{}).get('providers',{}).get('openai',{}).get('baseUrl','<none>'))" 2>/dev/null
echo -n "agent models.json openai.baseUrl: "; docker exec "$OC" sh -c 'cat /home/node/.openclaw/agents/main/agent/models.json' | python3 -c "import sys,json;print(json.load(sys.stdin).get('providers',{}).get('openai',{}).get('baseUrl','<none>'))" 2>/dev/null

say "STEP 3 — DECISIVE: gateway + local + infer, populated stale models.json vs proxy openclaw.json"
for desc in "infer:infer model run --model openai/gpt-4o --prompt hi --local --json" \
            "agentlocal:agent --local --session-id pm-l --message hi --json" \
            "gateway:agent --session-id pm-gw --message hi --json"; do
  name="${desc%%:*}"; cmd="${desc#*:}"
  clr "$MOCKA"; clr "$MOCKB"
  docker exec "$OC" node openclaw.mjs $cmd >/dev/null 2>&1 || true
  echo "[$name] mockA(stale models.json)=$(hits "$MOCKA")  mockB(proxy openclaw.json)=$(hits "$MOCKB")"
done

say "VERDICT"
echo "mockA hit -> CURSOR RIGHT: populated agent models.json baseUrl WINS; lock MUST rewrite models.json too."
echo "mockB hit -> openclaw.json authoritative even vs a populated stale models.json; one-file rewrite suffices."
echo "neither   -> populated models.json rejected/call failed; inspect."
