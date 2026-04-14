# Worthless Agent Discovery File (SKILL.md)

**Worthless** is a split-key reverse proxy that makes leaked API keys worthless. API keys are split into two information-theoretically secure shards: one stays on the user's machine, one is encrypted on the proxy. The real key only reconstructs in memory for a single API call, then is zeroed. If spending limits are hit, the key never forms at all.

## What Worthless Does

Worthless protects API keys in three scenarios:

1. **Local Development**: `worthless wrap` runs your code through an ephemeral proxy that intercepts API calls, injects the real key only when needed, and cleans up on exit.
2. **Daemon Mode**: `worthless up` starts a persistent local proxy on port 8787 (configurable) that stays running and protects all enrolled keys.
3. **CI/CD & Sidecar**: The proxy is designed to run as a sidecar container or process, protecting keys across environments with per-key spending limits and time-window gates.

---

## Installation & Setup

### Package Info
- **Package name**: `worthless`
- **Version**: 0.2.0
- **Entry point**: `worthless` (CLI command)
- **Python**: 3.10+
- **License**: AGPL-3.0
- **Status**: Pre-release

### Quick Install
```bash
pipx install worthless
# or: pip install worthless (in a virtualenv)
# or: curl -sSL worthless.sh | sh
```

### First-Time Setup (the magic way)
```bash
cd your-project
worthless
# → Detects API keys in .env, prompts to lock, starts proxy. Done.
```

Running `worthless` with no arguments auto-detects `.env`/`.env.local`, shows detected keys (var name + provider only), prompts `[y/N]` to lock, starts the proxy daemon, and reports healthy. One command from zero to protected.

### First-Time Setup (explicit commands)
```bash
worthless lock                       # Split keys in .env
worthless up -d                      # Start proxy daemon
worthless wrap python your_app.py    # Run code through proxy
```

### Non-interactive / CI
```bash
worthless --yes      # Auto-approve lock + proxy start
worthless --json     # Read-only state report (never writes)
```

---

## CLI Commands

All commands are available via `worthless <command> [OPTIONS]`.

### Global Options (apply to all commands)
- `--quiet`, `-q`: Suppress non-error output
- `--json`: Emit machine-readable JSON output
- `--debug`: Show full tracebacks on error
- `--version`, `-V`: Show version and exit

### Core Commands

#### `worthless lock [OPTIONS]`
**Scan `.env`, split API keys, store shards, rewrite `.env` with cryptographic decoys.**

Scans the current `.env` and `.env.local` files for high-entropy values that match known API key patterns (OpenAI, Anthropic, etc.). For each detected key:
1. Splits the key into two information-theoretic shards using XOR secret sharing
2. Stores Shard A unencrypted in `~/.worthless/shards_a/<alias>`
3. Stores Shard B encrypted in the SQLite database at `~/.worthless/worthless.db`
4. Replaces the original key in `.env` with a format-correct but cryptographically useless decoy
5. Records the location (file, line, variable name) for later recovery

**Options:**
- `--env, -e PATH`: Path to .env file (default: `.env`)
- `--provider, -p NAME`: Override provider auto-detection
- `--token-budget-daily N`: Daily token budget limit for this key

**Output (success):**
```
1 key(s) protected.
  openai-69ccc444  sk-proj-*  PROTECTED
```

#### `worthless unlock [OPTIONS]`
**Reconstruct original API keys from shards and restore `.env`.**

Reverses the `lock` operation. Locates Shard A files, fetches encrypted Shard B from the database, XOR-merges them to recover the original key, and restores it to `.env`.

**Options:**
- (None — unlocks all enrolled keys)

**Behavior:**
- If multiple `.env` files have the same key enrolled, prompts for disambiguation
- Zeros key material from memory after restoration
- Removes shard files and DB records

**Use case:** Temporary switch between `wrap`-mode and native SDK mode, or complete teardown.

#### `worthless scan [OPTIONS] [PATHS]`
**Detect exposed API keys with decoy awareness.**

