# Phase 1: Crypto Core and Storage - Research

**Researched:** 2026-03-14
**Domain:** Cryptographic key splitting (XOR), HMAC commitment, encrypted storage (SQLite)
**Confidence:** HIGH

## Summary

Phase 1 builds the cryptographic foundation: XOR key splitting with `secrets.token_bytes()`, HMAC-SHA256 commitment for tamper detection, `bytearray` zeroing for best-effort memory safety, and encrypted shard storage in SQLite via aiosqlite. This is a greenfield project with no existing code.

The crypto primitives are straightforward -- XOR splitting is a well-understood operation and Python's stdlib provides everything needed for HMAC and secure randomness. The main decision point is Shard B encryption at rest: use `cryptography` (pyca) with Fernet rather than rolling AES from stdlib. Fernet provides authenticated encryption (AES-128-CBC + HMAC-SHA256) in a single API call, eliminating padding/IV/mode mistakes.

**Primary recommendation:** Use Python stdlib (`secrets`, `hmac`, `hashlib`) for all crypto primitives. Use pyca `cryptography` Fernet for Shard B encryption at rest. Use `aiosqlite` for async SQLite storage. Use Ruff TID251 to ban the `random` module.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CRYP-01 | Key split into Shard A + Shard B using XOR with `secrets.token_bytes()` | XOR splitting is trivial with stdlib `secrets` -- no external library needed. See Architecture Patterns and Code Examples. |
| CRYP-02 | HMAC commitment verifies shard integrity on reconstruction | Python stdlib `hmac` + `hashlib` with `hmac.compare_digest()` for timing-safe comparison. See Code Examples. |
| CRYP-03 | Reconstructed key stored in `bytearray`, zeroed after use (best-effort in Python) | `bytearray` supports in-place mutation for zeroing. Documented limitations around GC/copies. See Common Pitfalls. |
| CRYP-04 | `secrets` module enforced, `random` module banned via lint rule | Ruff TID251 (`banned-api`) bans `random` at lint time. See Architecture Patterns. |
| STOR-01 | Shard B encrypted at rest (aiosqlite) | Fernet (pyca `cryptography` 46.0.5) encrypts shard bytes before SQLite INSERT. aiosqlite 0.22.1 for async access. See Standard Stack. |
| STOR-02 | Enrollment metadata persisted locally | Same aiosqlite database, separate table for metadata (key alias, provider, timestamps). See Architecture Patterns. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `secrets` | stdlib | CSPRNG for XOR mask generation | Python stdlib, cryptographically secure, uses `os.urandom()` under the hood |
| `hmac` | stdlib | HMAC-SHA256 commitment generation and verification | Python stdlib, includes `compare_digest()` for timing-safe comparison |
| `hashlib` | stdlib | SHA-256 hashing for HMAC | Python stdlib, OpenSSL-backed |
| `cryptography` | 46.0.5 | Fernet symmetric encryption for Shard B at rest | PyCA maintained, Fernet provides authenticated encryption (AES-128-CBC + HMAC-SHA256), no mode/padding/IV footguns |
| `aiosqlite` | 0.22.1 | Async SQLite for shard and metadata storage | Thin async wrapper over stdlib `sqlite3`, no heavy ORM needed for PoC |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `ruff` | latest | Linter with TID251 rule to ban `random` module | CI and pre-commit, enforces CRYP-04 |
| `pytest` | latest | Test framework | All unit and integration tests |
| `pytest-asyncio` | latest | Async test support for aiosqlite tests | Testing async storage layer |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Fernet (`cryptography`) | `Cipher` with AES-GCM from same library | AES-GCM is faster and uses 256-bit keys, but requires manual nonce management. Fernet is higher-level and safer for PoC. Switch to AES-256-GCM in Rust hardening phase. |
| Fernet (`cryptography`) | stdlib-only (no external crypto) | Python stdlib has no symmetric encryption. Would need to shell out or use `hashlib` PBKDF2 + custom AES -- exactly the kind of hand-rolling to avoid. |
| `aiosqlite` | `sqlite3` (sync) | The proxy will be async (FastAPI), so async storage from the start avoids blocking the event loop later. Minimal overhead now, pays off in Phase 3. |

**Installation:**
```bash
pip install cryptography==46.0.5 aiosqlite==0.22.1
# Dev dependencies
pip install ruff pytest pytest-asyncio
```

