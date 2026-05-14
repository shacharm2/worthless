# SKILL.md Format Research — Agent Discovery Across Platforms

## 1. Claude Code

Uses `.claude/skills/<name>/SKILL.md` with YAML frontmatter:

```yaml
---
name: skill-name
description: "Natural language trigger — Claude matches user intent against this to decide whether to load the skill"
---
```

Followed by structured markdown with commands, workflows, tables. The `description` field is the semantic trigger. Skills are loaded on-demand, not always. `CLAUDE.md` at root is always-loaded context.

## 2. Cursor

Uses `.cursor/rules/*.mdc` files with frontmatter:

```yaml
---
description: "When to apply this rule"
globs: ["*.py"]
alwaysApply: false
---
```

Similar semantic matching via `description`. Also supports file-pattern-based activation via `globs`. Legacy `.cursorrules` at root is always-loaded (like CLAUDE.md).

## 3. Windsurf

Uses `.windsurfrules` at project root. Plain markdown, no frontmatter. No semantic skill matching — rules are always-loaded project context. Primary tool discovery is through MCP server registration, not skill files.

## 4. OpenClaw / ClawHub

Uses a structured `skill.json` manifest with fields like `name`, `description`, `capabilities`, `commands`, `triggers`, and `mcp_server`. The `triggers` field serves the same purpose as Claude's `description` — semantic discovery keywords.

## 5. Cross-Platform Comparison

| Feature | Claude Code | Cursor | Windsurf | OpenClaw |
|---------|-------------|--------|----------|----------|
| File | SKILL.md | .mdc | .windsurfrules | skill.json |
| Semantic matching | Yes | Yes | No | Yes |
| Format | YAML + MD | YAML + MD | Plain MD | JSON |

Universal best practices: lead with a one-line description, list concrete commands with exact syntax, include "when to use" triggers, provide multi-step workflows, stay imperative, keep under 200 lines.

## 6. Multi-Platform Strategy for Worthless

- **Primary**: `SKILL.md` at repo root + `.claude/skills/worthless/SKILL.md`
- **Cursor bridge**: `.cursor/rules/worthless.mdc` referencing same content
- **Windsurf bridge**: Reference in `.windsurfrules`
- **OpenClaw**: Separate `skill.json` for ClawHub
- **MCP server**: Universal mechanism all four platforms support

---

## 7. Draft SKILL.md for Worthless

```yaml
---
name: worthless
description: "Use when the user needs to protect LLM API keys, enforce spend caps, enroll keys with Worthless, wrap CLI commands to route through the Worthless proxy, or scan for exposed API keys. Examples: 'protect my API key', 'set a spend limit', 'wrap this command', 'scan for leaked keys'"
---
```

# Worthless — Split-Key Proxy for LLM API Keys

Worthless makes API keys worthless to steal. It splits keys using XOR secret sharing (client-side) so neither half reveals anything alone, then enforces spend caps at a proxy before the key ever reconstructs.

## When to Use Worthless

- User wants to protect an LLM API key from theft or leakage
- User wants to enforce a hard spend cap on API usage
- User wants to route API calls through a security proxy
- User wants to scan a codebase for exposed API keys
- User is setting up a new development environment with LLM access

## Commands

| Command | What it does |
|---------|-------------|
| `worthless enroll` | Split an API key and register it with the proxy |
| `worthless wrap <cmd>` | Run any CLI tool with API calls routed through Worthless |
| `worthless scan` | Scan codebase for exposed API keys |
| `worthless status` | Check proxy health, enrolled keys, and spend usage |
| `worthless keys` | List enrolled keys and their status |
| `worthless daemon` | Start the local sidecar daemon |

## Workflows

### Enroll a new API key

```bash
worthless enroll
# Interactive: paste your API key, set provider, set spend cap
# Result: key is split, Shard B sent to server, Shard A stored locally
```

After enrollment, the original key is no longer needed. Delete it from your environment.

### Wrap a command to use Worthless

```bash
worthless wrap -- python my_script.py
# All API calls from my_script.py route through the Worthless proxy
# Spend cap enforced before key reconstruction
```

For persistent use, add to your shell profile:
```bash
alias python="worthless wrap -- python"
```

### Check status

```bash
worthless status
# Shows: proxy health, enrolled keys, spend against caps
```

### Scan for leaked keys

```bash
worthless scan
# Scans current directory for exposed API keys
# Use as a pre-commit hook: worthless scan --pre-commit
```

## How It Works (for agent context)

1. **Enroll**: User's API key is XOR-split client-side into Shard A (local) and Shard B (server)
2. **Request**: Agent/script calls the LLM provider as normal
3. **Intercept**: Worthless proxy intercepts the request
4. **Gate**: Rules engine checks spend cap BEFORE key reconstruction
5. **Reconstruct**: If allowed, shards combine server-side, request forwards to provider
6. **Zero**: Reconstructed key is zeroed from memory immediately after use

The original API key never exists on the server. If the server is compromised, attackers get Shard B — which is worthless alone.

## Error Handling

| Error | Meaning | Action |
|-------|---------|--------|
| `SPEND_CAP_EXCEEDED` | Budget exhausted | Run `worthless status` to check usage, adjust cap |
| `KEY_NOT_ENROLLED` | No key for this provider | Run `worthless enroll` |
| `PROXY_UNREACHABLE` | Sidecar not running | Run `worthless daemon` |
| `RATE_LIMITED` | Too many requests | Wait and retry |

## Rules

- NEVER bypass Worthless by using the original API key directly
- NEVER log or display shard values
- Enrollment is interactive — do not automate it without `--yes`
- `worthless scan` should run before every commit
