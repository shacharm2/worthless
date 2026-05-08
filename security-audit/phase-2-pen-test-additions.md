# Phase 2 — Pen-Test Additions to the worthless.sh Threat Model

> Consolidated input for WOR-349 Phase 2 (adversarial RED tests). Sources:
>
> 1. Independent penetration-tester audit (2026-04-25)
> 2. Web search of 2026-disclosed attacks against Cloudflare, install scripts, supply-chain
> 3. Existing `threat-model-worthless-sh.md` (11 sections, F-01..F-96)
>
> Each finding has an **explicit test assertion** so Phase 2 can build directly from this file. Sections map 1:1 to a `test/*.test.ts` file.

---

## Executive judgement

The existing 4 RED tests cover ~20% of the surface a Phase-3 implementation must survive. They exercise the happy-path UA branching only. The 5 most critical missing classes are below; this document maps each to a test file in `workers/worthless-sh/test/`.

**Top 5 ship-blocking attacks** (exploitability × impact):

| # | Attack | Test file |
|---|---|---|
| 1 | Cache-key confusion (UA × method × Accept-Encoding) — CDN serves attacker's payload to victim curl | `cache-keys.test.ts` |
| 2 | Path fall-through (`/install.sh`, `/.well-known/*`, `/admin`, `//`, `%00`, percent-encoded) | `paths.test.ts` |
| 3 | UA classifier ambiguity (`Mozilla/5.0 curl/8`, BOM, whitespace, case) | `ua-edge-cases.test.ts` |
| 4 | CF default HTML error page leaked into shell pipe — any uncaught throw → `curl \| sh of HTML` | `error-path.test.ts` |
| 5 | Range-request truncation (`curl -r 0-100`) — `set -e` does not save half-parsed scripts | `range-and-size.test.ts` |

---

## What the existing threat model already covers (no new tests needed)

These are out-of-band controls — not testable at the Vitest layer. Confirmed adequate in `threat-model-worthless-sh.md`:

