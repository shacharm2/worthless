# Fallback Shard Protection: Headless Platforms Without Real Credential Stores

**Research date**: 2026-04-04
**Researcher**: Security Engineer (Claude Opus 4.6)
**Problem**: On headless Linux, minimal Docker, and CI runners, no OS keychain exists. Shard B needs protection beyond "just another file on the same filesystem," but without introducing the circular Fernet problem (encrypting with a key that lives next to the ciphertext).

---

## The problem precisely stated

Worthless splits API keys into Shamir 2-of-3 shares across three trust domains: filesystem, OS keychain, and sidecar process. On a developer workstation with macOS Keychain or GNOME Keyring, each domain is genuinely independent. But on platforms where no real credential store exists -- headless servers, Docker containers, CI runners, minimal VMs -- Shard B has nowhere to go except the filesystem, which collapses two trust domains into one.

The question is: what can we do to make Shard B meaningfully harder to steal than Shard A, without requiring root, user interaction, or special hardware?

---

## Mechanism 1: Machine-Bound Key Derivation (MBKD)

### How it works

Derive an encryption key from machine-specific entropy that is not stored anywhere:

```
salt = read_file("/var/lib/worthless/install-salt")  # written once at install time
machine_id = read_file("/etc/machine-id")             # systemd machine identifier
derivation_key = HKDF-SHA256(
    ikm = machine_id || salt,
    salt = "worthless-shard-encryption-v1",
    info = "shard-b-protection"
)
encrypted_shard_b = AES-256-GCM(key=derivation_key, plaintext=shard_b, nonce=random)
```

The derived key is never stored. On each access, the system re-derives it from the same inputs.

### What it protects against

- **Exfiltration of the shard file alone.** If an attacker copies `~/.config/worthless/shards/b.enc` to another machine, they cannot decrypt it without `/etc/machine-id` and the install salt.
- **Backup/snapshot attacks.** A filesystem backup that does not include `/etc/machine-id` (unlikely but possible with selective backup tools) yields an unusable shard.
- **Offline analysis.** The encrypted shard is meaningless without the derivation inputs.

### What it does NOT protect against

- **Same-machine attacker.** `/etc/machine-id` is world-readable (0444 on every distro). The install salt is in a Worthless directory. Any process running as the same user (or any user) can read both and re-derive the key. This is the critical weakness.
- **Root access.** Trivially defeats this, as with any filesystem-based scheme.
- **Container escape.** If the attacker is already in the container, they have all derivation inputs.

### Additional entropy sources considered

| Source | Access method | Availability | User-space? | Added value |
|--------|-------------|-------------|------------|-------------|
| `/etc/machine-id` | read file | All systemd systems | Yes | World-readable, low entropy addition |
| CPU serial (`/proc/cpuinfo`) | read file | Most Linux | Yes | Often identical across VMs, sometimes unavailable |
| DMI data (`/sys/class/dmi/id/product_serial`) | read file | Physical machines | Root-only on many distros | Not user-space accessible |
| Disk serial (`/sys/block/*/serial`) | read file | Physical machines | Sometimes root-only | Not available in VMs/containers |
| MAC address | socket ioctl | Almost universal | Yes | Trivially spoofable, may change |
| Boot ID (`/proc/sys/kernel/random/boot_id`) | read file | All Linux | Yes | Changes every reboot -- makes shard unrecoverable after restart |

**Assessment**: Adding more machine identifiers increases complexity without meaningfully raising the bar. An attacker on the same machine has access to all the same inputs. The only source that adds real entropy is `boot_id`, but it makes the shard unrecoverable after reboot, which is a non-starter for persistent storage.

### UX cost

- Zero user interaction required.
- Install-time salt generation is automatic.
- Shard becomes non-portable (machine-bound), which is actually a feature.
- If `/etc/machine-id` changes (rare: system reinstall, container recreation), shard is lost and re-enrollment is required.

### Honest assessment

MBKD is **marginally better than plaintext** -- it defeats file-copy-to-another-machine attacks and adds one layer of indirection. But it is NOT a real trust domain separation. An attacker with filesystem access to the shard file almost certainly has access to `/etc/machine-id` and the install salt. This is "security by obscurity of the derivation scheme," not defense in depth.

**Rating: Weak.** Better than nothing, but do not claim this provides a meaningful security boundary.

---

## Mechanism 2: TPM 2.0 Sealed Storage

### How it works

