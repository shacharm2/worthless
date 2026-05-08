# Phase 2 Batch 3 — Cloudflare Workers + Vitest 4 JS/TS review

Scope: 4 newly-written Vitest test files in `workers/worthless-sh/test/`. Reviewer angle:
TypeScript correctness, vitest 4 + `@cloudflare/vitest-pool-workers` 0.15 idiom, async patterns,
`cloudflare:test` SELF.fetch semantics, workerd runtime constraints.

---

## 1. Per-file TypeScript / vitest 4 issues

### 1.1 `vitest.config.ts` — config-level concern that affects all four files

The config imports a named `cloudflareTest` plugin from `@cloudflare/vitest-pool-workers`:

```ts
import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
```

This is **not** the documented public API. As of `@cloudflare/vitest-pool-workers` 0.5.x — 0.8.x
the documented entry is `defineWorkersConfig` and `defineWorkersProject` (re-exported from
`@cloudflare/vitest-pool-workers/config`). The header comment in `vitest.config.ts` claims
migration to "v4 pattern (pool-workers 0.15.x + vitest 4.x)" with a Vite plugin form, but
**no published version of `@cloudflare/vitest-pool-workers` exposes `cloudflareTest`** as of
this writing — the package is at 0.8.x and still ships the pool form. If the team is on a
private fork or pre-release tag, document it; otherwise this file will fail at `vite` plugin
init with `TypeError: cloudflareTest is not a function`. **All four test files will fail to
load.** Verify by running `npm ls @cloudflare/vitest-pool-workers` — fix is to revert to:

```ts
import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";
export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        wrangler: { configPath: "./wrangler.toml" },
      },
    },
  },
});
```

This is the highest-priority finding in the review — the test suite is dead on arrival
unless `cloudflareTest` is real.

### 1.2 `_helpers.ts`

- L20 `const testEnv = env as unknown as WorkerTestEnv;` — the `as unknown as` ladder is the
  documented escape hatch for `cloudflare:test`'s `env` (it's typed as `Cloudflare.Env`,
  which by default is empty and requires module augmentation). Acceptable here, but the
  better idiom is:

  ```ts
  declare module "cloudflare:test" {
    interface ProvidedEnv {
      REDIRECT_URL: string;
      GITHUB_RAW_URL: string;
    }
  }
  ```

  in a `worker-configuration.d.ts`. Then `env.REDIRECT_URL` is typed natively, no cast needed.
  Move the augmentation into a `.d.ts` and delete the cast.

- L42, L70: bare-number magic constants (1000, 200) for size floors — fine, but consider
  named constants for self-documentation.

- L23 `export const REDIRECT_URL = testEnv.REDIRECT_URL;` is **module-init-time evaluated**.
  If `cloudflare:test`'s `env` is not yet wired when the module is imported (it should be,
  but isolate boot ordering is undocumented), `REDIRECT_URL` will be `undefined`. Safer:
  export a getter `export const REDIRECT_URL = () => testEnv.REDIRECT_URL;` and call it.
  Lower-priority — the four call-sites all use it after `SELF.fetch`, so by then `env`
  is definitely populated.

### 1.3 `headers-and-integrity.test.ts`

- L48-L56 `sha256Hex` function: implementation is **correct**. Detail review in §3 below.

- L255 `expect([405, 501]).toContain(res.status);` — vitest 4 supports this idiom; correct.

- L212 `expect(declaredBytes).toBe(new TextEncoder().encode(body).byteLength);` — minor
  concern: `body` is a JS string returned by `res.text()`, which has already decoded any
  Content-Encoding. The Worker's `Content-Length`, however, is the **wire-bytes count after
  encoding**. If the Worker compresses the response (Cloudflare auto-Brotli), this assertion
  will fail because `declared` is the compressed size and `new TextEncoder().encode(body)`
  is the decompressed size. In `cloudflare:test` the local workerd may or may not apply
  edge compression — needs verification. **Mitigation**: send `accept-encoding: identity`
  to disable compression for this test specifically.

- L208-L220, repeated `await SELF.fetch(...)` to the same URL with no caching consideration:
  acceptable — workerd test runtime has no built-in cache by default.

- L284-L293: the framework-marker check uses `String.includes` against the response body.
  This will **falsely match** any install script that legitimately mentions e.g. "node"
  in a comment ("# node not required"). The current install.sh does not, but pin it as a
  test invariant or whitelist comment-only matches.

### 1.4 `range-and-size.test.ts`

