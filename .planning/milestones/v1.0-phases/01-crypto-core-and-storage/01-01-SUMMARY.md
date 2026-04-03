---
phase: 01-crypto-core-and-storage
plan: 01
subsystem: crypto
tags: [xor, hmac, secrets, key-splitting, memory-zeroing, ruff, tdd]

# Dependency graph
requires: []
provides:
  - split_key function for XOR key splitting with HMAC commitment
  - reconstruct_key function with tamper detection
  - secure_key context manager for memory zeroing
  - SplitResult dataclass with redacted repr
  - ShardTamperedError exception
  - Ruff TID251 ban on random module
affects: [01-crypto-core-and-storage, 02-provider-adapters, cli, proxy]

# Tech tracking
tech-stack:
  added: [cryptography, aiosqlite, pytest, pytest-asyncio, ruff]
  patterns: [TDD red-green, CSPRNG via secrets module, HMAC-SHA256 commitment, bytearray zeroing]

key-files:
  created:
    - src/worthless/crypto/splitter.py
    - src/worthless/crypto/types.py
    - src/worthless/exceptions.py
    - tests/test_splitter.py
    - tests/test_lint.py
    - tests/conftest.py
    - pyproject.toml
  modified:
    - src/worthless/crypto/__init__.py

key-decisions:
  - "Used secrets.token_bytes for CSPRNG (stdlib, no external dependency for randomness)"
  - "HMAC-SHA256 commitment with 32-byte nonce for tamper detection"
  - "bytearray return type from reconstruct_key enables memory zeroing"

patterns-established:
  - "TDD workflow: RED (failing tests) -> GREEN (implementation) -> commit separately"
  - "SplitResult uses frozen dataclass with redacted __repr__ to prevent key material logging"
  - "secure_key context manager pattern for deterministic memory cleanup"

requirements-completed: [CRYP-01, CRYP-02, CRYP-03, CRYP-04]

# Metrics
duration: 18min
completed: 2026-03-14
---

# Phase 1 Plan 1: Crypto Primitives Summary

**XOR key splitting with HMAC-SHA256 tamper detection, bytearray zeroing, and ruff-enforced random module ban**

## Performance

- **Duration:** 18 min
- **Started:** 2026-03-14T17:46:15Z
- **Completed:** 2026-03-14T18:05:02Z
- **Tasks:** 2 + 1 hardening pass
- **Files modified:** 9

## Accomplishments
- XOR split/reconstruct roundtrip verified with HMAC commitment integrity
- Tamper detection: flipping any byte in either shard raises ShardTamperedError
- Memory zeroing via secure_key context manager verified in tests
- Ruff TID251 lint rule bans `random` module imports under src/
- Full TDD cycle: RED (9 failing tests) then GREEN (all 10 pass)
- Security hardening from external test suite review: shard length validation, zeroing on all exceptions, type guards

## Task Commits

Each task was committed atomically:

1. **Task 1: Scaffold project and write failing crypto tests** - `f32e78a` (test)
2. **Task 2: Implement crypto primitives to make tests pass** - `c02d318` (feat)
3. **Security hardening from external test suite** - `ee8bab6` (fix)

## Files Created/Modified
- `pyproject.toml` - Project config with deps, ruff TID251 ban, pytest config
- `src/worthless/__init__.py` - Package init
- `src/worthless/crypto/__init__.py` - Public API exports (split_key, reconstruct_key, secure_key, SplitResult, ShardTamperedError)
- `src/worthless/crypto/types.py` - SplitResult dataclass with redacted __repr__
- `src/worthless/crypto/splitter.py` - Core crypto: split_key, reconstruct_key, secure_key
- `src/worthless/exceptions.py` - ShardTamperedError exception
- `tests/conftest.py` - Shared fixtures (sample_api_key, sample_long_key)
- `tests/test_splitter.py` - 13 tests for CRYP-01, CRYP-02, CRYP-03 (9 original + 4 hardening)
- `tests/test_lint.py` - Lint enforcement test for CRYP-04

## Decisions Made
- Used `secrets.token_bytes` for CSPRNG (stdlib, no external dependency for randomness)
- HMAC-SHA256 with 32-byte random nonce for commitment (timing-safe comparison via hmac.compare_digest)
- `bytearray` return type from reconstruct_key enables deterministic memory zeroing
- Moved ruff `select` to `[tool.ruff.lint]` section to fix deprecation warning

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed ruff config deprecation**
- **Found during:** Task 2 (verification)
- **Issue:** ruff warned that top-level `select` is deprecated in favor of `[tool.ruff.lint]` section
- **Fix:** Moved `select` from `[tool.ruff]` to `[tool.ruff.lint]`
- **Files modified:** pyproject.toml
- **Verification:** ruff check runs without deprecation warning
- **Committed in:** c02d318 (Task 2 commit)

**2. [Rule 2 - Security] Shard length mismatch validation**
- **Found during:** External test suite review (qodo)
- **Issue:** `zip()` in reconstruct_key silently truncates mismatched shard lengths, enabling partial-key reconstruction
- **Fix:** Added explicit length check raising ValueError before XOR
- **Files modified:** src/worthless/crypto/splitter.py, tests/test_splitter.py
- **Verification:** New test_reconstruct_length_mismatch passes
- **Committed in:** ee8bab6

**3. [Rule 2 - Security] Zero buffer on all exceptions**
- **Found during:** External test suite review (qodo)
- **Issue:** reconstruct_key only zeroed buffer on HMAC failure; other exceptions could leak key material
- **Fix:** Wrapped verification in try/except that zeros key on any exception
- **Files modified:** src/worthless/crypto/splitter.py
- **Verification:** All tests pass
- **Committed in:** ee8bab6

**4. [Rule 2 - Security] Type guard on secure_key**
- **Found during:** External test suite review (qodo)
- **Issue:** secure_key accepted non-bytearray inputs, which could fail silently during zeroing
- **Fix:** Added isinstance check raising TypeError for non-bytearray inputs
- **Files modified:** src/worthless/crypto/splitter.py, tests/test_splitter.py
- **Verification:** New test_secure_key_rejects_non_bytearray passes
- **Committed in:** ee8bab6

---

**Total deviations:** 4 auto-fixed (1 bug, 3 security)
**Impact on plan:** All fixes essential for correctness and security. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Crypto primitives ready for use by storage layer (Plan 01-02)
- split_key/reconstruct_key API stable for CLI enrollment and proxy reconstruction
- SplitResult and ShardTamperedError exported from worthless.crypto for downstream use

## Self-Check: PASSED

All 9 created files verified present. All 3 commit hashes (f32e78a, c02d318, ee8bab6) verified in git log.

---
*Phase: 01-crypto-core-and-storage*
*Plan: 01*
*Completed: 2026-03-14*
