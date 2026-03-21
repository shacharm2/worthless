# Integrations

## Current External Integrations

### OpenAI API

Files:
- `src/worthless/adapters/openai.py`
- `tests/test_adapters.py`
- `tests/test_streaming.py`

Implemented behavior:
- maps local proxy path `/v1/chat/completions` to `https://api.openai.com/v1/chat/completions`
- injects `Authorization: Bearer <api_key>`
- relays JSON or SSE responses without provider-specific parsing

Status: implemented at adapter level only. No end-to-end upstream caller exists yet.

### Anthropic API

Files:
- `src/worthless/adapters/anthropic.py`
- `tests/test_adapters.py`
- `tests/test_streaming.py`

Implemented behavior:
- maps local proxy path `/v1/messages` to `https://api.anthropic.com/v1/messages`
- injects `x-api-key`
- defaults `anthropic-version` to `2023-06-01` if absent
- relays JSON or SSE responses without provider-specific parsing

Status: implemented at adapter level only. No end-to-end upstream caller exists yet.

### SQLite

Files:
- `src/worthless/storage/schema.py`
- `src/worthless/storage/repository.py`
- `tests/test_storage.py`

Implemented behavior:
- creates `shards` and `metadata` tables
- stores encrypted `shard_b`
- stores provider and crypto verification fields
- enables WAL mode

Status: implemented.

## Internal Integration Boundaries

### Crypto -> Storage

Observed coupling:
- tests convert `SplitResult` into `StoredShard` with `stored_shard_from_split()` in `tests/conftest.py`
- production code does not yet contain an enrollment orchestration layer

Implication:
- the interface between split-key creation and persistence is proven by tests, but not yet embodied in an application service

### Storage -> Proxy

Planned boundary from `.planning/ROADMAP.md`:
- proxy must read encrypted `shard_b`
- rules must run before decryption/reconstruction

Status: missing. No proxy package or service file exists.

### Adapters -> Proxy

Implemented boundary:
- `get_adapter(path)` in `src/worthless/adapters/registry.py`
- common contracts in `src/worthless/adapters/types.py`

Status: ready for a future HTTP proxy handler to consume.

## Expected Missing Integrations

Planned but not present in code:
- FastAPI request/response layer
- upstream HTTP client invocation using reconstructed keys
- OS keychain integration for client-side shard storage
- CLI environment wrapping for SDK transparent routing
- cost/metering integration
- policy/rule evaluation engine

## Sensitive Data Boundaries

### Present in Current Code

- API key bytes enter `split_key()` in `src/worthless/crypto/splitter.py`
- reconstructed key bytes exist transiently in `reconstruct_key()`
- encrypted `shard_b` is written to SQLite by `ShardRepository.store()`

### Explicitly Avoided in Current Code

- `SplitResult.__repr__` and `AdapterRequest.__repr__` redact secrets
- no logging calls exist in the implemented modules
- no prompt/response body inspection exists in the adapter layer

## Integration Maturity Snapshot

- OpenAI adapter: implemented
- Anthropic adapter: implemented
- SQLite persistence: implemented
- Proxy to provider call: missing
- CLI to proxy enrollment: missing
- SDK base URL override path: missing
