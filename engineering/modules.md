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

---

### `src/worthless/proxy`

Owns request admission, reconstruction, upstream relay, and post-response accounting. Async HTTPX reverse proxy. Receives requests, evaluates the rules engine, reconstructs the key, forwards to the upstream LLM, and streams the response. This is the trust boundary between client and upstream.

Key files:

- `app.py`: FastAPI app and catch-all proxy route
- `config.py`: proxy settings and validation
- `rules.py`: request gating logic
- `metering.py`: usage extraction and spend recording
- `errors.py`: provider-compatible error response helpers

#### Security invariants

- SR-03: the rules engine evaluates every request before XOR reconstruction — budget, rate, and allowlist checks happen first, without exception.
- SR-02: the reconstructed key buffer is zeroed immediately after the upstream httpx dispatch completes.
- SR-04: `Authorization` and `x-api-key` headers are masked in all outbound logs — they must never appear in plaintext.
- SR-06: the proxy runs in an isolated subprocess spawned by `cli wrap`.

#### Sensitive data flows

- Storage shards retrieved via `storage.retrieve()` flow into `crypto.reconstruct_key()`. SR-03 requires the rules engine to pass before this call is made.
- The reconstructed key flows directly into the httpx upstream request. SR-02 requires the buffer to be zeroed immediately after `httpx.send()`.
- The `Authorization` header flows from the inbound request. SR-04 and SR-05 require masking before it reaches any log call.

#### Edit rules

- Never call `crypto/` symbols directly other than at the existing `reconstruct_key` call site.
- Run `gitnexus_impact({target: 'proxy_request', direction: 'upstream'})` before editing `app.py`.
- New rules belong in the rules engine chain — never inline logic in `proxy_request`.
- All database and HTTP calls must be awaited — no sync-in-async patterns.

#### Tests

- `tests/test_proxy.py`
- `tests/test_rules.py`
- `tests/test_contract.py`

---

### `src/worthless/storage`

Owns encrypted Shard B persistence and schema evolution. `aiosqlite`-backed shard and enrollment registry. Persists Fernet-encrypted shards, enrollment records, token usage, and decoy hashes. All repository methods are async.

Key files:

- `repository.py`: encrypted shard repository and enrollment data access
- `schema.py`: schema creation and migrations

#### Security invariants

- SR-01: shard data is stored as `bytearray`, not `bytes` — this is enforced at the type boundary.
- SR-04: `EncryptedShard`, `EnrollmentRecord`, and `StoredShard` `__repr__` methods must redact sensitive fields — raw bytes must not appear in log output.
- The Fernet encryption key is loaded from the OS keyring — never from `.env` in production.
- `zero()` must be called before a `ShardRepository` instance goes out of scope.

#### Sensitive data flows

- `store()` shard bytes flow into SQLite. They are Fernet-encrypted before the write. SR-01 requires they be held as `bytearray` throughout.
- `retrieve()` returns decrypted shards that flow into `crypto.reconstruct_key()`. SR-02 requires the consumer to zero the buffer after use.
- Any dataclass `repr` output flows to loggers. SR-04 requires bytes fields to be redacted before they can reach any log call.

#### Edit rules

- All new repository methods must be async.
- Run `gitnexus_impact({target: 'ShardRepository', direction: 'upstream'})` before making schema changes.
- Schema migrations go into `migrate_db()` — do not alter existing `CREATE TABLE` statements.
- Every new dataclass must override `__repr__` to redact bytes fields (SR-04).

#### Tests

- `tests/test_storage.py`
- `tests/test_cli_lock.py`
- `tests/test_rules.py`

---

### `src/worthless/crypto`

Owns split/reconstruct behavior and key-material handling. This is the most security-sensitive module. Pyright strict mode is enforced throughout.

Key files:

- `splitter.py`: shard creation, reconstruction, and key handling helpers
- `types.py`: typed wrappers for mutable key material
- `charsets.py`: character set helpers for provider-compatible decoys

#### Security invariants

- SR-01: all key material is held as `bytearray` — `str` or `bytes` types are prohibited for key buffers.
- SR-02: `key_buf[:] = b'\x00' * len(key_buf)` is executed before any return from reconstruction.
- SR-07: `hmac.compare_digest` is the only permitted comparison for digest bytes — the `==` operator is banned.
- SR-08: `secrets.token_bytes()` is the only permitted CSPRNG — the `random` module is banned (CRYP-04).
- `split_key` is client-side only. This invariant is enforced by `tests/test_invariants.py` and must never be called from `proxy/` or `adapters/`.

#### Sensitive data flows

- `split_key()` input flows into `storage.store()` as shard bytes. SR-01 requires the shard to be stored as `bytearray`.
- `reconstruct_key()` output flows into the httpx upstream request. SR-02 requires the buffer to be zeroed immediately after dispatch.
- `reconstruct_key()` output must never reach any logger. SR-04 is an absolute prohibition — no logging inside `crypto/`.

