"""Pin the SR-02 (zero key material) contract for ``deploy/start.py``.

``deploy/start.py`` is the single-container Docker entrypoint. It:
    1. reads the fernet key from disk,
    2. ``split_to_tmpfs`` to disk-backed shares,
    3. ``spawn_sidecar``,
    4. ``os.execvp`` to replace itself with uvicorn.

Between steps 1 and 4 the process holds plaintext key material in memory.
SR-02 requires that the bytes be zeroed before the process replaces itself.
``up.py`` has equivalent coverage in ``test_up_with_sidecar.py``; this is
``deploy/start.py``'s parity test.

Regression target: the cleanup commit (24f6e4c) added the zero_buf calls to
match up.py. Without these tests, anyone removing them would only be caught
by reading the diff.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_START = REPO_ROOT / "deploy" / "start.py"


@pytest.fixture
def deploy_start_module():
    """Import ``deploy/start.py`` as a module without invoking ``main()``."""
    spec = importlib.util.spec_from_file_location("_deploy_start_under_test", DEPLOY_START)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_deploy_start_under_test"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("_deploy_start_under_test", None)


def _make_fake_shares(shard_a_seed: bytes, shard_b_seed: bytes) -> MagicMock:
    """Build a ShareFiles-shaped mock with mutable bytearray shards."""
    return MagicMock(
        shard_a=bytearray(shard_a_seed),
        shard_b=bytearray(shard_b_seed),
        run_dir=Path("/tmp/wor-test-deploy-start"),  # noqa: S108
        share_a_path=Path("/tmp/wor-test-deploy-start/share_a.bin"),  # noqa: S108
        share_b_path=Path("/tmp/wor-test-deploy-start/share_b.bin"),  # noqa: S108
    )


class TestDeployStartZeroizesOnSuccess:
    """Happy path: split + spawn succeed; both fernet bytes and shard bytes
    must be zeroed before ``os.execvp`` replaces the process.

    If ``os.execvp`` ran for real these assertions would never execute —
    that's the point: we mock ``execvp`` to a no-op so we can inspect memory
    state at the moment of process replacement.
    """

    def test_fernet_key_zeroed_after_split(
        self, deploy_start_module, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_fernet = bytearray(b"FERNET-PLAINTEXT-KEY-30-BYTES!")
        fake_shares = _make_fake_shares(b"shardA-data", b"shardB-data")
        fake_home = MagicMock(
            fernet_key=fake_fernet,
            base_dir=Path("/tmp/wor-test-home"),  # noqa: S108
        )

        monkeypatch.setattr(deploy_start_module, "ensure_home", lambda _: fake_home)
        monkeypatch.setattr(deploy_start_module, "split_to_tmpfs", lambda _k, _h: fake_shares)
        monkeypatch.setattr(deploy_start_module, "spawn_sidecar", lambda *_a, **_kw: MagicMock())
        monkeypatch.setattr(deploy_start_module.os, "execvp", lambda *_a: None)

        deploy_start_module.main()

        assert fake_fernet == bytearray(len(fake_fernet)), (
            f"SR-02 violation: fernet key bytes not zeroed, got {bytes(fake_fernet)!r}"
        )

    def test_sidecar_socket_env_set_before_execvp(
        self, deploy_start_module, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exec'd uvicorn process MUST see WORTHLESS_SIDECAR_SOCKET in
        its environment, otherwise the proxy can't find its IPC peer and
        refuses to bind. This is the deploy-side analogue of the wrap bug
        that became worthless-r67t."""
        fake_shares = _make_fake_shares(b"a" * 8, b"b" * 8)
        fake_home = MagicMock(
            fernet_key=bytearray(b"x" * 32),
            base_dir=Path("/tmp/wor-test-home"),  # noqa: S108
        )

        env_at_execvp: dict = {}

        def _capture_env_at_execvp(*_a):
            # Snapshot at exec time — that's the env the new process inherits.
            env_at_execvp.update(os.environ)

        monkeypatch.setattr(deploy_start_module, "ensure_home", lambda _: fake_home)
        monkeypatch.setattr(deploy_start_module, "split_to_tmpfs", lambda _k, _h: fake_shares)
        monkeypatch.setattr(deploy_start_module, "spawn_sidecar", lambda *_a, **_kw: MagicMock())
        monkeypatch.setattr(deploy_start_module.os, "execvp", _capture_env_at_execvp)

        deploy_start_module.main()

        assert "WORTHLESS_SIDECAR_SOCKET" in env_at_execvp, (
            "exec'd uvicorn missing WORTHLESS_SIDECAR_SOCKET — proxy can't "
            "find IPC peer, will refuse to bind. Same bug class as worthless-r67t."
        )
        assert env_at_execvp["WORTHLESS_SIDECAR_SOCKET"] == str(
            fake_shares.run_dir / "sidecar.sock"
        ), (
            f"socket path mismatch: env={env_at_execvp['WORTHLESS_SIDECAR_SOCKET']!r}, "
            f"expected={fake_shares.run_dir / 'sidecar.sock'!r}"
        )

    def test_shards_zeroed_before_execvp(
        self, deploy_start_module, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_shares = _make_fake_shares(
            b"\x11\x22\x33\x44\x55\x66\x77\x88",
            b"\x99\xaa\xbb\xcc\xdd\xee\xff\x00",
        )
        fake_home = MagicMock(
            fernet_key=bytearray(b"x" * 32),
            base_dir=Path("/tmp/wor-test-home"),  # noqa: S108
        )
        # Snapshot the bytearray identities so we can assert on the SAME objects.
        shard_a_ref = fake_shares.shard_a
        shard_b_ref = fake_shares.shard_b

        execvp_called: list[bool] = []

        def _record_execvp(*_a):
            # Critical: shards MUST already be zeroed by the time execvp runs.
            execvp_called.append(True)

        monkeypatch.setattr(deploy_start_module, "ensure_home", lambda _: fake_home)
        monkeypatch.setattr(deploy_start_module, "split_to_tmpfs", lambda _k, _h: fake_shares)
        monkeypatch.setattr(deploy_start_module, "spawn_sidecar", lambda *_a, **_kw: MagicMock())
        monkeypatch.setattr(deploy_start_module.os, "execvp", _record_execvp)

        deploy_start_module.main()

        assert execvp_called, "execvp was never reached — main() didn't complete"
        assert shard_a_ref == bytearray(len(shard_a_ref)), (
            f"SR-02: shard_a not zeroed, got {bytes(shard_a_ref)!r}"
        )
        assert shard_b_ref == bytearray(len(shard_b_ref)), (
            f"SR-02: shard_b not zeroed, got {bytes(shard_b_ref)!r}"
        )


class TestDeployStartZeroizesOnSpawnFailure:
    """Failure path: ``spawn_sidecar`` raises after ``split_to_tmpfs``
    succeeded. The cleanup branch must zero shard bytes AND remove the
    on-disk share files before re-raising.
    """

    def test_shards_zeroed_when_spawn_sidecar_raises(
        self, deploy_start_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_shares = MagicMock(
            shard_a=bytearray(b"AAAAA"),
            shard_b=bytearray(b"BBBBB"),
            run_dir=tmp_path / "run",
            share_a_path=tmp_path / "run" / "share_a.bin",
            share_b_path=tmp_path / "run" / "share_b.bin",
        )
        (tmp_path / "run").mkdir()
        fake_shares.share_a_path.write_bytes(b"AAAAA")
        fake_shares.share_b_path.write_bytes(b"BBBBB")

        shard_a_ref = fake_shares.shard_a
        shard_b_ref = fake_shares.shard_b

        fake_home = MagicMock(fernet_key=bytearray(b"f" * 32), base_dir=tmp_path)

        def _failing_spawn(*_a, **_kw):
            raise RuntimeError("simulated sidecar spawn failure")

        monkeypatch.setattr(deploy_start_module, "ensure_home", lambda _: fake_home)
        monkeypatch.setattr(deploy_start_module, "split_to_tmpfs", lambda _k, _h: fake_shares)
        monkeypatch.setattr(deploy_start_module, "spawn_sidecar", _failing_spawn)
        # execvp must never be reached on the failure path.
        execvp_calls: list[tuple] = []
        monkeypatch.setattr(
            deploy_start_module.os,
            "execvp",
            lambda *a: execvp_calls.append(a),
        )

        with pytest.raises(RuntimeError, match="simulated sidecar spawn failure"):
            deploy_start_module.main()

        assert not execvp_calls, "execvp ran despite spawn_sidecar failure"
        assert shard_a_ref == bytearray(len(shard_a_ref)), (
            f"SR-02: shard_a not zeroed on failure path, got {bytes(shard_a_ref)!r}"
        )
        assert shard_b_ref == bytearray(len(shard_b_ref)), (
            f"SR-02: shard_b not zeroed on failure path, got {bytes(shard_b_ref)!r}"
        )
        # Disk-side cleanup: share files unlinked, run dir rmdir'd.
        assert not fake_shares.share_a_path.exists(), "share_a not unlinked"
        assert not fake_shares.share_b_path.exists(), "share_b not unlinked"
        assert not fake_shares.run_dir.exists(), "run_dir not rmdir'd"
