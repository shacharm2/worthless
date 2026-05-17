# Cross-Platform Shard Storage: Synthesis & Recommendations

## Executive Summary

Five research tracks confirm: real trust-domain separation is achievable on every platform Worthless targets. macOS Keychain, Windows DPAPI, and Docker multi-container give 3 real trust domains. Linux kernel keyring + `PR_SET_DUMPABLE(0)` gives 2.5. Headless fallback with encrypted file gives an honest 2. The sidecar hardens itself without sudo on all platforms. Auto-detection picks the best available storage. No user intervention during operation.

## The Architecture

Shamir 2-of-3 over GF(256). Any 2 shards reconstruct the key. One shard alone is random noise.

**Enrollment (once):**
1. `worthless enroll` — user provides API key
2. Shamir split → 3 shards
3. Shard A → filesystem (`~/.config/worthless/shards/`)
4. Shard B → platform credential store (auto-detected)
5. Shard C → printed to terminal as backup, user stores offline
6. Original key zeroed from memory

**Per-request:**
1. Proxy loads Shard A from disk
2. Proxy sends Shard A + request payload to Rust sidecar over Unix socket
3. Sidecar retrieves Shard B from credential store
4. Sidecar reconstructs key in mlock'd memory (~15μs warm path)
5. Sidecar makes upstream HTTPS call, zeros key immediately after write
6. Response streams back through sidecar → proxy → client

**Recovery:** Lost laptop? Shard C (backup) + either A or B from any backup. Lost backup shard? A + B still work. Single shard compromised? Worthless alone.

## Platform Decision Matrix

