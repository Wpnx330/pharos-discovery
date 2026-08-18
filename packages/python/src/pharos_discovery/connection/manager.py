from __future__ import annotations

import asyncio
import itertools
import json
import time
from typing import Any, Literal, Protocol

import httpx

from pharos_discovery.errors import ConnectionFailed, TransportError
from pharos_discovery.install_kind import HTTP_TRANSPORTS, launch_command
from pharos_discovery.models import ApprovalToken, ServerCard

_HTTP_SSE_ALIASES = frozenset({"http+sse", "http-sse", "sse", "http"})
_HTTP_URL_FIELDS = ("endpoint", "local_endpoint")
_KIND2_NOT_READY = (
    "Local HTTP endpoint is not ready. Start the local server first, then retry connect."
)


class MCPTransport(Protocol):
    """Protocol for MCP transport implementations."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def send(self, message: dict[str, Any]) -> dict[str, Any]: ...
    async def is_alive(self) -> bool: ...


class HttpSSETransport:
    """HTTP+SSE transport for MCP servers.

    Sends JSON-RPC 2.0 requests via HTTP POST and reads the JSON response
    body.  Designed to work against any MCP server that accepts a single
    POST endpoint (the common case for ``http+sse`` and
    ``streamable-http`` servers).
    """

    def __init__(self, endpoint: str, token: ApprovalToken, timeout: float = 30.0):
        self._endpoint = endpoint
        self._token = token
        self._timeout = timeout
        self._connected = False
        self._last_activity: float | None = None
        self._client: httpx.AsyncClient | None = None
        self._id_counter = itertools.count(1)

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        self._connected = True
        self._last_activity = time.monotonic()

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False
        self._last_activity = None

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self._connected or self._client is None:
            raise TransportError("http+sse", "Not connected")
        self._last_activity = time.monotonic()

        # Ensure the message has an id for request/response correlation.
        if "id" not in message:
            message["id"] = next(self._id_counter)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        body = json.dumps(message)

        try:
            resp = await self._client.post(self._endpoint, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise TransportError("http+sse", str(exc)) from exc

        if resp.status_code >= 400:
            raise TransportError(
                "http+sse",
                f"HTTP {resp.status_code}: {resp.text[:500]}",
            )

        return _parse_mcp_response(resp)

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
        self._client: httpx.AsyncClient | None = None
        self._id_counter = itertools.count(1)

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        self._connected = True
        self._last_activity = time.monotonic()

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False
        self._last_activity = None

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self._connected or self._client is None:
            raise TransportError("streamable-http", "Not connected")
        self._last_activity = time.monotonic()

        if "id" not in message:
            message["id"] = next(self._id_counter)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        body = json.dumps(message)

        try:
            resp = await self._client.post(self._endpoint, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise TransportError("streamable-http", str(exc)) from exc

        if resp.status_code >= 400:
            raise TransportError(
                "streamable-http",
                f"HTTP {resp.status_code}: {resp.text[:500]}",
            )

        return _parse_mcp_response(resp)

    async def is_alive(self) -> bool:
        return self._connected

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def last_activity(self) -> float | None:
        return self._last_activity


class StdioTransport:
    """Standard I/O transport for local MCP servers.

    Spawns a subprocess and communicates over stdin/stdout using newline-
    delimited JSON-RPC 2.0 (the standard MCP stdio framing).
    """

    def __init__(self, command: str, token: ApprovalToken, timeout: float = 30.0):
        self._command = command
        self._token = token
        self._timeout = timeout
        self._connected = False
        self._last_activity: float | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._id_counter = itertools.count(1)

    async def connect(self) -> None:
        import shlex

        parts = shlex.split(self._command)
        self._proc = await asyncio.create_subprocess_exec(
            *parts,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._connected = True
        self._last_activity = time.monotonic()

    async def disconnect(self) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
            self._proc = None
        self._connected = False
        self._last_activity = None

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self._connected or self._proc is None or self._proc.stdin is None:
            raise TransportError("stdio", "Not connected")
        self._last_activity = time.monotonic()

        if "id" not in message:
            message["id"] = next(self._id_counter)

        line = json.dumps(message) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        await self._proc.stdin.drain()

        # Read one line of response.
        assert self._proc.stdout is not None
        raw = await asyncio.wait_for(self._proc.stdout.readline(), timeout=self._timeout)
        if not raw:
            raise TransportError("stdio", "Subprocess closed stdout")
        return json.loads(raw.decode("utf-8").strip())

    async def is_alive(self) -> bool:
        return self._connected and self._proc is not None and self._proc.returncode is None

    @property
    def command(self) -> str:
        return self._command

    @property
    def last_activity(self) -> float | None:
        return self._last_activity


def _parse_mcp_response(resp: httpx.Response) -> dict[str, Any]:
    """Parse an MCP HTTP response, handling both JSON and SSE formats."""
    content_type = resp.headers.get("content-type", "")

    if "text/event-stream" in content_type:
        # SSE: extract the first ``data:`` line containing a JSON object.
        for line in resp.text.splitlines():
            line = line.strip()
            if line.startswith("data:") and "{" in line:
                payload = line[len("data:"):].strip()
                if payload:
                    return json.loads(payload)
        raise TransportError("http", "SSE response contained no data lines")

    # Default: plain JSON body.
    try:
        return resp.json()
    except Exception as exc:
        raise TransportError("http", f"Failed to parse response: {exc}") from exc


class MCPConnection:
    """High-level MCP client helper wrapping a connected transport.

    Provides convenience methods for the standard MCP lifecycle:
    ``initialize`` → ``tools/list`` → ``tools/call``.
    """

    def __init__(self, transport: MCPTransport, server_id: str):
        self._transport = transport
        self._server_id = server_id
        self._initialized = False
        self._next_id = itertools.count(1)

    @property
    def server_id(self) -> str:
        return self._server_id

    @property
    def transport(self) -> MCPTransport:
        return self._transport

    @property
    def initialized(self) -> bool:
        return self._initialized

    async def _send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": next(self._next_id),
            "method": method,
        }
        if params:
            msg["params"] = params
        return await self._transport.send(msg)

    async def initialize(self) -> dict[str, Any]:
        """Send the MCP ``initialize`` request."""
        result = await self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pharos-discovery-sdk", "version": "0.1.0"},
        })
        # Send initialized notification (no id, no response expected).
        try:
            await self._transport.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:
            pass  # Best-effort notification.
        self._initialized = True
        return result

    async def list_tools(self) -> dict[str, Any]:
        """Send the MCP ``tools/list`` request."""
        return await self._send("tools/list")

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a ``tools/call`` request."""
        return await self._send("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })

    async def close(self) -> None:
        """Disconnect the underlying transport."""
        await self._transport.disconnect()

    async def disconnect(self) -> None:
        """Alias for :meth:`close` — disconnect the underlying transport."""
        await self._transport.disconnect()


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

    def get_mcp_connection(self, server_id: str) -> MCPConnection | None:
        """Get a high-level :class:`MCPConnection` wrapper for a server.

        Returns ``None`` if the server is not connected.
        """
        transport = self._connections.get(server_id)
        if transport is None:
            return None
        return MCPConnection(transport, server_id)

    def _create_transport(self, card: ServerCard, token: ApprovalToken) -> MCPTransport:
        """Create the appropriate transport based on server card.

        Kind 1: remote ``http(s)://`` ``card.endpoint``.
        Kind 2: same HTTP transports against a resolved local URL
        (``card.endpoint`` after start, or optional ``local_endpoint``).
        Missing URL → :class:`ConnectionFailed` for T2b to catch — never
        "publisher must provide endpoint."
        Kind 3: stdio from ``stdio_command`` / ``command`` / ``bin`` /
        runtime+package via :func:`launch_command`.
        """
        transports = _transport_names(card)
        if not transports:
            raise ConnectionFailed(card.id, "No transport types specified")

        http_url = _resolved_http_url(card)
        has_streamable = "streamable-http" in transports
        has_http_sse = any(name in _HTTP_SSE_ALIASES for name in transports)
        has_http = any(name in HTTP_TRANSPORTS for name in transports)

        # Prefer streamable-http > http+sse (and aliases) when a URL is ready.
        if has_streamable and http_url:
            return StreamableHTTPTransport(http_url, token)
        if has_http_sse and http_url:
            return HttpSSETransport(http_url, token)

        if "stdio" in transports:
            command = _stdio_launch_line(card)
            if command:
                return StdioTransport(command, token)

        # Kind 2 before T2b has started / written the local URL.
        if has_http and not http_url:
            raise ConnectionFailed(card.id, _KIND2_NOT_READY)

        raise ConnectionFailed(card.id, f"No usable transport from {transports}")


def _transport_names(card: Any) -> list[str]:
    raw = getattr(card, "transport", None)
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = []
    return [str(item).strip().lower() for item in items if str(item).strip()]


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    lowered = text.lower()
    if lowered.startswith("https://") or lowered.startswith("http://"):
        return text
    return None


def _resolved_http_url(card: Any) -> str | None:
    """Publisher / after-start ``endpoint``, else optional ``local_endpoint``."""
    for name in _HTTP_URL_FIELDS:
        url = _http_url(getattr(card, name, None))
        if url:
            return url
    return None


def _stdio_launch_line(card: Any) -> str | None:
    """stdio_command / command / bin / runtime+package — same as INSTALL_KINDS.

    Uses :func:`launch_command` first, then getattr so duck-typed cards
    (T2b MagicMock, SimpleNamespace) still map without ServerCard fields.
    """
    mapped = launch_command(card)
    if mapped:
        return mapped
    return launch_command({
        "stdio_command": getattr(card, "stdio_command", None),
        "command": getattr(card, "command", None),
        "bin": getattr(card, "bin", None),
        "runtime": getattr(card, "runtime", None),
        "package": getattr(card, "package", None),
    })