## Architecture Patterns

### Recommended Project Structure
```
src/
  worthless/
    __init__.py
    crypto/
      __init__.py
      splitter.py        # XOR split/reconstruct + HMAC commitment
      types.py           # Shard, Commitment, SplitResult dataclasses
    storage/
      __init__.py
      repository.py      # ShardRepository (aiosqlite + Fernet encryption)
      schema.py           # SQLite DDL and migrations
    exceptions.py         # IntegrityError, ShardTamperedError
tests/
  conftest.py             # Shared fixtures (temp DB, sample keys)
  test_splitter.py        # CRYP-01, CRYP-02, CRYP-03 tests
  test_storage.py         # STOR-01, STOR-02 tests
  test_lint.py            # CRYP-04 enforcement test
pyproject.toml            # Project config, ruff settings, dependencies
```

### Pattern 1: XOR Split with HMAC Commitment
**What:** Split an API key into two shards using XOR, generate HMAC commitment for integrity verification on reconstruction.
**When to use:** Every enrollment operation (CRYP-01, CRYP-02).
**Key design points:**
- The split function takes `key_bytes: bytes` and returns `(shard_a: bytes, shard_b: bytes, commitment: bytes, nonce: bytes)`
- `nonce` is a random value used as the HMAC key -- generated with `secrets.token_bytes(32)`
- `commitment = HMAC-SHA256(nonce, key_bytes)` -- proves the original key without storing it
- On reconstruction: `key = shard_a XOR shard_b`, then verify `HMAC-SHA256(nonce, key) == commitment`
- Use `hmac.compare_digest()` for timing-safe comparison

### Pattern 2: Bytearray Zeroing Context Manager
**What:** A context manager that ensures key material in `bytearray` is zeroed after use.
**When to use:** Every reconstruction operation (CRYP-03).
**Key design points:**
- Reconstructed key stored as `bytearray` (mutable, unlike `bytes`)
- Context manager zeros bytes on exit: `key_buf[:] = b'\x00' * len(key_buf)`
- This is best-effort in CPython -- the GC may have already copied bytes internally
- Document limitation: Python PoC cannot guarantee no residual copies in heap; Rust hardening phase will use `zeroize` crate

### Pattern 3: Fernet Encryption for Storage
**What:** Encrypt Shard B bytes with Fernet before writing to SQLite.
**When to use:** Every shard storage/retrieval (STOR-01).
**Key design points:**
- Generate a Fernet key once at first run, store it in a config file or derive from a passphrase
- For PoC: `Fernet.generate_key()` stored in a local file (e.g., `~/.worthless/storage.key`)
- Encrypt: `fernet.encrypt(shard_b_bytes)` -- returns a URL-safe base64 token
- Decrypt: `fernet.decrypt(token)` -- returns original bytes
- Fernet includes timestamp and HMAC -- tampered ciphertext is rejected automatically

### Pattern 4: Ruff TID251 for Module Banning
**What:** Lint rule that bans `import random` and `from random import *` across the codebase.
**When to use:** CI and pre-commit (CRYP-04).
**Configuration in pyproject.toml:**
```toml
[tool.ruff]
select = ["E", "F", "W", "TID"]

[tool.ruff.lint.flake8-tidy-imports.banned-api]
"random".msg = "Use the 'secrets' module for cryptographic randomness. The 'random' module is banned (CRYP-04)."
```

### Anti-Patterns to Avoid
- **Using `bytes` for key material:** `bytes` is immutable -- you cannot zero it after use. Always use `bytearray` for anything that needs to be wiped.
- **String representations of keys:** Never convert key bytes to `str` for logging or storage. Strings are immutable and interned by Python.
- **Manual AES implementation:** Do not hand-roll AES-CBC with padding. Use Fernet or a high-level recipe.
- **`==` for HMAC comparison:** Always use `hmac.compare_digest()` to prevent timing side-channels.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Symmetric encryption | Custom AES-CBC + PKCS7 + IV generation | `cryptography.fernet.Fernet` | Padding oracle attacks, IV reuse, missing authentication. Fernet handles all of this. |
| Timing-safe comparison | `==` on digest bytes | `hmac.compare_digest()` | Side-channel timing attacks can leak HMAC bytes one at a time. |
| Secure random bytes | `random.randbytes()` or `os.urandom()` directly | `secrets.token_bytes()` | `secrets` is the stdlib API designed for this. `random` is explicitly not cryptographically secure. |
| Async SQLite | Thread pool + raw `sqlite3` | `aiosqlite` | Correct async bridging with proper connection lifecycle. |

