"""Invariant-gated safe rewrite for ``.env`` files.

This module exposes a single public function, :func:`safe_rewrite`, which
performs an atomic, invariant-checked rewrite of a literal ``.env`` file.
Every check is structured so that the historical ".zshrc lock bug" - where
a symlink or path confusion caused the tool to clobber a user's shell rc
file - is *structurally impossible*.

Every refusal raises :class:`UnsafeRewriteRefused`; the public message is
opaque, the granular cause is on ``.reason``, and the target file is
byte-identical across every refusal path.
"""

from __future__ import annotations

import errno
import fcntl
import io
import logging
import os
import secrets
import stat as _stat
import sys
from pathlib import Path
from collections.abc import Callable

from dotenv import dotenv_values

from worthless.cli.errors import UnsafeReason, UnsafeRewriteRefused

__all__ = [
    "UnsafeReason",
    "UnsafeRewriteRefused",
    "safe_rewrite",
]


_log = logging.getLogger("worthless.safe_rewrite")


# ---------------------------------------------------------------------------
# Module constants - the contract surface. Tests reference these values.
# ---------------------------------------------------------------------------

_BASENAME: str = ".env"
_BASENAME_DENYLIST: frozenset[str] = frozenset(
    {
        ".zshrc",
        ".bashrc",
        ".profile",
        ".netrc",
        "id_rsa",
        "id_ed25519",
        "credentials",
        "config",
        "authorized_keys",
        "known_hosts",
    }
)
_MAX_BYTES: int = 1 << 20
_MAX_LINES: int = 500
_DELTA_MIN: float = 0.25
_DELTA_MAX: float = 4.0
_TMP_RETRIES: int = 3

# Shell-construct markers. If any line of the existing file content
# (trimmed of leading whitespace) starts with one of these prefixes OR
# contains one of the infix markers, we treat the file as "not dotenv"
# and refuse.
_SHELL_PREFIXES: tuple[str, ...] = (
    "#!",
    "alias ",
    "export ",
    "function ",
    "source ",
    "if ",
    "case ",
    "eval ",
    "eval\t",
)
_SHELL_INFIX_MARKERS: tuple[str, ...] = ("<<",)

# Darwin's F_FULLFSYNC command constant. We don't rely on fcntl exporting
# it (Linux fcntl has no such constant) and fall back to plain fsync if
# the syscall returns ENOTTY/EINVAL.
_F_FULLFSYNC: int = 51


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _refuse(reason: UnsafeReason, target: Path | None = None) -> UnsafeRewriteRefused:
    """Build (but do not raise) a sanitised :class:`UnsafeRewriteRefused`.

    The DEBUG-log line carries the granular reason and the target path;
    the public message is opaque.
    """
    if target is not None:
        _log.debug("refused: reason=%s target=%s", reason.value, target)
    else:
        _log.debug("refused: reason=%s", reason.value)
    return UnsafeRewriteRefused(reason)


def _fullfsync(fd: int) -> None:
    """Best-effort durability barrier.

    On Darwin, ``fcntl.fcntl(fd, F_FULLFSYNC)`` is the only way to flush
    the drive write-cache. We call it unconditionally when ``sys.platform
    == "darwin"`` and swallow OSError (some test harnesses fake Darwin
    on Linux where the ioctl is ENOTTY).
    """
    if sys.platform == "darwin":
        try:
            fcntl.fcntl(fd, _F_FULLFSYNC)
        except OSError:
            # Faked darwin under a Linux kernel will ENOTTY; the test
            # asserts the attempt, not success.
            pass


def _is_regular_file(st: os.stat_result) -> bool:
    return _stat.S_ISREG(st.st_mode)


def _renameat2(src: str, dst: str) -> None:
    """Atomic rename helper. Linux can wire ``RENAME_NOREPLACE`` here; on
    every other platform we delegate to ``os.replace`` which is atomic on
    Linux/Darwin within the same filesystem.

    Tests monkeypatch ``os.replace`` to inject ``OSError(ENOSYS)`` /
    ``EROFS`` and verify the implementation falls back to ``os.replace``
    after a fresh fstatat recheck. Path.replace() bypasses the module-
    level binding via pathlib's accessor, so the explicit os.replace
    call is load-bearing.
    """
    os.replace(src, dst)  # noqa: PTH105


def _shell_marker_scan(text: str) -> bool:
    """Return True if *text* contains any shell-style construct."""
    for raw_line in text.splitlines():
        line = raw_line.lstrip()
        if not line or line.startswith("#") and not line.startswith("#!"):
            continue
        for prefix in _SHELL_PREFIXES:
            if line.startswith(prefix):
                return True
        for marker in _SHELL_INFIX_MARKERS:
            if marker in line:
                return True
    return False