TPM 2.0 (Trusted Platform Module) can seal data to a specific platform state. The sealed blob can only be decrypted when the TPM's Platform Configuration Registers (PCRs) match the values present at seal time.

```bash
# Create a primary key in the owner hierarchy
tpm2_createprimary -C o -g sha256 -G rsa -c primary.ctx

# Create a sealing key
tpm2_create -C primary.ctx -g sha256 -u seal.pub -r seal.priv \
    -L sha256:0,1,7 -i shard_b.bin

# Load and seal
tpm2_load -C primary.ctx -u seal.pub -r seal.priv -c seal.ctx

# Unseal (only works when PCRs match)
tpm2_unseal -c seal.ctx -o shard_b_recovered.bin
```

### Root requirement analysis

**Can TPM operations run without root?**

The TPM device node is `/dev/tpm0` (direct) or `/dev/tpmrm0` (resource manager, preferred). Permissions vary:

| Distribution | Default `/dev/tpmrm0` permissions | Group | User-space access? |
|-------------|----------------------------------|-------|-------------------|
| Ubuntu 22.04+ | crw-rw---- root tss | `tss` group | Yes, if user is in `tss` group |
| Fedora 38+ | crw-rw---- root tss | `tss` group | Yes, if user is in `tss` group |
| Debian 12 | crw-rw---- root tss | `tss` group | Yes, if user is in `tss` group |
| RHEL 9 | crw-rw---- root tss | `tss` group | Yes, if user is in `tss` group |
| Arch | crw-rw---- root tss | `tss` group | Yes, if user is in `tss` group |

Adding a user to the `tss` group requires root once, but after that, all TPM operations are user-space. The `tpm2-abrmd` (TPM2 Access Broker & Resource Manager Daemon) service handles concurrent access.

**Using the owner hierarchy without an owner password** (the default), `tpm2_createprimary` and `tpm2_create` do not require elevated privileges -- they run as any user with `/dev/tpmrm0` access.

### Availability

| Platform | TPM 2.0 present? | Notes |
|----------|------------------|-------|
| Laptops (2016+) | ~100% | Windows 11 requires it; virtually all x86 laptops ship with discrete or firmware TPM |
| Servers (physical) | ~70-80% | Most enterprise servers (Dell, HPE, Lenovo) ship with TPM. Some budget/custom builds do not |
| Cloud VMs (AWS) | Via Nitro TPM (2022+) | `NitroTPM` parameter on instance launch. Supported on most instance types |
| Cloud VMs (Azure) | Via vTPM | Available on all Gen2 VMs. Default on Confidential VMs |
| Cloud VMs (GCP) | Via vTPM | Available on Shielded VMs (most instance types) |
| Docker containers | No | Containers do not have TPM access unless `/dev/tpmrm0` is bind-mounted (rare, security risk) |
| CI runners (GitHub Actions) | No | Standard runners have no TPM. Larger runners may have vTPM on Azure-backed infra |
| Raspberry Pi | No (Pi 4), maybe (Pi 5 with external module) | Not practical |

### Python/Rust libraries

| Library | Language | Maturity | Notes |
|---------|----------|----------|-------|
| `tpm2-pytss` | Python | Good | Official TPM2 Software Stack Python bindings. Wraps tpm2-tss C library. pip-installable with system deps |
| `tpm2-tools` | CLI (C) | Excellent | Production-grade, widely packaged. Shell out from any language |
| `tss-esapi` | Rust | Good | Part of the `tpm2-software` org. Wraps tpm2-tss ESAPI |
| `tpm2-rs` | Rust | Experimental | Pure Rust, no C deps. Less mature |

### What it protects against

- **File exfiltration.** The sealed blob is cryptographically bound to the specific TPM chip. Copying it to another machine is useless.
- **Boot state changes.** If PCR values change (different kernel, bootloader tampering), unseal fails.
- **Offline attacks.** The TPM's sealed storage is hardware-backed. No amount of offline computation breaks it.

### What it does NOT protect against

- **Same-user on same machine.** Any process running as a user in the `tss` group can unseal the blob. TPM does not enforce per-process access control.
- **Root access.** Root can read `/dev/tpmrm0` directly.
- **Platform unavailability.** Docker, most CI, and many VMs have no TPM. This is a complement to other mechanisms, not a universal solution.

### UX cost

