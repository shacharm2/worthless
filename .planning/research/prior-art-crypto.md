# Research: Indistinguishable Decoy/Canary API Key Generation

**Date:** 2026-03-30
**Task:** WOR-31 — Indistinguishable Decoy Generation
**Status:** Complete

---

## Topic 1: CSPRNG Best Practices for Mimicking Charsets

### Python's `secrets` Module

The `secrets` module (Python 3.6+) is the standard for generating cryptographically secure random data. It wraps the OS-level CSPRNG (`/dev/urandom` on Unix, `CryptGenRandom` on Windows).

**Key functions:**

| Function | Use Case |
|----------|----------|
| `secrets.token_bytes(n)` | Raw random bytes |
| `secrets.token_hex(n)` | Hex-encoded random bytes |
| `secrets.token_urlsafe(n)` | URL-safe base64-encoded random bytes |
| `secrets.choice(alphabet)` | Pick one character from a custom alphabet |

### Generating Keys That Match a Target Charset

To produce a string indistinguishable from a real API key over a given alphabet:

```python
import secrets

def generate_matching_key(alphabet: str, length: int) -> str:
    """Generate a CSPRNG string over the exact charset and length of the target format."""
    return ''.join(secrets.choice(alphabet) for _ in range(length))
```

**Why `secrets.choice` is correct:**
- Each call draws from the OS CSPRNG, so each character position is uniformly random over the alphabet.
- The output has identical statistical distribution to any other uniformly-random string over that alphabet — there is no distinguisher.
- Unlike `random.choice`, there is no predictable PRNG state an attacker can reconstruct.

### Base62 / Base64 Considerations

- **Base62** (`[A-Za-z0-9]`): Common for API keys (e.g., Stripe `sk_live_...`). Use `secrets.choice(string.ascii_letters + string.digits)`.
- **Base64 / URL-safe base64** (`[A-Za-z0-9+/=]` or `[A-Za-z0-9_-]`): Use `secrets.token_urlsafe(nbytes)`. Note: output length = `ceil(nbytes * 4/3)`, so choose `nbytes` to hit the target length.
- **Hex** (`[0-9a-f]`): Use `secrets.token_hex(nbytes)` for `2*nbytes` hex chars.

### Statistical Indistinguishability

A CSPRNG-generated string over alphabet A of length L is **computationally indistinguishable** from any other CSPRNG-generated string over the same A and L, assuming:
1. The generator is a secure CSPRNG (OS-level entropy source).
2. No structural markers or checksums are embedded (see Topic 2).
3. The alphabet and length exactly match the target format.

**Pitfall:** If a real key format includes a checksum, prefix, or embedded metadata (e.g., AWS access keys encode account ID), a decoy missing this structure could be distinguished. The decoy generator must replicate ALL structural constraints of the target format.

### Recommendation for Worthless

Use `secrets.choice()` over the exact charset extracted from the provider's key format. Store the charset and length per provider in a format registry. Never use `random` module.

---

## Topic 2: HMAC-Tag-in-Decoy Attack Surface

### The Idea

Embed a hidden HMAC tag within a decoy key so the system can later verify "this is our decoy" without an external lookup. For example, given a 40-char key, use the first 32 chars as payload and the last 8 chars as `HMAC(secret, payload)[:8]`.

### Theoretical Security: HMAC as a PRF

HMAC is provably a **pseudorandom function (PRF)** under standard assumptions (Bellare 2006, Gavin et al. 2014). This means:

> The output of HMAC is computationally indistinguishable from random bytes, **as long as the HMAC key remains secret**.

Key research:
- Bellare, Canetti, Krawczyk proved NMAC/HMAC is a secure PRF assuming the compression function is a PRF.
- The exact PRF-security of HMAC was tightened by Gavin, Poettering, and Stam (Crypto 2014).
- Distinguishing attacks exist for HMAC-MD5 (Wang et al. 2009) but require ~2^97 queries — not practical.

**Implication:** If the HMAC key is secret and the hash function is secure (SHA-256), the tag bytes are indistinguishable from random bytes. An attacker cannot detect the embedded HMAC without knowing the key.

### Practical Attack Surface

