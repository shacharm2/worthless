# Linear Ticket Mapping: Sidecar Architecture Spec

**Context:** WOR-141 — Map existing Linear tickets to the sidecar architecture spec (Section 13: Build Order).
**Spec:** `docs/research/sidecar-architecture-spec.md`
**Date:** 2026-04-04

## Spec Build Phases (Summary)

| Phase | Name | Scope |
|-------|------|-------|
| 1 | Shard Store Abstraction | Rust crate, ShardStore trait, platform backends, auto-detection |
| 2 | Sidecar Binary | Unix socket server, Shamir reconstruction, mlock/zeroize, vault+proxy modes, seccomp |
| 3 | Python Layer | CLI commands, sidecar lifecycle, FastAPI proxy, Fernet migration |
| 4 | Distribution | Maturin wheel build, cross-platform CI, `pip install worthless` |
| 5 | Hardening (post-launch) | Separate Unix user, Sigstore signing, security audit, SECURITY.md |

## Ticket Mapping

### Architecture Migration Epic (WOR-139)

| Ticket | Title | Status | Classification | Maps to Phase | Action | Notes |
|--------|-------|--------|---------------|---------------|--------|-------|
| WOR-139 | Architecture Migration: XOR+Fernet → Shamir+Sidecar | Backlog | UNAFFECTED | Meta | Keep | This IS the migration epic — parent of WOR-140 through WOR-144 |
| WOR-140 | Review new architecture design and confirm proxy-first approach | Backlog | ALIGNS | Pre-build | Keep | Gate before any build work |
| WOR-141 | Map existing Linear tickets to new architecture | Backlog | ALIGNS | Pre-build | Keep | This ticket (being done now) |
| WOR-142 | Archive obsoleted tickets with migration reason | Backlog | ALIGNS | Pre-build | Keep | Execute after this mapping is approved |
| WOR-143 | Create new implementation epics for Shamir+Sidecar | Backlog | ALIGNS | Pre-build | Keep | Create Phase 1-5 epics from this mapping |
| WOR-144 | Update CLAUDE.md, SECURITY_RULES.md, and roadmap | Backlog | ALIGNS | Pre-build | Keep | Update docs to reflect new architecture |

### Rust Reconstruction (WOR-60 and subtasks)

| Ticket | Title | Status | Classification | Maps to Phase | Action | Notes |
|--------|-------|--------|---------------|---------------|--------|-------|
| WOR-60 | Implement Rust reconstruction service over Unix socket | Backlog | NEEDS REWRITE | Phase 2 | Rewrite | Scope expands: now includes Shamir (not XOR), mlock/zeroize, vault+proxy modes, seccomp. The concept is right but implementation plan changes significantly |
| WOR-61 | Freeze reconstruction IPC contract: HTTP over UDS + CBOR | Backlog | NEEDS REWRITE | Phase 2 | Rewrite | IPC contract changes: Shamir share transport replaces Fernet-encrypted shard. Vault mode adds key-return path |
| WOR-62 | Scaffold Rust reconstruction service over UDS | Backlog | NEEDS REWRITE | Phase 2 | Rewrite | Now includes ShardStore trait (Phase 1), process self-protection, Shamir library |
| WOR-63 | Add Python reconstruction client and backend config switch | Backlog | NEEDS REWRITE | Phase 3 | Rewrite | Becomes sidecar lifecycle management (spawn, health check, shutdown) |
| WOR-64 | Move provider auth-header + upstream execution out of Python | Backlog | ALIGNS | Phase 2 | Keep | Proxy mode in sidecar does exactly this — upstream HTTPS via reqwest |
| WOR-65 | Add boundary and parity tests for Rust reconstruction cutover | Backlog | NEEDS REWRITE | Phase 2 | Rewrite | Tests need to cover Shamir reconstruction, not XOR |
| WOR-66 | Package reconstruction service for isolated non-root deployment | Backlog | NEEDS REWRITE | Phase 4 | Rewrite | Becomes maturin wheel packaging, not standalone binary packaging |

### PoC Security Fixes (WOR-138 and subtasks)

| Ticket | Title | Status | Classification | Maps to Phase | Action | Notes |
|--------|-------|--------|---------------|---------------|--------|-------|
| WOR-138 | PoC Security Fixes: key material exposure mitigations | Backlog | OBSOLETED | — | Archive | The spec eliminates the entire Fernet/XOR architecture these fixes target |
| WOR-134 | Fernet key persists as plaintext string in proxy memory | Backlog | OBSOLETED | — | Archive | Fernet is eliminated entirely. Sidecar uses mlock+zeroize in Rust — this Python-side fix becomes moot |
| WOR-135 | Docker: fernet.key + both shards on same volume | Backlog | OBSOLETED | — | Archive | Fernet eliminated. Sidecar architecture uses trust domain separation (platform credential stores), not Docker volumes |

