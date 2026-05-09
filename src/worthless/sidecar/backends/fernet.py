"""Fernet-backed sidecar crypto backend (v1.1).

Reconstructs a Fernet key from two equal-length XOR shares, then exposes
``seal`` / ``open`` / ``attest`` per ``engineering/ipc-contract.md`` \u00a7Ops.

Security notes:
    * The reconstructed key lives on the instance but is never serialised
      (``__repr__`` is redacted).
    * ``open`` / ``attest`` never leak ciphertext, plaintext, share bytes,
      or underlying exception messages; callers get a fixed safe string.
    * ``context`` is advisory in v1.1 (Fernet has no AAD binding);
      v2.0 backends are expected to bind it cryptographically.
"""

from __future__ import annotations

import hmac
import logging
from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from worthless.sidecar.backends.base import Backend, BackendError

__all__ = ["FernetBackend"]

_LOG = logging.getLogger(__name__)

_ATTEST_SALT = b"worthless-attest-v1"
_ATTEST_INFO = b"attest"
_ATTEST_SECRET_LEN = 32


class FernetBackend(Backend):
    """Fernet backend reconstructed from two XOR shares."""

    __slots__ = ("_fernet", "_attest_secret")

    def __init__(self, shares: tuple[bytes, bytes]) -> None:
        share_a, share_b = shares
        if len(share_a) != len(share_b):
            raise ValueError(
                f"FernetBackend: shares must be equal length; got {len(share_a)} and {len(share_b)}"
            )

        key = bytes(a ^ b for a, b in zip(share_a, share_b, strict=True))

        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError):
            # Do NOT chain the underlying exception — its message may echo
            # key bytes. Use `from None` to suppress the cause entirely.
            raise ValueError("FernetBackend: reconstructed key is not a valid Fernet key") from None

        # Derive a dedicated attest secret so ``attest`` output cannot be
        # used to recover or probe the Fernet key directly.
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=_ATTEST_SECRET_LEN,
            salt=_ATTEST_SALT,
            info=_ATTEST_INFO,
        )
        self._attest_secret = hkdf.derive(key)

    # ------------------------------------------------------------------ repr
    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<FernetBackend key_id=<redacted>>"

    # ------------------------------------------------------------------ ops
    async def seal(self, plaintext: bytes, context: bytes | None = None) -> bytes:
        if context is not None:
            _LOG.debug(
                "FernetBackend.seal: context provided but not cryptographically bound (v1.1)"
            )
        return self._fernet.encrypt(plaintext)

    async def open(
        self,
        ciphertext: bytes,
        context: bytes | None = None,
        key_id: bytes | None = None,
    ) -> bytes:
        # ``key_id`` is accepted for contract compatibility with future
        # multi-key backends; v1.1 has a single key and ignores it.
        if context is not None:
            _LOG.debug(
                "FernetBackend.open: context provided but not cryptographically bound (v1.1)"
            )
        if key_id is not None:
            _LOG.debug("FernetBackend.open: key_id accepted but ignored in v1.1")

        try:
            return self._fernet.decrypt(ciphertext)
        except InvalidToken:
            raise BackendError("BACKEND: decryption failed") from None
        except (TypeError, ValueError):
            # Malformed input (non-bytes, wrong shape) — same safe message.
            raise BackendError("BACKEND: decryption failed") from None

    async def attest(self, nonce: bytes, purpose: str | None = None) -> bytes:
        purpose_bytes = purpose.encode("utf-8") if purpose is not None else b""
        # Length-prefix each component so distinct (nonce, purpose) pairs map
        # to distinct MAC inputs. Without this, variable-length ``nonce``
        # concatenated with free-form ``purpose`` produces collisions across
        # purposes — e.g. ``attest(b"abc", "de")`` and ``attest(b"abcde", "")``
        # would hash the same bytes, letting an attacker forge a
        # ``decrypt``-purpose attestation from a ``liveness`` one once a
        # verifier exists. Fix: Q-prefix (8 bytes BE) each component.
        message = (
            len(nonce).to_bytes(8, "big")
            + nonce
            + len(purpose_bytes).to_bytes(8, "big")
            + purpose_bytes
        )
        mac = hmac.new(self._attest_secret, message, sha256)
        return mac.digest()
