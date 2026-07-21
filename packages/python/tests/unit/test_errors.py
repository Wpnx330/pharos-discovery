"""Tests for pharos_discovery.errors module."""

import pytest

from pharos_discovery.errors import (
    PharosError,
    RegistryUnavailable,
    NoServersFound,
    ApprovalDenied,
    ScopeNotApproved,
    ConnectionFailed,
    DiscoveryDegraded,
    SignatureVerificationFailed,
    HeadlessApprovalRequired,
    ConsentFatigueWarning,
    OAuthError,
    TransportError,
)


class TestRegistryUnavailable:
    def test_with_status_and_detail(self):
        err = RegistryUnavailable("https://reg.example.com", 503, "timeout")
        assert err.url == "https://reg.example.com"
        assert err.status == 503
        assert err.detail == "timeout"
        assert "Registry unavailable: https://reg.example.com (HTTP 503): timeout" in str(err)

    def test_minimal(self):
        err = RegistryUnavailable("https://reg.example.com")
        assert err.url == "https://reg.example.com"
        assert err.status is None
        assert "Registry unavailable: https://reg.example.com" in str(err)


class TestNoServersFound:
    def test_with_query(self):
        err = NoServersFound("flight search")
        assert err.query == "flight search"
        assert "flight search" in str(err)

    def test_without_query(self):
        err = NoServersFound()
        assert err.query == ""
        assert "No servers found" in str(err)


class TestApprovalDenied:
    def test_with_deny_reason(self):
        err = ApprovalDenied("server-123", "excessive_scopes")
        assert err.server_id == "server-123"
        assert err.deny_reason == "excessive_scopes"
        assert "server-123" in str(err)
        assert "excessive_scopes" in str(err)

    def test_without_deny_reason(self):
        err = ApprovalDenied("server-123")
        assert err.deny_reason is None


class TestScopeNotApproved:
    def test_attributes(self):
        err = ScopeNotApproved("flight_book", "server-456")
        assert err.scope == "flight_book"
        assert err.server_id == "server-456"
        assert "flight_book" in str(err)
        assert "server-456" in str(err)


class TestConnectionFailed:
    def test_attributes(self):
        err = ConnectionFailed("server-789", "connection refused")
        assert err.server_id == "server-789"
        assert err.detail == "connection refused"
        assert "server-789" in str(err)
        assert "connection refused" in str(err)


class TestDiscoveryDegraded:
    def test_message(self):
        err = DiscoveryDegraded()
        assert "Discovery degraded" in str(err)


class TestSignatureVerificationFailed:
    def test_default_message(self):
        err = SignatureVerificationFailed()
        assert err.detail == "Invalid signature"
        assert "Invalid signature" in str(err)

    def test_custom_detail(self):
        err = SignatureVerificationFailed("Bad key")
        assert err.detail == "Bad key"


class TestHeadlessApprovalRequired:
    def test_attributes(self):
        err = HeadlessApprovalRequired("server-novel")
        assert err.server_id == "server-novel"
        assert "server-novel" in str(err)


class TestConsentFatigueWarning:
    def test_attributes(self):
        err = ConsentFatigueWarning(7)
        assert err.count == 7
        assert "7" in str(err)


class TestOAuthError:
    def test_with_detail(self):
        err = OAuthError("invalid_grant", "token expired")
        assert err.error == "invalid_grant"
        assert err.detail == "token expired"
        assert "invalid_grant" in str(err)
        assert "token expired" in str(err)

    def test_without_detail(self):
        err = OAuthError("access_denied")
        assert err.detail is None
        assert "access_denied" in str(err)


class TestTransportError:
    def test_attributes(self):
        err = TransportError("http+sse", "stream closed")
        assert err.transport == "http+sse"
        assert err.detail == "stream closed"
        assert "http+sse" in str(err)
        assert "stream closed" in str(err)


class TestInheritance:
    @pytest.mark.parametrize(
        "err",
        [
            RegistryUnavailable("url"),
            NoServersFound(),
            ApprovalDenied("s"),
            ScopeNotApproved("sc", "s"),
            ConnectionFailed("s", "d"),
            DiscoveryDegraded(),
            SignatureVerificationFailed(),
            HeadlessApprovalRequired("s"),
            ConsentFatigueWarning(1),
            OAuthError("e"),
            TransportError("t", "d"),
        ],
    )
    def test_all_inherit_from_pharos_error(self, err):
        assert isinstance(err, PharosError)
        assert isinstance(err, Exception)