### Docker & CI

| Ticket | Title | Status | Classification | Maps to Phase | Action | Notes |
|--------|-------|--------|---------------|---------------|--------|-------|
| WOR-136 | Docker live e2e test with real LLM API call | Backlog | NEEDS REWRITE | Phase 3 | Rewrite | Docker topology changes: Python proxy + Rust sidecar. Test needs to cover sidecar spawn, IPC, and proxy-mode upstream call |
| WOR-137 | Move SAST workflow to nightly + main-merge only | Backlog | UNAFFECTED | — | Keep | CI scheduling is orthogonal to architecture |

### Infrastructure Hardening (WOR-15)

| Ticket | Title | Status | Classification | Maps to Phase | Action | Notes |
|--------|-------|--------|---------------|---------------|--------|-------|
| WOR-15 | Infrastructure hardening: file permissions + distroless container | Backlog | ALIGNS | Phase 5 | Keep & update | Maps directly to Phase 5: separate Unix user, distroless container, hardened deployment. Update description to reference spec Phase 5 |

### Security Hardening (WOR-80 — open subtasks)

| Ticket | Title | Status | Classification | Maps to Phase | Action | Notes |
|--------|-------|--------|---------------|---------------|--------|-------|
| WOR-86 | Add request-body and streaming resource controls | Backlog | UNAFFECTED | — | Keep | Proxy-layer concern, unchanged by sidecar |
| WOR-87 | Disable ambient transport env trust | Backlog | UNAFFECTED | — | Keep | Transport security, orthogonal |
| WOR-88 | Replace unsafe bootstrap/package-install paths | Backlog | NEEDS REWRITE | Phase 4 | Rewrite | Distribution changes with maturin wheels |
| WOR-90 | Create SECURITY.md for vulnerability reporting | Backlog | ALIGNS | Phase 5 | Keep | Directly in spec Phase 5 |
| WOR-96 | Sanitize error messages to prevent internal state leaks | Backlog | UNAFFECTED | — | Keep | Proxy-layer concern |
| WOR-120 | Add SR-07/SR-08 AST tests | Backlog | NEEDS REWRITE | Phase 2 | Rewrite | SR rules change with Shamir; constant-time compare and CSPRNG rules still apply but to different code paths |
| WOR-121 | Add protocol_version to shards table for crypto agility | Backlog | ALIGNS | Phase 3 | Keep | Migration from Fernet storage needs protocol versioning |
| WOR-122 | Add worthless revoke command | Backlog | NEEDS REWRITE | Phase 3 | Rewrite | Revoke must clear shards from platform credential stores, not just SQLite |

### CLI & DX Polish (WOR-82 — open subtasks)

| Ticket | Title | Status | Classification | Maps to Phase | Action | Notes |
|--------|-------|--------|---------------|---------------|--------|-------|
| WOR-94 | Create SKILL.md agent discovery file | Backlog | UNAFFECTED | — | Keep | UX concern, not architecture |
| WOR-95 | Add worthless down command | Backlog | NEEDS REWRITE | Phase 3 | Rewrite | Must stop both proxy AND sidecar processes |
| WOR-101 | MCP docs use wrong process type | Backlog | UNAFFECTED | — | Keep | Docs fix |
| WOR-102 | GitHub Actions CI recipe mismatch | Backlog | UNAFFECTED | — | Keep | CI fix |
| WOR-123 | Add worthless keys command | Backlog | NEEDS REWRITE | Phase 3 | Rewrite | Key listing must query platform credential stores |
| WOR-124 | Package worthless scan as pre-commit hook | Backlog | UNAFFECTED | — | Keep | Scanner is unchanged |
| WOR-126 | MCP server for Worthless management ops | Backlog | UNAFFECTED | — | Keep | Management layer, orthogonal |
| WOR-127 | Add @error_boundary decorator and --debug flag | Backlog | UNAFFECTED | — | Keep | Error handling, orthogonal |

### Release Prep (WOR-83 — open subtasks)

| Ticket | Title | Status | Classification | Maps to Phase | Action | Notes |
|--------|-------|--------|---------------|---------------|--------|-------|
| WOR-97 | Confirm AGPL-3.0 license file | Backlog | UNAFFECTED | — | Keep | Legal, orthogonal |
| WOR-98 | Align pyproject.toml version | Backlog | UNAFFECTED | — | Keep | Versioning, orthogonal |
| WOR-99 | Publish worthless to PyPI | Backlog | NEEDS REWRITE | Phase 4 | Rewrite | Now ships maturin wheel with Rust binary |
| WOR-100 | Go public: release worthless repo | Backlog | UNAFFECTED | — | Keep | Release gate, orthogonal |
| WOR-125 | Deploy configs: Docker Compose, Railway, Render | Backlog | NEEDS REWRITE | Phase 4 | Rewrite | Docker topology changes: proxy + sidecar containers |
| WOR-128 | Gate or hide broken docs before launch | Backlog | UNAFFECTED | — | Keep | Docs concern |

