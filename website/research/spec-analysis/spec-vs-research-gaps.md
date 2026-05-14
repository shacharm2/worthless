# Spec vs Research: Gap Analysis

**Date:** 2026-04-04
**Reviewer:** Security Engineer (Claude Opus 4.6)
**Verdict:** The spec incorporates ~80% of the research correctly. Five gaps need resolution before build.

---

## 1. Security Tiers -- MISSING FROM SPEC

**Research said:** The synthesis defined three explicit tiers with per-platform honest claims:
- Tier 1 (3 real trust domains): macOS Keychain, Docker multi-container, K8s
- Tier 2 (2.5 domains): Windows DPAPI, Linux desktop keyring, WSL2
- Tier 3 (2 domains): Linux headless encrypted file, PaaS env vars, CI

**Spec says:** Section 11 ("Honest positioning") gives one blanket claim: "You need both, simultaneously, to reconstruct a key." No tiers mentioned. No per-platform honest claim. All platforms presented as equally secure.

**Gap:** This is a **marketing honesty problem**. The spec's single claim is true for Tier 1 but misleading for Tier 3 (where a same-UID attacker reaches both shards). The synthesis explicitly designed tiered claims to prevent this.

**Fix:** Add a "Security Tiers" section to the spec mirroring the synthesis. Each platform in the auto-detection waterfall (Section 6) should map to a tier. The `worthless status` command should report which tier is active.

---

## 2. blahaj Crate -- SPEC CONTRADICTS RESEARCH

**Research said:** Both the security review and crypto verification recommended **rolling your own ~100-line GF(256) implementation** instead of depending on blahaj. Reasons:
- blahaj has ~823 all-time downloads (low adoption, low bus-factor)
- The crypto verification called "maintained" a stretch
- GF(256) Shamir is ~100 lines, auditable in an afternoon
- Zero supply chain risk vs. trusting a low-adoption fork
- Security review: "Roll your own ~100-line GF(256) implementation rather than depending on blahaj"

**Spec says:** Section 4: "Use the `blahaj` Rust crate (MIT/Apache-2.0) -- maintained fork of `sharks`." Lists it in the dependency table (Section 5).

**Gap:** Direct contradiction. The spec chose the option the research explicitly argued against.

**Fix:** Either (a) implement ~100-line GF(256) with proptest suite as recommended, or (b) document in the spec why blahaj was chosen despite the research, with a mitigation plan (vendoring, audit commitment, replacement criteria).

---

## 3. Kernel Keyring Reboot Problem -- ACKNOWLEDGED BUT NOT RESOLVED

**Research said:** The kernel keyring research and the fallback research both converged on a specific solution:
1. At enrollment: encrypt Shard B with a random AES-256-GCM key stored in `@u` keyring
2. Also store the encrypted shard on disk as a persistent backup
3. On reboot: re-derive the keyring key from machine-id + install salt, or re-inject from the encrypted file
4. The synthesis shows this as the recommended architecture diagram

**Spec says:** Open Decision #2 acknowledges the problem ("keyctl @u doesn't survive reboot") but offers no resolution. Section 6 for kernel keyring just says: "Note: does not persist across reboots -- re-inject at startup from backup or orchestrator."

**Gap:** The research provided a concrete fallback flow. The spec deferred it to build-time. This is a deployment-blocking UX issue -- every Linux server that reboots loses all enrolled keys until someone manually re-injects.

**Fix:** Incorporate the research's recommended architecture: encrypted file backup + keyring runtime cache. The sidecar startup sequence (Section 5) should include a step: "If kernel keyring is empty but encrypted shard file exists, decrypt and re-inject."

---

## 4. Fallback Encrypted Shard -- MAJOR GAP

**Research said:** The fallback research recommended specific crypto for the headless fallback:
- **Tier 2 (kernel keyring available):** AES-256-GCM with key stored in `@u` keyring
- **Tier 3 (no keyring):** Argon2id (or HKDF-SHA256) key derivation from `/etc/machine-id` + install-time salt, then AES-256-GCM encryption of Shard B
- The research was explicit: the encrypted file is defense-in-depth, not a real trust domain

**Spec says:** Section 6, "Fallback -- Separate-Path File": just stores Shard B as a plaintext file at `~/.local/share/worthless/shard_b/<alias>` with chmod 600. No encryption. No key derivation. No Argon2id.

