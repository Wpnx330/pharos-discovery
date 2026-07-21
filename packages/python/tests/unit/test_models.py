"""Tests for pharos_discovery.models — core types matching SPEC §8.3."""

import pytest
from pydantic import ValidationError

from pharos_discovery.models.server_card import (
    AuthSpec,
    Publisher,
    ServerCard,
)
from pharos_discovery.models.approval import (
    ApprovalRequest,
    ApprovalResponse,
    ApprovalToken,
    PlanApprovalRequest,
    PlanApprovalResponse,
)
from pharos_discovery.models.oauth import (
    OAuthResult,
    OAuthStatus,
    RevocationResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _publisher(**overrides):
    defaults = {"id": "did:web:example.com", "name": "TestPub", "verified": True, "verification_method": "domain_control"}
    defaults.update(overrides)
    return Publisher(**defaults)


def _auth(**overrides):
    defaults = {"type": "none"}
    defaults.update(overrides)
    return AuthSpec(**defaults)


def _server_card(**overrides):
    defaults = {
        "id": "urn:pharos:server-001",
        "display_name": "Flight Search",
        "description": "Search and book flights",
        "publisher": _publisher(),
        "version": "1.0.0",
        "transport": ["http+sse"],
        "capabilities": ["flight_search", "flight_book"],
        "tools_count": 5,
        "auth": _auth(),
        "availability": "native",
        "source_registry": "https://registry.pharos.dev",
        "published_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
        "status": "active",
    }
    defaults.update(overrides)
    return ServerCard(**defaults)


# ---------------------------------------------------------------------------
# ServerCard
# ---------------------------------------------------------------------------

class TestServerCard:
    def test_full_creation(self):
        card = _server_card(
            endpoint="https://api.example.com/mcp",
            pricing={"model": "freemium", "price_usd": 0.0, "unit": "per_call"},
            tags=["travel", "flights"],
            representative_queries=["book a flight", "find cheap flights"],
        )
        assert card.id == "urn:pharos:server-001"
        assert card.display_name == "Flight Search"
        assert card.publisher.verified is True
        assert card.transport == ["http+sse"]
        assert card.tools_count == 5
        assert card.tools_count_verified is False  # default
        assert card.availability == "native"
        assert len(card.tags) == 2
        assert card.status == "active"

    def test_minimal_fields(self):
        card = _server_card()
        # Optional fields should have defaults
        assert card.endpoint is None
        assert card.stdio_command is None
        assert card.pricing is None
        assert card.pricing_verified is False
        assert card.rating is None
        assert card.trust is None
        assert card.representative_queries == []
        assert card.pharos_score is None
        assert card.tags == []
        assert card.data_residency == []
        assert card.protocol_versions == []
        assert card.health_endpoint is None

    def test_serialization_roundtrip(self):
        card = _server_card()
        data = card.model_dump()
        restored = ServerCard(**data)
        assert restored == card

    def test_missing_required_field_fails(self):
        with pytest.raises(ValidationError):
            ServerCard(
                id="x",
                display_name="x",
                description="x",
                # missing publisher
                version="1.0.0",
                transport=["http+sse"],
                capabilities=["x"],
                tools_count=1,
                auth=_auth(),
                availability="native",
                source_registry="r",
                published_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
                status="active",
            )

    def test_invalid_transport_value(self):
        with pytest.raises(ValidationError):
            _server_card(transport=["grpc"])

    def test_invalid_status(self):
        with pytest.raises(ValidationError):
            _server_card(status="unknown")


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------

class TestPublisher:
    def test_verified_false(self):
        pub = _publisher(verified=False, verification_method=None)
        assert pub.verified is False
        assert pub.verification_method is None

    def test_minimal(self):
        pub = Publisher(id="did:web:x.com", name="X")
        assert pub.verified is None
        assert pub.verification_method is None
        assert pub.contact is None


# ---------------------------------------------------------------------------
# ApprovalRequest / Response / Token
# ---------------------------------------------------------------------------

class TestApprovalRequest:
    def test_creation(self):
        req = ApprovalRequest(
            server=_server_card(),
            purpose="Book a flight",
            requested_scopes=["flight_search", "flight_book"],
            requested_capabilities=["flight_search", "flight_book"],
            duration="session",
            render_id="render-001",
            selection_rationale="ranked #1 for flight_search",
        )
        assert req.purpose == "Book a flight"
        assert req.duration == "session"
        assert len(req.requested_scopes) == 2

    def test_selection_rationale_required(self):
        with pytest.raises(ValidationError):
            ApprovalRequest(
                server=_server_card(),
                purpose="x",
                requested_scopes=[],
                requested_capabilities=[],
                duration="once",
                render_id="r",
                # missing selection_rationale
            )

    def test_invalid_duration(self):
        with pytest.raises(ValidationError):
            ApprovalRequest(
                server=_server_card(),
                purpose="x",
                requested_scopes=[],
                requested_capabilities=[],
                duration="forever",
                render_id="r",
                selection_rationale="x",
            )


class TestApprovalResponse:
    def test_approved(self):
        resp = ApprovalResponse(
            approved=True,
            approved_scopes=["flight_search"],
            duration="session",
        )
        assert resp.approved is True
        assert resp.deny_reason is None

    def test_denied_with_reason(self):
        resp = ApprovalResponse(
            approved=False,
            approved_scopes=[],
            duration="",
            deny_reason="excessive_scopes",
        )
        assert resp.approved is False
        assert resp.deny_reason == "excessive_scopes"

    def test_invalid_deny_reason(self):
        with pytest.raises(ValidationError):
            ApprovalResponse(
                approved=False,
                approved_scopes=[],
                duration="",
                deny_reason="invalid_reason",
            )


class TestApprovalToken:
    def test_all_fields(self):
        token = ApprovalToken(
            token_id="tok-001",
            server_id="urn:pharos:server-001",
            approved_scopes=["flight_search"],
            approved_capabilities=["flight_search"],
            approved_oauth_scopes=[],
            duration="session",
            approved_at="2026-07-01T00:00:00Z",
            expires_at="2026-07-01T01:00:00Z",
            signature="base64sig",
        )
        assert token.token_id == "tok-001"
        assert token.server_id == "urn:pharos:server-001"
        assert token.approved_oauth_scopes == []
        assert token.signature == "base64sig"

    def test_missing_field_fails(self):
        with pytest.raises(ValidationError):
            ApprovalToken(
                token_id="tok-001",
                server_id="urn:pharos:server-001",
                approved_scopes=["flight_search"],
                approved_capabilities=["flight_search"],
                approved_oauth_scopes=[],
                duration="session",
                approved_at="2026-07-01T00:00:00Z",
                expires_at="2026-07-01T01:00:00Z",
                # missing signature
            )


# ---------------------------------------------------------------------------
# PlanApproval
# ---------------------------------------------------------------------------

class TestPlanApprovalRequest:
    def test_multiple_steps(self):
        req1 = ApprovalRequest(
            server=_server_card(id="urn:pharos:s1"),
            purpose="search flights",
            requested_scopes=["flight_search"],
            requested_capabilities=["flight_search"],
            duration="once",
            render_id="r1",
            selection_rationale="best for flights",
        )
        req2 = ApprovalRequest(
            server=_server_card(id="urn:pharos:s2"),
            purpose="file expense",
            requested_scopes=["expense_write"],
            requested_capabilities=["expense_write"],
            duration="once",
            render_id="r2",
            selection_rationale="best for expenses",
        )
        plan = PlanApprovalRequest(
            plan_summary="Book flight then file expense",
            steps=[req1, req2],
            render_id="plan-001",
        )
        assert len(plan.steps) == 2
        assert plan.steps[0].server.id == "urn:pharos:s1"
        assert plan.steps[1].server.id == "urn:pharos:s2"


class TestPlanApprovalResponse:
    def test_creation(self):
        resp = PlanApprovalResponse(
            approved=True,
            per_step=[
                ApprovalResponse(approved=True, approved_scopes=["flight_search"], duration="once"),
                ApprovalResponse(approved=True, approved_scopes=["expense_write"], duration="once"),
            ],
        )
        assert resp.approved is True
        assert len(resp.per_step) == 2


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

class TestOAuthResult:
    def test_server_side(self):
        # server_side: access_token is None (held by server, not agent)
        result = OAuthResult(
            authorized=True,
            scope=["flight_search"],
            acquired_via="redirect",
            auth_held_by="server",
            confirmed_at="2026-07-01T00:00:00Z",
        )
        assert result.authorized is True
        assert result.access_token is None
        assert result.auth_held_by == "server"

    def test_agent_side(self):
        result = OAuthResult(
            authorized=True,
            access_token="tok-abc",
            token_type="Bearer",
            expires_in=3600,
            scope=["flight_search"],
            acquired_via="inline",
            auth_held_by="agent",
            confirmed_at="2026-07-01T00:00:00Z",
        )
        assert result.access_token == "tok-abc"
        assert result.token_type == "Bearer"
        assert result.auth_held_by == "agent"

    def test_error(self):
        result = OAuthResult(
            authorized=False,
            scope=[],
            acquired_via="inline",
            auth_held_by="server",
            confirmed_at="2026-07-01T00:00:00Z",
            error="access_denied",
        )
        assert result.authorized is False
        assert result.error == "access_denied"


class TestOAuthStatus:
    def test_valid(self):
        status = OAuthStatus(valid=True, expires_at="2026-07-02T00:00:00Z", scopes=["x"])
        assert status.valid is True
        assert status.scopes == ["x"]

    def test_expired(self):
        status = OAuthStatus(valid=False)
        assert status.valid is False
        assert status.expires_at is None
        assert status.scopes == []


class TestRevocationResult:
    def test_confirmed(self):
        result = RevocationResult(
            revoked=True,
            revocation_confirmed=True,
            revocation_proof="signed-proof-abc",
        )
        assert result.revoked is True
        assert result.revocation_confirmed is True
        assert result.revocation_proof == "signed-proof-abc"

    def test_unconfirmed(self):
        result = RevocationResult(
            revoked=True,
            revocation_confirmed=False,
            fallback_revoke_url="https://example.com/revoke",
        )
        assert result.revocation_confirmed is False
        assert result.fallback_revoke_url == "https://example.com/revoke"
