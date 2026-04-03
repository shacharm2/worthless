# Phase 4: CLI - Research

**Researched:** 2026-03-26
**Domain:** CLI application (Typer + Rich), process lifecycle, .env rewriting, pre-commit integration
**Confidence:** HIGH

## Summary

Phase 4 builds six CLI commands (`lock`, `unlock`, `wrap`, `up`, `scan`, `status`) on top of the existing crypto, storage, and proxy layers. The codebase already has the core primitives: `split_key()`, `ShardRepository`, `enroll_stub.py`, `ProxySettings`, and the FastAPI proxy app with `/healthz` and `/readyz` endpoints. The CLI layer is orchestration, not new crypto or networking.

The primary technical challenges are: (1) prefix-preserving .env rewriting with atomic file replacement, (2) process lifecycle management for `wrap` (pipe-based death detection, signal forwarding, process groups), (3) decoy-aware scanning with entropy thresholding, and (4) SARIF output format for GitHub Code Scanning integration.

**Primary recommendation:** Use Typer 0.21+ for CLI framework, Rich for TTY output, python-dotenv for .env parsing (with custom atomic rewrite for the prefix-preserving replacement), and subprocess.Popen for process management in `wrap`/`up` commands.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **`lock` is the primary command** -- atomic lifecycle: find keys, split, rewrite .env, confirm. 90 seconds.
- `enroll` and `wrap` as lower-level composable primitives for scripting/CI
- `unlock` ships in v0.4.0 as manual undo
- Six commands total: `lock`, `unlock`, `wrap`, `up`, `scan`, `status`
- Key discovery: .env auto-scan for known API key patterns (sk-*, anthropic-*, AIza*, xai-*)
- Provider auto-detection via prefix patterns
- No vault, immediate delete from .env after shards confirmed
- Shard A storage: file-based, `~/.worthless/shard_a/{alias}` with `chmod 0600`
- Prefix-preserving .env rewriting: XOR only the suffix, keep prefix as-is
- First-run bootstrap: silent, creates `~/.worthless/` structure, Fernet key, SQLite DB
- Crash recovery via `.lock-in-progress` file and atomic .env rewrite (write-to-temp + `os.replace()`)
- Wrap auto-starts ephemeral proxy on OS-assigned random port
- `up` uses fixed default port 8787 with `--port` and `WORTHLESS_PORT` override
- Process lifecycle: Chrome pipe-based death detection + process groups (Python 3.11+)
- Graceful shutdown: SIGTERM to process group, wait, SIGKILL
- Core dumps disabled at startup (`resource.setrlimit`), `mlock()` key buffers
- Scan: fast (default, pre-commit) and deep (`--deep`) modes
- Scan decoy-awareness: reads `~/.worthless/` enrollment data to suppress known decoy keys
- Shannon entropy > 4.5 to skip placeholders
- LLM-key-specific only: sk-*, anthropic-*, AIza*, xai-*
- Exit codes: 0=clean, 1=unprotected found, 2=scan error
- Framework: Typer (confirmed)
- Output: Rich for interactive (TTY), plain for non-TTY
- Single Console wrapper, respects --json, --quiet, NO_COLOR, FORCE_COLOR, TTY detection
- Spinners/progress to stderr, stdout clean for piping
- `--format sarif` for scan
- Structured error codes (WRTLS-NNN)
- Never display partial key material
- `--json` only for scan and status (output commands), not imperative commands
- Pre-commit: `.pre-commit-hooks.yaml` primary, `--install-hook` fallback
- PID file + socket path for `up` mode state reconciliation
- 127.0.0.1 only, never 0.0.0.0

### Claude's Discretion
- .env format preservation details (comments, ordering, whitespace)
- Exact Rich spinner/progress bar design
- Internal module structure of the CLI package
- Test file organization
- Exact WRTLS-NNN error code numbering scheme

