"""Out-of-repo backup writer for WOR-276 recovery.

This module writes a byte-identical copy of a target file to a
per-repo, per-user bucket under ``$XDG_DATA_HOME/worthless/backups``
before ``safe_rewrite`` replaces it. All mode, ordering, atomicity
and rotation invariants are pinned by ``tests/backup/*`` and the
plan at ``docs/planning/wor-276-recovery-final-plan.md``.
"""

from __future__ import annotations

import calendar
import errno
import hashlib
import logging
import os
import re
import secrets
import stat
import sys
import time as _time_mod
from collections.abc import Callable
from pathlib import Path

from worthless.cli import safe_rewrite as _safe_rewrite_mod
from worthless.cli.errors import UnsafeReason, UnsafeRewriteRefused

__all__ = [
    "set_backup_hook",
    "set_post_backup_fsync_hook",
    "set_post_backup_rename_hook",
    "time_ns",
    "write_backup",
]


_log = logging.getLogger("worthless.cli.backup")


def time_ns() -> int:
    """Return current time in ns. Separate shim so tests can monkeypatch
    either ``time.time_ns`` or ``worthless.cli.backup.time_ns``.
    """
    return _time_mod.time_ns()


_BACKUP_NAME_RE = re.compile(
    r"^(?P<base>[^/]+?)"
    r"\.(?P<yy>\d{4})-(?P<mm>\d{2})-(?P<dd>\d{2})"
    r"T(?P<hh>\d{2}):(?P<mi>\d{2}):(?P<ss>\d{2})"
    r"\.(?P<ns>\d{9})"
    r"\.(?P<pid>\d+)"
    r"\.(?P<counter>\d+)"
    r"\.bak$"
)

_MARKER_NAME = ".first-run-seen"
_ROTATION_CAP = 50


# ---------------------------------------------------------------------------
# Per-process monotonic counter (collision-breaker when time_ns collides)
# ---------------------------------------------------------------------------


_BACKUP_COUNTER = 0


def _reset_counter_for_tests(start: int = 0) -> None:
    """Test seam: reset the process-wide counter to ``start``."""
    global _BACKUP_COUNTER  # noqa: PLW0603
    _BACKUP_COUNTER = start


def _next_counter() -> int:
    global _BACKUP_COUNTER  # noqa: PLW0603
    val = _BACKUP_COUNTER
    _BACKUP_COUNTER += 1
    return val


# ---------------------------------------------------------------------------
# Chaos hooks (zero-arg callables; used by sibling SIGKILL chaos tests)
# ---------------------------------------------------------------------------


_post_backup_fsync_hook: Callable[[], None] | None = None
_post_backup_rename_hook: Callable[[], None] | None = None


def set_post_backup_fsync_hook(hook: Callable[[], None] | None) -> None:
    """Install a hook fired after the backup tmp fsync, before the rename."""
    global _post_backup_fsync_hook  # noqa: PLW0603
    _post_backup_fsync_hook = hook


def set_post_backup_rename_hook(hook: Callable[[], None] | None) -> None:
    """Install a hook fired after the backup atomic rename, before return."""
    global _post_backup_rename_hook  # noqa: PLW0603
    _post_backup_rename_hook = hook


# ---------------------------------------------------------------------------
# Bucket path resolution (locked contract — see plan §3)
# ---------------------------------------------------------------------------


def _xdg_data_home() -> Path:
    """Return ``$XDG_DATA_HOME`` or the ``~/.local/share`` fallback.

    Per XDG spec: unset *or* empty value both fall back.
    """
    raw = os.environ.get("XDG_DATA_HOME")
    if not raw:
        return Path("~/.local/share").expanduser()
    return Path(raw)


def _bucket_for(repo_root: Path) -> str:
    """sha256-hex of the resolved repo-root path."""
    return hashlib.sha256(str(repo_root.resolve()).encode("utf-8")).hexdigest()


def _bucket_path(repo_root: Path) -> Path:
    return _xdg_data_home() / "worthless" / "backups" / _bucket_for(repo_root)


def infer_repo_root(target: Path) -> Path | None:
    """Walk up from target's directory until a ``.git`` marker is found.

    Returns ``None`` when no repo root can be inferred — backup is
    then skipped so legacy callers without XDG pinned can't pollute
    the real user's ``~/.local/share``.
    """
    try:
        start = target.resolve(strict=False).parent
    except OSError:
        return None
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Hardened directory opens (O_NOFOLLOW | O_DIRECTORY)
# ---------------------------------------------------------------------------


