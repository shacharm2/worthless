# New ticket brief — Grype vulnerability DB freshness pre-check

**Proposed project**: v1.2 (hardening — low-probability failure mode)
**Proposed priority**: P2 (defense-in-depth; current state is "probably fine")
**Proposed labels**: `v1.2`, `DevOps`, `security`
**Proposed parent epic**: none

## Story (ELI5)

Our release workflow uses Grype (via `anchore/scan-action`) to scan images for CRITICAL CVEs before publish. Grype fetches its vulnerability database from Anchore's endpoint at scan time. If that endpoint is down or serves stale data, chaos-engineer reviewer flagged a theoretical risk: the scan might silently pass with incomplete CVE data, letting a vulnerable image ship. Worth verifying whether current action behavior handles this correctly, and adding an explicit pre-check if not.

## Why this issue exists

WOR-236 swapped from Trivy to Grype after the TeamPCP compromise. Both tools fetch their vuln DB at scan time. `anchore/scan-action@v7.4.0` with `fail-build: true` is documented to fail on DB unavailability, but chaos-engineer flagged a concern that `only-fixed` filtering might run BEFORE the DB-freshness check, producing a silent empty-result pass. Current behavior is probably fine but not explicitly verified.

## What needs to be done

### Part 1: Investigation

- Read `anchore/scan-action` source at the pinned v7.4.0 SHA. Confirm the order of operations: DB fetch → failure if unreachable → scan → filter results.
- Test locally by blocking `toolbox-data.anchore.io` (Grype's DB endpoint) and running the action. Confirm it fails loud.

### Part 2: If investigation finds a gap

Add an explicit pre-scan step:

```yaml
- name: Verify Grype DB is fresh
  run: |
    grype db update --fail-on-stale
    grype db status | tee db-status.log
    # Assert DB built date is within last 7 days
    built_date=$(grype db status | grep -oP 'Built:\s+\K\S+')
    days_old=$(( ( $(date +%s) - $(date -d "$built_date" +%s) ) / 86400 ))
    if [ "$days_old" -gt 7 ]; then
      echo "::error::Grype DB is $days_old days old (threshold: 7). Scan may be incomplete."
      exit 1
    fi
```

This requires grype binary installed — either via `anchore/scan-action/download-grype` or direct install.

### Part 3: If investigation finds no gap

- Document the finding in a comment in `publish-docker.yml` near the Grype step, citing the upstream behavior.
- Close the ticket as "verified safe, no change."

## Acceptance criteria

- [ ] Investigation result documented (either in the ticket or in-line in the workflow).
- [ ] If a gap was found: pre-check step added and tested.
- [ ] If no gap: comment added in workflow confirming upstream behavior is safe, with link to the relevant anchore/scan-action code.

## Research context for the implementer

- Grype DB endpoint: `https://toolbox-data.anchore.io/grype/databases/`
- `anchore/scan-action@v7.4.0` SHA: `e1165082ffb1fe366ebaf02d8526e7c4989ea9d2`
- Current scan steps:
  - `publish-docker.yml`: two Grype steps (amd64 + arm64 tarball) with `fail-build: true, only-fixed: true, severity-cutoff: critical`
  - `docker-security.yml`: two Grype steps (fixable + informational) on PRs

## Dependencies

- WOR-236 (this ticket extends its Grype setup).

## Scope boundary

Does NOT include:
- Pinning Grype DB to a specific snapshot (releases would then be time-locked to DB version).
- Self-hosting the Grype DB (overkill; ongoing ops cost).
- Moving to a different scanner (Grype is the agreed choice).

## Effort estimate

- Investigation only: ~1 hour.
- Investigation + implementing pre-check if needed: ~3 hours total.
