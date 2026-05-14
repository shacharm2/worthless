# Worthless Sidecar Architecture — Technical Specification

## Status: Approved Design — Ready to Build
## Date: 2026-04-05
## Context: This document is the build spec for the Worthless sidecar reconstruction service.

---

## 1. What We're Building

A Rust sidecar process that reconstructs API keys from Shamir shares stored across separate OS trust domains. The sidecar supports two modes:

- **Vault mode** (`worthless get <alias>`): Returns the reconstructed key to the caller. Works with any API — LLM, Stripe, Twilio, anything.
- **Proxy mode** (`worthless start`): Makes the upstream HTTP call itself, enforces spend caps, returns only the response. LLM-specific.

Same sidecar, same shards, same reconstruction logic. The mode is per-request, not per-install.

---

## 2. Why We're Building It

The current architecture stores the Fernet encryption key as a plaintext file on the same filesystem as both XOR shards. A single file-read attack (path traversal, backup exfiltration, git leak, SSRF on a remote server) reconstructs every enrolled API key. The split-key security claim is cosmetic — we moved the problem to a master key, we didn't solve it.

This architecture eliminates the Fernet key entirely. Shamir shares live in separate OS trust domains. No single breach reveals enough to reconstruct any key. The API key never exists at rest — it is reconstructed on demand, used, and zeroed.

---

## 3. Architecture Overview

```
User's code
    │
    ├──── vault mode ──── worthless get <alias> ──┐
    │                                              │
    ├──── proxy mode ──── localhost:9191 ──┐       │
    │                                      │       │
    │                              Python Proxy    │
    │                              (metering,      │
    │                               spend caps,    │
    │                               no key access) │
    │                                      │       │
    │                                      ▼       ▼
    │                              ┌──────────────────┐
    │                              │   Rust Sidecar    │
    │                              │                    │
    │                              │ 1. Load Shard B    │
    │                              │    (platform store)│
    │                              │ 2. Receive Shard A │
    │                              │    (from caller)   │
    │                              │ 3. Shamir 2-of-3   │
    │                              │    reconstruct     │
    │                              │ 4. mlock + zeroize │
    │                              │                    │
    │                              │ Vault: return key  │
    │                              │ Proxy: make call,  │
    │                              │   return response  │
    │                              └──────────────────┘
    │                                      │
    │                              (proxy mode only)
    │                                      │
    │                                      ▼
    │                              LLM Provider
    │                              (OpenAI, Anthropic)
    │
    ▼
  Result
```

---

## 4. Key Splitting — Shamir 2-of-3 over GF(256)

At enrollment, the API key is split into three Shamir shares. Any two reconstruct the key. One share alone reveals zero information (information-theoretic security).

### Enrollment flow (`worthless enroll <alias>`)

```
Input: API key string (e.g., "<provider-api-key>")

1. Split key into 3 Shamir shares (2-of-3 threshold, GF(256))
2. Store Shard A → filesystem (platform-specific path, chmod 600)
3. Store Shard B → OS credential store (auto-detected, see Section 6)
4. Display Shard C → terminal, one time: "BACKUP THIS: <mnemonic phrase>"
5. Zero the original key from memory immediately
```

### Shard storage locations

```
Shard A (filesystem):
  macOS:   ~/.config/worthless/shards/<alias>/a  (chmod 600)
  Linux:   ~/.config/worthless/shards/<alias>/a  (chmod 600)
  Windows: %APPDATA%\worthless\shards\<alias>\a
  Docker:  /data/shards/<alias>/a  (on dedicated volume)

Shard B (OS credential store — see Section 6 for full details):
  macOS:   Keychain
  Windows: Credential Manager (DPAPI)
  Linux:   Kernel keyring (keyctl @u)
  WSL2:    Windows Credential Manager via wsl.exe bridge
  Docker:  Compose secret / K8s secret / platform env var

Shard C (user backup):
  Printed once at enrollment as BIP39-style mnemonic phrase
  User's responsibility to store (1Password, printed, etc.)
  Never stored by Worthless
```

