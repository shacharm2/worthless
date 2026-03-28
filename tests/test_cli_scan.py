"""Tests for the scan CLI command."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome, ensure_home

runner = CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def home_dir(tmp_path: Path) -> WorthlessHome:
    """Bootstrap a fresh WorthlessHome in tmp_path."""
    return ensure_home(tmp_path / ".worthless")


@pytest.fixture()
def env_with_real_key(tmp_path: Path) -> Path:
    """Create a .env with a real (unprotected) OpenAI key."""
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-proj-abc123def456ghi789jkl012mno345pqr678stu901vwx234\n")
    return env


@pytest.fixture()
def env_clean(tmp_path: Path) -> Path:
    """Create a .env with no API keys."""
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgres://localhost/db\nAPP_NAME=myapp\n")
    return env


@pytest.fixture()
def file_with_key(tmp_path: Path) -> Path:
    """Create a generic file with an API key in it."""
    f = tmp_path / "config.py"
    f.write_text('API_KEY = "sk-proj-abc123def456ghi789jkl012mno345pqr678stu901vwx234"\n')
    return f


# ---------------------------------------------------------------------------
# Tests: basic scan
# ---------------------------------------------------------------------------

class TestScanBasic:
    """Tests for basic scan behavior."""

    def test_scan_file_with_unprotected_key_exits_1(
        self, file_with_key: Path
    ) -> None:
        """Scan file with real key -> exit 1, shows UNPROTECTED."""
        result = runner.invoke(app, ["scan", str(file_with_key)])
        assert result.exit_code == 1, f"stdout={result.stdout}\nstderr={result.stderr}"
        assert "UNPROTECTED" in result.stderr or "unprotected" in result.stderr.lower()

    def test_scan_clean_file_exits_0(self, env_clean: Path) -> None:
        """Scan file with no API keys -> exit 0."""
        result = runner.invoke(app, ["scan", str(env_clean)])
        assert result.exit_code == 0

    def test_scan_env_with_decoy_shows_protected(
        self, home_dir: WorthlessHome, env_with_real_key: Path
    ) -> None:
        """After lock, scan should show PROTECTED and exit 0."""
        # First lock the key
        lock_result = runner.invoke(
            app,
            ["lock", "--env", str(env_with_real_key)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert lock_result.exit_code == 0, lock_result.output

        # Now scan -- the original file has a decoy, but shard_a has the real value
        # The scan should find NO unprotected keys (decoy is low entropy, filtered out)
        result = runner.invoke(
            app,
            ["scan", str(env_with_real_key)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    def test_scan_nonexistent_file_exits_0(self, tmp_path: Path) -> None:
        """Scanning a nonexistent file should not crash (exit 0 = clean)."""
        result = runner.invoke(app, ["scan", str(tmp_path / "nope.env")])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Tests: output formats
# ---------------------------------------------------------------------------

class TestScanFormats:
    """Tests for --format sarif, --json, --quiet."""

    def test_format_sarif_valid_json(self, file_with_key: Path) -> None:
        """--format sarif should produce valid SARIF v2.1.0 JSON on stdout."""
        result = runner.invoke(app, ["scan", "--format", "sarif", str(file_with_key)])
        assert result.exit_code == 1  # unprotected key found
        sarif = json.loads(result.stdout)
        assert sarif["version"] == "2.1.0"
        assert "$schema" in sarif
        assert len(sarif["runs"]) == 1
        assert len(sarif["runs"][0]["results"]) >= 1

    def test_format_json_valid_array(self, file_with_key: Path) -> None:
        """--json should produce a JSON array of findings on stdout."""
        result = runner.invoke(app, ["scan", "--json", str(file_with_key)])
        assert result.exit_code == 1
        findings = json.loads(result.stdout)
        assert isinstance(findings, list)
        assert len(findings) >= 1
        assert "provider" in findings[0]
        assert "is_protected" in findings[0]

    def test_quiet_suppresses_output(self, file_with_key: Path) -> None:
        """--quiet should produce no stderr output (exit code only)."""
        result = runner.invoke(app, ["-q", "scan", str(file_with_key)])
        assert result.exit_code == 1
        assert result.stderr.strip() == ""

    def test_show_suffix_reveals_chars(self, file_with_key: Path) -> None:
        """--show-suffix should reveal last 4 chars of key in output."""
        result = runner.invoke(app, ["scan", "--show-suffix", str(file_with_key)])
        assert result.exit_code == 1
        # The key ends with "x234", suffix should appear somewhere
        assert "x234" in result.stderr


# ---------------------------------------------------------------------------
# Tests: exit codes
# ---------------------------------------------------------------------------

class TestScanExitCodes:
    """Test exit code convention: 0=clean, 1=unprotected, 2=error."""

    def test_exit_0_clean(self, env_clean: Path) -> None:
        result = runner.invoke(app, ["scan", str(env_clean)])
        assert result.exit_code == 0

    def test_exit_1_unprotected(self, file_with_key: Path) -> None:
        result = runner.invoke(app, ["scan", str(file_with_key)])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Tests: --install-hook
# ---------------------------------------------------------------------------

class TestScanInstallHook:
    """Tests for --install-hook."""

    def test_install_hook_creates_executable(self, tmp_path: Path) -> None:
        """--install-hook should create .git/hooks/pre-commit."""
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)

        result = runner.invoke(
            app,
            ["scan", "--install-hook"],
            env={"GIT_DIR": str(tmp_path / ".git")},
            catch_exceptions=False,
        )
        hook = tmp_path / ".git" / "hooks" / "pre-commit"
        assert hook.exists(), f"stdout={result.stdout}\nstderr={result.stderr}"
        assert os.access(str(hook), os.X_OK)
        content = hook.read_text()
        assert "worthless scan" in content

    def test_install_hook_appends_to_existing(self, tmp_path: Path) -> None:
        """--install-hook should not overwrite existing hook."""
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        hook = git_dir / "pre-commit"
        hook.write_text("#!/bin/sh\necho existing\n")
        hook.chmod(0o755)

        result = runner.invoke(
            app,
            ["scan", "--install-hook"],
            env={"GIT_DIR": str(tmp_path / ".git")},
            catch_exceptions=False,
        )
        content = hook.read_text()
        assert "echo existing" in content
        assert "worthless scan" in content


# ---------------------------------------------------------------------------
# Tests: --pre-commit mode
# ---------------------------------------------------------------------------

class TestScanPrecommit:
    """Tests for --pre-commit mode (filenames passed as args)."""

    def test_precommit_processes_passed_files(
        self, file_with_key: Path, env_clean: Path
    ) -> None:
        """--pre-commit should scan only the files passed as args."""
        result = runner.invoke(
            app, ["scan", "--pre-commit", str(file_with_key), str(env_clean)]
        )
        assert result.exit_code == 1  # file_with_key has unprotected

    def test_precommit_clean_files_exit_0(self, env_clean: Path) -> None:
        """--pre-commit with clean files -> exit 0."""
        result = runner.invoke(app, ["scan", "--pre-commit", str(env_clean)])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Tests: entropy filtering
# ---------------------------------------------------------------------------

class TestScanEntropy:
    """Placeholder values should be filtered by entropy."""

    def test_placeholder_skipped(self, tmp_path: Path) -> None:
        """Low-entropy placeholder value should not be reported."""
        f = tmp_path / ".env"
        f.write_text("OPENAI_API_KEY=sk-proj-your-key-here-your-key-here-your-key-here\n")
        result = runner.invoke(app, ["scan", str(f)])
        # Low-entropy placeholder should be skipped -> clean
        assert result.exit_code == 0
