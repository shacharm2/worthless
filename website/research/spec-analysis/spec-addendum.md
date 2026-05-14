# Sidecar Architecture Spec — Addendum

**Date:** 2026-04-05
**Status:** Amendments to sidecar-architecture-spec.md based on research gaps and codebase analysis

These items are ADDITIONS to the spec. They don't change the architecture — they close gaps the spec left open.

---

## A1. Security Tiers (spec gap: no per-platform honesty)

The spec presents all platforms as having equal "trust domain separation." They don't.

### Add to Section 11 (Security Properties):

**Tier 1 — Three real trust domains:**
macOS (Keychain + Secure Enclave), Kubernetes (KMS-backed secrets), Docker multi-container

*Claim: "Stealing one shard reveals zero information. Reconstruction requires breaching two independent security boundaries."*

**Tier 2 — Two-and-a-half trust domains:**
Windows (DPAPI), Linux desktop (kernel keyring + PR_SET_DUMPABLE), WSL2

*Claim: "Shards are in separate access-control domains. A non-root attacker must defeat process isolation to reach the second shard."*

**Tier 3 — Two trust domains:**
Linux headless (encrypted file fallback), PaaS (env vars), CI runners

*Claim: "The key is split and exposure is limited to microseconds. Defense-in-depth encryption on Shard B raises the bar. An attacker with runtime process access can reach both shards."*

### Implementation:
- `worthless status` displays detected tier
- SECURITY_POSTURE.md documents per-tier honest claims
- Never market "three trust domains" without the platform qualifier

---

## A2. Encrypted File Fallback (spec gap: SeparatePathFileStore is plaintext)

The spec's last-resort fallback stores Shard B as a plain file at a different path. This is the weakest option the research evaluated.

### Replace SeparatePathFileStore with EncryptedFileStore:

```
Storage: ~/.local/share/worthless/shard_b/<alias>.enc
Encryption: AES-256-GCM
Key derivation: Argon2id(
    password = /etc/machine-id || hostname || install-time-salt,
    salt = per-alias random salt (stored alongside ciphertext),
    time_cost = 3, mem_cost = 64MB, parallelism = 1
)
```

**What it protects against:** Offline disk theft, backup exfiltration, accidental exposure in file listings.

**What it doesn't protect against:** Same-machine attacker who can read `/etc/machine-id` and re-derive the key. This is honest Tier 3.

**Optional upgrade:** `worthless enroll --passphrase` derives from a user-provided passphrase instead of machine entropy. Upgrades headless Linux to Tier 2 (passphrase is a separate trust domain). Prompted once at enrollment, never stored.

---

## A3. Kernel Keyring Reboot Strategy (spec open decision #2 — resolved)

Linux kernel keyring (`@u`) doesn't survive reboot.

### Strategy: Two-layer storage

1. **Persistent layer:** EncryptedFileStore (A2 above) — survives reboot
2. **Runtime layer:** Kernel keyring (`@u`) — runtime cache, faster access

### Flow:
- At enrollment: store Shard B in both encrypted file AND kernel keyring
- At sidecar startup: if keyring is empty (post-reboot), re-inject from encrypted file
- Per-request: read from kernel keyring (fast, kernel memory)
- The encrypted file is the source of truth. The keyring is a performance cache.

### Why both:
- Keyring: separate access path from filesystem (kernel-mediated), faster reads
- Encrypted file: survives reboot, portable across sessions

---

## A4. SHA-256 Integrity Check (spec gap: no shard verification)

Shamir reconstruction doesn't inherently detect corruption — if a shard has bit-flipped, you get a wrong key silently.

### Add to enrollment:
```
key_hash = SHA-256(original_api_key)
Store key_hash alongside Shard A metadata (filesystem, not secret)
```

### Add to reconstruction:
```
reconstructed_key = shamir_reconstruct(shard_a, shard_b)
if SHA-256(reconstructed_key) != stored_hash:
    return error("shard_integrity_failed")
```

