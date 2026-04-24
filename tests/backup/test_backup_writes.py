"""Backup-write invariants for WOR-276 (recovery works after a bad lock).

These are the 14 red tests enumerated in
``docs/planning/wor-276-recovery-final-plan.md`` §5. They exercise the
``worthless.cli.backup`` module which does not yet exist — every test
MUST fail on this run, and the failure MUST be attributable to
``ModuleNotFoundError: No module named 'worthless.cli.backup'`` or
``AttributeError: UnsafeReason has no attribute 'BACKUP'`` rather than
to fixture errors, typos, or collection issues.

Contract pins (locked by plan §3):

* Bucket path ``$XDG_DATA_HOME/worthless/backups/<sha256(resolved repo
  root)>/<basename>.<ISO8601_ns>.<pid>.<counter>.bak``.
* ``$XDG_DATA_HOME`` defaults to ``~/.local/share`` when unset OR empty.
* Bucket dir mode ``0o700``; backup file mode ``0o600``.
* Rotation keeps the last 50 per target, ordered by
  ``(timestamp_ns, counter)`` tuple (not filename-lex).
* ``UnsafeReason.BACKUP`` is the 14th enum member with value ``"backup"``.
"""

from __future__ import annotations

import calendar
import errno
import hashlib
import logging
import os
import re
import stat
import sys
import time

import pytest

from tests.backup.conftest import _BACKUP_NAME_RE, _bucket_for


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="backup module is POSIX-only",
)


# ---------------------------------------------------------------------------
# Helpers local to this module
# ---------------------------------------------------------------------------


def _import_backup():
    """Import the backup module or fail the test with a clear red signal.

    We do NOT use ``pytest.importorskip`` — a skipped test is not a red
    test, and the TDD contract requires these to fail loudly on this
    commit. Any ``ModuleNotFoundError`` propagates and is the correct
    red reason.
    """
    from worthless.cli import backup

    return backup


def _require_unsafe_reason_backup():
    """Return ``UnsafeReason.BACKUP`` or fail with a clear AttributeError."""
    from worthless.cli.errors import UnsafeReason

    # Deliberate attribute access — test must fail RED with AttributeError
    # on this commit because the enum member doesn't exist yet.
    return UnsafeReason.BACKUP  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Filename-derived sort key: parse ``<basename>.<ISO8601_ns>.<pid>.<counter>.bak``
# into ``(timestamp_ns: int, counter: int)``. The plan mandates this (over
# ``st_mtime_ns``) because backup files are written with ``shutil.copystat``,
# so their mtime reflects the *original file's* mtime, not backup creation
# time. Sorting by filename components is deterministic and monotonic.
#
# ``_BACKUP_NAME_RE`` lives in ``tests/backup/conftest.py`` so the whole suite
# shares one canonical copy.
# ---------------------------------------------------------------------------


def _parse_ts_counter_from_name(name: str) -> tuple[int, int]:
    """Return ``(timestamp_ns, counter)`` parsed from the backup filename.

    ``timestamp_ns`` is the UTC epoch-nanosecond integer derived from the
    ISO8601 component; ``counter`` is the per-process monotonic counter
    dot-field. Used as the "oldest" sort key for rotation assertions.

    Raises ``ValueError`` on malformed names so a silent broken sort is
    impossible.
    """
    m = _BACKUP_NAME_RE.match(name)
    if m is None:
        raise ValueError(f"unparsable backup filename: {name!r}")
    epoch_s = calendar.timegm(
        (
            int(m.group("yy")),
            int(m.group("mm")),
            int(m.group("dd")),
            int(m.group("hh")),
            int(m.group("mi")),
            int(m.group("ss")),
            0,
            0,
            0,
        )
    )
    ts_ns = epoch_s * 1_000_000_000 + int(m.group("ns"))
    return (ts_ns, int(m.group("counter")))


# ---------------------------------------------------------------------------
# Test 1: backup written on a successful rewrite — content = pre-bytes.
# ---------------------------------------------------------------------------


def test_backup_written_on_successful_rewrite(tmp_repo, fake_xdg, make_env_file, sha256_of) -> None:
    """After a successful ``safe_rewrite``, the bucket contains a ``.bak``
    file whose bytes are the pre-write contents of the target.
    """
    from worthless.cli.safe_rewrite import safe_rewrite

    backup = _import_backup()  # noqa: F841 — module import is the RED signal

    pre = b"OPENAI_API_KEY=sk-orig\n"
    env = make_env_file(tmp_repo / ".env", pre)
    pre_sha = hashlib.sha256(pre).hexdigest()

    safe_rewrite(env, b"OPENAI_API_KEY=sk-new\n", original_user_arg=env)

    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    baks = sorted(bucket.glob(".env.*.bak"))
    assert len(baks) == 1, f"expected exactly one .bak, got {baks!r}"
    assert sha256_of(baks[0]) == pre_sha, "backup does not contain pre-write bytes"


# ---------------------------------------------------------------------------
# Test 2: backup bucket path is sha256 of resolved repo root.
# ---------------------------------------------------------------------------


def test_backup_path_is_sha256_of_resolved_repo_root(tmp_repo, fake_xdg, make_env_file) -> None:
    """The parent-of-parent of the backup file must equal
    ``$XDG_DATA_HOME/worthless/backups/<sha256(resolved repo root)>``.
    """
    from worthless.cli.safe_rewrite import safe_rewrite

    _import_backup()

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    expected_bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    assert expected_bucket.is_dir(), f"bucket dir missing: {expected_bucket}"
    baks = list(expected_bucket.glob(".env.*.bak"))
    assert len(baks) == 1
    assert baks[0].parent == expected_bucket


# ---------------------------------------------------------------------------
# Test 3: filename has all four components — ISO8601_ns, pid, counter, .bak.
# ---------------------------------------------------------------------------


def test_backup_filename_format_all_four_components(tmp_repo, fake_xdg, make_env_file) -> None:
    """Filename matches
    ``<basename>.\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}.\\d{9}.\\d+.\\d+.bak``.
    """
    from worthless.cli.safe_rewrite import safe_rewrite

    _import_backup()

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    baks = list(bucket.glob(".env.*.bak"))
    assert len(baks) == 1
    name = baks[0].name

    pattern = (
        r"^\.env"
        r"\.\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{9}"  # ISO8601 with ns fraction
        r"\.\d+"  # pid
        r"\.\d+"  # counter
        r"\.bak$"
    )
    assert re.match(pattern, name), f"filename {name!r} does not match {pattern!r}"


# ---------------------------------------------------------------------------
# Test 4: backup file mode is 0o600.
# ---------------------------------------------------------------------------