### Deferred Ideas (OUT OF SCOPE)
- Phase 4.1: Session Tokens (hotel card model for agent-native access)
- Honeypot/canary shards (requires callback endpoint)
- `--rotate` flag (v2)
- OS keychain shard_a storage (v2)
- Time-limited escrow recovery (v2)
- 3-of-3 Shamir splitting (v2)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CLI-01 | `worthless enroll` splits key, stores Shard A locally, sends Shard B to proxy | Existing `enroll_stub.py` provides the core flow. `lock` wraps this with .env discovery, bootstrap, prefix-preserving rewrite. `enroll` is the lower-level primitive |
| CLI-02 | `worthless wrap` sets env vars so API calls route through proxy | Wrap spawns proxy subprocess, polls /healthz, injects `*_BASE_URL` env vars, spawns child. Pipe-based death detection for cleanup |
| CLI-03 | `worthless status` shows protected keys and proxy health | Reads `~/.worthless/` enrollment data + hits proxy `/healthz`. Supports `--json` output |
| CLI-04 | `worthless scan` pre-commit hook detects leaked keys in code | Regex-based key pattern detection with entropy thresholding, decoy-awareness, SARIF output. Ships `.pre-commit-hooks.yaml` |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| typer | >=0.21 | CLI framework | Pre-selected in PRD. Type-hint driven, auto-completion, built on Click. Supports async commands natively since 0.19+ |
| rich | >=14.0 | Terminal output formatting | Industry standard for Python CLI output. TTY detection, NO_COLOR, spinners, tables. Used by Typer internally |
| python-dotenv | >=1.0 | .env file parsing | `set_key()` modifies only the target line, preserving comments and ordering. Atomic-safe for our write-to-temp + os.replace() pattern |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| cryptography | >=46.0.5 | Fernet key generation for bootstrap | Already a dependency. Bootstrap generates `Fernet.generate_key()` |
| aiosqlite | >=0.22.1 | SQLite access for enrollment | Already a dependency. Used by ShardRepository |
| uvicorn | >=0.34 | ASGI server for proxy subprocess | Already a dependency. `wrap` and `up` start proxy via uvicorn |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| python-dotenv | Hand-rolled .env parser | python-dotenv handles quoting, comments, multiline. Don't hand-roll |
| subprocess.Popen | asyncio.create_subprocess | Popen is simpler for signal forwarding and process groups. asyncio subprocess has macOS limitations |
| Rich Console | Click echo | Rich provides TTY detection, NO_COLOR, spinners out of the box |

**Installation:**
```bash
uv add typer[all] rich python-dotenv
```

Note: `typer[all]` includes `rich` and `shellingham` (shell detection for completions). Since `rich` is already a transitive dep of `typer[all]`, listing it explicitly ensures version pinning.

## Architecture Patterns

### Recommended Module Structure
```
src/worthless/cli/
├── __init__.py          # Typer app definition, command registration
├── app.py               # Main Typer app, top-level options (--quiet, --json)
├── commands/
│   ├── lock.py          # lock command (primary enrollment flow)
│   ├── unlock.py        # unlock command (undo)
│   ├── wrap.py          # wrap command (ephemeral proxy + child)
│   ├── up.py            # up command (standalone proxy daemon)
│   ├── scan.py          # scan command (key detection)
│   └── status.py        # status command (health check)
├── console.py           # Single Console wrapper (TTY/plain/json routing)
├── dotenv_rewriter.py   # Prefix-preserving .env rewriting
├── bootstrap.py         # First-run ~/.worthless/ initialization
├── process.py           # Process lifecycle (pipe death detection, signal forwarding)
├── scanner.py           # Key pattern detection, entropy, decoy-awareness
├── errors.py            # WRTLS-NNN structured error codes
└── enroll_stub.py       # (existing) Test utility, kept for backward compat
```

### Pattern 1: Single Console Wrapper
**What:** All CLI output goes through one Console abstraction that handles TTY vs plain vs JSON.
**When to use:** Every command that produces output.
**Example:**
```python
import sys
from rich.console import Console

class WorthlessConsole:
    """Routes output through Rich (TTY) or plain text (pipe/CI)."""

    def __init__(self, *, quiet: bool = False, json_mode: bool = False):
        self._quiet = quiet
        self._json_mode = json_mode
        # stderr for spinners/progress, stdout for data
        self._err = Console(stderr=True, no_color=self._no_color)
        self._out = Console(no_color=self._no_color)

    @property
    def _no_color(self) -> bool:
        import os
        return "NO_COLOR" in os.environ

    def status(self, message: str):
        """Spinner on stderr (suppressed in quiet mode)."""
        if self._quiet:
            return _nullcontext()
        return self._err.status(message)

    def print_result(self, data: dict):
        """Print structured result to stdout."""
        if self._json_mode:
            import json
            print(json.dumps(data))
        elif not self._quiet:
            self._out.print(data)
```

