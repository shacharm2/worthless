# Modern Pre-Commit Hook Landscape (2025-2026)

Research date: 2025-03-25

---

## 1. Most Popular/Trending Pre-Commit Hooks (Python)

### Tier 1 — Universal adoption
| Hook | Language | Replaces | Notes |
|------|----------|----------|-------|
| **Ruff** (astral-sh/ruff-pre-commit) | Rust | Black, Flake8, isort, pyupgrade, bandit (partial), mccabe | Single tool for linting + formatting. De facto standard in 2025. |
| **Gitleaks** | Go | detect-secrets, trufflehog (at commit time) | Fast secret scanner. Lightweight, easy config via `.gitleaks.toml`. |
| **pre-commit/pre-commit-hooks** | Python | — | Bundle: check-yaml, end-of-file-fixer, trailing-whitespace, debug-statement-hook, check-merge-conflict, etc. |

### Tier 2 — Common in serious projects
| Hook | Purpose |
|------|---------|
| **mypy** / **pyright** | Type checking (see section 7) |
| **pip-audit** | Dependency vulnerability scanning |
| **codespell** | Typo detection in code and docs |
| **commitizen** / **conventional-pre-commit** | Commit message format enforcement |
| **prettier** | Non-Python file formatting (YAML, JSON, Markdown) |

### Tier 3 — Emerging / niche
| Hook | Purpose |
|------|---------|
| **semgrep** | Custom static analysis rules |
| **sqlfluff** | SQL linting |
| **hadolint** | Dockerfile linting |
| **taplo** | TOML formatting (Rust) |
| **deptry** | Detect missing/unused/transitive dependencies |

---

## 2. New Hooks That Emerged in 2025

### prek (Rust-based pre-commit replacement)
- **What:** Drop-in replacement for `pre-commit`, written in Rust by j178.
- **Performance:** ~10x faster execution, 2x faster installs, half the disk space.
- **Key features:** Uses `uv` for Python virtualenvs, parallel hook execution by priority, built-in Rust implementations of common hooks, monorepo/workspace mode, `repo: builtin` for offline zero-setup hooks.
- **Adoption:** Already used by CPython, Apache Airflow, FastAPI, Home Assistant.
- **Compatibility:** Reads existing `.pre-commit-config.yaml` files — true drop-in.
- **Source:** https://github.com/j178/prek

### Betterleaks (Gitleaks successor)
- **What:** Next-gen secrets scanner by the original Gitleaks author (zricethezav).
- **Performance:** 4-5x faster than Gitleaks on large repos.
- **Source:** https://appsecsanta.com/betterleaks

### deptry
- **What:** Detects missing, unused, and transitive dependencies in Python projects.
- **Relevance to AI code:** Catches hallucinated imports that reference packages not in your dependency tree.

---

## 3. AI-Powered Pre-Commit Hooks

### Current state: "defense against AI, not AI in hooks"
The consensus in 2025 is that LLM-based hooks are **too slow for pre-commit** (latency > 5 seconds kills adoption). The recommended architecture:

| Layer | Tool | Latency |
|-------|------|---------|
| Pre-commit (local) | Fast static checks (Ruff, Gitleaks, type checkers) | < 2 sec |
| CI/CD (async) | AI-powered review (CodeRabbit, Sourcery, Trunk) | Minutes |
| PR review (async) | LLM-based deep review | Minutes |

### Notable tools
- **CodeRabbit** — AI code review on PRs, posts inline comments. Not a pre-commit hook.
- **Sourcery** — AI-powered code quality in CI. Has a pre-commit hook but it's the static rule engine, not the LLM.
- **Trunk** — Consolidates linters + formatters + security scanners into one pre-commit workflow. AI features are in their cloud CI product.
- **partcad/pre-commit** — AI commit message generator (uses LLM to write commit messages from staged diffs). Niche.

### Defense-in-depth pattern for AI-generated code
From Brooks McMillin's research:
1. Pre-commit hooks block insecure patterns (static)
2. Automated review agents catch context-blind mistakes (CI)
3. CI workflows fail loudly when LLMs generate problematic code

---

## 4. Security-Focused Hooks Beyond Gitleaks/Bandit/Semgrep

| Tool | What it catches | Notes |
|------|----------------|-------|
| **TruffleHog** | 800+ secret types with **verification** (tests if secrets are still active) | Best in CI, heavier than Gitleaks |
| **Betterleaks** | Same as Gitleaks but faster | By original Gitleaks author |
| **detect-secrets** (Yelp) | Secrets via baseline approach — only alerts on NEW secrets | Good for legacy codebases |
| **pip-audit** | Known vulnerabilities in Python dependencies | PyPA official tool, pre-commit hook available |
| **safety** (Lucas-C/pre-commit-hooks-safety) | Python dependency safety check | Older, pip-audit preferred now |
| **trivy** | Container, filesystem, and dependency scanning | Broad but slower |

