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


class TestRunSyncAdversarial:
    """Probes for hostile coroutines and re-entry patterns. The threaded
    path uses a single-worker ThreadPoolExecutor scoped to the call — the
    worker thread must not survive the call or leak across invocations,
    and re-entry from inside a driven coroutine must not deadlock."""

    @pytest.mark.asyncio
    async def test_caller_recovers_when_coroutine_ignores_cancellation(self) -> None:
        """If a coroutine swallows ``CancelledError`` and keeps running, the
        ``Future.result(timeout=…)`` call still returns control to the
        caller as ``concurrent.futures.TimeoutError``. The dangling work
        is the coroutine's problem, not the caller's — what matters is the
        caller does not block forever past the declared timeout."""
        import time

        async def _stubborn() -> str:
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                await asyncio.sleep(5.0)
            return "done"

        start = time.monotonic()
        with pytest.raises(concurrent.futures.TimeoutError):
            run_sync(_stubborn(), timeout=0.1)
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, (
            f"run_sync caller blocked for {elapsed:.2f}s past timeout — "
            "stubborn coroutine pinned the caller thread"
        )

    def test_reentrant_run_sync_does_not_deadlock(self) -> None:
        """A coroutine driven by run_sync may itself call run_sync (e.g. via
        a sync helper that internally bridges to async). The outer call has
        no running loop, so it uses asyncio.run; the inner call sees a
        running loop and tunnels through a fresh ThreadPoolExecutor. Both
        must complete without deadlock."""

        async def _inner() -> int:
            return 42

        async def _outer() -> int:
            return run_sync(_inner())

        result = run_sync(_outer())
        assert result == 42
