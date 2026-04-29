# Phase 2 — Batch 3 Chaos / Failure-Mode Review

**Scope:** `cross-axis.test.ts` (13), `concurrency.test.ts` (8), `range-and-size.test.ts` (19), `headers-and-integrity.test.ts` (22) — RED tests against the WOR-300 stub Worker.
**Lens:** chaos engineering — verifying that the Batch-2 chaos review's "highest practical risk" gaps were closed before Phase 3.
**Prior review:** `phase-2-batch-2-chaos-review.md` (15 cross-axis scenarios, 12 unenforceable items, idempotency gap, isolate-state gap).

The cross-axis file substantially closes the §4 gap. Concurrency adequately models X-01..X-03. Replay determinism is **partially robust** — the contract is asserted but a specific class of nondeterminism (lazy memoization keyed on first request) can still slip through. Resource-exhaustion gaps remain on **headers** (count and per-value size). Upstream / fail-closed remains correctly out-of-scope for Vitest. Net verdict: **YELLOW-trending-GREEN** — ship Phase 3 RED gate, file 3 follow-up tickets.

---

## 1. Cross-axis coverage assessment vs prior §4 (the 6 highest-risk pairs)

The prior review's §4 table listed 15 stacked-misuse scenarios; the "highest practical risk" subset was the 6 below (those with worst-case = "install script served on a non-canonical surface" or "walkthrough exfil cross-origin"). Coverage map:

| Prior §4 # | Scenario | Covered in `cross-axis.test.ts`? | Test name |
|---|---|---|---|
| #1 | `POST /?explain=1` curl → 405 no body | YES | "POST /?explain=1 → 405, body is neither walkthrough nor install script" |
| #2 | `HEAD /install.sh` curl → 404 empty | PARTIAL — `HEAD /.well-known/security.txt` covers HEAD-on-non-canonical, but `HEAD /install.sh` specifically is **NOT** asserted | gap |
| #3 | `OPTIONS /?explain=1` browser+Origin → no ACAO`*` no walkthrough | NO — covered for `OPTIONS /admin` instead. The query × OPTIONS interaction is not asserted | gap |
| #4 | `GET /%2e%2e/install.sh?explain=1` curl → not 200 | PARTIAL — `/install.sh?explain=1` is covered, but the **traversal-encoded** form is not stacked with `?explain=1` | gap |
| #7 | `GET /.well-known/security.txt?explain=1` curl → security.txt, not walkthrough | YES | "/.well-known/security.txt?explain=1 → security.txt content, NOT walkthrough" |
| #8 | `POST /.well-known/security.txt` browser → 405 OR same as GET | YES | "POST /.well-known/security.txt → 405 OR same as GET, never 500" |

**Score: 3 of 6 fully covered, 3 partial.** The three gaps are **adjacent** to existing tests (one-line additions) and should be added as a follow-up commit, not as a Phase 3 blocker. The strictest-rule-wins design rule documented in the file header is sound and will prevent regression once these three are added.

### New combinations I'd add (post-Phase-3, not blocker)

