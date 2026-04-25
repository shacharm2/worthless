"""Shared test fixtures for Worthless."""

from __future__ import annotations

import json
import os
from pathlib import Path

import keyring
import keyring.backends.null
import pytest
from cryptography.fernet import Fernet
from hypothesis import HealthCheck, settings

from worthless.cli.bootstrap import WorthlessHome, ensure_home
from worthless.crypto import SplitResult, split_key
from worthless.proxy import app as _proxy_app_module
from worthless.storage.repository import ShardRepository, StoredShard

from tests._fakes.fake_ipc_supervisor import FakeIPCSupervisor
from tests.helpers import fake_openai_key

# Disable the real OS keyring for the entire test session.
#
# Context: on macOS, concurrent ``SecItemAdd`` calls from multiple pytest-xdist
# workers can hang indefinitely on the Keychain API. ``pytest-timeout`` then
# kills the test with a 30 s signal, which surfaces as apparently-random test
# failures that only ``--reruns 1`` was masking.
#
# The null backend is already in ``keystore._REJECTED_BACKENDS``, so
# ``keyring_available()`` returns False and the file-fallback path is used
# — same code path tests always exercised, minus the system-wide contention.
keyring.set_keyring(keyring.backends.null.Keyring())

# Suppress differing_executors health check ONLY when running under mutmut.
# Mutmut runs tests from its mutants/ directory with a different rootdir,
# which triggers this check spuriously.  In normal test runs, the check
# remains active to catch real working-directory issues.
if os.environ.get("MUTANT_UNDER_TEST"):
    settings.register_profile("mutmut", suppress_health_check=[HealthCheck.differing_executors])
    settings.load_profile("mutmut")

# CI profile: cap Hypothesis examples, derandomize for reproducibility,
# disable on-disk database for xdist compatibility.
# Activate with: HYPOTHESIS_PROFILE=ci
settings.register_profile(
    "ci",
    max_examples=50,
    derandomize=True,
    database=None,
)

# Extended profile: thorough property testing for scheduled runs.
# Activate with: HYPOTHESIS_PROFILE=extended
settings.register_profile(
    "extended",
    max_examples=500,
    database=None,
)

_profile = os.environ.get("HYPOTHESIS_PROFILE")
if _profile in ("ci", "extended"):
    settings.load_profile(_profile)


def make_repo(home: WorthlessHome) -> ShardRepository:
    """Build a ShardRepository from a WorthlessHome (test helper)."""
    return ShardRepository(str(home.db_path), home.fernet_key)


# ------------------------------------------------------------------
# CLI bootstrap fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def home_dir(tmp_path) -> WorthlessHome:
    """Bootstrap a fresh WorthlessHome in tmp_path."""
    return ensure_home(tmp_path / ".worthless")


@pytest.fixture()
def home_with_key(home_dir: WorthlessHome) -> WorthlessHome:
    """Home with one enrolled key (openai)."""
    import asyncio

    key = fake_openai_key()
    sr = split_key(key.encode())
    try:
        alias = "openai-a1b2c3d4"

        repo = make_repo(home_dir)
        asyncio.run(repo.initialize())
        stored = stored_shard_from_split(sr, provider="openai")
        asyncio.run(
            repo.store_enrolled(
                alias,
                stored,
                var_name="OPENAI_API_KEY",
                env_path="/tmp/.env",  # noqa: S108
            )
        )
    finally:
        sr.zero()
    return home_dir


# ------------------------------------------------------------------
# Adapter-layer fixtures (Phase 2)
# ------------------------------------------------------------------


@pytest.fixture
def sample_openai_body() -> bytes:
    """A minimal OpenAI chat completion request body."""
    return json.dumps({"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}).encode()


@pytest.fixture
def sample_anthropic_body() -> bytes:
    """A minimal Anthropic messages request body."""
    return json.dumps(
        {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "hi"}],
        }
    ).encode()


@pytest.fixture
def sample_api_key() -> bytearray:
    """A fake API key bytearray for adapter tests (SR-01: no immutable secrets)."""
    return bytearray(b"sk-test-fake-key-1234567890")