def _open_dir_nofollow(path: Path) -> int:
    """Open a directory with O_NOFOLLOW | O_DIRECTORY.

    Raises the raw OSError — callers wrap it with BACKUP as needed so
    the exception chain carries the original errno.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    return os.open(str(path), flags)


def _ensure_parent_dir(path: Path) -> int:
    """Create-if-missing a plain directory at ``path`` (any mode) and
    return a hardened fd. Refuses symlinks and non-directories.

    Used for ancestors of the bucket (``$XDG_DATA_HOME/worthless/``,
    ``.../backups/``) — the mode on those is not pinned by contract.
    """
    try:
        st = os.lstat(str(path))
    except FileNotFoundError:
        st = None
    except OSError as exc:
        raise UnsafeRewriteRefused(UnsafeReason.BACKUP) from exc

    if st is not None:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise UnsafeRewriteRefused(UnsafeReason.BACKUP)
    else:
        try:
            path.mkdir(parents=True, mode=0o700, exist_ok=True)
        except OSError as exc:
            raise UnsafeRewriteRefused(UnsafeReason.BACKUP) from exc

    try:
        return _open_dir_nofollow(path)
    except OSError as exc:
        raise UnsafeRewriteRefused(UnsafeReason.BACKUP) from exc


def _ensure_bucket_dir(bucket: Path) -> int:
    """Create-if-missing the per-repo bucket with mode 0o700 and return fd.

    Refuses if the path is a symlink, a non-directory, or a directory
    with any mode != 0o700.
    """
    try:
        st = os.lstat(str(bucket))
    except FileNotFoundError:
        st = None
    except OSError as exc:
        raise UnsafeRewriteRefused(UnsafeReason.BACKUP) from exc

    if st is not None:
        if stat.S_ISLNK(st.st_mode):
            raise UnsafeRewriteRefused(UnsafeReason.BACKUP)
        if not stat.S_ISDIR(st.st_mode):
            raise UnsafeRewriteRefused(UnsafeReason.BACKUP)
        if stat.S_IMODE(st.st_mode) != 0o700:
            raise UnsafeRewriteRefused(UnsafeReason.BACKUP)
    else:
        try:
            bucket.mkdir(parents=False, exist_ok=False, mode=0o700)
        except FileExistsError:
            # Race: recheck via lstat.
            try:
                st = os.lstat(str(bucket))
            except OSError as exc:
                raise UnsafeRewriteRefused(UnsafeReason.BACKUP) from exc
            if (
                stat.S_ISLNK(st.st_mode)
                or not stat.S_ISDIR(st.st_mode)
                or stat.S_IMODE(st.st_mode) != 0o700
            ):
                raise UnsafeRewriteRefused(UnsafeReason.BACKUP) from None
        except OSError as exc:
            raise UnsafeRewriteRefused(UnsafeReason.BACKUP) from exc
        # Defensive chmod — umask may have masked bits on some filesystems.
        try:
            bucket.chmod(0o700)
        except OSError:
            pass

    try:
        return _open_dir_nofollow(bucket)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafeRewriteRefused(UnsafeReason.BACKUP) from exc
        raise UnsafeRewriteRefused(UnsafeReason.BACKUP) from exc


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


def _parse_ts_counter(name: str) -> tuple[int, int] | None:
    """Parse ``(timestamp_ns, counter)`` from a backup filename or None."""
    m = _BACKUP_NAME_RE.match(name)
    if m is None:
        return None
    try:
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
    except (ValueError, OverflowError):
        return None
    ts_ns = epoch_s * 1_000_000_000 + int(m.group("ns"))
    return (ts_ns, int(m.group("counter")))


def _rotate_bucket(bucket: Path, basename: str) -> None:
    """Keep at most ``_ROTATION_CAP`` parseable backups for ``basename``.

    * Files whose names don't match the canonical regex are treated as
      quarantined: WARN-logged (naming the file) and left on disk.
    * Parseable files matching ``basename`` are sorted by the filename's
      ``(timestamp_ns, counter)`` tuple; entries beyond the cap are
      unlinked oldest-first.
    * ``os.unlink`` failures are WARN-logged with labelled errno;
      rotation is best-effort and never raises.
    """
    try:
        entries = list(bucket.iterdir())
    except OSError as exc:
        _log.warning(
            "rotation: scan failed for %s: errno=%s %s",
            bucket,
            exc.errno,
            exc.strerror,
        )
        return

    parseable: list[tuple[tuple[int, int], Path]] = []
    for entry in entries:
        if not entry.is_file():
            continue
        if entry.name == _MARKER_NAME:
            continue
        m = _BACKUP_NAME_RE.match(entry.name)
        if m is None:
            # Only warn for entries that LOOK like they want to be
            # rotation targets — e.g. matching the loose glob or sharing
            # the basename prefix. Random unrelated files are silent.
            if entry.name.endswith(".bak") or entry.name.startswith(basename + "."):
                _log.warning(
                    "rotation: quarantined malformed bucket entry %s "
                    "(does not match canonical <base>.<iso-ns>.<pid>.<counter>.bak)",
                    entry.name,
                )
            continue
        if m.group("base") != basename:
            continue
        key = _parse_ts_counter(entry.name)
        if key is None:
            _log.warning("rotation: unparsable ts/counter for %s", entry.name)
            continue
        parseable.append((key, entry))

    if len(parseable) <= _ROTATION_CAP:
        return

    parseable.sort(key=lambda kv: kv[0])
    to_delete = parseable[: len(parseable) - _ROTATION_CAP]
    for _key, path in to_delete:
        try:
            os.unlink(str(path))  # noqa: PTH108  # rotation spies monkeypatch os.unlink
        except FileNotFoundError:
            continue
        except OSError as exc:
            _log.warning(
                "rotation: unlink %s failed (best-effort): errno=%s %s",
                path.name,
                exc.errno,
                exc.strerror,
            )


# ---------------------------------------------------------------------------
# First-run notice + marker
# ---------------------------------------------------------------------------


def _emit_first_run_notice(bucket: Path) -> None:
    """Print a one-shot recovery hint to stderr; anchored by a 0o600 marker."""
    marker = bucket / _MARKER_NAME
    if marker.exists():
        return
    msg = (
        f"worthless: backups will be written to {bucket}.\n"
        f"  to recover a corrupted file, run: worthless restore <path>\n"
    )
    try:
        sys.stderr.write(msg)
        sys.stderr.flush()
    except OSError:
        pass
    try:
        fd = os.open(
            str(marker),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        os.close(fd)
        marker.chmod(0o600)
    except FileExistsError:
        pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Filename assembly
# ---------------------------------------------------------------------------


def _iso_from_ns(ts_ns: int) -> str:
    secs, frac_ns = divmod(ts_ns, 1_000_000_000)
    return _time_mod.strftime("%Y-%m-%dT%H:%M:%S", _time_mod.gmtime(secs)) + f".{frac_ns:09d}"


# ---------------------------------------------------------------------------
# write_backup — the public writer
# ---------------------------------------------------------------------------


def write_backup(target: Path, *, repo_root: Path) -> Path:
    """Write a 0o600, atomic, fsync'd copy of ``target`` into its bucket.

    Returns the absolute path of the final ``.bak``. Rotates the bucket
    to at most 50 entries per basename after the rename lands. Emits a
    one-shot first-run notice to stderr the first time a bucket is
    written to.
    """
    basename = target.name

    # Ensure the parent chain exists with symlink-safe fds held for the
    # duration of the write — an attacker can't swap these for symlinks
    # between our check and our rename once they're open.
    xdg = _xdg_data_home()
    worthless_dir = xdg / "worthless"
    backups_root = worthless_dir / "backups"
    bucket = backups_root / _bucket_for(repo_root)

    xdg_fd = _ensure_parent_dir(xdg)
    try:
        worthless_fd = _ensure_parent_dir(worthless_dir)
        try:
            backups_root_fd = _ensure_parent_dir(backups_root)
            try:
                bucket_fd = _ensure_bucket_dir(bucket)
                try:
                    return _write_backup_into(target, basename, bucket, bucket_fd)
                finally:
                    try:
                        os.close(bucket_fd)
                    except OSError:
                        pass
            finally:
                try:
                    os.close(backups_root_fd)
                except OSError:
                    pass
        finally:
            try:
                os.close(worthless_fd)
            except OSError:
                pass
    finally:
        try:
            os.close(xdg_fd)
        except OSError:
            pass


def _assemble_final_name(basename: str) -> str:
    ts_ns = globals()["time_ns"]()
    iso = _iso_from_ns(ts_ns)
    return f"{basename}.{iso}.{os.getpid()}.{_next_counter()}.bak"


def _copy_fd_to_fd(src_fd: int, dst_fd: int) -> None:
    while True:
        chunk = os.read(src_fd, 65536)
        if not chunk:
            break
        view = memoryview(chunk)
        while view:
            view = view[os.write(dst_fd, view) :]


def _open_secret_scratch(scratch_path: Path) -> int:
    """Open O_EXCL scratch fd then unlink — secret-contained inode."""
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    scratch_fd = os.open(str(scratch_path), flags, 0o600)
    try:
        os.unlink(str(scratch_path))  # noqa: PTH108  # B4 spy hooks os.unlink
    except FileNotFoundError:
        pass
    except OSError as exc:
        os.close(scratch_fd)
        raise UnsafeRewriteRefused(UnsafeReason.BACKUP) from exc
    return scratch_fd


def _copy_scratch_to_staging(scratch_fd: int, staging_path: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    staging_fd = os.open(str(staging_path), flags, 0o600)
    try:
        os.lseek(scratch_fd, 0, os.SEEK_SET)
        _copy_fd_to_fd(scratch_fd, staging_fd)
        os.fsync(staging_fd)
    finally:
        try:
            os.close(staging_fd)
        except OSError:
            pass


def _promote_staging(staging_path: Path, final_path: Path) -> None:
    # Fast-path existence check: if the final name is already there,
    # refuse. Load-bearing for the O_EXCL chaos test — the whole
    # (time_ns, pid, counter) triple collided.
    if final_path.exists():
        raise FileExistsError(
            errno.EEXIST,
            "backup destination already exists",
            str(final_path),
        )
    os.rename(str(staging_path), str(final_path))  # noqa: PTH104  # atomic rename semantics


def _write_backup_into(
    target: Path,
    basename: str,
    bucket: Path,
    bucket_fd: int,
) -> Path:
    """Inner write: filename assembly, secret-contained tmp, atomic rename."""
    final_name = _assemble_final_name(basename)
    final_path = bucket / final_name
    scratch_path = bucket / f"{final_name}.tmp-{secrets.token_hex(8)}"
    staging_path: Path | None = bucket / f"{final_name}.staging-{secrets.token_hex(8)}"

    scratch_fd = _open_secret_scratch(scratch_path)
    src_fd: int | None = None
    try:
        src_fd = os.open(str(target), os.O_RDONLY | os.O_NOFOLLOW)
        _copy_fd_to_fd(src_fd, scratch_fd)
        os.fsync(scratch_fd)

        if _post_backup_fsync_hook is not None:
            _post_backup_fsync_hook()

        assert staging_path is not None  # noqa: S101
        _copy_scratch_to_staging(scratch_fd, staging_path)
        _promote_staging(staging_path, final_path)
        staging_path = None

        os.fsync(bucket_fd)

        if _post_backup_rename_hook is not None:
            _post_backup_rename_hook()

        _emit_first_run_notice(bucket)

        try:
            _rotate_bucket(bucket, basename)
        except Exception as exc:  # noqa: BLE001
            _log.warning("rotation raised unexpectedly: %r", exc)

        return final_path
    finally:
        if src_fd is not None:
            try:
                os.close(src_fd)
            except OSError:
                pass
        try:
            os.close(scratch_fd)
        except OSError:
            pass
        if staging_path is not None:
            try:
                os.unlink(str(staging_path))  # noqa: PTH108  # symmetric with above
            except OSError:
                pass


# ---------------------------------------------------------------------------
# safe_rewrite hook binding
# ---------------------------------------------------------------------------


def _backup_caller(target: Path, repo_root: Path | None) -> None:
    """Dispatch to ``write_backup`` via dynamic lookup (test-friendly).

    Resolves ``write_backup`` from this module's ``globals()`` at call
    time so ``monkeypatch.setattr(backup, "write_backup", ...)`` is
    honoured without patching ``safe_rewrite`` too.
    """
    effective_root = repo_root if repo_root is not None else infer_repo_root(target)
    if effective_root is None:
        return
    fn = globals()["write_backup"]
    fn(target, repo_root=effective_root)


def set_backup_hook() -> None:
    """Bind the default pre-target-rename backup hook on ``safe_rewrite``."""
    _safe_rewrite_mod._set_backup_writer(_backup_caller)


# Auto-register on import so callers need not explicitly call
# ``set_backup_hook()`` — the first-run tests do call it explicitly
# (idempotent), the rest rely on this.
set_backup_hook()
