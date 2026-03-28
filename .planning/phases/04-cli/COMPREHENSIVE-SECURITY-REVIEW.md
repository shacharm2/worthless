# Comprehensive Security Review: Worthless CLI Split-Key Reverse Proxy

**Date:** 2026-03-27
**Reviewer:** Claude Opus 4.6
**Scope:** Full CLI + crypto + storage + proxy layers

---

## 1. Memory Safety: Key Material Zeroing in Python

### F-1.1: `_zero_buf` Cannot Guarantee Erasure in CPython

**Severity:** Medium (accepted PoC limitation)
**Description:**
`_zero_buf(buf)` does `buf[:] = bytearray(len(buf))`. In CPython, `bytearray` uses a contiguous C buffer. Slice assignment calls `memmove` on the *same* buffer when the replacement has the same length, so the zeroing is in-place. However:

- CPython's allocator (`pymalloc` / `malloc`) may have **already copied** the buffer during a prior resize (e.g., the generator expression `bytearray(a ^ b for a, b in zip(...))` builds incrementally, potentially triggering realloc). The old copy is freed but not zeroed by the allocator.
- `bytearray(len(buf))` allocates a *new* zero-filled buffer, then copies zeros into `buf`. The temporary is immediately freed but its allocation is not guaranteed to overlap the old data.

**Verdict:** Zeroing is best-effort in CPython. It prevents casual inspection (e.g., `gc.get_objects()`) but cannot prevent forensic memory analysis. This is documented as a known PoC limitation with a planned Rust FFI upgrade path (noted in `types.py:15`). **Acceptable for PoC.**

**PyPy note:** PyPy's garbage collector moves objects, making zeroing nearly useless. If PyPy support is ever needed, use `ctypes.memset` or cffi.

### F-1.2: Immutable `str` Copies of Key Material

**Severity:** High
**Description:**
Multiple code paths create Python `str` objects from key material. These are immutable and **cannot be zeroed**:

| Location | Expression | Lifetime |
|---|---|---|
| `lock.py:25` | `hashlib.sha256(api_key.encode())` | `api_key.encode()` creates a `bytes` copy; short-lived but unclearable |
| `lock.py:27` | `f"{provider}-{digest}"` | Contains hash of key, not key itself -- acceptable |
| `unlock.py:61` | `key_buf.decode()` | **Full API key as immutable `str`** -- persists until GC |
| `unlock.py:82` | `f"{var_name}={key_str}\n"` | **Full API key in f-string** -- new `str` object |
| `unlock.py:86` | `f"{alias}={key_str}\n"` | Same issue |
| `proxy/app.py:306` | `adapter.prepare_request(..., api_key=k)` | Adapter likely calls `k.decode()` (confirmed in comment at line 316-318) |
| `repository.py:83` | `bytes(shard.shard_b)` | Creates immutable `bytes` from `bytearray` for Fernet encrypt |

The `unlock.py:61` path is the worst: the full reconstructed key is held as a `str` that cannot be zeroed. The `key_buf` bytearray IS zeroed in the `finally` at line 91, but `key_str` lives on.

**Exploitation:** Memory dump of the process (via core dump, debugger attach, or `/proc/<pid>/mem`) reveals plaintext API keys.

