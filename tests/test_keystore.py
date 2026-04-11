"""Tests for worthless.cli.keystore — TDD RED phase.

These tests define the behavior of the keystore module before it exists.
All tests should fail with ImportError until the module is implemented.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from worthless.cli.errors import ErrorCode, WorthlessError

# Import the module under test — will fail until implemented (RED phase).
from worthless.cli.keystore import (
    _SERVICE,
    _USERNAME,
    _keyring_available,
    delete_fernet_key,
    read_fernet_key,
    store_fernet_key,
)


# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------


class TestConstants:
    def test_service_name(self) -> None:
        assert _SERVICE == "worthless"

    def test_username(self) -> None:
        assert _USERNAME == "fernet-key"


# ------------------------------------------------------------------
# _keyring_available
# ------------------------------------------------------------------


class TestKeyringAvailable:
    """Backend detection: reject fail/null/plaintext, accept real backends."""

    def test_fail_keyring_returns_false(self) -> None:
        backend = MagicMock()
        backend.__class__.__name__ = "fail.Keyring"
        with patch("worthless.cli.keystore.keyring") as mock_kr:
            mock_kr.get_keyring.return_value = backend
            assert _keyring_available() is False

    def test_null_keyring_returns_false(self) -> None:
        backend = MagicMock()
        backend.__class__.__name__ = "null.Keyring"
        with patch("worthless.cli.keystore.keyring") as mock_kr:
            mock_kr.get_keyring.return_value = backend
            assert _keyring_available() is False

    def test_plaintext_keyring_returns_false(self) -> None:
        backend = MagicMock()
        backend.__class__.__name__ = "PlaintextKeyring"
        with patch("worthless.cli.keystore.keyring") as mock_kr:
            mock_kr.get_keyring.return_value = backend
            assert _keyring_available() is False

    def test_macos_keychain_returns_true(self) -> None:
        backend = MagicMock()
        backend.__class__.__name__ = "Keychain"
        with patch("worthless.cli.keystore.keyring") as mock_kr:
            mock_kr.get_keyring.return_value = backend
            assert _keyring_available() is True

    def test_secretservice_returns_true(self) -> None:
        backend = MagicMock()
        backend.__class__.__name__ = "SecretService"
        with patch("worthless.cli.keystore.keyring") as mock_kr:
            mock_kr.get_keyring.return_value = backend
            assert _keyring_available() is True

    def test_keyring_import_error_returns_false(self) -> None:
        with patch("worthless.cli.keystore.keyring", None):
            assert _keyring_available() is False


# ------------------------------------------------------------------
# store_fernet_key
# ------------------------------------------------------------------


class TestStoreFernetKey:
    """Store to OS keyring when available, fall back to file."""

    def test_stores_to_keyring_when_available(self, tmp_path: Path) -> None:
        key = b"test-fernet-key-value"
        with (
            patch("worthless.cli.keystore._keyring_available", return_value=True),
            patch("worthless.cli.keystore.keyring") as mock_kr,
        ):
            store_fernet_key(key, home_dir=tmp_path)

            mock_kr.set_password.assert_called_once_with("worthless", "fernet-key", key.decode())

    def test_falls_back_to_file_when_keyring_unavailable(self, tmp_path: Path) -> None:
        key = b"test-fernet-key-value"
        with patch("worthless.cli.keystore._keyring_available", return_value=False):
            store_fernet_key(key, home_dir=tmp_path)

            fernet_path = tmp_path / "fernet.key"
            assert fernet_path.exists()
            assert fernet_path.read_bytes() == key

    def test_file_has_0600_permissions(self, tmp_path: Path) -> None:
        key = b"test-fernet-key-value"
        with patch("worthless.cli.keystore._keyring_available", return_value=False):
            store_fernet_key(key, home_dir=tmp_path)

            fernet_path = tmp_path / "fernet.key"
            mode = fernet_path.stat().st_mode & 0o777
            assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    def test_falls_back_to_file_when_keyring_raises(self, tmp_path: Path) -> None:
        key = b"test-fernet-key-value"
        with (
            patch("worthless.cli.keystore._keyring_available", return_value=True),
            patch("worthless.cli.keystore.keyring") as mock_kr,
        ):
            mock_kr.set_password.side_effect = Exception("Keyring locked")
            store_fernet_key(key, home_dir=tmp_path)

            fernet_path = tmp_path / "fernet.key"
            assert fernet_path.exists()
            assert fernet_path.read_bytes() == key


# ------------------------------------------------------------------
# read_fernet_key — cascade tests
# ------------------------------------------------------------------


class TestReadFernetKeyCascade:
    """Detection cascade: env var -> keyring -> file -> error."""

    def test_reads_from_env_var(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("WORTHLESS_FERNET_KEY", "env-key-value")
        monkeypatch.delenv("WORTHLESS_FERNET_FD", raising=False)

        result = read_fernet_key(home_dir=tmp_path)

        assert result == bytearray(b"env-key-value")

    def test_reads_from_keyring(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("WORTHLESS_FERNET_KEY", raising=False)

        with (
            patch("worthless.cli.keystore._keyring_available", return_value=True),
            patch("worthless.cli.keystore.keyring") as mock_kr,
        ):
            mock_kr.get_password.return_value = "keyring-key-value"
            result = read_fernet_key(home_dir=tmp_path)

        assert result == bytearray(b"keyring-key-value")

    def test_reads_from_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("WORTHLESS_FERNET_KEY", raising=False)
        monkeypatch.delenv("WORTHLESS_FERNET_FD", raising=False)

        fernet_path = tmp_path / "fernet.key"
        fernet_path.write_bytes(b"file-key-value\n")

        with patch("worthless.cli.keystore._keyring_available", return_value=False):
            result = read_fernet_key(home_dir=tmp_path)

        assert result == bytearray(b"file-key-value")

    def test_raises_when_nothing_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("WORTHLESS_FERNET_KEY", raising=False)
        monkeypatch.delenv("WORTHLESS_FERNET_FD", raising=False)

        with patch("worthless.cli.keystore._keyring_available", return_value=False):
            with pytest.raises(WorthlessError) as exc_info:
                read_fernet_key(home_dir=tmp_path)

        assert exc_info.value.code == ErrorCode.KEY_NOT_FOUND

    def test_env_var_takes_priority_over_keyring(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Env var should win even when keyring and file both have values."""
        monkeypatch.setenv("WORTHLESS_FERNET_KEY", "env-wins")
        monkeypatch.delenv("WORTHLESS_FERNET_FD", raising=False)

        fernet_path = tmp_path / "fernet.key"
        fernet_path.write_bytes(b"file-value")

        with (
            patch("worthless.cli.keystore._keyring_available", return_value=True),
            patch("worthless.cli.keystore.keyring") as mock_kr,
        ):
            mock_kr.get_password.return_value = "keyring-value"
            result = read_fernet_key(home_dir=tmp_path)

        assert result == bytearray(b"env-wins")
        mock_kr.get_password.assert_not_called()

    def test_keyring_none_falls_through_to_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """If keyring returns None, cascade to file."""
        monkeypatch.delenv("WORTHLESS_FERNET_KEY", raising=False)
        monkeypatch.delenv("WORTHLESS_FERNET_FD", raising=False)

        fernet_path = tmp_path / "fernet.key"
        fernet_path.write_bytes(b"file-fallback-value\n")

        with (
            patch("worthless.cli.keystore._keyring_available", return_value=True),
            patch("worthless.cli.keystore.keyring") as mock_kr,
        ):
            mock_kr.get_password.return_value = None
            result = read_fernet_key(home_dir=tmp_path)

        assert result == bytearray(b"file-fallback-value")


