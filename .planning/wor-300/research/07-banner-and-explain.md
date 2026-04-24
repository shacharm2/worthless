# Banner + `?explain=1` Copy (DRAFT — to be finalized in Phase 4 of implementation)

Status: **DEFERRED to implementation.** Two attempts to draft via agents failed to return usable output. Decision: draft during Phase 4 (walkthrough content creation) with voice sourced from `website-dev` branch + `README.md` + existing `install.sh` comments.

## Banner requirements (install.sh final output)

Bun-style. 8–12 lines. Every line earns its keep.

Must include:
- ✅ installed (with path: `~/.local/bin/worthless`)
- PATH export line (with detected shell rc path: `~/.zshrc` or `~/.bashrc`)
- **ONE** primary next command: `cd your-project && worthless lock`
- ONE discoverability hint:
  - For `-y` runs: `worthless --help --json` (agent-friendly)
  - For interactive: `curl worthless.sh?explain=1 | less` + `worthless --help`
- Source URL + issue tracker

Draft placeholder:
```
✅ worthless v0.3.0 installed to ~/.local/bin/worthless

  Add to PATH:    source ~/.zshrc   # (added: export PATH="$HOME/.local/bin:$PATH")
  Try it:         cd your-project && worthless lock
  Audit script:   curl worthless.sh?explain=1 | less
  Source:         https://github.com/shacharm2/worthless

  "Your app code doesn't change. worthless lock rewrites .env;
  worthless up starts a local proxy that substitutes your real key."
```

## `?explain=1` walkthrough requirements

Plain text. 40–60 lines. Audience: paranoid humans + AI agents auditing before execution.

Pick 6–8 decision points in `install.sh`. For each:
- **Line range** (e.g. "lines 40–55")
- **What it does** (plain English, one paragraph)
- **Why** (user benefit)
- **What could go wrong + exit code** if failure

Structure: `### Step N: <title>` followed by short para. Footer: `Still uncertain? Read the raw script: https://raw.githubusercontent.com/shacharm2/worthless/v0.3.0/install.sh`

Include the current `X-Worthless-Script-Sha256` at top so users can recompute and verify:

```
# Walkthrough for worthless install.sh v0.3.0
# Sha256: abc123... (verify: curl -sSL worthless.sh | sha256sum)
```

## Voice principles (from website-dev + README sampling)

- Direct, short sentences. No "we're excited to announce."
- Honest about risk. Mention sha256 pins, exit codes explicitly.
- Respect the reader's time.
- No hype. No marketing fluff.

## Why deferred

1. Drafting without the implemented script in hand produces generic copy.
2. Walkthrough line references must match actual line numbers at the time of shipping — must be generated AFTER `install.sh` is finalized for v0.3.0.
3. Banner tone needs to match whatever interactive vs. `-y` output looks like in the real CLI — also coupled to implementation.

**Action item in PLAN.md Phase 4:** draft + commit both copies. Reviewed by content-marketer after.