**Key insight:** The crypto primitives in this phase are simple (XOR, HMAC), but the surrounding concerns (timing safety, memory lifecycle, authenticated encryption) are where mistakes happen. Use established recipes for everything except the XOR operation itself.

## Common Pitfalls

### Pitfall 1: Bytearray Copies in Python Runtime
**What goes wrong:** You zero a `bytearray`, but Python's runtime may have already copied the bytes into immutable `bytes` objects, string representations, or other internal buffers.
**Why it happens:** CPython's memory allocator, garbage collector, and string interning create copies you cannot track or zero.
**How to avoid:** Accept this as a documented limitation of the Python PoC. Use `bytearray` and zero it (best-effort). Never convert key material to `str` or `bytes`. Document in SECURITY_POSTURE.md that Rust hardening phase will use `zeroize` crate with compiler barriers.
**Warning signs:** Key material appearing in `repr()`, f-strings, or log output.

### Pitfall 2: Fernet Key Storage Location
**What goes wrong:** The Fernet key used to encrypt Shard B at rest is stored insecurely (e.g., same SQLite database, world-readable file).
**Why it happens:** Encryption at rest is only useful if the encryption key has different access controls than the encrypted data.
**How to avoid:** Store the Fernet key in a separate file with restricted permissions (`0600`). For PoC, `~/.worthless/storage.key` with `os.chmod()`. In production, this would be a KMS-derived key.
**Warning signs:** Fernet key and encrypted shards in the same file/database.

### Pitfall 3: HMAC Nonce Reuse
**What goes wrong:** Using the same nonce for multiple key enrollments allows cross-key comparison attacks.
**Why it happens:** Generating the nonce once at initialization instead of per-enrollment.
**How to avoid:** Generate a fresh `secrets.token_bytes(32)` nonce for every `split()` call. Store the nonce alongside the commitment and Shard B.
**Warning signs:** A single nonce value in the codebase or config.

### Pitfall 4: SQLite WAL Mode for Concurrent Access
**What goes wrong:** Multiple async operations hit SQLite concurrently and get `database is locked` errors.
**Why it happens:** SQLite's default journal mode serializes writes aggressively.
**How to avoid:** Enable WAL mode on connection: `PRAGMA journal_mode=WAL`. This allows concurrent reads with one writer.
**Warning signs:** `OperationalError: database is locked` in tests or async operations.

### Pitfall 5: Logging Key Material
**What goes wrong:** Shard bytes, reconstructed keys, or Fernet tokens appear in debug logs.
**Why it happens:** Default `__repr__` on dataclasses will print all fields including byte values.
**How to avoid:** Override `__repr__` on all crypto dataclasses to redact sensitive fields. Never log raw bytes. The logging denylist from CLAUDE.md applies from day one.
**Warning signs:** Base64 strings > 32 chars in log output.

## Code Examples

Verified patterns from official sources:

### XOR Key Splitting (CRYP-01)
```python
# Source: Python stdlib docs (secrets, hmac)
import secrets
import hmac
import hashlib
from dataclasses import dataclass

@dataclass
class SplitResult:
    shard_a: bytes       # Client keeps this
    shard_b: bytes       # Server stores this (encrypted)
    commitment: bytes    # HMAC(nonce, original_key)
    nonce: bytes         # Random HMAC key, stored with shard_b

    def __repr__(self) -> str:
        return "SplitResult(shard_a=<redacted>, shard_b=<redacted>, ...)"

def split_key(api_key: bytes) -> SplitResult:
    """Split an API key into two XOR shards with HMAC commitment."""
    mask = secrets.token_bytes(len(api_key))
    shard_a = bytes(a ^ b for a, b in zip(api_key, mask))
    shard_b = mask
    nonce = secrets.token_bytes(32)
    commitment = hmac.new(nonce, api_key, hashlib.sha256).digest()
    return SplitResult(shard_a=shard_a, shard_b=shard_b,
                       commitment=commitment, nonce=nonce)
```