| Attack Vector | Risk Level | Notes |
|--------------|------------|-------|
| **Key compromise** | HIGH | If the HMAC key leaks, attacker can verify all decoys instantly. Single point of failure. |
| **Statistical analysis** | NEGLIGIBLE | HMAC-SHA256 output is PRF; no statistical distinguisher exists for practical query counts. |
| **Structural analysis** | LOW | If attacker knows the scheme (e.g., "last 8 bytes are HMAC of first 32"), they can test candidate HMAC keys. Kerckhoffs' principle: assume scheme is known. |
| **Brute force HMAC key** | LOW | 256-bit HMAC key = 2^256 search space. Infeasible. |
| **Side channel** | MEDIUM | If the system behaves differently on decoy vs real keys (timing, error messages), the marker is irrelevant — behavior leaks the classification. |
| **Reduced entropy** | LOW-MEDIUM | The tag bytes are determined by the payload bytes, reducing the key's effective entropy. For a 40-char base62 key: 40 * log2(62) = ~238 bits total. If 8 chars are HMAC-determined: effective entropy = 32 * log2(62) = ~190 bits. Still far more than needed. |

### Steganographic Markers — General Risk

Research on steganography detection (Fridrich, Goljan, Du 2001; general steganalysis) shows that hidden data can be detected through statistical anomalies. However, these techniques apply to media files (images, audio) where the cover medium has known statistical properties. For random-looking strings:

- There is no "natural" distribution to deviate from — the cover medium IS random.
- An HMAC tag in random bytes produces... more random bytes.
- Detection requires knowing or guessing the scheme AND the key.

### HMAC-in-Key vs External Registry

| Approach | Pros | Cons |
|----------|------|------|
| **HMAC tag in key** | No database needed; O(1) verification; works offline; portable | Key compromise breaks all decoys; reduces entropy slightly; scheme must stay secret for defense-in-depth |
| **External registry** | No entropy reduction; no embedded secret; can revoke/rotate individually | Requires database lookup; must be available at check time; scales with number of decoys |
| **Hybrid** | Best of both: HMAC for fast-path, registry for authoritative check | More complexity |

### Recommendation for Worthless