### Shamir implementation

Use the `blahaj` Rust crate (MIT/Apache-2.0) — maintained fork of `sharks` that fixes the coefficient bias vulnerability (RUSTSEC-2024-0398). Operates over GF(256) on raw byte arrays.

Performance: ~20-50µs to split, ~10-25µs to reconstruct a 50-byte key. Negligible.

### Recovery scenarios

```
Lost filesystem (Shard A)?  → Shard B (credential store) + Shard C (backup) → reconstruct
Lost credential store (Shard B)? → Shard A (filesystem) + Shard C (backup) → reconstruct
Lost backup (Shard C)?  → Shard A + Shard B still work (lose disaster recovery only)
Lost two shards?  → Key is gone. Re-enroll with the provider.
```

---

## 5. Sidecar Binary — Rust

### Startup sequence

```
1. Call platform-specific self-protection (see Section 7)
2. Apply seccomp-BPF filter (Linux) — allowlist: socket, connect, read, write,
   close, epoll_*, mmap, mprotect, mlock, clock_gettime, exit_group
   Block after init: open/openat, ptrace, fork, exec
3. Apply Landlock policy (Linux 6.7+) — read-only on shard directory,
   CONNECT_TCP on port 443 only
4. Open Unix domain socket (Linux/macOS) or named pipe (Windows)
5. Wait for requests
```

### Per-request flow

```
1. Receive request over Unix socket / named pipe:
   {
     "mode": "vault" | "proxy",
     "alias": "openai-prod",
     "shard_a": "<base64 bytes>",          // caller sends Shard A
     "request": { ... }                     // proxy mode only: HTTP request to forward
   }

2. Load Shard B from platform credential store (auto-detected)

3. Shamir reconstruct (2-of-3) into mlock'd Zeroizing<Vec<u8>> buffer
   - mlock() the buffer page — prevent swap
   - madvise(MADV_DONTDUMP) — exclude from core dumps

4a. VAULT MODE:
    - Return key bytes to caller over socket
    - Caller uses the key in their own process
    - Sidecar zeros the buffer immediately after send

4b. PROXY MODE:
    - Format: Authorization: Bearer <key>
    - Make upstream HTTPS call (reqwest with connection pooling)
    - Zero the key buffer immediately after TLS write (~15µs key lifetime)
    - Stream response back to caller over socket
    - Meter token usage from response headers (for spend cap enforcement)

5. zeroize() the buffer — volatile write + compiler fence
6. munlock() the page
```

### Key lifetime

```
Vault mode:  Key exists in sidecar memory from reconstruction until socket send.
             Caller holds the key for the duration of their API call.
             Same trust model as ssh-agent, 1Password CLI, any credential helper.

Proxy mode:  Key exists in sidecar memory for ~15µs (steady state with connection pooling).
             Breakdown:
               XOR reconstruction:        ~0.5µs
               Format Authorization header: ~2µs
               Write to TLS send buffer:   ~5-10µs
               Volatile zero + fence:      ~0.1µs
```

### Memory safety requirements

```
- All key material in Zeroizing<Vec<u8>> (auto-zeros on drop, even in panic unwind)
- Heap allocation only (Box<[u8]>) — avoid stack copies
- mlock() before writing key material — prevent swap exposure
- madvise(MADV_DONTDUMP) — prevent core dump exposure
- zeroize uses core::ptr::write_volatile + compiler_fence(SeqCst)
- The complete API key NEVER enters Python's address space
- Rust crates: zeroize (v1.8.2), secrecy (for Secret<T> wrapper)
```

### Dependencies (Rust)