### Pattern 2: Atomic .env Rewrite with Prefix Preservation
**What:** Read .env, replace key value with prefix-preserving shard_a, write to temp file, `os.replace()`.
**When to use:** `lock` command after successful shard storage.
**Example:**
```python
import os
import tempfile
from pathlib import Path
from dotenv import dotenv_values

def rewrite_env_key(env_path: Path, var_name: str, new_value: str) -> None:
    """Atomically replace a single key's value in .env, preserving all other content."""
    content = env_path.read_text()
    # Use line-by-line replacement to preserve comments, ordering, whitespace
    lines = content.splitlines(keepends=True)
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{var_name}="):
            # Replace value, preserve any inline comment
            new_lines.append(f"{var_name}={new_value}\n")
        else:
            new_lines.append(line)

    # Atomic write: temp file + os.replace
    fd, tmp = tempfile.mkstemp(dir=env_path.parent, suffix=".env.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.writelines(new_lines)
        os.replace(tmp, env_path)
    except BaseException:
        os.unlink(tmp)
        raise
```

### Pattern 3: Pipe-Based Death Detection (wrap mode)
**What:** Parent holds write end of pipe, child proxy inherits read end. Pipe EOF = parent died = proxy self-terminates.
**When to use:** `wrap` command process lifecycle.
**Example:**
```python
import os
import signal
import subprocess
import sys

def wrap_with_lifecycle(proxy_cmd: list[str], child_cmd: list[str], env: dict) -> int:
    """Spawn proxy + child with pipe-based death detection."""
    # Create liveness pipe
    read_fd, write_fd = os.pipe()

    # Proxy inherits read_fd, monitors for EOF
    proxy_env = {**env, "WORTHLESS_LIVENESS_FD": str(read_fd)}
    proxy = subprocess.Popen(
        proxy_cmd,
        env=proxy_env,
        pass_fds=(read_fd,),
        process_group=0,  # Python 3.11+
    )
    os.close(read_fd)  # Parent closes read end

    # Wait for proxy health
    # ... poll /healthz ...

    # Spawn child
    child = subprocess.Popen(child_cmd, env=env, process_group=0)

    # Wait for child, then cleanup
    try:
        return child.wait()
    finally:
        os.close(write_fd)  # Signals proxy to exit
        proxy.terminate()
        try:
            proxy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proxy.kill()
```

### Pattern 4: Prefix-Preserving XOR
**What:** Split only the suffix of the API key, keep the prefix intact in the .env decoy value.
**When to use:** `lock` command when creating shard_a for .env replacement.
**Example:**
```python
# Known prefixes (public, not secret)
PREFIXES = {
    "openai": ["sk-proj-", "sk-"],
    "anthropic": ["sk-ant-", "anthropic-"],
    "google": ["AIza"],
    "xai": ["xai-"],
}

def split_preserving_prefix(api_key: str, provider: str) -> tuple[str, bytes]:
    """Split key, return (decoy_for_env, full_shard_a_bytes).

    The decoy has the original prefix + XOR'd suffix (same length, same charset).
    The full shard_a is the complete XOR shard for reconstruction.
    """
    prefix = detect_prefix(api_key, provider)
    suffix = api_key[len(prefix):]

    sr = split_key(api_key.encode())

    # Build decoy: original prefix + hex-encoded shard_a suffix portion
    # Must match original length and charset constraints
    shard_a_suffix = sr.shard_a[len(prefix):]
    decoy = prefix + shard_a_suffix.hex()[:len(suffix)]

    return decoy, bytes(sr.shard_a)
```

**Note on prefix-preserving XOR and HMAC:** The HMAC commitment is computed over the full original key bytes. Since XOR splitting operates on the full key (prefix included), the prefix bytes in shard_a are XOR'd just like the rest. The "prefix preservation" is a display concern for the .env decoy -- the actual shard_a file at `~/.worthless/shard_a/{alias}` contains the full XOR shard. The HMAC scheme is unaffected.

