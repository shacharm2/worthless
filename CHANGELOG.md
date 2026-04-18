# Changelog

All notable changes to Worthless are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [SemVer](https://semver.org/).

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