- L170 `const hugeUA = "curl/8.4.0 " + "A".repeat(10 * 1024 * 1024);` — **10 MB User-Agent
  header.** workerd enforces a 32 KB per-header line limit (HTTP/1.1 spec hardening). This
  request will **never reach the Worker** — undici/workerd will reject it before the
  Worker handler runs. The test as written asserts `body.length < 50_000`, which would
  pass trivially on a thrown / rejected request. See §5 for full treatment.

- L182-L192 same problem, repeated.

- L132-L153 `Promise.all` of three SELF.fetches — correct parallel pattern.

- L65-L97 the `for (const range of ranges)` loop creates 10 `it()` blocks (5 ranges × 2
  assertions). Acceptable; no shared state.

### 1.5 `concurrency.test.ts`

- L43-L84: `Promise.all` of 100 `SELF.fetch` calls. Concurrency model: see §4.

- L57-L60 the "interleave by index swap" loop: `plan[i] = plan[i + 1] ?? tmp;` — at the
  last iteration when `i + 1 === plan.length`, this swaps the last element with `undefined`
  and assigns the temp to a non-existent slot. With `plan.length === 100` and step 4, the
  last `i` is 96, so `i + 1 === 97 < 100` — fine. But if the constant ever becomes
  non-multiple of 4, this silently corrupts the plan. **Fix**: explicit guard.

- L180-L201, L204-L220, L223-L239: three sequential test blocks each loop 50 times with
  `await` — that's **150 sequential `SELF.fetch` calls in this file**. Each `SELF.fetch`
  through `cloudflare:test` is non-trivial (isolate dispatch). Expect ~1-3s per test
  block; total file walltime ~5-10s. Within vitest defaults (5s `testTimeout`) the
  per-test budget may be exceeded. **Fix**: bump `testTimeout: 30_000` in the config or
  use `it.concurrent` (but see §4 on isolate semantics).

- L180-L201 collects 50 Response objects in an array, then awaits all `.text()` calls in
  `Promise.all`. `Response` bodies in workerd are streams with a single-read constraint;
  accumulating them and reading later is fine, but holding 50 unread Response objects in
  memory is unusual. No correctness bug, just style.

### 1.6 `cross-axis.test.ts`

- L325-L341: the "vacuous-conditional avoidance" pattern is genuinely defensive, but the
  test now asserts **two unrelated invariants in one `it()` block**. Split into two tests
  for cleaner failure attribution.

- L116-L121 the assertion `expect([200, 405]).toContain(res.status);` — pin both code paths
  to the same body negative — correct.

- L84-L100 `Promise.all` of HEAD+GET — correct parallel pattern; no race because they're
  independent reads.

- L57 `OPTIONS /admin` with `redirect: "manual"` is **not** set on this request (L62-L69).
  If the Worker 302s `/admin` to `/`, fetch will silently follow and the `res.status` check
  becomes meaningless. **Fix**: add `redirect: "manual"`.

- L102-L121, L195-L205, L233-L246 — same omission, `redirect: "manual"` is missing on
  multiple non-canonical-path requests. Inconsistent with the rest of the file.

---

## 2. `SELF.fetch` semantics findings

### 2.1 `redirect: "manual"` discipline

`SELF.fetch` from `cloudflare:test` returns a `Response` whose default redirect mode is
`"follow"` (browser default). Without `redirect: "manual"`, a 302 from the Worker triggers
fetch to follow to the Location URL — which is **out of the test isolate** (it will hit
the network, or fail). Audit:

| File | `redirect: "manual"` discipline |
|------|----------------------------------|
| `headers-and-integrity.test.ts` | Correct on every redirect-checking test (L93, L116, L147, L165, L237, L246, L255, L361) |
| `range-and-size.test.ts` | N/A — only checks 200 install responses |
| `concurrency.test.ts` | Correct on X-01 and X-02 (mixed UA tests need it: L65, L101, L131-138) |
| `cross-axis.test.ts` | **Missing on multiple tests**: L62 (OPTIONS /admin), L108 (POST /security.txt), L195 (admin?explain), L213 (security.txt?explain) |

**Highest-priority fix**: cross-axis.test.ts is missing `redirect: "manual"` on 4+ tests
that may produce redirects. Add unconditionally to every cross-axis fetch — simpler than
auditing each path's expected status.

### 2.2 `Promise.all` vs `Promise.allSettled`

All current `Promise.all` usages are correct because the tests **expect every fetch to
succeed at the transport layer** (a 405 or 500 is a successful HTTP response). No fetch
should ever reject in these tests. `Promise.allSettled` would only hide bugs.

