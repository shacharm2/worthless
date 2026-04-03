# Phase 4: CLI - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning
**Version target:** v0.4.0

<domain>
## Phase Boundary

Six CLI commands (`lock`, `unlock`, `wrap`, `up`, `scan`, `status`) that give a developer API key protection in 90 seconds from the terminal. Plus: first-run bootstrap, crash recovery, pre-commit hook integration, and prefix-preserving .env rewriting.

**Out of scope for Phase 4:**
- Session token endpoint (Phase 4.1 — agent-native access method)
- Honeypot/canary shards (backlog)
- Key rotation `--rotate` flag (v2)
- OS keychain storage (v2, file-based with 0600 perms for PoC)
- Git history scanning (recommend gitleaks/trufflehog)
- WOR-12 (spend cap TOCTOU), WOR-17 (chunked body), WOR-14 (E2E tests) — bundled into Phase 4 planning as proxy hardening prep

</domain>

<decisions>
## Implementation Decisions

### Command Architecture

- **`lock` is the primary command** — the atomic lifecycle that finds keys, splits them, rewrites .env, and confirms. One command, 90 seconds, done.
- `enroll` and `wrap` exist as lower-level composable primitives for scripting/CI/advanced use
- `unlock` ships in v0.4.0 as manual undo — reconstructs key from shards, restores .env, deletes shards
- Six commands total: `lock`, `unlock`, `wrap`, `up`, `scan`, `status`

### Enrollment / Lock Flow

- **Key discovery:** .env auto-scan for known API key patterns (sk-*, anthropic-*, AIza*, xai-*). Pipe/flag as fallback for non-.env workflows
- **Provider auto-detection:** Prefix patterns are unambiguous. Auto-detect silently with confirm gated on dev mode (auto-confirm in production). `--provider` flag to override
- **No vault, immediate delete:** Key destroyed from .env as soon as shards are confirmed stored. Recovery path = provider dashboard. No safety net, no vault, no flag
- **Shard A storage:** File-based, `~/.worthless/shard_a/{alias}` with `chmod 0600`. Keychain is v2. Document limitation in security posture
- **Key rotation:** `--rotate` deferred. v1: user re-runs `lock` with new key after rotating at provider. Old alias overwritten

### Prefix-Preserving .env Rewriting

- API key prefixes (sk-proj-, sk-ant-, anthropic-, AIza) are PUBLIC info, not secret
- XOR only the suffix (the secret part), keep the prefix as-is
- Result: `OPENAI_API_KEY=sk-proj-7f3a9b2e1d8c4f` — looks identical to a real key, same prefix, same length, same charset
- Attacker steals it, tries it, gets 401, thinks it's revoked. Zero entropy leaked on the secret portion
- `wrap` ignores this .env value — loads real shards from `~/.worthless/`
- This IS the actual shard_a with the original prefix preserved (not a cosmetic wrapper)
- **Researcher should verify:** prefix-preserving XOR doesn't interact badly with the HMAC commitment scheme

### First-Run Bootstrap

- `lock` detects first run (no `~/.worthless/` directory)
- Creates `~/.worthless/` structure, generates Fernet key, initializes SQLite DB
- All bootstrap steps silent with progress output, no interactive prompts
- Subsequent runs skip bootstrap, scan for new unprotected keys only
- Never re-split an already-locked key

### Crash Recovery

- `lock` writes `~/.worthless/.lock-in-progress` before starting, deletes after completing
- If present on next run: previous lock crashed — clean up orphaned shards/temp files and retry
- Atomic .env rewrite: write-to-temp + `os.replace()`. If it fails, original .env untouched

### Wrap Mechanics

- **Two access methods** (Phase 4 = wrap only, session tokens = Phase 4.1):
  1. **Wrap (env injection):** `worthless wrap python main.py` spawns child with `OPENAI_BASE_URL=http://127.0.0.1:{port}` injected. Per-session localhost auth token between proxy and child
  2. **Session tokens (Phase 4.1):** Hotel card model — `GET /v1/session/{alias}` returns short-lived token. Agent-native API for Claude Code, Cursor, MCP