#### Edit rules

- Pyright strict is enforced — all changes must be fully typed, `Any` is not permitted.
- Run `gitnexus_impact({target: 'split_key', direction: 'upstream'})` before touching `splitter.py`.
- Run `pytest tests/test_invariants.py` after any change to this module.
- Do not add logging anywhere inside `crypto/` — SR-04 is an absolute prohibition.

#### Tests

- `tests/test_splitter.py`
- `tests/test_properties.py`
- `tests/test_security_properties.py`
- `tests/test_invariants.py`

---

### `src/worthless/adapters`

Owns provider-specific request/response behavior.

Key files:

- `openai.py`
- `anthropic.py`
- `registry.py`
- `types.py`

---

### `src/worthless/mcp`

Owns the optional MCP server integration.

Key files:

- `server.py`

---

### `src/worthless/openclaw/`

Owns the OpenClaw integration layer. Detects OpenClaw presence, wires worthless providers into `openclaw.json`, and installs `SKILL.md` into the workspace skills folder. Best-effort and idempotent — failures in this layer never roll back lock-core (`.env`/DB) changes.

Key files:

- `integration.py`: `detect`, `apply_lock`, and `apply_unlock` — provider wiring and configuration management
- `skill.py`: `install`, `uninstall`, and `current_version` — SKILL.md lifecycle management
- `errors.py`: `OpenclawErrorCode` enum — all failure modes are enumerated here
- `skill_assets/SKILL.md`: the embedded skill file shipped with the package

Related file outside this module:

- `src/worthless/cli/sentinel.py`: atomic JSON sentinel write and read, called by lock/unlock and read by status/doctor

#### Security invariants

- L1: openclaw stage failures never roll back `.env`/DB writes — `.env` is the binding contract between lock and unlock.
- L2 (revised 2026-05-08): detected+failed exits with code 73 and writes the sentinel with a `[FAIL]` block — it does not exit 0.
- F-CFG-15: symlinked `openclaw.json` is refused before any read or write (three-layer defense).
- F-SKL-32: a skill folder owned by a foreign UID is refused and a `SKILL_FOREIGN_OWNER` event is emitted.
- F-SKL-34: a skill folder that is itself a symlink is refused.
- Shard A appearing in `openclaw.json`'s `apiKey` field is acceptable — it is non-secret on its own and matches `.env` wrap-mode (L5).

#### Sensitive data flows

- `apply_lock()` receives `shard_a` and writes it to the `apiKey` field in `openclaw.json`. Shard A is not secret on its own (it requires Shard B to reconstruct), so this is an acceptable sink.

#### Edit rules

- Never import CLI modules (`lock`, `unlock`, `doctor`) from within `openclaw/` — the dependency flows one way.
- New provider support goes into the `_PROVIDER_API` dict in `integration.py` only — R11 mandates a coverage test for every key in that dict.
- All new failure modes get an error code in `OpenclawErrorCode` in `errors.py` — extend the enum, never rename existing values.
- The sentinel schema is backward-compatible only: adding fields is safe, removing or renaming breaks `doctor` and `status` readers.
- When changing `skill_assets/SKILL.md` content, bump the `Version:` line to trigger stale-skill detection in `doctor`.

#### Tests

- `tests/openclaw/test_integration_detect.py`
- `tests/openclaw/test_integration_apply_lock.py`
- `tests/openclaw/test_integration_apply_unlock.py`
- `tests/openclaw/test_integration_concurrency.py`
- `tests/openclaw/test_integration_injection.py`
- `tests/openclaw/test_integration_idempotency.py`
- `tests/openclaw/test_skill_install.py`
- `tests/openclaw/test_trust_fix.py`
- `tests/openclaw/test_doctor_command_openclaw.py`
- `tests/test_openclaw_config.py`

---

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

---

## Cross-module edit rules

| Touching… | Also check… | Run after… |
|-----------|-------------|-----------|
| `crypto/splitter.py` | `cli/app.py` (only split_key call site) | `pytest tests/test_invariants.py` |
| `storage/repository.py` schema | `proxy/app.py` (retrieve), `cli/app.py` (store) | `pytest tests/test_storage.py tests/test_cli_lock.py` |
| `proxy/app.py` rules gating | `tests/test_rules.py` | `pytest tests/test_rules.py tests/test_proxy.py` |
| Any new dataclass in storage/ | `__repr__` redaction (SR-04) | `grep -r '__repr__' src/worthless/storage/` |
| `openclaw/integration.py` `_PROVIDER_API` | Add matching test asserting every key covered | `pytest tests/openclaw/` |
| `openclaw/skill_assets/SKILL.md` | Bump `Version:` line | `pytest tests/openclaw/test_skill_install.py` |
| `cli/sentinel.py` schema | `status.py` + `doctor.py` readers | `pytest tests/openclaw/test_trust_fix.py` |
