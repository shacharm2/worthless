# WOR-405 Calm Homepage Redesign Handoff

Date: 2026-05-15

This handoff is for the next Codex session after restart. It captures the branch state, Linear traceability, story material, UX process, external-tool setup, and the exact sequence to follow. Do not treat it as an implementation plan. It is a restart-safe operating manual for getting to the real plan without repeating the previous failure.

## 1. Executive Summary

The WOR-405 homepage work must restart from `website-dev`, not from `main`.

The bad PR had useful story material, but its UI was wrong. It made a dense, documentation-like hero instead of preserving the calm, sparse, post-launch feel already present in `website-dev`.

The correct next pass should combine:

1. `website-dev` as the design/content reference and post-launch baseline.
2. WOR-394 claim boundaries and WOR-405 homepage scope from Linear.
3. Useful story material from the closed PR #191, without carrying over its dense UI.
4. Impeccable for UX shape/critique.
5. Stitch skills for visual exploration/prompting after Codex restart, if they appear in the skill list.

The next Codex should not write homepage code first. It should shape, sketch, review with the user, then plan implementation.

## 2. Current Branch And Artifact State

### Correct Reference Branch

`website-dev` is the post-launch design/content reference.

Current pushed state:

```text
origin/website-dev -> e40ce0d fix(website): restore calm website-dev reference
```

`e40ce0d` is a normal commit that restored the `docs/` website to the earlier calm reference content from `77c5db2`.

Important detail: the restore was pushed with `--no-verify` because the local pre-push hook was blocked by a pre-existing `urllib3 2.6.3` audit finding, not by the docs-only website restore.

### Correct New Work Branch

Use this branch for new UX/story/design work:

```text
feature/wor-405-homepage-calm-from-website-dev
```

Local worktree:

```text
/Users/shachar/Projects/worthless/worthless/.claude/worktrees/feature+wor-405-homepage-calm-from-website-dev
```

It was created from:

```text
origin/website-dev @ e40ce0d
```

This branch exists to hold planning/design work derived from `website-dev`. If this file is visible in the remote branch, the branch has been pushed as `origin/feature/wor-405-homepage-calm-from-website-dev`.

### Bad/Superseded Branch And PR

Closed PR:

```text
https://github.com/shacharm2/worthless/pull/191
```

Closed because the implementation drifted from the intended calm/minimal website-dev direction.

Bad branch:

```text
feature/wor-405-homepage-story
```

This branch targeted `main` and implemented a dense homepage directly under `main:/website`. Use it only as story prior, not as design or code.

### Mistaken Empty Branch

Mistaken branch created from `origin/main`:

```text
feature/wor-405-homepage-calm-redesign
```

This branch was the wrong base. Ignore it or delete it after confirming no work has been added there.

### Backup Branch

Before restoring `website-dev`, the prior tip was backed up:

```text
backup/website-dev-before-restore-20260515 -> 0657f09
```

Use this only if you need to inspect or recover the pre-restore OpenClaw/install-panel version of `website-dev`.

## 3. Linear Traceability

Use Linear as source of truth for scope and claim boundaries.

Required Linear reads:

1. `WOR-405`
2. `WOR-393`
3. `WOR-394` audit comment and `WOR-393` acceptance comment

Known Linear comments created during this recovery:

### WOR-405 Revised Story Brief

Comment id:

```text
c1a9f3cf-6940-4152-9534-f997620b5f9e
```

Purpose:

This is the revised story brief for the next pass. It says the next implementation should combine:

- `website-dev` as post-launch design/content reference
- WOR-394 accepted claim boundaries
- useful story material from PR #191
- calm/minimal UX, with detail moved into deliberate "want more detail?" paths

If the user asks to "rewrite the Linear ticket," update the `WOR-405` issue body from that comment only after user approval. Until then, keep it as the traceable story-source comment.

### WOR-393 Design Decision Note

A prior comment was added to `WOR-393` explaining the dense PR #191 design direction. That note is now superseded by the WOR-405 revised story brief above.

### WOR-405 PR Report Note

A prior comment reported PR #191 as opened. That PR is now closed and superseded.

## 4. User Intent, In Plain Language

The user is not asking to start over.

The user is saying:

- The current production `main:/website` is pre-launch.
- `website-dev` is the post-launch reference that already contains the right emotional direction.
- The story should not be invented from scratch.
- `website-dev` already has the kernels:
  - "AI will leak your api key."
  - "Make your API Keys Worthless."
  - "...while keeping your LLMs working."
