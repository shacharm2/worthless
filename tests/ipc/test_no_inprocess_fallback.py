"""WOR-309 Phase 0 RED skeletons — three orthogonal "no in-process fallback" assertions.

From ``.research/04-test-plan.md`` §2. Three independent tests, each
attacking the same invariant from a different angle:

1. **Behavioral** — property test (Hypothesis, 100 cases) proving the 503
   body never echoes plaintext key bytes when IPC is broken.
2. **AST (static)** — parse ``worthless.proxy.app`` and walk every
   ``Import`` / ``ImportFrom`` node; assert no path leads to
   ``cryptography.fernet`` or ``worthless.crypto.*`` at module scope OR
   inside ``proxy_request`` / its helpers.
3. **Runtime introspection** — sys.modules snapshot diff: drive a
   request through ``broken_ipc_client``; assert ``cryptography.fernet``
   never enters sys.modules. Catches dynamic ``importlib`` loads that
   the AST scan would miss.

Coverage diff is the wrong tool here — it proves *executed*, not
*imported*. AST + sys.modules is the right pair.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


async def test_no_plaintext_in_503_body_property(broken_ipc_client) -> None:
    """Behavioral: property test (Hypothesis, 100 cases) — no key bytes in 503 body.

    GREEN: hypothesis.given(st.binary(min_size=1, max_size=128)) generates
    candidate shard plaintexts; drive each through the broken-IPC failure
    path; assert ``response.body`` (raw bytes) does not contain the
    plaintext as a substring.
    """
    raise NotImplementedError("Phase 1 — Hypothesis property test not authored yet")


async def test_no_crypto_import_static() -> None:
    """AST: ``worthless.proxy.app`` imports neither ``cryptography.fernet`` nor splitter.

    GREEN: ast.parse(Path(worthless.proxy.app.__file__).read_text());
    walk every Import/ImportFrom; assert the banned modules are absent at
    module scope AND inside any function body. Mirrored at
    tests/architecture/test_proxy_imports.py for CI enforcement.
    """
    raise NotImplementedError("Phase 1 — AST guard not implemented yet")


async def test_no_crypto_import_runtime(broken_ipc_client) -> None:
    """Runtime: sys.modules snapshot proves crypto is never dynamically imported.

    GREEN: snapshot sys.modules; ``del sys.modules['cryptography.fernet']``
    if loaded; drive a request via broken_ipc_client; assert
    ``'cryptography.fernet' not in sys.modules`` AND
    ``'worthless.crypto.splitter' not in sys.modules`` after the request.
    Catches importlib-based dynamic loads the AST scan misses.
    """
    raise NotImplementedError("Phase 1 — runtime introspection not implemented yet")
