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

    def test_caller_mutation_does_not_poison_cache(self, tmp_path: Path) -> None:
        """Consumer ``zero_buf`` on the returned bytearray must NOT poison the cache.

        Production callers (``unlock.py``, ``proxy/app.py``) zero their
        bytearray after using the key, per SR-01. The OLD design (return the
        cached object directly) would have meant their ``zero_buf`` zeroed
        the cache itself, so the next reader on the same ``WorthlessHome``
        would silently get all-zero bytes — decryption would corrupt without
        raising. This test simulates that exact flow: read, zero the returned
        copy, read again, assert clean key bytes are returned.

        A future regression to "return self._cached_fernet_key" (no copy)
        would fail this test loudly.
        """
        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        clean_key = b"poison-test-padded-to-44-bytes-1234567890123"
        # Mock returns a NEW bytearray each call so the cache stores its own
        # bytearray (not a reference the test holds onto).
        with patch(
            "worthless.cli.bootstrap.read_fernet_key",
            side_effect=lambda *_: bytearray(clean_key),
        ):
            first = home.fernet_key
            # Caller zeros their copy — simulates SR-01 ``zero_buf()`` after use.
            for i in range(len(first)):
                first[i] = 0
            assert all(b == 0 for b in first), "precondition: caller's copy is fully zeroed"

            # Subsequent reader must get the original clean key bytes from cache,
            # not whatever residue the caller left in their now-zeroed copy.
            second = home.fernet_key
            assert bytes(second) == clean_key, (
                f"cache was poisoned by caller mutation: returned "
                f"{bytes(second)!r}, expected {clean_key!r}"
            )
            assert any(b != 0 for b in second), (
                "second read returned all-zero bytes — cache was poisoned"
            )

    def test_property_returns_fresh_bytearray_copies_with_same_content(
        self, tmp_path: Path
    ) -> None:
        """Each property access returns an INDEPENDENT bytearray copy.

        After memoization, the cache is the canonical source but the property
        returns a fresh copy on each access so callers can ``zero_buf()`` their
        own copy (per SR-01) without poisoning the cache. This test pins:

        * SR-01 type contract — cached and returned values are ``bytearray``,
          not immutable ``bytes``.
        * True memoization — proven by ``call_count == 1`` over two accesses.
          The earlier version of this test asserted ``first is second``, which
          would have passed even without a cache because ``mock.return_value``
          is the same object on every call. Using ``side_effect=lambda *_:
          bytearray(...)`` returns a NEW object on each underlying read, so
          ``call_count`` is the only honest way to prove the cache is engaged.
        * Fresh-copy contract — ``first is not second`` confirms callers each
          get their own bytearray. A future regression to "return the cached
          object directly" would re-introduce the consumer-zeroing-poisons-
          cache bug; this assertion catches that.
        """
        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        # Mock returns a NEW bytearray each underlying call so identity-only
        # checks cannot false-pass.
        with patch(
            "worthless.cli.bootstrap.read_fernet_key",
            side_effect=lambda *_: bytearray(b"sr01-check-padded-to-44-bytes-1234567890123"),
        ) as mock_read:
            first = home.fernet_key
            second = home.fernet_key
            assert mock_read.call_count == 1, (
                f"fernet_key not memoized — read_fernet_key called "
                f"{mock_read.call_count} times for 2 accesses on the same instance"
            )
            assert isinstance(first, bytearray), "first read must be bytearray (SR-01)"
            assert isinstance(second, bytearray), "second read must be bytearray (SR-01)"
            assert first == second, "both reads must derive from the same cached source"
            assert first is not second, (
                "property must return a FRESH bytearray copy on each access so "
                "callers can zero_buf() their copy without poisoning the cache"
            )
