"""WOR-309 Phase 0 RED skeletons — unit tests for the proxy IPC client refactor.

Tests #1-13 from ``.research/04-test-plan.md``. All tests intentionally fail
with ``NotImplementedError`` until Phase 1 (`IPCSupervisor`) and Phase 2
(`ShardReader`) land. The fixtures (`broken_ipc_client`, `fake_sidecar`)
are wired in ``tests/ipc/conftest.py`` and exercise the *real* wire
protocol — no mocks of ``asyncio.open_unix_connection``.

Each docstring states the behaviour the eventual GREEN implementation
must satisfy so a reviewer can assess test coverage without re-reading
the plan.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


async def test_happy_path_decrypt(fake_sidecar) -> None:
    """#1 Round-trip one ``open`` request through the supervisor → 200 + plaintext.

    GREEN: IPCSupervisor.open(ct) returns the fake's ``FAKE-PT`` body
    after a successful HELLO handshake.
    """
    raise NotImplementedError("Phase 1 — IPCSupervisor not implemented yet")


async def test_503_on_broken_client(broken_ipc_client) -> None:
    """#2 Every IPC method raises ``IPCProtocolError`` → proxy returns HTTP 503.

    GREEN: proxy.app translates IPCProtocolError into a 503 response with
    no plaintext key bytes leaked into the body.
    """
    raise NotImplementedError("Phase 1 — proxy 503 mapping not implemented yet")


async def test_no_plaintext_in_503_body(broken_ipc_client) -> None:
    """#3 Property test: 503 body never contains key plaintext (Hypothesis, 100 cases).

    GREEN: hypothesis.given(st.binary()) drives shard plaintext through the
    failure path; assert the 503 JSON body excludes those bytes.
    """
    raise NotImplementedError("Phase 1 — Hypothesis property test not authored yet")


async def test_no_crypto_import_static() -> None:
    """#4 AST scan: ``worthless.proxy.app`` MUST NOT import crypto-fallback symbols.

    GREEN: walk ast.parse(proxy/app.py) for Import/ImportFrom; assert
    ``cryptography.fernet`` and ``worthless.crypto.splitter`` are absent
    at module scope and inside the request handler. Mirrors the AST CI
    guard at tests/architecture/test_proxy_imports.py.
    """
    raise NotImplementedError("Phase 1 — AST guard not implemented yet")


async def test_no_crypto_import_runtime(broken_ipc_client) -> None:
    """#5 sys.modules snapshot diff: serving with broken IPC must not import crypto.

    GREEN: snapshot sys.modules; ``del sys.modules['cryptography.fernet']``
    if loaded; drive a request via broken_ipc_client; assert
    ``'cryptography.fernet' not in sys.modules``.
    """
    raise NotImplementedError("Phase 1 — runtime introspection not implemented yet")


async def test_version_handshake_match(fake_sidecar) -> None:
    """#6 HELLO with matching protocol_version → handshake succeeds.

    GREEN: fake_sidecar advertises v=1 (default); IPCSupervisor accepts.
    """
    raise NotImplementedError("Phase 1 — IPCSupervisor not implemented yet")


async def test_version_handshake_too_new(fake_sidecar) -> None:
    """#7 HELLO advertising v=99 → IPCSupervisor refuses, no second frame written.

    GREEN: handle.protocol_version=99; supervisor raises a typed mismatch
    error and writes no further frames (assert handle.requests_seen == 0
    after handshake).
    """
    raise NotImplementedError("Phase 1 — version mismatch handling not implemented yet")


async def test_version_handshake_too_old(fake_sidecar) -> None:
    """#8 HELLO advertising v=0 → IPCSupervisor refuses, no second frame written.

    GREEN: handle.protocol_version=0; supervisor raises mismatch (same
    direction-agnostic invariant as #7).
    """
    raise NotImplementedError("Phase 1 — version mismatch handling not implemented yet")


async def test_timeout_fires_at_2s(fake_sidecar) -> None:
    """#9 Sidecar sleeps 3 s → client raises ``IPCTimeoutError`` within 2.1 s wall.

    GREEN: handle.sleep_before_response=3.0; assert wall-clock <= 2.1 s
    and IPCTimeoutError surfaces (mapped to 503 by the proxy layer).
    """
    raise NotImplementedError("Phase 1 — 2s timeout enforcement not implemented yet")


async def test_partial_request_cleaned_up(fake_sidecar) -> None:
    """#10 After a timeout: the writer is closed and the pool drops the socket.

    GREEN: trigger timeout; assert client._writer is None AND the FD count
    has not grown (no leak).
    """
    raise NotImplementedError("Phase 1 — timeout cleanup not implemented yet")


async def test_correlation_safety_after_timeout(fake_sidecar) -> None:
    """#11 Killer test: after req A times out, req B must receive B's response.

    GREEN: sidecar replies to A late, after B is sent. Supervisor must
    NOT mis-route A's late frame as B's reply. Correlation IDs are
    monotonic uint64 — proves no reuse across timeouts.
    """
    raise NotImplementedError("Phase 1 — correlation safety not implemented yet")


async def test_reconnect_after_fake_drop(fake_sidecar) -> None:
    """#12 Sidecar drops mid-session → next call rebuilds the pool and succeeds.

    GREEN: handle.drop_after_n_requests=1; first call OK, second call
    transparently reconnects. Caps re-checked on reconnect (security
    restoration C3) — handle.caps mismatch on reconnect terminates.
    """
    raise NotImplementedError("Phase 1 — reconnect logic not implemented yet")


async def test_32_concurrent_requests_distinct_responses(fake_sidecar) -> None:
    """#13 Concurrency: 32 coroutines round-trip distinct payloads without crosstalk.

    GREEN: each coroutine encodes its index in the seal payload; assert
    response.index == request.index for all 32. Proves length-prefix
    framing is intact and correlation IDs do not collide under burst.
    """
    raise NotImplementedError("Phase 1 — concurrency-safe supervisor not implemented yet")
