"""Proxy configuration from environment variables.

WOR-hlls: ``deploy_mode`` is the explicit trust-boundary contract.
Each mode pins the host bind, the X-Forwarded-Proto trust source, and
whether ``WORTHLESS_ALLOW_INSECURE`` is even legal. ``ProxySettings.validate``
refuses startup on any unsafe combination — operators can no longer silently
cross trust boundaries by flipping a single env var.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from worthless.cli.keystore import read_fernet_key


_PAAS_ENV_VARS: tuple[str, ...] = ("RENDER", "FLY_APP_NAME", "KUBERNETES_SERVICE_HOST")
"""Env vars that signal a public-PaaS runtime; require explicit deploy_mode."""

_PRIVATE_CIDRS: tuple[str, ...] = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")


class DeployMode(str, Enum):
    """Where worthless-proxy is running. Each mode pins one trust boundary.

    LOOPBACK
        Laptop / single-machine. Bind 127.0.0.1 only.
    LAN
        Docker / private network. Bind a private CIDR; trusted_proxies optional.
    PUBLIC
        PaaS (Render/Fly/etc) behind an edge. Bind 0.0.0.0; trusted_proxies REQUIRED.
        ``WORTHLESS_ALLOW_INSECURE`` is FORBIDDEN — operators must list the edge CIDR.
    """

    LOOPBACK = "loopback"
    LAN = "lan"
    PUBLIC = "public"


class ConfigError(ValueError):
    """Raised when ProxySettings.validate() finds an unsafe combination.

    Carries an actionable hint pointing at the env var(s) the operator
    must fix. Distinct from ``ValueError`` so callers can differentiate
    config errors from generic validation failures.
    """


def _default_db_path() -> str:
    return str(Path.home() / ".worthless" / "worthless.db")


def _env_bool(name: str) -> bool:
    """Return ``True`` when the environment variable *name* is a truthy string."""
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _read_deploy_mode() -> DeployMode:
    raw = os.environ.get("WORTHLESS_DEPLOY_MODE", DeployMode.LOOPBACK.value).strip().lower()
    try:
        return DeployMode(raw)
    except ValueError as exc:
        valid = ", ".join(m.value for m in DeployMode)
        raise ConfigError(
            f"WORTHLESS_DEPLOY_MODE={raw!r} is not valid. Choose one of: {valid}."
        ) from exc


def _read_trusted_proxies() -> tuple[str, ...]:
    raw = os.environ.get("WORTHLESS_TRUSTED_PROXIES", "").strip()
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _read_default_host(mode: DeployMode) -> str:
    """Pick the bind host: explicit env wins, otherwise a mode-safe default."""
    explicit = os.environ.get("WORTHLESS_HOST", "").strip()
    if explicit:
        return explicit
    if mode is DeployMode.PUBLIC:
        return "0.0.0.0"  # noqa: S104 — public mode binds the edge-facing iface intentionally
    return "127.0.0.1"


def _read_fernet_key() -> bytearray:
    """Read Fernet key: fd (secure pipe) -> keystore (env/keyring/file)."""
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

    try:
        home_env = os.environ.get("WORTHLESS_HOME")
        home_dir = Path(home_env) if home_env else None
        return read_fernet_key(home_dir)
    except Exception:
        return bytearray()


def _detected_paas_vars() -> list[str]:
    return [name for name in _PAAS_ENV_VARS if os.environ.get(name)]


def _is_private_ipv4_or_v6(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(cidr) for cidr in _PRIVATE_CIDRS)


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
    deploy_mode: DeployMode = field(default_factory=_read_deploy_mode)
    host: str = field(default="")
    trusted_proxies: tuple[str, ...] = field(default_factory=_read_trusted_proxies)

    def __post_init__(self) -> None:
        if not self.host:
            self.host = _read_default_host(self.deploy_mode)

    def validate(self) -> None:
        """Refuse startup on missing keys or unsafe deploy-mode combinations."""
        if len(self.fernet_key) == 0:
            raise ConfigError(
                "Fernet key not available. "
                "Set WORTHLESS_FERNET_KEY or check OS keyring, "
                "or verify entrypoint.sh ran successfully in Docker."
            )
        self._validate_deploy_mode()

    def _validate_deploy_mode(self) -> None:
        paas = _detected_paas_vars()
        if (
            paas
            and self.deploy_mode is DeployMode.LOOPBACK
            and "WORTHLESS_DEPLOY_MODE" not in os.environ
        ):
            raise ConfigError(
                f"Detected PaaS env var(s) {paas!r} but WORTHLESS_DEPLOY_MODE is unset. "
                "Refusing silent loopback default. Set WORTHLESS_DEPLOY_MODE=public "
                "(with WORTHLESS_TRUSTED_PROXIES) or =loopback explicitly."
            )

        if self.deploy_mode is DeployMode.LOOPBACK:
            if self.host != "127.0.0.1":
                raise ConfigError(
                    f"deploy_mode=loopback requires host=127.0.0.1, got {self.host!r}. "
                    "Set WORTHLESS_DEPLOY_MODE=lan or =public to bind anything else."
                )
            return

        if self.deploy_mode is DeployMode.LAN:
            if self.host not in ("127.0.0.1", "0.0.0.0") and not _is_private_ipv4_or_v6(  # noqa: S104
                self.host
            ):
                raise ConfigError(
                    f"deploy_mode=lan requires host in a private CIDR, got {self.host!r}. "
                    f"Allowed: 127.0.0.1, 0.0.0.0, or any address in {list(_PRIVATE_CIDRS)!r}."
                )
            return

        # PUBLIC
        if self.allow_insecure:
            raise ConfigError(
                "WORTHLESS_ALLOW_INSECURE is FORBIDDEN when deploy_mode=public. "
                "Set WORTHLESS_TRUSTED_PROXIES=<edge-CIDR> instead — the proxy then trusts "
                "X-Forwarded-Proto only from those peers."
            )
        if not self.trusted_proxies:
            raise ConfigError(
                "deploy_mode=public requires WORTHLESS_TRUSTED_PROXIES (CIDR list of the "
                "edge layer, e.g. Render/Fly internal CIDR). "
                "Refusing to trust X-Forwarded-Proto from arbitrary peers."
            )
