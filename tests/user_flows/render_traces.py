"""Render deterministic terminal traces for user-flow UX proof.

The renderer drives the real ``worthless`` console script in subprocesses
against isolated project directories and a throwaway ``WORTHLESS_HOME``. It
uses deterministic fake keys only and redacts key bodies before writing the
Markdown report.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path


TRACE_ROOT = Path(tempfile.gettempdir()) / "worthless-terminal-traces"
FERNET_KEY = base64.urlsafe_b64encode(
    hashlib.sha256(b"terminal-trace-fernet-key").digest()
).decode()
SECRET_VARS = frozenset({"OPENAI_API_KEY", "ANTHROPIC_API_KEY"})
FAKE_KEY_SEEDS = [
    ("sk-proj-", "trace-openai-round-trip"),
    ("sk-proj-", "trace-handoff-owner"),
    ("sk-proj-", "trace-rotation-old"),
    ("sk-proj-", "trace-rotation-new"),
    ("sk-proj-", "trace-shape-old"),
    ("sk-", "trace-shape-new"),
    ("sk-proj-", "trace-project-a"),
    ("sk-proj-", "trace-project-b"),
]
_PROTECTED_LABELS: dict[str, str] = {}


def fake_key(prefix: str, seed: str) -> str:
    """Generate a deterministic high-entropy fake key at runtime."""
    raw = hashlib.sha256(seed.encode()).digest()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")[:48]
    return prefix + body


@dataclass
class EnvSnapshot:
    label: str
    files: dict[Path, str]


@dataclass
class CommandTrace:
    command: list[str]
    cwd: Path
    home: Path
    exit_code: int
    stdout: str
    stderr: str
    before: EnvSnapshot
    after: EnvSnapshot


@dataclass
class Journey:
    title: str
    summary: str
    root: Path
    home: Path
    traces: list[CommandTrace] = field(default_factory=list)


class TraceRunner:
    def __init__(self, root: Path, *, title: str, summary: str) -> None:
        self.journey = Journey(
            title=title,
            summary=summary,
            root=root,
            home=root / ".worthless",
        )

    def run(
        self,
        args: list[str],
        *,
        cwd: Path,
        env_files: Iterable[Path],
        expect_exit: Callable[[int], bool] | None = None,
    ) -> None:
        before = snapshot_env_files("before", env_files)
        proc = subprocess.run(  # noqa: S603 - fixed executable plus controlled args.
            [resolve_worthless(), *args],
            cwd=cwd,
            env=scrubbed_env(self.journey.home),
            text=True,
            capture_output=True,
            check=False,
        )
        after = snapshot_env_files("after", env_files)
        if expect_exit is None:
            expect_exit = expected_success
        if not expect_exit(proc.returncode):
            raise RuntimeError(
                f"unexpected exit {proc.returncode} for {' '.join(args)}\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
        self.journey.traces.append(
            CommandTrace(
                command=["worthless", *args],
                cwd=cwd,
                home=self.journey.home,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                before=before,
                after=after,
            )
        )


def resolve_worthless() -> str:
    executable = shutil.which("worthless")
    if executable is None:
        raise RuntimeError(
            "Could not find the 'worthless' console script. "
            "Run this renderer via 'uv run python tests/user_flows/render_traces.py'."
        )
    return executable


def scrubbed_env(home: Path) -> dict[str, str]:
    keep = {
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", ""),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "TERM": os.environ.get("TERM", "dumb"),
        "TMPDIR": os.environ.get("TMPDIR", tempfile.gettempdir()),
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", ""),
        "VIRTUAL_ENV": os.environ.get("VIRTUAL_ENV", ""),
    }
    return {
        key: value
        for key, value in {
            **keep,
            "WORTHLESS_HOME": str(home),
            "WORTHLESS_FERNET_KEY": FERNET_KEY,
            "WORTHLESS_DB_PATH": "",
            "WORTHLESS_FERNET_KEY_PATH": "",
            "WORTHLESS_FERNET_FD": "",
            "WORTHLESS_KEYRING_BACKEND": "null",
            "WORTHLESS_PORT": "",
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
            "OPENAI_BASE_URL": "",
            "ANTHROPIC_BASE_URL": "",
            "COLUMNS": "120",
        }.items()
        if value
    }


def expected_success(code: int) -> bool:
    return code == 0


def snapshot_env_files(label: str, env_files: Iterable[Path]) -> EnvSnapshot:
    return EnvSnapshot(
        label=label,
        files={path: path.read_text() if path.exists() else "<missing>\n" for path in env_files},
    )


def reset_root() -> None:
    if TRACE_ROOT.exists():
        shutil.rmtree(TRACE_ROOT)
    TRACE_ROOT.mkdir(parents=True)


def build_lock_status_scan_unlock() -> Journey:
    runner = TraceRunner(
        TRACE_ROOT / "lock-status-scan-unlock",
        title="Lock, Status, Scan, Unlock",
        summary=(
            "A project key is protected, status shows one enrollment, scan no longer finds "
            "raw keys, and unlock restores the original fake key."
        ),
    )
    project = runner.journey.root / "project"
    project.mkdir(parents=True)
    env_file = project / ".env"
    env_file.write_text(f"OPENAI_API_KEY={fake_key('sk-proj-', 'trace-openai-round-trip')}\n")

    runner.run(["lock", "--env", str(env_file)], cwd=project, env_files=[env_file])
    runner.run(["status"], cwd=project, env_files=[env_file])
    runner.run(["scan", str(project)], cwd=project, env_files=[env_file])
    runner.run(["unlock", "--env", str(env_file)], cwd=project, env_files=[env_file])
    runner.run(["status"], cwd=project, env_files=[env_file])
    return runner.journey


def build_teammate_handoff_failure() -> Journey:
    runner = TraceRunner(
        TRACE_ROOT / "teammate-handoff-failure",
        title="Teammate Handoff Failure",
        summary=(
            "A locked .env copied without the local Worthless home fails with a "
            "plain-English recovery hint instead of a traceback."
        ),
    )
    owner_project = runner.journey.root / "owner-project"
    teammate_project = runner.journey.root / "teammate-project"
    owner_project.mkdir(parents=True)
    teammate_project.mkdir(parents=True)
    owner_env = owner_project / ".env"
    teammate_env = teammate_project / ".env"
    owner_env.write_text(f"OPENAI_API_KEY={fake_key('sk-proj-', 'trace-handoff-owner')}\n")

    runner.run(["lock", "--env", str(owner_env)], cwd=owner_project, env_files=[owner_env])
    teammate_env.write_text(owner_env.read_text())

    teammate = TraceRunner(
        runner.journey.root / "teammate-home",
        title=runner.journey.title,
        summary=runner.journey.summary,
    )
    teammate.journey = runner.journey
    teammate.journey.home = runner.journey.root / "teammate" / ".worthless"
    teammate.run(
        ["unlock", "--env", str(teammate_env)],
        cwd=teammate_project,
        env_files=[teammate_env],
        expect_exit=lambda code: code != 0,
    )
    return runner.journey


def build_rotation_relock() -> Journey:
    runner = TraceRunner(
        TRACE_ROOT / "rotation-relock",
        title="Rotation Relock",
        summary=(
            "A raw replacement key is treated as a new key, including when the replacement "
            "has a different OpenAI key shape."
        ),
    )
    same_shape = runner.journey.root / "same-shape"
    different_shape = runner.journey.root / "different-shape"
    same_shape.mkdir(parents=True)
    different_shape.mkdir(parents=True)
    same_env = same_shape / ".env"
    different_env = different_shape / ".env"

    same_env.write_text(f"OPENAI_API_KEY={fake_key('sk-proj-', 'trace-rotation-old')}\n")
    runner.run(["lock", "--env", str(same_env)], cwd=same_shape, env_files=[same_env])
    same_env.write_text(f"OPENAI_API_KEY={fake_key('sk-proj-', 'trace-rotation-new')}\n")
    runner.run(["lock", "--env", str(same_env)], cwd=same_shape, env_files=[same_env])
    runner.run(["unlock", "--env", str(same_env)], cwd=same_shape, env_files=[same_env])

    different_env.write_text(f"OPENAI_API_KEY={fake_key('sk-proj-', 'trace-shape-old')}\n")
    runner.run(
        ["lock", "--env", str(different_env)],
        cwd=different_shape,
        env_files=[different_env],
    )
    different_env.write_text(f"OPENAI_API_KEY={fake_key('sk-', 'trace-shape-new')}\n")
    runner.run(
        ["lock", "--env", str(different_env)],
        cwd=different_shape,
        env_files=[different_env],
    )
    runner.run(
        ["unlock", "--env", str(different_env)],
        cwd=different_shape,
        env_files=[different_env],
    )
    return runner.journey


def build_multi_project_isolation() -> Journey:
    runner = TraceRunner(
        TRACE_ROOT / "multi-project-isolation",
        title="Multi-Project Isolation",
        summary=(
            "Two projects share one Worthless home; unlocking one project leaves the other "
            "project protected until explicitly unlocked."
        ),
    )
    project_a = runner.journey.root / "project-a"
    project_b = runner.journey.root / "project-b"
    project_a.mkdir(parents=True)
    project_b.mkdir(parents=True)
    env_a = project_a / ".env"
    env_b = project_b / ".env"
    env_a.write_text(f"OPENAI_API_KEY={fake_key('sk-proj-', 'trace-project-a')}\n")
    env_b.write_text(f"OPENAI_API_KEY={fake_key('sk-proj-', 'trace-project-b')}\n")

    runner.run(["lock", "--env", str(env_a)], cwd=project_a, env_files=[env_a, env_b])
    runner.run(["lock", "--env", str(env_b)], cwd=project_b, env_files=[env_a, env_b])
    runner.run(["unlock", "--env", str(env_a)], cwd=project_a, env_files=[env_a, env_b])
    runner.run(["status"], cwd=project_a, env_files=[env_a, env_b])
    runner.run(["unlock", "--env", str(env_b)], cwd=project_b, env_files=[env_a, env_b])
    return runner.journey


def render_report(journeys: list[Journey]) -> str:
    _PROTECTED_LABELS.clear()
    lines = [
        "# Terminal Traces",
        "",
        "Deterministic UX proof generated by `tests/user_flows/render_traces.py`.",
        "Commands run against isolated temp projects with fake key material only.",
        "Key bodies and local temp paths are redacted for documentation safety.",
        "",
    ]
    for journey in journeys:
        lines.extend(render_journey(journey))
    return "\n".join(lines).rstrip() + "\n"


def render_journey(journey: Journey) -> list[str]:
    lines = [
        f"## {journey.title}",
        "",
        journey.summary,
        "",
        f"- Workspace: `{redact_paths(journey.root)}`",
        "",
    ]
    for index, trace in enumerate(journey.traces, start=1):
        lines.extend(render_trace(index, trace))
    return lines


def render_trace(index: int, trace: CommandTrace) -> list[str]:
    lines = [
        f"### {index}. `{redact_text(' '.join(trace.command))}`",
        "",
        f"- cwd: `{redact_paths(trace.cwd)}`",
        f"- WORTHLESS_HOME: `{redact_paths(trace.home)}`",
        f"- exit: `{trace.exit_code}`",
        "",
        "**.env before**",
        "",
        *render_snapshot(trace.before),
        "**stdout**",
        "",
        fenced(redact_text(trace.stdout) if trace.stdout else "<empty>\n"),
        "",
        "**stderr**",
        "",
        fenced(redact_text(trace.stderr) if trace.stderr else "<empty>\n"),
        "",
        "**.env after**",
        "",
        *render_snapshot(trace.after),
    ]
    return lines


def render_snapshot(snapshot: EnvSnapshot) -> list[str]:
    lines: list[str] = []
    for path, content in snapshot.files.items():
        lines.append(f"`{redact_paths(path)}`")
        lines.append("")
        lines.append(fenced(redact_env_content(content)))
        lines.append("")
    return lines


def fenced(content: str) -> str:
    return f"```text\n{content.rstrip()}\n```"


def redact_env_content(content: str) -> str:
    redacted_lines = []
    for line in content.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            redacted_lines.append(redact_text(line))
            continue
        key, value = line.split("=", 1)
        if key in SECRET_VARS:
            redacted_lines.append(f"{key}={redact_key_value(value)}")
        else:
            redacted_lines.append(f"{key}={redact_text(value)}")
    return "\n".join(redacted_lines) + ("\n" if content.endswith("\n") else "")


def redact_key_value(value: str) -> str:
    prefix = key_prefix(value)
    if value in known_fake_keys():
        digest = hashlib.sha256(value.encode()).hexdigest()[:10]
        return f"{prefix}<redacted:fake-raw:len={len(value)}:sha256={digest}>"

    label = _PROTECTED_LABELS.setdefault(value, f"protected-{len(_PROTECTED_LABELS) + 1}")
    return f"{prefix}<redacted:{label}:len={len(value)}>"


def key_prefix(value: str) -> str:
    for prefix in ("sk-ant-api03-", "sk-proj-", "sk-"):
        if value.startswith(prefix):
            return prefix
    return ""


def redact_text(value: str) -> str:
    redacted = redact_paths(value)
    redacted = redact_known_fake_values(redacted)
    return redacted


def redact_paths(value: object) -> str:
    return str(value).replace(str(TRACE_ROOT), "$TRACE_ROOT")


def redact_known_fake_values(value: str) -> str:
    redacted = value
    for fake in known_fake_keys():
        redacted = redacted.replace(fake, redact_key_value(fake))
    return redacted


def known_fake_keys() -> frozenset[str]:
    return frozenset(fake_key(prefix, seed) for prefix, seed in FAKE_KEY_SEEDS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/user_flows/TERMINAL_TRACES.md"),
        help="Markdown file to write.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    reset_root()
    journeys = [
        build_lock_status_scan_unlock(),
        build_teammate_handoff_failure(),
        build_rotation_relock(),
        build_multi_project_isolation(),
    ]
    report = render_report(journeys)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
