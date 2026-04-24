# Worthless — Roadmap

> **Generated file. Do not edit by hand.**
> Source: `.planning/snapshots/linear-*-post-cleanup-*.json`
> Regenerate: `python scripts/roadmap.py`

## Worthless v1.1 Release

### Wave 1 — CLI + Daemon Fixes

- ✅ **WOR-162** Verify scan pre-commit hook integration
- ✅ **WOR-164** Fix Python version badge vs pyproject.toml
- ✅ **WOR-168** Default spend cap on enrollment
- ✅ **WOR-169** Soften tagline for pre-Rust release
- ✅ **WOR-171** First-run success feedback in worthless wrap
- ✅ **WOR-176** Make mcp an optional dependency (worthless[mcp])
- ✅ **WOR-177** worthless --version flag
- ✅ **WOR-178** Post-lock next-step guidance
- ✅ **WOR-179** Check PyPI namespace availability for 'worthless'

### Wave 2 — Docker + Windows + PaaS

- ✅ **WOR-172** Windows experimental support — platform guards + CI
- ✅ **WOR-173** worthless down — stop running proxy daemon

### Wave 3 — Rules Engine

- ✅ **WOR-181** Wave 3: Rules Engine — model_allowlist, token_budget, time_window
  - ○ **WOR-159** ModelAllowlistRule: per-alias model restrictions
  - ✅ **WOR-160** TokenBudgetRule: daily/weekly/monthly token limits
  - ✅ **WOR-161** TimeWindowRule: time-based access restrictions
  - ✅ **WOR-182** Phase 0: Rule protocol refactor — add body parameter + spend_log cleanup
  - ✅ **WOR-183** Phase 1: Schema migration + structured error factories
  - ✅ **WOR-184** Phase 5: CLI rules configuration — lock flags + rules update/show

### Wave 4 — SKILL.md + Deploy Verification

- ✅ **WOR-163** SKILL.md agent discovery file
- ✅ **WOR-170** Verify Docker/Railway/Render configs end-to-end

### Wave 5 — README + Service Install

- ✅ **WOR-185** Move Fernet key from disk to OS keyring

### Wave 7 — Launch

- ○ **WOR-234** Launch Blockers
  - 🔄 **WOR-228** Default command: proxy PID detection broken — starts duplicate on second run
  - ○ **WOR-229** Ship worthless-mcp npm package for Claude Code / Cursor auto-install
  - ○ **WOR-235** worthless.sh universal install: rewrite install.sh to bootstrap via uv
  - ○ **WOR-236** Publish Docker image to GHCR (replaces native service path)

### (no milestone)

- ✅ **WOR-192** Wave 6: Magic Default Command + PyPI Publish
  - ✅ **WOR-165** README quickstart rewrite for pip install
  - ✅ **WOR-166** PyPI publish pipeline + first publish
  - ✅ **WOR-167** Version bump to 0.2.0
  - ✅ **WOR-180** End-to-end smoke test for product promise
  - ✅ **WOR-194** Refactor: extract start_daemon() + add quiet to _lock_keys()
  - ✅ **WOR-195** Magic default command — bare `worthless` sequential pipeline
  - ✅ **WOR-196** Release hardening: SECURITY.md, attestations, sdist safety, Docker perms
  - ✅ **WOR-197** Curl installer script for worthless.sh
- ○ **WOR-221** Research current repo flows and choose artifact format before audit mapping
- ○ **WOR-222** Research current SR enforcement and suppression landscape before gap analysis
- ○ **WOR-223** Research Semgrep, CodeQL, and AI-review capabilities against repo needs
- ○ **WOR-224** Research backlog-shaping approach before converting audit findings into implementation work

## Worthless v1.2

### (no milestone)

- ○ **WOR-193** Service Management
  - ○ **WOR-174** worthless service install — macOS launchd
  - ○ **WOR-175** worthless service install — Linux systemd
- ○ **WOR-214** Epic: worthless.cloud launch readiness
  - 🔄 **WOR-209** Domain setup: worthless.cloud (GitHub Pages) + worthless.sh (install script)
  - ✅ **WOR-210** Waitlist: email capture on worthless.cloud
  - ✅ **WOR-212** SEO + AEO: make worthless.cloud discoverable by search and AI
- ○ **WOR-216** Security Testing Tightening
  - 🔄 **WOR-217** Audit current repo flows into functionality inventory, state machines, and sensitive-data diagrams
  - 🔄 **WOR-218** Build SR coverage matrix and identify missing rules vs missing enforcement
  - 🔄 **WOR-219** Evaluate Semgrep, CodeQL, and AI-assisted security review roles for this repo
  - 🔄 **WOR-220** Convert audit findings into actionable Linear backlog items
- 🔄 **WOR-227** Documentation System
  - 🔄 **WOR-225** Developer Internal Documentation
  - ○ **WOR-226** External Website Documentation
- ○ **WOR-230** Epic: Python audit remediation backlog (2026-04-08 review)
  - ○ **WOR-231** Windows proxy startup path is internally inconsistent

## Worthless v2.0 Harden

### (no milestone)

- ○ **WOR-145** Phase 6: Shamir Core — GF(256) Shamir 2-of-3
- ○ **WOR-146** Phase 7: Shard Store — Platform credential store backends
- ○ **WOR-147** Phase 8: Sidecar Core — Rust binary with vault/proxy mode over IPC
- ○ **WOR-148** Phase 11: Python Layer Rewire — dual-mode proxy/CLI (light + secure coexist)
- ○ **WOR-149** Phase 10: Distribution — maturin wheels, Docker multi-container, CI builds
- ○ **WOR-150** Phase 13: Security Hardening and Documentation
- ○ **WOR-155** Phase 9: Sidecar Hardening — OS sandboxing and performance validation
- ○ **WOR-157** Phase 12: Migration — Optional Fernet-to-Shamir per-key conversion
