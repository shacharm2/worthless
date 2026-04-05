# Requirements: Worthless v2.0 Harden

**Defined:** 2026-04-06
**Core Value:** A developer installs Worthless and goes back to work with a quiet mind. Their API keys are architecturally worthless to anyone who steals them.
**Architectural Constraint:** Light mode (XOR + Fernet) is PERMANENT. Secure mode (Shamir + Rust sidecar) is additive. The two modes coexist forever.

## v2.0 Requirements

Requirements for the Harden milestone. Each maps to roadmap phases.

### Crypto Core

- [ ] **CRYPTO-01**: Shamir 2-of-3 secret sharing over GF(256) implemented in Rust with constant-time field arithmetic
- [ ] **CRYPTO-02**: Python companion module for enrollment-time splitting (wraps Rust via PyO3 or pure Python reimplementation)
- [ ] **CRYPTO-03**: Cross-compatibility verified — shares produced by Python are reconstructable by Rust and vice versa, proven by deterministic test vectors
- [ ] **CRYPTO-04**: SHA-256 commitment stored at enrollment, verified at reconstruction — tampered shards rejected before key forms
- [ ] **CRYPTO-05**: Shard C (recovery share) generated at enrollment with user-chosen backup format (base64 blob; mnemonic deferred)

### Shard Store

