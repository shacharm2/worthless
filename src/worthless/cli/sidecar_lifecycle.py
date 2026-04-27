"""Sidecar lifecycle helpers (WOR-384 Phase A).

Phase A scope: ``split_to_tmpfs`` plus the ``ShareFiles`` dataclass.
Phases B (spawn), C (shutdown/zeroing), D (up integration), and E land in
follow-up tickets — keep this module narrow.

Security rules touched by Phase A:
- SR-01: shard buffers are ``bytearray`` (mutable, zeroable).
- SR-04: never log share bytes; only log the run-dir path.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from worthless.crypto.splitter import split_key

_logger = logging.getLogger("worthless.cli.sidecar_lifecycle")

# On-disk layout under the Worthless home dir. Phases B (spawn) and C
# (shutdown) import these so the layout has a single source of truth.
_RUN_SUBDIR = "run"
_SHARE_A_NAME = "share_a.bin"
_SHARE_B_NAME = "share_b.bin"


@dataclass
class ShareFiles:
    """Handle returned by :func:`split_to_tmpfs`.

    The bytearrays remain in memory so Phase C (shutdown) can zero them
    after the sidecar terminates. Callers MUST NOT mutate ``shard_a`` or
    ``shard_b`` before shutdown — they back the live shares on disk and
    are also the buffers Phase C will overwrite with zeros.
    """

    share_a_path: Path
    share_b_path: Path
    shard_a: bytearray
    shard_b: bytearray
    run_dir: Path


def _write_share(path: Path, data: bytearray) -> None:
    """Atomically create *path* at mode 0o600 and write *data*.

    Uses ``O_EXCL`` so the file never exists at a wider mode, even
    transiently. Belt-and-braces ``fchmod`` defends against a permissive
    process umask masking the ``0o600`` mode bits passed to ``os.open``.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        written = 0
        while written < len(view):
            written += os.write(fd, view[written:])
    finally:
        os.close(fd)


def split_to_tmpfs(fernet_key: bytearray, home_dir: Path) -> ShareFiles:
    """Split *fernet_key* and write the two shares to ``home_dir/run/<pid>/``.

    The run directory is created at mode ``0o700``. Each share file is
    created atomically at mode ``0o600`` via ``os.O_EXCL`` so the bytes
    never exist on disk at a wider permission. Both files and the run
    dir are owned by the current uid.

    Args:
        fernet_key: The 44-byte fernet key, as a mutable bytearray.
        home_dir: The Worthless home dir (typically ``~/.worthless``).

    Returns:
        A :class:`ShareFiles` handle whose bytearrays the caller is
        expected to keep alive until Phase-C shutdown zeroes them.
    """
    run_dir = home_dir / _RUN_SUBDIR / str(os.getpid())
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    # ``mkdir(mode=...)`` is umask-masked on POSIX; pin explicitly.
    run_dir.chmod(0o700)

    result = split_key(bytes(fernet_key))
    shard_a = result.shard_a
    shard_b = result.shard_b

    share_a_path = run_dir / _SHARE_A_NAME
    share_b_path = run_dir / _SHARE_B_NAME
    _write_share(share_a_path, shard_a)
    _write_share(share_b_path, shard_b)

    # SR-04: log only the run dir path, never share bytes.
    _logger.debug("split_to_tmpfs: wrote shares under run_dir=%s", run_dir)

    return ShareFiles(
        share_a_path=share_a_path,
        share_b_path=share_b_path,
        shard_a=shard_a,
        shard_b=shard_b,
        run_dir=run_dir,
    )
