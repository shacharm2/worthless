# Launch Cleanup Plan — v1.1 Scope Cut

**Draft:** 6
**Date:** 2026-04-19
**Owner:** shachar@uglabs.io
**Supersedes:** drafts 1, 2, 3, 4, 5 (same filename).

---

## Changelog draft 5 → draft 6

Round 4 review by brutus + karen converged on real bugs:

1. **S4 was unsatisfiable.** Existing `install.sh` hard-exits on non-Python machines; plan sold NEW-A as "just host the script" but script doesn't actually work without Python. **Fixed:** NEW-A scope expanded to include install.sh rewrite via uv bootstrap. uv is a standalone binary (no Python needed), bootstraps Python + worthless in one curl pipe.
2. **S5 URL wrong.** Plan said `worthless/proxy:latest` (DockerHub namespace), NEW-B publishes `ghcr.io/<org>/worthless-proxy`. **Fixed:** S5 and NEW-B AC aligned on GHCR URL.
3. **Dockerfile path wrong.** Plan referenced `deploy/Dockerfile`, actual path is `./Dockerfile` at repo root. **Fixed.**
4. **NEW-A/NEW-B had no owner/due-date.** Plan could land, tickets sit, nothing ships. **Fixed:** A4 writes include `assigneeId` + `dueDate`; GATE 2 checks both are set.
5. **Beads re-classify loop missing.** If user rejects N of first 10, plan silently advanced. **Fixed:** B3 explicit loop — if any rejections, re-classify before continuing.
6. **Scope confirmation:** NEW-B (Docker GHCR) is a REAL blocker — replaces the abandoned native service path (WOR-193 slipping to v1.2). Docker runs persistent proxy, no manual `worthless up`. NEW-A (worthless.sh) is the universal CLI install path, non-Python included.

No other structural changes from draft 5.

---

## Preconditions

| Key | Value |
|---|---|
| Repo root | `/Users/shachar/Projects/worthless/worthless` |
| Branch | `chore/launch-cleanup` (create from `main` before any write) |
| Linear team ID | `f87e60df-91a3-4cef-9ecc-4c23506413dd` (WOR) |
| v1.1 project ID | `9c14710c-b487-46e9-b40f-a08a8e986648` |
| v2.0 project ID | `33529623-3e39-40bb-a4f9-8c825258cea7` |
| v1.2 project ID | **does not yet exist — create in Stream A1** |
| Beads CLI | `bd` on PATH (DB at `.beads/worthless.db`) |
| ROADMAP file | `.planning/ROADMAP.md` (tracked) |
| Generator script (new) | `scripts/roadmap.py` (create in Stream C) |
| Snapshot dir (new) | `.planning/snapshots/` (tracked — inputs to generator) |
| Checkpoint file (new) | `.planning/launch-cleanup-state.json` (**gitignored — add rule first**) |
| Gitignore patch | append line `.planning/launch-cleanup-state.json` to `.gitignore` |
| Existing install script | `/Users/shachar/Projects/worthless/worthless-website-dev/install.sh` (175 lines, not hosted) |
| Existing Docker assets | `deploy/docker-compose.yml`, `deploy/entrypoint.sh`, `railway.toml`, `render.yaml` |
| Missing publish workflow | No GHCR/DockerHub push in `.github/workflows/` — only PyPI and Trivy scan |

Cold-start executor reading this file alone must be able to execute.

---

## Launch definition (5 testable scenarios)

Launch = ALL FIVE pass on fresh machines:

| # | Scenario | Exercises | Platform |
|---|---|---|---|
| S1 | `pipx install worthless` → `worthless` (bare) → enroll + proxy 1 OpenAI request → spend visible in `worthless status`. Under 90s. | Python-install path + core flow (baseline) | macOS/Linux with Python |
| S2 | Run `worthless` twice in a row. Second run detects running proxy, no duplicate, no orphans. | **WOR-228** PID bug | macOS/Linux |
| S3 | Fresh Claude Code with only `npx worthless-mcp` in `.mcp.json` → restart → `worthless status` via MCP tools returns. | **WOR-229** npm MCP | Any OS with Node |
| S4 | `curl -sSL https://worthless.sh \| sh` on a machine WITHOUT Python → script downloads uv (standalone binary) → uv installs Python + worthless → `worthless lock` works. | **install.sh rewritten in repo; Worker already deployed in user Cloudflare** | macOS/Linux without Python |
| S5 | `docker pull ghcr.io/<org>/worthless-proxy:latest && docker run -d ghcr.io/<org>/worthless-proxy` → proxy starts as persistent service (no manual `worthless up`), accepts one proxied request. | **Docker image published on GHCR** — replaces native service path | Any OS with Docker |

