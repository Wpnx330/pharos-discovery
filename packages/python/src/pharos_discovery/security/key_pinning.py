"""Key pinning — verify server public keys against pinned SHA-256 hashes."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from pharos_discovery.errors import SignatureVerificationFailed


class KeyPinStore:
    """Persisted store of pinned public-key hashes keyed by server ID.

    Call :meth:`pin` to record a server's public-key SHA-256 hash, then
    :meth:`verify` with the raw public key to confirm it matches.
    Mismatches raise :class:`SignatureVerificationFailed`.
    """

    def __init__(self) -> None:
        self._pins: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- hashing
    @staticmethod
    def hash_public_key(public_key: str) -> str:
        """Return the hex SHA-256 digest of *public_key*."""
        return hashlib.sha256(public_key.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ API
    def pin(self, server_id: str, public_key_hash: str) -> None:
        """Pin *public_key_hash* for *server_id*."""
        with self._lock:
            self._pins[server_id] = public_key_hash

    def unpin(self, server_id: str) -> None:
        """Remove a pin (no-op if absent)."""
        with self._lock:
            self._pins.pop(server_id, None)

    def is_pinned(self, server_id: str) -> bool:
        with self._lock:
            return server_id in self._pins

    def verify(self, server_id: str, public_key: str) -> bool:
        """Verify *public_key* for *server_id*.

        Returns ``True`` if the key matches the pin. Raises
        :class:`SignatureVerificationFailed` if a pin exists but the key
        differs. Returns ``True`` if no pin exists (no pin = no constraint).
        """
        with self._lock:
            pinned = self._pins.get(server_id)
        if pinned is None:
            return True
        actual = self.hash_public_key(public_key)
        if actual != pinned:
            raise SignatureVerificationFailed(
                f"Public key hash mismatch for server '{server_id}': "
                f"expected {pinned}, got {actual}"
            )
        return True

    # ------------------------------------------------------------ persistence
    def save(self, path: str) -> None:
        with self._lock:
            data = dict(self._pins)
        Path(path).write_text(json.dumps(data, indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: str) -> "KeyPinStore":
        inst = cls()
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text())
            with inst._lock:
                inst._pins = {str(k): str(v) for k, v in data.items()}
        return inst

    # ----------------------------------------------------------- dunders
    def __len__(self) -> int:
        with self._lock:
            return len(self._pins)

    def __contains__(self, server_id: object) -> bool:
        return self.is_pinned(str(server_id))

    def __repr__(self) -> str:  # pragma: no cover - trivial
        with self._lock:
            return f"KeyPinStore(pins={len(self._pins)})"
