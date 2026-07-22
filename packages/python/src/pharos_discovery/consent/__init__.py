"""Consent store — persistent user approval/denial decisions for MCP servers."""

from pharos_discovery.consent.store import (
    ConsentRecord,
    ConsentStore,
)

__all__ = [
    "ConsentRecord",
    "ConsentStore",
]
