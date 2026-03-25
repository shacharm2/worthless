# Pre-Commit Hooks Research (2025-2026)

Research for `.pre-commit-config.yaml` selection. This document explains the "why" behind each hook choice.

## Secret Detection: Why Gitleaks

Evaluated three contenders:

| Tool | Verdict | Notes |
|---|---|---|
| **gitleaks** (chosen) | Best balance | Fast (Go binary), low false positives, excellent regex library covering sk-*, anthropic-*, AIza*, xai-* patterns. Native pre-commit hook. Active maintenance (v8.22+). Covers SR-05 denylist patterns. |
| detect-secrets (Yelp) | Runner-up | Python-based, baseline/allowlist model reduces noise. Slower. Good for large orgs with many false positives to manage. Overkill for a focused repo like worthless. |
| trufflehog | Too heavy | Designed for full-repo scanning and CI pipelines. Entropy-based detection has higher false positive rate. Pre-commit hook exists but slower than gitleaks. Better as a CI-only tool. |
| git-secrets (AWS) | Legacy | Shell-based, AWS-focused patterns. Not maintained actively. Superseded by gitleaks/trufflehog. |

**Decision**: Gitleaks. Fastest, lowest false positives, covers our exact key patterns, native hook support.

### Custom gitleaks config (future)

If we need to add worthless-specific patterns (e.g., shard material patterns), create `.gitleaks.toml`:

```toml
[extend]
# Add worthless-specific patterns
[[rules]]
id = "worthless-shard"
description = "Potential shard material"
regex = '''shard[_-]?[ab]\s*=\s*["'][A-Za-z0-9+/=]{32,}["']'''
```

## Code Quality: Why Ruff (not black + flake8 + isort)

Ruff replaces black, flake8, isort, pyupgrade, and more. Single tool, 10-100x faster (Rust-based). Already in the project's dev dependencies. The `ruff-pre-commit` repo provides both linter and formatter hooks.

The project already configures ruff in pyproject.toml with:
- TID251 banning `random` module (SR-08)
- Python 3.12 target
- Line length 100

## Security Static Analysis: Bandit as Hook

Bandit is already in the qa dependencies. Running it as a pre-commit hook catches security issues before they reach CI. Configured with `--severity-level=medium` to avoid noise from low-severity findings.

**Why not semgrep as a hook**: Semgrep requires network access for `--config auto` (downloads rules from registry). This makes it unsuitable for a pre-commit hook that should work offline. Keep semgrep in CI only.

## Supply Chain Security

### pip-audit (pre-push stage)

Too slow for every commit (~5-10s). Runs on `pre-push` and only when dependency files change. Checks against PyPI advisory database and OSV.

### uv lock --check

Verifies the lockfile matches pyproject.toml. Catches the case where someone updates a dependency but forgets to regenerate the lockfile. Fast, local-only.

### What we evaluated but deferred

| Tool | Status | Notes |
|---|---|---|
| **sigstore** (Python signing) | Deferred | Useful for package publishing, not pre-commit. Adopt when publishing to PyPI. |
| **SLSA provenance** | Deferred | GitHub Actions workflow concern, not pre-commit. Add to CI when releasing. |
| **syft/grype** (Anchore) | Deferred | Container-level SBOM/scanning. Relevant when Dockerfiles exist (Phase 3+). |
| **cosign** (Sigstore containers) | Deferred | Container image signing. Relevant for Rust reconstruction service container. |
| **in-toto** | Deferred | Full supply chain attestation framework. Enterprise-grade, overkill for current phase. |
| **ossf-scorecard** | CI only | GitHub Action that scores repo security posture. Not a pre-commit hook. |

### lockfile-lint equivalent for Python

Node.js has `lockfile-lint` which enforces that all packages come from approved registries. For Python/uv, `uv lock --check` verifies integrity. For registry pinning, configure `uv.toml` or `pyproject.toml` with `[tool.uv]` index settings when needed.

## Worthless-Specific Hooks

### Security rules enforcement

Custom hook validates SR-01/04/07/08 at the source level:
- **SR-07**: Catches `== digest` or `== hmac_` patterns that should use `hmac.compare_digest`
- **SR-08**: Catches `random.` usage (backup for ruff TID251 rule)

Not checked in pre-commit (too complex for regex):
- **SR-01** (bytearray vs bytes): Requires semantic analysis. Covered by code review and tests.
- **SR-02** (explicit zeroing): Covered by tests asserting zeroed buffers.
- **SR-03** (gate before reconstruct): Architectural, covered by integration tests.

### Private repo segmentation

Blocks references to `worthless-cloud` in committed files. This enforces the segmentation rule from CLAUDE.md.

## Commit Standards

`conventional-pre-commit` enforces conventional commit format. The allowed prefixes match the project's existing conventions from CLAUDE.md: `feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`, `ci:`, `perf:`, `build:`, `security:`.

## Commit Signing

Not enforced via pre-commit. Commit signing (GPG/SSH) is a git config concern, not a hook concern. Recommend configuring at the developer level:

```bash
git config commit.gpgsign true
git config gpg.format ssh
git config user.signingkey ~/.ssh/id_ed25519.pub
```

Enforce via branch protection rules on GitHub (require signed commits).

## Installation

```bash
# Add pre-commit to dev dependencies
uv add --optional dev pre-commit

# Install hooks
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
uv run pre-commit install --hook-type pre-push

# Verify all hooks pass on existing code
uv run pre-commit run --all-files
```

## Hook Execution Timing

| Stage | Hooks | Approx time |
|---|---|---|
| pre-commit | gitleaks, ruff, file hygiene, bandit, security rules, segmentation | ~3-5s |
| commit-msg | conventional-pre-commit | <1s |
| pre-push | pip-audit, uv lock check | ~5-10s |

Total pre-commit overhead: under 5 seconds for a typical change.
