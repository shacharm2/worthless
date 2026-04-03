# Security Review: worthless CLI (`src/worthless/cli/`)

**Reviewed:** 2026-03-27
**Reviewer:** security-reviewer agent (Claude Opus 4.6)
**Scope:** All files under `src/worthless/cli/`, plus `crypto/` and `storage/` where referenced

## Summary

- **Critical Issues:** 2
- **High Issues:** 5
- **Medium Issues:** 6
- **Low Issues:** 4
- **Info:** 3
- **Overall Risk Level:** HIGH

---

## CRITICAL Issues (Fix Immediately)

### C-01: Deep scan dumps entire process environment to a temp file with default permissions

**Severity:** CRITICAL
**Category:** Sensitive Data Exposure
**Location:** `src/worthless/cli/commands/scan.py:61-74`

**Issue:**
`_collect_deep_paths()` writes every environment variable (including secrets like `AWS_SECRET_ACCESS_KEY`, `DATABASE_URL`, `WORTHLESS_FERNET_KEY`, etc.) to a temp file created by `tempfile.mkstemp()`. The default umask applies -- on most systems this is `0o600` (owner-only), but `mkstemp` does not guarantee restrictive permissions on all platforms. More critically, the file contents are the **entire process environment in plaintext**, and the file persists on disk until the scan completes (or longer if an unhandled exception occurs between creation and the `finally` block).

```python
env_lines = [f"{k}={v}" for k, v in os.environ.items()]
fd, tmp = tempfile.mkstemp(prefix="worthless-env-", suffix=".env")
os.write(fd, "\n".join(env_lines).encode())
```

**Impact:**
- Every secret in the environment is written to disk in plaintext.
- If the process crashes between lines 63-74 (before the finally at line 260-261), the temp file is orphaned.
- On shared systems, another user may read the file depending on umask.

**Remediation:**
1. Explicitly set permissions: `fd, tmp = tempfile.mkstemp(...)` then `os.fchmod(fd, 0o600)` immediately after creation.
2. Better: scan `os.environ` in-memory instead of writing to a file. Refactor `scan_files()` to accept an iterable of `(source_name, lines)` rather than requiring file paths.
3. If file-on-disk is required, use `os.open()` with `O_EXCL | O_CREAT` and explicit `0o600` like `bootstrap.py` does.
4. Add a nested try/finally around the `os.write` to ensure `os.close(fd)` and `os.unlink(tmp)` on any failure.

---

### C-02: Fernet key passed via environment variable to child processes

**Severity:** CRITICAL
**Category:** Sensitive Data Exposure / Key Material Leakage
**Location:** `src/worthless/cli/commands/wrap.py:149-153`, `src/worthless/cli/commands/up.py:93-97`

**Issue:**
Both `wrap` and `up` commands pass `WORTHLESS_FERNET_KEY` as a plaintext environment variable to the proxy subprocess:

```python
proxy_env = {
    "WORTHLESS_DB_PATH": str(home.db_path),
    "WORTHLESS_FERNET_KEY": home.fernet_key.decode(),
    "WORTHLESS_SHARD_A_DIR": str(home.shard_a_dir),
}
```

Environment variables are:
- Visible in `/proc/<pid>/environ` on Linux (readable by same user, or root).
- Visible via `ps eww` on some systems.
- Inherited by **all** grandchild processes the proxy may spawn.
- Logged by crash reporters, APM tools, and container orchestrators.
- Persisted in core dumps (though core dumps are suppressed for the parent, the *child* proxy process does not call `disable_core_dumps()` itself).

**Impact:**
The Fernet key protects shard_b at rest. If leaked, an attacker with DB access can decrypt all shard_b values. Combined with shard_a files, this reconstructs every enrolled API key.

**Remediation:**
1. Pass the Fernet key via a file descriptor (like the liveness pipe) or a temporary file with 0600 permissions that the proxy reads and deletes on startup.
2. Alternatively, pass via stdin of the subprocess.
3. Have the proxy process call `disable_core_dumps()` on startup as well.
4. At minimum, document this as an accepted risk for the PoC and add a TODO for production hardening.

---

## HIGH Issues (Fix Before Production)

### H-01: `enroll` command accepts API key as a CLI argument

**Severity:** HIGH
**Category:** Sensitive Data Exposure
**Location:** `src/worthless/cli/commands/lock.py:182-194`

**Issue:**
The `enroll` command takes `--key` as a Typer option, meaning the API key appears in:
- Shell history (`~/.bash_history`, `~/.zsh_history`)
- Process listing (`ps aux`)
- System audit logs

```python
key: str = typer.Option(..., "--key", "-k", help="API key to enroll"),
```

**Impact:**
API keys are exposed in multiple persistent locations outside the CLI's control.

