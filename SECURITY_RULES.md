# Security Rules

Mandatory constraints for all Worthless code. Every commit, every phase, every review.
Referenced from CLAUDE.md. Verified by `/gsd:verify-work`.

## Memory Safety

**SR-01: No immutable types for secrets.**
Never store key material, shards, or reconstructed keys in `str`, `bytes`, or any immutable type. Use `bytearray` (Python) or `zeroize`-backed structs (Rust). Immutable objects linger in GC-managed memory and cannot be wiped.

**SR-02: Explicit memory zeroing.**
The instant an upstream HTTP request is dispatched, overwrite the reconstructed key buffer with zeros. Do not defer to garbage collection. Use `key_buf[:] = b'\x00' * len(key_buf)` (Python) or `zeroize` (Rust). Document any GC limitations in SECURITY_POSTURE.md.

## Gate Ordering

**SR-03: Check before build.**
The rules engine (spend cap, rate limit, model allowlist) MUST evaluate BEFORE XOR reconstruction runs. If the budget is blown, reject immediately (402/429). The key must never be reconstructed for a denied request. Zero KMS calls, zero key material touched.

## Telemetry

**SR-04: Zero telemetry on secrets.**
The reconstructed key, Shard A, Shard B, nonces, and commitments must NEVER be passed to any logging function, error handler, stack trace, or APM tool. Mask all `Authorization` and `x-api-key` headers in outbound request logs. Override `__repr__` on all crypto dataclasses to redact sensitive fields.

**SR-05: Logging denylist enforcement.**
These patterns must never appear in any log, anywhere:
- API key prefixes: `sk-*`, `anthropic-*`, `AIza*`, `xai-*`
- Certificate private keys
- Base64 strings > 32 chars in a security context
- Prompt or response content
- Raw user IP addresses

## Isolation

**SR-06: Sidecar/isolated execution.**
The reconstruction service runs in an isolated process with its own memory space. The main application (proxy, CLI, MCP server) never has access to the reconstruction service's memory or environment variables. In the Python PoC, reconstruction is in-process but architecturally separated — the Rust hardening phase enforces true process isolation (distroless container).

## Cryptographic Operations

**SR-07: Constant-time comparisons.**
All secret-dependent comparisons (HMAC verification, share validation) must use constant-time functions (`hmac.compare_digest` in Python, `constant_time_eq` in Rust). Never use `==` on digest bytes.

**SR-08: CSPRNG only.**
All random byte generation uses `secrets.token_bytes()` (Python) or `OsRng` (Rust). The `random` module is banned via Ruff TID251 lint rule. No exceptions.

## Traceability

| Rule | Phase enforced | Requirement |
|------|---------------|-------------|
| SR-01 | Phase 1+ | CRYP-03 |
| SR-02 | Phase 1+ | CRYP-03 |
| SR-03 | Phase 3 | CRYP-05 |
| SR-04 | All phases | Logging denylist |
| SR-05 | All phases | Logging denylist |
| SR-06 | Phase 3+ (full in Rust) | Architecture |
| SR-07 | Phase 1+ | CRYP-02 |
| SR-08 | Phase 1+ | CRYP-04 |
