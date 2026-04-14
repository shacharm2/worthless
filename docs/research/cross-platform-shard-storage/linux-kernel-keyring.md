# Linux Kernel Keyring as Shard B Storage

**Date:** 2026-04-04
**Context:** Evaluating `keyctl` / `add_key` / `request_key` syscalls for storing Shard B (~50 bytes) in a trust domain separate from the filesystem (where Shard A lives at `~/.config/worthless/`).

---

## 1. Keyring Types and Lifetimes

### Keyring Comparison

| Keyring | Specifier | Scope | Survives process restart? | Survives logout/login? | Survives reboot? |
|---------|-----------|-------|---------------------------|------------------------|------------------|
| Thread keyring | `@t` | Per-thread | No (destroyed on exit) | No | No |
| Process keyring | `@p` | Per-process | No (destroyed on exit) | No | No |
| Session keyring | `@s` | Per-login-session | Yes (within same session) | No | No |
| User-session keyring | `@us` | Per-UID, per-boot-session | Yes | No (destroyed when last process of UID exits) | No |
| **User keyring** | **`@u`** | **Per-UID** | **Yes** | **Yes** (persists as long as any process with that UID exists or files are open) | **No** |
| **Persistent keyring** | via `keyctl_get_persistent()` | Per-UID, time-limited | Yes | **Yes** (survives all sessions ending) | **No** |

**Key insight:** No kernel keyring survives reboot. The kernel has no persistent storage — all keys live in kernel memory and are destroyed on shutdown.

### User Keyring (`@u`) — Best Candidate

- Created automatically on first access by any process running as that UID.
- Shared across ALL processes of the same UID.
- Persists as long as the UID's record exists in the kernel (i.e., at least one process or open file belongs to that UID).
- **No root/sudo required** to create or read keys.
- On a server where the proxy runs as a daemon (always-on), the user keyring will persist indefinitely until reboot.

### Persistent Keyring — Alternative

- Accessed via `keyctl_get_persistent(uid, dest_keyring)`.
- Survives even after all processes of that UID have exited.
- Has an expiration timer (default: 3 days, configurable via `/proc/sys/kernel/keys/persistent_keyring_expiry`).
- Timer resets each time the keyring is accessed.
- Useful if the proxy is not always running, but requires periodic access to prevent expiry.