```
blahaj          — Shamir splitting over GF(256), MIT/Apache-2.0
zeroize         — Deterministic memory zeroing, Apache-2.0/MIT
secrecy         — Secret<T> wrapper with auto-zeroize, Apache-2.0/MIT
tokio           — Async runtime for socket + HTTP, MIT
reqwest         — HTTPS client (proxy mode), MIT/Apache-2.0
serde/serde_json — Socket protocol serialization, MIT/Apache-2.0

Platform-specific:
  security-framework  — macOS Keychain, MIT/Apache-2.0
  windows             — Windows Credential Manager (DPAPI), MIT/Apache-2.0
  linux-keyutils      — Linux kernel keyring (keyctl), MIT/Apache-2.0
```

---

## 6. Shard Store Abstraction — Cross-Platform

### Trait definition

```rust
trait ShardStore: Send + Sync {
    fn store(&self, alias: &str, shard: &[u8]) -> Result<()>;
    fn load(&self, alias: &str) -> Result<Zeroizing<Vec<u8>>>;
    fn delete(&self, alias: &str) -> Result<()>;
}
```

### Backend auto-detection waterfall

```rust
fn get_shard_store() -> Box<dyn ShardStore> {
    #[cfg(target_os = "macos")]
    if keychain_available() { return Box::new(KeychainStore::new()); }

    #[cfg(target_os = "windows")]
    if credential_manager_available() { return Box::new(CredentialManagerStore::new()); }

    #[cfg(target_os = "linux")]
    if in_wsl() && win_credman_available() { return Box::new(WslBridgeStore::new()); }

    #[cfg(target_os = "linux")]
    if secret_service_available() { return Box::new(SecretServiceStore::new()); }

    #[cfg(target_os = "linux")]
    if keyctl_available() { return Box::new(KernelKeyringStore::new()); }

    if let Ok(_) = std::env::var("WORTHLESS_SHARD_B") { return Box::new(EnvVarStore::new()); }

    // mounted secret (Docker/K8s)
    if Path::new("/run/secrets/worthless-shard-b").exists() { return Box::new(MountedSecretStore::new()); }

    // last resort — separate-path file, different directory from Shard A
    Box::new(SeparatePathFileStore::new())
}
```

### Platform details

#### macOS — Keychain

```
Crate: security-framework
API: SecItemAdd / SecItemCopyMatching
Key: service="worthless", account=<alias>
Flags: use -A (allow all apps) at enrollment — no popup on subsequent reads
Secure Enclave backs at-rest encryption on Apple Silicon
No sudo, no codesigning required for -A path
Headless: yes (no GUI popup with -A flag)
```

#### Windows — Credential Manager (DPAPI)

```
Crate: windows (windows::Security::Credentials)
API: CredWriteW / CredReadW
Target: "worthless/<alias>"
DPAPI encrypts at rest using user's login credential
Accessible only from the same user session
No admin required
Headless: yes
```

#### Linux Desktop — Secret Service (GNOME Keyring / KDE Wallet)

```
Crate: secret-service (via D-Bus)
API: org.freedesktop.Secret.Service D-Bus interface
Collection: "worthless", item label: <alias>
Same UID can access — partial trust domain separation
Falls through to kernel keyring if D-Bus unavailable
No sudo, no GUI required (D-Bus call)
Headless: only if D-Bus session bus is available
```

#### Linux Headless — Kernel Keyring (keyctl)

```
Crate: linux-keyutils
API: keyctl(KEYCTL_ADD, "user", "worthless:<alias>", <shard_bytes>, @u)
Keyring: @u (per-user, kernel-managed)
Lives in kernel memory, NOT on the filesystem
Available since Linux 2.6 — every modern distro
Same UID can access — but separate access path from filesystem
Combined with PR_SET_DUMPABLE(0), sidecar memory is its own trust domain
No sudo, no packages, no daemon
Headless: yes
Note: does not persist across reboots — re-inject at startup from backup or orchestrator
```

#### WSL2 — Bridge to Windows Credential Manager

```
Implementation: std::process::Command("wsl.exe", "cmdkey", "/add:worthless-<alias>", ...)
Stores Shard B in the Windows host's Credential Manager
Shard A is in Linux filesystem, Shard B is on a different operating system
Genuinely separate trust domains — Linux file-read can't reach Windows credentials
Fallback: kernel keyring (keyctl) if bridge unavailable
No sudo
Headless: yes
```

