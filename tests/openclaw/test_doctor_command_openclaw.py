"""U-DOC-* tests for the OpenClaw section of ``worthless doctor``.

Tests target ``_check_openclaw_section`` and ``_skill_installed_version``
from ``worthless.cli.commands.doctor`` directly — no CLI harness needed.
This keeps each test tight to one behaviour without setting up a full
WorthlessHome + keychain.

Spec: ``.claude/plans/graceful-dreaming-reef.md`` §"Phase 2.d" /
test matrix rows U-DOC-01..07.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


from worthless.cli.commands.doctor import _check_openclaw_section, _skill_installed_version
from worthless.openclaw.integration import IntegrationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(
    *,
    present: bool = True,
    config_path: Path | None = None,
    workspace_path: Path | None = None,
    skill_path: Path | None = None,
    home_dir: Path | None = None,
    notes: tuple[str, ...] = (),
) -> IntegrationState:
    return IntegrationState(
        present=present,
        config_path=config_path,
        workspace_path=workspace_path,
        skill_path=skill_path,
        home_dir=home_dir,
        notes=notes,
    )


def _make_enrollment(
    *,
    key_alias: str = "my-key",
    provider: str = "openai",
    env_path: str = "/fake/.env",
    var_name: str = "OPENAI_API_KEY",
) -> MagicMock:
    """Return a mock EnrollmentRecord with the fields doctor needs."""
    e = MagicMock()
    e.key_alias = key_alias
    e.provider = provider
    e.env_path = env_path
    e.var_name = var_name
    return e


def _make_repo(enrollments: list | None = None) -> MagicMock:
    """Return a mock ShardRepository whose list_enrollments coroutine resolves."""
    repo = MagicMock()
    repo.list_enrollments = AsyncMock(return_value=enrollments or [])
    return repo


# ---------------------------------------------------------------------------
# U-DOC-01: OpenClaw not detected
# ---------------------------------------------------------------------------


class TestUDoc01OpenclawAbsent:
    """U-DOC-01: detect() returns present=False → returns False, no output."""

    def test_absent_returns_false(self, capsys) -> None:
        """U-DOC-01: absent host produces no OpenClaw section."""
        state = _make_state(present=False)
        repo = _make_repo()

        with patch("worthless.openclaw.integration.detect", return_value=state):
            result = _check_openclaw_section(repo, fix=False, dry_run=False)

        assert result is False
        assert capsys.readouterr().out == ""

    def test_absent_fix_still_returns_false(self, capsys) -> None:
        """U-DOC-01 variant: --fix on an absent host is also a no-op."""
        state = _make_state(present=False)
        repo = _make_repo()

        with patch("worthless.openclaw.integration.detect", return_value=state):
            result = _check_openclaw_section(repo, fix=True, dry_run=False)

        assert result is False
        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# U-DOC-02: Skill ok + providers ok → silent pass
# ---------------------------------------------------------------------------


class TestUDoc02AllHealthy:
    """U-DOC-02: Skill installed + version match + providers wired → no issues."""

    def test_all_ok_returns_false_silently(self, tmp_path, capsys) -> None:
        """U-DOC-02: clean state → returns False, prints nothing."""
        workspace = tmp_path / "workspace"
        skill_dir = workspace / "skills" / "worthless"
        skill_dir.mkdir(parents=True)
        skill_dir.joinpath("SKILL.md").write_text(
            "Version: 0.1.0\n\nFake skill content.\n", encoding="utf-8"
        )
        config = tmp_path / "openclaw.json"

        state = _make_state(
            present=True,
            config_path=config,
            workspace_path=workspace,
        )
        enrollment = _make_enrollment(key_alias="my-key", provider="openai")
        repo = _make_repo(enrollments=[enrollment])

        with (
            patch("worthless.openclaw.integration.detect", return_value=state),
            patch("worthless.openclaw.skill.current_version", return_value="0.1.0"),
            patch("worthless.cli.commands.doctor.is_orphan", return_value=False),
            patch(
                "worthless.openclaw.config.get_provider",
                return_value={"baseUrl": "http://127.0.0.1:8787/my-key/v1"},
            ),
        ):
            result = _check_openclaw_section(repo, fix=False, dry_run=False)

        assert result is False
        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# U-DOC-03: Skill missing
# ---------------------------------------------------------------------------


class TestUDoc03SkillMissing:
    """U-DOC-03: Skill directory absent → issue surfaced, returns True."""

    def test_skill_not_installed(self, tmp_path, capsys) -> None:
        """U-DOC-03: no skill dir → ✗ line printed, returns True."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        # No skills/worthless/ subdir

        state = _make_state(present=True, workspace_path=workspace)
        repo = _make_repo()

        with (
            patch("worthless.openclaw.integration.detect", return_value=state),
            patch("worthless.openclaw.skill.current_version", return_value="0.1.0"),
        ):
            result = _check_openclaw_section(repo, fix=False, dry_run=False)

        assert result is True
        out = capsys.readouterr().out
        assert "OpenClaw:" in out
        assert "skill not installed" in out

    def test_skill_missing_fix_installs(self, tmp_path, capsys) -> None:
        """U-DOC-03 + fix: --fix reinstalls the missing skill."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)

        state = _make_state(present=True, workspace_path=workspace)
        repo = _make_repo()

        with (
            patch("worthless.openclaw.integration.detect", return_value=state),
            patch("worthless.openclaw.skill.current_version", return_value="0.1.0"),
            patch("worthless.openclaw.skill.install") as mock_install,
        ):
            result = _check_openclaw_section(repo, fix=True, dry_run=False)

        assert result is True
        mock_install.assert_called_once()
        out = capsys.readouterr().out
        assert "skill reinstalled" in out

    def test_skill_missing_fix_dry_run(self, tmp_path, capsys) -> None:
        """U-DOC-03 + dry-run: --fix --dry-run prints intent without installing."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)

        state = _make_state(present=True, workspace_path=workspace)
        repo = _make_repo()

        with (
            patch("worthless.openclaw.integration.detect", return_value=state),
            patch("worthless.openclaw.skill.current_version", return_value="0.1.0"),
            patch("worthless.openclaw.skill.install") as mock_install,
        ):
            result = _check_openclaw_section(repo, fix=True, dry_run=True)

        assert result is True
        mock_install.assert_not_called()
        out = capsys.readouterr().out
        assert "[dry-run]" in out


