# Conventions

## Code Style

Observed from `src/` and `tests/`:

- type annotations are used consistently
- modules carry short file-level docstrings
- public functions and methods have concise docstrings
- line length is set to 100 in `pyproject.toml`
- import style is straightforward and explicit

## Domain Conventions

### Secret handling

- secret-bearing buffers prefer `bytearray` over `bytes` when mutation/zeroing is required
- representations redact secrets:
  - `SplitResult.__repr__`
  - `AdapterRequest.__repr__`
- crypto zeroing funnels through `_zero_buf()` in `src/worthless/crypto/types.py`

### Header handling

- headers are normalized to lowercase in `strip_internal_headers()` in `src/worthless/adapters/types.py`
- internal headers use `x-worthless-` prefix
- hop-by-hop proxy headers are stripped before forwarding upstream

### Adapter design

- adapters are stateless
- shared behavior is centralized in `src/worthless/adapters/types.py`
- dispatch is path-based via `get_adapter()`
- streaming is treated as a transport concern, not parsed into higher-level events

### Storage design

- repository pattern is used instead of exposing SQL directly to callers
- `StoredShard` is a narrow tuple-like return type
- metadata uses generic key/value semantics for now

## Testing Conventions

- tests are grouped by requirement comments such as `CRYP-01`, `STOR-01`, `PROX-03`
- async tests use `pytest.mark.asyncio`
- fixtures in `tests/conftest.py` are shared across subsystems
- helper abstractions are minimal and local to tests

## Security Conventions

- `random` is banned by Ruff with an explicit message in `pyproject.toml`
- `secrets.token_bytes()` is used for randomness
- adapter `repr` and split result `repr` are expected not to leak sensitive data
- no logging layer is currently present in the production code

## Current Design Biases

- bottom-up subsystem development before top-level application orchestration
- simple PoC-first abstractions over production-grade service wiring
- explicitness over framework indirection

## Places Where Conventions Are Not Yet Established

Because the proxy and CLI do not exist yet, the repo does not yet establish conventions for:
- HTTP routing layout
- application service boundaries
- configuration loading
- CLI command organization
- structured logging
- metrics and observability
