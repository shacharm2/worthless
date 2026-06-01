#!/usr/bin/env bash
# probe-per-model-baseurl.sh — residual #3 (Karen), WOR-621/WOR-514. The last routing unknown.
#
# ModelDefinitionSchema has an optional per-model baseUrl. Question: does a per-model baseUrl
# override the PROVIDER baseUrl at routing time? If yes, lock rewriting only the provider
# baseUrl leaves a per-model baseUrl pinning the OLD endpoint -> another bypass lock must clear.
#
# State: openclaw.json openai.baseUrl = mockB (proxy, post-lock); but the gpt-4o model row
# carries its OWN baseUrl = mockA (stale). Route openai/gpt-4o: mockA (per-model wins) or mockB?
set -uo pipefail
IMG="${OPENCLAW_IMG:-ghcr.io/openclaw/openclaw:2026.5.3-1}"
NET=wor-pmb-net; MOCKA=wor-pmb-mockA; MOCKB=wor-pmb-mockB; OC=wor-pmb-oc; MOCK_IMG=wor-probe-mock
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

say "STEP 1 — provider baseUrl = mockB (proxy); per-model baseUrl on gpt-4o = mockA (stale)"
ocq config set models.providers.openai '{"baseUrl":"'"$URLB"'","api":"openai-completions","models":[]}' --strict-json
ocq config set models.providers.openai.apiKey "sk-SEED-bbbbbbbbbbbbbbbbbbbbbbbb"
# Try to set a per-model baseUrl via config; if strict rejects, hand-write the model row.
if ! oc config set models.providers.openai.models '[{"id":"gpt-4o","name":"gpt-4o","baseUrl":"'"$URLA"'"}]' --strict-json >/dev/null 2>&1; then
  echo "(config set models rejected; hand-writing the provider entry with a per-model baseUrl)"
  docker exec "$OC" sh -c 'cat > /tmp/prov.json <<JSON
{"baseUrl":"'"$URLB"'","api":"openai-completions","apiKey":"sk-SEED-bbbbbbbbbbbbbbbbbbbbbbbb","models":[{"id":"gpt-4o","name":"gpt-4o","baseUrl":"'"$URLA"'"}]}
JSON
node openclaw.mjs config set models.providers.openai "$(cat /tmp/prov.json)" --strict-json' || echo "(hand-write also failed; per-model baseUrl may be schema-rejected)"
fi
ocq config set agents.defaults.model.primary openai/gpt-4o
docker restart "$OC" >/dev/null; wait_oc
echo "--- resolved config ---"
echo -n "provider baseUrl: "; docker exec "$OC" sh -c 'cat /home/node/.openclaw/openclaw.json' | python3 -c "import sys,json;p=json.load(sys.stdin).get('models',{}).get('providers',{}).get('openai',{});print(p.get('baseUrl','<none>'))" 2>/dev/null
echo -n "per-model gpt-4o baseUrl: "; docker exec "$OC" sh -c 'cat /home/node/.openclaw/openclaw.json' | python3 -c "import sys,json;ms=json.load(sys.stdin).get('models',{}).get('providers',{}).get('openai',{}).get('models',[]);print(next((m.get('baseUrl','<none>') for m in ms if m.get('id')=='gpt-4o'),'<no gpt-4o row>'))" 2>/dev/null

say "STEP 2 — route openai/gpt-4o on all three paths"
for desc in "infer:infer model run --model openai/gpt-4o --prompt hi --local --json" \
            "agentlocal:agent --local --session-id pmb-l --message hi --json" \
            "gateway:agent --session-id pmb-gw --message hi --json"; do
  name="${desc%%:*}"; cmd="${desc#*:}"
  clr "$MOCKA"; clr "$MOCKB"
  docker exec "$OC" node openclaw.mjs $cmd >/dev/null 2>&1 || true
  echo "[$name] mockA(per-model stale)=$(hits "$MOCKA")  mockB(provider proxy)=$(hits "$MOCKB")"
done

say "VERDICT"
echo "mockA hit -> per-model baseUrl OVERRIDES provider; lock MUST clear/rewrite per-model baseUrls."
echo "mockB hit -> provider baseUrl authoritative; per-model baseUrl ignored (or absent). One-file provider rewrite suffices."
echo "neither   -> per-model baseUrl schema-rejected or call failed; inspect (also a useful result: schema won't accept it)."
