from __future__ import annotations

import time
from typing import Any


class ServerCardCache:
    """In-memory cache for ServerCards with TTL and ETag support."""

    def __init__(self, ttl_seconds: int = 300, conditional: bool = True):
        self._store: dict[str, dict[str, Any]] = {}
        self._ttl = ttl_seconds
        self._conditional = conditional

    def get(self, server_id: str) -> tuple[Any, str | None]:
        """Get cached entry. Returns (card, etag) or (None, None) if missing/expired."""
        entry = self._store.get(server_id)
        if entry is None:
            return None, None
        if time.monotonic() - entry["cached_at"] > self._ttl:
            del self._store[server_id]
            return None, None
        return entry["card"], entry.get("etag")

    def put(self, server_id: str, card: Any, etag: str | None = None) -> None:
        """Cache a server card with optional ETag."""
        self._store[server_id] = {
            "card": card,
            "etag": etag,
            "cached_at": time.monotonic(),
        }

    def invalidate(self, server_id: str) -> None:
        """Remove a specific entry."""
        self._store.pop(server_id, None)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._store.clear()

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now = time.monotonic()
        expired = [
            k for k, v in self._store.items() if now - v["cached_at"] > self._ttl
        ]
        for k in expired:
            del self._store[k]
        return len(expired)

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def conditional(self) -> bool:
        return self._conditional
