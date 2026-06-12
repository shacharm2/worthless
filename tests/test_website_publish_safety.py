"""Regression guards for main:website/ publish safety (WOR-737).

Production GitHub Pages deploys from ``website/`` on ``main``. Internal
research, planning, prompts, and adversarial review material must never
ship in that tree.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBSITE = REPO_ROOT / "website"

PROHIBITED_PATHS = [
    WEBSITE / "research",
    WEBSITE / "adversarial",
    WEBSITE / "ARCHITECTURE.md",
    WEBSITE / "security-model.md",
    WEBSITE / "risk-key-material-in-python-memory.md",
    WEBSITE / "DOMAIN_PLAN.md",
    WEBSITE / "superpowers",
    WEBSITE / "planning",
]

TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".txt", ".xml", ""}

INTERNAL_REFERENCE_NEEDLES = (
    "website/research/",
    "/research/",
    "website/adversarial/",
    "/adversarial/",
    "website/ARCHITECTURE.md",
    "/ARCHITECTURE.md",
    "security-model.md",
    "risk-key-material-in-python-memory.md",
    "DOMAIN_PLAN.md",
    "superpowers/plans/",
    "website/planning/",
    ".planning/",
    "ROADMAP.md",
    "worthless" + "-" + "cloud",
)

DISCOVERY_SURFACE_PATHS = (
    WEBSITE / "sitemap.xml",
    WEBSITE / "llms.txt",
    WEBSITE / "robots.txt",
)

INTERNAL_URL_FRAGMENTS = (
    "/research/",
    "/adversarial/",
    "ARCHITECTURE.md",
    "security-model.md",
    "risk-key-material-in-python-memory.md",
    "DOMAIN_PLAN.md",
    "/superpowers/",
)


def _public_text_files() -> list[Path]:
    files: list[Path] = []
    for path in WEBSITE.rglob("*"):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            path.read_text(encoding="utf-8")
            files.append(path)
    return sorted(files)


def test_website_tree_excludes_internal_planning_sources() -> None:
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in PROHIBITED_PATHS
        if path.is_file() or (path.is_dir() and any(path.rglob("*")))
    ]

    assert offenders == []


def test_website_text_files_do_not_reference_internal_paths() -> None:
    offenders: list[str] = []
    for path in _public_text_files():
        text = path.read_text(encoding="utf-8")
        for needle in INTERNAL_REFERENCE_NEEDLES:
            if needle in text:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {needle}")
                break

    assert offenders == []


def test_website_discovery_surfaces_exclude_internal_urls() -> None:
    offenders: list[str] = []
    for path in DISCOVERY_SURFACE_PATHS:
        text = path.read_text(encoding="utf-8")
        for fragment in INTERNAL_URL_FRAGMENTS:
            if fragment in text:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {fragment}")
                break

    assert offenders == []
