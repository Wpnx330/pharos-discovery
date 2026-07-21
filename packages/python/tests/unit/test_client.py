import pytest
import time
from unittest.mock import AsyncMock, MagicMock

from pharos_discovery.client import PharosClient
from pharos_discovery.adapters.registry import SearchResult
from pharos_discovery.models import (
    ServerCard, ApprovalRequest, ApprovalResponse, ApprovalToken,
)
from pharos_discovery.errors import (
    ApprovalDenied, ConnectionFailed, ConsentFatigueWarning,
    DiscoveryDegraded, HeadlessApprovalRequired, NoServersFound,
    RegistryUnavailable, ScopeNotApproved,
)


def make_card(server_id="urn:pharos:server-001"):
    return ServerCard(
        id=server_id,
        display_name="Test Server",
        description="A test server",
        publisher={"id": "did:web:example.com", "name": "TestPub"},
        version="1.0.0",
        transport=["http+sse"],
        capabilities=["search"],
        tools_count=3,
        auth={"type": "none"},
        availability="native",
        source_registry="https://registry.pharos.dev",
        published_at="2026-07-01T00:00:00Z",
        updated_at="2026-07-01T00:00:00Z",
        status="active",
    )


def make_approval_response(approved=True, scopes=None):
    return ApprovalResponse(
        approved=approved,
        approved_scopes=scopes or ["search"],
        duration="session",
        deny_reason=None if approved else "excessive_scopes",
    )


class MockApprovalHandler:
    def __init__(self, response=None):
        self.response = response or make_approval_response()
        self.calls = []

    async def request_approval(self, request):
        self.calls.append(request)
        return self.response


class MockConnectionHandler:
    def __init__(self):
        self.connect_calls = []
        self.disconnect_calls = []

    async def connect(self, card, token):
        self.connect_calls.append((card, token))
        return MagicMock(name=f"connection-{card.id}")

    async def disconnect(self, connection):
        self.disconnect_calls.append(connection)


@pytest.fixture
def client():
    return PharosClient(
        "https://registry.pharos.dev",
        approval_handler=MockApprovalHandler(),
        connection_handler=MockConnectionHandler(),
    )


class TestSearch:
    @pytest.mark.anyio
    async def test_search_success(self, client, monkeypatch):
        card = make_card()
        result = SearchResult(card=card, score=1.0)
        monkeypatch.setattr(client._adapter, "search", AsyncMock(return_value=[result]))
        results = await client.search("test")
        assert len(results) == 1
        assert results[0].card.id == "urn:pharos:server-001"

    @pytest.mark.anyio
    async def test_search_cache_fallback(self, client, monkeypatch):
        card = make_card()
        client._cache.put(card.id, card)
        monkeypatch.setattr(client._adapter, "search", AsyncMock(side_effect=RegistryUnavailable("url", 503)))
        results = await client.search("test")
        assert len(results) == 1
        assert results[0].card.id == card.id

    @pytest.mark.anyio
    async def test_search_degraded_no_cache(self, client, monkeypatch):
        monkeypatch.setattr(client._adapter, "search", AsyncMock(side_effect=RegistryUnavailable("url", 503)))
        with pytest.raises(DiscoveryDegraded):
            await client.search("test")

    @pytest.mark.anyio
    async def test_search_no_servers_propagates(self, client, monkeypatch):
        monkeypatch.setattr(client._adapter, "search", AsyncMock(side_effect=NoServersFound("test")))
        with pytest.raises(NoServersFound):
            await client.search("test")


