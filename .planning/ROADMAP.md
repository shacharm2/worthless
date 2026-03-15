# Roadmap: Worthless

## Overview

Worthless delivers a split-key reverse proxy that makes API keys architecturally worthless to steal. The roadmap builds bottom-up: crypto primitives and storage first, then provider protocol handling, then the proxy that wires them together, then the CLI that gives developers their 90-second setup experience, and finally security documentation that honestly states what the PoC does and does not guarantee.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Crypto Core and Storage** - XOR splitting, HMAC commitment, shard storage with encryption at rest
- [ ] **Phase 2: Provider Adapters** - OpenAI and Anthropic request/response transformers with SSE streaming
- [ ] **Phase 3: Proxy Service** - FastAPI proxy with gate-before-reconstruct and transparent routing
- [ ] **Phase 4: CLI** - Enroll, wrap, status, and scan commands for the 90-second setup experience
- [ ] **Phase 5: Security Posture Documentation** - Honest documentation of protection status, confidence levels, and known limitations

## Phase Details

### Phase 1: Crypto Core and Storage
**Goal**: The cryptographic foundation exists and is independently verified -- keys can be split, stored, and reconstructed with integrity guarantees
**Depends on**: Nothing (first phase)
**Requirements**: CRYP-01, CRYP-02, CRYP-03, CRYP-04, STOR-01, STOR-02
**Success Criteria** (what must be TRUE):
  1. A key can be split into two shards and reconstructed to the original key using XOR
  2. Tampered shards are detected and rejected via HMAC verification
  3. Reconstructed key material is zeroed from memory after use (bytearray confirmed)
  4. The `random` module cannot be used anywhere in the codebase (lint rule enforced)
  5. Shard B is encrypted at rest in SQLite and enrollment metadata persists across restarts
**Plans**: 2 plans

Plans:
- [ ] 01-01-PLAN.md — XOR splitting, HMAC commitment, bytearray zeroing, lint enforcement (TDD)
- [ ] 01-02-PLAN.md — Encrypted shard storage with Fernet + aiosqlite (TDD)

### Phase 2: Provider Adapters
**Goal**: Stateless request/response transformers correctly handle OpenAI and Anthropic protocols, including streaming
**Depends on**: Nothing (can be built in parallel with Phase 1)
**Requirements**: PROX-01, PROX-02, PROX-03
**Success Criteria** (what must be TRUE):
  1. An OpenAI-format request to `/v1/chat/completions` produces a valid upstream request and parses the response
  2. An Anthropic-format request to `/v1/messages` produces a valid upstream request and parses the response
  3. SSE streaming works for both providers -- chunks arrive in real-time, not buffered
**Plans**: 2 plans

Plans:
- [ ] 02-01-PLAN.md — Adapter contracts, OpenAI/Anthropic request transforms and non-streaming relay (TDD)
- [ ] 02-02-PLAN.md — SSE streaming relay for both providers (TDD)

### Phase 3: Proxy Service
**Goal**: A running FastAPI proxy enforces the three architectural invariants -- client-side splitting, gate before reconstruction, server-side direct upstream call
**Depends on**: Phase 1, Phase 2
**Requirements**: CRYP-05, PROX-04, PROX-05
**Success Criteria** (what must be TRUE):
  1. The rules engine evaluates every request BEFORE Shard B is decrypted (gate-before-reconstruct verified)
  2. Setting `BASE_URL` to the proxy address causes API calls from any HTTP client to route through the proxy transparently
  3. The reconstructed key is used server-side for the upstream call and never appears in any response to the client
**Plans**: TBD

Plans:
- [ ] 03-01: TBD
- [ ] 03-02: TBD

### Phase 4: CLI
**Goal**: A developer can protect their API keys in 90 seconds using terminal commands, with no configuration files or dashboards
**Depends on**: Phase 1, Phase 3
**Requirements**: CLI-01, CLI-02, CLI-03, CLI-04
**Success Criteria** (what must be TRUE):
  1. `worthless enroll` accepts an API key, splits it, stores Shard A locally, sends Shard B to the proxy, and confirms protection
  2. `worthless wrap` configures environment variables so subsequent API calls route through the proxy without changing application code
  3. `worthless status` shows which keys are protected and whether the proxy is healthy
  4. `worthless scan` detects API keys in staged files and can block commits as a pre-commit hook
**Plans**: TBD

Plans:
- [ ] 04-01: TBD
- [ ] 04-02: TBD

### Phase 5: Security Posture Documentation
**Goal**: An honest, auditable document states exactly what Worthless protects, at what confidence level, and what its known limitations are
**Depends on**: Phase 3, Phase 4
**Requirements**: DOCS-01
**Success Criteria** (what must be TRUE):
  1. SECURITY_POSTURE.md exists with protection status for each architectural invariant and its confidence level
  2. Known limitations of the Python PoC (memory model, GC non-determinism) are explicitly documented with the planned Rust mitigation path
**Plans**: TBD

Plans:
- [ ] 05-01: TBD

## Progress

**Execution Order:**
Phases execute in numeric order. Phases 1 and 2 can be executed in parallel.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Crypto Core and Storage | 0/2 | Not started | - |
| 2. Provider Adapters | 1/2 | In progress | - |
| 3. Proxy Service | 0/2 | Not started | - |
| 4. CLI | 0/2 | Not started | - |
| 5. Security Posture Documentation | 0/1 | Not started | - |
