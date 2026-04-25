# Phase 2 — Batch 2 Chaos / Failure-Mode Review

**Scope:** `paths.test.ts`, `methods.test.ts`, `query-canonicalization.test.ts` (RED tests against the WOR-300 stub Worker).
**Lens:** chaos engineering — degraded states, races, exhaustion, partial failures, cross-axis abuse, Cloudflare-specific failure modes.

The three batch-2 files are sound for what they cover (parser-differential bugs, reflection, method abuse, conditional-request bypass), but they implicitly assume a *steady-state* Worker. They have **zero coverage of dependency degradation, isolate state reuse, body-size exhaustion, cross-axis combinations, timing oracles, or fail-closed behaviour under CF-edge degradation.** Below: the gaps, the specific tests to add, what cannot be tested in Vitest, and the cross-axis attacks that fall through every existing assertion.

---

## 1. Coverage gaps in chaos/failure dimensions

### A. Degraded upstream state — NOT COVERED
The Worker per stub contract serves install.sh inline, but the threat model implies the script could be fetched from `GITHUB_RAW_URL` (env var present in `Env`). None of the three files exercise:
- `GITHUB_RAW_URL` returning 503 / slow / TLS error / DNS NXDOMAIN.
- Partial response from upstream (connection closed mid-body) — does the Worker stream a half-script to `sh`? That is the **worst-case failure**: a half-script piped to shell can leave a partially configured system that an attacker exploits.
- Upstream content-type drift (upstream returns `text/html` instead of `text/plain`).
- Upstream body length anomalies (0 bytes, 100MB).

The stub returns a static body, so this isn't testable today — but the test suite should pin the contract that any upstream failure produces a **fail-closed** response (302 → wless.io OR 503 + shell-safe text), never a partial body.

### B. Race conditions / isolate state reuse — NOT COVERED
Cloudflare Workers reuse V8 isolates across requests. Module-level state (e.g., a memoized UA classifier regex, a Last-Modified timestamp, a counter) can leak between concurrent requests. None of the batch-2 files send concurrent requests with conflicting expectations to detect cross-request contamination. The HEAD/GET parity test in `methods.test.ts` (line 47) uses `Promise.all` for parity, but does **not** check that interleaved curl-vs-browser requests don't poison each other's responses.

### C. Resource exhaustion — PARTIALLY COVERED
- 8KB **path** is tested (P-06). Good.
- 10MB **User-Agent** is tested in `error-path.test.ts`. Good.
- **NOT tested:** 64KB query strings, 1000-header request, 1MB single header value, POST body of 100MB (POST should be 405 but the Worker may consume the body before checking method → exhaustion before rejection), Slowloris-style trickle (unenforceable in Vitest, flag for staging).
- **NOT tested:** cumulative URL line at the CF documented 16KB cap edge — does the Worker handle exactly 16384 vs 16385 bytes consistently?

### D. Partial responses — NOT COVERED in batch 2
Range requests are deferred to `range-and-size.test.ts` (different file). Batch 2 has no chunked-transfer, no early-close, no `Connection: close` mid-stream tests. **Mostly unenforceable at Vitest layer** — `SELF.fetch` is undici-based and buffers fully — flag for integration.

### E. Method × path × query interaction — NOT COVERED
Each file is single-axis. Real attackers stack axes:
- `POST /install.sh?explain=1` with curl UA → does it return 405 (correct), 200 walkthrough (CSRF leak), or the install script (worst)?
- `HEAD /admin` with curl UA → 404 with empty body? or accidentally 200?
- `OPTIONS /?explain=1` with attacker `Origin` → does the OPTIONS handler honour `?explain=1` and reflect the param into a CORS preflight body?
- `TRACE /%2e%2e/install.sh` with sentinel header → XST + traversal stacked.

None of these combinations exist in the test suite.

### F. Time-based / timing-oracle attacks — NOT COVERED
The spec mentions wall-clock-now `Last-Modified`. Batch 2 has no:
- Timing-oracle assertions — does response time leak UA classification before the body is sent? (Practically untestable in Vitest with millisecond precision, but flag.)
- `Last-Modified` value sanity — is it monotonic per request (avoid clock-skew leak), is it stable across the same input (caching), is it never in the future?
- `Date` header drift between concurrent requests in the same isolate.

