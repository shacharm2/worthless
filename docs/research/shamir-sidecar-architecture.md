# Split-key architecture for API key protection in Worthless

**The simplest architecture that meets all of Worthless's constraints is a Rust sidecar process that holds one Shamir shard, receives a second shard per-request over a Unix socket, reconstructs the key in mlock'd memory, makes the upstream HTTPS call itself, and zeros the key — all within ~15 microseconds of key exposure.** MPC is technically elegant but provides negligible security benefit when both parties run on the same machine, and no existing product or academic paper applies MPC to bearer token forwarding. The Fernet encryption layer can be eliminated entirely: Shamir 2-of-3 splitting across filesystem, OS keychain, and the sidecar process creates three independent trust domains where any single breach reveals zero information. This architecture adds **<1ms latency** per request, ships as a single `pip install` via maturin wheels, and requires zero cryptographic knowledge from the user.

---

## How Shamir splitting actually protects an API key

Shamir's Secret Sharing exploits a simple mathematical fact: two points define a line, but one point tells you nothing about where the line goes. For a 2-of-3 scheme, the system draws a random line through a y-intercept equal to the secret, then evaluates three points on that line. Any two points reconstruct the line (and the y-intercept), but a single point is consistent with every possible secret — this is **information-theoretic security**, unbreakable regardless of computational power.

For byte strings like `sk-proj-abc123...`, the standard approach operates over **GF(256)** — a finite field where every element maps to one byte (0–255). Each byte of the API key gets its own random polynomial. Addition in GF(256) is XOR (one CPU instruction), and multiplication uses 256-entry lookup tables. The result: shares are exactly the same size as the original key, and the entire split/reconstruct cycle for a 50-byte key completes in **20–50 microseconds** for splitting and **10–25 microseconds** for reconstruction. This is negligible — roughly 10,000× faster than a single network round-trip to OpenAI.

The best Rust library for this is the **`blahaj` crate** (MIT/Apache-2.0), a maintained fork of the popular `sharks` crate that fixes a coefficient bias vulnerability documented in **RUSTSEC-2024-0398**. The original `sharks` crate (~166K downloads) is effectively abandoned, and its random polynomial coefficients were sampled from [1,255] instead of [0,255], enabling brute-force attacks with repeated sharing. For Python-only prototyping, `shamir-mnemonic` by SatoshiLabs (MIT) implements the SLIP-0039 standard over GF(256), though its README explicitly warns against use with sensitive secrets without hardening. The `vsss-rs` crate (~1.6M downloads, Apache-2.0/MIT) supports Feldman and Pedersen verifiable secret sharing but operates over elliptic curve scalar fields, not raw byte arrays — wrong tool for splitting API key strings.