#### Docker — Orchestrator Secrets

```
Compose secret:  /run/secrets/worthless-shard-b (tmpfs, never on disk)
K8s secret:      mounted as tmpfs volume
Platform env:    WORTHLESS_SHARD_B (Railway, Render, Fly.io)
CRITICAL: Shard B NEVER goes on the data volume
Docker Compose runs sidecar as a separate container with its own volume
No sudo (container is already isolated)
```

#### Fallback — Separate-Path File

```
Path: ~/.local/share/worthless/shard_b/<alias> (chmod 600)
Different directory tree from Shard A (~/.config/worthless/shards/)
Weakest option — same UID, same filesystem
Still better than current (no master Fernet key, shares are individually useless)
Used only when no credential store is available (bare containers, CI, exotic environments)
```

### None of these require sudo or elevated privileges.

---

## 7. Process Self-Protection — Per Platform

The sidecar protects its own memory from same-machine attacks at startup with one platform-specific syscall.

### Linux (all variants including WSL2)

```rust
// Block ptrace and /proc/<pid>/mem reads from same-UID processes
// Available since Linux 2.3.20, no sudo
unsafe { libc::prctl(libc::PR_SET_DUMPABLE, 0) };

// Combined with Yama LSM level 1 (default on Ubuntu, Debian, Fedora):
// - Only a direct parent can ptrace
// - /proc/<pid>/mem reads blocked for non-parent same-UID processes
// - Core dumps disabled
// Result: sidecar memory is a real trust domain without root
```

### macOS

```rust
// Block debugger attachment from any process, including same-user
// Available since OS X 10.5, no sudo
unsafe { libc::ptrace(libc::PT_DENY_ATTACH, 0, std::ptr::null_mut(), 0) };

// Combined with Keychain access control:
// Sidecar memory is protected from debugging
// Shard B is protected by Keychain ACLs + Secure Enclave (Apple Silicon)
```

### Windows

```rust
// SetProcessMitigationPolicy for dynamic code injection prevention
// IsDebuggerPresent() check at startup
// Less robust than Linux/macOS kernel-level protection
// Primary isolation comes from DPAPI encryption of Shard B — different access path
```

### Docker

```
No process self-protection needed — the container boundary IS the isolation.
Sidecar runs in a separate container from the proxy.
Separate volumes, separate network namespace, separate PID namespace.
```

---

## 8. Socket Protocol

Communication between Python (proxy/CLI) and the Rust sidecar uses Unix domain sockets (Linux/macOS) or named pipes (Windows).

### Socket path

```
Linux/macOS: /tmp/worthless-<uid>.sock  (or XDG_RUNTIME_DIR if available)
Windows:     \\.\pipe\worthless-<username>
Docker:      /var/run/worthless/sidecar.sock  (shared volume between containers)
```

### Request format (JSON over socket)

```json
// Vault mode — return the key
{
  "mode": "vault",
  "alias": "stripe-prod",
  "shard_a": "base64-encoded-shard-a-bytes"
}

// Proxy mode — make the upstream call
{
  "mode": "proxy",
  "alias": "openai-prod",
  "shard_a": "base64-encoded-shard-a-bytes",
  "request": {
    "method": "POST",
    "url": "https://api.openai.com/v1/chat/completions",
    "headers": { "Content-Type": "application/json" },
    "body": "{ ... }"
  }
}
```

### Response format

```json
// Vault mode response
{
  "ok": true,
  "key": "<provider-api-key>"
}

// Proxy mode response
{
  "ok": true,
  "status": 200,
  "headers": { ... },
  "body": "{ ... }",
  "usage": { "input_tokens": 150, "output_tokens": 42, "cost_usd": 0.003 }
}

// Error response (both modes)
{
  "ok": false,
  "error": "spend_cap_exceeded",
  "message": "Daily cap of $10.00 reached. Remaining: $0.00"
}
```

### Performance

