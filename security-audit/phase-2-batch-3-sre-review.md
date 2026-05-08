# Phase 2 Batch 3 — SRE review of headers/range/concurrency/cross-axis tests

Reviewer angle: tier-0 supply-chain endpoint, SLO target = "every request returns
the documented response in <100ms p99 globally". Files reviewed:

- `workers/worthless-sh/test/headers-and-integrity.test.ts`
- `workers/worthless-sh/test/range-and-size.test.ts`
- `workers/worthless-sh/test/concurrency.test.ts`
- `workers/worthless-sh/test/cross-axis.test.ts`

Pre-implementation, the stub returns 500 — every assertion below is currently
red. The question is whether, once green, these tests pin the right behaviors
to keep the service inside SLO.

---

## 1. SLI / SLO observability gaps

What IS pinned (good):
- `X-Worthless-Script-Sha256` body-integrity SLI is asserted three ways:
  presence (`/^[0-9a-f]{64}$/`), value-equals-sha256(body), and absence on the
  walkthrough. This gives us a first-class **integrity SLI** the edge can sample.
- `Content-Length` parity with body byte length is asserted. That's a usable
  **truncation SLI** — a synthetic prober can read the header without buying
  the body.
- `expectInstallScript` includes a 1KB body floor, giving a cheap **availability
  proxy** when scripted from a black-box prober.

What is MISSING and matters for SLO/incident response:

a. **No trace-context / correlation header asserted.** No test pins
   `traceparent` (W3C Trace Context), no `X-Request-ID`, no echo-back of
   `CF-Ray`. When a user reports `curl worthless.sh | sh` failed at 14:32 UTC,
   the on-call has no header to grep Cloudflare logs by. `CF-Ray` is added by
   the edge for free, but no test asserts the Worker preserves/echoes it on
   every response shape (200 install, 200 walkthrough, 302 redirect, 405, 404,
   500). MTTR target of <30 min is unachievable without a request-id pin.

b. **No latency SLI is even nameable.** Workers can emit `Server-Timing`
   cheaply (`Server-Timing: app;dur=2.1`) — that single header would let us
   compute Worker-internal p99 vs. edge-total p99 separately. Without it, when
   p99 > 100ms we cannot tell "Worker code is slow" from "Cloudflare is slow"
   without filing a CF support ticket.

c. **No `Vary: User-Agent` test on the install response despite C-02 in the
   spec.** Batch 1 review flagged C-02 as vacuous; nothing in the four files
   under review fixes it. This is a **cache-poisoning SLI gap** — the day a
   CDN or corporate proxy sits in front of worthless.sh and caches the script
   keyed on URL alone, browsers start receiving the script and curl starts
   receiving the redirect. No alarm will fire because the Worker itself is
   healthy.

d. **No `Cache-Control` assertion on the install response.** Tests don't pin
   `no-store` / `private`. If Phase 3 ships `Cache-Control: public, max-age=3600`
   we get a 1-hour lag between deploying a security patch to install.sh and
   users actually receiving it. That's an **error-budget multiplier of 3600x**
   for any post-deploy rollback.

e. **No `Date` header sanity test.** Replay determinism (`X-03`) explicitly
   excludes `Date` and `CF-Ray` from the byte-equality contract — fine — but
   nothing pins that `Date` *is present* and is a valid HTTP-date. A missing
   `Date` breaks downstream HTTP caches and some curl progress meters.

---

## 2. Failure-mode hierarchy assessment

The Range decision is correct and well-justified. `range-and-size.test.ts`
pins **200 + full body** (silent degrade) over **416 Range Not Satisfiable**.
The trade-off is documented at lines 56-64: 416 with `set -e` aborts the
install; 200 with full body lets `sh` execute the canonical script. From an
SLO standpoint:

- 416 → counts against availability SLI (4xx-as-error) and burns budget.
- 200-full-body → succeeds, no budget burn, no user-visible failure.

This is the right call for a tier-0 install endpoint. RFC-strictness loses to
user outcome — pin it explicitly in the runbook so a future engineer doesn't
"fix" it.

