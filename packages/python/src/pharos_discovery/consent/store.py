"""Consent store — records user approval/denial decisions for MCP servers.

T18: A persistent consent store with in-memory storage and optional JSON file
persistence. Thread-safe via ``threading.Lock``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Literal

from pydantic import BaseModel, Field


Decision = Literal["approved", "denied"]


class ConsentRecord(BaseModel):
    """A single consent decision for a server + set of scopes."""

    server_id: str
    scopes: list[str] = Field(default_factory=list)
    decision: Decision
    timestamp: float = Field(default_factory=lambda: time.time())
    expires_at: float | None = None

    def is_expired(self) -> bool:
        """Return True if this record has expired (no expiry → never expires)."""
        if self.expires_at is None:
            return False
        return time.time() >= self.expires_at


class ConsentStore:
    """Thread-safe in-memory consent store with optional JSON persistence.

    Parameters
    ----------
    persist_path:
        If provided, the store will load from / save to this JSON file path.
    """

    def __init__(self, persist_path: str | None = None) -> None:
        self._persist_path = persist_path
        self._lock = threading.Lock()
        self._records: dict[str, ConsentRecord] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def record(
        self,
        server_id: str,
        scopes: list[str],
        decision: Decision,
        ttl: float | None = None,
    ) -> ConsentRecord:
        """Store a consent decision for *server_id*.

        Parameters
        ----------
        ttl:
            Time-to-live in seconds. If provided, ``expires_at`` is set to
            ``timestamp + ttl``. If ``None`` the record never expires.
        """
        now = time.time()
        record = ConsentRecord(
            server_id=server_id,
            scopes=list(scopes),
            decision=decision,
            timestamp=now,
            expires_at=(now + ttl) if ttl is not None else None,
        )
        with self._lock:
            self._records[server_id] = record
            self._save()
        return record

    def check(self, server_id: str, scopes: list[str]) -> ConsentRecord | None:
        """Return a valid consent record for *server_id* covering *scopes*, or ``None``.

        A record is returned only if:
        - it exists,
        - it is not expired,
        - it was approved (not denied),
        - all requested *scopes* are a subset of the record's scopes.
        """
        with self._lock:
            record = self._records.get(server_id)
        if record is None:
            return None
        if record.is_expired():
            return None
        if record.decision != "approved":
            return None
        if not set(scopes).issubset(set(record.scopes)):
            return None
        return record

    def revoke(self, server_id: str) -> bool:
        """Remove all consent records for *server_id*. Returns ``True`` if a record was removed."""
        with self._lock:
            existed = server_id in self._records
            if existed:
                del self._records[server_id]
                self._save()
            return existed

    def list_all(self) -> list[ConsentRecord]:
        """Return all consent records (including expired ones)."""
        with self._lock:
            return list(self._records.values())

    def is_valid(self, record: ConsentRecord) -> bool:
        """Return ``True`` if *record* is not expired."""
        return not record.is_expired()

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #

    def _save(self) -> None:
        """Serialise records to disk (caller must hold ``self._lock``)."""
        if self._persist_path is None:
            return
        data = {
            sid: rec.model_dump(mode="json")
            for sid, rec in self._records.items()
        }
        tmp = self._persist_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self._persist_path)

    def _load(self) -> None:
        """Deserialise records from disk (called from ``__init__``)."""
        if self._persist_path is None:
            return
        if not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return
        with self._lock:
            for sid, raw in data.items():
                self._records[sid] = ConsentRecord.model_validate(raw)