### HMAC Verification on Reconstruction (CRYP-02)
```python
# Source: Python stdlib docs (hmac.compare_digest)
class ShardTamperedError(Exception):
    """Raised when HMAC verification fails during reconstruction."""

def reconstruct_key(
    shard_a: bytes,
    shard_b: bytes,
    commitment: bytes,
    nonce: bytes,
) -> bytearray:
    """Reconstruct key from shards, verify integrity, return as bytearray."""
    # XOR reconstruction
    key = bytearray(a ^ b for a, b in zip(shard_a, shard_b))
    # HMAC verification (timing-safe)
    expected = hmac.new(nonce, bytes(key), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, commitment):
        # Zero before raising
        key[:] = b'\x00' * len(key)
        raise ShardTamperedError("HMAC verification failed: shards may be tampered")
    return key
```

### Bytearray Zeroing Context Manager (CRYP-03)
```python
# Source: Python memory management docs
from contextlib import contextmanager

@contextmanager
def secure_key(key_buf: bytearray):
    """Context manager that zeros key material on exit (best-effort)."""
    try:
        yield key_buf
    finally:
        key_buf[:] = b'\x00' * len(key_buf)
```

### Fernet Encryption for Shard B (STOR-01)
```python
# Source: cryptography.io Fernet docs (v46.0.5)
from cryptography.fernet import Fernet

def encrypt_shard(shard_b: bytes, fernet_key: bytes) -> bytes:
    """Encrypt shard B for storage at rest."""
    f = Fernet(fernet_key)
    return f.encrypt(shard_b)

def decrypt_shard(token: bytes, fernet_key: bytes) -> bytes:
    """Decrypt shard B from storage."""
    f = Fernet(fernet_key)
    return f.decrypt(token)
```

### aiosqlite Storage Schema (STOR-01, STOR-02)
```python
# Source: aiosqlite docs (v0.22.1)
import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS shards (
    key_alias     TEXT PRIMARY KEY,
    shard_b_enc   BLOB NOT NULL,      -- Fernet-encrypted shard B
    commitment    BLOB NOT NULL,      -- HMAC commitment
    nonce         BLOB NOT NULL,      -- HMAC nonce
    provider      TEXT NOT NULL,      -- 'openai' | 'anthropic'
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS metadata (
    key           TEXT PRIMARY KEY,
    value         TEXT NOT NULL
);
"""

async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.commit()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `os.urandom()` directly | `secrets.token_bytes()` | Python 3.6 (2016) | `secrets` is the blessed API for security tokens |
| `pycrypto` | `cryptography` (pyca) | ~2018 | pycrypto is abandoned, cryptography is actively maintained |
| Sync `sqlite3` | `aiosqlite` | Stable since 2020 | Required for async frameworks (FastAPI) |
| Manual AES-CBC | Fernet recipe | Available since `cryptography` 0.7 | Fernet eliminates padding/IV/auth footguns |

**Deprecated/outdated:**
- `pycrypto` / `pycryptodome`: Do not use for new projects. `cryptography` is the standard.
- `os.urandom()` for tokens: Use `secrets.token_bytes()` instead (same source, clearer intent).

## Open Questions

1. **Fernet Key Derivation for PoC**
   - What we know: Fernet needs a 32-byte URL-safe base64 key. `Fernet.generate_key()` creates one.
   - What's unclear: For the PoC, should we derive the Fernet key from a user passphrase (PBKDF2) or just generate and store it?
   - Recommendation: Generate and store in `~/.worthless/storage.key` with `0600` permissions. Passphrase derivation adds UX friction for the PoC. Revisit for production.

2. **Shard A Storage Location (Client-Side)**
   - What we know: Phase 1 focuses on Shard B storage. Shard A is "client keeps this."
   - What's unclear: Where does the client store Shard A in the PoC? Filesystem? Keyring?
   - Recommendation: For Phase 1, store Shard A in a local file (`~/.worthless/shards/{alias}.key`). The `keyring` library (STATE.md blocker) is a Phase 4 CLI concern. Phase 1 only needs to prove the crypto works.

3. **Key Alias Scheme**
   - What we know: Need a way to identify which key is which.
   - What's unclear: Human-readable alias? Hash-based ID? Both?
   - Recommendation: Accept a human-readable alias at enrollment (e.g., "openai-main"), use it as primary key in SQLite.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio |
| Config file | `pyproject.toml` (Wave 0: create `[tool.pytest.ini_options]` section) |
| Quick run command | `pytest tests/ -x -q` |
| Full suite command | `pytest tests/ -v --tb=short` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CRYP-01 | XOR split produces two shards that XOR back to original | unit | `pytest tests/test_splitter.py::test_xor_roundtrip -x` | -- Wave 0 |
| CRYP-01 | Shards are correct length (same as input key) | unit | `pytest tests/test_splitter.py::test_shard_length -x` | -- Wave 0 |
| CRYP-01 | Neither shard equals the original key | unit | `pytest tests/test_splitter.py::test_shards_differ_from_key -x` | -- Wave 0 |
| CRYP-02 | Valid shards pass HMAC verification | unit | `pytest tests/test_splitter.py::test_hmac_valid -x` | -- Wave 0 |
| CRYP-02 | Tampered shard_a fails HMAC verification | unit | `pytest tests/test_splitter.py::test_hmac_tampered_shard_a -x` | -- Wave 0 |
| CRYP-02 | Tampered shard_b fails HMAC verification | unit | `pytest tests/test_splitter.py::test_hmac_tampered_shard_b -x` | -- Wave 0 |
| CRYP-03 | Reconstructed key is a bytearray | unit | `pytest tests/test_splitter.py::test_reconstruct_returns_bytearray -x` | -- Wave 0 |
| CRYP-03 | Bytearray is zeroed after context manager exit | unit | `pytest tests/test_splitter.py::test_bytearray_zeroed -x` | -- Wave 0 |
| CRYP-04 | `random` module not importable (ruff check passes) | lint | `ruff check src/ --select TID251` | -- Wave 0 |
| STOR-01 | Shard B encrypted before storage, decrypted on retrieval | integration | `pytest tests/test_storage.py::test_shard_roundtrip -x` | -- Wave 0 |
| STOR-01 | Raw SQLite column does not contain plaintext shard | integration | `pytest tests/test_storage.py::test_shard_encrypted_at_rest -x` | -- Wave 0 |
| STOR-02 | Enrollment metadata persists across connection reopen | integration | `pytest tests/test_storage.py::test_metadata_persistence -x` | -- Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/ -x -q`
- **Per wave merge:** `pytest tests/ -v --tb=short && ruff check src/ --select TID251`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `pyproject.toml` -- project config with dependencies, ruff config, pytest config
- [ ] `tests/conftest.py` -- shared fixtures (temp SQLite DB, sample API key bytes, Fernet key)
- [ ] `tests/test_splitter.py` -- covers CRYP-01, CRYP-02, CRYP-03
- [ ] `tests/test_storage.py` -- covers STOR-01, STOR-02
- [ ] Framework install: `pip install cryptography aiosqlite pytest pytest-asyncio ruff`

