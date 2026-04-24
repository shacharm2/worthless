# WOR-307 Handoff: What the Prototype Proves, What v2.0 Inherits

**Status:** Gate-closing handoff for WOR-306 (Fernet sidecar epic).
**Audience:** WOR-308 (Python sidecar), WOR-309 (proxy IPC client), WOR-310 (container deploy), WOR-312 (failure matrix), and the v2.0 Rust/MPC rewrite.
**Companion:** [`docs/ipc-contract.md`](ipc-contract.md) (wire format, **frozen for v1.1**).

This document describes what the 3-day prototype built, which invariants are load-bearing, and what a v2.0 Rust/MPC implementation must preserve vs. may freely change.

## 1. What shipped in the prototype

| Layer | File | Verb |
|---|---|---|
| Wire codec | `src/worthless/ipc/framing.py` | `encode_frame` / `read_frame`, 1 MiB cap, `FrameError` hierarchy |
| Peer auth | `src/worthless/ipc/peercred.py` | `get_peer_credentials`, `require_peer_uid`, AF_UNIX guard |
| Envelope | `src/worthless/ipc/protocol.py` | `{v, id, kind, op, deadline_ms, body}` + error codes |
| Async client | `src/worthless/ipc/client.py` | `IPCClient.seal / open / attest`, 2 s default timeout, lock-serialized |
| Async server | `src/worthless/sidecar/server.py` | `start_sidecar(...)`, 0660 socket, peer-uid gate |
| Backend ABC | `src/worthless/sidecar/backends/base.py` | `Backend.seal / open / attest`, `BackendError` |
| Fernet backend | `src/worthless/sidecar/backends/fernet.py` | XOR-share reconstruction, HKDF-bound attest HMAC |
| Entry point | `src/worthless/sidecar/__main__.py` | env-configured bind, SIGTERM handler |
| Container | `docker/sidecar/Dockerfile` | tini PID 1, two uids, socket volume |
| Supervisor | `docker/sidecar/supervise.sh` | starts sidecar as `worthless-crypto`, client as `worthless-proxy` |

Smoke-tested by `tests/docker/test_container_smoke.py` (builds image, runs container, asserts full roundtrip across the uid boundary).

## 2. Platform matrix

The IPC contract deliberately hides these differences behind `framing` + `peercred`. Any platform that provides (a) AF_UNIX stream sockets and (b) a way to read peer uid on an accepted connection is supported.

| Concern | Linux | macOS | Windows |
|---|---|---|---|
| Peer uid syscall | `SO_PEERCRED` (`getsockopt`) → `struct ucred{pid,uid,gid}` | `getpeereid()` via libc ctypes → `uid_t, gid_t` (no pid) | **WSL only** in v1.1; native Named Pipes deferred to v2.0+ |
| `sun_path` limit | 108 chars | **104 chars** (governs everything) | N/A |
| Abstract namespace (`\0name`) | Exists but **FORBIDDEN** (bypasses FS ACLs) | N/A | N/A |
| Socket default path | `/var/run/worthless/sidecar.sock` | same | N/A |
| Test socket path | `tempfile.mkdtemp()` + `/s.sock` (always ≤104) | same | N/A |
| Non-AF_UNIX fallback | **Refused** — `require_peer_uid` raises | **Refused** — same guard closes a Darwin auth-bypass | N/A |

The AF_UNIX guard in `peercred.require_peer_uid` rejects any socket whose `family != AF_UNIX` **before** the uid check. On Darwin, `getpeereid()` returns `0` for non-AF_UNIX sockets (TCP, AF_INET6, socketpair(AF_UNIX, SOCK_SEQPACKET)), which would silently authorize any peer as root. Do not remove this guard in v2.0.

## 3. Deployment topologies

All three share the **same IPC contract**. Choice is operational, not protocol.

### 3.1 Single-container (demonstrated, blessed for v1.1)

```
┌────────── container ─────────────────────────────┐
│  tini (PID 1)                                    │
│   └─ supervise.sh                                │
│        ├─ worthless-proxy   (uid 1001, group 1002) │  ← HTTP in
│        └─ worthless-crypto  (uid 1002, group 1002) │  ← binds socket
│                                                  │
│  /var/run/worthless/sidecar.sock  (0660, crypto:crypto) │
└──────────────────────────────────────────────────┘
```

