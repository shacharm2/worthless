# WOR-276 commit 5b — `_lock_keys` transactional refactor

Branch: `feature/wor-276-transactional-lock`.
Worktree: `.claude/worktrees/epic-kilby-73c682`.

Goal: collapse the current per-key `rewrite_env_key` loop in
`src/worthless/cli/commands/lock.py:53` into a single atomic pipeline —
**Pass-1 DB writes → one `rewrite_env_keys` call with a verify hook →
compensating unwind on failure**. This preserves the WOR-252 "zshrc
lock bug" guarantees while making multi-key lock transactional.

**IMPORTANT — flock step dropped.** Original plan included
`fcntl.flock(home.db_path, LOCK_EX|LOCK_NB)` across verify→rename.
`_lock_async` is already wrapped in `bootstrap.acquire_lock(home)` at
`lock.py:357,391` — an O_EXCL lockfile that serializes every
`worthless` process. The flock would have blocked only what's already
blocked (concurrent `worthless lock/unlock`); non-worthless SQLite
writers to `home.db_path` are outside the threat model since we own
the file. Result: `_acquire_db_flock` / `_release_db_flock` helpers
are NOT needed. Tests 11/12 (threaded flock contention) are
**permanently dropped**, not merely deferred.

---

## 1. `PlannedUpdate` dataclass

Pass-1 collects one `PlannedUpdate` per key (both fresh-enroll and
re-lock). Fields are everything the `rewrite_env_keys` update dict, the
verify hook, AND the compensating unwind need — no re-reads.

```python
@dataclass(eq=False)
class PlannedUpdate:
    alias: str                    # DB key; unwind target
    var_name: str                 # .env key to update
    env_path_str: str             # str(env_path.resolve()); unwind arg
    provider: str                 # for BASE_URL resolution
    shard_a: bytearray            # the decoy to write into .env
    shard_b: bytearray            # from DB (re-lock) or split (fresh)
    commitment: bytes             # for reconstruct_key_fp
    nonce: bytes                  # for reconstruct_key_fp
    prefix: str
    charset: str
    was_fresh_enroll: bool        # unwind: delete_enrolled vs just delete_enrollment
```

Notes:
- **No `plaintext` field.** `reconstruct_key_fp` already calls
  `_verify_commitment` internally and raises `ShardTamperedError` on
  HMAC mismatch (`splitter.py:207`). If reconstruction succeeds, the
  shards round-trip by definition — no redundant compare needed.
  Eliminates a plaintext-bearing bytearray held across the whole pipeline.
- Top-level `finally` zeros `shard_a` and `shard_b` for every planned update.
- `@dataclass(eq=False)` (not `frozen=True`) so we can zero the
  bytearrays in place without the auto-generated `__hash__` on
  unhashable `bytearray` fields becoming a landmine.
- `prefix`/`charset` must be strings matching what
  `reconstruct_key_fp` expects (see `splitter.py:197`) — no `None`.
  Legacy rows without prefix/charset already raise `SHARD_STORAGE_FAILED`
  in pass-1 (current behaviour at `lock.py:121`), preserved.

---

## 2. Verify-hook closure

Signature (matches `safe_rewrite` contract,
`safe_rewrite.py:788` — callable, no args, raises to abort):

```python
def _build_verify_hook(
    repo: ShardRepository,
    planned: list[PlannedUpdate],
    loop: asyncio.AbstractEventLoop,
) -> Callable[[], None]:
```

Capture: `repo`, `planned`, and the running event loop. Hook runs
synchronously from inside `safe_rewrite`; coroutines dispatched via
`asyncio.run_coroutine_threadsafe(coro, loop).result()` — reuses the
same aiosqlite connection and transaction-visibility semantics as
pass-1 (no sync `sqlite3.connect` race on journal mode).

Per planned update the hook:
1. `shard = run_coroutine_threadsafe(repo.fetch_encrypted(p.alias), loop).result()`.
2. `decrypted = repo.decrypt_shard(shard)` → yields `shard_a_db`.
3. `reconstructed = reconstruct_key_fp(shard_a_db, p.shard_b,
   p.commitment, p.nonce, p.prefix, p.charset)` (splitter.py:197).
   **This call is the verify step** — on HMAC-commitment mismatch it
   raises `ShardTamperedError`; we catch that specifically.
