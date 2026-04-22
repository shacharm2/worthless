"""Special-file invariants: FIFO, /dev/null, /proc, AF_UNIX socket, char device.

Any non-regular file at the target path must be refused before any
``open`` (let alone write) happens. Catches adversarial symlink swaps
that point at device nodes or IPC endpoints.
"""

from __future__ import annotations

import os
import shutil
import socket
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from worthless.cli.errors import UnsafeReason, UnsafeRewriteRefused
from worthless.cli.safe_rewrite import safe_rewrite


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="special-file semantics are POSIX-only",
)


def test_refuses_fifo(tmp_path) -> None:
    """A named pipe at the target path is refused.

    Reading from a FIFO blocks until a writer attaches; we must refuse
    on the lstat gate, never open it.
    """
    fifo = tmp_path / ".env"
    os.mkfifo(str(fifo))
    assert stat.S_ISFIFO(os.lstat(str(fifo)).st_mode)

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        safe_rewrite(fifo, b"A=1\n", original_user_arg=fifo)

    assert exc_info.value.reason in {
        UnsafeReason.SPECIAL_FILE,
        UnsafeReason.PATH_IDENTITY,
    }
    # FIFO still there, no tmp leak.
    assert stat.S_ISFIFO(os.lstat(str(fifo)).st_mode)
    assert list(tmp_path.glob(".env.tmp-*")) == []


def test_refuses_dev_null(tmp_path) -> None:
    """A symlink at ``.env`` pointing to ``/dev/null`` is refused.

    Writing would silently succeed and corrupt nothing, but that's a
    feature, not a gate. We refuse so the caller gets a clean error
    instead of an illusory "success".
    """
    env_link = tmp_path / ".env"
    env_link.symlink_to("/dev/null")

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        safe_rewrite(env_link, b"A=1\n", original_user_arg=env_link)

    assert exc_info.value.reason in {
        UnsafeReason.SYMLINK,
        UnsafeReason.SPECIAL_FILE,
        UnsafeReason.PATH_IDENTITY,
    }


@pytest.mark.skipif(
    not Path("/proc/self/environ").exists(),
    reason="/proc/self/environ not present (likely macOS)",
)
def test_refuses_proc_self_environ(tmp_path) -> None:
    """A symlink at ``.env`` pointing to ``/proc/self/environ`` is refused.

    ``/proc/self/environ`` is world-readable on Linux and reveals the
    caller's environment including secrets. Refuse on symlink gate.
    """
    env_link = tmp_path / ".env"
    env_link.symlink_to("/proc/self/environ")

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        safe_rewrite(env_link, b"A=1\n", original_user_arg=env_link)

    assert exc_info.value.reason in {
        UnsafeReason.SYMLINK,
        UnsafeReason.SPECIAL_FILE,
        UnsafeReason.PATH_IDENTITY,
    }


def test_refuses_af_unix_socket(tmp_path) -> None:
    """An AF_UNIX socket at the target path is refused.

    lstat reports ``S_IFSOCK``; our special-file gate must catch it.

    Note: AF_UNIX sun_path is capped at 104 bytes on macOS, which
    pytest-xdist worker tmp dirs routinely exceed. Bind the socket
    inside a short-path tmp directory (``tempfile.gettempdir()``
    honors ``$TMPDIR`` and falls back to ``/tmp``) so ``sock.bind()``
    doesn't fail before we ever reach ``safe_rewrite``.
    """
    short_dir = tempfile.mkdtemp(prefix="sr-")
    sock_path = Path(short_dir) / ".env"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(sock_path))
        assert stat.S_ISSOCK(os.lstat(str(sock_path)).st_mode)

        with pytest.raises(UnsafeRewriteRefused) as exc_info:
            safe_rewrite(sock_path, b"A=1\n", original_user_arg=sock_path)

        assert exc_info.value.reason in {
            UnsafeReason.SPECIAL_FILE,
            UnsafeReason.PATH_IDENTITY,
        }
    finally:
        sock.close()
        shutil.rmtree(short_dir, ignore_errors=True)


def test_refuses_character_device_via_symlink(tmp_path) -> None:
    """A symlink pointing at ``/dev/tty`` (or ``/dev/zero``) is refused.

    We cannot mknod a character device in a test sandbox without root,
    so we exercise the same code path via a symlink to an existing
    char-device node. The symlink gate fires first regardless.
    """
    target = "/dev/zero" if Path("/dev/zero").exists() else "/dev/null"
    env_link = tmp_path / ".env"
    env_link.symlink_to(target)

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        safe_rewrite(env_link, b"A=1\n", original_user_arg=env_link)

    assert exc_info.value.reason in {
        UnsafeReason.SYMLINK,
        UnsafeReason.SPECIAL_FILE,
        UnsafeReason.PATH_IDENTITY,
    }
