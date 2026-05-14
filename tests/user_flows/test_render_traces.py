"""Unit coverage for user-flow terminal trace rendering helpers."""

from __future__ import annotations

from pathlib import Path

from tests.user_flows import render_traces


def test_teammate_handoff_trace_keeps_distinct_command_homes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """The handoff trace must not mutate the owner's journey home."""
    monkeypatch.setattr(render_traces, "TRACE_ROOT", tmp_path)

    def _fake_run(
        self,
        args,
        *,
        cwd,
        env_files,
        home=None,
        expect_exit=None,
    ) -> None:
        command_home = home or self.journey.home
        before = render_traces.snapshot_env_files("before", env_files)
        after = render_traces.snapshot_env_files("after", env_files)
        self.journey.traces.append(
            render_traces.CommandTrace(
                command=["worthless", *args],
                cwd=cwd,
                home=command_home,
                exit_code=1 if expect_exit else 0,
                stdout="",
                stderr="",
                before=before,
                after=after,
            )
        )

    monkeypatch.setattr(render_traces.TraceRunner, "run", _fake_run)

    journey = render_traces.build_teammate_handoff_failure()

    assert journey.home == tmp_path / "teammate-handoff-failure" / ".worthless"
    assert len(journey.traces) == 2
    assert journey.traces[0].home == journey.home
    assert journey.traces[1].home == (
        tmp_path / "teammate-handoff-failure" / "teammate" / ".worthless"
    )