```
Unix socket round-trip (small message): 2-5µs
Named pipe round-trip (Windows):        ~10µs
TCP localhost (for comparison):         ~15µs

For a ~1KB request + 50-byte shard → sidecar → response:
Total IPC overhead: 15-60µs  (~0.01% of a 200ms LLM API call)
```

---

## 9. Python Layer

### CLI commands

```bash
# Enrollment — one-time per key
worthless enroll <alias>
# Interactive: paste key, choose daily cap (optional)
# Splits into 3 Shamir shares, stores A + B, prints C
# Spawns sidecar if not running

# Vault mode — get a key for any API
worthless get <alias>
# Prints reconstructed key to stdout
# Usage: export STRIPE_KEY=$(worthless get stripe-prod)
# Usage: curl -H "Authorization: Bearer $(worthless get openai)" https://api.openai.com/...

# Proxy mode — LLM proxy with spend caps
worthless start
# Starts Python proxy on localhost:9191
# Spawns Rust sidecar as subprocess (invisible to user)
# User sets OPENAI_BASE_URL=http://localhost:9191/v1

# Status
worthless status
# Shows enrolled keys, spend status, sidecar health, platform backend detected

# Key management
worthless keys list                    # List enrolled aliases
worthless keys rotate <alias>         # Rotate: paste new key, re-split, same shards locations
worthless keys revoke <alias>         # Delete all shards for this alias
worthless scan                        # Scan files for exposed API keys (pre-commit hook)
```

### Sidecar lifecycle management

```python
# worthless start / worthless get automatically manage the sidecar:

def ensure_sidecar():
    """Spawn sidecar if not already running."""
    sock_path = get_socket_path()
    if socket_responsive(sock_path):
        return  # already running

    sidecar_bin = shutil.which("worthless-sidecar")  # installed via maturin wheel
    proc = subprocess.Popen(
        [sidecar_bin, "--socket", sock_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    wait_for_socket(sock_path, timeout=5.0)
    atexit.register(lambda: proc.terminate())
```

The user never starts the sidecar manually. It's spawned on demand and shut down when the parent exits.

### Proxy mode integration

```python
# The Python proxy (FastAPI) handles:
# - HTTP routing (localhost:9191 → sidecar → upstream)
# - Spend cap enforcement (Redis counters, checked BEFORE forwarding to sidecar)
# - Metering (extract usage from sidecar response, update counters)
# - Model allowlists, rate limits, time windows
#
# The Python proxy does NOT handle:
# - Key material of any kind (never sees Shard A, Shard B, or the reconstructed key)
# - Upstream HTTPS calls (sidecar does this in proxy mode)
# - Cryptographic operations (all in Rust sidecar)
```

---

## 10. Distribution

### Package structure

```
worthless/                      # PyPI package
├── worthless/                  # Python source
│   ├── __init__.py
│   ├── cli.py                  # CLI: enroll, get, start, status, keys, scan
│   ├── proxy.py                # FastAPI proxy (localhost:9191)
│   ├── sidecar_client.py       # Unix socket / named pipe client
│   ├── shard_split.py          # Python-side Shamir split (enrollment only)
│   └── platform_detect.py      # Detect OS, credential store availability
├── src/                        # Rust source (sidecar binary)
│   ├── main.rs                 # Socket server, request dispatch
│   ├── shard_store/            # Platform abstraction (Section 6)
│   │   ├── mod.rs
│   │   ├── keychain.rs         # macOS
│   │   ├── credential_manager.rs # Windows
│   │   ├── kernel_keyring.rs   # Linux
│   │   ├── wsl_bridge.rs       # WSL2
│   │   ├── env_var.rs          # Docker/CI
│   │   └── file_fallback.rs    # Last resort
│   ├── reconstruct.rs          # Shamir reconstruction + mlock + zeroize
│   ├── protect.rs              # PR_SET_DUMPABLE / PT_DENY_ATTACH per platform
│   ├── sandbox.rs              # seccomp-BPF + Landlock (Linux)
│   └── upstream.rs             # HTTPS client for proxy mode (reqwest)
├── Cargo.toml
├── pyproject.toml              # maturin build config
└── .github/workflows/
    └── wheels.yml              # Build platform-specific wheels via maturin-action
```

