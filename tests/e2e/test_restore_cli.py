"""E2E tests for ``worthless restore`` — RED phase (WOR-276 Phase 3).

These tests invoke the real CLI via subprocess. The ``restore`` subcommand
does not yet exist; each test is expected to fail with a nonzero exit and
stderr mentioning an unknown command (argparse/Click ``No such command
'restore'``) until Phase 3 lands the implementation.

Invariants under test:

* After a successful safe_rewrite that creates a backup, ``worthless restore
  <target>`` restores the pre-rewrite bytes exactly (byte-identical SHA-256),
  both in interactive (``y\\n`` on stdin) and non-interactive (``--force``)
  modes.
* Restore preserves exotic byte sequences (UTF-8 BOM, CRLF endings,
  ``export`` lines) bit-for-bit.
* If a safe_rewrite refuses mid-flight, no ``.bak`` is produced, and
  ``worthless restore --list`` prints nothing and exits 0.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_env(tmp_path: Path) -> dict[str, str]:
    """Return a child-process env with ``XDG_DATA_HOME`` isolated to tmp.

    Ensures the test never touches the user's real ``~/.local/share`` and
    that every E2E invocation sees a fresh backup bucket.
    """

    xdg = tmp_path / "xdg-data"
    xdg.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["XDG_DATA_HOME"] = str(xdg)
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return env


def _run_safe_rewrite_via_python(
    *,
    target: Path,
    new_content: bytes,
    env: dict[str, str],
    expect_refuse: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Drive ``safe_rewrite`` from a one-shot Python snippet.

    Used to create a real backup (sub-PR 2 integration path) without
    depending on ``worthless lock`` being backup-wired yet. When
    ``expect_refuse`` is True, the snippet swallows ``UnsafeRewriteRefused``
    and exits 7 so callers can assert the refusal code.
    """

    encoded = new_content.hex()
    snippet = textwrap.dedent(
        f"""
        from pathlib import Path
        from worthless.cli.safe_rewrite import safe_rewrite
        from worthless.cli.errors import UnsafeRewriteRefused

        target = Path({str(target)!r})
        content = bytes.fromhex({encoded!r})
        try:
            safe_rewrite(
                target,
                content,
                original_user_arg=target,
                repo_root=target.parent,
                allow_outside_repo=True,
            )
        except UnsafeRewriteRefused as exc:
            print(f"REFUSED:{{exc.reason.name}}")
            raise SystemExit(7)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        env=env,
        cwd=str(target.parent),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if expect_refuse:
        assert proc.returncode == 7, (
            f"expected UnsafeRewriteRefused (exit 7), got {proc.returncode}: "
            f"{proc.stdout!r} {proc.stderr!r}"
        )
    else:
        assert proc.returncode == 0, f"safe_rewrite snippet failed: {proc.stdout!r} {proc.stderr!r}"
    return proc


# ---------------------------------------------------------------------------
# Tests (plan §5 lines 33-35)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "stdin"),
    [("prompt", "y\n"), ("force", None)],
    ids=["prompt", "force"],
)
def test_lock_then_corrupt_then_restore_round_trip(
    tmp_path: Path,
    worthless_cli: list[str],
    mode: str,
    stdin: str | None,
) -> None:
    """``restore`` must recover byte-identical pre-corruption content.

    Holds for both interactive (``y\\n``) and non-interactive (``--force``)
    invocations; the produced SHA-256 must match the pre-rewrite baseline.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    env_file = repo / ".env"
    original = b"API_KEY=preserved\nDB_URL=postgres://x\n"
    env_file.write_bytes(original)
    pre_sha = _sha256(original)

    child_env = _build_env(tmp_path)
    _run_safe_rewrite_via_python(
        target=env_file,
        new_content=b"API_KEY=preserved\nDB_URL=postgres://x\nNEW=1\n",
        env=child_env,
    )

    env_file.write_bytes(b"\x00\x01\x02 corrupted garbage \xff\xfe")

    argv = [*worthless_cli, "restore", str(env_file)]
    if mode == "force":
        argv.append("--force")

    proc = subprocess.run(
        argv,
        env=child_env,
        cwd=str(repo),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, (
        f"restore failed ({mode}): exit={proc.returncode} "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert _sha256(env_file.read_bytes()) == pre_sha, (
        f"restore did not recover byte-identical content ({mode})"
    )


def test_restore_preserves_bom_crlf_and_export_lines(
    tmp_path: Path,
    worthless_cli: list[str],
) -> None:
    """Restore must be a pure byte copy — BOM, CRLF, and ``export`` survive.

    Exotic content is round-tripped through the backup and compared at the
    byte level; any whitespace/encoding normalization would break secrets.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    env_file = repo / ".env"
    exotic = b'\xef\xbb\xbfexport FOO=bar\r\nexport BAZ="qux quux"\r\n'
    env_file.write_bytes(exotic)
    pre_sha = _sha256(exotic)

    child_env = _build_env(tmp_path)
    _run_safe_rewrite_via_python(
        target=env_file,
        new_content=exotic + b"export EXTRA=1\r\n",
        env=child_env,
    )

    env_file.write_bytes(b"CORRUPTED\n")

    proc = subprocess.run(
        [*worthless_cli, "restore", "--force", str(env_file)],
        env=child_env,
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, (
        f"restore failed: exit={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    restored = env_file.read_bytes()
    assert _sha256(restored) == pre_sha, (
        f"byte mismatch: expected {pre_sha} got {_sha256(restored)}"
    )
    assert restored.startswith(b"\xef\xbb\xbf"), "BOM stripped"
    assert b"\r\n" in restored, "CRLF collapsed to LF"
    assert b"export FOO=bar" in restored, "export lines mutated"


def test_restore_after_aborted_write_has_nothing_to_restore(
    tmp_path: Path,
    worthless_cli: list[str],
) -> None:
    """A refused safe_rewrite must leave no ``.bak``; ``--list`` is empty.

    Empty-list is a success condition, not an error — exit 0 with no stdout
    entries proves the atomicity of the backup+rename pairing.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    env_file = repo / ".env"
    env_file.write_bytes(b"API_KEY=baseline\n")

    child_env = _build_env(tmp_path)
    oversized = b"".join(f"K{i}=v{i}\n".encode() for i in range(600))
    _run_safe_rewrite_via_python(
        target=env_file,
        new_content=oversized,
        env=child_env,
        expect_refuse=True,
    )

    proc = subprocess.run(
        [*worthless_cli, "restore", "--list", str(env_file)],
        env=child_env,
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, (
        f"--list should exit 0 on empty set; got {proc.returncode}: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert proc.stdout.strip() == "", f"expected empty listing, got stdout={proc.stdout!r}"
