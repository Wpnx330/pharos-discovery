import pytest
import time
from pharos_discovery.approval.engine import ApprovalEngine
from pharos_discovery.models import (
    ApprovalRequest,
    ApprovalResponse,
    ApprovalToken,
    ServerCard,
)


def make_card():
    return ServerCard(
        id="urn:pharos:server-001",
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


def make_request():
    return ApprovalRequest(
        server=make_card(),
        purpose="Test purpose",
        requested_scopes=["search"],
        requested_capabilities=["search"],
        duration="session",
        render_id="render-001",
        selection_rationale="testing",
    )


def make_response():
    return ApprovalResponse(
        approved=True,
        approved_scopes=["search"],
        duration="session",
    )


@pytest.fixture
def engine():
    return ApprovalEngine("test-secret-key")


class TestCreateToken:
    def test_creates_valid_token(self, engine):
        token = engine.create_token(make_request(), make_response())
        assert token.token_id.startswith("tok_")
        assert token.server_id == "urn:pharos:server-001"
        assert token.approved_scopes == ["search"]
        assert token.approved_capabilities == ["search"]
        assert token.signature  # non-empty
        assert token.signature != "unsigned"

    def test_token_has_expiry(self, engine):
        token = engine.create_token(make_request(), make_response(), token_ttl_seconds=1800)
        approved_at = int(token.approved_at)
        expires_at = int(token.expires_at)
        assert expires_at - approved_at == 1800

    def test_unique_token_ids(self, engine):
        token1 = engine.create_token(make_request(), make_response())
        token2 = engine.create_token(make_request(), make_response())
        # Uniqueness comes from monotonic_ns(); no sleep needed.
        assert token1.token_id != token2.token_id


class TestVerifyToken:
    def test_valid_token(self, engine):
        token = engine.create_token(make_request(), make_response())
        assert engine.verify_token(token) is True

    def test_tampered_scope(self, engine):
        token = engine.create_token(make_request(), make_response())
        token.approved_scopes = ["admin"]  # tamper!
        assert engine.verify_token(token) is False

    def test_tampered_expiry(self, engine):
        token = engine.create_token(make_request(), make_response())
        token.expires_at = str(int(time.time()) + 999999)
        assert engine.verify_token(token) is False

    def test_tampered_server_id(self, engine):
        token = engine.create_token(make_request(), make_response())
        token.server_id = "urn:pharos:evil"
        assert engine.verify_token(token) is False

    def test_tampered_capabilities(self, engine):
        token = engine.create_token(make_request(), make_response())
        token.approved_capabilities = ["admin"]  # tamper!
        assert engine.verify_token(token) is False

    def test_tampered_duration(self, engine):
        token = engine.create_token(make_request(), make_response())
        token.duration = "persistent"  # tamper!
        assert engine.verify_token(token) is False

    def test_tampered_approved_at(self, engine):
        token = engine.create_token(make_request(), make_response())
        token.approved_at = str(int(time.time()) - 99999)  # tamper!
        assert engine.verify_token(token) is False

    def test_tampered_token_id(self, engine):
        token = engine.create_token(make_request(), make_response())
        token.token_id = "tok_evil1234567890"
        assert engine.verify_token(token) is False

    def test_different_secret_fails(self):
        engine1 = ApprovalEngine("secret-1")
        engine2 = ApprovalEngine("secret-2")
        token = engine1.create_token(make_request(), make_response())
        assert engine2.verify_token(token) is False

    def test_same_secret_cross_engine_verifies(self):
        secret = "shared-secret"
        engine1 = ApprovalEngine(secret)
        engine2 = ApprovalEngine(secret)
        token = engine1.create_token(make_request(), make_response())
        assert engine2.verify_token(token) is True


class TestIsExpired:
    def test_not_expired(self, engine):
        token = engine.create_token(make_request(), make_response(), token_ttl_seconds=3600)
        assert engine.is_expired(token) is False

    def test_expired(self, engine):
        token = engine.create_token(make_request(), make_response(), token_ttl_seconds=-1)
        assert engine.is_expired(token) is True

    def test_invalid_expiry(self, engine):
        token = engine.create_token(make_request(), make_response())
        token.expires_at = "invalid"
        assert engine.is_expired(token) is True

    def test_zero_ttl_is_immediately_expired(self, engine):
        token = engine.create_token(make_request(), make_response(), token_ttl_seconds=0)
        # expires_at == approved_at == now; >= means immediately expired
        assert engine.is_expired(token) is True


class TestIsValid:
    def test_fresh_valid_token(self, engine):
        token = engine.create_token(make_request(), make_response(), token_ttl_seconds=3600)
        assert engine.is_valid(token) is True

    def test_expired_token_not_valid(self, engine):
        token = engine.create_token(make_request(), make_response(), token_ttl_seconds=-1)
        # signature is fine, but expired => not valid
        assert engine.verify_token(token) is True
        assert engine.is_expired(token) is True
        assert engine.is_valid(token) is False

    def test_tampered_token_not_valid(self, engine):
        token = engine.create_token(make_request(), make_response())
        token.approved_scopes = ["admin"]
        assert engine.is_valid(token) is False


class TestEmptySecret:
    def test_empty_secret_rejected(self):
        with pytest.raises(ValueError):
            ApprovalEngine("")

    def test_none_secret_rejected(self):
        with pytest.raises((ValueError, TypeError)):
            ApprovalEngine(None)  # type: ignore[arg-type]


class TestSignatureFormat:
    def test_signature_is_hex(self, engine):
        token = engine.create_token(make_request(), make_response())
        # HMAC-SHA256 produces 64 hex chars
        assert len(token.signature) == 64
        int(token.signature, 16)  # should not raise

    def test_signature_deterministic(self, engine):
        # Same token fields produce same signature
        token1 = engine.create_token(make_request(), make_response())
        # Recreate with same fields
        token2 = ApprovalToken(
            token_id=token1.token_id,
            server_id=token1.server_id,
            approved_scopes=token1.approved_scopes,
            approved_capabilities=token1.approved_capabilities,
            approved_oauth_scopes=token1.approved_oauth_scopes,
            duration=token1.duration,
            approved_at=token1.approved_at,
            expires_at=token1.expires_at,
            signature="",
        )
        token2.signature = engine._sign(token2)
        assert token1.signature == token2.signature
