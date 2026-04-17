# Worthless Security Posture

> Last verified: 2026-04-03 | Commit: `4f79fe6` | Python PoC

**This document is not a compliance certification.** Worthless has not been audited or certified under any compliance framework (SOC 2, FIPS, ISO 27001, etc.). OWASP references below show which vulnerability classes the architecture addresses. Full framework mapping ships with enterprise tier.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

---

## TL;DR

Worthless makes stolen API keys worthless to the thief. The real key is split into two XOR shards on the client — neither half reveals anything alone. Every request passes through a rules engine that enforces spend caps before the key ever reconstructs. Budget blown = key never forms = request never reaches the provider.

Three architectural invariants protect this claim. All three are **Enforced** (CI-tested). The Python PoC has known memory-safety limitations documented below with a concrete Rust hardening path.

---

## Table of Contents

- [Glossary](#glossary)
- [Confidence Tier Definitions](#confidence-tier-definitions)
- [Trust Boundary Diagram](#trust-boundary-diagram)
- [Confidence Summary Table](#confidence-summary-table)
- [Invariant 1: Client-Side Splitting](#invariant-1-client-side-splitting)
- [Invariant 2: Gate Before Reconstruction](#invariant-2-gate-before-reconstruction)
- [Invariant 3: Server-Side Direct Upstream Call](#invariant-3-server-side-direct-upstream-call)
- [Non-Goals](#non-goals)
- [SR Reverse-Mapping Table](#sr-reverse-mapping-table)
- [Known Limitations (Python PoC)](#known-limitations-python-poc)
- [Breach Scenario: Shard B Database Compromise](#breach-scenario-shard-b-database-compromise)
- [Forensic Logging](#forensic-logging)
- [Supply Chain](#supply-chain)
- [License](#license)
- [Residual Risk Summary](#residual-risk-summary)
- [Update Cadence and Ownership](#update-cadence-and-ownership)
- [Changelog](#changelog)

---

## Glossary

| Term | Definition |
|------|-----------|
| **Shard A** | Client-held half of the split key. Format-preserving: same prefix, charset, and length as the original key (SR-12). Lives in the developer's `.env` file. Sent to the proxy per-request via `Authorization: Bearer` (OpenAI) or `x-api-key` (Anthropic). Never stored server-side. |
| **Shard A (in .env)** | Format-preserving split output written to `.env` after enrollment, replacing the original API key. Shard A preserves the prefix, charset, and length of the original key (SR-12), so tools expecting an `*_API_KEY` variable continue to work. Shard A is one half of the XOR split — it is cryptographically bound to the original key but reveals nothing without Shard B. The SDK sends it as `Authorization: Bearer <shard-A>` (OpenAI) or `x-api-key: <shard-A>` (Anthropic) to the proxy. |
| **Shard B** | Server-held half of the split key (the random XOR mask). Encrypted at rest with Fernet. Combined with Shard A only during reconstruction. |
| **Commitment** | HMAC-SHA256 digest binding the original key to both shards. Used to detect tampering during reconstruction. |
| **Nonce** | Random 32-byte value used as the HMAC key for the commitment. Generated via `secrets.token_bytes` (CSPRNG). |
| **Gate** | The rules engine evaluation that happens before any key reconstruction. Checks spend caps, rate limits, and model allowlists. A denied request never touches key material. |
| **Enrollment** | The one-time process where a user's API key is split and Shard B is stored server-side. The full key exists only during the split operation on the client. |
| **Reconstruction** | Recombining Shard A + Shard B via XOR after HMAC verification. Happens server-side, inside a `secure_key` context manager that zeros the buffer on exit. |

---

## Confidence Tier Definitions

| Tier | Meaning | Evidence standard | Bypass requires |
|------|---------|-------------------|-----------------|
| **Enforced** | Property verified by automated CI tests. Regression breaks the build. | Test name in `tests/` that fails if property violated. | Modifying or deleting the test suite (caught in PR review). |
| **Best-effort** | Implementation is correct, but runtime or language constraints prevent a full guarantee. | Code path + specific known gap documented. | Exploiting the runtime gap (e.g., GC non-determinism). |
| **Planned** | Not yet implemented. On the Rust hardening roadmap. | Reference to [ROADMAP.md](.planning/ROADMAP.md). | N/A — property does not exist yet. |

**Hard cap:** No more than 3 items at Best-effort tier. More would indicate an architecture problem, not a documentation gap.

---

## Trust Boundary Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT BOUNDARY                          │
│                                                                 │
│  Developer machine / CI agent                                   │
│                                                                 │
│  ┌──────────┐    api_key    ┌──────────────┐                    │
│  │  .env    │──────────────>│  split_key() │                    │
│  │  file    │               │  (CLI only)  │                    │
│  └──────────┘               └──────┬───────┘                    │
│                                    │                            │
│                          ┌─────────┴─────────┐                  │
│                          │                   │                  │
│                    Shard A (kept)      Shard B + commitment      │
│                    stored locally      + nonce (sent once)       │
│                                              │                  │
├──────────────────────────────────────────────┼──────────────────┤
│                  NETWORK BOUNDARY             │                  │
│                                              │                  │
│  Enrollment: Shard B + commitment + nonce ───┘                  │
│  Request:    Authorization / x-api-key header (Shard A)         │
│                                                                 │
│  *** Full API key NEVER crosses this boundary ***               │
│  *** Reconstructed key NEVER crosses this boundary ***          │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                     PROXY BOUNDARY                              │
│                                                                 │
│  ┌───────────────┐   deny (402/429)   ┌─────────────────────┐  │
│  │ Rules Engine  │───────────────────>│ Client gets error   │  │
│  │ (spend cap,   │                    │ Key never forms     │  │
│  │  rate limit,  │                    └─────────────────────┘  │
│  │  allowlist)   │                                              │
│  └───────┬───────┘                                              │
│          │ allow                                                │
│          v                                                      │
├─────────────────────────────────────────────────────────────────┤
│               RECONSTRUCTION BOUNDARY                           │
│                                                                 │
│  ┌──────────────────┐  ┌─────────────────┐  ┌───────────────┐  │
│  │ Fernet decrypt   │─>│ reconstruct_key │─>│ secure_key()  │  │
│  │ (Shard B)        │  │ (XOR + HMAC)    │  │ context mgr   │  │
│  └──────────────────┘  └─────────────────┘  └───────┬───────┘  │
│                                                     │           │
│            ┌────────────────────────────────────────┘           │
│            │  key_buf (bytearray, zeroed on exit)               │
│            v                                                    │
│  ┌─────────────────┐         ┌──────────────────────────────┐  │
│  │ Upstream call   │────────>│ LLM Provider (OpenAI, etc.) │  │
│  │ (httpx)         │         └──────────────────────────────┘  │
│  └─────────────────┘                                            │
│                                                                 │
│  *** Reconstructed key NEVER returns to proxy layer ***         │
│  *** Reconstructed key NEVER sent in response ***               │
│  *** key_buf zeroed immediately after dispatch ***              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

Invariant violations would be visible as:
- **Inv 1 violated:** `split_key` import appears outside CLIENT BOUNDARY (caught by AST scan)
- **Inv 2 violated:** Arrow from Rules Engine to Reconstruction without a gate check (caught by source ordering test)
- **Inv 3 violated:** Arrow from Reconstruction back across Proxy Boundary (caught by AST containment test)

---

## Confidence Summary Table

| Property | Tier | Evidence | Upgrade Path |
|----------|------|----------|-------------|
| **Invariant 1:** Client-side splitting | **Enforced** | `test_invariants::TestSplitKeyNeverServerSide` (AST + grep scan) | N/A — already enforced |
| **Invariant 2:** Gate before reconstruction | **Enforced** | `test_security_properties::TestGateBeforeDecrypt` (source ordering + AST) | N/A — already enforced |
| **Invariant 3:** Server-side containment | **Enforced** | `test_invariants::TestInvariant3ServerSideContainment` (AST flow analysis) | N/A — already enforced |
| **SR-01:** Bytearray for secrets | **Enforced** | `test_security_properties::TestZeroAfterUse`, `test_security_properties::TestEdgeCases::test_secure_key_rejects_bytes` | N/A |
| **SR-02:** Explicit zeroing | **Enforced** | `test_invariants::TestKeyBufZeroedAfterDispatch` (proxy-style flow + failure path) | N/A |
| **SR-03:** Gate before decrypt | **Enforced** | `test_security_properties::TestGateBeforeDecrypt::test_evaluate_precedes_decrypt_in_proxy_handler` | N/A |
| **SR-04:** Zero telemetry on secrets | **Enforced** | `test_security_properties::TestReprRedaction` (Hypothesis-powered) | N/A |
| **SR-05:** Logging denylist | **Best-effort** | `test_security_properties::TestSanitizeNeverLeaksMessage` covers upstream errors. No CI test for log output scanning. | Add log output capture test or structured logging with schema enforcement. |
| **SR-06:** Sidecar isolation | **Planned** | Python PoC is in-process. Rust hardening: distroless container + seccomp. See [ROADMAP.md](.planning/ROADMAP.md). | Rust reconstruction service in separate process. |
| **SR-07:** Constant-time compare | **Enforced** | `test_security_properties::TestSR07ConstantTimeCompare` (AST scan for hmac.compare_digest) | N/A |
| **SR-08:** CSPRNG only | **Enforced** | `test_security_properties::TestSR08CSPRNGOnly` (AST scan + Ruff TID251 lint rule) | N/A |
| **Memory zeroing completeness** | **Best-effort** | `_zero_buf` zeroes bytearrays. CPython GC may retain intermediate `bytes` copies. | Rust `zeroize` crate with compiler barriers. |
| **Process isolation** | **Planned** | In-process reconstruction in Python PoC. | Rust distroless container + seccomp sandbox. |

**Best-effort count: 2** (under the 3-item cap).

---

## Invariant 1: Client-Side Splitting

**Claim:** The `split_key` function runs exclusively on the client. The server never receives the full API key or Shard A. The server receives only Shard B, commitment, and nonce at enrollment time.

**Confidence:** Enforced

**Evidence:**
- `tests/test_invariants.py::TestSplitKeyNeverServerSide::test_ast_no_split_key_import` — AST scan of every server-side Python file; fails if any imports `split_key`
- `tests/test_invariants.py::TestSplitKeyNeverServerSide::test_grep_no_split_key_string` — String grep catches dynamic imports (`getattr`, `importlib`)
- `tests/test_invariants.py::TestSplitKeyNeverServerSide::test_no_star_import_in_server_modules` — Blocks `from worthless.crypto import *` which would silently pull in `split_key`
- `tests/test_invariants.py::TestSplitKeyNeverServerSide::test_server_files_found` — Guards against vacuously true test (ensures server files exist)
- `tests/test_invariants.py::TestSplitKeyNeverServerSide::test_client_dirs_exist` — Validates the allowlist directories exist

**Mechanism:** Server-side directories are determined by _exclusion_ from a `_CLIENT_DIRS` allowlist (`{"cli", "crypto"}`). New packages land in the server bucket by default — safe by construction.

**Enforcing SRs:** SR-01 (bytearray for all shard types), SR-08 (CSPRNG for mask generation via `secrets.token_bytes`)

**Attacker prerequisites:** Must have code execution in the CLI process to intercept `split_key` input before shards are separated.

**Limitations:**
- Dynamic imports via string concatenation (e.g., `getattr(mod, 'split' + '_key')`) are not caught. This guards against accidental imports, not adversarial code.
- Test-time enforcement, not compile-time. A developer could bypass by modifying the test suite (caught in PR review).

**Rust mitigation:** The `split_key` function will not exist in the reconstruction binary. The Rust reconstruction service imports only XOR + HMAC verification — the split operation lives in a separate client-side crate.

---

## Invariant 2: Gate Before Reconstruction

**Claim:** The rules engine (spend cap, rate limit, model allowlist) evaluates every request BEFORE XOR reconstruction runs. A denied request results in zero KMS calls, zero Fernet decryption, and zero key material touched.

**Confidence:** Enforced

**Evidence:**
- `tests/test_security_properties.py::TestGateBeforeDecrypt::test_evaluate_precedes_decrypt_in_proxy_handler` — Static analysis: `rules_engine.evaluate` appears before `repo.decrypt_shard` in the proxy handler source
- `tests/test_security_properties.py::TestGateBeforeDecrypt::test_fetch_encrypted_returns_encrypted_type` — `EncryptedShard` exposes `shard_b_enc` (ciphertext), not `shard_b` (plaintext)
- `tests/test_security_properties.py::TestGateBeforeDecrypt::test_fetch_encrypted_source_has_no_decrypt_calls` — AST scan confirms `fetch_encrypted` never calls any decrypt method
- `tests/test_security_properties.py::TestGateBeforeDecrypt::test_gate_deny_prevents_decrypt` — Hypothesis-powered: denial return statement precedes decrypt call in source

**Mechanism:** The repository exposes a two-step API: `fetch_encrypted()` returns an `EncryptedShard` (ciphertext only), then `decrypt_shard()` converts it to a `StoredShard`. The rules engine evaluates between these two calls. The denial check includes an early `return Response(...)` before `decrypt_shard` is reached.

**Enforcing SRs:** SR-03 (check before build)

**Attacker prerequisites:** Must bypass the rules engine evaluation or modify the proxy source code to reorder the gate and decrypt calls.

**Limitations:**
- In-process means the gate and reconstruction share memory space. No hardware isolation boundary between them.
- Source ordering test is a heuristic — it checks textual position, not control flow graph.

**Rust mitigation:** Process boundary between rules engine and reconstruction service. Seccomp sandbox restricts reconstruction service syscalls. The reconstruction service accepts pre-authorized tokens from the gate, not raw requests.

---

## Invariant 3: Server-Side Direct Upstream Call

**Claim:** The reconstruction service calls the LLM provider directly. The reconstructed key is contained within a `secure_key` context manager and never returns to the proxy layer, never transits the network, and is zeroed immediately after the upstream HTTP call.

**Confidence:** Enforced

**Evidence:**
- `tests/test_invariants.py::TestInvariant3ServerSideContainment::test_reconstruct_result_flows_through_secure_key` — AST scan: the variable holding `reconstruct_key()` result is passed as argument to `secure_key()` in proxy/app.py
- `tests/test_invariants.py::TestInvariant3ServerSideContainment::test_key_not_used_outside_secure_key_block` — AST scan: the `as k` alias from `with secure_key(key_buf) as k:` is never referenced after the with-block exits
- `tests/test_invariants.py::TestSplitKeyNeverServerSide::test_proxy_app_uses_secure_key` — AST scan: proxy/app.py contains a `with secure_key(...)` statement
- `tests/test_invariants.py::TestKeyBufZeroedAfterDispatch::test_key_buf_zeroed_proxy_style_flow` — Runtime test: key_buf is all zeros after `secure_key` exits
- `tests/test_invariants.py::TestKeyBufZeroedAfterDispatch::test_key_buf_zeroed_on_dispatch_failure` — Runtime test: zeroing happens even when upstream call raises

**Mechanism:** `secure_key()` is a context manager (`contextlib.contextmanager`) that yields the `bytearray` key buffer and calls `_zero_buf(key_buf)` in its `finally` block. `_zero_buf` overwrites the buffer in-place with `bytearray(len(buf))`. The proxy's `finally` block also zeros shard material.

**Enforcing SRs:** SR-01 (bytearray, not bytes), SR-02 (explicit zeroing via `_zero_buf`), SR-04 (redacted `__repr__` on all crypto types)

**Attacker prerequisites:** Must have code execution in the FastAPI process to intercept the reconstructed key during the brief window between reconstruction and zeroing.

**Limitations:**
- Shared memory space: the key exists as a `bytearray` in the same process. No page-level isolation.
- GC non-determinism: intermediate `bytes` objects created during HMAC computation or XOR may linger in CPython's managed heap.
- No `mlock`: the page containing the key buffer can be swapped to disk.
- No compiler barrier: CPython's optimizer could theoretically elide the zeroing (unlikely in practice, but not formally prevented).

**Rust mitigation:**
- `zeroize` crate: deterministic zeroing with compiler barriers (`core::ptr::write_volatile`)
- `mlock`: pin key pages in RAM, prevent swap
- Distroless container: minimal attack surface, no shell
- `seccomp`: restrict syscalls to network + memory only
- Stack-allocated buffers: key material on stack, not heap — deterministic lifetime

---

## Non-Goals

Worthless does **not** protect against:

1. **Compromised client machine.** If an attacker has full access to the CLI process, they can intercept the API key before `split_key` runs. Worthless protects keys _after_ splitting, not before.

2. **Malicious LLM provider.** The provider receives the full API key in the `Authorization` header (that's the point — the request must work). A malicious provider could log or store requests regardless of Worthless.

3. **Side-channel timing attacks on the Python PoC.** Python's runtime makes constant-time guarantees difficult. HMAC verification uses `hmac.compare_digest` (SR-07), but other operations (XOR loop, bytearray allocation) are not constant-time. The Rust hardening phase addresses this.

4. **Memory forensics on the proxy host.** CPython's garbage collector may retain copies of key material. `_zero_buf` clears the primary buffer, but intermediate `bytes` objects from HMAC/XOR computation may persist until GC collects them. See [Known Limitations](#known-limitations-python-poc).

5. **Supply chain attacks on Python dependencies.** Worthless depends on `cryptography` (Fernet), `httpx`, `fastapi`, `aiosqlite`, and others. A compromised dependency could exfiltrate key material. Mitigation: `pip-audit` in CI, but no full SBOM or reproducible builds in V1.

6. **Compromised proxy server.** An attacker with shell access to the proxy host can read process memory, attach a debugger, or modify the application. Worthless assumes the proxy is trusted infrastructure.

7. **Nation-state adversaries with physical access.** Hardware-level attacks, cold boot attacks, or electromagnetic side channels are out of scope for a software-only solution.

---

## SR Reverse-Mapping Table

| SR | Name | Invariant(s) | Tests | Code Location |
|----|------|-------------|-------|--------------|
| SR-01 | Bytearray for secrets | 1, 3 | `test_security_properties::TestZeroAfterUse`, `test_security_properties::TestEdgeCases::test_secure_key_rejects_bytes` | `src/worthless/crypto/types.py` (SplitResult fields), `src/worthless/crypto/splitter.py` (reconstruct_key returns bytearray), `src/worthless/storage/repository.py` (StoredShard fields) |
| SR-02 | Explicit zeroing | 3 | `test_invariants::TestKeyBufZeroedAfterDispatch` (proxy flow + failure path), `test_security_properties::TestZeroAfterUse` | `src/worthless/crypto/types.py::_zero_buf`, `src/worthless/crypto/splitter.py::secure_key` |
| SR-03 | Gate before decrypt | 2 | `test_security_properties::TestGateBeforeDecrypt` (4 tests: source ordering, encrypted type, no-decrypt AST, deny-prevents-decrypt) | `src/worthless/proxy/app.py` (create_app handler: evaluate -> deny check -> decrypt_shard), `src/worthless/storage/repository.py` (fetch_encrypted / decrypt_shard split) |
| SR-04 | Zero telemetry on secrets | 3 | `test_security_properties::TestReprRedaction` (4 Hypothesis tests: SplitResult repr/str, StoredShard, EncryptedShard) | `src/worthless/crypto/types.py::SplitResult.__repr__`, `src/worthless/storage/repository.py::StoredShard.__repr__`, `src/worthless/storage/repository.py::EncryptedShard.__repr__` |
| SR-05 | Logging denylist | 2, 3 | `test_security_properties::TestSanitizeNeverLeaksMessage` (4 Hypothesis tests: message replacement, binary, valid JSON, type preservation) (error sanitization only; no full log capture test) | `src/worthless/proxy/app.py::_sanitize_upstream_error` |
| SR-06 | Sidecar isolation | 3 | None (Planned — Python PoC is in-process) | Architecture decision; Rust hardening delivers true process isolation |
| SR-07 | Constant-time compare | 1, 2, 3 | `test_security_properties::TestSR07ConstantTimeCompare` (3 tests: files exist, compare_digest usage, no equality on digest vars) | `src/worthless/crypto/splitter.py::reconstruct_key` (`hmac.compare_digest`) |
| SR-08 | CSPRNG only | 1 | `test_security_properties::TestSR08CSPRNGOnly` (parametrized AST scan + crypto usage check) | `src/worthless/crypto/splitter.py` (`secrets.token_bytes`), Ruff TID251 lint rule bans `random` module |

---

## Known Limitations (Python PoC)

### Memory Safety: GC Non-Determinism

**What it means:** CPython uses reference counting with a cycle-detecting garbage collector. When `_zero_buf` overwrites a `bytearray`, the primary buffer is cleared. However, intermediate `bytes` objects created during HMAC computation (`hmac.new(...).digest()` returns immutable `bytes`) or XOR operations may linger in the managed heap until the GC collects them.

**Realistic exploit narrative:** An attacker with code execution in the FastAPI process could scan the process heap for byte patterns matching API key prefixes (`sk-`, `anthropic-`). The window is between reconstruction and GC collection of intermediates — typically milliseconds under normal load, but unbounded in theory.

**Attacker prerequisites:** Code execution in the FastAPI process (e.g., via a dependency vulnerability or server-side request forgery leading to code injection).

**Risk level:** Medium. Requires process-level access. The primary buffer IS zeroed; only intermediate copies are at risk.

**Rust mitigation:** `zeroize` crate provides `Zeroize` trait with `Drop` implementation and compiler barrier (`core::ptr::write_volatile`). Stack-allocated buffers with deterministic lifetimes. No GC — memory freed when scope exits.

### Memory Safety: No mlock

**What it means:** The operating system may swap the page containing the key buffer to disk. A forensic analysis of the swap partition could recover key material.

**Attacker prerequisites:** Physical access to the host machine or access to the swap partition/file.

**Risk level:** Low. Requires physical or root access. Most cloud VMs use encrypted swap or no swap.

**Rust mitigation:** `mlock(2)` system call pins key pages in RAM. `madvise(MADV_DONTDUMP)` excludes them from core dumps.

### Memory Safety: No Compiler Barrier

**What it means:** In theory, an optimizing compiler or interpreter could elide the zeroing operation if it determines the buffer is not read after zeroing. CPython's bytecode interpreter does not perform this optimization in practice, but there is no formal guarantee.

**Attacker prerequisites:** None beyond the GC non-determinism attack — this is an additive concern, not an independent attack vector.

**Risk level:** Low. CPython does not optimize away `bytearray` slice assignments in practice.

**Rust mitigation:** `zeroize` uses `core::ptr::write_volatile` which is guaranteed not to be elided. The `Zeroize` trait's `Drop` implementation provides automatic cleanup.

### Process Isolation: In-Process Reconstruction

**What it means:** The reconstruction service runs in the same Python process as the FastAPI proxy. They share memory space. There is no hardware or OS-level isolation between the rules engine and the reconstruction logic.

**Realistic exploit narrative:** A vulnerability in any FastAPI dependency (or in the rules engine itself) could potentially access the reconstruction function or read its memory directly, bypassing the gate.

**Attacker prerequisites:** Code execution in the FastAPI process, plus knowledge of the reconstruction code path.

**Risk level:** Medium. Architectural — cannot be fixed without process separation.

**Rust mitigation:** Reconstruction runs in a separate distroless container with its own memory space. Communication via Unix domain socket or gRPC. `seccomp` restricts the reconstruction process to network + memory syscalls only. The proxy process never has access to the reconstruction service's memory.

### Key Lifecycle: No Bulk Rotation

**What it means:** There is no `worthless revoke` command and no bulk rotation mechanism in V1. If a breach is detected, each affected key must be manually re-enrolled via `worthless enroll`.

**Attacker prerequisites:** N/A — this is an operational limitation, not an exploitable vulnerability.

**Risk level:** Medium (operational). A large-scale breach affecting many keys would require manual re-enrollment of each one.

**Rust mitigation:** Bulk rotation CLI command and API endpoint planned for V2.

### Memory Safety: `api_key.decode()` Creates Immutable str Copy

**What it means:** In `src/worthless/proxy/app.py`, the reconstructed `bytearray` key buffer is decoded to a `str` (`api_key.decode()`) before being passed to httpx as an Authorization header value. Python `str` objects are immutable and cannot be zeroed — the copy persists in the managed heap until the garbage collector reclaims it.

**Realistic exploit narrative:** Same as GC non-determinism — an attacker with code execution in the FastAPI process could scan the heap for the decoded string. The `secure_key` context manager zeros the `bytearray` source, but the `str` copy is beyond reach.

**Attacker prerequisites:** Code execution in the FastAPI process.

**Risk level:** Medium. The `str` copy has an unbounded lifetime in the managed heap. This is noted in code comments but has no programmatic mitigation in the Python PoC.

**Rust mitigation:** The Rust reconstruction service uses stack-allocated byte buffers with `zeroize` — no string conversion required. The HTTP client accepts byte slices directly.

### Shard B Data-at-Rest: Fernet Encryption

**What it means:** Shard B is encrypted at rest using Fernet, and the Fernet key resides on the proxy host's filesystem or environment. If an attacker gains access to the host, the Fernet key is exposed. However, Shard A is never present, so the full API key remains safe.
**Attacker prerequisites:** Full shell/root access to the proxy host machine.
**Risk level:** Low. Compromise of the proxy server is an explicit non-goal. Shard B alone cannot be used to reconstruct the keys.
**Mitigation path:** None for V1. Future phases may explore hardware security modules (HSMs) or external KMS, but the underlying assumption that the proxy host is secure remains central.

### Cryptographic Agility: No Protocol Versioning

**What it means:** The shard storage schema (`shards` table) has no version column. The XOR + HMAC-SHA256 scheme is the only supported protocol. Upgrading to a different scheme requires a migration that touches every stored shard.
**Attacker prerequisites:** N/A (operational limitation).
**Risk level:** Low (operational). Protocol upgrades are infrequent, but without versioning, rolling upgrades are impossible.
**Mitigation path:** Add a `protocol_version` column to the `shards` table (default 1). The reconstruction code path branches on version, enabling gradual migration.

---

## Breach Scenario: Shard B Database Compromise

**Scenario:** An attacker gains read access to the SQLite database containing encrypted Shard B values.

**Immediate impact:** Shard B values are Fernet-encrypted. Without the Fernet key, the ciphertext is useless. The attacker also sees commitments and nonces (not secret — they're HMAC parameters, not key material).

**If Fernet key is also compromised:** The attacker can decrypt all Shard B values. However, Shard B alone is worthless (by design) — reconstructing any API key requires the corresponding Shard A, which is held on the client and never stored server-side.

**If both Shard A and Shard B are compromised:** The attacker can reconstruct the original API key. This requires compromising both the client (Shard A) and the server (Shard B + Fernet key).

**Response procedure:**
1. Rotate the Fernet key (invalidates all encrypted Shard B values)
2. Re-enroll all affected keys via `worthless enroll` (generates new shards)
3. Revoke the compromised API keys at the provider (OpenAI, Anthropic dashboard)
4. No bulk rotation in V1 — each key must be re-enrolled individually

---

## Forensic Logging

**What IS currently logged** (verified from `src/worthless/proxy/app.py`):

| Event | Logged? | Content |
|-------|---------|---------|
| Ambiguous alias inference | Yes | Warning with match count and provider name. No key material. (`logger.warning`) |
| Spend recording failure | Yes | Warning with alias name only. No key material. (`logger.warning`) |
| Gate denials (402/429) | No | Denial responses are returned directly; no log entry for audit trail |
| Enrollment events | No | Enrollment is CLI-side; no server-side log |
| Upstream call success/failure | No | Success/failure not logged |
| Request metadata (IP, model, tokens) | No | Not logged (tokens recorded in spend_log table, not logger) |

**Gaps and recommendations:**
- **Gate denials should be logged** with alias, rule that triggered, and timestamp — essential for anomaly detection and incident response
- **Upstream call failures should be logged** with status code (not response body) for debugging
- **Enrollment events should be logged** server-side when Shard B is stored, for audit trail
- **Spend events are recorded** in the `spend_log` SQLite table (alias, tokens, model, provider, timestamp) but not emitted to the application logger

**Denylist compliance (SR-05):** The proxy logs only alias names and provider names. No API keys, shard bytes, commitments, nonces, request bodies, response bodies, or IP addresses appear in log output. Upstream error messages are sanitized via `_sanitize_upstream_error` before any output (OWASP A09:2021 — Security Logging and Monitoring Failures).

---

## Supply Chain

Dependency auditing: `pip-audit` runs in CI. Full SBOM and supply chain policy ships with enterprise tier.

---

## License

Worthless is licensed under the GNU Affero General Public License v3 (AGPLv3). For enterprise evaluators: AGPLv3 requires that modified versions of the software, when offered as a network service, must make the source code available. Organizations running an unmodified Worthless proxy internally have no additional obligations beyond the standard AGPL terms. A commercial license for teams with AGPL concerns is planned.

---

## Residual Risk Summary

| Risk | Severity | Limitation Reference | Mitigation Status |
|------|----------|---------------------|-------------------|
| GC retains intermediate key copies | Medium | [Memory Safety: GC Non-Determinism](#memory-safety-gc-non-determinism) | Best-effort (primary buffer zeroed; intermediates at GC mercy) |
| `api_key.decode()` creates immutable str copy | Medium | [Memory Safety: `api_key.decode()` Creates Immutable str Copy](#memory-safety-api_keydecode-creates-immutable-str-copy) | Gap — Python `str` cannot be zeroed; Rust eliminates string conversion |
| Key pages swappable to disk | Low | [Memory Safety: No mlock](#memory-safety-no-mlock) | Planned (Rust `mlock`) |
| In-process reconstruction shares memory | Medium | [Process Isolation: In-Process Reconstruction](#process-isolation-in-process-reconstruction) | Planned (Rust distroless container) |
| No bulk key rotation | Medium | [Key Lifecycle: No Bulk Rotation](#key-lifecycle-no-bulk-rotation) | Planned (V2) |
| No protocol versioning for shard schema | Low | [Cryptographic Agility: No Protocol Versioning](#cryptographic-agility-no-protocol-versioning) | Gap — requires schema migration |
| Fernet key on proxy host | Medium | [Shard B Data-at-Rest](#shard-b-data-at-rest-fernet-encryption) | Accepted (non-goal: compromised proxy) |
| No gate denial audit log | Medium | [Forensic Logging](#forensic-logging) | Gap — logging not implemented |
| Zeroing may be elided (theoretical) | Low | [Memory Safety: No Compiler Barrier](#memory-safety-no-compiler-barrier) | Best-effort (CPython does not optimize away in practice) |

---

## Update Cadence and Ownership

**Phase triggers:** Any GSD phase touching `src/worthless/crypto/`, `src/worthless/proxy/`, or `SECURITY_RULES.md` MUST update this document. The phase executor flags affected sections as "needs review"; the security-reviewer agent confirms or updates during the review gate.

**Ownership:** The security-reviewer agent (see `CLAUDE.md` multi-agent review gates) is the designated reviewer for all changes to this document.

**How to flag sections:** Add `<!-- NEEDS REVIEW: [reason] -->` HTML comments inline. These are picked up during review gates.

---

## Changelog

| Date | Change | Author |
|------|--------|--------|
| 2026-04-03 | Initial security posture document. Commit `4f79fe6`. | Phase 05 executor |