The HMAC-in-key approach is sound **if the HMAC key is properly protected** (e.g., derived from the user's master secret via HKDF). The theoretical indistinguishability guarantee is strong. The main risk is operational (key management), not cryptographic.

Consider the hybrid approach: HMAC tag for local/offline verification, plus an optional registry for authoritative checks.

---

## Topic 3: Prior Art — Canary/Decoy Credentials

### 3.1 Thinkst Canarytokens

**How they work:**
- Free service at canarytokens.org generates AWS API keys as honey tokens.
- Creates a real AWS IAM user with **zero permissions** on a Thinkst-owned AWS account.
- Generates real AWS access key + secret key for that user.
- When anyone uses the key (e.g., `aws sts get-caller-identity`), CloudTrail logs the attempt.
- A Lambda function parses the log and sends an alert with source IP, user agent, etc.

**Critical vulnerability — static fingerprinting:**
- Tal Be'ery (2023) discovered that AWS access key IDs encode the AWS account ID in characters 5-12.
- TruffleHog (Truffle Security) enumerated ~6 unique AWS account IDs used by canarytokens.org by sampling ~500 canary keys from public GitHub repos.
- **Result:** Any attacker can now identify canarytokens.org keys purely statically, without triggering any alert.
- TruffleHog ships this detection natively.

**Lesson for Worthless:** Canary keys that share infrastructure fingerprints (account IDs, common prefixes, known signing authorities) are detectable. True indistinguishability requires that decoys have no shared structural fingerprint.

**Mitigations by Thinkst:**
- Self-hosted Canarytokens use the customer's own AWS account, defeating the shared-account fingerprint.
- Paid Thinkst Canary product uses diverse accounts.

### 3.2 GitGuardian (ggcanary)

**Approach:**
- Open-source Terraform config (`ggcanary`) to create AWS honey token credentials.
- Uses the customer's own AWS account (avoids the canarytokens.org fingerprint problem).
- Detection via CloudTrail + S3 + Lambda pipeline.
- GitGuardian's scanner uses regex pattern matching + entropy analysis + contextual validation.
- Validity checks: non-intrusive API calls to verify if a detected secret is live.

**Relevance:** GitGuardian does NOT have a "known fake" allowlist. They detect secrets by format, not by checking a canary registry. This means format-matching decoys would be flagged as real secrets by GitGuardian — which is actually desirable for Worthless (decoys should look real to scanners).

### 3.3 TruffleHog

**Approach:**
- Scans for 800+ secret types using regex + verification.
- For each detected secret, attempts to verify it's live by making a non-intrusive API call.
- **Canary detection:** Specifically identifies canarytokens.org AWS keys by account ID.
- Cannot detect self-hosted canary keys or keys from unknown canary services.

**Key insight:** TruffleHog's canary detection works because canarytokens.org uses a small, known set of AWS accounts. If decoy keys don't share a common fingerprint, TruffleHog cannot distinguish them from real keys.

### 3.4 Other Tools and Approaches

| Tool/Project | Approach | Detection Method |
|-------------|----------|-----------------|
| **Canarytokens.org** | Real AWS keys on shared accounts | CloudTrail monitoring; vulnerable to account ID fingerprinting |
| **ggcanary** (GitGuardian) | Real AWS keys on customer accounts | CloudTrail monitoring; resistant to fingerprinting |
| **SpaceCrab** (Spacelift) | AWS honey tokens | Similar CloudTrail approach |
| **Tracebit** | Canary AWS credentials | Emphasizes using customer's own account; realistic IAM user names |
| **HoneyBits** | Fake credentials in config files | Format mimicry; no verification endpoint |
| **detect-secrets** (Yelp) | Allowlist-based exclusion | Known false positives can be allowlisted by hash |

### 3.5 How Real vs Fake is Determined

Three fundamental approaches exist:

1. **Registry lookup:** Check the key against a database of known decoys. Requires network access. Used by: custom solutions, enterprise canary platforms.

2. **Structural markers:** The key itself contains a detectable pattern (e.g., specific account ID, HMAC tag, known prefix). Used by: canarytokens.org (unintentionally — the shared account IS the marker). Vulnerable to: static analysis if the marker is public.

3. **Verification (liveness check):** Try to use the key against the real API. If it works, it's real (or a canary). If it fails, it's fake. Used by: TruffleHog, GitGuardian. Cannot distinguish "real" from "canary with zero permissions."

**For Worthless:**
- Decoy keys should pass format validation (regex, charset, length, prefix).
- Decoy keys should NOT be verifiable as fake via liveness checks (they will fail authentication, which is the same as a revoked real key — indistinguishable).
- HMAC tags provide a private marker that is cryptographically indistinguishable from random (Topic 2).
- No shared infrastructure fingerprint should exist across decoys.

---

## Synthesis: Design Principles for Worthless Decoy Generation

### Principle 1: Format Fidelity
Decoys must exactly match the target provider's key format: prefix, charset, length, any structural rules (checksums, embedded metadata). Maintain a per-provider format registry.

### Principle 2: Cryptographic Randomness
Use `secrets.choice()` over the provider's exact alphabet. Every non-structural byte must be uniformly random from the OS CSPRNG.

### Principle 3: Private Marker (HMAC Tag)
Embed an HMAC-SHA256 tag (truncated) within the key's random portion. The HMAC key must be derived from the user's master secret via HKDF. This allows O(1) offline verification that a key is a decoy, with no external lookup required. The tag is computationally indistinguishable from random bytes.

### Principle 4: No Shared Fingerprint
Unlike canarytokens.org, decoys must not share any common structural element (account ID, issuer prefix, signing authority) that could be enumerated.

### Principle 5: Behavioral Indistinguishability
A decoy key that is used against the real API should produce the same error as a revoked or invalid real key. Since decoys are never registered with the provider, they will fail authentication — identical to an expired/revoked key.

---

## Sources

- [Python secrets module documentation](https://docs.python.org/3/library/secrets.html)
- [Bellare — Exact PRF-Security of NMAC and HMAC (2014)](https://eprint.iacr.org/2014/578.pdf)
- [PRFs, PRPs and other fantastic things — Cryptography Engineering](https://blog.cryptographyengineering.com/2023/05/08/prfs-prps-and-other-fantastic-things/)
- [TruffleHog Now Detects AWS Canaries](https://trufflesecurity.com/blog/canaries)
- [Thinkst — AWS API Key Canarytoken (2017)](https://blog.thinkst.com/2017/09/canarytokens-new-member-aws-api-key.html)
- [Thinkst — AWS Infrastructure Canarytoken (2025)](https://blog.thinkst.com/2025/09/introducing-the-aws-infrastructure-canarytoken.html)
- [Tracebit — Deploying Effective Canary AWS Credentials](https://tracebit.com/blog/deploying-effective-canary-aws-credentials)
- [GitGuardian ggcanary on GitHub](https://github.com/GitGuardian/ggcanary)
- [GitGuardian — HMAC Secrets Explained](https://blog.gitguardian.com/hmac-secrets-explained-authentication/)
- [Canarytokens.org AWS Key Documentation](https://docs.canarytokens.org/guide/aws-keys-token.html)
- [Summit Route — Guidance on Deploying Honey Tokens](https://summitroute.com/blog/2018/06/22/guidance_on_deploying_honey_tokens/)
- [AWS Honey Tokens: The Good, the Bad, and the Ugly — DeceptIQ](https://deceptiq.com/blog/aws-honey-tokens-good-bad-ugly)
- [Acalvio — Understanding Honeytokens](https://www.acalvio.com/resources/glossary/honeytoken/)
- [Huntress — What Is a Honey Token?](https://www.huntress.com/cybersecurity-101/topic/what-is-honey-token)
