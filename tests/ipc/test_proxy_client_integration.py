"""WOR-309 Phase 0 RED skeletons — integration tests against a real sidecar subprocess.

Tests #14-15 from ``.research/04-test-plan.md``. Both spawn the actual
``python -m worthless.sidecar`` binary via the ``subprocess_sidecar``
fixture and exercise the wire protocol end-to-end. Slow — kept tight.

Marked ``integration`` so the default test run can opt in/out per the
pyproject markers list.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_real_sidecar_handshake(subprocess_sidecar) -> None:
    """#14 Spawn the real sidecar; supervisor connects + completes HELLO.

    GREEN: IPCSupervisor connects over the tmp UDS, completes the HELLO
    handshake at protocol v=1, and surfaces ``backend_caps`` matching
    ``("seal", "open", "attest")``.
    """
    raise NotImplementedError("Phase 1 — IPCSupervisor not implemented yet")


async def test_real_sidecar_reconnect_after_sigkill(subprocess_sidecar) -> None:
    """#15 SIGKILL the real sidecar mid-session; restart it; next call succeeds.

    GREEN: kill child by pid; relaunch a fresh sidecar on the same socket;
    issue a second IPC call and assert the supervisor transparently
    rebuilt the connection without falling back to in-process crypto.
    """
    raise NotImplementedError("Phase 1 — reconnect-after-SIGKILL not implemented yet")
