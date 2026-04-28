"""Structured error codes (WRTLS-NNN) and exception type."""

from __future__ import annotations

import functools
import logging
import sys
import traceback
from enum import Enum, IntEnum

logger = logging.getLogger(__name__)


class ErrorCode(IntEnum):
    """Numeric codes for every anticipated CLI failure mode."""

    BOOTSTRAP_FAILED = 100
    ENV_NOT_FOUND = 101
    KEY_NOT_FOUND = 102
    SHARD_STORAGE_FAILED = 103
    PROXY_UNREACHABLE = 104
    LOCK_IN_PROGRESS = 105
    SCAN_ERROR = 106
    PORT_IN_USE = 107
    WRAP_CHILD_FAILED = 108
    PROXY_NOT_RUNNING = 109
    PLATFORM_UNSUPPORTED = 110
    UNSAFE_REWRITE_REFUSED = 111
    SIDECAR_CRASHED = 112
    SIDECAR_NOT_READY = 113
    UNKNOWN = 199


class UnsafeReason(str, Enum):
    """Internal granular reason for ``UnsafeRewriteRefused``.

    Exposed on :attr:`UnsafeRewriteRefused.reason` and logged at DEBUG level.
    Never appears in the user-facing message.
    """

    PLATFORM = "platform"
    BASENAME = "basename"
    PATH_IDENTITY = "path_identity"
    SPECIAL_FILE = "special_file"
    SYMLINK = "symlink"
    CONTAINMENT = "containment"
    SIZE = "size"
    SNIFF = "sniff"
    DELTA = "delta"
    TOCTOU = "toctou"
    TMP_COLLISION = "tmp_collision"
    IO_ERROR = "io_error"
    LOCKED = "locked"


_UNSAFE_REWRITE_PUBLIC_MESSAGE = "unsafe rewrite refused"


class WorthlessError(Exception):
    """CLI-layer exception carrying a structured error code."""

    def __init__(self, code: ErrorCode, message: str, *, exit_code: int = 1) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        super().__init__(str(self))

    def __str__(self) -> str:  # noqa: D105
        return f"WRTLS-{self.code.value}: {self.message}"


class UnsafeRewriteRefused(WorthlessError):
    """Raised by ``safe_rewrite`` when any invariant refuses a rewrite.

    The public message is intentionally opaque. The granular cause is
    available via :attr:`reason` and is logged at DEBUG level. Neither
    absolute paths nor environment data ever appear in ``str(exc)``.
    """

    def __init__(self, reason: UnsafeReason) -> None:
        self.reason = reason
        super().__init__(
            ErrorCode.UNSAFE_REWRITE_REFUSED,
            _UNSAFE_REWRITE_PUBLIC_MESSAGE,
        )
        logger.debug("UnsafeRewriteRefused: reason=%s", reason.value)


def sanitize_exception(exc: Exception, *, generic: str = "an internal error occurred") -> str:
    """Return a user-safe single-line description for *exc*.

    Always returns the *generic* message to avoid leaking file paths, DB
    paths, stack traces, or library internals.  The original exception is
    logged at DEBUG level so operators can diagnose with ``--verbose`` or
    log-level configuration.
    """
    logger.debug("Sanitized exception: %r", exc)
    return generic


# ---------------------------------------------------------------------------
# Debug mode flag (toggled by --debug on the root CLI callback)
# ---------------------------------------------------------------------------

_debug: bool = False


def set_debug(enabled: bool) -> None:
    """Enable or disable debug mode (full tracebacks on error)."""
    global _debug  # noqa: PLW0603
    _debug = enabled


# ---------------------------------------------------------------------------
# @error_boundary — unified error handling for CLI commands
# ---------------------------------------------------------------------------


def error_boundary(fn=None, *, exit_code: int = 1):  # noqa: ANN001, ANN201
    """Decorator that catches exceptions and prints structured WRTLS errors.

    * ``WorthlessError`` → print code + message, exit with ``exc.exit_code``.
    * ``typer.Exit`` → re-raise as-is (already handled).
    * Any other ``Exception`` → wrap in WRTLS-199, exit with *exit_code*.
    * In ``--debug`` mode, full tracebacks are printed to stderr.

    Can be used bare (``@error_boundary``) or with an explicit fallback
    exit code (``@error_boundary(exit_code=2)``).
    """
    # Deferred: errors.py is imported before typer-based modules load.
    # TODO: break cycle by moving error_boundary to its own module.
    import typer

    def decorator(func):  # noqa: ANN001, ANN202
        @functools.wraps(func)
        def wrapper(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            try:
                return func(*args, **kwargs)
            except typer.Exit:
                raise
            except WorthlessError as exc:
                if _debug:
                    traceback.print_exc(file=sys.stderr)
                else:
                    # Deferred: console imports errors, so we can't import at top.
                    from worthless.cli.console import get_console

                    get_console().print_error(exc)
                raise typer.Exit(code=exc.exit_code) from exc
            except Exception as exc:
                if _debug:
                    traceback.print_exc(file=sys.stderr)
                else:
                    from worthless.cli.console import get_console

                    safe_msg = sanitize_exception(exc)
                    get_console().print_error(WorthlessError(ErrorCode.UNKNOWN, safe_msg))
                raise typer.Exit(code=exit_code) from exc

        return wrapper

    if fn is not None:
        # Bare @error_boundary usage
        return decorator(fn)
    # Parameterized @error_boundary(exit_code=2) usage
    return decorator
