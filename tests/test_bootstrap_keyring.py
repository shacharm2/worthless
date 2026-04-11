"""Tests for bootstrap.py keystore integration (WOR-187).

These tests verify that bootstrap delegates Fernet key storage and
retrieval to the keystore module instead of doing direct file I/O.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from worthless.cli.bootstrap import WorthlessHome, ensure_home
from worthless.cli.errors import ErrorCode, WorthlessError


class TestEnsureHomeUsesKeystore:
    """ensure_home() must delegate to store_fernet_key for new keys."""

    def test_calls_store_fernet_key_when_key_missing(self, tmp_path: Path):
        """When no fernet key exists, ensure_home calls store_fernet_key."""
        with (
            patch(
                "worthless.cli.bootstrap.read_fernet_key",
                side_effect=WorthlessError(ErrorCode.KEY_NOT_FOUND, "no key"),
            ),
            patch("worthless.cli.bootstrap.store_fernet_key") as mock_store,
        ):
            ensure_home(base_dir=tmp_path / ".worthless")
            mock_store.assert_called_once()
            key_arg = mock_store.call_args[0][0]
            assert isinstance(key_arg, bytes)
            assert len(key_arg) == 44  # Fernet keys are 44 bytes base64

    def test_store_receives_home_base_dir(self, tmp_path: Path):
        """store_fernet_key is called with the home base_dir."""
        base = tmp_path / ".worthless"
        with (
            patch(
                "worthless.cli.bootstrap.read_fernet_key",
                side_effect=WorthlessError(ErrorCode.KEY_NOT_FOUND, "no key"),
            ),
            patch("worthless.cli.bootstrap.store_fernet_key") as mock_store,
        ):
            ensure_home(base_dir=base)
            call_args = mock_store.call_args
            if len(call_args[0]) > 1:
                assert call_args[0][1] == base
            else:
                assert call_args[1].get("home_dir") == base

    def test_does_not_call_store_when_key_exists(self, tmp_path: Path):
        """When fernet key already exists, store_fernet_key is NOT called."""
        base = tmp_path / ".worthless"
        with patch("worthless.cli.keystore.keyring_available", return_value=False):
            ensure_home(base_dir=base)

        with (
            patch("worthless.cli.bootstrap.store_fernet_key") as mock_store,
            patch(
                "worthless.cli.bootstrap.read_fernet_key",
                return_value=bytearray(b"x" * 44),
            ),
        ):
            ensure_home(base_dir=base)
        mock_store.assert_not_called()

    def test_idempotent_no_error_on_second_call(self, tmp_path: Path):
        """Calling ensure_home twice does not raise."""
        base = tmp_path / ".worthless"
        with patch("worthless.cli.keystore.keyring_available", return_value=False):
            ensure_home(base_dir=base)
            ensure_home(base_dir=base)

    def test_store_fernet_key_error_wrapped_in_worthless_error(self, tmp_path: Path):
        """If store_fernet_key raises, ensure_home wraps it in WorthlessError."""
        with (
            patch(
                "worthless.cli.bootstrap.read_fernet_key",
                side_effect=WorthlessError(ErrorCode.KEY_NOT_FOUND, "no key"),
            ),
            patch(
                "worthless.cli.bootstrap.store_fernet_key",
                side_effect=OSError("keyring exploded"),
            ),
        ):
            with pytest.raises(WorthlessError) as exc_info:
                ensure_home(base_dir=tmp_path / ".worthless")
            assert exc_info.value.code.value == 100  # BOOTSTRAP_FAILED

    def test_no_direct_os_open_for_fernet_key(self, tmp_path: Path):
        """ensure_home must NOT use os.open to write the fernet key directly."""
        import os

        original_os_open = os.open
        fernet_opens: list[str] = []

        def tracking_open(path, flags, mode=0o777, *args, **kwargs):
            if "fernet" in str(path):
                fernet_opens.append(str(path))
            return original_os_open(path, flags, mode, *args, **kwargs)

        with (
            patch("os.open", side_effect=tracking_open),
            patch(
                "worthless.cli.bootstrap.read_fernet_key",
                side_effect=WorthlessError(ErrorCode.KEY_NOT_FOUND, "no key"),
            ),
            patch("worthless.cli.bootstrap.store_fernet_key") as mock_store,
        ):
            ensure_home(base_dir=tmp_path / ".worthless")

        if mock_store.called:
            assert fernet_opens == [], (
                f"ensure_home called os.open for fernet key directly: {fernet_opens}"
            )


class TestFernetKeyPropertyUsesKeystore:
    """WorthlessHome.fernet_key must delegate to read_fernet_key."""

    def test_calls_read_fernet_key(self, tmp_path: Path):
        """fernet_key property calls read_fernet_key with base_dir."""
        home = WorthlessHome(base_dir=tmp_path / ".worthless")

        with patch(
            "worthless.cli.bootstrap.read_fernet_key",
            return_value=bytearray(b"test-key-value-padded-to-44-bytes-12345678901"),
        ) as mock_read:
            _ = home.fernet_key
            mock_read.assert_called_once_with(home.base_dir)

    def test_returns_bytearray(self, tmp_path: Path):
        """fernet_key property returns bytearray per SR-01."""
        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        fake_key = bytearray(b"fake-fernet-key-44-chars-padded-to-44-bytes")

        with patch(
            "worthless.cli.bootstrap.read_fernet_key",
            return_value=fake_key,
        ):
            result = home.fernet_key
            assert isinstance(result, bytearray), f"Expected bytearray, got {type(result).__name__}"
            assert result == bytes(fake_key)

    def test_propagates_key_not_found_error(self, tmp_path: Path):
        """When read_fernet_key raises KEY_NOT_FOUND, property propagates it."""
        home = WorthlessHome(base_dir=tmp_path / ".worthless")

        with patch(
            "worthless.cli.bootstrap.read_fernet_key",
            side_effect=WorthlessError(ErrorCode.KEY_NOT_FOUND, "no key"),
        ):
            with pytest.raises(WorthlessError) as exc_info:
                _ = home.fernet_key
            assert exc_info.value.code == ErrorCode.KEY_NOT_FOUND