def test_backup_file_mode_is_0600(tmp_repo, fake_xdg, make_env_file) -> None:
    """``os.stat(.bak).st_mode & 0o777 == 0o600``."""
    from worthless.cli.safe_rewrite import safe_rewrite

    _import_backup()

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    baks = list(bucket.glob(".env.*.bak"))
    assert len(baks) == 1
    mode = stat.S_IMODE(baks[0].stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# Test 5: bucket dir mode is 0o700.
# ---------------------------------------------------------------------------


def test_bucket_dir_mode_is_0700(tmp_repo, fake_xdg, make_env_file) -> None:
    """``os.stat(bucket_dir).st_mode & 0o777 == 0o700``."""
    from worthless.cli.safe_rewrite import safe_rewrite

    _import_backup()

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    mode = stat.S_IMODE(bucket.stat().st_mode)
    assert mode == 0o700, f"expected 0o700, got {oct(mode)}"


# ---------------------------------------------------------------------------
# Test 6: refuse if bucket dir pre-exists with weaker mode.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("weaker_mode", [0o755, 0o750, 0o770, 0o777])
def test_backup_refuses_if_bucket_dir_has_weaker_mode(
    tmp_repo, fake_xdg, make_env_file, weaker_mode
) -> None:
    """If the bucket dir already exists with ANY mode != 0o700, raise
    ``UnsafeRewriteRefused(UnsafeReason.BACKUP)`` and leave target untouched.

    The contract is strict: only 0o700 is accepted. Parametrized across a
    representative set of weaker/looser modes (group-readable, group-writable,
    world-readable, world-writable) to lock down that ANY mode != 0o700 is
    refused — not just the canonical 0o755.
    """
    from worthless.cli.errors import UnsafeRewriteRefused
    from worthless.cli.safe_rewrite import safe_rewrite

    _import_backup()
    backup_reason = _require_unsafe_reason_backup()

    # Pre-create the bucket with a non-0o700 mode (attack surface).
    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    bucket.mkdir(parents=True, mode=weaker_mode)
    bucket.chmod(weaker_mode)

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    assert exc_info.value.reason == backup_reason
    # Target must be untouched.
    assert env.read_bytes() == b"KEY=v\n"


# ---------------------------------------------------------------------------
# Test 7: backup-write failure aborts the rewrite.
# ---------------------------------------------------------------------------


def test_backup_write_failure_aborts_rewrite(
    tmp_repo, fake_xdg, make_env_file, sha256_of, monkeypatch
) -> None:
    """If ``write_backup`` raises ``OSError(ENOSPC)``, the target must not
    be touched (baseline sha unchanged) and the refusal reason must be
    ``UnsafeReason.BACKUP``.
    """
    from worthless.cli.errors import UnsafeRewriteRefused
    from worthless.cli.safe_rewrite import safe_rewrite

    backup = _import_backup()
    backup_reason = _require_unsafe_reason_backup()

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    baseline = sha256_of(env)

    def _boom(*a, **kw):  # noqa: ANN002, ANN003
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr(backup, "write_backup", _boom)

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    assert exc_info.value.reason == backup_reason
    assert sha256_of(env) == baseline, "target clobbered despite backup failure"


# ---------------------------------------------------------------------------
# Test 8: fsync is invoked on the backup tmp fd BEFORE rename.
# ---------------------------------------------------------------------------


def test_backup_file_fsync_before_rename(
    tmp_repo, fake_xdg, make_env_file, monkeypatch, fd_to_path
) -> None:
    """``os.fsync`` (or ``os.fdatasync`` where available) is called on the
    backup-tmp fd before it is renamed into its final ``.bak`` name. We spy
    by recording fsync/fdatasync fd targets and rename source paths in call
    order; the first fsync of a path inside the bucket must precede the
    first rename of a path inside the bucket. Both ``os.fsync`` and
    ``os.fdatasync`` are accepted (the latter is Linux-only — macOS lacks
    ``os.fdatasync`` and the monkeypatch is skipped there).
    """
    _import_backup()
    from worthless.cli.safe_rewrite import safe_rewrite

    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)

    events: list[tuple[str, str]] = []
    real_fsync = os.fsync
    real_fdatasync = getattr(os, "fdatasync", None)
    real_rename = os.rename
    real_replace = os.replace

    def _rec_fsync(fd):  # noqa: ANN001
        # Resolve via the cross-platform ``fd_to_path`` fixture — fails
        # loudly on platforms where resolution is unsupported, so tests
        # 8/9 cannot silently degrade to always-passing tautologies.
        events.append(("fsync", fd_to_path(fd)))
        return real_fsync(fd)

    def _rec_fdatasync(fd):  # noqa: ANN001
        # Record under the same "fsync" event kind — the ordering-invariant
        # check treats fsync and fdatasync equivalently for regular files.
        events.append(("fsync", fd_to_path(fd)))
        return real_fdatasync(fd)

    def _rec_rename(src, dst, *a, **kw):  # noqa: ANN001, ANN003
        events.append(("rename", str(src)))
        return real_rename(src, dst, *a, **kw)

    def _rec_replace(src, dst, *a, **kw):  # noqa: ANN001, ANN003
        events.append(("replace", str(src)))
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "fsync", _rec_fsync)
    if real_fdatasync is not None:
        monkeypatch.setattr(os, "fdatasync", _rec_fdatasync)
    monkeypatch.setattr(os, "rename", _rec_rename)
    monkeypatch.setattr(os, "replace", _rec_replace)

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    # Find first bucket-touching event of each kind.
    bucket_prefix = str(bucket)
    first_fsync_idx = next(
        (i for i, (kind, path) in enumerate(events) if kind == "fsync" and bucket_prefix in path),
        None,
    )
    first_rename_idx = next(
        (
            i
            for i, (kind, path) in enumerate(events)
            if kind in ("rename", "replace") and bucket_prefix in path
        ),
        None,
    )
    assert first_fsync_idx is not None, f"no fsync on a bucket fd observed: {events}"
    assert first_rename_idx is not None, f"no rename into bucket observed: {events}"
    assert first_fsync_idx < first_rename_idx, f"fsync-before-rename violated: events={events}"


# ---------------------------------------------------------------------------
# Test 9: fsync is invoked on the bucket dir fd BEFORE the function returns.
# ---------------------------------------------------------------------------


def test_backup_dir_fsync_before_return(
    tmp_repo, fake_xdg, make_env_file, monkeypatch, fd_to_path
) -> None:
    """After backup rename, the bucket directory fd must be ``fsync``'d
    before ``write_backup`` (or the outer ``safe_rewrite``) returns —
    otherwise the backup rename is not durable.
    """
    _import_backup()
    from worthless.cli.safe_rewrite import safe_rewrite

    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)

    fsync_paths: list[str] = []
    real_fsync = os.fsync

    def _rec(fd):  # noqa: ANN001
        fsync_paths.append(fd_to_path(fd))
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _rec)

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    # At least one fsync must have targeted the bucket directory itself.
    bucket_str = str(bucket)
    assert any(p == bucket_str or p.rstrip("/") == bucket_str for p in fsync_paths), (
        f"bucket dir fd was never fsync'd: {fsync_paths}"
    )