- Requires `tss` group membership (one-time root operation) or system-packaged `tpm2-abrmd`.
- `tpm2-pytss` or `tpm2-tools` must be installed (system package, not pip-only).
- Seal/unseal adds ~5-50ms per operation (hardware-dependent).
- PCR policy must be carefully chosen -- too strict and kernel updates break unseal; too loose and the binding is meaningless.

### Honest assessment

TPM 2.0 is the **gold standard** for machine-bound secret storage when available. It provides genuine hardware-backed protection that no software-only scheme can match. But it is unavailable on the exact platforms where we need the fallback (Docker, CI, minimal VMs). It should be the **top tier** of a defense-in-depth strategy on platforms that support it, not the fallback.

**Rating: Excellent where available. Unavailable where needed most.**

---

## Mechanism 3: Memory-Mapped File with mlock (Volatile Shard)

### How it works

```python
import mmap, os

# Create anonymous mmap (no file backing)
fd = os.open("/dev/zero", os.O_RDWR)  # or use mmap.MAP_ANONYMOUS
buf = mmap.mmap(fd, 4096, mmap.MAP_SHARED | mmap.MAP_ANONYMOUS,
                mmap.PROT_READ | mmap.PROT_WRITE)

# Lock into physical memory (prevent swap)
import ctypes
libc = ctypes.CDLL("libc.so.6")
libc.mlock(ctypes.c_void_p(id(buf)), 4096)

# Write shard into locked memory
buf[:len(shard_b)] = shard_b

# Unlink any file path -- shard exists only in memory
# (If using a named file initially, os.unlink it after mmap)
```

The shard exists only in locked physical memory. No filesystem path. No swap exposure.

### What it protects against

- **Filesystem attacks.** There is no file to steal.
- **Swap exposure.** mlock prevents the page from being swapped to disk.
- **Core dump exposure.** Combined with `PR_SET_DUMPABLE(0)` and `MADV_DONTDUMP`, the shard does not appear in core dumps.

### What it does NOT protect against

- **Same-UID process memory reads.** On default Linux, any process running as the same UID can read `/proc/<pid>/mem`. This is the fundamental problem.
- **Root access.** Root can read any process memory.
- **Process restart.** The shard is lost when the process exits. Requires re-enrollment or a persistent backup somewhere.
- **Yama ptrace_scope dependency.** With `ptrace_scope=1` (default on Ubuntu), only parent processes can ptrace children. But `ptrace_scope=0` (Arch default, many servers) allows any same-UID process to read memory.

### The persistence problem

This mechanism only protects the shard while the process is running. After restart, the shard must be reloaded from somewhere persistent. Options:

1. **Re-enrollment.** User must re-register the API key after every restart. Unacceptable for headless operation.
2. **Encrypted persistent copy + volatile key.** Store encrypted shard on disk, decrypt at startup into mlock'd memory, keep the decryption key only in memory. But where does the decryption key come from at startup? This is the bootstrap problem again.
3. **Sidecar holds in memory, gets shard from enrollment flow.** The enrollment flow sends the shard to the sidecar over a Unix socket. The sidecar holds it in mlock'd memory forever. On restart, re-enrollment is required. This works for long-running servers but not for CLI tools or CI.

### UX cost

- mlock has per-process limits (default 64KB on most systems, configurable via `ulimit -l` or `/etc/security/limits.conf`). A single shard (~50-100 bytes) is well within limits.
- Requires process to stay running. Not suitable for CLI invocations.
- Re-enrollment after restart is a significant UX cost for headless systems.

### Honest assessment

Volatile memory is an excellent **runtime** protection mechanism but does not solve the **persistence** problem. It is a component of a solution, not a complete solution. Best used as: persistent encrypted shard on disk + sidecar decrypts at startup into mlock'd memory + sidecar holds volatile copy for request-time access.

**Rating: Strong runtime protection. Does not solve persistence.**

---

## Mechanism 4: Linux Kernel Keyring as Encryption Key Source

### How it works

The Linux kernel keyring (`keyctl(2)`) stores opaque blobs in kernel memory, indexed by description strings. Keys are organized into keyrings; the most useful for unprivileged use are:

- `@u` -- User keyring. Per-UID, persists across sessions until the user's last process exits.
- `@us` -- User session keyring. Per-login-session, lost on logout.
- `@s` -- Session keyring. Per-session, inherited by children.

