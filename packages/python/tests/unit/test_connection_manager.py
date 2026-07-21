import pytest
import asyncio
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
        response = await manager.send(card.id, {"id": "req-1", "method": "search"})
        assert response["status"] == "ok"
        assert response["id"] == "req-1"

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