### Anti-Patterns to Avoid
- **Shell=True in subprocess:** Never use `shell=True` for `wrap` command spawning. Security risk and breaks signal forwarding.
- **Async subprocess on macOS:** `asyncio.create_subprocess_*` has limitations on macOS. Use `subprocess.Popen` with `process_group=0`.
- **Blocking the event loop in Typer async commands:** If using async Typer commands, subprocess management should use `asyncio.to_thread()` or stay synchronous.
- **Rich output to stdout:** Spinners, progress bars, and status messages MUST go to stderr. Only data output goes to stdout.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| .env parsing | Custom .env parser | python-dotenv | Handles quoting, comments, multiline, export prefix, UTF-8 BOM |
| CLI argument parsing | argparse boilerplate | Typer | Type hints, auto-completion, auto-help, subcommands |
| Terminal formatting | ANSI escape codes | Rich Console | TTY detection, NO_COLOR, Windows support, Unicode width |
| SARIF output | Custom JSON schema | Dict conforming to SARIF v2.1.0 spec | SARIF is a simple JSON schema -- no library needed, but follow spec exactly |
| Fernet key generation | Custom key derivation | `Fernet.generate_key()` | Already used in codebase, correct key size guaranteed |
| Shannon entropy | Custom entropy calc | `-sum(p * log2(p))` one-liner | Standard formula, no library needed, ~5 lines of code |

**Key insight:** The CLI layer is primarily orchestration of existing primitives. The crypto, storage, and proxy layers are done. Don't re-implement them.

## Common Pitfalls

### Pitfall 1: Signal Forwarding in wrap
**What goes wrong:** Child process receives SIGINT directly (terminal sends to foreground process group), proxy gets orphaned.
**Why it happens:** Default signal handling doesn't account for process group semantics.
**How to avoid:** Put proxy and child in a new process group (`process_group=0`). Forward SIGTERM/SIGINT to the process group. Use `signal.signal(signal.SIGINT, handler)` in the parent.
**Warning signs:** Proxy process still running after `wrap` exits. Port still bound.

### Pitfall 2: .env Rewrite Race Condition
**What goes wrong:** Two `lock` invocations simultaneously corrupt .env.
**Why it happens:** No file locking between concurrent CLI runs.
**How to avoid:** The `.lock-in-progress` file serves as an advisory lock. Check for it before starting, create it atomically with `O_CREAT | O_EXCL`.
**Warning signs:** Truncated .env file, missing variables.

### Pitfall 3: Decoy False Positives in Scan
**What goes wrong:** `scan` flags every .env as containing leaked keys because the decoy values look like real keys.
**Why it happens:** Prefix-preserving decoys are designed to be indistinguishable from real keys.
**How to avoid:** Scan MUST load `~/.worthless/` enrollment data and compare against known decoy values. No enrollment data = CI mode (flag everything).
**Warning signs:** Every commit blocked by pre-commit hook after first `lock`.

### Pitfall 4: Port Conflict on `up`
**What goes wrong:** `up` fails because port 8787 is already bound by a previous `up` instance.
**Why it happens:** No PID file cleanup after crash.
**How to avoid:** Check PID file on startup. If PID exists but process is dead, clean up and reclaim. If alive, error with actionable message.
**Warning signs:** "Address already in use" error with no guidance.

### Pitfall 5: Python str Copies of Key Material
**What goes wrong:** `api_key.decode()` creates an immutable `str` that cannot be zeroed.
**Why it happens:** HTTP headers require `str`, not `bytearray`.
**How to avoid:** This is a documented PoC limitation. Minimize the lifetime of `str` copies. Don't add MORE str copies in the CLI layer. All CLI-side key handling should use `bytearray`.
**Warning signs:** Key material visible in core dump or memory inspection.

### Pitfall 6: Typer and Async
**What goes wrong:** Typer async commands work but subprocess.Popen + asyncio event loop can conflict.
**Why it happens:** Mixing sync subprocess management with async Typer commands.
**How to avoid:** Keep `wrap` and `up` commands synchronous. Only use async for commands that need `await` (e.g., `lock` calling `ShardRepository`). Use `asyncio.run()` explicitly where needed rather than relying on Typer's async detection.
**Warning signs:** Event loop already running errors.

## Code Examples

