"""Containment invariants: ``.env`` must live inside ``repo_root``.

Covers:

* Outside-repo path refused by default.
* ``allow_outside_repo=True`` bypasses the gate.
* ``repo_root=None`` skips the check (trusted-caller opt-out).
* ``realpath`` escape via symlinked directory is caught.
* Bind-mount / overlay escapes caught via ``st_dev`` (fsid) comparison.
* Mount-ID mismatch between repo_root and resolved target refused.
"""

from __future__ import annotations

import os
import sys

import pytest

from worthless.cli.errors import UnsafeReason, UnsafeRewriteRefused
from worthless.cli.safe_rewrite import safe_rewrite


def test_refuses_env_outside_repo(tmp_path, in_fake_repo, make_env_file, sha256_of) -> None:
    """An ``.env`` located outside ``repo_root`` is refused by default."""
    outside = make_env_file(tmp_path / "outside" / ".env", b"KEY=v\n")
    baseline = sha256_of(outside)

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        safe_rewrite(
            outside,
            b"A=1\n",
            original_user_arg=outside,
            repo_root=in_fake_repo,
        )

    assert exc_info.value.reason == UnsafeReason.CONTAINMENT
    assert sha256_of(outside) == baseline


def test_accepts_outside_repo_with_override(tmp_path, in_fake_repo, make_env_file) -> None:
    """``allow_outside_repo=True`` bypasses the containment gate."""
    outside = make_env_file(tmp_path / "outside" / ".env", b"KEY=v\n")
    new_content = b"KEY=new\n"

    safe_rewrite(
        outside,
        new_content,
        original_user_arg=outside,
        repo_root=in_fake_repo,
        allow_outside_repo=True,
    )

    assert outside.read_bytes() == new_content


def test_skips_containment_when_repo_root_is_none(tmp_path, make_env_file) -> None:
    """``repo_root=None`` → containment check is not performed."""
    env = make_env_file(tmp_path / ".env", b"KEY=v\n")
    new_content = b"KEY=new\n"

    # Caller explicitly opts out of containment; gate must skip cleanly.
    safe_rewrite(
        env,
        new_content,
        original_user_arg=env,
        repo_root=None,
    )

    assert env.read_bytes() == new_content


def test_refuses_realpath_escape_via_symlinked_directory(
    tmp_path, in_fake_repo, make_env_file, sha256_of
) -> None:
    """A ``repo/inner`` symlinked to ``/outside`` → ``inner/.env`` escapes → refused.

    The ``.env`` under the symlinked-directory resolves (via realpath)
    to outside the repo root. Containment must refuse.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    real_env = make_env_file(outside / ".env", b"KEY=v\n")
    baseline = sha256_of(real_env)

    inner_link = in_fake_repo / "inner"
    os.symlink(str(outside), str(inner_link))

    env_via_link = inner_link / ".env"

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        safe_rewrite(
            env_via_link,
            b"A=1\n",
            original_user_arg=env_via_link,
            repo_root=in_fake_repo,
        )

    assert exc_info.value.reason in {
        UnsafeReason.SYMLINK,
        UnsafeReason.CONTAINMENT,
        UnsafeReason.PATH_IDENTITY,
    }
    assert sha256_of(real_env) == baseline


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="bind-mount escape requires Linux mount namespaces",
)
def test_refuses_bind_mount_escape(tmp_path, in_fake_repo) -> None:
    """A bind-mounted subtree from outside the repo resolves via fsid mismatch.

    We cannot actually bind-mount in an unprivileged CI container, so
    this test exercises the *detection path*: if the resolved target's
    ``statvfs(f_fsid)`` differs from the repo root's, containment must
    refuse regardless of realpath prefix match.

    The implementation is expected to capture repo_root's fsid and
    compare against target's fsid. We assert the refusal but do not
    construct a real bind mount.
    """
    # Build a scenario: repo root on tmpfs 1, .env on tmpfs 2 (simulated).
    # We can't construct this without CAP_SYS_ADMIN; assert the contract
    # shape instead: at minimum, any implementation must not silently
    # accept mismatched fsid.
    env = in_fake_repo / ".env"
    env.write_bytes(b"KEY=v\n")

    # Monkey-patch statvfs to return different fsids for repo_root vs target.
    real_statvfs = os.statvfs
    call_count = {"n": 0}

    def _fake_statvfs(path, *a, **kw):  # noqa: ANN001, ANN003
        call_count["n"] += 1
        result = real_statvfs(path, *a, **kw)

        # Fake a different fsid for the target than for the repo root.
        class _R:
            pass

        r = _R()
        for attr in dir(result):
            if not attr.startswith("_"):
                try:
                    setattr(r, attr, getattr(result, attr))
                except AttributeError:
                    pass
        # First call: repo_root. Second call: target. Differ.
        r.f_fsid = 42 if call_count["n"] == 1 else 43
        return r

    # We assert the contract via normal call; the implementation may or
    # may not use statvfs yet, but the test is RED until the fsid check
    # is wired AND a refusal is raised for mismatched fsids.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "statvfs", _fake_statvfs)

        with pytest.raises(UnsafeRewriteRefused) as exc_info:
            safe_rewrite(
                env,
                b"A=1\n",
                original_user_arg=env,
                repo_root=in_fake_repo,
            )

        assert exc_info.value.reason == UnsafeReason.CONTAINMENT


def test_refuses_mount_id_mismatch(tmp_path, in_fake_repo) -> None:
    """A resolved target on a different filesystem (statvfs fsid differs) → refuse.

    This is the cross-platform version of the bind-mount test: we
    monkeypatch ``os.statvfs`` so the repo root and the target report
    differing ``f_fsid`` values, and assert containment refuses.
    """
    env = in_fake_repo / ".env"
    env.write_bytes(b"KEY=v\n")

    real_statvfs = os.statvfs
    results: list[str] = []

    def _fake_statvfs(path, *a, **kw):  # noqa: ANN001, ANN003
        results.append(str(path))
        real = real_statvfs(path, *a, **kw)

        class _R:
            f_bsize = real.f_bsize
            f_frsize = real.f_frsize
            f_blocks = real.f_blocks
            f_bfree = real.f_bfree
            f_bavail = real.f_bavail
            f_files = real.f_files
            f_ffree = real.f_ffree
            f_favail = real.f_favail
            f_flag = real.f_flag
            f_namemax = real.f_namemax
            # Deliberately differ: first call → 100, second → 200.
            f_fsid = 100 if len(results) == 1 else 200

        return _R()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "statvfs", _fake_statvfs)

        with pytest.raises(UnsafeRewriteRefused) as exc_info:
            safe_rewrite(
                env,
                b"A=1\n",
                original_user_arg=env,
                repo_root=in_fake_repo,
            )

        assert exc_info.value.reason == UnsafeReason.CONTAINMENT
