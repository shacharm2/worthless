#!/usr/bin/env bash
# probe-modelsjson-precedence.sh — settle Cursor's claim-2 refutation (WOR-621 / WOR-514).
#
# CLAIM UNDER TEST (Cursor): the per-agent agents/main/agent/models.json baseUrl WINS over
# openclaw.json. If true, rewriting ONLY openclaw.json (the Phase 3 design) leaves traffic
# on the OLD endpoint — the design is incomplete and must also rewrite models.json.
#
# The earlier probe-routing-precedence.sh PROBE A wrote models.json with "models": [] (empty),
# which Cursor argues doesn't register as a real provider table — so openclaw.json "won" by
# default, an invalid test. This probe reproduces the REAL incident scenario: a populated
# agent models.json with a live baseUrl, THEN the naive openclaw.json-only rewrite.
#
# DECISIVE OUTCOMES:
#   STEP 3 (rewrite openclaw.json ONLY): mockA hit -> Cursor RIGHT, design incomplete.
#                                        mockB hit -> my original result holds, openclaw.json authoritative.
#   STEP 4 (also rewrite models.json):   mockB hit -> confirms the fix (rewrite BOTH).
set -uo pipefail
IMG="${OPENCLAW_IMG:-ghcr.io/openclaw/openclaw:2026.5.3-1}"
NET=wor-mp-net; MOCKA=wor-mp-mockA; MOCKB=wor-mp-mockB; OC=wor-mp-oc; MOCK_IMG=wor-probe-mock
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

say "STEP 1 — seed openclaw.json openai -> mockA (known-good shape); restart; let OpenClaw generate the agent models.json; baseline infer"
ocq config set models.providers.openai '{"baseUrl":"'"$URLA"'","api":"openai-completions","models":[]}' --strict-json
ocq config set models.providers.openai.apiKey "sk-SEED-aaaaaaaaaaaaaaaaaaaaaaaa"
ocq config set agents.defaults.model.primary openai/gpt-4o
docker restart "$OC" >/dev/null; wait_oc
# Drive one infer so OpenClaw generates/syncs the per-agent models.json from openclaw.json
oc infer model run --model openai/gpt-4o --prompt warmup --local --json >/dev/null 2>&1 || true
docker restart "$OC" >/dev/null; wait_oc
clr "$MOCKA"; clr "$MOCKB"
oc infer model run --model openai/gpt-4o --prompt hi --local --json >/dev/null 2>&1 || true
echo "baseline: mockA=$(hits "$MOCKA") mockB=$(hits "$MOCKB")  (expect mockA=1)"

say "STEP 2 — inspect where baseUrl actually lives after OpenClaw syncs"
echo "--- openclaw.json providers.openai.baseUrl ---"
docker exec "$OC" sh -c 'cat /home/node/.openclaw/openclaw.json' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('models',{}).get('providers',{}).get('openai',{}).get('baseUrl','<none>'))" 2>/dev/null || echo "(parse fail)"
echo "--- agent models.json (full provider table) ---"
docker exec "$OC" sh -c 'cat /home/node/.openclaw/agents/main/agent/models.json 2>/dev/null || echo "{}"' | python3 -c "import sys,json;d=json.load(sys.stdin);print(json.dumps(d.get('providers',{}).get('openai',{}),indent=2))" 2>/dev/null || echo "(no agent models.json / parse fail)"

say "STEP 3 — DECISIVE: rewrite ONLY openclaw.json openai.baseUrl -> mockB; restart; infer"
ocq config set models.providers.openai.baseUrl "$URLB"
docker restart "$OC" >/dev/null; wait_oc
clr "$MOCKA"; clr "$MOCKB"
oc infer model run --model openai/gpt-4o --prompt hi --local --json >/dev/null 2>&1 || true
S3A=$(hits "$MOCKA"); S3B=$(hits "$MOCKB")
echo "[infer --local] after openclaw.json-ONLY rewrite: mockA=$S3A  mockB=$S3B"
# DECISIVE: the AGENT path is the real incident path (Ido). Test it in the SAME state
# (openclaw.json=mockB, agent models.json still=mockA-preserved) BEFORE STEP 4 overwrites it.
clr "$MOCKA"; clr "$MOCKB"
oc agent --local --session-id mp-sess --message hi --json >/dev/null 2>&1 || true
S3AA=$(hits "$MOCKA"); S3AB=$(hits "$MOCKB")
echo "[agent --local] after openclaw.json-ONLY rewrite: mockA=$S3AA  mockB=$S3AB"
# THE decisive one: GATEWAY path (no --local) — this is Ido's real incident path (web UI / daemon).
clr "$MOCKA"; clr "$MOCKB"
echo "--- agent via GATEWAY (no --local) ---"
oc agent --session-id mp-gw-sess --message hi --json 2>&1 | tail -4 || true
S3GA=$(hits "$MOCKA"); S3GB=$(hits "$MOCKB")
echo "[agent GATEWAY] after openclaw.json-ONLY rewrite: mockA=$S3GA  mockB=$S3GB"
echo "  GATEWAY hits mockA -> CURSOR RIGHT: real incident path honours stale models.json; MUST rewrite models.json."
echo "  GATEWAY hits mockB -> openclaw.json wins even on the gateway path; models.json rewrite NOT required."
echo "  GATEWAY hits neither -> gateway call failed; inconclusive (check error above)."
echo "--- agent models.json openai.baseUrl AFTER the openclaw.json rewrite ---"
docker exec "$OC" sh -c 'cat /home/node/.openclaw/agents/main/agent/models.json 2>/dev/null || echo "{}"' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('providers',{}).get('openai',{}).get('baseUrl','<none>'))" 2>/dev/null || echo "(none)"

say "STEP 4 — also rewrite agent models.json openai.baseUrl -> mockB; restart; infer (the candidate fix)"
docker exec "$OC" sh -c 'F=/home/node/.openclaw/agents/main/agent/models.json; [ -f "$F" ] && python3 -c "import json;p=\"$F\";d=json.load(open(p));d.setdefault(\"providers\",{}).setdefault(\"openai\",{})[\"baseUrl\"]=\"'"$URLB"'\";json.dump(d,open(p,\"w\"))" && echo "patched agent models.json" || echo "no agent models.json to patch"'
docker restart "$OC" >/dev/null; wait_oc
clr "$MOCKA"; clr "$MOCKB"
oc infer model run --model openai/gpt-4o --prompt hi --local --json >/dev/null 2>&1 || true
S4A=$(hits "$MOCKA"); S4B=$(hits "$MOCKB")
echo "after rewriting BOTH: mockA=$S4A  mockB=$S4B  (mockB hit => rewriting both is the fix)"

say "VERDICT"
echo "STEP3 openclaw.json-only:  mockA=$S3A mockB=$S3B"
echo "STEP4 both rewritten:      mockA=$S4A mockB=$S4B"