Launch blockers = exactly the issues needed for S1–S5 to pass.

---

## Decisions pinned

1. **Launch blockers (4):**
   - **WOR-228** PID bug — confirmed Urgent Backlog
   - **WOR-229** worthless-mcp npm — confirmed Urgent Backlog
   - **NEW-A: `worthless.sh` install endpoint** — narrow ticket. Serve existing `install.sh` from the domain. Parent: Launch Blockers epic. Does NOT include marketing site, SEO, email routing (those stay in WOR-214 → v1.2).
   - **NEW-B: Publish Docker image to registry** — new ticket. GHCR or DockerHub publish workflow on tag push. First image: `worthless/proxy:0.3.0`. Parent: Launch Blockers epic.
2. **WOR-214 (worthless.cloud) whole epic moves to v1.2** — except the narrow worthless.sh install endpoint extracted as NEW-A.
3. **Taxonomy:** GSD milestone = Linear Project. GSD phase = Linear Milestone. Beads scratchpad = notes. Linear parent issue = epic. Linear leaf issue = ticket.
4. **WOR-193** rename: "Wave 7: Service Management" → "Service Management". Move to v1.2.
5. **ROADMAP.md stays committed**, generated deterministically from committed snapshot JSON. CLAUDE.md "one doc" exception justified by: source is the snapshot, doc is derived.

---

## Risk register

| ID | Risk | Mitigation |
|---|---|---|
| R1 | Mid-execution context reset — 25+ writes + 45 Beads triages | Checkpoint file + resume protocol |
| R2 | `save_issue(parentId: null)` silently no-ops | A0.8 dry-run + A4/A5 read-back equality |
| R3 | Beads triage misclassifies real work as KEEP | Sub-gate after first 10, user spot-check |
| R4 | Generator script non-deterministic across runs | Script reads checked-in snapshot JSON, not live Linear. Natural sort. |
| R5 | WOR-214 children (209, 210, 212) orphan when epic moves | Move whole epic atomically; NEW-A is a new ticket, not a split of 209 |
| R6 | Snapshot JSON schema drift | `.planning/snapshots/SCHEMA.md` defines fields |
| R7 (NEW) | NEW-A and NEW-B tickets have no implementer | Plan only restructures. Execution of WOR-228/229/NEW-A/NEW-B is post-plan, separate branches per ticket |
| R8 (NEW) | Docker image publish requires registry credentials not yet set | NEW-B's AC includes "GHCR/DockerHub secret configured in GitHub settings" — user task, not plan task |

---

## Execution order

```
A0 Pre-flight (read + dry-run)      → GATE 1
A1-A6 Linear restructure            → read-back + checkpoint per write
                                    → GATE 2
B  Beads triage (all 45)            → decision table + batch migration
C  Generator script + ROADMAP regen → sha256 stability test → GATE 3
D  Three atomic commits + PR
```

Sequential. No parallelism.

---

## Stream A0 — Pre-flight

| Step | Action | Tool | Verify |
|---|---|---|---|
| A0.1 | Branch | `git checkout -b chore/launch-cleanup` | clean tree |
| A0.2 | Add gitignore line | edit `.gitignore` append `.planning/launch-cleanup-state.json` | grep confirms |
| A0.3 | Commit gitignore | `git commit -m "chore: gitignore launch-cleanup checkpoint"` | 1-file commit |
| A0.4 | Snapshot v1.1 — per-issue `get_issue` loop | Linear MCP | `.planning/snapshots/linear-v11-2026-04-18.json` |
| A0.5 | Snapshot v2.0 | Linear MCP | `.planning/snapshots/linear-v20-2026-04-18.json` |
| A0.6 | Snapshot Beads — `bd show --json` per issue | `bd` | `.planning/snapshots/beads-2026-04-18.json` (~45 entries) |
| A0.7 | Write snapshot schema | Write | `.planning/snapshots/SCHEMA.md` (fields: id, title, parentId, projectId, projectMilestoneId, labels, state, sortOrder, priority) |
| A0.8 | Dry-run `save_issue(parentId: null)` on throwaway test issue | Create + detach + cancel | If fails, HALT |
| A0.9 | Init checkpoint | `.planning/launch-cleanup-state.json = {"stream": "A0", "completed": [A0.1..A0.8], "last_id": null}` | exists, gitignored |
| A0.10 | Baseline verify S1-S5 on current main | Manual | Confirm: S1 passes, S2 fails (WOR-228 reproduces), S3 fails (WOR-229 missing), S4 fails (domain not live), S5 fails (no registry image). **If S1 fails, halt — bigger blocker exists.** |