### Typer App Setup
```python
# Source: Typer docs + project conventions
import typer

app = typer.Typer(
    name="worthless",
    help="Protect your API keys in 90 seconds.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,  # We handle errors ourselves
)

@app.callback()
def main(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress non-error output"),
):
    """Worthless: split-key protection for API keys."""
    pass

@app.command()
def lock(
    env_file: Path = typer.Option(Path(".env"), "--env", "-e", help="Path to .env file"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Override auto-detection"),
):
    """Protect all API keys in your .env file."""
    ...
```

### Entry Point Configuration (pyproject.toml)
```toml
[project.scripts]
worthless = "worthless.cli:app"
```

### .pre-commit-hooks.yaml
```yaml
- id: worthless-scan
  name: Worthless API Key Scanner
  entry: worthless scan --pre-commit
  language: python
  types: [text]
  stages: [pre-commit]
  pass_filenames: true
```

### Shannon Entropy Calculation
```python
import math
from collections import Counter

def shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of a string."""
    if not s:
        return 0.0
    counts = Counter(s)
    length = len(s)
    return -sum(
        (count / length) * math.log2(count / length)
        for count in counts.values()
    )
```

### SARIF Output Skeleton
```python
# Source: OASIS SARIF v2.1.0 spec
def sarif_output(findings: list[dict]) -> dict:
    return {
        "version": "2.1.0",
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "worthless-scan",
                    "version": "0.4.0",
                    "informationUri": "https://github.com/your-org/worthless",
                    "rules": [{
                        "id": "WRTLS-001",
                        "shortDescription": {"text": "Unprotected API key detected"},
                    }],
                }
            },
            "results": [
                {
                    "ruleId": "WRTLS-001",
                    "level": "error",
                    "message": {"text": f"Unprotected {f['provider']} API key in {f['file']}"},
                    "locations": [{
                        "physicalLocation": {
                            "artifactLocation": {"uri": f["file"]},
                            "region": {"startLine": f["line"]},
                        }
                    }],
                }
                for f in findings
            ],
        }],
    }
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| argparse | Typer (type-hint CLI) | 2020+ | Less boilerplate, auto-completion, auto-help |
| Click groups | Typer app + commands | 2020+ | Same underlying engine, cleaner API |
| Manual ANSI codes | Rich Console | 2020+ | Cross-platform, NO_COLOR standard |
| print() for CLI | Rich + stderr separation | 2022+ | Pipeable output, CI-friendly |
| Sync-only CLI | Typer async support | 0.19+ (Sep 2025) | Native async def commands |

**Deprecated/outdated:**
- `typer.run()` for single-command apps: Use `app()` with `@app.command()` for consistency
- `click.echo()` directly: Use Rich Console for formatting, or `print()` for plain output

## Open Questions

1. **Prefix-preserving decoy charset matching**
   - What we know: XOR produces arbitrary bytes. The hex encoding of shard_a suffix will have different charset than the original key (original uses alphanumeric, hex is 0-9a-f only).
   - What's unclear: Should the decoy use base62/base64 encoding to better match the original key's charset? Or is hex sufficient since the prefix already signals "this looks like a key"?
   - Recommendation: Use base64url encoding (matching OpenAI's key format) truncated to original suffix length. This produces a more realistic-looking decoy.

2. **mlock() feasibility in Python**
   - What we know: `mlock()` via ctypes is possible but limited to ~2.6MB. Python objects aren't guaranteed to stay at fixed addresses (GC can't be controlled).
   - What's unclear: Whether `mlock()` provides meaningful security given Python's memory model.
   - Recommendation: Implement `resource.setrlimit(RLIMIT_CORE, (0, 0))` for core dump prevention (straightforward). Defer `mlock()` to Rust reconstruction service. Document as PoC limitation.

3. **Typer async vs sync for wrap/up commands**
   - What we know: Typer 0.19+ supports async commands. But `wrap` and `up` are primarily subprocess management (synchronous).
   - What's unclear: Whether mixing async Typer with sync subprocess creates issues.
   - Recommendation: Make `lock`, `unlock`, `status` async (they call ShardRepository which is async). Make `wrap`, `up`, `scan` synchronous (subprocess management, file I/O).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ with pytest-asyncio 0.24+ |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/test_cli*.py -x --timeout=30` |