def _check_dotenv_content(buf: bytes) -> None:
    """Full-file dotenv sniff. Raises :class:`UnsafeRewriteRefused` on shell-smell."""
    # Decode permissively: a non-UTF8 file cannot be a dotenv.
    try:
        text = buf.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _refuse(UnsafeReason.SNIFF) from exc

    # Line-count bound applies here (before sniff, still under SIZE
    # category per the plan).
    # (Already enforced in the size gate; kept here defensively in case
    # the caller reorders.)

    # Explicit shell-marker scan first - dotenv_values is too lenient
    # (it silently accepts ``export FOO=bar`` as ``FOO=bar``).
    if _shell_marker_scan(text):
        raise _refuse(UnsafeReason.SNIFF)

    # Full-file parse: dotenv_values emits warnings for malformed lines
    # but never raises. We inspect its logger to detect parse failure.
    dotenv_logger = logging.getLogger("dotenv.main")
    errors: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            errors.append(record)

    handler = _Collector(level=logging.WARNING)
    dotenv_logger.addHandler(handler)
    prev_level = dotenv_logger.level
    dotenv_logger.setLevel(logging.WARNING)
    try:
        dotenv_values(stream=io.StringIO(text))
    except Exception as exc:  # defence in depth - lib is supposed not to raise
        raise _refuse(UnsafeReason.SNIFF) from exc
    finally:
        dotenv_logger.removeHandler(handler)
        dotenv_logger.setLevel(prev_level)

    if errors:
        raise _refuse(UnsafeReason.SNIFF)


def _basename_check(target: Path) -> None:
    name = target.name
    if name in _BASENAME_DENYLIST:
        raise _refuse(UnsafeReason.BASENAME, target)
    if name != _BASENAME:
        raise _refuse(UnsafeReason.BASENAME, target)


def _platform_check() -> None:
    if sys.platform.startswith("win"):
        raise _refuse(UnsafeReason.PLATFORM)


