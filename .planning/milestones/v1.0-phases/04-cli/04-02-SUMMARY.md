---
phase: 04-cli
plan: 02
subsystem: cli
tags: [typer, xor-splitting, fernet, dotenv, shards, lock-unlock]

requires:
  - phase: 04-cli
    provides: Typer app, bootstrap, dotenv_rewriter, key_patterns, console, errors
  - phase: 01-crypto-core
    provides: split_key, reconstruct_key, SplitResult
  - phase: 03.1-proxy-hardening
    provides: ShardRepository, StoredShard, EncryptedShard
provides:
  - lock command: .env scan, split, shard storage, prefix-preserving decoy rewrite
  - unlock command: key reconstruction, .env restoration, shard cleanup
  - enroll command: lower-level single-key enrollment primitive
  - ShardRepository.delete() method
  - Metadata files (.meta) for alias-to-var_name mapping
affects: [04-03, 04-04]

tech-stack:
  added: []
  patterns: [low-entropy decoy for idempotent re-scan, deterministic alias via sha256, metadata sidecar files]

key-files:
  created:
    - src/worthless/cli/commands/__init__.py
    - src/worthless/cli/commands/lock.py
    - src/worthless/cli/commands/unlock.py
    - tests/test_cli_lock.py
    - tests/test_cli_unlock.py
  modified:
    - src/worthless/cli/app.py
    - src/worthless/cli/bootstrap.py
    - src/worthless/storage/repository.py

key-decisions:
  - "Low-entropy decoy pattern (WRTLS filler) keeps Shannon entropy below 4.5 threshold for idempotent lock"
  - "Deterministic alias: provider + sha256(key)[:8] hex for reproducible enrollment"
  - "Metadata sidecar (.meta JSON) stores var_name for .env restoration on unlock"
  - "WORTHLESS_HOME env var support for test isolation and custom install paths"

patterns-established:
  - "Low-entropy decoy: prefix + 8-char hash tag + repeating WRTLS filler"
  - "Metadata sidecar pattern: {alias}.meta alongside shard_a file"
  - "Register commands via register_*_commands(app) functions in command modules"

requirements-completed: [CLI-01]

duration: 7min
completed: 2026-03-26
---

# Phase 04 Plan 02: Lock/Unlock Commands Summary

**Lock/unlock lifecycle: .env key splitting with XOR shards, prefix-preserving decoys, and lossless round-trip restoration**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-26T21:44:28Z
- **Completed:** 2026-03-26T21:51:48Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments
- Lock command scans .env, splits API keys via XOR, stores shard_a locally (0600) and shard_b encrypted in SQLite
- Unlock command reconstructs original keys from shards and restores .env losslessly
- Enroll command provides lower-level CLI primitive for scripting/CI key enrollment
- 15 passing tests covering round-trip, idempotency, prefix preservation, error cases

## Task Commits

Each task was committed atomically (TDD red/green):

1. **Task 1: lock command** - `9beade6` (test) -> `5041059` (feat)
2. **Task 2: unlock command** - `c307222` (test) -> `5fbbce7` (feat)

_TDD: each task has separate RED (test) and GREEN (feat) commits_

## Files Created/Modified
- `src/worthless/cli/commands/__init__.py` - Command module package
- `src/worthless/cli/commands/lock.py` - Lock and enroll commands: scan, split, store, rewrite
- `src/worthless/cli/commands/unlock.py` - Unlock command: reconstruct, restore, cleanup
- `src/worthless/cli/app.py` - Registered lock, enroll, unlock commands
- `src/worthless/cli/bootstrap.py` - Fixed schema column name (alias -> key_alias)
- `src/worthless/storage/repository.py` - Added delete() method
- `tests/test_cli_lock.py` - 9 tests for lock and enroll
- `tests/test_cli_unlock.py` - 6 tests for unlock and round-trip

## Decisions Made
- Low-entropy decoy pattern (tag + repeating WRTLS) keeps Shannon entropy below scanner threshold for idempotent re-scan
- Deterministic alias generation (provider-sha256[:8]) enables idempotent enrollment checks
- Metadata sidecar files (.meta JSON) store var_name mapping for .env restoration
- WORTHLESS_HOME env var enables test isolation without monkeypatching

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed bootstrap schema column name mismatch**
- **Found during:** Task 1 (lock command implementation)
- **Issue:** Bootstrap `_init_db` created column `alias` but ShardRepository uses `key_alias`
- **Fix:** Changed bootstrap schema to use `key_alias` matching the async schema
- **Files modified:** `src/worthless/cli/bootstrap.py`
- **Verification:** All 271 tests pass including storage integration tests
- **Committed in:** `5041059` (Task 1 feat commit)

**2. [Rule 2 - Missing Critical] Added ShardRepository.delete() method**
- **Found during:** Task 2 (unlock command implementation)
- **Issue:** Repository had no way to remove shard entries after unlock
- **Fix:** Added `delete(alias)` async method with DELETE SQL
- **Files modified:** `src/worthless/storage/repository.py`
- **Verification:** Unlock cleanup tests pass
- **Committed in:** `5fbbce7` (Task 2 feat commit)

**3. [Rule 2 - Missing Critical] Added metadata sidecar files for var_name tracking**
- **Found during:** Task 2 (unlock command implementation)
- **Issue:** No way to map alias back to .env variable name for restoration
- **Fix:** Lock writes `{alias}.meta` JSON with var_name and env_path
- **Files modified:** `src/worthless/cli/commands/lock.py`, `src/worthless/cli/commands/unlock.py`
- **Verification:** Round-trip lock/unlock restores identical .env content
- **Committed in:** `5fbbce7` (Task 2 feat commit)

---

**Total deviations:** 3 auto-fixed (1 bug, 2 missing critical)
**Impact on plan:** All fixes essential for correctness. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Lock/unlock lifecycle complete: ready for scan command (04-03)
- All 271 tests pass, no regressions
- Command registration pattern established for future commands

---
*Phase: 04-cli*
*Completed: 2026-03-26*