# ---------------------------------------------------------------------------
# Test 10: backup uses atomic tmp+rename — no .bak.tmp-* residue on success.
# ---------------------------------------------------------------------------


def test_backup_atomic_via_tmp_rename(tmp_repo, fake_xdg, make_env_file) -> None:
    """Bucket must contain no ``*.bak.tmp-*`` file after a successful
    rewrite — the backup is written as a tmp file then atomically
    renamed into place.
    """
    _import_backup()
    from worthless.cli.safe_rewrite import safe_rewrite

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    residue = list(bucket.glob("*.bak.tmp-*"))
    assert residue == [], f"leftover backup-tmp residue: {residue}"


# ---------------------------------------------------------------------------
# Test 11: rotation keeps the newest 50 per target.
# ---------------------------------------------------------------------------


def test_backup_rotation_keeps_last_50(tmp_repo, fake_xdg, make_env_file) -> None:
    """After 55 sequential rewrites of the same target, the bucket
    contains exactly 50 ``.bak`` files, and the 50 kept are the newest
    by ``(timestamp_ns, counter)`` tuple parsed from the **filename**
    — not ``st_mtime_ns`` (which is copied from the source file via
    ``shutil.copystat`` and so cannot be trusted to reflect backup
    creation order) and not filename lex order either.
    """
    _import_backup()
    from worthless.cli.safe_rewrite import safe_rewrite

    env = make_env_file(tmp_repo / ".env", b"KEY=v0\n")

    # Record every backup filename we ever observe in the bucket across
    # all 55 rewrites. We key by name (dedupes repeat observations) and
    # derive the sort key purely from the filename itself — no stat().
    seen_names: set[str] = set()

    for i in range(1, 56):
        safe_rewrite(env, f"KEY=v{i}\n".encode(), original_user_arg=env)
        bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
        for p in bucket.glob(".env.*.bak"):
            seen_names.add(p.name)

    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    final = sorted(bucket.glob(".env.*.bak"))
    assert len(final) == 50, f"expected 50 backups after rotation, got {len(final)}"

    # Compute the newest-50 set by the filename-derived (ts_ns, counter)
    # key. Any unparsable name is a bug in the production code's naming
    # contract and surfaces here as ValueError, not a silent pass.
    newest_50_names = {
        name
        for name, _ in sorted(
            ((n, _parse_ts_counter_from_name(n)) for n in seen_names),
            key=lambda kv: kv[1],
            reverse=True,
        )[:50]
    }
    assert {p.name for p in final} == newest_50_names, (
        "rotation did not retain the newest 50 by filename-derived "
        "(timestamp_ns, counter); got "
        f"{sorted(p.name for p in final)!r}"
    )


# ---------------------------------------------------------------------------
# Test 12: rotation failure does not abort the write — warn + continue.
# ---------------------------------------------------------------------------


def test_backup_rotation_failure_does_not_abort_write(
    tmp_repo, fake_xdg, make_env_file, monkeypatch, caplog
) -> None:
    """If the rotation ``os.unlink`` of an old backup raises
    ``PermissionError``, the current rewrite still succeeds (new target
    content written, a new ``.bak`` is created) and a WARNING-level log
    record is emitted on the dedicated ``worthless.cli.backup`` logger.

    The assertion walks ``caplog.records`` directly and matches on the
    structural ``(levelno, name)`` pair rather than substring-grepping
    the message text — that keeps the test robust to wording changes
    and prevents unrelated modules whose messages happen to contain
    "backup"/"rotate" from satisfying it.
    """
    _import_backup()
    from worthless.cli.safe_rewrite import safe_rewrite

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")

    # Seed 50 existing backups so the 51st triggers rotation.
    for i in range(50):
        safe_rewrite(env, f"KEY=v{i}\n".encode(), original_user_arg=env)

    real_unlink = os.unlink
    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)

    def _denied(path, *a, **kw):  # noqa: ANN001, ANN003
        if str(path).startswith(str(bucket)) and str(path).endswith(".bak"):
            raise PermissionError(errno.EACCES, "denied")
        return real_unlink(path, *a, **kw)

    monkeypatch.setattr(os, "unlink", _denied)

    # Force capture on the dedicated logger regardless of propagation
    # config — the contract (plan §5) pins the logger name.
    caplog.set_level(logging.WARNING, logger="worthless.cli.backup")
    pre_existing = {p.name for p in bucket.glob(".env.*.bak")}
    safe_rewrite(env, b"KEY=final\n", original_user_arg=env)

    # (1) Write still succeeded: target content matches.
    assert env.read_bytes() == b"KEY=final\n", "rewrite was aborted despite rotation failure"

    # (2) A NEW backup was produced (not just the 50 seeds).
    post = {p.name for p in bucket.glob(".env.*.bak")}
    assert post - pre_existing, "no new .bak was produced after the rotation-failing rewrite"

    # (3) Structural log assertion: at least one LogRecord at WARNING
    # level on the ``worthless.cli.backup`` logger. This is the primary
    # gate — no substring matching on message text.
    warnings_on_backup_logger = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and rec.name == "worthless.cli.backup"
    ]
    assert warnings_on_backup_logger, (
        "expected >=1 WARNING record on logger 'worthless.cli.backup'; "
        f"got records={[(r.name, r.levelname, r.getMessage()) for r in caplog.records]!r}"
    )


# ---------------------------------------------------------------------------
# Test 13: $XDG_DATA_HOME is honoured when set to an explicit path.
# ---------------------------------------------------------------------------


def test_xdg_data_home_honoured_when_set(tmp_repo, tmp_path, make_env_file, monkeypatch) -> None:
    """When ``$XDG_DATA_HOME=/some/dir``, the backup lands under
    ``/some/dir/worthless/backups/...``.
    """
    _import_backup()
    from worthless.cli.safe_rewrite import safe_rewrite

    xdg = tmp_path / "explicit-xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(tmp_path / "home-should-not-be-used"))

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    bucket = xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    assert bucket.is_dir(), f"backup bucket not under explicit XDG_DATA_HOME: {bucket}"
    assert list(bucket.glob(".env.*.bak")), "no .bak file under explicit XDG_DATA_HOME"


# ---------------------------------------------------------------------------
# Test 14: empty or unset $XDG_DATA_HOME both fall back to ~/.local/share.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["unset", "empty"])
def test_xdg_data_home_empty_falls_back_to_local_share(
    tmp_repo, tmp_path, make_env_file, monkeypatch, mode
) -> None:
    """Per XDG spec, XDG_DATA_HOME defaults to ``~/.local/share`` when
    unset *or* empty. Both cases must land the backup under
    ``$HOME/.local/share/worthless/backups/<bucket>/``.
    """
    _import_backup()
    from worthless.cli.safe_rewrite import safe_rewrite

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    if mode == "unset":
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_DATA_HOME", "")

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    expected_bucket = (
        fake_home / ".local" / "share" / "worthless" / "backups" / _bucket_for(tmp_repo)
    )
    assert expected_bucket.is_dir(), f"fallback bucket missing (mode={mode}): {expected_bucket}"
    assert list(expected_bucket.glob(".env.*.bak")), f"no .bak under fallback bucket (mode={mode})"


