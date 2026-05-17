# Shamir+Sidecar Implementation Plan

**Date:** 2026-04-05
**Status:** Final plan — reviewed by 3 expert agents, 14 findings incorporated (1 CRITICAL, 5 HIGH, 8 MEDIUM)
**Basis:** sidecar-architecture-spec.md + spec-addendum.md + all research tracks

---

## Goal

Replace XOR+Fernet with Shamir 2-of-3 + Rust sidecar. Proxy mode default, vault mode available. Fernet eliminated entirely. API key never enters Python's address space.

---

## Phase 1: GF(256) Shamir Core

**What:** Rust crate implementing Shamir 2-of-3 secret sharing over GF(256)
**Size:** ~250-300 lines implementation + ~400-600 lines tests
**Ships:** `worthless-crypto` internal crate

### Deliverables
- GF(256) lookup tables from AES irreducible polynomial (0x11b), constant-time indexing
- `split(secret: &[u8], threshold: u8, shares: u8) -> Vec<Share>`
- `reconstruct(shares: &[Share]) -> Vec<u8>`
- **CRITICAL: Coefficient range rules:**
  - Constant term `a0` = the secret byte (not random)
  - Non-constant coefficients `a1` sampled from **[1, 255]** (NOT [0, 255]). If `a1=0`, the polynomial degenerates to a constant and every share equals the secret — probability 1/256 per byte of catastrophic information leak. This is a DIFFERENT bug from RUSTSEC-2024-0398 (which was x-coordinate bias).
- Shares are same size as the original secret

### Verification (mandatory gate — do not proceed to Phase 2 without passing)
1. **Static:** Cross-verify GF(256) tables against NIST FIPS 197, codahale/shamir (Java), Wikipedia
2. **Roundtrip:** Property test — split then reconstruct with all 3 combinations (A+B, A+C, B+C), random keys 1-500 bytes
3. **Independence:** Statistical test — single shard distribution is uniform
4. **Coefficient range:** Assert `a1` sampled from [1, 255]. Test that a1=0 is rejected/impossible. Test that x-coordinates are distinct and non-zero.
5. **Degenerate polynomial test:** Force a1=0 in test harness, verify that the implementation either rejects it or resamples. This is the most important single test.
6. **Timing oracle:** Reconstruction timing must not leak share validity (constant-time, aligns with SR-07)
7. **Edge cases:** Empty, 1-byte, all-zeros, all-0xFF, max length
8. **Cross-implementation:** Test vectors from codahale/shamir, split in Python ↔ reconstruct in Rust
9. **Mutation testing:** cargo-mutants, 100% kill rate
10. **Documentation:** Mathematical basis, polynomial choice, worked example with known seed, explicit note on a1≠0 requirement

### Python companion
- `shard_split.py` — Python-side Shamir for enrollment (wraps Rust via PyO3 or reimplements in pure Python)
- **Cross-compatibility requirement:** Shares produced by Python must be reconstructable by Rust, and vice versa. The GF(256) polynomial evaluation must be identical across both implementations. The random coefficients will differ (each uses OS CSPRNG) — that's expected. Verify with deterministic test vectors using a fixed seed → known shares → known reconstruction.

---

## Phase 2: Shard Store Abstraction

**What:** Rust `ShardStore` trait with platform backends + auto-detection
**Size:** ~800-1000 lines (6 backends v1, 2 deferred)
**Ships:** Platform credential store integration

### ShardStore trait
```rust
trait ShardStore: Send + Sync {
    fn store(&self, alias: &str, shard: &[u8]) -> Result<()>;
    fn load(&self, alias: &str) -> Result<Zeroizing<Vec<u8>>>;
    fn delete(&self, alias: &str) -> Result<()>;
}
```

### Backends — v1 (6 backends, ship these)
1. **macOS Keychain** — `security-framework` crate, `-A` flag, no GUI popups (~80 lines)
2. **Windows Credential Manager** — `windows` crate, DPAPI, headless (~100 lines)
3. **Linux Kernel Keyring** — `linux-keyutils` crate, `keyctl @u`, no root (~80 lines)
4. **Environment Variable** — `WORTHLESS_SHARD_B`, for Docker/CI (~20 lines, trivial)
5. **Mounted Secret** — `/run/secrets/worthless-shard-b`, for Docker/K8s (~20 lines, trivial)
6. **Encrypted File Fallback** — Argon2id + AES-256-GCM, last resort (~200 lines, spec addendum A2)

### Backends — deferred (post-launch, smallest user bases + hardest to implement)
7. **WSL2 Bridge** — `cmdkey.exe` interop. Hardest backend, fragile error handling, smallest user base. Falls back to kernel keyring.
8. **Linux Secret Service** — D-Bus async is painful, GNOME Keyring may not be unlocked. Falls back to kernel keyring.