| Platform | Shard A | Shard B | Sidecar Hardening | Trust Domains | Auto-detect |
|---|---|---|---|---|---|
| macOS | `~/.config/worthless/` | Keychain (`security` CLI, `-A` flag) | PT_DENY_ATTACH + mlock | **3** (Tier 1) | `sys.platform == 'darwin'` |
| Windows | `%APPDATA%\worthless\` | Credential Manager (DPAPI) | Restrictive DACL + VirtualLock | **2.5** (Tier 2) | `sys.platform == 'win32'` |
| Linux desktop | `~/.config/worthless/` | Secret Service (libsecret) or kernel keyring (`@u`) | PR_SET_DUMPABLE(0) + mlock + seccomp | **2.5** (Tier 2) | `$DISPLAY` set + `secret-tool --version` or `keyctl` |
| Linux headless | `~/.config/worthless/` | Kernel keyring (`@u`) if available, else encrypted file (Argon2id) | Same | **2–2.5** (Tier 2–3) | No `$DISPLAY`, check `keyctl` |
| Docker Compose | Volume on proxy container | Compose secret on sidecar container | Container boundary + seccomp | **3** (Tier 1) | `/.dockerenv` |
| Kubernetes | PVC or ConfigMap | K8s Secret (CSI + KMS) | Pod security context | **3** (Tier 1) | `KUBERNETES_SERVICE_HOST` |
| Railway/Render/Fly | Ephemeral FS | Platform secret (env var) | Platform sandbox | **2** (Tier 3) | `RAILWAY_*`, `RENDER_*`, `FLY_*` |
| WSL2 | `~/.config/worthless/` | Windows Credential Manager via `cmdkey.exe` | prctl + mlock | **2.5** (Tier 2) | `WSL_DISTRO_NAME` |
| CI runners | Temp dir | CI secret (env var) | None beyond CI sandbox | **2** (Tier 3) | `CI=true` |

## Shard B: Per-Platform Strategy

### macOS
- **Mechanism:** `security add-generic-password -A -s com.worthless -a <key-id> -w <shard>` at enrollment. Read via `security find-generic-password -s com.worthless -a <key-id> -w`.
- **Trust quality:** Real. Keychain encrypted at rest via Secure Enclave (Apple Silicon) or T2 chip.
- **Headless:** Yes. `-A` flag pre-authorizes all apps — no popup on read.
- **Survives reboot:** Yes.
- **Setup:** None.

### Windows
- **Mechanism:** `CredWriteW` / `CredReadW` via Win32 API. Target: `worthless/shard-b/<key-id>`. Or `keyring` Python library (WinVault backend).
- **Trust quality:** Real (partial). DPAPI encrypts with user login credential derivative.
- **Headless:** Yes. Works from services, SSH, scheduled tasks.
- **Survives reboot:** Yes.
- **Setup:** None.

### Linux (kernel keyring)
- **Mechanism:** `keyctl add user worthless:shard-b:<key-id> <data> @u`. Read via `keyctl read <key-id>`.
- **Trust quality:** Partial. Kernel memory, not filesystem. Same-UID processes CAN read `@u` keys. Combined with sidecar's `PR_SET_DUMPABLE(0)`, raises the bar significantly.
- **Headless:** Yes. No D-Bus, no GUI.
- **Survives reboot:** No. Sidecar re-injects from encrypted file at startup.
- **Setup:** `keyutils` package (pre-installed on most distros).

### Linux headless (encrypted file fallback)
- **Mechanism:** Shard B encrypted with AES-256-GCM, key derived via Argon2id from `/etc/machine-id` + install-time salt. Stored at `~/.config/worthless/shards/<key-id>.shard-b.enc`.
- **Trust quality:** Defense-in-depth only. Same-machine attacker can re-derive key.
- **Headless:** Yes.
- **Survives reboot:** Yes.
- **Setup:** None (auto-derived). Optional passphrase at enrollment for stronger protection.

### Docker Compose
- **Mechanism:** Compose `secrets:` directive. Shard B file mounted in sidecar container only at `/run/secrets/worthless-shard-b`. Proxy container never sees it.
- **Trust quality:** Real. Container boundary = separate Linux namespaces.
- **Headless:** Yes.
- **Survives reboot:** Yes (secret file persists on host).
- **Setup:** `worthless enroll --docker` outputs compose snippet + shard file.

### Kubernetes
- **Mechanism:** K8s Secret mounted via CSI Secret Store Driver, backed by cloud KMS.
- **Trust quality:** Real. Hardware-backed KMS, RBAC-restricted.
- **Headless:** Yes.
- **Survives reboot:** Yes.
- **Setup:** Helm chart includes SecretProviderClass templates.

### PaaS / CI
- **Mechanism:** Platform secret → env var `WORTHLESS_SHARD_B`.
- **Trust quality:** Equivalent to filesystem (honest Tier 3).
- **Headless:** Yes.
- **Survives reboot:** Yes.
- **Setup:** `worthless enroll --paas` outputs the value to store in platform dashboard.

## Sidecar Hardening (All Platforms)

### Linux
| Technique | API | Purpose |
|---|---|---|
| Block memory reads | `prctl(PR_SET_DUMPABLE, 0)` | Blocks `/proc/pid/mem` and ptrace from same-UID |
| Lock memory | `mlock()` on shard buffers | Prevents swap to disk |
| Exclude from dumps | `madvise(MADV_DONTDUMP)` | Excluded from core dumps |
| Syscall filter | seccomp-BPF allowlist | Restricts to minimum syscalls |
| No privilege escalation | `prctl(PR_SET_NO_NEW_PRIVS, 1)` | Blocks setuid |

### macOS
| Technique | API | Purpose |
|---|---|---|
| Block debugger | `ptrace(PT_DENY_ATTACH)` | Kills process on debug attempt |
| Lock memory | `mlock()` | Prevents swap |
| No core dumps | `setrlimit(RLIMIT_CORE, 0)` | No core dumps |

### Windows
| Technique | API | Purpose |
|---|---|---|
| Restrict access | `SetSecurityInfo` (owner-only DACL) | Only sidecar user can access process |
| Lock memory | `VirtualLock()` | Prevents paging |
| Remove debug | Strip `SeDebugPrivilege` from token | Blocks external debugging |

## Security Tiers

### Tier 1: Three Real Trust Domains
**Platforms:** macOS (Secure Enclave), Kubernetes (KMS), Docker multi-container

**Attacker needs:** Two independent breaches — filesystem AND keychain/KMS/container boundary.

**Honest claim:** *"Stealing one shard reveals zero information about the API key. Reconstruction requires breaching two independent security boundaries."*

### Tier 2: Two-and-a-Half Trust Domains
**Platforms:** Windows (DPAPI), Linux desktop (keyring + process isolation), WSL2

**Attacker needs:** User-level access that bypasses process isolation. Root defeats both.

**Honest claim:** *"Shards are in separate access-control domains. A non-root attacker must defeat process isolation to reach the second shard. This is strictly stronger than any single-file secret storage."*

### Tier 3: Two Trust Domains
**Platforms:** Linux headless (encrypted file), PaaS (env vars), CI

**Attacker needs:** Filesystem access (can re-derive encryption key on headless Linux).

**Honest claim:** *"The key is split and the sidecar zeroes material after use, limiting exposure to microseconds. Defense-in-depth encryption on Shard B raises the bar vs. plaintext. This is stronger than a plaintext key on disk, but an attacker with runtime process access can reach both shards."*

### Tier Comparison

| Property | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| Separate encryption domains | Yes | Partially | No |
| Root defeats both shards | Requires 2 breaches | Yes | Yes |
| Non-root attacker blocked | Yes | Usually | No |
| Offline disk theft defeated | Yes | Yes | Partially |
| Key exposure window | ~15μs | ~15μs | ~15μs |

## Docker Architecture

```yaml
version: "3.8"
services:
  proxy:
    image: worthless/proxy:latest
    ports: ["8443:8443"]
    volumes:
      - shard-a:/data/shards:ro
      - sidecar-sock:/run/worthless:rw
    environment:
      WORTHLESS_SIDECAR_SOCKET: /run/worthless/sidecar.sock
    depends_on: [sidecar]

  sidecar:
    image: worthless/sidecar:latest
    volumes:
      - sidecar-sock:/run/worthless:rw
    secrets: [worthless-shard-b]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    environment:
      WORTHLESS_SHARD_B_PATH: /run/secrets/worthless-shard-b