## Sources

### Primary (HIGH confidence)
- Python stdlib docs: `secrets`, `hmac`, `hashlib` modules -- core crypto primitives
- [cryptography.io Fernet docs (v46.0.5)](https://cryptography.io/en/stable/fernet/) -- Fernet API, algorithm details
- [aiosqlite PyPI (v0.22.1)](https://pypi.org/project/aiosqlite/) -- version and Python support
- [Ruff TID251 banned-api rule](https://docs.astral.sh/ruff/rules/banned-api/) -- import banning configuration

### Secondary (MEDIUM confidence)
- [Python memory management docs](https://docs.python.org/3/c-api/memory.html) -- bytearray zeroing limitations
- [Ruff settings for flake8-tidy-imports](https://docs.astral.sh/ruff/settings/) -- banned-api configuration syntax

### Tertiary (LOW confidence)
- None -- all findings verified with official documentation.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries are well-established, versions verified on PyPI
- Architecture: HIGH -- XOR splitting is trivially correct, patterns are standard
- Pitfalls: HIGH -- memory zeroing limitations are well-documented in CPython
- Storage: HIGH -- Fernet + aiosqlite is a standard pattern for encrypted-at-rest SQLite

**Blocker resolutions:**
- "Shard B encryption at rest: stdlib vs pyca cryptography" -- **Resolved: Use pyca `cryptography` Fernet.** Python stdlib has no symmetric encryption API. Fernet provides authenticated encryption with zero configuration footguns.
- "keyring reliability on headless Linux" -- **Deferred to Phase 4.** Phase 1 does not need keyring. Shard A storage for Phase 1 uses filesystem. CLI phase will address keyring with fallback strategy.

**Research date:** 2026-03-14
**Valid until:** 2026-04-14 (stable domain, 30-day validity)
