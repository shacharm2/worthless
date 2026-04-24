# MCP Capability Audit (for WOR-300)

Source files audited:
- MCP server: `src/worthless/mcp/server.py` (single file, FastMCP over stdio)
- CLI entry: `src/worthless/cli/app.py`
- CLI commands: `src/worthless/cli/commands/{lock,unlock,scan,status,wrap,up,down,revoke,mcp}.py`
- Tests: `tests/test_mcp_server.py`

MCP is an **optional extra** (`worthless[mcp]`). `register_mcp_commands` is wrapped in `try/except ImportError` so the `worthless mcp` subcommand only appears when `mcp.server.fastmcp.FastMCP` is installed.

## Current MCP tools exposed

Four tools, all registered via `@mcp.tool()` in `src/worthless/mcp/server.py`. All return `str` (JSON-serialized).

| Tool | Maps to CLI | Parameters | Returns |
|---|---|---|---|
| `worthless_status` | `worthless status` | (none) | JSON: `{keys: [{alias, provider}], proxy: {healthy, port, mode, requests_proxied}}` |
| `worthless_scan` | `worthless scan` | `paths: list[str] \| None = None`, `deep: bool = False` | JSON: `{findings: [{file, line, var_name, provider, is_protected, value_preview}], summary: {total, protected, unprotected}, enrollment_checker_available}` |
| `worthless_lock` | `worthless lock` | `env_path: str = ".env"` | JSON `{"locked": <count>}` (see `_lock_keys` in lock.py; wrapped via thread to avoid nested event loop) |
| `worthless_spend` | (no CLI equivalent) | `alias: str \| None = None` | JSON: `{spend: [{alias, provider, total_tokens, request_count}]}` (queries `spend_log` table) |

Tests (`tests/test_mcp_server.py`, 207 lines): `TestWorthlessStatus`, `TestWorthlessScan`, `TestWorthlessLock`, `TestWorthlessSpend` — each tool has 2-4 async test cases. All tests `pytest.importorskip("mcp")`.

## Current CLI commands

Registered in `src/worthless/cli/app.py` lines 72-109.

| Command | MCP equivalent? | Notes |
|---|---|---|
| `worthless` (default, no subcmd) | Partial | Calls `run_default` (scan → lock → up pipeline). MCP has no single "do-the-magic" tool; agent must call `scan` + `lock` + start proxy out-of-band. |
| `worthless lock` | Yes (reduced) | CLI: `--env`, `--provider`, `--token-budget-daily`, `--keys-only`. MCP only takes `env_path`. **Drops `provider`, `token_budget_daily`, `keys_only`.** |
| `worthless enroll` | No | Direct-key enrollment (`--alias --key/--key-stdin --provider`). Not exposed. |
| `worthless unlock` | No | Restores keys from shards. CLI: `--alias`, `--env`. |
| `worthless scan` | Yes (partial) | CLI: positional `paths`, `--deep`, `--pre-commit`, `--format {text,sarif,json}`, `--show-suffix`, `--install-hook`, `--json`. MCP only takes `paths` + `deep`. **Drops `--pre-commit`, `--install-hook`, format selection (always JSON).** |
| `worthless status` | Yes (full) | CLI has no options beyond global `--json`; MCP tool always returns JSON. Parity. |
| `worthless wrap COMMAND...` | No | Ephemeral proxy + child process (`python main.py` etc.). Inherently shell-bound — spawns a child. Not meaningful over stdio MCP. |
| `worthless up` | No | Start proxy daemon. CLI: `--port`, `--daemon`. No MCP start/stop tools. |
| `worthless down` | No | Stop proxy daemon. No MCP tool. |
| `worthless revoke` | No | Permanent key revoke. CLI: `--alias` (required). No MCP tool — **destructive**, blast-radius concern. |
| `worthless mcp` | N/A | Launches MCP server itself over stdio. |
| `worthless --version` | No | Version string. |

Global flags from `_main` callback: `--quiet`, `--json`, `--debug`, `--yes`, `--version`. MCP tools have no equivalent of `--yes` (auto-approve) or `--debug`.

## Gaps (CLI commands with no MCP equivalent)