**Ref:** [persistent-keyring(7)](https://man7.org/linux/man-pages/man7/persistent-keyring.7.html), [user-keyring(7)](https://man7.org/linux/man-pages/man7/user-keyring.7.html), [keyrings(7)](https://man7.org/linux/man-pages/man7/keyrings.7.html)

---

## 2. Trust Domain Separation

### Is the kernel keyring a DIFFERENT trust domain from the filesystem?

**Partially yes, but with caveats.**

**What it provides:**
- Key data lives in **kernel memory**, not on disk. It cannot be read by scanning the filesystem.
- An attacker who compromises only filesystem access (e.g., backup exfiltration, container escape to host filesystem, misconfigured NFS mount) **cannot** read kernel keyring keys.
- `/proc/keys` exposes key **metadata** (type, description, permissions, UID) but **NOT payload data**. The payload can only be read via the `KEYCTL_READ` operation, which requires appropriate permissions and runs through the kernel's access control.

**What it does NOT provide:**
- **Same-UID access is unrestricted by default.** Any process running as the same UID can access keys in `@u` with default permissions (possessor + user permissions are both set).
- If an attacker achieves code execution as the same UID, they can read both Shard A (filesystem) and Shard B (keyring) — the trust domains collapse.

### Permission Model

Key permissions are a 32-bit mask with four 8-bit fields:

```
PPuuggoo
│ │ │ └─ other
│ │ └─── group
│ └───── user (UID match)
└─────── possessor (has key in a searchable keyring)
```

Permission bits per field:
- `0x01` — view (see metadata)
- `0x02` — read (read payload)
- `0x04` — write (update payload)
- `0x08` — search
- `0x10` — link
- `0x20` — setattr

**Default permissions for user-type keys:** `0x3f010000` — possessor gets all permissions, user gets view only. However, processes with the same UID and the user keyring in their searched hierarchy **are** possessors.

### Can you restrict to only the creating process?

**In theory:**

```bash
# Set permissions to possessor-only, no user/group/other access
keyctl setperm <key_id> 0x3f000000
```

**In practice:** This only helps if the key is in a keyring that other processes cannot search. Since `@u` is shared by all processes of the same UID, all same-UID processes are possessors. To truly isolate:

1. Create a new keyring (not linked to `@u` or `@s`).
2. Add the key to that keyring.
3. Set permissions to possessor-only.
4. Only the creating process (and its children via session inheritance) would be possessors.

**Problem:** This makes the key inaccessible after process restart, defeating the persistence requirement.

### `/proc/keys` Exposure

- `/proc/keys` shows key metadata for keys where the reader has `view` permission.
- It does **NOT** expose payload data.
- You can remove view permission from user/group/other to hide the key from `/proc/keys` for non-possessor processes.

**Ref:** [Kernel Key Retention Service](https://docs.kernel.org/security/keys/core.html), [keyctl_setperm(3)](https://man7.org/linux/man-pages/man3/keyctl_setperm.3.html)

---

## 3. Container Behavior

### Docker

**keyctl is BLOCKED by Docker's default seccomp profile.**

Since CVE-2016-0728, the `keyctl` syscall is in Docker's seccomp denylist. The `add_key` and `request_key` syscalls are also blocked.

**Why:** Kernel keyrings are **not namespaced**. Without seccomp blocking, a container could read keys from the host or other containers running as the same UID. The `keyctl-unmask` tool by antitree demonstrated that containers could enumerate and read keys across container boundaries.

**Workaround:** You can allow keyctl by providing a custom seccomp profile:

```json
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "syscalls": [
    {
      "names": ["keyctl", "add_key", "request_key"],
      "action": "SCMP_ACT_ALLOW"
    }
  ]
}
```

But this is a security risk in multi-tenant environments.

**Practical impact for Worthless:** If the proxy runs in a Docker container, kernel keyring is NOT available without explicit seccomp override. This is a **deployment blocker** for Docker-based installations.

### Rootless Podman

Same situation as Docker — default seccomp profile blocks keyctl. Rootless Podman also uses user namespaces, which provides some isolation, but keyctl support requires explicit allowlisting.

### WSL2

**Uncertain/Problematic.** Research findings:

- WSL2 runs a real Linux kernel (Microsoft-maintained fork, currently based on 5.15.x or 6.x).
- `CONFIG_KEYS` is likely enabled in the default WSL2 kernel config, but this is not guaranteed across all WSL2 distributions.
- Reports of keyring issues on Ubuntu 24.04 under WSL2 (kernel 5.15.167.4-1) exist.
- gnome-keyring (userspace) has known issues on WSL2; kernel keyring (syscall-level) is a different subsystem but may also have edge cases.
- The WSL2 kernel can be rebuilt with custom config, but requiring users to do this is impractical.

**Practical impact:** WSL2 support is uncertain and fragile. Cannot be relied upon.

**Ref:** [Docker seccomp profiles](https://docs.docker.com/engine/security/seccomp/), [keyctl-unmask](https://github.com/antitree/keyctl-unmask), [WSL2 keyring issues](https://learn.microsoft.com/en-us/answers/questions/3931834/keyrings-not-working-on-ubuntu-24-04-under-wsl2)

---

## 4. API Access

### Python

| Library | PyPI Package | Status | Notes |
|---------|-------------|--------|-------|
| [sassoftware/python-keyutils](https://github.com/sassoftware/python-keyutils) | `keyutils` | **Archived** (last release 2015) | Cython bindings, works but unmaintained |
| [tuxberlin/python-keyctl](https://github.com/tuxberlin/python-keyctl) | `python-keyctl` | Low activity | Basic management |
| [marcus-h/python-keyring-keyutils](https://github.com/marcus-h/python-keyring-keyutils) | — | Active-ish | Backend for `python-keyring`, higher-level |
| **ctypes (DIY)** | — | Always works | Directly call `libkeyutils.so` |

**Recommended approach for Worthless:** Use `ctypes` to call `libkeyutils.so` directly. This avoids depending on unmaintained packages and gives full control.

```python
import ctypes
import ctypes.util

# Load libkeyutils
_lib = ctypes.CDLL(ctypes.util.find_library("keyutils") or "libkeyutils.so.1")

# Constants
KEY_SPEC_USER_KEYRING = -4  # @u

# add_key(type, description, payload, plen, keyring) -> key_serial_t
_lib.add_key.argtypes = [
    ctypes.c_char_p,  # type
    ctypes.c_char_p,  # description
    ctypes.c_void_p,  # payload
    ctypes.c_size_t,  # plen
    ctypes.c_int32,   # keyring
]
_lib.add_key.restype = ctypes.c_int32

# keyctl_read_alloc(key, buffer_ptr) -> ssize_t
_lib.keyctl_read_alloc.argtypes = [ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p)]
_lib.keyctl_read_alloc.restype = ctypes.c_long

def store_shard(description: bytes, payload: bytes) -> int:
    """Store a shard in the user keyring. Returns key serial number."""
    key_id = _lib.add_key(
        b"user",
        description,
        payload,
        len(payload),
        KEY_SPEC_USER_KEYRING,
    )
    if key_id < 0:
        raise OSError(ctypes.get_errno())
    return key_id

def read_shard(key_id: int) -> bytes:
    """Read a shard from the keyring by key serial number."""
    buf = ctypes.c_void_p()
    length = _lib.keyctl_read_alloc(key_id, ctypes.byref(buf))
    if length < 0:
        raise OSError(ctypes.get_errno())
    result = ctypes.string_at(buf, length)
    # Note: should free(buf) in production
    return result
```

**Alternative:** Use raw syscalls via `ctypes` without `libkeyutils.so`:

```python
import ctypes

# Syscall numbers (x86_64)
SYS_add_key = 248
SYS_request_key = 249
SYS_keyctl = 250

libc = ctypes.CDLL(None)
syscall = libc.syscall
```

### Rust

| Crate | Repo | Status | Notes |
|-------|------|--------|-------|
| [`linux-keyutils`](https://docs.rs/linux-keyutils) | [landhb/linux-keyutils](https://github.com/landhb/linux-keyutils) | Active | Pure syscall interface, no C deps, small footprint. Only depends on `libc` + `bitflags`. |
| [`keyutils`](https://docs.rs/keyutils) | [mathstuf/rust-keyutils](https://github.com/mathstuf/rust-keyutils) | Active | FFI bindings to `libkeyutils`. Requires `libkeyutils-dev`. |
| [`keyring`](https://docs.rs/keyring) | [hwchen/keyring-rs](https://github.com/hwchen/keyring-rs) | Active (v3.6+) | Cross-platform. Uses `linux-keyutils` as optional backend on Linux. |

**Recommended for Worthless:** `linux-keyutils` — no C dependencies, direct syscall interface, well-suited for the reconstruction service (Rust, distroless container).

```rust
use linux_keyutils::{KeyRing, KeyRingIdentifier, KeyType};

// Get the user keyring
let ring = KeyRing::from_special_id(KeyRingIdentifier::User, false)?;

// Add a key
let key = ring.add_key(KeyType::User, "worthless:shard_b", shard_b_bytes)?;

// Read it back
let data = key.read()?;
```

**Ref:** [linux-keyutils docs](https://docs.rs/linux-keyutils/latest/linux_keyutils/), [keyutils docs](https://docs.rs/keyutils/latest/keyutils/)

---

## 5. Practical Limits

| Parameter | Default | Source |
|-----------|---------|--------|
| Max payload per key ("user" type) | **32,767 bytes** | Kernel hard limit |
| Max keys per non-root user | **200** | `/proc/sys/kernel/keys/maxkeys` |
| Max total bytes per non-root user | **20,000 bytes** | `/proc/sys/kernel/keys/maxbytes` |
| Max keys for root | 1,000,000 | `/proc/sys/kernel/keys/root_maxkeys` |
| Max bytes for root | 25,000,000 | `/proc/sys/kernel/keys/root_maxbytes` |
| Persistent keyring default expiry | 3 days (259,200s) | `/proc/sys/kernel/keys/persistent_keyring_expiry` |

**For Worthless:** A single ~50-byte shard fits trivially within all limits. Even with overhead (description string, keyring link = 4 bytes), we would use well under 200 bytes of quota.

**Performance:** `keyctl_read()` is a single syscall. For a 50-byte "user" type key, it is effectively instantaneous — sub-microsecond. No context switch overhead beyond the syscall itself. This is negligible compared to the network round-trip for the upstream LLM call.

**Ref:** [keyrings(7)](https://man7.org/linux/man-pages/man7/keyrings.7.html)

---

## 6. Security Properties

### Memory Protection

- Key payloads are stored in **kernel memory** (ring 0). Not directly accessible from userspace via `/proc/<pid>/mem`.
- Since Linux 4.8, if payloads are large enough to be stored in tmpfs (internal kernel optimization), they are **encrypted before writing to tmpfs**, preventing leakage to swap.
- For our 50-byte payload, storage will be inline in kernel slab memory — **never swapped to disk**.
- `/proc/keys` shows metadata only, never payload data.

### Process Isolation

- **`/proc/<pid>/mem`:** Cannot read kernel memory. Key payloads in kernel slab are inaccessible via ptrace or `/proc/<pid>/mem` even by same-UID processes.
- **Same-UID processes:** CAN read key payloads via `keyctl(KEYCTL_READ)` if they have the key in a searchable keyring and have read permission. The user keyring (`@u`) is shared across all processes of the same UID, so **same-UID processes can read the key by default**.
- **Different-UID processes:** Cannot access unless "other" permissions are set (not set by default).

### `fork()` Behavior

- **Session keyring:** Inherited across `fork()`, `vfork()`, `clone()`, and preserved across `execve()` (even for set-UID binaries).
- **Process keyring:** Replaced with empty one in child (unless `CLONE_THREAD`).
- **Thread keyring:** Destroyed in child.
- **User keyring (`@u`):** Not "inherited" per se — it's shared. Any process with the same UID accesses the same user keyring.

### Key Discoverability

An attacker with same-UID code execution can:

1. List all keys visible to them: `keyctl show @u` or `cat /proc/keys`
2. Find the worthless shard by description: `keyctl search @u user worthless:shard_b`
3. Read the payload: `keyctl read <key_id>` or `keyctl pipe <key_id>`

This is a **realistic attack** if an attacker achieves RCE as the service user.

**Mitigation:** Use the "logon" key type instead of "user". Logon keys:
- Can be created and updated from userspace
- Payload is **only readable from kernel space** (not userspace)
- Cannot be read via `keyctl read`
- Used by filesystem encryption (fscrypt), ecryptfs, etc.

**However:** If the proxy needs to read Shard B from userspace (which it does, to XOR-reconstruct the API key), the "logon" type is not usable. Only kernel consumers (like fscrypt) can read logon keys.

**Ref:** [Kernel Key Retention Service](https://docs.kernel.org/security/keys/core.html), [Cloudflare blog](https://blog.cloudflare.com/the-linux-kernel-key-retention-service-and-why-you-should-use-it-in-your-next-application/)

---

## 7. Recommendation

### Should Worthless use the kernel keyring for Shard B on Linux?

**YES, as an optional elevated-security backend — NOT as the default or only backend.**

### Rationale

**Advantages:**
1. **Different trust domain from filesystem** — An attacker with filesystem-only access (backup theft, NFS misconfiguration, container filesystem escape) cannot read kernel keyring keys. This is a real and meaningful separation.
2. **Not swappable** — 50-byte shard stays in kernel slab memory. No risk of leaking to swap partition or core dumps.
3. **No disk persistence** — If the machine is seized while powered off, Shard B is gone. Encryption at rest is not needed.
4. **Fast** — Single syscall, sub-microsecond for 50 bytes.
5. **No external dependencies** — Available on any Linux 5.15+ with `CONFIG_KEYS` (virtually all distros).
6. **Works headless** — No user interaction needed. Pure syscall, no D-Bus, no desktop session.

**Disadvantages / Limitations:**
1. **Does NOT survive reboot** — Shard B must be re-enrolled after every reboot. This is a significant UX cost for servers that reboot.
2. **Same-UID code execution defeats it** — If an attacker gets RCE as the proxy's UID, they can `keyctl read` the shard. The trust domain separation only holds against filesystem-only attackers.
3. **Docker: blocked by default** — Requires custom seccomp profile. Major deployment friction.
4. **WSL2: uncertain** — May or may not work depending on kernel build.
5. **No macOS/Windows equivalent** — Cannot be the cross-platform solution. Keychain (macOS) and DPAPI (Windows) are different APIs with different semantics.
6. **Reboot recovery requires a fallback** — Need either (a) encrypted-on-disk shard B that gets loaded into keyring on boot, or (b) re-enrollment from cloud/user.

### Recommended Architecture

```
┌─────────────────────────────────────────────┐
│              Shard B Storage                 │
├─────────────────────────────────────────────┤
│                                             │
│  [Primary: Encrypted file]                  │
│   ~/.config/worthless/shard_b.enc           │
│   Encrypted with machine-derived key        │
│   (PBKDF2 of machine-id + user salt)        │
│   Works everywhere. Survives reboot.        │
│                                             │
│  [Elevated: Kernel keyring (Linux only)]    │
│   Loaded from encrypted file at boot/start  │
│   Used for runtime reads (fast, no disk)    │
│   Shard B in kernel memory, not swappable   │
│   Cleared from keyring on daemon shutdown   │
│                                             │
└─────────────────────────────────────────────┘
```

**Flow:**
1. At enrollment: Shard B encrypted to disk AND loaded into kernel keyring.
2. At proxy startup: Load Shard B from encrypted file into kernel keyring. Delete from process memory.
3. At each request: Read from kernel keyring (fast syscall, no disk I/O, no decrypt).
4. At shutdown: Revoke key from keyring (explicit cleanup).
5. After reboot: Proxy startup re-loads from encrypted file.

**This gives us:**
- Reboot survival (encrypted file)
- Runtime protection (kernel memory, not swappable)
- Trust domain separation (filesystem attacker can't read keyring; keyring attacker can't decrypt file without machine key)
- Docker compatibility (fall back to encrypted file only)
- Cross-platform path (macOS uses Keychain, Windows uses DPAPI, Linux uses keyring)

### Keyring Usage Details

| Parameter | Value |
|-----------|-------|
| Key type | `"user"` |
| Description | `"worthless:shard_b:{enrollment_id}"` |
| Target keyring | `@u` (user keyring) for persistence across process restarts |
| Permissions | `0x3f010000` (possessor: all, user: view only) |
| Fallback | Encrypted file if keyring unavailable |

### When to NOT use the keyring

- Docker containers (seccomp blocks it)
- WSL2 (uncertain support)
- Systems where the proxy is frequently stopped and no other processes run as that UID (user keyring may be garbage collected)
- Cross-platform deployments where consistent behavior is required

### Implementation Priority

This should be a **Phase 2 hardening feature**, not a Phase 1/PoC requirement. For the PoC:
- Store Shard B in an encrypted file (different location from Shard A for basic separation)
- Abstract the storage interface so kernel keyring can be plugged in later

```python
# Storage interface (Phase 1)
class ShardStore(Protocol):
    def store(self, enrollment_id: str, shard: bytes) -> None: ...
    def load(self, enrollment_id: str) -> bytes: ...
    def delete(self, enrollment_id: str) -> None: ...

# Phase 1: FileShardStore (encrypted file)
# Phase 2: KeyringShardStore (kernel keyring, Linux only)
# Phase 2: KeychainShardStore (macOS Keychain)
# Phase 2: DPAPIShardStore (Windows DPAPI)
```

---

## Sources

- [keyrings(7) — Linux manual page](https://man7.org/linux/man-pages/man7/keyrings.7.html)
- [user-keyring(7) — Linux manual page](https://man7.org/linux/man-pages/man7/user-keyring.7.html)
- [persistent-keyring(7) — Linux manual page](https://man7.org/linux/man-pages/man7/persistent-keyring.7.html)
- [session-keyring(7) — Linux manual page](https://www.man7.org/linux/man-pages/man7/session-keyring.7.html)
- [Kernel Key Retention Service — Linux kernel docs](https://docs.kernel.org/security/keys/core.html)
- [add_key(2) — Linux manual page](https://man7.org/linux/man-pages/man2/add_key.2.html)
- [keyctl_setperm(3) — Linux manual page](https://man7.org/linux/man-pages/man3/keyctl_setperm.3.html)
- [Docker seccomp security profiles](https://docs.docker.com/engine/security/seccomp/)
- [keyctl-unmask — Container keyring isolation research](https://github.com/antitree/keyctl-unmask)
- [antitree — Keyctl-unmask blog post](https://www.antitree.com/2020/07/keyctl-unmask-going-florida-on-the-state-of-containerizing-linux-keyrings/)
- [WSL2 keyring issues — Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/3931834/keyrings-not-working-on-ubuntu-24-04-under-wsl2)
- [Cloudflare — Linux Kernel Key Retention Service](https://blog.cloudflare.com/the-linux-kernel-key-retention-service-and-why-you-should-use-it-in-your-next-application/)
- [linux-keyutils Rust crate](https://docs.rs/linux-keyutils/latest/linux_keyutils/)
- [keyutils Rust crate](https://docs.rs/keyutils/latest/keyutils/)
- [sassoftware/python-keyutils](https://github.com/sassoftware/python-keyutils)
- [mjg59 — Working with the kernel keyring](https://mjg59.dreamwidth.org/37333.html)