- **Wrap auto-starts ephemeral proxy:** Spins up proxy, polls /health until ready, then spawns child. Zero separate setup
- **Port strategy:** OS-assigned random port for `wrap` (ephemeral, no conflicts). Fixed default 8787 for `up` (stable, discoverable). `--port` flag and `WORTHLESS_PORT` env var override for `up`. Actionable bind-failure error
- **Multi-key:** wrap reads all enrolled aliases, sets all provider base URLs simultaneously. All enrolled keys available through proxy
- **Signal handling:** Mirror child exit code. Forward signals to child, wait for exit, kill proxy
- **Proxy crash mid-session:** Let child continue, warn on stderr. Child gets connection refused on next API call (loud, obvious). Don't kill child — might be mid-write

### Process Lifecycle (wrap mode)

- Chrome's pipe-based death detection + process groups (industry standard, cross-platform)
- Proxy + child in same process group (Python 3.11+ `process_group=0`)
- Liveness pipe: wrap holds write end, proxy inherits read end. Pipe EOF = wrap died = proxy self-terminates. Only cross-platform mechanism that survives SIGKILL
- Graceful shutdown: SIGTERM to process group, wait, then SIGKILL (nginx/foreman pattern)
- On Linux: additionally use `prctl(PR_SET_PDEATHSIG)` for defense-in-depth. No macOS equivalent — pipe is primary

### Process Lifecycle (up mode)

- Tailscale's state reconciliation pattern
- PID file + socket path written on startup
- On restart: detect stale PID file, clean up, reconcile state
- Clients detect broken connection (TCP RST / socket EOF)
- `up -d` for daemon mode, `up` for foreground (Ctrl+C to stop)
- All modes bind 127.0.0.1 only, never 0.0.0.0

### Key Material on Crash

- Disable core dumps at startup (`resource.setrlimit`)
- `mlock()` key buffers to prevent swap
- Accept that Python `str` in HTTP headers is unzeroable — documented PoC limitation, Rust reconstruction service fixes this in Harden milestone

### Scan & Pre-commit

- **Two modes:**
  - **Fast (default, pre-commit):** Working tree + staged files + .env/.env.local. Milliseconds. Blocks commit if real key found
  - **Deep (`--deep`, manual/CI):** Everything in fast mode + os.environ + common config files. For git history: recommend gitleaks/trufflehog
- **Decoy-awareness (critical):** scan reads `~/.worthless/` enrollment data to suppress known decoy keys. Without this, every .env commit is a false positive because decoys look like real keys
- **Entropy thresholding:** Shannon entropy > 4.5 to skip placeholders like `sk-your-key-here`
- **LLM-key-specific only:** sk-*, anthropic-*, AIza*, xai-*. Don't scan for AWS/SSH/DB secrets — that's gitleaks territory. Worthless scan's moat is decoy-awareness
- **CI scan:** No ~/.worthless/ needed. Plain regex on committed files. Any key pattern in source = block. .env is gitignored so never reaches CI
- **Known gaps (documented):** base64-encoded keys, string concatenation bypasses

### Scan Output

- Show ALL findings (protected + unprotected), not just first
- Protected (decoy) keys shown as PROTECTED — confirms lock worked
- Fully masked by default (no suffix chars). `--show-suffix` for local debugging only
- Summary line at bottom with count
- Exit codes: 0 = clean, 1 = unprotected key found, 2 = scan error (ESLint/Semgrep convention)
- Context-aware action: TTY -> "Run: worthless lock". Non-TTY/CI -> "See: docs.worthless.dev/ci-setup"
- NO_COLOR respected

### Scan Hook Installation

- **Primary:** pre-commit framework integration — ship `.pre-commit-hooks.yaml`
- **Fallback:** `worthless scan --install-hook` writes directly to `.git/hooks/pre-commit`
- Both paths supported — two lines of code difference

### CLI Framework & Output