4. `finally`: zero `reconstructed`, zero `decrypted` via `.zero()`.

Exception classification (from security review finding #4):
- `ShardTamperedError` → `UnsafeRewriteRefused(VERIFY_FAILED)`.
  Expected integrity-check outcome.
- `ValueError` from `reconstruct_key_fp` (prefix mismatch, length
  mismatch, bad charset) → `WorthlessError(SHARD_STORAGE_FAILED)`.
  Loud logic-bug surface — `.env` still byte-identical, but we do NOT
  silently refuse: it means the DB row itself is malformed.
- Any other unexpected exception → `UnsafeRewriteRefused(VERIFY_FAILED)`
  wrapped at the hook boundary to preserve the opaque refusal contract.

Per `safe_rewrite.py` `_hook_before_replace` fires before the atomic
rename — raising leaves `.env` byte-identical. The caller then runs
compensating DB unwind.

---

## 3. Split of `_lock_keys` into inner helpers

All helpers live inside `_lock_async` (closure over `repo`, `home`,
`env_path`, `keys_only`, `provider_override`, `token_budget_daily`).
Each helper owns one seam so tests can monkey-patch at the seam.

```
_scan_candidates(env_path, enrolled_locations) -> list[(var_name, value, provider)]
    # thin wrapper around scan_env_keys + provider filter + alias calc

_pass1_db_writes(candidates, planned_out: list[PlannedUpdate]) -> None
    # MUTATES planned_out in-place — appends each PlannedUpdate AFTER its DB write succeeds.
    # On exception: partial entries are already in planned_out, so the caller's
    # top-level `finally` zeros every bytearray it allocated, even on partial-failure paths.
    # Per candidate: decide re-lock vs fresh, do the DB write(s), append PlannedUpdate.

_build_verify_hook(repo, planned, loop) -> Callable[[], None]     # §2

_batch_rewrite(env_path, planned, keys_only, verify_hook) -> None
    # builds updates = {p.var_name: p.shard_a.decode() for p in planned}
    # builds additions = {BASE_URL_VAR: _proxy_base_url(p.alias) for fresh-enroll p if not keys_only and BASE_URL not already present}
    # single rewrite_env_keys(env_path, updates, additions=..., _hook_before_replace=verify_hook)

_compensating_unwind(repo, planned) -> list[Exception]
    # for each p in reversed(planned):
    #     try: await repo.delete_enrollment(p.alias, p.env_path_str)
    #          if p.was_fresh_enroll and not await repo.list_enrollments(p.alias):
    #              await repo.delete_enrolled(p.alias)
    #     except Exception as e: errors.append(e); continue
    # Returns list of unwind errors (may be empty). Never raises itself.
    # Caller surfaces non-empty errors via console WARN after re-raising the original
    # — so DB drift is observable, not silently buried in logger.debug.
```

`_lock_async` top-level body reduces to:

```
candidates = _scan_candidates(...)
planned: list[PlannedUpdate] = []
try:
    await _pass1_db_writes(candidates, planned)   # mutates planned; DB state advances
    if not planned: return 0
    hook = _build_verify_hook(repo, planned, asyncio.get_running_loop())
    _batch_rewrite(env_path, planned, keys_only, hook)   # .env commits here
    return len(planned)
except Exception:
    if planned:
        unwind_errors = await _compensating_unwind(repo, planned)
        if unwind_errors:
            _warn_drift(len(unwind_errors))   # console WARN — DB has stale rows
    raise
finally:
    for p in planned: _zero_planned(p)
```

(Cross-process serialization is provided by `acquire_lock(home)` at the
caller, `lock.py:357,391`.)

Seams for tests:
- Monkey-patch `_pass1_db_writes` → simulate partial failure.
- Monkey-patch `_build_verify_hook` → force `VERIFY_FAILED`.
- Monkey-patch `rewrite_env_keys` → simulate `UnsafeRewriteRefused`.

---

## 4. Re-lock vs fresh-enroll unification

Today (`lock.py:119-188` re-lock vs `lock.py:190-249` fresh): two
branches each doing their own `rewrite_env_key` + optional
`add_or_rewrite_env_key(BASE_URL)` + DB write in ad-hoc order with
per-key compensating paths.

Unified pipeline — both paths build a `PlannedUpdate` in pass-1:

| Step | Re-lock | Fresh-enroll |
|---|---|---|
| DB row exists? | yes — verify commitment | no — `split_key_fp` |
| Pass-1 DB write | `repo.add_enrollment(alias, var, env)` | `repo.store_enrolled(alias, stored, …, prefix, charset)` |
| `shard_b` source | `repo.decrypt_shard(db_shard).shard_b` | `sr.shard_b` |
| `shard_a` source | `derive_shard_a_fp(value, shard_b, prefix, charset)` | `sr.shard_a` |
| `prefix`/`charset` | from stored row (mandatory — see §1) | from `sr` |
| BASE_URL in `additions` | only if var not already present in `.env` AND not `keys_only` | only if not `keys_only` |
| Unwind | `delete_enrollment(alias, env)` only | `delete_enrollment` then `delete_enrolled` if last |

The `was_fresh_enroll` flag on `PlannedUpdate` drives the unwind
asymmetry. Re-lock must NOT `delete_enrolled` — that would destroy a
shared alias still used by other `.env` files.

One nuance: today's re-lock path snapshots whole-file `.env` content
(`lock.py:136, 157`) to roll back after a BASE_URL write fails. Under
the new model this whole-file snapshot is **gone** — `rewrite_env_keys`
is a single atomic `safe_rewrite`, so either both the key update AND
the BASE_URL addition commit, or neither does. Net: simpler.

BASE_URL "already present" detection: in pass-1, after the scan, read
the env file once (already done by `scan_env_keys`) and build a
`set[str]` of existing keys so fresh-enroll plans can skip redundant
BASE_URL additions (avoids `KeyError` from `rewrite_env_keys` on
pre-existing addition keys — `dotenv_rewriter.py:776` treats additions
as pure inserts).

---

## 5. Error + exit-path matrix

| Case | DB state | `.env` state | flock | User sees |
|---|---|---|---|---|
| Happy path | all rows present | all keys rewritten + BASE_URL | released | "N key(s) protected." |
| Pass-1 fails mid-loop | partial rows (rolled back in pass-1 itself, see below) | byte-identical | never acquired | `WorthlessError(SHARD_STORAGE_FAILED)` |
| `_acquire_db_flock` fails (contended) | all pass-1 rows present → unwound | byte-identical | never held | `WorthlessError(LOCKED)` "another worthless process is running" |
| `rewrite_env_keys` raises `UnsafeRewriteRefused(*)` (SYMLINK/SIZE/TOCTOU/etc.) | all rows → unwound | byte-identical (contract of `safe_rewrite`) | released | `UnsafeRewriteRefused` propagates with opaque message |
| Verify hook raises `UnsafeRewriteRefused(VERIFY_FAILED)` | all rows → unwound | byte-identical (hook fires pre-rename, `safe_rewrite.py:788`) | released | "verify failed" hint |
| Unexpected exception in batch_rewrite | all rows → unwound | byte-identical | released | wrapped `WorthlessError(SHARD_STORAGE_FAILED)` |
| Unexpected exception during unwind | partial rollback | byte-identical | released | original error surfaces; unwind failures logged at WARN |

Pass-1 internal unwind: if pass-1 fails on the Kth key, the function
itself must roll back the 0..K-1 DB rows before raising — implement as
a try/except that calls `_compensating_unwind(repo, planned_so_far)`
then re-raises. This keeps the top-level `except` branch from having
to know whether the failure was pre- or post-flock.

---

## 6. Test impact

Tests that need updating:

- `tests/test_cli_lock.py:625` — patches
  `worthless.cli.commands.lock.rewrite_env_key`. After refactor the
  call site is `rewrite_env_keys` (plural). Update patch target; the
  assertion "raises and DB is rolled back" becomes "raises and DB is
  unwound for ALL planned aliases" (count changes from 1→N).
- `tests/test_cli_lock.py:449, 716-721` — patch
  `add_or_rewrite_env_key` to simulate BASE_URL failure. That function
  is no longer called on the lock path; BASE_URL writes are folded
  into `rewrite_env_keys.additions`. Rewrite the test to patch
  `rewrite_env_keys` and assert atomic rollback (no partial `.env`).
- Any test asserting `original_env_content` snapshot-and-restore (grep
  for `original_env_content` in tests) is obsolete — single
  `safe_rewrite` supersedes it. Delete those cases rather than
  retrofit: the invariant they tested ("partial BASE_URL write gets
  reverted") is now structurally impossible.
- No existing `tests/transactional_lock/` directory. Create
  `tests/transactional_lock/test_lock_pipeline.py` with cases:
  pass-1-fails, flock-fails, verify-fails, end-to-end happy.
- Related GitNexus-flagged tests (`test_default_db_path` in
  `test_config.py`, `test_no_args_runs_default_command` in
  `test_console.py`) do NOT touch `_lock_keys` internals — they should
  keep passing unchanged; re-run as a sanity check.

---

## 7. Open questions (resolve before coding)

- **`home.db_path` existence**: `flock` needs an existing file. First
  `worthless lock` on a fresh install — does `repo.initialize()`
  (`lock.py:86`) create the SQLite file before pass-1 runs? Confirm in
  `storage/schema.py` `init_db`. If not, the flock helper must create
  the parent and `O_CREAT` the db path (or we flock on a sibling
  `.lock` file — matches `bootstrap.acquire_lock` pattern at
  `lock.py:15`).
- **flock fd source**: open a fresh `os.open(db_path, O_RDONLY|
  O_CLOEXEC)` for flock, OR reuse the aiosqlite connection's fd?
  Recommendation: fresh FD. aiosqlite's connection pool may close/reopen
  the underlying handle, and we want the flock lifetime decoupled from
  DB query lifetime. Cross-check with `bootstrap.acquire_lock` — if it
  already flocks a `.worthless.lock` file, reuse that mechanism
  instead of locking `db_path` directly.
- **Hook → asyncio bridge**: `safe_rewrite` calls the hook
  synchronously from the running event-loop thread (since
  `_lock_async` is itself running under `asyncio.run`). Options: (a)
  make the hook body fully sync by using a sync SQLite read (open
  `sqlite3.connect(db_path)` inside the hook — simplest, avoids
  re-entrant loop issues), (b) `asyncio.run_coroutine_threadsafe`
  against the loop from a thread (heavier). Recommend (a): the hook
  does exactly one `SELECT` per alias; a sync connection is cheap,
  has no re-entrancy risk, and keeps the hook's failure mode
  obvious.
- **`_check_basename` + additions**: `rewrite_env_keys` only calls
  `safe_rewrite` on an existing file — there is no create branch. Lock
  only runs when a `.env` exists (checked at `lock.py:72`), so this
  is fine. Document the invariant.
- **Zeroing `PlannedUpdate.plaintext`**: the plaintext comes from
  `scan_env_keys` which returns `str`. We need a `bytearray` for
  zeroing. Standardise: pass-1 encodes `value.encode()` into a
  bytearray immediately and the original `str` drops out of scope.
- **Ordering of `additions`**: `rewrite_env_keys` iterates `additions`
  in dict-insertion order (`dotenv_rewriter.py:776`). Plan order =
  scan order, which is stable. Confirm no tests assert a particular
  BASE_URL line position.
- **Idempotent re-run**: if the user re-runs `lock` after a mid-flight
  failure where DB was unwound but `.env` is clean, pass-1 will re-
  plan everything and succeed. If DB unwind itself failed, the next
  run sees stale rows and routes through the re-lock branch (which
  verifies commitment against the re-supplied plaintext). Fine.

---

## Implementation order (≈30 min)

1. Add `PlannedUpdate` dataclass + `_zero_planned` helper.
2. Extract `_pass1_db_writes` with internal rollback of partial plans.
3. Extract `_acquire_db_flock` / `_release_db_flock` (reuse
   `bootstrap.acquire_lock` if it already covers this scope).
4. Extract `_build_verify_hook` (sync sqlite read, per Open Q #3).
5. Extract `_batch_rewrite` — one `rewrite_env_keys` call.
6. Extract `_compensating_unwind`.
7. Rewrite `_lock_async` body to the 15-line orchestrator in §3.
8. Update tests in `tests/test_cli_lock.py` per §6; add
   `tests/transactional_lock/test_lock_pipeline.py`.
9. Delete dead code: per-key `rewrite_env_key` import,
   `original_env_content` snapshot block, BASE_URL-only
   `add_or_rewrite_env_key` call site.

End state: `_lock_async` body ≤ 25 lines; every crash class in §5 is
covered by a named test; `.env` and DB cannot disagree.
