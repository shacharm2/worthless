"""Proxy configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from worthless.cli.keystore import read_fernet_key

#: Capabilities the proxy expects from the sidecar HELLO frame (WOR-309).
#: Caps shrinking across reconnects is fatal — see C3 in
#: ``.research/10-security-signoff.md``.
DEFAULT_SIDECAR_CAPS: frozenset[str] = frozenset({"open", "seal", "attest"})

#: IPC protocol version (msgpack envelope schema). Bump on breaking changes.
DEFAULT_SIDECAR_PROTOCOL_VERSION: int = 1

#: Default Unix Domain Socket path. Single-container deployment puts the
#: socket on a tmpfs volume shared by the proxy and sidecar uids.
DEFAULT_SIDECAR_SOCKET_PATH: str = "/run/worthless/sidecar.sock"


def _default_db_path() -> str:
    return str(Path.home() / ".worthless" / "worthless.db")


def _env_bool(name: str) -> bool:
    """Return ``True`` when the environment variable *name* is a truthy string."""
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _read_fernet_key() -> bytearray:
    """Read Fernet key: fd (secure pipe) -> keystore (env/keyring/file).

    Fd is checked first because it's the secure pipe transport from the
    parent CLI — env vars leak via /proc on Linux. The keystore cascade
    handles persistent storage backends (env override, keyring, file).

    Returns bytearray per SR-01 (mutable, can be zeroed).
    Returns empty bytearray if no key found — ProxySettings.validate()
    catches that as a startup error.
    """
    # 1. Inherited fd — secure pipe from parent CLI, always preferred
    fd_str = os.environ.get("WORTHLESS_FERNET_FD")
    if fd_str:
        try:
            fd = int(fd_str)
        except ValueError:
            pass
        else:
            try:
                raw = os.read(fd, 4096)
                return bytearray(raw.strip())
            except OSError:
                pass
            finally:
                os.close(fd)

    # 2. Keystore cascade (env -> keyring -> file)
    # Respect WORTHLESS_HOME so the keyring username hash matches the
    # home_dir used at enrollment time (worthless-2fd namespacing).
    try:
        home_env = os.environ.get("WORTHLESS_HOME")
        home_dir = Path(home_env) if home_env else None
        return read_fernet_key(home_dir)
    except Exception:
        return bytearray()


@dataclass
class ProxySettings:
    """Proxy configuration loaded from environment variables."""

    db_path: str = field(
        default_factory=lambda: os.environ.get("WORTHLESS_DB_PATH", _default_db_path())
    )
    fernet_key: bytearray = field(default_factory=lambda: _read_fernet_key())
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
    sidecar_socket_path: str = field(
        default_factory=lambda: os.environ.get(
            "WORTHLESS_SIDECAR_SOCKET", DEFAULT_SIDECAR_SOCKET_PATH
        )
    )
    sidecar_protocol_version: int = field(
        default_factory=lambda: int(
            os.environ.get(
                "WORTHLESS_SIDECAR_PROTOCOL_VERSION", str(DEFAULT_SIDECAR_PROTOCOL_VERSION)
            )
        )
    )
    sidecar_expected_caps: frozenset[str] = field(default_factory=lambda: DEFAULT_SIDECAR_CAPS)
    sidecar_max_concurrency: int = field(
        default_factory=lambda: int(os.environ.get("WORTHLESS_SIDECAR_MAX_CONCURRENCY", "32"))
    )
    sidecar_request_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("WORTHLESS_SIDECAR_REQUEST_TIMEOUT", "2.0"))
    )

    def validate(self) -> None:
        """Raise if required settings are missing.

        WOR-309: ``fernet_key`` is no longer required at proxy boot. The
        sidecar holds the key; the proxy only reads ciphertext-at-rest and
        delegates ``open()`` over IPC. Existing setups still load the key
        for backwards-compat (e.g. CLI flows reused by the proxy container)
        but the proxy itself never decrypts.
        """
