# Fernet-key hardening — ticket drafts (v1.1 polish + v1.2 epic + v2.0 skeleton)

Drafted while expert-panel context was fresh. File these via Linear API after the 4 must-fix-merge items land.

**Decisions locked:**
- Skip Tier C entirely (hardware-wrap and Python-side separate-UID become obsolete in v2.0)
- Fold C1 (per-request reconstruction zero) into v1.2 as a small child
- v1.2 = comprehensive at-rest + runtime hardening (Tier A + Tier B + C1)
- v2.0 = distributed trust (Rust + Shamir 2-of-3 + MPC + Cloud KMS + separate UID)

---

## v1.1 polish (file in "Worthless v1.1 Release" project)

### TICKET: Honest README/landing framing for v1.1 security claims

> "v1.1 protects against proxy-process compromise, not against any local attacker who can read your filesystem."

**Why:** Brutus's expert review on PR #116 surfaced the framing gap: the shard-separation architecture *looks like* "your master key is hard to steal" but `cat ~/.worthless/fernet.key` bypasses it. v1.1's real user-facing security feature is the **rules engine + spend cap** — even a stolen API key can't burn more than your budget.

**What to change:**
- README hero copy: replace "your keys are split" framing with "your keys can't burn more than your budget" framing
- `docs/security.md`: add a section "v1.1 threat model" explicitly listing what's protected and what's not
- Landing-page copy (worthless.sh): same

**AC:** A reader who skims the README understands: (1) the rules engine is the v1.1 security feature, (2) the master key still exists on disk in v1.1, (3) v2.0 will distribute the key across hosts. No claim that implies unconditional "key is hard to steal."

**Out of scope:** changing actual security behavior. This is a docs-and-marketing ticket only.

---

## v1.2 epic (file in "Worthless v1.2 Hardening" project)

### EPIC: v1.2 Fernet-key hardening — kill at-rest plaintext, split across processes at runtime

> "Make stealing Worthless's master key require root, ptrace, OR compromising both processes simultaneously."

After v1.1 ships the lifecycle plumbing, v1.2 makes the security claims real. Three layers of defense, all surviving the v2.0 Rust rewrite (none of these throw away when v2.0 lands):

1. **At rest**: Fernet key moves from plaintext file → OS keyring (encrypted by user login)
2. **In memory**: mlock + PR_SET_DUMPABLE prevent swap leak and same-UID memory dumps
3. **At runtime**: Fernet key XOR-split across proxy + sidecar processes — single-process compromise of either yields only an XOR mask

