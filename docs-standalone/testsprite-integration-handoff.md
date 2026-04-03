# TestSprite Integration Handoff

> Session: 2026-04-03. Goal: get TestSprite MCP running against the Worthless proxy.

## 1. Setup

### MCP Configuration

TestSprite connects via MCP. Config lives in `.mcp.json` at the project root:

```json
{
  "mcpServers": {
    "TestSprite": {
      "command": "npx",
      "args": ["-y", "@testsprite/testsprite-mcp@latest"],
      "env": {
        "API_KEY": "sk-user-t4l01MOFEIPOYMcrEVmUjS9_f4zcBjBlT_fN7tzJfGkOC3nBKgfA5umYT1SRgEjbshkyFjEeOEQgJ6utc7wSqRPvEH5Gk7D5vHVj9qF9m3iHHMVxko0246fAz4haXekPDm4"
      }
    }
  }
}
```

**Critical:** The `-y` flag in `args` is required. Without it, `npx` prompts for install confirmation and hangs silently in Claude Code's non-interactive MCP spawning. This was our first fix.

### Account

- User: shacharm2 (shacharm@gmail.com)
- Plan: Free (144 credits at session start)
- API key page: https://www.testsprite.com/dashboard/settings/apikey

### Prerequisites

- Node.js 22+ (we have v25.8.1)
- macOS firewall must be off or allow connections (ours is off)
- The local server must be running before executing tests

## 2. App Structure Requirements

### The root route problem (RESOLVED)

TestSprite's tunnel creates a reverse connection from their cloud to your local server. Before running tests, it sends a **probe request** — `GET /` — through the tunnel to confirm connectivity. If the probe gets a 401, TestSprite interprets it as "tunnel client not found" and aborts.

Worthless's catch-all route `@app.api_route("/{path:path}")` matched `GET /` and returned 401 (no `x-worthless-key` header). This made every tunnel probe fail.

**Fix applied:** Added a root route before the catch-all in `src/worthless/proxy/app.py`:

```python
@app.get("/")
async def root():
    return {"service": "worthless-proxy", "status": "ok"}
```

This sits in the health endpoints section, before the catch-all proxy route. FastAPI matches explicit routes before parametric ones, so `GET /` now returns 200 while all other paths still hit the catch-all.

**Evidence:** Server logs showed every failed probe as `GET / HTTP/1.1 401 Unauthorized`. After the fix, probes show `GET / HTTP/1.1 200 OK` and the local probe stage passes.

### Starting the server for testing

```bash
WORTHLESS_FERNET_KEY="lEKy9cC_tj-fv-gMQPDr__FIP5lDywBpkHwgbJMtgP8=" \
WORTHLESS_DB_PATH="/tmp/worthless-test.db" \
WORTHLESS_SHARD_A_DIR="/tmp/worthless-shard-a" \
WORTHLESS_ALLOW_INSECURE=true \
uv run uvicorn worthless.proxy.app:create_app --factory --host 0.0.0.0 --port 8000
```

- `WORTHLESS_ALLOW_INSECURE=true` disables TLS enforcement (otherwise non-HTTPS requests get 401)
- `--host 0.0.0.0` needed so the tunnel can reach the server (not just localhost)
- The Fernet key is a throwaway test key, not production

### Verify server is healthy before running TestSprite

```bash
curl -s http://localhost:8000/       # Should return 200 {"service":"worthless-proxy","status":"ok"}
curl -s http://localhost:8000/healthz # Should return 200 {"status":"ok"}
```

## 3. Tool Chain Workflow

TestSprite uses a sequential MCP tool chain. Each step returns `next_action` telling you what to call next.

### Step 1: Bootstrap

```
testsprite_bootstrap(localPort=8000, type="backend", projectPath="...", testScope="codebase")
```

Creates `testsprite_tests/tmp/config.json` with `localEndpoint`, `backendAuthType`, etc.

### Step 2: Code Summary (AI-generated)

```
testsprite_generate_code_summary(projectRootPath="...")
```

This tool does NOT generate the summary itself. It tells the AI to scan the codebase and write `testsprite_tests/tmp/code_summary.yaml` in a specific YAML schema (version "2", features with endpoints). The AI must create this file.

Our code_summary.yaml covers: Health Check, Readiness Check, Proxy Gateway (with /v1/chat/completions, /v1/messages, /v1/models endpoints), Request Body Size Limit, and Rules Engine.

### Step 3: Standardized PRD

```
testsprite_generate_standardized_prd(projectPath="...")
```

Creates `testsprite_tests/standard_prd.json` from the code summary + any existing PRD file (`testsprite_prd.md` at project root).

### Step 4: Backend Test Plan

```
testsprite_generate_backend_test_plan(projectPath="...")
```

