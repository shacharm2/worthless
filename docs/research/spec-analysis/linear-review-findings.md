# Linear Board Review: Shamir+Sidecar Architecture

**Date:** 2026-04-04
**Reviewer:** Reality assessment of WOR-145 through WOR-150 epics vs. implementation plan

---

## What's Good

- **6 epics exist (WOR-145-150)** and are correctly assigned to the "Shamir+Sidecar Architecture" milestone
- **Phase 1 has subtasks** (WOR-151-154) with reasonable decomposition
- **No circular dependencies** in the parent chain
- **Implementation plan is thorough** — the spec addendum closes real gaps (integrity checks, peer auth, reboot strategy)
- **Ticket mapping document is honest** — correctly identifies 22 NEEDS REWRITE, 3 OBSOLETED

---

## Critical Gaps

### 1. Zero of 22 NEEDS REWRITE tickets have been reparented [CRITICAL]

The ticket mapping identifies 25 tickets needing rewrite. None of them have been moved under the new Phase epics. They're still parented under old epics (WOR-80, WOR-82, WOR-83, WOR-67, WOR-7, WOR-15, WOR-47). This means:

- WOR-143 ("Create new implementation epics") is marked Backlog but the epics already exist (WOR-145-150). The ticket is half-done: epics created, reparenting not done.
- Anyone looking at WOR-147 (Phase 3) sees zero children, when it should have ~10 tickets under it.
- The old epics (WOR-80, WOR-82, WOR-83) show as "Done" but still have open subtasks — misleading.

**Fix:** Reparent all 25 NEEDS REWRITE tickets under their mapped Phase epic. Update WOR-143 to reflect this.

### 2. Three tickets still not archived [HIGH]

WOR-134, WOR-135, WOR-138 are all still `Backlog`, not archived. WOR-142 ("Archive obsoleted tickets") is also still Backlog. This is a 2-minute task that hasn't been done.

**Fix:** Archive all three now. Close WOR-142.

### 3. WOR-60 subtasks are stale and conflict with Phase 3 [HIGH]

WOR-60-66 are still parented under WOR-15 (Infrastructure Hardening). They describe XOR reconstruction, CBOR envelope, HTTP-over-UDS — all concepts that changed. The implementation plan says Phase 3 is Shamir + JSON socket protocol + vault+proxy modes + seccomp/Landlock.

Specific conflicts:
- WOR-61 says "CBOR envelope" — spec says JSON
- WOR-62 says "scaffold over UDS" — now includes ShardStore trait (Phase 2 dependency)
- WOR-63 says "config switch" — now means full sidecar lifecycle management
- WOR-66 says "isolated non-root deployment" — now means maturin wheels (Phase 5)

**Fix:** Either rewrite WOR-60-66 descriptions to match the new spec, or archive them and create fresh subtasks under WOR-147. I'd archive and start fresh — the cognitive overhead of reading stale descriptions is worse than creating new tickets.

### 4. Phases 2-6 have zero subtasks [HIGH]

Only Phase 1 (WOR-145) has subtasks. The other 5 epics are empty shells with point estimates but no decomposition. This is a problem because:

- Phase 2 (Shard Store) has **8 platform backends** — each is a distinct work item with different dependencies (macOS SDK, Windows API, D-Bus, kernel keyutils, etc.)
- Phase 3 (Sidecar Binary) at 8 points covers: async socket server, Shamir reconstruction, mlock/zeroize, seccomp-BPF, Landlock, connection pooling, streaming, vault mode, proxy mode. That's at minimum 8-10 subtasks.
- Phase 4 (Python Layer) modifies ~12 files and adds 5 new ones. No breakdown.

This isn't "premature decomposition" — it's missing work breakdown for phases that will start within weeks.

**Fix:** Create subtasks for at least Phases 2, 3, and 4 now. Phase 5-6 can wait.

---

## Estimate Reality Check

| Phase | Points | Assessment |
|-------|--------|------------|
| Phase 1 (Shamir Core) | 3 | **Reasonable.** ~100 lines Rust + tests + Python companion. Well-scoped. |
| Phase 2 (Shard Store) | 5 | **Underestimated.** 8 platform backends, each with different APIs and CI requirements. Linux two-layer strategy alone is 2-3 points. Realistic: 8-10 points. |
| Phase 3 (Sidecar Binary) | 8 | **Borderline.** The spec says ~1500 lines. But seccomp-BPF allowlists, Landlock filesystem policies, connection pooling, and streaming proxying are each non-trivial. The "per-request flow" has 8 steps, each with error handling. Realistic: 10-13 points. |
| Phase 4 (Python Layer) | 8 | **Reasonable if sidecar works.** Mostly rewiring existing code. Migration tool is the wild card. |
| Phase 5 (Distribution) | 3 | **Underestimated.** Cross-platform maturin builds with native Rust binaries on 4+ targets, CI workflows, and platform-specific smoke tests. Maturin is well-documented but cross-compilation debugging eats time. Realistic: 5 points. |
| Phase 6 (Hardening) | 5 | **Reasonable.** Mostly documentation + optional features. |