# ---------------------------------------------------------------------------
# Test 15: rotation survives backward clock jumps (ntpd step / DST).
#
# Covers plan §8 risk row "Clock skew under ntpd step / DST" (finding M5
# from qa-expert). The product sorts retained backups by
# ``(timestamp_ns, counter)`` parsed from the filename — we must prove
# that:
#
#   (a) same-ns collisions produce DISTINCT filenames (the per-process
#       monotonic ``counter`` field breaks ties — without it, the second
#       write would collide on the rename target path),
#   (b) when the wall-clock jumps BACKWARD, the counter for a later
#       write at the same ``ts_ns`` is strictly greater than the counter
#       for the earlier write at that same ``ts_ns`` (so (ts_ns, counter)
#       tuples can be compared and remain distinct within a single ns
#       bucket), and
#   (c) rotation past 50 keeps exactly 50, and the evicted file is the
#       one with the smallest ``(ts_ns, counter)`` tuple — NOT the
#       earliest wall-clock write. Under backward skew these diverge.
#
# Deliberately uses a non-monotonic pinned sequence containing a
# duplicate (500_000_000 appears twice) so the tie-breaker is exercised,
# and at least one backward step (2_000_000_000 -> 500_000_000) so
# wall-clock chronology and sort order diverge.
# ---------------------------------------------------------------------------


def test_backup_rotation_survives_backward_clock_jumps(
    tmp_repo, fake_xdg, make_env_file, monkeypatch
) -> None:
    _import_backup()
    from worthless.cli.safe_rewrite import safe_rewrite

    # Non-monotonic sequence: values chosen so that (i) 500_000_000
    # repeats to force a same-ns collision and (ii) position 3 -> 4
    # jumps BACKWARD (2_000_000_000 -> 500_000_000). Write #i returns
    # pinned_seq[i-1] from ``time.time_ns()``.
    pinned_seq = [
        1_000_000_000,
        500_000_000,
        2_000_000_000,
        500_000_000,
        1_500_000_000,
    ]

    # Per-write constant: ``time.time_ns()`` returns the SAME value
    # every time it is called during a given ``safe_rewrite`` call,
    # no matter how many times prod invokes it (filename stamp, log
    # line, etc.). The test updates ``current_ns["v"]`` between writes.
    # This replaces a prior iterator-based design that exhausted and
    # broke the write-N -> pinned_seq[N-1] pairing if prod called
    # ``time.time_ns()`` more than once per rewrite.
    current_ns: dict[str, int] = {"v": 0}
    monkeypatch.setattr(time, "time_ns", lambda: current_ns["v"])

    env = make_env_file(tmp_repo / ".env", b"KEY=v0\n")
    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)

    # --- Phase 1: 5 writes covering the non-monotonic / collision region.
    # Capture every bucket filename observed after each write, in the
    # order produced. Write #i corresponds to pinned_seq[i-1].
    names_per_write: list[set[str]] = []
    seen_all: set[str] = set()
    for i in range(1, 6):
        current_ns["v"] = pinned_seq[i - 1]
        safe_rewrite(env, f"KEY=v{i}\n".encode(), original_user_arg=env)
        current = {p.name for p in bucket.glob(".env.*.bak")}
        new_names = current - seen_all
        names_per_write.append(new_names)
        seen_all |= current

    # (a) Distinctness: each of the 5 writes produced exactly one new
    # filename (no collision on the final ``.bak`` path).
    assert all(len(s) == 1 for s in names_per_write), (
        "expected each of the 5 writes to produce exactly one new .bak; "
        f"got per-write-new-names={names_per_write!r}"
    )
    write_name = [next(iter(s)) for s in names_per_write]  # write_name[i] for write #(i+1)
    assert len(set(write_name)) == 5, f"5 writes did not yield 5 unique filenames: {write_name!r}"

    # Parse each filename's (ts_ns, counter) tuple.
    parsed = [_parse_ts_counter_from_name(n) for n in write_name]

    # Sanity: each write's ts_ns matches the pinned value. If the
    # production code sources ts_ns from anywhere other than
    # ``time.time_ns()`` this will fail loudly (correct red signal).
    for idx, (ts_ns, _counter) in enumerate(parsed):
        assert ts_ns == pinned_seq[idx], (
            f"write #{idx + 1}: ts_ns in filename ({ts_ns}) != pinned "
            f"time.time_ns() ({pinned_seq[idx]}); parsed={parsed!r}"
        )

    # (b) Same-ns tie-breaker: writes #2 and #4 both pinned to
    # 500_000_000. Their counters must differ, AND the LATER write's
    # counter must be strictly greater (that is the per-process
    # monotonic property that makes (ts_ns, counter) a total order).
    _, counter_w2 = parsed[1]
    _, counter_w4 = parsed[3]
    assert counter_w2 != counter_w4, (
        f"same-ns writes #2 and #4 collided on counter={counter_w2}; parsed={parsed!r}"
    )
    assert counter_w4 > counter_w2, (
        "per-process counter must be strictly monotonic across writes; "
        f"write #2 counter={counter_w2}, write #4 counter={counter_w4}, "
        f"parsed={parsed!r}"
    )

    # --- Phase 2: drive 46 more writes to reach 51 total, triggering
    # rotation. After the 51st write the bucket must hold exactly 50
    # files, and the one evicted must be the smallest by
    # (ts_ns, counter) — NOT the earliest wall-clock write. Every
    # ts_ns here is strictly greater than max(pinned_seq), so the
    # minimum-tuple across all 51 writes must fall inside phase 1.
    for i in range(6, 52):
        current_ns["v"] = 10_000_000_000 + (i - 6)
        safe_rewrite(env, f"KEY=v{i}\n".encode(), original_user_arg=env)
        for p in bucket.glob(".env.*.bak"):
            seen_all.add(p.name)

    final = list(bucket.glob(".env.*.bak"))
    assert len(final) == 50, f"expected exactly 50 backups after rotation past 50, got {len(final)}"

    # The oldest-by-tuple across every name ever observed is the one
    # whose (ts_ns, counter) is minimal. Because the extra writes all
    # used ts_ns >= 10_000_000_000, the minimum MUST lie within the
    # first 5 pinned writes — and specifically among writes #2 and #4
    # (the two 500_000_000 entries). Of those, the smaller counter wins.
    all_parsed = {n: _parse_ts_counter_from_name(n) for n in seen_all}
    oldest_name = min(all_parsed, key=lambda n: all_parsed[n])
    oldest_tuple = all_parsed[oldest_name]
    assert oldest_tuple[0] == 500_000_000, (
        "oldest-by-tuple should land on one of the two 500_000_000-ns "
        f"writes; got oldest_tuple={oldest_tuple!r}, parsed={all_parsed!r}"
    )
    # It must NOT be the earliest wall-clock write (that was write #1
    # with ts_ns=1_000_000_000 — a strictly larger ts_ns). This is the
    # load-bearing assertion: rotation follows filename tuple order,
    # not wall-clock chronology.
    first_wall_clock_name = write_name[0]
    assert oldest_name != first_wall_clock_name, (
        "rotation oldest-by-tuple collapsed to earliest wall-clock write "
        "— backward-clock skew invariant violated; "
        f"oldest={oldest_name!r}, first_wall_clock={first_wall_clock_name!r}"
    )

    # Expected survivor set = every observed name minus the single
    # minimum-tuple name. (Only one eviction happens: 51 -> 50.)
    expected_survivors = set(all_parsed) - {oldest_name}
    actual_survivors = {p.name for p in final}
    assert actual_survivors == expected_survivors, (
        "rotation survivor set != (all observed) - (min-tuple); "
        f"missing={expected_survivors - actual_survivors!r}, "
        f"unexpected={actual_survivors - expected_survivors!r}"
    )


