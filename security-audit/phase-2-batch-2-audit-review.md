# Phase 2 Batch 2 — Standards/Compliance Audit Review

> RFC + OWASP + CWE compliance review of `paths.test.ts`, `methods.test.ts`,
> `query-canonicalization.test.ts`. Reviewer had Read/Grep only.

## 1. Per-RFC compliance findings

### RFC 9110 (HTTP Semantics)
- **§15.5.6 (405 Method Not Allowed):** M-02 partially compliant. Asserts `Allow` non-null AND forbidden methods absent. **Gap:** does not assert `Allow` lists `GET, HEAD, OPTIONS` positively. RFC says SHOULD enumerate.
- **§9.3.2 (HEAD):** M-01 asserts status + content-type + empty body. **Gap:** no `Content-Length` parity (RFC 9110 §8.6 — HEAD SHOULD include `Content-Length` matching what GET would return).
- **§15.5.15 (414):** P-06 asserts 414 specifically — correct.
- **§9.3.8 (TRACE):** M-04 forbids 200, asserts no XST echo. **Gap:** RFC SHOULD be 405; current test allows any 4xx.

### RFC 9116 (security.txt)
- **§2.5.3 Contact (REQUIRED):** Asserted. Compliant.
- **§2.5.5 Expires (REQUIRED):** **MISSING — CRITICAL.** Test passes against a security.txt with no `Expires:` field, which is NON-CONFORMANT per RFC 9116. Must add: `expect(body).toMatch(/^Expires:\s+\d{4}-\d{2}-\d{2}T/m)` + future-date check + ≤1y range check.
- **Charset:** Content-Type matches `^text/plain` but does not require `charset=utf-8` per RFC 9116 §2.3.

### RFC 7230 §3.2.4 / RFC 9112 §2.2 (Header Injection)
- Q-03 checks query value not in body or any response header. **Gap:** no CRLF (`%0d%0a`) in query as smuggling probe.

## 2. CWE coverage gaps

| CWE | Coverage | Gap |
|---|---|---|
| CWE-22 (Path Traversal) | Partial (P-05) | **Missing double-encoded `%252e%252e`** — single-decode WAF bypass. |
| CWE-79 (XSS) | Partial (Q-03 body-echo) | No HTML-context reflection (`<script>` payload). |
| CWE-93/113 (CRLF Injection) | **MISSING** | No `%0d%0a` in query, path, or UA. |
| CWE-444 (Request Smuggling) | **MISSING** | `/install.sh\r\n\r\nGET /admin` not tested. Likely unreachable at workerd; smoke test recommended. |
| CWE-601 (Open Redirect) | Covered (Host-confusion redirect Location pinned) | OK. |

## 3. OWASP API Security Top 10 (2023) Coverage Matrix

| ID | Category | Coverage | Notes |
|---|---|---|---|
| API1 | Broken Object Level Auth | N/A | Single resource. |
| API2 | Broken Auth | N/A | Anonymous endpoint. |
| API3 | Broken Property Level Auth | ✓ | Q-02 pins exact value `1`. |
| API4 | Unrestricted Resource Consumption | ✓ partial | P-06 long path covered. |
| API5 | Broken Function Level Auth | ✓ | M-02 + P-01. |
| API6 | Unrestricted Sensitive Flows | N/A | |
| API7 | SSRF | N/A | Worker has no outbound fetch. |
| API8 | Security Misconfiguration | ✓ partial | M-03 covers CORS basics; **gap: ACAO=`*` + ACAC=`true` not tested.** |
| API9 | Improper Inventory Management | ✓ | P-02 security.txt. |
| API10 | Unsafe API Consumption | N/A | |

**Coverage rate (applicable):** 6/6 partial = 100%; ~70% full when weighting partial as 0.5.

## 4. Specific assertion fixes (8 total)

1. **methods.test.ts M-02 (405 Allow)** — add positive `GET/HEAD/OPTIONS` enumeration check.
2. **methods.test.ts M-03 (CORS)** — add ACAO=`*` + ACAC=`true` interaction check; pin OPTIONS status to 204 or 405 (per spec, 204).
3. **methods.test.ts M-01 (HEAD)** — add `Content-Length` parity check.
4. **paths.test.ts P-02 (security.txt)** — add `Expires:` REQUIRED-field assertion + future-date + ≤1y range.
5. **paths.test.ts P-05 (traversal)** — add double-encoded `%252e%252e` variant.
6. **query-canonicalization.test.ts Q-03** — extend SENTINEL set with CRLF and HTML payloads.
7. **paths.test.ts P-01** — add request-smuggling probe (smoke test, edge-layer expected).
8. **methods.test.ts M-04 (TRACE)** — tighten to `expect([405, 501]).toContain(res.status)`.

## 5. New tests recommended for compliance gaps

1. `security-txt-rfc9116-fields.test.ts` — verify ALL RFC 9116 fields (REQUIRED + RECOMMENDED + OPTIONAL).
2. `cors-credentials-interaction.test.ts` — CIS Benchmark §CORS.
3. `crlf-injection.test.ts` — CWE-93/113 across surfaces.
4. `double-encoded-traversal.test.ts` — extends P-05.
5. `request-smuggling.test.ts` — smoke test.
6. `server-banner-disclosure.test.ts` — NIST SP 800-53 SI-11.

## Summary

**Top 3 fixes** (priority order):
1. **P-02 must assert `Expires:` field** — without this, a malformed RFC 9116 file passes the test and Phase 3 ships non-conformant.
2. **M-03 must assert ACAO=`*` + ACAC=`true` is forbidden, and pin OPTIONS to 204** — Fetch-spec violation, OWASP API8, CIS Benchmarks.
3. **M-02 must assert `Allow:` enumerates GET/HEAD/OPTIONS positively** — RFC 9110 §15.5.6 SHOULD.
