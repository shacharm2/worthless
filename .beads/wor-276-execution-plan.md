# WOR-276 v2 Execution Plan

Source of truth: [WOR-276 Redesign v2 Linear doc](https://linear.app/plumbusai/document/wor-276-redesign-v2-transactional-lock-safe-abort-semantics-8cfca96ff492). This file is local scratchpad — Linear wins on conflicts.

**Branch:** `feature/wor-276-transactional-lock` (cut from `feat/wor-276-recovery-works`).

## Commit sequence (atomic, ordered)

1. `refactor(wor-276): remove Wave 1 backup seam from safe_rewrite` — deletes lines 56-73 (`_backup_writer`, `_set_backup_writer`) and lines 775-785 (backup-writer call block) in `src/worthless/cli/safe_rewrite.py`. Tests: none new; existing safe_rewrite/safe_restore suite stays green.
2. `feat(wor-276)!: delete backup module + tests` — `rm src/worthless/cli/backup.py`, `rm tests/backup/test_backup_writes.py`, `rm tests/backup/test_first_run.py`, trim `tests/backup/test_restore.py` to only cases exercising `safe_restore` core (rename dir → `tests/safe_restore/`), purge XDG/time_ns fixtures from `tests/backup/conftest.py`, remove `src/worthless/RECOVERY.md`, drop `force-include` from `pyproject.toml`, unbind `set_backup_hook()` call sites.
3. `refactor(wor-276)!: rename UnsafeReason.BACKUP → VERIFY_FAILED` — `src/worthless/cli/errors.py:52`, update 2 refs in `tests/safe_rewrite/test_chaos.py`. Tests: existing enum-exhaustiveness test covers.
4. `feat(wor-276): fs_check refuses non-atomic filesystems` — new `src/worthless/cli/fs_check.py` with `UnsupportedFilesystem` + `require_atomic_fs(path)`. Reads `/proc/self/mountinfo` (Linux), checks `statfs.f_type` against SMB/CIFS/NFS/FAT/9P/FUSE magics, rejects `/mnt/c` WSL prefix, rejects symlink-crossing FS. Wire into `_safe_rewrite_core` before `_platform_check`. Tests 17, 18, 19, 20 land here.
5. `feat(wor-276): shard flock spans verify→rename` — extend `safe_rewrite._hook_before_replace` contract so caller holds shard flock across verify + rename; update `src/worthless/cli/commands/lock.py` to hold `fcntl.flock(shard_fd, LOCK_EX)` across the call. Tests 1, 8 land.
6. `feat(wor-276): multi-key batch transactional lock` — refactor `commands/lock.py` to build full rewritten `.env` once, invoke `safe_rewrite` once with all N keys, reject whole batch on any verify failure. Tests 11, 12, 13, 14, 15, 16 land.
7. `feat(wor-276): restore CLI command` — new `src/worthless/cli/commands/restore.py` wrapping `safe_restore` (thin typer wrapper). Register in `__main__.py`. Tests: 3 CLI subprocess tests in `tests/e2e/test_restore_cli.py` (happy, refuses bad basename, refuses non-atomic FS).
8. `feat(wor-276): safe-abort error UX` — rework `UnsafeRewriteRefused` print path in `errors.py:error_boundary` to lead with ".env unchanged" + opaque reason; no path leakage. Tests 5, 10 land.
9. `docs(wor-276): T-9 in-flight rollback + T-10 reconstruction-verify` — append to `docs/security.md` only. DO NOT touch `.planning/security/key-lock-threat-model.md`.
10. `feat(wor-276): verify.py in-memory reconstruction verifier` **[LAST — HMAC input stubbed]** — new `src/worthless/cli/verify.py`: `verify_reconstruction(shard_a: bytearray, shard_b: bytearray, expected_hmac: bytes) -> bool`. bytearray-only inputs, `ctypes.mlock`+`munlock`, `resource.setrlimit(RLIMIT_CORE,(0,0))`, `prctl(PR_SET_DUMPABLE, 0)` on Linux, zero-on-exit via try/finally with `ctypes.memset`, `hmac.compare_digest` on the reconstruction HMAC. HMAC **input derivation is a TODO** — caller passes opaque `expected_hmac: bytes`. Wire into `commands/lock.py` batch path before rename. Tests 2, 3, 4, 6, 7, 9 land.

## File changes

**Deleted:** `src/worthless/cli/backup.py` (~576 lines), `src/worthless/RECOVERY.md`, `tests/backup/test_backup_writes.py`, `tests/backup/test_first_run.py`, most of `tests/backup/test_restore.py`.

**Renamed:** `UnsafeReason.BACKUP` → `VERIFY_FAILED` (`errors.py:52`); surviving restore-core tests → `tests/safe_restore/`.

**Added:** `src/worthless/cli/fs_check.py`, `src/worthless/cli/verify.py`, `src/worthless/cli/commands/restore.py`, `tests/fs_check/`, `tests/verify/`, `tests/transactional_lock/`, `tests/e2e/test_restore_cli.py`.

**Edited:** `safe_rewrite.py` (strip backup seam; add `require_atomic_fs`), `commands/lock.py` (batch rewrite + shard flock + verify call), `errors.py` (enum rename + error UX), `__main__.py` (register restore), `docs/security.md` (T-9, T-10).

## Test-to-commit map

| Commit | Tests |
|---|---|
| 4 fs_check | 17, 18, 19, 20 |
| 5 shard flock | 1, 8 |
| 6 batch tx | 11, 12, 13, 14, 15, 16 |
| 7 restore CLI | 3 e2e (not in the 20) |
| 8 safe-abort UX | 5, 10 |
| 10 verify.py | 2, 3, 4, 6, 7, 9 |

Every commit 4-10 ships covering tests in the same commit. No red lands in main.

## Risks & rollback points

- **Commit 6 (batch transactional lock)** is the highest-risk: rewrites `lock.py` control flow. Rollback painful once 7-10 stack on top.
- **Commit 10 (verify.py)** mlock/prctl behavior varies per-platform; tests 2/3 need Linux for PR_SET_DUMPABLE.
- **Commit 4 (fs_check)** may false-positive on exotic dev setups; keep `WORTHLESS_FORCE_FS=1` escape hatch documented in `docs/security.md`.
- Safe rollback window: **after commit 3** (pure deletion/rename, no behavior change).

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