- **Framework:** Typer (pre-selected in PRD, confirmed)
- **Output:** Rich for interactive (TTY), plain for non-TTY. Single Console wrapper routes all Rich output through one abstraction that respects `--json`, `--quiet`, NO_COLOR, FORCE_COLOR, TTY detection
- **Spinners/progress to stderr, stdout clean for piping**
- **`--quiet` flag** for scripts (suppress all non-error output)
- **`--format sarif`** for scan (free GitHub Code Scanning integration, same as ruff/semgrep)
- **Structured error codes (WRTLS-NNN)** in both human and JSON output — agents branch on codes not English
- **Never display partial key material** — use aliases or key fingerprints
- **`--json` scope rule:** If command's value is its side effect (lock, unlock, wrap, up) -> no --json. If value is its output (scan, status) -> ship --json. Exit codes + WRTLS-NNN cover machine readability for imperative commands

| Command | Value is... | --json? | --format sarif? |
|---------|-------------|---------|-----------------|
| lock | Side effect (splits key) | No | No |
| unlock | Side effect (restores) | No | No |
| wrap | Side effect (runs child) | No | No |
| up | Side effect (starts proxy) | No | No |
| scan | Output (list of findings) | Yes | Yes |
| status | Output (proxy health) | Yes | No |

### `status` Command

- `worthless status --json` emits `{"port": N, "mode": "up"}` so agents can discover the endpoint programmatically
- Shows which keys are protected, proxy health, enrolled providers

### Claude's Discretion

- .env format preservation details (comments, ordering, whitespace)
- Exact Rich spinner/progress bar design
- Internal module structure of the CLI package
- Test file organization
- Exact WRTLS-NNN error code numbering scheme

</decisions>

<specifics>
## Specific Ideas

- "Think `docker compose up` vs manually running each container" — lock is the docker compose up of key protection
- Process lifecycle patterns sourced from Chrome (pipe-based death detection), nginx/foreman (graceful shutdown), Tailscale (state reconciliation), Docker/supervisord
- Research exists at `.planning/research/process-lifecycle-cleanup.md`
- Scan output modeled after ESLint/Semgrep conventions (exit codes, SARIF format)
- Port 8787 as the fixed default for `up` mode

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `enroll_stub.py`: Core split -> store flow exists as test utility. Lock command wraps this with .env discovery, bootstrap, crash recovery, and prefix-preserving rewrite
- `ProxySettings` dataclass: All proxy config loaded from WORTHLESS_* env vars. Lock needs to generate these during bootstrap
- `split_key()` in `crypto/splitter.py`: The XOR splitting primitive. Lock calls this
- `ShardRepository` in `storage/repository.py`: Shard B encrypted storage. Lock uses this for enrollment
- `proxy/app.py`: FastAPI proxy app. Wrap and up need to start this as a subprocess

### Established Patterns
- All config via environment variables (WORTHLESS_* prefix)
- bytearray for all key material (SR-01), explicit zeroing after use (SR-02)
- Fernet encryption for shard_b at rest
- `__repr__` redaction on crypto dataclasses (SR-04)
- Provider detection via request path (/v1/chat/completions = OpenAI, /v1/messages = Anthropic)

### Integration Points
- `pyproject.toml` [project.scripts]: needs `worthless` CLI entry point
- `.pre-commit-hooks.yaml`: new file for pre-commit framework integration
- `~/.worthless/` directory structure: new (created by bootstrap)
- Proxy `/health` endpoint: may need to be added if not present

</code_context>

<deferred>
## Deferred Ideas

- **Phase 4.1: Session Tokens** — Hotel card model for agent-native access. GET /v1/session/{alias} returns short-lived bearer token. Unblocks Claude Code, Cursor, MCP integration
- **Honeypot/canary shards** — Backlog. Requires Worthless-operated callback endpoint (Thinkst Canarytoken architecture). Ship after core is battle-tested
- **`--rotate` flag** — v2 convenience. Atomic old->new key swap
- **OS keychain shard_a storage** — v2. macOS Keychain, Linux secret-service
- **Time-limited escrow recovery** — v2 if users demand it
- **3-of-3 Shamir splitting** — v2 team/escrow concern

</deferred>

---

*Phase: 04-cli*
*Context gathered: 2026-03-26*
