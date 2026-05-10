"""Lock command -- scan .env, split keys (format-preserving), store shards."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import typer

from worthless.cli.bootstrap import WorthlessHome, acquire_lock, get_home
from worthless.cli.commands.up import _resolve_port
from worthless.cli.console import get_console
from worthless.cli.dotenv_rewriter import (
    build_enrolled_locations,
    rewrite_env_keys,
    scan_env_keys,
)
from worthless.cli.errors import (
    ErrorCode,
    UnsafeReason,
    UnsafeRewriteRefused,
    WorthlessError,
    error_boundary,
    sanitize_exception,
)
from worthless.cli.key_patterns import CANONICAL_KEY_VAR_RE, detect_prefix
from worthless.cli.providers import lookup_by_name, lookup_by_url
from worthless.crypto.reconstruction import (
    _verify_commitment,  # noqa: PLC2701 — intentional internal use for re-lock guard
)
from worthless.crypto.splitter import (
    derive_shard_a_fp,
    reconstruct_key_fp,
    split_key_fp,
)
from worthless.crypto.types import zero_buf
from worthless.exceptions import ShardTamperedError
from worthless.storage.repository import ShardRepository, StoredShard

logger = logging.getLogger(__name__)

# Map of supported wire protocols → canonical fallback env var name.
# Used only when the user's API-key var doesn't follow the ``<NAME>_API_KEY``
# convention. After 8rqs, ``wrap`` no longer owns this — lock has the only
# remaining consumer (plus ``unlock`` which imports it), so the map lives here.
#
# Keys here are WIRE PROTOCOLS (``openai``, ``anthropic``), NOT registry
# provider names. Post-HF1, ``detect_provider`` can return registry names
# like ``openrouter`` / ``groq`` / ``together`` that all speak the
# ``openai`` wire protocol. ``_pass1_db_writes`` translates the detected
# registry name to its protocol via ``lookup_by_name`` before checking
# this map. ``worthless-9t74`` will promote that translation into a
# structural ``provider name`` vs ``protocol`` separation post-merge.
_PROVIDER_ENV_MAP: dict[str, str] = {
    "openai": "OPENAI_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
}

_SUPPORTED_PROTOCOLS = frozenset(_PROVIDER_ENV_MAP.keys())
_ALIAS_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _make_alias(provider: str, api_key: str) -> str:
    """Deterministic alias: provider + first 8 hex chars of sha256(key)."""
    digest = hashlib.sha256(bytearray(api_key.encode())).hexdigest()[:8]  # nosec B303 -- non-cryptographic fingerprint
    return f"{provider}-{digest}"


def _proxy_base_url(alias: str) -> str:
    """Build the proxy BASE_URL for a given alias."""
    return f"http://127.0.0.1:{_resolve_port(None)}/{alias}/v1"


def _derive_base_url_var(var_name: str, provider: str) -> str:
    """Derive the corresponding ``*_BASE_URL`` variable name for a key var.

    Convention: ``OPENROUTER_API_KEY`` → ``OPENROUTER_BASE_URL``. Falls
    back to the canonical ``_PROVIDER_ENV_MAP`` mapping when the key var
    name doesn't end in ``_API_KEY``.
    """
    if var_name.endswith("_API_KEY"):
        return var_name[: -len("_API_KEY")] + "_BASE_URL"
    return _PROVIDER_ENV_MAP.get(provider, "OPENAI_BASE_URL")


def _resolve_upstream_base_url(
    base_url_var: str, env_values: dict[str, str | None], provider: str
) -> str:
    """Pick the upstream URL for the DB row.

    Prefers the user's explicit ``*_BASE_URL`` value from ``.env`` when set
    AND when that URL is in the provider registry. Otherwise falls back to
    the bundled registry default for the provider.

    Refuses unregistered user URLs (M3 / Blocker #1): an attacker who can
    write to .env should not be able to redirect the proxy at an arbitrary
    upstream. worthless-rzi1 (P1 follow-up) adds per-request re-validation
    to close the post-lock-tamper variant; worthless-8fbg adds RFC1918 /
    loopback hardening. See seam 2 in worthless-8rqs design notes.
    """
    user_value = env_values.get(base_url_var)
    if user_value:
        if lookup_by_url(user_value) is None:
            raise WorthlessError(
                ErrorCode.INVALID_INPUT,
                f"unknown upstream URL {user_value!r} from {base_url_var}. "
                "Register it first: 'worthless providers register --name <n> "
                "--url <url> --protocol openai|anthropic'.",
            )
        return user_value
    entry = lookup_by_name(provider)
    if entry is None:  # pragma: no cover — provider is validated above
        return "https://api.openai.com/v1"
    return entry.url


@dataclass(eq=False)
class _PlannedUpdate:
    """One key's in-flight lock plan — built pass-1, consumed by hook + unwind."""

    alias: str
    var_name: str
    env_path_str: str
    provider: str
    shard_a: bytearray
    shard_b: bytearray
    commitment: bytearray
    nonce: bytearray
    prefix: str
    charset: str
    was_fresh_enroll: bool
    # 8rqs Phase 7: per-enrollment base_url plumbing. ``base_url_var`` is the
    # .env variable name to write the local proxy URL to (e.g.
    # ``OPENROUTER_BASE_URL`` for ``OPENROUTER_API_KEY``); preserves the
    # user's naming convention rather than forcing OPENAI_BASE_URL.
    base_url_var: str = ""

    def zero(self) -> None:
        for buf in (self.shard_a, self.shard_b, self.commitment, self.nonce):
            buf[:] = b"\x00" * len(buf)


def _build_verify_hook(planned: list[_PlannedUpdate]):
    """Return a zero-arg hook that raises on any shard round-trip failure.

    Fires inside ``safe_rewrite`` after the tmp fsync but before the atomic
    rename — see ``safe_rewrite._hook_before_replace``. Raising aborts the
    rename, leaving ``.env`` byte-identical.
    """

    def _hook() -> None:
        for p in planned:
            reconstructed: bytearray | None = None
            try:
                reconstructed = reconstruct_key_fp(
                    p.shard_a,
                    p.shard_b,
                    p.commitment,
                    p.nonce,
                    p.prefix,
                    p.charset,
                )
            except ShardTamperedError:
                # from None: drop traceback chain holding shard-material locals.
                raise UnsafeRewriteRefused(UnsafeReason.VERIFY_FAILED) from None
            except ValueError as exc:
                raise WorthlessError(
                    ErrorCode.SHARD_STORAGE_FAILED,
                    sanitize_exception(exc, generic="shard reconstruction malformed"),
                ) from None
            except Exception:  # noqa: BLE001 — preserve opaque refusal contract
                raise UnsafeRewriteRefused(UnsafeReason.VERIFY_FAILED) from None
            finally:
                if reconstructed is not None:
                    zero_buf(reconstructed)

    return _hook


async def _pass1_db_writes(
    repo: ShardRepository,
    candidates: list[tuple[str, str, str]],
    env_str: str,
    token_budget_daily: int | None,
    planned_out: list[_PlannedUpdate],
    env_values: dict[str, str | None],
) -> None:
    """Do every DB write; append each ``_PlannedUpdate`` to *planned_out*.

    MUTATES *planned_out* so partial-failure paths still expose the
    bytearrays the caller's ``finally`` needs to zero.

    *env_values* is the parsed ``.env`` (from ``dotenv_values``) so we can
    read the user's existing ``*_BASE_URL`` value at lock time and store
    that as the upstream URL in the DB.
    """
    for var_name, value, detected_provider in candidates:
        # Translate registry-name → wire-protocol. Post-HF1,
        # ``detect_provider`` returns registry names like ``openrouter``
        # for OpenAI-protocol-compatible services. Lock's downstream uses
        # (alias building, _PROVIDER_ENV_MAP fallback, _SUPPORTED_PROTOCOLS
        # gate, DB ``provider`` column for proxy adapter dispatch) all
        # need the WIRE PROTOCOL, not the registry name.
        #
        # Pre-HF1, detect_provider only returned ``openai``/``anthropic``,
        # so name == protocol and this translation was a no-op. Post-HF1
        # we MUST resolve via the registry or OpenRouter keys would be
        # silently skipped, leaving the plaintext key in .env (the exact
        # leak 8rqs is meant to close). worthless-9t74 will promote this
        # into a structural separation of name/protocol post-merge.
        registry_entry = lookup_by_name(detected_provider)
        protocol = registry_entry.protocol if registry_entry else detected_provider

        if protocol not in _SUPPORTED_PROTOCOLS:
            get_console().print_warning(
                f"Skipping {var_name}: wire protocol {protocol!r} not yet "
                f"supported (detected provider: {detected_provider!r})"
            )
            continue

        # Use the wire protocol for everything downstream so adapter
        # dispatch in the proxy picks the right format, and so the alias
        # namespace stays stable across pre- and post-HF1 enrollments.
        provider = protocol

        base_url_var = _derive_base_url_var(var_name, provider)

        # M4 (Blocker #2): warn on non-canonical var names. Apps that read
        # MY_OPENAI_KEY directly (instead of OPENAI_API_KEY) and construct
        # the SDK client without base_url= will fall through to the real
        # provider — bypassing the proxy and leaking shard-A on the wire.
        # Soft warning per product-manager review; worthless-v5sy adds
        # --strict for CI/team policy. See seam 5 in worthless-8rqs notes.
        if not CANONICAL_KEY_VAR_RE.match(var_name):
            get_console().print_warning(
                f"env var {var_name!r} doesn't match the canonical "
                f"'<PROVIDER>_API_KEY' pattern. lock will set "
                f"{base_url_var}, but if your app reads {var_name} "
                f"without auto-detecting {base_url_var}, requests will "
                f"bypass the proxy and send shard-A upstream. Rename to "
                f"follow <PROVIDER>_API_KEY to silence this warning."
            )

        upstream_base_url = _resolve_upstream_base_url(base_url_var, env_values, provider)

        alias = _make_alias(provider, value)
        db_shard = await repo.fetch_encrypted(alias)

        if db_shard is not None:
            if not db_shard.prefix or not db_shard.charset:
                raise WorthlessError(
                    ErrorCode.SHARD_STORAGE_FAILED,
                    f"Alias {alias!r} predates format-preserving split. "
                    "Run `worthless unlock --all` then re-lock this .env.",
                )
            stored_decrypted = await repo.decrypt_shard(db_shard)
            verify_payload = bytearray(value.encode("utf-8"))
            try:
                try:
                    _verify_commitment(
                        verify_payload,
                        stored_decrypted.commitment,
                        stored_decrypted.nonce,
                    )
                except ShardTamperedError as exc:
                    raise WorthlessError(
                        ErrorCode.SHARD_STORAGE_FAILED,
                        f"Alias {alias!r} exists but {var_name} does not match "
                        "the originally-locked key (commitment mismatch).",
                    ) from exc

                derived_shard_a = derive_shard_a_fp(
                    value,
                    stored_decrypted.shard_b,
                    db_shard.prefix,
                    db_shard.charset,
                )
                await repo.add_enrollment(alias, var_name=var_name, env_path=env_str)
                planned_out.append(
                    _PlannedUpdate(
                        alias=alias,
                        var_name=var_name,
                        env_path_str=env_str,
                        provider=provider,
                        shard_a=derived_shard_a,
                        shard_b=bytearray(stored_decrypted.shard_b),
                        commitment=bytearray(stored_decrypted.commitment),
                        nonce=bytearray(stored_decrypted.nonce),
                        prefix=db_shard.prefix,
                        charset=db_shard.charset,
                        was_fresh_enroll=False,
                        base_url_var=base_url_var,
                    )
                )
            finally:
                zero_buf(verify_payload)
                stored_decrypted.zero()
            continue

        # Fresh enroll
        try:
            prefix = detect_prefix(value, provider)
        except ValueError:
            prefix = ""
        sr = split_key_fp(value, prefix, provider)
        try:
            stored = StoredShard(
                shard_b=sr.shard_b,
                commitment=sr.commitment,
                nonce=sr.nonce,
                provider=provider,
            )
            await repo.store_enrolled(
                alias,
                stored,
                var_name=var_name,
                env_path=env_str,
                token_budget_daily=token_budget_daily,
                prefix=sr.prefix,
                charset=sr.charset,
                base_url=upstream_base_url,
            )
            planned_out.append(
                _PlannedUpdate(
                    alias=alias,
                    var_name=var_name,
                    env_path_str=env_str,
                    provider=provider,
                    shard_a=bytearray(sr.shard_a),
                    shard_b=bytearray(sr.shard_b),
                    commitment=bytearray(sr.commitment),
                    nonce=bytearray(sr.nonce),
                    prefix=sr.prefix,
                    charset=sr.charset,
                    was_fresh_enroll=True,
                    base_url_var=base_url_var,
                )
            )
        finally:
            sr.zero()


def _batch_rewrite(
    env_path: Path,
    planned: list[_PlannedUpdate],
    keys_only: bool,
    existing_env_keys: set[str],
) -> None:
    """One ``safe_rewrite`` call for every planned update + BASE_URL changes."""
    updates: dict[str, str] = {p.var_name: p.shard_a.decode("utf-8") for p in planned}
    additions: dict[str, str] = {}
    if not keys_only:
        for p in planned:
            local_proxy = _proxy_base_url(p.alias)
            if p.base_url_var in existing_env_keys:
                # User already has e.g. OPENROUTER_BASE_URL=<upstream>;
                # rewrite the value to the local proxy URL (the upstream
                # value was already captured into the DB at pass-1 time).
                updates[p.base_url_var] = local_proxy
            elif (
                p.base_url_var and p.base_url_var not in updates and p.base_url_var not in additions
            ):
                # Fresh: lock auto-creates the BASE_URL var with a one-line
                # stderr notice so the user knows something was added.
                additions[p.base_url_var] = local_proxy
                sys.stderr.write(
                    f"worthless: added {p.base_url_var}={local_proxy} to {env_path} (was missing)\n"
                )
                sys.stderr.flush()

    rewrite_env_keys(
        env_path,
        updates,
        additions=additions or None,
        _hook_before_replace=_build_verify_hook(planned),
    )


async def _compensating_unwind(
    repo: ShardRepository, planned: list[_PlannedUpdate]
) -> list[Exception]:
    """Best-effort rollback of pass-1 DB writes. Returns list of failures."""
    errors: list[Exception] = []
    for p in reversed(planned):
        try:
            await repo.delete_enrollment(p.alias, p.env_path_str)
            if p.was_fresh_enroll:
                # Fresh-enroll created this shard + its only enrollment in this
                # same pass — delete_enrollment above just removed the last one.
                await repo.delete_enrolled(p.alias)
        except Exception as exc:  # noqa: BLE001 — keep unwinding subsequent aliases
            logger.debug("Unwind failed for alias %s", p.alias, exc_info=True)
            errors.append(exc)
    return errors


def _lock_keys(
    env_path: Path,
    home: WorthlessHome,
    provider_override: str | None = None,
    token_budget_daily: int | None = None,
    quiet: bool = False,
    keys_only: bool = False,
) -> int:
    """Transactional multi-key lock.

    Pass-1 does every DB write and builds a ``_PlannedUpdate`` list. A single
    ``rewrite_env_keys`` call then updates ``.env`` atomically, with a verify
    hook that reconstructs every shard before the rename commits. On any
    failure the DB writes are unwound so the system is consistent either way.
    """
    console = get_console()

    if not env_path.exists():
        raise WorthlessError(ErrorCode.ENV_NOT_FOUND, f"File not found: {env_path}")
    if env_path.is_symlink():
        raise WorthlessError(ErrorCode.ENV_NOT_FOUND, f"Refusing to follow symlink: {env_path}")

    if not quiet:
        console.print_hint(f"Scanning {env_path} for API keys...")

    async def _lock_async() -> int:
        from dotenv import dotenv_values  # noqa: PLC0415 — local import keeps test surface tight

        repo = ShardRepository(str(home.db_path), home.fernet_key)
        await repo.initialize()

        env_str = str(env_path.resolve())
        all_enrollments = await repo.list_enrollments()
        enrolled_locations = build_enrolled_locations(all_enrollments)

        scanned = scan_env_keys(env_path, enrolled_locations=enrolled_locations)
        if not scanned:
            console.print_warning("No unprotected API keys found.")
            return 0

        # 8rqs Phase 7: snapshot the full .env so _pass1 can read existing
        # *_BASE_URL values and pull them into the DB row.
        env_values = dict(dotenv_values(env_path))

        candidates = [
            (var_name, value, provider_override or detected_provider)
            for var_name, value, detected_provider in scanned
        ]
        existing_env_keys = set(env_values.keys())

        planned: list[_PlannedUpdate] = []
        try:
            if not quiet:
                # HF2 UX: name the keys so the user knows exactly which env
                # vars are being touched — prior "Protecting N key(s)..."
                # was opaque about which secrets just changed.
                key_names = ", ".join(var_name for var_name, _, _ in candidates)
                console.print_hint(f"  Protecting {key_names}...")
            # 8rqs Phase 7: env_values flows through so _pass1_db_writes can
            # read each key's matching *_BASE_URL from .env at lock time.
            await _pass1_db_writes(
                repo, candidates, env_str, token_budget_daily, planned, env_values
            )
            if not planned:
                return 0
            _batch_rewrite(env_path, planned, keys_only, existing_env_keys)
            return len(planned)
        except Exception:
            if planned:
                unwind_errors = await _compensating_unwind(repo, planned)
                if unwind_errors:
                    console.print_warning(
                        f"Database may contain {len(unwind_errors)} stale row(s); "
                        "run `worthless unlock --all` to reconcile."
                    )
            raise
        finally:
            for p in planned:
                p.zero()

    count = asyncio.run(_lock_async())

    if count and env_path.exists():
        current = env_path.stat().st_mode
        if current & (stat.S_IRWXG | stat.S_IRWXO):
            env_path.chmod(current & ~(stat.S_IRWXG | stat.S_IRWXO))

    if not quiet:
        if count:
            # Tell the story: keys are now split between this machine and the
            # OS keystore, so the .env file no longer holds usable secrets.
            # Prior "{count} key(s) protected." read like a unit test (UX P1).
            console.print_success(
                f"Done. {count} key(s) split between this machine and your "
                f"system keystore — {env_path.name} no longer contains a "
                f"usable secret."
            )
            console.print_hint(
                "Next: run `worthless wrap <command>` or `worthless up` for daemon mode"
            )
        else:
            console.print_warning("No unprotected API keys found.")

    return count


def _enroll_single(
    alias: str,
    key: str,
    provider: str,
    home: WorthlessHome,
) -> None:
    """Enroll a single key (no .env scanning).

    Write order: DB first — matching _lock_keys pattern.
    Compensation on failure: clean up the DB row.
    """
    if not _ALIAS_RE.match(alias):
        raise WorthlessError(ErrorCode.SCAN_ERROR, f"Invalid alias: {alias!r}")

    try:
        prefix = detect_prefix(key, provider)
    except ValueError:
        prefix = ""

    sr = split_key_fp(key, prefix, provider)

    async def _enroll_async():
        repo = ShardRepository(str(home.db_path), home.fernet_key)
        await repo.initialize()

        existing = await repo.fetch_encrypted(alias)
        if existing is not None:
            raise WorthlessError(
                ErrorCode.SCAN_ERROR,
                f"Alias {alias!r} is already enrolled",
            )

        stored = StoredShard(
            shard_b=sr.shard_b,
            commitment=sr.commitment,
            nonce=sr.nonce,
            provider=provider,
        )
        # 8rqs: enroll falls back to the registry default URL — _enroll_single
        # is the no-.env path so there's no user var to read.
        registry_default = lookup_by_name(provider)
        base_url = registry_default.url if registry_default else None
        await repo.store_enrolled(
            alias,
            stored,
            var_name=alias,
            env_path=None,
            prefix=sr.prefix,
            charset=sr.charset,
            base_url=base_url,
        )

    try:
        asyncio.run(_enroll_async())
    except WorthlessError:
        raise
    except Exception as exc:
        raise WorthlessError(
            ErrorCode.SHARD_STORAGE_FAILED,
            sanitize_exception(exc, generic="failed to enroll key"),
        ) from exc
    finally:
        sr.zero()

    console = get_console()
    console.print_success(f"Enrolled {alias} ({provider}).")


def register_lock_commands(app: typer.Typer) -> None:
    """Register lock and enroll commands on the Typer app."""

    @app.command()
    @error_boundary
    def lock(
        env: Path = typer.Option(Path(".env"), "--env", "-e", help="Path to .env file"),
        provider: str | None = typer.Option(
            None, "--provider", "-p", help="Override provider auto-detection"
        ),
        token_budget_daily: int | None = typer.Option(
            None, "--token-budget-daily", help="Daily token budget limit"
        ),
        keys_only: bool = typer.Option(
            False, "--keys-only", help="Only rewrite API keys (skip BASE_URL)"
        ),
    ) -> None:
        """Protect API keys in a .env file."""
        # Pre-announce the macOS Keychain dialog so users aren't surprised by a
        # system prompt mid-command. The dialog labels itself "python3.10" not
        # "worthless"; without this hint, first-time users panic and click Deny.
        # Per HF2 (worthless-mnlp) the prompt fires at most once per invocation
        # (cache + lock + probe-via-property collapse 3+ → 1).
        if sys.platform == "darwin":
            console = get_console()
            console.print_hint(
                "macOS may ask once to access your Keychain — click 'Always Allow' "
                "so we don't ask again."
            )
        home = get_home()
        with acquire_lock(home):
            _lock_keys(
                env,
                home,
                provider_override=provider,
                token_budget_daily=token_budget_daily,
                keys_only=keys_only,
            )

    @app.command()
    @error_boundary
    def enroll(
        alias: str = typer.Option(..., "--alias", "-a", help="Key alias"),
        key: str | None = typer.Option(
            None,
            "--key",
            "-k",
            help="API key (use --key-stdin instead to avoid shell history)",
        ),
        key_stdin: bool = typer.Option(False, "--key-stdin", help="Read API key from stdin"),
        provider: str = typer.Option(..., "--provider", "-p", help="Provider name"),
    ) -> None:
        """Enroll a single API key (scripting/CI primitive)."""
        home = get_home()

        if key_stdin:
            actual_key = sys.stdin.readline().strip()
            if not actual_key:
                raise WorthlessError(ErrorCode.KEY_NOT_FOUND, "No key provided on stdin")
        elif key:
            actual_key = key
        else:
            raise WorthlessError(ErrorCode.KEY_NOT_FOUND, "Provide --key or --key-stdin")

        with acquire_lock(home):
            _enroll_single(alias, actual_key, provider, home)
