"""OS keyring-backed Fernet key storage with file fallback."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from worthless.cli.errors import ErrorCode, WorthlessError

logger = logging.getLogger(__name__)

_SERVICE = "worthless"
_USERNAME = "fernet-key"

# Backend class names that are not real credential stores.
_REJECTED_BACKENDS = frozenset(
    {
        "fail.Keyring",
        "null.Keyring",
        "PlaintextKeyring",
    }
)

try:
    import keyring
except ImportError:  # pragma: no cover
    keyring = None  # type: ignore[assignment]


def _keyring_available() -> bool:
    """Return True if the OS keyring backend is a real credential store."""
    if keyring is None:
        return False
    try:
        backend = keyring.get_keyring()
        name = type(backend).__name__
        if name in _REJECTED_BACKENDS:
            logger.debug("Keyring backend rejected: %s", name)
            return False
        logger.debug("Keyring backend accepted: %s", name)
        return True
    except Exception:
        return False


def store_fernet_key(key: bytes, home_dir: Path | None = None) -> None:
    """Store Fernet key in OS keyring, falling back to file.

    Args:
        key: Raw Fernet key bytes (from ``Fernet.generate_key()``).
        home_dir: Directory for file fallback (default ``~/.worthless``).
    """
    if _keyring_available():
        try:
            keyring.set_password(_SERVICE, _USERNAME, key.decode())
            logger.info("Fernet key stored in OS keyring")
            return
        except Exception:
            logger.debug("Keyring write failed, falling back to file")

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
    """Read Fernet key using detection cascade.

    Order: env var -> fd -> keyring -> file -> error.
    Returns bytearray per SR-01 (mutable, can be zeroed).
    """
    # 1. Environment variable
    env_val = os.environ.get("WORTHLESS_FERNET_KEY")
    if env_val:
        return bytearray(env_val.encode())

    # 2. File descriptor
    fd_str = os.environ.get("WORTHLESS_FERNET_FD")
    if fd_str:
        try:
            fd = int(fd_str)
            data = os.read(fd, 4096)
            os.close(fd)
            return bytearray(data.strip())
        except (ValueError, OSError):
            pass

    # 3. Keyring
    if _keyring_available():
        try:
            value = keyring.get_password(_SERVICE, _USERNAME)
            if value is not None:
                return bytearray(value.encode())
        except Exception:
            logger.debug("Keyring read failed, falling back to file")

    # 4. File
    fernet_path = _fernet_file_path(home_dir)
    if fernet_path.exists():
        return bytearray(fernet_path.read_bytes().strip())

    # 5. Error
    raise WorthlessError(
        ErrorCode.KEY_NOT_FOUND,
        "No Fernet key found. Run 'worthless enroll' or set WORTHLESS_FERNET_KEY.",
    )


def delete_fernet_key(home_dir: Path | None = None) -> None:
    """Remove Fernet key from keyring and file. Never raises on missing."""
    if _keyring_available():
        try:
            keyring.delete_password(_SERVICE, _USERNAME)
        except Exception:
            logger.debug("Keyring delete failed (may not exist)")

    fernet_path = _fernet_file_path(home_dir)
    fernet_path.unlink(missing_ok=True)
