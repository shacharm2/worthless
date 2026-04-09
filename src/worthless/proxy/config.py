"""Proxy configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_db_path() -> str:
    return str(Path.home() / ".worthless" / "worthless.db")


def _default_shard_a_dir() -> str:
    return str(Path.home() / ".worthless" / "shard_a")


def _env_bool(name: str) -> bool:
    """Return ``True`` when the environment variable *name* is a truthy string."""
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _read_fernet_key() -> str:
    """Read Fernet key from inherited fd (preferred) or env var (fallback)."""
    fd_str = os.environ.get("WORTHLESS_FERNET_FD")
    if fd_str:
        try:
            fd = int(fd_str)
            key = os.read(fd, 4096).decode().strip()
            os.close(fd)
            return key
        except (ValueError, OSError):
            pass
    return os.environ.get("WORTHLESS_FERNET_KEY", "")


@dataclass
class ProxySettings:
    """Proxy configuration loaded from environment variables."""

    db_path: str = field(
        default_factory=lambda: os.environ.get("WORTHLESS_DB_PATH", _default_db_path())
    )
    fernet_key: str = field(default_factory=lambda: _read_fernet_key())
    default_rate_limit_rps: float = field(
        default_factory=lambda: float(os.environ.get("WORTHLESS_RATE_LIMIT_RPS", "100.0"))
    )
    upstream_timeout: float = field(
        default_factory=lambda: float(os.environ.get("WORTHLESS_UPSTREAM_TIMEOUT", "120.0"))
    )
    streaming_timeout: float = field(
        default_factory=lambda: float(os.environ.get("WORTHLESS_STREAMING_TIMEOUT", "300.0"))
    )
    allow_insecure: bool = field(default_factory=lambda: _env_bool("WORTHLESS_ALLOW_INSECURE"))
    shard_a_dir: str = field(
        default_factory=lambda: os.environ.get("WORTHLESS_SHARD_A_DIR", _default_shard_a_dir())
    )
    allow_alias_inference: bool = field(
        default_factory=lambda: _env_bool("WORTHLESS_ALLOW_ALIAS_INFERENCE")
    )
    max_request_bytes: int = field(
        default_factory=lambda: int(
            os.environ.get("WORTHLESS_MAX_REQUEST_BYTES", str(10 * 1024 * 1024))
        )
    )

    def validate(self) -> None:
        """Raise if required settings are missing."""
        if not self.fernet_key:
            raise ValueError(
                "Fernet key not available. "
                "Set WORTHLESS_FERNET_KEY or WORTHLESS_FERNET_KEY_PATH, "
                "or check that entrypoint.sh ran successfully in Docker."
            )
