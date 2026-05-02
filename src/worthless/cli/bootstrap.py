"""First-run ~/.worthless/ initialization and lock management."""

from __future__ import annotations

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
from worthless.cli.keystore import (
    migrate_file_to_keyring,
    read_fernet_key,
    read_fernet_key_from_file,
    store_fernet_key,
)
from worthless.cli.platform import IS_WINDOWS

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
    def bootstrapped_marker(self) -> Path:
        """Marker file written at the end of a successful ensure_home().

        HF3 (worthless-cmpf): used to distinguish "first-run /
        previously-failed bootstrap" (probe must run, key must be
        generated) from "bootstrap completed at least once" (probe
        gated). Stronger than ``base_dir.exists()`` because a failed
        prior run leaves the dir present but the keystore empty; the
        marker is only created at the END of a successful ensure_home,
        so its presence is a positive signal of completed bootstrap.
        """
        return self.base_dir / ".bootstrapped"

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
                    self._cached_fernet_key = read_fernet_key(self.base_dir)
        return bytearray(self._cached_fernet_key)


def _fernet_key_present(home: WorthlessHome) -> bool:
    """True if a Fernet key is provisioned WITHOUT touching the keyring.

    HF3 (worthless-cmpf): cheap probe that lets read-only commands
    (``worthless scan`` in particular) bypass the keystore entirely.
    Sources, in priority order:

    1. ``WORTHLESS_FERNET_KEY`` env var — used by IPC fd transport
       and CI environments.
    2. The on-disk fernet key file — pre-keyring fallback that still
       exists on legacy installs.

    The keyring is intentionally NOT consulted here — its access on
    macOS triggers a per-call keychain prompt, which is the very UX
    bug HF3 is closing. For users with only a keyring entry (no env
    var, no file), this returns False and the caller falls through
    to the keyring probe — that's correct: first-run detection has
    to happen somewhere.
    """
    if os.environ.get("WORTHLESS_FERNET_KEY"):
        return True
    if home.fernet_key_path.exists():
        return True
    return False


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

        # HF3 (worthless-cmpf): the keystore-touching logic is gated on
        # a marker file written at the END of a successful ensure_home.
        # Marker absent → first-run OR a previous bootstrap that crashed
        # mid-way; we MUST run the full probe-and-generate flow so a
        # clean macOS box (or a partially-bootstrapped one) ends up with
        # a usable Fernet key. Marker present → bootstrap completed at
        # least once; we only do work the user explicitly opted into:
        #
        #   • env var set → call ``home.fernet_key`` so HF2's per-
        #     instance cache populates from the env value (the cascade
        #     short-circuits at step 1, no keyring touch).
        #   • file fallback only → read the file directly via
        #     ``read_fernet_key_from_file`` and pre-populate the cache,
        #     bypassing ``read_fernet_key``'s keyring step entirely.
        #     Migration is intentionally NOT run on this path: it would
        #     re-introduce a keyring API touch on every CLI invocation,
        #     defeating the read-only-no-keychain contract for legacy
        #     file installs. Migration still happens on first-run via
        #     the branch above; existing file installs migrate the next
        #     time they're set up cleanly (or a future ``worthless
        #     doctor --migrate`` command).
        #   • neither (keyring-only) → skip everything; key-using
        #     commands fetch lazily via ``home.fernet_key`` (HF2
        #     amortizes the prompt within a CLI invocation), read-only
        #     commands never touch the keyring.
        if not home.bootstrapped_marker.exists():
            try:
                _ = home.fernet_key
            except WorthlessError as exc:
                if exc.code != ErrorCode.KEY_NOT_FOUND:
                    raise
                key = Fernet.generate_key()
                store_fernet_key(key, home_dir=home.base_dir)
            else:
                migrate_file_to_keyring(home.base_dir)
        elif os.environ.get("WORTHLESS_FERNET_KEY"):
            _ = home.fernet_key
        elif home.fernet_key_path.exists():
            home._cached_fernet_key = read_fernet_key_from_file(home.base_dir)
        # else: keyring-only post-bootstrap → skip; lazy fetch later.

        # Mark bootstrap complete. Writing the marker at the END means
        # crashed runs leave it absent → next invocation re-runs the
        # full probe-and-generate flow (FINDING 1: stronger than
        # ``base_dir.exists()`` because mkdir success doesn't imply key
        # was provisioned). 0o600 matches the rest of the home tree.
        home.bootstrapped_marker.touch(mode=0o600, exist_ok=True)
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
