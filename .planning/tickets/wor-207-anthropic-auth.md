# WOR-207: Anthropic adapter auth extraction

> "Anthropic uses x-api-key, not Authorization: Bearer."

The proxy extracts shard-A from `Authorization: Bearer` (SR-09). This works for OpenAI which uses Bearer auth. Anthropic uses `x-api-key` header instead. The Anthropic live E2E test is skipped because of this.

## What

The proxy's shard-A extraction needs to be adapter-aware:
- OpenAI: `Authorization: Bearer <shard-A>`
- Anthropic: `x-api-key: <shard-A>`

## Options

1. **Adapter-specific auth extraction** — each adapter declares where to find the key. Proxy calls `adapter.extract_key(request)` instead of hardcoding Bearer logic.
2. **Normalize at wrap** — `wrap` injects a middleware that moves `x-api-key` to `Authorization: Bearer` before the request hits the proxy.
3. **Accept both** — proxy checks `Authorization: Bearer` first, falls back to `x-api-key`.

## AC

- `uv run pytest tests/test_e2e_live.py -m live` — Anthropic test passes (unskipped)
- Proxy correctly reconstructs Anthropic keys from `x-api-key` header