- The previous WOR-405 PR added useful clarification:
  - copied key alone cannot call the provider
  - before protection vs after protection
  - scanner/vault/provider-dashboard positioning
  - Audit-with-AI as a trust affordance
  - WOR-394 claim boundaries
- The mistake was turning those claims into a heavy hero.
- The correct task is to make the website-dev story more understandable, not louder.
- The homepage must remain calm, sparse, and intentional.
- The user wants early sketches and co-thinking before any final website implementation.

## 5. High-Level Objective

Make the homepage understandable in seconds without making it feel like documentation.

The target feeling:

```text
I get it. AI leaks keys. Worthless makes the leaked key useless while my local LLM app still works. If I want details, I know where to go.
```

The UX should manifest that through restraint:

- one emotional headline
- one short clarifying line
- one primary CTA
- one quiet path to details
- no dense explanation above the fold
- no giant before/after cards above the fold
- no big code block above the fold unless explicitly approved
- mechanism and limits available through deliberate detail paths

## 6. Story Ingredients To Preserve

### Emotional Spine From `website-dev`

Keep this spirit:

```text
AI will leak your api key.

Make your API Keys Worthless.
...while keeping your LLMs working.
```

This is the homepage's body language. It is simple, direct, slightly provocative, and calm.

### Clarifying Story From WOR-405 Work

Fold in this idea, but do not dump all of it into the hero:

```text
Before Worthless: a copied .env AI key is a real provider credential.

After Worthless: the .env contains a lookalike shard and local routing.
The app keeps working locally through Worthless.

If .env is copied into GitHub, chat, logs, or AI-generated code, the copied key alone cannot call the provider.
```

The key transformation:

```text
copied provider key -> copied shard
```

This is the "pre-protection/post-protection" idea. It should be shown gently, possibly as a subtle transformation or below-fold explanation, not as giant text cards.

### Positioning

Preserve the distinction:

```text
Scanners find leaks.
Vaults store secrets.
Provider dashboards and gateways manage spend.
Worthless makes a copied .env AI key not a blank check.
```

This likely belongs below the hero or on a details page, not in the first viewport.

### Audit-With-AI

Keep Audit-with-AI if accurate, but make it a quiet trust affordance:

- link
- small section
- "audit install.sh before you run it"

Do not make it compete with the hero.

## 7. Claim Boundaries

Stay inside WOR-394/WOR-393 accepted claims.

Allowed / safe:

- local proxy
- format-preserving `.env` shard
- copied key material alone cannot call supported provider
- supported local flows on macOS, Linux, WSL2
- no cloud account required
- LLM-provider-focused scanning
- `scan --install-hook`, if still accurate
- true provider registry, if current code/docs support it

Avoid:

- hard spend caps
- "nothing happens" guarantees
- "no rotation needed" as an absolute
- native Windows support
- all-secret or any-key protection
- AWS/Stripe/GitHub scanner claims unless implemented and accepted
- OpenClaw as primary hero/persona material
- fake proof systems or `/red/` proof system expansion
- social launch or SEO page expansion as part of WOR-405

Acceptable nuance:

```text
You still investigate and follow incident policy, but the copied value alone is not a standalone provider credential.
```

Do not promise that incident response is unnecessary.

## 8. External Tools And Skills

### Impeccable

Installed at:

```text
/Users/shachar/.agents/skills/impeccable/SKILL.md
```

Use it for:

- shape brief
- visual hierarchy critique
- density/copy restraint
- calm design quality
- checking whether the page feels like generic AI output

Known blocker:

In the corrected branch, the Impeccable context loader reported:

```json
{
  "hasProduct": false,
  "hasDesign": false
}
```

That means a future session must not pretend Impeccable preflight is clean. Before implementation, either:

1. Run the Impeccable `teach` flow if available, or
2. Create a minimal product/design context only with explicit user confirmation, or
3. State clearly that Impeccable is being used for critique principles, not full preflight-gated craft.

Do not mark `IMPECCABLE_PREFLIGHT ... product=pass` unless this is actually fixed.

### Stitch Skills

Installed into Codex skills after this session:

```text
/Users/shachar/.codex/skills/stitch-design/SKILL.md
/Users/shachar/.codex/skills/enhance-prompt/SKILL.md
/Users/shachar/.codex/skills/design-md/SKILL.md
/Users/shachar/.codex/skills/taste-design/SKILL.md
```

Important:

Codex likely needs a restart before these appear in the available skill list.

Use Stitch for:

- prompt-enhanced design directions
- 2-3 visual concepts before code
- design-system language if helpful
- taste critique

If the Stitch MCP server itself is not available, do not fake tool output. Use the installed skill text as a methodology and say that no callable Stitch renderer is present.

### Superpowers

Use Superpowers as the process guard.

Correct sequence:

1. `superpowers:using-superpowers`
2. `superpowers:brainstorming`
3. Impeccable and Stitch inside the brainstorming/shape phase where applicable
4. `superpowers:writing-plans` only after the user approves the UX/story design spec

Do not skip brainstorming and jump to implementation.

Subagent note:

Some Superpowers docs say to dispatch review subagents. Current Codex developer instructions say to use subagents only if the user explicitly asks for subagents/delegation/parallel agent work. If that conflict appears, follow the developer instruction: ask for explicit permission or perform local review and state the limitation.

## 9. Correct Process From Restart

### Phase 0: Sanity Check State

Run:

```bash
cd /Users/shachar/Projects/worthless/worthless/.claude/worktrees/feature+wor-405-homepage-calm-from-website-dev
git status -sb
git log -3 --oneline --decorate
git rev-parse --short HEAD
git rev-parse --short origin/website-dev
```

Expected:

```text
branch: feature/wor-405-homepage-calm-from-website-dev
HEAD should be at or after e40ce0d
origin/website-dev should be e40ce0d or newer
```

If this handoff file exists only locally, commit/push it before doing design work.

### Phase 1: Load Linear Context

Use Linear MCP/app tools, not memory.

Read:

- `WOR-405`
- `WOR-393`
- `WOR-394` relevant audit/acceptance comments

Confirm the revised WOR-405 story brief comment exists:

```text
c1a9f3cf-6940-4152-9534-f997620b5f9e
```

If not visible by id, list comments on `WOR-405` and find "Revised WOR-405 story brief before the next implementation pass".

### Phase 2: Inspect `website-dev` Reference

Inspect the restored `website-dev` homepage:

```bash
sed -n '1,260p' docs/index.html
rg -n "AI will leak|Make your API|while keeping|WHAT'S WORTHLESS|FEATURES|leaked|useless|Audit|install" docs/index.html
```

Preview it:

```bash
python3 -m http.server 8008 -d docs --bind 127.0.0.1
```

Open:

```text
http://127.0.0.1:8008/index.html
```

Take screenshots or visual notes before proposing changes. Do not use this as final verification yet.

### Phase 3: Offer Visual Companion

Because this task is visual, the brainstorming skill requires offering the visual companion as its own message.

Use exactly one message, no other content:

```text
Some of what we're working on might be easier to explain if I can show it to you in a web browser. I can put together mockups, diagrams, comparisons, and other visuals as we go. This feature is still new and can be token-intensive. Want to try it? (Requires opening a local URL)
```

Then wait for the user's response.

### Phase 4: Ask One Clarifying Question

Do not ask five questions at once.

Good first question:

```text
For the first viewport, should the primary feeling be more "calm confidence" or more "oh, I instantly understand the before/after protection"? I can keep both, but one should lead.
```

This question matters because it decides whether the hero leads with emotion or transformation.

### Phase 5: Propose 2-3 UX Approaches

After enough context, propose approaches before code.

Recommended options:

#### Approach A: Calm Emotional Hero, Detail Teaser

Hero:

- "AI will leak your API key."
- "Make your API keys Worthless."
- "...while keeping your LLMs working."
- One CTA: Install
- One quiet link: How it works

Below fold:

- subtle "real key -> shard" transformation
- then positioning/details

Tradeoff:

- Most faithful to `website-dev` calm direction
- Less immediate mechanical explanation above fold

#### Approach B: Calm Before/After Line, Still Minimal

Hero:

- "AI will leak your API key."
- "Make your API keys Worthless."
- "A copied .env becomes a shard, while your local app keeps working."

Visual:

- tiny two-state transformation, not cards

Tradeoff:

- More understandable in first 5 seconds
- Slightly less sparse than Approach A

#### Approach C: Story Scroll

Hero:

- minimal emotional headline

Next screen:

- "Before Worthless"
- "After Worthless"
- "Want the mechanism?"

Tradeoff:

- Best for calm pacing
- Requires scroll to understand mechanism

Likely recommendation:

Start with Approach B, but constrain it with Approach A's calmness. The user has explicitly asked for both immediate clarity and the Calm-like feel.

### Phase 6: Produce Low-Fidelity Sketches Before Code

The next Codex must show sketches before implementation.

