# User-flow tests

These tests exercise Worthless the way a user experiences it: real CLI command
dispatch, real filesystem writes, real SQLite state, and real keyring access
only when the test explicitly guards for it.

Run them explicitly:

```bash
pytest -m user_flow
```

The default test sweep excludes `user_flow` because these tests can be slower,
can start real subprocesses, and may require platform facilities such as a
native keyring.

Rules for new tests:

- Always isolate `WORTHLESS_HOME` under `tmp_path`; never touch the developer's
  real `~/.worthless`.
- Use scanner-safe fake keys from `tests.helpers`.
- Scrub real provider credentials from subprocess environments before invoking
  a child process.
- Guard real keyring tests with `keyring_available()` and clean up with
  `delete_fernet_key(home_dir=...)`.
- Prefer chained assertions over isolated command checks when the regression is
  about a real user journey.
- Keep suite scope aligned with Linear `WOR-439` and its child issues.
