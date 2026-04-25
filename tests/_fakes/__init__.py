"""Test doubles for WOR-309 proxy/sidecar test surface.

The fakes here mirror the public surface of production classes so tests
can inject them as ``app.state.ipc_supervisor`` (or wherever the seam is)
without spinning up a real subprocess sidecar.

Constraints:
* Honour SR-01 — return :class:`bytearray` for plaintext (never bytes).
* Match :class:`worthless.proxy.ipc_supervisor.IPCSupervisor` public surface
  exactly so production code can't tell the difference at runtime.
"""
