from __future__ import annotations

import asyncio
import time
from typing import Any, Literal, Protocol

from pharos_discovery.errors import ConnectionFailed, TransportError
from pharos_discovery.models import ApprovalToken, ServerCard


class MCPTransport(Protocol):
    """Protocol for MCP transport implementations."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def send(self, message: dict[str, Any]) -> dict[str, Any]: ...
    async def is_alive(self) -> bool: ...


class HttpSSETransport:
    """HTTP+SSE transport for MCP servers.

    Connects via HTTP POST for commands and SSE for streaming responses.
    """

    def __init__(self, endpoint: str, token: ApprovalToken, timeout: float = 30.0):
        self._endpoint = endpoint
        self._token = token
        self._timeout = timeout
        self._connected = False
        self._last_activity: float | None = None

    async def connect(self) -> None:
        # In production, this would establish HTTP connection + SSE stream
        # For now, mark as connected
        self._connected = True
        self._last_activity = time.monotonic()

    async def disconnect(self) -> None:
        self._connected = False
        self._last_activity = None

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self._connected:
            raise TransportError("http+sse", "Not connected")
        self._last_activity = time.monotonic()
        # In production, POST to endpoint with auth header, read SSE response
        return {"status": "ok", "id": message.get("id", "unknown")}

    async def is_alive(self) -> bool:
        return self._connected

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def last_activity(self) -> float | None:
        return self._last_activity


class StreamableHTTPTransport:
    """Streamable HTTP transport for MCP servers (single endpoint, bidirectional)."""

    def __init__(self, endpoint: str, token: ApprovalToken, timeout: float = 30.0):
        self._endpoint = endpoint
        self._token = token
        self._timeout = timeout
        self._connected = False
        self._last_activity: float | None = None

    async def connect(self) -> None:
        self._connected = True
        self._last_activity = time.monotonic()

    async def disconnect(self) -> None:
        self._connected = False
        self._last_activity = None

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self._connected:
            raise TransportError("streamable-http", "Not connected")
        self._last_activity = time.monotonic()
        return {"status": "ok", "id": message.get("id", "unknown")}

    async def is_alive(self) -> bool:
        return self._connected

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def last_activity(self) -> float | None:
        return self._last_activity


class StdioTransport:
    """Standard I/O transport for local MCP servers."""

    def __init__(self, command: str, token: ApprovalToken, timeout: float = 30.0):
        self._command = command
        self._token = token
        self._timeout = timeout
        self._connected = False
        self._last_activity: float | None = None

    async def connect(self) -> None:
        # In production, spawn subprocess
        self._connected = True
        self._last_activity = time.monotonic()

    async def disconnect(self) -> None:
        self._connected = False
        self._last_activity = None

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self._connected:
            raise TransportError("stdio", "Not connected")
        self._last_activity = time.monotonic()
        return {"status": "ok", "id": message.get("id", "unknown")}

    async def is_alive(self) -> bool:
        return self._connected

    @property
    def command(self) -> str:
        return self._command

    @property
    def last_activity(self) -> float | None:
        return self._last_activity


class ConnectionManager:
    """Manages MCP transport connections lifecycle.

    Creates appropriate transport based on server card, handles reconnection,
    health checks, and tracks connection state.
    """

    def __init__(self, health_check_interval: float = 60.0, max_retries: int = 3):
        self._connections: dict[str, MCPTransport] = {}
        self._health_interval = health_check_interval
        self._max_retries = max_retries
        self._retry_count: dict[str, int] = {}

    @property
    def active_count(self) -> int:
        return len(self._connections)

    @property
    def connection_ids(self) -> list[str]:
        return list(self._connections.keys())

    async def connect(
        self,
        card: ServerCard,
        token: ApprovalToken,
    ) -> MCPTransport:
        """Create and establish a connection to an MCP server."""
        if card.id in self._connections:
            existing = self._connections[card.id]
            if await existing.is_alive():
                return existing

        transport = self._create_transport(card, token)

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                await transport.connect()
                self._connections[card.id] = transport
                self._retry_count[card.id] = 0
                return transport
            except Exception as exc:
                last_exc = exc
                self._retry_count[card.id] = attempt + 1
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(0.1 * (attempt + 1))

        raise ConnectionFailed(card.id, f"Failed after {self._max_retries} retries: {last_exc}")

    async def disconnect(self, server_id: str) -> None:
        """Disconnect and remove a connection."""
        transport = self._connections.pop(server_id, None)
        if transport:
            await transport.disconnect()
        self._retry_count.pop(server_id, None)

    async def disconnect_all(self) -> None:
        """Disconnect all active connections."""
        ids = list(self._connections.keys())
        for sid in ids:
            await self.disconnect(sid)

    async def send(self, server_id: str, message: dict[str, Any]) -> dict[str, Any]:
        """Send a message to a connected server."""
        transport = self._connections.get(server_id)
        if transport is None:
            raise ConnectionFailed(server_id, "Not connected")
        return await transport.send(message)

    async def health_check(self, server_id: str) -> bool:
        """Check if a connection is alive."""
        transport = self._connections.get(server_id)
        if transport is None:
            return False
        return await transport.is_alive()

    async def health_check_all(self) -> dict[str, bool]:
        """Check all connections. Returns dict of server_id to alive."""
        results: dict[str, bool] = {}
        for sid, transport in self._connections.items():
            results[sid] = await transport.is_alive()
        return results

    def get_transport(self, server_id: str) -> MCPTransport | None:
        """Get the transport for a server, if connected."""
        return self._connections.get(server_id)

    def _create_transport(self, card: ServerCard, token: ApprovalToken) -> MCPTransport:
        """Create the appropriate transport based on server card."""
        transports = card.transport

        if not transports:
            raise ConnectionFailed(card.id, "No transport types specified")

        # Prefer streamable-http > http+sse > stdio
        if "streamable-http" in transports and card.endpoint:
            return StreamableHTTPTransport(card.endpoint, token)
        if "http+sse" in transports and card.endpoint:
            return HttpSSETransport(card.endpoint, token)
        if "stdio" in transports and card.stdio_command:
            return StdioTransport(card.stdio_command, token)

        raise ConnectionFailed(card.id, f"No usable transport from {transports}")