Creates `testsprite_tests/testsprite_backend_test_plan.json` with test cases.

### Step 5: Generate Code and Execute

```
testsprite_generate_code_and_execute(projectName="worthless", projectPath="...", testIds=[], additionalInstruction="", serverMode="production")
```

This MCP tool returns a `next_action` of type `"Run in Terminal"` with a CLI command:

```bash
node /path/to/npx/cache/.../index.js generateCodeAndExecute
```

In Cursor/VS Code, the IDE runs this automatically. In Claude Code, we run it via Bash. The CLI reads `testsprite_tests/tmp/config.json`, sets up a tunnel to TestSprite's cloud, and executes tests remotely.

### Step 6: Report Generation (after tests pass)

The AI reads `testsprite_tests/tmp/raw_report.md` and writes a formatted report to `testsprite_tests/testsprite-mcp-test-report.md`.

## 4. Issues Resolved

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| MCP server not connecting | Missing `-y` flag in npx args | Added `-y` to `.mcp.json` args array |
| Tunnel probe returning 401 | Catch-all route matches `GET /` | Added explicit `@app.get("/")` root route returning 200 |
| Server startup crash | Missing `WORTHLESS_FERNET_KEY` env var | Set test Fernet key in env |
| TLS enforcement blocking probes | `allow_insecure` defaults to false | Set `WORTHLESS_ALLOW_INSECURE=true` |

## 5. Issues Unresolved

### Tunnel registration failure

After fixing the root route (local probe passes with 200), the tunnel setup itself fails at the network level. Two error variants observed:

1. **`McpTunnelError: Failed to set up testing tunnel: fetch failed`** — The CLI can't reach TestSprite's tunnel registration endpoint at all. This is a network-level failure before any tunnel is established.

2. **`Error: Tunnel returned 401: Tunnel client not found`** — The tunnel registers (websocket to `tun.testsprite.com:7300` succeeds), but the HTTP probe through `tun.testsprite.com:8080` can't find the registered client. Possibly a race condition or relay sync issue.

These errors alternate between attempts. The tunnel worked for `test-repo` earlier the same day (9:20 AM), but has not worked for any project since ~12:00 PM.

**What we've ruled out:**
- Node.js version (v25.8.1, above the 22+ requirement)
- macOS firewall (disabled)
- Server not running (confirmed with curl, 200 on root and healthz)
- API key (account info returns successfully, 144 credits available)
- Stale config (deleted testsprite_tests entirely, re-ran from scratch)
- npx cache (tried both old and new cache paths)
- App returning 401 on probe (fixed with root route)

**What we haven't tried:**
- Testing from a different network (VPN, hotspot)
- Asking on TestSprite Discord (discord.gg/QQB9tJ973e)
- Checking if there's a rate limit on tunnel creation (we made ~10 attempts)
- Waiting and retrying later (could be transient)
- Using TestSprite's web portal instead of MCP

## 6. How to Continue

### Quick retry

1. Ensure server is running on port 8000 (see Section 2)
2. `rm -rf testsprite_tests`
3. Follow the tool chain (Section 3) — bootstrap through execute
4. If tunnel fails, try again after 30 minutes

### If tunnel keeps failing

1. Post to TestSprite Discord with the error: `McpTunnelError: Failed to set up testing tunnel: fetch failed` and `Tunnel returned 401: Tunnel client not found`
2. Include: Node v25.8.1, macOS, free plan, local probe passes (200 on GET /)
3. Ask if there's a rate limit on tunnel creation or if the relay has known issues

### Files to preserve

- `src/worthless/proxy/app.py` — contains the root route fix (line ~232)
- `.mcp.json` — TestSprite MCP config with `-y` flag
- `testsprite_prd.md` — existing PRD at project root (input for TestSprite)

### Files to delete before retrying

- `testsprite_tests/` — entire directory (TestSprite's primary troubleshooting step)

### npx cache note

The MCP tool hardcodes the npx cache path from when it was first installed. If you clear the npx cache, the path changes and the CLI command in `next_action` becomes stale. Use `find ~/.npm/_npx -name "testsprite-mcp" -type d` to find the current path.

## 7. Documentation References

- TestSprite docs: https://docs.testsprite.com/mcp/getting-started/introduction
- MCP Tools Reference: https://docs.testsprite.com/mcp/core/tools
- Test Execution Troubleshooting: https://docs.testsprite.com/mcp/troubleshooting/test-execution-issues
- Application Detection: https://docs.testsprite.com/mcp/troubleshooting/application-detection-issues
- Discord: https://discord.gg/QQB9tJ973e
- npm: https://www.npmjs.com/package/@testsprite/testsprite-mcp (v0.0.36 as of 2026-04-03)