Scans files for high-entropy strings that match known API key patterns. Ignores decoys that were created by Worthless (checks Shannon entropy + format coherence).

**Options:**
- `--deep`: Scan beyond `.env`/`.env.local` — includes `*.yml`, `*.yaml`, `*.toml`, `*.json` in project root, plus env var dump
- `--format {sarif|human}`: Output format (default: human-readable)
- `PATHS`: Explicit file/directory paths to scan

**Output:**
- Lists findings with file, line, value preview, and confidence level
- SARIF format for CI/CD integration

**Use case:** Security audit before deploying; detect accidentally-committed keys in Git history.

#### `worthless status [OPTIONS]`
**List enrolled keys and check proxy health.**

Queries the database for all enrolled key aliases (provider + deterministic ID) and attempts to reach the local proxy on its configured port.

**Options:**
- (None)

**Output:**
```
Locked keys:
  openai-69ccc444   openai      PROTECTED
  anthropic-a1b2c3  anthropic   PROTECTED

Proxy: http://127.0.0.1:8787 (running)
```

#### `worthless wrap [OPTIONS] COMMAND [ARGS...]`
**Ephemeral proxy + child process lifecycle.**

Starts a temporary reverse proxy on a random port, injects `{PROVIDER}_BASE_URL` environment variables (e.g., `OPENAI_BASE_URL=http://127.0.0.1:XXXXX`), spawns a child process with those env vars, waits for the child to exit, and cleans up the proxy.

The child's API SDK calls automatically route through the proxy. No code changes required.

**Options:**
- (None — command and args are pass-through)

**Example:**
```bash
worthless wrap python -c "import openai; print(openai.OpenAI().models.list())"
worthless wrap npm test
worthless wrap pytest
```

**Behavior:**
- Proxy inherits all enrolled keys automatically
- If a spending rule fires, request is rejected with 402 Payment Required
- Cleanup is automatic on child exit (SIGTERM forwarding, process tree termination)

#### `worthless up [OPTIONS]`
**Start the proxy server (foreground or daemon mode).**

Starts a long-lived reverse proxy that protects all enrolled keys. Binds to a configurable port (default 8787).

**Options:**
- `--port PORT`, `-p PORT`: Bind port (default: 8787 or `$WORTHLESS_PORT`)
- `--daemon`, `-d`: Run in background; logs to `~/.worthless/proxy.log`

**Behavior:**
- Foreground mode: logs to stdout, exits with proxy
- Daemon mode: forks to background, returns immediately, writes PID to `~/.worthless/proxy.pid`
- Startup health check ensures proxy is listening before returning
- Rejects new `up` commands if proxy is already running on the configured port

**Example:**
```bash
worthless up                          # foreground, port 8787
worthless up --port 9999              # foreground, custom port
worthless up -d --port 9999           # daemon mode

# In another shell:
export OPENAI_BASE_URL=http://127.0.0.1:8787
python your_app.py
```

#### `worthless down [OPTIONS]`
**Stop the running proxy daemon.**

Reads the PID file (`~/.worthless/proxy.pid`), sends SIGTERM to the process group, polls for graceful shutdown, escalates to SIGKILL after timeout (5s), and cleans up the PID file.

**Options:**
- (None)

**Behavior:**
- Idempotent: succeeds even if proxy is not running
- Graceful: gives proxy time to flush logs and close connections
- Process-tree aware: kills all child processes spawned by the proxy

#### `worthless revoke [OPTIONS] ALIAS`
**Wipe an enrolled key (delete shards and all DB records).**

Permanently deletes Shard A from disk (with zero-fill to resist recovery), deletes Shard B and all associated records (enrollments, spend logs, time windows) from the database in a single atomic transaction.

**Options:**
- (None — takes alias as argument)

**Alias format:**
- Deterministic: `{provider}-{first8hexofsha256(key)}`
- Example: `openai-69ccc444`, `anthropic-a1b2c3d4`

**Behavior:**
- Zeroes Shard A contents before unlink (best-effort on CoW filesystems; full-disk encryption is the real protection)
- Atomic DB cleanup: all related records removed in one transaction
- Idempotent: succeeds even if alias doesn't exist