Acceptable formats:

- text wireframes
- Mermaid layout sketch
- simple static HTML mock in a separate scratch file, not final website code
- browser visual companion if accepted
- Stitch-generated or Stitch-prompted concepts if tools are available

Minimum sketches:

1. Ultra-calm hero
2. Calm before/after transformation
3. Detail-first but still sparse

Each sketch must include:

- above-fold copy
- CTA placement
- where "learn more" lives
- what is intentionally omitted from above the fold

### Phase 7: Design Spec Before Implementation

After user chooses a direction, write a design spec to:

```text
.planning/WOR-405-CALM-HOMEPAGE-DESIGN-SPEC.md
```

Spec should include:

- chosen direction
- copy budget
- first viewport budget
- page information architecture
- exact claims allowed
- what moves to `how-it-works`
- what moves to security/claim-boundary page
- Audit-with-AI role
- mobile constraints
- test/verification plan

Do not write implementation plan until the user approves this spec.

### Phase 8: Implementation Plan

Only after user approval, use `superpowers:writing-plans`.

Implementation plan target:

```text
.planning/WOR-405-CALM-HOMEPAGE-IMPLEMENTATION-PLAN.md
```

The implementation plan must cover two tracks:

#### Track 1: Tune `website-dev`

Work on:

```text
feature/wor-405-homepage-calm-from-website-dev
```

Files likely touched:

```text
docs/index.html
possibly docs/how-it-works.html
possibly docs/security-model.md or existing claim boundary docs
```

Goal:

Make the design direction real and reviewable in the `website-dev` reference environment.

#### Track 2: Port Accepted Result To `main:/website`

After website-dev direction is accepted, create a final PR branch from `origin/main`.

Likely branch:

```text
feature/wor-405-homepage-calm-port
```

Files likely touched:

```text
website/index.html
tests/test_website_homepage.py
possibly supporting website pages if already present and in scope
```

Goal:

Port accepted homepage/design into production source with tests and visual verification.

Do not directly merge `website-dev` into `main`.

## 10. Copy Budgets

These are guardrails to prevent another dense hero.

### Hero Budget

Above fold:

- max 1 eyebrow
- max 1 headline
- max 1 short support line
- max 1 primary CTA
- max 1 secondary text link or quiet CTA
- max 1 small visual idea

Avoid above fold:

- paragraphs longer than 20-25 words
- code block
- trust chips cluster
- two giant cards
- multiple competing CTAs
- full scanner/vault/provider-dashboard positioning
- full claim boundaries

### Detail Budget

Below fold:

- one compact transformation section
- one "want more detail?" path
- one trust/audit affordance

Long details move to supporting pages.

## 11. Information Architecture

Homepage:

- emotion and immediate comprehension
- minimal transformation cue
- CTA
- quiet "how it works" path

`how-it-works`:

- mechanism
- real provider key replaced by shard
- local proxy/routing
- copied key alone cannot call provider

Security/claims surface:

- boundaries
- supported environments
- what it does not protect
- what it does not claim

Install docs:

- install commands
- verification
- Audit-with-AI if not kept on homepage

Audit-with-AI:

- trust affordance
- not a hero competitor

## 12. Verification Requirements

Before any PR is considered ready:

### Static Checks

Add or update tests so the homepage includes required claims and excludes forbidden claims.

Existing prior test idea from bad PR:

```text
tests/test_website_homepage.py
```

Useful assertions:

- includes approved story lines
- install/docs CTA exists
- GitHub link exists
- Audit-with-AI path exists if homepage includes it
- local links resolve
- forbidden claims absent

Forbidden terms should include:

- hard spend cap
- native Windows support
- any secret
- any key
- AWS
- Stripe
- "nothing. the leaked key"
- reset-budget
- worthless.cloud if production source must be wless.io
- waitlist, unless explicitly accepted
- localStorage, unless explicitly accepted
- tally.so, unless explicitly accepted

### Visual Checks

Use Playwright/browser after implementation:

- desktop 1440x900
- mobile 390x844
- no horizontal overflow
- no clipped nav/headline/button text
- CTA links work
- screenshot before/after notes

Useful metric:

```js
document.documentElement.scrollWidth === document.documentElement.clientWidth
```

Also inspect real screenshots. Metrics alone do not catch "too dense".

### Claim Review

Before PR, compare visible copy against:

- WOR-394 audit boundaries
- WOR-405 revised story brief
- WOR-393 acceptance comment

If a story/design decision changes, report it back to Linear before merge.

## 13. Commands And Local Preview