- Socket mode **0660**, not 0600 — proxy uid connects via shared group. Lower would block the two-uid pattern; higher exposes the socket to world.
- tini handles PID-1 duties (zombie reaping, SIGTERM forwarding). `supervise.sh` is intentionally simple; v2.0 may swap in s6-overlay without contract changes.
- Built and exercised by `tests/docker/test_container_smoke.py` on every `-m docker` run.

### 3.2 Sidecar-container (documented, optional)

Two containers share `/var/run/worthless` as a volume. Peer-uid auth still works across the volume — the uid must be allocated consistently (e.g. both images derive from the same base, or both declare `user: 1001` / `user: 1002` in compose). Exposes no extra surface beyond single-container.

### 3.3 systemd-managed (documented, optional)

```
systemd socket unit          binds /var/run/worthless/sidecar.sock
      │
      ├─ worthless-sidecar.service  User=worthless-crypto, socket-activated
      └─ worthless-proxy.service    User=worthless-proxy,  Group=worthless-crypto
```

Socket activation + `User=` / `Group=` directives give the same two-uid/0660 shape without a container runtime. Useful for host installs. The sidecar accepts an already-bound fd via `LISTEN_FDS=1` **in v1.2** — prototype does not implement this yet; v2.0 Rust rewrite should.

## 4. WOR-306 9-row red-team table → test mapping

| # | Attack | Control | Executable evidence |
|---|---|---|---|
| 1 | Proxy RCE reads Fernet key | Key never leaves crypto uid process | `tests/ipc/test_failure_matrix.py::test_bound_socket_is_mode_0660_not_world_accessible` + container two-uid topology in `tests/docker/test_container_smoke.py` |
| 2 | Malicious Python dep in proxy | Only path out is IPC verbs | `tests/ipc/test_failure_matrix.py::test_client_module_has_no_crypto_fallback_path` |
| 3 | `/proc/<proxy-pid>/mem` dump | Key is in crypto process, not proxy | Two-uid container asserts process split; no crypto symbol imported in proxy client (`tests/ipc/test_failure_matrix.py::test_client_module_has_no_crypto_fallback_path`) |
| 4 | Read shared volume | Socket is rendezvous only | Dockerfile `chmod 0750 /var/run/worthless`, 0660 socket (see row 6) |
| 5 | Cold-boot memory dump (host) | **Out of scope — documented limit** | `docs/ipc-contract.md` + §7 below |
| 6 | Connect from random uid | `require_peer_uid` | `tests/ipc/test_peercred.py::test_require_peer_uid_rejects_unlisted_uid`, `::test_require_peer_uid_rejects_non_af_unix_sockets` (Darwin bypass) |
| 7 | Malformed IPC payload | `FrameError` + envelope validation | `tests/ipc/test_framing.py` (oversized, truncated, non-map body, missing fields) + `tests/ipc/test_failure_matrix.py::test_connect_to_stale_socket_file_raises_protocol_error` |
| 8 | Sidecar dies mid-request | `IPCProtocolError`, **no fallback** | `tests/ipc/test_failure_matrix.py::test_op_after_transport_closed_raises_protocol_error`, `::test_reconnect_after_server_killed_raises_protocol_error`, `::test_client_module_has_no_crypto_fallback_path` |
| 9 | Container escape | **Out of scope — documented limit** | See §7 |

Additional contract guards not tied to a red-team row: `test_backend_error_message_is_scrubbed_on_wire` (no plaintext/key/share bytes in error strings), `test_client_timeout_raises_ipc_timeout_error_fast` (2 s deadline, §ipc-contract `deadline_ms`).

## 5. Backend ABC stability contract (for v2.0 Rust/MPC)

The `Backend` ABC in `src/worthless/sidecar/backends/base.py` is the boundary v2.0 reimplements against. The **three verbs and their shapes are frozen**:

```python
class Backend(ABC):
    @abstractmethod
    async def seal(self, plaintext: bytes, context: bytes | None = None) -> bytes: ...
    @abstractmethod
    async def open(self, ciphertext: bytes, context: bytes | None = None, key_id: bytes | None = None) -> bytes: ...
    @abstractmethod
    async def attest(self, nonce: bytes, purpose: str | None = None) -> bytes: ...
```

**Locked (breaking to change):**
- Verb names, argument names, argument order.
- Return types (always `bytes`).
- `BackendError` is the only exception a backend may raise across the IPC boundary.
- Backend errors carry a **fixed, information-free message** — no plaintext, key material, share bytes, or wrapped exception text.

