# Phase 4 CLI Code Review

**Verdict: Approve with Warnings** — No critical or blocking issues. Architecture is clean, file sizes are reasonable (largest is scan.py at 261 lines), test coverage is solid for a PoC.

## HIGH Priority (3 items)

**[H-01] Double `os.close(fd)` in `dotenv_rewriter.py:85-88`** — The error handler calls `os.get_inheritable(fd)` on an already-closed fd. If `os.replace()` fails after `os.close()` succeeds, this is undefined behavior. Fix: close fd in a `finally` block before calling `os.replace()`.

**[H-02] `unlock.py` calls `asyncio.run()` twice per alias (lines 52, 87)** — Each call creates/tears down a new event loop. Fragile if ever called from async context. The `lock` command correctly wraps all async work in one `asyncio.run()`. Fix: make `_unlock_alias` async, run it from a single `asyncio.run()` at command level.

**[H-03] `_start_daemon` in `up.py:104-149` duplicates proxy spawn logic** — Manually builds uvicorn command and Popen call instead of using `spawn_proxy()` from `process.py`. Changes to spawn_proxy won't propagate to daemon mode. Fix: parameterize `spawn_proxy()` with a daemon flag.

## MEDIUM Priority (6 items)

**[M-01]** `console.py:58-62` — `_no_color` property re-reads env each call but constructor caches value on line 50. Confusing divergence.

**[M-02]** `process.py:123,129` — `assert proc.stdout is not None` stripped by `python -O`. Use explicit `raise RuntimeError`.

**[M-03]** `wrap.py:156` — Session token generated and injected into child env but never passed to proxy. Proxy can't validate it. False sense of security.

**[M-04]** `scan.py:61` — Deep scan dumps entire `os.environ` to temp file. Scanning `PATH`, `HOME`, `SSH_AUTH_SOCK` produces noise. Filter to vars containing KEY/TOKEN/SECRET/API.

**[M-05]** `scanner.py:28-48` — `load_enrollment_data` reads shard_a files as text, but shard_a is raw XOR output, not the original key. The `value in enrolled` comparison on line 82 can never be True. The `is_protected` path via enrollment_data is dead code. Decoy detection works only because decoys have low entropy (filtered at line 73).

**[M-06]** No `worthless down`/`stop` command. Users must manually kill the daemon.

## LOW Priority (6 items)

**[L-01]** `app.py` late imports with `noqa: E402` — consider `register_all()` in `commands/__init__.py`.
**[L-02]** `_PROVIDER_ENV_MAP` only covers openai/anthropic; google/xai keys won't get proxy injection.
**[L-03]** `wrap` and `up` have no CLI integration tests (only unit tests of helper functions).
**[L-04]** `status.py` queries SQLite directly instead of using `ShardRepository`.
**[L-05]** `enroll --key` exposes API key in shell history/procfs.
**[L-06]** Missing `__all__` in command modules.

## Architecture Strengths

- Clean module boundaries: console/errors/process as shared infra, commands as leaves
- Consistent error pattern: `WorthlessError` with WRTLS-NNN codes caught at command boundary
- Atomic file operations throughout (O_CREAT|O_EXCL, os.replace)
- Proper key material hygiene with bytearray + zero()
- Lock file with stale detection is solid
- SARIF output for CI integration
- ~96 tests with proper tmp_path isolation
