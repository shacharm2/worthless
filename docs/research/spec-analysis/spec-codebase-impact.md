# Spec vs Codebase Impact Analysis

**Date:** 2026-04-05
**Source:** Engineering planner agent analysis of sidecar-architecture-spec.md against current src/worthless/

---

## 1. File-by-File Impact Map

### SURVIVES (unchanged or minor tweaks)

| File | Notes |
|------|-------|
| `__init__.py` | Empty |
| `exceptions.py` | ShardTamperedError stays relevant for Shamir verification |
| `adapters/__init__.py` | Empty |
| `adapters/types.py` | ProviderAdapter protocol, AdapterRequest/Response survive as socket protocol format |
| `adapters/registry.py` | Path-to-provider mapping. Used by proxy to route to sidecar |
| `adapters/openai.py` | Request preparation survives. Auth header injection moves to sidecar |
| `adapters/anthropic.py` | Same as openai.py |
| `proxy/errors.py` | Error response factories unchanged |
| `proxy/middleware.py` | BodySizeLimitMiddleware unchanged |
| `proxy/metering.py` | Token extraction survives — data source changes from upstream response to sidecar response `usage` field |
| `proxy/rules.py` | RulesEngine, SpendCapRule, RateLimitRule unchanged. Gate-before-reconstruct ordering preserved |
| `cli/__init__.py` | Empty |
| `cli/console.py` | Unchanged |
| `cli/errors.py` | Unchanged |
| `cli/key_patterns.py` | Unchanged |
| `cli/decoy.py` | Unchanged — spec doesn't mention decoys but they must survive |
| `cli/dotenv_rewriter.py` | Unchanged |
| `cli/scanner.py` | Unchanged |
| `cli/commands/scan.py` | Unchanged |
| `cli/commands/mcp.py` | Unchanged |
| `mcp/server.py` | Queries DB only, unchanged |

### MODIFIED (significant changes)

| File | What Changes |
|------|-------------|
| `proxy/app.py` | **MAJOR.** Current: auth → rules → Fernet decrypt → XOR reconstruct → httpx upstream → stream relay. New: auth → rules → load shard_a → sidecar IPC {mode:"proxy", alias, shard_a, request} → relay response. Removes: `reconstruct_key`, `secure_key`, `httpx` upstream, Fernet decrypt. Adds: sidecar IPC client. httpx upstream calls eliminated from Python entirely |
| `proxy/config.py` | `fernet_key` field removed. `_read_fernet_key()` deleted. New: `sidecar_socket_path`, `sidecar_binary_path`. `validate()` no longer checks Fernet key |
| `cli/app.py` | Adds: `get` (vault mode), `start` (alias for `up`). May rename `lock` → `enroll` |
| `cli/bootstrap.py` | **MAJOR.** `ensure_home()` no longer generates Fernet key. `fernet_key_path` removed. New: `shards_dir` (~/.config/worthless/shards/<alias>/a). Adds `ensure_sidecar()`. Migration detection: if fernet.key exists, warn and offer migrate |
| `cli/commands/lock.py` | XOR split → Shamir 2-of-3. Shard B → credential store via sidecar. Shard C → printed once. ShardRepository usage gutted of Fernet |
| `cli/commands/unlock.py` | Reconstruction via sidecar vault-mode IPC instead of in-process XOR |
| `cli/commands/revoke.py` | Must also delete Shard B from credential store (via sidecar). Currently only deletes DB + shard_a file |
| `cli/commands/up.py` | Must spawn sidecar alongside proxy. Fernet fd passing removed. Port: currently 8787, spec says 9191 — needs resolution |
| `cli/commands/wrap.py` | Same as up.py — sidecar spawn. `build_proxy_env` drops Fernet key |
| `cli/commands/status.py` | Add sidecar health check, platform credential store backend display, security tier |
| `cli/process.py` | `build_proxy_env` drops Fernet. `spawn_proxy` drops Fernet fd. New: `spawn_sidecar()`, `ensure_sidecar()` |
| `cli/enroll_stub.py` | XOR → Shamir, removes Fernet |
| `crypto/splitter.py` | **REPLACED.** XOR → Shamir 2-of-3. Python splitting used at enrollment only. `reconstruct_key` moves to Rust sidecar — Python never reconstructs |
| `crypto/types.py` | `SplitResult` changes from (shard_a, shard_b, commitment, nonce) to (shard_a, shard_b, shard_c). HMAC commitment eliminated — Shamir has built-in integrity |
| `storage/repository.py` | **MAJOR.** Fernet eliminated. `EncryptedShard` gone. `shard_b_enc` column gone. DB shrinks to: enrollments, spend_log, enrollment_config, metadata. Shard storage moves out of SQLite entirely |
| `storage/schema.py` | `shards` table redesigned — no `shard_b_enc`. Becomes alias registry (alias, provider, created_at) |

### NEW FILES NEEDED

#### Rust (sidecar binary)

