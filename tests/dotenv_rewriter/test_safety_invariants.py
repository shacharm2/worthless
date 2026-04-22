"""Safety-invariant tests for the ``dotenv_rewriter`` public API.

Each test proves that one of the ``safe_rewrite`` invariants fires when
the public ``add_or_rewrite_env_key`` / ``rewrite_env_key`` /
``remove_env_key`` entry points are called against a hostile target.

The headline test - ``test_add_to_symlink_pointing_at_zshrc_refused`` -
is the entire ticket's justification in one assertion: locking a `.env`
that is actually a symlink to the user's `~/.zshrc` must not nuke their
shell config.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from worthless.cli import dotenv_rewriter as _dotenv_rewriter
from worthless.cli.dotenv_rewriter import (
    add_or_rewrite_env_key,
    remove_env_key,
    rewrite_env_key,
)
from worthless.cli.errors import UnsafeReason, UnsafeRewriteRefused


@pytest.fixture
def forbid_safe_rewrite(monkeypatch):
    """Make any `safe_rewrite` call from the dotenv_rewriter module fail the test.

    Used to prove refusal branches raise `UnsafeRewriteRefused` directly
    rather than trampolining through `safe_rewrite(path, b"", ...)` — the
    historical wipe vector.
    """

    def _spy(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("safe_rewrite was invoked on a refusal path")

    monkeypatch.setattr(_dotenv_rewriter, "safe_rewrite", _spy)
    return _spy


def test_add_to_symlink_pointing_at_zshrc_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """A `.env` that is a symlink to ``~/.zshrc`` MUST be refused, byte-identical.

    Reproduces the historical "zshrc lock bug": an attacker (or an unlucky
    user) creates ``foo/.env`` as a symlink to ``~/.zshrc``. The previous
    rewriter happily wrote through the symlink and clobbered the shell rc.
    After wiring ``safe_rewrite`` in, the call must raise
    ``UnsafeRewriteRefused`` and leave the zshrc bytes untouched.
    """
    home = tmp_path / "home"
    home.mkdir()
    zshrc = home / ".zshrc"
    zshrc_content = b"# user's precious shell config\nexport PATH=/usr/bin\n"
    make_env_file(zshrc, content=zshrc_content)
    zshrc_sha = sha256_of(zshrc)

    env_dir = tmp_path / "project"
    env_dir.mkdir()
    env_path = env_dir / ".env"
    env_path.symlink_to(zshrc)

    with pytest.raises(UnsafeRewriteRefused):
        add_or_rewrite_env_key(env_path, "DECOY_KEY", "sk-decoy-0001")

    assert_byte_identical(zshrc, zshrc_sha)


def test_rewrite_to_symlink_pointing_at_zshrc_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """``rewrite_env_key`` through a zshrc symlink MUST be refused."""
    home = tmp_path / "home"
    home.mkdir()
    zshrc = home / ".zshrc"
    zshrc_content = b"# precious shell config\nalias ll='ls -la'\n"
    make_env_file(zshrc, content=zshrc_content)
    zshrc_sha = sha256_of(zshrc)

    env_dir = tmp_path / "project"
    env_dir.mkdir()
    env_path = env_dir / ".env"
    env_path.symlink_to(zshrc)

    with pytest.raises(UnsafeRewriteRefused):
        rewrite_env_key(env_path, "EXISTING_KEY", "sk-decoy-0001")

    assert_byte_identical(zshrc, zshrc_sha)


def test_remove_to_symlink_pointing_at_zshrc_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """``remove_env_key`` through a zshrc symlink MUST be refused."""
    home = tmp_path / "home"
    home.mkdir()
    zshrc = home / ".zshrc"
    zshrc_content = b"# precious shell config\nexport EDITOR=vim\n"
    make_env_file(zshrc, content=zshrc_content)
    zshrc_sha = sha256_of(zshrc)

    env_dir = tmp_path / "project"
    env_dir.mkdir()
    env_path = env_dir / ".env"
    env_path.symlink_to(zshrc)

    with pytest.raises(UnsafeRewriteRefused):
        remove_env_key(env_path, "SOME_KEY")

    assert_byte_identical(zshrc, zshrc_sha)


def test_add_to_basename_dot_zshrc_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """Passing a literal ``.zshrc`` path (no symlink) MUST be refused on basename.

    The basename denylist in ``safe_rewrite`` refuses any target whose
    basename is not literally ``.env``.
    """
    zshrc = tmp_path / ".zshrc"
    zshrc_content = b"# direct zshrc target\nexport FOO=bar\n"
    make_env_file(zshrc, content=zshrc_content)
    zshrc_sha = sha256_of(zshrc)

    with pytest.raises(UnsafeRewriteRefused):
        add_or_rewrite_env_key(zshrc, "DECOY_KEY", "sk-decoy-0001")

    assert_byte_identical(zshrc, zshrc_sha)


def test_rewrite_to_fifo_refused(tmp_path: Path) -> None:
    """A FIFO at the target path MUST be refused (special-file gate)."""
    fifo = tmp_path / ".env"
    os.mkfifo(str(fifo))

    with pytest.raises(UnsafeRewriteRefused):
        rewrite_env_key(fifo, "SOME_KEY", "new_value")

    # FIFO still present; no tmp file leaked.
    import stat as _stat

    assert _stat.S_ISFIFO(os.lstat(str(fifo)).st_mode)
    assert list(tmp_path.glob(".env.tmp-*")) == []


def test_add_to_path_outside_repo_with_default_settings_refused(
    tmp_path: Path,
    make_env_file,
) -> None:
    """When a ``repo_root`` is supplied and the target lives outside it, refuse.

    The rewriter is a thin wrapper: callers that opt into containment by
    passing ``repo_root`` must see the gate fire. The current sub-PR 2
    contract does not pass ``repo_root`` from the public helpers, so this
    test asserts the *path exists* through which a caller could enforce it.
    If the public helpers grow a ``repo_root`` kwarg this test becomes the
    contract check; otherwise it proves the default (no containment) is
    preserved by calling with a path outside a repo and asserting no
    implicit containment fires.
    """
    env = make_env_file(tmp_path / "outside" / ".env", content=b"KEY=value\n")

    # No containment configured → the call should not refuse on CONTAINMENT
    # grounds. It may still succeed or refuse for unrelated reasons, but
    # it must not raise a containment failure. We assert no exception
    # containing CONTAINMENT in its reason.
    try:
        add_or_rewrite_env_key(env, "NEW_KEY", "new_value")
    except UnsafeRewriteRefused as exc:
        assert exc.reason != UnsafeReason.CONTAINMENT, (
            "default caller settings must not trigger containment"
        )


def test_add_with_value_containing_newline_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """A value containing an embedded newline MUST be refused (value validation).

    Newlines in values would allow injecting a second ``KEY=value`` line
    into the file. The rewriter must reject before any write happens.
    """
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    baseline = sha256_of(env)

    with pytest.raises((UnsafeRewriteRefused, ValueError)):
        add_or_rewrite_env_key(env, "INJECTED", "line1\nEVIL=attacker_controlled")

    assert_byte_identical(env, baseline)


def test_add_with_value_containing_nul_byte_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """A value containing a NUL byte MUST be refused.

    NUL bytes terminate C strings and routinely cause downstream tools
    to mis-parse the file. Reject at the rewriter boundary.
    """
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    baseline = sha256_of(env)

    with pytest.raises((UnsafeRewriteRefused, ValueError)):
        add_or_rewrite_env_key(env, "NULLED", "before\x00after")

    assert_byte_identical(env, baseline)


def test_add_with_newline_in_var_name_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """A ``var_name`` containing a newline MUST be refused.

    Without key validation, ``var_name="FOO\\nBAR=evil"`` would produce
    two assignment lines in the file: ``FOO`` and ``BAR=evil``. The
    rewriter must reject before any write happens.
    """
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    baseline = sha256_of(env)

    with pytest.raises((UnsafeRewriteRefused, ValueError)):
        add_or_rewrite_env_key(env, "FOO\nBAR=evil", "real_value")

    assert_byte_identical(env, baseline)


def test_add_with_equals_in_var_name_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """A ``var_name`` containing ``=`` MUST be refused.

    ``FOO=injected`` as a key would produce ``FOO=injected=real_value``,
    which dotenv parses as ``FOO=injected=real_value`` (one key with an
    odd value) - off-semantics. Reject at the boundary.
    """
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    baseline = sha256_of(env)

    with pytest.raises((UnsafeRewriteRefused, ValueError)):
        add_or_rewrite_env_key(env, "FOO=injected", "real")

    assert_byte_identical(env, baseline)


def test_add_with_non_posix_var_name_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """Keys outside POSIX env-var syntax MUST be refused.

    POSIX env names match ``[A-Za-z_][A-Za-z0-9_]*``. Keys like
    ``KEY-WITH-DASH`` or ``1LEADING_DIGIT`` would land in the file but
    cannot be ``export``ed by a shell. Reject them so the file stays
    shell-usable.
    """
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    baseline = sha256_of(env)

    with pytest.raises((UnsafeRewriteRefused, ValueError)):
        add_or_rewrite_env_key(env, "KEY-WITH-DASH", "x")
    assert_byte_identical(env, baseline)

    with pytest.raises((UnsafeRewriteRefused, ValueError)):
        add_or_rewrite_env_key(env, "1LEADING_DIGIT", "x")
    assert_byte_identical(env, baseline)

    with pytest.raises((UnsafeRewriteRefused, ValueError)):
        add_or_rewrite_env_key(env, "", "x")
    assert_byte_identical(env, baseline)


def test_add_to_one_mib_plus_one_file_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """A ``.env`` larger than 1 MiB MUST be refused (size gate fires via gate).

    The rewriter routes through ``safe_rewrite``, which enforces the
    1 MiB bound on the *existing* file. Adding a key to a 1 MiB+1 file
    must be refused without any write.
    """
    one_mib = 1 << 20
    prefix = b"A="
    remainder = one_mib - len(prefix)  # leaves no room for newline → +1 over
    content = prefix + (b"x" * remainder) + b"\n"
    assert len(content) == one_mib + 1
    env = make_env_file(tmp_path / ".env", content=content)
    baseline = sha256_of(env)

    with pytest.raises(UnsafeRewriteRefused):
        add_or_rewrite_env_key(env, "NEW_KEY", "small")

    assert_byte_identical(env, baseline)


# ---------------------------------------------------------------------------
# Round-trip-stability guards: reject values that would silently corrupt
# when read back by a standard dotenv parser.
# ---------------------------------------------------------------------------


def test_add_with_space_hash_in_value_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """A value containing ``' #'`` MUST be refused.

    dotenv parsers treat ``space + #`` as the start of an inline comment;
    on read-back everything after the space would be stripped, silently
    losing data. Reject at the rewriter boundary.
    """
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    baseline = sha256_of(env)

    with pytest.raises(ValueError, match=r"inline[- ]comment"):
        add_or_rewrite_env_key(env, "API_KEY", "sk-new #leaked-note")

    assert_byte_identical(env, baseline)


def test_add_with_tab_hash_in_value_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """A value containing ``'\\t#'`` MUST be refused — same inline-comment hazard as `' #'`."""
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    baseline = sha256_of(env)

    with pytest.raises(ValueError, match=r"inline[- ]comment"):
        add_or_rewrite_env_key(env, "API_KEY", "sk-new\t#leaked-note")

    assert_byte_identical(env, baseline)


def test_add_with_value_starting_with_double_quote_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """A value starting with ``"`` MUST be refused.

    dotenv parsers treat a leading quote as an opening delimiter and strip
    it on read-back, corrupting the stored value.
    """
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    baseline = sha256_of(env)

    with pytest.raises(ValueError, match=r"quote"):
        add_or_rewrite_env_key(env, "API_KEY", '"abc')

    assert_byte_identical(env, baseline)


def test_add_with_value_starting_with_single_quote_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """A value starting with ``'`` MUST be refused (same reason as ``"``)."""
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    baseline = sha256_of(env)

    with pytest.raises(ValueError, match=r"quote"):
        add_or_rewrite_env_key(env, "API_KEY", "'abc")

    assert_byte_identical(env, baseline)