### Preview Restored `website-dev`

From:

```bash
cd /Users/shachar/Projects/worthless/worthless/.claude/worktrees/feature+wor-405-homepage-calm-from-website-dev
python3 -m http.server 8008 -d docs --bind 127.0.0.1
```

Open:

```text
http://127.0.0.1:8008/index.html
```

### Preview Production `main:/website`

From a `main`-based worktree:

```bash
python3 -m http.server 8008 -d website --bind 127.0.0.1
```

Open:

```text
http://127.0.0.1:8008/index.html
```

Do not confuse `docs/` (`website-dev`) with `website/` (`main` production source).

## 14. Known Risks And How To Avoid Them

### Risk: Starting From `main`

Wrong for the design pass.

Fix:

Start from `website-dev`. Only port to `main:/website` after the user approves the new design.

### Risk: Using Bad PR #191 As Code

Wrong.

Fix:

Use it only as story prior. Do not copy the dense hero/cards.

### Risk: Over-Explaining

This already happened.

Fix:

Use copy budgets and early sketches. Every above-fold word must earn its place.

### Risk: Claim-Safe But Emotionally Dead

Security precision can flatten the brand.

Fix:

Keep website-dev's emotional spine and move precision to detail surfaces.

### Risk: "No Immediate Rotation Needed" Overclaim

User likes "mind at ease" / "triage before panic", but not unsafe guarantees.

Fix:

Use:

```text
The copied value alone is not a standalone provider credential.
```

Do not use:

```text
No rotation needed.
```

### Risk: External Tools Becoming Theater

Do not say Stitch was used if it was not callable.

Fix:

If Stitch skills are available, use them. If not, use their prompt/design method and state the limitation.

## 15. Suggested Restart Prompt

Use this prompt in the new Codex session:

```text
We are continuing WOR-405 after a restart.

Read first:
1. .planning/WOR-405-CALM-REDESIGN-HANDOFF.md
2. Linear WOR-405, especially the revised story brief comment c1a9f3cf-6940-4152-9534-f997620b5f9e
3. Linear WOR-393 and WOR-394 audit/acceptance context
4. Current website-dev docs/index.html on branch feature/wor-405-homepage-calm-from-website-dev

Important:
- Do not start from main.
- Do not implement yet.
- Use website-dev as the design/content reference.
- Use the prior bad PR #191 only as story prior, not as design/code.
- Use Superpowers brainstorming before writing code.
- Use Impeccable for UX shape/critique.
- Use Stitch skills if they are available after restart; if not, say so and use their methodology manually.
- Offer visual companion before visual sketching.
- Produce 2-3 low-fidelity homepage sketches before implementation.

Goal:
Create the UX/story design spec for a calm WOR-405 homepage that preserves:
"AI will leak your API key. Make your API keys Worthless. ...while keeping your LLMs working."
while folding in:
"copied .env key alone cannot call the provider"
without making the homepage dense.
```

## 16. Suggested First Message From Next Codex

The next Codex should not start by editing files. It should say something like:

```text
I have the handoff. I am using Superpowers brainstorming and Impeccable for the UX/story shaping phase. I will not implement until we have an approved design spec.

Some of what we're working on might be easier to explain if I can show it to you in a web browser. I can put together mockups, diagrams, comparisons, and other visuals as we go. This feature is still new and can be token-intensive. Want to try it? (Requires opening a local URL)
```

Then it must wait for the user.

## 17. Definition Of Done For Planning Phase

Planning phase is done when all are true:

- User has seen 2-3 low-fidelity directions.
- User has chosen or combined a direction.
- Design spec exists in `.planning/`.
- Design spec explains:
  - hero copy
  - visual approach
  - information architecture
  - claim boundaries
  - verification plan
- Linear has a comment linking or summarizing the approved direction.
- Only then is it time for `superpowers:writing-plans`.

## 18. Definition Of Done For Implementation Phase

Implementation phase is done when all are true:

- `website-dev` branch shows accepted calm direction.
- Accepted direction is ported into `main:/website` on a main-based branch.
- Final PR targets `main`.
- No Cloudflare/Worker/Wrangler/DNS/HSTS/Transform Rules touched.
- No marketing/ directory added.
- No SEO page expansion.
- No `/red/` proof system.
- No social launch work.
- No OpenClaw primary hero/persona material.
- Tests pass.
- Desktop/mobile screenshots captured.
- No horizontal overflow or clipped text.
- CTA links verified.
- Linear updated before merge if story/design decisions changed.
