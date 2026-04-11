# Roadmap: Worthless

## Milestones

- [x] **v1.0 MVP** -- Phases 1-5 (shipped 2026-04-03)
- [ ] **v2.0 Harden** -- Phases 6-13 (in progress)

## Phases

<details>
<summary>v1.0 MVP (Phases 1-5) -- SHIPPED 2026-04-03</summary>

- [x] Phase 1: Crypto Core and Storage (2/2 plans) -- completed 2026-03-15
- [x] Phase 2: Provider Adapters (2/2 plans) -- completed 2026-03-15
- [x] Phase 3: Proxy Service (2/2 plans) -- completed 2026-03-20
- [x] Phase 03.1: Proxy Hardening (3/3 plans) -- completed 2026-03-21
- [x] Phase 4: CLI (4/4 plans) -- completed 2026-03-27
- [x] Phase 04.1: Post-CLI Wave 1 Overhaul (4/4 plans) -- completed 2026-04-02
- [x] Phase 04.2: Test Hardening (3/3 plans) -- completed 2026-04-02
- [x] Phase 5: Security Posture Documentation (2/2 plans) -- completed 2026-04-03

Full details: [milestones/v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md)

</details>

### v2.0 Harden

**Milestone Goal:** Add secure mode (Shamir 2-of-3 + Rust sidecar) alongside the permanent light mode (XOR + Fernet). Light mode stays unchanged forever. Secure mode is purely additive.

**Architectural Constraint:** Light mode (XOR + Fernet) is PERMANENT. No phase removes Fernet code paths. Secure mode is additive. The two modes coexist forever.

**Execution Order:** (6 || 7) -> 8 -> 9 -> (10 || 11) -> 12 -> 13

- [ ] **Phase 6: Shamir Core** - GF(256) Shamir 2-of-3 in Rust with Python companion and cross-compatibility
- [ ] **Phase 7: Shard Store** - Platform credential store backends with auto-detection waterfall
- [ ] **Phase 8: Sidecar Core** - Rust sidecar binary with vault mode, proxy mode, and SSE streaming over IPC
- [ ] **Phase 9: Sidecar Hardening** - OS-level sandboxing and performance validation
- [ ] **Phase 10: Distribution** - maturin wheels, CI cross-platform builds, Docker multi-container, fallback binaries
- [ ] **Phase 11: Python Layer Rewire** - Proxy, CLI, and adapter layer rewired for dual-mode operation (light + secure)
- [ ] **Phase 12: Migration** - Optional `worthless migrate` for Fernet-to-Shamir conversion with per-key rollback
- [ ] **Phase 13: Security Hardening and Documentation** - Security gates, pre-commit hooks, install target, security tier docs

## Phase Details

### Phase 6: Shamir Core
**Goal**: Developers have a verified Shamir 2-of-3 secret sharing implementation that produces cross-compatible shares between Rust and Python
**Depends on**: Nothing (first phase of v2.0, parallel with Phase 7)
**Requirements**: CRYPTO-01, CRYPTO-02, CRYPTO-03, CRYPTO-04, CRYPTO-05
**Success Criteria** (what must be TRUE):
  1. Rust library splits a secret into 3 shares and reconstructs from any 2, with constant-time GF(256) arithmetic
  2. Python companion module splits secrets at enrollment time using the same algorithm
  3. Shares produced by Python reconstruct correctly in Rust (and vice versa), proven by deterministic test vectors
  4. SHA-256 commitment is stored at enrollment and verified at reconstruction -- tampered shards are rejected before the key forms
  5. Shard C (recovery share) is generated at enrollment in base64 backup format
**Plans**: TBD

Plans:
- [ ] 06-01: TBD
- [ ] 06-02: TBD