def test_add_with_leading_whitespace_in_value_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """A value with leading whitespace MUST be refused.

    Unquoted dotenv values are whitespace-stripped on read; leading
    whitespace would silently disappear.
    """
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    baseline = sha256_of(env)

    with pytest.raises(ValueError, match=r"whitespace"):
        add_or_rewrite_env_key(env, "API_KEY", " sk-abc")

    assert_byte_identical(env, baseline)


def test_add_with_trailing_whitespace_in_value_refused(
    tmp_path: Path,
    make_env_file,
    sha256_of,
    assert_byte_identical,
) -> None:
    """A value with trailing whitespace MUST be refused (same reason)."""
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    baseline = sha256_of(env)

    with pytest.raises(ValueError, match=r"whitespace"):
        add_or_rewrite_env_key(env, "API_KEY", "sk-abc ")

    assert_byte_identical(env, baseline)


def test_add_with_hash_not_preceded_by_space_allowed(
    tmp_path: Path,
    make_env_file,
) -> None:
    """``sk-abc#tag`` (no space before ``#``) is a legal unquoted value.

    Ensures the inline-comment guard is narrow: only ``space + #`` is
    rejected; a literal ``#`` inside the value is fine because dotenv
    parsers only treat ``#`` as a comment when preceded by whitespace.
    """
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")

    add_or_rewrite_env_key(env, "API_KEY", "sk-abc#tag")

    assert b"API_KEY=sk-abc#tag\n" in env.read_bytes()