#### `worthless enroll [OPTIONS]`
**Enroll a single API key (scripting/CI primitive).**

Lower-level than `lock` — enrolls one key by alias without scanning `.env`. Designed for CI pipelines and scripts where the key comes from a secret manager.

**Options:**
- `--alias, -a NAME`: Key alias (required)
- `--key, -k VALUE`: API key (use --key-stdin instead to avoid shell history)
- `--key-stdin`: Read API key from stdin
- `--provider, -p NAME`: Provider name (required)

**Use case:** `echo "$OPENAI_KEY" | worthless enroll --alias ci-openai --provider openai --key-stdin`

#### `worthless mcp [OPTIONS]`
**Start the MCP server (stdio transport).**

Starts a Model Context Protocol (MCP) server over stdin/stdout. Available when installed with the `[mcp]` extra: `pip install worthless[mcp]`.

Exposes the following tools:
- `worthless_status()`: Show enrolled keys and proxy health (JSON)
- `worthless_lock(env_path)`: Lock all keys in a `.env` file (returns summary)
- `worthless_scan(paths, deep)`: Scan for exposed keys (returns SARIF or JSON)
- `worthless_spend(alias)`: Token spend history per key alias (JSON)

**Options:**
- (None)

**Use case:** Integration with Claude Code, Cursor, OpenClaw, or other AI agents; agents can query proxy status, run code through the proxy, and audit for exposed keys programmatically.

---

## Proxy Rules Engine

The proxy enforces a "gate-before-reconstruct" pipeline: rules are evaluated **before** the key is reconstructed. A denied request never causes the key to form in memory.

### SpendCapRule
**Denies requests when accumulated spend (in tokens) exceeds a per-key cap.**

- Queries `spend_log` table for cumulative tokens spent by the key alias
- Returns HTTP 402 Payment Required if cap is exceeded
- **Note**: Spend cap is a pre-check; actual spend is recorded in the metering layer after the upstream response. Concurrent requests may both pass the cap check before either records its spend (production fix: reserve estimated tokens at check time, reconcile after response).

### RateLimitRule
**Denies requests when the per-key rate exceeds a configured limit (requests per second).**

- Tracks request count in a sliding window
- Returns HTTP 429 Too Many Requests if rate is exceeded
- Per-key: each enrolled key has its own rate limit

### TokenBudgetRule
**Denies requests when token usage exceeds a daily, weekly, or monthly budget.**

- Queries `spend_log` table for tokens spent within the configured time window (day/week/month)
- Returns HTTP 429 Too Many Requests if any budget period is exceeded
- Supports independent daily, weekly, and monthly limits (any or all can be set)
- Configured at enrollment via `worthless lock --token-budget-daily=N`

### TimeWindowRule
**Denies requests outside of a configured time window (e.g., business hours only).**

- Checks current time against enrolled time window (start and end time in a configured timezone)
- Returns HTTP 403 Forbidden if outside the window
- Supports recurring schedules (e.g., "Monday–Friday, 9am–5pm EST")

### Custom Rules
Agents can extend the rules engine by implementing the `Rule` protocol:

```python
from typing import Protocol

@runtime_checkable
class Rule(Protocol):
    async def evaluate(
        self,
        alias: str,
        request: object,
        *,
        provider: str = "openai",
        body: bytes = b""
    ) -> ErrorResponse | None: ...
```

If `evaluate()` returns `None`, the request is allowed. If it returns an `ErrorResponse`, the proxy rejects the request with that error code and message.

---

## MCP Tools

When running `worthless mcp`, these tools are exposed:

### `worthless_status() -> str`
Show enrolled keys and proxy health. Returns JSON:
```json
{
  "keys": [
    {"alias": "openai-69ccc444", "provider": "openai"},
    {"alias": "anthropic-a1b2c3d4", "provider": "anthropic"}
  ],
  "proxy": {
    "running": true,
    "port": 8787,
    "health": "ok"
  }
}
```