**Remediation:**
1. Read from stdin when `--key` is `-` or omitted: `key = sys.stdin.readline().strip()`
2. Use `typer.prompt(hide_input=True)` for interactive input.
3. Support `--key-file` to read from a file.
4. At minimum, document the risk in `--help` text.

---

### H-02: enroll_stub writes shard_a with default permissions

**Severity:** HIGH
**Category:** Insecure File Permissions
**Location:** `src/worthless/cli/enroll_stub.py:54-57`

**Issue:**
```python
os.makedirs(shard_a_dir, exist_ok=True)
shard_a_path = Path(shard_a_dir) / alias
shard_a_path.write_bytes(shard_a)
```

`os.makedirs` uses the default umask (typically 0o755) and `Path.write_bytes` uses default permissions (typically 0o644). This means shard_a files are world-readable.

Compare with `lock.py:85-89` which correctly uses `os.open(..., O_EXCL, 0o600)`.

**Impact:**
Any local user can read shard_a files created by the enroll_stub, which is half the key material needed to reconstruct API keys.

**Remediation:**
```python
os.makedirs(shard_a_dir, mode=0o700, exist_ok=True)
fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    os.write(fd, shard_a)
finally:
    os.close(fd)
```

---

### H-03: enroll_stub does not zero SplitResult after use

**Severity:** HIGH
**Category:** Key Material in Memory
**Location:** `src/worthless/cli/enroll_stub.py:39-59`

**Issue:**
`split_key()` returns a `SplitResult` containing both shards. The `enroll_stub` function never calls `sr.zero()`. The `shard_a` bytes are copied to a new `bytes` object on line 52 (`shard_a = bytes(sr.shard_a)`) and returned, but the original `sr` is never zeroed.

Compare with `lock.py:78-110` which correctly calls `sr.zero()` in a `finally` block.

**Impact:**
Key material persists in memory until garbage collected, which could be indefinitely in long-running processes or test suites.

**Remediation:**
```python
sr = split_key(api_key.encode())
try:
    # ... store shard_b, write shard_a ...
    shard_a = bytes(sr.shard_a)
    return shard_a
finally:
    sr.zero()
```

---

### H-04: PID file has no permissions restriction

**Severity:** HIGH
**Category:** Insecure File Permissions
**Location:** `src/worthless/cli/process.py:183-185`

**Issue:**
```python
def write_pid(pid_path: Path, pid: int, port: int) -> None:
    pid_path.write_text(f"{pid}\n{port}\n")
```

PID file is written with default permissions (typically 0o644). While PID files are not secret, they enable attacks:
- An attacker who can write to the PID file can point `status` and `up` commands at an arbitrary PID/port.
- If the PID file is in `~/.worthless/` (mode 0o700), the directory ACL protects it. But `write_pid` does not verify the parent directory permissions.

**Remediation:**
Use `os.open()` with explicit `0o600` permissions, or at minimum verify the parent directory is 0o700.

---

### H-05: TOCTOU race in lock acquisition

**Severity:** HIGH
**Category:** Race Condition
**Location:** `src/worthless/cli/bootstrap.py:154-166` (check_stale_lock) and `bootstrap.py:105-126` (acquire_lock)

**Issue:**
`check_stale_lock()` checks the lock file's age and unlinks it if stale, but this check is separate from `acquire_lock()`. Between the stale check and the `O_CREAT|O_EXCL` in `acquire_lock`, another process could:
1. Create a fresh lock (after stale one is removed)
2. Be mid-operation when the current process creates its own lock

The `acquire_lock` itself is atomic (O_EXCL), but `check_stale_lock` is called separately, creating a TOCTOU window. Also, `check_stale_lock` is not called by `acquire_lock` -- it's up to callers to call both, and the `lock` command does NOT call `check_stale_lock` before `acquire_lock`.

**Impact:**
In the stale-lock scenario, two CLI invocations could both believe they hold the lock, leading to concurrent `.env` rewrites or duplicate shard enrollment.

**Remediation:**
1. Integrate stale-lock cleanup into `acquire_lock` itself:
   ```python
   def acquire_lock(home):
       try:
           fd = os.open(str(home.lock_file), O_WRONLY | O_CREAT | O_EXCL, 0o600)
           os.close(fd)
       except FileExistsError:
           _maybe_reclaim_stale(home)
           # Retry once
           fd = os.open(str(home.lock_file), O_WRONLY | O_CREAT | O_EXCL, 0o600)
           os.close(fd)
   ```
2. Consider using `fcntl.flock()` for advisory locking instead of file existence.

---

## MEDIUM Issues (Fix When Possible)

### M-01: dotenv_rewriter temp file double-close risk

