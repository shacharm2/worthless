# OpenClaw — Engineering Reference

> Internal reference for the worthless × OpenClaw integration. Public install
> docs live in `docs/install-openclaw.md`. This file is for contributors.

## TL;DR

OpenClaw is a real, MIT-licensed, multi-channel AI gateway daemon (npm `openclaw`,
`ghcr.io/openclaw/openclaw`). Users install skills via a separate CLI called
`clawhub`. The crowd-favorite install path for worthless is **one command**:
`clawhub install worthless`. Today that command 404s — the skill is not yet
published. Everything between "user types the command" and "user gets a
protected API call" is unbuilt; the proxy + openclaw.json baseUrl path
underneath has been working since WOR-213.

---

## What OpenClaw is

A self-hosted Node.js gateway that connects messaging channels (Discord,
WhatsApp, Telegram, Slack, iMessage, Matrix, Teams, Signal, etc.) to AI agents.
One daemon, many channels, an in-house agent ("Pi") that can call skills.
Skills are folders of markdown + YAML frontmatter (`SKILL.md`) that declare
required CLI bins; the agent shells out to those bins using `--json` output.
Different product from Claude Code, but adopted Anthropic's open SKILL.md
"Agent Skills" spec — that's why the schema looks familiar.

---

## Verified evidence

| Source | Evidence | Verified |
|---|---|---|
| npm `openclaw@2026.5.3-1` | 53.6 MB, 141 versions, MIT, `bin: openclaw`, repo github.com/openclaw/openclaw | direct probe |
| npm `clawhub@0.12.2` | CLI: `install <slug>`, `search`, `skill publish`, browser-OAuth login | `clawhub --help` locally |
| `ghcr.io/openclaw/openclaw:latest` | Multi-arch OCI image, version 2026.5.3-1, source github.com/openclaw/openclaw, built 2026-05-04 | `docker pull` + `docker inspect` |
| `tests/openclaw/docker-compose.yml` | Worthless × OpenClaw e2e stack working today | merged in WOR-213 |
| `tests/openclaw/openclaw-config/openclaw.json` | Real openclaw.json schema we route via | shipped in repo |

---

## Integration paths, ranked by friction

| Rank | Path | Commands | When to use |
|---|---|---|---|
| 1 | `clawhub install worthless` | 1 | Default for OpenClaw users (when skill is published) |
| 2 | Sideload from a GitHub URL | 1 (`clawhub install <github-url>`) | Pre-publish testing; users on patched forks |
| 3 | Manual: pull image, edit `openclaw.json` baseUrl, restart gateway | 4+ | Contributor / debug only — never in user-facing docs |

"Just ask OpenClaw to install it" via chat is technically possible but slower
and chicken-and-egg (requires the skill to already exist on ClawHub). Not a
real path.

---

## State today

| Layer | Status | Linear |
|---|---|---|
| Worthless proxy + Compose stack with OpenClaw | ✅ working | WOR-213 done |
| openclaw.json `models.providers.<x>.baseUrl` routes through proxy | ✅ verified | WOR-213 done |
| Drop-in OpenAI/Anthropic SDK compat through OpenClaw | ✅ verified | WOR-211 done |
| `worthless lock` rewrites `.env` (host-side) | ✅ working | shipped |
| OpenClaw skill folder authored | ❌ not started | new ticket needed |
| Skill published on ClawHub | ❌ not started | new ticket needed |
| Skill install hook auto-writes openclaw.json baseUrl | ❌ not started | depends on WOR-321 logic |
| `worthless lock` detects + rewrites `openclaw.json` (non-clawhub users) | ❌ backlog | WOR-321 |
| End-to-end test: `clawhub install worthless` → protected request | ❌ not started | new ticket child |

---

## Critical product decisions (lock before code)

These apply regardless of OpenClaw — any chat-agent + spend-cap integration hits
them. Adversarial research surfaced concrete prior-art failures for each.

