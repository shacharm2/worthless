"""Abstract backend interface for the sidecar.

The sidecar server speaks only to :class:`Backend`; concrete backends
(Fernet today, KMS/MPC later) are swappable without touching server code.

See ``engineering/ipc-contract.md`` \u00a7Ops for the wire-level semantics of
``seal`` / ``open`` / ``attest`` that every backend implementation must
honour.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BackendError(Exception):
    """Raised by backend implementations on crypto / key failures.

    The error message is deliberately fixed and information-free: backends
    MUST NOT echo ciphertext, plaintext, key material, or the underlying
    exception text. Callers that need diagnostics should rely on structured
    logging inside the backend, not on exception contents.
    """


class Backend(ABC):
    """Abstract crypto backend for the sidecar.

    v1.1 implementation: :class:`worthless.sidecar.backends.fernet.FernetBackend`.
    v2.0 will add KMS and MPC backends; the sidecar server talks only to
    this interface so backends are swappable without touching server code.

    All methods are async to allow future backends to do I/O (KMS calls,
    MPC rounds). The Fernet backend implements them synchronously under
    the hood but keeps the async signature.
    """

    @abstractmethod
    async def seal(self, plaintext: bytes, context: bytes | None = None) -> bytes:
        """Encrypt ``plaintext``; return opaque ciphertext bytes."""

    @abstractmethod
    async def open(
        self,
        ciphertext: bytes,
        context: bytes | None = None,
        key_id: bytes | None = None,
    ) -> bytes:
        """Decrypt ``ciphertext``; return plaintext."""

    @abstractmethod
    async def attest(self, nonce: bytes, purpose: str | None = None) -> bytes:
        """Return opaque evidence binding ``nonce`` to backend identity."""
