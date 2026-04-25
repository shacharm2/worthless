# Key Lock Transition: Threat Model

The "key lock" is the moment a plaintext API key is read, split, and the original destroyed. This is Worthless's most dangerous operation -- a failure here means permanent key loss or key exposure.

## Threat Surface

### T-1: Key residue in memory (HIGH)

**Current state:** `split_key()` accepts `bytes` (immutable) via `api_key.encode()` in the enrollment stub. The original `str` and the `bytes` copy both linger in CPython's interned/GC heap. SR-01 says "no immutable types for secrets" but enrollment currently violates this -- the raw key arrives as a `str` parameter and is `.encode()`d to `bytes`, neither of which can be zeroed.

**Remediation:** The enrollment CLI must convert the key to `bytearray` at the earliest possible moment (ideally at the read boundary -- stdin, file read, or env var read), then zero the bytearray after `split_key` returns. The `str` copy from the environment or file is unavoidable but should be overwritten in the env dict (`os.environ[var] = "REDACTED"`) and the local variable deleted.

### T-2: Key residue on disk (CRITICAL)

Four disk locations where the key persists after "deletion":

1. **The .env file itself.** Simple rewrite (read, remove line, write) leaves the old content recoverable via filesystem journal (ext4, APFS). Secure deletion on modern SSDs with wear-leveling is effectively impossible without full-disk encryption.
2. **Editor swap/backup files.** `.env.swp`, `.env.bak`, `.env~` created by editors.
3. **Shell history.** If the key was ever passed as a CLI argument or exported via `export KEY=...`, it is in `~/.bash_history` or `~/.zsh_history`.
4. **Git history.** If `.env` was ever committed (even once, even if later `.gitignore`d), the key is in the reflog and pack files permanently.

**Remediation:**
- Overwrite the key value in-place in the .env file before rewriting (write same-length random bytes over the key line, fsync, then rewrite the file without the key). This is best-effort -- SSD FTL makes guarantees impossible.
- `worthless scan` MUST check `git log -p` and `git reflog` for key patterns, not just the working tree. If a key is found in history, warn loudly and recommend `git filter-repo` or BFG.
- Warn the user if shell history contains key patterns.
- Document that full-disk encryption (FileVault, LUKS) is the only real defense against disk forensics.

### T-3: Race condition during transition (HIGH)

The transition has a dangerous window:

```
[1] Read key from .env
[2] Split into shards
[3] Store shard_b on server
[4] Write shard_a to local storage
[5] Rewrite .env with proxy URL
[6] Delete original key from .env
```

If the process crashes between steps 3 and 6, the key still exists in .env AND the shards exist. An attacker with disk access gets both. If it crashes between 5 and 4, the key is gone and shard_a was never saved -- permanent key loss.

**Remediation:** Use a write-ahead log (WAL) pattern:

1. Write a `.worthless-enroll.lock` file containing: alias, timestamp, state (SPLITTING/STORED/LOCKED).
2. On crash recovery, the lock file tells the CLI exactly where it stopped.
3. The key is only removed from .env AFTER shard_a is confirmed written and shard_b is confirmed stored.
4. The lock file is removed last.

### T-4: No recovery path (CRITICAL -- design decision)

If the user loses shard_a (disk failure, accidental deletion), the original key is unrecoverable by design. This is the "point of no return" question.

**Options:**

| Approach | Security | UX | Recommendation |
|----------|----------|----|----------------|
| No recovery | Strongest -- key exists nowhere | Worst -- must rotate at provider | Default for security-conscious users |
| Time-limited escrow | Medium -- encrypted backup with TTL | Good -- 24h recovery window | Good compromise for v1 |
| Provider re-enrollment | Strong -- just split a new key | Best -- but requires provider key rotation | Must support regardless |

**Recommendation:** Default to no-recovery with loud warnings. Support re-enrollment (new key, new split) as the recovery path. Time-limited escrow is a v2 feature if users demand it.

### T-5: Key rotation gap (MEDIUM)

When a provider key is rotated:

1. Old shards become useless (correct -- they reconstruct the old key).
2. User must run `worthless enroll` again with the new key.
3. During the gap between rotation and re-enrollment, all API calls fail.

**Remediation:**
- `worthless enroll` should support `--rotate` flag: enroll new key, then deactivate old shards atomically.
- The proxy should return a clear error (not generic 401) when reconstruction produces a key the provider rejects, suggesting `worthless enroll --rotate`.

### T-6: .env file permissions (MEDIUM)

Most .env files are created with default permissions (0644 -- world-readable). During the transition window, the key is readable by any local user.

**Remediation:**
- Before reading .env, check permissions. Warn if not 0600.
- After rewriting .env, set permissions to 0600.
- `worthless scan` should flag .env files with loose permissions.

### T-7: Shard A storage location (HIGH)

The enrollment stub writes shard_a as a plain file (`shard_a_path.write_bytes(shard_a)`). This is unencrypted on disk.

**Remediation:**
- Use OS keychain (macOS Keychain, Linux secret-service, Windows Credential Manager) as the default shard_a storage.
- File-based storage should be a fallback with 0600 permissions and a warning.
- shard_a files should never be in a git-trackable directory.

### T-8: Process environment leakage (MEDIUM)

`os.environ["OPENAI_API_KEY"]` is readable by:
- `/proc/PID/environ` on Linux (same-user readable)
- `ps eww` on macOS
- Core dumps
- Child processes (inherited by default)

**Remediation:**
- Read the key, immediately delete from `os.environ`, convert to `bytearray`.
- Document that the key exists in process memory briefly and this is acceptable for the PoC; the Rust reconstruction service eliminates this in production.

## Summary: Mandatory for v1

| ID | Fix | Effort |
|----|-----|--------|
| T-1 | bytearray at read boundary, zero after split | S |
| T-2 | git history scanning in `worthless scan` | M |
| T-3 | WAL-based crash recovery for enrollment | M |
| T-4 | No-recovery default + re-enrollment support | S |
| T-6 | Permission checks on .env | S |
| T-7 | Keychain-first shard_a storage | L |

## Summary: Document-only for v1, fix in hardening phase

| ID | Fix | Why deferred |
|----|-----|--------------|
| T-2 (disk) | Full-disk encryption advisory | Cannot solve in software on SSDs |
| T-5 | --rotate flag | Requires provider-specific key management |
| T-8 | /proc environ | Solved by Rust isolation in hardening phase |
