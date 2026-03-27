# Brutus Stress-Test Review: Phase 04 CLI

**Verdict:** MODIFY

The core idea -- replace real keys with decoys and reconstruct only inside an ephemeral proxy -- is sound and solves a real problem. But execution has 5 concrete issues ranging from bug to security theater.

---

## Ship-Blockers

### 1. Provider mapping gap (BUG)

`key_patterns.py` detects 4 providers (openai, anthropic, google, xai) but `wrap.py` only maps 2 (openai, anthropic) to `BASE_URL` env vars. A user who locks a Google API key gets it replaced with a decoy, then `wrap` never redirects traffic -- silent auth failure.

- **File:** `src/worthless/cli/commands/wrap.py` lines 31-34
- **Cost:** Silent data-path failure for 50% of detected providers

### 2. `check_stale_lock` is dead code

`bootstrap.py:154` defines `check_stale_lock()` but `acquire_lock()` at line 104 never calls it. Any crash during `lock` permanently locks out the user.

- **File:** `src/worthless/cli/bootstrap.py`
- **Cost:** Permanent lockout after crash, no recovery path

### 3. Double-close bug in rewriter

`dotenv_rewriter.py:88` -- `os.get_inheritable(fd)` on an already-closed fd raises `OSError`, masking the original exception during error handling.

- **File:** `src/worthless/cli/dotenv_rewriter.py`
- **Cost:** Swallowed errors during .env rewrite failures

---

## Architectural Concerns

### 4. Shard co-location

Both shards + Fernet key live under `~/.worthless/` with the same UID. This is fine for the threat model "prevent accidental git commits" but is NOT protection against local code execution. Docs must be honest about this.

### 5. Liveness pipe unmonitored

`WORTHLESS_LIVENESS_FD` is set in env but appears unconsumed by the proxy. Orphaned proxies will accumulate after parent death.

### 6. .env rewrite race

`rewrite_env_key` is called N times in a loop (one per key), creating N race windows instead of one atomic batch write. Between the read and write, another tool (direnv, docker-compose, IDE) may modify the file.

---

## What Survived

- **XOR splitting + HMAC commitment:** Correct
- **Decoy generation with controlled entropy:** Clever, makes lock idempotent
- **SARIF output for CI:** Smart differentiator
- **Atomic write pattern** (tempfile + `os.replace`): Correct on POSIX despite the error-handler bug
- **Typer choice:** Irrelevant at this scale, fine

---

## Required Changes

1. **Fix provider mapping** -- add google/xai to `_PROVIDER_ENV_MAP` in `wrap.py`, or auto-derive from enrolled providers
2. **Wire `check_stale_lock`** -- call it from `acquire_lock` so crash recovery is automatic
3. **Fix double-close** -- guard the fd close in the error handler
4. **Batch .env rewrites** -- read once, modify all keys, write once
5. **Document threat model** -- be explicit that shard co-location protects against git exposure, not local attackers
6. **Monitor liveness pipe** -- proxy must watch for EOF and self-terminate
