# WOR-31 Security Review: Indistinguishable Decoy Generation

**Date:** 2026-03-30
**Reviewer:** Security review (Claude Code)

## Summary

- **Critical Issues:** 0
- **High Issues:** 1
- **Medium Issues:** 2
- **Low Issues:** 2
- **Info:** 1

---

## HIGH: Unsalted SHA-256 Hash Enables Decoy Verification (Hash Oracle)

**Severity:** HIGH
**Category:** Cryptographic Weakness
**Location:** Design doc, Decision 2 (`decoy_hash = SHA-256(decoy_value)`)

**Issue:**
The design stores `SHA-256(decoy_value)` unsalted in the `enrollments` table. An attacker with DB read access (e.g., stolen `~/.worthless/worthless.db`) can verify which `.env` values are decoys by hashing each value and checking for a match in `decoy_hash`. This is trivial -- iterate the 5-20 values in `.env`, hash each, check against the DB. The entire attack takes microseconds.

**Impact:**
The attacker learns exactly which values are decoys and which are real keys. This defeats the entire purpose of indistinguishable decoys. The threat model (attacker has partial access to the machine) makes DB access plausible -- the DB is a regular file in `~/.worthless/`.

**Remediation:**
Use HMAC-SHA256 keyed with the Fernet key (which is already derived from the user's master key and stored separately):

```python
import hmac
def _decoy_hash(value: str, fernet_key: bytes) -> str:
    return hmac.new(fernet_key, value.encode(), "sha256").hexdigest()
```

This way, an attacker needs both the DB *and* the Fernet key to verify decoy status. Since the Fernet key is derived from the user's master secret (stored in a different location or derived at runtime), compromising just the DB file is insufficient.

**Note:** The design doc explicitly rejected HMAC for *embedding* in the decoy value (Option A/C), which is a different concern. Using HMAC for the *registry hash* does not embed anything in the decoy -- it just makes the lookup table useless without the key.

---

## MEDIUM: Timing Side Channel in `is_known_decoy()`

**Severity:** MEDIUM
**Category:** Side Channel
**Location:** Design doc, Decision 3 (`is_known_decoy()` implementation)

**Issue:**
The proposed `is_known_decoy()` performs a DB query. The code path for "value is a decoy" (query returns a row, skip locking) differs from "value is a real key" (query returns None, proceed to split/encrypt/write). An attacker with timing visibility (e.g., observing `lock` command duration) could infer which keys were skipped as decoys vs. processed as real keys.

**Impact:**
Low practical impact. The attacker would need to observe `lock` execution timing with sub-millisecond precision, and would need to correlate timing to specific `.env` lines. The `lock` command is run locally, not over a network. Additionally, the existing entropy-check path has the same timing differential.

**Remediation:**
No immediate action needed. If this becomes a concern:
- Add a constant-time sleep to equalize both paths (not recommended -- adds latency)
- Process all keys uniformly and discard results for decoys (overcomplicated)

**Verdict:** Accept the risk. Local CLI timing attacks are not in the realistic threat model.

---

## MEDIUM: DB as Single Point of Truth -- Loss Destroys Decoy Identification

**Severity:** MEDIUM
**Category:** Availability / Data Integrity
**Location:** Design doc, Decision 1 (registry-only approach)

**Issue:**
If the SQLite DB (`~/.worthless/worthless.db`) is deleted or corrupted, there is no way to distinguish decoys from real keys. The `lock` command would attempt to re-lock decoy values, treating them as real keys and splitting them into shards. This produces garbage shards that cannot reconstruct any real key.

**Blast radius:**
- **Re-lock after DB loss:** Decoys get "locked" again, producing useless shards. Real keys that were already locked have their decoy values locked (double-lock). Recovery requires manual identification of which `.env` values are real vs. decoy.
- **Scan after DB loss:** All values reported as "status unknown" (acceptable per Decision 4).
- **Unlock after DB loss:** Impossible -- shard_b is in the DB. This is an existing risk, not introduced by WOR-31.

**Remediation:**
This is an inherent tradeoff of the registry-only approach and is documented in the design doc. Mitigations:
1. Document that `~/.worthless/` should be backed up (already good practice since shard_b lives there)
2. Consider a `worthless backup` command that exports the DB
3. The re-lock case should be hardened: if `is_known_decoy()` fails (no DB), refuse to lock rather than silently re-locking decoys

---

## LOW: CSPRNG Usage is Correct

**Severity:** LOW (positive finding)
**Category:** Cryptographic Correctness
**Location:** Design doc, Decision 5 and Per-Provider Spec

**Issue:**
`secrets.choice()` is the correct Python API for cryptographically secure random selection. It delegates to `os.urandom()` via `secrets.SystemRandom`. The proposed usage -- selecting characters from the exact provider charset -- produces output that is computationally indistinguishable from uniform random over that charset.

**One minor pitfall to verify during implementation:**
The `{random:N}` template substitution must use `secrets.choice()` for *each character independently*. If someone accidentally uses `random.choice()` (from the `random` module, which is Mersenne Twister / not cryptographically secure), the decoys become predictable. The design doc correctly specifies `secrets`, but the implementation should be reviewed.

**Remediation:** None needed at design level. Add a code review check during implementation to verify `secrets` (not `random`) is imported.

---

## LOW: Open Source Algorithm Visibility

**Severity:** LOW
**Category:** Security by Obscurity Consideration
**Location:** Design doc, overall approach

**Issue:**
The generation algorithm is public (open source). An attacker who reads the code knows:
- The exact charset per provider
- The structural template (prefix, marker positions, suffix)
- That `secrets.choice()` is used

**Impact:**
None. This is security by design, not obscurity. The properties that matter are:
1. CSPRNG output is indistinguishable from real provider key randomness (both are uniform random over the same charset) -- knowing the algorithm doesn't help
2. No structural fingerprint exists (unlike Canarytokens.org's shared AWS account IDs)
3. The only way to verify if a key is real vs. decoy is to call the provider's API (which the attacker presumably cannot do without revealing themselves)

**Remediation:** None needed. The design correctly avoids security-through-obscurity.

---

## INFO: Provider Format Hardcoding May Drift

**Severity:** INFO
**Category:** Maintenance Risk
**Location:** Design doc, `PROVIDER_FORMATS` dict

**Issue:**
Provider key formats are hardcoded. If OpenAI changes their key format (e.g., drops the `T3BlbkFJ` marker, changes length), generated decoys would no longer match real keys and could become distinguishable.

**Remediation:**
The design doc acknowledges this ("Format registry is data-driven; easy to update"). Consider:
- Adding format version metadata
- Periodic validation against real keys in tests (integration test that generates a key via the provider API and compares format)

---

## Security Checklist

- [x] No hardcoded secrets in design or code
- [x] CSPRNG correctly specified (`secrets.choice`)
- [x] Parameterized SQL queries throughout `repository.py`
- [x] Fernet encryption at rest for shard_b
- [x] File permissions set to 0o600 for shard_a files
- [x] Symlink traversal blocked (`lock.py:63`)
- [x] Alias input validated with regex (`lock.py:186`)
- [x] Atomic DB operations with BEGIN IMMEDIATE (`repository.py:212`)
- [x] Compensation logic for partial failures (`lock.py:150-163`)
- [x] Sensitive data zeroed after use (`sr.zero()`, `StoredShard.zero()`)
- [ ] **Decoy hash should use HMAC, not plain SHA-256** (HIGH finding above)
- [x] No PII in logs or error messages

## Recommendation

**APPROVE WITH CHANGES** -- Address the HIGH finding (switch from SHA-256 to HMAC-SHA256 keyed with the Fernet key) before implementation. The MEDIUM findings are acceptable risks with documentation.
