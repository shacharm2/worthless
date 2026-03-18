# Research Summary: Worthless

**Domain:** Split-key API proxy for LLM provider key security
**Researched:** 2026-03-14
**Overall confidence:** HIGH

## Executive Summary

Worthless is a split-key reverse proxy that makes API keys worthless to steal by eliminating the complete key from any single location. The architecture uses XOR secret sharing to split keys into two shards (client + server), reconstructs them ephemerally per-request in volatile memory, and enforces policy gates before any reconstruction occurs. This is genuinely unoccupied territory -- every competitor (Portkey, Helicone, LiteLLM, Infisical, Vault) protects the key; none eliminate it.

The stack is Python 3.12 / FastAPI for the PoC proxy and CLI, with a clear upgrade path to Rust for the security-critical reconstruction service. The Python PoC prioritizes shipping speed over memory safety guarantees, with honest documentation of limitations. The Rust hardening phase delivers the real security promise via `zeroize` crate and process isolation.

The primary technical risks are well-understood: Python's non-deterministic GC means reconstructed keys may linger in heap memory (mitigated with `bytearray` + explicit zeroing), XOR without authentication enables tampering (mitigated with HMAC commitment), and the proxy becomes a single point of failure for all LLM API calls (mitigated with health checks and graceful degradation). None of these are blockers for PoC.

The competitive landscape validates the positioning. LLM API key theft is an escalating real-world threat (Operation Bizarre Bazaar, $82K Gemini incident), and the market gap for "key elimination" vs. "key protection" is genuine. The 90-second setup target is achievable with the Typer CLI + env var wrapping approach.

## Key Findings

**Stack:** Python 3.12 / FastAPI / httpx / Typer / aiosqlite / stdlib crypto. Zero third-party crypto dependencies. uv for project management. Rust (axum + zeroize) for hardening phase.

**Architecture:** Five-component split-trust model: CLI sidecar (has Shard A), proxy (has encrypted Shard B + rules), reconstruction service (ephemeral key access), SQLite storage, provider adapters. Three non-negotiable invariants: client-side splitting, gate before reconstruction, server-side direct upstream call.

**Critical pitfall:** Reconstructed key in Python process memory is the honest limitation of the PoC. Use `bytearray` + explicit zeroing, document it, deliver the real guarantee in Rust.

## Implications for Roadmap

Based on research, suggested phase structure:

1. **Foundation (Storage + Models + Crypto)** - Build bottom-up from data layer
   - Addresses: SQLite schema, Pydantic models, XOR split/combine, HMAC commitment
   - Avoids: Building proxy before the crypto core is tested in isolation
   - Rationale: Crypto correctness is the product's security claim -- must be verified independently

2. **Provider Adapters** - Stateless request/response transformers
   - Addresses: OpenAI + Anthropic formatting, SSE streaming, auth header differences
   - Avoids: Generic passthrough that silently fails on provider differences (Pitfall #10)
   - Can be tested against real APIs with real keys before the proxy exists

3. **Reconstruction Module** - In-process Python for PoC
   - Addresses: XOR combine, upstream call, memory zeroing (best-effort)
   - Avoids: Premature Rust work before the protocol is proven
   - Depends on: Crypto core + provider adapters

4. **Proxy Service** - FastAPI with gate-before-reconstruct
   - Addresses: Request routing, auth middleware, rules engine, metering, SSE relay
   - Avoids: Feature creep (no caching, no content filtering, no load balancing)
   - Depends on: Storage + reconstruction module

5. **CLI** - Typer commands for enroll, wrap, status
   - Addresses: 90-second setup target, transparent proxying via env var override
   - Avoids: Exposing key during enrollment (getpass input, Pitfall #12)
   - Depends on: Crypto core (for splitting) + proxy API (for enrollment registration)

6. **Hardening** - Rust reconstruction + pen-test
   - Addresses: Memory safety guarantee (zeroize), process isolation, real security promise
   - Avoids: Shipping PoC limitations as production claims
   - This phase needs its own research spike for Rust FFI or separate-process communication

**Phase ordering rationale:**
- Layers 1-2 (Foundation + Adapters) can be built in parallel -- no dependencies between them
- Layer 3 (Reconstruction) needs both 1 and 2
- Layer 4 (Proxy) needs 1 and 3 -- this is the critical path
- Layer 5 (CLI) needs 1 and 4 -- last to build but first thing users touch
- Layer 6 (Hardening) is a separate milestone entirely

**Research flags for phases:**
- Phase 4 (Proxy): SSE streaming passthrough needs integration testing with real provider responses -- mock-only testing may miss edge cases in chunked transfer encoding
- Phase 5 (CLI): `worthless wrap` env var behavior needs testing against specific SDK versions (OpenAI Python, Anthropic Python, LangChain) -- SDK precedence rules vary
- Phase 6 (Hardening): Needs dedicated research into Rust-Python IPC patterns (Unix socket, gRPC, or in-process FFI via PyO3)

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All libraries verified against current PyPI/crates.io versions. FastAPI + httpx + Typer is the standard 2025/2026 async Python stack. |
| Features | HIGH | Competitive landscape well-documented. Feature set is tightly scoped with clear anti-features. |
| Architecture | HIGH | Split-trust model follows established secret sharing patterns. Three invariants are well-defined. |
| Pitfalls | HIGH | All pitfalls sourced from documented security properties of XOR sharing, Python memory model, and real-world proxy patterns. |
| Rust Hardening | MEDIUM | Rust crates (zeroize, axum, reqwest) verified, but IPC pattern between Python proxy and Rust reconstruction service needs phase-specific research. |

## Gaps to Address

- **SDK compatibility matrix:** Which exact versions of OpenAI Python SDK, Anthropic Python SDK, and LangChain have been tested with base_url override? Need integration tests, not assumptions.
- **Rust-Python IPC:** How does the proxy call the Rust reconstruction service? Options: Unix socket, HTTP on localhost, PyO3 in-process FFI. Each has different security properties. Needs research in hardening phase.
- **Shard B encryption at rest:** STACK.md recommends stdlib crypto for XOR but notes `cryptography` (pyca) may be needed for AES encryption of Shard B in SQLite. This decision should be made when implementing the storage layer.
- **keyring reliability:** The `keyring` library for OS keychain access is recommended at MEDIUM confidence. Need to test on macOS Keychain, Linux Secret Service (GNOME/KDE), and headless Linux (where keyring typically fails). Fallback strategy needed.
- **Windows support:** All research focused on macOS/Linux. Windows keychain (Credential Locker) via `keyring` is untested. Defer to post-PoC unless a Windows user shows up.

## Sources

All sources documented in individual research files:
- STACK.md: PyPI versions, crate docs, official framework documentation
- FEATURES.md: Competitor documentation (Portkey, Helicone, LiteLLM, Infisical, Vault)
- ARCHITECTURE.md: Cryptographic splitting references, NIST split knowledge definition, LLM gateway patterns
- PITFALLS.md: Security advisories, Wikipedia (secret sharing, timing attacks), PEP 506, real-world incidents