# ---------------------------------------------------------------------------
# Test 16: bucket key follows symlinks but NOT bind-mounts / distinct roots.
#
# Covers plan §8 risks-table row "Bind-mount collision" (qa-expert finding
# M6). The contract pins the bucket key to
# ``sha256(str(repo_root.resolve()))``. ``Path.resolve()`` follows
# symlinks — so two symlinked views of the same real directory MUST hash
# to the same bucket — but it does NOT peek through the mount table —
# so two DISTINCT real directories (which is the closest portable
# simulation of a bind-mount we can stage in CI without root) MUST hash
# to different buckets.
#
# Guards against a future refactor that stops following symlinks
# (Invariant A would go red) or that collapses two distinct resolved
# repo roots into one bucket (Invariants B + C would go red). It does
# NOT rule out a bind-mount-based collision — plan §8 accepts that as
# documented behavior and CI cannot portably stage ``mount --bind``.
#
# RED signal: ``_import_backup()`` raises ``ImportError`` (specifically
# "cannot import name 'backup'") before any of the symlink / multi-repo
# staging matters.
# ---------------------------------------------------------------------------


def test_bucket_key_uses_resolved_path_not_inode(tmp_path, fake_xdg, make_env_file) -> None:
    """Bucket key == ``sha256(str(repo_root.resolve()))``:

    * Invariant A: a symlinked view of a real repo resolves THROUGH the
      symlink, so ``real_repo`` and ``symlinked_repo`` land in the SAME
      bucket.
    * Invariant B: two DISTINCT real repo roots (portable stand-in for a
      bind-mount — CI can't ``mount --bind`` without root) land in
      DISTINCT buckets.
    * Invariant C: no bucket-key collision between distinct resolved
      repo roots — the ``other_real_repo`` bucket name differs from the
      ``real_repo`` bucket name, and both bucket dirs coexist on disk
      under ``$XDG_DATA_HOME/worthless/backups/``.
    """
    from worthless.cli.safe_rewrite import safe_rewrite

    _import_backup()

    # Build three repo roots as siblings under tmp_path, outside the
    # ``xdg-data-home`` and ``fake-home`` subdirs created by fake_xdg.
    real_repo = tmp_path / "real"
    (real_repo / ".git").mkdir(parents=True)
    other_real_repo = tmp_path / "other_real"
    (other_real_repo / ".git").mkdir(parents=True)

    # Symlink pointing at the real repo. Windows is already excluded by
    # the suite-level platform skip in tests/backup/conftest.py; on
    # Linux+macOS ``symlink_to`` is reliable without elevated privileges.
    symlinked_repo = tmp_path / "via_symlink"
    symlinked_repo.symlink_to(real_repo, target_is_directory=True)

    # One rewrite in each of the three roots. Target basenames are the
    # same (``.env``) so any accidental cross-repo collision would show
    # up as a unified bucket with multiple identical basenames.
    env_real = make_env_file(real_repo / ".env", b"KEY=real\n")
    safe_rewrite(env_real, b"KEY=real2\n", original_user_arg=env_real)

    env_via_link = make_env_file(symlinked_repo / ".env", b"KEY=link\n")
    safe_rewrite(env_via_link, b"KEY=link2\n", original_user_arg=env_via_link)

    env_other = make_env_file(other_real_repo / ".env", b"KEY=other\n")
    safe_rewrite(env_other, b"KEY=other2\n", original_user_arg=env_other)

    bucket_real_name = _bucket_for(real_repo)
    bucket_symlink_name = _bucket_for(symlinked_repo)
    bucket_other_name = _bucket_for(other_real_repo)

    # Sanity (pure-python): symlink resolves to real_repo, other does not.
    assert bucket_symlink_name == bucket_real_name, (
        "symlink did not resolve to real repo at the path level — test "
        "precondition violated before touching the product"
    )
    assert bucket_other_name != bucket_real_name, (
        "two distinct real repo roots hashed to the same bucket at the "
        "path level — test precondition violated before touching the product"
    )

    backups_root = fake_xdg / "worthless" / "backups"

    # Invariant A: real + symlinked view share ONE bucket with two .bak files.
    bucket_real = backups_root / bucket_real_name
    assert bucket_real.is_dir(), f"unified real/symlink bucket missing: {bucket_real}"
    real_baks = sorted(bucket_real.glob(".env.*.bak"))
    assert len(real_baks) == 2, (
        "expected symlinked view + real view to unify into ONE bucket with "
        f"2 .bak files; got {len(real_baks)}: {real_baks!r}"
    )

    # Invariant B: distinct real repo has its own bucket with one .bak.
    bucket_other = backups_root / bucket_other_name
    assert bucket_other.is_dir(), (
        f"separate-real-repo bucket missing — resolved-path bucket keying regressed: {bucket_other}"
    )
    other_baks = sorted(bucket_other.glob(".env.*.bak"))
    assert len(other_baks) == 1, (
        f"expected 1 .bak in other_real_repo bucket; got {len(other_baks)}: {other_baks!r}"
    )

    # Invariant C: two distinct bucket DIRS coexist on disk, and the
    # real/other bucket names are not equal. Guards against a future
    # key change that collapses distinct resolved repo roots into a
    # single bucket.
    on_disk = {p.name for p in backups_root.iterdir() if p.is_dir()}
    assert {bucket_real_name, bucket_other_name}.issubset(on_disk), (
        f"expected both bucket dirs on disk; got {sorted(on_disk)!r}"
    )
    assert bucket_real_name != bucket_other_name, (
        "bucket key collapsed two distinct resolved repo roots — the "
        "documented contract `sha256(str(repo_root.resolve()))` was regressed"
    )