### Proxy Performance (WOR-81)

| Ticket | Title | Status | Classification | Maps to Phase | Action | Notes |
|--------|-------|--------|---------------|---------------|--------|-------|
| WOR-81 | Proxy Performance (epic) | Backlog | UNAFFECTED | — | Keep | Proxy perf is orthogonal; proxy stays Python |
| WOR-91 | Remove full-buffering from proxy relay | Backlog | UNAFFECTED | — | Keep | Proxy streaming, unchanged |
| WOR-92 | Measure SpendCapRule contention | Backlog | UNAFFECTED | — | Keep | Proxy concern |
| WOR-93 | Verify spend_log writes under SQLite lock | Backlog | UNAFFECTED | — | Keep | Storage concern |

### Testing (WOR-7 and test-related)

| Ticket | Title | Status | Classification | Maps to Phase | Action | Notes |
|--------|-------|--------|---------------|---------------|--------|-------|
| WOR-7 | Testing and CI Foundation | Todo | UNAFFECTED | — | Keep | Test infra is orthogonal |
| WOR-14 | Integration/E2E test suite with real components | Backlog | NEEDS REWRITE | Phase 3 | Rewrite | E2E must include sidecar |
| WOR-32 | Live uvicorn test harness for CLI integration tests | Backlog | NEEDS REWRITE | Phase 3 | Rewrite | Harness must spawn sidecar too |
| WOR-46 | Wrap/up integration coverage | Backlog | NEEDS REWRITE | Phase 3 | Rewrite | wrap/up commands change with sidecar lifecycle |
| WOR-48 | Enable OpenAPI schema + schemathesis | Backlog | UNAFFECTED | — | Keep | Proxy API unchanged |
| WOR-76 | Property test: split_key/reconstruct_key roundtrip | Backlog | NEEDS REWRITE | Phase 1 | Rewrite | Shamir replaces XOR — roundtrip test needs new primitives |
| WOR-77 | Live uvicorn test harness (dupe of WOR-32) | Backlog | NEEDS REWRITE | Phase 3 | Rewrite | Same as WOR-32 |
| WOR-47 | Atheris fuzz targets for SSE + adapter normalization | Backlog | UNAFFECTED | — | Keep | SSE parsing unchanged |
| WOR-51 | Atheris fuzz: SSE stream chunk parser | Backlog | UNAFFECTED | — | Keep | Unchanged |
| WOR-52 | Atheris fuzz: adapter normalization + split_key | Backlog | NEEDS REWRITE | Phase 1 | Rewrite | split_key becomes Shamir |
| WOR-73 | Test up.py and wrap.py via CliRunner | Backlog | NEEDS REWRITE | Phase 3 | Rewrite | Commands change with sidecar |
| WOR-74 | Test enroll_stub.py and unlock multi-enrollment | Backlog | NEEDS REWRITE | Phase 3 | Rewrite | Enrollment changes for Shamir + credential store |
| WOR-75 | Test proxy upstream error handlers | Backlog | UNAFFECTED | — | Keep | Proxy error handling unchanged |
| WOR-78 | Conftest DRY consolidation | Backlog | UNAFFECTED | — | Keep | Test hygiene |
| WOR-79 | xdist parallel tests + mock sleeps | Backlog | UNAFFECTED | — | Keep | Test hygiene |
| WOR-132 | Validate TestSprite tunnel connectivity | Backlog | UNAFFECTED | — | Keep | Test infra |
| WOR-133 | Run first successful TestSprite test suite | Backlog | UNAFFECTED | — | Keep | Test infra |

### Other Standalone Tickets

