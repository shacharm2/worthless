# Technology Stack

**Project:** Worthless (split-key API proxy)
**Researched:** 2026-03-14

## Recommended Stack

### Core Proxy Service (Python)

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Python | 3.12 | Runtime | Per PROJECT.md constraint. 3.12 has perf improvements and better typing. | HIGH |
| FastAPI | >=0.115, <1.0 | HTTP framework, proxy routing | Async-native, SSE/streaming support (added in recent releases), Starlette 1.0+ backing. Same author ecosystem as Typer. Pin `>=0.115` to get `strict_content_type` control needed for proxying arbitrary provider requests. | HIGH |
| Uvicorn | >=0.32 | ASGI server | Standard FastAPI deployment. Supports `--ssl-cert-reqs=2` for mTLS enforcement. Hypercorn is the alternative if HTTP/2 becomes required, but Uvicorn is simpler for PoC. | HIGH |
| httpx | >=0.28 | Upstream HTTP client | Async streaming (`aiter_bytes`), connection pooling, mTLS client certs. The proxy pattern: receive request via FastAPI, forward via `httpx.AsyncClient.stream()`, return `StreamingResponse`. Do NOT use `requests` (sync-only). | HIGH |
| Pydantic | >=2.12 | Data validation, settings | FastAPI's native validation layer. Use `pydantic-settings` for env-based config. v2 is 10-50x faster than v1 via Rust core. | HIGH |

### CLI / Sidecar (Python)

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Typer | >=0.12 | CLI framework | Type-hint-driven, built on Click, auto-completion, same-author as FastAPI. `worthless enroll`, `wrap`, `scan`, `status` all map cleanly to Typer commands/subcommands. Do NOT use the deprecated `typer-cli` package -- install `typer` directly. | HIGH |
| Rich | >=14.0 | Terminal output | Tables, colored output, progress bars for enrollment confirmation. Typer uses Rich internally. "N keys protected. Done." output benefits from Rich formatting. | HIGH |
| keyring | >=25.0 | OS keychain access | Store Shard A in macOS Keychain / Linux Secret Service / Windows Credential Locker. Platform-agnostic API. Do NOT roll custom keychain integration. | MEDIUM |

### Database (PoC Phase)

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| SQLite | (stdlib) | Local storage | Zero-dependency, single-file, perfect for local-only proxy. Stores enrollment records, shard metadata, audit logs. | HIGH |
| aiosqlite | >=0.20 | Async SQLite bridge | Wraps sqlite3 for asyncio compatibility with FastAPI. Single shared thread per connection, no blocking event loop. | HIGH |

**Do NOT use SQLAlchemy for PoC.** Direct aiosqlite queries are simpler for the 3-4 tables needed (enrollments, audit_log, config). SQLAlchemy adds unnecessary abstraction at this scale. Reconsider when adding PostgreSQL for hosted mode.

### Cryptography

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| `os.urandom` | (stdlib) | Nonce generation | CSPRNG, no external dependency. Used for generating the random pad in XOR split. | HIGH |
| `hmac` + `hashlib` | (stdlib) | Commitment scheme | HMAC-SHA256 for the enrollment commitment (proves client holds Shard A without revealing it). Standard library, auditable, no supply chain risk. | HIGH |
| `secrets` | (stdlib) | Token generation | `secrets.token_urlsafe()` for enrollment tokens, session IDs. Preferred over `os.urandom` for string-safe tokens. | HIGH |

**Do NOT use PyCryptodome or `cryptography` for the XOR split itself.** XOR is a single Python operation (`bytes(a ^ b for a, b in zip(key, pad))`). Adding a crypto library for XOR is over-engineering. Use stdlib `hmac`/`hashlib` for the commitment hash. Reserve `cryptography` (pyca) only if you need AES encryption of Shard B at rest in a later phase.

### Reconstruction Service (Rust -- Hardening Phase)

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Rust | stable (1.80+) | Reconstruction runtime | Memory safety, no GC, deterministic cleanup. The reconstructed key lives in Rust memory only. | HIGH |
| axum | 0.8.x | HTTP framework | Tokio-based, ergonomic, strong typing. Handles the internal RPC from Python proxy. | HIGH |
| reqwest | >=0.12 | Upstream HTTP client | Makes the actual call to OpenAI/Anthropic after reconstruction. Streaming support. | HIGH |
| zeroize | >=1.8 | Memory zeroing | `Zeroizing<String>` wrapper auto-zeros key material on drop. Uses `write_volatile` + memory fences to prevent compiler optimization. Critical for the security claim "key never persists in memory." | HIGH |
| secrecy | >=0.8 | Secret wrapper types | `SecretString` / `SecretVec` prevent accidental logging/display of key material. Complements zeroize. | MEDIUM |
| tokio | >=1.40 | Async runtime | Standard Rust async runtime. Axum is built on it. | HIGH |

### Testing

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| pytest | >=8.0 | Test framework | Standard Python testing. Use with `pytest-asyncio` for async test functions. | HIGH |
| pytest-asyncio | >=0.24 | Async test support | `@pytest.mark.asyncio` decorator for testing async FastAPI endpoints and httpx calls. | HIGH |
| respx | >=0.21 | httpx mocking | Mock upstream provider calls (OpenAI, Anthropic) in tests. Purpose-built for httpx, supports async. Note: with httpx 0.28+, use `using="httpx"` in respx context. | HIGH |
| httpx (TestClient) | -- | FastAPI integration tests | `httpx.AsyncClient` with `ASGITransport` for async endpoint testing. Built into FastAPI's test patterns. | HIGH |
| pytest-cov | >=5.0 | Coverage | Track test coverage. Target 90%+ for crypto and proxy paths. | MEDIUM |

