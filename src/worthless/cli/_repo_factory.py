"""Construct a :class:`ShardRepository` honouring WORTHLESS_FERNET_IPC_ONLY.

WOR-465 A3b 3/3 shared helper. Every CLI command that instantiates a
ShardRepository against the real Fernet key (lock, unlock, wrap, doctor,
revoke, default_command) goes through :func:`open_repo` instead of the
old ``ShardRepository(str(home.db_path), home.fernet_key)`` pattern.

When ``WORTHLESS_FERNET_IPC_ONLY=1`` is set the helper opens an
:class:`IPCClient` against the sidecar socket and hands it to the
repository — the calling process NEVER materialises the key. When the
flag is unset, behaviour is identical to the legacy in-process path.

The factory is an async context manager because the IPCClient owns a
Unix-domain socket that must be closed at the end of the command. The
non-IPC branch is a no-op teardown for symmetry. Read-only commands
that use a placeholder key (scan, status) do NOT go through here —
they never touch ``home.fernet_key`` and have nothing to delegate.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from worthless.ipc.client import IPCClient
from worthless.storage.repository import ShardRepository

if TYPE_CHECKING:
    from worthless.cli.bootstrap import WorthlessHome

_FERNET_IPC_ONLY_ENV = "WORTHLESS_FERNET_IPC_ONLY"
_SIDECAR_SOCKET_ENV = "WORTHLESS_SIDECAR_SOCKET"
_DEFAULT_SIDECAR_SOCKET = "/run/worthless/sidecar.sock"


def _flag_on() -> bool:
    """Whitespace-tolerant truthy check; see ``_env_bool`` in proxy.config."""
    return os.environ.get(_FERNET_IPC_ONLY_ENV, "").strip().lower() in ("1", "true", "yes")


@asynccontextmanager
async def open_repo(home: WorthlessHome) -> AsyncIterator[ShardRepository]:
    """Yield a ShardRepository wired for the current trust mode.

    * Flag ON: opens an :class:`IPCClient` against ``WORTHLESS_SIDECAR_SOCKET``
      (default ``/run/worthless/sidecar.sock``) and constructs the
      repository against it. The calling process never reads
      ``home.fernet_key``.
    * Flag OFF (bare-metal): constructs the repository against
      ``home.fernet_key`` exactly as the legacy code did.

    In both modes ``close()`` runs on exit so SR-02 key-zeroing still
    fires for the bare-metal path.
    """
    if _flag_on():
        socket_path = Path(os.environ.get(_SIDECAR_SOCKET_ENV, _DEFAULT_SIDECAR_SOCKET))
        async with IPCClient(socket_path) as client:
            repo = ShardRepository(str(home.db_path), client)
            try:
                yield repo
            finally:
                repo.close()
        return

    repo = ShardRepository(str(home.db_path), home.fernet_key)
    try:
        yield repo
    finally:
        repo.close()