**Severity:** MEDIUM
**Category:** Resource Handling Bug
**Location:** `src/worthless/cli/dotenv_rewriter.py:87-93`

**Issue:**
```python
except BaseException:
    os.close(fd) if not os.get_inheritable(fd) else None
```

This attempts to close `fd` in the error path, but `fd` was already closed on line 85 (`os.close(fd)`) in the happy path. If `os.replace()` on line 86 raises, `fd` is already closed, and `os.get_inheritable(fd)` will raise `OSError` (bad file descriptor), which is swallowed. However, if `os.write()` on line 84 raises, `fd` is NOT yet closed, and the error handler correctly closes it.

The logic is fragile. A cleaner pattern:

```python
closed = False
try:
    os.write(fd, content)
    os.close(fd)
    closed = True
    os.replace(tmp_path, str(env_path))
except BaseException:
    if not closed:
        os.close(fd)
    os.unlink(tmp_path)
    raise
```

---

### M-02: Proxy subprocess inherits full parent environment

**Severity:** MEDIUM
**Category:** Information Leakage
**Location:** `src/worthless/cli/process.py:95`

**Issue:**
```python
full_env = {**os.environ, **env, ...}
```

The proxy child inherits the entire parent environment, including potentially sensitive variables like `AWS_SECRET_ACCESS_KEY`, `DATABASE_URL`, `GITHUB_TOKEN`, etc. The proxy only needs `WORTHLESS_*` variables plus basic system variables (`PATH`, `HOME`, etc.).

**Remediation:**
Construct a minimal environment:
```python
PASSTHROUGH = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH"}
full_env = {k: v for k, v in os.environ.items() if k in PASSTHROUGH}
full_env.update(env)
```

---

### M-03: `_resolve_port` does not validate port range

**Severity:** MEDIUM
**Category:** Input Validation
**Location:** `src/worthless/cli/commands/up.py:38-48`

**Issue:**
```python
env_port = os.environ.get("WORTHLESS_PORT")
if env_port:
    return int(env_port)
```

No validation that the port is in the valid range (1-65535). A negative port or port > 65535 will be passed to uvicorn, which may behave unexpectedly. `int()` conversion also raises `ValueError` on non-numeric input, which is unhandled and will produce an ugly traceback.

**Remediation:**
```python
try:
    p = int(env_port)
    if 1 <= p <= 65535:
        return p
except ValueError:
    pass
```

---

### M-04: `_unlock_alias` returns reconstructed key as a string

**Severity:** MEDIUM
**Category:** Key Material Lifetime
**Location:** `src/worthless/cli/commands/unlock.py:34-94`

**Issue:**
`_unlock_alias` returns `key_str` (a Python `str` object) on line 89. Python strings are immutable and cannot be zeroed. While `key_buf` (the bytearray) is zeroed in the finally block on line 91, the `str` copy (`key_str = key_buf.decode()` on line 61) persists in memory until garbage collected.

The returned `key_str` is not used by the caller in `unlock` (the return value is ignored on line 116), but it still exists on the stack.

**Impact:**
Reconstructed API keys persist as immutable strings in memory. In a long-running process or if the GC is delayed, these could be extracted from a memory dump.

**Remediation:**
1. Don't return the key string -- the function already does everything with it (rewrite env, print to stdout).
2. If the key must be used externally, return the bytearray and let the caller zero it.

---

### M-05: `WORTHLESS_ALLOW_INSECURE` hardcoded to "true"

**Severity:** MEDIUM
**Category:** Security Misconfiguration
**Location:** `src/worthless/cli/process.py:95`, `src/worthless/cli/commands/up.py:126`

**Issue:**
Both `spawn_proxy` and `_start_daemon` hardcode `WORTHLESS_ALLOW_INSECURE` to `"true"`:
```python
full_env = {..., "WORTHLESS_ALLOW_INSECURE": env.get("WORTHLESS_ALLOW_INSECURE", "true")}
```

This bypasses whatever security check `WORTHLESS_ALLOW_INSECURE` guards in the proxy. The value defaults to `"true"` even if the caller doesn't set it.

**Remediation:**
1. Default to `"false"` and require explicit opt-in.
2. Only set this flag in development/test mode, not in the default code path.

---

### M-06: Hook injection does not sanitize pre-commit content

**Severity:** MEDIUM
**Category:** Code Injection
**Location:** `src/worthless/cli/commands/scan.py:147-169`

**Issue:**
`_install_hook` appends to an existing pre-commit hook file without validating the existing content. If a malicious actor has already modified the hook, `worthless scan --install-hook` legitimizes the hook by appending to it. More importantly, the `$@` in the snippet could be a concern if pre-commit passes untrusted arguments:

```python
snippet = f'\n{marker}\nworthless scan --pre-commit "$@"\n'
```