**Gap:** The spec's fallback is the weakest option the research considered (the "baseline" that Tier 3 improves upon). The research explicitly rated plaintext file storage as "Weak" and recommended at minimum HKDF-based machine-binding. The spec doesn't even use HKDF.

**Fix:** The `SeparatePathFileStore` should encrypt the shard with a machine-derived key (HKDF from machine-id + install salt at minimum). This won't stop same-machine attackers but defeats file-copy and backup exfiltration attacks. The research's Mechanism 1 (MBKD) is the minimum viable implementation.

---

## 5. Docker Multi-Container -- CORRECTLY INCORPORATED

**Research said:** Proxy + sidecar as separate containers. Shard A on proxy volume, Shard B as Compose secret on sidecar only. UDS on tmpfs shared volume.

**Spec says:** Section 6 (Docker) and Section 8 (socket path) match exactly. The synthesis's Docker Compose YAML is faithfully reflected in the spec's architecture.

**Verdict:** No gap. Well incorporated.

---

## 6. MLOCK Limits -- CORRECTLY INCORPORATED (minor note)

**Research said:** The process isolation research mentioned mlock per-process limits (default 64KB on most Linux). A single shard (~50 bytes) uses one 4KB page.

**Spec says:** Open Decision #6: "Default RLIMIT_MEMLOCK is 64KB on most Linux -- each 50-byte key uses one 4KB page, so ~16 concurrent."

**Verdict:** Correctly incorporated. The process isolation research didn't flag this as a problem because it focused on single-shard scenarios; the spec correctly extrapolated to concurrent requests. The 16-concurrent limit is realistic for solo dev / small team usage. May need revisiting for high-traffic deployments.

**Note:** The process isolation research did mention mlock limits in the context of "mlock has per-process limits (default 64KB)" for the volatile shard mechanism. The spec's extrapolation to concurrent request capacity is a reasonable addition.

---

## 7. macOS -A Flag -- CORRECTLY INCORPORATED

**Research said:** The synthesis confirmed `-A` flag on `security add-generic-password` pre-authorizes all apps, no popup on subsequent reads. No sudo, no codesigning required.

**Spec says:** Section 6 (macOS -- Keychain): "Flags: use -A (allow all apps) at enrollment -- no popup on subsequent reads. No sudo, no codesigning required for -A path."

**Verdict:** No gap. Verbatim match with research.

---

## 8. Spec Additions Not Covered by Research

### 8a. Vault Mode

**What the spec adds:** A `worthless get <alias>` command that returns the reconstructed key to stdout. Designed for non-LLM APIs (Stripe, Twilio).

**Research coverage:** None of the research documents discuss vault mode. All research assumes proxy mode (sidecar makes the upstream call).

**Risk assessment:** Vault mode has a fundamentally different security profile -- the key enters the caller's process memory. The security review (Section 4, Invariant 3) notes this explicitly: "key never enters Python's address space" applies only to proxy mode. The spec acknowledges this in Section 5 (Key lifetime) and Section 11 ("What this architecture does NOT protect against", item 2), but the research never validated the vault mode threat model.

