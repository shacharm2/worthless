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
        assert result == "env-key-value"

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
        assert result == "keyring-key"

    def test_returns_empty_when_nothing_found(self) -> None:
        with patch(
            "worthless.proxy.config.read_fernet_key",
            side_effect=WorthlessError(ErrorCode.KEY_NOT_FOUND, "no key"),
        ):
            result = _read_fernet_key()
        assert result == ""


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
        assert s.fernet_key == "keyring-settings-key"

    def test_settings_validate_passes_with_keyring(self) -> None:
        with patch(
            "worthless.proxy.config.read_fernet_key",
            return_value=bytearray(b"valid-key"),
        ):
            s = ProxySettings()
            s.validate()  # should not raise

    def test_settings_validate_fails_when_no_key(self) -> None:
        with patch(
            "worthless.proxy.config.read_fernet_key",
            side_effect=WorthlessError(ErrorCode.KEY_NOT_FOUND, "no key"),
        ):
            s = ProxySettings()
        with pytest.raises(ValueError, match="Fernet key not available"):
            s.validate()


# ===========================================================================
# build_proxy_env (cli/process.py)
# ===========================================================================


class TestBuildProxyEnvKeyring:
    def test_keyring_available_omits_fernet_key(self) -> None:
        home = FakeHome()
        with patch("worthless.cli.process._keyring_available", return_value=True):
            env = build_proxy_env(home)
        assert "WORTHLESS_FERNET_KEY" not in env
        assert env["WORTHLESS_DB_PATH"] == str(home.db_path)
        assert env["WORTHLESS_SHARD_A_DIR"] == str(home.shard_a_dir)
        assert env["WORTHLESS_ALLOW_ALIAS_INFERENCE"] == "true"

    def test_keyring_unavailable_includes_fernet_key(self) -> None:
        home = FakeHome()
        with patch("worthless.cli.process._keyring_available", return_value=False):
            env = build_proxy_env(home)
        assert env["WORTHLESS_FERNET_KEY"] == "test-fernet-key-value"

    def test_keyring_available_still_has_all_other_keys(self) -> None:
        home = FakeHome()
        with patch("worthless.cli.process._keyring_available", return_value=True):
            env = build_proxy_env(home)
        expected = {"WORTHLESS_DB_PATH", "WORTHLESS_SHARD_A_DIR", "WORTHLESS_ALLOW_ALIAS_INFERENCE"}
        assert expected.issubset(set(env.keys()))
        assert "WORTHLESS_FERNET_KEY" not in env


# ===========================================================================
# fernet_transport (cli/process.py)
# ===========================================================================


class TestFernetTransportKeyring:
    def test_keyring_available_yields_none_tuple(self) -> None:
        env = {"WORTHLESS_FERNET_KEY": "some-key", "WORTHLESS_DB_PATH": "/tmp/db"}  # noqa: S108
        with patch("worthless.cli.process._keyring_available", return_value=True):
            with fernet_transport(env) as (fernet_key, fernet_fd, fernet_fds):
                assert fernet_key is None
                assert fernet_fd is None
                assert fernet_fds == []

    def test_keyring_unavailable_unix_creates_pipe(self) -> None:
        env = {"WORTHLESS_FERNET_KEY": "pipe-me"}
        with patch("worthless.cli.process._keyring_available", return_value=False):
            with patch("worthless.cli.process.IS_WINDOWS", False):
                with fernet_transport(env) as (fernet_key, fernet_fd, fernet_fds):
                    assert fernet_key is None
                    assert fernet_fd is not None
                    assert isinstance(fernet_fd, int)
                    assert fernet_fds == [fernet_fd]
                    data = os.read(fernet_fd, 4096)
                    assert data == b"pipe-me"

    def test_keyring_unavailable_env_key_popped(self) -> None:
        env = {"WORTHLESS_FERNET_KEY": "pop-me", "OTHER": "keep"}
        with patch("worthless.cli.process._keyring_available", return_value=False):
            with patch("worthless.cli.process.IS_WINDOWS", False):
                with fernet_transport(env) as (fernet_key, fernet_fd, fernet_fds):
                    if fernet_fd is not None:
                        os.read(fernet_fd, 4096)  # drain
        assert "WORTHLESS_FERNET_KEY" not in env
        assert env["OTHER"] == "keep"

    def test_keyring_available_no_fd_created(self) -> None:
        env = {"WORTHLESS_FERNET_KEY": "no-leak"}
        with patch("worthless.cli.process._keyring_available", return_value=True):
            with fernet_transport(env) as (fernet_key, fernet_fd, fernet_fds):
                assert fernet_fd is None
                assert fernet_fds == []
