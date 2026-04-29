# Phase 2 Batch 1 — Penetration-Tester Review

> Independent adversarial review of the 3 newly-written RED test files in
> `workers/worthless-sh/test/`: `error-path.test.ts`, `cache-keys.test.ts`,
> `ua-edge-cases.test.ts`. Reviewer had Read/Grep only; this file is the
> authored artifact.

## 1. Verdict

| File | Verdict |
|---|---|
| `error-path.test.ts` | **APPROVE WITH FIXES** — shell-safe prefix regex too narrow; 10MB-UA forcing may not reach handler. |
| `cache-keys.test.ts` | **APPROVE WITH FIXES** — C-02 / C-03 second tests are vacuous; one cosmetic redundancy in C-04. |
| `ua-edge-cases.test.ts` | **APPROVE WITH FIXES** — U-05 (b) vacuous; one composite-policy ambiguity to document. |

No file requires full rework.

## 2. Per-file findings

### `error-path.test.ts`

**Strong:**
- E-02: positive content-type AND explicit absence of `<html`, `<!DOCTYPE`, `error code: N`. Cannot pass tautologically.
- E-03: adversarial regex set covers V8 stack frames `at X (`, `.ts:N:N`, `node_modules`, `workerd`, named error classes.
- `assertShellSafeBody` enforces three independent checks (text/plain + positive prefix + negative HTML markers).

**Issues:**
- E-01 prefix regex `/^(echo |#|set -e|exit )/` is too narrow. Idiomatic guards like `command -v sh >/dev/null || exit 1` would FAIL the assertion. Broaden to `/^(echo |# |#!|set [\-+]|exit |true$|false$|: )/m`, or split into a helper that allows any POSIX-utility-name first token.
- 10MB UA may be rejected at the workerd layer (32KB header default) before reaching the Worker handler. Test exercises workerd, not the Worker. Recommend `fetchMock`-based upstream rejection instead — see §4.
- Missing: `Content-Length` matches `body.length` on the error path (CDN-mediated truncation could re-introduce HTML). Low priority.

### `cache-keys.test.ts`

**Strong:**
- C-01: install-script + walkthrough both assert `Vary: User-Agent` via lowercased + comma-split helper.
- C-04: byte-equality of decoded bodies across `gzip`/`identity` Accept-Encoding.
- C-05 final test: byte-identical bodies across two reads — would catch a templated timestamp.

**Issues:**
- **C-02 second test is vacuous**. Body is `if (sMaxAgeMatch && Number(sMaxAgeMatch[1]) > 60) { expect(...) }`. If Phase 3 ships without `s-maxage`, the test passes silently. Replace conditional with unconditional: assert either `Cache-Control: no-store` OR `Vary: User-Agent` is present on every 200.
- **C-03 second test is vacuous**. Same `if (cc.includes("public"))` shape. Convert to unconditional: `Cache-Control` MUST be present on the 302.
- **C-05 ETag comparison weak**. `null === null` passes against the stub. Strengthen by serializing the two reads (await one, then the other) and adding a third request from a different code path.
- C-04 cosmetic: `expect(gzipBody.length === identityBody.length)` is redundant after `expect(gzipBody === identityBody)`. Harmless.
- Missing: `Accept-Encoding: br` (Brotli) and `Accept-Encoding: *` wildcard. Workers auto-negotiate Brotli on Cloudflare.
- Cache-key composition itself is not testable from `cloudflare:test` (CF cache mocked out). Flag for integration test.

### `ua-edge-cases.test.ts`

**Strong:**
- `SAFE_REDIRECT_UAS` covers 15 distinct adversarial inputs across U-01..U-04, U-06, U-08. Both 302 status AND Location asserted.
- U-05 length attack splits into 64KB padding test + identical-classification test.
- U-07 newline injection iterates ALL response headers checking for `\r`, `\n`, `X-Inject`, `pwned`.
- "control" suite (canonical curl/wget/Go-http) is the over-blocking guard.

**Issues:**
- **U-05 (b) is vacuous**. Asserts long and short junk classify identically; against the stub both return 500, status matches, location null matches. Add explicit `expect(longRes.status).toBe(302)` to lock in the safe outcome.
- U-08 composite-policy ambiguity: `Wget/1.21.4 (compatible; bingbot/2.0)` is in `SAFE_REDIRECT_UAS` (302), but `Wget/1.21.4` standalone is in control (200). Contract becomes "any space-separated suffix containing a known-bot/browser token rejects." Phase 3 may implement "exact-match allowlist" instead, in which case both pass for different reasons. Add: `Wget/1.21.4 (with-trivial-suffix)` with documented expected behavior.
- Control suite duplicates `ua-curl.test.ts`. Acceptable as a regression guard; flag the overlap for future cleanup.
- Missing: U-07 should also assert `Content-Length` matches body byte length (HTTP response splitting check).

