"""OS keyring-backed Fernet key storage with file fallback."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import keyring

from worthless.cli.errors import ErrorCode, WorthlessError

logger = logging.getLogger(__name__)

_SERVICE = "worthless"
_USERNAME = "fernet-key"


def _keyring_username(home_dir: Path | None = None) -> str:
    """Derive a per-install keyring username to avoid collisions.

    Two worthless installs on the same machine (e.g. staging vs prod) get
    unique keyring entries based on the resolved home directory path.
    """
    if home_dir is None:
        home_dir = Path.home() / ".worthless"
    digest = hashlib.sha256(str(home_dir.resolve()).encode()).hexdigest()[:12]
    return f"fernet-key-{digest}"


# Fully-qualified backend names that are not real credential stores.
# Matched against module.qualname (e.g. "keyring.backends.fail.Keyring").
_REJECTED_BACKENDS = frozenset(
    {
        "keyring.backends.fail.Keyring",
        "keyring.backends.null.Keyring",
        "keyrings.alt.file.PlaintextKeyring",
    }
)


def keyring_available() -> bool:
    """Return True if the OS keyring backend is a real credential store."""
    try:
        backend = keyring.get_keyring()
        fqn = f"{type(backend).__module__}.{type(backend).__qualname__}"
        if fqn in _REJECTED_BACKENDS:
            logger.debug("Keyring backend rejected: %s", fqn)
            return False
        logger.debug("Keyring backend accepted: %s", fqn)
        return True
    except Exception:
        return False


def store_fernet_key(key: bytes, home_dir: Path | None = None) -> None:
    """Store Fernet key in OS keyring, falling back to file.

    Args:
        key: Raw Fernet key bytes (from ``Fernet.generate_key()``).
        home_dir: Directory for file fallback (default ``~/.worthless``).
    """
    if keyring_available():
        try:
            keyring.set_password(_SERVICE, _keyring_username(home_dir), key.decode())
            logger.info("Fernet key stored in OS keyring")
            # Clean up stale fernet.key file left from pre-keyring installs
            try:
                stale = _fernet_file_path(home_dir)
                if stale.exists():
                    stale.unlink()
                    logger.info("Removed stale fernet.key file")
            except OSError:
                logger.warning("Could not remove stale fernet.key file; remove it manually")
            return
        except Exception:
            logger.warning("Keyring write failed, falling back to file")

    _write_key_file(key, home_dir)


def migrate_file_to_keyring(home_dir: Path | None = None) -> bool:
    """Opportunistically promote a file-based Fernet key to the OS keyring.

    Returns True if migration succeeded, False if skipped or failed.
    Never raises — all failures are swallowed to debug log.
    """
    try:
        if not keyring_available():
            logger.debug("migrate_file_to_keyring: keyring unavailable, skip")
            return False
        # File-existence check FIRST so we don't fire keyring.get_password on
        # every CLI invocation just to confirm "no migration needed". When
        # there is no fernet.key file (the common case post-migration), we
        # return immediately without touching the keyring — preserves the
        # HF2 1-keyring-read-per-CLI promise on the existing-key path.
        fernet_path = _fernet_file_path(home_dir)
        if not fernet_path.exists():
            logger.debug("migrate_file_to_keyring: no file at %s, skip", fernet_path)
            return False
        # File exists. Now check keyring — worth a get_password only when a
        # file is present and migration is actually possible.
        username = _keyring_username(home_dir)
        if keyring.get_password(_SERVICE, username) is not None:
            logger.debug("migrate_file_to_keyring: keyring already has key, skip")
            return False
        # Read from file and store to keyring (which also cleans up the file).
        # store_fernet_key deletes the file on keyring success and re-creates
        # it on fallback. If the file still exists afterward, keyring write
        # failed and the migration did not actually happen.
        key_bytes = fernet_path.read_bytes().strip()
        store_fernet_key(key_bytes, home_dir)
        if fernet_path.exists():
            logger.debug("Keyring write fell back to file; migration not successful")
            return False
        logger.info("Migrated Fernet key from file to OS keyring")
        return True
    except Exception:
        logger.debug("File-to-keyring migration skipped", exc_info=True)
        return False


def read_fernet_key_from_file(home_dir: Path | None = None) -> bytearray:
    """Read the Fernet key directly from the on-disk file fallback.

    HF3 (worthless-cmpf): companion to ``read_fernet_key`` for callers
    that know the file is the authoritative source and want to bypass
    the keyring step in the cascade. ``read_fernet_key``'s order is
    env → keyring → file, so calling it on a file-only system still
    invokes the keyring backend (silent on macOS when no entry exists,
    but still an API touch — and prompts on backends that authenticate
    the lookup itself). Use this helper from bootstrap when
    ``_fernet_key_present`` is True via file alone.

    Returns ``bytearray`` per SR-01.

    Raises ``WorthlessError(KEY_NOT_FOUND)`` if the file does not exist.
    """
    fernet_path = _fernet_file_path(home_dir)
    if not fernet_path.exists():
        raise WorthlessError(
            ErrorCode.KEY_NOT_FOUND,
            f"Fernet key file not found at {fernet_path}.",
        )
    return bytearray(fernet_path.read_bytes().strip())


def _fernet_file_path(home_dir: Path | None) -> Path:
    """Resolve fernet key file path, respecting WORTHLESS_FERNET_KEY_PATH."""
    env_path = os.environ.get("WORTHLESS_FERNET_KEY_PATH")
    if env_path:
        return Path(env_path)
    if home_dir is None:
        home_dir = Path.home() / ".worthless"
    return home_dir / "fernet.key"


def _write_key_file(key: bytes, home_dir: Path | None) -> None:
    """Write key to file with 0o600 permissions."""
    fernet_path = _fernet_file_path(home_dir)
    fd = os.open(str(fernet_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    logger.info("Fernet key stored in file")


def read_fernet_key(home_dir: Path | None = None) -> bytearray:
    """Read Fernet key from storage backends.

    Order: env var -> keyring -> file -> error.
    Returns bytearray per SR-01 (mutable, can be zeroed).

    Note: pipe fd transport (WORTHLESS_FERNET_FD) is handled by
    proxy/config.py, not here. Fd is a transport mechanism, not storage.
    """
    # 1. Environment variable
    env_val = os.environ.get("WORTHLESS_FERNET_KEY")
    if env_val:
        return bytearray(env_val.encode())

    # 2. Keyring (namespaced username only — no legacy fallback)
    if keyring_available():
        try:
            value = keyring.get_password(_SERVICE, _keyring_username(home_dir))
            if value is not None:
                return bytearray(value.encode())
        except Exception:
            logger.debug("Keyring read failed, falling back to file")

    # 3. File
    fernet_path = _fernet_file_path(home_dir)
    if fernet_path.exists():
        return bytearray(fernet_path.read_bytes().strip())

    # 4. Error
    raise WorthlessError(
        ErrorCode.KEY_NOT_FOUND,
        "No Fernet key found. Run 'worthless enroll' or set WORTHLESS_FERNET_KEY.",
    )


def delete_fernet_key(home_dir: Path | None = None) -> None:
    """Remove Fernet key from keyring and file. Never raises on missing."""
    if keyring_available():
        try:
            keyring.delete_password(_SERVICE, _keyring_username(home_dir))
        except Exception:
            logger.debug("Keyring delete failed (may not exist)")

    fernet_path = _fernet_file_path(home_dir)
    fernet_path.unlink(missing_ok=True)
