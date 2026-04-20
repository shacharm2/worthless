#!/usr/bin/env python3
"""Generate .planning/ROADMAP.md deterministically from checked-in Linear snapshots.

Usage:
    python scripts/roadmap.py

Reads:  .planning/snapshots/linear-*-post-cleanup-*.json
Writes: .planning/ROADMAP.md

Do NOT hand-edit ROADMAP.md — it will be overwritten on next run.
Edit Linear, refresh snapshots, then re-run this script.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SNAPSHOT_DIR = REPO_ROOT / ".planning" / "snapshots"
OUTPUT = REPO_ROOT / ".planning" / "ROADMAP.md"

# Canonical project order
PROJECT_ORDER = ["v1.1", "v1.2", "v2.0"]

STATE_EMOJI = {
    "Done": "✅",
    "In Progress": "🔄",
    "Cancelled": "❌",
    "Backlog": "○",
    "Todo": "○",
    "In Review": "🔄",
}

PRIORITY_LABEL = {0: "urgent", 1: "high", 2: "medium", 3: "low", 4: "backlog"}


def load_snapshots() -> dict[str, dict]:
    """Load post-cleanup snapshots keyed by project slug."""
    snapshots: dict[str, dict] = {}
    for path in sorted(SNAPSHOT_DIR.glob("linear-*-post-cleanup-*.json")):
        # extract slug: linear-v11-post-cleanup-... -> v11
        m = re.match(r"linear-(v\d+\w*)-post-cleanup", path.name)
        if not m:
            continue
        raw_slug = m.group(1)  # v11, v12, v20
        # normalise to v1.1, v1.2, v2.0
        slug = raw_slug[0] + raw_slug[1] + "." + raw_slug[2:]  # v1.1
        data = json.loads(path.read_text())
        snapshots[slug] = data
    return snapshots


def build_tree(issues: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Return (top_level, children_by_parent_id)."""
    by_id = {i["id"]: i for i in issues}
    children: dict[str, list[dict]] = {}
    top_level: list[dict] = []

    for issue in issues:
        parent = issue.get("parent")
        if parent and parent["id"] in by_id:
            children.setdefault(parent["id"], []).append(issue)
        else:
            top_level.append(issue)

    # Natural sort by identifier number
    def sort_key(i: dict) -> int:
        m = re.search(r"\d+", i.get("identifier", "0"))
        return int(m.group()) if m else 0

    top_level.sort(key=sort_key)
    for v in children.values():
        v.sort(key=sort_key)

    return top_level, children


def fmt_issue(issue: dict, indent: int = 0) -> str:
    ident = issue.get("identifier", "?")
    title = issue.get("title", "")
    state = issue.get("state", {}).get("name", "")
    emoji = STATE_EMOJI.get(state, "○")
    prefix = "  " * indent
    return f"{prefix}- {emoji} **{ident}** {title}"


def render_project(slug: str, data: dict) -> list[str]:
    lines: list[str] = []
    project_name = data.get("project", slug)
    lines.append(f"## {project_name}")
    lines.append("")

    issues = data.get("issues", [])
    if not issues:
        lines.append("_No issues._")
        lines.append("")
        return lines

    top_level, children = build_tree(issues)

    # Group top-level by milestone
    by_milestone: dict[str, list[dict]] = {}
    no_milestone: list[dict] = []
    for issue in top_level:
        ms = issue.get("projectMilestone")
        if ms:
            key = ms["name"]
            by_milestone.setdefault(key, []).append(issue)
        else:
            no_milestone.append(issue)

    # Sort milestones naturally
    def ms_sort(name: str) -> int:
        m = re.search(r"\d+", name)
        return int(m.group()) if m else 999

    for ms_name in sorted(by_milestone.keys(), key=ms_sort):
        lines.append(f"### {ms_name}")
        lines.append("")
        for epic in by_milestone[ms_name]:
            lines.append(fmt_issue(epic, indent=0))
            for child in children.get(epic["id"], []):
                lines.append(fmt_issue(child, indent=1))
        lines.append("")

    if no_milestone:
        lines.append("### (no milestone)")
        lines.append("")
        for epic in no_milestone:
            lines.append(fmt_issue(epic, indent=0))
            for child in children.get(epic["id"], []):
                lines.append(fmt_issue(child, indent=1))
        lines.append("")

    return lines


def main() -> None:
    snapshots = load_snapshots()

    lines: list[str] = [
        "# Worthless — Roadmap",
        "",
        "> **Generated file. Do not edit by hand.**",
        "> Source: `.planning/snapshots/linear-*-post-cleanup-*.json`",
        "> Regenerate: `python scripts/roadmap.py`",
        "",
    ]

    missing = [s for s in PROJECT_ORDER if s not in snapshots]
    if missing:
        print(
            f"WARNING: no post-cleanup snapshot for: {', '.join(missing)}",
            file=sys.stderr,
        )

    for slug in PROJECT_ORDER:
        if slug not in snapshots:
            continue
        lines.extend(render_project(slug, snapshots[slug]))

    OUTPUT.write_text("\n".join(lines) + "\n")
    print(f"Written: {OUTPUT.relative_to(REPO_ROOT)}")
    print(f"Projects: {', '.join(k for k in PROJECT_ORDER if k in snapshots)}")
    total = sum(len(s.get("issues", [])) for s in snapshots.values())
    print(f"Issues:   {total}")


if __name__ == "__main__":
    main()
