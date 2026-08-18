from types import SimpleNamespace

import pytest
import asyncio
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from pharos_discovery.connection.manager import (
    ConnectionManager,
    HttpSSETransport,
    StreamableHTTPTransport,
    StdioTransport,
)
from pharos_discovery.errors import ConnectionFailed, TransportError
from pharos_discovery.models import ServerCard, ApprovalToken


def make_card(transport="http+sse", endpoint="https://server.example.com/mcp", stdio_cmd=None):
    return ServerCard(
        id="urn:pharos:server-001",
        display_name="Test Server",
        description="A test server",
        publisher={"id": "did:web:example.com", "name": "TestPub"},
        version="1.0.0",
        transport=[transport],
        endpoint=endpoint,
        stdio_command=stdio_cmd,
        capabilities=["search"],
        tools_count=3,
        auth={"type": "none"},
        availability="native",
        source_registry="https://registry.pharos.dev",
        published_at="2026-07-01T00:00:00Z",
        updated_at="2026-07-01T00:00:00Z",
        status="active",
    )


def make_token():
    return ApprovalToken(
        token_id="tok-001",
        server_id="urn:pharos:server-001",
        approved_scopes=["search"],
        approved_capabilities=["search"],
        approved_oauth_scopes=[],
        duration="session",
        approved_at="2026-07-01T00:00:00Z",
        expires_at="9999999999",
        signature="signed",
    )


@pytest.fixture
def manager():
    return ConnectionManager(max_retries=3)


class TestTransportCreation:
    @pytest.mark.anyio
    async def test_creates_http_sse(self, manager):
        card = make_card("http+sse")
        transport = manager._create_transport(card, make_token())
        assert isinstance(transport, HttpSSETransport)

    @pytest.mark.anyio
    async def test_creates_streamable_http(self, manager):
        card = make_card("streamable-http")
        transport = manager._create_transport(card, make_token())
        assert isinstance(transport, StreamableHTTPTransport)

    @pytest.mark.anyio
    async def test_creates_stdio(self, manager):
        card = make_card("stdio", endpoint=None, stdio_cmd="python -m myserver")
        transport = manager._create_transport(card, make_token())
        assert isinstance(transport, StdioTransport)

    @pytest.mark.anyio
    async def test_prefers_streamable_http(self, manager):
        card = make_card("streamable-http")
        # When multiple transports available, streamable-http preferred
        card.transport = ["http+sse", "streamable-http"]
        transport = manager._create_transport(card, make_token())
        assert isinstance(transport, StreamableHTTPTransport)

    @pytest.mark.anyio
    async def test_no_transport_raises(self, manager):
        card = make_card("http+sse", endpoint=None)  # no endpoint
        with pytest.raises(ConnectionFailed):
            manager._create_transport(card, make_token())


class TestConnect:
    @pytest.mark.anyio
    async def test_connect_success(self, manager):
        card = make_card("http+sse")
        transport = await manager.connect(card, make_token())
        assert await transport.is_alive() is True
        assert manager.active_count == 1

    @pytest.mark.anyio
    async def test_connect_returns_existing(self, manager):
        card = make_card("http+sse")
        t1 = await manager.connect(card, make_token())
        t2 = await manager.connect(card, make_token())
        assert t1 is t2
        assert manager.active_count == 1

    @pytest.mark.anyio
    async def test_connect_retries_on_failure(self, manager):
        card = make_card("http+sse")
        token = make_token()

        # Mock transport that fails twice then succeeds
        call_count = [0]
        original_connect = HttpSSETransport.connect

        async def flaky_connect(self):
            call_count[0] += 1
            if call_count[0] < 3:
                raise Exception("connection refused")
            self._connected = True
            self._last_activity = 100.0

        with patch.object(HttpSSETransport, "connect", flaky_connect):
            transport = await manager.connect(card, token)
            assert call_count[0] == 3
            assert await transport.is_alive() is True

    @pytest.mark.anyio
    async def test_connect_fails_after_max_retries(self):
        mgr = ConnectionManager(max_retries=2)
        card = make_card("http+sse")

        async def always_fail(self):
            raise Exception("connection refused")

        with patch.object(HttpSSETransport, "connect", always_fail):
            with pytest.raises(ConnectionFailed):
                await mgr.connect(card, make_token())