```python
import subprocess

# Store a random AES key in the user keyring
aes_key = os.urandom(32)
result = subprocess.run(
    ["keyctl", "padd", "user", "worthless-shard-key", "@u"],
    input=aes_key, capture_output=True
)
key_id = result.stdout.strip()

# Later, retrieve it
result = subprocess.run(
    ["keyctl", "pipe", key_id],
    capture_output=True
)
aes_key = result.stdout

# Use AES key to decrypt Shard B from disk
shard_b = AES_GCM_decrypt(key=aes_key, ciphertext=read_file("shard_b.enc"))
```

The AES key lives in kernel memory (not the filesystem). The encrypted shard lives on the filesystem. An attacker needs access to both kernel keyring AND filesystem to recover Shard B.

### Is this actually non-circular?

**Yes, with caveats.** The key and the ciphertext are in different storage media:

| Component | Storage medium | Access mechanism |
|-----------|---------------|-----------------|
| Encrypted Shard B | Filesystem | read() syscall |
| AES encryption key | Kernel keyring | keyctl() syscall |

A file-read vulnerability (e.g., path traversal, backup exposure) gets the encrypted shard but not the keyring key. A keyring access vulnerability gets the key but not the encrypted shard. This is genuine separation -- not as strong as a hardware trust boundary, but meaningfully better than both items on the same filesystem.

### Root requirement

**No root required for `@u` keyring operations.** The `keyctl` utility and the `keyctl(2)` syscall work for any unprivileged user operating on their own keyrings. The kernel enforces that UID X cannot read UID Y's `@u` keyring.

### Persistence

| Keyring | Persists across sessions? | Persists across reboots? |
|---------|--------------------------|-------------------------|
| `@u` (user) | Yes, until last process for that UID exits | **No** |
| `@us` (user-session) | No, lost on logout | No |
| `@s` (session) | No, lost when session ends | No |

**Critical limitation**: The `@u` keyring is lost when the last process for that UID exits. On a server with a persistent daemon, this is fine. On a CI runner that starts fresh each job, the keyring key is lost between jobs.

**On reboot**, the keyring is always lost. The shard encryption key must be re-created and the shard re-encrypted, which means the plaintext shard must come from somewhere -- back to the bootstrap problem.

### Availability

| Platform | Kernel keyring available? | `keyctl` utility? |
|----------|--------------------------|-------------------|
| Ubuntu/Debian | Yes (kernel 2.6.10+) | `keyutils` package |
| Fedora/RHEL | Yes | `keyutils` package |
| Alpine Linux | Yes (if CONFIG_KEYS=y) | `keyutils` package |
| Docker (default) | **Depends on host kernel** | Must install `keyutils` in container |
| CI runners | Yes (on Linux) | Must install `keyutils` |
| macOS | **No** (different mechanism) | N/A |

### Python/Rust access

| Library | Language | Notes |
|---------|----------|-------|
| `keyctl` (subprocess) | Any | Shell out to `keyctl` CLI. Simple, works everywhere |
| `python-keyutils` | Python | Ctypes wrapper around libkeyutils. Last release 2019 but functional |
| `keyutils` crate | Rust | Bindings to libkeyutils. Maintained |
| Direct `syscall(2)` | Any | `keyctl(2)` is a single syscall. Can be called via ctypes without any external library |

### What it protects against

- **File-only exfiltration.** Copying the filesystem (backup, container image layer) gets only the encrypted shard.
- **Offline analysis.** Without the runtime keyring key, the encrypted shard is useless.
- **Cross-user attacks.** Kernel enforces UID-based keyring isolation.

### What it does NOT protect against

- **Same-UID process.** Any process running as the same UID can call `keyctl_read()` on the `@u` keyring. The kernel keyring has no per-process isolation within a UID.
- **Root access.** Root can read any keyring via `/proc/keys` or `keyctl` with appropriate capabilities.
- **Reboot.** Key is lost. Re-enrollment or alternative bootstrap needed.
- **Non-Linux platforms.** macOS has no kernel keyring equivalent at this level.

### UX cost

- Requires `keyutils` package installed (1-2 second `apt install`).
- Automatic key generation and storage -- zero user interaction.
- Transparent to the user unless they reboot and the sidecar must re-derive/re-enroll.
- On long-running servers (the primary headless use case), the `@u` keyring persists for the server's uptime, which may be months or years.

### Honest assessment

The kernel keyring is the **best available non-circular fallback on Linux**. It provides genuine storage-medium separation (kernel memory vs. filesystem) without requiring root, user interaction, or special hardware. The limitation is reboot persistence, but for long-running servers and daemons (the primary headless use case), this is acceptable. For CI runners that restart per-job, the keyring key must be re-seeded from an external secret (environment variable from CI secrets manager).