With 2-of-3 splitting, shards can live in genuinely separate trust domains. **Shard A** goes to the filesystem (`~/.config/worthless/shard_a`, chmod 600) — the same security model SSH has used for decades with private keys. **Shard B** goes to the OS keychain (macOS Keychain backed by Secure Enclave on Apple Silicon, or Linux's Secret Service API via GNOME Keyring/KDE Wallet). **Shard C** embeds in the sidecar's compiled configuration or an environment variable. An attacker must breach two of these three systems simultaneously — filesystem permissions, OS credential store, and process memory — to reconstruct the key. This is categorically stronger than the current model where a single file-read attack gets everything.

---

## Why MPC is the wrong tool for bearer tokens

The most intellectually tempting approach is Multi-Party Computation: two processes each hold one shard, and through a cryptographic protocol, they compute `shard_a XOR shard_b` without either process ever seeing the complete key. Since XOR is a single gate in a boolean circuit, and the **Free XOR optimization** (Kolesnikov & Schneider, ICALP 2008) makes XOR gates literally free — zero ciphertexts, zero cryptographic operations, just local label XORing — this seems like it should cost nothing.

The Free XOR technique works by constraining all wire labels in a garbled circuit to differ by a global secret offset Δ. If wire A has labels (L₀, L₀⊕Δ) and wire B has labels (M₀, M₀⊕Δ), then the XOR output labels are simply (L₀⊕M₀, L₀⊕M₀⊕Δ). The evaluator just XORs whatever two labels it holds — no decryption needed. For Worthless's 50-byte key, that's **400 XOR gates, all free**: zero garbled table entries to transmit, zero symmetric-key operations.

But the protocol still requires **Oblivious Transfer** for the evaluator's input bits. The evaluator needs to learn labels for its 400 input bits without revealing which bits it holds. This requires 128 base OTs (public-key operations costing ~10–20ms) plus OT extension for the remaining 272 (~0.1ms using IKNP-style extension with AES-NI). With base OTs precomputed and cached, the amortized per-request cost drops to **<1ms on localhost**. Major frameworks supporting Free XOR include **MP-SPDZ** (~1.5K GitHub stars, C++, MIT-like, actively maintained), **EMP-toolkit** (~700 stars, C++, MIT), and **Swanky** by Galois (~123 stars, Rust, MIT — but explicitly marked "research software, do not deploy in production").

Here is the fundamental problem: **bearer tokens must be literally transmitted to the upstream API**. Unlike threshold ECDSA signing, where MPC can produce a valid signature without any party ever learning the private key, because the mathematical structure of elliptic curve signatures permits distributed computation. Bearer tokens have no such structure — they're opaque strings that must be transmitted verbatim.

The only way to avoid this would be to perform the **entire TLS encryption inside MPC**, so the output is ciphertext that can safely be transmitted without exposing the plaintext key. **TLSNotary** (by the Privacy & Scaling Explorations team, ~404 GitHub stars, Apache-2.0/MIT) does exactly this: two parties jointly derive TLS session keys and compute AES encryption via garbled circuits, so neither party ever sees the complete TLS key. But TLSNotary's approach requires both parties to collaboratively perform every AES-GCM encryption operation on every request, adding substantial complexity and latency. Running AES-128 in MPC requires evaluating ~6,400 AND gates per block (AND gates cost 2 ciphertexts each with half-gates optimization), plus multiple communication rounds for the CBC/GCM mode. The ABY framework benchmarks show this takes milliseconds per block on a LAN.

**When both MPC parties run on the same machine, the security gain over a sidecar is negligible.** An attacker with root access can read both processes' memory regardless of the cryptographic protocol between them. MPC's security assumption requires parties in **separate trust domains** — different machines, different administrators. For a developer workstation running Worthless, this condition is not met. The sidecar pattern achieves equivalent practical security at a fraction of the complexity.

No existing product or academic paper was found that applies MPC specifically to bearer token forwarding. The entire MPC key management industry — Fireblocks, ZenGo, Coinbase (via acquired Unbound Security), Sepior/Blockdaemon, Sodot — focuses exclusively on threshold ECDSA/EdDSA signing. The closest exception is **Coinbase's cb-mpc library** (open-source, 2025), which uses MPC to derive symmetric AES-GCM-SIV keys for PII encryption — but these are derived keys used for encryption, not literal bearer tokens injected into HTTP headers.

---

## The sidecar architecture that actually works

The recommended design is a **Rust sidecar process** that acts as the sole custodian of the reconstructed API key. The Python proxy never touches the complete key — it sends request metadata and one shard over a Unix domain socket, and receives only the API response back.

The flow works as follows. At startup, the sidecar reads Shard A from its own protected file and holds it in mlock'd memory. Per request, Python sends the request payload plus Shard B (retrieved from OS keychain) over a Unix socket. The sidecar XORs the shards, writes the `Authorization: Bearer sk-...` header into the HTTPS request, fires it to the upstream API, zeros the key buffer with `zeroize`'s volatile write, and streams the response back to Python. The key exists as a contiguous value for approximately **10–20 microseconds** with connection pooling — the time to reconstruct, format the header, and write it to the TLS send buffer. Once the bytes are written to the socket's send buffer, the TLS library has already encrypted them into ciphertext, and the plaintext key can be immediately zeroed *before the response even arrives*.

**Unix socket IPC adds negligible overhead.** Benchmarks consistently show **2–5 microseconds** round-trip latency for small messages on modern Linux. For a ~1KB request payload plus 50-byte shard going to the sidecar, and a 1–100KB response coming back, total IPC overhead is **15–60 microseconds** — roughly **0.01%** of a typical 200ms OpenAI API call. TCP localhost, by comparison, adds ~15 microseconds due to the full network stack traversal.

**Process isolation hardens the sidecar against local attacks through layered OS primitives:**

- **Landlock** (Linux kernel 6.7+, ABI version 4): Restricts filesystem access to read-only on the shard file and grants CONNECT_TCP only on port 443. No root privileges required. Works on Ubuntu 22.04+, Fedora 35+, Debian 12+. The `landrun` CLI wrapper (2,156 GitHub stars, Go, Apache-2.0) simplifies policy application.
- **seccomp-BPF** (Linux 3.5+): Allowlists only necessary syscalls — `socket`, `connect`, `read`, `write`, `close`, `epoll_*`, `mmap`, `mprotect`, `mlock`, `clock_gettime`, `exit_group`. Blocks `open`/`openat` after initialization, `ptrace`, `fork`, `exec`. The filter is inherited by child processes and cannot be removed once applied.
- **Separate Unix user**: The sidecar runs as a dedicated user (e.g., `worthless-sidecar`), so DAC prevents the main process from reading `/proc/<sidecar-pid>/mem` without `CAP_SYS_PTRACE`.
- **Core dump prevention**: `prctl(PR_SET_DUMPABLE, 0)` plus `madvise(MADV_DONTDUMP)` on the key buffer prevents the secret from appearing in core dumps.

On **macOS**, `sandbox-exec` with a custom Scheme-like profile provides equivalent isolation — deny by default, allow `network-outbound` on port 443 and `file-read-data` on the shard path. Despite Apple marking it as "deprecated," it remains functional and is used by Chromium and Bazel for production sandboxing.

**Distribution is a solved problem.** The `maturin` build tool (PyO3/maturin, MIT/Apache-2.0) packages Rust binaries into Python wheels. Using `bin` bindings, the sidecar binary lands in the virtualenv's `bin/` directory on `pip install`. Platform-specific wheels (manylinux2014 x86_64/aarch64, macOS universal2, Windows x86_64) are built via the `PyO3/maturin-action` GitHub Action. This is the same approach used by `ruff` (7–10MB wheels), `pydantic-core` (~2MB wheels), and the `cryptography` package. A focused sidecar binary would be **2–5MB** per platform.

---

## Memory safety: Rust solves what Python cannot

Python's memory model is fundamentally hostile to secrets. Strings are immutable — once `"sk-proj-abc123"` exists as a Python string, it cannot be overwritten and persists until garbage collection reclaims it (which is non-deterministic). `bytearray` can be explicitly zeroed with `ba[:] = b'\x00' * len(ba)`, but the GC may have already copied the underlying buffer during compaction, and any conversion to `bytes()` creates an unkillable immutable copy. String interning can create additional immortal references. The `cryptography` library's solution is instructive: it delegates all sensitive operations to Rust/C, keeping key material out of Python's managed heap entirely.

The Rust ecosystem provides robust primitives for secret handling. The **`zeroize` crate** (v1.8.2, Apache-2.0/MIT, zero dependencies) uses `core::ptr::write_volatile` combined with a `compiler_fence(SeqCst)` to guarantee the compiler will not elide the zeroing. The **`secrecy` crate** (Apache-2.0/MIT) wraps this in `Secret<T>` types that auto-zero on drop and prevent accidental exposure via `Debug`/`Display`. For Worthless, the key buffer should be heap-allocated (`Box<[u8]>`) to avoid stack copies, mlock'd to prevent swap exposure, and wrapped in a `Zeroizing<Vec<u8>>` that guarantees zeroing on drop — even in panic unwind paths.

The critical architectural decision is that **the complete API key should never enter Python's address space**. Python sends the shard bytes (which are useless individually) to the Rust sidecar. The sidecar reconstructs, uses, and zeros the key entirely within its own mlock'd memory. This is not theoretical — it is exactly how Cloudflare Workers operate at scale. Workers runtimes never see TLS private keys; a separate proxy service handles TLS termination and communicates with the runtime over a Unix socket.

The minimum realistic **key lifetime** — time between reconstruction and zeroing — breaks down as follows for the steady-state (warm connection with HTTP keep-alive):

| Step | Time |
|---|---|
| XOR reconstruction (50 bytes) | ~0.5 µs |
| Format Authorization header | ~2 µs |
| Write to TLS send buffer (reqwest/hyper) | ~5–10 µs |
| Volatile zero + compiler fence | ~0.1 µs |
| **Total** | **~8–15 µs** |

On a cold first request requiring a TLS 1.3 handshake, the key must persist through the one-RTT handshake (~1–5ms locally, 30–150ms over the internet). After that, connection pooling amortizes the cost, and subsequent requests achieve the microsecond-range key lifetime. The key can be zeroed **immediately after the request bytes are written** to the TLS socket — the response does not require the API key.

---

## Eliminating the Fernet bootstrap key

The current Worthless architecture encrypts Shard B with a Fernet key stored as a plaintext file on the same filesystem as both shards. This creates a circular dependency: the encryption meant to protect the shard is defeated by the same file-read attack that would compromise the shard itself. The fix is not better encryption — it is **eliminating the encryption layer entirely** and relying on trust domain separation as the security primitive.

Every key management system eventually terminates its chain of trust at some non-cryptographic root. **HashiCorp Vault** terminates at human operators who each hold a Shamir shard — the master key is split 5-of-3 at initialization, and each shard can be PGP-encrypted for transport, but ultimately a human must type their shard to unseal Vault after restart. **LUKS disk encryption** terminates at a passphrase (typed by a human) or a key file (protected by filesystem permissions). **AWS KMS** terminates at FIPS 140-3 Level 3 validated HSMs — tamper-resistant hardware is the root of trust. **Kubernetes encryption at rest** terminates at the KMS provider's authentication credentials, which in cloud environments means IAM instance identity.

For Worthless, the trust root is **the operating system's access control mechanisms** — the same foundation SSH has relied on for 25+ years. SSH private keys are stored at `~/.ssh/id_rsa` with `chmod 600`, and the SSH client refuses to use keys with overly permissive permissions. No additional encryption wraps the private key file (unless the user adds a passphrase). The entire global SSH ecosystem trusts this model.

With Shamir 2-of-3 across three trust domains, each individual shard is information-theoretically useless — it reveals exactly zero bits about the API key, regardless of computational power. Encrypting a useless shard adds complexity (and a bootstrap key that must itself be protected) without meaningful security gain. The defense comes from the **separation between trust domains**, not from encrypting the shards within those domains. Integrity protection (detecting shard tampering) can be achieved with a simple HMAC or checksum that doesn't require a secret key — or by verifying the reconstructed key against a stored hash before use.

The realistic threat model for a developer workstation is: (1) another local user reading your files — defeated by file permissions; (2) a malicious process running as your user — defeated by the shard living in the OS keychain or a separate-user sidecar; (3) root compromise — defeats everything, including Fernet, since root can read the Fernet key file too. Fernet provides defense-in-depth only against the scenario where an attacker can read one shard location but not the Fernet key file, which in the current architecture is a null set since they're on the same filesystem.

---

## Nobody else is doing this — and that's the opportunity

After extensive research, **no existing product or open-source project splits bearer tokens per-request and reconstructs them for forwarding to an upstream API**. This is a genuine gap in the market, not an oversight.

The secret management industry — Infisical, Doppler, AWS Secrets Manager, Azure Key Vault, Google Cloud Secret Manager, CyberArk — universally uses **envelope encryption**: secrets are encrypted with data keys, data keys are encrypted with master keys, master keys live in HSMs or KMS. The decrypted secret exists in process memory for the full duration of use. None split individual secrets.

HashiCorp Vault uses Shamir splitting, but only for its **master unseal key** — individual secrets stored in the KV engine are not split. Once Vault is unsealed, secrets are served in plaintext to authenticated clients.

The MPC key management companies — Fireblocks (MPC-CMP protocol), ZenGo (2-party TSS), Sepior/Blockdaemon, Sodot — focus exclusively on **threshold ECDSA/EdDSA signing** for cryptocurrency wallets. They can produce valid signatures without reconstructing the private key, because the mathematical structure of elliptic curve signatures permits distributed computation. Bearer tokens have no such structure — they're opaque strings that must be transmitted verbatim.

The closest analogue is **Akeyless's Distributed Fragments Cryptography (DFC)**, which is FIPS 140-2 certified (NIST Certificate #3589) and performs symmetric encryption/decryption without ever combining key fragments. But DFC operates on *encryption keys*, not bearer tokens — the fragments participate in a challenge-response protocol that produces ciphertext, never a reconstructed key. Academic work follows the same pattern: **DiSE** (CCS 2018), **ATSE** (CCS 2021), and **HiSE** (IACR CiC 2025) implement threshold symmetric-key encryption where the key is used for encrypt/decrypt operations without reconstruction. None address the case where the secret itself must be literally transmitted.

The fundamental insight is that **bearer tokens are fundamentally different from cryptographic keys**. A cryptographic key is used to *compute* something (a signature, a ciphertext) — and MPC can distribute that computation. A bearer token is *presented* — it must exist whole at the point of transmission. Worthless's value proposition is not that the key never exists, but that it exists for **microseconds instead of indefinitely**, in **an isolated process instead of the main application**, across **multiple trust domains instead of one file**.

---

## Recommended architecture for Worthless

The following design satisfies all five constraints: key exposure limited to microseconds, Shamir 2-of-3 with separate trust domains, cross-platform without special hardware, <5ms added latency, and zero-configuration installation.

**Key splitting (one-time setup):** When the user registers an API key, the Python CLI splits it into three Shamir shares over GF(256) using the `blahaj` Rust crate (or an equivalent ~100-line GF(256) implementation to minimize dependencies). Shard A is written to `~/.config/worthless/shards/<key_id>/a` with chmod 600. Shard B is stored in the OS keychain (macOS Keychain via `security` CLI, Linux via Secret Service D-Bus API). Shard C is passed to the sidecar process, which stores it in its own protected directory (chmod 600, owned by a separate user if possible).

**Per-request flow:** The Python proxy retrieves Shard B from the OS keychain and sends it plus the request payload to the sidecar over a Unix domain socket. The sidecar XORs Shard B with its stored Shard C (any 2-of-3 suffices), formats the Authorization header in a `Zeroizing<Vec<u8>>` buffer (mlock'd, MADV_DONTDUMP), writes the request to its pooled HTTPS connection, immediately zeros the buffer, and streams the response back to Python. The sidecar applies seccomp-BPF and Landlock (on Linux) or sandbox-exec (on macOS) to itself after initialization. Total added latency: **<0.5ms** (Unix socket IPC + XOR reconstruction + header formatting).

**Distribution:** The sidecar ships as a Rust binary inside platform-specific PyPI wheels built with maturin. `pip install worthless` installs the Python proxy and the native sidecar binary into the virtualenv. First run auto-generates shards and configures the keychain entry. No cryptographic configuration. No manual key management. The user experience is: `pip install worthless`, `worthless add-key sk-proj-...`, `worthless start` — identical to the current flow, but with the master key file eliminated and the API key never existing as a whole in the Python process.

This design is not novel cryptography — it is well-understood primitives (Shamir splitting, process isolation, memory zeroing) composed in a way that no existing product has applied to the specific problem of bearer token proxying. The security guarantee is concrete: compromising any single trust domain (filesystem, keychain, sidecar memory) reveals zero information about the API key. Compromising two requires simultaneous breach of independent OS security boundaries. And even then, the key exists for ~15 microseconds instead of the lifetime of the process.
