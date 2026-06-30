"""End-to-end terminal-EFFECT tests for ``worthless scan`` output hardening.

The byte-level tests in ``test_lock_audit_gate.py`` assert injection bytes are
removed from the output string. These prove the user-visible promise one level
up — the terminal isn't hijacked — by rendering output through a tiny VT100
screen model. Each test first asserts the attack DOES mutate the screen when raw
(precondition), so a broken model or a dropped sanitiser fails loudly instead of
passing vacuously.

Two hijack classes, two proof styles: screen-mutation attacks (CSI clear/cursor)
get the screen-effect proof here; out-of-band attacks (OSC 52 clipboard, OSC 8,
title-set, bidi) leave no grid change, so ``test_out_of_band_attacks_stripped``
proves them at the byte level (no ESC/BEL/bidi-override reaches the terminal).
Self-contained — no third-party emulator dependency.
"""

from __future__ import annotations

from worthless.cli.code_scanner import CodeFinding
from worthless.cli.commands.scan import _format_code_findings_human, _format_skipped_human
from worthless.cli.scanner import SkippedFile

ESC = "\x1b"
BEL = "\x07"
RLO = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE (bidi reorder), built to avoid a literal bidi char
# A warning already on the user's screen before scan prints its output. If an
# attack fires, this line gets wiped or overwritten; if neutralised, it stays.
_PRIOR_WARNING = "PRIOR_WARNING_UNPROTECTED_KEY_IN_config_py"


class _VTScreen:
    """Minimal VT100 screen — interprets ONLY the CSI sequences these attacks
    use (``ESC[2J`` erase-screen, ``ESC[H`` cursor-home, ``ESC[A`` cursor-up,
    ``ESC[2K`` erase-line); other escapes are ignored. Faithful enough to show
    whether an attack's escape actually mutates the rendered screen."""

    def __init__(self, rows: int = 24, cols: int = 80) -> None:
        self.rows, self.cols = rows, cols
        self.grid = [[" "] * cols for _ in range(rows)]
        self.r = self.c = 0

    def feed(self, text: str) -> None:
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == ESC and i + 1 < n and text[i + 1] == "[":
                j = i + 2
                while j < n and text[j] in "0123456789;":
                    j += 1
                if j < n:
                    self._csi(text[i + 2 : j], text[j])
                    i = j + 1
                    continue
            if ch == ESC:
                i += 1
            elif ch == "\r":
                self.c = 0
                i += 1
            elif ch == "\n":
                self.r = min(self.r + 1, self.rows - 1)
                i += 1
            else:
                if 0 <= self.r < self.rows and 0 <= self.c < self.cols:
                    self.grid[self.r][self.c] = ch
                self.c = min(self.c + 1, self.cols - 1)
                i += 1

    def _csi(self, params: str, final: str) -> None:
        nums = [int(p) for p in params.split(";") if p]
        n0 = nums[0] if nums else 0
        if final == "J" and n0 == 2:  # erase entire screen
            self.grid = [[" "] * self.cols for _ in range(self.rows)]
        elif final == "H":  # cursor home (only the bare ESC[H form is fed)
            self.r = self.c = 0
        elif final == "A":  # cursor up
            self.r = max(0, self.r - (n0 or 1))
        elif final == "K" and n0 == 2:  # erase whole line
            self.grid[self.r] = [" "] * self.cols

    def rows_text(self) -> list[str]:
        return ["".join(row).rstrip() for row in self.grid if "".join(row).strip()]


def _render(text: str) -> list[str]:
    """Feed a prior warning + text through the screen; return non-blank rows."""
    screen = _VTScreen()
    screen.feed(_PRIOR_WARNING + "\r\n")
    screen.feed(text.replace("\n", "\r\n"))
    return screen.rows_text()


def _warning_survived(rows: list[str]) -> bool:
    return any(_PRIOR_WARNING in row for row in rows)


def test_source_snippet_clear_screen_attack_neutralised() -> None:
    """A scanned source line with ESC[2J (clear screen) can't wipe the terminal."""
    # ESC[2J clears the screen, ESC[H homes the cursor, then a forged "clean" msg.
    attack = f'KEY="sk-x"{ESC}[2J{ESC}[H>> SCAN CLEAN: 0 issues <<'

    # Precondition — the attack is REAL: raw, it wipes the prior warning.
    raw_rows = _render(f"       -> {attack}")
    assert not _warning_survived(raw_rows), "ESC[2J should wipe the screen when raw"

    # The fix — fed through the real formatter, the warning survives intact.
    finding = CodeFinding(
        file="config.py",
        line=1,
        column=1,
        matched_url="https://api.openai.com",
        provider_name="openai",
        suggested_env_var="OPENAI_BASE_URL",
        line_text=attack,
    )
    safe_rows = _render(_format_code_findings_human([finding]))
    assert _warning_survived(safe_rows), "sanitised output must not clear the screen"
    assert ESC not in "".join(safe_rows), "no raw ESC may reach the terminal"


def test_skipped_filename_cursor_forge_attack_neutralised() -> None:
    """A skipped file whose name moves the cursor up can't overwrite real output."""
    # ESC[1A moves cursor up one line, ESC[2K clears it — overwriting the warning
    # with a forged reassuring line.
    attack_name = f"big{ESC}[1A{ESC}[2KFORGED_no_issues.env"

    raw_rows = _render(f"  {attack_name}  [timeout]")
    assert not _warning_survived(raw_rows), "cursor-move + clear-line should overwrite when raw"

    safe_rows = _render(_format_skipped_human([SkippedFile(file=attack_name, reason="timeout")]))
    assert _warning_survived(safe_rows), "sanitised skipped path must not move the cursor"
    assert ESC not in "".join(safe_rows), "no raw ESC may reach the terminal"


def test_out_of_band_attacks_stripped() -> None:
    """OSC 52 (clipboard), OSC 8 (hyperlink), title-set, and bidi reorder leave
    NO grid change — a screen model can't express them. Prove neutralisation at
    the byte level instead: the bytes these attacks require (ESC, BEL, the bidi
    override) must never reach the terminal in the real formatter's output.

    Each payload is embedded in attacker-controlled scanned content (the source
    line) and in a skipped-file name, so both real sinks are covered.
    """
    osc52_clipboard = f"{ESC}]52;c;ZXZpbA=={BEL}"  # write attacker data to clipboard
    osc8_hyperlink = f"{ESC}]8;;http://evil{ESC}\\click{ESC}]8;;{ESC}\\"  # spoofed link
    title_set = f"{ESC}]0;PWNED{BEL}"  # rewrite the window title
    payload = f'url = "x"  # {osc52_clipboard}{osc8_hyperlink}{title_set}{RLO}evil'

    code_out = _format_code_findings_human(
        [
            CodeFinding(
                file="config.py",
                line=1,
                column=1,
                matched_url="https://api.openai.com",
                provider_name="openai",
                suggested_env_var="OPENAI_BASE_URL",
                line_text=payload,
            )
        ]
    )
    skipped_out = _format_skipped_human(
        [SkippedFile(file=f"big{title_set}{RLO}.env", reason="timeout")]
    )

    for label, out in (("code finding", code_out), ("skipped", skipped_out)):
        for name, ch in (("ESC", ESC), ("BEL", BEL), ("bidi-override", RLO)):
            assert ch not in out, f"{name} reached terminal via {label} output"