### `worthless_lock(env_path: str = ".env") -> str`
Lock all keys in a `.env` file. Returns summary of keys locked.

### `worthless_scan(paths: list[str], deep: bool) -> str`
Scan files for exposed keys. Returns SARIF or human-readable findings.

### `worthless_spend(alias: str | None = None) -> str`
Token spend history. Pass alias for one key, omit for all. Returns JSON.

---

## Environment Variables

- `WORTHLESS_PORT`: Default port for `worthless up` (default: 8787)
- `WORTHLESS_DEBUG`: Enable debug logging (when set)
- `OPENAI_BASE_URL`: (Set by `wrap` and `up`) Proxy endpoint for OpenAI SDK
- `ANTHROPIC_BASE_URL`: (Set by `wrap` and `up`) Proxy endpoint for Anthropic SDK

---

## Database Schema

Worthless stores all state in `~/.worthless/worthless.db` (SQLite):

- `shards`: Encrypted Shard B + metadata (key_alias, provider, created_at)
- `enrollments`: Where each key is enrolled (env file, variable name, line number)
- `spend_log`: Token spend per key alias (for cap enforcement)
- `enrollment_config`: Per-key rules (spend cap, rate limit, time window)

All tables are ACID-transactional. Key deletion via `revoke` is atomic: all records for a single alias are removed in one transaction.

---

## File Locations

- `~/.worthless/`: Home directory for Worthless state
  - `worthless.db`: SQLite database (shards, enrollments, spend logs, rules)
  - `shards_a/`: Unencrypted Shard A files (one per enrolled key)
  - `proxy.pid`: PID and port of running proxy (when `up -d` is active)
  - `proxy.log`: Proxy logs (when `up -d` is active)

---

## Common Workflows

### Protect a key in local development
```bash
worthless lock
worthless wrap python your_app.py
```

### Keep proxy running for all your tools
```bash
worthless up -d
export OPENAI_BASE_URL=http://127.0.0.1:8787
# Now all your Python/Node/etc. code routes through the proxy
worthless status                  # check health
worthless down                    # stop when done
```

### Audit for exposed keys
```bash
worthless scan --deep
```

### Permanently delete a key
```bash
worthless revoke openai-69ccc444
```

### Restore original key from shards (temporary)
```bash
worthless unlock
# Your .env now has the real key
# Use it directly (not through proxy)
worthless lock
# Split again, back to decoy
```

### Pre-commit hook (scan before every commit)
```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: worthless-scan
        name: Scan for exposed API keys
        entry: worthless scan --format sarif
        language: system
        pass_filenames: false
```

---

## Integration with AI Agents

Agents (Claude Code, Cursor, OpenClaw) can invoke Worthless via:

1. **CLI shell commands** (primary):
   ```bash
   worthless status
   worthless scan --deep
   worthless wrap pytest
   ```

2. **MCP server** (when available):
   ```
   Tool: worthless_status() -> {"keys": [...], "proxy": {...}}
   Tool: worthless_scan(paths, deep) -> "key exposures found"
   Tool: worthless_wrap(command, args) -> "command output"
   ```

Agents should:
- Call `worthless status` to check if a proxy is running before issuing API calls
- Use `worthless scan` to audit code before committing
- Use `worthless wrap` to transparently route agent code through the proxy, gaining all spending/rate/time controls
- Call `worthless lock` before version control to ensure keys are never committed

---

## Security Notes

- **Shard A** is stored unencrypted on your machine in `~/.worthless/shards_a/`. Full-disk encryption is required for protection at rest.
- **Shard B** is encrypted in the database, but only an encryption boundary — not a trust boundary. The proxy should run on trusted infrastructure.
- **Key Reconstruction** happens only in proxy memory during a single API call. Key is zero-filled immediately after use.
- **Spend Cap** is best-effort pre-check, not a hard enforcement boundary (production fix pending).
- For production deployments, see [SECURITY_RULES.md](SECURITY_RULES.md) for crypto constraints and threat model.