The size-cap hierarchy is also correct: 100KB ceiling on install (8x headroom
over ~12KB script), 50KB ceiling on walkthrough, 50KB ceiling on echo-attack
response. These are **automatable as alerts**: a Worker analytics rule on
"response_bytes > 50000 && path != / || > 100000" should page.

Cross-axis "strictest rule wins" is the correct safety hierarchy: 405 beats
302 beats 200-script. The chaos §4 #1 test (POST `?explain=1` → 405 not
walkthrough) is exactly the kind of thing that prevents a CSRF-via-method-
confusion incident from ever filing.

What's WEAK in failure-mode coverage:

- **No explicit assertion that 5xx responses preserve security headers.**
  `nosniff`, HSTS, CSP all pinned for 200 and 302 — none for the 500 path. An
  error-page that drops nosniff is a sniff-XSS opportunity precisely when the
  Worker is already misbehaving (highest-blast-radius moment).
- **No assertion on `Retry-After` for any 4xx/5xx.** Without it, `curl
  --retry 3` will hammer at default backoff during a brownout, amplifying
  load on the failing isolate.
- **No assertion that 405 includes `Allow: GET, HEAD`.** RFC 9110 §15.5.6
  requires it. Lack of `Allow` makes some HTTP clients retry the same method
  forever.

---

## 3. Cold-start / warm-isolate concerns

Tests almost cover this but miss the headline case:

- `concurrency.test.ts` X-02 and X-03 verify **warm-isolate** behavior across
  20 interleaved pairs and 50 sequential requests. Strong on isolate-state
  poisoning.
- `concurrency.test.ts` "HEAD followed by GET" (line 241) is the closest thing
  to a warmup test, but it warms with HEAD and reads with GET — different code
  paths.
- **What is NOT tested**: a single request hitting a freshly-spawned isolate
  in isolation, with no prior request to prime any module-level cache. In
  workerd's test runner this is hard to simulate, but it can be approximated:
  spawn a brand-new SELF binding per test (vitest `beforeEach` with a fresh
  `getMiniflareInstance`) and assert the very first response equals the
  steady-state response from the warm-isolate test.

If a Phase 3 implementation does `let installScript: string | null = null;
async function getScript() { if (!installScript) installScript = await
fetch(GITHUB_RAW_URL); return installScript; }` then **cold isolates fan
out network calls to GitHub**. That latency (50-300ms) would silently push
p99 outside SLO during regional cold-spins. No current test would catch it.

---

## 4. Recommended new tests (testable in vitest, sandbox-ready)

Add to existing files:

