"""Tests for error message sanitization (WOR-96).

Ensures no error path leaks stack traces, file paths, DB paths,
or internal state to end users.
"""

from __future__ import annotations

import json
import re


from worthless.cli.errors import ErrorCode, WorthlessError, sanitize_exception
from worthless.proxy.app import _sanitize_upstream_error
from worthless.proxy.errors import (
    auth_error_response,
    gateway_error_response,
    rate_limit_error_response,
    spend_cap_error_response,
)


# ---------------------------------------------------------------------------
# Patterns that must NEVER appear in user-facing error output
# ---------------------------------------------------------------------------

_LEAK_PATTERNS = [
    re.compile(r"/[a-zA-Z0-9_./-]{3,}\.(db|key|sqlite|json|py)"),  # Unix file paths
    re.compile(r"[A-Z]:\\"),  # Windows paths
    re.compile(r"Traceback \(most recent call last\)"),  # Stack traces
    re.compile(r"File \""),  # Stack trace frames
    re.compile(r"line \d+, in "),  # Stack trace frames
    re.compile(r"\.worthless/"),  # Home directory internals
    re.compile(r"shard_a/"),  # Shard directory
    re.compile(r"fernet"),  # Fernet key references (case-insensitive later)
]


def _contains_leak(text: str) -> str | None:
    """Return the first leak pattern matched, or None."""
    for pattern in _LEAK_PATTERNS:
        m = pattern.search(text)
        if m:
            return f"pattern {pattern.pattern!r} matched: {m.group()!r}"
    # Case-insensitive check for fernet
    if "fernet" in text.lower():
        return "contains 'fernet'"
    return None


# ---------------------------------------------------------------------------
# sanitize_exception unit tests
# ---------------------------------------------------------------------------


class TestSanitizeException:
    """Tests for the sanitize_exception helper."""

    def test_generic_message_returned(self):
        exc = Exception("something failed at /home/user/.worthless/worthless.db")
        result = sanitize_exception(exc)
        assert result == "an internal error occurred"

    def test_custom_generic_message(self):
        exc = OSError("Permission denied: /etc/shadow")
        result = sanitize_exception(exc, generic="storage operation failed")
        assert result == "storage operation failed"

    def test_no_file_paths_in_output(self):
        exc = FileNotFoundError(
            "[Errno 2] No such file: '/home/user/.worthless/shard_a/openai-abc'"
        )
        result = sanitize_exception(exc)
        assert _contains_leak(result) is None

    def test_no_db_paths_in_output(self):
        exc = Exception("database is locked: /tmp/worthless.db")
        result = sanitize_exception(exc)
        assert _contains_leak(result) is None

    def test_no_traceback_in_output(self):
        exc = RuntimeError('Traceback (most recent call last):\n  File "/app/main.py"')
        result = sanitize_exception(exc)
        assert _contains_leak(result) is None

    def test_aiosqlite_error_sanitized(self):
        exc = Exception("no such table: shards (in /home/user/.worthless/worthless.db)")
        result = sanitize_exception(exc)
        assert _contains_leak(result) is None

    def test_cryptography_error_sanitized(self):
        exc = Exception("Fernet key must be 32 url-safe base64-encoded bytes")
        result = sanitize_exception(exc)
        assert "fernet" not in result.lower()

    def test_returns_string(self):
        exc = ValueError("bad value")
        result = sanitize_exception(exc)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# WorthlessError format tests
# ---------------------------------------------------------------------------


class TestWorthlessErrorFormat:
    """WorthlessError should produce structured WRTLS-NNN codes, not raw exceptions."""

    def test_error_code_format(self):
        err = WorthlessError(ErrorCode.UNKNOWN, "an internal error occurred")
        text = str(err)
        assert text == "WRTLS-199: an internal error occurred"
        assert _contains_leak(text) is None

    def test_all_error_codes_produce_clean_output(self):
        for code in ErrorCode:
            err = WorthlessError(code, "test message")
            text = str(err)
            assert text.startswith("WRTLS-")
            assert _contains_leak(text) is None


# ---------------------------------------------------------------------------
# Proxy error response tests
# ---------------------------------------------------------------------------


