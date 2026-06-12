"""Tests for `worthless uninstall` (WOR-435).

Starts with the mode-clamp safety helper (brutus /merge-ready gate-6 P1):
restore the original mode, but NEVER looser than 0o600 on a file that now
holds the reconstructed real key — no group/other access to a secret.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome

runner = CliRunner()


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (None, None),  # never captured (pre-715) → leave file as-is
        (0o600, 0o600),  # already secure → unchanged
        (0o400, 0o400),  # tighter than 600 (read-only) → preserved exactly
        (0o644, 0o600),  # world-readable → clamped (no group/other read of the key)
        (0o640, 0o600),  # group-readable → clamped
        (0o666, 0o600),  # world-writable → clamped
        (0o700, 0o700),  # owner-only (with exec) → unchanged (no group/other)
        (0o755, 0o700),  # group/other exec+read → clamped to owner-only
    ],
)
def test_secure_restore_mode_clamps_group_and_other(
    original: int | None, expected: int | None
) -> None:
    """secure_restore_mode strips ALL group/other bits — never re-exposes the key.

    The original is preserved when it's already owner-only (incl. tighter
    read-only 0o400); anything granting group/other access is clamped so the
    restored .env (now holding the real key) is owner-only.
    """
    from worthless.cli.commands.uninstall import secure_restore_mode

    assert secure_restore_mode(original) == expected


@pytest.mark.parametrize(
    ("orig", "expected_final"),
    [
        (0o644, 0o600),  # world-readable → clamped owner-only
        (0o640, 0o600),  # group-readable → clamped owner-only
        (0o600, 0o600),  # already secure → unchanged
    ],
)
def test_uninstall_restores_key_and_applies_mode_policy(
    home_dir: WorthlessHome, tmp_path, orig: int, expected_final: int
) -> None:
    """End-to-end: lock a real .env, run `worthless uninstall --yes`, assert the
    real key is back, the mode policy applied, and ~/.worthless wiped.
    """
    from tests.helpers import fake_key

    key = fake_key("sk-")
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={key}\n")
    env.chmod(orig)

    locked = runner.invoke(
        app, ["lock", "--env", str(env)], env={"WORTHLESS_HOME": str(home_dir.base_dir)}
    )
    assert locked.exit_code == 0, locked.output
    assert (env.stat().st_mode & 0o777) == 0o600  # lock tightened it
    assert key not in env.read_text()  # shard-A, not the real key

    uninst = runner.invoke(
        app, ["uninstall", "--yes"], env={"WORTHLESS_HOME": str(home_dir.base_dir)}
    )
    assert uninst.exit_code == 0, uninst.output
    assert key in env.read_text(), "real key not restored to .env"
    assert (env.stat().st_mode & 0o777) == expected_final, (
        f"mode policy: expected 0o{expected_final:o}, got 0o{env.stat().st_mode & 0o777:o}"
    )
    assert not home_dir.base_dir.exists(), "~/.worthless not wiped"


def test_uninstall_idempotent_second_run_is_clean(home_dir: WorthlessHome, tmp_path) -> None:
    """A second uninstall on an already-clean machine exits 0 (nothing to do)."""
    from tests.helpers import fake_key

    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={fake_key('sk-')}\n")
    runner.invoke(app, ["lock", "--env", str(env)], env={"WORTHLESS_HOME": str(home_dir.base_dir)})
    runner.invoke(app, ["uninstall", "--yes"], env={"WORTHLESS_HOME": str(home_dir.base_dir)})

    second = runner.invoke(
        app, ["uninstall", "--yes"], env={"WORTHLESS_HOME": str(home_dir.base_dir)}
    )
    assert second.exit_code == 0, second.output
