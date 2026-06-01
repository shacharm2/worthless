#!/usr/bin/env bash
# probe-auth-profiles-bypass.sh — empirical answer to the Phase 3 root question (WOR-514).
#
# QUESTION
#   When `worthless lock` rewrites models.providers.openai.baseUrl to point at the
#   Worthless proxy, does OpenClaw actually route through the new baseUrl — or does a
#   cached credential in auth-profiles.json pin the request to the ORIGINAL endpoint,
#   silently bypassing the redirect (Ido's incident)?
#
# METHOD (unambiguous: two mock upstreams, distinct DNS names)
#   mockA = the "original" provider endpoint (simulates api.openai.com)
#   mockB = the "worthless proxy" endpoint (where lock redirects baseUrl)
#   Each mock records every request it receives (GET /captured-headers).
#
#   PHASE A  provider.openai.baseUrl -> mockA; force inference; expect mockA hit.
#            Inspect auth-profiles.json to see what (if anything) got cached.
#   PHASE B  rewrite ONLY baseUrl -> mockB (leave apiKey + auth-profiles untouched);
#            clear both mocks; force inference; observe which mock gets hit.
#   PHASE C  same redirect, but drive the AGENT path (the real incident path)
#            via `agent --local --message`.
#
# VERDICT
#   mockB hit in B/C  -> baseUrl is load-bearing; Phase 3 = "rewrite baseUrl". Clean.
#   mockA hit in B/C  -> auth-profiles.json pins the endpoint; Phase 3 must also
#                        neutralize that cache. Harder + riskier (mutating OpenClaw's file).
#
# Run:  bash tests/openclaw/probe-auth-profiles-bypass.sh
# Needs Docker. Self-cleaning. No host ports published (reads hits via docker exec).
set -uo pipefail

IMG="${OPENCLAW_IMG:-ghcr.io/openclaw/openclaw:2026.5.3-1}"
NET=wor-probe-net
MOCKA=wor-probe-mockA
MOCKB=wor-probe-mockB
OC=wor-probe-oc
MOCK_IMG=wor-probe-mock
HERE="$(cd "$(dirname "$0")" && pwd)"

cleanup() {
  docker rm -f "$OC" "$MOCKA" "$MOCKB" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

say() { printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }

# Count requests a given mock has received (run python INSIDE the mock container — no curl).
hits() {  # $1 = container name
  docker exec "$1" python -c \
    "import urllib.request,json;print(len(json.load(urllib.request.urlopen('http://localhost:9999/captured-headers'))['headers']))" \
    2>/dev/null || echo "ERR"
}
clear_hits() {  # $1 = container name
  docker exec "$1" python -c \
    "import urllib.request;req=urllib.request.Request('http://localhost:9999/captured-headers',method='DELETE');urllib.request.urlopen(req)" \
    >/dev/null 2>&1 || true
}
oc() { docker exec "$OC" node openclaw.mjs "$@"; }

say "BUILD mock image"
docker build -t "$MOCK_IMG" "$HERE/mock-upstream" >/dev/null

say "NETWORK + MOCKS"
docker network create "$NET" >/dev/null
docker run -d --name "$MOCKA" --network "$NET" "$MOCK_IMG" >/dev/null
docker run -d --name "$MOCKB" --network "$NET" "$MOCK_IMG" >/dev/null

say "OPENCLAW container"
docker run -d --name "$OC" --network "$NET" \
  -e OPENCLAW_ACCEPT_TERMS=yes --user node "$IMG" >/dev/null
# wait for it to settle
for i in $(seq 1 30); do
  docker exec "$OC" node openclaw.mjs config get gateway >/dev/null 2>&1 && break
  sleep 2
done

say "PHASE A — seed provider.openai -> mockA, force one inference"
oc config set models.providers.openai \
  '{"baseUrl":"http://'"$MOCKA"':9999/openai/v1","api":"openai-completions","models":[]}' --strict-json
oc config set models.providers.openai.apiKey "sk-probe-FAKEKEY-aaaaaaaaaaaaaaaaaaaa"
oc config set agents.defaults.model.primary openai/gpt-4o >/dev/null 2>&1 || true
clear_hits "$MOCKA"; clear_hits "$MOCKB"

echo "--- infer model run (local) ---"
oc infer model run --model openai/gpt-4o --prompt "say hi" --local --json 2>&1 | tail -8 || true
echo "mockA hits after A: $(hits "$MOCKA")   mockB hits after A: $(hits "$MOCKB")"

say "auth-profiles.json after PHASE A (does a credential get cached?)"
docker exec "$OC" sh -c 'find /home/node/.openclaw -name "auth-profiles.json" -exec echo "== {} ==" \; -exec cat {} \; 2>/dev/null' \
  || echo "(no auth-profiles.json found)"
echo "--- infer model auth status ---"
oc infer model auth status 2>&1 | tail -15 || true

say "PHASE B — rewrite ONLY baseUrl -> mockB (apiKey + auth-profiles untouched)"
oc config set models.providers.openai.baseUrl "http://$MOCKB:9999/openai/v1"
clear_hits "$MOCKA"; clear_hits "$MOCKB"
echo "--- infer model run (local) after redirect ---"
oc infer model run --model openai/gpt-4o --prompt "say hi again" --local --json 2>&1 | tail -8 || true
B_A=$(hits "$MOCKA"); B_B=$(hits "$MOCKB")
echo "mockA hits after B: $B_A   mockB hits after B: $B_B"

say "PHASE C — same redirect, AGENT path (the real incident path)"
clear_hits "$MOCKA"; clear_hits "$MOCKB"
echo "--- agent --local --session-id --message ---"
oc agent --local --session-id wor-probe-sess --message "say hi from agent" --json 2>&1 | tail -12 || true
C_A=$(hits "$MOCKA"); C_B=$(hits "$MOCKB")
echo "mockA hits after C: $C_A   mockB hits after C: $C_B"

say "VERDICT"
echo "PHASE B (infer): mockA=$B_A mockB=$B_B"
echo "PHASE C (agent): mockA=$C_A mockB=$C_B"
echo
echo "Interpretation:"
echo "  mockB hit  -> baseUrl IS load-bearing; Phase 3 = rewrite baseUrl (clean)."
echo "  mockA hit  -> auth-profiles.json PINS the endpoint; Phase 3 must neutralize the cache (hard)."
echo "  neither    -> redirect broke the call entirely (also a signal: proxy becomes load-bearing by failing closed)."