# ---------------------------------------------------------------------------
# Test 17: rotation is defensive against malformed / human-authored bucket
# files — skip, warn, continue.
#
# Covers QA M7. The bucket dir lives under ``$XDG_DATA_HOME`` and is a
# plain directory on disk; a human may drop files there (editor swap
# files, ``README`` debris, a stray tarball). The rotation logic must
# treat any entry whose filename does not match the documented
# ``<basename>.<iso-ns>.<pid>.<counter>.bak`` pattern as *quarantined*
# — specifically it must:
#
#   1. Log a WARNING on the ``worthless.cli.backup`` logger that names
#      the offending filename (so the user can see why we skipped it).
#   2. Skip the file when computing the sort-and-evict ordering — it
#      must neither be treated as "newest" (which would evict a valid
#      .bak in its place) nor as "oldest" (which would delete it).
#   3. Not raise / not abort the in-flight rewrite — rotation failure
#      already degrades to "warn + continue" (see Test 12), and this
#      sub-case is a specialisation of that policy.
#   4. Not delete or rename the malformed file — the contract is
#      *quarantine*, not *clean*. If we silently deleted unknown files
#      in $XDG_DATA_HOME we'd be a data-loss hazard.
#
# Note on ``.env.abc.def.ghi.bak``: it DOES match the loose glob
# ``.env.*.bak``, which is exactly why invariant 2 filters via
# ``_BACKUP_NAME_RE`` (the structured regex defined at module scope
# earlier in this file) rather than the glob — otherwise the count
# would over-read and the test would pass for the wrong reason.
#
# RED signal: ``_import_backup()`` raises ``ImportError`` (specifically
# "cannot import name 'backup'") BEFORE any of the malformed-file
# staging, rotation, or log-capture matters.
# ---------------------------------------------------------------------------


def test_rotation_ignores_and_warns_on_malformed_bucket_files(
    tmp_repo, fake_xdg, make_env_file, caplog
) -> None:
    """Rotation treats non-conforming bucket entries as quarantined.

    Four invariants (QA M7):

    1. Every malformed file staged in the bucket before rotation is
       still on disk afterwards — rotation MUST NOT delete or rename
       human-authored files it does not recognize.
    2. The 50-entry cap is enforced against *parseable* .bak files
       only (those matching ``_BACKUP_NAME_RE``). Malformed files do
       not count toward or against the cap.
    3. At least one WARNING is emitted on the ``worthless.cli.backup``
       logger, and the message names at least one of the malformed
       filenames so a human operator can locate the quarantined file.
    4. Rotation must not raise — 51 rewrites must complete even with
       garbage sitting in the bucket.

    RED signal: ``_import_backup()`` raises ``ImportError`` (specifically
    "cannot import name 'backup' from 'worthless.cli'") before any of
    the malformed-file staging or rotation behavior matters.
    """
    from worthless.cli.safe_rewrite import safe_rewrite

    _import_backup()

    # Stage the bucket with a mix of malformed / human-authored junk
    # BEFORE any rewrite — the rotation logic must encounter them on
    # its very first pass, not only once valid .baks have accumulated.
    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)
    bucket.mkdir(parents=True, mode=0o700)

    malformed = [
        ".env.no-extension",  # missing .bak suffix entirely
        "README",  # wrong shape, no .env prefix
        ".env.abc.def.ghi.bak",  # .bak but non-numeric components
        ".env..12345..bak",  # empty ts / counter components
        ".env.swp",  # classic editor swap file
    ]
    for name in malformed:
        (bucket / name).write_bytes(b"junk\n")

    # Drive 51 real rewrites (one over the documented cap of 50) so
    # the rotation / evict codepath is exercised at least once.
    env = make_env_file(tmp_repo / ".env", b"KEY=v0\n")
    caplog.set_level(logging.WARNING, logger="worthless.cli.backup")
    for i in range(1, 52):
        safe_rewrite(env, f"KEY=v{i}\n".encode(), original_user_arg=env)

    # Invariant 1: every malformed file is still on disk (neither
    # deleted nor renamed).
    for name in malformed:
        assert (bucket / name).exists(), (
            f"rotation deleted malformed bucket entry {name!r} — contract is quarantine, not clean"
        )

    # Invariant 2: the cap (50) applies only to files whose names
    # match the documented ``<basename>.<iso-ns>.<pid>.<counter>.bak``
    # structure. The loose glob ``.env.*.bak`` would over-count
    # because ``.env.abc.def.ghi.bak`` matches it — so we filter via
    # the module-scope regex ``_BACKUP_NAME_RE``.
    valid_baks = sorted(bucket.glob(".env.*.bak"))
    parseable = [p for p in valid_baks if _BACKUP_NAME_RE.match(p.name)]
    assert len(parseable) == 50, (
        f"expected 50 parseable .bak files post-rotation; got "
        f"{len(parseable)}: {[p.name for p in parseable]!r}"
    )

    # Invariant 3: at least one WARNING was emitted to the product
    # logger, and the message names at least one of the malformed
    # files so an operator can locate what was skipped.
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "worthless.cli.backup"
    ]
    assert warnings, "no WARNING recorded on worthless.cli.backup logger"
    flattened = " | ".join(r.getMessage() for r in warnings)
    assert any(name in flattened for name in malformed), (
        f"WARNING did not name any malformed bucket entry; records: {flattened!r}"
    )