**Rating: Good. Best available software-only Linux fallback.**

---

## Mechanism 5: Encrypted Memory with Process Isolation

### How it works

Combine multiple process-hardening primitives to create a "memory jail" for the shard:

```c
// In the Rust sidecar:
prctl(PR_SET_DUMPABLE, 0);           // Prevent core dumps
mlock(shard_buf, shard_len);          // Prevent swap
madvise(shard_buf, shard_len, MADV_DONTDUMP);  // Exclude from dumps

// Yama ptrace_scope (system-wide, not per-process):
// 0 = any same-UID can ptrace (weak)
// 1 = only parent can ptrace children (Ubuntu default)
// 2 = only CAP_SYS_PTRACE (strong)
// 3 = no ptrace at all
```

With `PR_SET_DUMPABLE(0)`, the `/proc/<pid>/mem` file becomes unreadable by same-UID processes (they get EPERM on open). This is a **real isolation boundary** -- not just obscurity.

### The PR_SET_DUMPABLE trick -- why this is stronger than it looks

When a process sets `PR_SET_DUMPABLE` to 0:

1. `/proc/<pid>/mem` returns EACCES to same-UID readers.
2. `/proc/<pid>/maps` returns EACCES (so attacker cannot even find the shard address).
3. `ptrace(PTRACE_ATTACH)` returns EPERM from same-UID (even with Yama scope 0).
4. Core dumps are suppressed.

This means a **same-UID attacker cannot read the sidecar's memory**. They would need:
- Root access (CAP_SYS_PTRACE), or
- A kernel vulnerability, or
- To compromise the sidecar process itself (e.g., code execution within it)

This is genuinely a different trust domain from the filesystem.

### But the persistence problem remains

The shard is safe in memory while the sidecar runs. On restart, where does it come from?

**Bootstrap flow for long-running servers:**
1. At enrollment, Shard B is encrypted with kernel keyring key and stored on disk.
2. At sidecar startup, decrypt Shard B using keyring key into mlock'd memory.
3. Set `PR_SET_DUMPABLE(0)`.
4. Shard now lives only in protected memory. The encrypted copy on disk is protected by keyring separation.

**Bootstrap flow for CI/ephemeral:**
1. CI secret manager provides Shard B via environment variable.
2. Sidecar reads it from env, copies to mlock'd memory, clears env.
3. Set `PR_SET_DUMPABLE(0)`.
4. Shard exists only in protected memory for the job duration.

### What it protects against

- **Same-UID process memory reading.** `PR_SET_DUMPABLE(0)` is a kernel-enforced barrier.
- **Core dumps and crash reports.** Shard never appears in dumps.
- **Swap exposure.** mlock prevents paging.
- **Filesystem attacks (runtime).** Once loaded, the shard is not on disk in plaintext.

### What it does NOT protect against

- **Root/CAP_SYS_PTRACE.** Root can still read any process memory.
- **Kernel vulnerabilities.** Kernel bugs could bypass ptrace restrictions.
- **Cold boot attacks.** Physical memory can be read after power-off (exotic, requires physical access).
- **The bootstrap moment.** At startup, the plaintext shard must transit from some persistent store into memory. That transit is the vulnerability window.

### UX cost

- Zero user interaction.
- Automatic at sidecar startup.
- Invisible to the user -- just a hardened sidecar.
- Requires the sidecar to be a long-running process (not suitable for one-shot CLI).

### Honest assessment

`PR_SET_DUMPABLE(0)` in the sidecar creates a **genuine trust domain** -- same-UID processes cannot read its memory. Combined with kernel keyring for the persistent encrypted copy, this is a real two-domain separation: filesystem + process memory with kernel-enforced isolation. This is the best we can do without hardware trust anchors.

**Rating: Strong. Creates a real (kernel-enforced) trust boundary.**

---

## Mechanism 6: The Honest "Tier 2" Assessment

### What can we honestly claim on no-keychain platforms?

On a platform with only filesystem + sidecar process:

| Attack | Sidecar with PR_SET_DUMPABLE(0) + kernel keyring encryption | Current XOR + Fernet | Plain files (baseline) |
|--------|-------------------------------------------------------------|---------------------|----------------------|
| Filesystem read (backup, path traversal) | Gets encrypted shard only. Cannot decrypt without keyring key | Gets encrypted shard + Fernet key = full compromise | Gets both shards = full compromise |
| Same-UID process read | Cannot read sidecar memory. Cannot read keyring... wait, CAN read keyring | Can read Fernet key file = full compromise | Can read both shard files = full compromise |
| Root compromise | Full compromise | Full compromise | Full compromise |
| Container escape from sibling | Depends on container isolation | Full compromise (shared volume) | Full compromise |

**The kernel keyring weakness for same-UID**: a same-UID process CAN call `keyctl_read()` on the `@u` keyring. BUT combined with `PR_SET_DUMPABLE(0)` on the sidecar, the attacker faces:
- Kernel keyring gives them the AES key
- Filesystem gives them the encrypted shard
- They CAN reconstruct Shard B
- But they still need Shard A (or Shard C from sidecar memory, which they CANNOT read)

So the defense holds at the Shamir level: even if Shard B is compromised, the attacker needs a second shard. `PR_SET_DUMPABLE(0)` protects Shard C in the sidecar. Shard A is on the filesystem. If the same attacker has filesystem access AND keyring access, they have Shard A and Shard B -- game over.

### Honest conclusion for same-UID attacker

On a no-keychain platform with same-UID attacker:
- **Shard A**: filesystem -- compromised
- **Shard B**: encrypted on disk, key in keyring -- compromised (same UID can read keyring)
- **Shard C**: sidecar memory with PR_SET_DUMPABLE(0) -- **protected**

The attacker has 2 of 3 shards. **The Shamir scheme is compromised.**

But this is STILL better than the current architecture where a same-UID attacker gets the full key from a single file read (Fernet key + encrypted shard on same filesystem).

### Comparison with current XOR + Fernet

| Property | Shamir 2-of-3 + sidecar + MBKD/keyring | Current XOR + Fernet |
|----------|----------------------------------------|---------------------|
| Shards needed to reconstruct | 2 of 3 | Both (equivalent to 2 of 2) |
| File-only attack | Gets 1 shard (useless) or encrypted shard | Gets everything (Fernet key is a file too) |
| Same-UID attack | Gets 2 shards (A from disk + B from keyring). Protected shard C in sidecar may save you, but A+B suffices | Gets everything |
| Root attack | Gets everything | Gets everything |
| Trust domains (workstation) | 3 (filesystem, keychain, sidecar) | 1 (filesystem) |
| Trust domains (headless) | 2 (filesystem, sidecar memory) | 1 (filesystem) |

**The Shamir + sidecar architecture is strictly better in all scenarios**, even on headless platforms where it degrades to 2 trust domains. On headless, it is equivalent to a 2-of-2 scheme where the attacker must breach both filesystem and sidecar process memory.

---

## Mechanism 7: What Other Security Tools Do

### SSH Agent

**Model**: Keys loaded into memory from encrypted files (passphrase-derived key). After load, the agent holds keys in memory. The file can be deleted.

**Persistence**: Keys persist in agent memory for the session (or with timeout). On restart, user must re-enter passphrase or use `ssh-add` with a key file.

**Headless bootstrap**: SSH agent on headless servers typically uses key files with no passphrase (filesystem permissions only) or is pre-loaded by an orchestration tool (Ansible, Puppet) that injects the key.

**Lesson for Worthless**: SSH agent's model is essentially "sidecar holds key in memory, filesystem has encrypted backup." Same architecture we are converging on.

### GPG Agent

**Model**: Private keys stored in `~/.gnupg/` encrypted with a passphrase-derived key (iterated+salted S2K). GPG agent caches the decrypted key in memory with a configurable TTL.

**Headless bootstrap**: `--batch --passphrase-fd 0` reads the passphrase from stdin (piped from a secret manager). Or `--pinentry-mode loopback` with `--passphrase`. Alternatively, pre-unlocked agent with `gpg-preset-passphrase`.

**Lesson for Worthless**: The "passphrase" model does not work for headless operation. GPG's headless mode essentially reduces to "inject the secret from an external source" -- which is what CI secret managers do.

### HashiCorp Vault Auto-Unseal

**Model**: Vault's master key is Shamir-split. Manual unseal requires K-of-N operators to enter their shards. Auto-unseal delegates to an external KMS:

| Auto-unseal method | How it works | Requires |
|-------------------|-------------|----------|
| AWS KMS | Master key encrypted by AWS KMS key. Vault calls KMS Decrypt at startup | AWS credentials + KMS key |
| Azure Key Vault | Same pattern, Azure Key Vault as KMS | Azure AD credentials |
| GCP Cloud KMS | Same pattern, GCP KMS | GCP service account |
| Transit (another Vault) | Master key encrypted by a separate Vault instance | Network access to seal Vault |
| HSM (PKCS#11) | Master key sealed to HSM | Physical HSM device |

**Lesson for Worthless**: Vault explicitly acknowledges that **without an external trust anchor (cloud KMS, HSM, or human operators), there is no non-circular way to persist a secret across restarts**. Cloud KMS is the industry solution for headless auto-bootstrap.

### Industry-accepted "good enough" for headless secret storage

The industry consensus is a tiered model:

1. **Best**: Hardware trust anchor (TPM, HSM, Secure Enclave)
2. **Good**: Cloud KMS (AWS KMS, Azure Key Vault, GCP KMS) -- the trust anchor is the cloud provider's IAM
3. **Acceptable**: Filesystem permissions + process isolation + memory protection -- the SSH key model
4. **Weak**: Filesystem permissions alone -- the "chmod 600 and pray" model

For headless servers without cloud KMS access, the industry-accepted answer is literally "filesystem permissions + process isolation." SSH has operated on this model for 25+ years.

---

## RECOMMENDATION: Tiered Fallback Strategy

Worthless should implement a **tiered shard protection strategy** that automatically selects the strongest available mechanism on each platform. The tiers are ordered by security strength.

### Tier 0: OS Credential Store (Full Strength)

**Platforms**: macOS (Keychain), Linux with GNOME Keyring/KDE Wallet, Windows (Credential Manager)

**Mechanism**: Shard B stored in the OS credential store. Shard C in sidecar memory. Three independent trust domains.

**Security claim**: "Compromising any single trust domain reveals zero information about the API key."

**Detection**: Attempt D-Bus connection to `org.freedesktop.secrets` (Linux) or check for `security` CLI (macOS). If available, use this tier.

### Tier 1: TPM Sealed Storage

**Platforms**: Physical machines and cloud VMs with TPM 2.0

**Mechanism**: Shard B sealed to TPM (bound to PCR state). Shard C in sidecar memory with PR_SET_DUMPABLE(0).

**Security claim**: "Shard B is hardware-bound. Compromising the filesystem alone is insufficient."

**Detection**: Check for `/dev/tpmrm0`. If accessible, use this tier. Fall through if not.

### Tier 2: Kernel Keyring + Process Isolation (Recommended Headless Fallback)

**Platforms**: Headless Linux servers, some Docker containers, some CI runners

**Mechanism**:
1. At enrollment, generate a random AES-256-GCM key. Store it in the `@u` kernel keyring.
2. Encrypt Shard B with this key. Store the encrypted blob on the filesystem.
3. At sidecar startup, retrieve keyring key, decrypt Shard B into mlock'd memory, set PR_SET_DUMPABLE(0).
4. Shard B now exists only in sidecar's protected memory. Shard A is on the filesystem. Shard C is also in sidecar memory.

**Security claim**: "Compromising the filesystem alone yields only an encrypted shard. The decryption key is in a separate storage medium (kernel memory). The sidecar's process memory is kernel-protected against same-UID reads."

**Honest caveat**: A same-UID attacker can read the kernel keyring AND the filesystem, obtaining Shard B. But Shard C remains protected in the sidecar (PR_SET_DUMPABLE(0)). The attacker would have Shards A and B -- sufficient for reconstruction. This tier degrades to a 2-trust-domain model where the meaningful boundary is between "filesystem + keyring" and "sidecar memory."

**Detection**: Check for `keyctl` binary or try `keyctl(2)` syscall. Linux-only.

**Reboot handling**: On reboot, the keyring key is lost. The sidecar must re-derive it. Options:
- **Long-running servers**: The keyring persists for server uptime (months/years). Non-issue.
- **Reboot recovery**: Store a "recovery nonce" at enrollment time. On reboot, re-derive the keyring key from MBKD (machine-id + install salt + recovery nonce). This is weaker than a fresh keyring key but provides continuity.
- **CI runners**: Inject Shard B directly from CI secret manager (env var). No keyring needed -- the CI platform IS the trust anchor.

### Tier 3: Machine-Bound Key Derivation (Minimum Viable Protection)

**Platforms**: Minimal Docker containers, embedded systems, any Linux without keyring support

**Mechanism**:
1. At enrollment, generate a random install salt. Store in Worthless config directory.
2. Derive AES key: `HKDF(machine-id || install-salt || worthless-version-salt)`.
3. Encrypt Shard B with derived key. Store on filesystem.
4. Sidecar decrypts at startup into mlock'd memory with PR_SET_DUMPABLE(0).

**Security claim**: "The encrypted shard is not portable -- it cannot be decrypted on a different machine. An attacker must have access to the specific machine's identity to derive the decryption key."

**Honest caveat**: On the same machine, any process can read `/etc/machine-id` and the install salt. This is marginal protection -- it defeats file-copy attacks but not same-machine attackers. The real protection at this tier comes from PR_SET_DUMPABLE(0) on the sidecar, not from the encryption.

**Detection**: Fallback tier. Used when no higher tier is available.

### Tier CI: Environment Variable Injection

**Platforms**: CI/CD runners (GitHub Actions, GitLab CI, CircleCI, etc.)

**Mechanism**:
1. During team enrollment, Shard B is stored as a CI secret (e.g., `WORTHLESS_SHARD_B`).
2. At job start, sidecar reads from environment, copies to mlock'd memory, clears env.
3. PR_SET_DUMPABLE(0).

**Security claim**: "Shard B is managed by the CI platform's secret store. It enters the runner only at job time and exists only in protected process memory."

**Detection**: Check for `CI=true` or platform-specific env vars (`GITHUB_ACTIONS`, `GITLAB_CI`, etc.).

### Tier selection algorithm

```
if os_credential_store_available():
    use Tier 0
elif tpm_available():
    use Tier 1
elif ci_environment_detected():
    use Tier CI
elif kernel_keyring_available():
    use Tier 2
else:
    use Tier 3  # MBKD fallback
```

### Security posture summary

| Tier | File-only attack | Same-UID attack | Root attack | Trust domains | Honest rating |
|------|-----------------|-----------------|-------------|--------------|---------------|
| Tier 0 (Keychain) | Safe | Safe | Compromised | 3 | Excellent |
| Tier 1 (TPM) | Safe | Partial (still need 2nd shard) | Compromised | 2.5 (hardware-bound) | Very Good |
| Tier 2 (Keyring) | Safe | Compromised (but need 2 shards, sidecar protects 3rd) | Compromised | 2 | Good |
| Tier 3 (MBKD) | Safe (non-portable) | Compromised | Compromised | 1.5 (weak separation) | Marginal |
| Tier CI (Env) | N/A (ephemeral) | Compromised | Compromised | 2 (CI secrets + memory) | Good for context |

### What the documentation should say

For Tier 0 and Tier 1: "Worthless provides strong protection against single-domain compromise. An attacker must breach two independent trust boundaries to reconstruct your API key."

For Tier 2: "On headless Linux, Worthless uses kernel keyring separation and process memory isolation to protect your key. This is equivalent to the security model used by SSH agents and GPG agents -- meaningfully stronger than a single encrypted file, but not as strong as hardware-backed protection."

For Tier 3: "On minimal platforms without kernel keyring support, Worthless provides machine-binding (the encrypted shard cannot be used on another machine) and process memory isolation. This is the minimum viable protection tier. For stronger security on headless platforms, deploy with a cloud KMS or use a platform with kernel keyring support."

### Future enhancement: Cloud KMS auto-unseal (Tier 0.5)

Following Vault's model, a future version could support cloud KMS as a trust anchor:
- Store Shard B encrypted with a cloud KMS key (AWS KMS, Azure Key Vault, GCP KMS)
- At startup, call KMS Decrypt to recover Shard B
- The trust anchor is the cloud IAM credential (instance role, service account)

This would slot between Tier 0 and Tier 1 and would be the **gold standard for cloud-hosted headless deployments**. Not V1 scope, but the architecture should not preclude it.

---

## Implementation priority

1. **V1**: Implement Tier 0 (keychain), Tier 2 (kernel keyring), and Tier 3 (MBKD) with automatic selection. These cover workstations, headless servers, and Docker/CI respectively.
2. **V1 stretch**: Tier CI (env var injection) with documentation for GitHub Actions / GitLab CI.
3. **V1.1**: Tier 1 (TPM) for physical servers and cloud VMs.
4. **V2**: Cloud KMS auto-unseal for cloud-native deployments.

The `worthless security-check` command should report which tier is active and warn if operating at Tier 3 with recommendations for upgrading to a stronger tier.
