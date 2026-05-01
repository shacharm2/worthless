# Changelog

All notable changes to Worthless are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **`WORTHLESS_DEPLOY_MODE` trust-boundary contract** (WOR-345). Three modes — `loopback` / `lan` / `public` — pin host bind, X-Forwarded-Proto trust source, and whether `WORTHLESS_ALLOW_INSECURE` is even legal. `public` mode requires `WORTHLESS_TRUSTED_PROXIES` (validated as CIDR; placeholders are rejected at startup). PaaS auto-detection (RENDER / FLY_APP_NAME / KUBERNETES_SERVICE_HOST) refuses silent loopback default.

### Changed
- **BREAKING — Docker default bind is now loopback** (`127.0.0.1`), not `0.0.0.0`. The Dockerfile no longer hard-codes `--host 0.0.0.0`; bind is composed by `entrypoint.sh` from `WORTHLESS_DEPLOY_MODE`. `docker run -p 8787:8787 worthless` without setting the env var binds only inside the container — set `-e WORTHLESS_DEPLOY_MODE=lan` to restore network reachability behind a private network, or `=public` (with `WORTHLESS_TRUSTED_PROXIES`) for edge deployments. `deploy/docker-compose.env.example` and `deploy/render.yaml` updated accordingly.

### Fixed
- **OpenRouter API keys classified as `openrouter`** (worthless-lj0z, advances WOR-381). `sk-or-v1-...` and `sk-or-...` keys were previously mislabeled `openai` because the generic `sk-` prefix won the longest-first match; the new `openrouter` prefixes now beat it. **Behaviour change:** SARIF/scan consumers filtering on `provider == "openai"` for OpenRouter keys will see relabels to `provider == "openrouter"` — update filter logic accordingly. Detection only; per-enrollment proxy routing for `provider == "openrouter"` is tracked separately under `worthless-8rqs` and remains future work.
- **macOS Keychain prompts collapse to one per CLI invocation** (worthless-mnlp). `WorthlessHome.fernet_key` is now memoized per-instance via a private `_cached_fernet_key` field, so a single `worthless lock` triggers exactly one keychain ACL probe instead of 3+ (bootstrap probe + `ShardRepository` init + proxy env injection each previously hit `read_fernet_key` independently, and macOS re-evaluates `SecKeychainItemCopyContent` ACL on every call so 'Always Allow' didn't suppress later prompts). The bootstrap probe in `ensure_home()` now routes through the property so its read populates the cache; subsequent consumers within the same CLI invocation are served from cache. The property returns a *fresh bytearray copy* on each access so callers can `zero_buf()` their own copy (per SR-01) without poisoning the cache. Cache is process-scoped — new CLI invocations still re-prompt once.

## [0.3.0] — 2026-04-18

First release published to PyPI. `pip install worthless` now works.

### Added
- **Magic default command** (`worthless` with no arguments). Detects API keys in `.env`/`.env.local`, prompts to lock, starts the proxy daemon, reports healthy — zero-config first-time setup. `--yes` for non-interactive, `--json` for read-only state.
- **Format-preserving key split** (WOR-207 Phase 1). Shard A now preserves the original key's prefix, charset, and length, so scanners and SDK validators see a key-shaped token. Database migration included for existing installs.
- **Anthropic key authentication** (WOR-207 Phase 2). `x-api-key` support alongside OpenAI-style `Authorization: Bearer`.
- **SR-09 shard separation enforcement**. Pre-commit hook blocks commits that would co-locate Shard A and Shard B in any code path.
- **Pre-commit stack**: gitleaks, semgrep, bandit, pip-audit, ruff, codespell, actionlint, zizmor, SR-rule custom checks.
- **Docker e2e test suite**. Single-container and compose-stack fixtures; read-only container + tmpfs for `/tmp`.
- **OpenClaw integration harness** (WOR-213). Live attack suite validates 9 attack vectors against real OpenAI + Anthropic.
- Security FAQ and threat-model docs (WOR-196).

### Changed
- `DEVELOPMENT.md` and `SECURITY.md` rewritten for doc accuracy — commands and paths now match the shipping CLI.
- Test suite no longer hardcodes version literals; assertions read from `importlib.metadata` / `pyproject.toml` to prevent drift.

### Fixed
- CodeQL alerts resolved: SR-01 bytearray usage, B603 subprocess hardening, tamper-detection test correctness, schema SQL formatting.
- CodeRabbit review comments across docs and CLI (WOR-196 #55, #58).

### Security
- All crypto operations use `bytearray` + explicit zeroing (SR-01).
- Constant-time comparisons on all digest/HMAC paths (SR-07).
- Gate evaluation strictly precedes shard reconstruction (SR-03).
- Published artifacts built via PyPI trusted publishing (OIDC, no long-lived tokens).

[0.3.0]: https://github.com/shacharm2/worthless/releases/tag/v0.3.0