### G. Idempotency under retry — NOT COVERED
A curl client that gets a TCP reset retries. The retry must produce **byte-identical** output. Batch 2 has zero replay-equality tests:
- Two sequential GETs with identical headers must return identical bodies and identical `Content-Length`.
- The only acceptable per-request variation is `Date` and possibly `CF-Ray`. Any other diff = nondeterminism = supply-chain risk (a TOCTOU between bytes-counted and bytes-served).

### H. Cloudflare-specific failure modes / fail-closed — UNDER-COVERED
The error-path.test.ts file pins shell-safe error bodies, which is excellent. Batch 2 does not assert:
- The Worker fails **closed** (302 → wless.io OR 5xx + shell-safe text) when env vars are missing — e.g., `GITHUB_RAW_URL` is undefined.
- Behaviour when `crypto.subtle` or `caches.default` throws (CF degraded mode).
- Behaviour when the runtime injects a `cf-error-status` header (CF debug instrumentation).
- Behaviour on a `Request` with `cf` property absent (worker-test mode vs prod).

---

## 2. Specific tests to ADD per file

### `paths.test.ts` (add 7)
1. **8KB path under concurrent load** — fire 20 concurrent 8KB-path requests in parallel and assert all 20 return 414. (`expect(results.every(r => r.status === 414)).toBe(true)`).
2. **Path with 1000-segment depth** — `'/' + 'a/'.repeat(1000)` — assert `< 500`.
3. **Sequential idempotency on `/`** — two back-to-back curl GETs return identical body bytes. (`expect(body1).toEqual(body2)`).
4. **`/.well-known/security.txt` body never contains the request Host** — anti-reflection on the disclosure path.
5. **`/admin` + browser UA** — must redirect, must not leak that `/admin` exists differently from `/foo`. Compare 404 body bytes between `/admin` and `/foo` — they must match (no enumeration oracle).
6. **Path with valid UTF-8 BOM** — `/\uFEFF` → `< 500`.
7. **Path with mixed-case percent-encoding** — `/%2E%2E/install.sh` vs `/%2e%2e/install.sh` — same status, same body (no parser-differential).

### `methods.test.ts` (add 8)
1. **POST with 1MB body and curl UA** — assert 405 returned **before** the Worker reads the body (measure response time deviation < 50ms vs an empty POST). Mostly a smoke test — record-only if Vitest can't measure precisely.
2. **POST `/?explain=1`** — cross-axis: assert 405, body NOT walkthrough, body NOT script.
3. **HEAD `/?explain=1`** — assert 200, content-type matches GET-walkthrough, body length 0.
4. **HEAD `/admin`** — assert status matches GET `/admin`, body length 0.
5. **OPTIONS with no `Origin` header** — assert no CORS headers leak (no ACAO at all).
6. **OPTIONS with `Origin: null`** — common file:// origin — assert ACAO is not literal `"null"`.
7. **Conditional GET on a fresh isolate vs a warm isolate** — fire one GET, then send `If-Modified-Since` with the response's `Last-Modified` value verbatim — assert 200, not 304. (Pins the contract that the Worker's own `Last-Modified` cannot be used as a cache validator.)
8. **HEAD followed by GET on same isolate** — body of GET unaffected by prior HEAD. (`expect(getBody.length).toBeGreaterThan(0)`).

### `query-canonicalization.test.ts` (add 6)
1. **64KB query string** — `?explain=1&junk=` + 64KB of `A` — assert `< 500`, assert response is walkthrough or 414, never 5xx.
2. **1000 query params** — `?` + `Array(1000).fill('x=1').join('&')` — assert `< 500`.
3. **Sentinel reflection in 100 different param names** — `?a=SENT&b=SENT&c=SENT...` — none echo into body or any header.
4. **`?explain[]=1`** (PHP-style) — assert install script (not walkthrough) — pins parser policy.
5. **`?explain=1` with query repeated 50 times** — `?explain=1&explain=1&...` — assert walkthrough, assert response time < 1s (no quadratic parser blowup).
6. **Unicode normalization** — `?explain=\u00311` (precomposed digit-1) vs `?explain=1` — assert behaviour is identical OR documented; flag any divergence.

---

## 3. Tests unenforceable at the Vitest layer

These need integration / load / staging — flag for WOR-3xx follow-up tickets, not RED tests:

