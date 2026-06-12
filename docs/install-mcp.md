---
title: "Install — Claude Code, Cursor, or Windsurf"
description: "Run the Worthless proxy locally and point your editor's SDK at it."
---

# Install -- Claude Code, Cursor, or Windsurf

Run the Worthless proxy locally, then point your editor's SDK at it.

:::note
Worthless includes both an HTTP proxy and an MCP server. Your editor
talks to AI providers through the proxy via `BASE_URL`. The MCP server
(`worthless mcp`) exposes lock, scan, status, and spend tools for
agent-driven workflows (Claude Code, Cursor, Windsurf).
:::

## Prerequisites

```bash
git clone https://github.com/shacharm2/worthless && cd worthless
uv pip install -e .
worthless lock          # protect your .env keys
```

## Claude Code

Option A — run the proxy, then launch Claude Code (recommended):

```bash
worthless up -d          # start proxy in background on port 8787
export OPENAI_BASE_URL=http://localhost:8787
export ANTHROPIC_BASE_URL=http://localhost:8787
claude                   # SDK calls route through the proxy
```

Option B — `worthless wrap` (convenience):

```bash
worthless wrap claude    # ephemeral proxy, injects BASE_URL, launches Claude Code
```

`worthless up` is the canonical way to run the proxy. `worthless wrap` still
works today, but it is slated to be replaced by `up` — prefer `up` for anything
you intend to keep.

## Cursor

:::caution[Untested]
We have **not** verified that Cursor honors a custom `OPENAI_BASE_URL` for its
built-in AI features. These steps mirror the Claude Code approach and are a
reasonable starting point — but treat them as unverified. Tried it? [Open an
issue](https://github.com/shacharm2/worthless/issues) and tell us what happened.
:::

Start the proxy, then configure Cursor's environment:

```bash
worthless up -d
```

In Cursor settings, add:

```
OPENAI_BASE_URL=http://localhost:8787
```

## Windsurf

:::caution[Untested]
Same as Cursor: we have **not** verified that Windsurf honors a custom
`OPENAI_BASE_URL` for its built-in AI. Unverified starting point.
:::

Start the proxy, then configure Windsurf's environment:

```bash
worthless up -d
```

In Windsurf settings, add:

```
OPENAI_BASE_URL=http://localhost:8787
```

## Run the proxy in Docker (alternative)

You don't have to run the proxy locally. The same proxy ships as a signed
container — see [Docker install](/install-docker/). Start it, then point your
editor's `BASE_URL` at the mapped port (`localhost:8787`) exactly as above.

## How it works

1. `worthless lock` splits your API key and replaces `.env` with shard-A (format-preserving, looks like a real key)
2. `worthless up` (or `wrap`) starts a local HTTP proxy on port 8787
3. Your editor's SDK calls hit `localhost:8787` instead of the provider directly
4. The proxy reconstructs the real key in memory, makes the upstream call, and zeros the key

Your editor works identically. Your key is never stored in plaintext.

:::note[Planned: Cloud proxy]
A hosted proxy that eliminates the need to run locally is on the roadmap.
:::