class TestDisconnect:
    @pytest.mark.anyio
    async def test_disconnect(self, manager):
        card = make_card("http+sse")
        await manager.connect(card, make_token())
        assert manager.active_count == 1
        await manager.disconnect(card.id)
        assert manager.active_count == 0

    @pytest.mark.anyio
    async def test_disconnect_all(self, manager):
        card1 = make_card("http+sse")
        card2 = make_card("streamable-http")
        card2.id = "urn:pharos:server-002"
        await manager.connect(card1, make_token())
        await manager.connect(card2, make_token())
        assert manager.active_count == 2
        await manager.disconnect_all()
        assert manager.active_count == 0

    @pytest.mark.anyio
    async def test_disconnect_nonexistent(self, manager):
        # Should not raise
        await manager.disconnect("urn:pharos:nonexistent")


class TestSend:
    @pytest.mark.anyio
    async def test_send_message(self, manager):
        card = make_card("http+sse")
        await manager.connect(card, make_token())
        # Mock the HTTP POST that the real transport now performs.
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            response = await manager.send(card.id, {"id": "req-1", "method": "search"})
        assert response["result"]["ok"] is True

    @pytest.mark.anyio
    async def test_send_not_connected(self, manager):
        with pytest.raises(ConnectionFailed):
            await manager.send("urn:pharos:unknown", {"method": "test"})


class TestHealthCheck:
    @pytest.mark.anyio
    async def test_health_check_alive(self, manager):
        card = make_card("http+sse")
        await manager.connect(card, make_token())
        assert await manager.health_check(card.id) is True

    @pytest.mark.anyio
    async def test_health_check_nonexistent(self, manager):
        assert await manager.health_check("urn:pharos:unknown") is False

    @pytest.mark.anyio
    async def test_health_check_all(self, manager):
        card1 = make_card("http+sse")
        card2 = make_card("streamable-http")
        card2.id = "urn:pharos:server-002"
        await manager.connect(card1, make_token())
        await manager.connect(card2, make_token())
        results = await manager.health_check_all()
        assert len(results) == 2
        assert all(results.values())


class TestGetTransport:
    @pytest.mark.anyio
    async def test_get_transport(self, manager):
        card = make_card("http+sse")
        await manager.connect(card, make_token())
        transport = manager.get_transport(card.id)
        assert transport is not None
        assert isinstance(transport, HttpSSETransport)

    @pytest.mark.anyio
    async def test_get_transport_nonexistent(self, manager):
        assert manager.get_transport("urn:pharos:unknown") is None


def _card_with_extras(card: ServerCard, **extras) -> SimpleNamespace:
    """Duck-typed card: ServerCard fields plus launch extras T2b may attach."""
    data = card.model_dump()
    data.update(extras)
    return SimpleNamespace(**data)


class TestKind1Unchanged:
    @pytest.mark.anyio
    async def test_remote_https_endpoint_uses_http_transport(self, manager):
        card = make_card("http+sse", endpoint="https://world-time.example/mcp")
        transport = manager._create_transport(card, make_token())
        assert isinstance(transport, HttpSSETransport)
        assert transport.endpoint == "https://world-time.example/mcp"

    @pytest.mark.anyio
    async def test_endpoint_plus_bin_tiebreak_stays_http_not_stdio(self, manager):
        """F2: remote endpoint + bin is Kind 1 — connect the URL, do not spawn."""
        card = make_card("http+sse", endpoint="http://host.docker.internal:8765")
        proxied = _card_with_extras(card, bin="npx -y test-echo-server")
        transport = manager._create_transport(proxied, make_token())
        assert isinstance(transport, HttpSSETransport)
        assert transport.endpoint == "http://host.docker.internal:8765"


