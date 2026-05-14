# Worthless Split-Key Proxy: Formal Threat Model

**Version:** 1.0
**Date:** 2026-04-08
**Status:** Draft for review
**Scope:** Worthless V1 self-hosted deployment (Docker Compose / PaaS)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Trust Boundaries](#2-trust-boundaries)
3. [Attacker Profiles](#3-attacker-profiles)
4. [Attack Trees](#4-attack-trees)
5. [STRIDE Analysis](#5-stride-analysis)
6. [Mitigations Map](#6-mitigations-map)
7. [Accepted Risks](#7-accepted-risks)
8. [Comparative Analysis](#8-comparative-analysis)
9. [Recommendations](#9-recommendations)

---

## 1. System Overview

### Components

| Component | Language | Trust Level | Description |
|-----------|----------|-------------|-------------|
| CLI / Sidecar | Python | Client-trusted | Enrollment, shard storage (OS keychain), `wrap` command |
| Proxy | Python / FastAPI | Server-trusted | mTLS termination, rules engine, metering, audit logging |
| Reconstruction Service | Rust (distroless) | Highest trust | KMS decrypt, XOR recombine, upstream call, memory zeroing |
| KMS | Cloud-managed | External trust | Encrypts Shard B at rest |
| LLM Provider | External | Untrusted upstream | OpenAI, Anthropic API endpoints |

### Data Flow (Normal Request)

```
Client                    Proxy                  Reconstruction         LLM Provider
  |                         |                         |                      |
  |-- Shard A + request --> |                         |                      |
  |                         |-- rules evaluation ---> |                      |
  |                         |   (BEFORE any KMS call) |                      |
  |                         |                         |                      |
  |                         |-- if ALLOWED ---------->|                      |
  |                         |   Shard A + request     |                      |
  |                         |                         |-- KMS decrypt B ---->|
  |                         |                         |   XOR(A, B) = key    |
  |                         |                         |-- upstream call ---->|
  |                         |                         |<-- response ---------|
  |                         |<-- response ------------|                      |
  |<-- response ------------|   (key zeroed)          |                      |
```

### Enrollment Flow

```
Client                    Server (Proxy)
  |                         |
  | 1. Generate random nonce|
  | 2. key XOR nonce = B    |
  | 3. commitment = H(key)  |
  |                         |
  |-- Shard B, commitment,  |
  |   nonce metadata ------>|
  |                         | 4. KMS-encrypt(B)
  |                         | 5. Store encrypted B + commitment
  |                         |
  | 6. Store Shard A in     |
  |    OS keychain          |
```

**Critical invariant:** The full API key and Shard A never leave the client during enrollment. The server receives only Shard B, the commitment hash, and the nonce.

---

## 2. Trust Boundaries

### Boundary Diagram

```
+------------------------------------------------------------------+
|  CLIENT TRUST ZONE                                                |
|  +--------------------+                                          |
|  | OS Keychain        | <-- Shard A stored here                  |
|  +--------------------+                                          |
|  | CLI / Sidecar      | <-- Split logic, enrollment              |
|  +--------------------+                                          |
+------------------------------------------------------------------+
         | mTLS (TB-1)
         v
+------------------------------------------------------------------+
|  PROXY TRUST ZONE                                                 |
|  +--------------------+                                          |
|  | FastAPI Proxy      | <-- Rules engine, metering, audit        |
|  +--------------------+                                          |
|  | Rules DB / Redis   | <-- Spend caps, rate limits              |
|  +--------------------+                                          |
+------------------------------------------------------------------+
         | Internal network / localhost (TB-2)
         v
+------------------------------------------------------------------+
|  RECONSTRUCTION TRUST ZONE (highest privilege)                    |
|  +--------------------+                                          |
|  | Rust Service       | <-- XOR recombine, upstream call         |
|  | (distroless)       |    memory zeroing                        |
|  +--------------------+                                          |
+------------------------------------------------------------------+
         | TLS (TB-3)            | Authenticated (TB-4)
         v                       v
+------------------+    +------------------+
| Cloud KMS        |    | LLM Provider     |
| (external trust) |    | (untrusted)      |
+------------------+    +------------------+
```

### Trust Boundary Crossings

| ID | Boundary | What Crosses | Direction | Protection |
|----|----------|-------------|-----------|------------|
| TB-1 | Client -> Proxy | Shard A, request payload, client cert | Inbound | mTLS, certificate pinning |
| TB-2 | Proxy -> Reconstruction | Shard A, request metadata, authorization token | Inbound | Internal network isolation, auth token |
| TB-3 | Reconstruction -> KMS | KMS decrypt request for Shard B | Outbound | IAM role, TLS |
| TB-4 | Reconstruction -> LLM | Full API key (reconstructed), prompt | Outbound | TLS, provider authentication |
| TB-5 | CLI -> OS Keychain | Shard A read/write | Local | OS keychain ACL, biometric/password |

**Key observation:** Shard A crosses TB-1 and TB-2 on every request. The full API key exists only within the Reconstruction Trust Zone, briefly, before zeroing.

---

## 3. Attacker Profiles

### AP-1: Compromised Server (Insider Threat)

**Description:** Attacker gains shell access or code execution on the proxy or reconstruction service. This includes a malicious operator, a compromised dependency, or an RCE vulnerability.

**Capabilities:**
- Read process memory
- Inspect network traffic between proxy and reconstruction service
- Access encrypted Shard B in storage
- Modify rules engine behavior
- Read environment variables and mounted secrets

**Goal:** Recover full API keys to use directly against LLM providers.

### AP-2: External Network Attacker

**Description:** Attacker positioned on the network between client and server, or between server and upstream provider. Includes ISP-level adversary, compromised CDN, or cloud tenant in same VPC.

**Capabilities:**
- Passive traffic capture
- Active MITM if TLS is misconfigured
- DNS poisoning
- Replay attacks

**Goal:** Intercept API keys or hijack sessions to make unauthorized LLM calls.

### AP-3: Supply Chain Attacker

**Description:** Attacker compromises a dependency (PyPI package, Cargo crate, Docker base image, CI action) to inject malicious code into Worthless components.

**Capabilities:**
- Arbitrary code execution within the compromised component
- Exfiltrate secrets during build or runtime
- Modify application logic silently

**Goal:** Exfiltrate API keys, Shard B material, or KMS credentials.

### AP-4: Stolen Device (Client Compromise)

**Description:** Attacker obtains physical or remote access to a developer's machine where the CLI is installed and Shard A is stored.

**Capabilities:**
- Access OS keychain (if device is unlocked or password is known)
- Read CLI configuration files
- Impersonate the legitimate client
- Access proxy endpoint with stolen client certificate

**Goal:** Use the victim's API key allocation or extract Shard A for offline reconstruction.

### AP-5: Compromised CI/CD

**Description:** Attacker compromises the CI/CD pipeline (GitHub Actions, build system) where Worthless components are built or where API keys may be enrolled.

**Capabilities:**
- Modify build artifacts
- Inject backdoors into Docker images
- Access CI secrets (KMS credentials, enrollment tokens)
- Publish tampered packages

**Goal:** Ship compromised binaries, steal enrollment credentials, or inject persistent backdoors.

---

## 4. Attack Trees

### 4.1 AP-1: Compromised Server

```
GOAL: Recover full API key
|
+-- [A1.1] Memory dump of reconstruction service
|   |-- Requires: shell access to reconstruction host
|   |-- Blocked by: Distroless container (no shell), short key lifetime,
|   |   memory zeroing after use
|   |-- Residual risk: MEDIUM - memory zeroing is best-effort in Rust
|   |   (compiler may optimize away; need volatile writes or zeroize crate)
|   +-- Verdict: PARTIALLY MITIGATED
|
+-- [A1.2] Intercept Shard A in transit (proxy -> reconstruction)
|   |-- Requires: network access between proxy and reconstruction
|   |-- Blocked by: Internal network isolation (same host / localhost)
|   |-- Residual risk: LOW if on same host; MEDIUM if separate containers
|   |   without encrypted channel
|   +-- Verdict: MITIGATED (with caveat on deployment topology)
|
+-- [A1.3] Decrypt Shard B from storage + steal Shard A from request
|   |-- Requires: KMS access + ability to intercept a live request
|   |-- Blocked by: KMS IAM policies (reconstruction service only),
|   |   gate-before-reconstruct (no KMS call if denied)
|   |-- Residual risk: HIGH if attacker has both proxy access AND KMS
|   |   credentials (e.g., compromised cloud IAM role)
|   +-- Verdict: PARTIALLY MITIGATED
|
+-- [A1.4] Modify rules engine to approve all requests
|   |-- Requires: write access to proxy code or config
|   |-- Blocked by: Immutable container images, audit logging
|   |-- Residual risk: MEDIUM - audit log may not be monitored in real-time
|   +-- Verdict: PARTIALLY MITIGATED
|
+-- [A1.5] Exfiltrate KMS credentials from environment
|   |-- Requires: access to reconstruction service environment
|   |-- Blocked by: Distroless (no shell), IAM instance roles (no static
|   |   credentials in env)
|   |-- Residual risk: LOW with instance roles; HIGH with static credentials
|   +-- Verdict: DEPENDS ON DEPLOYMENT
```

### 4.2 AP-2: External Network Attacker

```
GOAL: Intercept or reconstruct API key
|
+-- [A2.1] MITM between client and proxy
|   |-- Requires: break mTLS
|   |-- Blocked by: mTLS with certificate pinning
|   |-- Residual risk: LOW (requires CA compromise or client misconfiguration)
|   +-- Verdict: MITIGATED
|
+-- [A2.2] MITM between reconstruction and LLM provider
|   |-- Requires: position between reconstruction and provider
|   |-- Blocked by: TLS to provider, certificate validation
|   |-- Residual risk: LOW (provider TLS is well-maintained)
|   +-- Verdict: MITIGATED
|
+-- [A2.3] Replay captured request to proxy
|   |-- Requires: captured mTLS session
|   |-- Blocked by: mTLS (attacker lacks client private key), request nonces
|   |-- Residual risk: VERY LOW
|   +-- Verdict: MITIGATED
|
+-- [A2.4] DNS poisoning to redirect client to attacker-controlled proxy
|   |-- Requires: DNS control
|   |-- Blocked by: mTLS certificate validation (client verifies server cert)
|   |-- Residual risk: LOW if cert pinning is implemented; MEDIUM if relying
|   |   on system CA store only
|   +-- Verdict: MOSTLY MITIGATED
```

### 4.3 AP-3: Supply Chain Attacker

```
GOAL: Inject code that exfiltrates key material
|
+-- [A3.1] Compromised Python dependency in proxy
|   |-- Requires: typosquatting or maintainer takeover
|   |-- Blocked by: pip-audit, dependency pinning, Semgrep/Bandit SAST
|   |-- Residual risk: MEDIUM - proxy sees Shard A in transit but not full key
|   +-- Verdict: PARTIALLY MITIGATED (Shard A alone is useless without B)
|
+-- [A3.2] Compromised Cargo crate in reconstruction service
|   |-- Requires: crate maintainer compromise
|   |-- Blocked by: cargo-audit, minimal dependency surface (distroless)
|   |-- Residual risk: HIGH - reconstruction service handles full key
|   +-- Verdict: CRITICAL RISK - minimal deps are essential
|
+-- [A3.3] Compromised Docker base image
|   |-- Requires: base image tampering
|   |-- Blocked by: Image pinning by digest, Trivy/Dockle scanning in CI
|   |-- Residual risk: LOW with digest pinning; HIGH without
|   +-- Verdict: MITIGATED (per recent commit 1057e5f)
|
+-- [A3.4] Compromised CI action or build script
|   |-- Requires: access to CI configuration
|   |-- Blocked by: Branch protection, signed commits, CI audit trail
|   |-- Residual risk: MEDIUM - CI compromise is a common attack vector
|   +-- Verdict: PARTIALLY MITIGATED
```

### 4.4 AP-4: Stolen Device

```
GOAL: Extract Shard A and use victim's API allocation
|
+-- [A4.1] Read Shard A from OS keychain
|   |-- Requires: device access + keychain unlock (password/biometric)
|   |-- Blocked by: OS keychain encryption, device lock
|   |-- Residual risk: HIGH if device is unlocked; LOW if locked with
|   |   strong password/biometric
|   +-- Verdict: DEPENDS ON DEVICE SECURITY
|
+-- [A4.2] Steal client mTLS certificate + Shard A -> impersonate client
|   |-- Requires: A4.1 + access to client cert private key
|   |-- Blocked by: Keychain ACLs, certificate stored separately
|   |-- Residual risk: MEDIUM - if both are in keychain, single unlock
|   |   exposes both
|   +-- Verdict: PARTIALLY MITIGATED
|
+-- [A4.3] Use wrap command directly on stolen device
|   |-- Requires: device access with active session
|   |-- Blocked by: Device lock screen, session timeouts
|   |-- Residual risk: HIGH for unlocked device with active daemon
|   +-- Verdict: WEAK - no per-request authentication by default
|
+-- [A4.4] Extract Shard A for offline combination with stolen Shard B
|   |-- Requires: A4.1 + separate server compromise (AP-1)
|   |-- Blocked by: Requires two independent compromises
|   |-- Residual risk: LOW (requires combining AP-1 + AP-4)
|   +-- Verdict: MITIGATED (split-key design working as intended)
```

### 4.5 AP-5: Compromised CI/CD

```
GOAL: Inject persistent backdoor or steal enrollment credentials
|
+-- [A5.1] Tamper with Docker image in CI
|   |-- Requires: CI pipeline access
|   |-- Blocked by: Image signing, digest pinning, Trivy scanning
|   |-- Residual risk: MEDIUM - depends on image signing enforcement
|   +-- Verdict: PARTIALLY MITIGATED
|
+-- [A5.2] Steal KMS credentials from CI secrets
|   |-- Requires: access to CI secrets store
|   |-- Blocked by: Least-privilege CI roles, short-lived credentials
|   |-- Residual risk: HIGH if CI has production KMS access; LOW if isolated
|   +-- Verdict: DEPENDS ON CI CONFIGURATION
|
+-- [A5.3] Modify pre-commit hooks to skip security checks
|   |-- Requires: repo write access
|   |-- Blocked by: Branch protection on main, PR review requirement
|   |-- Residual risk: LOW
|   +-- Verdict: MITIGATED
```

---

## 5. STRIDE Analysis

### 5.1 Proxy (Python / FastAPI)

| Threat | Category | Description | Severity | Mitigation | Residual Risk |
|--------|----------|-------------|----------|------------|---------------|
| T-P1 | **S**poofing | Attacker impersonates legitimate client | HIGH | mTLS with client certificates | LOW |
| T-P2 | **T**ampering | Attacker modifies request to bypass rules | HIGH | Request integrity via mTLS, input validation | LOW |
| T-P3 | **R**epudiation | User denies making a request | MEDIUM | Audit logging with client cert identity | LOW |
| T-P4 | **I**nfo Disclosure | Shard A leaked in logs | HIGH | Logging denylist (SR-04), no secrets in logs/repr | LOW if enforced |
| T-P5 | **D**enial of Service | Flood proxy to prevent legitimate use | MEDIUM | Rate limiting, pids-limit on container | MEDIUM |
| T-P6 | **E**levation | Bypass rules engine to reach reconstruction | CRITICAL | Gate-before-reconstruct invariant (SR-03) | LOW |

### 5.2 Reconstruction Service (Rust / Distroless)

| Threat | Category | Description | Severity | Mitigation | Residual Risk |
|--------|----------|-------------|----------|------------|---------------|
| T-R1 | **S**poofing | Unauthorized caller invokes reconstruction | CRITICAL | Internal network only, auth token from proxy | LOW-MEDIUM |
| T-R2 | **T**ampering | Attacker modifies Shard A in transit | HIGH | Internal network isolation, request signing | LOW |
| T-R3 | **R**epudiation | Cannot trace which request triggered reconstruction | MEDIUM | Structured audit logging with request ID | LOW |
| T-R4 | **I**nfo Disclosure | Full API key leaked from memory | CRITICAL | Memory zeroing (SR-02), bytearray not bytes (SR-01), distroless (no shell), short key lifetime | MEDIUM |
| T-R5 | **I**nfo Disclosure | Full API key leaked in logs | CRITICAL | Logging denylist, no key material in any log path | LOW if enforced |
| T-R6 | **D**enial of Service | Exhaust KMS quota | MEDIUM | Gate-before-reconstruct (reduces KMS calls to approved requests only) | LOW |
| T-R7 | **E**levation | Attacker gains code execution in Rust service | CRITICAL | Distroless (minimal attack surface), memory safety (Rust), minimal dependencies | LOW-MEDIUM |

### 5.3 CLI / Sidecar

| Threat | Category | Description | Severity | Mitigation | Residual Risk |
|--------|----------|-------------|----------|------------|---------------|
| T-C1 | **S**poofing | Malicious binary replaces CLI | HIGH | Package signing, checksum verification | MEDIUM |
| T-C2 | **T**ampering | Attacker modifies enrolled shard in keychain | MEDIUM | OS keychain integrity, commitment hash verification | LOW |
| T-C3 | **R**epudiation | User claims they didn't enroll a key | LOW | Enrollment audit log on server side | LOW |
| T-C4 | **I**nfo Disclosure | Shard A extracted from keychain | HIGH | OS keychain ACLs, device security | MEDIUM |
| T-C5 | **I**nfo Disclosure | Full API key visible during split operation | HIGH | Immediate zeroing after split (SR-02), bytearray (SR-01) | MEDIUM |
| T-C6 | **D**enial of Service | CLI cannot reach proxy | LOW | Retry logic, clear error messages | LOW |
| T-C7 | **E**levation | CLI process gains elevated privileges | MEDIUM | No setuid, minimal OS permissions | LOW |

### 5.4 Enrollment Flow

| Threat | Category | Description | Severity | Mitigation | Residual Risk |
|--------|----------|-------------|----------|------------|---------------|
| T-E1 | **S**poofing | Attacker enrolls a key they don't own | MEDIUM | Enrollment requires the full key (proves ownership) | LOW |
| T-E2 | **T**ampering | MITM modifies Shard B during enrollment | HIGH | mTLS for enrollment channel | LOW |
| T-E3 | **R**epudiation | Dispute about which key was enrolled | LOW | Commitment hash H(key) stored server-side | LOW |
| T-E4 | **I**nfo Disclosure | Full key exposed during enrollment window | HIGH | Key exists in memory only during split, then zeroed | MEDIUM |
| T-E5 | **T**ampering | Attacker replays enrollment to overwrite shard | MEDIUM | Enrollment idempotency, nonce uniqueness | LOW |

---

## 6. Mitigations Map

This table maps each architectural decision to the threats it blocks.

| Architectural Decision | Threats Blocked | Security Rules |
|----------------------|-----------------|----------------|
| **Client-side splitting** | Server never sees full key: blocks AP-1 from trivial key theft | Core invariant #1 |
| **Gate before reconstruction** | Denied requests never touch KMS or key material: blocks T-P6, T-R6, reduces AP-1 attack window | SR-03, Core invariant #2 |
| **Server-side direct upstream call** | Reconstructed key never transits network: blocks A2.2 for reconstructed key, prevents proxy from seeing full key | Core invariant #3 |
| **mTLS** | Blocks A2.1, A2.3, A2.4, T-P1, T-P2, T-E2 | Network security |
| **Distroless container** | No shell access: blocks A1.1 (makes memory dump harder), A1.5 (no tools to exfiltrate), reduces T-R7 attack surface | Container hardening |
| **Memory zeroing** | Reduces window for A1.1, T-R4, T-C5, T-E4 | SR-01 (bytearray), SR-02 (explicit zeroing) |
| **KMS encryption of Shard B** | Blocks offline decryption of Shard B by AP-1 without KMS access: strengthens A1.3 | Encryption at rest |
| **Logging denylist** | Blocks T-P4, T-R5, prevents key material in logs | SR-04 |
| **Constant-time comparison** | Blocks timing side channels on commitment verification | SR-07 |
| **CSPRNG for nonce generation** | Blocks nonce prediction attacks on T-E5 | SR-08 |
| **Audit logging** | Enables detection of A1.4, supports T-P3, T-R3, T-C3 | Compliance |
| **Image pinning by digest** | Blocks A3.3, A5.1 (base image tampering) | Supply chain |
| **Trivy/Dockle CI scanning** | Detects A3.3 (known vulnerabilities in images) | Supply chain |
| **pip-audit / cargo-audit** | Detects A3.1, A3.2 (known vulnerable dependencies) | Supply chain |
| **Pids-limit on containers** | Mitigates T-P5 (fork bomb DoS) | Container hardening |
| **Network isolation** | Blocks A1.2, strengthens T-R1, T-R2 | Network segmentation |

---

## 7. Accepted Risks

These are known risks that are **explicitly not fully mitigated** in V1, along with the rationale.

### AR-1: Memory Forensics on Reconstruction Service

**Risk:** An attacker with root access to the reconstruction service host can dump process memory and potentially recover the full API key during the brief reconstruction window.

**Why accepted:**
- Distroless container and memory zeroing (zeroize crate) reduce but do not eliminate this risk
- Compiler optimizations may elide zeroing; even `volatile` writes are not guaranteed on all platforms
- True mitigation requires hardware enclaves (SGX/SEV) which are out of V1 scope
- The window is very short (microseconds for XOR + upstream call)

**Severity:** MEDIUM (requires root on the most hardened component)

**Future mitigation:** Hardware enclave support, or moving reconstruction into a TEE.

### AR-2: No Per-Request Client Authentication Beyond mTLS

**Risk:** If a client certificate and Shard A are stolen together (AP-4), the attacker can make requests until the certificate is revoked. There is no per-request MFA or challenge-response.

**Why accepted:**
- Per-request authentication would add unacceptable latency for LLM streaming use cases
- The rules engine (spend cap, rate limit) bounds the damage
- Certificate revocation is the intended response

**Severity:** MEDIUM (bounded by spend caps)

**Future mitigation:** Optional per-request TOTP or hardware token for high-value keys.

### AR-3: Single-Operator Trust Model

**Risk:** In self-hosted deployment, the operator controls both the proxy and reconstruction service. A malicious or compromised operator can extract key material.

**Why accepted:**
- Self-hosted means the operator already trusts themselves with their infrastructure
- The split-key design protects against external compromise, not the infrastructure owner
- Multi-party trust requires Shamir secret sharing or MPC, which is a V2 feature

**Severity:** LOW (by design -- self-hosted operators are the trust root)

**Future mitigation:** Shamir sidecar architecture (already researched) for multi-party deployment.

### AR-4: OS Keychain as Shard A Store

**Risk:** OS keychain security varies by platform. macOS Keychain is strong (hardware-backed on Apple Silicon), but Linux keyring implementations vary. A compromised desktop environment can access keychain without additional authentication.

**Why accepted:**
- OS keychain is the industry standard for credential storage
- Worthless cannot improve on the OS security boundary
- Alternative (encrypted file) would be strictly worse

**Severity:** MEDIUM (platform-dependent)

**Future mitigation:** Document minimum keychain requirements per platform; support hardware tokens (FIDO2) for Shard A storage.

### AR-5: Supply Chain Risk on Reconstruction Service Dependencies

**Risk:** A compromised Cargo crate in the reconstruction service would have access to the full API key during reconstruction (A3.2).

**Why accepted:**
- Minimal dependency surface reduces probability
- cargo-audit catches known vulnerabilities
- Full mitigation requires formal verification of all dependencies, which is impractical

**Severity:** MEDIUM-HIGH (low probability, critical impact)

**Future mitigation:** Dependency vendoring, reproducible builds, SBOM generation and monitoring.

### AR-6: No Real-Time Anomaly Response in V1

**Risk:** Spend velocity anomaly detection exists (V1 Free tier), but automated response (key suspension, alert-then-block) is limited to email/Slack alerts. A fast-moving attacker could exhaust a spend cap before a human responds.

**Why accepted:**
- Spend caps are the hard limit -- they are enforced in the gate, not by anomaly detection
- Anomaly detection is defense-in-depth, not the primary control
- Automated suspension logic is complex to get right without false positives

**Severity:** LOW (spend cap is the real control)

**Future mitigation:** Automated progressive response (reduce rate limit on anomaly, require re-auth).

### AR-7: Proxy-to-Reconstruction Channel Not Encrypted in Docker Compose

**Risk:** In default Docker Compose deployment, proxy and reconstruction communicate over Docker internal network without TLS. A container escape could sniff Shard A.

**Why accepted:**
- Docker internal network is isolated by default
- Adding mTLS between internal services adds significant deployment complexity for self-hosted users
- Container escape is a high-skill attack that implies broader compromise

**Severity:** LOW-MEDIUM (requires container escape)

**Future mitigation:** Optional internal mTLS, Unix domain socket communication, or sidecar proxy (Envoy/Linkerd).

---

## 8. Comparative Analysis

### vs. Traditional API Key Storage (Environment Variable / .env File)

| Dimension | Traditional | Worthless |
|-----------|------------|-----------|
| Key at rest | Plaintext in env/file | Split: Shard A in keychain, Shard B KMS-encrypted on server |
| Key in transit | Sent with every request (TLS only) | Shard A sent; full key reconstructed server-side only |
| Server compromise impact | Full key stolen immediately | Attacker gets encrypted Shard B (useless without KMS) and Shard A only during active requests |
| Spend control | None (provider-side only) | Hard spend cap enforced before reconstruction |
| Revocation | Rotate key at provider | Revoke client cert + delete Shard B (no provider rotation needed) |
| Blast radius of stolen key | Unlimited spend until rotated | Bounded by spend cap; key useless without both shards |

**Verdict:** Worthless provides strictly stronger security for the common case (server compromise, stolen credentials). The split-key design means no single point of compromise yields a usable key.

### vs. HashiCorp Vault with Dynamic Secrets

| Dimension | Vault Dynamic Secrets | Worthless |
|-----------|----------------------|-----------|
| Architecture | Centralized secret store, leases | Distributed split-key, no central store of full key |
| Key visibility | Vault has the full key (or generates it) | No single component ever stores the full key at rest |
| Rotation | Automatic lease-based rotation | Not applicable -- key never stored whole |
| Spend control | Not built-in (separate concern) | Integrated gate-before-reconstruct |
| Operational complexity | High (Vault cluster, unsealing, HA) | Lower (proxy + reconstruction service) |
| Provider support | Must integrate with each provider | Works with any API key (provider-agnostic XOR split) |
| Single point of failure | Vault compromise = all secrets exposed | Server compromise = encrypted Shard B only |

**Verdict:** Vault is more mature for general secret management but stores full secrets centrally. Worthless has a fundamentally stronger security property for API keys specifically: no single compromise yields the full key. However, Worthless is purpose-built for API key proxying and does not replace general secret management.

### vs. Cloud Provider Key Management (AWS Secrets Manager, GCP Secret Manager)

| Dimension | Cloud KMS/Secrets Manager | Worthless |
|-----------|--------------------------|-----------|
| Key at rest | Encrypted by cloud KMS | Split: half in client keychain, half KMS-encrypted on server |
| Access control | IAM policies | mTLS + rules engine + IAM for KMS |
| Spend control | None | Integrated |
| Key visibility to cloud | Cloud provider can see the key | Cloud provider sees only Shard B (useless alone) |
| Vendor lock-in | High | Low (self-hosted, cloud-agnostic) |

**Verdict:** Cloud secret managers protect keys from external attackers but the cloud provider (and any compromised IAM principal) can access the full key. Worthless ensures even the hosting infrastructure never sees the complete key.

---

## 9. Recommendations

### Critical (Address Before V1 GA)

1. **Verify memory zeroing effectiveness in Rust reconstruction service.** Confirm use of the `zeroize` crate with `ZeroizeOnDrop` derive macro. Add integration tests that verify key material is not present in process memory after reconstruction completes. Consider `mlock()` to prevent key material from being swapped to disk.

2. **Document and enforce minimum deployment topology.** Proxy and reconstruction service MUST run on the same host (or with encrypted channel) in production. Docker Compose default should use `network_mode: "none"` for reconstruction service with Unix socket communication to proxy.

3. **Formalize the internal auth token between proxy and reconstruction.** The token must be short-lived, scoped to a single request, and not replayable. Consider HMAC-signed request tokens with timestamp and nonce.

### High Priority (V1.1)

4. **Implement certificate revocation checking (CRL or OCSP).** Without this, a stolen client certificate remains valid until expiry even after the operator knows it is compromised.

5. **Add canary/tripwire detection for reconstruction service tampering.** Runtime integrity monitoring (Falco rules) to detect unexpected process execution, file access, or network connections from the distroless container.

6. **Vendor and pin all Cargo dependencies for reconstruction service.** Given AR-5, the reconstruction service should vendor its dependencies and build from vendored sources only.

### Medium Priority (V1.x)

7. **Investigate SGX/SEV enclave for reconstruction.** This would close AR-1 (memory forensics) completely.

8. **Add optional per-request challenge for high-value keys.** TOTP or FIDO2 challenge for keys above a configurable value threshold.

9. **Implement Shamir secret sharing for multi-party deployments.** Extends the trust model beyond single-operator (closes AR-3 for team deployments).

10. **Add SBOM generation and continuous dependency monitoring.** Automated alerts when any reconstruction service dependency has a new CVE.

---

## Appendix A: Security Rules Cross-Reference

| Security Rule | Threat(s) Addressed | Verification Method |
|---------------|---------------------|---------------------|
| SR-01: bytearray not bytes | T-R4, T-C5, T-E4 (prevents immutable key copies) | SAST rule, code review |
| SR-02: explicit zeroing | T-R4, T-C5, T-E4 (reduces memory exposure window) | Unit test, mutation testing |
| SR-03: gate before reconstruct | T-P6, T-R6, A1.3 (no KMS call if denied) | Integration test, audit log verification |
| SR-04: no secrets in logs/repr | T-P4, T-R5 (prevents log-based exfiltration) | SAST rule, log audit |
| SR-07: constant-time compare | Timing side channels on commitment verification | Unit test with timing measurement |
| SR-08: CSPRNG only | T-E5 (prevents nonce prediction) | Code review, SAST rule |

## Appendix B: Threat Model Maintenance

This threat model should be reviewed and updated:
- Before each major release
- After any security incident
- When new components are added to the architecture
- When deployment topology options change
- After penetration testing exercises
- At minimum annually

**Owner:** Security engineering
**Next scheduled review:** Before V1 GA release