### Properties:
- The hash reveals nothing about the key (SHA-256 is preimage-resistant)
- Catches: bit rot, truncated shards, wrong alias, tampered shards
- Cost: one hash per reconstruction (~1µs)
- The hash is NOT secret — it can live alongside Shard A metadata

---

## A5. Unix Socket Peer Authentication (spec gap: no IPC auth)

Any same-UID process can connect to the Unix socket and request key reconstruction.

### Add to sidecar socket handler:
```rust
// Linux: SO_PEERCRED — get PID, UID, GID of connecting process
let cred = socket.peer_cred()?;

// Verify UID matches sidecar's own UID
if cred.uid != getuid() {
    return Err("unauthorized: UID mismatch");
}

// Optional (Phase 5 hardening): verify PID is the expected proxy process
// Store expected PID at sidecar startup, check on each connection
```

### Properties:
- Blocks cross-user connections (defense-in-depth alongside socket file permissions)
- PID verification blocks rogue same-UID processes (but PID can be recycled — use with caution)
- `SO_PEERCRED` is Linux-specific. macOS uses `LOCAL_PEERCRED`. Windows named pipes have built-in SID checking.

---

## A6. Existing Features Not in Spec (codebase analysis)

These features exist in the current codebase and must survive the migration. The spec doesn't mention them.

### `worthless unlock` command
Restores original API keys from shards back into `.env`. In new architecture: sends vault-mode request to sidecar, writes key back to `.env`, removes decoy.

### Decoy key system
`make_decoy()`, `decoy_hash()`, `WRTLS` prefix. Replaces real keys in `.env` with realistic-looking fakes. Orthogonal to Shamir — works the same way.

### `worthless lock --env .env` batch auto-scan
Scans `.env` for all API keys, enrolls them all non-interactively. The spec's `worthless enroll <alias>` is per-key interactive. Implementation must provide a batch wrapper:
```bash
worthless lock --env .env
# Equivalent to: for each key found, run enroll, collect all Shard C backups, print at end
```

### Port default
Current code uses 8787. Spec says 9191. **Decision: keep 8787.** Update spec.

---

## A7. GF(256) Shamir Implementation — Verification Requirements

Per project decision: roll our own ~100-line GF(256) Shamir instead of depending on blahaj crate.

### Verification plan:

**Static verification (lookup tables):**
- GF(256) multiplication table generated from irreducible polynomial 0x11b (AES polynomial)
- Cross-verify against published tables in: NIST FIPS 197 (AES spec), codahale/shamir (Java), Wikipedia GF(256) article
- Property: `a * b = exp_table[(log_table[a] + log_table[b]) % 255]` for all non-zero a, b
- Document the specific polynomial used and why

**Functional verification (split/reconstruct):**
1. Roundtrip: split then reconstruct with all 3 combinations (A+B, A+C, B+C) — property test over random keys, lengths 1-500 bytes
2. Single shard independence: given one shard, verify uniform distribution of possible secrets (statistical test)
3. Wrong shard detection: flip bytes in shard, verify reconstruction produces different key (caught by SHA-256 check from A4)
4. Coefficient range: assert all polynomial coefficients sampled from [0, 255] inclusive (not [1, 255] — the sharks/RUSTSEC-2024-0398 bug)
5. Edge cases: empty key, 1-byte key, all-zeros, all-0xFF, max length

**Cross-implementation verification:**
- Known test vectors from codahale/shamir (Java) and gf256 crate (Rust)
- Split in Python (enrollment) → reconstruct in Rust (sidecar) and vice versa
- Byte-for-byte shard format compatibility

**Mutation testing:**
- Run mutmut/cargo-mutants on the GF(256) implementation
- Target: 100% mutation kill rate on splitter module
- Any surviving mutant = test gap, fix before shipping

**Documentation:**
- Document the mathematical basis (Shamir 1979, Lagrange interpolation over finite fields)
- Document the specific GF(256) irreducible polynomial and reduction method
- Document why [0, 255] coefficient range matters (with reference to RUSTSEC-2024-0398)
- Include worked example: split "HELLO" with known random seed, show intermediate polynomial evaluations
