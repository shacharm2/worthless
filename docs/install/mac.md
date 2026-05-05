---
title: "Install — macOS"
description: "Native install on Apple Silicon or Intel Mac, ~90 seconds zero to working proxy."
---

# Install on macOS

Zero to working proxy in ~90 seconds. Apple Silicon (M1/M2/M3/M4) and
Intel Macs both supported. macOS 11 (Big Sur) or newer.

## 0. Prerequisites

```bash
# Confirm macOS version (must be 11+)
sw_vers -productVersion

# Confirm curl + bash present (default on macOS)
command -v curl bash
```

You do NOT need: Homebrew, pyenv, Python pre-installed, Xcode, sudo.
worthless's installer (`uv tool install`) bootstraps everything inside
`~/.local/`.

## 1. Install

```bash
curl -sSL https://worthless.sh | sh
```

What happens:
1. Downloads `install.sh` from the Cloudflare Worker at worthless.sh
2. Verifies the Astral `uv` installer's SHA256 (pinned in install.sh)
3. Installs `uv` to `~/.local/bin/uv`
4. Runs `uv tool install worthless` → `~/.local/bin/worthless`
5. Prints activation hints if `~/.local/bin` isn't on your PATH

**No keychain popups** during install. The keychain is only touched
when you first lock a key (step 3).

## 2. Verify install

```bash
worthless --version
```

Expected output:

```
worthless 0.3.3
```

If you see "command not found", `~/.local/bin` isn't on your PATH yet.
The installer printed activation hints for your shell. Either restart
your terminal or run the suggested `export PATH=...` line.

## 3. First lock — expect ONE keychain popup

```bash
cd /path/to/your/project   # one with .env
cat .env
# OPENAI_API_KEY=<your-real-openai-key-here>

worthless
```

What happens:
1. worthless detects the API key in `.env`
2. Prompts: `Lock these keys? [y/N]:` — type `y`
3. **macOS Keychain popup appears once** — it asks worthless can access
   "fernet-key" — click **"Always Allow"**
4. Key is split: shard A stays in `.env` (decoy), shard B encrypted in
   `~/.worthless/`
5. Proxy spawns on `127.0.0.1:8787`

`.env` is rewritten:

```diff
- OPENAI_API_KEY=<your-real-openai-key-here>
+ OPENAI_API_KEY=<decoy-prefix>...                    # decoy, useless alone
+ OPENAI_BASE_URL=http://127.0.0.1:8787/openai-<alias>/v1
```

**Subsequent runs of `worthless` produce zero popups.** The "Always
Allow" you clicked grants the binary permanent ACL trust.

## 4. Point your app at the proxy

If your app already reads `.env` (most do via `dotenv` /
`python-dotenv` / Next.js / etc.), **nothing changes in your code**.
The OpenAI/Anthropic SDK picks up `OPENAI_BASE_URL` automatically and
routes through `127.0.0.1:8787`.

If your app loads env vars another way, point it explicitly:

```python
from openai import OpenAI
client = OpenAI()  # reads OPENAI_API_KEY + OPENAI_BASE_URL from .env
```

## 5. Verify it actually works

Make a real call:

```bash
curl -s "http://127.0.0.1:8787/openai-<alias>/v1/models" \
  -H "Authorization: Bearer $(grep OPENAI_API_KEY .env | cut -d= -f2)"
```

Expected: a JSON list of OpenAI models. If you get this, the proxy
reconstructed your key, hit OpenAI, and returned the response — all
without your real key ever leaving the proxy process.

## 6. Daily use

| You do | What survives | What you do |
|---|---|---|
| Close terminal | Proxy keeps running (background) | Nothing |
| `worthless down` | Proxy stops | `worthless up` to restart |
| Reboot Mac | **Proxy is gone** | `worthless up` from a terminal |
| Wake from sleep | Proxy keeps running | Nothing |
| Switch projects (`cd`) | Each project's `.env` has its own proxy URL | Nothing — same daemon serves all |

**The reboot gap is real.** Until WOR-174 ships a launchd LaunchAgent
in v1.1, you manually `worthless up` after every reboot.
Workaround: add `worthless up &> /dev/null &` to your `~/.zshrc`.

## 7. Uninstall (manual, until WOR-435 ships)

```bash
# Stop proxy
worthless down

# Remove binary
uv tool uninstall worthless

# Purge keychain entries (loop drains all)
while security delete-generic-password -s worthless 2>/dev/null; do :; done

# Wipe state
rm -rf ~/.worthless
```

After WOR-435 ships, this becomes one command: `worthless uninstall`.

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| "command not found: worthless" | `~/.local/bin` not on PATH | `export PATH="$HOME/.local/bin:$PATH"` or restart terminal |
| Multiple popups during step 3 | Bug — should be 1 | File issue with `worthless --version` + `sw_vers -productVersion` |
| App gets `connection refused` on `127.0.0.1:8787` | Proxy not running | `worthless up` |
| App gets `connection refused` after reboot | Proxy died with the boot — see §6 | `worthless up` |
| Proxy started but `/healthz` reports a different PID | Stale orphan proxy on port 8787 | `worthless down` then `worthless up` (tracked as `worthless-6gkb`) |

## What worthless does NOT defend against

- Your laptop being compromised. If an attacker has root/admin on your
  Mac, they can read shard A from `.env` AND extract the keychain
  entry AND query the proxy directly. worthless raises the bar against
  *exfiltrated `.env` files*, not local-attacker scenarios.
- Non-LLM secrets. `worthless scan` only flags OpenAI / Anthropic /
  Google / xAI / OpenRouter key prefixes. Use `gitleaks` or
  `trufflehog` for general secret scanning.