**Remediation:**
1. `unlock.py`: Don't return `key_str`. Write `key_buf` directly to the env file as bytes. Avoid `.decode()` entirely where possible.
2. `proxy/app.py`: The comment at line 316-318 already acknowledges this and notes the Rust reconstruction service will fix it.
3. For the PoC: minimize the scope where `str` copies exist (e.g., don't assign to a variable -- pass directly).

### F-1.3: `sr.zero()` Reachability in Exception Paths

**Severity:** Low
**Description:**
In `lock.py:78-110`, `sr.zero()` is in a `finally` block that covers the entire loop body -- **correct**.

In `_enroll_single` (lock.py:121-144), `sr.zero()` is in a `finally` that covers the `os.open` and `asyncio.run` calls -- **correct**.

In `split_key()` itself: no zeroing of the `api_key` parameter (caller's responsibility) -- the intermediate `mask` and generator expression produce `bytearray` objects that are referenced only by `SplitResult`, which the caller zeros.

**Verdict:** Exception paths are well-handled. No missing `finally` blocks found.

---

## 2. Process Isolation Gaps

### F-2.1: Fernet Key in Environment Variable

**Severity:** Critical
**Description:**
`wrap.py:149-153` and `up.py:93-97` pass `WORTHLESS_FERNET_KEY` as an env var:

```python
proxy_env = {
    "WORTHLESS_FERNET_KEY": home.fernet_key.decode(),
    ...
}
```

On Linux, `/proc/<pid>/environ` is readable by the same UID (or root). On macOS, `ps eww` or `sysctl kern.procargs2` can expose it. The env is also inherited by any grandchild processes the proxy spawns (e.g., if uvicorn forks workers).

**Exploitation scenario:** Malware running as the same user reads `/proc/<proxy_pid>/environ` to obtain the Fernet key. Combined with reading `~/.worthless/worthless.db` and `~/.worthless/shard_a/*`, all API keys are reconstructed.

**Remediation:**
1. Pass via inherited file descriptor: `os.pipe()` -> write key to write end -> pass read fd via `pass_fds` -> proxy reads and closes fd.
2. Or: write key to a tmpfile with 0600 perms, pass the path, proxy reads and deletes on startup.
3. Proxy should also call `disable_core_dumps()` in its own process.

### F-2.2: Session Token Not Validated by Proxy

**Severity:** High
**Description:**
`wrap.py` generates `session_token = secrets.token_urlsafe(32)` and passes it to the child via `WORTHLESS_SESSION_TOKEN`. However, examining `proxy/app.py`, the proxy **does not check this token anywhere**. The catch-all route at line 213 validates only:
- `x-worthless-alias` header (present + format)
- `x-worthless-shard-a` header or file fallback
- Rules engine evaluation

There is no `WORTHLESS_SESSION_TOKEN` validation. Any process on localhost that can guess or observe an alias can make requests through the proxy.

**Exploitation:** A malicious process on the same machine sends requests to `127.0.0.1:<port>` with a valid alias header. The proxy reconstructs the key and forwards the request. The attacker doesn't even need the session token since it's never checked.

**Remediation:**
1. Add session token validation in the proxy's catch-all route:
   ```python
   expected_token = os.environ.get("WORTHLESS_SESSION_TOKEN")
   if expected_token:
       provided = request.headers.get("authorization", "").removeprefix("Bearer ")
       if not hmac.compare_digest(provided, expected_token):
           return _uniform_401()
   ```
2. Have `_build_child_env` in `wrap.py` also set an auth header in the child's HTTP client config.

### F-2.3: Child Process Can Access Proxy Directly

**Severity:** Medium
**Description:**
The child process receives `WORTHLESS_SESSION_TOKEN` and knows the proxy port (via `{PROVIDER}_BASE_URL`). Even if session token validation is added, the child has the token. A malicious child (or malicious dependency within the child) can:
1. Read `WORTHLESS_SESSION_TOKEN` from its own env
2. Read the port from `OPENAI_BASE_URL`
3. Send arbitrary requests through the proxy with any alias

This is somewhat by design (the child needs to use the proxy), but it means the security boundary is between the child and the proxy's rules engine, not between the child and the proxy itself.

**Verdict:** Accepted architectural property. The rules engine (rate limits, spend caps) is the real control layer.

### F-2.4: Liveness Pipe Holding

**Severity:** Low
**Description:**
The liveness pipe (`create_liveness_pipe`) passes `read_fd` to the proxy. If the parent dies, the write end closes, and the proxy detects EOF. However, if the proxy forks or leaks the fd to a grandchild, that grandchild holding the fd would prevent the EOF detection from working.

Since uvicorn with `--workers 1` (default) doesn't fork, this is not currently exploitable. If `--workers N` is used, child workers would inherit the fd.

**Remediation:** Set `FD_CLOEXEC` on the read_fd in the proxy after reading it (or use `os.pipe2(os.O_CLOEXEC)` on Linux 2.6.27+).

### F-2.5: `process_group=0` Implications

**Severity:** Informational
**Description:**
`process_group=0` creates a new process group with the child as leader. This is correct for signal forwarding (`os.killpg`) and prevents the child from receiving signals intended for the parent's group. No security issue.

---

## 3. File Permission Attack Vectors

### F-3.1: TOCTOU Between exists() and O_CREAT|O_EXCL

**Severity:** Informational (non-issue)
**Description:**
In `lock.py:79-80`:
```python
if shard_a_path.exists():
    console.print_warning(f"Skipping {var_name} (already enrolled as {alias})")
    continue
...
fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
```

The `exists()` check is a fast-path skip for already-enrolled keys. The actual protection is `O_EXCL`, which is atomic at the kernel level. If another process creates the file between the `exists()` check and `os.open()`, the `O_EXCL` raises `FileExistsError`. The `try/finally` around this handles the error path correctly.

**Verdict:** No vulnerability. The `exists()` is a UX optimization, not a security gate.

### F-3.2: `.meta` Files Written with Default Permissions

**Severity:** High
**Description:**
`lock.py:96-99`:
```python
meta_path.write_text(json.dumps({
    "var_name": var_name,
    "env_path": str(env_path.resolve()),
}))
```

`Path.write_text()` uses the process's umask. If umask is 0o022 (common default), the file is created as 0o644 (world-readable). The `.meta` file reveals:
- Which env variable holds the key (`var_name`)
- The full path to the `.env` file (`env_path`)

While not directly sensitive, this is information leakage that helps an attacker target specific files.

**Remediation:**
```python
fd = os.open(str(meta_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
try:
    os.write(fd, json.dumps({...}).encode())
finally:
    os.close(fd)
```

### F-3.3: PID File Written with Default Permissions

**Severity:** Medium
**Description:**
`process.py:183-185` `write_pid()` uses `Path.write_text()` with default umask. A local attacker who can write to the PID file could:
1. Change the recorded PID to a process they control
2. Change the port to redirect traffic

However, since `~/.worthless/` should be mode 0700, the directory ACL protects the file. The risk is if the base directory permissions are wrong.

**Remediation:** Use `os.open(..., 0o600)` for explicit permissions.

### F-3.4: `rewrite_env_key` Temp File Permissions

**Severity:** Low
**Description:**
`dotenv_rewriter.py:82`: `tempfile.mkstemp()` creates files with mode 0600 on POSIX systems (this is `mkstemp`'s documented behavior, NOT umask-dependent). The `os.replace()` then atomically moves it to the `.env` path, preserving the 0600 permissions.

However, the original `.env` file might have had different permissions (e.g., 0644 for team sharing). After `os.replace()`, the `.env` has 0600, which may break other tools' access.

**Remediation:** Read the original file's mode before replacement and `os.chmod()` the new file to match.

### F-3.5: Symlink Attack on `~/.worthless/`

**Severity:** Medium
**Description:**
If an attacker creates `~/.worthless` as a symlink to `/tmp/evil/` before the user runs `worthless` for the first time, `ensure_home()` follows the symlink:
```python
home.base_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
```

`mkdir` with `exist_ok=True` succeeds if the target exists (even via symlink). Then `os.chmod(home.base_dir, 0o700)` sets permissions on the *symlink target*, not the symlink itself. All shard files and the Fernet key are written to the attacker-controlled directory.

**Exploitation:** Attacker creates `ln -s /tmp/attacker-dir ~/.worthless` before first run. User runs `worthless lock`. Fernet key and all shards land in `/tmp/attacker-dir/`.

**Remediation:**
1. Before `mkdir`, check that the path does not exist as a symlink:
   ```python
   if home.base_dir.is_symlink():
       raise WorthlessError(ErrorCode.INIT_FAILED, f"{home.base_dir} is a symlink -- refusing to use it")
   ```
2. After `mkdir`, verify ownership: `os.stat(home.base_dir).st_uid == os.getuid()`.
3. Use `os.open(dir, O_DIRECTORY | O_NOFOLLOW)` to open the directory without following symlinks.

### F-3.6: Pre-creation Race on `~/.worthless/`

**Severity:** Medium
**Description:**
On multi-user systems, an attacker could create `~/.worthless/` with permissive permissions before the victim runs the tool. `ensure_home()` calls `os.chmod(home.base_dir, 0o700)` which fixes this -- **but only for the base dir and shard_a_dir**. It does not check/fix permissions on `fernet.key` or `worthless.db` if they were pre-created by the attacker.

However, the Fernet key uses `O_CREAT | O_EXCL` which will fail if the file already exists. If the attacker pre-created `fernet.key`, the user gets an error. But if the attacker pre-created the *directory* with the right name and permissions, the user's `mkdir(exist_ok=True)` succeeds, and the Fernet key is written to the attacker's directory.

**Verdict:** The symlink check (F-3.5) covers this case too.

---

## 4. Race Conditions

### F-4.1: .env Read-Write Gap in `_lock_keys`

**Severity:** Medium
**Description:**
`_lock_keys` reads all keys via `scan_env_keys(env_path)`, then iterates and calls `rewrite_env_key()` for each key. If another process modifies `.env` between the scan and the rewrite, the rewrite may:
1. Overwrite a value that was changed by the other process
2. Fail to find the variable (if renamed or removed) -- this raises `KeyError`, which is unhandled in the loop

The `acquire_lock` mechanism only protects against concurrent `worthless` invocations, not against other tools editing `.env`.

**Remediation:**
1. Read the file once, perform all transformations in memory, write once atomically.
2. Or: accept this as inherent to the design (`.env` files are not transactional).

### F-4.2: Lock File Has No PID

**Severity:** Medium
**Description:**
`acquire_lock()` creates a lock file but doesn't write the PID. `check_stale_lock()` uses file mtime to detect staleness (> 5 minutes). This means:
- A legitimate long-running operation (e.g., locking 100 keys over slow I/O) could have its lock stolen after 5 minutes.
- A process that crashes leaves a lock that blocks others for up to 5 minutes.

**Remediation:**
Write PID to the lock file. Check if the PID is still alive before reclaiming:
```python
fd = os.open(str(home.lock_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, str(os.getpid()).encode())
os.close(fd)
```

### F-4.3: Daemon PID Written Before Health Confirmation

**Severity:** Low
**Description:**
`_start_daemon()` calls `write_pid()` immediately after `Popen`, then checks health. If the proxy crashes during startup, the PID file points to a dead process. The next `up` invocation will detect this via `check_pid()` and reclaim -- **this works correctly**.

**Verdict:** Not a security issue, just a UX concern (briefly incorrect PID file).

### F-4.4: TOCTOU in `cleanup_stale_pid`

**Severity:** Low
**Description:**
Between `check_pid(pid)` returning False and `pid_path.unlink()`, a new process could be assigned the same PID (PID recycling). On Linux, PIDs wrap at 32768 (or `pid_max`). However, this would only cause the stale PID file to be removed -- the new process with the same PID is unrelated to worthless and wouldn't be affected.

**Verdict:** Theoretical PID recycling risk. Not practically exploitable.

### F-4.5: Unlock Shard Deletion Order

**Severity:** Medium
**Description:**
`unlock.py:76-78`:
```python
shard_a_path.unlink(missing_ok=True)  # 1. Delete shard_a file
meta_path.unlink(missing_ok=True)      # 2. Delete metadata
asyncio.run(repo.delete(alias))        # 3. Delete DB entry
```

If the process crashes after step 1 but before step 3, shard_b remains in the DB but shard_a is gone. The key cannot be reconstructed. This is a **data loss** risk, not a security risk (the key was already restored to `.env`).

However, if the crash happens after restoring the `.env` but before deleting shards, the shards remain. On next `lock`, the key would be re-enrolled with a new alias (different shard split), so the old shards become orphaned but harmless.

**Remediation:** Delete DB entry first (if shard_a deletion fails, the key can still be reconstructed for retry). Or use a SQLite transaction that marks the shard as "unlocking" before file operations.

---

## 5. Cryptographic Implementation Review

### F-5.1: XOR Splitting is Information-Theoretically Secure

**Severity:** Informational (positive finding)
**Description:**
`splitter.py:37-41`:
```python
mask = bytearray(secrets.token_bytes(len(api_key)))  # CSPRNG
shard_a = bytearray(a ^ b for a, b in zip(api_key, mask))
shard_b = mask
```

`secrets.token_bytes()` uses the OS CSPRNG (`/dev/urandom` on POSIX, `CryptGenRandom` on Windows). XOR with a uniform random mask of equal length is a one-time pad -- each shard individually reveals nothing about the key. This is information-theoretically secure.

### F-5.2: HMAC Commitment Scheme is Correct

**Severity:** Informational (positive finding)
**Description:**
```python
nonce = bytearray(secrets.token_bytes(32))
commitment = bytearray(hmac.new(nonce, api_key, hashlib.sha256).digest())
```

- Nonce is 32 bytes from CSPRNG -- sufficient
- HMAC-SHA256 with nonce as key and api_key as message
- Verification uses `hmac.compare_digest()` (constant-time) -- correct

The commitment binds the key to the shards without revealing it. The nonce prevents precomputation attacks.

**Note:** Using the nonce as the HMAC *key* (rather than the message) is unconventional but not insecure. The standard construction would be `HMAC(api_key, nonce)` but since both are high-entropy, the order doesn't affect security.

### F-5.3: Fernet is Appropriate but Limited

**Severity:** Medium
**Description:**
Fernet provides AES-128-CBC + HMAC-SHA256 with a timestamp. Limitations:
1. **No associated data (AEAD):** Cannot bind the ciphertext to the alias. An attacker who can modify the DB could swap shard_b values between aliases.
2. **AES-128 not AES-256:** Sufficient for current threat model but below modern recommendations.
3. **Timestamp not validated:** Fernet includes a timestamp but the code doesn't check it (Fernet.decrypt without `ttl` parameter). This is fine for this use case.

**Remediation for production:** Consider using `cryptography.hazmat` with AES-256-GCM and alias as associated data. For PoC, Fernet is acceptable.

### F-5.4: Fernet Key is the Single Point of Failure

**Severity:** High
**Description:**
`~/.worthless/fernet.key` is stored as plaintext (base64-encoded Fernet key) with 0600 permissions. If this file is compromised:
1. Attacker decrypts all shard_b values from `worthless.db`
2. Combined with shard_a files from `~/.worthless/shard_a/`, all API keys are reconstructed

The threat model states "malware can read files but may not have root." If malware has read access to the user's home directory, it can read both `fernet.key` and `shard_a/` files.

**Mitigation assessment:** The directory is 0700, so only same-user or root can access. This is the best that can be done without hardware security (keychain, TPM, etc.).

**Remediation for production:**
1. macOS: Store in Keychain via `security add-generic-password`
2. Linux: Use `libsecret` / GNOME Keyring / KDE Wallet
3. Both: Support password-derived key wrapping (PBKDF2 of user password wraps the Fernet key)

### F-5.5: Alias Collision Probability

**Severity:** Low
**Description:**
`_make_alias` uses 8 hex chars (32 bits). Birthday collision at 50% probability requires ~65,536 keys per provider. For a developer tool managing 5-20 keys, the probability is negligible (~0.0003% for 20 keys).

If a collision occurs, `O_EXCL` prevents overwriting the shard_a file, and the key is skipped with a warning. **No security impact, just a UX edge case.**

### F-5.6: Decoy Does Not Leak Shard Information

**Severity:** Informational (positive finding)
**Description:**
`_make_decoy()` uses `hashlib.sha256(shard_a).hexdigest()[:8]`. SHA-256 is a one-way function -- the 8-char hex tag reveals nothing about `shard_a`. The decoy's purpose is to be recognizably low-entropy (via the "WRTLS" filler) so that re-scanning skips it. This works correctly.

---

## 6. Supply Chain and Dependency Concerns

### F-6.1: Dependency Assessment

| Dependency | Purpose | Maintained? | Recent CVEs | Network Calls? |
|---|---|---|---|---|
| `cryptography` | Fernet encryption | Yes (actively maintained by PyCA) | CVE-2023-49083 (fixed), CVE-2024-26130 (fixed) | No |
| `httpx` | HTTP client for health checks and proxy | Yes (Encode team) | No critical CVEs in 2024-2025 | Yes -- health checks to localhost only in CLI |
| `typer` | CLI framework | Yes (tiangolo) | No known CVEs | No |
| `uvicorn` | ASGI server for proxy | Yes (Encode team) | No critical CVEs in 2024-2025 | Yes -- listens on 127.0.0.1 |
| `aiosqlite` | Async SQLite | Yes | No known CVEs | No |
| `fastapi` | Web framework for proxy | Yes (tiangolo) | No critical CVEs | No (runs on uvicorn) |
| `starlette` | ASGI toolkit (via FastAPI) | Yes | CVE-2024-24762 (multipart DoS, fixed) | No |

**Verdict:** All dependencies are actively maintained with no unpatched critical CVEs. The supply chain risk is standard for a Python project.

### F-6.2: `sys.executable` Hijacking

**Severity:** Low
**Description:**
`process.py:80`: `sys.executable` points to the Python interpreter that's running the current process. If an attacker can modify `sys.executable` or place a malicious Python on `PATH`, they could intercept the proxy subprocess. However, this requires the attacker to already have write access to the Python installation or the user's PATH, which implies a deeper compromise.

### F-6.3: `WORTHLESS_ALLOW_INSECURE` Hardcoded to "true"

**Severity:** Medium
**Description:**
`process.py:95`:
```python
"WORTHLESS_ALLOW_INSECURE": env.get("WORTHLESS_ALLOW_INSECURE", "true")
```

This disables TLS enforcement in the proxy (`config.py:36-38`). The proxy checks `x-forwarded-proto` for HTTPS when `allow_insecure=False`. Since the proxy runs on localhost, TLS between client and proxy is unnecessary (traffic doesn't leave the machine). However, hardcoding this default means there's no way to enforce TLS without explicitly overriding it.

**Verdict:** Acceptable for localhost-only proxy. Should default to `False` if the proxy is ever exposed beyond localhost.

---

## 7. OWASP Top 10 Mapping

### A01: Broken Access Control

| Finding | Severity |
|---|---|
| F-3.2: `.meta` files world-readable (default umask) | High |
| F-3.5: Symlink attack on `~/.worthless/` | Medium |
| F-3.3: PID file world-readable | Medium |
| F-2.2: Session token not validated by proxy | High |

### A02: Cryptographic Failures

| Finding | Severity |
|---|---|
| F-5.4: Fernet key stored plaintext on disk | High |
| F-1.2: Immutable `str` copies of key material | High |
| F-5.3: Fernet lacks AEAD (no alias binding) | Medium |
| F-2.1: Fernet key in environment variable | Critical |

### A03: Injection

| Finding | Severity |
|---|---|
| Alias format validated with `_ALIAS_RE.fullmatch()` in proxy | N/A (mitigated) |
| `var_name` in lock.py comes from `.env` parsing, used in `re.escape()` | N/A (mitigated) |
| **Path traversal in alias:** `lock.py` uses `home.shard_a_dir / alias` where alias = `f"{provider}-{digest}"`. Provider comes from `detect_provider()` which returns a fixed set of strings. Digest is hex. No injection possible. | N/A (mitigated) |

**Verdict:** Injection risks are well-mitigated.

### A04: Insecure Design

**Is the split-key model fundamentally sound?**

The design splits each key into two shards stored in different locations (file system + encrypted SQLite). An attacker needs:
1. Shard A (from `~/.worthless/shard_a/`)
2. Shard B (from `~/.worthless/worthless.db`) + Fernet key (from `~/.worthless/fernet.key`)

Since all three are in `~/.worthless/`, an attacker with read access to the directory gets everything. The model primarily protects against:
- **.env leaks** (git commits, screenshots, logs) -- the decoy is useless without shards
- **Partial file access** (attacker can read `.env` but not `~/.worthless/`)
- **Memory dumps of non-proxy processes** (key exists only in proxy memory during requests)

**Verdict:** The design is sound for its stated threat model. It does NOT protect against full filesystem compromise of the same user.

### A05: Security Misconfiguration

| Finding | Severity |
|---|---|
| F-6.3: `WORTHLESS_ALLOW_INSECURE` defaults to true | Medium |
| `disable_core_dumps()` only in parent, not proxy child | Low |
| No Content Security Policy on proxy (N/A -- API proxy, not web app) | N/A |

### A06: Vulnerable Components

No unpatched vulnerabilities found in current dependency versions. See F-6.1.

### A07: Authentication and Identification Failures

| Finding | Severity |
|---|---|
| F-2.2: Proxy does not validate session token | High |
| Proxy uses uniform 401 responses (anti-enumeration) | Positive |
| No brute-force protection on alias guessing | Medium |

### A08: Software and Data Integrity Failures

| Finding | Severity |
|---|---|
| HMAC commitment prevents shard tampering | Positive |
| F-5.3: Fernet lacks AEAD -- shard_b could be swapped between aliases | Medium |
| F-4.5: Unlock deletion order creates inconsistent state on crash | Medium |

### A09: Security Logging and Monitoring Failures

| Finding | Severity |
|---|---|
| No security event logging (failed auth, tampered shards, rate limit hits) | Medium |
| `logger.warning` for spend recording failures only | Low |
| Key material never appears in logs (redacted `__repr__`) | Positive |

### A10: Server-Side Request Forgery (SSRF)

**Severity:** Medium
**Description:**
The proxy forwards requests to upstream provider URLs determined by the adapter registry (`get_adapter()`). The adapter maps paths to provider base URLs. If an attacker can:
1. Register a custom adapter (not currently possible -- registry is code-defined)
2. Manipulate the path to hit an internal service

Currently, adapters only route to hardcoded provider URLs (api.openai.com, api.anthropic.com, etc.). The `follow_redirects=False` setting on the httpx client prevents redirect-based SSRF.

**Verdict:** SSRF is mitigated by fixed adapter URLs and disabled redirects.

---

## 8. Additional Findings

### F-8.1: Double Lock Idempotency

**Severity:** Informational
**Description:**
Running `worthless lock` twice on the same `.env`:
1. First run: keys detected (high entropy), split, decoys written
2. Second run: `scan_env_keys()` reads the decoys, checks entropy. Decoys have low entropy (repeating "WRTLS" pattern). Shannon entropy of a typical decoy is ~3.2 bits, well below the 4.5 threshold.
3. Second run reports "No unprotected API keys found."

**Verdict:** Idempotency works correctly via the entropy filter.

### F-8.2: `enroll --key` in Shell History

**Severity:** High
**Description:**
CLI argument `--key sk-proj-abc123...` appears in:
- `~/.zsh_history` / `~/.bash_history`
- `ps aux` output while running
- `/proc/<pid>/cmdline` on Linux

**Remediation:** Already noted in prior review (H-01). Add `--key-file` or stdin support.

### F-8.3: Unlock Prints Key to stdout

**Severity:** Medium
**Description:**
`unlock.py:72-73` and `78-79` write the full API key to stdout as a recovery mechanism. This key may be captured by:
- Terminal scrollback buffers (tmux, screen)
- Shell output logging (`script`, CI/CD logs)
- Screen recording / screenshots

**Remediation:**
1. Add `--quiet` flag that suppresses stdout output
2. Write to `.env` only (default behavior when `.env` exists)
3. Warn the user before printing

### F-8.4: Scanner Reads Binary Shards as Text

**Severity:** Low
**Description:**
`scanner.py:39-44`:
```python
values.add(f.read_text().strip())
```

Shard_a files are binary XOR output. `read_text()` will raise `UnicodeDecodeError` for most binary content, which is caught by the `except (UnicodeDecodeError, OSError): continue` on line 45. The shard value is skipped, so the enrollment check doesn't work for that shard.

However, this function appears to be comparing scanned values against enrollment data, not shards against shards. The function name `load_enrollment_data` is misleading -- it's actually unused for its stated purpose since the entropy filter already handles decoy detection.

**Verdict:** The code handles the error gracefully. The function's utility is questionable but not a security risk.

### F-8.5: dotenv_rewriter Error Handler

**Severity:** Low
**Description:**
`dotenv_rewriter.py:88`:
```python
os.close(fd) if not os.get_inheritable(fd) else None
```

If `fd` is already closed (line 85), `os.get_inheritable(fd)` raises `OSError` (bad file descriptor). This exception propagates, masking the original exception. The `try/except BaseException` at line 87 is supposed to handle cleanup, but it can itself raise.

**Remediation:**
```python
except BaseException:
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(tmp_path)
    except OSError:
        pass
    raise
```

---

## Executive Summary

| ID | Finding | Severity | Category |
|----|---------|----------|----------|
| F-2.1 | Fernet key passed via environment variable | **Critical** | Process Isolation |
| F-2.2 | Session token not validated by proxy | **High** | Authentication |
| F-1.2 | Immutable `str` copies of key material (`.decode()`) | **High** | Memory Safety |
| F-5.4 | Fernet key stored plaintext on disk | **High** | Cryptographic |
| F-3.2 | `.meta` files written with default permissions | **High** | File Permissions |
| F-8.2 | `enroll --key` exposed in shell history / ps | **High** | Data Exposure |
| F-3.5 | Symlink attack on `~/.worthless/` | **Medium** | File Permissions |
| F-4.1 | .env read-write gap in `_lock_keys` | **Medium** | Race Condition |
| F-4.2 | Lock file has no PID for staleness detection | **Medium** | Race Condition |
| F-4.5 | Unlock shard deletion order (data loss on crash) | **Medium** | Race Condition |
| F-5.3 | Fernet lacks AEAD (alias binding) | **Medium** | Cryptographic |
| F-6.3 | `WORTHLESS_ALLOW_INSECURE` defaults to true | **Medium** | Configuration |
| F-8.3 | Unlock prints key to stdout | **Medium** | Data Exposure |
| F-3.3 | PID file default permissions | **Medium** | File Permissions |
| F-2.3 | Child process can use proxy freely | **Medium** | Design |
| F-1.1 | `_zero_buf` best-effort in CPython | **Medium** | Memory Safety |
| F-3.4 | `rewrite_env_key` may change .env permissions | **Low** | File Permissions |
| F-4.3 | PID written before health check | **Low** | Race Condition |
| F-4.4 | TOCTOU in `cleanup_stale_pid` | **Low** | Race Condition |
| F-5.5 | 32-bit alias collision space | **Low** | Cryptographic |
| F-8.4 | Scanner reads binary shards as text | **Low** | Data Handling |
| F-8.5 | dotenv_rewriter double-close risk | **Low** | Resource Handling |
| F-2.4 | Liveness pipe fd inheritance | **Low** | Process Isolation |
| F-6.2 | `sys.executable` hijacking | **Low** | Supply Chain |
| F-2.5 | `process_group=0` implications | **Info** | Process Isolation |
| F-3.1 | TOCTOU between exists() and O_EXCL (non-issue) | **Info** | File Permissions |
| F-5.1 | XOR splitting is information-theoretically secure | **Info** (positive) | Cryptographic |
| F-5.2 | HMAC commitment scheme is correct | **Info** (positive) | Cryptographic |
| F-5.6 | Decoy does not leak shard information | **Info** (positive) | Cryptographic |
| F-8.1 | Double-lock idempotency works correctly | **Info** (positive) | Design |

---

## Prioritized Remediation Roadmap

### Immediate (PoC blockers)

| Priority | Finding | Effort | Description |
|----------|---------|--------|-------------|
| 1 | F-2.2 | 2h | Add session token validation in proxy catch-all route |
| 2 | F-3.2 | 30min | Fix `.meta` file permissions to use `os.open(..., 0o600)` |
| 3 | F-8.2 | 1h | Add `--key-file` / stdin support for `enroll` |
| 4 | F-8.5 | 15min | Fix double-close in dotenv_rewriter error handler |

### Pre-production hardening

| Priority | Finding | Effort | Description |
|----------|---------|--------|-------------|
| 5 | F-2.1 | 4h | Pass Fernet key via fd or tmpfile instead of env var |
| 6 | F-3.5 | 1h | Add symlink check in `ensure_home()` |
| 7 | F-1.2 | 2h | Minimize `str` copies of key material in unlock path |
| 8 | F-4.2 | 1h | Write PID to lock file for staleness detection |
| 9 | F-6.3 | 15min | Default `WORTHLESS_ALLOW_INSECURE` to false |
| 10 | F-4.5 | 1h | Reorder unlock: delete DB first, then files |
| 11 | F-3.3 | 15min | Fix PID file permissions |

### Production (v1.0)

| Priority | Finding | Effort | Description |
|----------|---------|--------|-------------|
| 12 | F-5.4 | 8h | Platform keychain integration for Fernet key |
| 13 | F-5.3 | 4h | Replace Fernet with AES-256-GCM + AEAD |
| 14 | F-1.1 | 8h | Rust FFI for key reconstruction (eliminates Python memory issues) |
| 15 | F-4.1 | 2h | Single-pass .env rewrite (read once, transform, write once) |