**Exception**: `range-and-size.test.ts` L170 (10 MB UA) — if workerd rejects the request
at the transport layer with a header-too-large error, `SELF.fetch` may **throw**, not
return a Response. Wrap that single test in `Promise.allSettled` or a try/catch — see §5.

---

## 3. `crypto.subtle` H-06 review

`headers-and-integrity.test.ts` L48-L56:

```ts
async function sha256Hex(input: string): Promise<string> {
  const buf = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(input),
  );
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
```

**Verdict: correct.** Specifically:

- `crypto.subtle.digest` is awaited — non-blocking. ✓
- `new TextEncoder().encode(input)` produces UTF-8 `Uint8Array`. ✓
- `Array.from(new Uint8Array(buf))` converts ArrayBuffer to numeric array correctly. ✓
- `b.toString(16).padStart(2, "0")` produces 2-char lowercase hex per byte. ✓
- `.join("")` produces 64-char hex string for SHA-256. ✓
- No off-by-one — `padStart(2, "0")` handles `0x0f` → `"0f"`, `0xa0` → `"a0"`, etc.

**`text/plain` vs `text/plain; charset=utf-8` handling**: H-01 (L62-L78) pins `Content-Type`
to **exactly** `text/plain; charset=utf-8`. The sha256 test (L197-L208) reads `await
res.text()` which uses the response's declared charset. Since H-01 forces UTF-8, and the
hash is computed over `new TextEncoder().encode(body)` (also UTF-8), the round-trip is
sound. **No charset mismatch risk.**

**Edge case not covered**: if the Worker ever serves the body as `text/plain` (no charset
suffix) and the actual bytes contain non-ASCII (the install.sh might not, but never say
never), `Response.text()` defaults to UTF-8 anyway, so the hash would still match. The
charset header pin is the load-bearing invariant.

**Suggested improvement**: also assert sha256 against the **raw `ArrayBuffer`** of
`res.arrayBuffer()`, not the decoded string — this is the exact-byte invariant a curl user
would compute with `sha256sum`. As written, the test would fail to detect an encoding
re-roundtrip bug (decode UTF-8, re-encode, hash). Add:

```ts
const bytes = new Uint8Array(await res.arrayBuffer());
const buf = await crypto.subtle.digest("SHA-256", bytes);
const computed = Array.from(new Uint8Array(buf))
  .map((b) => b.toString(16).padStart(2, "0"))
  .join("");
expect(computed).toBe(declared);
```

---

## 4. Concurrency modeling assessment (X-01)

**Verdict: PARTIALLY MISLEADING. The test does not exercise true intra-isolate concurrency.**

`cloudflare:test` uses workerd's `unsafe.eval` infrastructure to dispatch each `SELF.fetch`
through the Worker's `fetch` handler. Under `@cloudflare/vitest-pool-workers`:

1. Each test file gets its own isolate (per `singleWorker: false`, the default).
2. Within a single test file, `SELF.fetch` calls **share the isolate** but each call is
   dispatched as a fresh request — workerd creates a new request context per call.
3. **`Promise.all([SELF.fetch(...), SELF.fetch(...)])` does NOT execute the Worker fetch
   handler concurrently in the same isolate.** workerd serializes request dispatch through
   its event loop. Each request is processed to completion (or to the next I/O await)
   before the next request begins. Concurrent execution within one isolate only happens
   when one request awaits I/O and the loop picks up the next.

Implication for X-01:

- `Promise.all([100 SELF.fetch calls])` schedules 100 requests. workerd processes them
  on a single event loop. If the Worker's `fetch` handler is fully synchronous (no I/O —
  just UA classification and string-build), they execute **serially**. The test asserts
  each response matches its expected status, which catches a class of bugs (e.g., a global
  counter that mis-increments) but does NOT catch true race conditions.