# ---------------------------------------------------------------------------
# Bonus tests B1-B3 from wor-276-recovery-final-plan.md §5a (security review).
#
# These close the symlink-swap / fd-flag / ghost-tmp attack windows that the
# core 14 cover only implicitly. They share the same RED contract: the
# per-test ``_import_backup()`` call must raise ``ImportError: cannot import
# name 'backup' from 'worthless.cli'`` on this commit.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["real_dir", "dangling", "file_target"])
def test_refuses_if_bucket_dir_is_preexisting_symlink(
    variant, tmp_path, tmp_repo, fake_xdg, make_env_file, sha256_of
) -> None:
    """Attacker pre-plants the bucket as a symlink; rewrite refuses.

    Parametrised to cover three symlink-target shapes:

    * ``real_dir`` — target is an existing attacker-owned directory
      (the classic secrets-exfil vector).
    * ``dangling`` — target does not exist at all; naive
      ``stat.S_ISDIR`` on the resolved path would still refuse to
      create the bucket, but naive ``os.makedirs(exist_ok=True)`` on
      a dangling symlink materialises the attacker-chosen target.
    * ``file_target`` — target is a regular file, not a directory;
      product must refuse rather than trying to open it with
      ``O_DIRECTORY`` and racing the attacker on the error recovery.
    """
    from worthless.cli.errors import UnsafeRewriteRefused
    from worthless.cli.safe_rewrite import safe_rewrite

    _import_backup()
    backup_reason = _require_unsafe_reason_backup()

    attacker_sink = tmp_path / "attacker-sink"
    backups_root = fake_xdg / "worthless" / "backups"
    backups_root.mkdir(parents=True, mode=0o700)
    bucket_path = backups_root / _bucket_for(tmp_repo)

    if variant == "real_dir":
        attacker_sink.mkdir(mode=0o700)
        symlink_target = attacker_sink
    elif variant == "dangling":
        symlink_target = tmp_path / "does-not-exist"
        assert not symlink_target.exists()
    elif variant == "file_target":
        attacker_sink.write_bytes(b"not-a-directory\n")
        symlink_target = attacker_sink
    else:
        raise AssertionError(f"unknown variant {variant!r}")
    os.symlink(str(symlink_target), str(bucket_path))

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    baseline = sha256_of(env)

    with pytest.raises(UnsafeRewriteRefused) as excinfo:
        safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    assert excinfo.value.reason == backup_reason, (
        f"[{variant}] symlinked bucket must raise with UnsafeReason.BACKUP"
    )

    if variant == "real_dir":
        # Attacker dir must stay empty — no bytes must leak there.
        assert list(attacker_sink.iterdir()) == [], (
            "backup leaked into attacker-controlled symlink target"
        )
    elif variant == "dangling":
        # Naive materialisation would create the target dir; refusal
        # must leave it non-existent.
        assert not symlink_target.exists(), "dangling symlink target materialised despite refusal"
    elif variant == "file_target":
        # The attacker-supplied file must remain a regular file, same
        # bytes, unchanged mtime-compatible: critically, not clobbered
        # or promoted to a directory.
        assert attacker_sink.is_file(), "file_target sink mutated from file"
        assert attacker_sink.read_bytes() == b"not-a-directory\n", (
            "file_target sink bytes clobbered despite refusal"
        )

    # And no backup file may appear anywhere else under the backups root
    # either — we refused before writing, period.
    stray = [p for p in backups_root.rglob("*.bak") if p.is_file()]
    stray_tmp = [p for p in backups_root.rglob("*.bak.tmp-*") if p.is_file()]
    assert stray == [] and stray_tmp == [], (
        f"[{variant}] backup bytes written despite refusal: bak={stray!r} tmp={stray_tmp!r}"
    )

    # Target file must be pristine.
    assert sha256_of(env) == baseline, f"[{variant}] target rewritten despite symlinked bucket"


def test_refuses_if_backups_root_is_preexisting_symlink(
    tmp_path, tmp_repo, fake_xdg, make_env_file, sha256_of
) -> None:
    """Attacker swaps ``$XDG_DATA_HOME/worthless/backups`` itself for a
    symlink, redirecting every bucket — not just this repo's. The
    product must refuse to follow the intermediate-dir symlink, not
    just the leaf bucket.

    Extends B1 to cover the case where the attacker owns
    ``$XDG_DATA_HOME/worthless/`` (e.g. a shared-HOME CI setup) but
    not the leaf bucket name (which is repo-specific).
    """
    from worthless.cli.errors import UnsafeRewriteRefused
    from worthless.cli.safe_rewrite import safe_rewrite

    _import_backup()
    backup_reason = _require_unsafe_reason_backup()

    attacker_backups = tmp_path / "attacker-backups"
    attacker_backups.mkdir(mode=0o700)

    worthless_dir = fake_xdg / "worthless"
    worthless_dir.mkdir(parents=True, mode=0o700)
    backups_root = worthless_dir / "backups"
    os.symlink(str(attacker_backups), str(backups_root))

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    baseline = sha256_of(env)

    with pytest.raises(UnsafeRewriteRefused) as excinfo:
        safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    assert excinfo.value.reason == backup_reason, (
        "intermediate-dir symlink must raise with UnsafeReason.BACKUP"
    )
    assert list(attacker_backups.iterdir()) == [], (
        "backup leaked into attacker-controlled backups_root symlink target"
    )
    assert sha256_of(env) == baseline, "target rewritten despite symlinked backups_root"


def test_bucket_dir_opened_with_o_nofollow_o_directory(
    tmp_repo, fake_xdg, make_env_file, monkeypatch
) -> None:
    """Every open of the bucket dir itself must pass O_NOFOLLOW | O_DIRECTORY;
    this is the syscall-level defence against a bucket-swap TOCTOU where
    the dir gets replaced with a symlink between stat() and open().
    """
    from worthless.cli.safe_rewrite import safe_rewrite

    _import_backup()

    backups_root = fake_xdg / "worthless" / "backups"
    backups_root_str = str(backups_root)
    bucket = backups_root / _bucket_for(tmp_repo)
    bucket_str = str(bucket)
    bucket_name = bucket.name

    real_open = os.open
    observed_bucket: list[tuple[str, int]] = []
    observed_root: list[tuple[str, int]] = []

    def _matches_bucket(path_str: str, dir_fd) -> bool:  # noqa: ANN001
        # Absolute-path open of the bucket itself.
        if path_str == bucket_str or path_str.rstrip("/") == bucket_str:
            return True
        # ``openat``-style relative open: the path is the leaf bucket
        # name resolved against a dir_fd on the backups root. Also
        # catches the rarer case of a trailing ``bucket_str`` substring
        # when the impl passes a computed relative prefix.
        if dir_fd is not None and (path_str == bucket_name or bucket_str in path_str):
            return True
        return False

    def _matches_backups_root(path_str: str) -> bool:  # noqa: ANN001
        return path_str == backups_root_str or path_str.rstrip("/") == backups_root_str

    def _spy(path, flags, mode=0o777, *, dir_fd=None):  # noqa: ANN001
        try:
            path_str = os.fspath(path)
        except TypeError:
            path_str = str(path)
        if _matches_bucket(path_str, dir_fd):
            observed_bucket.append((path_str, int(flags)))
        if _matches_backups_root(path_str):
            observed_root.append((path_str, int(flags)))
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", _spy)

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    assert observed_bucket, (
        "no os.open call targeted the bucket directory — O_NOFOLLOW|O_DIRECTORY "
        "invariant cannot be verified"
    )
    required = os.O_NOFOLLOW | os.O_DIRECTORY
    for path_str, flags in observed_bucket:
        assert (flags & os.O_NOFOLLOW) == os.O_NOFOLLOW, (
            f"bucket-dir open missing O_NOFOLLOW: path={path_str!r} flags={oct(flags)}"
        )
        assert (flags & os.O_DIRECTORY) == os.O_DIRECTORY, (
            f"bucket-dir open missing O_DIRECTORY: path={path_str!r} flags={oct(flags)}"
        )
        assert (flags & required) == required, (
            f"bucket-dir open missing O_NOFOLLOW|O_DIRECTORY bits: "
            f"path={path_str!r} flags={oct(flags)}"
        )

    # Extends B1b: the ``backups`` parent dir itself must also be opened
    # with O_NOFOLLOW|O_DIRECTORY, otherwise an attacker who swaps the
    # parent for a symlink between first-run mkdir and the per-rewrite
    # dir-fd open silently redirects every bucket.
    assert observed_root, (
        "no os.open call targeted the backups_root parent — intermediate-dir "
        "O_NOFOLLOW|O_DIRECTORY invariant cannot be verified"
    )
    hardened_root = [(p, f) for (p, f) in observed_root if (f & required) == required]
    assert hardened_root, (
        f"backups_root open missing O_NOFOLLOW|O_DIRECTORY bits on every "
        f"observed call: {[(p, oct(f)) for (p, f) in observed_root]!r}"
    )


