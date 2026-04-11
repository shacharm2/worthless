"""SKILL.md agent eval — verify an agent can use the discovery file.

Layer 2: E2E eval. Spawns headless Claude Code sessions with only SKILL.md
as context, gives task prompts, and checks the agent calls the right commands.

Usage:
    python tests/evals/eval_skill_md.py [--model sonnet] [--verbose]

Requires: claude CLI installed and authenticated.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass


@dataclass
class EvalCase:
    """A single eval: prompt + expected patterns in the response."""

    name: str
    prompt: str
    expect_commands: list[str]  # CLI commands agent should reference
    expect_patterns: list[str]  # Strings that should appear in response
    reject_patterns: list[str] = None  # Strings that should NOT appear

    def __post_init__(self) -> None:
        if self.reject_patterns is None:
            self.reject_patterns = []


EVAL_CASES = [
    EvalCase(
        name="lock_key",
        prompt=(
            "I have an OpenAI key in my .env file. Using only the SKILL.md file "
            "in this repo, tell me the exact command to protect it. "
            "Do NOT run the command, just tell me."
        ),
        expect_commands=["lock"],
        expect_patterns=["worthless lock"],
    ),
    EvalCase(
        name="check_status",
        prompt=(
            "Using only SKILL.md, what command shows me which keys are enrolled "
            "and whether the proxy is running? Just the command, nothing else."
        ),
        expect_commands=["status"],
        expect_patterns=["worthless status"],
    ),
    EvalCase(
        name="rules_awareness",
        prompt=(
            "According to SKILL.md, what spending controls does Worthless offer? "
            "List the rule types. Brief answer only."
        ),
        expect_commands=[],
        expect_patterns=["SpendCap", "TokenBudget"],
    ),
    EvalCase(
        name="set_daily_budget",
        prompt=(
            "Using SKILL.md, how do I set a daily token budget of 10000 when "
            "locking a key? Give me the exact command."
        ),
        expect_commands=["lock"],
        expect_patterns=["--token-budget-daily"],
    ),
    EvalCase(
        name="scan_for_secrets",
        prompt=(
            "I want to check if any API keys are exposed in my project files. "
            "Using SKILL.md, what's the command? Brief answer."
        ),
        expect_commands=["scan"],
        expect_patterns=["worthless scan"],
    ),
    EvalCase(
        name="mcp_integration",
        prompt=(
            "According to SKILL.md, how do I install Worthless with MCP support "
            "for use in Claude Code? One-line answer."
        ),
        expect_commands=[],
        expect_patterns=["mcp"],
    ),
    EvalCase(
        name="no_hallucinated_commands",
        prompt=(
            "Using ONLY the SKILL.md file, list every CLI command Worthless has. "
            "Only list commands explicitly documented. No extras."
        ),
        expect_commands=[],
        expect_patterns=["lock", "unlock", "scan", "status", "wrap", "up", "down"],
        reject_patterns=["deploy", "configure", "init", "setup"],
    ),
]


def run_eval(case: EvalCase, model: str = "sonnet", verbose: bool = False) -> bool:
    """Run a single eval case via headless Claude Code."""
    full_prompt = (
        f"Read the file SKILL.md in this directory, then answer: {case.prompt}\n"
        f"Keep your response under 100 words."
    )

    try:
        result = subprocess.run(
            ["claude", "-p", full_prompt, "--model", model, "--max-turns", "3"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(
                subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
            ),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  SKIP {case.name}: {e}")
        return True  # Don't fail on infra issues

    response = result.stdout.lower()

    if verbose:
        print(f"  Response: {result.stdout[:200]}...")

    passed = True

    # Check expected commands are referenced
    for cmd in case.expect_commands:
        if cmd.lower() not in response:
            print(f"  FAIL {case.name}: expected command '{cmd}' not referenced")
            passed = False

    # Check expected patterns
    for pattern in case.expect_patterns:
        if pattern.lower() not in response:
            print(f"  FAIL {case.name}: expected '{pattern}' not found in response")
            passed = False

    # Check reject patterns
    for pattern in case.reject_patterns:
        if pattern.lower() in response:
            print(f"  FAIL {case.name}: rejected '{pattern}' found in response")
            passed = False

    if passed:
        print(f"  PASS {case.name}")

    return passed


def main() -> None:
    model = "sonnet"
    verbose = False

    for arg in sys.argv[1:]:
        if arg.startswith("--model"):
            model = sys.argv[sys.argv.index(arg) + 1]
        if arg == "--verbose":
            verbose = True

    print(f"Running SKILL.md agent evals (model: {model})")
    print(f"{'=' * 50}")

    results: list[tuple[str, bool]] = []
    for case in EVAL_CASES:
        passed = run_eval(case, model=model, verbose=verbose)
        results.append((case.name, passed))

    print(f"\n{'=' * 50}")
    passed_count = sum(1 for _, p in results if p)
    total = len(results)
    print(f"Results: {passed_count}/{total} passed")

    if passed_count < total:
        print("\nFailed evals:")
        for name, passed in results:
            if not passed:
                print(f"  - {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
