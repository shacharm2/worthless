"""Architectural invariant tests for Worthless security boundaries.

These tests enforce structural constraints that are too important to leave
to code review alone.  A failing test here means an architectural invariant
has been violated — treat as a P0.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conftest import assert_zeroed
from worthless.crypto.splitter import reconstruct_key, secure_key, split_key

# Root of the worthless package under src/
_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "worthless"

# Allowlist: directories where split_key IS permitted (client-side or definition).
# Everything else under src/worthless/ is server-side and must NOT import split_key.
# When adding a new package, it lands in the "server" bucket by default — safe by
# construction.  Only add to this allowlist after confirming client-side usage.
_CLIENT_DIRS = {"cli", "crypto"}


# ---------------------------------------------------------------------------
# WOR-53 — Invariant #1: split_key is never imported server-side
# ---------------------------------------------------------------------------


def _collect_server_python_files() -> list[Path]:
    """Collect all .py files under server-side subdirectories of _SRC_ROOT.

    Server-side = everything EXCEPT the allowlisted client dirs.  This is an
    allowlist (not a denylist) so new packages are scanned by default.
    """
    files: list[Path] = []
    for py_file in sorted(_SRC_ROOT.rglob("*.py")):
        rel_parts = py_file.relative_to(_SRC_ROOT).parts
        if rel_parts and rel_parts[0] in _CLIENT_DIRS:
            continue
        files.append(py_file)
    return files


def _extract_imported_names(source: str) -> set[str]:
    """Parse Python source with AST and return all directly imported names.

    Handles:
      - ``from foo import split_key``
      - ``from foo import split_key as alias``
      - ``import split_key`` (unlikely but covered)
    Does NOT catch dynamic imports (getattr, importlib) — the grep test below
    covers those.
    """
    names: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return names

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[-1])
                if alias.asname:
                    names.add(alias.asname)
    return names


# Cache: one read + one parse per file, shared across all tests.
_file_cache: dict[Path, tuple[str, ast.Module]] = {}


def _get_cached(py_file: Path) -> tuple[str, ast.Module]:
    """Return (source, AST) for a file, caching to avoid redundant I/O."""
    if py_file not in _file_cache:
        source = py_file.read_text()
        _file_cache[py_file] = (source, ast.parse(source))
    return _file_cache[py_file]


class TestSplitKeyNeverServerSide:
    """Invariant #1: split_key runs on the client exclusively."""

    server_files = _collect_server_python_files()

    @pytest.mark.parametrize(
        "py_file",
        server_files,
        ids=[str(f.relative_to(_SRC_ROOT)) for f in server_files],
    )
    def test_ast_no_split_key_import(self, py_file: Path) -> None:
        """AST scan: no server module imports split_key."""
        source, _ = _get_cached(py_file)
        imported = _extract_imported_names(source)
        assert "split_key" not in imported, (
            f"{py_file.relative_to(_SRC_ROOT)} imports 'split_key' — "
            f"this violates architectural invariant #1 (client-side splitting only)"
        )

    @pytest.mark.parametrize(
        "py_file",
        server_files,
        ids=[str(f.relative_to(_SRC_ROOT)) for f in server_files],
    )
    def test_grep_no_split_key_string(self, py_file: Path) -> None:
        """Grep scan: no server module references 'split_key' as a string.

        Catches dynamic imports like ``getattr(mod, 'split_key')`` or
        ``importlib.import_module(...).split_key`` that AST import scanning misses.

        Limitation: does not catch string-concatenation bypasses like
        ``getattr(mod, 'split' + '_key')``.  This test guards against
        accidental imports, not adversarial code.
        """
        source, _ = _get_cached(py_file)
        for lineno, line in enumerate(source.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            assert "split_key" not in line, (
                f"{py_file.relative_to(_SRC_ROOT)}:{lineno} references 'split_key' — "
                f"this violates architectural invariant #1 (client-side splitting only)"
            )

    def test_server_files_found(self) -> None:
        """At least one server-side .py file must exist for these tests to be meaningful."""
        assert self.server_files, (
            f"No server-side .py files found under {_SRC_ROOT} (excluding {_CLIENT_DIRS}) — "
            f"invariant tests are vacuously true and need updating"
        )

    def test_client_dirs_exist(self) -> None:
        """Every entry in _CLIENT_DIRS must be a real directory."""
        for d in _CLIENT_DIRS:
            assert (_SRC_ROOT / d).is_dir(), (
                f"_CLIENT_DIRS lists '{d}' but {_SRC_ROOT / d} does not exist — "
                f"remove it or the allowlist is silently over-permissive"
            )

    def test_proxy_app_uses_secure_key(self) -> None:
        """proxy/app.py MUST call secure_key to wrap key_buf.

        Without this, the zeroing mechanism tests prove the tool works
        but not that the proxy actually uses it.  An AST scan ensures
        ``secure_key`` appears in a ``with`` statement in the proxy.
        """
        proxy_app = _SRC_ROOT / "proxy" / "app.py"
        assert proxy_app.exists(), "proxy/app.py not found"
        _, tree = _get_cached(proxy_app)

        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.With):
                for item in node.items:
                    call = item.context_expr
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                        if call.func.id == "secure_key":
                            found = True
                            break
            if found:
                break

        assert found, (
            "proxy/app.py does not use 'with secure_key(...)' — "
            "key_buf will not be zeroed after dispatch (SR-02 violation)"
        )

    def test_no_star_import_in_server_modules(self) -> None:
        """No server module uses ``from worthless.crypto import *``.

        crypto/__init__.py re-exports split_key (needed by CLI).  A star import
        in a server module would silently pull it in.
        """
        for py_file in self.server_files:
            _, tree = _get_cached(py_file)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and "crypto" in node.module:
                    star_names = [a.name for a in node.names if a.name == "*"]
                    assert not star_names, (
                        f"{py_file.relative_to(_SRC_ROOT)} uses "
                        f"'from {node.module} import *' — "
                        f"this would pull in split_key, violating invariant #1"
                    )