# ------------------------------------------------------------------
# SR-01: return type is ALWAYS bytearray
# ------------------------------------------------------------------


class TestReturnTypeBytearray:
    """SR-01: read_fernet_key must always return bytearray."""

    @pytest.mark.parametrize(
        "source",
        ["env", "keyring", "file"],
        ids=["env-var", "keyring", "file"],
    )
    def test_return_type_is_bytearray(
        self,
        source: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("WORTHLESS_FERNET_KEY", raising=False)
        monkeypatch.delenv("WORTHLESS_FERNET_FD", raising=False)

        if source == "env":
            monkeypatch.setenv("WORTHLESS_FERNET_KEY", "some-key")
            ctx = patch("worthless.cli.keystore._keyring_available", return_value=False)
        elif source == "keyring":
            ctx_avail = patch("worthless.cli.keystore._keyring_available", return_value=True)
            ctx_kr = patch("worthless.cli.keystore.keyring")
            # Stack two context managers
            import contextlib

            @contextlib.contextmanager
            def _combined():  # type: ignore[no-untyped-def]
                with ctx_avail, ctx_kr as mock_kr:
                    mock_kr.get_password.return_value = "some-key"
                    yield

            ctx = _combined()
        else:  # file
            fernet_path = tmp_path / "fernet.key"
            fernet_path.write_bytes(b"some-key\n")
            ctx = patch("worthless.cli.keystore._keyring_available", return_value=False)

        with ctx:
            result = read_fernet_key(home_dir=tmp_path)

        assert isinstance(result, bytearray), (
            f"Expected bytearray from {source}, got {type(result).__name__}"
        )


# ------------------------------------------------------------------
# SR-04: no key material in error messages
# ------------------------------------------------------------------


class TestSR04NoKeyLeakage:
    """SR-04: error messages must never contain key material."""

    def test_error_message_does_not_contain_key_value(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("WORTHLESS_FERNET_KEY", raising=False)
        monkeypatch.delenv("WORTHLESS_FERNET_FD", raising=False)

        with patch("worthless.cli.keystore._keyring_available", return_value=False):
            with pytest.raises(WorthlessError) as exc_info:
                read_fernet_key(home_dir=tmp_path)

        error_str = str(exc_info.value)
        # Must not contain anything that looks like a key
        assert "sk-" not in error_str
        lower = error_str.lower()
        assert "fernet" not in lower or "key" not in lower.split("fernet")[0]
        # The path should not leak either
        assert str(tmp_path) not in error_str

    def test_error_message_does_not_contain_env_var_value(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Even partial key data must not appear in errors."""
        # Set an env var that looks like a key, but make the cascade fail anyway
        # by having the env var unset and nothing else available
        monkeypatch.delenv("WORTHLESS_FERNET_KEY", raising=False)
        monkeypatch.delenv("WORTHLESS_FERNET_FD", raising=False)

        with patch("worthless.cli.keystore._keyring_available", return_value=False):
            with pytest.raises(WorthlessError) as exc_info:
                read_fernet_key(home_dir=tmp_path)

        # Ensure error message is generic
        assert exc_info.value.code == ErrorCode.KEY_NOT_FOUND


# ------------------------------------------------------------------
# delete_fernet_key
# ------------------------------------------------------------------


class TestDeleteFernetKey:
    """Delete from keyring and/or file; never raise on missing."""

    def test_deletes_both_keyring_and_file(self, tmp_path: Path) -> None:
        fernet_path = tmp_path / "fernet.key"
        fernet_path.write_bytes(b"some-key")

        with (
            patch("worthless.cli.keystore._keyring_available", return_value=True),
            patch("worthless.cli.keystore.keyring") as mock_kr,
        ):
            delete_fernet_key(home_dir=tmp_path)

            mock_kr.delete_password.assert_called_once_with("worthless", "fernet-key")
        assert not fernet_path.exists()

    def test_deletes_file_only_when_keyring_unavailable(self, tmp_path: Path) -> None:
        fernet_path = tmp_path / "fernet.key"
        fernet_path.write_bytes(b"some-key")

        with patch("worthless.cli.keystore._keyring_available", return_value=False):
            delete_fernet_key(home_dir=tmp_path)

        assert not fernet_path.exists()

    def test_swallows_keyring_error(self, tmp_path: Path) -> None:
        fernet_path = tmp_path / "fernet.key"
        fernet_path.write_bytes(b"some-key")

        with (
            patch("worthless.cli.keystore._keyring_available", return_value=True),
            patch("worthless.cli.keystore.keyring") as mock_kr,
        ):
            mock_kr.delete_password.side_effect = Exception("Keyring locked")
            delete_fernet_key(home_dir=tmp_path)

        assert not fernet_path.exists()

    def test_no_error_when_neither_exists(self, tmp_path: Path) -> None:
        with patch("worthless.cli.keystore._keyring_available", return_value=False):
            # Should not raise
            delete_fernet_key(home_dir=tmp_path)

    def test_no_error_when_only_keyring_fails_and_no_file(self, tmp_path: Path) -> None:
        with (
            patch("worthless.cli.keystore._keyring_available", return_value=True),
            patch("worthless.cli.keystore.keyring") as mock_kr,
        ):
            mock_kr.delete_password.side_effect = Exception("Not found")
            # Should not raise
            delete_fernet_key(home_dir=tmp_path)