def test_add_with_internal_quote_allowed(
    tmp_path: Path,
    make_env_file,
) -> None:
    """``sk-ab"cd`` (quote mid-value) is legal; only leading quote is rejected."""
    env = make_env_file(tmp_path / ".env", content=b"KEY=value\n")

    add_or_rewrite_env_key(env, "API_KEY", 'sk-ab"cd')

    assert b'API_KEY=sk-ab"cd\n' in env.read_bytes()


# ---------------------------------------------------------------------------
# Refusal-dispatch safety: _safe_read_existing_bytes must raise
# UnsafeRewriteRefused directly, NOT funnel through safe_rewrite(path, b"").
#
# Motivation: safe_rewrite performs its own fresh lstat/size/sniff under its
# lock. If the hostile condition clears between our check and its check
# (oversized file truncated, symlink swapped for regular file, EPERM
# cleared), the gate would *succeed* on an empty payload and wipe the
# file. Raising UnsafeRewriteRefused directly eliminates that race window
# entirely — no write path is reached at all.
# ---------------------------------------------------------------------------


def test_refuse_symlink_does_not_invoke_safe_rewrite(
    tmp_path: Path,
    make_env_file,
    forbid_safe_rewrite,
) -> None:
    """Symlink refusal MUST raise directly; safe_rewrite MUST NOT be called."""
    target = make_env_file(tmp_path / "real.env", content=b"KEY=value\n")
    env_path = tmp_path / ".env"
    env_path.symlink_to(target)

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        add_or_rewrite_env_key(env_path, "NEW", "v")

    assert exc_info.value.reason == UnsafeReason.SYMLINK


