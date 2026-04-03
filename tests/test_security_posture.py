"""Smoke tests for SECURITY_POSTURE.md structure and completeness.

These tests validate that the security posture document exists, covers all
required sections, and does not overclaim compliance certifications.

Most tests skip gracefully when SECURITY_POSTURE.md does not yet exist —
they become active once Plan 05-02 creates the document.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_POSTURE_PATH = _REPO_ROOT / "SECURITY_POSTURE.md"
_SECURITY_PATH = _REPO_ROOT / "SECURITY.md"

_skip_no_posture = pytest.mark.skipif(
    not _POSTURE_PATH.exists(),
    reason="SECURITY_POSTURE.md not yet created",
)


def _posture_text() -> str:
    """Read SECURITY_POSTURE.md content (only called when file exists)."""
    return _POSTURE_PATH.read_text()


# ---------------------------------------------------------------------------
# SECURITY.md (no skip — created in this phase)
# ---------------------------------------------------------------------------


class TestSecurityMd:
    """SECURITY.md must exist at repo root with disclosure policy."""

    def test_security_md_exists(self) -> None:
        assert _SECURITY_PATH.exists(), (
            "SECURITY.md not found at repo root — "
            "vulnerability disclosure policy is missing"
        )


# ---------------------------------------------------------------------------
# SECURITY_POSTURE.md structure
# ---------------------------------------------------------------------------


@_skip_no_posture
class TestSecurityPostureExists:
    def test_security_posture_exists(self) -> None:
        assert _POSTURE_PATH.exists()


@_skip_no_posture
class TestSecurityPostureStructure:
    """Validate required sections and content in SECURITY_POSTURE.md."""

    def test_has_table_of_contents(self) -> None:
        text = _posture_text()
        assert "Table of Contents" in text or "## Contents" in text, (
            "SECURITY_POSTURE.md must have a Table of Contents"
        )

    def test_has_glossary(self) -> None:
        text = _posture_text()
        assert "Glossary" in text, (
            "SECURITY_POSTURE.md must have a Glossary section"
        )

    def test_covers_all_three_invariants(self) -> None:
        text = _posture_text()
        assert "Client-Side Splitting" in text or "client-side split" in text.lower(), (
            "SECURITY_POSTURE.md must cover Invariant 1: Client-Side Splitting"
        )
        assert "Gate Before Reconstruction" in text or "gate before reconstruct" in text.lower(), (
            "SECURITY_POSTURE.md must cover Invariant 2: Gate Before Reconstruction"
        )
        assert "Server-Side Direct Upstream" in text or "server-side direct" in text.lower(), (
            "SECURITY_POSTURE.md must cover Invariant 3: Server-Side Direct Upstream Call"
        )

    def test_covers_all_security_rules(self) -> None:
        text = _posture_text()
        for rule_num in range(1, 9):
            rule_id = f"SR-{rule_num:02d}"
            assert rule_id in text, (
                f"SECURITY_POSTURE.md must reference {rule_id}"
            )

    def test_uses_defined_confidence_scale(self) -> None:
        text = _posture_text()
        assert "Enforced" in text, "Must use 'Enforced' confidence level"
        assert "Best-effort" in text or "Best-Effort" in text, (
            "Must use 'Best-effort' confidence level"
        )
        assert "Planned" in text, "Must use 'Planned' confidence level"

    def test_has_known_limitations(self) -> None:
        text = _posture_text()
        assert "Known Limitation" in text or "Limitations" in text, (
            "SECURITY_POSTURE.md must have Known Limitations section"
        )

    def test_has_rust_mitigation(self) -> None:
        text = _posture_text()
        assert "Rust" in text or "zeroize" in text, (
            "SECURITY_POSTURE.md must reference Rust or zeroize as mitigation path"
        )

    def test_has_non_goals(self) -> None:
        text = _posture_text()
        has_non_goals = any(
            phrase in text
            for phrase in ("Non-Goals", "Out of Scope", "Does NOT protect", "Non-goals")
        )
        assert has_non_goals, (
            "SECURITY_POSTURE.md must have Non-Goals or Out of Scope section"
        )

    def test_cites_test_evidence(self) -> None:
        text = _posture_text()
        assert "test_invariants" in text, (
            "SECURITY_POSTURE.md must cite test_invariants.py as evidence"
        )
        assert "test_security_properties" in text, (
            "SECURITY_POSTURE.md must cite test_security_properties.py as evidence"
        )

    def test_has_trust_boundary_diagram(self) -> None:
        text = _posture_text()
        has_diagram = (
            "Trust Boundary" in text
            or "trust boundary" in text
            or "┌" in text
            or "```" in text
        )
        assert has_diagram, (
            "SECURITY_POSTURE.md must have a trust boundary diagram"
        )

    def test_has_residual_risk_table(self) -> None:
        text = _posture_text()
        assert "Residual Risk" in text or "residual risk" in text, (
            "SECURITY_POSTURE.md must have a Residual Risk section"
        )

    def test_has_changelog(self) -> None:
        text = _posture_text()
        assert "Changelog" in text or "Change Log" in text, (
            "SECURITY_POSTURE.md must have a Changelog section"
        )

    def test_has_last_verified_date(self) -> None:
        text = _posture_text()
        assert "Last verified:" in text or "Last reviewed:" in text, (
            "SECURITY_POSTURE.md must have a 'Last verified:' or 'Last reviewed:' date"
        )

    def test_links_to_security_md(self) -> None:
        text = _posture_text()
        assert "SECURITY.md" in text, (
            "SECURITY_POSTURE.md must link to SECURITY.md"
        )

    def test_no_compliance_overclaiming(self) -> None:
        text = _posture_text()
        overclaims = []
        for claim in ("SOC 2 certified", "FIPS validated", "ISO 27001 certified"):
            if claim in text:
                overclaims.append(claim)
        assert not overclaims, (
            f"SECURITY_POSTURE.md overclaims compliance: {overclaims} — "
            f"Worthless is not certified for any of these"
        )