This is a minor concern since git pre-commit hooks don't typically receive arguments, but the pattern of writing shell code to files warrants care.

**Remediation:**
Validate that the existing hook content is a valid shell script and warn if it contains suspicious content.

---

## LOW Issues (Consider Fixing)

### L-01: `disable_core_dumps()` only affects the parent process

**Severity:** LOW
**Category:** Defense in Depth
**Location:** `src/worthless/cli/process.py:32-43`

**Issue:**
`disable_core_dumps()` is called in the parent (CLI) process, but subprocess children (the proxy, the wrapped child) inherit the RLIMIT_CORE setting only if they don't reset it. The proxy (a Python/uvicorn process) does not explicitly call `disable_core_dumps()`.

**Remediation:**
Have the proxy app call `disable_core_dumps()` on startup (in `create_app()`), or set `RLIMIT_CORE` via `subprocess.Popen(preexec_fn=...)`.

---

### L-02: Alias derivation uses truncated SHA-256

**Severity:** LOW
**Category:** Collision Risk
**Location:** `src/worthless/cli/commands/lock.py:25-28`

**Issue:**
```python
digest = hashlib.sha256(api_key.encode()).hexdigest()[:8]
return f"{provider}-{digest}"
```

8 hex characters = 32 bits of entropy. Birthday collision probability reaches 50% at ~65,000 keys per provider. For a CLI tool managing a handful of keys this is fine, but should be documented.

**Remediation:**
Increase to 12-16 hex characters, or document the limitation.

---

### L-03: Scanner loads all shard_a files into memory as a set

**Severity:** LOW
**Category:** Information Exposure
**Location:** `src/worthless/cli/scanner.py:28-48`

**Issue:**
`load_enrollment_data()` reads all shard_a file contents into a Python `set`. These are half of the key material. They persist in memory for the duration of the scan. Since shard_a values are binary XOR masks (not the actual keys), this is lower severity, but it increases the window where key material is in memory.

**Remediation:**
Consider using a hash-based comparison (store SHA-256 of shard_a, compare hashes) to avoid holding raw shard_a bytes in memory.

---

### L-04: Error messages may leak internal paths

**Severity:** LOW
**Category:** Information Disclosure
**Location:** Various (e.g., `dotenv_rewriter.py:78`, `process.py:147-149`)

**Issue:**
Error messages include full file paths, environment details, and uvicorn output. In a CLI context this is generally acceptable, but be cautious about these appearing in CI logs or crash reports.

---

## INFO (Observations)

### I-01: Good practice -- `SplitResult.__repr__` redacts all fields
**Location:** `src/worthless/crypto/types.py:48-58`
This prevents accidental logging of key material. Well done.

### I-02: Good practice -- atomic file operations in bootstrap
**Location:** `src/worthless/cli/bootstrap.py:66-76`
Fernet key creation uses `O_CREAT | O_EXCL` with explicit `0o600`. This is the correct pattern.

### I-03: Good practice -- key zeroing in lock/unlock paths
**Location:** `src/worthless/cli/commands/lock.py:109-110`, `unlock.py:91-93`
Both `lock` and `unlock` zero key material in `finally` blocks. The `lock` command is particularly thorough.

---

## Security Checklist

- [x] No hardcoded secrets in source
- [x] SQL queries are parameterized (SQLite `?` placeholders throughout)
- [x] XSS prevention (N/A -- CLI tool, no HTML output)
- [ ] CSRF protection (N/A)
- [x] Authentication required on proxy (session token in wrap mode)
- [ ] All file writes use restrictive permissions -- **FAIL** (enroll_stub, PID file)
- [x] Key material zeroed after use -- mostly, except enroll_stub
- [x] Core dump suppression -- parent only, not child
- [ ] Environment variable hygiene -- **FAIL** (Fernet key in env, full env inheritance)
- [x] Error messages do not leak secrets
- [x] Dependencies use parameterized queries
- [ ] Temp files secured -- **FAIL** (deep scan env dump)

---

## Priority Remediation Order

1. **C-01**: Refactor deep scan to avoid writing env to disk (quick fix, high impact)
2. **C-02**: Move Fernet key from env var to fd/file passing (architecture change)
3. **H-01**: Add stdin/prompt input for `enroll --key` (quick fix)
4. **H-02**: Fix enroll_stub permissions (quick fix)
5. **H-03**: Add `sr.zero()` to enroll_stub (one-liner)
6. **H-05**: Integrate stale lock check into acquire_lock (moderate refactor)
7. **M-05**: Change WORTHLESS_ALLOW_INSECURE default to false
8. Everything else in severity order

---

> Security review performed by Claude Opus 4.6 security-reviewer agent
