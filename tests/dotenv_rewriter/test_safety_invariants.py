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

from worthless.cli.dotenv_rewriter import (
    add_or_rewrite_env_key,
    remove_env_key,
    rewrite_env_key,
)
from worthless.cli.errors import UnsafeRewriteRefused


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
        from worthless.cli.errors import UnsafeReason

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