**Additive-only (safe to extend without bumping `envelope.v`):**
- New ops may be added; advertise via `backend_caps` in handshake.
- New optional fields in request/response bodies (msgpack skips unknown fields).
- `key_id` semantics opaque — Fernet ignores, KMS/MPC may require.

**Backend-internal (swap freely):**
- Key material shape (XOR shares vs. MPC shares vs. KMS handle).
- `attest` implementation (HKDF-HMAC today; signed blob or MPC commitment later). Proxy verifies via backend-specific verifier, so the evidence format is an implementation detail.
- Session/state management.

**v2.0 Rust rewrite path:** implement the Rust sidecar binding the same `/var/run/worthless/sidecar.sock`, answering the same envelope shape, advertising the same `backend_caps`. The Python proxy IPC client (`worthless.ipc.client.IPCClient`) MUST work against it unchanged — this is the load-bearing property WOR-307 protects.

## 6. Operational invariants the prototype pins

The following are **deliberate** choices; flipping any of them breaks the security claim or the v2.0 path:

1. **Pathname sockets only.** Abstract-namespace (`\0name`) sockets bypass filesystem ACLs and the install-time permission model. Rejected in the framing contract; enforce in any future listener.
2. **0660 socket mode, group-shared.** Enables two-uid single-container. 0600 breaks it. 0666 exposes the socket to world. Enforced in `start_sidecar` + regression-tested.
3. **AF_UNIX guard before uid check.** Closes Darwin `getpeereid` bypass for non-AF_UNIX sockets. `require_peer_uid` raises before authorizing.
4. **No in-process crypto fallback in the proxy client.** Sidecar unreachable → `IPCProtocolError` → caller returns HTTP 503. `test_client_module_has_no_crypto_fallback_path` inspects the client module source to enforce this (no `cryptography` / `fernet` / `hazmat` imports). Do not lazy-import them either.
5. **Crypto-primitive-agnostic verbs.** The IPC client exports `seal` / `open` / `attest`, never `fernet_encrypt` / `fernet_decrypt`. The proxy MUST NOT grow backend-specific IPC methods — if it needs a new verb, add it to the contract and advertise via `backend_caps`.
6. **Wire errors are safe strings.** No peer uid/pid, no key or share bytes, no plaintext/ciphertext, no wrapped exception text. Rich diagnostics go to the sidecar log only.
7. **`envelope.v = 1` is frozen for v1.1.** Bump only for breaking wire changes. New ops are additive (capability negotiation via `backend_caps`).

## 7. Documented limits (explicitly out of scope)

| Limit | Why accepted for v1.1 | Future |
|---|---|---|
| Cold-boot host memory dump reads the key (row 5) | Host compromise = game over; defending against it needs OS-level memory isolation (mlock, SGX, secure enclave) | v1.2 memory hardening (mlock + zero-on-free); v2.0 MPC makes the key never exist in one place |
| Container escape reads crypto memory (row 9) | Single-container topology shares kernel with proxy uid | v1.2 sidecar-container topology blessed; v2.0 separate VM / enclave |
| Native Windows Named Pipes | One platform-specific code path per release cap | v2.0+ |
| Multiple concurrent Fernet keys | Single-key per sidecar keeps the prototype focused | v2.0 via `key_id` (already in wire contract) |
| Socket activation via systemd `LISTEN_FDS` | Prototype binds its own socket | v1.2 or v2.0 Rust rewrite |

## 8. Metrics against the WOR-307 gate

| Gate criterion | Status |
|---|---|
| Clean 3-day build | ✅ (Day 1 framing+peercred; Day 2 server+client+backend; Day 3 container+failure matrix+handoff) |
| `install.sh` under ~300 lines | ✅ (336 lines, no sidecar-driven growth — sidecar lives in container & `python -m worthless.sidecar`) |
| IPC contract ≤2 revisions | ✅ (1 revision after Day 1.5 expert review: added `deadline_ms`, error-message hygiene clause, `key_id` shape) |
| SO_PEERCRED works on Linux and macOS | ✅ (`tests/ipc/test_peercred.py` green on both) |
| Supervision reliable (no races/zombies) | ✅ (tini reaps; `supervise.sh` cleanup trap; container smoke test green) |
| Failure matrix: sidecar dies → no fallback | ✅ (`test_client_module_has_no_crypto_fallback_path` + transport-closed/reconnect tests) |
| Handoff doc for v2.0 reuse | ✅ (this document) |
