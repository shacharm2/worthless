"""Tests for worthless._async.run_sync — the sync-to-async bridge used by
CLI commands that may run inside or outside an event loop."""

from __future__ import annotations

import asyncio
import concurrent.futures

import pytest

from worthless._async import run_sync


class TestRunSyncTimeout:
    """The ``timeout`` parameter on ``run_sync`` was added so callers can cap
    how long a single async roundtrip is allowed to block the sync entry
    point. The plumbing must actually fire when exceeded — otherwise the
    parameter is a footgun (silently ignored)."""

    @pytest.mark.asyncio
    async def test_timeout_in_threaded_path_raises(self) -> None:
        """When called from within a running event loop, run_sync dispatches
        to a worker thread and forwards ``timeout`` to ``Future.result``.
        A coroutine that sleeps longer than the timeout must raise
        ``concurrent.futures.TimeoutError``."""

        async def _sleeper() -> None:
            await asyncio.sleep(5.0)

        with pytest.raises(concurrent.futures.TimeoutError):
            run_sync(_sleeper(), timeout=0.1)
