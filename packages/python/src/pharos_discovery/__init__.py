"""Pharos Discovery SDK — search, approve, and connect to MCP servers."""

__version__ = "0.1.0"

from pharos_discovery.security import Blocklist, KeyPinStore
from pharos_discovery.events import EVENT_TYPES, SSEEvent, EventSubscriber

__all__ = [
    "Blocklist",
    "KeyPinStore",
    "EVENT_TYPES",
    "SSEEvent",
    "EventSubscriber",
]