def test_ghost_bak_tmp_unlinked_on_write_failure(
    tmp_repo, fake_xdg, make_env_file, sha256_of, monkeypatch, fd_to_path
) -> None:
    """A fsync failure mid-backup must not leave a 0o600 ghost under
    $XDG_DATA_HOME; orphaned tmp files accumulate across retries and
    silently pin the user's old secrets on disk.
    """
    from worthless.cli.errors import UnsafeRewriteRefused
    from worthless.cli.safe_rewrite import safe_rewrite

    _import_backup()
    backup_reason = _require_unsafe_reason_backup()

    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)

    real_fsync = os.fsync
    fired = {"count": 0}

    def _boom(fd):  # noqa: ANN001
        # Inject ENOSPC only for the backup tmp fd; leave other fsyncs
        # alone so the test can't false-pass by crashing unrelated I/O.
        try:
            path = fd_to_path(fd)
        except BaseException:
            return real_fsync(fd)
        if ".bak.tmp-" in path and str(bucket) in path:
            fired["count"] += 1
            raise OSError(errno.ENOSPC, "no space left on device")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _boom)

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    baseline = sha256_of(env)

    with pytest.raises(UnsafeRewriteRefused) as excinfo:
        safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    assert excinfo.value.reason == backup_reason, (
        "fsync-mid-backup failure must surface as UnsafeReason.BACKUP"
    )
    assert fired["count"] >= 1, (
        "ENOSPC injection never fired on any .bak.tmp-* fsync — the "
        "fd_to_path filter missed the target fd (likely a macOS symlink "
        "mismatch), so refusal came from elsewhere and the test would "
        "false-pass"
    )
    # ENOSPC must be attributable to the refusal (either as __cause__ or
    # in the str form) so an operator can diagnose the root cause.
    cause_chain = []
    cur: BaseException | None = excinfo.value
    while cur is not None:
        cause_chain.append(cur)
        cur = cur.__cause__ or cur.__context__
    matched = (
        any(isinstance(exc, OSError) and exc.errno == errno.ENOSPC for exc in cause_chain)
        or "ENOSPC" in str(excinfo.value)
        or "no space left" in str(excinfo.value)
    )
    assert matched, (
        f"ENOSPC root cause not attributable to refusal: chain={cause_chain!r} "
        f"str={str(excinfo.value)!r}"
    )

    # Ghost cleanup: no tmp fragments survive.
    if bucket.exists():
        ghosts = list(bucket.glob("*.bak.tmp-*"))
        assert ghosts == [], f"ghost backup-tmp fragments left after failure: {ghosts!r}"
        # And no .bak ever got promoted — the tmp failed before rename.
        promoted = list(bucket.glob("*.bak"))
        assert promoted == [], f".bak was promoted despite fsync failure on the tmp: {promoted!r}"

    # Target must be pristine.
    assert sha256_of(env) == baseline, "target rewritten despite backup-fsync failure"


def test_bak_tmp_opened_with_o_tmpfile_or_immediate_unlink(
    tmp_repo, fake_xdg, make_env_file, monkeypatch
) -> None:
    """Secret-leak window: ``.bak.tmp-*`` must be either unnamed
    (``O_TMPFILE`` on Linux) OR unlinked from the bucket directory
    before any plaintext byte is written. Without this, a SIGKILL
    between ``os.write`` and the promote-rename leaves a
    world-unreadable-but-on-disk named tmp file containing the old
    secrets, pinned under ``$XDG_DATA_HOME``.

    Extends B3: B3 asserts ghost cleanup on fsync failure; this test
    asserts structural containment even when nothing fails.
    """
    from worthless.cli.safe_rewrite import safe_rewrite

    _import_backup()

    bucket = fake_xdg / "worthless" / "backups" / _bucket_for(tmp_repo)

    real_open = os.open
    real_write = os.write
    real_unlink = os.unlink

    # Per-fd observation: the earliest event between write and unlink
    # for a given fd is what pins containment.
    opens: dict[int, tuple[str, int]] = {}
    first_event: dict[int, str] = {}
    unlinked_paths: set[str] = set()

    def _spy_open(path, flags, mode=0o777, *, dir_fd=None):  # noqa: ANN001
        try:
            path_str = os.fspath(path)
        except TypeError:
            path_str = str(path)
        if dir_fd is None:
            fd = real_open(path, flags, mode)
        else:
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if ".bak.tmp-" in path_str and str(bucket) in path_str:
            opens[fd] = (path_str, int(flags))
        return fd

    def _spy_write(fd, data, *args, **kwargs):
        if fd in opens and fd not in first_event:
            first_event[fd] = "write"
        return real_write(fd, data, *args, **kwargs)

    def _spy_unlink(path, *args, **kwargs):
        try:
            path_str = os.fspath(path)
        except TypeError:
            path_str = str(path)
        unlinked_paths.add(path_str)
        for fd, (p, _f) in opens.items():
            if p == path_str and fd not in first_event:
                first_event[fd] = "unlink"
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", _spy_open)
    monkeypatch.setattr(os, "write", _spy_write)
    monkeypatch.setattr(os, "unlink", _spy_unlink)

    env = make_env_file(tmp_repo / ".env", b"KEY=v\n")
    safe_rewrite(env, b"KEY=new\n", original_user_arg=env)

    assert opens, (
        "no os.open call targeted a bucket/*.bak.tmp-* path — cannot "
        "verify O_TMPFILE / early-unlink containment"
    )

    o_tmpfile = getattr(os, "O_TMPFILE", 0)
    for fd, (path_str, flags) in opens.items():
        has_tmpfile = bool(o_tmpfile) and (flags & o_tmpfile) == o_tmpfile
        unlinked_first = first_event.get(fd) == "unlink"
        if has_tmpfile:
            continue
        if sys.platform == "darwin" and not o_tmpfile:
            # macOS has no O_TMPFILE. Enforce the unlink-before-write
            # path as the mandatory containment pattern.
            assert unlinked_first, (
                f"[darwin] .bak.tmp-* fd={fd} path={path_str!r} was "
                f"written before any unlink — SIGKILL-window secret "
                f"leak vector"
            )
            continue
        assert unlinked_first, (
            f".bak.tmp-* fd={fd} path={path_str!r} flags={oct(flags)} "
            f"was neither opened O_TMPFILE nor unlinked before first "
            f"write; first_event={first_event.get(fd)!r}"
        )
