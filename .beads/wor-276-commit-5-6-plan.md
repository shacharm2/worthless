# WOR-276 Commit 5+6 — Transactional Multi-Key Lock

Merge shard-flock-across-verify and multi-key-batch into one commit.
Single `.env` rewrite per `lock` invocation covers all N keys;
verification runs inside `_hook_before_replace` under an exclusive
SQLite flock.

## 1. Hook contract

Keep `_hook_before_replace: Callable[[], None] | None` zero-arg. Bind
state via closure over `(repo, db_flock_fd, planned_updates: list[_PlannedUpdate])`.
Inside the hook: re-`fstat` db under LOCK_EX, reconstruct each key via
`reconstruct_key_fp`, compare to original plaintext, raise
`UnsafeRewriteRefused(UnsafeReason.VERIFY_FAILED)` on any mismatch.

## 2. Batch rewrite

Add to `dotenv_rewriter.py`:
```python
def rewrite_env_keys(
    env_path: Path,
    updates: dict[str, str],
    *,
    additions: dict[str, str] | None = None,
    _hook_before_replace: Callable[[], None] | None = None,
) -> None
```
- Read `existing` once.
- Walk `lines` once; rebuild matching keys via `_rebuild_assignment_preserving_format`.
- Append `additions` at end.
- One `safe_rewrite(env_path, new_content, expected_baseline_sha256=sha256(existing), _hook_before_replace=hook)` call.

Keep `rewrite_env_key`/`add_or_rewrite_env_key` for unlock/unenroll callers.

## 3. Shard flock

Shards live in SQLite at `home.db_path` (see `ShardRepository`). Open
`db_fd = os.open(home.db_path, O_RDONLY)`; `fcntl.flock(db_fd, LOCK_EX|LOCK_NB)`.
Held across the `safe_rewrite` call including hook; released in `finally`.
Acquire AFTER pass-1 DB writes complete (aiosqlite needs its own lock);
hold only around `rewrite_env_keys`.

## 4. Failure modes

- Verify-fail on any key → hook raises → `safe_rewrite` unlinks tmp,
  never renames. `.env` byte-identical.
- Compensating DB unwind: loop over planned updates, call
  `delete_enrollment`/`delete_enrolled` for every alias written in
  pass-1.
- TOCTOU / filesystem gate fire earlier; unchanged.

## 5. TDD tests (`tests/cli/test_lock_transactional.py`)

1. `test_batch_lock_all_or_nothing` — break verify on key #2 of 3;
   assert `.env` byte-identical, zero shards, nonzero exit.
8. `test_batch_lock_single_safe_rewrite_call` — instrument
   `safe_rewrite` call count; 3 keys → exactly 1 call.
11. `test_verify_runs_under_db_flock` — concurrent thread gets
   `EWOULDBLOCK` on `flock(LOCK_EX|LOCK_NB)` during hook.
12. `test_concurrent_shard_swap_blocked` — thread UPDATE during hook
   window blocks until rename completes.
13. `test_verify_fail_leaves_env_identical` — no ghost tmp/staging,
   byte-identical `.env`.
14. `test_verify_fail_rolls_back_db` — `list_enrollments()` empty,
   `fetch_encrypted(alias)` returns None.
15. `test_rewrite_env_keys_single_call` — direct unit; patch
   `safe_rewrite`; one call with all updates.
16. `test_rewrite_env_keys_missing_var_raises` — KeyError before any write.

## 6. File changes

- `dotenv_rewriter.py`: ADD `rewrite_env_keys`.
- `commands/lock.py`: split into pass-1 (DB writes, no `.env`), flock
  acquire, batch rewrite with verify hook, compensating unwind. Remove
  inline `original_env_content` snapshot-restore (safe_rewrite already
  guarantees byte-identity on refusal).
- `safe_rewrite.py`: docstring-only — clarify hook is the verify panic
  point.

## 7. Risks

- Existing `tests/cli/test_lock*.py` asserting per-key `safe_rewrite`
  count will break. Update to batch contract.
- DB-first ordering: orphan rows if pass-1 raises. Wrap pass-1 in
  try/except + full unwind.
- Re-lock branch writes key + BASE_URL; handle via `updates` (existing)
  or `additions` (new).
- aiosqlite flock contention: fall back to sentinel file
  `home.base_dir / ".shard-lock"` if needed.
