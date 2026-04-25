# Install — Claude Code, Cursor, or Windsurf

Worthless ships an MCP server exposing `lock`, `scan`, `status`, and `spend`
tools to AI coding agents. The recommended install path needs only Node 18+;
Python and `uv` are bootstrapped automatically on first run.

## Recommended — npm wrapper via `.mcp.json`

Add to your project's `.mcp.json` (Claude Code, Cursor, and Windsurf all read
this file):

```json
{
  "mcpServers": {
    "worthless": {
      "command": "npx",
      "args": ["-y", "worthless-mcp"]
    }
  }
}
```

Restart your editor. On first launch, `worthless-mcp`:

1. Finds or installs `uv` (one-time, ~5 s).
2. Runs `uvx worthless[mcp]==<pinned-version> mcp` — `uvx` caches the Python
   environment, so subsequent starts are instant.
3. Streams MCP protocol over stdio to the editor.

Total cold-start install time: **< 30 s** on a fresh machine with Node only.
No Python toolchain awareness required.

### Available tools

- `worthless_status()` — running proxy state, protected keys
- `worthless_lock(env_path)` — split the real key, rewrite `.env` with a shard
- `worthless_scan(paths, deep)` — find accidental key exposures
- `worthless_spend(alias)` — per-provider spend readout

## Alternative — manual CLI install

If you already use `pipx` or want the full CLI (`worthless wrap`,
`worthless up`, proxy mode), install the Python package directly:

```bash
pipx install worthless
# or: curl -sSL https://worthless.sh | sh
worthless lock              # protect your .env keys
worthless wrap claude       # starts proxy + launches Claude Code
```

Then point your editor at the HTTP proxy:

```bash
worthless up -d             # background proxy on :8787
export OPENAI_BASE_URL=http://localhost:8787
export ANTHROPIC_BASE_URL=http://localhost:8787
```

For Cursor / Windsurf, set the same `*_BASE_URL` variables in the editor's
environment settings.

## How the split-key proxy works

1. `worthless lock` splits your API key and replaces `.env` with shard-A
   (format-preserving, looks like a real key).
2. `worthless up` (or `wrap`) starts a local HTTP proxy on port 8787.
3. Your editor's SDK calls hit `localhost:8787` instead of the provider
   directly.
4. The proxy reconstructs the real key in memory, makes the upstream call,
   and zeros the key.

Your editor works identically. Your key is never stored in plaintext.

> [!NOTE]
> **Planned: Cloud proxy.** A hosted proxy that eliminates the need to run
> locally is planned. See the [roadmap](../ROADMAP.md) for timeline.