**GATE 1 — user confirms:**
- All snapshots present and correct
- Dry-run detach works
- Current state: S1 pass, S2/S3/S4/S5 fail as expected

---

## Stream A — Linear restructure

Every write = one MCP call + read-back equality + checkpoint update.

### Read-back equality (logical helper)
After `save_issue(id, ...fields)`:
1. `result = get_issue(id)`
2. For each written field, assert `result[field] == expected`
3. Mismatch → HALT, log to checkpoint
4. Match → advance checkpoint

### A1. Create v1.2 project
1. `list_projects` confirms "Worthless v1.2" absent
2. `save_project({name: "Worthless v1.2", teamIds: [WOR], state: "planned"})` → record `V12_PROJECT_ID`
3. Read-back: `get_project(V12_PROJECT_ID).name == "Worthless v1.2"`
4. Checkpoint

### A2. Create "Wave 7 — Launch" milestone in v1.1
5. `save_milestone({projectId: V11, name: "Wave 7 — Launch", sortOrder: 7})` → record `W7_MILESTONE_ID`
6. Read-back via `list_milestones(projectId: V11)`
7. Checkpoint

### A3. Create "Launch Blockers" epic
8. `save_issue({title: "Launch Blockers", projectId: V11, projectMilestoneId: W7_MILESTONE_ID, labels: ["epic"], teamId: WOR})` → record `LB_EPIC_ID`
9. Read-back equality on `{projectId, projectMilestoneId}`
10. Checkpoint

### A4. Create NEW-A and NEW-B tickets, reparent existing blockers
11. `save_issue({title: "worthless.sh universal install: rewrite install.sh to bootstrap via uv", description: "In-repo deliverable: rewrite worthless-website-dev/install.sh to bootstrap uv (standalone binary, no Python needed) then use uv to install Python + worthless. Existing script hard-exits on non-Python machines — rewrite removes that dependency. Out of repo (user's private Cloudflare infra, not part of this ticket or repo): the Cloudflare Worker that serves install.sh at https://worthless.sh is user-owned ops work. Domain worthless.sh already live in user's Cloudflare. AC: curl -sSL https://worthless.sh | sh on fresh non-Python macOS/Linux box → uv downloaded → Python + worthless installed → worthless lock works.", parentId: LB_EPIC_ID, projectId: V11, projectMilestoneId: W7_MILESTONE_ID, priority: 1, labels: ["v1.1", "DevOps"], assigneeId: "<user-self>", dueDate: "<user-set>"})` → record `NEW_A_ID` → read-back
12. `save_issue({title: "Publish Docker image to GHCR (replaces native service path)", description: "Docker is the launch-day persistent proxy path — launchd/systemd is slipping to v1.2. New GitHub Actions workflow: on tag v* push, build from ./Dockerfile (repo root) and publish ghcr.io/<org>/worthless-proxy:X.Y.Z and :latest. Uses GITHUB_TOKEN (no external credentials needed). AC: docker pull ghcr.io/<org>/worthless-proxy:0.3.0 && docker run -d ghcr.io/<org>/worthless-proxy works from any machine with Docker, proxy runs persistently without manual worthless up. Prerequisite: package visibility set to public in repo settings after first push.", parentId: LB_EPIC_ID, projectId: V11, projectMilestoneId: W7_MILESTONE_ID, priority: 1, labels: ["v1.1", "DevOps"], assigneeId: "<user-self>", dueDate: "<user-set>"})` → record `NEW_B_ID` → read-back
13. For **WOR-228, WOR-229**: capture original parentId/milestoneId from A0.4 snapshot → `save_issue(id, {parentId: LB_EPIC_ID, projectMilestoneId: W7_MILESTONE_ID})` → read-back → checkpoint
14. Checkpoint after each

### A5. Move non-blocker epics to v1.2
Epics (detach children → move parent → reattach, each with read-back):

| Epic | Children | Rename? |
|---|---|---|
| WOR-193 | WOR-174, WOR-175 | → "Service Management" |
| WOR-216 | WOR-217, 218, 219, 220 | no |
| WOR-227 | WOR-225, 226 | no |
| WOR-230 | WOR-231 + 5 Beads migrated in Stream B | no |
| WOR-214 | WOR-209, 210, 212 (whole epic — NEW-A is separate new ticket) | no |

For each:
15. Detach each child: `save_issue(child_id, {parentId: null})` → read-back `parentId == null` (R2 critical)
16. Move epic: `save_issue(epic_id, {projectId: V12, projectMilestoneId: null, title: <new if rename>})` → read-back
17. Reattach each child: `save_issue(child_id, {parentId: epic_id, projectId: V12})` → read-back
18. Checkpoint per fully-moved epic

