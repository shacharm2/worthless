"""WOR-309 Phase 0 RED skeleton — CI guard banning crypto imports from proxy.

Per security signoff §C8 (``.research/10-security-signoff.md``): the
splitter is dead code in the proxy image post-WOR-309 and the architect's
"keep the symbol" call is defensible **iff** an AST CI guard makes
"future temptation" a CI failure rather than a code-review judgment call.

This test enumerates every Python module under ``worthless.proxy.*`` and
asserts none of them import:

* ``worthless.crypto.splitter`` (the in-process reconstruction path)
* ``cryptography.fernet`` (the Fernet primitive used for shard encryption)
* ``worthless.crypto`` as a package (catches ``from worthless import crypto``)

The assertion runs over both ``Import`` and ``ImportFrom`` AST nodes, at
module top-level AND inside any function/class body — ``importlib`` use is
covered by the runtime sys.modules check in
``tests/ipc/test_no_inprocess_fallback.py`` (the two together close the
gap; static catches source references, runtime catches dynamic loads).

Phase 0: RED skeleton; Phase 1 implements the walker.
"""

from __future__ import annotations


def test_proxy_modules_do_not_import_crypto_splitter() -> None:
    """AST CI guard: no ``worthless.proxy.*`` module imports crypto-fallback symbols.

    GREEN:
        1. iter_modules(worthless.proxy) → list of module file paths
        2. for each path: ast.parse(text); ast.walk for Import/ImportFrom
        3. assert no node references ``worthless.crypto.splitter``,
           ``cryptography.fernet``, or imports the ``worthless.crypto``
           package as a whole.
        4. include a clear error message naming the offending file + line.
    """
    raise NotImplementedError("Phase 1 — AST import-graph CI guard not implemented yet")
