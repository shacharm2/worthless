# Security Review: Shamir 2-of-3 + Rust Sidecar Proposal

**Reviewer**: Security Engineer (Claude Opus 4.6)
**Date**: 2026-04-04
**Verdict**: Proposal is sound. Strengthens posture in 3 of 4 deployment modes. Recommendations below.

---

## 1. Does Shamir 2-of-3 deliver "any single breach = zero info"?

**Finding**: True for 3 of 4 deployment modes. Fails on Docker single-host.

| Mode | Shard A (filesystem) | Shard B (keychain) | Shard C (sidecar) | Single breach = zero info? |
|---|---|---|---|---|
| Local workstation | ~/.config, 0600 | macOS Keychain / Secret Service | Sidecar process (separate user) | **YES** -- three distinct OS trust boundaries |
| Docker single-host | Volume mount | No OS keychain available | Same container or sibling container | **NO** -- all three shards accessible via `docker exec` or host volume read. Two shards likely share a trust domain. |
| Railway/Render PaaS | Ephemeral filesystem | Platform secret store (env var) | Sidecar binary in same container | **PARTIAL** -- platform secrets are a separate domain from filesystem, but sidecar shares the container. Effectively 2 trust domains, not 3. |
| Separate-host (proxy + sidecar) | Host A filesystem | Host A keychain | Host B process | **YES** -- genuine network separation. Strongest mode. |

**Evidence**: Shamir's information-theoretic guarantee is mathematically absolute -- one shard reveals zero bits. But the guarantee is only meaningful if shards actually reside in independent trust domains. In Docker single-host, a volume mount or container exec reaches all shards. The research paper acknowledges MPC has the same limitation on single-host but does not apply the same scrutiny to its own proposal.

**Recommendation**: Document Docker single-host as a "convenience" deployment, not a security-hardened one. Ship a `worthless security-check` command that warns when multiple shards share a trust domain. For Docker, consider making Shard B an environment variable injected via Docker secrets (Swarm) or an external secret store, which at least separates it from the filesystem volume.

---

## 2. Does eliminating Fernet weaken or strengthen posture?

**Finding**: Strengthens. The research's argument is correct.

**Evidence**: The current Fernet key sits on the same filesystem as both shards. Any attack that reads shard A can also read `fernet.key`, making the encryption layer a no-op against every realistic threat. The bootstrap problem document confirms this: "The security claim collapses to steal the Fernet key instead." Fernet's only theoretical value is if an attacker can read the SQLite DB but not the Fernet key file -- but they are on the same volume with the same permissions, so this scenario is empty.

**Edge case considered**: Could Fernet help if a backup/snapshot captures the DB but not the key file? Marginally, but Shamir already covers this -- a DB backup captures at most one shard, which is information-theoretically useless. Shamir is strictly stronger here.

**Recommendation**: Eliminate Fernet. Replace the integrity guarantee (Fernet includes HMAC) with a SHA-256 hash of the original key stored alongside the shards. Verify after reconstruction: `sha256(reconstructed) == stored_hash`. This catches tampering without requiring a secret key.

---

## 3. The blahaj crate claim

**Finding**: RUSTSEC-2024-0398 is real. blahaj is a reasonable choice but warrants scrutiny.