### Build / Project Management

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| uv | >=0.5 | Package manager + venv | 10-100x faster than pip. Lockfile support (`uv.lock`). Python version management built in. The 2025/2026 standard for new Python projects. | HIGH |
| Hatchling | >=1.25 | Build backend | PEP 621 compliant, used as `[build-system]` in pyproject.toml. uv defaults to hatchling. Lightweight, no runtime dependency. | MEDIUM |
| Ruff | >=0.8 | Linter + formatter | Replaces flake8 + black + isort. Same team as uv (Astral). Single tool, fast. | HIGH |
| pre-commit | >=4.0 | Git hooks | Run ruff, type checks, and `worthless scan` (the secret-scanning hook) on commit. | MEDIUM |
| mypy | >=1.13 | Type checking | Catch type errors in proxy routing, Pydantic models. Use `--strict` for crypto modules. | MEDIUM |

### Infrastructure (Deferred, Schema Only for PoC)

| Technology | Version | Purpose | When |
|------------|---------|---------|------|
| Redis | 7.x | Hot-path metering, rate limiting | Post-PoC, when spend caps are added |
| PostgreSQL | 16 | Multi-tenant storage | Hosted/team mode |
| Docker | -- | Container packaging | Post-local-validation |

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| HTTP client | httpx | aiohttp | httpx has cleaner API, same-ecosystem as FastAPI, built-in streaming. aiohttp is older and has its own event loop opinions. |
| CLI | Typer | Click | Typer IS Click underneath, but with type hints. Same power, less boilerplate. Same author as FastAPI -- ecosystem coherence. |
| CLI | Typer | argparse | Too verbose, no auto-completion, poor UX for the "90-second setup" target. |
| Package mgr | uv | Poetry | Poetry is slower, had PEP 621 support only since Jan 2025. uv is the clear 2025/2026 winner for new projects. |
| Package mgr | uv | Hatch | Hatch has no lockfile and no CLI dependency management. uv covers the same ground faster. Use Hatchling as build backend only. |
| Async SQLite | aiosqlite | SQLAlchemy async | Over-engineered for 3-4 tables. Direct SQL is simpler, more auditable for a security tool. |
| Crypto | stdlib hmac/hashlib | PyCryptodome | XOR split needs no library. HMAC-SHA256 is in stdlib. Adding PyCryptodome increases supply chain attack surface for a security product. |
| Rust HTTP | axum | actix-web | axum is lighter, Tokio-native, better type ergonomics. actix-web has its own runtime and is heavier for a single-purpose reconstruction service. |
| Terminal | Rich | colorama | Rich is a superset -- tables, panels, formatted output. colorama is just ANSI colors. |
| Testing | respx | pytest-httpx | respx is purpose-built for httpx mocking with better pattern matching and async support. pytest-httpx works but respx is more expressive for route-based mocking. |

## Installation

```bash
# Create project with uv
uv init worthless --python 3.12
cd worthless

# Core runtime
uv add fastapi[standard] httpx pydantic-settings aiosqlite typer rich keyring

# Dev dependencies
uv add --dev pytest pytest-asyncio pytest-cov respx ruff mypy pre-commit

# Rust reconstruction service (separate workspace, hardening phase)
# cargo init reconstruction --name worthless-reconstruct
# cargo add axum tokio reqwest zeroize secrecy
```

## Key Version Pins (pyproject.toml)

```toml
[project]
requires-python = ">=3.12"
dependencies = [
    "fastapi[standard]>=0.115",
    "httpx>=0.28",
    "pydantic-settings>=2.6",
    "aiosqlite>=0.20",
    "typer>=0.12",
    "rich>=14.0",
    "keyring>=25.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "respx>=0.21",
    "ruff>=0.8",
    "mypy>=1.13",
    "pre-commit>=4.0",
]
```

## Sources

- [FastAPI Release Notes](https://fastapi.tiangolo.com/release-notes/) -- FastAPI >=0.115, Starlette 1.0+ support, streaming/SSE
- [httpx PyPI](https://pypi.org/project/httpx/) -- v0.28.1 current stable
- [Pydantic PyPI](https://pypi.org/project/pydantic/) -- v2.12.5 stable, v2.13 in beta
- [Typer PyPI](https://pypi.org/project/typer/) -- v0.12+, replaces typer-cli
- [Rich GitHub](https://github.com/Textualize/rich) -- v14.3.3 (Feb 2026)
- [zeroize crate docs](https://docs.rs/zeroize/latest/zeroize/) -- v1.8.2, write_volatile + memory fence
- [axum GitHub](https://github.com/tokio-rs/axum) -- v0.8.x on crates.io
- [respx GitHub](https://github.com/lundberg/respx) -- httpx mocking, v0.21+
- [uv guide](https://pydevtools.com/handbook/explanation/uv-complete-guide/) -- 2025 standard Python package manager
- [Python hmac docs](https://docs.python.org/3/library/hmac.html) -- stdlib HMAC-SHA256
- [FastAPI mTLS guide](https://ahaw021.medium.com/mutual-tls-mtls-for-fastapi-with-uvicorn-and-hypercorn-7711cdc1567d) -- Uvicorn ssl-cert-reqs for mTLS
- [aiosqlite docs](https://aiosqlite.omnilib.dev/en/latest/) -- async SQLite bridge
