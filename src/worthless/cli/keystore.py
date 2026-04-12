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

    # 2. Keyring (namespaced username first, then legacy fallback)
    if keyring_available():
        try:
            value = keyring.get_password(_SERVICE, _keyring_username(home_dir))
            if value is not None:
                return bytearray(value.encode())
            # Legacy fallback for pre-namespacing installs
            value = keyring.get_password(_SERVICE, _USERNAME)
            if value is not None:
                logger.info("Found Fernet key under legacy keyring entry; re-enroll to migrate")
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
        for username in (_keyring_username(home_dir), _USERNAME):
            try:
                keyring.delete_password(_SERVICE, username)
            except Exception:
                logger.debug("Keyring delete failed for %s (may not exist)", username)

    fernet_path = _fernet_file_path(home_dir)
    fernet_path.unlink(missing_ok=True)
