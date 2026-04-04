"""Structured error codes (WRTLS-NNN) and exception type."""

from __future__ import annotations

import logging
from enum import IntEnum

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
    REVOKE_NOT_FOUND = 109
    UNKNOWN = 199


class WorthlessError(Exception):
    """CLI-layer exception carrying a structured error code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(str(self))

    def __str__(self) -> str:  # noqa: D105
        return f"WRTLS-{self.code.value}: {self.message}"


def sanitize_exception(exc: Exception, *, generic: str = "an internal error occurred") -> str:
    """Return a user-safe single-line description for *exc*.

    Always returns the *generic* message to avoid leaking file paths, DB
    paths, stack traces, or library internals.  The original exception is
    logged at DEBUG level so operators can diagnose with ``--verbose`` or
    log-level configuration.
    """
    logger.debug("Sanitized exception (%s): %s", type(exc).__name__, exc)
    return generic