1. **`up` / `down`** — Agents can't start or stop the proxy. `worthless_status` reports `proxy.healthy=false` but gives no tool to fix it. Blocks any agent-driven "set up a project" flow.
2. **`unlock`** — Agents can protect keys but can't restore them. Any recovery/offboarding path requires shell-out.
3. **`enroll`** — Direct programmatic enrollment (without a `.env` file). Agents can't register a key they hold in memory/config.
4. **`revoke`** — Destructive, but needed for key-compromise response playbooks. Appropriate MCP surface with explicit confirmation.
5. **`wrap`** — Inherently process-spawning; MCP equivalent doesn't make sense over stdio. Leave as CLI-only.
6. **Default pipeline (bare `worthless`)** — No "zero-config bootstrap" MCP tool; agent must stitch `scan → lock → up` itself.
7. **Parameter gaps on existing tools**:
   - `worthless_lock` missing: `provider`, `token_budget_daily`, `keys_only`.
   - `worthless_scan` missing: `format` (sarif), `pre_commit`, `install_hook`, `show_suffix`.
8. **Structured errors** — MCP tools raise `WorthlessError` but return plain `json.dumps(...)` strings on success. No standardized `{ok, data, error}` envelope; callers must parse free-form JSON.

## Recommendations for WOR-300 era

### 1. Minimum MCP parity to ship WOR-300 (P3 AI-agent persona)
Add these tools so an agent can drive worthless end-to-end without shell-outs:
- `worthless_up(port: int | None = None, daemon: bool = True) -> str` — start proxy; return `{pid, port, mode}`.
- `worthless_down() -> str` — stop proxy; idempotent; return `{stopped: bool, was_pid}`.
- `worthless_unlock(alias: str | None = None, env_path: str = ".env") -> str` — mirror CLI.
- `worthless_enroll(alias: str, key: str, provider: str) -> str` — programmatic enrollment. Handle secret carefully — accept via MCP param, zero-buffer immediately.
- Expand `worthless_lock` params to match CLI: `provider`, `token_budget_daily`, `keys_only`.
- Expand `worthless_scan` to accept `format: Literal["json","sarif"] = "json"` and `pre_commit: bool = False`.

### 2. Can defer
- `worthless_revoke` — destructive; defer until a confirmation/two-step-approval pattern is designed. Agents should prefer `unlock` + re-lock for rotation.
- `worthless_wrap` — process-spawning model doesn't fit MCP; keep CLI-only.
- `worthless_bootstrap` (default pipeline) — compose from `scan/lock/up` in the agent prompt rather than ship a mega-tool.
- `worthless_version` — trivial; `status` can be extended to include it.

### 3. Structured output (`--json`) status per tool
| Tool | CLI `--json` | MCP always-JSON | Schema documented? |
|---|---|---|---|
| `status` | yes (`_main --json`) | yes | No — informal dict |
| `scan` | yes (`--json` / `--format json`) | yes | No — informal dict |
| `lock` | no | yes (count only) | No |
| `spend` | no CLI | yes | No |

**Gap:** No Pydantic/JSON-schema contract. For P3, agents need stable schemas to chain tool calls. Recommend: introduce `worthless.services.*` layer (TODO(WOR-126) already noted in `server.py`) returning typed models, then serialize in both CLI and MCP.

### 4. Exit code / error surface status
- CLI: rich `ErrorCode` enum (`BOOTSTRAP_FAILED`, `KEY_NOT_FOUND`, `PROXY_UNREACHABLE`, `PORT_IN_USE`, `SCAN_ERROR`, `WRAP_CHILD_FAILED`, `SHARD_STORAGE_FAILED`, etc.) mapped to exit codes via `@error_boundary`.
- MCP: `WorthlessError` propagates through FastMCP as an exception → becomes an MCP error response. Error codes are **not** preserved in a structured way an agent can branch on.
- **Recommendation:** wrap every MCP tool return in `{ok: bool, error_code?: str, message?: str, data?: {...}}` so agents can handle `KEY_NOT_FOUND` vs `PROXY_UNREACHABLE` programmatically.

### 5. Architectural note
`server.py` has `TODO(WOR-126): move _list_enrolled_keys, _check_proxy_health into worthless.services.status so both CLI and MCP import public API.` This extraction is a **prerequisite** for safe MCP parity — currently MCP tools reach into `worthless.cli.commands.*` private helpers (`_lock_keys`, `_collect_fast_paths`, etc.), which couples them to CLI-internal refactors. WOR-300 should land WOR-126 (or its successor) first.