### Linux two-layer strategy (spec addendum A3)
- Persistent: EncryptedFileStore (survives reboot)
- Runtime: KernelKeyringStore (cache, kernel memory, faster reads)
- At startup: if keyring empty, re-inject from encrypted file

### CI matrix
- Linux x86_64 (Ubuntu 22.04+)
- macOS (Apple Silicon + Intel)
- Windows (x86_64)

---

## Phase 3A: Sidecar Core (functionality)

**What:** Rust binary — Unix socket server, vault mode, proxy mode with upstream HTTPS
**Size:** ~800 lines
**Depends on:** Phase 1 + Phase 2
**Ships:** Working `worthless-sidecar` binary (without OS hardening)

### Startup sequence
1. Platform self-protection: PR_SET_DUMPABLE (Linux), PT_DENY_ATTACH (macOS), DACL (Windows) — **mandatory, ships in 3A**
2. Open Unix socket (Linux/macOS) or named pipe (Windows)
3. Wait for requests

### Per-request flow
1. Receive request over socket: `{mode, alias, shard_a, request?}`
2. Verify peer UID (SO_PEERCRED / LOCAL_PEERCRED — mandatory in 3A, PID pinning deferred to Phase 6)
3. Load Shard B from ShardStore (auto-detected)
4. Shamir reconstruct into mlock'd `Zeroizing<Vec<u8>>` + MADV_DONTDUMP
5. SHA-256 integrity check against stored hash (hash provided by Phase 4 enrollment; stub/skip until then)
6. **Vault mode:** return key bytes over socket, zero immediately
7. **Proxy mode:** format Authorization header, reqwest HTTPS call (connection pooled), zero after TLS write (~15µs), return response + usage
8. zeroize() + munlock()

### Streaming design decision (MUST resolve before starting 3A)
Proxy mode streaming (SSE) is the hardest engineering problem in the plan. Three-hop pipeline: provider → sidecar → proxy → client. Options:
- **Buffered** (simpler, worse UX): wait for full response, send all at once. Kills real-time token streaming.
- **Streaming** (harder, required for production): length-prefixed frames or chunked JSON over socket. Provider-specific SSE parsing duplicated in Rust for usage extraction.
Decision required: which approach for v1? Buffered is acceptable for initial integration; streaming can be added as a follow-up before launch.

### Supply chain gate
- `cargo audit` on all dependencies before first build
- `cargo vet` for security-critical crates: `zeroize`, `secrecy`, `security-framework`, `linux-keyutils`
- Pin exact versions with hash verification in `Cargo.lock`

### Rust dependencies
- Internal `worthless-crypto` crate (Phase 1)
- `zeroize` 1.8, `secrecy` 0.8 — memory safety
- `tokio` — async runtime
- `reqwest` with rustls — HTTPS client (proxy mode)
- `serde` + `serde_json` — socket protocol
- `libc` — prctl, ptrace, mlock, madvise
- Platform: `security-framework` (macOS), `windows` (Win), `linux-keyutils` (Linux)

### Socket protocol (JSON)
```
Request:  {mode: "vault"|"proxy", alias, shard_a, request?}
Response: {ok, key?} (vault) or {ok, status, headers, body, usage} (proxy)
Error:    {ok: false, error, message}
```

---

## Phase 3B: Sidecar Hardening

**What:** seccomp-BPF, Landlock, advanced sandboxing
**Size:** ~200 lines
**Depends on:** Phase 3A (working sidecar)
**Ships:** Hardened sidecar. Can ship in parallel with Phase 4.

### Deliverables
- seccomp-BPF allowlist — **build empirically with `strace`, not prescriptively.** The spec's allowlist is incomplete (missing `getrandom`, `futex`, `brk`, `getsockopt`, `ioctl`, `sigaltstack`, `rt_sigaction`, `statx`, `sendto/recvfrom` for DNS). One wrong syscall = SIGKILL.
- Landlock (Linux 6.7+) — read-only shard dir, CONNECT_TCP 443 only. Graceful degradation on older kernels.
- Verification: `strace` the sidecar under real load, diff against allowlist, iterate until clean

---

## Phase 4: Python Layer

**What:** Rewire CLI + proxy to use sidecar. Add vault mode. Migration tool.
**Size:** ~1000 lines modified, ~300 lines new. ~80-120 tests break (not 60 as originally estimated).
**Depends on:** Phase 3A (sidecar core must be running)
**Ships:** Updated CLI + proxy

