# WOR-276 v2 Execution Plan

Source of truth: [WOR-276 Redesign v2 Linear doc](https://linear.app/plumbusai/document/wor-276-redesign-v2-transactional-lock-safe-abort-semantics-8cfca96ff492). This file is local scratchpad — Linear wins on conflicts.

**Branch:** `feature/wor-276-transactional-lock` (cut from `feat/wor-276-recovery-works`).

## Commit sequence (atomic, ordered)

**Status legend:** ✅ done · 🔨 in-flight · ⛔ blocked · 📋 pending

1. ✅ `refactor(wor-276): remove Wave 1 backup seam from safe_rewrite` — deletes lines 56-73 (`_backup_writer`, `_set_backup_writer`) and lines 775-785 (backup-writer call block) in `src/worthless/cli/safe_rewrite.py`. Tests: none new; existing safe_rewrite/safe_restore suite stays green.
2. ✅ `feat(wor-276)!: delete backup module + tests` — `rm src/worthless/cli/backup.py`, `rm tests/backup/test_backup_writes.py`, `rm tests/backup/test_first_run.py`, trim `tests/backup/test_restore.py` to only cases exercising `safe_restore` core (rename dir → `tests/safe_restore/`), purge XDG/time_ns fixtures from `tests/backup/conftest.py`, remove `src/worthless/RECOVERY.md`, drop `force-include` from `pyproject.toml`, unbind `set_backup_hook()` call sites.
3. ✅ `refactor(wor-276)!: rename UnsafeReason.BACKUP → VERIFY_FAILED` — `src/worthless/cli/errors.py:52`, update 2 refs in `tests/safe_rewrite/test_chaos.py`. Tests: existing enum-exhaustiveness test covers.
4. ✅ `feat(wor-276): fs_check refuses non-atomic filesystems` — new `src/worthless/cli/fs_check.py` with `UnsupportedFilesystem` + `require_atomic_fs(path)`. Reads `/proc/self/mountinfo` (Linux), checks `statfs.f_type` against SMB/CIFS/NFS/FAT/9P/FUSE magics, rejects `/mnt/c` WSL prefix, rejects symlink-crossing FS. Wire into `_safe_rewrite_core` before `_platform_check`. Tests 17, 18, 19, 20 land here.
5. **Split into 5a (✅ done) + 5b (🔨 next):**
   - 5a. ✅ `feat(wor-276): add rewrite_env_keys batch helper` — new public function in `dotenv_rewriter.py` (commit `5786344`). Single `safe_rewrite` call for N updates, all-or-nothing contract, forwards `_hook_before_replace`. 7 green unit tests in `tests/dotenv_rewriter/test_rewrite_env_keys.py`.
   - 5b. 🔨 `feat(wor-276): multi-key batch transactional lock` — **merges original commits 5 + 6**. Refactor `commands/lock.py`: pass-1 DB writes → one `rewrite_env_keys` call with verify hook closure → compensating DB unwind on `UnsafeRewriteRefused`. Hook reconstructs each key via `reconstruct_key_fp`, compares to original plaintext, raises `VERIFY_FAILED` on mismatch. **No db_path flock** — `acquire_lock(home)` at `lock.py:357,391` already O_EXCL-serializes all `worthless` processes, so an inner flock is redundant. **Scoped to 5 integration tests** in `tests/transactional_lock/`; tests 11/12 (threaded flock contention) **dropped permanently** — the flock step they exercised no longer exists. Design doc: `.beads/wor-276-commit-5b-design.md`.
6. ~~Merged into 5b.~~
7. ✅ `feat(wor-276): restore CLI command` — `src/worthless/cli/commands/restore.py` wrapping `safe_restore` (commit `3665510`, simplified `28e22ad`). Bounded stdin read (`_MAX_BYTES + 1`), refuses empty stdin, refuses non-`.env` basename. 3 e2e tests green.
8. ✅ `feat(wor-276): safe-abort error UX` — landed prior to 7.
9. ✅ `docs(wor-276): T-9 in-flight rollback + T-10 reconstruction-verify` — appended to `docs/security.md`. `.planning/security/key-lock-threat-model.md` untouched.
10. ⛔ `feat(wor-276): verify.py in-memory reconstruction verifier` **[BLOCKED on HMAC panel]** — new `src/worthless/cli/verify.py`: `verify_reconstruction(shard_a, shard_b, expected_hmac)`. bytearray-only inputs, `ctypes.mlock`+`munlock`, `RLIMIT_CORE=0`, `PR_SET_DUMPABLE=0` (Linux), zero-on-exit try/finally with `ctypes.memset`, `hmac.compare_digest`. Ships with opaque `expected_hmac: bytes` + `pytest.xfail` sentinel `b"\x00" * 32` until panel resolves. Tests 2, 3, 4, 6, 7, 9.

## Commit 5b — planned test names (locked scope, 5 tests)

`tests/transactional_lock/test_lock_transactional.py`:
- `test_batch_lock_single_safe_rewrite_call` — 3-key lock → exactly 1 `safe_rewrite` call.
- `test_batch_lock_all_or_nothing_env_identical` — verify-fail on key #2 of 3 → `.env` byte-identical.
- `test_batch_lock_all_or_nothing_db_rolled_back` — same fault → `list_enrollments()` empty, `fetch_encrypted` None for every alias.
- `test_batch_lock_happy_path_all_enrolled` — no verify failure → all keys enrolled, `.env` has all shard_a + BASE_URL entries, exit 0.
- `test_batch_lock_rewrite_refused_leaves_no_ghost_tmp` — verify-fail → no `.env.tmp.*`/`.env.staging.*` artifacts.

**Deferred** (belt-and-suspenders, post-HMAC-panel):
- Original test 11 `test_verify_runs_under_db_flock` (threaded EWOULDBLOCK assertion).
- Original test 12 `test_concurrent_shard_swap_blocked` (thread UPDATE during hook window).

## Commit 5b — expert chain (before writing code)

1. `backend-developer` — propose control-flow refactor of `_lock_keys` (pass-1 DB, flock acquire, batch rewrite, compensating unwind).
2. `test-automator` — write the 5 RED integration tests first; confirm they fail against current code.
3. `security-auditor` — review hook closure: plaintext lifetime, zeroization, flock release ordering on every exit path (success, `UnsafeRewriteRefused`, exception).
4. Implement; run regression; `/simplify` pass.

## File changes

**Deleted:** `src/worthless/cli/backup.py` (~576 lines), `src/worthless/RECOVERY.md`, `tests/backup/test_backup_writes.py`, `tests/backup/test_first_run.py`, most of `tests/backup/test_restore.py`.

**Renamed:** `UnsafeReason.BACKUP` → `VERIFY_FAILED` (`errors.py:52`); surviving restore-core tests → `tests/safe_restore/`.

**Added:** `src/worthless/cli/fs_check.py`, `src/worthless/cli/verify.py`, `src/worthless/cli/commands/restore.py`, `tests/fs_check/`, `tests/verify/`, `tests/transactional_lock/`, `tests/e2e/test_restore_cli.py`.

**Edited:** `safe_rewrite.py` (strip backup seam; add `require_atomic_fs`), `commands/lock.py` (batch rewrite + shard flock + verify call), `errors.py` (enum rename + error UX), `__main__.py` (register restore), `docs/security.md` (T-9, T-10).

## Test-to-commit map

| Commit | Tests | Status |
|---|---|---|
| 4 fs_check | 17, 18, 19, 20 | ✅ |
| 5a rewrite_env_keys helper | 7 unit tests in `tests/dotenv_rewriter/test_rewrite_env_keys.py` | ✅ |
| 5b batch tx lock | 5 integration tests (see above); 11 + 12 deferred | 🔨 next |
| 7 restore CLI | 3 e2e | ✅ |
| 8 safe-abort UX | 5, 10 | ✅ |
| 9 docs | — | ✅ |
| 10 verify.py | 2, 3, 4, 6, 7, 9 | ⛔ HMAC panel |

Every commit 4-10 ships covering tests in the same commit. No red lands in main.

## Risks & rollback points

- **Commit 5b (batch transactional lock)** is the highest-risk: rewrites `lock.py` control flow. Rollback painful once 10 stacks on top.
- **Commit 10 (verify.py)** mlock/prctl behavior varies per-platform; tests 2/3 need Linux for PR_SET_DUMPABLE.
- **Commit 4 (fs_check)** may false-positive on exotic dev setups; keep `WORTHLESS_FORCE_FS=1` escape hatch documented in `docs/security.md`.
- Safe rollback window: **after commit 3** (pure deletion/rename, no behavior change).
- **Anti-pattern (ruled out):** structural-only pass-1/pass-2 refactor without batching. Opens a *new* inconsistency window between DB writes and per-key env writes — strictly worse than either current code or 5b target. Batching + hook + flock must land together.

## Blocking dependencies

1. **HMAC derivation panel output** — blocks commit 10 from shipping real HMAC. Plan: commit 10 lands with `expected_hmac` as an opaque caller-supplied `bytes` and a `TODO(wor-XXX-hmac-panel)` comment. `commands/lock.py` integration in commit 10 passes a sentinel `b"\x00" * 32` with a `pytest.xfail` marker until the panel resolves.
2. Linux kernel ≥ 3.15 for `RENAME_NOREPLACE` — already assumed by existing safe_rewrite; no new blocker.

## Estimated order of work (solo + AI-assisted)

| Step | Days |
|---|---|
| Commits 1-3 (deletion + rename) | 0.5 |
| Commit 4 (fs_check + 4 tests) | 1.0 |
| Commit 5 (shard flock + 2 tests) | 0.5 |
| Commit 6 (batch tx + 6 tests) — hardest | 2.0 |
| Commit 7 (restore CLI + 3 e2e) | 0.5 |
| Commit 8 (safe-abort UX + 2 tests) | 0.5 |
| Commit 9 (docs) | 0.25 |
| Commit 10 (verify.py + 6 tests, HMAC stubbed) | 1.5 |
| **Total** | **~7 days** |

HMAC panel resolution + follow-up commit wiring real HMAC: +0.5 day.
