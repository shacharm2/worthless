"""Storage models — pure dataclasses, no cryptography import.

Split out of ``repository.py`` (WOR-309 Phase 2) so the proxy can import
shard records without transitively pulling Fernet. The proxy now
delegates decryption to the sidecar over IPC; it only needs the
ciphertext-at-rest record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple


class EncryptedShard(NamedTuple):
    """Raw encrypted shard record — no Fernet decryption applied."""

    shard_b_enc: bytes
    commitment: bytes
    nonce: bytes
    provider: str
    prefix: str | None = None
    charset: str | None = None
    # worthless-8rqs: per-enrollment upstream URL. None means the row was
    # created before 8rqs landed; Phase-6 readers refuse to use it and prompt
    # the user to re-lock.
    base_url: str | None = None

    def __repr__(self) -> str:
        return (
            f"EncryptedShard(shard_b_enc=<{len(self.shard_b_enc)} bytes>, "
            f"commitment=<{len(self.commitment)} bytes>, "
            f"nonce=<{len(self.nonce)} bytes>, provider={self.provider!r}, "
            f"prefix={self.prefix!r}, base_url={self.base_url!r})"
        )


@dataclass
class EnrollmentRecord:
    """A single enrollment binding a key alias to a var name and optional env path.

    ``provider`` is denormalized via JOIN with ``shards`` at load time so
    callers don't need a separate lookup or alias-prefix parsing. The
    canonical source remains ``shards.provider``; this is a read-side
    convenience.
    """

    key_alias: str
    var_name: str
    env_path: str | None = None
    decoy_hash: str | None = field(default=None, repr=False)
    provider: str | None = None


@dataclass
class StoredShard:
    """Decrypted shard record with bytearray fields (SR-01 compliance)."""

    shard_b: bytearray
    commitment: bytearray
    nonce: bytearray
    provider: str

    def __repr__(self) -> str:
        return (
            f"StoredShard(shard_b=<{len(self.shard_b)} bytes>, "
            f"commitment=<{len(self.commitment)} bytes>, "
            f"nonce=<{len(self.nonce)} bytes>, provider={self.provider!r})"
        )

    def zero(self) -> None:
        """Zero all cryptographic fields in place (SR-02)."""
        for buf in (self.shard_b, self.commitment, self.nonce):
            buf[:] = b"\x00" * len(buf)
