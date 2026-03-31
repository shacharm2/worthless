# WOR-31 Design Doc: Indistinguishable Decoy Generation

**Date:** 2026-03-30
**Status:** Draft — awaiting review

---

## Problem Statement

`_make_decoy()` generates keys with a repeating `WRTLS` filler pattern. Anyone inspecting a `.env` file can instantly tell which values are real secrets and which are decoys. This defeats the purpose of decoy-based protection.

## Goal

After this change:
1. A decoy key is statistically indistinguishable from a real provider key (same charset, length, entropy, structural markers)
2. Worthless can still identify its own decoys via a private hash registry
3. The entropy-threshold detection path is removed as the primary decoy identification mechanism

---

## Design Decision 1: Detection Approach

**Decision: Hybrid — HMAC fast-path + hash registry (authoritative)**

| Option | Verdict | Rationale |
|--------|---------|-----------|
| A. HMAC marker only | Rejected | No revocation; key compromise reveals all decoys |
| B. Registry only | Rejected | Requires DB for every check; no offline support |
| C. Hash registry + HMAC | Rejected | HMAC adds complexity for marginal benefit — we always have the DB during lock/scan |
| **D. Hash registry only (simple)** | **Selected** | We always have the DB when locking and scanning. HMAC adds crypto key management complexity with no practical benefit — `lock` and `scan` both already open the DB. Offline CI scanning (without DB) reports findings as "status unknown" which is acceptable. |

**Rationale for simplicity over HMAC:** The research shows HMAC is cryptographically sound, but worthless already requires the SQLite DB for enrollment tracking. Every code path that needs to distinguish decoys from real keys (`lock`, `scan`, `unlock`) already has DB access. Adding HMAC key derivation, HKDF, and key storage is complexity without a use case.

---

## Design Decision 2: Schema

**Decision: Add `decoy_hash` column to the `enrollments` table**

```sql
ALTER TABLE enrollments ADD COLUMN decoy_hash TEXT;
```

- When a decoy is generated, store `SHA-256(decoy_value)` in the enrollment row
- One enrollment = one env var = one decoy hash (1:1 relationship, no new table needed)
- To check if a value is a decoy: `SELECT 1 FROM enrollments WHERE decoy_hash = SHA256(value)`
- Index on `decoy_hash` for fast lookups

**Why not a separate table?** Decoys are always associated with an enrollment. A separate table adds a join and foreign key management for no benefit.

---

## Design Decision 3: Idempotency (re-lock safety)

**Decision: Query DB before locking — skip if value matches `decoy_hash`**

Current flow (`dotenv_rewriter.py:47`):
```python
if shannon_entropy(value) < ENTROPY_THRESHOLD: continue  # skip decoys
```

New flow:
```python
if repo.is_known_decoy(value): continue  # skip our own decoys
```

Where `repo.is_known_decoy(value)` does:
```python
def is_known_decoy(self, value: str) -> bool:
    h = hashlib.sha256(value.encode()).hexdigest()
    return self.conn.execute(
        "SELECT 1 FROM enrollments WHERE decoy_hash = ?", (h,)
    ).fetchone() is not None
```

This replaces the entropy check as the idempotency gate for decoys. The entropy check is **kept** but only for filtering low-entropy placeholders (e.g., `your-api-key-here`, `TODO`, `xxx`).

---

## Design Decision 4: CI/Offline Scanning

**Decision: `scan_files()` without DB reports `is_protected = None` (unknown)**

- With DB: `is_protected = repo.is_known_decoy(value)` → True/False
- Without DB: `is_protected = None` → scanner reports finding as "status unknown"
- CI pipelines that want definitive answers must provide DB path
- This is acceptable: CI scanning is informational, not a security gate

---

## Design Decision 5: Statistical Validation

**Decision: Chi-squared test in the test suite**

Add a test that:
1. Generates N=1000 decoys per provider
2. Generates N=1000 "real-like" keys (same format, independent CSPRNG)
3. Runs chi-squared test on character frequency distributions
4. Asserts p-value > 0.01 (no statistically significant difference)
5. Asserts Shannon entropy of decoys is within ±0.5 bits of real keys

This test proves indistinguishability at the statistical level.

---

## Design Decision 6: Migration (Existing WRTLS Decoys)

**Decision: Re-lock upgrades old decoys automatically**

When `lock` encounters a value that:
- Matches the old WRTLS pattern (`shannon_entropy < 4.5` AND contains "WRTLS")
- Is associated with an enrollment that has `decoy_hash IS NULL`

It replaces the old decoy with a new high-entropy decoy and populates `decoy_hash`.

This is a no-op for users who haven't locked yet, and a seamless upgrade for existing users.

---

## Design Decision 7: Entropy Threshold

**Decision: Keep ENTROPY_THRESHOLD but change its role**

- **Before:** Primary decoy detection mechanism
- **After:** Placeholder filter only — skips values like `your-api-key-here`, `TODO`, `changeme`
- The threshold (4.5 bits) is appropriate for placeholder detection
- Decoy detection moves entirely to the hash registry

The constant stays in `key_patterns.py` but its docstring changes to reflect the new role.

---

## Per-Provider Decoy Generation Spec

```python
PROVIDER_FORMATS = {
    "openai": {
        "prefix": "sk-proj-",
        "charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-",
        "structure": "{prefix}{random:74}T3BlbkFJ{random:74}",
        "total_length": 164,
    },
    "anthropic": {
        "prefix": "sk-ant-api03-",
        "charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-",
        "structure": "{prefix}{random:93}AA",
        "total_length": 108,
    },
    "google": {
        "prefix": "AIzaSy",
        "charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-",
        "structure": "{prefix}{random:33}",
        "total_length": 39,
    },
    "xai": {
        "prefix": "xai-",
        "charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
        "structure": "{prefix}{random:80}",
        "total_length": 84,
    },
}
```

Generation function:
```python
import secrets

def make_decoy(provider: str) -> str:
    fmt = PROVIDER_FORMATS[provider]
    # Build from structure template, replacing {random:N} with CSPRNG chars
    ...
```

---

## Files to Modify

| File | Change |
|------|--------|
| `key_patterns.py` | Add `PROVIDER_FORMATS` dict; tighten Google prefix to `AIzaSy`; update `ENTROPY_THRESHOLD` docstring |
| `lock.py` | Replace `_make_decoy()` with format-aware generator; store `decoy_hash` on enrollment |
| `schema.py` | Add `decoy_hash TEXT` column to enrollments |
| `repository.py` | Add `is_known_decoy(value)` and `set_decoy_hash(enrollment_id, hash)` methods |
| `dotenv_rewriter.py` | Replace entropy-only check with `is_known_decoy()` + entropy check for placeholders |
| `scanner.py` | Wire `is_protected` to `is_known_decoy()` when DB available; `None` when not |
| Tests | Update 6 tests per research report; add chi-squared indistinguishability test |

---

## Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Re-lock tries to lock existing decoys | HIGH | `is_known_decoy()` check before locking |
| Schema migration on existing DBs | MEDIUM | SQLite `ALTER TABLE ADD COLUMN` is safe; column defaults to NULL |
| CI scanning without DB | MEDIUM | `is_protected = None`; document in CLI help |
| Provider key format changes | LOW | Format registry is data-driven; easy to update |
| xAI key length approximation | LOW | Conservative estimate; verify with real key before release |
