---
status: complete
phase: 04-cli
source: 04-01-SUMMARY.md, 04-02-SUMMARY.md, 04-03-SUMMARY.md, 04-04-SUMMARY.md
started: 2026-03-28T08:00:00Z
updated: 2026-03-28T15:30:00Z
---

## Current Test

[testing complete]

## Tests

### 1. CLI Help and Entry Point
expected: Running `worthless --help` shows app description and lists all 7 commands.
result: pass

### 2. Bootstrap (~/.worthless/ initialization)
expected: First command creates ~/.worthless/ with 0700 dirs, 0600 files (fernet.key, DB).
result: pass

### 3. Lock a .env File
expected: `worthless lock` scans .env, splits keys, rewrites with decoy, prints "1 key(s) protected."
result: pass

### 4. Lock Idempotency
expected: Re-running lock on locked .env prints "No unprotected API keys found."
result: pass

### 5. Unlock Restores Original
expected: `worthless unlock` reconstructs key, restores exact original .env content.
result: pass

### 6. Scan Detects Exposed Keys
expected: Scan reports unprotected keys (exit 1). After lock, "No API keys found." (exit 0).
result: pass

### 7. Scan SARIF Output
expected: `--format sarif` outputs valid SARIF v2.1.0 JSON with tool name "worthless".
result: pass

### 8. Status Shows Enrolled Keys
expected: Status shows aliases with providers. `--json` outputs parseable JSON.
result: pass

### 9. Enroll Single Key via Stdin
expected: `echo key | worthless enroll --key-stdin` enrolls without shell history exposure.
result: pass

### 10. Unsupported Provider Gating
expected: Lock warns and skips google/xai keys as unsupported for proxy redirect.
result: pass

### 11. Duplicate Key Value Handling
expected: Two vars with same key value — both rewritten with decoys.
result: pass

### 12. Multi-env Lock
expected: Same key in two .env files — both protected, second lock says "1 key(s) protected."
result: pass

### 13. Wrap Command
expected: Ephemeral proxy + child process with BASE_URL injection.
result: skipped
reason: Requires live proxy infrastructure (uvicorn). Tracked as WOR-32.

### 14. Up Command (Foreground)
expected: Proxy starts foreground, Ctrl+C cleans up.
result: skipped
reason: Requires live proxy infrastructure. Tracked as WOR-32.

### 15. Up Command (Daemon)
expected: Proxy starts background, PID file written, health check passes.
result: skipped
reason: Requires live proxy infrastructure. Tracked as WOR-32.

### 16. Quiet and JSON Modes
expected: --quiet suppresses output. --json emits machine-readable JSON.
result: pass

## Summary

total: 16
passed: 13
issues: 0
pending: 0
skipped: 3

## Gaps

[none — 3 skipped tests tracked as WOR-32 in Linear with "debt" label]
