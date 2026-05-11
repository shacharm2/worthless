"""Proof test: the proxy uid MUST NOT read fernet.key under the flag.

This is the load-bearing security invariant for WOR-465. If a proxy
RCE can read the Fernet key off disk, WOR-310's "offline key theft
blocked under proxy compromise" claim is void.

The test pair is structured as a NEGATIVE + POSITIVE-CONTROL bracket:

* **Negative** (flag ON): proxy boot MUST NOT call ``read_fernet_key``.
  Recorder list stays empty.
* **Positive control** (flag OFF): proxy boot MUST call ``read_fernet_key``
  at least once — proves the negative test isn't passing for the wrong
  reason (e.g. a future refactor that deletes the call site entirely).

Without the positive control, a regression that removes the legacy
``read_fernet_key`` call would let the negative test pass silently.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from worthless.proxy import config as proxy_config


@pytest.fixture
def read_fernet_key_recorder(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """Replace ``worthless.cli.keystore.read_fernet_key`` with a recorder.

    Yields the list of recorded call args. Tests assert on its length.
    The recorder returns an empty bytearray so the legacy code path
    keeps working — we are pinning a CALL-SITE invariant, not crashing
    the system.
    """
    calls: list[Any] = []

    def _recording_read_fernet_key(home_dir: Any = None) -> bytearray:
        calls.append(home_dir)
        return bytearray()

    # Patch at the module the proxy actually imports from. proxy/config.py
    # does `from worthless.cli.keystore import read_fernet_key`, so the
    # bound name lives on proxy.config too — patch BOTH to be safe.
    monkeypatch.setattr(
        "worthless.cli.keystore.read_fernet_key",
        _recording_read_fernet_key,
    )
    monkeypatch.setattr(
        "worthless.proxy.config.read_fernet_key",
        _recording_read_fernet_key,
    )
    yield calls


def test_proxy_uid_never_calls_read_fernet_key_with_flag_on(
    read_fernet_key_recorder: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON: proxy uid MUST NOT touch read_fernet_key on any path.

    This is the security invariant. The proxy is supposed to delegate
    all crypto to the sidecar over IPC; if anything in the proxy boot
    or fernet-key resolution path still calls read_fernet_key, the
    proxy uid has key material in memory and a proxy RCE wins.
    """
    monkeypatch.setenv("WORTHLESS_FERNET_IPC_ONLY", "1")
    # Strip any FD-pass env so we don't take that legacy short-circuit.
    monkeypatch.delenv("WORTHLESS_FERNET_FD", raising=False)

    fernet_key = proxy_config._read_fernet_key()

    assert read_fernet_key_recorder == [], (
        "WOR-465 invariant broken: under WORTHLESS_FERNET_IPC_ONLY=1, "
        "the proxy MUST NOT call read_fernet_key. Recorder saw "
        f"{len(read_fernet_key_recorder)} call(s) with args "
        f"{read_fernet_key_recorder!r}."
    )
    assert fernet_key == bytearray(), (
        "Flag-on path must yield an empty bytearray — the proxy has no "
        "in-process key. Anything non-empty means a key was sourced and "
        "is now sitting in the proxy uid's memory."
    )


def test_proxy_uid_DOES_call_read_fernet_key_without_flag(
    read_fernet_key_recorder: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSITIVE CONTROL: flag OFF MUST call read_fernet_key at least once.

    Without this, the negative test could pass for the wrong reason —
    e.g. a future refactor that deletes the read_fernet_key call site
    entirely. The positive control proves the *behaviour changes
    across the flag*, not just that the call is silent on the flag-on
    side.
    """
    monkeypatch.delenv("WORTHLESS_FERNET_IPC_ONLY", raising=False)
    monkeypatch.delenv("WORTHLESS_FERNET_FD", raising=False)

    proxy_config._read_fernet_key()

    assert len(read_fernet_key_recorder) >= 1, (
        "Positive-control failed: with WORTHLESS_FERNET_IPC_ONLY unset, "
        "the bare-metal/legacy path MUST call read_fernet_key. Zero "
        "calls observed — either the legacy call site was removed "
        "(making the negative test meaningless) or the recorder fixture "
        "is not wired correctly. Either way, fix BEFORE trusting the "
        "negative-direction test."
    )