### Phase 7: Shard Store
**Goal**: Shard B is stored in platform-native credential stores with automatic backend selection -- no user configuration required
**Depends on**: Nothing (first phase of v2.0, parallel with Phase 6)
**Requirements**: SHARD-01, SHARD-02, SHARD-03, SHARD-04, SHARD-05, SHARD-06, SHARD-07, SHARD-08
**Success Criteria** (what must be TRUE):
  1. Running `worthless lock` on macOS stores Shard B in Keychain; on Linux uses kernel keyring; on Windows uses Credential Manager -- without user choosing a backend
  2. Docker environments read Shard B from `/run/secrets/worthless-shard-b` automatically
  3. CI/PaaS environments read Shard B from `WORTHLESS_SHARD_B` environment variable
  4. Encrypted file fallback works on any platform where native store is unavailable (AES-256-GCM, NOT plaintext)
  5. CI matrix tests pass for all shipped backends across macOS, Linux, and Windows
**Plans**: TBD

Plans:
- [ ] 07-01: TBD
- [ ] 07-02: TBD

### Phase 8: Sidecar Core
**Goal**: A Rust sidecar binary handles key reconstruction and upstream calls over IPC, keeping key material in mlock'd memory that never hits swap or core dumps
**Depends on**: Phase 6, Phase 7
**Requirements**: SIDE-01, SIDE-02, SIDE-03, SIDE-04, SIDE-05, SIDE-06, SIDE-07
**Success Criteria** (what must be TRUE):
  1. Sidecar binary communicates over Unix domain socket (macOS/Linux) or named pipe (Windows), with peer UID verification rejecting unauthorized callers
  2. Vault mode returns reconstructed key bytes over the socket and zeroes them immediately after send
  3. Proxy mode makes the upstream HTTPS call directly (key never leaves the sidecar process), returning the response and usage metadata
  4. SSE streaming responses pass through the sidecar socket without buffering the full response
  5. Key material lives in mlock'd `Zeroizing<Vec<u8>>` with MADV_DONTDUMP -- never in swap or core dumps
**Plans**: TBD

Plans:
- [ ] 08-01: TBD
- [ ] 08-02: TBD
- [ ] 08-03: TBD

### Phase 9: Sidecar Hardening
**Goal**: The sidecar process is sandboxed at the OS level and validated for production-grade latency under concurrent load
**Depends on**: Phase 8
**Requirements**: HARD-01, HARD-02, HARD-03, HARD-04, HARD-05, PERF-01, PERF-02
**Success Criteria** (what must be TRUE):
  1. On Linux, the sidecar runs under seccomp-BPF (restricted syscall set) and Landlock (restricted filesystem) with PR_SET_NO_NEW_PRIVS
  2. On macOS, the sidecar runs under a sandbox-exec profile or Seatbelt equivalent restricting capabilities
  3. On Windows, the sidecar runs under a Job Object restricting capabilities
  4. IPC round-trip latency is under 50ms p99 (excluding upstream call time)
  5. 10 concurrent streaming requests complete without degradation, with mlock budget managed via buffer pool if needed
**Plans**: TBD

Plans:
- [ ] 09-01: TBD
- [ ] 09-02: TBD

### Phase 10: Distribution
**Goal**: Users install Worthless with `pip install worthless` and get both the Python package and Rust sidecar binary, with Docker multi-container deployment available
**Depends on**: Phase 8
**Requirements**: DIST-01, DIST-02, DIST-03, DIST-04, DIST-05, DIST-06, DOCK-01, DOCK-02, DOCK-03, DOCK-04
**Success Criteria** (what must be TRUE):
  1. `pip install worthless` on manylinux2014 (x86_64, aarch64), macOS (universal2), and Windows (x86_64) delivers a working wheel with the sidecar binary included
  2. `docker compose up` starts proxy + sidecar in separate containers with pre-configured networking, neither container holding both shards
  3. Sidecar container uses a distroless base image with minimal attack surface
  4. CI pipeline builds and tests wheels across all target platforms on every PR
  5. `cargo audit` and `cargo vet` gates fail the build on known vulnerabilities
