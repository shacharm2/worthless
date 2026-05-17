# Process-Level Isolation Without Root/Sudo

## Research Goal

Determine the minimum set of self-hardening a Rust sidecar can apply — without root, without special setup — to make its memory a credible separate trust domain from the filesystem shard. The attacker model: a compromised process running as the same UID on the same machine.

---

## 1. `prctl(PR_SET_DUMPABLE, 0)` (Linux)

### What It Does

Sets the process's "dumpable" attribute to 0. This flag controls:
- Whether the kernel writes core dumps for this process
- Whether `/proc/<pid>/mem`, `/proc/<pid>/maps`, `/proc/<pid>/environ` are readable by non-root
- Whether `ptrace(PTRACE_ATTACH)` is permitted by non-root processes

### Protection Against Same-UID Attacker

| Attack Vector | Protected? | Notes |
|---|---|---|
| Read `/proc/<pid>/mem` | **YES** | Kernel checks dumpable flag; returns EPERM if 0 |
| Read `/proc/<pid>/maps` | **YES** | Same check — ownership changes to root:root when dumpable=0 |
| Read `/proc/<pid>/environ` | **YES** | Same mechanism |
| `ptrace(PTRACE_ATTACH)` | **YES** | ptrace checks dumpable; non-root gets EPERM |
| `process_vm_readv()` | **YES** | Uses the same `ptrace_may_access()` kernel check internally |
| Core dump analysis | **YES** | No core dump is written |

### Kernel Version Requirement

- `PR_SET_DUMPABLE` exists since Linux 2.3.20 (1999). Universally available.
- The `/proc` ownership change (root:root) behavior was solidified in Linux 3.4+.

### Survives `fork()`?

**YES** — the dumpable attribute is inherited by child processes. However, it is **reset to 1** on `execve()` of a non-setuid binary. The sidecar must call `prctl(PR_SET_DUMPABLE, 0)` early in its own `main()`, not rely on the parent setting it.

### Works Without Root?

**YES** — any process can reduce its own dumpable flag. This is a self-hardening operation.

### Key Caveat

A process with `CAP_SYS_PTRACE` can bypass this. Root can always read. But our threat model is same-UID unprivileged — and against that, `PR_SET_DUMPABLE=0` is highly effective.

---

## 2. User Namespaces (`CLONE_NEWUSER`) Without Root

### What It Does

Creates a new user namespace where the process gets UID 0 (inside the namespace) mapped to its real UID (outside). This is the gateway to other unprivileged namespace types (mount, PID, network).

### Unprivileged Creation

**YES** — unprivileged user namespace creation has been supported since:
- Linux 3.8 (2013) — initial support
- Enabled by default on most distros

**BUT**: Some distros restrict it:
- **Debian 11+**: Enabled by default (`kernel.unprivileged_userns_clone = 1`)
- **Ubuntu 22.04/24.04**: Enabled by default
- **RHEL/CentOS 7**: Disabled by default (requires `echo 1 > /proc/sys/user/max_user_namespaces`)
- **Fedora 38+**: Enabled by default
- **Arch Linux**: Enabled by default
- **Alpine**: Enabled by default

### Memory Isolation

**NO** — user namespaces do NOT provide memory isolation by themselves. A process outside the namespace with the same real UID can still read `/proc/<pid>/mem` IF the dumpable flag is 1. The namespace changes the apparent UID inside, but the kernel's access checks for `/proc` use the real credentials.

However, user namespaces DO enable:
- Mount namespaces (hide the shard file)
- PID namespaces (hide from `ps`, but `/proc/<pid>` on the host still exists)
- Network namespaces (restrict to a Unix socket)

### Protection Against Same-UID Attacker

| Attack Vector | Protected? | Notes |
|---|---|---|
| Read `/proc/<pid>/mem` | **NO** | Unless combined with `PR_SET_DUMPABLE=0` |
| `ptrace` | **NO** | Unless Yama or dumpable blocks it |
| Read shard file on disk | **YES** | If combined with mount namespace, the file is hidden |
| Network sniffing | **YES** | If combined with network namespace |

### Docker Implications

