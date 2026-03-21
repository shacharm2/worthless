# Structure

## Top-Level Layout

- `src/worthless/`
  The only production package currently implemented.
- `tests/`
  Unit and async integration-style tests for the implemented subsystems.
- `.planning/`
  GSD project artifacts: requirements, roadmap, phase plans, summaries, research.
- `.claude/`, `.cursor/`, `.qodo/`, `.tools/`, `.beads/`
  Agent/editor/project workflow support.

## Production Package Layout

### `src/worthless/crypto/`

Files:
- `src/worthless/crypto/__init__.py`
- `src/worthless/crypto/splitter.py`
- `src/worthless/crypto/types.py`

Purpose:
- secret splitting
- reconstruction validation
- memory zeroing helpers

### `src/worthless/storage/`

Files:
- `src/worthless/storage/__init__.py`
- `src/worthless/storage/schema.py`
- `src/worthless/storage/repository.py`

Purpose:
- database schema
- encrypted shard persistence
- metadata persistence

### `src/worthless/adapters/`

Files:
- `src/worthless/adapters/__init__.py`
- `src/worthless/adapters/types.py`
- `src/worthless/adapters/openai.py`
- `src/worthless/adapters/anthropic.py`
- `src/worthless/adapters/registry.py`

Purpose:
- shared adapter contracts
- provider-specific request transforms
- path-based dispatch

### Package root

Files:
- `src/worthless/__init__.py`
- `src/worthless/exceptions.py`

Purpose:
- root namespace
- shared exception definitions

## Test Layout

- `tests/test_splitter.py`
  crypto invariants and zeroing behavior
- `tests/test_storage.py`
  encrypted persistence and metadata behavior
- `tests/test_adapters.py`
  request transform and non-streaming relay behavior
- `tests/test_streaming.py`
  SSE passthrough behavior
- `tests/test_lint.py`
  security lint rule for banning `random`
- `tests/conftest.py`
  shared fixtures for crypto, storage, and adapter layers
- `tests/helpers.py`
  mock streaming response helpers

## Planning Layout

- `.planning/PROJECT.md`
  product intent and scope
- `.planning/ROADMAP.md`
  milestone phases and target behavior
- `.planning/STATE.md`
  current execution status
- `.planning/phases/01-crypto-core-and-storage/`
  completed phase artifacts
- `.planning/phases/02-provider-adapters/`
  completed phase artifacts
- `.planning/research/`
  pre-implementation architecture and stack research

## Naming Patterns

- subsystem directories use nouns: `crypto`, `storage`, `adapters`
- provider modules use provider names: `openai.py`, `anthropic.py`
- storage DTOs use explicit names: `StoredShard`
- data transfer types use `AdapterRequest`, `AdapterResponse`, `SplitResult`

## Missing Structural Areas

No directories currently exist for:
- `src/worthless/proxy/`
- `src/worthless/cli/`
- `src/worthless/reconstruction/`
- `docs/` or runtime security posture docs inside the repo

Those missing directories map directly to the unstarted roadmap phases.