### Supply chain attack context (2025)
- **PyPI Phishing Campaign (July 2025):** Targeted maintainers with credential-harvesting emails.
- **GhostAction Attack (Sept 2025):** Injected code into 570+ GitHub Actions workflows, stealing 3,300+ secrets.
- **Slopsquatting:** Attackers register package names that LLMs commonly hallucinate (38% are real-name conflations, 13% typo variants, 51% pure fabrications).

---

## 5. Community Recommendations (2026)

The "Ultimate Pre-Commit Hooks Guide for 2025" (Gatlen Culp, Medium) recommends this stack:

```yaml
# Minimal recommended stack
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks  # basics
  - repo: https://github.com/astral-sh/ruff-pre-commit     # lint + format
  - repo: https://github.com/gitleaks/gitleaks              # secrets
  - repo: https://github.com/pre-commit/mirrors-mypy        # types
  - repo: https://github.com/codespell-project/codespell    # typos
```

For security-conscious projects, add:
```yaml
  - repo: https://github.com/pypa/pip-audit               # dep vulns
  - repo: https://github.com/returntocorp/semgrep          # custom rules
```

---

## 6. Alternatives to the pre-commit Framework

| Tool | Language | Pros | Cons |
|------|----------|------|------|
| **prek** | Rust | 10x faster, drop-in compatible, uses uv, monorepo support | New (2025), smaller community |
| **Lefthook** | Go | Fast (parallel), single binary, no Python dependency | Smaller hook ecosystem, manual hook config |
| **Husky** | Node.js | Dominant in JS/TS ecosystem | Requires Node.js, not natural for Python |
| **lint-staged** | Node.js | Only checks staged files | JS-centric |
| **pre-commit** | Python | Largest ecosystem, most hooks, battle-tested | Slower, Python dependency |

### Verdict for Python projects
- **If staying with pre-commit ecosystem:** Switch to **prek** for speed while keeping all existing `.pre-commit-config.yaml` configs.
- **If polyglot or perf-critical:** Consider **Lefthook** but expect to write more manual hook configs.
- **JAX switched to Lefthook** (github.com/jax-ml/jax/issues/32846) — notable signal.

---

## 7. Type-Checking Hooks (mypy vs pyright)

Both are commonly used in pre-commit now, with caveats:

| Aspect | mypy | pyright |
|--------|------|---------|
| Speed | Slower | Faster |
| Strictness | Configurable | Stricter by default |
| Pre-commit support | Official mirror | Community wrapper (RobertCraigie/pyright-python) |
| Third-party deps | Mirror passes `--ignore-missing-imports` automatically (reduces quality) | Needs `venvPath`/`venv` config to find deps |
| Large projects | Several seconds, can frustrate devs | Faster but still non-trivial |

### Practical recommendation
Run type checking in **CI** rather than pre-commit for large projects. For small/medium projects, pyright is preferred for speed.

Configuration for pyright pre-commit:
```yaml
- repo: https://github.com/RobertCraigie/pyright-python
  rev: v1.1.408
  hooks:
    - id: pyright
```

---

## 8. Hooks for AI-Generated Code Issues

### The slopsquatting problem
LLMs hallucinate package names following predictable patterns. Attackers register these names with malicious code ("slopsquatting").

### Available mitigations as pre-commit hooks
| Approach | Tool | How |
|----------|------|-----|
| Validate imports exist | **deptry** | Catches imports of packages not in your dependency tree |
| Check deps against allowlist | Custom hook | Compare requirements against approved-packages.txt |
| Scan for known malicious packages | **pip-audit** | Checks against vulnerability databases |
| Detect phantom APIs | Static analysis + semgrep | Custom rules for non-existent module attributes |

### Missing: no dedicated "anti-hallucination" pre-commit hook exists yet
This is a gap in the ecosystem. A hook that cross-references every import statement against PyPI's actual package index would catch slopsquatting. The closest is `deptry` + `pip-audit` combined.

---

## 9. SPDX / License Compliance Hooks