1. **Fail-open vs fail-closed when proxy unreachable.** LiteLLM had to retrofit
   this post-launch (PR #9533). Default position must be explicit.
2. **Mid-stream cap-hit behavior.** Pi may already be streaming a reply when
   cap trips. Decide: kill stream + error code, or finish-then-block-next.
3. **Atomic increment-and-check on spend cap.** Naïve pre-check + post-record
   races: 3 parallel sub-agent calls all pass pre-check, all reconstruct, all
   exceed cap. Required: atomic Redis increment.
4. **`key_known_dead` cache on upstream 401s.** Otherwise each retry burns KMS
   calls + meter cycles for a key the provider has revoked.
5. **Non-retryable error code on `cap_exceeded`.** Hypothesis: agents will loop
   on a generic 403 and burn context. Needs an error-code semantics experiment
   against Claude Code / Cursor / OpenClaw before launch.

---

## Phase scope (under everything-claude-code)

If WOR-94's parent ticket is filed today, realistic ~2–3 days end-to-end:

| Step | Owner agent | Deliverable |
|---|---|---|
| Author skill folder (`SKILL.md` + frontmatter + install hook) | `architect` + manual | `skills/worthless/` in repo, lints with `clawhub` |
| Install hook writes `models.providers.<x>.baseUrl` to openclaw.json | `tdd-guide` enforces tests-first | host-side hook script |
| Decide + document 5 product questions above | `security-reviewer` | ADR in `engineering/adr/` |
| End-to-end test: `clawhub install worthless` → first message → protected | `e2e-runner` adapts existing compose | `tests/test_openclaw_skill_e2e.py` |
| Publish to ClawHub | manual | `clawhub login && clawhub skill publish ./skills/worthless` |
| Update `docs/install.html` OpenClaw panel to single-command | `doc-updater` | install page truthful |

Risks: `clawhub` may have an automated dangerous-code scanner that flags
key-handling skills (per [clawhub#669](https://github.com/openclaw/clawhub/issues/669)
— flagged but unverified). Sideload via GitHub URL works if publish blocks.

---

## Open questions (need experiments, not reasoning)

1. Does `models.providers.*.baseUrl` hot-reload on `openclaw.json` change, or
   does the gateway need restart? Test: edit baseUrl mid-conversation, send a
   message, observe.
2. Does `clawhub install <slug>` run skill install hooks unattended on Linux
   (CLI install) or only via the macOS Skills UI? Docs imply UI-only.
3. Does Pi loop on `403 cap_exceeded` from worthless? Test: trigger the cap,
   observe Pi's retry behavior.
4. Multi-user identity: gateway does NOT pass normalized sender ID to tools
   (channel-prefixed: `telegram:123`, `whatsapp:+15551234567`). Does the
   worthless skill need to read inbound sender for per-user spend, or is that
   v1.x scope?
5. Does ClawHub publish require an organization account, or does any logged-in
   user with a verified email work? Test: `clawhub login` from a throwaway.

---

## References

- Linear: [WOR-211](https://linear.app/plumbusai/issue/WOR-211) (drop-in SDK compat — done),
  [WOR-213](https://linear.app/plumbusai/issue/WOR-213) (OpenClaw integration test — done),
  [WOR-321](https://linear.app/plumbusai/issue/WOR-321) (worthless lock multi-config — backlog),
  [WOR-94](https://linear.app/plumbusai/issue/WOR-94) (SKILL.md agent discovery — backlog).
- Code: `tests/openclaw/`, `tests/test_openclaw_e2e.py`, `tests/test_openclaw_live.py`.
- External: [docs.openclaw.ai](https://docs.openclaw.ai), [github.com/openclaw/openclaw](https://github.com/openclaw/openclaw),
  [VoltAgent/awesome-openclaw-skills](https://github.com/VoltAgent/awesome-openclaw-skills).