| Full suite command | `uv run pytest --timeout=60` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CLI-01 | lock splits key, stores shards, rewrites .env | integration | `uv run pytest tests/test_cli_lock.py -x` | No -- Wave 0 |
| CLI-01 | enroll (lower-level) stores shard_a + shard_b | unit | `uv run pytest tests/test_cli_enroll.py -x` | No -- Wave 0 |
| CLI-02 | wrap spawns proxy, injects env vars, runs child | integration | `uv run pytest tests/test_cli_wrap.py -x` | No -- Wave 0 |
| CLI-02 | wrap signal forwarding and cleanup | integration | `uv run pytest tests/test_cli_wrap.py::test_signal_forwarding -x` | No -- Wave 0 |
| CLI-03 | status shows enrolled keys and proxy health | unit | `uv run pytest tests/test_cli_status.py -x` | No -- Wave 0 |
| CLI-03 | status --json outputs machine-readable format | unit | `uv run pytest tests/test_cli_status.py::test_json_output -x` | No -- Wave 0 |
| CLI-04 | scan detects unprotected keys | unit | `uv run pytest tests/test_cli_scan.py -x` | No -- Wave 0 |
| CLI-04 | scan suppresses known decoys | unit | `uv run pytest tests/test_cli_scan.py::test_decoy_suppression -x` | No -- Wave 0 |
| CLI-04 | scan SARIF output format | unit | `uv run pytest tests/test_cli_scan.py::test_sarif_output -x` | No -- Wave 0 |
| CLI-04 | scan pre-commit hook integration | integration | `uv run pytest tests/test_cli_scan.py::test_precommit_hook -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_cli*.py -x --timeout=30`
- **Per wave merge:** `uv run pytest --timeout=60`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_cli_lock.py` -- covers CLI-01 (lock + enroll)
- [ ] `tests/test_cli_wrap.py` -- covers CLI-02 (wrap + process lifecycle)
- [ ] `tests/test_cli_status.py` -- covers CLI-03 (status + json output)
- [ ] `tests/test_cli_scan.py` -- covers CLI-04 (scan + decoy + SARIF + hook)
- [ ] `tests/test_dotenv_rewriter.py` -- covers prefix-preserving .env rewrite
- [ ] `tests/test_console.py` -- covers Console wrapper (TTY/plain/json routing)
- [ ] `tests/test_bootstrap.py` -- covers first-run ~/.worthless/ initialization
- [ ] `tests/test_process.py` -- covers pipe death detection + signal forwarding
- [ ] Add `typer`, `rich`, `python-dotenv` to dependencies in pyproject.toml
- [ ] Add `worthless = "worthless.cli:app"` to `[project.scripts]`

## Sources

### Primary (HIGH confidence)
- Codebase inspection: `src/worthless/cli/enroll_stub.py`, `crypto/splitter.py`, `storage/repository.py`, `proxy/app.py`, `proxy/config.py` -- existing primitives verified
- pyproject.toml -- current dependencies and test configuration verified
- Proxy endpoints: `/healthz` and `/readyz` exist (not `/health`)

### Secondary (MEDIUM confidence)
- [Typer PyPI](https://pypi.org/project/typer/) -- v0.21.0 (Jan 2026), async support since 0.19+
- [Rich Console docs](https://rich.readthedocs.io/en/latest/console.html) -- NO_COLOR, TTY detection, stderr routing
- [python-dotenv](https://pypi.org/project/python-dotenv/) -- `set_key()` preserves comments by modifying only target line
- [SARIF v2.1.0 spec](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html) -- OASIS standard
- [pre-commit.com](https://pre-commit.com/) -- `.pre-commit-hooks.yaml` format
- [Python subprocess docs](https://docs.python.org/3/library/subprocess.html) -- `process_group` parameter (3.11+)

### Tertiary (LOW confidence)
- mlock() in Python via ctypes -- feasibility uncertain, limited to ~2.6MB, GC complicates fixed addresses
- zeroize PyPI package -- uses Rust FFI for secure memory zeroing, not yet evaluated for this project

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- Typer, Rich, python-dotenv are well-established, versions verified
- Architecture: HIGH -- patterns derived from CONTEXT.md decisions + existing codebase patterns
- Pitfalls: HIGH -- process lifecycle, .env rewrite, and decoy-awareness pitfalls derived from design decisions
- Validation: MEDIUM -- test structure proposed but not yet implemented

**Research date:** 2026-03-26
**Valid until:** 2026-04-26 (stable domain, all libraries mature)
