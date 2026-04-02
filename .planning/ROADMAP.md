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
- [x] **Phase 4: CLI** - Enroll, wrap, status, and scan commands for the 90-second setup experience (completed 2026-03-27)
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
- [x] 02-01-PLAN.md — Adapter contracts, OpenAI/Anthropic request transforms and non-streaming relay (TDD)
- [x] 02-02-PLAN.md — SSE streaming relay for both providers (TDD)

### Phase 3: Proxy Service
**Goal**: A running FastAPI proxy enforces the three architectural invariants -- client-side splitting, gate before reconstruction, server-side direct upstream call
**Depends on**: Phase 1, Phase 2
**Requirements**: CRYP-05, PROX-04, PROX-05
**Success Criteria** (what must be TRUE):
  1. The rules engine evaluates every request BEFORE Shard B is decrypted (gate-before-reconstruct verified)
  2. Setting `BASE_URL` to the proxy address causes API calls from any HTTP client to route through the proxy transparently
  3. The reconstructed key is used server-side for the upstream call and never appears in any response to the client
**Plans**: 2 plans

Plans:
- [x] 03-01-PLAN.md — Rules engine, metering, error responses, adapter bytearray migration
- [x] 03-02-PLAN.md — FastAPI proxy app with gate-before-reconstruct pipeline and transparent routing

### Phase 03.1: Proxy Hardening (INSERTED)

**Goal:** Fix all blocker, high, and medium-severity findings from the Phase 3 multi-agent review — SSE streaming, CRYP-05 gate ordering, SR-01 bytearray compliance, error handling, anti-enumeration, performance, and code quality
**Requirements**: CRYP-05, PROX-04, PROX-05
**Depends on:** Phase 3
**Plans:** 3/3 plans complete

Plans:
- [ ] 03.1-01-PLAN.md — Split ShardRepository for gate-before-decrypt, bytearray compliance, repr redaction, dead code removal
- [ ] 03.1-02-PLAN.md — SSE streaming, gate ordering wiring, zeroing, error handling, upstream sanitization
- [ ] 03.1-03-PLAN.md — Body size limit middleware, CORS denial, atomic spend cap, rate limiter TTL, persistent DB

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

### Phase 04.1: Post-CLI Wave 1 overhaul (INSERTED)

**Goal:** Reconcile all docs, examples, and port references to match the shipped CLI so the product story is honest and consistent. Fix code bugs, rewrite README quickstart-first with real terminal output, create wire protocol doc, and rename x-worthless-alias header to x-worthless-key.
**Requirements**: 04.1a-BUG, 04.1a-LINT, 04.1a-PORT, 04.1a-TEST, 04.1b-README, 04.1b-DOCS, 04.1b-EXAMPLES, 04.1b-PRECOMMIT, 04.1b-WALKTHROUGH, 04.1c-RENAME
**Depends on:** Phase 4
**Plans:** 4/4 plans complete

Plans:
- [x] 04.1-01-PLAN.md — Code prerequisites: port standardization (8787), wrap crash fix, ruff lint errors, test failures
- [x] 04.1-02-PLAN.md — README rewrite (quickstart-first), PROTOCOL.md, integration docs, examples directory
- [x] 04.1-03-PLAN.md — Header rename: x-worthless-alias to x-worthless-key (separate branch)
- [ ] 04.1-04-PLAN.md — Gap closure: fix scan temp file leak, README enroll terminology, PROTOCOL.md link

### Phase 04.2: Test Hardening (INSERTED)

**Goal:** Stabilize, extend, and wire the test suite into a 5-tier CI model with coverage gates, flaky quarantine, and GHA workflows -- the last quality gate before v1 release work begins
**Requirements**: WOR-73, WOR-74, WOR-75, WOR-76, WOR-78, WOR-79, CI-T1, CI-T2, CI-T3, CI-T4, CI-T5
**Depends on:** Phase 04.1
**Plans:** 1/3 plans executed

Plans:
- [ ] 04.2-01-PLAN.md — Test infra config: xdist loadscope, rerunfailures, coverage parallel, Hypothesis CI profile, conftest DRY, coverage floor script
- [ ] 04.2-02-PLAN.md — WOR-67 test backlog (WOR-73/74/75/76) + carryover fixes (flaky decoy, tempdir audit, mutmut:skip)
- [ ] 04.2-03-PLAN.md — 5-tier GHA workflows (tests.yml, sast.yml, scheduled.yml, pre-release.yml, benchmarks.yml) + TESTING.md update

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
| 2. Provider Adapters | 2/2 | Complete | 2026-03-15 |
| 3. Proxy Service | 2/2 | Complete | 2026-03-20 |
| 03.1. Proxy Hardening | 0/3 | Complete    | 2026-03-21 |
| 4. CLI | 4/4 | Complete   | 2026-03-27 |
| 04.1. Post-CLI Wave 1 overhaul | 4/4 | Complete    | 2026-04-02 |
| 04.2. Test Hardening | 1/3 | In Progress|  |
| 5. Security Posture Documentation | 0/1 | Not started | - |
