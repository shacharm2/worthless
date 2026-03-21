# Stack

## Summary

`worthless/` is a small Python 3.12 package with three implemented subsystems:
- `src/worthless/crypto/` for split-key primitives
- `src/worthless/storage/` for encrypted SQLite persistence
- `src/worthless/adapters/` for provider protocol transforms

The planned proxy service and CLI are not in the tree yet.

## Languages and Runtime

- Primary language: Python 3.12
- Packaging: setuptools with `src/` layout in `pyproject.toml`
- Async model: `async`/`await` for storage and response relay paths
- Test runner: `pytest`

## Direct Dependencies

Declared in `pyproject.toml`:

- `cryptography>=46.0.5`
  Used for Fernet encryption of `shard_b` at rest in `src/worthless/storage/repository.py`
- `aiosqlite>=0.22.1`
  Used for async SQLite access in `src/worthless/storage/schema.py` and `src/worthless/storage/repository.py`
- `httpx>=0.28`
  Used as the upstream response type and streaming abstraction in `src/worthless/adapters/types.py`

## Dev and QA Tooling

- `pytest`, `pytest-asyncio`
- `ruff`
- `pytest-benchmark`, `pytest-console-scripts`, `pytest-cov`, `pytest-randomly`, `pytest-timeout`, `pytest-xdist`
- `hypothesis`, `respx`, `syrupy`
- `bandit`, `pip-audit`, `schemathesis`, `semgrep`, `mutmut`
- `atheris`

Only the core unit and async test stack is exercised by the currently checked-in code. The broader QA toolchain is configured but not visibly wired into repo-local scripts yet.

## Implemented Runtime Components

### Crypto

Files:
- `src/worthless/crypto/splitter.py`
- `src/worthless/crypto/types.py`

Implemented capabilities:
- XOR split of API keys into `shard_a` and `shard_b`
- HMAC commitment validation during reconstruction
- explicit in-place zeroing through `bytearray`
- `secure_key()` context manager for bounded key lifetime

### Storage

Files:
- `src/worthless/storage/schema.py`
- `src/worthless/storage/repository.py`

Implemented capabilities:
- SQLite schema creation
- WAL mode enablement
- Fernet encryption of `shard_b` before persistence
- async store/retrieve/list/metadata accessors

### Provider Adapters

Files:
- `src/worthless/adapters/types.py`
- `src/worthless/adapters/openai.py`
- `src/worthless/adapters/anthropic.py`
- `src/worthless/adapters/registry.py`

Implemented capabilities:
- path-based adapter selection
- OpenAI request transformation
- Anthropic request transformation
- hop-by-hop and internal header stripping
- non-streaming response relay
- SSE streaming passthrough via `httpx.Response.aiter_bytes()`

## Not Yet Implemented

Planned in `.planning/ROADMAP.md` but not present in `src/`:

- FastAPI proxy service
- rules engine / gate-before-reconstruct enforcement
- reconstruction service boundary for direct upstream calls
- CLI commands (`enroll`, `wrap`, `status`, `scan`)
- security posture documentation artifact

## Configuration Surfaces

Current repo-local configuration:
- `pyproject.toml` for package, test, lint, and coverage settings
- no checked-in runtime `.env` or service config
- no FastAPI app settings module yet
- no CLI option parsing layer yet

## Security-Relevant Library Choices

- `secrets.token_bytes()` is used for CSPRNG in `src/worthless/crypto/splitter.py`
- `random` is intentionally banned through Ruff `TID251` in `pyproject.toml`
- `cryptography.Fernet` is the only at-rest encryption primitive in the implemented code
- no network server framework is in the implemented package yet