**Evidence**:
- RUSTSEC-2024-0398 documents a coefficient bias in `sharks` where random coefficients were sampled from [1,255] instead of [0,255]. This reduces the entropy of each polynomial and enables brute-force recovery with multiple share sets. The advisory is in the official RustSec database.
- `blahaj` is a fork that fixes this specific issue. However, "maintained fork" needs verification -- fork activity, audit status, and bus factor matter.
- GF(256) Shamir over byte arrays is approximately 100 lines of code. The algorithm is well-documented (SLIP-0039, Wikipedia, Hashicorp Vault's Go implementation).

**Recommendation**: Roll your own ~100-line GF(256) implementation rather than depending on blahaj. Rationale:
1. The algorithm is simple enough to audit in an afternoon.
2. Zero external dependency = zero supply chain risk.
3. Worthless already has Rust in the stack (sidecar). A focused implementation with exhaustive tests is safer than trusting a low-bus-factor fork.
4. Include property-based tests (proptest) proving: (a) any 2-of-3 reconstructs correctly, (b) any 1-of-3 is uniformly distributed, (c) round-trip for all byte values.

---

## 4. Impact on the 3 architectural invariants

**Finding**: All three invariants hold. Two are strengthened.

| Invariant | Current | After Shamir+Sidecar | Change |
|---|---|---|---|
| 1. Client-side splitting | Client splits via XOR | Client splits via Shamir 2-of-3 | **Strengthened** -- information-theoretic vs computational. Server receives one shard, not an encrypted shard. |
| 2. Gate before reconstruction | Rules engine before XOR | Rules engine before sidecar IPC | **Unchanged** -- SR-03 applies identically. Python proxy evaluates rules, then sends shard to sidecar only if approved. |
| 3. Server-side direct upstream call | Reconstruction service calls provider | Sidecar reconstructs and calls provider | **Strengthened** -- key never enters Python's address space at all. Current Python PoC has key in-process (SR-06 notes this). Sidecar enforces true process isolation. |

**Recommendation**: Rewrite invariant 1 to say "Shamir 2-of-3 splitting" instead of "XOR splitting." Invariants 2 and 3 need no changes. Update SR-06 to remove the "Python PoC, architecturally separated" caveat -- the sidecar makes true isolation the default, not a future goal.

---

## 5. New attack surfaces

**Finding**: Four new attack surfaces, all manageable.

### 5a. OS Keychain API attacks
- **Risk**: Malware with accessibility permissions (macOS) or D-Bus access (Linux) can query keychain entries.
- **Mitigation**: On macOS, Keychain ACLs restrict access to the `worthless` binary. On Linux, Secret Service requires the calling process to authenticate via D-Bus. This is no worse than SSH agent forwarding, which the industry accepts.

### 5b. Unix Domain Socket permissions
- **Risk**: Any process running as the same user can connect to the UDS and send a shard + request.
- **Mitigation**: Socket file permissions (0600), plus the sidecar should authenticate the connecting process via `SO_PEERCRED` (Linux) or `LOCAL_PEERPID` (macOS) to verify it is the expected worthless proxy PID. Document this in SECURITY_RULES.md.

### 5c. Sidecar process memory
- **Risk**: ptrace, /proc/pid/mem, or core dumps could leak the reconstructed key during its ~15us lifetime.
- **Mitigation**: The research correctly identifies `PR_SET_DUMPABLE(0)`, `MADV_DONTDUMP`, mlock, and running as a separate user to block ptrace. These are standard hardening. The 15us window is orders of magnitude smaller than the current indefinite-lifetime Python string.

### 5d. Maturin supply chain
- **Risk**: Pre-built wheels from PyPI could be tampered with. Maturin builds introduce a Rust toolchain dependency.
- **Mitigation**: Sign wheels with Sigstore (PEP 740). Publish SLSA provenance. Ship a `--build-from-source` fallback. This is the same trust model as `cryptography`, `pydantic-core`, and `ruff`.

**Recommendation**: Add SR-09 (UDS authentication via SO_PEERCRED) and SR-10 (wheel signing/provenance) to SECURITY_RULES.md.

---

## 6. "Nobody else does this" -- novel or not worth doing?

**Finding**: Novel, not negligent. The gap is real and explained by the bearer token problem.

**Evidence**: The research correctly identifies that the entire MPC/threshold crypto industry targets signing operations (ECDSA/EdDSA) where the private key need never be reconstructed. Bearer tokens are fundamentally different -- they must be transmitted verbatim. This means:
- MPC cannot avoid reconstruction (it can only avoid reconstruction for *computable* operations).
- Envelope encryption (Vault, AWS Secrets Manager, etc.) protects secrets at rest but the decrypted token lives in process memory for its entire usage lifetime.
- No product minimizes the *exposure window* of a bearer token to microseconds.

The research's Akeyless DFC and TLSNotary analysis is accurate -- these are the closest analogues, and both address different problems (encryption key usage and TLS attestation, respectively).

**Recommendation**: This is a genuine differentiator. Market it as "microsecond key exposure" rather than "split-key" -- the exposure window is the concrete security improvement over every alternative, and it is easy to benchmark and verify.

---

## Summary

| Area | Verdict | Action |
|---|---|---|
| Shamir 2-of-3 security | Strong in 3/4 modes, weak in Docker single-host | Document limitations, add `security-check` command |
| Fernet elimination | Correct decision | Replace with SHA-256 integrity check |
| blahaj crate | Real vulnerability fix, but prefer own impl | Write ~100-line GF(256) with proptest suite |
| Architectural invariants | All hold, two strengthened | Update invariant 1 wording, update SR-06 |
| New attack surfaces | Manageable with standard hardening | Add SR-09 (UDS auth), SR-10 (wheel signing) |
| Market novelty | Genuine gap, not an oversight | Lean into microsecond exposure as differentiator |

**Overall**: Proceed with the architecture. The Fernet bootstrap problem is real and this proposal solves it cleanly. Priority risk to address is the Docker single-host trust domain collapse.