### A6. Orphan sweep
19. `list_issues(projectId: V11, parent: null)` → expect only `LB_EPIC_ID` + historical completed epics
20. `list_issues(projectId: V12, parent: null)` → expect only 5 moved epics
21. Unexpected orphan → HALT, escalate

**Commit 1:** `git add .gitignore .planning/snapshots/ && git commit -m "chore(planning): snapshot Linear+Beads + gitignore checkpoint"`.

**GATE 2 — user verifies in Linear UI:**
- v1.1 project: Wave 7 — Launch with Launch Blockers epic containing 4 children: WOR-228, WOR-229, NEW-A, NEW-B
- v1.2 project: 5 epics (WOR-193/216/227/230/214) with children intact
- No orphans either direction
- **NEW-A and NEW-B each have an assignee and a due date set** — if not, mark tickets unready and set before merging plan PR

---

## Stream B — Beads triage (all 45)

### B1. Decision table

| Classification | Criteria | Action |
|---|---|---|
| **MIGRATE** | Clear AC, owner-assignable, fits existing v1.2 epic | Create Linear under epic (body + notes preserved). `bd close --reason="migrated-to-linear WOR-X"` |
| **CLOSE-OBSOLETE** | Fixed, duplicate, invalidated by architecture | `bd close --reason="<specific>"` |
| **KEEP** | Active breadcrumb, not actionable, experiment | Leave open |

### B2. Pre-mapped targets (update IDs after Stream A)
- `worthless-2l9, bbi, bb1, 12i, ei6` → WOR-230 (Python Audit Remediation, v1.2) — MIGRATE
- `worthless-ake` (PID bug) → duplicate of WOR-228 — CLOSE-OBSOLETE
- `worthless-igc` (MCP) → duplicate of WOR-229 — CLOSE-OBSOLETE
- `worthless-70u` (Windows stdin) → duplicate of WOR-231 — CLOSE-OBSOLETE
- `worthless-0vd` (Windows epic) → MIGRATE as new Linear epic under v1.2
- `worthless-ta5` → duplicate of WOR-230 — CLOSE-OBSOLETE
- All others (≈32) → classify per decision table

### B3. Batch process
22. First 10 Beads → classify into `.planning/beads-triage-2026-04-18.md` (tracked)
23. **Sub-gate** — user reviews first 10, approves or rejects row-by-row
24. **Re-classify loop:** if user rejects ≥1 of first 10, re-classify those N with updated criteria, write back to file, repeat sub-gate until user approves all 10. Do NOT advance to remaining 35 with unresolved rejections.
25. Classify remaining 35 using validated criteria
26. MIGRATE set: `bd show --json` → `save_issue` with body+notes as description → read-back → `bd close`
27. CLOSE-OBSOLETE set: `bd close --reason=...`
28. Checkpoint per batch of 5

### B4. Sanity check
28. `bd list --status=open --json` → count == KEEP set
29. `list_issues(projectId: V12)` → expect MIGRATE count added

**Commit 2:** `git add .planning/beads-triage-2026-04-18.md && git commit -m "docs(planning): triage 45 Beads issues, migrate to Linear v1.2"`.

---

## Stream C — Generator script + ROADMAP regen

### C1. Snapshot refresh (post-Linear)
30. Re-run A0.4/A0.5 after all Linear writes. New snapshots:
    - `.planning/snapshots/linear-v11-post-cleanup-2026-04-18.json`
    - `.planning/snapshots/linear-v12-post-cleanup-2026-04-18.json`
    - `.planning/snapshots/linear-v20-post-cleanup-2026-04-18.json`

### C2. Write `scripts/roadmap.py`
31. New file: `scripts/roadmap.py`. Contract:
    - **Input:** glob `.planning/snapshots/linear-*-post-cleanup-*.json` — file-based only
    - **No network I/O.** No MCP. No curl.
    - **Sort:** projects by `name` asc; milestones by `sortOrder` then name; issues by natural-numeric identifier (split `WOR-`, int part asc — WOR-9 before WOR-10)
    - **Section template:**
      ```
      ## Milestone: {project.name}
      ### {milestone.name} ({status}: X/Y done)
      - [{x}] {epic_title} — {epic_id}
        - [{x}] {child_title} — {child_id}
      ```
    - **Orphans:** explicit `## Orphans (no epic parent)` section, not silent drop
    - **Missing refs:** `[MISSING]` inline + stderr log
    - **Header:** `<!-- GENERATED by scripts/roadmap.py from .planning/snapshots/linear-*-post-cleanup-*.json. Do not hand-edit. -->`

