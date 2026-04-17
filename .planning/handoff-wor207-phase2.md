# WOR-207 Phase 2 Handoff: Lock Rewrite + Decoy Deletion

## What this is

P0 security fix. The proxy had both shard halves (shard-A from disk, shard-B from DB). PR #50 added format-preserving `split_key_fp`. PR #51 added `prefix`/`charset` DB columns. Phase 2 rewires the `lock` command to use the new split and deletes the entire decoy system.

## Branch

`gsd/wor-196-release-hardening` — up to date with main.

## Plan

Full plan at `.claude/plans/noble-roaming-knuth.md`. Read it first.

## What to do (TDD)

### 1. Write new tests first (10 tests in `tests/test_cli_lock.py`)

```
test_lock_writes_shard_a_to_env         — .env API_KEY = format-valid shard-A
test_lock_writes_base_url_to_env        — .env BASE_URL = http://127.0.0.1:8787/<alias>/v1
test_lock_keys_only_skips_base_url      — --keys-only flag
test_lock_stores_prefix_charset_in_db   — DB has prefix/charset
test_lock_no_shard_a_file               — no file at shard_a_dir
test_relock_skips_enrolled_via_db       — second lock skips enrolled keys
test_lock_base_url_contains_alias       — alias in URL path
test_scan_env_keys_no_decoy_param       — scan_env_keys works without is_decoy
test_add_or_rewrite_creates_new_var     — new dotenv function
test_add_or_rewrite_updates_existing    — new dotenv function
```

### 2. Delete obsolete tests

- `tests/test_decoy.py` — entire file (28 tests)
- `TestOldDecoyMigration` class in `tests/test_cli_lock.py` (5 tests, lines ~643-845)
- 5 decoy hash tests in `tests/test_storage.py` (lines ~218-304)

### 3. Implement (7 steps from plan)

Step 1: `dotenv_rewriter.py` — add `add_or_rewrite_env_key`, remove `is_decoy` param from `scan_env_keys`
Step 2: Delete decoy system across 8 files (decoy.py, lock.py, repository.py, scan.py, default_command.py, scanner.py, mcp/server.py, test_cli_adversarial_edge_cases.py)
Step 3: Rewire `_lock_keys` to `split_key_fp` + re-lock guard via enrollment DB
Step 4: Add BASE_URL writing + `--keys-only` flag
Step 5: Simplify compensation (no file rollback)
Step 6: Rewire `_enroll_single`
Step 7: Update existing tests to match new behavior

### 4. Verify

```bash
uv run pytest
grep -r make_decoy src/                              # zero
grep -r decoy_hash src/worthless/                     # zero (except schema.py)
grep -r shard_a_dir src/worthless/cli/commands/lock.py # zero
grep -r "split_key(" src/worthless/cli/               # zero (only split_key_fp)
```

## Key design decisions

- NO `shard_a_hash` in DB — SR-11 prohibits shard-A-derived data on server
- Alias in URL path: `http://proxy:8787/<alias>/v1` — proxy extracts alias from path
- `lock` default writes both shard-A + BASE_URL; `--keys-only` skips BASE_URL
- Re-lock guard: enrollment DB check (`find_enrollment_by_location`), not hash matching
- Proxy port: `WORTHLESS_PORT` env var or default 8787
- No migration needed — prelaunch, no users

## Critical gotcha (from Brutus review)

The decoy system is used in MORE places than just lock.py:
- `src/worthless/cli/commands/scan.py` — `_build_decoy_checker()`
- `src/worthless/cli/default_command.py` — `_scan_with_decoys()`
- `src/worthless/cli/scanner.py` — `scan_files(is_decoy=...)`
- `src/worthless/mcp/server.py` — `_build_decoy_checker_async()`
- `tests/test_cli_adversarial_edge_cases.py` — `is_decoy` with `scan_env_keys`

All must be updated in Step 2.