7. **`TRACE /` with sentinel header** — XST + reflection stacked. `methods.test.ts` covers TRACE alone; cross-axis with a sentinel header is missing. One line.
8. **`GET /` with `?explain=1` × 50 repetitions** — quadratic-parser DoS (prior §4 #9). Not in `cross-axis.test.ts`; should live in `query-canonicalization.test.ts` instead.
9. **`GET /%2e%2e/install.sh?explain=1` with sentinel header** — three-axis (path × query × header reflection). The closest existing test is the compound-UA triple-stack, which uses UA not header.
10. **`OPTIONS / + 8KB path`** — long path × OPTIONS preflight (prior §4 #14). Currently no test combines path-length × OPTIONS.

---

## 2. Concurrency modeling — does X-01 actually exercise concurrency or is it cosmetic?

**Verdict: largely cosmetic in the workerd test runtime, but still useful as a contract pin.**

`cloudflare:test`'s `SELF.fetch` runs against `workerd` in single-isolate test mode. The runtime **may** dispatch the 100 `Promise.all` requests through the same isolate sequentially (event-loop ordered), in which case true V8-level concurrency is not exercised. The test would PASS even if the implementation has a race window that production-edge fan-out would expose.

**However**, the test is not worthless — it pins the **observable contract** (each request gets the response its UA earns). What it cannot pin: the V8-isolate-reuse hazard the test is named for.

### Real check I'd add

Add an **affirmative module-state-poisoning probe** that would detect lazy-memoization regressions:

```text
// Pseudocode contract — add to concurrency.test.ts
it("classifier verdict from request N does not appear in headers of request N+1", async () => {
  // Send curl request, inspect response for any debug header containing "curl"
  // Send browser request immediately after, assert NO header on the browser
  //   response contains "curl" or any classification artifact from the prior request
});
```

This is the only check that would catch a `let lastClassification: string | null = null` module global. The current X-01/X-02 tests would pass even with that bug, because the **status-and-Location** contract is satisfied independently per request, while the **leaked-state-in-headers** vector goes uninspected.

**Suggested follow-up ticket:** WOR-3xx — "Add module-state-leak probe to concurrency.test.ts" (1 hour).

---

## 3. Replay determinism — robust or bypassable?

**Verdict: bypassable by lazy-memoize-on-first-request.**

The 50-sequential-reads test asserts `bodies[i] === bodies[0]` for all i ∈ [1,50]. This catches:

- A timestamp-in-body bug
- A counter-in-body bug
- A nonce-in-body bug
- A "regenerate script per request" bug

This does **NOT** catch:

- A Phase 3 implementation that does `const SCRIPT = SCRIPT ?? generateOnce()` (caches the first response in module state and returns it forever). Every subsequent read returns the **identical cached bytes** — passes the assertion — but the contract being pinned was "the bytes are stable because the source is stable", not "the bytes are stable because we cached the first reply".
- A Phase 3 implementation that nondeterministically picks one of two equivalent script copies on cold-start, then caches. Two cold isolates could serve different bytes; the test runs in one isolate so it never sees both.

### Strengthening I'd add

Two-line addition:

```text
it("body byte-equals the bundled install.sh asset (not just self-consistent)", async () => {
  // Pseudocode — Phase 3 will land `import INSTALL_SH from "../../install.sh?raw"`.
  // Assert response body equals INSTALL_SH literally. Self-consistency is necessary
  // but not sufficient; bundled-source-equality is the load-bearing contract.
});
```

The `headers-and-integrity.test.ts` H-08 file header acknowledges this gap explicitly ("Once Phase 3 lands, this file should add `import INSTALL_SH from "../../install.sh?raw"` and assert `expect(body).toBe(INSTALL_SH)`"). The acknowledgement is good; the **test itself is not yet there**. Phase 3 must add this on day one or the determinism story is theatre.

---

## 4. Resource-exhaustion gaps still open

Per the prior review §1.C, these were the named gaps:

| Vector | Tested in Batch 3? | Where |
|---|---|---|
| 64 KB query string | YES | covered in Batch 2 `query-canonicalization.test.ts` |
| 1000 headers | NO | not testable via `SELF.fetch` — undici applies its own header-count cap; flag for staging |
| 1 MB single header value | PARTIAL | `range-and-size.test.ts` tests 10 MB UA; other headers (Cookie, X-Forwarded-For, Accept) untested |
| 1 MB POST body | NO | `methods.test.ts` covers POST 405; body-size × POST not tested |
| Slowloris trickle | NO | unenforceable in Vitest (correctly flagged) |
| 16 KB URL boundary | NO | path tested at 8 KB only; 16384 vs 16385 boundary not pinned |

**Three actionable gaps** (1000 headers and 1 MB POST body need a workerd integration harness, not Vitest; 1 MB on Cookie/XFF is a one-line addition).

### Specifically testable in Vitest right now

1. **1 MB Cookie header value**: `SELF.fetch(url, { headers: { cookie: "k=" + "A".repeat(1024*1024) } })` — assert response < 50 KB and never 5xx.
2. **1 MB X-Forwarded-For**: same shape — these are common echo-target headers in misconfigured proxies.
3. **8192 vs 8193 byte path boundary** — currently `paths.test.ts` has 8 KB but no boundary-pair test (per the prior review's "16384 vs 16385" point, scaled).

### Not testable in Vitest (correctly out of scope, file as WOR-3xx)

- 1000 headers (undici cap)
- 1 MB POST body before-vs-after method check (timing precision)
- True isolate-isolate isolation (single-isolate test mode)
- CPU/memory budget enforcement (workerd test relaxes both)

---

## 5. Upstream / fail-closed contracts (prior review §3)

**Acknowledged: NOT testable in Vitest at the current stub layer.**

The Phase 2 RED suite is correct to defer this. Real coverage requires either:

- A `fetch` mock layer (Phase 3 introduces real upstream — Phase 2 stub returns inline) — adds testing-library complexity for marginal Phase 2 value.
- A workerd-level fault injection harness (e.g., `unstable_dev` with a mock service binding) — appropriate for **WOR-3xx integration suite**, not the vitest RED gate.

**Counter-proposal: split into two follow-up tickets**

- WOR-3xx-A — "Upstream fail-closed contract tests" — Vitest with `vi.spyOn(globalThis, 'fetch')`. Tests: upstream 503 → 302 wless.io OR 503 + shell-safe text. Upstream slow (timeout) → same. Upstream content-type drift (`text/html`) → reject, not relay. ~6 tests, 1 day.
- WOR-3xx-B — "Workerd integration chaos drills" — service binding fault injection, partial-body simulation, regional-outage simulation. ~12 scenarios, 3 days, runs nightly not on PR.

This split keeps the RED PR gate fast (< 30s) while still capturing fail-closed contracts before production traffic.

---

## 6. Final verdict — Phase 2 → Phase 3 transition

**Chaos-coverage score: YELLOW-trending-GREEN. Ship the Phase 2 RED gate. File 3 follow-up tickets before Phase 3 GA.**

### Green (ready)

- Cross-axis design rule ("strictest individual rule wins") is documented and enforced in 3 of 6 highest-risk scenarios.
- Replay determinism is **asserted as a contract** (50 sequential, identical bodies, identical Content-Length, identical Content-Type, plus HEAD-warm-then-GET).
- X-01/X-02/X-03 concurrency tests pin the **observable contract** even if they cannot exercise true V8 concurrency.
- Range × all-RFC-9110-syntaxes covered (5 syntaxes × 2 assertions = 10 tests).
- Accept-Encoding cross-encoding byte-equality covered (gzip == br == identity).
- Echo-defence size cap covered (10 MB UA, 50 KB ceiling).
- Header-banner disclosure covered (X-Powered-By, Via, Server-token blacklist, body-token blacklist).
- HSTS preload-eligible string pinned exactly.
- SHA-256 header value asserted to equal SHA-256(body) — H-06 robust.

### Yellow (file follow-up, do not block Phase 3 RED → GREEN)

- Three of six highest-risk cross-axis pairs are partial: `HEAD /install.sh`, `OPTIONS /?explain=1` browser+Origin, `GET /%2e%2e/install.sh?explain=1`. One-line additions each.
- Replay determinism cannot distinguish "stable source" from "lazy memoize first reply". Phase 3 MUST add `expect(body).toBe(INSTALL_SH)` against the bundled asset.
- Concurrency tests cannot detect debug-header leakage of prior-request classification. Add module-state-leak probe.

### Red (must close before Phase 3 production cutover, not before RED→GREEN)

- Upstream fail-closed contracts entirely unasserted — file WOR-3xx-A (Vitest with fetch mock) and WOR-3xx-B (workerd integration drills).
- True V8-isolate-reuse behaviour unverified — staging chaos-drill ticket.
- 1 MB header value class (Cookie, XFF) untested — one-line additions to `range-and-size.test.ts`, do before GA.

### Top 3 chaos gaps still open (post-Batch-3)

1. **Lazy-memoize bypass of replay determinism** — the determinism contract is satisfied by a buggy implementation that caches first reply. Closes only when bundled-asset byte-equality lands in Phase 3.
2. **Upstream fail-closed contracts entirely unasserted** — half-script piped to `sh` is the worst-case supply-chain failure and currently has zero RED-test coverage. WOR-3xx-A.
3. **Three of six highest-risk cross-axis pairs are partial** — HEAD/install.sh, OPTIONS/?explain=1, traversal-encoded with explain trigger.

### Recommended follow-up tickets

- WOR-3xx-1: Close 3 partial cross-axis pairs (HEAD, OPTIONS+query, traversal+query) — 1 hour.
- WOR-3xx-2: Add module-state-leak probe to concurrency.test.ts — 1 hour.
- WOR-3xx-3: Add bundled-asset byte-equality assertion in headers-and-integrity.test.ts (Phase 3 dependency) — 1 hour after Phase 3 lands.
- WOR-3xx-4: Upstream fail-closed contract tests via `vi.spyOn(globalThis, 'fetch')` — 1 day, blocker for Phase 3 GA.
- WOR-3xx-5: Workerd integration chaos drills (service binding fault injection, partial-body, regional outage) — 3 days, runs nightly.
- WOR-3xx-6: 1 MB Cookie/XFF/Accept header-value tests — 1 hour, append to `range-and-size.test.ts`.
- WOR-3xx-7: 8192 vs 8193 byte path boundary pair — 1 hour, append to `paths.test.ts`.
