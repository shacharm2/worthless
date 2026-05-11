"""Run an async coroutine to completion from a sync caller, regardless of
whether an event loop is already running.

Plain ``asyncio.run`` raises ``RuntimeError`` when invoked from a context
that already has a running loop (MCP server lazy bootstrap, pytest-asyncio
embeddings, etc). This helper probes for a live loop and tunnels through
a thread when one is found, so sync entry points that need a single async
roundtrip do not crash inside reusable code.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


def run_sync(coro: Awaitable[T], timeout: float | None = None) -> T:
    """Drive ``coro`` to completion and return its result.

    If the calling thread already has a running event loop, the coroutine
    is dispatched to a one-shot ``ThreadPoolExecutor`` worker (which gets
    its own fresh loop via ``asyncio.run``); otherwise ``asyncio.run`` is
    invoked directly on the current thread.

    This function is thread-safe; concurrent callers each get their own
    worker thread and event loop.

    ``timeout`` is forwarded to ``Future.result()`` in the threaded path;
    it has no effect in the direct path (use ``asyncio.wait_for`` inside
    the coroutine for that case). Raises ``concurrent.futures.TimeoutError``
    if exceeded — the caller returns immediately; the worker thread continues
    running until the coroutine finishes naturally (Python cannot kill
    threads). For CLI callers this is fine because the process is about to
    exit; long-running services should not rely on ``timeout`` to reclaim
    resources from runaway coroutines.

    Exceptions propagate to the caller unchanged.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(asyncio.run, coro).result(timeout=timeout)  # type: ignore[arg-type]
    finally:
        pool.shutdown(wait=False)
