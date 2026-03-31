# WOR-31 Research Report: Indistinguishable Decoy Generation

**Date:** 2026-03-30

---

## 1. Provider Key Anatomy

| Provider | Prefix | Charset | Total Length | Structural Rules |
|----------|--------|---------|-------------|------------------|
| OpenAI | `sk-proj-` | base64url `[A-Za-z0-9_-]` | ~164 | Embedded `T3BlbkFJ` marker at position ~82 splitting two ~74-char random segments |
| Anthropic | `sk-ant-api03-` | base64url `[A-Za-z0-9_-]` | 108 | Must end with `AA` (padding artifact). 93 random chars between prefix and suffix |
| Google | `AIzaSy` | base64url `[A-Za-z0-9_-]` | 39 | Flat random after 6-char prefix. No internal structure |
| xAI | `xai-` | alphanumeric `[A-Za-z0-9]` | ~84 | Flat random after prefix. Least documented format |

**No provider uses client-side checksums.** A format-correct fake key is indistinguishable from a real key without an API call.

**Codebase note:** `key_patterns.py` uses `AIza` as Google's prefix — should be tightened to `AIzaSy` (all Google AI keys use the full 6-char prefix).

Full details: `.planning/research/provider-key-formats.md`

---

## 2. Codebase Impact Map

### `_make_decoy()` — Current Implementation
- **Defined:** `lock.py:34-48`
- **Called from:** `lock.py:109` (re-lock), `lock.py:146` (first lock), 3 test calls
- **Behavior:** Generates `prefix + 8-char SHA256 hex + repeating "WRTLS"` — trivially distinguishable

### ENTROPY_THRESHOLD = 4.5 — Dependency Chain
- **Defined:** `key_patterns.py:41`
- **Used by:**
  - `dotenv_rewriter.py:47` — skips values below threshold in `scan_env_keys()` (idempotency gate)
  - `scanner.py:49` — skips values below threshold in `scan_files()`
- **Tests relying on it:**
  - `test_cli_security_hardening.py:309` — asserts decoys ARE below threshold
  - `test_dotenv_rewriter.py:16,23` — hardcoded 4.5 in entropy tests
  - `test_cli_scan.py:67,76` — asserts fake keys ARE above threshold

### `scan_env_keys()` Idempotency Flow
- `dotenv_rewriter.py:27-52`: reads `.env` line by line
- **Line 47:** `if shannon_entropy(value) < ENTROPY_THRESHOLD: continue` — this is how decoys are skipped during re-lock
- If decoys become high-entropy, this gate breaks and re-lock would try to lock already-locked keys

### `scan_files()` — `is_protected` Assignment
- `scanner.py:58`: **hardcoded `is_protected = False`** — always False today
- Lines 37-38: TODO comment acknowledging hash-based enrollment lookup needed

---

## 3. Prior Art Comparison

| System | Mechanism | Weakness |
|--------|-----------|----------|
| Canarytokens.org | Real AWS keys on shared accounts | TruffleHog fingerprints the ~6 shared account IDs — canaries detected statically |
| ggcanary (GitGuardian) | Real AWS keys on customer's own account | No fingerprint; requires AWS infrastructure |
| Thinkst Canary (paid) | Diverse accounts, real keys | Costly; overkill for our use case |
| HoneyBits | Fake creds in config files | Format mimicry only, no verification |
| detect-secrets (Yelp) | Allowlist by hash | Registry-based exclusion |

**Key lesson:** Shared structural fingerprints are fatal. Canarytokens.org was defeated because all canary keys shared a small set of AWS account IDs.

---

## 4. Cryptographic Findings

### CSPRNG Generation
- `secrets.choice(alphabet)` over the exact provider charset produces output that is **computationally indistinguishable** from real keys
- No statistical distinguisher exists against uniform CSPRNG draws

### HMAC-in-Key Viability
- HMAC-SHA256 is a proven PRF — output is indistinguishable from random bytes if the key is secret
- Embedding a truncated HMAC tag in the decoy's random portion is cryptographically sound
- **Risk:** HMAC key compromise reveals all decoys. Mitigate by deriving from user's master secret via HKDF
- Reduces effective entropy slightly (e.g., 238→190 bits for a 40-char key) — still far more than needed

### HMAC vs Registry vs Hybrid

| Approach | Offline? | Entropy Loss | Key Compromise Risk | DB Required? |
|----------|----------|-------------|-------------------|-------------|
| HMAC tag | Yes | Slight | Yes — reveals all decoys | No |
| Registry | No | None | No | Yes |
| Hybrid | Yes (fast path) | Slight | Partial (HMAC only) | Yes (authoritative) |

---

## 5. Tests That Must Change

| Test | File | Current Assertion | Impact |
|------|------|------------------|--------|
| `test_decoy_has_low_entropy` | `test_cli_security_hardening.py:302` | Asserts entropy < 4.5 | **Must invert** — new decoys will have high entropy |
| `test_decoy_not_detected_by_scanner` | `test_cli_security_hardening.py:312` | Relies on entropy filtering | Must change to hash-registry check |
| `test_decoy_not_detected_by_scan_env_keys` | `test_cli_security_hardening.py:325` | Relies on entropy filtering | Must change to hash-registry check |
| `test_decoy_low_entropy_skipped` | `test_scanner.py:42` | Tests entropy skip path | Must change to registry skip |
| `test_skips_low_entropy` | `test_scanner.py:34` | Tests entropy skip path | Keep for placeholder detection |
| `test_low_entropy_placeholder` | `test_dotenv_rewriter.py:12` | Tests entropy skip | Keep for placeholder detection |

---

Full research details in:
- `.planning/research/provider-key-formats.md`
- `.planning/research/prior-art-crypto.md`