# ---------------------------------------------------------------------------
# U-DOC-04: Provider not wired
# ---------------------------------------------------------------------------


class TestUDoc04ProviderMissing:
    """U-DOC-04: worthless-* provider absent in openclaw.json → issue surfaced."""

    def test_provider_not_wired(self, tmp_path, capsys) -> None:
        """U-DOC-04: get_provider returns None → ✗ line printed, returns True."""
        workspace = tmp_path / "workspace"
        skill_dir = workspace / "skills" / "worthless"
        skill_dir.mkdir(parents=True)
        skill_dir.joinpath("SKILL.md").write_text("Version: 0.1.0\n", encoding="utf-8")
        config = tmp_path / "openclaw.json"

        state = _make_state(present=True, config_path=config, workspace_path=workspace)
        enrollment = _make_enrollment(key_alias="my-key", provider="openai")
        repo = _make_repo(enrollments=[enrollment])

        with (
            patch("worthless.openclaw.integration.detect", return_value=state),
            patch("worthless.openclaw.skill.current_version", return_value="0.1.0"),
            patch("worthless.cli.commands.doctor.is_orphan", return_value=False),
            patch("worthless.openclaw.config.get_provider", return_value=None),
        ):
            result = _check_openclaw_section(repo, fix=False, dry_run=False)

        assert result is True
        out = capsys.readouterr().out
        assert "worthless-openai" in out
        assert "not wired" in out

    def test_provider_wrong_base_url(self, tmp_path, capsys) -> None:
        """U-DOC-07 variant: entry exists but baseUrl is wrong → issue surfaced."""
        workspace = tmp_path / "workspace"
        skill_dir = workspace / "skills" / "worthless"
        skill_dir.mkdir(parents=True)
        skill_dir.joinpath("SKILL.md").write_text("Version: 0.1.0\n", encoding="utf-8")
        config = tmp_path / "openclaw.json"

        state = _make_state(present=True, config_path=config, workspace_path=workspace)
        enrollment = _make_enrollment(key_alias="my-key", provider="openai")
        repo = _make_repo(enrollments=[enrollment])

        with (
            patch("worthless.openclaw.integration.detect", return_value=state),
            patch("worthless.openclaw.skill.current_version", return_value="0.1.0"),
            patch("worthless.cli.commands.doctor.is_orphan", return_value=False),
            patch(
                "worthless.openclaw.config.get_provider",
                return_value={"baseUrl": "http://127.0.0.1:9999/different/v1"},
            ),
        ):
            result = _check_openclaw_section(repo, fix=False, dry_run=False)

        assert result is True
        out = capsys.readouterr().out
        assert "baseUrl mismatch" in out


# ---------------------------------------------------------------------------
# U-DOC-05: Stale skill
# ---------------------------------------------------------------------------