1. **Slowloris** — sending 1 byte/sec headers for 60 seconds. `SELF.fetch` is fully buffered.
2. **TCP reset mid-response** — Vitest's `SELF` does not expose socket-level abort.
3. **Connection-pool exhaustion** — needs real CF edge with HTTP/2 multiplexing.
4. **CF regional outage failover** — needs CF API to disable a region; staging only.
5. **Cache-coherence across CF colos** — needs multi-PoP probing; staging only.
6. **CPU-time budget exhaustion** (Worker 50ms cap) — workerd in test mode does not enforce CPU limits realistically.
7. **Memory budget** (128MB limit) — workerd test mode does not enforce.
8. **DNS / TLS termination failures** — out of Worker scope; CF infrastructure layer.
9. **Real-edge HTTP/3 vs HTTP/2 path differences** — not in workerd test.
10. **Real-time clock skew between isolates** — workerd test mode uses host clock.
11. **CF cache plan being downgraded mid-incident** — out-of-band, ops drill territory.
12. **Concurrent-isolate isolation guarantees** — workerd test mode runs single-isolate.

Recommendation: file a follow-up ticket "WOR-3xx — staging chaos drills for worthless.sh" referencing this list.

---

## 4. Cross-axis attack scenarios not covered

These are the **stacked-misuse** scenarios where each individual axis is tested but the combination falls through:

| # | Method | Path | Query | UA | Expected | Why it matters |
|---|---|---|---|---|---|---|
| 1 | `POST` | `/` | `?explain=1` | `curl` | 405, no body | CSRF + walkthrough leak |
| 2 | `HEAD` | `/install.sh` | — | `curl` | 404, empty body | HEAD on a non-canonical path is the cheapest existence-probe — pin no-leak |
| 3 | `OPTIONS` | `/` | `?explain=1` | browser + Origin | no ACAO`*`, no walkthrough | Cross-origin walkthrough exfil |
| 4 | `GET` | `/%2e%2e/install.sh` | `?explain=1` | `curl` | not 200, not walkthrough | Traversal + trigger stacked |
| 5 | `TRACE` | `/` | `?explain=SENT` | `curl + sentinel hdr` | not 200, no echo | XST + reflection stacked |
| 6 | `GET` | `//` | `?explain=1` | `curl` | walkthrough OR redirect, never 5xx | Slash-canonicalization × trigger |
| 7 | `GET` | `/.well-known/security.txt` | `?explain=1` | `curl` | security.txt, NOT walkthrough | Trigger param must not hijack other paths |
| 8 | `POST` | `/.well-known/security.txt` | — | browser | 405 OR 200 same as GET | Method × path × disclosure surface |
| 9 | `GET` | `/` | `?explain=1` × 50 reps | `curl` | walkthrough, < 1s | Quadratic parser DoS via duplicate trigger |
| 10 | `GET` | `/` | `?` + 64KB junk | `curl` | install.sh OR 414, never 5xx | Query-size × happy-path interaction |
| 11 | `HEAD` | `/` + 8KB | — | `curl` | 414, empty body | Long path × HEAD parity |
| 12 | `GET` | `/` | `?EXPLAIN=1&explain=1` | `curl` | walkthrough (case rule + first-wins) | Two query rules stacked |
| 13 | `GET` | `/` | `?explain=1` | malicious Host header | walkthrough, body never echoes Host | Host × trigger |
| 14 | `OPTIONS` | `/` + 8KB | — | browser | 414, no ACAO`*` | Long path × OPTIONS preflight |
| 15 | `GET` | `/` | `?explain=1#frag-via-%23` | `curl` | install.sh (Q-05) AND no fragment in any header | Fragment × trigger × reflection |

Recommendation: add a new file `test/cross-axis.test.ts` (10–15 tests) targeting these specifically. Each is one line but each catches a different fall-through.

---

## 5. Risk summary

The batch-2 tests do an excellent job of pinning **single-axis parser-differential** contracts. They are blind to:

- **Stacked-axis misuse** (highest risk: §4 above).
- **Dependency / upstream degradation fail-closed contracts** (§A, §H).
- **Resource exhaustion beyond UA and path** (§C — query, headers, body).
- **Idempotency / replay equality** (§G — supply-chain trust requires byte-equality).
- **Isolate-shared state poisoning** (§B — partially testable in Vitest with concurrent fire).

If only one item from this review is implemented before Phase 3 ships, it should be the **cross-axis test file** (§4) — because every individual axis having a clean test creates a **false sense of coverage** that the combinations also work.
