# Shard Data Flow State Machine (Security Audit)

> Generated during WOR-207 Phase 2. Use to verify isolation invariants.

## Why This Exists

During Phase 2 (lock rewrite), we changed `lock` to use `split_key_fp` but forgot to update the proxy's `reconstruct_key` call AND discovered the proxy reads shard-A from a shared filesystem — violating SR-09. Neither the semgrep rules (not wired into pre-commit) nor the invariant tests (no SR-09 test) caught this. This document maps every shard flow so violations are discoverable.

## SR-09 Violation Found

**What:** `proxy/app.py:280-286` reads shard-A from `~/.worthless/shard_a/<alias>` — same filesystem as the CLI client.

**Why it's wrong:** SR-09 says "Shard-A arrives exclusively via the Authorization header per-request. No WORTHLESS_SHARD_A_DIR, no disk fallback." The proxy and client should be isolated — no shared filesystem for shard material.

**Root cause of missed detection:**
1. Semgrep rules `sr09-no-shard-a-in-proxy` exist but aren't in pre-commit (deferred to CI)
2. No invariant test in `test_invariants.py` for SR-09
3. Handoff spec was written top-down without tracing proxy data reads

---

## Operations That Touch Shards

### LOCK (split + store)

| What | Where | Boundary |
|------|-------|----------|
| Create shard-A + shard-B | `splitter.py:split_key_fp()` | Memory (CLI) |
| Write shard-A to .env | `lock.py` → `rewrite_env_key()` | Memory → Disk (client) |
| Write shard-A to file | `lock.py` → `os.open(shard_a_dir)` | Memory → Disk (shared!) |
| Encrypt + store shard-B | `repository.py:store_enrolled()` | Memory → DB (Fernet) |
| Zero shards | `sr.zero()` in finally | Memory |

### UNLOCK (reconstruct + restore)

| What | Where | Boundary |
|------|-------|----------|
| Read shard-A from .env | `unlock.py` → `dotenv_values()` | Disk → Memory (client) |
| Read shard-A from file (legacy) | `unlock.py` → `shard_a_path.read_bytes()` | Disk → Memory |
| Fetch + decrypt shard-B | `repository.py:fetch_encrypted + decrypt_shard` | DB → Memory |
| Reconstruct key | `reconstruct_key_fp()` or `reconstruct_key()` | Memory |
| Write key to .env | `rewrite_env_key()` | Memory → Disk |
| Zero all | finally block | Memory |

### PROXY REQUEST (gate-before-reconstruct)

| What | Where | Boundary |
|------|-------|----------|
| Receive shard-A from header | `app.py:272` `x-worthless-shard-a` | Network → Memory |
| **Read shard-A from file (SR-09 VIOLATION)** | `app.py:280-286` | **Disk → Memory (shared FS!)** |
| Fetch encrypted shard-B | `app.py:267` | DB → Memory |
| Rules gate (pre-decrypt) | `app.py:296` | Memory |
| Decrypt shard-B (post-gate) | `app.py:314` | Memory |
| Reconstruct key | `app.py:320-326` | Memory |
| Call upstream | `app.py:337-354` | Memory → Network |
| Zero all | `app.py:442-444` finally | Memory |

### WRAP / UP (spawn proxy)

| What | Where | Boundary |
|------|-------|----------|
| Pass Fernet key via pipe | `process.py:91-131` | FD inheritance |
| **Pass WORTHLESS_SHARD_A_DIR to proxy (SR-09 VIOLATION)** | `process.py:49` | **Env var → subprocess** |
| Inject BASE_URL for child | `wrap.py:_build_child_env()` | Env var → child process |

### SCAN / STATUS (read-only)

No shard material accessed. Metadata only.

### ENROLL (programmatic)

| What | Where | Boundary |
|------|-------|----------|
| Split key | `lock.py:_enroll_single()` or `enroll_stub.py` | Memory |
| Store shard-B | `repository.py:store_enrolled()` | Memory → DB |
| Return shard-A | `enroll_stub.py:58` (caller responsibility) | Memory → caller |

### REVOKE (secure delete)

| What | Where | Boundary |
|------|-------|----------|
| Zero shard-A file | `revoke.py:44-47` `os.write(zeros) + fsync` | Disk |
| Delete shard-A file | `revoke.py:50` `unlink()` | Disk |
| Delete all DB records | `repository.py:revoke_all()` | DB |

---

## Isolation Violations (Current)

| ID | Violation | File | Fix |
|----|-----------|------|-----|
| V1 | Proxy reads shard-A from shared FS | `proxy/app.py:280-286` | Extract from Authorization header |
| V2 | `WORTHLESS_SHARD_A_DIR` passed to proxy | `process.py:49` | Remove from proxy env |
| V3 | `shard_a_dir` in ProxySettings | `proxy/config.py:81-83` | Remove field |
| V4 | `_infer_alias_from_path` scans shard_a_dir | `proxy/app.py:77-106` | Extract alias from URL path |
| V5 | Lock writes shard-A file (for proxy) | `lock.py:153-159` | Remove file write |

## Enforcement Gaps

| Gap | Current | Fix |
|-----|---------|-----|
| Semgrep not in pre-commit | Rules exist, deferred to CI | Wire `.semgrep/worthless-rules.yml` into pre-commit (local, no network) |
| No invariant test for SR-09 | `test_invariants.py` has #1-#3, no #4 | Add AST + grep scan for shard_a in proxy/ |
| No invariant test for SR-10 | Dual-shard co-location not tested | Add test: no process config gives access to both shard locations |