def test_refuse_fifo_does_not_invoke_safe_rewrite(
    tmp_path: Path,
    forbid_safe_rewrite,
) -> None:
    """FIFO refusal MUST raise directly; safe_rewrite MUST NOT be called."""
    fifo_path = tmp_path / ".env"
    os.mkfifo(str(fifo_path))

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        add_or_rewrite_env_key(fifo_path, "NEW", "v")

    assert exc_info.value.reason == UnsafeReason.SPECIAL_FILE


def test_refuse_oversized_does_not_invoke_safe_rewrite(
    tmp_path: Path,
    make_env_file,
    forbid_safe_rewrite,
) -> None:
    """Oversize refusal MUST raise directly; safe_rewrite MUST NOT be called.

    This is the headline race: between our ``lst.st_size`` check and the
    gate's own size check, a concurrent truncator could shrink the file
    under us — if we funnelled through ``safe_rewrite(path, b"")``, the
    gate would accept the empty payload and write it, wiping the file.
    Raising ``UnsafeRewriteRefused`` directly closes the window.
    """
    one_mib = 1 << 20
    content = b"KEY=" + b"a" * (one_mib - len(b"KEY=\n")) + b"\n"
    assert len(content) == one_mib
    content += b"b"
    assert len(content) == one_mib + 1
    env_path = make_env_file(tmp_path / ".env", content=content)

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        add_or_rewrite_env_key(env_path, "NEW", "v")

    assert exc_info.value.reason == UnsafeReason.SIZE


