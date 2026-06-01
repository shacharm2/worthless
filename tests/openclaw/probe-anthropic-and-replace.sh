#!/usr/bin/env bash
# probe-anthropic-and-replace.sh — close the two open matrix cells (WOR-621 routing-behavior).
#
# PART 1 (HIGH) — Anthropic api type: every prior probe used `openai-completions`. OpenClaw
#   treats api types differently (beta-header suppression). Does rewriting an anthropic-messages
#   provider's baseUrl redirect routing the same way? Mock serves /v1/messages.
# PART 2 (MED) — models.mode = "replace": prior probes used the default "merge" (which preserves
#   the stale agent models.json baseUrl). Does "replace" change precedence?
#
# No real Anthropic key: routing is value-independent; apiKey is a placeholder; mock upstream answers.
set -uo pipefail
IMG="${OPENCLAW_IMG:-ghcr.io/openclaw/openclaw:2026.5.3-1}"
NET=wor-ar-net; MOCKA=wor-ar-mockA; MOCKB=wor-ar-mockB; OC=wor-ar-oc; MOCK_IMG=wor-probe-mock
HERE="$(cd "$(dirname "$0")" && pwd)"
cleanup(){ docker rm -f "$OC" "$MOCKA" "$MOCKB" >/dev/null 2>&1||true; docker network rm "$NET" >/dev/null 2>&1||true; }
trap cleanup EXIT; cleanup
say(){ printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }
hits(){ docker exec "$1" python -c "import urllib.request,json;print(len(json.load(urllib.request.urlopen('http://localhost:9999/captured-headers'))['headers']))" 2>/dev/null||echo ERR; }
clr(){ docker exec "$1" python -c "import urllib.request;urllib.request.urlopen(urllib.request.Request('http://localhost:9999/captured-headers',method='DELETE'))" >/dev/null 2>&1||true; }
oc(){ docker exec "$OC" node openclaw.mjs "$@"; }
ocq(){ docker exec "$OC" node openclaw.mjs "$@" >/dev/null 2>&1||true; }
wait_oc(){ for i in $(seq 1 30); do oc config get gateway >/dev/null 2>&1 && return; sleep 2; done; }
route_all(){ # $1 = model ; prints mockA/mockB for infer, agent-local, gateway
  for d in "infer:infer model run --model $1 --prompt hi --local --json" \
           "agentlocal:agent --local --session-id s --message hi --json" \
           "gateway:agent --session-id g --message hi --json"; do
    n="${d%%:*}"; c="${d#*:}"; clr "$MOCKA"; clr "$MOCKB"
    docker exec "$OC" node openclaw.mjs $c >/dev/null 2>&1 || true
    echo "  [$n] mockA=$(hits "$MOCKA")  mockB=$(hits "$MOCKB")"
  done
}

say "BUILD + START"
docker build -t "$MOCK_IMG" "$HERE/mock-upstream" >/dev/null
docker network create "$NET" >/dev/null
docker run -d --name "$MOCKA" --network "$NET" "$MOCK_IMG" >/dev/null
docker run -d --name "$MOCKB" --network "$NET" "$MOCK_IMG" >/dev/null
docker run -d --name "$OC" --network "$NET" -e OPENCLAW_ACCEPT_TERMS=yes --user node "$IMG" >/dev/null
wait_oc

# ===========================================================================
say "PART 1 — ANTHROPIC api type: provider baseUrl mockA -> rewrite mockB"
ocq config set models.providers.anthro '{"baseUrl":"http://'"$MOCKA"':9999","api":"anthropic-messages","models":[{"id":"claude-3-5-haiku","name":"Claude 3.5 Haiku"}]}' --strict-json
ocq config set models.providers.anthro.apiKey "sk-ant-PLACEHOLDER-lowentropy-aaaa"
ocq config set agents.defaults.model.primary anthro/claude-3-5-haiku
docker restart "$OC" >/dev/null; wait_oc
echo "baseline (baseUrl=mockA): expect mockA=1"; route_all anthro/claude-3-5-haiku
echo "--- rewrite ONLY provider baseUrl -> mockB; restart ---"
ocq config set models.providers.anthro.baseUrl "http://$MOCKB:9999"
docker restart "$OC" >/dev/null; wait_oc
echo "after rewrite -> mockB:"; route_all anthro/claude-3-5-haiku
echo "VERDICT P1: mockB on all paths => anthropic api type follows baseUrl, same as openai. mockA => api-type-specific bypass."

# ===========================================================================
say "PART 2 — models.mode = replace: openclaw.json=mockB vs stale populated agent models.json=mockA"
# Fresh openai provider -> mockB (post-lock proxy)
ocq config set models.providers.openai '{"baseUrl":"http://'"$MOCKB"':9999/openai/v1","api":"openai-completions","models":[]}' --strict-json
ocq config set models.providers.openai.apiKey "sk-SEED-lowentropy-aaaaaaaaaaaa"
ocq config set agents.defaults.model.primary openai/gpt-4o
ocq config set models.mode replace
docker restart "$OC" >/dev/null; wait_oc
# Hand-write a stale POPULATED agent models.json -> mockA
docker exec "$OC" sh -c 'AG=/home/node/.openclaw/agents/main/agent; mkdir -p "$AG"; cat > "$AG/models.json" <<JSON
{ "providers": { "openai": { "baseUrl": "http://'"$MOCKA"':9999/openai/v1", "api": "openai-completions", "apiKey": "sk-STALE-aaaaaaaaaaaaaaaaaaaa", "models": [ { "id": "gpt-4o", "name": "gpt-4o" } ] } } }
JSON
echo "wrote stale populated agent models.json -> mockA"'
docker restart "$OC" >/dev/null; wait_oc
echo "mode=$(oc config get models.mode 2>/dev/null || echo '?')"
echo "agent models.json baseUrl after restart: $(docker exec "$OC" sh -c 'cat /home/node/.openclaw/agents/main/agent/models.json' | python3 -c "import sys,json;print(json.load(sys.stdin).get('providers',{}).get('openai',{}).get('baseUrl','<none>'))" 2>/dev/null)"
echo "route openai/gpt-4o under replace mode:"; route_all openai/gpt-4o
echo "VERDICT P2: mockB => openclaw.json authoritative under replace too. mockA => replace preserved stale agent file."

say "SUMMARY"
echo "P1 anthropic: see 'after rewrite -> mockB' block above"
echo "P2 replace:   see 'route openai/gpt-4o under replace mode' block above"
