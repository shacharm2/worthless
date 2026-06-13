# 05 — Completeness Critique (gaps, contradictions, unverified claims)

> Critic pass over 01-prior-art, 02-openclaw-testability, 03-seam-coverage, 04-framework-design.
> Question: what is MISSING, CONTRADICTORY, or UNVERIFIED before we commit to a design?

## The single biggest flakiness/false-green risk

**The whole design rests on the EMBEDDED FALLBACK path, and that path is exactly what makes Lane B
silently NOT exercise the proxy.** 02 (`agent-via-gateway.ts:239-265`) celebrates that a turn
completes even if the Gateway is unreachable. But a journey test that relies on fallback can pass
while routing through a code path that *differs* from what a real user's Gateway-backed GUI uses —
and worse, if the proxy/baseUrl is misconfigured, the embedded agent may resolve a cached/default
key and still return content (a green test that proves nothing transited worthless). **Lane B must
assert from the MOCK SIDE that the request actually arrived** (02 §5 does this for the existing test;
04's Lane B must inherit it as a hard gate, not a nicety). A returncode==0 + non-empty content
assertion alone is the canonical false-green here.

## Modalities NOT researched / assumed

1. **Headless `agent --message` content shape is UNVERIFIED.** 02 §"Caveat" admits it could not grep
   the `--json` result emit in `agent-command.ts` and says "capture one real turn and pin the field
   path." 04 Lane A/B both assert on returned content (AP7) **as if this is known**. This is an
   assumption dressed as a finding. Nobody ran `docker exec ... agent --message ... --json` and pasted
   the actual JSON. Until that 5-minute capture exists, AP7's assertion target does not exist.
2. **`--local` embedded path vs Gateway path divergence is unexamined.** The GUI bug was found through
   the *Gateway-backed GUI*. 02 recommends driving `--local`/embedded (no daemon). Nobody verified the
   embedded request-shaping path applies the same `compat.*` flattening as the Gateway path. If they
   diverge, Lane B tests the wrong path and the GUI-only class stays GUI-only.
3. **`acpx` (01 §1c) is cited but never validated** — listed as "a candidate" from doc summaries; no
   one ran it. Don't let it into the design as load-bearing.
4. **Anthropic side of the journey is asserted symmetrically but only the OpenAI path has a real
   driver.** The mock emits `/v1/messages` (02 §3) but no end-to-end Anthropic agent turn is shown.

## Contradictions between dimensions

- **"80% built, just finish it" (02) vs "Phase 1 = days of work, Lane B needs a driver decision" (04).**
  02 says the journey test exists and needs 3 assertions added. 04 scopes a multi-phase, multi-day,
  digest-pinning, passthrough-recording build. Both can't be right about effort. Likely 02 overstates
  readiness (the content-shape caveat alone blocks "finish it").
- **01 leans on OpenClaw's own vitest e2e harness ("reuse, don't rebuild"); 02 explicitly says do NOT
  build inside OpenClaw's vitest.** These are reconcilable (01 means reuse the *pattern/script*, 02
  means don't couple to the *suite*) but the synthesizer must state which, because "reuse OpenClaw's
  e2e" reads as "run inside it."

## Claims stated without file:line / URL evidence

- **All of 01's OpenClaw doc claims are paraphrased from WebSearch summaries** (01 provenance: "WebFetch
  is blocked… paraphrased… should be spot-checked"). The three-tier suite, the two-knob live gate, the
  `mock-openai/gpt-5.5` model names — none verified against live docs or the clone. Treat as unconfirmed.
- **pee0 file:lines (`model-auth.ts:173`, `models-config.ts:184`, `auth-profiles/store.ts`) in 03 row
  10 are never re-verified against HEAD `2eae30e779`** the way 02 §2 re-verified the other seams. The
  highest-value security gap rests on un-spot-checked coordinates.

## pee0 cache-bypass — what a journey test MUST deliberately cover

This is underweighted across all four files relative to its severity. **`lock` does NOT neutralize a
pre-cached key** (03 row 10): if `agents/<id>/agent/auth-profiles.json` (or `models.json`) already
holds a plaintext real key from a prior first-use, the agent sends the REAL key straight upstream and
the proxy sees nothing. A naive journey test priming a CLEAN environment will go green and give
**false confidence that lock is load-bearing when it isn't.** The test must:
1. **Prime the cache first** (write a plaintext key into the auth-profiles/models.json cache), THEN
   run `lock`, THEN drive a turn — and assert the proxy received the request (or the call fails
   closed). 03 names this but 04's Lane A/B fixtures do not encode it.
2. **Negative assertion from the mock:** the mock upstream must verify the REAL key never arrives on a
   direct (non-proxy) path — i.e. confirm no second listener got the cached key.
Without rung this, the suite is worse than nothing: it ships a green check over a real bypass.

## Smaller gaps

- No one defined **what "fails closed" means** for pee0 — is it a worthless requirement or just hoped?
- **Mock fidelity drift** (03 tier L "is the mock still shaped like the real provider?") has no owner,
  schedule, or failing condition — it's mentioned, not designed.
- SSE chunk-boundary control (01 §3) is required of the mock but `mock-upstream/app.py` was not checked
  for whether it can split an event across flushes; if it can't, the SSE-framing class stays uncovered.