# ---------------------------------------------------------------------------
# WOR-54 — SR-02: key_buf zeroed after proxy dispatch
# ---------------------------------------------------------------------------


class TestKeyBufZeroedAfterDispatch:
    """SR-02: reconstructed key buffer must be zeroed after upstream dispatch.

    Tests the proxy-style flow: reconstruct → secure_key → dispatch → verify
    zeroing.  The mechanism (secure_key) is unit-tested in test_splitter.py;
    these tests verify the boundary integration.
    """

    def test_key_buf_zeroed_proxy_style_flow(self) -> None:
        """Mimics the proxy's reconstruct → secure_key → dispatch flow.

        Mirrors proxy/app.py lines 330-366: reconstruct, wrap in secure_key,
        simulate an upstream call, then verify zeroing.
        """
        api_key = b"sk-prod-real-key-abcdef1234567890"
        result = split_key(api_key)

        shard_a = bytearray(result.shard_a)
        shard_b = bytearray(result.shard_b)
        commitment = bytearray(result.commitment)
        nonce = bytearray(result.nonce)

        key_buf = reconstruct_key(shard_a, shard_b, commitment, nonce)
        assert bytes(key_buf) == api_key

        dispatched_key: bytes | None = None
        with secure_key(key_buf) as k:
            dispatched_key = bytes(k)

        assert dispatched_key == api_key, "key was correct during dispatch"
        assert_zeroed(key_buf)

        # shard_a is the caller's responsibility (proxy zeros it in finally block)
        assert any(b != 0 for b in shard_a), "shard_a is NOT zeroed by secure_key (caller's job)"

    def test_key_buf_zeroed_on_dispatch_failure(self) -> None:
        """secure_key zeros key_buf even when the upstream call fails."""
        result = split_key(b"sk-test-key-1234567890abcdef")
        key_buf = reconstruct_key(
            result.shard_a, result.shard_b, result.commitment, result.nonce
        )
        ref = key_buf

        with pytest.raises(ConnectionError):
            with secure_key(key_buf):
                raise ConnectionError("simulated upstream failure")

        assert ref is key_buf, "secure_key must not replace the object"
        assert_zeroed(ref)
