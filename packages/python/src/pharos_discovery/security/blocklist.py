"""Blocklist — maintain a persistent set of blocked MCP server IDs."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import ClassVar


class Blocklist:
    """Thread-safe in-memory blocklist with JSON file persistence.

    Stores ``{server_id: reason}`` pairs. Use :meth:`add`, :meth:`remove`,
    :meth:`is_blocked` for membership management and :meth:`save`/:meth:`load`
    for persistence.
    """

    def __init__(self) -> None:
        self._entries: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ API
    def add(self, server_id: str, reason: str) -> None:
        """Block *server_id* for *reason*."""
        with self._lock:
            self._entries[server_id] = reason

    def remove(self, server_id: str) -> None:
        """Unblock *server_id* (no-op if not present)."""
        with self._lock:
            self._entries.pop(server_id, None)

    def is_blocked(self, server_id: str) -> bool:
        with self._lock:
            return server_id in self._entries

    def list(self) -> dict[str, str]:
        """Return a shallow copy of the blocklist."""
        with self._lock:
            return dict(self._entries)

    # ------------------------------------------------------------ persistence
    def save(self, path: str) -> None:
        """Write the blocklist to *path* as JSON."""
        with self._lock:
            data = dict(self._entries)
        Path(path).write_text(json.dumps(data, indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: str) -> "Blocklist":
        """Load a blocklist from *path* (returns empty instance if missing)."""
        inst = cls()
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text())
            with inst._lock:
                inst._entries = {str(k): str(v) for k, v in data.items()}
        return inst

    # ----------------------------------------------------------- dunders
    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, server_id: object) -> bool:
        return self.is_blocked(str(server_id))

    def __repr__(self) -> str:  # pragma: no cover - trivial
        with self._lock:
            return f"Blocklist(entries={len(self._entries)})"