**Recommendation:** Not a gap per se (the spec is honest about vault mode's weaker guarantees), but the security review should be updated to explicitly evaluate vault mode as a separate attack surface.

### 8b. Mnemonic Backup (Shard C)

**What the spec adds:** Shard C displayed as BIP39-style mnemonic phrase at enrollment. Open Decision #1 discusses format options.

**Research coverage:** The synthesis mentions "Shard C printed to terminal as backup, user stores offline" but does not discuss mnemonic encoding. No research on BIP39 word lists, encoding density, or UX testing.

**Risk assessment:** Low risk. BIP39 encoding is well-understood. The word list is a build-time dependency, not a security concern. Open Decision #1 is reasonable.

### 8c. Proxy Streaming (SSE)

**What the spec adds:** Open Decision #3 asks whether the sidecar should stream SSE responses through the Unix socket or buffer them.

**Research coverage:** The crypto verification (Section 6) discusses TLS write timing and hyper buffer lifecycle but does not address SSE streaming through the IPC layer.

**Risk assessment:** Medium complexity, low security risk. Streaming affects UX (time-to-first-token) but doesn't change the security model -- the key is already zeroed before the response arrives.

### 8d. SHA-256 Integrity Check

**Research said:** The security review recommended replacing Fernet's HMAC integrity guarantee with "a SHA-256 hash of the original key stored alongside the shards. Verify after reconstruction."

**Spec says:** No mention of integrity verification after reconstruction.

**Gap:** Minor but real. Without an integrity check, a corrupted shard silently produces a wrong key, which then fails at the provider with an opaque "invalid API key" error. SHA-256 verification would catch this at reconstruction time with a clear error message.

**Fix:** Add to Section 4 or 5: store `sha256(original_key)` alongside shards at enrollment. After reconstruction, verify the hash before using the key.

### 8e. UDS Authentication (SO_PEERCRED)

**Research said:** The security review recommended SR-09: authenticate the connecting process via `SO_PEERCRED` (Linux) or `LOCAL_PEERPID` (macOS) to verify it is the expected worthless proxy PID.

**Spec says:** Section 8 (Socket Protocol) describes the socket path and message format but does not mention peer authentication.

**Gap:** Without UDS authentication, any same-UID process that knows the socket path can send requests to the sidecar and reconstruct keys (if they can supply Shard A). The security review flagged this as attack surface 5b.

**Fix:** Add to Section 8: sidecar authenticates the peer PID via SO_PEERCRED/LOCAL_PEERPID and maintains a list of authorized PIDs (the proxy PID registered at startup).

### 8f. Windows Process Hardening

**Research said:** The process isolation research recommended restrictive DACL + suspended-start pattern to eliminate the startup race window. Also recommended stripping `SeDebugPrivilege` from the token.

**Spec says:** Section 7 (Windows): mentions `SetProcessMitigationPolicy` and `IsDebuggerPresent()` check. Calls it "Less robust than Linux/macOS kernel-level protection."

**Gap:** The research's recommendations (DACL restriction, suspended-start pattern, SeDebugPrivilege stripping) are more specific and effective than the spec's approach (mitigation policy + debugger check). The spec undersells what is achievable on Windows.

**Fix:** Replace Section 7's Windows implementation with the research's recommended approach: restrictive DACL at startup, suspended-start pattern from Python parent, VirtualLock on shard memory.

### 8g. PR_SET_NO_NEW_PRIVS

**Research said:** The process isolation research listed `prctl(PR_SET_NO_NEW_PRIVS, 1)` as a required step before installing seccomp-BPF. The synthesis includes it in the sidecar hardening table.

**Spec says:** Section 5 mentions seccomp-BPF but does not mention PR_SET_NO_NEW_PRIVS.

**Gap:** Minor. PR_SET_NO_NEW_PRIVS is a prerequisite for unprivileged seccomp installation. The build will discover this, but it should be in the spec.

**Fix:** Add to Section 5 startup sequence, before the seccomp step.

---

## Summary

| # | Item | Status | Severity | Action |
|---|------|--------|----------|--------|
| 1 | Security tiers | Missing | **High** | Add tiered claims per platform |
| 2 | blahaj vs self-impl | Contradicts research | **High** | Resolve: self-implement or justify blahaj |
| 3 | Kernel keyring reboot | Unresolved | **Medium** | Incorporate encrypted-file-backup flow |
| 4 | Fallback encrypted shard | Plaintext instead of encrypted | **High** | Add HKDF + AES-256-GCM to SeparatePathFileStore |
| 5 | Docker multi-container | Correct | None | -- |
| 6 | MLOCK limits | Correct | None | -- |
| 7 | macOS -A flag | Correct | None | -- |
| 8a | Vault mode | New (unvalidated) | Low | Review separately |
| 8b | Mnemonic backup | New (reasonable) | Low | -- |
| 8c | Proxy streaming | New (unresolved) | Low | -- |
| 8d | SHA-256 integrity | Missing from spec | **Medium** | Add hash verification after reconstruction |
| 8e | UDS authentication | Missing from spec | **Medium** | Add SO_PEERCRED/LOCAL_PEERPID |
| 8f | Windows hardening | Weaker than research | Low | Upgrade to DACL approach |
| 8g | PR_SET_NO_NEW_PRIVS | Missing from spec | Low | Add to startup sequence |

**Three high-severity gaps** must be resolved before build begins: security tiers, blahaj decision, and fallback encryption.
