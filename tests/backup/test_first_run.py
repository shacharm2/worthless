"""RED-phase tests for WOR-276 first-run notice + RECOVERY.md shipping.

These tests assert end-user-visible invariants, not implementation:

* The first backup written to a new per-repo bucket prints a one-shot
  hint (to stderr) telling the user where backups live and how to
  restore; subsequent backups to the same bucket stay silent.
* The silence is anchored by a `0o600` ``.first-run-seen`` marker inside
  the bucket directory.
* The scriptable contract — ``stdout`` stays clean — is pinned
  independently of whatever the notice text happens to be.
* ``RECOVERY.md`` is shipped inside the wheel (reachable via
  ``importlib.resources``) and its *first* fenced code block is the
  literal recovery command users copy-paste.

The module under test does not exist yet (``worthless.cli.backup``). Per
RED convention, each test imports it inside the test body so collection
still succeeds and failure messages are attached to individual tests.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="backup suite is macOS + Linux only",
)


# ---------------------------------------------------------------------------
# Bucket path helper (pure; duplicates the locked contract in the plan)
# ---------------------------------------------------------------------------


def _bucket_for(repo_root: Path) -> str:
    """SHA-256 of the resolved repo root — the locked bucket name."""
    return hashlib.sha256(str(repo_root.resolve()).encode("utf-8")).hexdigest()


def _bucket_dir(xdg: Path, repo_root: Path) -> Path:
    """Resolve the expected on-disk bucket directory for ``repo_root``."""
    return xdg / "worthless" / "backups" / _bucket_for(repo_root)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_run_prints_backup_path_once(
    tmp_repo: Path,
    fake_xdg: Path,
    make_env_file,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """First backup in a fresh bucket emits the notice; second is silent."""
    from worthless.cli import backup  # RED: module doesn't exist
    from worthless.cli.safe_rewrite import safe_rewrite

    target = make_env_file(tmp_repo / ".env", content=b"A=1\n")
    bucket = _bucket_dir(fake_xdg, tmp_repo)

    # Bind the backup hook so safe_rewrite writes through to backup.py.
    backup.set_backup_hook()  # type: ignore[attr-defined]

    safe_rewrite(
        target,
        b"A=2\n",
        original_user_arg=target,
        repo_root=tmp_repo,
    )
    first = capsys.readouterr()
    assert str(bucket) in first.err
    assert "worthless restore" in first.err

    safe_rewrite(
        target,
        b"A=3\n",
        original_user_arg=target,
        repo_root=tmp_repo,
    )
    second = capsys.readouterr()
    assert str(bucket) not in second.err
    assert "worthless restore" not in second.err


def test_first_run_marker_file_created(
    tmp_repo: Path,
    fake_xdg: Path,
    make_env_file,
) -> None:
    """A ``.first-run-seen`` marker exists in the bucket after one backup."""
    from worthless.cli import backup  # RED: module doesn't exist
    from worthless.cli.safe_rewrite import safe_rewrite

    target = make_env_file(tmp_repo / ".env", content=b"A=1\n")
    backup.set_backup_hook()  # type: ignore[attr-defined]

    safe_rewrite(
        target,
        b"A=2\n",
        original_user_arg=target,
        repo_root=tmp_repo,
    )

    bucket = _bucket_dir(fake_xdg, tmp_repo)
    assert (bucket / ".first-run-seen").is_file()


def test_first_run_marker_mode_is_0600(
    tmp_repo: Path,
    fake_xdg: Path,
    make_env_file,
) -> None:
    """The marker is written with ``0o600`` — same secrecy class as ``.env``."""
    from worthless.cli import backup  # RED: module doesn't exist
    from worthless.cli.safe_rewrite import safe_rewrite

    target = make_env_file(tmp_repo / ".env", content=b"A=1\n")
    backup.set_backup_hook()  # type: ignore[attr-defined]

    safe_rewrite(
        target,
        b"A=2\n",
        original_user_arg=target,
        repo_root=tmp_repo,
    )

    marker = _bucket_dir(fake_xdg, tmp_repo) / ".first-run-seen"
    assert marker.stat().st_mode & 0o777 == 0o600


def test_first_run_message_goes_to_stderr_not_stdout(
    tmp_repo: Path,
    fake_xdg: Path,
    make_env_file,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stdout stays clean so ``worthless`` composes in pipelines and scripts."""
    from worthless.cli import backup  # RED: module doesn't exist
    from worthless.cli.safe_rewrite import safe_rewrite

    target = make_env_file(tmp_repo / ".env", content=b"A=1\n")
    bucket = _bucket_dir(fake_xdg, tmp_repo)
    backup.set_backup_hook()  # type: ignore[attr-defined]

    safe_rewrite(
        target,
        b"A=2\n",
        original_user_arg=target,
        repo_root=tmp_repo,
    )

    captured = capsys.readouterr()
    assert str(bucket) in captured.err
    assert "worthless restore" in captured.err
    assert str(bucket) not in captured.out
    assert "worthless restore" not in captured.out


def test_recovery_md_shipped_in_wheel() -> None:
    """``RECOVERY.md`` is reachable via ``importlib.resources`` — i.e. packaged."""
    from importlib.resources import files

    resource = files("worthless").joinpath("RECOVERY.md")
    assert resource.is_file()


def test_recovery_md_first_fenced_block_is_the_command() -> None:
    """The first fenced block in ``RECOVERY.md`` is the copy-paste command."""
    from importlib.resources import files

    text = files("worthless").joinpath("RECOVERY.md").read_text(encoding="utf-8")
    match = re.search(r"```[a-z]*\n(.*?)\n```", text, re.DOTALL)
    assert match is not None, "RECOVERY.md has no fenced code block"
    assert "worthless restore" in match.group(1)