1. **`headers-and-integrity.test.ts`** — pin `Cache-Control` on install
   response is `no-store` OR `private, max-age=0` OR includes `Vary: User-Agent`
   (unconditional, fixes Batch 1's vacuous C-02).
2. **`headers-and-integrity.test.ts`** — pin `Vary: User-Agent` on the 302
   redirect (fixes vacuous C-03).
3. **`headers-and-integrity.test.ts`** — pin `Date` header is present and
   parses as a valid HTTP-date on every response shape.
4. **New `error-headers.test.ts`** — for every error path (POST → 405,
   `/admin` → 404, intentionally-triggered 500), assert: `nosniff` present,
   HSTS present, body does NOT contain framework markers, `Allow` header
   present on 405, body length < 4KB.
5. **New `cold-isolate.test.ts`** — using `vitest` workspaces with isolated
   miniflare instances per test, fire ONE request to a fresh isolate and
   assert byte-equality with a warm-isolate request. Repeat for install,
   redirect, walkthrough.
6. **`concurrency.test.ts`** — add 200 concurrent requests (2x the current
   100) to surface workerd's concurrent-request limit and any per-isolate
   queueing artefacts.
7. **`concurrency.test.ts`** — pin replay-after-error: send a request that
   the Worker rejects (e.g., POST), then a valid GET — body identical to
   solo GET. Tests that error paths don't pollute isolate state.
8. **`concurrency.test.ts`** — `Server-Timing` header presence on every 200
   (drives latency SLI). Even just `Server-Timing: app` (no value) is enough
   to pin the contract.
9. **`headers-and-integrity.test.ts`** — `traceparent` echo or `X-Request-ID`
   echo on every response (pick one, pin it). Required for incident response.
10. **`range-and-size.test.ts`** — `Retry-After` header on simulated 503
    (Phase 3 will need to add a brownout endpoint or feature flag for this;
    can stub via test-only env var).

Total: **10 new tests**, all writeable in vitest with the existing
`SELF.fetch` helper plus one new `getMiniflareInstance` import.

---

## 5. Operational gaps (out-of-scope here, ticket separately)

None of these are testable in vitest, but all are required before tier-0
production:

- **`/health` endpoint**: SRE convention is `GET /health` → `200 text/plain
  "ok"`, no auth, no logging. Used by external probers (Pingdom, BetterStack,
  Cloudflare Health Checks) to drive the availability SLI. Currently undecided
  — flag for explicit decision: ship `/health`, or document "synthetic prober
  uses `GET /` with `User-Agent: worthless-prober/1`" instead.
- **Sampled access log**: no spec section discusses logging. For forensics
  after a malicious-download report, we need at minimum: timestamp, CF-Ray,
  path, UA family classification (curl/browser/other), response status,
  response sha256 (to prove which bytes the user got). Cloudflare Logpush
  to R2 with 1% sampling is the cheapest path. **Ticket: WOR-XXX "Worker
  access log via Logpush + 1% sample to R2"**.
- **Capacity / rate-limit testing**: free-tier Workers cap at 100k req/day.
  HN front-page = ~50k requests/hour. No load test exists. **Ticket:
  WOR-XXX "k6 burst load test against staging worthless.sh, target 1k RPS
  for 5 min"**. Not vitest-shaped — needs `k6` or `vegeta` against a
  staging deployment.
- **Cloudflare WAF rules**: no test covers WAF interaction. WAF rules
  applied at the zone level can return 403 before the Worker sees the
  request. Synthetic prober + alert on "% requests reaching Worker" would
  catch a misconfigured WAF rule that silently drops traffic. **Ticket:
  WOR-XXX "Synthetic prober + Worker-reach SLI"**.
- **Error-budget policy**: no document defines what triggers a feature
  freeze. Recommend: 99.9% availability over 30 days = 43 min budget. If
  burned in <1 week, freeze install.sh changes. **Ticket: WOR-XXX
  "Error-budget policy doc + burn-rate alert at 2% / hr"**.
- **On-call runbook**: with 219 tests passing but a real curl hanging at
  30s, the runbook would have nothing to point at. Need a runbook entry
  for "install hangs but Worker tests pass" that walks through: check
  CF status, check `cf-ray` in user's curl `-v`, check Logpush samples,
  check synthetic prober history. **Ticket: WOR-XXX "Runbook: worthless.sh
  install hang triage"**.

---

## 6. Verdict

**Conditionally ready for Phase 3 implementation, NOT ready for tier-0
production traffic.**

The four test files are excellent for their declared scope (security and
cross-axis safety). The Range/encoding/integrity contracts are correctly
pinned and the failure-mode hierarchy is sound. Phase 3 implementation can
proceed against this red bar with confidence.

But the SLO observability story is not yet pinned in tests. Before
worthless.sh handles real curl-pipe-sh traffic at scale, items 1-4 from
section 4 (cache headers, error-page security headers, request-id echo,
Server-Timing) need to be tests-first additions to the same files, and the
operational tickets in section 5 need to be filed and at least one (access
log) shipped. Without those, a Phase 3 deploy that passes all 219 tests can
still produce: a CDN-cached cross-UA poisoning incident, a 5xx storm with
no correlation IDs, or a silent SLO breach with no latency telemetry.

Recommend: land Phase 3 implementation against current red bar to stay on
schedule, but block tier-0 cutover (DNS swap to production worthless.sh)
until section 4 items 1-4 and section 5 access-log ticket are merged.
