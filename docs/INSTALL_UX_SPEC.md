# Install Hero — UX + UI Spec
## Sources: Evil Martians (100 devtool study 2025), HubSpot (330K CTAs), ProductLed, NNGroup, Carbon/Cloudscape design systems

---

## The verdict in one sentence

**Two equal-weight cards below a unified headline. Dark code block inset. No tabs. No persona-selection click. Full runnable snippets.**

---

## Why cards, not tabs

Tabs imply "these are alternatives — pick one." Cards imply "these are parallel equal paths." Vercel, Fly.io, and Stripe all use cards for fundamentally different user tracks (not OS variants). Tabs are correct for macOS vs Linux vs Windows inside the solo dev card. Not correct for solo dev vs OpenClaw — those are different products.

Evil Martians 100-devtool study: "tabbed feature blocks are correct for when features fall into logical categories or serving multiple user personas" — but the split happens below a unified hero headline, not in it.

---

## Structure

```
[Eyebrow]
[H1: Make your API Keys Worthless.]      ← already good, works for both personas
[Sub: ...while keeping your LLMs working.]

┌────────────────────────────────┐  ┌──────────────────────────────────────┐
│  Solo Dev                      │  │  Claude Code / OpenClaw              │
│  macOS · Linux · Windows       │  │  No install required                 │
│                                │  │                                      │
│  $ curl worthless.sh | sh      │  │  { "worthless-proxy": {              │
│                           [⎘]  │  │      "baseUrl": "...",         [⎘]  │
│                                │  │      "apiKey": "<shard-A>"           │
│  [macOS] [Linux] [Windows]     │  │    }                                 │
│  ↑ small OS switcher, below    │  │  }                                   │
│    command, not above          │  │                                      │
│                                │  │  Done in 60 seconds. No CLI.        │
│  [ Install CLI →  ]            │  │  [ Configure now →  ]               │
│  solid, brand color            │  │  ghost button, outlined             │
└────────────────────────────────┘  └──────────────────────────────────────┘

Below cards (small, muted):  Or install via: pip · uv · Docker
```

---

## UX rules (research-backed)

| Rule | Source | Applied to worthless |
|------|--------|----------------------|
| Personalized CTAs convert 202% better than generic | HubSpot 330K CTA study | "Install CLI" > "Get Started". "Configure for Claude Code" > "Learn More" |
| 15-Minute Rule: first value in <15 min or devs leave | daily.dev / developer growth research | Solo dev: 90s install target. OpenClaw: 3 min config target |
| Show complete runnable snippet, not truncated | Tailscale/Cloudflare tunnel comparison | Full openclaw.json block visible. No "see docs for full config" |
| Never require a click to reveal the second path | NNGroup progressive disclosure research | Both cards always visible. No "choose your persona" gate |
| No email before install command is visible | ProductLed straight-line onboarding | Command is in the hero, no signup wall |
| One step removed = measurable abandonment | ProductLed: email confirmation removal → 6-fig ARR | Every non-essential step in install flow is a leak |

---

## UI rules (design system consensus: Carbon/IBM, Cloudscape/AWS, PatternFly/RedHat)

**Code block:**
- `background: #0d1117` (GitHub dark), `border-radius: 8px`, `padding: 14px 18px`
- Font: JetBrains Mono (already in use), 14px
- `$` prefix: `color: #6b7280` (dimmed), command: `color: #e6edf3`
- `border: 1px solid rgba(255,255,255,0.08)` for separation against light bg

**Copy button:**
- Top-right, absolute positioned inside code block
- Desktop: hover-reveal (opacity 0→1, 150ms). Mobile: always visible
- Click: clipboard icon → check icon (100ms transition)
- Auto-revert after 1500ms — no dismiss, no toast, no page-level feedback
- aria-label changes: "Copy to clipboard" → "Copied"

**CTA buttons:**
- Solo dev card: solid filled, brand color (--slate #4a6fa5 or teal #0a9396)
- OpenClaw card: ghost outlined — NOT both solid (Refactoring UI: hierarchy must be visible without reading text)
- Never same visual weight for two different actions

**Light palette stays:**
- NNGroup: ~47% of users have astigmatism — white-on-dark causes halation. Light is safer default.
- Tailscale (security/networking tool) chose light specifically to feel approachable vs. threatening.
- Worthless's light blue is differentiated — every other security CLI tool goes dark.
- Dark-inset code blocks on a light page = terminal aesthetic where it matters, approachability everywhere else.

---

## OS switcher inside Solo Dev card

Small, below the command (not above — Bun's editorial choice):
```
$ curl worthless.sh | sh     [⎘]

  [macOS]  [Linux]  [Windows]
```

Windows shows PowerShell equivalent. Auto-detect from `navigator.userAgent` on page load, but always show tabs so user can override. Never hide non-detected options.

---

## "Install with AI" placement

**Not in the hero.** It competes with both tracks and confuses the majority.

**In Section 4 (WHAT'S NEXT / early access):** Already the right home.
- "Already using Claude Code? Install via OpenClaw →" (deeplink when published)
- Copy-paste prompt for any AI assistant (zero infra, works now)

---

## Phased implementation

### Phase 1 — now (worthless.sh domain not live yet)
Keep `pip install worthless` as primary.
Add `uv tool install worthless` as small secondary line below.
Add "Other methods →" text link stub.
**Don't pretend curl is ready before the domain is live.**

### Phase 2 — when WOR-209 (worthless.sh domain) is done
Switch hero to two-card layout.
Card 1: `curl worthless.sh | sh` with OS switcher
Card 2: OpenClaw config snippet (full, runnable)
Both dark-inset code blocks with copy buttons.

### Phase 3 — /install page
Persona-sorted: 🐍 Python → pip/uv/pipx · 🖥 Any OS → curl/PowerShell · 🐳 Docker · 🤖 AI agent → OpenClaw + copy-paste prompt