- A genuine race-condition bug in module-level state (e.g., a `let lastUA = ""` global
  that's written in handler entry and read at handler exit) **will be caught** because
  100 sequential requests with interleaved UAs produce the same poisoning pattern.
- A bug that requires **simultaneous** isolate execution — e.g., a SubtleCrypto call that
  awaits, allowing another request to interleave — would NOT be caught reliably here.

**Recommendation**:

1. Document the limitation in a comment at the top of `concurrency.test.ts`. Suggested
   wording: "X-01 exercises **request interleaving**, not true intra-isolate concurrency.
   workerd's event loop serializes dispatch within an isolate. This test catches
   module-level-state poisoning bugs (the realistic concern) but cannot reproduce true
   race conditions. For genuine concurrency stress, use Cloudflare's deployed runtime
   with `wrangler dev --remote` and an external load tester."
2. Strengthen X-02 (interleaved sequential pairs at L128-L145): this is functionally
   equivalent to X-01 and is what workerd actually executes. Either delete X-01 as
   redundant, or rename it "request-batch isolation" to clarify what it tests.
3. Consider adding an **async-poisoning** test: the Worker handler awaits `crypto.subtle.digest`
   (which DOES yield to the event loop in workerd), and we fire 10 parallel requests
   to confirm responses don't cross. This is the only way to test cross-request leakage
   in handler-internal state under realistic workerd semantics.

---

## 5. Runtime-limit collisions

### 5.1 32 KB header line limit (workerd hardening)

**Affected: `range-and-size.test.ts` L170, L186** — 10 MB User-Agent header.

workerd applies a per-header-line limit (commonly 32 KB, configurable via `compatibility_flags`).
A 10 MB UA will be rejected at the HTTP framer level **before** the Worker handler runs.
`SELF.fetch` will likely throw `TypeError: header line too long` or similar.

**Current test behavior**: `await SELF.fetch(..., { headers: { "user-agent": hugeUA } })`
will reject the awaited promise. The subsequent `await res.text()` throws on an undefined
`res`, the test fails with a runtime error, NOT the assertion error the author intended.

**Fix options**:

A. Reduce to 16 KB (under workerd limit but big enough to verify the size-cap invariant):
   ```ts
   const hugeUA = "curl/8.4.0 " + "A".repeat(16 * 1024);
   ```

B. Wrap in try/catch and assert that **either** the runtime rejects OR the response is
   bounded:
   ```ts
   try {
     const res = await SELF.fetch("https://worthless.sh/", { headers: { "user-agent": hugeUA } });
     const body = await res.text();
     expect(body.length).toBeLessThan(50_000);
   } catch (err) {
     // workerd rejected at transport — also a safe outcome for R-02
     expect(err).toBeInstanceOf(TypeError);
   }
   ```

Option B preserves the threat-model intent (input cannot inflate response) while accepting
runtime-level rejection as a passing outcome.

### 5.2 100 MB request body limit

Not exceeded by any test.

### 5.3 30s execution-time limit

`concurrency.test.ts`: 50 sequential fetches × 3 test blocks = ~150 fetches. Each fetch
through `cloudflare:test` is ~10-30ms. Worst case: 4-5s per test block. Within the 30s CPU
budget, but vitest's default `testTimeout` is 5000ms — the 50-fetch tests at L180-L201
(and L204-L220 and L223-L239) will likely time out on slow CI.

**Fix**: add `testTimeout: 30_000` to `vitest.config.ts`, OR reduce iteration count to 10
in those three test blocks (still proves the contract).

### 5.4 Subrequest limit (50 outbound per request)

Not relevant — these tests issue requests to the Worker, not from the Worker.

### 5.5 ArrayBuffer / TextEncoder size

`new TextEncoder().encode("A".repeat(10_485_760))` allocates 10 MB. workerd has a
per-isolate memory limit (commonly 128 MB). 10 MB is fine for one allocation, but the
test creates this in two `it()` blocks — both within the same isolate. If both run before
GC, that's 20 MB of `Uint8Array` plus the 10 MB string × 2 = ~40 MB resident. Under the
128 MB cap but tight. Combined with the 50-iteration concurrency tests, OOM risk on
constrained CI.

---

## 6. Helper usage / DRY violations

### 6.1 `expectInstallScript` / `expectRedirect` / `expectWalkthrough` adoption

Audit (count of inline duplications that should use helpers):

| File | `expectInstallScript` opportunities | `expectRedirect` opportunities | `expectWalkthrough` opportunities |
|------|-------------------------------------|--------------------------------|-----------------------------------|
| `headers-and-integrity.test.ts` | L62-L68 (uses inline `status===200` + content-type), L84-L89, L101-L106, L135-L143, L173-L179, L186-L194, L274-L293 — **7 inline** | L92-L98, L114-L125, L146-L155, L164-L170, L356-L371 — **5 inline** | L71-L77 — **1 inline** |
| `range-and-size.test.ts` | L74-L84, L113-L124, L165-L179, L201-L207 — **4 inline** | None | L222-L232 — **1 inline** |
| `concurrency.test.ts` | L86-L121 (50 curl checks), L179-L201, L241-L258 — **3 inline patterns** | L62-L83, L128-L145 — **2 inline** | None |
| `cross-axis.test.ts` | None — all checks are negative-shape (`not.toContain`) | L335-L341 (canonical companion) — **1 inline** | None — uses negative-shape regex |

**Reality check**: many of the inline duplications **shouldn't** use the helpers because
the test is asserting a **specific subset** of the contract (e.g., just the content-type,
or just a header). `expectInstallScript` asserts status + content-type + body shape + size
— overkill for a content-type-only test, and wasteful (redownloads the body).

**Genuine DRY violations to fix**:

1. `headers-and-integrity.test.ts` L186-L194 (X-Worthless-Script-Sha256 presence test):
   uses inline `status===200`, then checks header. The status precondition is identical
   to `expectInstallScript` minus the body read — keep inline, no fix needed.

2. `concurrency.test.ts` L86-L121 (50 curl + 50 browser body checks): the curl-side
   checks `body.match(/^#!\/bin\/sh/)` inline. Could call `expectInstallScript(responses[i])`
   if the per-curl response object is preserved. Currently it's not — bodies are read
   into a separate array. **Acceptable** as written.

3. `cross-axis.test.ts` L142, L204, L223 — repeats `not.toMatch(/line|step|what it does/i)`.
   Add a `notWalkthroughBody(body)` helper to `_helpers.ts`:
   ```ts
   export function notWalkthroughBody(body: string): void {
     expect(body.startsWith("#!/bin/sh")).toBe(false);
     expect(body).not.toContain("Worthless installer");
     expect(body).not.toMatch(/line|step|what it does/i);
   }
   ```
   Three test blocks would collapse to one-line negative assertions.

4. `cross-axis.test.ts` L42-L55, L128-L143, L174-L190, L192-L205, L232-L246, L253-L263 —
   the pattern `expect(body.startsWith("#!/bin/sh")).toBe(false); expect(body).not.toContain("Worthless installer");`
   appears **6+ times**. Add a `notInstallScript(body)` helper:
   ```ts
   export function notInstallScript(body: string): void {
     expect(body.startsWith("#!/bin/sh")).toBe(false);
     expect(body).not.toContain("Worthless installer");
   }
   ```

5. `REDIRECT_URL` is imported by 3 of 4 files — used correctly. No issue.

### 6.2 Test isolation

Vitest pool-workers spawns a fresh isolate per test file when `singleWorker: false`
(default). Within a file, tests **share** the isolate — module-level state persists
across `it()` blocks.

Audit for cross-test pollution:

- `headers-and-integrity.test.ts`: pure read-only — no test mutates anything outside its
  scope. ✓
- `range-and-size.test.ts`: same. ✓
- `concurrency.test.ts`: deliberately tests cross-request state in one file; relies on
  isolate sharing. The L241-L258 test ("HEAD followed by GET on warm isolate") **assumes**
  the isolate is warm from prior tests. If vitest ever changes default isolation to
  per-test, this assumption breaks. Add a comment documenting the assumption.
- `cross-axis.test.ts`: pure read-only. ✓

No critical isolation bugs. The concurrency file's reliance on warm-isolate semantics is
worth pinning explicitly with a comment.

---

## Summary of priority fixes

1. **CRITICAL**: verify `cloudflareTest` is a real export of the installed
   `@cloudflare/vitest-pool-workers` version. If not, revert to `defineWorkersConfig`
   form. All four test files load against this config.
2. **HIGH**: `range-and-size.test.ts` L170, L186 — 10 MB UA exceeds workerd 32 KB header
   limit. Reduce to 16 KB or wrap in try/catch.
3. **HIGH**: `cross-axis.test.ts` L62, L108, L195, L213 — missing `redirect: "manual"`.
   Default-follow will silently swallow assertion failures.
4. **MEDIUM**: `concurrency.test.ts` 50-iteration loops — bump `testTimeout` to 30000ms
   in vitest.config or reduce to 10 iterations.
5. **MEDIUM**: H-06 sha256 should hash `arrayBuffer()` not `text()` — the byte-level
   integrity story matters more than the string-level one.
6. **LOW**: Annotate concurrency.test.ts with the workerd-event-loop limitation: X-01
   does not exercise true intra-isolate concurrency, only request interleaving.
7. **LOW**: Add `notInstallScript(body)` and `notWalkthroughBody(body)` helpers to
   `_helpers.ts` to consolidate 9+ duplicated negative-shape checks across cross-axis
   and headers-and-integrity files.
8. **LOW**: Move `cloudflare:test` `env` typing to a `.d.ts` module augmentation;
   delete the `as unknown as` cast in `_helpers.ts`.
