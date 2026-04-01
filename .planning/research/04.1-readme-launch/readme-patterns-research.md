# README Patterns Research: Top Developer Security & CLI Tools

**Date:** 2026-03-31
**Purpose:** Inform Worthless README design by analyzing high-star GitHub repos

---

## Repos Analyzed

### Security / Secrets Tools
| Tool | Stars | Category |
|------|-------|----------|
| Infisical | ~15K | Secrets platform |
| SOPS (getsops/sops) | ~17K | Encrypted file editor |
| git-crypt | ~8K | Transparent git encryption |
| dotenvx | ~4K | Secure dotenv |
| Doppler CLI | ~1K | Secrets management CLI |

### Developer Proxy Tools
| Tool | Stars | Category |
|------|-------|----------|
| mitmproxy | ~39K | Intercepting HTTP proxy |
| ngrok | ~25K | Tunnel / ingress |

### Viral CLI Tools
| Tool | Stars | Category |
|------|-------|----------|
| fzf | ~67K | Fuzzy finder |
| ripgrep | ~50K | Fast grep replacement |
| bat | ~50K | cat with syntax highlighting |
| lazygit | ~55K | Terminal git UI |
| zoxide | ~24K | Smarter cd |

---

## Top 5 Patterns

### Pattern 1: "Show, Don't Tell" Above the Fold

**What:** The first screenful (roughly 25 lines of rendered markdown) contains a visual demo -- GIF, terminal screenshot, or architecture diagram -- that answers "what does this do?" without reading a word.

**Who does it best:**
- **lazygit**: Full-width GIF of the TUI in action. You immediately understand it is a visual git interface. Multiple feature GIFs follow.
- **bat**: Terminal screenshot showing syntax highlighting and git integration side-by-side with plain `cat` output. The value proposition is instantly visible.
- **fzf**: Animated GIF showing fuzzy matching in real-time. The demo IS the pitch.
- **Infisical**: Dashboard screenshot + architecture diagram showing the product in context.

**Who skips it (and pays for it):**
- **SOPS**: Text-only README (RST format). Despite being excellent software, the README reads like a manual, not a pitch. Requires significant reading investment before understanding the tool.
- **git-crypt**: Minimal README, no visuals. High utility but low discoverability.
- **Doppler CLI**: Directs to docs site immediately. README is a thin shim.

**Takeaway for Worthless:** Lead with a terminal recording or diagram showing the split-key flow. A 3-frame sequence (enroll -> wrap -> proxy intercept) would be ideal. The security claim becomes visceral when you SEE the key never leaving the client.

---

### Pattern 2: One-Command Install, Then Working Example Within 30 Seconds of Reading

**What:** The fastest path from "I found this repo" to "I have it running" is compressed to 2-3 code blocks maximum. Install -> Run -> See output.

**Who does it best:**
- **zoxide**: `curl -sSfL ... | sh` one-liner, then `z foo` and you are using it. Two commands total.
- **bat**: `brew install bat` then `bat README.md`. Done. The README shows the output right there.
- **fzf**: `brew install fzf` or git clone + install script. Then shows the immediate shell integration.
- **dotenvx**: `curl -sfS "https://dotenvx.sh/" | sudo sh` then `dotenvx run -- node index.js`. Two commands.
- **ripgrep**: `brew install ripgrep` then `rg "pattern" .` with immediate benchmark context.
- **Doppler**: "Setup should only take a minute (literally)."

**Who gets it wrong:**
- Tools that require config files, environment variables, or account creation before showing any value.
- READMEs that put installation below the fold or split it across a separate docs site.

**Takeaway for Worthless:** The 90-second setup flow must be visible in the README. Show: `pip install worthless` -> `worthless enroll` -> `worthless wrap -- python app.py`. Three commands, working protection.

---

### Pattern 3: Trust Through Transparency (Security-Specific)

**What:** Security tools earn trust not through marketing claims but through: (a) explicit documentation of what they protect and what they DON'T, (b) cryptographic details in the README itself, (c) documented limitations, and (d) external validation signals.

**Who does it best:**
- **git-crypt**: Explicitly documents: "does not encrypt file names, commit messages, symlink targets." Lists that revoking access is not supported. States AES-256-CTR with synthetic IV. This honesty IS the trust signal.
- **SOPS**: Documents the encryption scheme (AES256_GCM, unique IV per value, data key architecture). Explains exactly what is encrypted and what remains visible (keys vs values).
- **Infisical**: SOC 2 badge, security audit links, CNCF membership mention. External validation prominently displayed.

**What builds trust in README (ranked by impact):**
1. **Threat model / limitations section** -- "What this does NOT protect against"
2. **Crypto primitives named explicitly** -- AES-256, XOR secret sharing, etc.
3. **Architecture invariants stated as promises** -- "The server never sees your full key"
4. **External audit / compliance badges** -- SOC 2, CNCF, security policy link
5. **SECURITY.md linked prominently** -- Vulnerability reporting process
6. **License clarity** -- MIT/Apache dual-license is the gold standard for trust

**Who gets it wrong:**
- Vague security claims ("military-grade encryption") without naming primitives
- No limitations section -- implies either ignorance or dishonesty
- Security docs buried in a wiki or separate site

**Takeaway for Worthless:** Include a "Security Model" section in the README with the three architectural invariants. Name the crypto (XOR secret sharing, AES-256). Add a "What Worthless Does NOT Protect Against" subsection. Link SECURITY_RULES.md. This transparency is a competitive moat -- most commercial tools hide their limitations.

---

### Pattern 4: Layered Information Architecture

**What:** The README serves three audiences simultaneously through progressive disclosure: (1) "What is this?" scanners, (2) "How do I install it?" doers, (3) "How does it work?" evaluators. The best READMEs never force audience #1 to scroll past content meant for audience #3.