**Plans**: TBD

Plans:
- [ ] 10-01: TBD
- [ ] 10-02: TBD
- [ ] 10-03: TBD

### Phase 11: Python Layer Rewire
**Goal**: The Python proxy, CLI, and adapter layer support dual-mode operation -- `worthless up` runs light mode exactly as v1.0, `worthless up --secure` routes through the sidecar
**Depends on**: Phase 8 (parallel with Phase 10)
**Requirements**: PY-01, PY-02, PY-03, PY-04, PY-05, PY-06, PY-07, PY-08, PY-09, PY-10, PY-11, PY-12, PY-13, PY-14, PY-15
**Success Criteria** (what must be TRUE):
  1. `worthless up` (no flag) runs exactly as v1.0 shipped -- XOR + Fernet, single process, no sidecar dependency
  2. `worthless up --secure` starts the proxy and sidecar, routing requests through IPC for reconstruction
  3. `worthless lock` in secure mode uses Shamir 2-of-3 splitting and stores shards via platform credential store
  4. `worthless get <alias>` retrieves a reconstructed key via sidecar vault mode
  5. `worthless wrap`, provider adapters (OpenAI, Anthropic), and MCP server all work identically in both modes
**Plans**: TBD

Plans:
- [ ] 11-01: TBD
- [ ] 11-02: TBD
- [ ] 11-03: TBD

### Phase 12: Migration
**Goal**: Users with existing Fernet enrollments can optionally convert to Shamir with per-key rollback, while mixed key state works seamlessly
**Depends on**: Phase 11
**Requirements**: MIG-01, MIG-02, MIG-03, MIG-04, MIG-05, MIG-06, MIG-07
**Success Criteria** (what must be TRUE):
  1. `worthless migrate` converts a Fernet enrollment to Shamir 2-of-3 for a specific key, with the option to roll back
  2. Migration is per-key and atomic -- failure on one key does not affect others
  3. Mixed state works: some keys use Fernet, some use Shamir, the proxy routes each correctly
  4. Crash recovery resumes from any point -- a partially-migrated key still works via the Fernet path
  5. `fernet.key` deletion is a separate explicit user action, never automatic
**Plans**: TBD

Plans:
- [ ] 12-01: TBD
- [ ] 12-02: TBD

### Phase 13: Security Hardening and Documentation
**Goal**: Security posture is documented with platform-specific trust tiers, CI gates enforce Shamir + sidecar invariants, and the 90-second install target is maintained
**Depends on**: Phase 11
**Requirements**: HARD-06, HARD-07, HARD-08, HARD-09, HARD-10
**Success Criteria** (what must be TRUE):
  1. Security tiers (Tier 1/2/3) are documented per platform with trust domain analysis
  2. `worthless install --hardened` runs the sidecar as a separate Unix user for UID isolation
  3. Pre-commit hooks enforce Shamir + sidecar security rules alongside existing v1.0 rules
  4. CI blocks merge on any test failure -- green suite gates all merges
  5. `pip install worthless && worthless lock` completes in under 90 seconds
**Plans**: TBD

Plans:
- [ ] 13-01: TBD
- [ ] 13-02: TBD

## Progress

**Execution Order:** (6 || 7) -> 8 -> 9 -> (10 || 11) -> 12 -> 13

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 6. Shamir Core | v2.0 | 0/? | Not started | - |
| 7. Shard Store | v2.0 | 0/? | Not started | - |
| 8. Sidecar Core | v2.0 | 0/? | Not started | - |
| 9. Sidecar Hardening | v2.0 | 0/? | Not started | - |
| 10. Distribution | v2.0 | 0/? | Not started | - |
| 11. Python Layer Rewire | v2.0 | 0/? | Not started | - |
| 12. Migration | v2.0 | 0/? | Not started | - |
| 13. Security Hardening and Documentation | v2.0 | 0/? | Not started | - |