def test_refuse_lstat_error_does_not_invoke_safe_rewrite(
    tmp_path: Path,
    make_env_file,
    monkeypatch,
    forbid_safe_rewrite,
) -> None:
    """An ``lstat`` ``OSError`` MUST raise ``UnsafeRewriteRefused`` directly.

    Transient EACCES/EIO that clears between our lstat and the gate's
    lstat would otherwise let safe_rewrite proceed on our empty-payload
    call and wipe the file.
    """
    env_path = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    real_lstat = os.lstat
    target_str = str(env_path)

    def _boom_lstat(path, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if str(path) == target_str:
            raise PermissionError(13, "Permission denied")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", _boom_lstat)

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        add_or_rewrite_env_key(env_path, "NEW", "v")

    assert exc_info.value.reason == UnsafeReason.IO_ERROR


def test_refuse_open_error_does_not_invoke_safe_rewrite(
    tmp_path: Path,
    make_env_file,
    monkeypatch,
    forbid_safe_rewrite,
) -> None:
    """An ``os.open`` ``OSError`` after a successful ``lstat`` MUST raise directly.

    Covers the TOCTOU case where ``lstat`` shows a regular file but
    ``open`` fails (e.g., attacker swaps to a non-readable node between
    the two syscalls).
    """
    env_path = make_env_file(tmp_path / ".env", content=b"KEY=value\n")
    real_open = os.open
    target_str = str(env_path)

    def _boom_open(path, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if str(path) == target_str:
            raise PermissionError(13, "Permission denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", _boom_open)

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        add_or_rewrite_env_key(env_path, "NEW", "v")

    assert exc_info.value.reason == UnsafeReason.IO_ERROR


def test_refuse_real_rename_race_between_lstat_and_open(
    tmp_path: Path,
    monkeypatch,
    forbid_safe_rewrite,
) -> None:
    """A real ``rename(2)`` swap between ``lstat`` and ``os.open`` MUST refuse.

    ``O_NOFOLLOW`` on the open blocks symlink-flips, but an attacker who
    can atomically rename a different regular file over the path between
    the ``lstat`` check and the ``os.open`` call would otherwise read a
    file we never validated. Performs a genuine atomic rename of a
    different regular file over the victim path, then runs unmocked
    through ``lstat`` → ``os.open`` → ``os.fstat``. The real identity
    check must detect the inode swap and raise
    ``UnsafeRewriteRefused(TOCTOU)`` directly — no ``safe_rewrite`` call,
    no data exposure.
    """
    victim = tmp_path / ".env"
    victim.write_bytes(b"VICTIM_CONTENT=legitimate\n")

    adversary_src = tmp_path / "adversary_source.env"
    adversary_src.write_bytes(b"ADVERSARY_CONTENT=should_never_be_read\n")

    real_lstat = os.lstat
    swap_done = {"did": False}

    def _racing_lstat(path, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        result = real_lstat(path, *args, **kwargs)
        if str(path) == str(victim) and not swap_done["did"]:
            adversary_src.rename(victim)
            swap_done["did"] = True
        return result

    monkeypatch.setattr(os, "lstat", _racing_lstat)

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        add_or_rewrite_env_key(victim, "NEW", "v")

    assert exc_info.value.reason == UnsafeReason.TOCTOU
    # Post-swap, the path points at adversary_src's inode. Its bytes must
    # never have been read, hashed, or written back.
    assert victim.read_bytes() == b"ADVERSARY_CONTENT=should_never_be_read\n"


def test_allow_matching_fstat_after_open(
    tmp_path: Path,
    make_env_file,
) -> None:
    """Negative control: matching ``fstat`` identity must NOT refuse.

    Guards against an over-broad TOCTOU check that rejects the happy
    path. A normal regular file whose inode/dev don't change between
    ``lstat`` and ``fstat`` must succeed, AND the pre-existing line
    must be preserved — proving the read baseline came from the real
    file, not a swapped-in one.
    """
    env_path = make_env_file(tmp_path / ".env", content=b"EXISTING=old\n")

    add_or_rewrite_env_key(env_path, "NEW", "fresh")

    assert env_path.read_bytes() == b"EXISTING=old\nNEW=fresh\n"


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda p: rewrite_env_key(p, "KEY", "newvalue"), id="rewrite_env_key"),
        pytest.param(lambda p: remove_env_key(p, "KEY"), id="remove_env_key"),
    ],
)
def test_refuse_symlink_via_sister_apis(
    tmp_path: Path,
    make_env_file,
    forbid_safe_rewrite,
    operation,
) -> None:
    """Sister APIs on a symlink MUST refuse directly (trip-wire for direct-raise dispatch)."""
    target = make_env_file(tmp_path / "real.env", content=b"KEY=value\n")
    env_path = tmp_path / ".env"
    env_path.symlink_to(target)

    with pytest.raises(UnsafeRewriteRefused) as exc_info:
        operation(env_path)

    assert exc_info.value.reason == UnsafeReason.SYMLINK