**Consistent section ordering across high-star repos:**

```
1. Hero (logo + tagline + badges)           -- 5 seconds
2. Visual demo (GIF/screenshot/diagram)      -- 10 seconds
3. What is this? (1-2 sentences)             -- 15 seconds
4. Install (one-liner + alternatives)        -- 30 seconds
5. Quick start (working example)             -- 60 seconds
--- fold for most visitors ---
6. Features list                             -- evaluators
7. Configuration / advanced usage            -- adopters
8. How it works / architecture               -- contributors
9. Contributing / license / security         -- community
```

**Who does it best:**
- **bat**: Tagline -> screenshot -> what it does -> install -> use it. Perfect progressive disclosure.
- **ripgrep**: Name + what it does -> benchmarks (trust!) -> install -> usage -> advanced.
- **fzf**: Description -> demo GIF -> install -> usage -> tips. TOC for the long tail.
- **lazygit**: Tagline -> mega GIF -> feature GIFs -> install -> usage.

**Anti-patterns:**
- Starting with a "Table of Contents" before showing what the tool does
- Putting "Contributing" or "License" above "Install"
- Feature matrix before a working example
- Docs-site README (just links, no substance)

**Takeaway for Worthless:** Follow the 9-section template. Worthless has two extra audience segments: (a) security evaluators who need the trust section early, and (b) agent/MCP users who need the machine interface. Place security model at position 6 (after quick start, for evaluators) and agent/MCP setup at position 7.

---

### Pattern 5: Badges as Social Proof, Not Decoration

**What:** Badges serve as trust signals and project health indicators. The best repos use 3-5 carefully chosen badges that answer specific questions a visitor has. Too many badges = noise. Zero badges = "is this maintained?"

**Effective badge selection (in order of impact):**

| Badge | Question it answers |
|-------|-------------------|
| CI/Build status | "Does this even work?" |
| Version / latest release | "Is this actively maintained?" |
| License | "Can I use this?" |
| Downloads / installs | "Do other people use this?" |
| Security audit / SLSA | "Can I trust this with secrets?" |

**Who does it best:**
- **Infisical**: MIT license + CI + stars + "Featured on" badges. Social proof heavy.
- **ripgrep**: Minimal -- CI status + crates.io version. Lets the content speak.
- **bat**: CI + version + license. Clean and functional.

**Anti-patterns:**
- 10+ badges creating a "badge wall" that pushes content below fold
- Vanity badges (code style, "made with love") that answer no visitor question
- Broken/red badges left unfixed

**Takeaway for Worthless:** Use exactly 4-5 badges: CI status, PyPI version, license (MIT), downloads, and a security-specific badge (link to SECURITY.md or security policy). Add a "Featured on" or "Used by" section once there is social proof to display.

---

## What the Best READMEs Do That Mediocre Ones Don't

| Best READMEs | Mediocre READMEs |
|---|---|
| Answer "what is this?" in under 5 seconds with a visual | Require reading 3 paragraphs to understand the tool |
| Get to a working example in under 60 seconds of reading | Bury install instructions or require account creation first |
| Name their limitations explicitly | Make vague security/performance claims |
| Serve 3 audiences (scanner/doer/evaluator) with progressive disclosure | Write for one audience (usually the author) |
| Use badges as trust signals | Use badges as decoration or skip them entirely |
| Show the output, not just the input (terminal screenshots of results) | Show only the commands without showing what happens |
| Keep the README self-contained for the 80% case | Redirect to a docs site for basic information |
| Have a clear visual hierarchy (headers, code blocks, images break up text) | Wall of text with no visual breaks |

---

## Recommended Worthless README Structure

Based on this research, the optimal structure for Worthless:

```
1. Logo + tagline: "Make API keys worthless to steal"
2. Badges: CI | PyPI | License (MIT) | Downloads | Security Policy
3. Terminal recording: 90-second enroll -> wrap -> protected request flow
4. One-paragraph pitch: What it does, who it's for (2-3 sentences max)
5. Install: `pip install worthless` (one-liner, alternatives in collapsible)
6. Quick start: 3 commands to working protection
   --- fold ---
7. How it works: Split-key diagram, 3 invariants, architecture overview
8. Security model: Crypto primitives, threat model, what it does NOT protect
9. Features: Rules engine, metering, MCP server, CLI commands
10. For AI agents: SKILL.md reference, MCP setup, --json flag
11. Configuration: Advanced setup, Docker, team deployment
12. Contributing + License + Security policy links
```

---

## Sources

- [Infisical GitHub](https://github.com/Infisical/infisical)
- [SOPS GitHub](https://github.com/getsops/sops)
- [git-crypt GitHub](https://github.com/AGWA/git-crypt)
- [dotenvx GitHub](https://github.com/dotenvx/dotenvx)
- [Doppler CLI GitHub](https://github.com/DopplerHQ/cli)
- [mitmproxy GitHub](https://github.com/mitmproxy/mitmproxy)
- [ngrok GitHub](https://github.com/inconshreveable/ngrok)
- [fzf GitHub](https://github.com/junegunn/fzf)
- [ripgrep GitHub](https://github.com/BurntSushi/ripgrep)
- [bat GitHub](https://github.com/sharkdp/bat)
- [lazygit GitHub](https://github.com/jesseduffield/lazygit)
- [zoxide GitHub](https://github.com/ajeetdsouza/zoxide)
- [awesome-readme](https://github.com/matiassingers/awesome-readme)
- [README best practices - DEV](https://dev.to/belal_zahran/the-github-readme-template-that-gets-stars-used-by-top-repos-4hi7)
- [README examples that get stars](https://blog.beautifulmarkdown.com/10-github-readme-examples-that-get-stars)