class TestKind2LocalHttp:
    @pytest.mark.anyio
    async def test_uses_card_endpoint_after_start(self, manager):
        card = make_card("http+sse", endpoint="http://127.0.0.1:8765")
        transport = manager._create_transport(card, make_token())
        assert isinstance(transport, HttpSSETransport)
        assert transport.endpoint == "http://127.0.0.1:8765"

    @pytest.mark.anyio
    async def test_uses_local_endpoint_field_when_publisher_endpoint_missing(self, manager):
        card = make_card("streamable-http", endpoint=None)
        proxied = _card_with_extras(card, local_endpoint="http://127.0.0.1:9000/mcp")
        transport = manager._create_transport(proxied, make_token())
        assert isinstance(transport, StreamableHTTPTransport)
        assert transport.endpoint == "http://127.0.0.1:9000/mcp"

    @pytest.mark.anyio
    async def test_missing_local_url_raises_catchable_connection_failed(self, manager):
        """Kind 2 before T2b start — T2b must catch this, not a publisher-blame message."""
        card = make_card("http+sse", endpoint=None)
        proxied = _card_with_extras(card, bin="npx -y test-echo-server")
        with pytest.raises(ConnectionFailed) as excinfo:
            manager._create_transport(proxied, make_token())
        detail = str(excinfo.value).lower()
        assert "publisher must provide" not in detail
        assert "local" in detail and "endpoint" in detail
        assert "not ready" in detail

    @pytest.mark.anyio
    async def test_http_sse_alias_uses_resolved_local_url(self, manager):
        card = make_card("http+sse", endpoint=None)
        # transport alias used by registry / INSTALL_KINDS classifier
        proxied = _card_with_extras(
            card,
            transport=["http-sse"],
            local_endpoint="http://127.0.0.1:8765",
        )
        transport = manager._create_transport(proxied, make_token())
        assert isinstance(transport, HttpSSETransport)
        assert transport.endpoint == "http://127.0.0.1:8765"


class TestKind3StdioLaunchMapping:
    @pytest.mark.anyio
    async def test_stdio_command_unchanged(self, manager):
        card = make_card("stdio", endpoint=None, stdio_cmd="python -m myserver")
        transport = manager._create_transport(card, make_token())
        assert isinstance(transport, StdioTransport)
        assert transport.command == "python -m myserver"

    @pytest.mark.parametrize(
        "extras,expected",
        [
            ({"command": "npx -y @scope/mcp-server"}, "npx -y @scope/mcp-server"),
            ({"bin": "./mcp-server"}, "./mcp-server"),
            ({"runtime": "npx", "package": "@scope/mcp-server"}, "npx -y @scope/mcp-server"),
            ({"runtime": "uvx", "package": "mcp-server-git"}, "uvx mcp-server-git"),
            ({"runtime": "docker", "package": "myimg:latest"}, "docker run -i --rm myimg:latest"),
            ({"runtime": "python", "package": "src.server"}, "python3 src.server"),
            ({"runtime": "binary", "package": "bin/server"}, "bin/server"),
        ],
    )
    @pytest.mark.anyio
    async def test_maps_command_bin_runtime_like_install_kinds(self, manager, extras, expected):
        card = make_card("stdio", endpoint=None, stdio_cmd=None)
        proxied = _card_with_extras(card, **extras)
        transport = manager._create_transport(proxied, make_token())
        assert isinstance(transport, StdioTransport)
        assert transport.command == expected

    @pytest.mark.anyio
    async def test_prefers_explicit_stdio_command_over_runtime(self, manager):
        card = make_card("stdio", endpoint=None, stdio_cmd="uvx mcp-server-git")
        proxied = _card_with_extras(card, runtime="npx", package="@scope/other")
        transport = manager._create_transport(proxied, make_token())
        assert isinstance(transport, StdioTransport)
        assert transport.command == "uvx mcp-server-git"

    @pytest.mark.anyio
    async def test_mixed_http_and_stdio_without_endpoint_falls_back_to_stdio(self, manager):
        card = make_card("http+sse", endpoint=None, stdio_cmd="npx -y echo")
        card.transport = ["http+sse", "stdio"]
        transport = manager._create_transport(card, make_token())
        assert isinstance(transport, StdioTransport)
        assert transport.command == "npx -y echo"


class TestStdioTransportSafety:
    def test_stdio_connect_does_not_use_shell_true(self):
        import inspect
        from pharos_discovery.connection import manager as mgr_mod

        source = inspect.getsource(mgr_mod.StdioTransport.connect)
        source += inspect.getsource(mgr_mod.ConnectionManager._create_transport)
        assert "shell=True" not in source
        assert "create_subprocess_shell" not in source
        assert "pharos " not in source
