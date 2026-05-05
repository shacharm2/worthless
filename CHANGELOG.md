# Changelog

All notable changes to Worthless are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [SemVer](https://semver.org/).

## [Unreleased]

## [0.3.4] — 2026-05-06

The "make `worthless wrap` actually work end-to-end" hotfix. v0.3.3's HF10 fresh-install verification surfaced that `worthless wrap python main.py` on a clean machine left the child unable to reach the proxy: `lock` writes port 8787 to `.env` but `wrap` was binding a random port the child had no way to discover. The earlier "wrap works" verdict was contaminated by a stale `worthless up` daemon hogging 8787 across sessions. Fix: `wrap` now binds the same port `lock` wrote.

### Fixed
- **`worthless wrap` proxy now binds the port `lock` wrote to `.env`** (worthless-djoe, closes worthless-hyb1). Pre-fix `wrap` called `spawn_proxy(port=0)` (OS-random), which gave the proxy a port the child couldn't discover — post-8rqs `wrap` had stopped injecting `*_BASE_URL` vars into child env, so the only port the child saw was `.env`'s. With lock writing 8787 and wrap binding random, the child hit 8787 and got connection-refused. Now `wrap` calls `_resolve_port(None)` (same priority chain `lock` and `up` use: arg → `WORTHLESS_PORT` env → `8787` default), so child + proxy agree. Regression introduced in v0.3.3 / PR #127 (worthless-8rqs).
- **`worthless wrap` gives a clean error when its port is already in use.** If `worthless up` is already running on the same port, wrap names it: *"port 8787 is already serving a worthless proxy (`worthless up` is running). Either run your command directly (the daemon proxies it already), or stop the daemon and re-run wrap."* If a non-worthless process holds the port, wrap names that case too. `up` and `wrap` are alternatives, not combinable on the same port.

## [0.3.3] — 2026-05-05

The "make `curl install.sh | sh` actually work" release. v0.3.2 dogfood surfaced 9 hotfixes (HF1-9) covering keychain UX, scan/status correctness, unlock messaging, and orphan recovery. Plus install-matrix supply-chain hardening (WOR-317-320) so the release pipeline can't be tampered with via floating-tag base images.

### Added
- **`WORTHLESS_DEPLOY_MODE` trust-boundary contract** (WOR-345). Three modes — `loopback` / `lan` / `public` — pin host bind, X-Forwarded-Proto trust source, and whether `WORTHLESS_ALLOW_INSECURE` is even legal. `public` mode requires `WORTHLESS_TRUSTED_PROXIES` (validated as CIDR; placeholders are rejected at startup). PaaS auto-detection (RENDER / FLY_APP_NAME / KUBERNETES_SERVICE_HOST) refuses silent loopback default.
- **`worthless doctor --fix`** (HF7, worthless-3907, PR #128). Recovers stuck states from the v0.3.2 dogfood scenario where `unlock` reported "no keys" but `status` reported "PROTECTED". Surgical delete by `(alias, env_path)` tuple. Shared `cli/orphans.py` module owns the `is_orphan` predicate plus canonical user-facing phrases — no drift between scan/status/doctor wording.
- **`worthless lock` supports multiple providers side-by-side** (PR #127). One `.env` can have OpenAI + Anthropic + Google + xAI keys; each enrolls with its own per-provider upstream URL.
- **Per-key unlock messaging** (HF4, worthless-5u6y, PR #123). `worthless unlock` now prints `Restored {var} ({provider}, alias {alias_id}) → {env_path}` per key, plus skip lines for missing shard-A vars. Final summary `Restored N, skipped K`. No more silent batch-fatal exceptions.
- **Scanner scope docs** (HF6, worthless-8axm, PR #123). README + SKILL.md + threat model document the LLM-provider-keys-only scope; recommend gitleaks/trufflehog as companions for general secret detection.
- **Cross-command state-machine integration tests** (HF8, worthless-5koc, PR #120). Tests covering commitment-mismatch, partial-unlock, scan-orphan, status-orphan, unlock-db-wipe, unlock-no-db-row contracts.
- **Install matrix supply-chain hardening** (WOR-317-320, PR #132). Pinned base-image digests (`@sha256:...`) on every fixture, Astral installer SHA verification in `Dockerfile.ubuntu-with-uv`, BuildKit cache mount on uv-running RUN steps, non-root user fixture (`ubuntu-nonroot`), idempotency check fixture (`ubuntu-idempotency`) that runs `install.sh` twice and diffs snapshots. `install.sh` gains a fast-path: `WORTHLESS_VERSION` set + matches installed → return early before `uv tool install/upgrade` writes metadata.

### Changed
- **BREAKING — Docker default bind is now loopback** (`127.0.0.1`), not `0.0.0.0`. The Dockerfile no longer hard-codes `--host 0.0.0.0`; bind is composed by `entrypoint.sh` from `WORTHLESS_DEPLOY_MODE`. `docker run -p 8787:8787 worthless` without setting the env var binds only inside the container — set `-e WORTHLESS_DEPLOY_MODE=lan` to restore network reachability behind a private network, or `=public` (with `WORTHLESS_TRUSTED_PROXIES`) for edge deployments. `deploy/docker-compose.env.example` and `deploy/render.yaml` updated accordingly.
- **`worthless scan` JSON shape** (HF5, worthless-gmky, PR #131): bare-array → `{schema_version: 2, findings, orphans}`. Schema version is pinned exactly in a contract test. Consumers must update.

### Fixed
- **OpenRouter API keys classified as `openrouter`** (HF1, worthless-lj0z, PR #124, advances WOR-381). `sk-or-v1-...` and `sk-or-...` keys were previously mislabeled `openai` because the generic `sk-` prefix won the longest-first match; the new `openrouter` prefixes now beat it. **Behaviour change:** SARIF/scan consumers filtering on `provider == "openai"` for OpenRouter keys will see relabels to `provider == "openrouter"` — update filter logic accordingly. Detection only; per-enrollment proxy routing for `provider == "openrouter"` is tracked separately under `worthless-8rqs` and remains future work.
- **macOS Keychain prompts collapse to one per CLI invocation** (HF2, worthless-mnlp, PR #125). `WorthlessHome.fernet_key` is now memoized per-instance via a private `_cached_fernet_key` field, so a single `worthless lock` triggers exactly one keychain ACL probe instead of 3+. Cache populate uses a per-instance `threading.Lock` with double-checked init so concurrent first-readers collapse to a single `read_fernet_key` call. Cache is process-scoped — new CLI invocations still re-prompt once until "Always Allow" is granted.
- **`worthless scan` and `worthless status` no longer prompt for keychain on read-only paths** (HF3, worthless-cmpf, PR #126). New `_fernet_key_present(home)` gate at the scan path; placeholder `bytearray(b"")` for the unused parameter on missing-key paths. Scans + status checks now produce zero popups even on a brand-new machine.
- **`worthless status` and `worthless scan` flag broken DB rows as `BROKEN`** (HF5, worthless-gmky, PR #131) instead of lying that they're `PROTECTED`. Plain-English wording: `PROBLEM_PHRASE = "can't restore"`, `FIX_PHRASE = "worthless doctor --fix"`. Per-alias aggregation: `BROKEN` iff all enrollments are orphan; `PROTECTED` if any enrollment is healthy.
- **`worthless unlock` default `--env` no longer surfaces HF4's hard error on implicit defaults** (pnn2, PR #129). Implicit `--env=.env` doesn't trigger the per-key skip messaging path that was meant only for explicit user-named paths.

### Security
- **Pinned base-image digests across the install test matrix** (WOR-319). Every `FROM` line uses `@sha256:<digest>` instead of floating tags, so a compromised upstream tag cannot ship malware via our matrix.
- **Astral uv installer SHA verification** in `Dockerfile.ubuntu-with-uv` (WOR-319). The fixture awks `ASTRAL_INSTALLER_SHA256` out of `install.sh` at build time so the SHA stays in lockstep with `UV_VERSION`.

### Verified manually
- **HF9 (worthless-xw2m) keychain popup contract on dev machine, 2026-05-05**. Wiped keychain (drained 6 entries to 0) + `~/.worthless/`, fresh `uv tool install` from main, ran `worthless lock` + status / scan / unlock / lock / up / down. **0 popups across all commands.** HF2 unit + user-flow tests prove the 1-popup contract on truly clean machines via real-keyring spy. Phase-2 second-machine run is best-practice but not blocker.

### Known limitations
- **Manual `worthless up` after every reboot** until WOR-174 (macOS launchd) and WOR-175 (Linux systemd) ship in v1.1.
- **Docker app + host worthless** requires manually editing `.env` to use `host.docker.internal:8787` instead of `127.0.0.1:8787` — `worthless lock` writes `127.0.0.1` blindly today (filed as v1.2 work).
- **Two `worthless up` proxy bugs** discovered during HF9 manual QA: stale orphan proxy on port 8787 (`worthless-6gkb`) and `worthless up &` exits prematurely (`worthless-n8tj`). Both filed as v0.3.4 / v1.2 work; do not affect lock/scan/status/unlock paths.

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

[0.3.3]: https://github.com/shacharm2/worthless/releases/tag/v0.3.3
[0.3.0]: https://github.com/shacharm2/worthless/releases/tag/v0.3.0
