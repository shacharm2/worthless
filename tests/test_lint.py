"""Lint enforcement: the random module must be banned (CRYP-04)."""

import subprocess


def test_random_module_banned() -> None:
    """ruff TID251 must flag any use of the random module under src/."""
    result = subprocess.run(
        ["ruff", "check", "src/", "--select", "TID251"],  # noqa: S607
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"ruff TID251 violations found:\n{result.stdout}"
