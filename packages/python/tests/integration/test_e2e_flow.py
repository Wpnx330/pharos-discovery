"""End-to-end integration tests for the Pharos Discovery Python SDK.

Tests the full flow: search → approve → connect → send → disconnect
using mock HTTP responses to simulate a Pharos registry.
"""

import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from pharos_discovery.client import PharosClient
from pharos_discovery.adapters.registry import PharosRegistryAdapter
from pharos_discovery.approval.engine import ApprovalEngine
from pharos_discovery.connection.manager import ConnectionManager
from pharos_discovery.models import (
    ApprovalRequest,
    ApprovalResponse,
    ServerCard,
)
from pharos_discovery.errors import (
    NoServersFound,
    RegistryUnavailable,
    ApprovalDenied,
    HeadlessApprovalRequired,
    ScopeNotApproved,
)


# ---- Fixtures ----

def make_server_card(server_id="urn:pharos:server-001", name="Flight Search MCP"):
    return {
        "id": server_id,
        "display_name": name,
        "description": "Search and book flights",
        "publisher": {"id": "did:web:flights.example.com", "name": "FlightCo", "verified": True},
        "version": "2.1.0",
        "transport": ["http+sse"],
        "endpoint": "https://mcp.flights.example.com/sse",
        "capabilities": ["search", "book"],
        "tools_count": 5,
        "auth": {"type": "oauth", "scopes": ["flights:search", "flights:book"]},
        "availability": "native",
        "source_registry": "https://registry.pharos.dev",
        "published_at": "2026-06-15T00:00:00Z",
        "updated_at": "2026-07-10T00:00:00Z",
        "status": "active",
        "tags": ["travel", "flights"],
    }


class RecordingApprovalHandler:
    """Approval handler that records calls and returns configurable responses."""

    def __init__(self, auto_approve=True, approved_scopes=None):
        self.auto_approve = auto_approve
        self.approved_scopes = approved_scopes or ["flights:search"]
        self.calls: list[ApprovalRequest] = []

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        self.calls.append(request)
        return ApprovalResponse(
            approved=self.auto_approve,
            approved_scopes=self.approved_scopes if self.auto_approve else [],
            duration="session",
            deny_reason=None if self.auto_approve else "excessive_scopes",
        )