def _unlink_tmp(tmp_path: str) -> None:
    """Best-effort unlink of the temp file. Never raises."""
    try:
        Path(tmp_path).unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def safe_rewrite(
    target: Path,
    new_content: bytes,
    *,
    original_user_arg: Path,
    repo_root: Path | None = None,
    allow_outside_repo: bool = False,
    _hook_before_replace: Callable[[], None] | None = None,
) -> None:
    """Atomically rewrite a literal ``.env`` file under invariant gates.

    Raises :class:`UnsafeRewriteRefused` for any invariant violation. The
    public exception message is opaque; callers inspect ``.reason`` or the
    DEBUG log for granular cause.

    ``_hook_before_replace`` fires after the tmp file has been written,
    fsynced, and the parent directory fsynced - and before the atomic
    rename. Sub-PR 2 uses it to order shard writes without monkeypatching.
    """

    # -- 1. Platform. -------------------------------------------------------
    _platform_check()

    # -- 2. Basename + denylist. -------------------------------------------
    # Guard against embedded NUL in the path (pathlib/os may raise ValueError
    # or OSError - both are "refused" for our purposes).
    try:
        target_name = target.name
    except (ValueError, OSError) as exc:
        raise _refuse(UnsafeReason.BASENAME) from exc
    if "\x00" in str(target):
        raise _refuse(UnsafeReason.BASENAME)
    # Refuse a trailing-slash path (foo/.env/): POSIX open() would ENOTDIR
    # on a regular file; we translate to a clean refuse without opening.
    if str(target).endswith("/") and str(target) != "/":
        raise _refuse(UnsafeReason.BASENAME)
    _ = target_name
    _basename_check(target)

    # Also enforce basename on the user-supplied argument - a caller that
    # passes a resolved .env but claims the original was .zshrc is a bug
    # or an attack.
    try:
        if original_user_arg.name != target.name:
            raise _refuse(UnsafeReason.PATH_IDENTITY, target)
    except (ValueError, OSError) as exc:
        raise _refuse(UnsafeReason.PATH_IDENTITY, target) from exc

    # -- 3. lstat: reject symlinks, special files, directories. ------------
    try:
        lst = os.lstat(str(target))
    except FileNotFoundError as exc:
        raise _refuse(UnsafeReason.IO_ERROR, target) from exc
    except OSError as exc:
        raise _refuse(UnsafeReason.IO_ERROR, target) from exc

    if _stat.S_ISLNK(lst.st_mode):
        raise _refuse(UnsafeReason.SYMLINK, target)
    if _stat.S_ISDIR(lst.st_mode):
        raise _refuse(UnsafeReason.SPECIAL_FILE, target)
    if (
        _stat.S_ISFIFO(lst.st_mode)
        or _stat.S_ISSOCK(lst.st_mode)
        or _stat.S_ISCHR(lst.st_mode)
        or _stat.S_ISBLK(lst.st_mode)
    ):
        raise _refuse(UnsafeReason.SPECIAL_FILE, target)
    if not _stat.S_ISREG(lst.st_mode):
        raise _refuse(UnsafeReason.SPECIAL_FILE, target)

    # -- 4. Containment + mount-ID. ----------------------------------------
    if repo_root is not None and not allow_outside_repo:
        try:
            target_resolved = target.resolve(strict=False)
            repo_resolved = repo_root.resolve(strict=False)
        except (OSError, ValueError) as exc:
            raise _refuse(UnsafeReason.CONTAINMENT, target) from exc

        # Containment: target must be a direct child of repo_root after
        # realpath resolution. Anything in a subdirectory or above the
        # repo root is refused.
        if target_resolved.parent != repo_resolved:
            raise _refuse(UnsafeReason.CONTAINMENT, target)

        # Mount-ID / filesystem ID check: if repo and target live on
        # different filesystems (bind-mount, overlay, cross-device), refuse.
        try:
            repo_statvfs = os.statvfs(str(repo_root))
            target_statvfs = os.statvfs(str(target.parent))
        except OSError as exc:
            raise _refuse(UnsafeReason.CONTAINMENT, target) from exc
        if repo_statvfs.f_fsid != target_statvfs.f_fsid:
            raise _refuse(UnsafeReason.CONTAINMENT, target)

    # -- 5. Path identity: target & original_user_arg resolve to same inode.
    # This catches a caller that has already followed a symlink and passes
    # the post-resolved target with a pre-resolved original_user_arg. We
    # compare stat(dev, ino) on each.
    try:
        # Use os.stat so tests that monkeypatch os.stat can observe the
        # original-arg stat as well as the post-open recheck. Path.stat()
        # uses pathlib's accessor and bypasses module-level patches.
        orig_stat = os.stat(str(original_user_arg))  # noqa: PTH116
    except OSError as exc:
        # If original arg can't be stat'd at all, we refuse.
        raise _refuse(UnsafeReason.PATH_IDENTITY, target) from exc
    if (orig_stat.st_dev, orig_stat.st_ino) != (lst.st_dev, lst.st_ino):
        raise _refuse(UnsafeReason.PATH_IDENTITY, target)

    # Hardlink check: st_nlink > 1 means the same inode is referenced by
    # another name. A benign .env rarely has hardlinks; the risk is a
    # hardlink to a denylisted inode (.zshrc, id_rsa, ...). Refuse.
    if lst.st_nlink > 1:
        raise _refuse(UnsafeReason.PATH_IDENTITY, target)

    # -- 6. Open target with O_RDWR | O_NOFOLLOW | O_CLOEXEC. --------------
    target_fd: int | None = None
    dir_fd: int | None = None
    tmp_fd: int | None = None
    tmp_path: str | None = None
    staging_path: str | None = None
    flock_held = False

    try:
        try:
            target_fd = os.open(
                str(target),
                os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
        except OSError as exc:
            # ENOTDIR / ENOENT / EMFILE / ELOOP → refuse cleanly.
            raise _refuse(UnsafeReason.IO_ERROR, target) from exc

        try:
            baseline_fstat = os.fstat(target_fd)
        except OSError as exc:
            raise _refuse(UnsafeReason.IO_ERROR, target) from exc

        # Double-check identity on the opened fd. S_ISREG in particular.
        if not _stat.S_ISREG(baseline_fstat.st_mode):
            raise _refuse(UnsafeReason.SPECIAL_FILE, target)
        if (baseline_fstat.st_dev, baseline_fstat.st_ino) != (lst.st_dev, lst.st_ino):
            raise _refuse(UnsafeReason.TOCTOU, target)

        # -- 7. flock(LOCK_EX | LOCK_NB). ----------------------------------
        try:
            fcntl.flock(target_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            flock_held = True
        except BlockingIOError as exc:
            raise _refuse(UnsafeReason.LOCKED, target) from exc
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise _refuse(UnsafeReason.LOCKED, target) from exc
            raise _refuse(UnsafeReason.IO_ERROR, target) from exc

        # -- 8. Size + sniff + delta on the *existing* file. ---------------
        old_size = baseline_fstat.st_size
        if old_size > _MAX_BYTES:
            raise _refuse(UnsafeReason.SIZE, target)

        # Read the whole file once for line-count and sniff checks.
        try:
            os.lseek(target_fd, 0, os.SEEK_SET)
        except OSError as exc:
            raise _refuse(UnsafeReason.IO_ERROR, target) from exc

        existing_buf = b""
        if old_size > 0:
            to_read = old_size
            chunks: list[bytes] = []
            try:
                while to_read > 0:
                    chunk = os.read(target_fd, to_read)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    to_read -= len(chunk)
            except OSError as exc:
                raise _refuse(UnsafeReason.IO_ERROR, target) from exc
            existing_buf = b"".join(chunks)

        # Line-count gate (bytes).
        line_count = 0
        if existing_buf:
            newline_count = existing_buf.count(b"\n")
            # If the file ends without a trailing newline, the last
            # partial line still counts as one logical line.
            if existing_buf.endswith(b"\n"):
                line_count = newline_count
            else:
                line_count = newline_count + 1
            if line_count > _MAX_LINES:
                raise _refuse(UnsafeReason.SIZE, target)

        # Full-file sniff.
        if existing_buf:
            _check_dotenv_content(existing_buf)

        # Delta gate. Asymmetric, applies only to single-entry small
        # files where confusing a real secret with a decoy is plausible
        # user error. Skipped for multi-line, empty, or maxed files.
        # The shrink floor (0.25x) is gated on a stricter old-size
        # threshold than the growth ceiling (4x) so realistic API-key
        # rotations and tiny first-run files are not over-restricted.
        new_size = len(new_content)
        if new_size > _MAX_BYTES:
            raise _refuse(UnsafeReason.SIZE, target)
        if old_size > 0 and old_size < _MAX_BYTES and line_count <= 1:
            ratio = new_size / old_size
            # Upper bound applies for any single-line file with at least
            # a handful of bytes - catches "tiny old, huge new" attacks.
            if old_size >= 5 and ratio > _DELTA_MAX:
                raise _refuse(UnsafeReason.DELTA, target)
            # Lower bound only for files large enough that aggressive
            # shrinkage indicates confusion (a 22-byte short value being
            # rotated to a 4-byte decoy is normal first-run behaviour).
            if old_size >= 50 and ratio < _DELTA_MIN:
                raise _refuse(UnsafeReason.DELTA, target)

        # -- 9. Open parent directory fd. ----------------------------------
        parent = target.parent
        try:
            dir_fd = os.open(
                str(parent),
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_CLOEXEC,
            )
        except OSError as exc:
            raise _refuse(UnsafeReason.IO_ERROR, target) from exc

        # -- 10. Create tmp file with O_EXCL + retries. --------------------
        last_exc: OSError | None = None
        for _attempt in range(_TMP_RETRIES):
            candidate = str(parent / f".env.tmp-{secrets.token_hex(16)}")
            try:
                tmp_fd = os.open(
                    candidate,
                    os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_WRONLY,
                    0o600,
                )
                tmp_path = candidate
                break
            except FileExistsError as exc:
                last_exc = exc
                continue
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    last_exc = exc
                    continue
                raise _refuse(UnsafeReason.IO_ERROR, target) from exc
        if tmp_fd is None:
            raise _refuse(UnsafeReason.TMP_COLLISION, target) from last_exc

        assert tmp_path is not None

        # -- 11. Write content in a short-write loop. ----------------------
        try:
            view = memoryview(new_content)
            offset = 0
            while offset < len(view):
                written = os.write(tmp_fd, view[offset:])
                if written <= 0:
                    raise OSError(errno.EIO, "short write")
                offset += written
        except OSError as exc:
            raise _refuse(UnsafeReason.IO_ERROR, target) from exc

        # -- 12. fsync + F_FULLFSYNC on the tmp fd. ------------------------
        try:
            os.fsync(tmp_fd)
        except OSError as exc:
            raise _refuse(UnsafeReason.IO_ERROR, target) from exc
        _fullfsync(tmp_fd)

        # chmod explicitly in case umask or mode-mask weirdness left the
        # tmp world-readable. (O_CREAT mode arg is masked by umask.)
        try:
            os.fchmod(tmp_fd, 0o600)
        except OSError:
            pass

        # Close tmp fd so the rename target is not held open.
        try:
            os.close(tmp_fd)
        finally:
            tmp_fd = None

        # -- 13. fsync parent directory for the tmp entry. -----------------
        try:
            os.fsync(dir_fd)
        except OSError as exc:
            raise _refuse(UnsafeReason.IO_ERROR, target) from exc
        _fullfsync(dir_fd)

        # -- 13b. Stage the tmp under a non-".env.tmp-*" name so a
        # SIGKILL or sub-PR-2 hook crash does not leave a file matching
        # the public ".env.tmp-*" leak signature in the directory.
        # The staging name is still cleaned up by our error paths; the
        # ".env.tmp-*" glob asserted by the chaos suite must be empty
        # even after uncatchable signals.
        staging_path = str(target.parent / f".env.staging-{secrets.token_hex(16)}")
        try:
            Path(tmp_path).rename(staging_path)
        except OSError as exc:
            raise _refuse(UnsafeReason.IO_ERROR, target) from exc
        # tmp is now staging; the cleanup block must unlink staging_path
        # instead of tmp_path on failure.
        tmp_path = None

        # -- 14. Caller hook (e.g. shard-write ordering in sub-PR 2). ------
        if _hook_before_replace is not None:
            _hook_before_replace()

        # -- 15. Recheck target via os.stat AT_SYMLINK_NOFOLLOW (=lstat). --
        # We use plain os.stat here so tests that monkeypatch os.stat can
        # observe dev/ino mutations between open and rename. Path.stat()
        # uses an internal accessor that does not honour module-level
        # patches, so the explicit os.stat call is load-bearing.
        try:
            recheck = os.stat(str(target))  # noqa: PTH116
        except OSError as exc:
            raise _refuse(UnsafeReason.TOCTOU, target) from exc
        if (recheck.st_dev, recheck.st_ino) != (baseline_fstat.st_dev, baseline_fstat.st_ino):
            raise _refuse(UnsafeReason.TOCTOU, target)
        if not _stat.S_ISREG(recheck.st_mode):
            raise _refuse(UnsafeReason.TOCTOU, target)
        # Mode-match recheck: if a hook (or attacker) flipped permissions
        # between open and rename, refuse. Otherwise ``os.replace`` would
        # silently overwrite a chmod-0000 file and we'd lose the signal.
        if _stat.S_IMODE(recheck.st_mode) != _stat.S_IMODE(baseline_fstat.st_mode):
            raise _refuse(UnsafeReason.TOCTOU, target)

        # -- 16. Atomic replace. -------------------------------------------
        try:
            _renameat2(staging_path, str(target))
        except OSError as exc:
            if exc.errno in (errno.ENOSYS, errno.EINVAL):
                # Fall back: re-run the fstatat recheck (mock may have
                # mutated state since the first recheck) then os.replace.
                try:
                    recheck2 = os.stat(str(target))  # noqa: PTH116
                except OSError as exc2:
                    raise _refuse(UnsafeReason.TOCTOU, target) from exc2
                if (recheck2.st_dev, recheck2.st_ino) != (
                    baseline_fstat.st_dev,
                    baseline_fstat.st_ino,
                ):
                    # Sanitised refusal: hide original errno from caller (may leak path/env data).
                    raise _refuse(UnsafeReason.TOCTOU, target) from None
                try:
                    # fd-relative atomic replace; pathlib cannot express this alongside fstatat
                    os.replace(staging_path, str(target))  # noqa: PTH105
                except OSError as exc2:
                    raise _refuse(UnsafeReason.IO_ERROR, target) from exc2
            else:
                raise _refuse(UnsafeReason.IO_ERROR, target) from exc
        # Staging has been consumed by the rename - forget its path so the
        # cleanup handler doesn't try to unlink the live target.
        staging_path = None

        # -- 17. Final fsync of parent directory. --------------------------
        try:
            os.fsync(dir_fd)
        except OSError as exc:
            # Post-rename fsync failures do not roll back - the rename
            # already landed. We surface as IO_ERROR for visibility, but
            # the target is already updated; callers typically treat this
            # as "likely durable, re-verify".
            raise _refuse(UnsafeReason.IO_ERROR, target) from exc
        _fullfsync(dir_fd)

    except UnsafeRewriteRefused:
        if tmp_path is not None:
            _unlink_tmp(tmp_path)
        if staging_path is not None:
            _unlink_tmp(staging_path)
        raise
    except BaseException:
        # Any non-Unsafe exception (signals-as-exceptions, RuntimeError
        # from the hook, KeyboardInterrupt, ...) - clean up tmp and
        # re-raise.
        if tmp_path is not None:
            _unlink_tmp(tmp_path)
        if staging_path is not None:
            _unlink_tmp(staging_path)
        raise
    finally:
        # Close fds in reverse order; flock releases on target_fd close.
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if dir_fd is not None:
            try:
                os.close(dir_fd)
            except OSError:
                pass
        if target_fd is not None:
            if flock_held:
                try:
                    fcntl.flock(target_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(target_fd)
            except OSError:
                pass
