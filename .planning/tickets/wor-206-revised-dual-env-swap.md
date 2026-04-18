# WOR-206: Zero-Code-Change Dual Env Var Replacement (Revised)

> "If the key is in the env, the env is the attack surface."

Today `worthless wrap` sets `*_BASE_URL` to route traffic through the proxy but leaves the real API key in the child's environment. Any crash dump, `/proc/pid/environ` read, or supply-chain dependency calling `os.environ["OPENAI_API_KEY"]` leaks the full key. The proxy protects the wire, but the env is wide open.

Shard-A already solves this. It looks exactly like a valid API key (same prefix, same length), but is cryptographically useless without shard-B. SDKs accept it without validation errors. The proxy already knows how to receive shard-A, combine with shard-B, and reconstruct. We just need to wire `wrap` to inject shard-A into the child env instead of the real key.

## What

Replace real API keys in the child process environment with their shard-A values and set `*_BASE_URL` to the proxy. No session tokens, no new token store, no lifecycle management.

## Why

1. **Security**: shard-A alone is random garbage — useless without shard-B. Exfiltrating the child env yields nothing actionable.
2. **Fail-closed**: if the proxy crashes, the child can't accidentally fall through to the real provider. Today it can.
3. **Zero-code-change**: shard-A preserves the key prefix (`sk-proj-*`, `anthropic-*`). Every SDK that reads `*_API_KEY` from env accepts it.
4. **No new abstractions**: shard-A already exists on disk. The proxy already handles shard-A in Authorization headers. This is just plumbing.

## How

### Step 1: DB Migration — `shard_a_hash` column

Add `shard_a_hash TEXT` to the `shards` table (SHA-256 of shard-A). Populated at enrollment time. Used at wrap time to detect ALREADY_REPLACED keys (env var value whose hash matches a stored shard-A hash).

**Why needed**: Without this, we can't distinguish "this env var contains shard-A" from "this env var contains a real key that isn't enrolled." The `enrollments` table maps aliases to env var names, but there's no way to go from a shard-A value back to its enrollment.

### Step 2: Key Classification in `_build_child_env`

For each env var in the child environment:

```
hash = sha256(env_value)
if hash matches a commitment in shards table    → ENROLLED (real key, replace with shard-A)
elif hash matches a shard_a_hash in shards table → ALREADY_REPLACED (skip key, still set BASE_URL)
else                                              → NOT_OURS (leave untouched)
```

Build the child env dict with ENROLLED keys excluded, then inject shard-A values. The real key value is never copied into the child env dict.

### Step 3: Post-Swap Verification

For each replaced key, verify reconstruction works BEFORE spawning the child:

- Send shard-A as Bearer token to the proxy's alias resolution path
- Proxy resolves shard-A → alias → shard-B → reconstruction check (no upstream call)
- New lightweight endpoint: `GET /internal/verify` — does alias resolution + commitment check, returns 200/401. Bound to 127.0.0.1 only. Constant-time comparison (`hmac.compare_digest`).
- Retry 3x (100ms, 500ms, 1500ms backoff)
- On failure: ABORT that provider. Never revert to real key. Log clearly.

### Step 4: Transparency Output

```
worthless: scanning env for enrolled keys...
worthless: OPENAI_API_KEY -- enrolled, replacing with shard
worthless: ANTHROPIC_API_KEY -- not enrolled, skipping
worthless: STRIPE_API_KEY -- not an LLM key, skipping
worthless: env OPENAI_BASE_URL=http://127.0.0.1:18981/v1
worthless: verifying reconstruction... ok
worthless: exec python app.py
```

### Step 5: Edge Cases

| Case | Behavior |
|---|---|
| Nested `worthless wrap` | ALREADY_REPLACED detected via `shard_a_hash`. Key skipped, BASE_URL still set. |
| `WORTHLESS_WRAPPED=1` already set | Log warning, skip all key replacement. Proxy + child still launch. |
| Existing `*_BASE_URL` | Warn + overwrite. Store original in `WORTHLESS_ORIGINAL_*_BASE_URL`. |
| Provider verification fails 3x | Abort that provider. Other verified providers proceed. Child launches with partial protection + clear warning. |
| Proxy crash mid-session | Child holds shard-A, can't auth anywhere. Fail-closed. This is a security improvement over today. |

## Security Compliance

| Rule | Status | Notes |
|---|---|---|
| SR-01 (bytearray) | EXEMPT | Shard-A in env must be `str` (OS API). Useless alone — not actionable key material. |
| SR-02 (zeroing) | PARTIAL | `os.environ.pop()` removes real key ref. Python `str` GC limitation documented. |
| SR-03 (gate before reconstruct) | UNCHANGED | Proxy still gates before reconstruction. |
| SR-04 (no secrets in logs) | ENFORCED | Transparency log shows var names only, never values. Comment at scan site. |
| SR-07 (constant-time) | ENFORCED | `hmac.compare_digest` in verify endpoint token comparison. |

## KPIs

| KPI | Target | How to Measure |
|-----|--------|----------------|
| Real keys in child env | 0 | Test: child dumps env, grep for commitment-matching values |
| SDK acceptance | 100% Python/Node | Test: `openai.OpenAI(api_key=shard_a_value)` doesn't throw |
| Verification latency | < 200ms per key | Timer in test around verify loop |
| Classification accuracy | 100% | Test with enrolled, already-replaced, non-LLM, unenrolled-LLM keys |
| Fail-closed on proxy crash | Child gets auth errors, not success | Test: kill proxy, child request fails |

## Tests (15)

### Unit (6)
1. `test_classify_enrolled` — env var matching commitment hash → ENROLLED
2. `test_classify_already_replaced` — env var matching shard_a_hash → ALREADY_REPLACED
3. `test_classify_not_ours` — `sk-*` prefix but no DB match → NOT_OURS
4. `test_classify_non_llm` — `STRIPE_API_KEY` → NOT_OURS
5. `test_child_env_excludes_real_key` — real key never copied into child env dict
6. `test_already_replaced_still_sets_base_url` — ALREADY_REPLACED skips key but sets URL

### Integration (5)
7. `test_verify_happy` — proxy returns 200 on valid shard-A → replacement proceeds
8. `test_verify_retry_then_succeed` — proxy fails 2x, succeeds 3rd → replacement proceeds
9. `test_verify_abort_on_3x_failure` — proxy fails 3x → that provider aborted, others proceed
10. `test_multi_provider_replacement` — OpenAI + Anthropic both enrolled → both replaced
11. `test_mixed_env_preservation` — non-enrolled keys untouched after replacement

### E2E (2)
12. `test_e2e_child_env_no_real_keys` — spawned child's env contains shard-A, not real key
13. `test_e2e_stderr_transparency` — stderr contains expected scan/verify/exec lines, no key values

### Security (2)
14. `test_fail_closed_proxy_crash` — kill proxy after replacement, child request fails (doesn't fall through)
15. `test_no_key_values_in_logs` — grep all stderr/stdout for real key and shard-A values → zero matches

## Pre-Requisites

1. **DB migration**: add `shard_a_hash` column to `shards` table
2. **Backfill**: existing enrollments need `shard_a_hash` populated (read shard-A file, hash, update row)
3. **`/internal/verify` endpoint** on proxy (alias resolution without upstream call)

## AC

`worthless wrap <cmd>` replaces all enrolled API keys with shard-A values, sets `*_BASE_URL` to proxy, verifies reconstruction per-key (3 retries, abort on failure), and logs every decision to stderr — verified by 15 tests covering classification, verification, fail-closed, and zero-key-leak.