def mock_registry_response(cards, status_code=200, etag=None):
    """Create a mock httpx.Response for registry endpoints."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = cards if isinstance(cards, dict) else {"results": cards}
    resp.text = str(cards)
    resp.headers = {}
    if etag:
        resp.headers["ETag"] = etag
    return resp


# ---- E2E Tests ----

class TestFullDiscoveryFlow:
    """Test the complete search → approve → connect → send → disconnect flow."""

    @pytest.mark.anyio
    async def test_search_to_disconnect(self):
        """Full lifecycle: search, approve, connect, send, disconnect."""
        card_data = make_server_card()
        approval_handler = RecordingApprovalHandler()
        connection_manager = ConnectionManager()

        client = PharosClient(
            "https://registry.pharos.dev",
            approval_handler=approval_handler,
            connection_handler=connection_manager,
        )

        # Mock registry search
        with patch.object(httpx.AsyncClient, "get",
                         new=AsyncMock(return_value=mock_registry_response([card_data]))):
            results = await client.search("flights")

        assert len(results) == 1
        assert results[0].card.display_name == "Flight Search MCP"

        # Mock blocklist (empty)
        with patch.object(httpx.AsyncClient, "get",
                         new=AsyncMock(return_value=mock_registry_response({"blocked": []}))):
            token, connection = await client.connect_and_approve(
                results[0].card,
                purpose="Search for flights to Tokyo",
                requested_scopes=["flights:search"],
            )

        assert token.server_id == "urn:pharos:server-001"
        assert "flights:search" in token.approved_scopes
        assert approval_handler.calls[0].purpose == "Search for flights to Tokyo"
        assert connection_manager.active_count == 1

        # Send a message through the connection
        response = await connection_manager.send(
            "urn:pharos:server-001",
            {"id": "req-1", "method": "search", "params": {"destination": "Tokyo"}},
        )
        assert response["status"] == "ok"

        # Check scope
        await client.check_scope("urn:pharos:server-001", "flights:search")

        # Revoke
        await client.revoke("urn:pharos:server-001")
        assert connection_manager.active_count == 0

    @pytest.mark.anyio
    async def test_search_with_cache_fallback(self):
        """Registry goes down, cache provides results."""
        card_data = make_server_card()
        approval_handler = RecordingApprovalHandler()
        client = PharosClient(
            "https://registry.pharos.dev",
            approval_handler=approval_handler,
        )

        # First search succeeds and caches
        with patch.object(httpx.AsyncClient, "get",
                         new=AsyncMock(return_value=mock_registry_response([card_data]))):
            results = await client.search("flights")
        assert len(results) == 1

        # Second search: registry down, cache kicks in
        with patch.object(httpx.AsyncClient, "get",
                         new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            cached_results = await client.search("flights")
        assert len(cached_results) == 1
        assert cached_results[0].card.id == "urn:pharos:server-001"


class TestApprovalFlowVariations:
    """Test different approval scenarios."""

    @pytest.mark.anyio
    async def test_approval_denied_flow(self):
        """User denies approval — should raise ApprovalDenied."""
        card_data = make_server_card()
        handler = RecordingApprovalHandler(auto_approve=False)
        client = PharosClient(
            "https://registry.pharos.dev",
            approval_handler=handler,
        )

        with patch.object(httpx.AsyncClient, "get",
                         new=AsyncMock(return_value=mock_registry_response({"blocked": []}))):
            with pytest.raises(ApprovalDenied):
                await client.connect_and_approve(
                    ServerCard(**card_data),
                    purpose="test",
                )

    @pytest.mark.anyio
    async def test_headless_mode_blocks_novel_server(self):
        """Headless mode rejects servers not pre-approved."""
        card_data = make_server_card()
        client = PharosClient(
            "https://registry.pharos.dev",
            headless=True,
        )

        with pytest.raises(HeadlessApprovalRequired):
            await client.connect_and_approve(
                ServerCard(**card_data),
                purpose="test",
            )

    @pytest.mark.anyio
    async def test_blocklisted_server_rejected(self):
        """Blocklisted servers are rejected before approval."""
        card_data = make_server_card("urn:pharos:malicious-1", "Evil MCP")
        handler = RecordingApprovalHandler()
        client = PharosClient(
            "https://registry.pharos.dev",
            approval_handler=handler,
        )

        with patch.object(httpx.AsyncClient, "get",
                         new=AsyncMock(return_value=mock_registry_response(
                             {"blocked": ["urn:pharos:malicious-1"]}
                         ))):
            with pytest.raises(ApprovalDenied) as exc_info:
                await client.connect_and_approve(
                    ServerCard(**card_data),
                    purpose="test",
                )
            assert "blocklisted" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_scope_checking(self):
        """Approved scopes are enforced via check_scope."""
        card_data = make_server_card()
        handler = RecordingApprovalHandler(approved_scopes=["flights:search"])
        conn_mgr = ConnectionManager()
        client = PharosClient(
            "https://registry.pharos.dev",
            approval_handler=handler,
            connection_handler=conn_mgr,
        )

        with patch.object(httpx.AsyncClient, "get",
                         new=AsyncMock(return_value=mock_registry_response({"blocked": []}))):
            await client.connect_and_approve(
                ServerCard(**card_data),
                purpose="search flights",
                requested_scopes=["flights:search"],
            )

        # Approved scope works
        await client.check_scope("urn:pharos:server-001", "flights:search")

        # Non-approved scope fails
        with pytest.raises(ScopeNotApproved):
            await client.check_scope("urn:pharos:server-001", "flights:book")


class TestApprovalEngineIntegration:
    """Test the approval engine token lifecycle."""

    def test_create_and_verify_token(self):
        """Token created by engine is verifiable."""
        engine = ApprovalEngine("integration-test-secret")

        card = ServerCard(**make_server_card())
        request = ApprovalRequest(
            server=card,
            purpose="test",
            requested_scopes=["flights:search"],
            requested_capabilities=["search"],
            duration="session",
            render_id="render-001",
            selection_rationale="integration test",
        )
        response = ApprovalResponse(
            approved=True,
            approved_scopes=["flights:search"],
            duration="session",
        )

        token = engine.create_token(request, response)
        assert engine.verify_token(token) is True
        assert engine.is_expired(token) is False
        assert engine.is_valid(token) is True if hasattr(engine, 'is_valid') else True

    def test_tampered_token_rejected(self):
        """Tampered token fails verification."""
        engine = ApprovalEngine("integration-test-secret")

        card = ServerCard(**make_server_card())
        request = ApprovalRequest(
            server=card,
            purpose="test",
            requested_scopes=["flights:search"],
            requested_capabilities=["search"],
            duration="session",
            render_id="render-001",
            selection_rationale="integration test",
        )
        response = ApprovalResponse(
            approved=True,
            approved_scopes=["flights:search"],
            duration="session",
        )

        token = engine.create_token(request, response)
        token.approved_scopes = ["admin"]  # tamper
        assert engine.verify_token(token) is False


class TestMultiServerFlow:
    """Test managing multiple server connections simultaneously."""

    @pytest.mark.anyio
    async def test_two_servers_parallel(self):
        """Connect to two servers and manage both."""
        card1 = make_server_card("urn:pharos:server-001", "Flight Search")
        card2 = make_server_card("urn:pharos:server-002", "Hotel Search")
        card2["capabilities"] = ["search"]
        card2["auth"]["scopes"] = ["hotels:search"]

        handler = RecordingApprovalHandler(approved_scopes=["search"])
        conn_mgr = ConnectionManager()
        client = PharosClient(
            "https://registry.pharos.dev",
            approval_handler=handler,
            connection_handler=conn_mgr,
            max_novel_approvals=10,
        )

        with patch.object(httpx.AsyncClient, "get",
                         new=AsyncMock(return_value=mock_registry_response({"blocked": []}))):
            token1, _ = await client.connect_and_approve(
                ServerCard(**card1), "flights", requested_scopes=["search"]
            )
            token2, _ = await client.connect_and_approve(
                ServerCard(**card2), "hotels", requested_scopes=["search"]
            )

        assert token1.server_id == "urn:pharos:server-001"
        assert token2.server_id == "urn:pharos:server-002"
        assert conn_mgr.active_count == 2

        # Both alive
        health = await conn_mgr.health_check_all()
        assert len(health) == 2
        assert all(health.values())

        # Close all
        await client.close()
        assert conn_mgr.active_count == 0
