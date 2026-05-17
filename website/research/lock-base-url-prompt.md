# Prompt: `worthless lock` should rewrite BASE_URL alongside API keys

## Context

Worthless splits API keys into two shards so a stolen `.env` is worthless. Currently `lock` only rewrites the API key with a decoy. The matching `*_BASE_URL` (e.g. `OPENAI_BASE_URL`) is only set by `worthless wrap`, which injects it into a child process env at runtime.

This means if someone doesn't use `wrap` — they just run their code normally reading `.env` — requests still go directly to the provider with a decoy key (which fails). The user has to manually set the base URL or use `wrap`.

## Desired behavior

When `worthless lock` protects a key, it should ALSO:

1. **Write the matching `*_BASE_URL` into `.env`** pointing at the Worthless proxy (e.g. `OPENAI_BASE_URL=http://localhost:PORT/v1`). If a base URL already exists in `.env`, back it up as a comment (e.g. `# worthless-original: OPENAI_BASE_URL=https://api.openai.com/v1`) and overwrite.

2. **Verify the pair works** by sending a minimal request through the proxy — a single-character completion or a lightweight endpoint call — to confirm the proxy is reachable, the shard is enrolled, and reconstruction works. This proves the lock succeeded end-to-end, not just that bytes were written.

3. **On `worthless unlock`**, restore the original API key AND remove/restore the original base URL.

After `lock`, the user's code just works — it reads `.env`, gets the proxy base URL + decoy key, and every request flows through Worthless automatically. No `wrap` needed for the common `.env`-based case.

`wrap` becomes the fallback for when you can't control `.env` (wrapping an arbitrary binary, CI pipelines, etc.).

## Key files

| File | Role |
|------|------|
| `src/worthless/cli/commands/lock.py` | Lock command — needs base URL rewrite logic added |
| `src/worthless/cli/commands/unlock.py` | Unlock command — needs base URL restore logic |
| `src/worthless/cli/commands/wrap.py` | Has `_PROVIDER_ENV_MAP` (provider → env var mapping) — reuse this |
| `src/worthless/cli/dotenv_rewriter.py` | `rewrite_env_key()` and `scan_env_keys()` — the .env manipulation primitives |
| `src/worthless/cli/commands/up.py` | Proxy startup — need to know default port |
| `src/worthless/proxy/app.py` | Proxy app — health endpoint for verification |
| `src/worthless/defaults.py` | Default port and other constants |

## Provider → env var mapping (from wrap.py)

```python
_PROVIDER_ENV_MAP = {
    "openai": "OPENAI_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
}
```

## Design constraints

- **Proxy must be running** for the verification step. If the proxy isn't running, `lock` should either (a) start it temporarily for verification then stop, or (b) skip verification with a warning and a `--verify` flag to opt in later. Decide based on what feels cleanest — don't over-engineer.
- **Original base URL preservation**: If `.env` already has `OPENAI_BASE_URL=https://custom-endpoint.com`, that's important — the user might be using a custom endpoint. Store it so `unlock` can restore it. Could be a comment in `.env`, a field in the DB enrollment, or both.
- **Port discovery**: `lock` needs to know what port the proxy runs on. Check `src/worthless/defaults.py` for the default, and whether there's a config mechanism.
- **Idempotency**: Running `lock` twice should not double-write or corrupt the base URL.
- **`unlock` symmetry**: `unlock` must restore original base URLs, not just delete them. If no original existed, remove the line entirely.

## What NOT to do

- Don't refactor `wrap` in this change — it still serves its purpose for non-`.env` workflows.
- Don't add new dependencies.
- Don't change the split/shard logic — only the `.env` rewriting and verification.
- Don't add mid-file imports — all imports at top of file.

## Testing

- Lock a `.env` with `OPENAI_API_KEY` → verify both key replaced with decoy AND `OPENAI_BASE_URL` written
- Lock when `OPENAI_BASE_URL` already exists → verify original is preserved, can be restored by unlock
- Unlock → verify both original key and original base URL restored
- Lock twice (idempotent) → no corruption
- Verification step: mock a proxy health check, confirm lock reports success/failure accurately