- [ ] **SHARD-01**: Rust `ShardStore` trait with auto-detection waterfall (platform-appropriate backend selected without user configuration)
- [ ] **SHARD-02**: macOS Keychain backend — `security` CLI, `-A` flag for headless, survives reboot
- [ ] **SHARD-03**: Windows Credential Manager backend — DPAPI-protected, survives reboot
- [ ] **SHARD-04**: Linux kernel keyring backend — `keyctl` with two-layer strategy (keyring for speed + encrypted file for reboot persistence)
- [ ] **SHARD-05**: Docker mounted secrets backend — reads from `/run/secrets/worthless-shard-b`
- [ ] **SHARD-06**: Encrypted file fallback backend — AES-256-GCM with platform-derived key, NOT plaintext (replaces spec's SeparatePathFileStore)
- [ ] **SHARD-07**: Environment variable backend — `WORTHLESS_SHARD_B` for CI/PaaS environments
- [ ] **SHARD-08**: CI matrix testing across all shipped backends (macOS, Windows, Linux kernel keyring, Docker, encrypted file, env var)

### Sidecar

- [ ] **SIDE-01**: Rust sidecar binary communicating over Unix domain socket (macOS/Linux) or named pipe (Windows)
- [ ] **SIDE-02**: Vault mode — returns reconstructed key bytes over socket, zeroes immediately after send
- [ ] **SIDE-03**: Proxy mode — formats Authorization header, makes upstream HTTPS call via reqwest, returns response + usage metadata
- [ ] **SIDE-04**: Peer UID verification via SO_PEERCRED (Linux) / LOCAL_PEERCRED (macOS) — only the spawning user's processes can connect
- [ ] **SIDE-05**: Reconstruction into mlock'd `Zeroizing<Vec<u8>>` with MADV_DONTDUMP — key material never hits swap or core dumps
- [ ] **SIDE-06**: Connection pooling for upstream HTTPS (reqwest ConnectionPool) — warm connections keep key lifetime ~15µs
- [ ] **SIDE-07**: SSE streaming support in proxy mode — sidecar streams LLM responses through socket without buffering full response

### Python Layer

- [ ] **PY-01**: `sidecar_client.py` — Unix socket / named pipe IPC client with timeout and error handling
- [ ] **PY-02**: `platform_detect.py` — OS + credential store detection for enrollment routing
- [ ] **PY-03**: `shard_split.py` — Python-side Shamir splitting for enrollment (calls Rust via PyO3 or pure Python)
- [ ] **PY-04**: `worthless get <alias>` command — vault mode, returns reconstructed key to stdout
- [ ] **PY-05**: `worthless up --secure` command — starts proxy + sidecar, routes through IPC
- [ ] **PY-06**: `proxy/app.py` rewired — in secure mode, steps 7-11 of request flow collapse into one sidecar IPC call
- [ ] **PY-07**: Light mode code paths preserved exactly as shipped in v1.0 — `worthless up` (no flag) runs XOR + Fernet single-process, zero sidecar dependency
- [ ] **PY-08**: `worthless lock` updated — in secure mode, enrollment uses Shamir 2-of-3 and stores shards via platform credential store
- [ ] **PY-09**: `worthless unlock` updated — in secure mode, uses sidecar vault mode for key restoration
- [ ] **PY-10**: `cryptography` dependency retained for light mode; secure mode uses Shamir via Rust — both coexist in the same install
- [ ] **PY-11**: `worthless wrap` preserved and working in both light and secure modes
- [ ] **PY-12**: Provider adapter layer (OpenAI, Anthropic) unchanged — adapters work identically regardless of mode
- [ ] **PY-13**: MCP server compatibility maintained — MCP commands work in both modes
- [ ] **PY-14**: Key revocation support — `worthless revoke <alias>` deletes all shards across stores
- [ ] **PY-15**: httpx connection cleanup — proper async lifecycle management for sidecar and direct connections

### Migration

- [ ] **MIG-01**: `worthless migrate` command converts Fernet enrollments to Shamir 2-of-3
- [ ] **MIG-02**: Per-key atomic migration with rollback — NOT all-or-nothing batch
- [ ] **MIG-03**: Migration state machine in SQLite: `{not_started, in_progress, complete}` per key
- [ ] **MIG-04**: Mixed state supported — some keys Fernet, some Shamir, both work simultaneously
- [ ] **MIG-05**: Crash recovery — resume from any point, partially-migrated keys still work via Fernet path
- [ ] **MIG-06**: `fernet.key` deletion is a separate explicit step after full migration, never automatic
- [ ] **MIG-07**: `.env` file compatibility maintained during and after migration — `worthless wrap` output unchanged

### Distribution

- [ ] **DIST-01**: maturin-built wheels shipping Python + Rust sidecar binary via `pip install worthless`
- [ ] **DIST-02**: Platform wheels: manylinux2014 x86_64 + aarch64, macOS universal2, Windows x86_64
- [ ] **DIST-03**: Docker multi-container deployment — proxy and sidecar in separate containers, neither has both shards
- [ ] **DIST-04**: CI pipeline for cross-platform wheel builds (GitHub Actions matrix)
- [ ] **DIST-05**: Supply chain gates — `cargo audit` + `cargo vet` in CI, fail on known vulnerabilities
- [ ] **DIST-06**: Fallback binary distribution via GitHub Releases for platforms where wheel build fails

### Docker

- [ ] **DOCK-01**: Docker Compose config — proxy + sidecar + optional Redis, pre-configured networking
- [ ] **DOCK-02**: Sidecar container image — distroless base, minimal attack surface
- [ ] **DOCK-03**: Docker secrets integration for shard storage — sidecar reads Shard B from mounted secret
- [ ] **DOCK-04**: Docker networking — proxy ↔ sidecar communicate over internal network, sidecar not exposed

### Hardening

- [ ] **HARD-01**: seccomp-BPF syscall filter — sidecar restricted to socket, read, write, mlock, madvise, sigaction, exit
- [ ] **HARD-02**: Landlock filesystem restriction — sidecar can only access socket path and shard store path
- [ ] **HARD-03**: macOS process hardening — sandbox-exec profile or Seatbelt equivalent
- [ ] **HARD-04**: Windows process hardening — Job Object restrictions limiting sidecar capabilities
- [ ] **HARD-05**: PR_SET_NO_NEW_PRIVS — sidecar cannot gain privileges after start
- [ ] **HARD-06**: Security tiers documented per platform (Tier 1: three real trust domains, Tier 2: two-and-a-half, Tier 3: two)
- [ ] **HARD-07**: Optional `worthless install --hardened` — runs sidecar as separate Unix user for UID isolation
- [ ] **HARD-08**: Pre-commit hooks updated for Shamir + sidecar security rules
- [ ] **HARD-09**: Green test suite gates merge — CI blocks on any test failure
- [ ] **HARD-10**: 90-second install target maintained — `pip install worthless && worthless lock` works in under 90s

### Performance

- [ ] **PERF-01**: Sidecar IPC round-trip < 50ms p99 for single request (reconstruction + upstream call excluded)
- [ ] **PERF-02**: 10 concurrent streaming requests without degradation — mlock budget management with buffer pool if needed

## v2.1 Requirements (Deferred)

### Shard Store

- **SHARD-D1**: WSL2 Bridge backend — `cmdkey.exe` interop (hardest backend, fragile, smallest user base)
- **SHARD-D2**: Linux Secret Service backend — D-Bus async, GNOME Keyring / KDE Wallet (may not be unlocked)

### Docker

- **DOCK-05**: Kubernetes CSI integration — CSI driver for shard injection into pods

### Crypto

- **CRYPTO-D1**: BIP39 mnemonic format for Shard C recovery backup
- **CRYPTO-D2**: PID pinning for sidecar socket authentication (beyond UID verification)

### Performance

- **PERF-D1**: Sidecar warm-start optimization — keep sidecar alive between requests via daemon mode

## Out of Scope

| Feature | Reason |
|---------|--------|
| Dashboard UI | SaaS, worthless-cloud repo |
| Team management UI | SaaS, worthless-cloud repo |
| Fernet elimination | Light mode is permanent — Fernet stays forever |
| Forced migration | Migration is optional — users choose secure mode |
| `cryptography` removal | Needed for light mode Fernet encryption |
| SSO/SAML | Enterprise tier, not v2.0 |
| Response caching / load balancing | Proxy feature, not security hardening |
| Gemini provider support | Stretch goal, not v2.0 scope |
| MPC / threshold signatures | Potential v3.0, not v2.0 |
| Hosted cloud proxy | Requires worthless-cloud infrastructure |

## Traceability

Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| *(populated by roadmapper)* | | |

**Coverage:**
- v2.0 requirements: 63 total
- Mapped to phases: 0 (pending roadmap)
- Unmapped: 63 ⚠️

---
*Requirements defined: 2026-04-06*
*Last updated: 2026-04-06 after initial definition*
