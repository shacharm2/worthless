# Lessons Learned

## 2026-04-11: NEVER use mid-file imports in test files

**What happened:** TDD agent and manual edits kept putting imports inside test function bodies. User corrected this 3+ times in one session.

**Rule:** ALL imports go at the top of the file. No exceptions. When writing test files or editing existing ones, check imports are at module level before submitting.

## 2026-04-11: Required deps must hard-import, not try/except

**What happened:** `keyring` was a required dependency but code did `try: import keyring / except ImportError: keyring = None`. Karen flagged this as a security defect — silent degradation from OS keyring to plaintext file.

**Rule:** If a package is in `pyproject.toml` dependencies (not optional), import it directly. Use `_keyring_available()` (backend check) for graceful degradation, not import guards.

## 2026-03-21: Don't contradict yourself across messages

**What happened:** Said "there's no V1 in GSD" then two messages later explained what V1 means. V1 scope is clearly defined in CLAUDE.md.

**Rule:** Before saying "X doesn't exist," check CLAUDE.md and project docs. If you just read it, you know it. Don't pretend otherwise.
