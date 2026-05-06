"""Embedded ``SKILL.md`` install/uninstall for OpenClaw.

We own ``~/.openclaw/workspace/skills/worthless/`` (locked decision L3)
and overwrite stale content. Installs are stage-then-rename so a crash
mid-copy leaves the user with either the old folder or no folder — never
half-written state.

Symlinks are refused on both install and uninstall (F34): an attacker
who can plant a link could redirect a privileged write or sweep an
unrelated directory on unlock.

Spec: ``.claude/plans/graceful-dreaming-reef.md`` §"Public API contracts
for Phase 2.a" / ``worthless.openclaw.skill`` and failure-mode rows
F30, F31, F33, F34, F35.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import tempfile
from importlib import (
    resources,
)  # nosemgrep: python.lang.compatibility.python37.python37-compatibility-importlib2  # noqa: E501 -- worthless requires Python 3.10+; importlib.resources is stdlib
from pathlib import Path

from worthless.openclaw.errors import (
    OpenclawErrorCode,
    OpenclawIntegrationError,
)

_SKILL_PACKAGE = "worthless.openclaw.skill_assets"
_SKILL_FILE = "SKILL.md"
_SKILL_DIR_NAME = "worthless"
_VERSION_LINE = re.compile(r"^Version:\s*(\S+)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Asset access
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _read_skill_asset() -> str:
    """Return the embedded ``SKILL.md`` body as a UTF-8 string.

    ``importlib.resources.files()`` is Python 3.9+ and avoids the legacy
    pkg_resources caching issues called out in risk register R5. The
    cache is process-lifetime (the asset is embedded and immutable).
    Tests that monkeypatch this function should call ``_read_skill_asset.cache_clear()``.
    """
    return resources.files(_SKILL_PACKAGE).joinpath(_SKILL_FILE).read_text(encoding="utf-8")


def current_version() -> str:
    """Return the version string declared in the embedded ``SKILL.md``.

    Parses the first ``Version: <token>`` line. Phase 3 will replace the
    body with the real skill content; the parsing contract stays.
    """
    body = _read_skill_asset()
    match = _VERSION_LINE.search(body)
    if not match:
        # Defensive: a future SKILL.md without a Version line is a bug we
        # want to catch loudly rather than silently fall back.
        raise OpenclawIntegrationError(
            OpenclawErrorCode.SKILL_INSTALL_FAILED,
            "embedded SKILL.md is missing a Version: line",
        )
    return match.group(1)


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------


def _refuse_if_symlink(path: Path) -> None:
    """Raise SYMLINK_REFUSED if ``path`` is a symlink.

    Uses ``lstat`` semantics via ``Path.is_symlink`` so a dangling link
    is still caught. Canonicalize via ``Path.resolve`` (F35) for the
    final on-disk identity check elsewhere.
    """
    if path.is_symlink():
        raise OpenclawIntegrationError(
            OpenclawErrorCode.SYMLINK_REFUSED,
            f"refusing to follow symlink at {path}",
        )


def install(target_dir: Path) -> Path:
    """Install the embedded skill folder at ``target_dir/worthless/``.

    Stage into ``target_dir/.worthless.tmp.<pid>/``, then atomic-rename
    over any existing folder. Cleans up the staging dir on any failure.

    Returns the resolved final path. Raises :class:`OpenclawIntegrationError`
    on hard refusals (e.g. symlink at the destination, missing version).
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    final = target_dir / _SKILL_DIR_NAME
    _refuse_if_symlink(final)

    # ``staging`` is bound INSIDE the try so a mkdtemp failure
    # (target_dir not writable, ENOSPC) wraps cleanly as
    # SKILL_INSTALL_FAILED instead of leaking a raw OSError to callers.
    staging: Path | None = None
    try:
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".worthless.tmp.{os.getpid()}.",
                dir=str(target_dir),
            )
        )
        # Copy every embedded asset file into the staging dir. Using the
        # importlib.resources contents traversal keeps this content-agnostic
        # (R10) — Phase 3 can add files without us touching this code.
        package_root = resources.files(_SKILL_PACKAGE)
        for entry in package_root.iterdir():
            name = entry.name
            if name == "__init__.py" or name.startswith("__pycache__"):
                continue
            if not entry.is_file():
                continue
            (staging / name).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")

        # ``rmtree(ignore_errors=True)`` skips the prior ``exists()`` stat:
        # we own ``final`` per L3 and we're about to overwrite it anyway.
        shutil.rmtree(final, ignore_errors=True)

        # ``os.replace`` (not ``Path.replace``) is patchable at the module
        # level so failure-injection tests can simulate disk-full / EACCES.
        os.replace(staging, final)  # noqa: PTH105
        staging = None  # replace consumed it
    except Exception as exc:
        # Any failure (mkdtemp failure, EACCES, ENOSPC, simulated rename
        # failures) is wrapped as SKILL_INSTALL_FAILED so callers in
        # apply_lock() can surface it as a structured event without
        # leaking raw OSError types into --json output. Always clean up
        # the staging dir (F33) — guarded since mkdtemp may not have run.
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, OpenclawIntegrationError):
            raise
        raise OpenclawIntegrationError(
            OpenclawErrorCode.SKILL_INSTALL_FAILED,
            f"failed to install skill into {final}: {exc}",
        ) from exc

    return final.resolve()


def uninstall(target_dir: Path) -> bool:
    """Remove ``target_dir/worthless/`` if present. Returns True on removal.

    Tolerant: a missing target_dir or worthless/ subfolder returns False
    without error — supports doctor/unlock retries on partially-installed
    hosts (F-XS-44).

    Refuses to follow a symlink at the destination (F34).
    """
    final = target_dir / _SKILL_DIR_NAME
    # One ``lstat`` decides the whole branch: symlink → refuse;
    # missing → no-op; real dir → remove. Saves three pre-stats.
    try:
        st_mode = final.lstat().st_mode
    except FileNotFoundError:
        return False

    import stat as _stat

    if _stat.S_ISLNK(st_mode):
        raise OpenclawIntegrationError(
            OpenclawErrorCode.SYMLINK_REFUSED,
            f"refusing to follow symlink at {final}",
        )

    shutil.rmtree(final)
    return True
