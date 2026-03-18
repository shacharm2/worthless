# Architecture

## As-Built Architecture

The current repository is a bottom-up implementation of the Worthless design rather than a running product. The shipped code forms three reusable layers:

1. crypto primitives
2. encrypted shard persistence
3. provider protocol adapters

There is no top-level application service yet.

## Implemented Components

### 1. Crypto Core

Files:
- `src/worthless/crypto/splitter.py`
- `src/worthless/crypto/types.py`
- `src/worthless/exceptions.py`

Responsibilities:
- split a plaintext API key into two XOR shards
- bind shards to an HMAC commitment and nonce
- reconstruct only after commitment verification
- zero secret material on scope exit

Important detail:
- all secret-bearing structures are `bytearray`-based so zeroing is possible

### 2. Encrypted Shard Storage

Files:
- `src/worthless/storage/schema.py`
- `src/worthless/storage/repository.py`

Responsibilities:
- initialize SQLite state
- encrypt `shard_b` at rest
- retrieve decrypted server-side shard material when asked
- persist non-secret metadata

Important detail:
- repository methods each open their own connection, which is acceptable for the current PoC scale but explicitly a production concern

### 3. Provider Adapter Layer

Files:
- `src/worthless/adapters/types.py`
- `src/worthless/adapters/openai.py`
- `src/worthless/adapters/anthropic.py`
- `src/worthless/adapters/registry.py`

Responsibilities:
- choose provider implementation by path
- normalize outbound headers
- inject provider auth headers
- relay upstream responses
- support streaming via raw byte passthrough

Important detail:
- the adapters are intentionally stateless transformers; they do not know about storage or crypto

## Missing Runtime Layers

Planned in `.planning/ROADMAP.md` but absent from `src/`:

### 4. Proxy Service

Expected responsibilities:
- accept local client traffic
- identify provider from path
- enforce gate-before-reconstruct
- reconstruct key only after rules pass
- call upstream and return response

Status: missing.

### 5. CLI and Enrollment Layer

Expected responsibilities:
- accept user API keys securely
- split and persist the correct shard
- store client-side shard locally
- set environment variables or base URLs for transparent routing

Status: missing.

### 6. Security Posture Surface

Expected responsibilities:
- explicitly document what the Python PoC guarantees
- distinguish PoC memory semantics from future Rust hardening

Status: missing.

## Data Flow Today

The code supports only subsystem-level flows:

### Crypto flow

`plaintext key` -> `split_key()` -> `SplitResult(shard_a, shard_b, commitment, nonce)` -> `reconstruct_key()` -> `secure_key()`

### Storage flow

`StoredShard` -> `ShardRepository.store()` -> SQLite `shards` table -> `ShardRepository.retrieve()`

### Adapter flow

`path` -> `get_adapter()` -> `prepare_request()` -> upstream `httpx.Response` -> `relay_response()`

These flows are isolated and tested, but not yet composed into a single runtime.

## Intended Full Architecture

The intended system can already be inferred from the roadmap and research docs:

`client app` -> `local proxy/CLI surface` -> `policy gate` -> `retrieve encrypted shard_b` -> `reconstruct key` -> `provider adapter` -> `OpenAI/Anthropic`

Current implementation status by segment:
- client app integration: missing
- CLI/local proxy surface: missing
- policy gate: missing
- retrieve encrypted shard_b: implemented
- reconstruct key: implemented
- provider adapter: implemented
- upstream network call: missing

## Core Architectural Invariants

Sourced from `.planning/PROJECT.md` and `.planning/ROADMAP.md`:

1. client-side splitting
2. gate before reconstruction
3. server-side direct upstream call

Current status:
- invariant 1 is partially embodied by the crypto code, but not by a user-facing enrollment flow
- invariant 2 is not implemented because no proxy/rules engine exists
- invariant 3 is not implemented because no upstream calling service exists

## Immediate Architectural Next Step

Phase 3 should introduce an application layer that composes:
- `ShardRepository`
- `reconstruct_key()` and `secure_key()`
- `get_adapter()`
- request gating and metering

That phase is where the repo transitions from validated subsystems into an actual working product.
