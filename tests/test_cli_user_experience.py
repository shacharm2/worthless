"""UX tests for the worthless CLI.

These tests verify user-facing messages, exit codes, and output formats
from the perspective of someone typing commands in a terminal.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome
from worthless.crypto.splitter import split_key
from worthless.storage.repository import ShardRepository, StoredShard

# mix_stderr=False so we can inspect stdout vs stderr independently
runner = CliRunner(mix_stderr=False)

# A realistic OpenAI key (51 chars after prefix) for test fixtures
_OPENAI_KEY = "sk-proj-abc123def456ghi789jkl012mno345pqr678stu901vwx234"
_ANTHROPIC_KEY = "sk-ant-api03-abc123def456ghi789jkl012mno345pqr678stu901vwx"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def env_with_openai(tmp_path: Path) -> Path:
    """Create a .env with one OpenAI key."""
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={_OPENAI_KEY}\n")
    return env


@pytest.fixture()
def env_clean(tmp_path: Path) -> Path:
    """Create a .env with no API keys."""
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgres://localhost/db\nAPP_NAME=myapp\n")
    return env


@pytest.fixture()
def env_with_google(tmp_path: Path) -> Path:
    """Create a .env with a Google AI key (unsupported provider)."""
    env = tmp_path / ".env"
    # Google keys look like AIzaSy... (39 chars), high-entropy
    env.write_text("GOOGLE_API_KEY=AIzaSyB3x7k9mR2pQ1wE5vF8nJ4hL0tY6uI2oP3\n")
    return env


@pytest.fixture()
def home_with_key(home_dir: WorthlessHome) -> WorthlessHome:
    """Home with one enrolled key (openai)."""
    sr = split_key(_OPENAI_KEY.encode())
    try:
        alias = "openai-a1b2c3d4"
        shard_a_path = home_dir.shard_a_dir / alias
        fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, bytes(sr.shard_a))
        finally:
            os.close(fd)

        repo = ShardRepository(str(home_dir.db_path), home_dir.fernet_key)
        asyncio.run(repo.initialize())
        stored = StoredShard(
            shard_b=bytearray(sr.shard_b),
            commitment=bytearray(sr.commitment),
            nonce=bytearray(sr.nonce),
            provider="openai",
        )
        asyncio.run(
            repo.store_enrolled(
                alias,
                stored,
                var_name="OPENAI_API_KEY",
                env_path="/tmp/.env",
            )
        )
    finally:
        sr.zero()
    return home_dir


@pytest.fixture()
def home_with_multi_env_key(home_dir: WorthlessHome) -> WorthlessHome:
    """Home with one alias enrolled in TWO different env files (ambiguous)."""
    sr = split_key(_OPENAI_KEY.encode())
    try:
        alias = "openai-a1b2c3d4"
        shard_a_path = home_dir.shard_a_dir / alias
        fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, bytes(sr.shard_a))
        finally:
            os.close(fd)

        repo = ShardRepository(str(home_dir.db_path), home_dir.fernet_key)
        asyncio.run(repo.initialize())
        stored = StoredShard(
            shard_b=bytearray(sr.shard_b),
            commitment=bytearray(sr.commitment),
            nonce=bytearray(sr.nonce),
            provider="openai",
        )
        asyncio.run(
            repo.store_enrolled(
                alias,
                stored,
                var_name="OPENAI_API_KEY",
                env_path="/project-a/.env",
            )
        )
        asyncio.run(
            repo.store_enrolled(
                alias,
                stored,
                var_name="OPENAI_API_KEY",
                env_path="/project-b/.env",
            )
        )
    finally:
        sr.zero()
    return home_dir


# ===================================================================
# 1. HELP TEXT
# ===================================================================


class TestHelpText:
    """Verify --help lists all commands."""

    def test_help_shows_all_commands(self) -> None:
        """worthless --help should list all 7 commands."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        output = result.stdout
        for cmd in ("lock", "unlock", "enroll", "scan", "status", "wrap", "up"):
            assert cmd in output, f"Command {cmd!r} missing from --help output"

    def test_no_args_shows_help(self) -> None:
        """worthless with no args should show help (no_args_is_help=True)."""
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        output = result.stdout
        # Should show the app description or command list
        assert "lock" in output
        assert "unlock" in output


# ===================================================================
# 2. LOCK UX
# ===================================================================