@pytest.fixture
def mock_openai_sse_chunks() -> list[bytes]:
    """Realistic OpenAI SSE chunks."""
    return [
        b'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Hello"}}]}\n\n',
        b'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":" world"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]


@pytest.fixture
def mock_anthropic_sse_chunks() -> list[bytes]:
    """Realistic Anthropic SSE chunks."""
    return [
        b'event: content_block_delta\ndata: {"type":"content_block_delta",'
        b'"delta":{"type":"text_delta","text":"Hello"}}\n\n',
        b'event: content_block_delta\ndata: {"type":"content_block_delta",'
        b'"delta":{"type":"text_delta","text":" world"}}\n\n',
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]


# ------------------------------------------------------------------
# Crypto-layer fixtures (Phase 1)
# ------------------------------------------------------------------


@pytest.fixture()
def sample_api_key_bytes() -> bytes:
    """A realistic-length API key for crypto tests."""
    return b"sk-test-key-1234567890abcdef"


@pytest.fixture()
def sample_long_key() -> bytes:
    """A 64-byte key for testing longer keys."""
    return b"sk-long-" + b"A" * 56


def assert_zeroed(buf: bytearray) -> None:
    """Assert every byte in *buf* is zero."""
    assert all(b == 0 for b in buf), f"Buffer not zeroed: {buf[:8].hex()}..."


def write_shard_a(home: WorthlessHome, alias: str, data: bytes) -> Path:
    """Write a shard_a file with 0o600 perms. Returns the file path."""
    shard_a_path = home.shard_a_dir / alias
    fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return shard_a_path


# ------------------------------------------------------------------
# Storage-layer fixtures (Phase 1)
# ------------------------------------------------------------------


@pytest.fixture()
def tmp_db_path(tmp_path) -> str:
    """Temporary SQLite database path."""
    return str(tmp_path / "test.db")


@pytest.fixture()
def fernet_key() -> bytes:
    """A freshly generated Fernet key."""
    return Fernet.generate_key()


@pytest.fixture()
def sample_split_result(sample_api_key_bytes: bytes) -> SplitResult:
    """A real SplitResult produced from the sample API key."""
    return split_key(sample_api_key_bytes)


def stored_shard_from_split(sr: SplitResult, provider: str = "openai") -> StoredShard:
    """Build a StoredShard from a SplitResult (wrapping in bytearray per SR-01)."""
    return StoredShard(
        shard_b=bytearray(sr.shard_b),
        commitment=bytearray(sr.commitment),
        nonce=bytearray(sr.nonce),
        provider=provider,
    )


@pytest.fixture()
async def repo(tmp_db_path: str, fernet_key: bytes) -> ShardRepository:
    """An initialized ShardRepository backed by a temp database."""
    r = ShardRepository(tmp_db_path, fernet_key)
    await r.initialize()
    return r


# ------------------------------------------------------------------
# WOR-309 Phase 5: autouse FakeIPCSupervisor injection
# ------------------------------------------------------------------
#
# Phase 3 rewired the proxy to call ``app.state.ipc_supervisor.open(...)``
# instead of an in-process Fernet decrypt. ``app.py:180-189`` honours a
# pre-set ``app.state.ipc_supervisor`` and skips eager-connect when
# ``app.state.ipc_supervisor_preconnected`` is truthy.
#
# Two layers of injection — both autouse, both bypassed by ``real_ipc``:
#
# 1. Session-scoped: wrap ``worthless.proxy.app.create_app`` once at
#    session start. Required because module-scoped fixtures (like
#    ``live_proxy`` in tests/test_contract.py) execute their setup
#    *outside* function-scoped autouse fixtures. If we only patched at
#    function scope, those module fixtures would build a real proxy
#    against a non-existent sidecar socket and 503 every test.
#
# 2. Function-scoped: re-wrap per-test so a leftover patch from a
#    `real_ipc`-marked test (which clobbered the wrap) is restored. Also
#    yields the per-test ``fakes_seen`` list to test code that wants to
#    inspect the fake.


def _make_create_app_wrapper(original_create_app, fakes_seen: list[FakeIPCSupervisor]):
    """Return a wrapper that stamps a FakeIPCSupervisor onto every app."""

    def _wrapped(*args, **kwargs):
        app = original_create_app(*args, **kwargs)
        if getattr(app.state, "ipc_supervisor", None) is None:
            fake = FakeIPCSupervisor()
            app.state.ipc_supervisor = fake
            app.state.ipc_supervisor_preconnected = True
            fakes_seen.append(fake)
        return app

    return _wrapped


_ORIGINAL_CREATE_APP = _proxy_app_module.create_app


@pytest.fixture(autouse=True, scope="session")
def _session_fake_ipc_supervisor():
    """Patch ``create_app`` for the whole test session.

    Walks every already-imported module that captured ``create_app`` by
    ``from ... import create_app`` and rebinds the captured reference to
    the wrapped version. Restored on session teardown.
    """
    import sys

    fakes_seen: list[FakeIPCSupervisor] = []
    wrapper = _make_create_app_wrapper(_ORIGINAL_CREATE_APP, fakes_seen)

    # Snapshot every captured reference so we can restore them.
    captured_locations: list[tuple[object, str]] = []
    for mod in list(sys.modules.values()):
        if mod is None or mod is _proxy_app_module:
            continue
        captured = getattr(mod, "create_app", None)
        if captured is _ORIGINAL_CREATE_APP:
            captured_locations.append((mod, "create_app"))
            setattr(mod, "create_app", wrapper)
    _proxy_app_module.create_app = wrapper

    try:
        yield fakes_seen
    finally:
        _proxy_app_module.create_app = _ORIGINAL_CREATE_APP
        for mod, attr in captured_locations:
            setattr(mod, attr, _ORIGINAL_CREATE_APP)


@pytest.fixture(autouse=True)
def _autouse_fake_ipc_supervisor(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Per-test wrapping. Honours ``real_ipc`` opt-out.

    ``real_ipc``-marked tests need the *original* ``create_app`` so the
    lifespan really connects to a subprocess sidecar. We unconditionally
    rebind to the original at function-scope; ``monkeypatch`` restores the
    session-scope wrap on teardown.
    """
    if request.node.get_closest_marker("real_ipc") is not None:
        import sys

        monkeypatch.setattr(_proxy_app_module, "create_app", _ORIGINAL_CREATE_APP)
        for mod in list(sys.modules.values()):
            if mod is None or mod is _proxy_app_module:
                continue
            captured = getattr(mod, "create_app", None)
            if captured is not None and captured is not _ORIGINAL_CREATE_APP:
                monkeypatch.setattr(mod, "create_app", _ORIGINAL_CREATE_APP)
        yield None
        return

    # Convenience: tests that want the fake from
    # ``app.state.ipc_supervisor`` already have it; this list is just a
    # placeholder for parameter symmetry with the session fixture.
    yield []