### Sub-phases (each ends with green test suite)
- **4A:** Add new files (`sidecar_client.py`, `shard_split.py`, `platform_detect.py`, `cli/commands/get.py`) with their own tests. Zero breakage.
- **4B:** Wire into CLI commands (lock, unlock, get, up). Update CLI tests.
- **4C:** Rewire `proxy/app.py` to use sidecar IPC. Update proxy tests.
- **4D:** Remove Fernet code paths, update config.py, repository.py, schema.py. Fix remaining tests.
- **4E:** Migration command (`migrate.py`) with per-key atomic safety.

### New files
- `sidecar_client.py` — Unix socket / named pipe IPC client (can develop with mock sidecar before Phase 3A ships)
- `platform_detect.py` — OS + credential store detection (pure Python, no Rust dependency)
- `shard_split.py` — Python-side Shamir for enrollment
- `cli/commands/get.py` — vault mode (`worthless get <alias>`)
- `cli/commands/migrate.py` — Fernet → Shamir migration (spec Section 12)

### Migration safety (CRITICAL — review findings)
- Per-key atomic migration with rollback — NOT all-or-nothing
- Migration state machine in SQLite: `{not_started, in_progress, complete}` per key
- Fernet code path stays active until ALL keys show `complete`
- `fernet.key` deletion is a separate explicit step after full migration
- Each key reconstructed and re-split individually — never hold all keys in memory simultaneously
- Crash at any point → resume safely, partially-migrated keys still work via old path

### SHA-256 hash storage (cross-phase dependency)
- Phase 4 enrollment computes `SHA-256(original_key)` and stores it alongside Shard A metadata
- Phase 3A reconstruction verifies against stored hash (stub/skip until Phase 4 provides the hash)
- Track this as an explicit integration point between phases

### Modified files (major)
- `proxy/app.py` — steps 7-11 collapse to one sidecar IPC call
- `proxy/config.py` — remove fernet_key, add sidecar_socket_path
- `cli/bootstrap.py` — remove Fernet key generation, add sidecar lifecycle
- `cli/commands/lock.py` — XOR → Shamir, Shard B → credential store, Shard C → printed
- `cli/commands/unlock.py` — reconstruction via sidecar vault mode
- `cli/commands/up.py` — spawn sidecar alongside proxy
- `cli/commands/status.py` — show security tier + sidecar health
- `cli/process.py` — add spawn_sidecar(), ensure_sidecar()
- `storage/repository.py` — gut Fernet logic, keep enrollment/spend CRUD
- `crypto/splitter.py` — XOR → Shamir (enrollment only, reconstruct moves to Rust)
- `crypto/types.py` — SplitResult changes to (shard_a, shard_b, shard_c)

### Preserved features (spec addendum A6)
- `worthless unlock` — via sidecar vault mode
- Decoy key system (make_decoy, decoy_hash, WRTLS prefix)
- `worthless lock --env .env` batch auto-scan — wraps enroll per key, prints all backups at end
- Port 8787 (not 9191 as spec says)
- MCP server integration

