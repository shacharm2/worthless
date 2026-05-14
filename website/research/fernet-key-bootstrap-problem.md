# The Fernet Key Bootstrap Problem

## Status: Research / Design Exploration

**Author**: Generated for deep analysis
**Date**: 2026-04-04
**Related**: worthless-3sd (P1), worthless-rgu (P2)

---

## 0. Architectural Constraint: Light Mode is Permanent

**Light mode (XOR + Fernet, single process) is never removed.** It is the default, the entry point, and the forever-supported path for local development and vibe coders.

Any future architecture (sidecar, MPC, Shamir, secure enclaves) is additive — a "secure mode" that layers on top. The two modes coexist permanently:

| | Light Mode | Secure Mode |
|---|---|---|
| **Split primitive** | XOR (kept forever) | XOR or Shamir (user's choice) |
| **Encryption at rest** | Fernet | Not needed (process isolation) |
| **Architecture** | Single process | Sidecar makes upstream calls |
| **Start command** | `worthless up` | `worthless up --secure` or Docker |
| **User** | Vibe coder, local dev | Production, Docker, cloud |
| **Security claim** | `.env` decoy protects against casual exposure | API key never exists in internet-facing process |

v2.0 adds secure mode. It does NOT overhaul, replace, or deprecate light mode. The codebase, CLI, and XOR splitting are shared between both modes.

This constraint must survive into any future milestone planning, PRD, or roadmap.

---

## 1. The Problem Statement

Worthless protects API keys by splitting them into two XOR shards:
- **Shard A**: stored as a file on disk
- **Shard B**: encrypted with a Fernet key, stored in SQLite

The Fernet key is the master secret. Whoever has it plus access to the database can decrypt every Shard B and, combined with Shard A (same filesystem), reconstruct every API key.

**The security claim "API keys are worthless to steal" collapses to "steal the Fernet key instead."** We moved the problem, we didn't solve it.

The Fernet key currently:
- Lives as a plaintext file at `~/.worthless/fernet.key` (local install)
- Lives at `/data/fernet.key` inside the Docker volume
- Is read into a Python `str` (immutable, cannot be zeroed) and held for the entire proxy lifetime
- Sits on the same filesystem/volume as both shards

---

## 2. Current State Across All Deployment Modes

### 2.1 Local Install (developer workstation)

```
~/.worthless/
├── fernet.key          # plaintext, 0600 perms
├── worthless.db        # contains Fernet-encrypted shard_b
└── shard_a/
    ├── openai          # plaintext XOR shard
    └── anthropic
```

**Threat model**: Malware on the developer's machine, stolen laptop, compromised dependency with filesystem access, another process running as the same user.

**Current protection**: Unix permissions (0600/0700). That's it.

**Attack**: Any process running as the user can read all three (fernet.key + shard_a + shard_b from DB). Game over.

### 2.2 Docker (self-hosted)

```
/data/                  # Docker named volume
├── fernet.key          # plaintext, 0400 perms
├── worthless.db        # Fernet-encrypted shard_b
└── shard_a/
    └── ...
```

**Threat model**: Docker socket access, container escape, compromised dependency inside container, volume read from host.

**Current protection**: Non-root user, read_only filesystem, cap_drop ALL, no-new-privileges, Fernet key passed via FD (not env var). But FD is consumed into a Python string that lives forever.

**Attack**: `docker exec` or volume mount from host reads everything. Container compromise reads `app.state.settings.fernet_key` from memory.

### 2.3 Cloud (Railway/Render) — Planned

Same as Docker but the volume is managed by the platform. Additional threat: platform operators can read volumes.

### 2.4 Future: Worthless Cloud (multi-tenant hosted proxy)

The Fernet key problem becomes critical here. A single compromise exposes every tenant's keys.

---

## 3. The Bootstrap Paradox

Any scheme to protect the Fernet key runs into a recursion:

1. Split the Fernet key using Worthless itself → need a second Fernet key to encrypt the first one's shard_b → need a third...
2. Encrypt the Fernet key with a password → password is now the master secret, same problem
3. Derive the Fernet key from hardware (TPM/HSM) → requires hardware, not universal
4. Derive from multiple sources (MPC/Shamir) → need to store the shares somewhere

**The recursion must bottom out somewhere.** The question is: where does it bottom out for each deployment mode, and what does the user need to do?

---

## 4. Dogfooding: Using Worthless to Protect Its Own Fernet Key

### 4.1 The Idea

Treat the Fernet key as just another secret. Split it into shards. Store Shard A somewhere, encrypt Shard B with... what?

Options for the "inner" encryption:
- **A password/passphrase** the user provides at startup (interactive or env var)
- **A hardware-bound key** (TPM, Secure Enclave, Keychain)
- **A platform secret** (Docker secrets, k8s secrets, cloud KMS)
- **A derived key** from multiple independent sources (MPC-style)

### 4.2 Password-Protected Bootstrap

```
Startup flow:
1. User provides passphrase (stdin, env var, or keyfile on separate mount)
2. Derive a key from passphrase (Argon2id)
3. Use derived key to decrypt fernet_shard_b
4. XOR fernet_shard_a + fernet_shard_b → Fernet key
5. Use Fernet key to decrypt API key shard_b values
6. Zero Fernet key after use (if using bytearray)
```

**Pros**: Universal, no hardware, no cloud dependency. User chooses the passphrase.
**Cons**: Passphrase must be provided on every restart. If it's in an env var, we're back to "steal one secret." If interactive, breaks automated deploys.

**Vibe coder impact**: Must remember a passphrase or store it somewhere. Moderate friction.

### 4.3 OS Keychain/Credential Store

```
Startup flow:
1. On first boot, generate Fernet key, store in OS keychain
   - macOS: Keychain (keyring library)
   - Linux: libsecret/kwallet
   - Windows: Credential Manager
2. On subsequent boots, read from keychain
3. Keychain is unlocked by user login session
```

**Pros**: Zero extra config for local installs. OS handles protection. Biometric unlock on macOS.
**Cons**: Only works for local installs. Docker containers don't have keychains. Headless Linux servers may not have libsecret. CI environments definitely don't.

**Vibe coder impact**: Zero. `worthless lock` just works. The key is in their keychain, protected by their login password/biometrics.

### 4.4 Platform Secrets

```
Docker:     docker secret → mounted at /run/secrets/fernet_key (tmpfs, never on disk)
Kubernetes: k8s secret → mounted as file or env var
Railway:    dashboard secret → injected as env var
Render:     dashboard secret → injected as env var
```

**Pros**: Standard practice. Platform manages encryption at rest.
**Cons**: Env var injection is visible in `docker inspect` / `/proc`. File mount secrets (Docker swarm, k8s) are better but require orchestrator features not available in standalone Docker Compose.

**Vibe coder impact**: Low for cloud (they set a secret in a dashboard). Higher for Docker (Docker secrets requires Swarm mode).

---

## 5. MPC and Shamir's Secret Sharing

### 5.0 The Critical Distinction: Shamir vs MPC

These solve **different problems** and are often confused. The difference matters for Worthless because it determines whether the API key ever exists as a complete value.

```
SHAMIR answers:  "Where do I store the secret so no single breach exposes it?"
MPC answers:     "How do I USE the secret without it ever existing as a whole?"
```

| | At rest | In memory at reconstruction | During upstream API call |
|---|---|---|---|
| **Shamir** | Protected — need K of N shares from separate locations | **Full secret exists** at the reconstruction point | Full secret exists in the calling process |
| **MPC** | Protected — shares distributed across parties | **Secret never exists** in any single process | Only at TLS write (~microseconds), or never with enclave |
| **Sidecar C2** | Protected — separate trust domains per process | Full secret exists **briefly in sidecar only** | Never in the proxy; briefly in sidecar |

**Shamir** is about storage and availability. It protects secrets at rest and provides disaster recovery (lose one share, reconstruct from the others). But the moment you reconstruct, the full secret is in memory — same as if you'd stored it in one place.

**MPC** is about computation without reconstruction. The parties jointly compute the result (e.g., "inject this API key into an HTTP header") without any party ever holding the complete secret. The key literally never exists as a contiguous value in any process's address space.

**Sidecar C2** is the pragmatic middle ground. The secret exists briefly in an isolated process with no inbound network. Not as strong as MPC (the sidecar does hold the full key for microseconds), but eliminates exposure from the internet-facing proxy entirely.

**For Worthless, the right framing is all three combined — they're layers, not alternatives:**

```
Layer 1 — SHAMIR (at rest):
  API key split into 3 shares (2-of-3 threshold)
  Share 1 → proxy storage (isolated filesystem)
  Share 2 → sidecar storage (isolated filesystem)
  Share 3 → user backup (offline)
  No single breach exposes the key. Lose one share, recover from the other two.

Layer 2 — SIDECAR (architecture):
  Proxy: internet-facing, holds Share 1, no access to Share 2
  Sidecar: no inbound network, holds Share 2, no access to Share 1
  Communication: unix socket only
  Neither process can reconstruct alone.

Layer 3 — MPC (computation):
  Per-request, proxy sends Share 1 to the MPC protocol
  Sidecar contributes Share 2 to the MPC protocol
  The protocol computes: reconstruct key → inject into Authorization header → TLS write
  NEITHER process ever holds the full API key in its address space
  The reconstructed key exists only inside the MPC circuit / garbled gates

Result: The API key never exists as a complete value in any process's memory.
Not at rest (Shamir). Not in the internet-facing process (sidecar). Not in any process (MPC).
```

**This is the endgame architecture.** Each layer is independently valuable and can be shipped incrementally, but the full stack is where the security claim becomes provably true: stealing any single component — the proxy, the sidecar, the storage, the memory dump — gives you nothing.

### 5.1 Shamir's Secret Sharing (SSS)

Shamir's scheme splits a secret into N shares where any K shares can reconstruct the original (K-of-N threshold scheme). **It solves the at-rest problem, not the in-use problem.**

**Applied to Fernet key**:
- Generate Fernet key
- Split into 3 shares (2-of-3 threshold)
- Store shares in different locations:
  - Share 1: filesystem (`/data/fernet_share_1`)
  - Share 2: environment variable or Docker secret
  - Share 3: backup (offline, printed, in password manager)
- Any 2 of 3 can reconstruct the key

**Why this is interesting**:
- Losing one share doesn't lose the key (availability)
- Stealing one share doesn't reveal the key (confidentiality)
- The shares can be stored across trust boundaries

**Why this is hard for vibe coders**:
- They need to understand what shares are and where to put them
- Backup management ("what do I do with share 3?")
- Recovery when a share is lost

**Libraries**: `secretsharing`, `shamir-mnemonic`, or implement directly (Shamir over GF(256) is ~50 lines of Python).

### 5.2 Multi-Party Computation (MPC)

MPC means multiple parties jointly compute a function over their private inputs **without any party ever seeing the other's input or the intermediate values.** Applied to Worthless, this means:

**The Fernet key never exists.** The API key never exists. Until the exact moment bytes are written to the upstream HTTP socket.

#### How MPC would work in Worthless

```
Current flow (broken):
  shard_a (file) + shard_b (DB) → decrypt shard_b with Fernet → XOR → API key (plaintext in memory for proxy lifetime)

MPC flow:
  Party 1 (process A): holds shard_a
  Party 2 (process B): holds encrypted shard_b + Fernet share

  Step 1: Party 2 decrypts shard_b using its Fernet share → produces masked intermediate
  Step 2: Parties jointly compute XOR via oblivious transfer or garbled circuits
  Step 3: Result is the API key, but NEITHER party sees it in full
  Step 4: The key is injected into the HTTP Authorization header via shared memory / pipe
  Step 5: Zeroed immediately after the upstream TLS handshake sends the header
```

**What this eliminates:**
- Fernet key never exists as a complete value in any process's memory
- API key never exists as a complete value until the HTTP write (~microseconds)
- A compromised dependency in the proxy process sees neither the Fernet key nor the full API key
- Memory dump of any single process reveals nothing useful

**What this does NOT eliminate:**
- The upstream API (OpenAI, Anthropic) requires a plaintext `Authorization: Bearer sk-...` header. At the TLS application layer, the full key must exist momentarily in the process doing the HTTP call.
- Unless paired with a **secure enclave** (SGX/TDX/SEV) where the plaintext exists only inside hardware-encrypted memory.

#### Practical MPC approaches

**Option A: Two-process architecture (no crypto libraries needed)**

```
Process 1 (proxy): handles HTTP, holds shard_a
Process 2 (sidecar): holds Fernet key + access to shard_b DB

Per request:
1. Proxy sends key_alias to sidecar via unix socket
2. Sidecar decrypts shard_b, XORs with... wait, it needs shard_a too.
```

This doesn't work as pure 2-party MPC because XOR reconstruction needs both shards in one place. The sidecar would need shard_a OR the proxy would need decrypted shard_b. One of them sees the full key.

**Option B: Garbled circuits (real MPC)**

Use a 2PC protocol (Yao's garbled circuits or GMW) to compute `XOR(shard_a, Fernet_decrypt(shard_b))` without either party seeing the result. The output is secret-shared and used to construct the HTTP header via oblivious transfer.

Libraries: `mp-spdz`, `EMP-toolkit`, `ABY` (C++), `mpyc` (pure Python but slow).

**Performance cost**: Garbled circuits for AES-128 decryption + XOR = ~10ms per operation (benchmarks from `EMP-toolkit`). At 100 RPS this is 1 second of CPU per second. Significant but not prohibitive.

**Option C: Sidecar as reconstruction service (the right architecture)**

There are two versions of the sidecar idea, and the difference matters:

**C1: Sidecar returns API key to proxy (wrong)**
```
Proxy → "give me key for alias X" → Sidecar → returns API key → Proxy makes upstream call
```
The proxy still holds the full API key in memory for the duration of the request. A compromised proxy dependency can read it. You protected the Fernet key but the API key still flows through the compromised process. This is "isolate the vault but hand the cash to the robber."

**C2: Sidecar makes the upstream call itself (right)**
```
Proxy → "forward this request using alias X" → Sidecar → reconstructs key, injects auth header, makes upstream HTTPS call, zeros key, returns response → Proxy
```
The proxy NEVER sees the API key. The sidecar:
- Holds the Fernet key (isolated process, no inbound network)
- Decrypts shard_b, XORs with shard_a → API key
- Injects `Authorization: Bearer <key>` into the upstream request
- Makes the upstream HTTPS call itself
- Zeros the key immediately after the TLS write
- Returns the response (which contains no key material) to the proxy

**This is the PRD's Rust reconstruction service, implemented locally.**

The proxy becomes a routing/auth/metering layer that never touches secrets. The sidecar is a minimal, auditable process (ideally Rust — no GC, deterministic zeroing, small binary) with:
- No inbound network access (unix socket from proxy only)
- Outbound HTTPS only (to upstream API providers)
- seccomp/landlock locked to: read /data, one unix socket, outbound TLS
- No dynamic dependencies, no plugin system, no request parsing beyond "alias + forwarded request"

A compromised proxy dependency gets: request/response content (which the proxy needs for metering anyway) but NEVER the API key, NEVER the Fernet key, NEVER the shards.

**This is the right next step.** It's not true MPC but it provides the key property: the internet-facing process never holds secrets. And it's the same architecture as Worthless Cloud's reconstruction service — pulling it forward to local/Docker means the cloud version is just a deployment change, not an architecture change.

**Option D: Secure enclave (the endgame)**

```
Enclave (SGX/TDX/SEV): holds Fernet key + does all crypto
Process (proxy): sends encrypted shard_b + shard_a to enclave
Enclave: decrypts, XORs, returns API key via attestation-bound channel
```

Even root cannot read enclave memory. This is the maximum possible protection.

**Availability**: AWS Nitro Enclaves, Azure Confidential Computing, GCP Confidential VMs. Not available on developer laptops or cheap VPS.

#### MPC summary for Worthless

| Approach | Fernet key exposure | API key exposure | Who sees the key? | Performance | Complexity | Deployment |
|----------|-------------------|-----------------|-------------------|-------------|------------|------------|
| Current | Entire proxy lifetime | Entire proxy lifetime | Proxy (internet-facing) | Baseline | None | Everywhere |
| Sidecar returns key (C1, wrong) | Sidecar lifetime | Per-request in proxy | Proxy (internet-facing) | ~0.5ms | Medium | Everywhere |
| **Sidecar makes call (C2, right)** | **Sidecar lifetime** | **Microseconds in sidecar only** | **Sidecar (no inbound network)** | **~1-2ms** | **Medium** | **Everywhere** |
| Garbled circuits (B) | Never | Microseconds at TLS write | Neither party fully | ~10ms | High | Everywhere |
| Secure enclave (D) | Never (hardware) | Never (hardware) | Hardware only | ~1ms | Very high | Cloud only |

**The key realization**: MPC doesn't just protect the Fernet key — it protects the API key too. The whole chain becomes zero-knowledge until the moment of use. This is the actual endgame for a product whose entire value prop is "your keys are worthless to steal."

### 5.4 The Nuclear Option: Eliminate the Fernet Key Entirely

The Fernet key exists because shard_b is stored in SQLite, which lives on the same filesystem as the proxy. Without encryption, anyone who reads the DB gets shard_b in plaintext. Combined with shard_a (same filesystem), game over.

But **why is shard_b on the same filesystem as the proxy?** Because in the current single-process architecture, the proxy needs to read shard_b to reconstruct the key. Everything is in one trust domain.

With the sidecar architecture (C2), trust domains are separated:

```
TRUST DOMAIN 1 — Proxy (internet-facing)
  - Handles HTTP routing, auth, metering
  - Holds shard_a (or receives it from client via header)
  - NEVER sees shard_b, NEVER sees the API key
  - Compromising this process gets you: request/response content, shard_a

TRUST DOMAIN 2 — Sidecar (no inbound network)
  - Holds shard_b (plaintext — no Fernet encryption needed)
  - Receives shard_a from proxy per-request via unix socket
  - Reconstructs API key, makes upstream call, zeros, returns response
  - Compromising this process gets you: shard_b + transient API keys
  - But this process has: no inbound network, no dynamic deps, seccomp-locked
```

**In this model, Fernet encryption of shard_b is redundant.** The isolation IS the protection. An attacker who compromises the proxy gets shard_a but not shard_b (different process, different storage). An attacker who compromises the sidecar gets shard_b but the sidecar has no inbound network — how did they get in?

**What this eliminates:**
- The Fernet key (the entire bootstrap problem disappears)
- The `cryptography` dependency in the sidecar (if written in Rust, no Python at all)
- Key rotation complexity (no re-encryption of shard_b values)
- The `fernet.key` file (nothing to steal)
- Every concern in this document's section 2 (all deployment modes simplified)

**What this requires:**
- The sidecar architecture (C2) — the proxy and reconstruction service are separate processes
- shard_a and shard_b stored in separate locations (separate filesystem paths, ideally separate volumes in Docker)
- The sidecar is the ONLY process that can read shard_b storage
- The proxy is the ONLY process that can read shard_a storage
- Unix socket between them, nothing else

**This is Worthless dogfooding its own principle.** The same idea that protects API keys (split into shards, no single location holds both) also protects the infrastructure. No master key means no bootstrap problem. No Fernet means no "steal one key instead of another." The recursion bottoms out at process isolation and filesystem permissions — things the OS already enforces.

**Migration path from current architecture:**
1. Phase 1 (now): Single process, Fernet-encrypted shard_b. Ship what works.
2. Phase 2: bytearray + zeroing. Reduce exposure window.
3. Phase 3: Sidecar (C2). Proxy never sees secrets. Fernet still used (defense-in-depth).
4. Phase 4: Drop Fernet. Sidecar stores shard_b plaintext in its own trust domain. The Fernet key and the bootstrap problem cease to exist.
5. Phase 5: Rust sidecar with secure enclave support for cloud deployments.

Each phase is backward-compatible. Users don't need to re-enroll keys. The migration is internal — the secret sharing scheme is the only crypto that matters, and Fernet ceases to be load-bearing.

### 5.5 XOR vs Shamir for the Split Itself

Worthless currently uses XOR to split API keys: `shard_a = random; shard_b = key XOR shard_a`. This is a degenerate 2-of-2 Shamir scheme. Lose either shard and the key is gone forever.

**Should the API key splitting move from XOR to Shamir K-of-N?**

With Shamir 2-of-3 applied to API key splitting:

```
worthless enroll:
  1. Take API key
  2. Split into 3 Shamir shares (2-of-3 threshold)
  3. Share 1 → proxy's storage (shard_a equivalent)
  4. Share 2 → sidecar's storage (shard_b equivalent, no Fernet needed)
  5. Share 3 → displayed to user as backup (mnemonic, QR, or base64)

Reconstruction (per-request):
  Proxy sends Share 1 to sidecar via unix socket
  Sidecar combines Share 1 + Share 2 → API key
  Sidecar makes upstream call, zeros key

Recovery scenarios:
  Lost proxy storage?   → Share 2 (sidecar) + Share 3 (backup) → reconstruct
  Lost sidecar storage? → Share 1 (proxy) + Share 3 (backup) → reconstruct
  Lost backup?          → Share 1 + Share 2 still work (just lose disaster recovery)
```

**Why Shamir over XOR:**
- Availability: any 2 of 3 shares reconstruct. XOR needs both — lose one, lose everything.
- Flexibility: can do 3-of-5 for enterprise (2 in infra, 1 with admin, 1 with security team, 1 offline).
- The third share as user backup solves the "what if my Docker volume dies" problem without any external backup system.

**Why XOR might still be fine:**
- Simpler. 50 lines of Python vs ~100 for Shamir over GF(256).
- For the sidecar architecture with proper backups, 2-of-2 is sufficient.
- Shamir adds a UX concept ("backup share") that vibe coders might not manage well.

**The pragmatic answer:** Keep XOR for the split (simple, proven). Use Shamir only if/when we need K-of-N threshold semantics (multi-party enterprise, disaster recovery). The sidecar architecture provides the security benefit regardless of which splitting scheme is used — the key insight is process isolation, not the polynomial.

### 5.3 Practical Shamir Scheme for Worthless

```
worthless lock:
  1. Generate Fernet key
  2. Split into 3 Shamir shares (2-of-3)
  3. Share 1 → ~/.worthless/fernet_share_1 (filesystem)
  4. Share 2 → OS keychain (macOS/Linux/Windows)
  5. Share 3 → printed to terminal: "BACKUP THIS: xxxxx"
  6. Delete original Fernet key from memory

worthless up:
  1. Read Share 1 from filesystem
  2. Read Share 2 from OS keychain
  3. Reconstruct Fernet key from any 2 shares
  4. Use key, then zero it (bytearray)

Docker:
  1. Share 1 → /data/fernet_share_1 (volume)
  2. Share 2 → Docker secret or env var WORTHLESS_FERNET_SHARE
  3. Share 3 → user's backup
  4. Entrypoint reconstructs from shares 1+2

Recovery (lost laptop, corrupted volume):
  1. Share 3 (backup) + Share 2 (keychain export) → reconstruct
  2. Or: Share 3 + Share 1 (filesystem backup) → reconstruct
```

**This is genuinely better than the current scheme because:**
- No single storage location holds the complete Fernet key
- Compromise of the filesystem alone is not sufficient (need keychain too)
- Compromise of the keychain alone is not sufficient (need filesystem too)
- Loss of any one share is recoverable

---

## 6. Comparison Matrix

| Approach | Local | Docker | Cloud | Vibe Coder UX | Security Improvement | Implementation Effort |
|----------|-------|--------|-------|---------------|---------------------|-----------------------|
| Status quo (plaintext file) | Works | Works | Works | Zero friction | Baseline (poor) | Done |
| bytearray + zeroing (WOR-134) | Works | Works | Works | Zero friction | Marginal (smaller window) | Small |
| OS Keychain | Works | No | No | Zero friction | Good for local | Medium |
| Password/passphrase | Works | Works | Works | Moderate friction | Good | Medium |
| Docker secrets | No | Swarm only | Partial | Low friction | Good | Small |
| Shamir 2-of-3 | Works | Works | Works | One-time backup step | Significant | Medium-Large |
| Shamir + Keychain | Works | Partial | No | Near-zero friction | Strong | Large |
| KMS sidecar | Partial | Works | Works | Ops expertise needed | Strong | Large |
| HSM/TPM | Hardware-dependent | No | Cloud KMS | Expert only | Maximum | Very Large |

---

## 7. Proposed Path

### Phase 1: Immediate (WOR-134)
- Convert `settings.fernet_key` from `str` to `bytearray`
- Zero after decrypt operation
- Zero friction, zero config change
- Shrinks the attack window from "entire proxy lifetime" to "during request processing"

### Phase 2: Local Install Improvement
- Store Fernet key in OS keychain (macOS Keychain, libsecret, Credential Manager)
- Fallback to file for headless/CI environments
- `worthless lock` auto-detects and uses keychain if available
- Zero additional UX for users with a desktop OS

### Phase 3: Shamir Bootstrap
- Split Fernet key into 2-of-3 Shamir shares
- Share 1: filesystem (existing location)
- Share 2: keychain (local) or Docker secret/env (container) or platform secret (cloud)
- Share 3: displayed once at enrollment, user's responsibility to backup
- Reconstruct at startup from shares 1+2
- Zero the reconstructed key after use

### Phase 4: Sidecar Architecture (MPC-lite)
- Isolate Fernet key + decrypt into a separate process with no network access
- Proxy never sees Fernet key; sidecar returns reconstructed API key via unix socket/FD
- Sidecar locked down with seccomp/landlock (read-only FS, one socket)
- Works everywhere — local, Docker, cloud. No hardware dependency.
- A compromised proxy dependency gets nothing.

### Phase 5: True MPC / Secure Enclave (Worthless Cloud)
- Garbled circuits or oblivious transfer — neither party sees the full key
- Or: secure enclave (SGX/TDX/SEV) — plaintext only in hardware-encrypted memory
- The endgame for multi-tenant hosted proxy
- Requires either MPC library integration or cloud-specific enclave support

---

## 8. Open Questions for Deep Analysis

1. **Shamir over GF(256) vs XOR splitting**: Worthless already uses XOR for API key splitting. XOR is a degenerate 2-of-2 Shamir scheme. Should we use proper Shamir (K-of-N threshold) for the Fernet key, or is 2-of-2 XOR sufficient? What's the UX cost of the third share?

2. **Key rotation**: If the Fernet key is Shamir-split, how does key rotation work? Re-split, redistribute shares, re-encrypt all shard_b values? What happens during rotation — is there a window where both old and new keys must be available?

3. **Recovery UX**: If a vibe coder loses their laptop, how do they recover? They need Share 3 (backup) plus one other share. Is a mnemonic phrase (BIP39-style) better than a base64 blob for the backup share? Could we generate a QR code?

4. **Headless Linux / CI**: No keychain available. What's the best Share 2 storage? Environment variable (weak)? A separate file on a different mount (relies on infra)? Password-protected file (requires interaction)?

5. **Docker Compose (not Swarm)**: Docker secrets require Swarm mode. For standalone Compose, Share 2 must come from env_file or a bind-mounted secret file. Is this meaningfully better than the current single-file approach?

6. **Performance**: Shamir reconstruction adds ~0.1ms per startup. Negligible. But if we zero the Fernet key after each request's decrypt, we need to reconstruct per-request. Shamir per-request is still fast (~0.1ms) but adds up at high RPS. Should we keep the key alive for a TTL window (e.g., 5 seconds) instead of per-request? For the sidecar approach, the unix socket round-trip adds ~0.5ms. For garbled circuits, ~10ms. What's the acceptable latency budget?

6b. **MPC feasibility in Python**: `mpyc` is pure Python but slow for AES circuits. `EMP-toolkit` and `ABY` are C++ with Python bindings. Is the sidecar approach (Option C — not true MPC but process isolation) sufficient for the "Harden" milestone, with true MPC deferred to "Cloud"?

6c. **The sidecar UX question**: For local install, does the user run two processes? Does `worthless up` spawn the sidecar automatically? For Docker, two containers in compose? Or a single container with two processes (supervised by tini)? Each has different failure modes.

7. **The "two shards on one volume" problem (WOR-135)**: Even with Shamir, if Share 1 and the encrypted shard_b database are on the same Docker volume, a volume compromise gets you Share 1 + all encrypted shard_b values. You still need Share 2. Is this sufficient separation? Or should the DB be on a separate volume from Share 1?

8. **Backward compatibility**: Users already have `~/.worthless/fernet.key`. Migration path? Auto-split on next `worthless lock`? Refuse to start with a bare fernet.key and require `worthless migrate`?

9. **Does this actually help against the primary threat?** The primary threat for a vibe coder is: malware exfiltrates `~/.worthless/`. If malware runs as the user, it can read the keychain too (macOS Keychain prompts, but malware can click "Allow"). Is the Shamir approach security theater for the local case, and only meaningful for the Docker/cloud case where trust boundaries are real?

10. **Comparison to age/sops/vault**: Tools like `age` (file encryption), `sops` (encrypted config), and HashiCorp Vault (secret management) solve similar problems. Should Worthless integrate with these rather than building its own key protection? E.g., `WORTHLESS_FERNET_KEY_CMD="sops -d fernet.key.enc"` — delegate to existing tools.

---

## 9. Existing Art

- **age** (filippo.io/age): File encryption with recipient-based keys. Could encrypt fernet.key with the user's SSH key or a passphrase.
- **sops** (Mozilla): Encrypted file format supporting KMS, PGP, age backends. Could manage fernet.key.enc alongside the repo.
- **HashiCorp Vault**: Enterprise secret management. Transit engine can encrypt/decrypt without exposing keys.
- **Shamir implementations**: `ssss` (CLI), `python-shamir-mnemonic` (Trezor's BIP-style), `hashicorp/vault` (built-in Shamir for unseal keys).
- **Signal Protocol**: Uses double-ratchet, not directly applicable, but the idea of ephemeral keys that rotate is relevant.
- **Keybase/Saltpack**: Encrypts to identities, could be a "Share 2 = encrypted to your Keybase identity" approach.
- **macOS Keychain / libsecret / Windows DPAPI**: OS-level credential stores, transparent to applications via the `keyring` Python library.

---

## 10. Recommendation for Claude Deep Analysis

When analyzing this document, consider:

1. **Which threat model matters most for each deployment mode?** Don't design for HSM when the user is a vibe coder on a MacBook.

2. **What's the minimal change that provides meaningful security improvement without UX regression?** The answer might be different for local vs Docker vs cloud.

3. **Is Shamir actually the right primitive here, or is the problem better solved by compartmentalization?** (Separate processes, separate volumes, separate trust domains rather than cryptographic splitting.)

4. **The vibe coder test**: If someone follows a YouTube tutorial to set up Worthless, will they understand what's happening? If not, will they break their own setup? Security that users disable or misconfigure is worse than no security.

5. **Evaluate the "delegate to existing tools" option** (age/sops/vault integration) vs building native Shamir. Which has better long-term maintenance, broader compatibility, and user trust?

6. **Can we make the security progressive?** The levels should reflect the three-layer architecture (Shamir + Sidecar + MPC), not just key storage:

   - Level 0: Plaintext Fernet key, single process (current, works everywhere)
   - Level 1: bytearray + zeroing, OS keychain where available (auto-detected, zero config)
   - Level 2: Sidecar C2 — reconstruction service makes upstream calls, proxy never sees keys (process isolation, no Fernet needed)
   - Level 3: Sidecar C2 + Shamir 2-of-3 — shares across trust domains, user backup share for disaster recovery
   - Level 4: Sidecar C2 + MPC — key never exists as complete value in any process, garbled circuits or oblivious transfer for reconstruction
   - Level 5: Secure enclave (SGX/TDX/SEV) — key never exists outside hardware-encrypted memory

   Each level is backward-compatible. Users don't re-enroll keys. The API key splitting primitive (XOR or Shamir) is the only crypto that persists across all levels — everything else is infrastructure around it.

7. **Evaluate the three-layer endgame (Shamir + Sidecar + MPC) for feasibility.** Specifically:
   - Is MPC over garbled circuits practical in Python, or does this force the sidecar to be Rust/C++?
   - What's the per-request latency budget? Current proxy overhead is ~2ms. Sidecar adds ~1ms (unix socket round-trip). MPC adds ~10ms (garbled AES circuit). Is 13ms total acceptable for LLM API calls that take 500ms-30s?
   - Can `mpyc` (pure Python MPC) handle the throughput, or is a compiled library (EMP-toolkit, ABY) required?
   - Is there a simpler MPC protocol for XOR reconstruction specifically? XOR is a single-gate circuit — garbled circuit overhead for one XOR gate is trivial compared to AES.

8. **The "Fernet elimination" path.** If the sidecar architecture makes Fernet redundant (section 5.4), what's the migration path for existing users who have Fernet-encrypted shard_b in their databases? Options:
   - Decrypt-and-re-store: one-time migration that reads all shard_b values with the old Fernet key and writes them plaintext into the sidecar's isolated storage
   - Dual-mode: sidecar supports both encrypted (legacy) and plaintext shard_b, auto-detects
   - Breaking change: require re-enrollment (users run `worthless enroll` again for each key)

   Which is least disruptive? Which is most secure (no Fernet key lingering for backward compat)?

9. **The XOR-is-one-gate insight.** Current API key splitting is XOR: `key = shard_a XOR shard_b`. In MPC terms, XOR is a single gate — the simplest possible circuit. Garbled circuit protocols have near-zero overhead for XOR gates (they're "free" in the half-gates optimization). This means MPC for Worthless's specific use case might be dramatically cheaper than the general AES-circuit benchmarks suggest. Is the MPC overhead actually ~0.01ms rather than ~10ms for a single XOR reconstruction? If so, the performance argument against MPC collapses entirely.

10. **Does the sidecar need to exist as a separate binary, or can it be a subprocess?** Options:
    - Separate binary (Rust): minimal attack surface, deterministic memory zeroing, ~2MB static binary. But requires building/distributing a second artifact.
    - Python subprocess: same language, shares the worthless package, easier to ship. But Python GC means non-deterministic zeroing, and a compromised dependency in the main package affects both processes.
    - WASM sandbox (wasmtime/wasmer): single binary ships a WASM module for the sidecar. Process isolation via WASM sandbox, not OS processes. Emerging but not mature for production crypto.

    For vibe coders: a subprocess that `worthless up` spawns automatically is invisible. A separate binary they need to install is friction. A Rust binary bundled via PyPI wheels (like `cryptography` does) is the best of both worlds but adds build complexity.

11. **Can the client-side shard (shard_a) be sent per-request via header instead of stored on the proxy?** The code already supports `x-worthless-shard-a` header (app.py:288). If the proxy never stores shard_a at all — the client sends it with every request — then compromising the proxy gets you nothing. The sidecar holds shard_b, the client holds shard_a, the MPC combines them. True zero-trust split. But this means every client (IDE, CLI tool, CI runner) must store and send shard_a. Is that UX acceptable? For Claude Code / Cursor / Windsurf, a plugin could handle this transparently.

12. **What's the competitive landscape?** Do any existing API key management tools use MPC or split-key with process isolation? If Worthless ships sidecar + MPC before anyone else, that's a defensible technical moat. If others already do this, what can we learn from their implementation?