Docker containers already run in user namespaces (when `userns-remap` is enabled). Running `CLONE_NEWUSER` inside a container works on most configurations but may fail if:
- The container runtime restricts it via seccomp (Docker's default seccomp profile allows `clone` with `CLONE_NEWUSER` since Docker 20.10+)
- `sysctl kernel.unprivileged_userns_clone=0` is set on the host

### Verdict

User namespaces are a **nice-to-have** for hiding the shard file (mount namespace) but are **not reliable for memory isolation** and **not portable** (may be disabled). Do not depend on this.

---

## 3. seccomp-BPF Self-Sandboxing

### What It Does

The process installs a BPF filter that restricts which syscalls IT can make. After initialization (loading the shard, opening the Unix socket), the sidecar could block:
- `open()` / `openat()` (except the socket fd)
- `execve()` (no shell escape)
- `ptrace()` (can't be used as a springboard)
- `socket()` (no new network connections)

### Does It Prevent Other Processes From Attacking the Sidecar?

**NO.** seccomp-BPF only restricts the process that installed it. It is a self-restriction mechanism. A same-UID attacker process is NOT affected by the sidecar's seccomp filter.

However, seccomp is still valuable for **defense in depth**: if the sidecar is exploited (e.g., via a malicious request over the socket), the attacker's shellcode can't:
- Open files to exfiltrate the shard
- Spawn shells
- Make network connections
- ptrace other processes

### Kernel Version Requirement

- seccomp-BPF: Linux 3.5+ (2012)
- `SECCOMP_SET_MODE_FILTER`: Linux 3.17+ for the `seccomp()` syscall (preferred over `prctl`)
- The process must have `PR_SET_NO_NEW_PRIVS` set before installing a filter (unprivileged, self-setting)

### Works Without Root?

**YES** — after setting `PR_SET_NO_NEW_PRIVS`, any process can install seccomp filters on itself.

### Survives `fork()`?

**YES** — seccomp filters are inherited across `fork()` and preserved across `execve()`. They CANNOT be removed once installed.

---

## 4. Mount Namespaces Without Root

### What It Does

`unshare(CLONE_NEWNS)` creates a new mount namespace. Inside it, the process can:
- Bind-mount files to different locations
- Unmount filesystems
- Mount tmpfs
- Effectively hide or replace files from its own view

### Without Root?

**Requires `CLONE_NEWUSER` first.** You must create a user namespace (to gain "root" inside it), then create a mount namespace. The sequence:

```
CLONE_NEWUSER → CLONE_NEWNS → mount/unmount operations
```

This works on distros that allow unprivileged user namespaces (see section 2).

### Can the Sidecar Hide Its Shard File?

**Sort of.** The sidecar can:
1. Read the shard into memory
2. Enter a new user+mount namespace
3. Unmount or overlay the shard file location

But this only hides the file from the sidecar's own view (and its children). Other processes in the original mount namespace can still see and read the shard file.

**To hide from others:** The sidecar would need to delete the shard file after reading it. But that defeats persistence (the shard needs to survive restarts).

### Practical Value

**Low for our use case.** The shard file on disk is not the primary concern — the Python CLI holds Shard A, the sidecar holds Shard B, and neither alone is useful. Mount namespaces add complexity without meaningful security gain.

---

## 5. Yama LSM (`/proc/sys/kernel/yama/ptrace_scope`)

### What It Does

Yama is a Linux Security Module that restricts `ptrace()` beyond the standard DAC (discretionary access control) checks. It has four levels:

| Level | Name | Restriction |
|---|---|---|
| 0 | Classic | Standard DAC: same-UID can ptrace |
| 1 | Restricted | Only a direct parent can ptrace its children (or processes that explicitly opt in via `PR_SET_PTRACER`) |
| 2 | Admin-only | Only processes with `CAP_SYS_PTRACE` can ptrace |
| 3 | No ptrace | Nobody can ptrace anything, including root |

### Default Values by Distro

| Distro | Default Yama Level | Source |
|---|---|---|
| **Ubuntu 22.04** | **1** (restricted) | Enabled since Ubuntu 10.10 |
| **Ubuntu 24.04** | **1** (restricted) | Same |
| **Debian 11 (Bullseye)** | **1** (restricted) | Default since Debian 10 |
| **Debian 12 (Bookworm)** | **1** (restricted) | Same |
| **Fedora 38+** | **1** (restricted) | Default |
| **RHEL 8/9** | **1** (restricted) | Default |
| **Arch Linux** | **1** (restricted) | Default |
| **Alpine** | **Yama not enabled** | Minimal kernel config |
| **openSUSE** | **1** (restricted) | Default |

### Protection at Level 1 (the common case)

At level 1, a same-UID process that is NOT the parent of the sidecar CANNOT:
- `ptrace(PTRACE_ATTACH)` the sidecar → **EPERM**
- `ptrace(PTRACE_SEIZE)` the sidecar → **EPERM**

A same-UID process CAN still:
- Read `/proc/<pid>/mem` → **YES, if dumpable=1** (Yama only restricts ptrace, not /proc file access)
- Use `process_vm_readv()` → **YES, if dumpable=1** (uses `ptrace_may_access()` which checks dumpable but NOT Yama... actually, this is subtle — see section 6)

### Is Yama Level 1 Sufficient Alone?

**NO.** Yama level 1 blocks `ptrace` from non-parents, but does NOT block:
- `/proc/<pid>/mem` reads (governed by dumpable flag, not Yama)
- `process_vm_readv()` (governed by `ptrace_may_access()` which checks dumpable but Yama's influence on this is implementation-dependent)

Yama must be combined with `PR_SET_DUMPABLE=0` for comprehensive protection.

---

## 6. Combined: `PR_SET_DUMPABLE=0` + Yama Level 1

This is the **key combination** for Linux. Let's analyze every attack vector:

### Attack Vector Analysis

| Attack Vector | Blocked By | Result |
|---|---|---|
| `ptrace(PTRACE_ATTACH)` | Both (Yama blocks non-parent; dumpable=0 blocks everyone) | **BLOCKED** |
| Read `/proc/<pid>/mem` | `PR_SET_DUMPABLE=0` (file ownership becomes root:root) | **BLOCKED** |
| Read `/proc/<pid>/maps` | `PR_SET_DUMPABLE=0` (same ownership change) | **BLOCKED** |
| Read `/proc/<pid>/environ` | `PR_SET_DUMPABLE=0` (same) | **BLOCKED** |
| `process_vm_readv()` | `PR_SET_DUMPABLE=0` (calls `ptrace_may_access()` internally, which checks dumpable) | **BLOCKED** |
| Core dump analysis | `PR_SET_DUMPABLE=0` (no core dump written) | **BLOCKED** |
| `/proc/<pid>/cmdline` | **NOT BLOCKED** — always world-readable | Ensure no secrets in argv |
| `/proc/<pid>/fd/` | `PR_SET_DUMPABLE=0` (directory becomes root:root) | **BLOCKED** |
| `/proc/<pid>/exe` | Always readable (symlink to binary) | Not sensitive |
| `kill()` signals | **NOT BLOCKED** — same UID can signal | DoS possible, not data exfil |
| Read shard file on disk | **NOT BLOCKED** — filesystem permissions | Must be 0600 and ideally deleted after read |

### `process_vm_readv()` Deep Dive

The `process_vm_readv()` syscall was added in Linux 3.2. Its permission check:

```c
// kernel source: mm/process_vm_access.c
if (!ptrace_may_access(task, PTRACE_MODE_ATTACH_REALCREDS))
    return -EPERM;
```

`ptrace_may_access()` checks:
1. If the target is dumpable=0 and the caller lacks `CAP_SYS_PTRACE` → **EPERM**
2. Yama restrictions (if enabled and applicable)

So **`PR_SET_DUMPABLE=0` alone blocks `process_vm_readv()` from unprivileged same-UID processes.**

### What Can a Same-UID Attacker Actually Do?

With both protections in place:
- **Read memory:** NO
- **Read /proc files:** NO (except cmdline, exe, status — non-sensitive metadata)
- **Attach debugger:** NO
- **Send signals (SIGKILL):** YES — DoS only, no data extraction
- **Read the shard file:** YES, if it still exists on disk with same-UID permissions
- **Impersonate the Unix socket:** YES, if they know the path (bind before the sidecar starts)

### Remaining Risks

1. **DoS via signals** — The attacker can kill the sidecar but cannot extract secrets. Acceptable.
2. **Shard file on disk** — The file must be `chmod 0600`. Consider deleting after loading into memory (but then restarts require re-enrollment). Alternative: `memfd_create()` as ephemeral storage.
3. **Socket hijacking** — Use abstract Unix sockets with a random name, or verify the peer's PID via `SO_PEERCRED`.
4. **`/proc/<pid>/cmdline`** — Never pass secrets as command-line arguments. The sidecar receives the shard path, not the shard content, via argv.
5. **SCM_RIGHTS fd passing** — If a same-UID process has access to the socket, it could potentially interact with the sidecar. Auth the socket connection.

---

## 7. macOS Equivalents

### `PT_DENY_ATTACH`

**What it does:** A ptrace request (`ptrace(PT_DENY_ATTACH, 0, 0, 0)`) that prevents any debugger from attaching to the process. If a debugger tries to attach, the target process is killed instead.

| Property | Value |
|---|---|
| Works without root | **YES** — self-hardening |
| Blocks ptrace | **YES** — process is killed if ptrace attach is attempted |
| Blocks `task_for_pid()` | **YES** — on modern macOS (10.11+), `task_for_pid()` requires the caller to have the `com.apple.security.cs.debugger` entitlement OR root |
| Blocks same-UID memory reads | **Partially** — `task_for_pid()` is the macOS equivalent of `/proc/pid/mem` |
| Survives fork | **NO** — must be called in each process |
| Kernel version | Available since macOS 10.5 |

### `task_for_pid()` Protection (macOS)

On macOS, reading another process's memory requires calling `task_for_pid()` to get a Mach task port. This is restricted:

- Since **macOS 10.11 (El Capitan)** with SIP enabled: `task_for_pid()` requires:
  - Root, OR
  - `com.apple.security.cs.debugger` entitlement (only Apple-signed debuggers), OR
  - The target has the `com.apple.security.get-task-allow` entitlement (debug builds only)

- **For unsigned binaries** (our sidecar): SIP does NOT grant extra protection to unsigned binaries, but `task_for_pid()` still requires the caller to be root or have the debugger entitlement. Regular same-UID processes CANNOT call `task_for_pid()` on macOS 10.11+.

### `sandbox-exec` (macOS Seatbelt)

**What it does:** macOS's sandbox framework. Can restrict file access, network, IPC, etc.

| Property | Value |
|---|---|
| Works without root | **YES** |
| API | `sandbox_init()` C API, or `sandbox-exec` CLI |
| Can restrict the sidecar | **YES** — restrict file reads, network, etc. |
| Can prevent others from reading sidecar memory | **NO** — sandbox restricts the sandboxed process, not others |
| Deprecated? | `sandbox-exec` CLI deprecated since macOS 10.15, but `sandbox_init()` API still functional. Apple's Sandbox framework (App Sandbox via entitlements) is the replacement |

### macOS Summary

macOS provides **strong default protection** against same-UID memory reads:
- `task_for_pid()` is restricted to root/entitled processes since macOS 10.11
- `PT_DENY_ATTACH` adds an extra layer (kills the process if debugger attaches)
- No equivalent of Linux's `/proc/<pid>/mem` file

**Recommended macOS hardening:**
1. Call `ptrace(PT_DENY_ATTACH, 0, 0, 0)` at startup
2. That's essentially sufficient — macOS's default protections are strong

### `posix_spawn` with `POSIX_SPAWN_DISABLE_ASLR`

This is **irrelevant** — it disables ASLR (weakens security). Not useful for our purposes. Including it here only to note it should NOT be used.

---

## 8. Windows Equivalents

### Process Protection Levels (PPL)

Windows has Protected Process Light (PPL) since Windows 8.1. However:

| Property | Value |
|---|---|
| Works without admin | **NO** — PPL requires a Microsoft-signed binary with specific EKU certificates |
| Works for third-party code | **NO** — only Microsoft/antivirus vendors |
| Relevant? | **NO** |

### Access Control on Process Objects

On Windows, every process has a security descriptor. By default, a process running as a user can open handles to other processes running as the same user with `PROCESS_VM_READ` access.

**However**, a process CAN modify its own DACL (discretionary access control list):

```c
// Pseudocode — the sidecar can deny PROCESS_VM_READ to Everyone
SetSecurityInfo(GetCurrentProcess(), DACL_SECURITY_INFORMATION, restrictive_dacl);
```

| Property | Value |
|---|---|
| Works without admin | **YES** — any process can restrict its own DACL |
| Blocks `ReadProcessMemory()` | **YES** — if DACL denies `PROCESS_VM_READ` |
| Blocks `OpenProcess()` | **YES** — handle creation is checked against DACL |
| Survives `CreateProcess()` | **NO** — child gets default DACL; must self-harden |
| Can be reversed? | **YES** — if attacker can get `WRITE_DAC` access first (race condition at startup) |

### Job Objects

Windows Job Objects can restrict child processes, but they don't prevent same-user memory reads on the job's processes. Not directly useful.

### Windows Summary

**Recommended Windows hardening:**
1. Set a restrictive DACL on the process handle at startup, denying `PROCESS_ALL_ACCESS` to `Everyone` and granting only to `SYSTEM` and the current user's logon SID (or just `SYSTEM`)
2. This blocks `OpenProcess()` + `ReadProcessMemory()` from same-user processes
3. **Race condition risk:** There's a window between process start and DACL modification. Mitigate by having the parent (Python CLI) create the process in a suspended state, set the DACL, then resume.

---

## Summary Matrix

| Mechanism | Platform | Root Required | Blocks Same-UID Memory Read | Blocks Same-UID ptrace/debug | Reliable | Portable |
|---|---|---|---|---|---|---|
| `PR_SET_DUMPABLE=0` | Linux | NO | **YES** | **YES** | **YES** | Linux only |
| Yama level 1 | Linux | NO (default) | No (only ptrace) | **YES** (non-parent) | YES (default on major distros) | Linux only |
| User namespaces | Linux | NO (usually) | No | No | **NO** (may be disabled) | Linux only |
| Mount namespaces | Linux | NO (with userns) | No | No | **NO** (depends on userns) | Linux only |
| seccomp-BPF | Linux | NO | No (self only) | No (self only) | YES | Linux only |
| `PT_DENY_ATTACH` | macOS | NO | Partial | **YES** | YES | macOS only |
| `task_for_pid` restrictions | macOS | NO (default) | **YES** | **YES** | **YES** (since 10.11) | macOS only |
| Process DACL | Windows | NO | **YES** | **YES** | YES (race risk) | Windows only |
| PPL | Windows | **YES** (MS-signed) | YES | YES | YES | Windows only |

---

## RECOMMENDATION: Minimum Self-Hardening Per Platform

### Linux (minimum, always apply)

```rust
// 1. MUST: Set non-dumpable immediately on startup (before loading secrets)
prctl(PR_SET_DUMPABLE, 0);

// 2. MUST: Verify it took effect
assert!(prctl(PR_GET_DUMPABLE) == 0);

// 3. SHOULD: Install seccomp-BPF after initialization
//    - Allow: read/write on existing fds, mmap, mprotect, sigaction,
//      rt_sigreturn, exit_group, clock_gettime, getrandom
//    - Block: open/openat, socket, execve, ptrace, fork, clone
//    This prevents exploitation of the sidecar from being leveraged.

// 4. SHOULD: Lock memory pages containing the shard
mlock(shard_ptr, shard_len);  // prevent swap-out
madvise(shard_ptr, shard_len, MADV_DONTDUMP);  // extra insurance vs core dumps

// 5. NICE-TO-HAVE: If user namespaces are available, enter one
//    (but do NOT depend on this — detect and skip if unavailable)
```

**Rationale:** `PR_SET_DUMPABLE=0` alone blocks all same-UID memory reads AND ptrace on every mainstream Linux kernel since 2.6+. Combined with Yama level 1 (default on Ubuntu/Debian/Fedora/RHEL), this provides defense in depth. seccomp is not for protecting FROM attackers but for limiting damage IF the sidecar itself is compromised.

**What this gives you against a same-UID attacker:**
- Cannot read memory via `/proc/<pid>/mem` (**blocked**)
- Cannot read memory via `process_vm_readv()` (**blocked**)
- Cannot attach a debugger via `ptrace` (**blocked** by both dumpable and Yama)
- Cannot read `/proc/<pid>/maps` or `/proc/<pid>/environ` (**blocked**)
- CAN send signals (DoS only — acceptable)
- CAN read shard file on disk if permissions allow (mitigate with 0600 + consider ephemeral storage)

### macOS (minimum, always apply)

```rust
// 1. MUST: Deny debugger attachment
ptrace(PT_DENY_ATTACH, 0, std::ptr::null_mut(), 0);

// 2. SHOULD: Lock memory pages
mlock(shard_ptr, shard_len);

// 3. NICE-TO-HAVE: Use mmap with MAP_PRIVATE for shard storage
```

**Rationale:** macOS already restricts `task_for_pid()` to root/entitled processes since El Capitan (10.11, released 2015). `PT_DENY_ATTACH` adds an extra kill-on-debug-attempt guarantee. This is sufficient for same-UID protection.

### Windows (minimum, always apply)

```rust
// 1. MUST: Restrict process DACL immediately at startup
//    Deny PROCESS_ALL_ACCESS to Everyone
//    Allow only SYSTEM and the specific logon session SID
SetSecurityInfo(GetCurrentProcess(), ...);

// 2. SHOULD: Create the sidecar process suspended, set DACL, then resume
//    (eliminates the race window)

// 3. SHOULD: Use VirtualLock() on shard memory pages
VirtualLock(shard_ptr, shard_len);

// 4. NICE-TO-HAVE: Use a named mutex/event with restricted DACL for IPC
```

### Cross-Platform Priority

| Priority | Action | Linux | macOS | Windows |
|---|---|---|---|---|
| P0 (must ship) | Block same-UID memory reads | `PR_SET_DUMPABLE=0` | `PT_DENY_ATTACH` | Restrictive DACL |
| P0 (must ship) | Lock shard in memory | `mlock()` + `MADV_DONTDUMP` | `mlock()` | `VirtualLock()` |
| P1 (should ship) | Self-sandbox | seccomp-BPF | (macOS sandbox deprecated) | (limited options) |
| P2 (nice to have) | Namespace isolation | `CLONE_NEWUSER` | N/A | N/A |

### Detection and Graceful Degradation

The sidecar should:
1. **Always** apply P0 hardening — these APIs are universally available without root
2. **Detect** P1/P2 availability at runtime and apply if possible
3. **Log** which hardening mechanisms were applied (to stderr, at startup)
4. **Never fail to start** because an optional hardening mechanism is unavailable
5. **Warn** if running on a system with Yama level 0 (rare, but possible on Alpine/custom kernels)

### The Security Claim

With P0 hardening applied, the Worthless sidecar can credibly claim:

> "A compromised process running as the same user CANNOT read the sidecar's memory or extract the shard from the running process. The shard on disk is protected by file permissions (0600). The shard in memory is protected by OS-level process isolation mechanisms that do not require root or elevated privileges."

This claim holds on:
- Linux 3.4+ with default Yama (Ubuntu, Debian, Fedora, RHEL, Arch, openSUSE) — the vast majority of deployments
- macOS 10.11+ (El Capitan, 2015) — all supported macOS versions
- Windows 8.1+ — with race-condition caveat mitigated by suspended-start pattern

The claim does NOT hold against:
- An attacker with root/admin
- An attacker with `CAP_SYS_PTRACE`
- Kernel exploits
- Physical access / cold boot attacks
- Side-channel attacks (timing, cache, speculative execution)

These are explicitly out of scope for the PoC. The threat model is: "your Python AI agent got prompt-injected and is now trying to steal your API key from a different process."