### C3. Determinism test
32. Run `python scripts/roadmap.py` → `sha256sum .planning/ROADMAP.md`
33. Run again (same inputs) → sha256 must match
34. If mismatch → fix source of non-determinism

### C4. CLAUDE.md exception paragraph
35. Append to `/Users/shachar/Projects/worthless/worthless/CLAUDE.md` (tracked) under a new section `## ROADMAP.md as generated artifact`:

> `.planning/ROADMAP.md` is a generated artifact, not a parallel source of truth. It is produced deterministically by `scripts/roadmap.py` from the checked-in Linear snapshots under `.planning/snapshots/linear-*-post-cleanup-*.json`. The repo's one-doc rule (from `feedback_merge_plans_with_tickets.md`) is preserved because nobody hand-authors ROADMAP.md — edit Linear, refresh the snapshot, regenerate, commit. Editing ROADMAP.md by hand will be overwritten on next regen. Run: `python scripts/roadmap.py`.

**GATE 3 — user reviews:**
- ROADMAP.md v1.1: Wave 7 — Launch + 4 blockers (WOR-228, 229, NEW-A, NEW-B)
- ROADMAP.md v1.2: 5 epics with children
- ROADMAP.md v2.0 unchanged
- sha256 identical on 2 runs

**Commit 3:** `git add scripts/roadmap.py .planning/ROADMAP.md .planning/snapshots/linear-*-post-cleanup-*.json CLAUDE.md && git commit -m "feat(planning): roadmap generator + regen after scope cut"`.

---

## Stream D — PR

36. `git push -u origin chore/launch-cleanup`
37. `gh pr create --title "Launch cleanup: v1.1 scope cut + Beads migration + ROADMAP generator"`
38. **Do not merge.** Open for review.

---

## Definition of Done

1. Linear v1.1 = completed Waves 1–6 + Wave 7 — Launch (Launch Blockers epic with WOR-228, WOR-229, NEW-A worthless.sh, NEW-B Docker publish).
2. Linear v1.2 = 5 epics (WOR-193/216/227/230/214) with children + migrated Beads under WOR-230.
3. `bd list --status=open` = only KEEP scratchpad.
4. `scripts/roadmap.py` exists. Twice-run sha256 identical.
5. `.planning/ROADMAP.md` reflects post-cleanup snapshot. Header says generated.
6. 3 commits on `chore/launch-cleanup`. PR open, not merged.
7. Checkpoint: `{"stream": "D", "completed": all, "status": "ready-for-review"}`.
8. Launch-readiness baseline recorded: S1 pass, S2/S3/S4/S5 fail. Post-plan execution tickets (WOR-228/229/NEW-A/NEW-B) each gets its own branch.

---

## Resume protocol (context reset)

New session reads `.planning/launch-cleanup-state.json`:
- `{"stream": "A5", "completed": [...], "last_id": "WOR-174"}` → resume A5 at next epic, skipping completed
- File missing → start A0. Snapshots idempotent on re-fetch.
- Dry-run (A0.8) idempotent (cancels test issue on re-run)

---

## Resolved decisions (from user, 2026-04-19)

1. **Registry:** GHCR. Uses `GITHUB_TOKEN`, no external credentials needed.
2. **worthless.sh hosting:** Cloudflare Worker. Versioned + instant rollback.
3. **Beads triage mechanism:**
   - I classify first 10 open Beads issues per decision table
   - Write to `.planning/beads-triage-2026-04-18.md` (tracked) — one row per issue with classification, rationale, target epic (for MIGRATE) or reason (for CLOSE-OBSOLETE)
   - **User opens that file and manually spots/corrects each row**
   - User signals ready (comment or "approved" message)
   - I then classify remaining ~35 using the validated criteria
   - Second manual pass optional on remaining 35 if criteria drifted

## Still open for user

1. **gsd-2** — unlocated. Not in primary path for v1.1 cleanup. Skip unless you want me to look harder.
2. **CLAUDE.md exception paragraph for ROADMAP.md generation** — OK to add during Stream C34, or want to write yourself?

---

## What this plan does NOT do

- Does NOT ship WOR-228, WOR-229, NEW-A, NEW-B — plan only restructures. Each gets its own execution branch after plan lands.
- Does NOT touch v2.0 phases.
- Does NOT set up registry credentials or DNS records — user actions, not plan actions.
- Does NOT automate future Linear → ROADMAP sync (post-launch: pre-push hook + CI check).
