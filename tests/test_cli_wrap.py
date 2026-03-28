"""Tests for the ``worthless wrap`` command."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

runner = CliRunner()


class TestWrapEnvInjection:
    """wrap injects BASE_URL env vars for enrolled providers."""

    def test_child_env_has_base_url(self, tmp_path: Path):
        """wrap should inject OPENAI_BASE_URL into child environment."""
        from worthless.cli.commands.wrap import _build_child_env

        child_env = _build_child_env(port=9999, providers=["openai"])
        assert child_env["OPENAI_BASE_URL"] == "http://127.0.0.1:9999"

    def test_child_env_anthropic(self, tmp_path: Path):
        """wrap should inject ANTHROPIC_BASE_URL for anthropic provider."""
        from worthless.cli.commands.wrap import _build_child_env

        child_env = _build_child_env(port=8888, providers=["anthropic"])
        assert child_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8888"

    def test_child_env_multiple_providers(self):
        """wrap injects env vars for all enrolled providers."""
        from worthless.cli.commands.wrap import _build_child_env

        child_env = _build_child_env(port=7777, providers=["openai", "anthropic"])
        assert child_env["OPENAI_BASE_URL"] == "http://127.0.0.1:7777"
        assert child_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:7777"

    def test_child_env_no_session_token(self):
        """Session token should not be in child env (dead code removed)."""
        from worthless.cli.commands.wrap import _build_child_env

        child_env = _build_child_env(port=9999, providers=["openai"])
        assert "WORTHLESS_SESSION_TOKEN" not in child_env


class TestWrapExitCode:
    """wrap mirrors child exit code."""

    @pytest.mark.integration
    @pytest.mark.timeout(30)
    def test_mirrors_child_exit_code(self, tmp_path: Path):
        """wrap should exit with the child's exit code."""
        from worthless.cli.commands.wrap import _run_child_and_wait

        # Run a child that exits with code 42
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(42)"],
            process_group=0,
        )
        code = _run_child_and_wait(proc)
        assert code == 42

    @pytest.mark.integration
    @pytest.mark.timeout(30)
    def test_mirrors_zero_exit(self, tmp_path: Path):
        """wrap should exit 0 when child exits 0."""
        from worthless.cli.commands.wrap import _run_child_and_wait

        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            process_group=0,
        )
        code = _run_child_and_wait(proc)
        assert code == 0


class TestWrapNoKeys:
    """wrap errors when no keys are enrolled."""

    def test_no_enrolled_keys_raises(self, tmp_path: Path):
        from worthless.cli.commands.wrap import _list_enrolled_providers
        from worthless.cli.bootstrap import ensure_home

        home = ensure_home(tmp_path / ".worthless")
        providers = _list_enrolled_providers(home)
        assert providers == []


class TestWrapLivenessPipe:
    """wrap creates liveness pipe for proxy death detection."""

    def test_liveness_pipe_created(self):
        from worthless.cli.process import create_liveness_pipe

        read_fd, write_fd = create_liveness_pipe()
        try:
            os.fstat(read_fd)
            os.fstat(write_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)
