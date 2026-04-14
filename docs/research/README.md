# Shamir+Sidecar Architecture — Research & Design

**Decision date:** 2026-04-05
**Status:** Plan finalized, ready for implementation
**Destination:** worthless-cloud/docs/architecture/shamir-sidecar/

---

## What this is

The complete decision trail for replacing Worthless's XOR+Fernet split-key architecture with Shamir 2-of-3 secret sharing + a Rust sidecar process. From problem statement through deep research, platform analysis, expert review, spec creation, gap analysis, and final implementation plan.

## Reading order

### 1. The Problem
**`01-problem/fernet-key-bootstrap-problem.md`**
The Fernet encryption key sits on the same filesystem as the shards it protects. A single file-read attack reconstructs every enrolled API key. The split-key security claim is cosmetic.

### 2. Deep Research
**`02-research/shamir-sidecar-architecture.md`** — Claude deep research output. Proposes Shamir 2-of-3 across three OS trust domains + Rust sidecar. Analyzes MPC (rejected — bearer tokens must be transmitted verbatim), reviews the Shamir math, and identifies that no existing product splits bearer tokens per-request.

**`02-research/shamir-sidecar-security-review.md`** — Security agent review. Confirms: Shamir claim holds in 3 of 4 deployment modes. Docker single-host is the gap. Recommends rolling own GF(256) over blahaj crate.

**`02-research/shamir-sidecar-verification.md`** — Crypto claim verification. RUSTSEC-2024-0398 confirmed real. blahaj has 823 downloads (low bus-factor). ~15µs timing claim valid on warm path only. MPC dismissal accurate.

**`02-research/ux-impact-analysis.md`** — UX impact. Keychain breaks agent story on some platforms. Binary wheel supply chain needs investment. Migration is a breaking change without a tool. Docker UX regresses from single to multi-container.

### 3. Platform Research (5 parallel tracks)
**`03-platform-research/SYNTHESIS.md`** — Start here. Decision matrix covering 9 platforms, 3 security tiers, per-platform Shard B strategy, sidecar hardening tables, Docker reference architecture.

Individual tracks (read for detail):
- **`linux-kernel-keyring.md`** — `keyctl @u` works without root, headless, kernel memory. Doesn't survive reboot. Two-layer strategy: encrypted file (persistent) + keyring (runtime cache).
- **`macos-windows-credentials.md`** — macOS Keychain with `-A` flag works headlessly from unsigned binaries. Windows DPAPI/Credential Manager fully headless. WSL2 bridges to Windows.
- **`docker-container-injection.md`** — Non-Swarm Compose secrets are bind-mounts, not tmpfs. Multi-container with shared UDS is the answer. `docker exec` defeats all same-container mechanisms.
- **`process-isolation-no-sudo.md`** — `PR_SET_DUMPABLE(0)` is the killer finding. One syscall, no root, blocks same-UID ptrace and `/proc/pid/mem`. Available since Linux 2.3.20.
- **`fallback-encrypted-shard.md`** — Argon2id + AES-256-GCM for headless platforms. Machine-bound key derivation is weak but honest. 4-tier automatic detection strategy.

### 4. Build Spec
**`04-spec/sidecar-architecture-spec.md`** — The build spec from Claude Chat. Defines: Shamir splitting, sidecar binary (Rust), vault mode + proxy mode, shard store abstraction with 8 platform backends, socket protocol, Python layer, distribution via maturin, security properties, migration path, build order.

**`04-spec/spec-addendum.md`** — 7 amendments based on research gaps:
- A1: Security tiers (3 honest tiers per platform)
- A2: Encrypted file fallback (Argon2id, not plaintext)
- A3: Kernel keyring reboot strategy (two-layer)
- A4: SHA-256 integrity check on reconstruction
- A5: Unix socket peer authentication (SO_PEERCRED)
- A6: Existing features not in spec (unlock, decoys, batch lock, port 8787)
- A7: GF(256) verification requirements (static, functional, cross-impl, mutation)

### 5. Gap Analysis
**`05-analysis/spec-codebase-impact.md`** — File-by-file impact map against current src/worthless/. Every file classified: survives, modified, replaced, new. Traces the proxy-mode request flow change (steps 7-11 collapse to one sidecar IPC call). Verifies the spec's "UNCHANGED" claims (port is wrong, 90s target at risk).

**`05-analysis/spec-vs-research-gaps.md`** — 3 HIGH gaps (no tiers, blahaj vs roll-own, plaintext fallback) + 2 MEDIUM (keyring reboot, missing integrity check). All resolved in the addendum.

**`05-analysis/ticket-mapping.md`** — 71 Linear tickets mapped: 3 obsoleted (WOR-134, 135, 138), 22 need rewrite, 8 align, 38 unaffected.

### 6. The Plan
**`06-plan/implementation-plan.md`** — 6 phases with dependencies, deliverables, verification requirements, Linear ticket impact, and full research traceability.

---

## Key decisions

| Decision | Rationale | Source |
|----------|-----------|--------|
| Shamir 2-of-3 over XOR 2-of-2 | Information-theoretic security, backup shard, 3 trust domains | 02-research/shamir-sidecar-architecture.md |
| Roll own GF(256), not blahaj | 823 downloads, low bus-factor, ~100 lines, fully auditable | 02-research/shamir-sidecar-verification.md |
| Eliminate Fernet entirely | Circular bootstrap problem. Encrypting a useless shard adds complexity without security | 01-problem/fernet-key-bootstrap-problem.md |
| PR_SET_DUMPABLE(0) as trust boundary | One syscall, no root, blocks same-UID memory reads. Available everywhere | 03-platform-research/process-isolation-no-sudo.md |
| Docker multi-container, not single | `docker exec` defeats all same-container shard separation | 03-platform-research/docker-container-injection.md |
| Proxy mode default, vault mode available | Proxy mode = minimum code changes to existing codebase, spend caps work. Vault mode = generic key retrieval for any API | 04-spec/sidecar-architecture-spec.md |
| 3 honest security tiers | Not all platforms are equal. macOS ≠ headless Linux. Document honestly | 03-platform-research/SYNTHESIS.md |
| Keep port 8787 | Existing code uses 8787, no reason to change | 05-analysis/spec-codebase-impact.md |

## Participants

- **Claude Chat (deep research)** — Original proposal, build spec
- **Claude Code (Opus 4.6)** — Expert agent coordination, gap analysis, plan synthesis
- **Security engineer agents** — Security review, process isolation research, fallback research
- **DevOps agents** — Docker injection research, SAST analysis
- **Research analysts** — Crypto verification, kernel keyring research, credential store research
- **Product manager agent** — UX impact analysis
- **Brutus agent** — Adversarial stress test (4 real threats, 2 worth watching, 1 strawman)
- **Engineering planner agent** — Codebase impact, ticket mapping