volumes:
  shard-a: {}
  sidecar-sock:
    driver_opts:
      type: tmpfs
      device: tmpfs

secrets:
  worthless-shard-b:
    file: ./shard-b.key
```

**Key properties:** Proxy has Shard A only. Sidecar has Shard B only. Neither container can reconstruct alone. UDS on tmpfs never hits disk.

## What This Replaces

| Current | New | Change |
|---|---|---|
| Fernet symmetric key | Eliminated | No more key-management-for-key-management |
| XOR 2-of-2 | Shamir 2-of-3 | Adds backup shard, survives loss of one |
| SQLite (both shards in same DB) | Filesystem + credential store | Separate trust domains |
| Python reconstruction | Rust sidecar IPC | Memory-safe, process-isolated, ~15μs exposure |
| Single trust domain | 2–3 trust domains | Multi-breach required |
| No process hardening | prctl/mlock/seccomp/PT_DENY_ATTACH | Defense-in-depth |

## Impact on Linear Tickets

| Ticket | Impact |
|---|---|
| WOR-134 (Fernet in memory) | **Obsoleted** — Fernet eliminated |
| WOR-135 (Docker same-volume) | **Obsoleted** — multi-container separation |
| WOR-138 (PoC Security Fixes epic) | **Obsoleted** — both children gone |
| WOR-60 (Rust sidecar, 6 subtasks) | **Accelerated** — this provides the sidecar's storage + hardening spec |
| WOR-136 (Docker e2e test) | **Rewritten** — must test multi-container sidecar flow |
| WOR-137 (SAST nightly) | **Unaffected** |

## Open Questions

1. **Kernel keyring doesn't survive reboot.** Sidecar must re-inject from encrypted file at boot. Is a systemd user service the right approach?
2. **macOS headless Keychain.** Servers/CI on macOS need `security unlock-keychain`. Fall back to encrypted file?
3. **Windows service accounts.** DPAPI behaves differently under `LocalSystem`. Verify Credential Manager works.
4. **Shard rotation.** Re-enrolling requires regenerating all 3 shards. Docker/K8s need infrastructure secret updates — UX for this?
5. **PaaS dual-container.** Railway/Render support multi-container. Should we provide a compose that elevates PaaS from Tier 3 to Tier 1?
6. **Seccomp profile distribution.** Embed BPF bytecode in binary or distribute as JSON for Docker/K8s?
7. **WSL2 performance.** `cmdkey.exe` crosses Windows/Linux boundary. Benchmark latency impact.

## Research Sources

| Track | File |
|---|---|
| Linux Kernel Keyring | [linux-kernel-keyring.md](./linux-kernel-keyring.md) |
| macOS & Windows Credentials | [macos-windows-credentials.md](./macos-windows-credentials.md) |
| Docker Container Injection | [docker-container-injection.md](./docker-container-injection.md) |
| Process Isolation Without Sudo | [process-isolation-no-sudo.md](./process-isolation-no-sudo.md) |
| Fallback Encrypted Shard | [fallback-encrypted-shard.md](./fallback-encrypted-shard.md) |
| Original Proposal | [shamir-sidecar-architecture.md](../shamir-sidecar-architecture.md) |
| Security Review | [shamir-sidecar-security-review.md](../shamir-sidecar-security-review.md) |
| Crypto Verification | [shamir-sidecar-verification.md](../shamir-sidecar-verification.md) |
| UX Impact Analysis | [ux-impact-analysis.md](../ux-impact-analysis.md) |