| File | Purpose | Spec Section |
|------|---------|-------------|
| `Cargo.toml` | Project config + deps | 5, 10 |
| `src/main.rs` | Socket server, request dispatch, startup | 5 |
| `src/shard_store/mod.rs` | ShardStore trait + auto-detection waterfall | 6 |
| `src/shard_store/keychain.rs` | macOS Keychain | 6 |
| `src/shard_store/credential_manager.rs` | Windows DPAPI | 6 |
| `src/shard_store/kernel_keyring.rs` | Linux keyctl | 6 |
| `src/shard_store/wsl_bridge.rs` | WSL2 → Windows bridge | 6 |
| `src/shard_store/secret_service.rs` | Linux D-Bus | 6 |
| `src/shard_store/env_var.rs` | Docker/CI | 6 |
| `src/shard_store/mounted_secret.rs` | Docker/K8s mounted secret | 6 |
| `src/shard_store/file_fallback.rs` | Last resort (should be encrypted per research) | 6 |
| `src/reconstruct.rs` | Shamir reconstruct + mlock + zeroize | 5 |
| `src/protect.rs` | PR_SET_DUMPABLE / PT_DENY_ATTACH | 7 |
| `src/sandbox.rs` | seccomp-BPF + Landlock | 5 |
| `src/upstream.rs` | reqwest HTTPS client for proxy mode | 5 |

#### Python (new)

| File | Purpose | Spec Section |
|------|---------|-------------|
| `sidecar_client.py` | Unix socket / named pipe IPC client | 8, 9 |
| `platform_detect.py` | OS + credential store detection | 9, 10 |
| `shard_split.py` | Python-side Shamir split (enrollment only) | 4, 9 |
| `cli/commands/get.py` | `worthless get <alias>` vault mode | 9 |
| `cli/commands/migrate.py` | Fernet → Shamir migration | 12 |
| `.github/workflows/wheels.yml` | maturin cross-platform builds | 10 |

---

## 2. Proxy-Mode Request Flow — Current vs New

### Current (proxy/app.py lines 215-396)

```
1. Extract alias from x-worthless-key header
2. Validate alias, TLS, headers
3. repo.fetch_encrypted(alias) → EncryptedShard
4. Load shard_a from header or filesystem
5. rules_engine.evaluate() → GATE
6. get_adapter(path) → OpenAI/Anthropic adapter
7. repo.decrypt_shard(encrypted) → Fernet decrypt → StoredShard
8. reconstruct_key(shard_a, shard_b, commitment, nonce) → XOR → key_buf
9. secure_key(key_buf) context manager
10. adapter.prepare_request() → upstream URL + auth header
11. httpx_client.send(upstream_req, stream=True)
12. adapter.relay_response()
13. Stream response to client
14. record_spend() background task
15. Zero key_buf, shard_a in finally block
```

### New

```
1. Extract alias                                    [UNCHANGED]
2. Validate                                          [UNCHANGED]
3. Check alias exists (lightweight)                  [SIMPLIFIED]
4. Load shard_a from filesystem                      [UNCHANGED]
5. rules_engine.evaluate() → GATE                   [UNCHANGED]
6. get_adapter(path) → determine upstream URL        [SIMPLIFIED]
7. Send to sidecar: {mode:"proxy", alias, shard_a,  [NEW — replaces 7-11]
   request: {method, url, headers, body}}
8. Sidecar internally: load B, reconstruct, call, zero
9. Relay sidecar response to client                  [SIMILAR to 12-13]
10. record_spend() from sidecar response.usage       [SIMPLIFIED]
11. Zero shard_a in finally                          [SIMPLIFIED]
```

**Steps 7-11 collapse into one sidecar IPC call.** Python never touches Fernet, reconstruct_key, secure_key, httpx upstream, or auth headers.

---

## 3. Dependency Changes

### Python REMOVED
| Package | Why |
|---------|-----|
| `cryptography` | Fernet eliminated. Check: may still need for mTLS |

### Python ADDED
| Package | Why |
|---------|-----|
| `maturin` | Build system for Rust binary in wheels |

### Python UNCHANGED
`aiosqlite`, `httpx` (kept for health checks), `fastapi`, `uvicorn`, `typer`, `python-dotenv`, `mcp`

### Cargo.toml (new)
- `blahaj` (or self-implemented GF(256)) — Shamir splitting
- `zeroize` 1.8 — memory zeroing
- `secrecy` 0.8 — Secret<T> wrapper
- `tokio` — async runtime
- `reqwest` with rustls — HTTPS client
- `serde` + `serde_json` — socket protocol
- `libc` — prctl, ptrace, mlock
- Platform-specific: `security-framework` (macOS), `windows` (Win), `linux-keyutils` (Linux)

### pyproject.toml
- Build backend: `setuptools` → `maturin`
- `[tool.maturin] bindings = "bin"`

---

## 4. Section 14 "UNCHANGED" Claims — Verification

| Claim | Verdict | Detail |
|-------|---------|--------|
| CLI commands (enroll, start, status, scan) | **PARTIALLY CORRECT** | Names survive but 3 of 4 have significant implementation changes. `scan` is the only truly unchanged one |
| Proxy endpoint (localhost:9191) | **INCORRECT** | Current port is 8787, not 9191. This is a port change |
| Spend cap enforcement | **CORRECT** | Rules engine fires before reconstruction in both architectures |
| BASE_URL swap | **CORRECT** | `_PROVIDER_ENV_MAP` in wrap.py unchanged |
| 90-second setup | **AT RISK** | Shard C backup adds mandatory human interaction per key. Batch `lock` flow has no equivalent |
| MCP server | **CORRECT** | Queries DB, doesn't touch crypto |

---

## 5. Things the Spec Misses

1. **`worthless unlock` command** — restores original keys from shards. Not mentioned, needs sidecar vault mode
2. **Decoy key system** — `make_decoy`, `decoy_hash`, WRTLS prefix. Not mentioned, must survive
3. **`worthless lock --env .env` auto-scan** — fully non-interactive batch enrollment. Spec's `enroll` is per-key interactive. Need batch wrapper
4. **Port default** — 8787 vs 9191 discrepancy
5. **enrollment/enrollment_config/spend_log DB tables** — still needed even though shards table changes