### Removed
- `cryptography` dependency (Fernet)
- Fernet key generation, fernet.key file
- Fernet encrypt/decrypt in repository
- httpx upstream HTTPS calls from proxy (sidecar makes the upstream call in proxy mode; proxy still handles routing, metering, spend caps — it's still in the request path, it just never touches the API key or talks to the LLM provider directly)

### Clarification: who reads which shard
- **Shard A** is always read by the **Python layer** (proxy or CLI) from the filesystem, then passed to the sidecar over the socket. The sidecar never touches the filesystem for Shard A.
- **Shard B** is always read by the **Rust sidecar** from the platform credential store. Python never touches Shard B.
- This separation is intentional: neither process ever holds both shards. The socket protocol makes this explicit — every request includes `shard_a` as a field.

### Clarification: proxy vs vault mode data flow
- **Proxy mode:** User's code → Python proxy on :8787 (routing, rules, metering) → sidecar via UDS (reconstruct, upstream HTTPS call, zero) → proxy (relay response, record spend) → user's code. Proxy is in the path but never sees the key.
- **Vault mode:** CLI (`worthless get`) → sidecar via UDS directly (reconstruct, return key, zero) → CLI prints key to stdout → user's code makes its own upstream call. The Python proxy on :8787 is NOT in this path at all.

### Clarification: vault and proxy mode share the same sidecar socket
Both modes connect to the same Rust sidecar over the same Unix socket. The difference is the entry point on the Python side:
- **Proxy mode:** FastAPI app in `proxy/app.py` uses `sidecar_client.py` to talk to the sidecar
- **Vault mode:** CLI command in `cli/commands/get.py` uses `sidecar_client.py` to talk to the sidecar directly — no FastAPI, no proxy process needed
- `sidecar_client.py` is the shared module both use. The `mode` field in the socket request (`"vault"` vs `"proxy"`) tells the sidecar which flow to execute.

---

## Phase 5: Distribution

**What:** Package Rust sidecar into PyPI wheels via maturin
**Size:** Build config + CI workflow
**Depends on:** Phase 3 + 4 working together
**Ships:** `pip install worthless` delivers everything

### Deliverables
- `pyproject.toml` → maturin build backend with `bindings = "bin"`
- Platform wheels: manylinux2014 x86_64/aarch64, macOS universal2, Windows x86_64
- GitHub Actions workflow via `PyO3/maturin-action`
- Sidecar binary lands in virtualenv `bin/` on install (~2-5MB per platform)
- Smoke test: `pip install worthless && worthless enroll test-key && worthless get test-key`

---

## Phase 6: Hardening

**What:** Security documentation, optional hardening, audit prep
**Depends on:** Everything shipped
**Ships:** Docs + hardening options

### Deliverables
- Security tiers in SECURITY_POSTURE.md (spec addendum A1)
- Updated CLAUDE.md architectural invariants (Shamir replaces XOR, Fernet eliminated)
- Updated SECURITY_RULES.md (new rules for Shamir, credential stores, sidecar hardening)
- `worthless install --hardened` — separate Unix user for sidecar (optional)
- Verifiable builds (Sigstore/cosign signing)
- Security audit of sidecar + GF(256) implementation
- Updated .planning/ROADMAP.md

---

## Phase Dependencies

```
Phase 1 (Shamir core)
   ↓
Phase 2 (Shard stores)      ← can overlap with Phase 1 (different modules)
   ↓
Phase 3A (Sidecar core)     ← needs Phase 1 + 2
   ↓
Phase 4 (Python layer)      ← needs Phase 3A; sub-phases 4A-4B can start with mock sidecar
   │
Phase 3B (Sidecar hardening) ← runs in parallel with Phase 4, needs Phase 3A
   ↓
Phase 5 (Distribution)      ← needs Phase 3A + 4 integrated
   ↓
Phase 6 (Hardening)         ← post-launch
```

### Parallel tracks (maximizes velocity)
```
Track A (Rust):    Phase 1 → Phase 2 → Phase 3A → Phase 3B
Track B (Python):  sidecar_client.py (mock) → shard_split.py → Phase 4A-4E
Track C (Infra):   maturin spike (prototype) → Phase 5
```
Track B can start immediately — `sidecar_client.py` develops against a mock socket server. 70% of Python work proceeds independently of Rust.

### Prerequisite spike (before Phase 3A)
Maturin prototype: minimal Cargo.toml + hello-world binary + pyproject.toml with maturin backend. Verify:
- `uv pip install -e .` places binary in `$VIRTUAL_ENV/bin/`
- GitHub Actions `maturin-action` builds wheels for all 4 targets
- Existing Python tests still run with maturin as build backend

---

## Linear Ticket Impact

### Archive (obsoleted by Fernet elimination)
- WOR-134 — Fernet key plaintext in memory
- WOR-135 — Docker same-volume collapse
- WOR-138 — PoC Security Fixes epic (parent of 134+135)

### Rewrite (concept survives, implementation changes)
- WOR-60 + subtasks (WOR-61–66) — Rust sidecar aligns with Phase 3, but XOR → Shamir + vault mode
- WOR-136 — Docker e2e test must exercise multi-container sidecar flow

### Keep (unaffected)
- WOR-137 — SAST nightly
- WOR-15 — Infrastructure hardening (aligns with Phase 6)
- 38 other tickets — proxy perf, CI, test hygiene, docs

### Create (new epics under WOR-143)
- Epic per phase (1-6) with subtasks

---

## Research Basis

All decisions traced to research documents in `docs/research/`:

| Decision | Source |
|----------|--------|
| Shamir 2-of-3 over GF(256) | shamir-sidecar-architecture.md |
| Roll own impl, not blahaj | shamir-sidecar-verification.md, spec-vs-research-gaps.md |
| Platform credential stores | cross-platform-shard-storage/SYNTHESIS.md |
| PR_SET_DUMPABLE as trust boundary | cross-platform-shard-storage/process-isolation-no-sudo.md |
| Encrypted file fallback | cross-platform-shard-storage/fallback-encrypted-shard.md |
| Docker multi-container | cross-platform-shard-storage/docker-container-injection.md |
| Security tiers (honest claims) | cross-platform-shard-storage/SYNTHESIS.md |
| SHA-256 integrity check | spec-analysis/spec-addendum.md (A4) |
| UDS peer auth | spec-analysis/spec-addendum.md (A5) |
| Preserved features | spec-analysis/spec-codebase-impact.md |