| Ticket | Title | Status | Classification | Maps to Phase | Action | Notes |
|--------|-------|--------|---------------|---------------|--------|-------|
| WOR-12 | Spend cap token reservation (TOCTOU) | Backlog | UNAFFECTED | — | Keep | Proxy-layer metering concern |
| WOR-13 | Load/perf testing: SQLite bottleneck | Backlog | UNAFFECTED | — | Keep | Storage concern |
| WOR-16 | Rate limiter IP privacy (GDPR/CCPA) | Backlog | UNAFFECTED | — | Keep | Proxy concern |
| WOR-17 | Chunked body size enforcement | Backlog | UNAFFECTED | — | Keep | Proxy concern |
| WOR-19 | Document CRLF header non-risk | Backlog | UNAFFECTED | — | Keep | Docs |
| WOR-20 | Add mTLS client cert rotation | Backlog | NEEDS REWRITE | Phase 5 | Rewrite | mTLS changes with sidecar IPC model |
| WOR-21 | Add per-alias rate_limit rule | Backlog | UNAFFECTED | — | Keep | Proxy rules engine |
| WOR-23 | Wave-based persona testing rollout | Backlog | UNAFFECTED | — | Keep | Test methodology |
| WOR-24 | Wave 2 black-box persona harness | Backlog | UNAFFECTED | — | Keep | Test methodology |
| WOR-44 | Syrupy snapshots + pytest-benchmark | Backlog | UNAFFECTED | — | Keep | Test tooling |
| WOR-49 | Syrupy snapshot assertions on adapters | Backlog | UNAFFECTED | — | Keep | Test tooling |
| WOR-50 | pytest-benchmark baselines for crypto/proxy | Backlog | NEEDS REWRITE | Phase 2 | Rewrite | Crypto hot path moves to Rust |
| WOR-55 | Public release readiness (epic) | Backlog | UNAFFECTED | — | Keep | Release gate |
| WOR-56 | Wire SonarQube Cloud quality gates | Backlog | UNAFFECTED | — | Keep | CI tooling |
| WOR-57 | Enable GitHub Copilot code review | Backlog | UNAFFECTED | — | Keep | CI tooling |
| WOR-58 | Evaluate TestSprite for automated test gen | Backlog | ALREADY DONE | — | Keep | WOR-129 completed this evaluation |
| WOR-10 | Add macOS/manual CI lane | Backlog | UNAFFECTED | — | Keep | CI concern |
| WOR-11 | Evaluate static analysis and PR review automation | Backlog | UNAFFECTED | — | Keep | CI concern |

### Completed Tickets (Done/Canceled)

| Ticket | Title | Status | Classification | Notes |
|--------|-------|--------|---------------|-------|
| WOR-1 | Phase 1: Crypto Core and Storage | Done | ALREADY DONE | XOR+Fernet crypto — superseded by Shamir but the work informed the spec |
| WOR-2 | Phase 2: Provider Adapters | Done | ALREADY DONE | Adapters unchanged by sidecar architecture |
| WOR-3 | Phase 3: Proxy Service | Done | ALREADY DONE | Proxy stays Python, adapts to talk to sidecar |
| WOR-4 | Phase 4: CLI | Done | ALREADY DONE | CLI commands adapt in Phase 3 of new spec |
| WOR-5 | Phase 5: Security Posture Documentation | Done | ALREADY DONE | Security docs need update (WOR-144) |
| WOR-6 | Phase 3.1: Proxy Hardening | Done | ALREADY DONE | Hardening carries forward |
| WOR-25 | Pre-commit hooks | Done | ALREADY DONE | Unchanged |
| WOR-30 | Smoke test round-trip | Done | ALREADY DONE | Needs adaptation for Shamir |
| WOR-31 | Indistinguishable decoy generation | Done | ALREADY DONE | Unchanged |
| WOR-59 | 04.1: Post-CLI Wave 1 overhaul | Done | ALREADY DONE | Unchanged |
| WOR-67 | Test Suite: DRY, Speed & Coverage | Done | ALREADY DONE | Foundation carries forward |
| WOR-72 | Enable pytest-xdist + Hypothesis fast | Done | ALREADY DONE | Unchanged |
| WOR-80 | Security Hardening (epic) | Done | ALREADY DONE | Subtasks remain open — see above |
| WOR-82 | CLI & DX Polish (epic) | Done | ALREADY DONE | Subtasks remain open — see above |
| WOR-83 | Release Prep (epic) | Done | ALREADY DONE | Subtasks remain open — see above |
| WOR-129 | TestSprite Integration | Done | ALREADY DONE | Unchanged |
| WOR-33/34 | (Canceled duplicates) | Canceled | — | Already cleaned up |
| WOR-37/38/39 | (Duplicates) | Duplicate | — | Already cleaned up |

## Summary Counts

| Classification | Count | Action |
|----------------|-------|--------|
| ALIGNS | 8 | Keep and update description to reference spec phase |
| OBSOLETED | 3 | Archive with migration reason (WOR-134, WOR-135, WOR-138) |
| NEEDS REWRITE | 22 | Concept survives, implementation plan changes |
| UNAFFECTED | 38 | Orthogonal to architecture change |
| ALREADY DONE | 17 | Completed work the spec builds on |

## Recommended Next Steps

1. **WOR-142**: Archive WOR-134, WOR-135, WOR-138 with reason "Obsoleted by sidecar architecture — Fernet eliminated"
2. **WOR-143**: Create Phase 1-5 epics from the spec, then re-parent NEEDS REWRITE tickets under the appropriate phase epic
3. **WOR-144**: Update CLAUDE.md architectural invariants (Shamir replaces XOR, Fernet eliminated, sidecar holds shard)