- Supply-chain (signed tags, scoped CF token, GitHub environment reviewers) → §1, §5, §6
- DNS/TLS/CAA/registrar lock → §4
- Sigstore/cosign (deferred to v1.1, WOR-303) → §6
- Post-install rootkit/persistence → §8 (out of Worker scope; install.sh's job)
- Social engineering, typosquatting, regulatory → §9, §10

---

## Net-new findings — by test file

### `test/methods.test.ts` — HTTP method abuse

**M-01 — HEAD with curl UA.** A HEAD request with `User-Agent: curl/8.4.0` should return the same status + content-type as GET (so HTTP clients can size the response), but **must not** include the body in any code path. Some Worker frameworks accidentally serve GET handlers for HEAD requests with bodies.

Test: `expect(res.status).toBe(200); expect(res.headers.get("content-type")).toMatch(/^text\/plain/); expect((await res.text()).length).toBe(0);`

**M-02 — POST/PUT/DELETE/PATCH with curl UA.** Should return 405 Method Not Allowed. **Must not** serve install.sh on POST — an attacker who can trigger a POST from a victim browser (CSRF) shouldn't get the script.

Test: `for (const method of ["POST","PUT","DELETE","PATCH"]) { expect(res.status).toBe(405); }`

**M-03 — OPTIONS preflight.** A browser preflight should return 204 with no `Access-Control-Allow-Origin: *`. CORS wildcard would let any site fetch install.sh contents and exfil.

Test: `expect(res.headers.get("access-control-allow-origin")).not.toBe("*");`

**M-04 — CONNECT/TRACE.** Should be rejected at edge (Cloudflare blocks by default, but assert).

---

### `test/paths.test.ts` — Path fall-through and traversal

**P-01 — `/install.sh`, `/install`, `/get`, `/sh`.** All non-`/` paths with curl UA should redirect to wless.io OR return 404. **Must not** also serve install.sh — that creates parallel install vectors that bypass cache rules.

Test: `for (const path of ["/install.sh","/install","/get","/sh"]) { expect([302,404]).toContain(res.status); }`

**P-02 — `/.well-known/security.txt`.** Should return 200 with a real security contact, not the install script. (Bug bounty / responsible disclosure hygiene — see RFC 9116.)

**P-03 — Trailing-slash variants.** `/`, `//`, `///` — must canonicalize. `//` on some routers hits a different handler.

**P-04 — Null byte / percent-encoded path.** `/%00`, `/install.sh%00`, `/\x00`. Some path parsers truncate at null; the suffix must not change behavior.

**P-05 — Path traversal probes.** `/../install.sh`, `/%2e%2e/install.sh`. Should return same response as `/` for that UA, not 500.

**P-06 — Long path (DoS).** Path of 8KB. Should return 414 URI Too Long, not crash the isolate.

---

### `test/query-canonicalization.test.ts` — Query string handling

**Q-01 — `?explain=1` case sensitivity.** `?EXPLAIN=1`, `?Explain=1`. Decide once: case-sensitive or case-insensitive. Test enforces the choice.

**Q-02 — `?explain=true` vs `?explain=1`.** Only `1` is the documented contract. `?explain=true` → serve script (the curl branch), not walkthrough. Otherwise the contract grows in ways we don't control.

**Q-03 — `?explain=1&x=y` and unknown query params.** Unknown params are ignored; `?explain=1` still wins. **Must not** echo unknown params anywhere in the response (header reflection class).

**Q-04 — Repeated params (`?explain=1&explain=0`).** Define behavior — Worker convention says first wins. Test enforces.

**Q-05 — Fragment after query (`?explain=1#frag`).** Fragments don't reach the server, but assert with literal `%23` encoded.

---

### `test/cache-keys.test.ts` — CDN cache poisoning resistance

This is the highest-impact class. Cloudflare's default cache key includes Host + path + query but NOT User-Agent. Without an explicit `Vary: User-Agent` (or a custom cache key), a browser request can be cached and served to the next curl, or vice versa.

**C-01 — `Vary: User-Agent` present on all 200 responses.** Required so CDN caches respect UA branching.

Test: `expect(res.headers.get("vary")?.toLowerCase()).toContain("user-agent");`

**C-02 — `Cache-Control` is private/no-store on the install-script response, OR includes `Vary: User-Agent` — pick one.** A long `s-maxage` without Vary is the classic poisoning vector (CVE-2026-2836-class, even though Cloudflare's own CDN wasn't vulnerable).

**C-03 — `Cache-Control` on the redirect.** A redirect with `Cache-Control: public, max-age=86400` cached against UA-blind key would serve the redirect to curl. Assert no-cache or `Vary: User-Agent`.

**C-04 — Accept-Encoding does not affect cache key in a way that leaks.** Both `gzip` and `identity` should produce identical *decoded* bodies. (Otherwise a CDN serving a gzipped variant to a non-gzip client = corruption.)

**C-05 — No `ETag` or `Last-Modified` that's deterministic across deploys.** A stable ETag enables cache validation skip; an unstable one (timestamp) breaks reproducible-byte verification. Pick one and document.

---

### `test/headers-and-integrity.test.ts` — Response header contract

**H-01 — `Content-Type: text/plain; charset=utf-8`.** Exact match. Browsers sniff `text/html` → XSS risk if any UA branch ever returns the script to a browser context.

**H-02 — `X-Content-Type-Options: nosniff`.** Required.

**H-03 — `Content-Security-Policy` on the redirect 302.** A redirect to wless.io should still set CSP so if a browser quirk renders the body, no scripts run.

**H-04 — `Strict-Transport-Security`.** `max-age=63072000; includeSubDomains; preload`.

**H-05 — `Referrer-Policy: no-referrer`.** Don't leak `worthless.sh` referrer to wless.io (fingerprinting reduction).

**H-06 — `X-Worthless-Script-Sha256` header.** The body's sha256, so users can verify reproducibility (`curl worthless.sh \| sha256sum` → matches header). **Mandatory** for the install-script response — this is the "trust by computation" mechanism.

Test: `const body = await res.text(); const expected = res.headers.get("x-worthless-script-sha256"); expect(await sha256(body)).toBe(expected);`

**H-07 — No `Server`, `X-Powered-By`, `Via` reflection.** No version disclosure.

**H-08 — Response body byte-exact equals canonical install.sh.** This is the integrity assertion. Without it, an attacker who hijacks the deploy pipeline can append a single shell command and the UA tests would still pass.

---

### `test/range-and-size.test.ts` — Truncation and size attacks

**R-01 — `Range: bytes=0-100`.** Cloudflare auto-handles ranges on cached responses. A partial install.sh piped to sh = `set -e` half-parses, leaves the system in a broken state. Either:
   (a) Ignore Range header and always return full body, OR
   (b) Return 416 Range Not Satisfiable on the install endpoint.

Test enforces whichever you pick. Recommended: (a) — `expect(res.status).toBe(200); expect(body.length).toBe(canonicalLength);`

**R-02 — Compression bomb resistance.** Worker request body limit on Cloudflare is 100MB; the install.sh response is fixed-size, but assert the Worker doesn't echo any request data into the response. Already covered by Q-03 but test from the size angle: send 10MB UA, assert response < 50KB.

**R-03 — Response size is bounded.** Assert `body.length < 100_000` — install.sh is ~12KB; anything 10× that is a sign of corruption or template injection.

---

### `test/error-path.test.ts` — Failure mode safety

This is the sleeper. If the Worker `throw`s an unhandled exception, Cloudflare returns its own HTML error page with status 500. A user piping `curl | sh` then executes HTML as shell. `<` and `>` are valid in some shell contexts; the result is at minimum a noisy failure, at worst a code-execution surface.

**E-01 — Forced exception path returns text/plain with `set -e false` exit guard.** Worker handler should be wrapped: any caught error returns a tiny shell snippet like `echo "worthless.sh: server error, see https://wless.io/status" >&2; exit 1`.

Test: simulate by passing a malformed input (10MB UA per R-02). Assert: `status in [500,503]; content-type text/plain; body starts with "echo" or "#" — never "<".`

**E-02 — Cloudflare's default 1xxx error pages are shadowed.** Configure error page handler in wrangler.toml or use `cf.errorPage` Worker hook so even a runtime panic doesn't yield HTML.

**E-03 — No stack trace / file path in error body.** Even in dev, never `console.error(err.stack)` into the response.

---

### `test/ua-edge-cases.test.ts` — User-Agent classifier hardening

**U-01 — Ambiguous UAs.** `Mozilla/5.0 curl/8.4.0` (lying browser? legitimate composite?). Decide: positive allowlist (must START with curl/, wget/, etc.) wins over substring match. Test forces the choice.

**U-02 — Case sensitivity.** `CURL/8.4.0`, `Curl/8.4.0`. UA strings ARE case-sensitive per RFC 9110, but real clients are inconsistent. Test pins the policy.

**U-03 — Whitespace tricks.** Leading/trailing whitespace, tab characters, `curl/8.4.0\t` with embedded tab.

**U-04 — Unicode tricks.** RTL override (U+202E), zero-width joiner (U+200D), BOM (U+FEFF) prefixed UA. Should not bypass classifier.

**U-05 — Length attacks.** 64KB UA. Should not panic the isolate; should be classified the same as truncated version.

**U-06 — Empty vs missing UA.** `User-Agent: ` (empty value) vs no header at all. Both → fail-safe redirect.

**U-07 — Newline injection in UA (`curl/8\r\nX-Inject: 1`).** Already filtered by Cloudflare/Workers runtime in 2026, but assert no header echo and no double-response.

**U-08 — Composite real-world bots.** `curl/8.4.0 (compatible; Googlebot/2.1)`. Decide: allowlist-first (curl/) → script, OR redirect (treat compound UAs as ambiguous → safe path). Recommend the latter for paranoia.

---

### `test/concurrency.test.ts` — Chaos / race conditions

**X-01 — 100 concurrent requests, mixed UAs.** No request gets the wrong response.

**X-02 — Same client, two requests interleaved (curl + browser).** No state leak.

**X-03 — Worker isolate reuse.** Cloudflare reuses isolates. Any module-level mutable state would leak between requests. Test: send 50 requests, assert no response affects the next.

---

## Out-of-band controls — NOT testable in Vitest

These need other mechanisms (CI, Cloudflare config, GitHub settings):

| Control | Where it lives | Tracked in |
|---|---|---|
| Signed tags + protected `v*` ruleset | GitHub repo settings | WOR-323 |
| OIDC-bound deploy identity | GitHub Actions workflow | WOR-323 |
| Required-reviewer Actions environment | GitHub Actions env config | WOR-330 |
| Scoped Cloudflare API token (Worker-only, no DNS) | CF dashboard + Actions secret | WOR-323 |
| SLSA-3 provenance / cosign signing | CI pipeline + Sigstore | WOR-303 |
| DNSSEC, CAA pinning, registrar lock | Domain registrar | threat-model §4 |
| CT-log monitoring (cert.transparency alerts) | External (e.g., crt.sh subscription) | WOR-324 backlog |
| Reproducible-build verification of install.sh contents | CI step + signed `.sha256` artifact | WOR-303 |
| Cloudflare WAF rule against unusual request patterns | CF dashboard | post-launch |
| Two-person rule on `install.sh` diffs | Branch protection + CODEOWNERS | WOR-323 |
| Corp MITM / SSL inspection scenarios (user-side) | Documentation (`operator-hardening.md`) | already covered |

---

## Cross-references — 2026 attacks consulted

- **CVE-2026-2836** (Cloudflare Pingora cache poisoning, host-blind cache key) — drove C-01..C-04. Cloudflare's CDN unaffected, but the class is real.
- **Bitwarden CLI 2026.4.0 npm compromise** (Apr 22, 2026, 1.5h window) — reinforces deploy-pipeline integrity (out-of-band).
- **Axios npm supply-chain attack** (Q1 2026, account takeover) — same class, bypassed CI/CD via direct npm publish. Mitigation: only publish from CI with OIDC.
- **Shai-Hulud 2.0** (Zapier/ENS/PostHog/Postman, Q1 2026) — package-publishing maintainer-account compromise. Out-of-band.
- **PIPEPunisher / SNAKE Security 2025** ("Breaking the PIPE: Abusing PIPE to Shell Installations") — server-side detection of curl-to-bash piping. The `?explain=1` design is the direct mitigation; document in operator-hardening.
- **CVE-2026-24910** (Bun trust validation bypass via package-name spoofing) — not directly applicable, but reminds: trust boundaries must be byte-exact, not name-based.
- **Hacker News classics** ([id=11532599](https://news.ycombinator.com/item?id=11532599), [id=17636032](https://news.ycombinator.com/item?id=17636032)) — server-side curl-detection technique. Already considered in design.

---

## Implementation order for Phase 2

Build the test files in this order — earliest finds the worst bugs:

1. `error-path.test.ts` — finds the "HTML in shell pipe" bug if it exists
2. `cache-keys.test.ts` — most likely to cause real-world incident
3. `ua-edge-cases.test.ts` — broadest classifier surface
4. `paths.test.ts` — easy to forget, easy to test
5. `methods.test.ts`
6. `headers-and-integrity.test.ts`
7. `query-canonicalization.test.ts`
8. `range-and-size.test.ts`
9. `concurrency.test.ts`

Estimated count: **~50 RED tests across 9 files**, on top of the existing 15. Total Phase 2 surface: ~65 RED tests for Phase 3 to turn green.
