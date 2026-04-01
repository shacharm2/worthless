# Install -- Claude Code, Cursor, or Windsurf (MCP)

Run the Worthless proxy locally, then point your MCP-compatible editor at it.

## Prerequisites

```bash
git clone https://github.com/shacharm2/worthless && cd worthless
uv pip install -e .
worthless lock          # protect your .env keys
```

## Claude Code

Add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "worthless": {
      "command": "worthless",
      "args": ["up", "--port", "8787"],
      "env": {}
    }
  }
}
```

Or copy from [`examples/mcp.json`](../examples/mcp.json).

Set your environment so the SDK routes through the proxy:

```bash
export OPENAI_BASE_URL=http://localhost:8787
export ANTHROPIC_BASE_URL=http://localhost:8787
```

Or use `worthless wrap` to inject these automatically:

```bash
worthless wrap claude
```

## Cursor

Cursor reads MCP config from `~/.cursor/mcp.json`. Use the same JSON as above.

```bash
cp examples/mcp.json ~/.cursor/mcp.json
```

Then set `OPENAI_BASE_URL=http://localhost:8787` in Cursor's environment settings.

## Windsurf

Windsurf reads MCP config from `~/.windsurf/mcp.json`. Same JSON format.

```bash
cp examples/mcp.json ~/.windsurf/mcp.json
```

Set `OPENAI_BASE_URL=http://localhost:8787` in Windsurf's environment configuration.

## How it works

1. `worthless lock` splits your API key and replaces `.env` with a decoy
2. `worthless up` (or `wrap`) starts a local proxy on port 8787
3. Your editor's SDK calls hit `localhost:8787` instead of the provider directly
4. The proxy reconstructs the real key in memory, makes the upstream call, and zeros the key

Your editor works identically. Your key is never stored in plaintext.

> [!NOTE]
> **Planned: Cloud MCP Server.** A hosted MCP server that eliminates the need to
> run a local proxy is planned. See the [roadmap](../ROADMAP.md) for timeline.
