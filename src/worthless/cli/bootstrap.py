"""First-run ~/.worthless/ initialization and lock management."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Generator

from cryptography.fernet import Fernet

from worthless.cli.errors import ErrorCode, WorthlessError, sanitize_exception
from worthless.cli.keystore import migrate_file_to_keyring, read_fernet_key, store_fernet_key
from worthless.cli.platform import IS_WINDOWS

logger = logging.getLogger(__name__)

_DEFAULT_BASE = Path.home() / ".worthless"
_STALE_LOCK_SECONDS = 300  # 5 minutes


@dataclass
class WorthlessHome:
    """Paths within the ``~/.worthless/`` directory tree."""

    base_dir: Path = field(default_factory=lambda: _DEFAULT_BASE)
    # HF2 / worthless-mnlp: per-instance cache for the Fernet key so a single
    # CLI invocation triggers exactly one keychain probe (one macOS Keychain
    # prompt on first run). Excluded from init/repr/compare so it doesn't
    # leak into reprs or break dataclass equality across cached/uncached
    # instances.
    _cached_fernet_key: bytearray | None = field(
        default=None, init=False, repr=False, compare=False
    )
    # Per-instance lock guarding the lazy-populate path. The check-then-set
    # in ``fernet_key`` is two operations; without this lock, two threads
    # accessing the property concurrently on the same instance can both
    # observe ``None`` and both call ``read_fernet_key`` — firing duplicate
    # macOS Keychain prompts and discarding one bytearray without
    # ``zero_buf``. Real call site: ``src/worthless/mcp/server.py`` runs
    # FastMCP's asyncio loop on the main thread but dispatches blocking
    # work (``_do_lock``) via ``loop.run_in_executor`` to the default
    # thread pool — main + executor can both touch ``home.fernet_key``.
    _cache_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    @property
    def db_path(self) -> Path:
        return self.base_dir / "worthless.db"

    @property
    def fernet_key_path(self) -> Path:
        env_path = os.environ.get("WORTHLESS_FERNET_KEY_PATH")
        if env_path:
            return Path(env_path)
        return self.base_dir / "fernet.key"

    @property
    def shard_a_dir(self) -> Path:
        return self.base_dir / "shard_a"

    @property
    def lock_file(self) -> Path:
        return self.base_dir / ".lock-in-progress"

    @property
    def fernet_key(self) -> bytearray:
        """Read the Fernet key via keystore cascade (SR-01: mutable bytearray).

        Memoized per-instance after the first read. macOS Keychain
        re-evaluates the per-call ACL on every ``SecKeychainItemCopyContent``
        call, so without this cache a single ``worthless lock`` triggers 3+
        keychain prompts (bootstrap probe + ShardRepository init + proxy env
        injection). Cache is process-scoped — new CLI invocations still
        re-fetch once, which is acceptable per the bead spec.

        Each access returns a *fresh bytearray copy* of the cached value so
        callers can ``zero_buf()`` their own copy (SR-01) without poisoning
        the cache. The cache itself stays intact for subsequent reads;
        consumer mutation of one returned bytearray does not propagate.

        The lazy populate uses double-checked locking: the outer ``is None``
        check is a fast path for the warm-cache case (no lock cost on the
        ~99.9% of accesses where the cache is already populated); the inner
        check inside ``self._cache_lock`` serialises concurrent first-readers
        so only one thread calls ``read_fernet_key`` and only one Keychain
        prompt fires.
        """
        if self._cached_fernet_key is None:
            with self._cache_lock:
                if self._cached_fernet_key is None:
                    logger.debug("WorthlessHome.fernet_key cache MISS — reading from keystore")
                    self._cached_fernet_key = read_fernet_key(self.base_dir)
                else:
                    logger.debug(
                        "WorthlessHome.fernet_key cache HIT (won the lock race; another "
                        "thread populated)"
                    )
        else:
            logger.debug("WorthlessHome.fernet_key cache HIT (warm path)")
        return bytearray(self._cached_fernet_key)


def ensure_home(base_dir: Path | None = None) -> WorthlessHome:
    """Create ``~/.worthless/`` structure on first run (idempotent).

    Creates directories with 0700 permissions, generates a Fernet key if
    missing, and initialises the SQLite database.
    """
    home = WorthlessHome(base_dir=base_dir or _DEFAULT_BASE)

    try:
        # Create directories
        home.base_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        home.shard_a_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        # Ensure permissions are correct even if dir already existed
        if not IS_WINDOWS:
            home.base_dir.chmod(0o700)
            home.shard_a_dir.chmod(0o700)

        # Validate custom fernet key path if set via env var
        fernet_path = home.fernet_key_path
        fernet_parent = fernet_path.parent
        if os.environ.get("WORTHLESS_FERNET_KEY_PATH") and not fernet_parent.is_dir():
            raise WorthlessError(
                ErrorCode.BOOTSTRAP_FAILED,
                f"WORTHLESS_FERNET_KEY_PATH directory does not exist: {fernet_parent}\n"
                "Create it or mount a volume at that path.",
            )

        # Generate Fernet key if missing (keyring or file fallback). Route
        # the existence probe through ``home.fernet_key`` so the read also
        # populates ``WorthlessHome``'s per-instance cache — collapses the
        # bootstrap probe + first ``ShardRepository`` init into a single
        # ``keyring.get_password`` on the existing-key path. The missing-key
        # branch generates + stores the key, then populates the cache from
        # the freshly generated key so the next consumer also skips the
        # re-read — both paths converge on 1 keyring read per CLI invocation.
        try:
            _ = home.fernet_key
        except WorthlessError as exc:
            if exc.code != ErrorCode.KEY_NOT_FOUND:
                raise
            logger.info("ensure_home: no Fernet key found, generating new one")
            key = Fernet.generate_key()
            store_fernet_key(key, home_dir=home.base_dir)
            # Seed the cache directly from the generated key so the next
            # ``home.fernet_key`` consumer doesn't trigger a re-read.
            # SR-01: bytearray is mutable so callers can zero_buf().
            home._cached_fernet_key = bytearray(key)
            logger.debug("ensure_home: cache seeded from generated key")
        else:
            logger.debug("ensure_home: existing Fernet key found, cache populated by probe")
            migrate_file_to_keyring(home.base_dir)
    except WorthlessError:
        raise
    except OSError as exc:
        fernet_env = os.environ.get("WORTHLESS_FERNET_KEY_PATH")
        if fernet_env:
            raise WorthlessError(
                ErrorCode.BOOTSTRAP_FAILED,
                f"Cannot write fernet key to {fernet_env}: "
                f"{sanitize_exception(exc, generic='permission denied or path invalid')}\n"
                "Check that the directory exists and is writable.",
            ) from exc
        raise WorthlessError(
            ErrorCode.BOOTSTRAP_FAILED,
            sanitize_exception(exc, generic="failed to initialise home directory"),
        ) from exc

    # Initialise database (idempotent — CREATE TABLE IF NOT EXISTS)
    try:
        _init_db(home)
    except (OSError, sqlite3.DatabaseError) as exc:
        raise WorthlessError(
            ErrorCode.SHARD_STORAGE_FAILED,
            sanitize_exception(exc, generic="failed to initialise database"),
        ) from exc

    return home


def _init_db(home: WorthlessHome) -> None:
    """Create the SQLite database using the canonical schema and run migrations."""
    import asyncio

    from worthless.storage.schema import SCHEMA, migrate_db

    conn = sqlite3.connect(str(home.db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")

        # Run forward-only migrations BEFORE the full schema so that
        # upgraded installs whose enrollments table pre-dates the
        # decoy_hash column get it added before CREATE INDEX touches it.
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "enrollments" in tables:
            cursor = conn.execute("PRAGMA table_info(enrollments)")
            columns = {row[1] for row in cursor.fetchall()}
            if "decoy_hash" not in columns:
                try:
                    conn.execute("ALTER TABLE enrollments ADD COLUMN decoy_hash TEXT")
                    conn.commit()
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise

        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
    finally:
        conn.close()

    # Run async migrations (WOR-183: rules engine columns, spend_log cleanup)
    try:
        asyncio.get_running_loop()
        # Already in async context — schedule as a task won't work from sync.
        # Use a new thread's event loop instead.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(asyncio.run, migrate_db(str(home.db_path))).result()
    except RuntimeError:
        # No running loop — safe to use asyncio.run()
        asyncio.run(migrate_db(str(home.db_path)))

    # Restrict DB file permissions (no-op on Windows — NTFS ACLs are different)
    if not IS_WINDOWS:
        home.db_path.chmod(0o600)


@contextmanager
def acquire_lock(home: WorthlessHome) -> Generator[None, None, None]:
    """Acquire an exclusive lock file using O_CREAT|O_EXCL."""
    check_stale_lock(home)
    try:
        fd = os.open(
            str(home.lock_file),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(fd)
    except FileExistsError:
        raise WorthlessError(
            ErrorCode.LOCK_IN_PROGRESS,
            "Another worthless operation is in progress. "
            "Remove ~/.worthless/.lock-in-progress if stale.",
        ) from None  # FileExistsError context is not useful to callers
    try:
        yield
    finally:
        try:
            home.lock_file.unlink()
        except FileNotFoundError:
            pass


def get_home() -> WorthlessHome:
    """Resolve WorthlessHome from WORTHLESS_HOME env var or default."""
    env_home = os.environ.get("WORTHLESS_HOME")
    if env_home:
        return ensure_home(Path(env_home))
    return ensure_home()


def resolve_home() -> WorthlessHome | None:
    """Try to load WorthlessHome; return None if not initialized."""
    try:
        env_home = os.environ.get("WORTHLESS_HOME")
        if env_home:
            base = Path(env_home)
            if base.exists():
                return ensure_home(base)
            return None
        default = Path.home() / ".worthless"
        if default.exists():
            return ensure_home(default)
        return None
    except Exception:
        return None


def check_stale_lock(home: WorthlessHome) -> None:
    """Remove stale lock files (> 5 min old), raise on fresh locks."""
    if not home.lock_file.exists():
        return
    age = time.time() - home.lock_file.stat().st_mtime
    if age > _STALE_LOCK_SECONDS:
        home.lock_file.unlink(missing_ok=True)
    else:
        raise WorthlessError(
            ErrorCode.LOCK_IN_PROGRESS,
            f"Lock file is {int(age)}s old (< {_STALE_LOCK_SECONDS}s). "
            "Another operation may be running.",
        )