**Threat-model delta:**
- Before v1.2: `cat ~/.worthless/fernet.key` wins
- After v1.2: attacker needs (a) root/admin OR (b) compromise BOTH processes simultaneously OR (c) ptrace as your UID (still a real bar — most malware doesn't ship ptrace)

**Children:**
- A1 (OS keyring at rest)
- A2 (mlock memory pages)
- A3 (PR_SET_DUMPABLE + macOS sandbox profile)
- B1 (runtime XOR-split Fernet key across proxy + sidecar)
- B2 (O_TMPFILE for share files)
- C1 (per-request reconstruction zero)

**Out of scope:** Hardware-wrap (Secure Enclave/TPM), separate-UID sidecar — both subsumed by v2.0.

**Promote to v2.0 when:** all 6 children land + the v1.2 readme update reflects the new claims.

---

### CHILD A1: OS keyring at rest

> "`cat ~/.worthless/fernet.key` returns 'no such file'"

**What:** Move `~/.worthless/fernet.key` from plaintext file → OS keyring. Per-OS:
- macOS: Keychain (encrypted by user's login password, auto-unlocked at login)
- Linux: Secret Service via D-Bus (gnome-keyring / KWallet)
- Windows: Credential Manager (future Windows support; not v1.2 blocking)

**Migration:** at boot, detect old `~/.worthless/fernet.key`, move it into the keyring, delete the file. One-time, idempotent.

**Implementation notes:**
- Use the `keyring` Python package (already in worthless's dep tree for tests)
- Keyring service name: `worthless`, account: `fernet-key`
- The `keystore.py` module's `read_fernet_key` / `store_fernet_key` are the only call sites that change

**AC:**
- After boot, `~/.worthless/fernet.key` does not exist (was migrated)
- `worthless lock` and `worthless up` work without prompt after first migration
- First-time keyring access prompts the user (macOS Keychain prompt, GNOME keyring unlock)
- Worktree contains a regression test that `read_fernet_key()` succeeds without any plaintext file present
- Worktree contains a migration test (plant old file, run boot, verify file gone + key in keyring)

**Risks:**
- CI runners often lack a configured keyring → CI tests need a `keyring.backends.fail.Keyring` fallback path that surfaces a clear error
- macOS Keychain prompts can be confusing first time → docs section explaining the prompt

---

### CHILD A2: mlock memory pages

> "Prevents the kernel from paging the Fernet key bytearray to swap"

**What:** Call `mlock(2)` on the Fernet key bytearray's page after allocation. Same for the in-memory shard buffers.

**Implementation:**
- Linux only (macOS `mlock` is advisory per BSD, lacks the lock-into-RAM guarantee)
- Use `ctypes` to call `libc.mlock(addr, len)` directly — no Python wrapper has this
- Address: `id(buf)` doesn't give the underlying buffer address; use `ctypes.cast` on `(ctypes.c_char * len).from_buffer(buf)`
- Must raise `RLIMIT_MEMLOCK` to a value large enough for our buffers (default is often 64KB)
- Best-effort: if `mlock` fails with EPERM (no `CAP_IPC_LOCK` or `RLIMIT_MEMLOCK` too low), log a warning and continue

**AC:**
- Linux: `mincore(2)` confirms the pages are resident-locked
- macOS: skipped with a doc note
- The pages don't appear in `/proc/swaps` even under memory pressure (verified in a stress test that allocates more than RAM)
- No regression on machines without `CAP_IPC_LOCK` (warning logged, key still works)

**Pairs naturally with A1** (after the keyring decrypts the key into a bytearray, immediately mlock that bytearray).

---

### CHILD A3: PR_SET_DUMPABLE + macOS sandbox profile

> "Prevents same-UID processes from reading the sidecar's memory via /proc or task_for_pid"

**What:** Disable the ability of OTHER processes (even same UID) to read the sidecar's memory.

**Linux:**
- Call `prctl(PR_SET_DUMPABLE, 0)` early in the sidecar's main
- Effects: `/proc/<sidecar>/mem` becomes readable only by root; `gcore <sidecar>` from a different process fails; kernel core dumps disabled

**macOS:**
- Use `sandbox_init` with a custom profile that denies `task_for_pid` to other processes
- Profile: deny `(allow process-fork)`, deny `(allow mach-lookup (global-name "com.apple.system_extensions"))`
- Falls back gracefully if `sandbox_init` is unavailable

**AC:**
- Linux: `gcore <sidecar_pid>` from a different process (same UID) fails with EACCES
- macOS: `lldb -p <sidecar_pid>` from another tty fails to attach
- Sidecar's own crash handler still works (`SIGSEGV` is still caught — we're not disabling self-introspection, just other-process introspection)
- No regression on debugging during dev (`worthless up --debug` adds an env var that disables this for local development)

---

### CHILD B1: Runtime XOR-split Fernet key across proxy + sidecar

> "Single-process memory dump of either proxy or sidecar yields only an XOR mask"

**What:** Currently the sidecar holds the whole Fernet key after boot. Change to: split the Fernet key into two XOR shares; proxy holds shard A, sidecar holds shard B; per-request, proxy adds shard A to the IPC payload; sidecar XORs to get the full key, decrypts, returns.

**IPC contract change:**
- Existing reconstruct request frame: `(shard_b_ciphertext, commitment, nonce)`
- New reconstruct request frame: `(shard_b_ciphertext, commitment, nonce, fernet_shard_a)`
- Versioned bump in the IPC envelope (currently v1)

**Implementation:**
- At sidecar boot: read Fernet key from keyring (A1), split via `secrets.token_bytes(N)` mask, send shard A back to the proxy (over the IPC during sidecar's first message), keep shard B locally
- At per-request: proxy ships its shard A in the request envelope; sidecar XORs and decrypts
- Both shards must be mlocked (A2)

**Threat model:**
- Memory dump of proxy: `shard_a` only — random bytes, no info about the Fernet key
- Memory dump of sidecar: `shard_b` only — same
- Both dumps simultaneously: XOR yields the Fernet key

**AC:**
- End-to-end test: dump both processes' memory while a request is in flight (`gcore` on Linux), verify neither dump alone yields a Fernet-key-shaped string
- IPC contract test: the new envelope is parsed correctly and the request flow succeeds
- Performance: per-request overhead < 100µs (one XOR + one Fernet decrypt)

---

### CHILD B2: O_TMPFILE for share files

> "Share files exist as inodes only — no directory entry visible to other processes"

**What:** Replace the boot-time share file creation in `split_to_tmpfs` with `O_TMPFILE`-style anonymous files. The sidecar reads them via fd inheritance, not via path.

**Implementation:**
- Linux: `os.open(parent_dir, os.O_TMPFILE | os.O_RDWR, 0o600)` creates an inode without a directory entry
- macOS: no `O_TMPFILE`; fall back to current behavior (create + immediate unlink with fd held open) — same effective semantics
- Pass the fd to the sidecar via `pass_fds` (already supported by Popen)

**AC:**
- Linux: `ls ~/.worthless/run/<pid>/` shows no `share_*.bin` entries during a session
- `lsof` shows the inodes are held by sidecar fds
- On crash (SIGKILL), the inodes are auto-deleted by the kernel (no orphan files on next boot)
- macOS: documented to not support O_TMPFILE; current unlink-on-create behavior preserved

**Pairs with B1** (the share files only exist briefly at boot to hand the shards across the spawn boundary; once both processes have their shards in memory, the files can be unlinked even sooner).

---

### CHILD C1: Per-request reconstruction zero

> "Reconstructed Fernet key bytearray is zeroed within microseconds of the decrypt completing, not session-lifetime"

**What:** Today the sidecar reconstructs the Fernet key once at boot and holds it for the session. With B1 in place, reconstruction happens per-request (proxy ships shard A, sidecar XORs with shard B). Make sure the reconstructed key bytearray lives only for the duration of the Fernet decrypt — zeroed immediately after the decrypt returns.

**Implementation:**
- In the sidecar's reconstruct flow, the reconstructed key is a local bytearray
- Wrap the Fernet decrypt in `try: ... finally: zero_buf(reconstructed_key)`
- Window: from the XOR completion to `zero_buf` return — microseconds

**AC:**
- Instrumented test: log the wallclock between reconstruct + zero. Median < 1ms, p99 < 10ms.
- Memory dump taken between requests: no Fernet-key-shaped bytes in the sidecar's heap

**Note:** before B1, this ticket is meaningless (the key is held for the whole session). After B1, it's the natural completion of the per-request reconstruction model.

---

## v2.0 epic (file in "Worthless v2.0 Harden" project)

### EPIC: v2.0 Distributed trust — Rust sidecar + Shamir 2-of-3 + MPC reconstruct + Cloud KMS

> "An attacker compromising your laptop alone — even with root — cannot reconstruct an API key without also compromising the cloud KMS."

**The v2.0 architecture:**

```
                    v1.x architecture                  v2.0 architecture
─────────────────────────────────────────  ──────────────────────────────────────
  Local laptop:                              Local laptop:
    proxy (Python)                             proxy (Python or Go)
    sidecar (Python)                           Rust sidecar — minimal, audited
      ↓                                          ↓
    Fernet key in OS keyring                  Shamir share #1 (2-of-3)
    XOR-split across processes (v1.2)            ↓
                                              Local mlock + sandboxed UID

                                            Cloud KMS (worthless-cloud or BYOK):
                                              Shamir share #2
                                              MPC-aware decrypt service

                                            Optional 3rd party (recovery):
                                              Shamir share #3
                                              user's phone / hardware key / co-host
```

**Cryptographic upgrades:**

| v1.x primitive | v2.0 primitive |
|---|---|
| 2-of-2 XOR (proxy + sidecar, both required) | **Shamir 2-of-3** (any 2 of 3 reconstruct, 1 yields nothing) |
| Reconstruction = local computation (sidecar XORs) | **MPC reconstruction** (no party sees the full key) |
| Trust model: 1 host (your laptop) | **Trust model: 2+ hosts** (laptop + KMS, optionally + recovery) |
| Survives: proxy compromise | **Survives**: total laptop compromise alone, total KMS compromise alone |

**Children (sketch only — full ticket bodies after v1.2 lands):**

- v2.0-1: Rust sidecar rewrite (small TCB, memory-safe, ~1000 LOC target)
- v2.0-2: Shamir 2-of-3 secret sharing (replace XOR; migration path from v1.x XOR shares)
- v2.0-3: Cloud KMS share custody (worthless-cloud-hosted or BYOK Vault/AWS KMS)
- v2.0-4: MPC reconstruct protocol (no single party sees the full key during decrypt)
- v2.0-5: Recovery share UX (phone enrollment, hardware key fallback)
- v2.0-6: Separate-UID enforcement (sidecar runs as `worthless-sidecar` UID; proxy as user; SO_PEERCRED becomes meaningful)
- v2.0-7: Migration from v1.x → v2.0 (one-time re-enrollment, no key loss)

**Pre-v2.0 dependencies:**
- v1.1 (lifecycle plumbing — done)
- v1.2 (at-rest encryption + runtime split — children above)

The IPC contract from WOR-309/WOR-384 + v1.2-B1 (4-field reconstruct envelope) is the through-line. Rust sidecar in v2.0 implements the same envelope; proxy code doesn't change.

**Out of scope (defer further):**
- HSM-only deployments (FIPS / regulated environments) — v2.x or v3.0
- Multi-tenant cloud KMS (different teams' shares isolated) — v2.x
- M-of-N for N > 3 (4-of-5, 5-of-7) — v2.x

---

## Filing plan

**Order of creation** (parents before children for the parentId references):

1. v1.1: Honest README framing — single ticket
2. v1.2 epic — parent
3. v1.2 children: A1, A2, A3, B1, B2, C1 — each with `parentId = v1.2 epic`
4. v2.0 epic — parent
5. (v2.0 children: deferred until v1.2 lands; just file the epic skeleton)

**Linear projects to use:**
- "Worthless v1.1 Release" for the framing ticket
- "Worthless v1.2 Hardening" for the epic + 6 children
- "Worthless v2.0 Harden" for the epic skeleton

**Linear team:** worthless (single team)

**Labels:**
- All v1.2 tickets: `v1.2`, `security`
- v2.0 epic: `v2.0`, `security`, `architecture`
- v1.1 framing: `v1.1`, `docs`

**Priority:**
- v1.1 framing: 2 (high — ships with v1.1)
- v1.2 epic + children: 2 (high — security)
- v2.0 epic skeleton: 3 (medium — long lead)

After v1.2 ships, we delete this file (per `feedback_no_plan_md_artifacts`). Linear becomes the source of truth.
