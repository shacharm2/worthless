"""Tests for keyring integration in proxy config and CLI process transport.

WOR-188: Proxy config delegates to keystore.read_fernet_key() for the
full cascade. CLI process skips pipe transport when keyring is available.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.cli.process import build_proxy_env, fernet_transport
from worthless.proxy.config import ProxySettings, _read_fernet_key


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all WORTHLESS_* env vars so each test starts clean."""
    for key in list(os.environ):
        if key.startswith("WORTHLESS_"):
            monkeypatch.delenv(key, raising=False)


@dataclass
class FakeHome:
    """Minimal stand-in for WorthlessHome used by build_proxy_env."""

    base_dir: Path = field(default_factory=lambda: Path("/fake"))
    db_path: Path = field(default_factory=lambda: Path("/fake/worthless.db"))
    fernet_key: bytes = b"test-fernet-key-value"
    shard_a_dir: Path = field(default_factory=lambda: Path("/fake/shard_a"))


# ===========================================================================
# _read_fernet_key (proxy/config.py) — delegates to keystore
# ===========================================================================


class TestReadFernetKeyCascade:
    """_read_fernet_key delegates to keystore.read_fernet_key."""

    def test_returns_key_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WORTHLESS_FERNET_KEY", "env-key-value")
        result = _read_fernet_key()
        assert result == bytearray(b"env-key-value")

    def test_returns_key_from_keyring(self) -> None:
        with patch(
            "worthless.cli.keystore.read_fernet_key",
            return_value=bytearray(b"keyring-key"),
        ):
            # Patch at the call site in config.py
            with patch(
                "worthless.proxy.config.read_fernet_key",
                return_value=bytearray(b"keyring-key"),
            ):
                result = _read_fernet_key()
        assert result == bytearray(b"keyring-key")

    def test_returns_empty_when_nothing_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Defense in depth against CI py3.10 flake: CLI subprocess tests
        # earlier in the same xdist loadscope group can call ``ensure_home()``
        # without an explicit base_dir, leaving a real ``~/.worthless/fernet.key``
        # on the runner. Pin HOME so the keystore file fallback resolves to an
        # empty dir, and patch at both call sites so the cascade can't see it.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("WORTHLESS_FERNET_FD", raising=False)
        with (
            patch(
                "worthless.proxy.config.read_fernet_key",
                side_effect=WorthlessError(ErrorCode.KEY_NOT_FOUND, "no key"),
            ),
            patch(
                "worthless.cli.keystore.read_fernet_key",
                side_effect=WorthlessError(ErrorCode.KEY_NOT_FOUND, "no key"),
            ),
        ):
            result = _read_fernet_key()
        assert result == bytearray()


# ===========================================================================
# ProxySettings integration
# ===========================================================================


class TestProxySettingsKeyring:
    def test_settings_loads_from_keyring(self) -> None:
        with patch(
            "worthless.proxy.config.read_fernet_key",
            return_value=bytearray(b"keyring-settings-key"),
        ):
            s = ProxySettings()
        assert s.fernet_key == bytearray(b"keyring-settings-key")

    def test_settings_validate_passes_with_keyring(self) -> None:
        with patch(
            "worthless.proxy.config.read_fernet_key",
            return_value=bytearray(b"valid-key"),
        ):
            s = ProxySettings()
            s.validate()  # should not raise

    def test_settings_validate_does_not_fail_when_no_key(self) -> None:
        """WOR-309: missing Fernet key no longer fails ``validate()``.

        The proxy delegates decrypt to the sidecar, so the key is optional
        at proxy boot. Pre-WOR-309 this raised ``ValueError``; post-WOR-309
        the absence of a raise is the regression guard.
        """
        with patch(
            "worthless.proxy.config.read_fernet_key",
            side_effect=WorthlessError(ErrorCode.KEY_NOT_FOUND, "no key"),
        ):
            s = ProxySettings()
        s.validate()  # MUST NOT raise — the proxy never decrypts


# ===========================================================================
# build_proxy_env (cli/process.py)
# ===========================================================================


class TestBuildProxyEnvKeyring:
    def testkeyring_available_omits_fernet_key(self) -> None:
        home = FakeHome()
        with patch("worthless.cli.process.keyring_available", return_value=True):
            env = build_proxy_env(home)
        assert "WORTHLESS_FERNET_KEY" not in env
        assert env["WORTHLESS_DB_PATH"] == str(home.db_path)
        assert "WORTHLESS_SHARD_A_DIR" not in env
        assert "WORTHLESS_ALLOW_ALIAS_INFERENCE" not in env

    def test_keyring_unavailable_includes_fernet_key(self) -> None:
        home = FakeHome()
        with patch("worthless.cli.process.keyring_available", return_value=False):
            env = build_proxy_env(home)
        assert env["WORTHLESS_FERNET_KEY"] == "test-fernet-key-value"

    def testkeyring_available_still_has_all_other_keys(self) -> None:
        home = FakeHome()
        with patch("worthless.cli.process.keyring_available", return_value=True):
            env = build_proxy_env(home)
        assert "WORTHLESS_DB_PATH" in env
        assert "WORTHLESS_FERNET_KEY" not in env
        assert "WORTHLESS_SHARD_A_DIR" not in env
        assert "WORTHLESS_ALLOW_ALIAS_INFERENCE" not in env


# ===========================================================================
# fernet_transport (cli/process.py)
# ===========================================================================


class TestFernetTransportKeyring:
    def testkeyring_available_yields_none_tuple(self) -> None:
        env = {"WORTHLESS_FERNET_KEY": "some-key", "WORTHLESS_DB_PATH": "/tmp/db"}  # noqa: S108
        with patch("worthless.cli.process.keyring_available", return_value=True):
            with fernet_transport(env) as (fernet_key, fernet_fd, fernet_fds):
                assert fernet_key is None
                assert fernet_fd is None
                assert fernet_fds == []

    def test_keyring_unavailable_unix_creates_pipe(self) -> None:
        env = {"WORTHLESS_FERNET_KEY": "pipe-me"}
        with patch("worthless.cli.process.keyring_available", return_value=False):
            with patch("worthless.cli.process.IS_WINDOWS", False):
                with fernet_transport(env) as (fernet_key, fernet_fd, fernet_fds):
                    assert fernet_key is None
                    assert fernet_fd is not None
                    assert isinstance(fernet_fd, int)
                    assert fernet_fds == [fernet_fd]
                    data = os.read(fernet_fd, 4096)
                    os.close(fernet_fd)
                    assert data == b"pipe-me"

    def test_keyring_unavailable_env_key_popped(self) -> None:
        env = {"WORTHLESS_FERNET_KEY": "pop-me", "OTHER": "keep"}
        with patch("worthless.cli.process.keyring_available", return_value=False):
            with patch("worthless.cli.process.IS_WINDOWS", False):
                with fernet_transport(env) as (fernet_key, fernet_fd, fernet_fds):
                    if fernet_fd is not None:
                        os.read(fernet_fd, 4096)  # drain
        assert "WORTHLESS_FERNET_KEY" not in env
        assert env["OTHER"] == "keep"

    def testkeyring_available_no_fd_created(self) -> None:
        env = {"WORTHLESS_FERNET_KEY": "no-leak"}
        with patch("worthless.cli.process.keyring_available", return_value=True):
            with fernet_transport(env) as (fernet_key, fernet_fd, fernet_fds):
                assert fernet_fd is None
                assert fernet_fds == []