| Tool | What it does | Pre-commit support |
|------|-------------|-------------------|
| **REUSE** (fsfe/reuse-tool) | Validates SPDX headers, downloads license texts, lints compliance | Yes, `reuse lint` as hook (v6.2.0) |
| **espressif/check-copyright** | Checks and adds SPDX headers | Yes, with `--replace` and `--config` args |
| **mz-lictools** | Maintains SPDX headers with author tracking and year updates | Yes |
| **ansys/pre-commit-hooks** | `add-license-headers` with custom templates | Yes |
| **FOSSA** | Full dependency license compliance + SBOM | CI tool, not pre-commit |

### Recommended for Worthless
REUSE is the most mature and standards-compliant option. Configuration:
```yaml
- repo: https://github.com/fsfe/reuse-tool
  rev: v6.2.0
  hooks:
    - id: reuse
```

---

## 10. Fast Rust/Go Hooks Replacing Python Tools

| Old (Python) | New (Rust/Go) | Speedup |
|-------------|--------------|---------|
| Black + Flake8 + isort | **Ruff** (Rust) | 10-100x |
| pre-commit framework | **prek** (Rust) | ~10x execution |
| pip (for hook envs) | **uv** (Rust, used by prek) | 10-100x installs |
| Gitleaks | **Betterleaks** (Go) | 4-5x |
| pylint | **Ruff** (Rust) | 50-100x |
| pyflakes | **Ruff** (Rust) | included |
| pyupgrade | **Ruff** (Rust) | included |
| TOML formatting | **taplo** (Rust) | N/A (no Python equivalent) |

---

## Recommendations for Worthless

Given the project's security-first nature and crypto/key-handling code:

### Immediate wins
1. **Switch to prek** — drop-in, 10x faster, uses uv (project already uses uv).
2. **Add Ruff** — replaces Black + Flake8 + isort + pyupgrade + partial bandit.
3. **Keep Gitleaks** (or evaluate Betterleaks) — critical for a key-management project.
4. **Add pip-audit** — supply chain protection, especially relevant given slopsquatting.
5. **Add deptry** — catches hallucinated imports from AI-assisted development.

### Security-specific
6. **Custom semgrep rules** — enforce SECURITY_RULES.md constraints (SR-01 through SR-08) at commit time.
7. **Existing custom hook** — keep the worthless-cloud reference blocker.

### Nice-to-have
8. **REUSE** — SPDX compliance if/when going open-source.
9. **pyright** in CI (not pre-commit) — type safety without commit-time friction.
10. **codespell** — catches typos in docs and code comments.

---

## Sources

- [Ultimate Pre-Commit Hooks Guide 2025](https://gatlenculp.medium.com/effortless-code-quality-the-ultimate-pre-commit-hooks-guide-for-2025-57ca501d9835)
- [prek — Rust pre-commit replacement](https://github.com/j178/prek)
- [prek documentation](https://prek.j178.dev/)
- [Home Assistant switches to prek](https://developers.home-assistant.io/blog/2026/01/13/replace-pre-commit-with-prek/)
- [Lefthook vs pre-commit](https://0xdc.me/blog/git-hooks-management-with-pre-commit-and-lefthook/)
- [JAX switches to Lefthook](https://github.com/jax-ml/jax/issues/32846)
- [AI-powered pre-commit hooks](https://brooksmcmillin.com/blog/coding-safer-with-llms/)
- [Pre-commit hooks are back thanks to AI](https://briandouglas.me/posts/2025/08/27/pre-commit-hooks-are-back-thanks-to-ai)
- [Gitleaks vs TruffleHog](https://www.jit.io/resources/appsec-tools/trufflehog-vs-gitleaks-a-detailed-comparison-of-secret-scanning-tools)
- [Betterleaks](https://appsecsanta.com/betterleaks)
- [Best Secret Scanning Tools 2025](https://www.aikido.dev/blog/top-secret-scanning-tools)
- [Slopsquatting attacks](https://www.contrastsecurity.com/security-influencers/slopsquatting-attacks-how-ai-phantom-dependencies-create-security-risks)
- [Package hallucinations (Snyk)](https://snyk.io/articles/package-hallucinations/)
- [Python supply chain security](https://bernat.tech/posts/securing-python-supply-chain/)
- [pip-audit](https://github.com/pypa/pip-audit)
- [REUSE tool](https://reuse.readthedocs.io/en/latest/readme.html)
- [Ruff pre-commit](https://github.com/astral-sh/ruff-pre-commit)
- [pyright pre-commit](https://github.com/RobertCraigie/pyright-python)
- [Dependency security with pip-audit](https://calmops.com/programming/python/dependency-security-vulnerability-scanning/)
- [prek HN discussion](https://news.ycombinator.com/item?id=46873138)
