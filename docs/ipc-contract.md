# IPC Contract: Proxy ↔ Sidecar

**Status:** Draft (WOR-307 prototype — v1.1)
**Stability:** Wire format frozen for v1.1. Ops may add kinds/fields backward-compatibly.
**Audience:** Proxy IPC client authors, sidecar server authors, v2.0 Rust/MPC reimplementers.

## Design constraint: crypto-primitive-agnostic

The contract names **verbs** (`seal`, `open`, `attest`), never algorithms (`fernet_*`, `aes_*`, `mpc_*`). Ciphertext, evidence, and context are **opaque bytes** to the proxy. This lets v2.0 swap Fernet → MPC (or KMS, or HSM) with **zero proxy IPC client changes**.

Shape is modelled after [Google Tink's `Aead`](https://developers.google.com/tink/aead) and [AWS KMS Encrypt/Decrypt](https://docs.aws.amazon.com/kms/latest/APIReference/API_Encrypt.html).

## Transport

- **Socket:** Unix domain stream socket (`SOCK_STREAM`), path `/var/run/worthless/sidecar.sock` (configurable).
- **Auth:** peer-uid check via `SO_PEERCRED` (Linux) / `getpeereid()` (macOS). Sidecar rejects if peer uid ∉ allowlist.
- **Framing:** length-prefixed binary frames.

## Frame

```
┌─────────────┬──────────────────────────┐
│ length (4B) │ msgpack-encoded envelope │
│  uint32 BE  │     (≤ length bytes)     │
└─────────────┴──────────────────────────┘
```

- **Max frame size:** 1 MiB. Larger → `PROTO` error, connection closed.
- **Serialization:** [msgpack](https://msgpack.org/), `use_bin_type=True`, `raw=False`.

## Envelope

```
{
  "v":    1,                                   // protocol version (uint)
  "id":   <uint64>,                            // request id; response echoes
  "kind": "req" | "resp" | "err",
  "op":   "hello" | "seal" | "open" | "attest",
  "body": { ... op-specific ... }
}
```

## Handshake (once per connection)

Client → Server:
```
req hello { "client_versions": [1] }
```

Server → Client:
```
resp hello { "version": 1, "backend_caps": ["seal","open","attest"] }
```

Server MUST NOT leak backend identity (no `"backend": "fernet"`). Only capabilities.

## Ops

### `seal` — protect plaintext

Req body: `{ "plaintext": <bytes>, "context": <bytes|null> }`
Resp body: `{ "ciphertext": <bytes> }`

- `ciphertext` is opaque: Fernet token, KMS envelope, MPC share bundle — proxy never parses.
- `context` is optional associated data (tenant id, purpose). Fernet backend currently ignores; KMS/MPC backends MAY bind.

### `open` — recover plaintext

Req body: `{ "ciphertext": <bytes>, "context": <bytes|null> }`
Resp body: `{ "plaintext": <bytes> }`

- `context` MUST match the value passed to `seal` or open fails with `BACKEND` error.

### `attest` — prove liveness & identity

Req body: `{ "nonce": <bytes> }`
Resp body: `{ "evidence": <bytes> }`

- `evidence` is opaque: Fernet = HMAC(nonce, key-derived-secret); KMS = signed blob; MPC = share-commitment proof.
- Proxy verifies via backend-specific verifier (lives in proxy, one per backend).

## Errors

Err body: `{ "code": <str>, "message": <str> }`

| Code      | Meaning                                                   |
|-----------|-----------------------------------------------------------|
| `AUTH`    | Peer uid not in allowlist. Connection closed.             |
| `PROTO`   | Frame too large, malformed msgpack, unknown op/kind.      |
| `BACKEND` | Crypto operation failed (bad ciphertext, context mismatch). |
| `TIMEOUT` | Op exceeded server-side deadline.                         |

## No-fallback rule

Sidecar unreachable, dead, or returning `AUTH`/`PROTO`/`TIMEOUT` → proxy returns **HTTP 503** to upstream caller. **Never** falls back to in-process crypto. Enforced in proxy IPC client (WOR-309) and tested in failure matrix (WOR-312).

## Planned files using this contract

| File | Role | Ticket |
|---|---|---|
| `src/worthless/ipc/framing.py` | length-prefix + msgpack codec | WOR-307 |
| `src/worthless/ipc/peercred.py` | Linux/macOS peer-uid auth | WOR-307 |
| `src/worthless/ipc/protocol.py` | envelope types + op enums | WOR-307 |
| `src/worthless/ipc/client.py` | async client (used by proxy) | WOR-307 → WOR-309 |
| `src/worthless/sidecar/server.py` | async server | WOR-307 → WOR-308 |
| `src/worthless/sidecar/backends/base.py` | abstract seal/open/attest | WOR-307 |
| `src/worthless/sidecar/backends/fernet.py` | Fernet backend | WOR-307 → WOR-308 |
| `docker/Dockerfile.sidecar` | single-container, tini + 2 uids | WOR-307 → WOR-310 |
| `tests/ipc/test_framing.py` | codec tests | WOR-307 |
| `tests/ipc/test_peercred.py` | peer-uid auth tests | WOR-307 |
| `tests/ipc/test_roundtrip.py` | end-to-end seal→open | WOR-307 |
| `tests/ipc/test_failure_matrix.py` | sidecar-dies scenarios | WOR-307 → WOR-312 |
| `docs/wor-307-handoff.md` | v2.0 Rust/MPC reuse notes | WOR-307 |

## Versioning

- `envelope.v` bumps for **breaking** wire changes only.
- New ops (`rotate`, `export_public`) added backward-compatibly: server advertises in `backend_caps`.
- Unknown `op` → `PROTO` error with capability list echoed back.