**Total claimed: 32 points. Realistic: 40-44 points.**

---

## Migration Epic (WOR-139) Status

All 5 subtasks (WOR-140-144) are Backlog. None are in-progress or done. But:

- WOR-141 (ticket mapping) has the mapping document written — the ticket should be in-progress or done
- WOR-143 (create epics) — epics exist (WOR-145-150), but reparenting isn't done. Half-complete.
- WOR-140 (review architecture) — the spec addendum exists, suggesting this review happened. Should be done.
- WOR-142 (archive obsoleted) — not done, as shown above
- WOR-144 (update docs) — not done

**Fix:** Update statuses: WOR-140 and WOR-141 should be Done. WOR-143 should be In Progress.

---

## Orphaned Tickets Worth Noting

These Backlog tickets have no parent and aren't epics:
- **WOR-103** — "Auth collapse from alias inference + Shard A fallback" — This is a security finding. Does it still apply under Shamir? Needs triage.
- **WOR-104** — "OpenAI and Anthropic metering gaps" — Proxy-layer concern, still valid. Should be parented somewhere.
- **WOR-118** — "Add worthless down command" — duplicate of WOR-95 (already mapped to Phase 3 rewrite). Archive one.

There are also duplicated ticket numbers in Linear vs. the mapping doc. WOR-95 is "Add worthless down command" in the mapping but WOR-118 is the same thing floating as an orphan. Similarly WOR-117 maps to WOR-127.

---

## Duplicate / Stale Ticket Pairs

| Mapping Doc | Linear Orphan | Topic | Action |
|-------------|---------------|-------|--------|
| WOR-95 | WOR-118 | `worthless down` command | Archive WOR-118, keep WOR-95 |
| WOR-127 | WOR-117 | `@error_boundary` + `--debug` | Archive WOR-117, keep WOR-127 |
| WOR-101 | WOR-116 | MCP docs wrong process type | Archive WOR-116, keep WOR-101 |
| WOR-102 | WOR-115 | GitHub Actions CI mismatch | Archive WOR-115, keep WOR-102 |
| WOR-98 | WOR-114 | pyproject.toml version align | Archive WOR-114, keep WOR-98 |
| WOR-97 | WOR-113 | AGPL-3.0 license file | Archive WOR-113, keep WOR-97 |
| WOR-32 | WOR-77 | Live uvicorn test harness | Archive WOR-77, keep WOR-32 |

That's 7 duplicate pairs creating noise on the board.

---

## Action Plan (Priority Order)

1. **[5 min] Archive WOR-134, WOR-135, WOR-138.** Close WOR-142.
2. **[5 min] Update migration ticket statuses.** WOR-140 → Done. WOR-141 → Done. WOR-143 → In Progress.
3. **[10 min] Archive 7 duplicate tickets** (WOR-77, WOR-113, WOR-114, WOR-115, WOR-116, WOR-117, WOR-118).
4. **[30 min] Reparent 25 NEEDS REWRITE tickets** under correct Phase epics per the mapping doc.
5. **[30 min] Archive WOR-60-66 and create fresh Phase 3 subtasks** with correct Shamir/JSON/vault descriptions.
6. **[1 hr] Create subtasks for Phase 2, 3, 4.** Phase 2 needs per-backend tickets. Phase 3 needs startup/request-flow/security decomposition. Phase 4 needs per-file-group tickets.
7. **[15 min] Triage orphans** — WOR-103, WOR-104 need parent assignment or archival decision.
8. **[15 min] Adjust estimates** — Phase 2: 5→8, Phase 3: 8→13, Phase 5: 3→5.

---

## Prevention Recommendations

- **Do not mark WOR-143 as Done until reparenting is complete.** The epics existing is half the work; tickets living under them is the other half.
- **Each Phase epic should show its subtask count in the title or description** so empty epics are visually obvious.
- **The 22 NEEDS REWRITE tickets should be rewritten before Phase 3 starts** — otherwise developers will read stale XOR/CBOR descriptions and build the wrong thing.
- **Add a "Shamir Migration" label** to all affected tickets so they're filterable.