class TestConnectAndApprove:
    @pytest.mark.anyio
    async def test_full_flow(self, client, monkeypatch):
        card = make_card()
        monkeypatch.setattr(client._adapter, "get_blocklist", AsyncMock(return_value=[]))
        token, connection = await client.connect_and_approve(card, "test purpose")
        assert token.server_id == card.id
        assert token.approved_scopes == ["search"]
        assert connection is not None

    @pytest.mark.anyio
    async def test_headless_mode(self):
        client = PharosClient("https://registry.pharos.dev", headless=True)
        card = make_card()
        with pytest.raises(HeadlessApprovalRequired):
            await client.connect_and_approve(card, "test")

    @pytest.mark.anyio
    async def test_approval_denied(self):
        handler = MockApprovalHandler(make_approval_response(approved=False))
        client = PharosClient(
            "https://registry.pharos.dev",
            approval_handler=handler,
        )
        card = make_card()
        with pytest.raises(ApprovalDenied):
            await client.connect_and_approve(card, "test")

    @pytest.mark.anyio
    async def test_blocklisted_server(self, client, monkeypatch):
        card = make_card("urn:pharos:bad-1")
        monkeypatch.setattr(client._adapter, "get_blocklist", AsyncMock(return_value=["urn:pharos:bad-1"]))
        with pytest.raises(ApprovalDenied) as exc:
            await client.connect_and_approve(card, "test")
        assert "blocklisted" in str(exc.value)

    @pytest.mark.anyio
    async def test_cached_approval_reused(self, client, monkeypatch):
        card = make_card()
        monkeypatch.setattr(client._adapter, "get_blocklist", AsyncMock(return_value=[]))
        # First call
        token1, _ = await client.connect_and_approve(card, "test")
        # Second call should reuse token
        token2, _ = await client.connect_and_approve(card, "test")
        assert token1.token_id == token2.token_id

    @pytest.mark.anyio
    async def test_consent_fatigue(self, monkeypatch):
        handler = MockApprovalHandler()
        client = PharosClient(
            "https://registry.pharos.dev",
            approval_handler=handler,
            connection_handler=MockConnectionHandler(),
            max_novel_approvals=2,
        )
        monkeypatch.setattr(client._adapter, "get_blocklist", AsyncMock(return_value=[]))
        # Approve 3 novel servers
        for i in range(3):
            card = make_card(f"urn:pharos:server-{i}")
            if i < 2:
                await client.connect_and_approve(card, "test")
            else:
                with pytest.raises(ConsentFatigueWarning):
                    await client.connect_and_approve(card, "test")


class TestRevoke:
    @pytest.mark.anyio
    async def test_revoke_removes_approval(self, client, monkeypatch):
        card = make_card()
        monkeypatch.setattr(client._adapter, "get_blocklist", AsyncMock(return_value=[]))
        await client.connect_and_approve(card, "test")
        assert card.id in client._approved_servers
        await client.revoke(card.id)
        assert card.id not in client._approved_servers


class TestCheckScope:
    @pytest.mark.anyio
    async def test_scope_approved(self, client, monkeypatch):
        card = make_card()
        monkeypatch.setattr(client._adapter, "get_blocklist", AsyncMock(return_value=[]))
        await client.connect_and_approve(card, "test", requested_scopes=["search"])
        await client.check_scope(card.id, "search")  # should not raise

    @pytest.mark.anyio
    async def test_scope_not_approved(self, client, monkeypatch):
        card = make_card()
        monkeypatch.setattr(client._adapter, "get_blocklist", AsyncMock(return_value=[]))
        await client.connect_and_approve(card, "test", requested_scopes=["search"])
        with pytest.raises(ScopeNotApproved):
            await client.check_scope(card.id, "admin")

    @pytest.mark.anyio
    async def test_no_token(self, client):
        with pytest.raises(ScopeNotApproved):
            await client.check_scope("urn:pharos:unknown", "search")


class TestClose:
    @pytest.mark.anyio
    async def test_close_clears_state(self, client, monkeypatch):
        card = make_card()
        monkeypatch.setattr(client._adapter, "get_blocklist", AsyncMock(return_value=[]))
        await client.connect_and_approve(card, "test")
        await client.close()
        assert len(client._connections) == 0
        assert len(client._approved_servers) == 0