class TestProxyErrorSanitization:
    """Proxy error responses must not leak internal state."""

    def test_auth_error_no_leak(self):
        for provider in ("openai", "anthropic"):
            resp = auth_error_response(provider)
            body = resp.body.decode()
            assert _contains_leak(body) is None
            # Should not contain any path-like strings
            assert "/.worthless" not in body
            assert "shard" not in body.lower()

    def test_gateway_error_no_leak(self):
        for status, msg in [(502, "bad gateway"), (504, "gateway timeout")]:
            resp = gateway_error_response(status, msg)
            body = resp.body.decode()
            assert _contains_leak(body) is None

    def test_spend_cap_error_no_leak(self):
        resp = spend_cap_error_response()
        body = resp.body.decode()
        assert _contains_leak(body) is None

    def test_rate_limit_error_no_leak(self):
        resp = rate_limit_error_response(60)
        body = resp.body.decode()
        assert _contains_leak(body) is None

    def test_sanitize_upstream_error_strips_details(self):
        # Simulate an upstream error that contains internal details
        upstream_body = json.dumps(
            {
                "error": {
                    "message": "Invalid API key: sk-proj-abc123... (from /home/user/.env)",
                    "type": "authentication_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
            }
        ).encode()

        sanitized = _sanitize_upstream_error(401, upstream_body, "openai")
        parsed = json.loads(sanitized)
        assert parsed["error"]["message"] == "upstream provider error"
        assert "sk-proj" not in sanitized.decode()
        assert ".env" not in sanitized.decode()

    def test_sanitize_upstream_error_handles_malformed_json(self):
        sanitized = _sanitize_upstream_error(500, b"not json at all", "openai")
        parsed = json.loads(sanitized)
        assert parsed["error"]["message"] == "upstream provider error"

    def test_sanitize_upstream_error_anthropic_format(self):
        upstream_body = json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "overloaded_error",
                    "message": "Internal server error at shard /data/node-3",
                },
            }
        ).encode()

        sanitized = _sanitize_upstream_error(529, upstream_body, "anthropic")
        parsed = json.loads(sanitized)
        assert parsed["error"]["message"] == "upstream provider error"
        assert parsed["error"]["type"] == "overloaded_error"
        assert "/data/" not in sanitized.decode()


# ---------------------------------------------------------------------------
# CLI catch-all handler tests
# ---------------------------------------------------------------------------


class TestCLICatchAllHandlers:
    """Verify that CLI catch-all Exception handlers use sanitize_exception."""

    def test_lock_command_sanitizes_exceptions(self):
        """Lock command's catch-all should not leak raw exception text."""
        # Simulate what the catch-all handler does
        exc = OSError("[Errno 13] Permission denied: '/home/user/.worthless/worthless.db'")
        err = WorthlessError(ErrorCode.UNKNOWN, sanitize_exception(exc))
        text = str(err)
        assert "/home/user" not in text
        assert ".worthless" not in text
        assert "worthless.db" not in text

    def test_storage_exception_sanitized(self):
        """Storage exceptions must not leak DB paths."""
        exc = Exception("unable to open database file: /var/data/worthless.db")
        err = WorthlessError(
            ErrorCode.SHARD_STORAGE_FAILED,
            sanitize_exception(exc, generic="storage operation failed"),
        )
        text = str(err)
        assert "/var/data" not in text
        assert "worthless.db" not in text
        assert "storage operation failed" in text

    def test_bootstrap_exception_sanitized(self):
        """Bootstrap failures must not leak home directory paths."""
        exc = OSError("[Errno 13] Permission denied: '/home/user/.worthless'")
        err = WorthlessError(
            ErrorCode.BOOTSTRAP_FAILED,
            sanitize_exception(exc, generic="failed to initialise home directory"),
        )
        text = str(err)
        assert "/home/user" not in text
        assert "failed to initialise home directory" in text


# ---------------------------------------------------------------------------
# Typer pretty_exceptions_enable=False verification
# ---------------------------------------------------------------------------


class TestTyperConfiguration:
    """Verify Typer is configured to suppress pretty exceptions."""

    def test_pretty_exceptions_disabled(self):
        from worthless.cli.app import app

        assert app.pretty_exceptions_enable is False, (
            "pretty_exceptions_enable must be False to prevent stack traces in production"
        )