class TestUDoc05StaleSkill:
    """U-DOC-05: Skill version mismatch → reported; --fix updates it."""

    def test_stale_skill_reported(self, tmp_path, capsys) -> None:
        """U-DOC-05a: installed version older than bundled → issue surfaced."""
        workspace = tmp_path / "workspace"
        skill_dir = workspace / "skills" / "worthless"
        skill_dir.mkdir(parents=True)
        skill_dir.joinpath("SKILL.md").write_text("Version: 0.0.1\n", encoding="utf-8")

        state = _make_state(present=True, workspace_path=workspace)
        repo = _make_repo()

        with (
            patch("worthless.openclaw.integration.detect", return_value=state),
            patch("worthless.openclaw.skill.current_version", return_value="0.1.0"),
        ):
            result = _check_openclaw_section(repo, fix=False, dry_run=False)

        assert result is True
        out = capsys.readouterr().out
        assert "stale" in out
        assert "0.0.1" in out
        assert "0.1.0" in out

    def test_stale_skill_fix_updates(self, tmp_path, capsys) -> None:
        """U-DOC-05b: --fix on stale skill calls install and reports it."""
        workspace = tmp_path / "workspace"
        skill_dir = workspace / "skills" / "worthless"
        skill_dir.mkdir(parents=True)
        skill_dir.joinpath("SKILL.md").write_text("Version: 0.0.1\n", encoding="utf-8")

        state = _make_state(present=True, workspace_path=workspace)
        repo = _make_repo()

        with (
            patch("worthless.openclaw.integration.detect", return_value=state),
            patch("worthless.openclaw.skill.current_version", return_value="0.1.0"),
            patch("worthless.openclaw.skill.install") as mock_install,
        ):
            result = _check_openclaw_section(repo, fix=True, dry_run=False)

        assert result is True
        mock_install.assert_called_once()
        out = capsys.readouterr().out
        assert "skill reinstalled" in out


# ---------------------------------------------------------------------------
# U-DOC-06: Workspace absent
# ---------------------------------------------------------------------------


class TestUDoc06WorkspaceMissing:
    """U-DOC-06: detect() present=True but workspace=None → workspace issue."""

    def test_workspace_none(self, tmp_path, capsys) -> None:
        """U-DOC-06: present but workspace=None → 'workspace not found' issue."""
        state = _make_state(present=True, workspace_path=None)
        repo = _make_repo()

        with patch("worthless.openclaw.integration.detect", return_value=state):
            result = _check_openclaw_section(repo, fix=False, dry_run=False)

        assert result is True
        out = capsys.readouterr().out
        assert "workspace not found" in out


# ---------------------------------------------------------------------------
# U-DOC-07: Config absent (workspace present)
# ---------------------------------------------------------------------------


class TestUDoc07ConfigMissing:
    """U-DOC-07: present=True, workspace ok, config=None → provider issue."""

    def test_config_none_with_enrollment(self, tmp_path, capsys) -> None:
        """U-DOC-07: workspace present, config missing → 'not wired' per alias."""
        workspace = tmp_path / "workspace"
        skill_dir = workspace / "skills" / "worthless"
        skill_dir.mkdir(parents=True)
        skill_dir.joinpath("SKILL.md").write_text("Version: 0.1.0\n", encoding="utf-8")

        state = _make_state(
            present=True,
            workspace_path=workspace,
            config_path=None,  # no openclaw.json
        )
        enrollment = _make_enrollment(key_alias="my-key", provider="openai")
        repo = _make_repo(enrollments=[enrollment])

        with (
            patch("worthless.openclaw.integration.detect", return_value=state),
            patch("worthless.openclaw.skill.current_version", return_value="0.1.0"),
            patch("worthless.cli.commands.doctor.is_orphan", return_value=False),
        ):
            result = _check_openclaw_section(repo, fix=False, dry_run=False)

        assert result is True
        out = capsys.readouterr().out
        assert "worthless-openai" in out
        assert "not wired" in out


# ---------------------------------------------------------------------------
# _skill_installed_version unit tests
# ---------------------------------------------------------------------------


class TestSkillInstalledVersion:
    """Unit tests for the ``_skill_installed_version`` helper."""

    def test_returns_version_from_file(self, tmp_path) -> None:
        skill_dir = tmp_path / "worthless"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("Version: 1.2.3\n\nBody.", encoding="utf-8")
        assert _skill_installed_version(skill_dir) == "1.2.3"

    def test_returns_none_when_no_file(self, tmp_path) -> None:
        skill_dir = tmp_path / "worthless"
        skill_dir.mkdir()
        assert _skill_installed_version(skill_dir) is None

    def test_returns_none_when_dir_missing(self, tmp_path) -> None:
        assert _skill_installed_version(tmp_path / "missing") is None

    def test_returns_none_when_no_version_line(self, tmp_path) -> None:
        skill_dir = tmp_path / "worthless"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("No version here.\n", encoding="utf-8")
        assert _skill_installed_version(skill_dir) is None