### Build and install

```bash
# User installs:
pip install worthless

# This installs:
# 1. Python package (CLI + proxy)
# 2. Rust sidecar binary (worthless-sidecar) into virtualenv bin/
#    Platform-specific: manylinux2014 x86_64/aarch64, macOS universal2, Windows x86_64

# Built using maturin with bin bindings (same approach as ruff, pydantic-core)
# Sidecar binary: ~2-5MB per platform
```

---

## 11. Security Properties

### What this architecture guarantees

```
1. NO KEY AT REST — the API key never exists as a complete value in any file,
   database, encrypted blob, or persistent storage. Individual Shamir shares
   are information-theoretically useless (one share reveals zero bits about the key).

2. TRUST DOMAIN SEPARATION — Shard A (filesystem) and Shard B (OS credential store)
   live in separate access domains. A file-read attack gets Shard A but cannot reach
   the credential store. A credential store breach gets Shard B but not the filesystem.
   An attacker needs two simultaneous breaches of independent OS security boundaries.

3. MINIMAL KEY LIFETIME — in proxy mode, the reconstructed key exists for ~15µs
   in mlock'd, non-dumpable Rust memory inside an isolated process with no inbound network.
   In vault mode, the key exists in the caller's process for the duration of their API call
   (same model as ssh-agent, 1Password CLI, aws-vault).

4. FERNET KEY ELIMINATED — no master encryption key exists. The bootstrap recursion
   problem ("who protects the key that protects the key?") is resolved by making
   individual shares useless rather than encrypting them.

5. SIDECAR MEMORY ISOLATION — on Linux, prctl(PR_SET_DUMPABLE, 0) blocks same-UID
   ptrace and /proc/<pid>/mem reads. On macOS, PT_DENY_ATTACH blocks debugger attachment.
   A compromised proxy process cannot read sidecar memory.

6. PROXY NEVER TOUCHES SECRETS — in proxy mode, the Python process handles routing,
   metering, and spend caps. It never sees Shard B, never sees the reconstructed key,
   and never makes the upstream HTTPS call. All secret operations are in Rust.
```

### What this architecture does NOT protect against

```
1. Full machine compromise (root/admin access) — root can bypass PR_SET_DUMPABLE,
   read any process memory, read any file. Same boundary as 1Password, ssh-agent,
   and every credential helper. Out of scope.

2. Vault mode key exposure — in vault mode, the key is returned to the caller's process.
   If that process is compromised, the attacker gets the key for the duration of use.
   This is the same trust model as every credential helper in existence.

3. Two simultaneous trust domain breaches — an attacker who compromises both the
   filesystem AND the credential store can reconstruct the key. This requires two
   independent exploits targeting different OS subsystems.

4. Insider with physical access to hardware — can extract both shards given enough
   time and access. Same boundary as any software-only security system.
```

### Honest positioning

```
"Worthless makes API keys worthless to steal. Your key is split into pieces stored
in separate security domains on your OS. No piece alone reveals anything. The key
only exists for microseconds when you actually need it.

A stolen file gets you nothing. A breached credential store gets you nothing.
You need both, simultaneously, to reconstruct a key — and even then, the
spend cap fires before the key is used."
```

---

## 12. Fernet Elimination — Migration Path

### For new users

No Fernet key is ever created. Enrollment goes straight to Shamir 2-of-3 with platform credential store.

### For existing users (have ~/.worthless/fernet.key)

```bash
worthless migrate
# 1. Read existing fernet.key
# 2. Decrypt all shard_b values from SQLite
# 3. For each enrolled key: combine shard_a + decrypted shard_b → original API key
# 4. Re-split each key into 3 Shamir shares
# 5. Store new Shard A (filesystem), Shard B (credential store), print Shard C (backup)
# 6. Delete fernet.key, delete old SQLite entries
# 7. Zero all intermediate values
```

