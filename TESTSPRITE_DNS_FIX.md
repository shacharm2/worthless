# TestSprite DNS Workaround

## Problem
Local DNS resolution for `api.testsprite.com` hangs, causing TestSprite's
`generateCodeAndExecute` CLI to fail with `McpTunnelError: fetch failed`.

## What was changed (2026-04-04)

Added a hosts entry to bypass DNS:
```
sudo sh -c 'echo "18.215.20.131 api.testsprite.com" >> /etc/hosts'
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

## How to undo

Remove the hosts entry:
```
sudo sed -i '' '/api.testsprite.com/d' /etc/hosts
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

## Notes
- The IP `18.215.20.131` is an AWS ELB (`ts-api-185729551.us-east-1.elb.amazonaws.com`)
- ELB IPs can change — if TestSprite stops working later, remove the entry and check if DNS works again
- The MCP bootstrap (WebSocket-based) works fine without this fix; only the CLI execution path needs it

## API_KEY sourcing (2026-04-04)

The TestSprite MCP server receives `API_KEY` from `.mcp.json` env config, but
when it delegates to the CLI subprocess (`node index.js generateCodeAndExecute`),
that process only inherits the shell's environment — not the MCP server's env.
This causes tunnel establishment to fail with `fetch failed` (unauthenticated).

**Fix:** Use `scripts/testsprite_run.sh` instead of calling the CLI directly.
The wrapper sources `API_KEY` from the project `.env` file before invoking
the TestSprite CLI via npx.

```bash
./scripts/testsprite_run.sh
```
