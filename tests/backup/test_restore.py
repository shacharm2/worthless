"""RED-phase tests for the ``worthless restore`` CLI command (WOR-276 §5).

These are the 9 red tests enumerated in
``docs/planning/wor-276-recovery-final-plan.md`` §5 lines 102-110. They
exercise ``worthless.cli.commands.restore`` which does not yet exist —
every test MUST fail on this commit, and the failure MUST be
attributable to
``ModuleNotFoundError: No module named 'worthless.cli.commands.restore'``
rather than to fixture errors, typos, or collection issues.

Contract pins (locked by plan §3, §4):

* Bucket path
  ``$XDG_DATA_HOME/worthless/backups/<sha256(resolved repo root)>/<basename>
  .<ISO8601_ns>.<pid>.<counter>.bak`` (no ``Z`` suffix).
* ``restore --list`` prints newest-first by ``(timestamp_ns, counter)``.
* ``restore <target>`` calls ``safe_restore()`` (not ``safe_rewrite``);
  the restore path is the ONLY caller that is allowed to skip the delta
  gate — all other gates (symlink / size / TOCTOU / containment /
  path-identity) still fire.
* Divergence-since-backup (target sha differs from the ``.bak`` bytes)
  triggers a TTY confirmation: ``n`` → exit 1, ``y`` → proceed, non-TTY
  without ``--force`` → exit 2.
* ``--force`` skips the prompt only — never the gate.
* Interactive picker accepts a 1-based number or ``q`` (exit 0).
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="backup module is POSIX-only",
)


# ---------------------------------------------------------------------------
# Helpers local to this module (mirror tests/backup/test_backup_writes.py:49
# and the ``_make_fake_bak`` pattern from tests/safe_rewrite/test_chaos.py;
# re-created inline because cross-suite test imports are not permitted).
# ---------------------------------------------------------------------------


def _bucket_for(repo_root: Path) -> str:
    """Expected bucket name = sha256 hex of the resolved repo-root path."""
    return hashlib.sha256(str(repo_root.resolve()).encode("utf-8")).hexdigest()


_BACKUP_NAME_RE = re.compile(
    r"^(?P<base>[^/]+?)"
    r"\.(?P<yy>\d{4})-(?P<mm>\d{2})-(?P<dd>\d{2})"
    r"T(?P<hh>\d{2}):(?P<mi>\d{2}):(?P<ss>\d{2})"
    r"\.(?P<ns>\d{9})"
    r"\.(?P<pid>\d+)"
    r"\.(?P<counter>\d+)"
    r"\.bak$"
)


def _iso_from_ns(ts_ns: int) -> str:
    """Render ``time_ns``-style integer as ``YYYY-MM-DDTHH:MM:SS.<9-digit-ns>``.

    UTC, no ``Z`` suffix (contract pinned in plan §3 and tests/backup/
    test_backup_writes.py lines 195-201).
    """
    import time as _time

    secs, frac_ns = divmod(ts_ns, 1_000_000_000)
    tm = _time.gmtime(secs)
    return (
        f"{tm.tm_year:04d}-{tm.tm_mon:02d}-{tm.tm_mday:02d}"
        f"T{tm.tm_hour:02d}:{tm.tm_min:02d}:{tm.tm_sec:02d}"
        f".{frac_ns:09d}"
    )


def _make_fake_bak(
    bucket: Path,
    basename: str,
    ts_ns: int,
    pid: int,
    counter: int,
    content: bytes,
) -> Path:
    """Create a fake ``.bak`` with the production filename format + 0o600.

    Bucket dir is created at ``0o700`` if missing (matches the contract
    pinned in plan §3). ``basename`` is the target basename WITHOUT
    leading dot trimming — a ``.env`` target produces ``.env.<iso>...``.
    """
    bucket.mkdir(parents=True, exist_ok=True)
    bucket.chmod(0o700)
    name = f"{basename}.{_iso_from_ns(ts_ns)}.{pid}.{counter}.bak"
    path = bucket / name
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content)
    finally:
        os.close(fd)
    path.chmod(0o600)
    assert _BACKUP_NAME_RE.match(name), f"test helper produced malformed name: {name!r}"
    return path


def _import_restore():
    """Import the restore command module or fail the test with a clear red signal.

    We do NOT use ``pytest.importorskip`` — a skipped test is not a red
    test, and the TDD contract requires these to fail loudly on this
    commit. ``ModuleNotFoundError`` propagates and is the correct red
    reason.
    """
    from worthless.cli.commands import restore  # RED: doesn't exist yet

    return restore


def _invoke_app(args: list[str], *, input: str | None = None, env: dict | None = None):
    """Drive ``worthless.cli.app.app`` via Typer's CliRunner.

    Isolated here so each test doesn't repeat the import. Falls through
    to the same ImportError surface if the restore command has not been
    registered on the app yet — the RED contract still holds because
    ``_import_restore()`` is called first in every test.
    """
    from typer.testing import CliRunner

    from worthless.cli.app import app

    runner = CliRunner(mix_stderr=False)
    return runner.invoke(app, args, input=input, env=env)


# ---------------------------------------------------------------------------
# Test 15: restore --list on an empty bucket exits 0 with no stdout output.
# ---------------------------------------------------------------------------


def test_restore_list_empty_exits_zero(tmp_repo, fake_xdg, monkeypatch) -> None:
    """Fresh repo with no backups: ``restore --list`` exits 0, stdout empty.

    First-run stderr notice is permitted (plan §5 test 24), but stdout
    must be empty so downstream piping (``restore --list | head``) stays
    clean when there is nothing to list.
    """
    _import_restore()
    monkeypatch.chdir(tmp_repo)

    result = _invoke_app(["restore", "--list"])

    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    assert result.stdout == "", f"stdout must be empty on empty bucket, got {result.stdout!r}"


# ---------------------------------------------------------------------------
# Test 16: --list prints backups newest-first by (timestamp_ns, counter).
# ---------------------------------------------------------------------------


def test_restore_list_newest_first(tmp_repo, fake_xdg, monkeypatch) -> None:
    """Three backups at increasing ``time_ns``: ``--list`` prints them in
    descending order (newest first) — the filename with the largest
    ``(timestamp_ns, counter)`` tuple appears above the others.
    """
    _import_restore()
    monkeypatch.chdir(tmp_repo)

    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    ts1 = 1_700_000_000_000_000_000
    ts2 = 1_700_000_001_000_000_000
    ts3 = 1_700_000_002_000_000_000
    bak_old = _make_fake_bak(bucket, ".env", ts1, pid=111, counter=1, content=b"v1\n")
    bak_mid = _make_fake_bak(bucket, ".env", ts2, pid=222, counter=2, content=b"v2\n")
    bak_new = _make_fake_bak(bucket, ".env", ts3, pid=333, counter=3, content=b"v3\n")

    result = _invoke_app(["restore", "--list"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"

    out = result.stdout
    pos_new = out.find(bak_new.name)
    pos_mid = out.find(bak_mid.name)
    pos_old = out.find(bak_old.name)
    assert pos_new != -1 and pos_mid != -1 and pos_old != -1, (
        f"all three backup names must appear in output; stdout={out!r}"
    )
    assert pos_new < pos_mid < pos_old, (
        f"newest-first ordering violated: new@{pos_new} mid@{pos_mid} old@{pos_old}; stdout={out!r}"
    )


# ---------------------------------------------------------------------------
# Test 17: restore <target> writes via safe_restore (not safe_rewrite).
# ---------------------------------------------------------------------------


def test_restore_file_writes_via_safe_restore(
    tmp_repo, fake_xdg, make_env_file, monkeypatch
) -> None:
    """The restore write path MUST call ``safe_restore`` (the delta-gate-
    skipping seam) exactly once, with the target path and the backup-
    file bytes. This is the contract that makes a 10-KiB restore over a
    10-byte target legal (plan §4 option b).
    """
    _import_restore()
    monkeypatch.chdir(tmp_repo)

    env = make_env_file(tmp_repo / ".env", b"current\n")
    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    bak_content = b"pre-corruption bytes\n"
    bak = _make_fake_bak(
        bucket, ".env", 1_700_000_000_000_000_000, pid=999, counter=1, content=bak_content
    )

    calls: list[tuple[Path, bytes]] = []

    def spy(target, backup_bytes):
        calls.append((Path(target), bytes(backup_bytes)))
        # Emulate a successful restore so the command can finish.
        Path(target).write_bytes(bytes(backup_bytes))

    monkeypatch.setattr("worthless.cli.safe_rewrite.safe_restore", spy, raising=False)

    # Divergence: target ("current\n") != backup bytes — we pass --force
    # to bypass the prompt without needing a TTY in CliRunner.
    result = _invoke_app(["restore", str(env), "--force"])
    assert result.exit_code == 0, f"stderr={result.stderr!r} stdout={result.stdout!r}"

    assert len(calls) == 1, f"safe_restore must be called exactly once; got {calls!r}"
    called_target, called_bytes = calls[0]
    assert called_target.resolve() == env.resolve(), (
        f"safe_restore target mismatch: {called_target!r} vs {env!r}"
    )
    assert called_bytes == bak_content, (
        f"safe_restore must receive backup-file bytes; got {called_bytes!r}"
    )
    # Sanity: the actual ``.bak`` on disk still matches what spy received.
    assert bak.read_bytes() == bak_content


# ---------------------------------------------------------------------------
# Test 18: divergence-since-backup triggers the TTY prompt.
# Parametrized over the three stdin cases (plan §5 test 18).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stdin_input", "expected_exit", "target_should_change"),
    [
        # TTY "n": user declines, exit 1, target untouched.
        ("n\n", 1, False),
        # TTY "y": user confirms, exit 0, target restored.
        ("y\n", 0, True),
        # Non-TTY (empty stdin) + no --force: exit 2 (refuse without consent).
        ("", 2, False),
    ],
    ids=["tty-n-declines", "tty-y-confirms", "non-tty-no-force-refuses"],
)
def test_restore_prompts_when_target_diverged_since_backup(
    tmp_repo,
    fake_xdg,
    make_env_file,
    monkeypatch,
    stdin_input,
    expected_exit,
    target_should_change,
) -> None:
    """When the target's current sha differs from the ``.bak`` bytes, the
    command must not silently clobber: TTY ``n`` aborts (exit 1), TTY
    ``y`` proceeds (exit 0), and non-TTY invocations without ``--force``
    are refused (exit 2) so scripts can't accidentally overwrite work.
    """
    _import_restore()
    monkeypatch.chdir(tmp_repo)

    current_bytes = b"diverged-after-backup\n"
    backup_bytes = b"original-pre-backup\n"
    env = make_env_file(tmp_repo / ".env", current_bytes)
    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    _make_fake_bak(
        bucket, ".env", 1_700_000_000_000_000_000, pid=42, counter=1, content=backup_bytes
    )

    result = _invoke_app(["restore", str(env)], input=stdin_input)

    assert result.exit_code == expected_exit, (
        f"case={stdin_input!r} expected exit={expected_exit}, "
        f"got {result.exit_code}; stderr={result.stderr!r} stdout={result.stdout!r}"
    )
    final_bytes = env.read_bytes()
    if target_should_change:
        assert final_bytes == backup_bytes, (
            f"target must match backup after confirm; got {final_bytes!r}"
        )
    else:
        assert final_bytes == current_bytes, (
            f"target must be untouched on refusal/decline; got {final_bytes!r}"
        )


# ---------------------------------------------------------------------------
# Test 19: --force bypasses the prompt but NOT the safety gate.
# ---------------------------------------------------------------------------


def test_restore_force_bypasses_prompt_but_not_gate(
    tmp_repo, fake_xdg, make_env_file, monkeypatch
) -> None:
    """``--force`` must skip the divergence prompt on the happy path but
    still surface ``UnsafeReason.SYMLINK`` (and peers) when the gate
    refuses. Two sub-assertions in one test so the RED signal can't be
    gamed by implementing one half.
    """
    restore_mod = _import_restore()  # noqa: F841 — RED import signal
    from worthless.cli.errors import UnsafeReason, UnsafeRewriteRefused

    monkeypatch.chdir(tmp_repo)

    # --- Part A: happy path — --force + diverged target → restored, no prompt.
    env_a = make_env_file(tmp_repo / ".env", b"diverged\n")
    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    backup_bytes = b"original\n"
    _make_fake_bak(
        bucket, ".env", 1_700_000_000_000_000_000, pid=1, counter=1, content=backup_bytes
    )

    result_a = _invoke_app(["restore", str(env_a), "--force"], input="")
    assert result_a.exit_code == 0, (
        f"--force happy path must succeed without a TTY; "
        f"exit={result_a.exit_code} stderr={result_a.stderr!r}"
    )
    assert env_a.read_bytes() == backup_bytes, "target must equal backup after --force restore"

    # --- Part B: gate path — --force + symlinked backup → UnsafeReason.SYMLINK.
    other_repo = tmp_repo.parent / "other-repo"
    other_repo.mkdir()
    (other_repo / ".git").mkdir()
    env_b = make_env_file(other_repo / ".env", b"before\n")
    bucket_b = fake_xdg / "worthless" / "backups" / _bucket_for(other_repo)
    bucket_b.mkdir(parents=True, exist_ok=True)
    bucket_b.chmod(0o700)
    real_payload = other_repo / "real.bak.source"
    real_payload.write_bytes(b"attacker-controlled\n")
    iso = _iso_from_ns(1_700_000_000_000_000_000)
    bak_symlink = bucket_b / f".env.{iso}.2.1.bak"
    os.symlink(str(real_payload), str(bak_symlink))
    bak_symlink.chmod(0o600)

    monkeypatch.chdir(other_repo)
    result_b = _invoke_app(["restore", str(env_b), "--force"], input="")

    assert result_b.exit_code != 0, (
        f"--force must not mask UnsafeReason.SYMLINK; exit={result_b.exit_code} "
        f"stdout={result_b.stdout!r} stderr={result_b.stderr!r}"
    )
    combined = (result_b.stderr or "") + (result_b.stdout or "")
    if result_b.exception is not None and isinstance(result_b.exception, UnsafeRewriteRefused):
        assert result_b.exception.reason == UnsafeReason.SYMLINK
    else:
        assert UnsafeReason.SYMLINK.value in combined.lower(), (
            f"symlink refusal not surfaced to user; combined={combined!r}"
        )
    assert env_b.read_bytes() == b"before\n", "target must be untouched on gate refusal"


# ---------------------------------------------------------------------------
# Test 20: restore --list --all-repos lists every bucket, grouped.
# ---------------------------------------------------------------------------


def test_restore_all_repos_lists_every_bucket_grouped(tmp_repo, fake_xdg, monkeypatch) -> None:
    """With two distinct repo buckets present, ``--list --all-repos`` must
    enumerate backups from both, grouped under per-bucket headers that
    include a hint tying each group to its resolved repo root (so the
    user can tell which repo a given backup belongs to).
    """
    _import_restore()
    monkeypatch.chdir(tmp_repo)

    # Repo A — the cwd.
    bucket_a = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    bak_a = _make_fake_bak(
        bucket_a, ".env", 1_700_000_000_000_000_000, pid=1, counter=1, content=b"a\n"
    )

    # Repo B — sibling directory.
    repo_b = tmp_repo.parent / "repo-b"
    repo_b.mkdir()
    (repo_b / ".git").mkdir()
    bucket_b = fake_xdg / "worthless" / "backups" / _bucket_for(repo_b)
    bak_b = _make_fake_bak(
        bucket_b, ".env", 1_700_000_001_000_000_000, pid=2, counter=1, content=b"b\n"
    )

    result = _invoke_app(["restore", "--list", "--all-repos"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"

    out = result.stdout
    assert bak_a.name in out, f"bucket A backup missing from output: {out!r}"
    assert bak_b.name in out, f"bucket B backup missing from output: {out!r}"

    # Group headers must name each resolved repo root (or its bucket hash)
    # so the user can disambiguate. We accept either form: the resolved
    # repo-root path string OR the bucket-hash hex string.
    repo_a_hint = str(tmp_repo.resolve())
    repo_b_hint = str(repo_b.resolve())
    bucket_a_hash = _bucket_for(tmp_repo)
    bucket_b_hash = _bucket_for(repo_b)
    assert (repo_a_hint in out) or (bucket_a_hash in out), (
        f"no group header for repo A in output: {out!r}"
    )
    assert (repo_b_hint in out) or (bucket_b_hash in out), (
        f"no group header for repo B in output: {out!r}"
    )


# ---------------------------------------------------------------------------
# Test 21: interactive picker accepts a 1-based number or 'q' to quit.
# ---------------------------------------------------------------------------


def test_restore_interactive_picker_accepts_number_and_q(
    tmp_repo, fake_xdg, make_env_file, monkeypatch
) -> None:
    """With three backups present and no target argument, ``restore``
    shows a picker. Entering ``1`` restores the first listed backup
    (newest); entering ``q`` exits 0 without any write.
    """
    _import_restore()
    monkeypatch.chdir(tmp_repo)

    env = make_env_file(tmp_repo / ".env", b"current\n")
    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    # Three backups, ascending ts. Newest (last) is listed first.
    _make_fake_bak(bucket, ".env", 1_700_000_000_000_000_000, 1, 1, content=b"oldest\n")
    _make_fake_bak(bucket, ".env", 1_700_000_001_000_000_000, 2, 1, content=b"middle\n")
    _make_fake_bak(bucket, ".env", 1_700_000_002_000_000_000, 3, 1, content=b"newest\n")

    # --- Sub-case A: pick "1" → restores backup #1 in the listing (newest).
    result_pick = _invoke_app(["restore", str(env), "--force"], input="1\n")
    assert result_pick.exit_code == 0, (
        f"picker accepting '1' must exit 0; stderr={result_pick.stderr!r}"
    )
    assert env.read_bytes() == b"newest\n", (
        f"picking '1' must restore newest; got {env.read_bytes()!r}"
    )

    # --- Sub-case B: fresh run with stdin 'q' → exit 0, no further write.
    env.write_bytes(b"post-pick-state\n")
    result_quit = _invoke_app(["restore", str(env), "--force"], input="q\n")
    assert result_quit.exit_code == 0, f"picker 'q' must exit 0; stderr={result_quit.stderr!r}"
    assert env.read_bytes() == b"post-pick-state\n", (
        f"'q' must not write; target={env.read_bytes()!r}"
    )


# ---------------------------------------------------------------------------
# Test 22: restore refuses when the .bak is a symlink (UnsafeReason.SYMLINK).
# ---------------------------------------------------------------------------


def test_restore_refuses_symlinked_backup_file(
    tmp_repo, fake_xdg, make_env_file, monkeypatch
) -> None:
    """A ``.bak`` that is a symlink must be refused with
    ``UnsafeReason.SYMLINK`` — never followed — and the target must be
    unchanged. Locks the bucket against a symlink-swap attack.
    """
    _import_restore()
    from worthless.cli.errors import UnsafeReason, UnsafeRewriteRefused

    monkeypatch.chdir(tmp_repo)

    pre = b"target-before\n"
    env = make_env_file(tmp_repo / ".env", pre)

    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    bucket.mkdir(parents=True, exist_ok=True)
    bucket.chmod(0o700)

    # The symlink target lives outside the bucket — following it would
    # read attacker-controlled bytes.
    decoy = tmp_repo / "attacker.txt"
    decoy.write_bytes(b"attacker-controlled\n")

    iso = _iso_from_ns(1_700_000_000_000_000_000)
    bak_symlink = bucket / f".env.{iso}.7.1.bak"
    os.symlink(str(decoy), str(bak_symlink))

    result = _invoke_app(["restore", str(env), "--force"], input="")

    assert result.exit_code != 0, (
        f"symlinked .bak must be refused; exit={result.exit_code} stdout={result.stdout!r}"
    )
    if result.exception is not None and isinstance(result.exception, UnsafeRewriteRefused):
        assert result.exception.reason == UnsafeReason.SYMLINK, (
            f"refusal reason must be SYMLINK; got {result.exception.reason!r}"
        )
    else:
        combined = (result.stderr or "") + (result.stdout or "")
        assert UnsafeReason.SYMLINK.value in combined.lower(), (
            f"SYMLINK refusal reason not surfaced; combined={combined!r}"
        )
    assert env.read_bytes() == pre, "target must be untouched when .bak is a symlink"


# ---------------------------------------------------------------------------
# Test 23: nonexistent target exits non-zero with a user-facing stderr error.
# ---------------------------------------------------------------------------


def test_restore_nonexistent_target_exits_nonzero(tmp_repo, fake_xdg, monkeypatch) -> None:
    """``worthless restore /does/not/exist`` must exit non-zero and emit
    a user-facing error on stderr — never silently no-op.
    """
    _import_restore()
    monkeypatch.chdir(tmp_repo)

    missing = tmp_repo / "nope" / "definitely-not-here.env"
    assert not missing.exists()

    result = _invoke_app(["restore", str(missing)], input="")

    assert result.exit_code != 0, (
        f"nonexistent target must exit non-zero; exit={result.exit_code} stdout={result.stdout!r}"
    )
    assert result.stderr, "a user-facing error must be emitted on stderr"