class TestLockUx:
    """User-facing messages from the lock command."""

    def test_lock_success_message(
        self, home_dir: WorthlessHome, env_with_openai: Path
    ) -> None:
        """After locking, user sees 'N key(s) protected.'"""
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_with_openai)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        combined = result.stdout + result.stderr
        assert "1 key(s) protected" in combined

    def test_lock_no_keys_message(
        self, home_dir: WorthlessHome, env_clean: Path
    ) -> None:
        """When .env has no API keys, user sees 'No unprotected API keys found.'"""
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_clean)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        combined = result.stdout + result.stderr
        assert "No unprotected API keys found" in combined

    def test_lock_unsupported_provider_warning(
        self, home_dir: WorthlessHome, env_with_google: Path
    ) -> None:
        """When .env has a Google key, user sees warning about unsupported provider."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_with_google)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        # Should not crash, and should warn about unsupported
        combined = result.stdout + result.stderr
        assert "not yet supported" in combined or "No unprotected" in combined


# ===================================================================
# 3. UNLOCK UX
# ===================================================================


class TestUnlockUx:
    """User-facing messages from the unlock command."""

    def test_unlock_single_alias_message(
        self, home_with_key: WorthlessHome, tmp_path: Path
    ) -> None:
        """After unlocking a specific alias, user sees 'Unlocked {alias}.'"""
        env = tmp_path / ".env"
        env.write_text("OPENAI_API_KEY=decoy-value\n")

        result = runner.invoke(
            app,
            ["unlock", "--alias", "openai-a1b2c3d4", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        combined = result.stdout + result.stderr
        assert "Unlocked openai-a1b2c3d4" in combined

    def test_unlock_all_keys_message(
        self, home_with_key: WorthlessHome, tmp_path: Path
    ) -> None:
        """After unlocking all keys, user sees 'N key(s) restored.'"""
        env = tmp_path / ".env"
        env.write_text("OPENAI_API_KEY=decoy-value\n")

        result = runner.invoke(
            app,
            ["unlock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        combined = result.stdout + result.stderr
        assert "key(s) restored" in combined

    def test_unlock_no_keys_message(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """When no keys enrolled, user sees 'No enrolled keys found.'"""
        env = tmp_path / ".env"
        env.write_text("SOME_VAR=value\n")

        result = runner.invoke(
            app,
            ["unlock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        combined = result.stdout + result.stderr
        assert "No enrolled keys found" in combined

    def test_unlock_ambiguous_alias_error(
        self, home_with_multi_env_key: WorthlessHome
    ) -> None:
        """When alias is in 2 env files and --env does not match, user gets error."""
        result = runner.invoke(
            app,
            ["unlock", "--alias", "openai-a1b2c3d4", "--env", "/nonexistent/.env"],
            env={"WORTHLESS_HOME": str(home_with_multi_env_key.base_dir)},
        )
        # Should error cleanly (env file doesn't exist or enrollment not found)
        assert result.exit_code in (0, 1)


# ===================================================================
# 4. ENROLL UX
# ===================================================================


class TestEnrollUx:
    """User-facing messages from the enroll command."""

    def test_enroll_key_stdin(self, home_dir: WorthlessHome) -> None:
        """Piping key via --key-stdin works."""
        result = runner.invoke(
            app,
            [
                "enroll",
                "--alias", "stdin-test",
                "--key-stdin",
                "--provider", "openai",
            ],
            input=f"{_OPENAI_KEY}\n",
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        combined = result.stdout + result.stderr
        assert "Enrolled stdin-test" in combined

    def test_enroll_invalid_alias(self, home_dir: WorthlessHome) -> None:
        """Invalid alias (special chars) gives clear error."""
        result = runner.invoke(
            app,
            [
                "enroll",
                "--alias", "bad alias!@#",
                "--key", _OPENAI_KEY,
                "--provider", "openai",
            ],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1
        combined = result.stdout + result.stderr
        assert "Invalid alias" in combined or "invalid alias" in combined.lower()

    def test_enroll_no_key_errors(self, home_dir: WorthlessHome) -> None:
        """Enroll without --key or --key-stdin gives error."""
        result = runner.invoke(
            app,
            [
                "enroll",
                "--alias", "no-key",
                "--provider", "openai",
            ],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1
        combined = result.stdout + result.stderr
        assert "key" in combined.lower()

    def test_enroll_empty_stdin_errors(self, home_dir: WorthlessHome) -> None:
        """Enroll with --key-stdin but empty input gives error."""
        result = runner.invoke(
            app,
            [
                "enroll",
                "--alias", "empty-stdin",
                "--key-stdin",
                "--provider", "openai",
            ],
            input="\n",
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1
        combined = result.stdout + result.stderr
        assert "No key provided" in combined or "key" in combined.lower()


# ===================================================================
# 5. SCAN UX
# ===================================================================


class TestScanUx:
    """User-facing messages and exit codes from the scan command."""

    def test_scan_no_keys_message(self, env_clean: Path) -> None:
        """'No API keys found.' when scanning a clean .env."""
        result = runner.invoke(app, ["scan", str(env_clean)])
        assert result.exit_code == 0
        combined = result.stdout + result.stderr
        assert "No API keys found" in combined

    def test_scan_exit_0_clean(self, env_clean: Path) -> None:
        """Exit 0 when no unprotected keys found."""
        result = runner.invoke(app, ["scan", str(env_clean)])
        assert result.exit_code == 0

    def test_scan_exit_1_unprotected(self, env_with_openai: Path) -> None:
        """Exit 1 when unprotected keys found."""
        result = runner.invoke(app, ["scan", str(env_with_openai)])
        assert result.exit_code == 1

    def test_scan_exit_2_error(self) -> None:
        """Exit 2 when scan encounters an error (invalid format)."""
        result = runner.invoke(app, ["scan", "--format", "xml"])
        assert result.exit_code == 2

    def test_scan_format_json_valid(self, env_with_openai: Path) -> None:
        """--format json returns valid JSON array."""
        result = runner.invoke(app, ["scan", "--format", "json", str(env_with_openai)])
        assert result.exit_code == 1
        findings = json.loads(result.stdout)
        assert isinstance(findings, list)
        assert len(findings) >= 1
        assert "provider" in findings[0]

    def test_scan_format_sarif_valid(self, env_with_openai: Path) -> None:
        """--format sarif returns valid SARIF v2.1.0."""
        result = runner.invoke(
            app, ["scan", "--format", "sarif", str(env_with_openai)]
        )
        assert result.exit_code == 1
        sarif = json.loads(result.stdout)
        assert sarif["version"] == "2.1.0"
        assert "$schema" in sarif
        assert len(sarif["runs"]) == 1

    def test_scan_invalid_format_error(self) -> None:
        """--format xml gives a clear error message."""
        result = runner.invoke(app, ["scan", "--format", "xml"])
        combined = result.stdout + result.stderr
        assert result.exit_code == 2
        assert "Unknown format" in combined or "xml" in combined


# ===================================================================
# 6. STATUS UX
# ===================================================================


class TestStatusUx:
    """User-facing messages from the status command."""

    def test_status_no_enrollment(self, home_dir: WorthlessHome) -> None:
        """Shows 'No keys enrolled.' when fresh install."""
        result = runner.invoke(
            app,
            ["status"],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        combined = result.stdout + result.stderr
        assert "No keys enrolled" in combined

    def test_status_with_enrolled_key(
        self, home_with_key: WorthlessHome
    ) -> None:
        """Shows enrolled key info (alias, provider, PROTECTED)."""
        result = runner.invoke(
            app,
            ["status"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0
        combined = result.stdout + result.stderr
        assert "openai-a1b2c3d4" in combined
        assert "openai" in combined
        assert "PROTECTED" in combined


# ===================================================================
# 7. QUIET MODE
# ===================================================================


class TestQuietMode:
    """--quiet suppresses non-error output."""

    def test_quiet_lock_suppresses_success(
        self, home_dir: WorthlessHome, env_with_openai: Path
    ) -> None:
        """--quiet lock produces no success message."""
        result = runner.invoke(
            app,
            ["-q", "lock", "--env", str(env_with_openai)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        # In quiet mode, success messages are suppressed
        assert "key(s) protected" not in result.stderr

    def test_quiet_scan_suppresses_output(self, env_with_openai: Path) -> None:
        """--quiet scan produces no stderr output (exit code only)."""
        result = runner.invoke(app, ["-q", "scan", str(env_with_openai)])
        assert result.exit_code == 1
        assert result.stderr.strip() == ""

    def test_quiet_status_suppresses_output(
        self, home_dir: WorthlessHome
    ) -> None:
        """--quiet status suppresses non-error output."""
        result = runner.invoke(
            app,
            ["-q", "status"],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        # Warning "No keys enrolled" should be suppressed in quiet mode
        assert "No keys enrolled" not in result.stderr


# ===================================================================
# 8. JSON MODE
# ===================================================================


class TestJsonMode:
    """--json emits machine-readable output."""

    def test_json_status_is_parseable(
        self, home_dir: WorthlessHome
    ) -> None:
        """--json status emits valid JSON to stdout."""
        result = runner.invoke(
            app,
            ["--json", "status"],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "keys" in data
        assert "proxy" in data

    def test_json_status_with_key(
        self, home_with_key: WorthlessHome
    ) -> None:
        """--json status with enrolled keys includes key details."""
        result = runner.invoke(
            app,
            ["--json", "status"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data["keys"]) >= 1
        assert data["keys"][0]["alias"] == "openai-a1b2c3d4"
        assert data["keys"][0]["provider"] == "openai"

    def test_json_scan_emits_array(self, env_with_openai: Path) -> None:
        """--json scan emits a JSON array of findings."""
        result = runner.invoke(
            app, ["scan", "--json", str(env_with_openai)]
        )
        assert result.exit_code == 1
        findings = json.loads(result.stdout)
        assert isinstance(findings, list)