## 3. Tautology audit (the 11 "passing" tests)

| # | Test | Verdict |
|---|---|---|
| 1 | error-path: `error body leaks no internals` (×3) | **REAL CONTRACT** |
| 2 | cache-keys: `C-02 does not set long s-maxage without Vary` | **DEAD TEST — fix** |
| 3 | cache-keys: `C-03 redirect does not set Cache-Control: public without Vary` | **DEAD TEST — fix** |
| 4 | cache-keys: `C-05 ETag comparison` | **REAL CONTRACT (weak)** — strengthen |
| 5 | cache-keys: `C-05 Last-Modified not wall-clock-now` | REAL CONTRACT (negative-shape) |
| 6 | cache-keys: `C-05 byte-identical bodies across two reads` | **REAL CONTRACT** |
| 7 | ua-edge-cases: `U-05 long vs short junk classify identically` | **DEAD TEST — fix** |
| 8 | ua-edge-cases: `U-07 no X-Inject header` | REAL CONTRACT (weak) |
| 9 | ua-edge-cases: `U-07 no CRLF in any response header value` | **REAL CONTRACT** |
| 10 | error-path: `E-02 content-type is never text/html` | **REAL CONTRACT** |
| 11 | error-path: `E-02 forced-error response never contains <html>` | **REAL CONTRACT** |

**8 of 11 are real negative-shape contracts; 3 are dead tests needing fixes.**

## 4. E-01 forced-exception assessment

Writer's `__force_error=1` debug query param is **acceptable but I counter-propose `fetchMock`**:

**Problem with debug query param:** ships in production code (even if guarded by `env.DEBUG`); forgetting to gate it = trivial DoS. Adds test-only code paths that aren't natural to Worker logic.

**Counter-proposal (preferred):** Vitest mocking via `cloudflare:test`'s `fetchMock`. Force the upstream `env.GITHUB_RAW_URL` fetch to reject with `Network unreachable`. Worker's natural error wrapper handles it; no production code surface required. Phase 3 sees a clean try/catch around upstream fetch; tests force the throw via mock, not via input.

**If upstream-mock isn't viable** (e.g., install.sh embedded as static asset per ADR-001 Option A): use a `[env.test]` binding `FORCE_ERROR_PROBABILITY=0` in prod, override in test. Worker reads it at request time and throws when set. Production binding always 0.

**Recommendation to Phase 3:** wrap upstream/asset fetch in try/catch; tests force throw via `fetchMock` rather than input. Drop the 10MB UA / Range / Accept-Encoding proxies in `error-path.test.ts` — they may not reach the handler at all.

## 5. Adversarial gaps (top 5 not covered)

1. **Duplicate `User-Agent` headers** — HTTP allows multiple; Workers concatenates. Send `["User-Agent: Mozilla/5.0...", "User-Agent: curl/8.4.0"]` → must redirect (treat the browser victim's UA as authoritative), not serve script. **High exploitability.**
2. **`Accept-Encoding` ordering smuggling** — `gzip, identity` vs `identity, gzip`. Some CDNs key on literal value; permutation lets attackers force cache misses or response mismatch. Add: response identical regardless of ordering.
3. **Null-byte UA polyglot** — `curl/8.4.0\x00Mozilla/5.0`. Workers may strip or accept null bytes depending on workerd version. Classifier sees truncated string; downstream sees full. Test consistent classification.
4. **Host header confusion** — `Host: worthless.sh.evil.com`. Worker doesn't currently template Host into response, but if it ever does (banner, log echo), becomes XSS-on-shell. Add: response body never contains request Host value.
5. **Conditional-request bypass on redirect** — `If-Modified-Since: 1970-01-01` on the 302. Intermediate caches could serve stale 304 to a curl whose UA would have produced 200. Add: 304 never returned for install-script path.

## 6. Out-of-test-layer (defer to integration / future PRs)

- HTTP/2 PUSH_PROMISE poisoning — out of Worker control.
- IPv6 zone-id smuggling in Host header — requires integration-layer testing.

Both belong in a separate threat-model PR; correctly excluded from these test files.
