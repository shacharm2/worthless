"""First-run ~/.worthless/ initialization and lock management."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Generator

from cryptography.fernet import Fernet

from worthless.cli.errors import ErrorCode, WorthlessError, sanitize_exception
from worthless.cli.platform import IS_WINDOWS

_DEFAULT_BASE = Path.home() / ".worthless"
_STALE_LOCK_SECONDS = 300  # 5 minutes


@dataclass
class WorthlessHome:
    """Paths within the ``~/.worthless/`` directory tree."""

    base_dir: Path = field(default_factory=lambda: _DEFAULT_BASE)

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
    def fernet_key(self) -> bytes:
        """Read the Fernet key from disk."""
        return self.fernet_key_path.read_bytes().strip()


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

        # Generate Fernet key if missing
        if not fernet_path.exists():
            key = Fernet.generate_key()
            fd = os.open(
                str(fernet_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                os.write(fd, key)
                os.write(fd, b"\n")
            finally:
                os.close(fd)
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
    from worthless.storage.schema import SCHEMA

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
