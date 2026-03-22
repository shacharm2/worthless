# Backlog

Deferred items captured during planning and discussion. Not scoped to any phase yet.

## V2 / Hardening

- **mTLS cert rotation** — 365-day certs in PoC; add auto-rotation before expiry with re-enrollment (Phase 3 discussion)
- **rate_limit rule** — time-windowed counters per alias; deferred from Phase 3 rules engine (spend_cap only in PoC)
- **model_allowlist rule** — restrict which models a key can access; trivial to add after spend_cap proves the gate pattern
- **token_budget rule** — requires parsing LLM response token counts; complex, may need Redis for hosted
- **time_window rule** — implementation detail of rate_limit and spend_cap windows
- **Proxy bearer token** — separate auth to the proxy itself; unnecessary while Shard A serves as credential, needed for multi-tenant hosted
- **mTLS for multi-tenant** — client certificates map to tenant identity when proxy goes remote/hosted
- **Alias enumeration protection** — prevent probing which providers have enrolled keys in multi-tenant
- **Spend cap counter hardening** — audit that no code path increments counter before successful reconstruction

## Pre-Production (from Phase 3.1 reviews)

- **Spend cap token reservation** — check-and-record are separate operations (PoC limitation); production fix: reserve estimated tokens at check time, reconcile after response (Jenny/Karen review)
- **Load/perf testing** — determine max RPS before SQLite BEGIN IMMEDIATE becomes bottleneck; memory profile rate limiter under sustained malicious traffic (Gemini review)
- **Integration/E2E test suite** — real SQLite + real middleware + local mock upstream server in separate process; validates connection teardowns and timeouts end-to-end (Gemini review)
- **Infrastructure hardening** — shard_a_dir file permissions (chmod 400), distroless non-root container for reconstruction service (Gemini review)
- **Rate limiter IP privacy** — client_ip stored in memory; evaluate GDPR/CCPA compliance, consider hashing IPs if retention scope applies (Gemini review)
- **Chunked body size enforcement** — BodySizeLimitMiddleware only checks Content-Length header; chunked transfer-encoded requests bypass it (Karen review)

## Ideas (no commitment)

(Captured from discuss-phase conversations and reviews — evaluate later)
