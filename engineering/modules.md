# Module Map

## Package map

### `src/worthless/cli`

Owns the command-line user experience and local lifecycle orchestration.

Key files:

- `app.py`: Typer entrypoint and command registration
- `default_command.py`: no-argument "magic" flow
- `bootstrap.py`: first-run and local-state setup helpers
- `process.py`: subprocess/proxy lifecycle helpers
- `dotenv_rewriter.py`: `.env` rewriting and restoration helpers
- `keystore.py`: keyring/file-backed local key storage helpers
- `errors.py`: sanitized error handling and debug mode behavior
- `scanner.py` / `key_patterns.py`: detection of candidate API keys
- `commands/*.py`: user-facing subcommands

Command modules currently present:

- `lock.py`
- `unlock.py`
- `scan.py`
- `status.py`
- `wrap.py`
- `up.py`
- `down.py`
- `revoke.py`
- `mcp.py`

### `src/worthless/proxy`

Owns request admission, reconstruction, upstream relay, and post-response accounting.

Key files:

- `app.py`: FastAPI app and catch-all proxy route
- `config.py`: proxy settings and validation
- `rules.py`: request gating logic
- `metering.py`: usage extraction and spend recording
- `errors.py`: provider-compatible error response helpers

### `src/worthless/storage`

Owns encrypted Shard B persistence and schema evolution.

Key files:

- `repository.py`: encrypted shard repository and enrollment data access
- `schema.py`: schema creation and migrations

### `src/worthless/crypto`

Owns split/reconstruct behavior and key-material handling.

Key files:

- `splitter.py`: shard creation, reconstruction, and key handling helpers
- `types.py`: typed wrappers for mutable key material
- `charsets.py`: character set helpers for provider-compatible decoys

### `src/worthless/adapters`

Owns provider-specific request/response behavior.

Key files:

- `openai.py`
- `anthropic.py`
- `registry.py`
- `types.py`

### `src/worthless/mcp`

Owns the optional MCP server integration.

Key files:

- `server.py`

## Active rule set

The current proxy startup wiring enables these rules in order:

1. `SpendCapRule`
2. `TokenBudgetRule`
3. `RateLimitRule`

`TimeWindowRule` exists in code, but it is not currently wired into the active rules engine created in `proxy/app.py`.

## Test surface

The repo has broad test coverage across:

- CLI lifecycle and UX
- proxy behavior and hardening
- storage/crypto invariants
- streaming and metering
- deployment/static configuration
- security properties and adversarial scenarios

For maintainers, tests are part of the module map because many current behavioral guarantees are expressed most clearly in `tests/` rather than public docs.