Auto-detect on startup: if `fernet.key` exists, warn loudly and offer migration. Don't refuse to start — that breaks existing users. But make it clear the old mode is deprecated.

---

## 13. Build Order

### Phase 1: Shard Store Abstraction (Rust crate, ~500 lines)
- ShardStore trait + all platform backends
- Auto-detection waterfall
- Unit tests per platform (CI matrix: Linux, macOS, Windows)

### Phase 2: Sidecar Binary (Rust, ~1500 lines)
- Unix socket / named pipe server
- Shamir reconstruction with mlock + zeroize
- Process self-protection (PR_SET_DUMPABLE, PT_DENY_ATTACH)
- Vault mode (return key over socket)
- Proxy mode (upstream HTTPS call via reqwest)
- seccomp-BPF + Landlock (Linux)

### Phase 3: Python Layer
- CLI commands: enroll, get, start, status, keys, scan
- Sidecar lifecycle management (spawn, health check, shutdown)
- Proxy (FastAPI on localhost:9191) — routing, metering, spend caps
- Platform detection helper
- Migration from Fernet-based storage

### Phase 4: Distribution
- Maturin wheel build (pyproject.toml, Cargo.toml)
- GitHub Actions workflow for cross-platform wheels
- `pip install worthless` delivers everything

### Phase 5: Hardening (post-launch)
- `worthless install --hardened` — separate Unix user for sidecar
- Verifiable builds (Sigstore/cosign signing)
- Security audit of sidecar binary
- SECURITY.md with threat model documentation

---

## 14. What This Changes From Current Architecture

```
ELIMINATED:
- fernet.key file (the entire bootstrap problem)
- Fernet encryption of shard_b (replaced by trust domain separation)
- cryptography Python dependency (for Fernet — may still need for mTLS)
- Single-process architecture (proxy + reconstruction in one Python process)

ADDED:
- Rust sidecar binary (~2-5MB, ships in PyPI wheel)
- Shamir 2-of-3 splitting (replaces XOR 2-of-2)
- Platform credential store integration (5 backends)
- Unix socket IPC between Python and Rust
- Vault mode (generic key retrieval for any API)

UNCHANGED:
- User-facing CLI commands (enroll, start, status, scan)
- Proxy endpoint (localhost:9191)
- Spend cap enforcement model
- OPENAI_BASE_URL / ANTHROPIC_BASE_URL swap
- 90-second setup target
- MCP server integration
```

---

## 15. Open Decisions (Resolve During Build)

1. **Shard C format**: BIP39 mnemonic (12 words) vs base64 blob vs QR code? Mnemonic is friendliest for backup but adds a word list dependency.

2. **Kernel keyring persistence**: keyctl @u doesn't survive reboot. On reboot, does worthless auto-re-inject Shard B from Shard A + Shard C (requires user to provide backup), or do we use @us (user-session, also non-persistent)? Alternative: use a persistent keyring type if available.

3. **Proxy mode streaming**: For LLM streaming responses (SSE), does the sidecar stream through the Unix socket, or buffer the full response? Streaming is better UX but adds complexity to the socket protocol.

4. **Spend cap in vault mode**: Vault mode returns the raw key — should it check spend caps first? For LLM keys with caps, yes. For Stripe keys, no. Per-alias configuration: `worthless enroll stripe-prod --no-cap`.

5. **Connection pooling**: Sidecar should pool upstream HTTPS connections (reqwest ConnectionPool). The ~15µs key lifetime assumes warm connections. Cold first request adds TLS handshake time (~30-150ms) during which the key is alive.

6. **Concurrent requests**: Sidecar handles multiple simultaneous requests. Each gets its own mlock'd buffer. What's the max concurrent reconstructions before mlock limits are hit? Default RLIMIT_MEMLOCK is 64KB on most Linux — each 50-byte key uses one 4KB page, so ~16 concurrent. May need to raise or use a buffer pool.
