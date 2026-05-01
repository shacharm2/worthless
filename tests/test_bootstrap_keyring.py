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


class TestFernetKeyMemoization:
    """HF2 / worthless-mnlp: ``WorthlessHome.fernet_key`` is memoized
    per-instance to collapse 3+ keychain calls per ``worthless lock`` to 1.

    THIS IS process-scoped caching at the dataclass instance level. THIS IS
    NOT keychain permission permanence — macOS re-evaluates the
    ``SecKeychainItemCopyContent`` ACL on every call, so 'Always Allow' on
    the dialog only sticks to the exact call that triggered the dialog;
    subsequent reads in the same process re-prompt unless served from an
    in-memory cache. New CLI invocations still re-fetch (cache is per-process,
    not per-session) — that is acceptable per the bead spec.
    """

    def test_property_memoizes_first_read(self, tmp_path: Path):
        """Accessing ``.fernet_key`` 5x must call ``read_fernet_key`` exactly once.

        Bug repro: today the property re-reads on every access, firing a fresh
        keychain ACL probe each time. After memoization the first access
        populates a private cache and subsequent accesses return the cached
        bytearray.
        """
        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        fake_key = bytearray(b"memoized-key-padded-to-44-bytes-12345678901")

        with patch(
            "worthless.cli.bootstrap.read_fernet_key",
            return_value=fake_key,
        ) as mock_read:
            for _ in range(5):
                _ = home.fernet_key
            assert mock_read.call_count == 1, (
                f"fernet_key not memoized — read_fernet_key called "
                f"{mock_read.call_count} times for 5 accesses on the same instance"
            )

    def test_memoization_is_per_instance(self, tmp_path: Path):
        """Two ``WorthlessHome`` instances must each trigger one read.

        Memoization is per-dataclass-instance, not module-level. Multi-tenant
        test fixtures (or pytest-xdist workers sharing a process) must NOT
        share a fernet cache; each ``WorthlessHome`` object owns its own.
        """
        home_a = WorthlessHome(base_dir=tmp_path / "a" / ".worthless")
        home_b = WorthlessHome(base_dir=tmp_path / "b" / ".worthless")
        fake_key = bytearray(b"shared-fake-key-padded-to-44-bytes-1234567")

        with patch(
            "worthless.cli.bootstrap.read_fernet_key",
            return_value=fake_key,
        ) as mock_read:
            _ = home_a.fernet_key
            _ = home_a.fernet_key
            _ = home_b.fernet_key
            _ = home_b.fernet_key
            assert mock_read.call_count == 2, (
                f"each WorthlessHome should trigger one read, got {mock_read.call_count}"
            )

    def test_memoized_value_remains_bytearray_and_identity_stable(self, tmp_path: Path) -> None:
        """SR-01: the cached value stays a mutable bytearray, identity stable.

        If memoization stored the result as immutable ``bytes``, secret zeroing
        (``zero_buf``) could not wipe it in place. SR-01 pre-commit hooks would
        catch the type regression at commit time, but this test pins the
        runtime contract. ``is`` identity also confirms it is true memoization
        (one cached object), not a fresh-copy-each-time look-alike.
        """
        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        fake_key = bytearray(b"sr01-check-padded-to-44-bytes-1234567890123")

        with patch(
            "worthless.cli.bootstrap.read_fernet_key",
            return_value=fake_key,
        ):
            first = home.fernet_key
            second = home.fernet_key
            assert isinstance(first, bytearray), "first read must be bytearray (SR-01)"
            assert isinstance(second, bytearray), "second read must be bytearray (SR-01)"
            assert first is second, (
                "memoized fernet_key must return the same bytearray object on repeat "
                "access (true memoization, not a fresh copy)"
            )
