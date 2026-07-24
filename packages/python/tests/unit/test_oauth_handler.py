"""Unit tests for the MCP Apps inline OAuth iframe renderer (§18.5-18.6).

Tests PKCE generation, URL construction, state nonce generation, token
exchange, and the OAuthFlowHandler with mocked HTTP calls.
"""

import hashlib
import base64
import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pharos_discovery.connection.oauth.handler import (
    BrowserOAuthRenderer,
    OAuthFlowHandler,
    OAuthServerConfig,
    TerminalOAuthRenderer,
    compute_pkce_challenge,
    generate_pkce_verifier,
    generate_state_nonce,
)
from pharos_discovery.errors import OAuthError
from pharos_discovery.models import OAuthResult


# ---------------------------------------------------------------------------
# Sample OAuth server config (from SPEC.md §8.4 GET /v1/oauth/servers/{name})
# ---------------------------------------------------------------------------

SAMPLE_CONFIG_RESPONSE = {
    "name": "io.salesforce/salesforce-mcp",
    "version": "1.4.0",
    "oauth": {
        "app_registration": {
            "client_id": "3MVG9...public...",
            "auth_server_url": "https://login.salesforce.com",
            "grant_type": "authorization_code",
            "pkce_required": True,
            "scopes": ["api", "refresh_token", "offline_access"],
            "consent_defaults": {
                "scopes": ["api"],
                "overridable": True,
                "description": "Default scopes for agent access.",
            },
            "redirect_uri_pattern": "https://registry.pharos.dev/v1/oauth/callback/",
            "token_endpoint": "https://login.salesforce.com/services/oauth2/token",
            "authorize_endpoint": "https://login.salesforce.com/services/oauth2/authorize",
        },
        "ui": {
            "resource_uri": "ui://oauth/login",
            "csp": ["https://login.salesforce.com"],
            "description": "Pointer to the IdP authorize URL.",
        },
        "secret_handling": "server_side",
        "secret_description": "Client secret held by the MCP server.",
    },
}

SAMPLE_CALLBACK_RESPONSE = {
    "access_token": "00D...token...",
    "token_type": "Bearer",
    "expires_in": 3600,
    "refresh_token": "5Aep....",
    "scope": "api refresh_token offline_access",
    "id_token": "eyJhbG...eyJ...",
}


def make_config() -> OAuthServerConfig:
    return OAuthServerConfig(SAMPLE_CONFIG_RESPONSE)


# ---------------------------------------------------------------------------
# PKCE tests
# ---------------------------------------------------------------------------

class TestPKCE:
    def test_verifier_length(self):
        v = generate_pkce_verifier()
        assert len(v) == 64
        assert 43 <= len(v) <= 128

    def test_verifier_custom_length(self):
        v = generate_pkce_verifier(43)
        assert len(v) == 43
        v = generate_pkce_verifier(128)
        assert len(v) == 128

    def test_verifier_invalid_length(self):
        with pytest.raises(ValueError):
            generate_pkce_verifier(10)
        with pytest.raises(ValueError):
            generate_pkce_verifier(200)

    def test_verifier_unreserved_charset(self):
        v = generate_pkce_verifier()
        unreserved = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        assert set(v).issubset(unreserved)

    def test_verifier_randomness(self):
        v1 = generate_pkce_verifier()
        v2 = generate_pkce_verifier()
        assert v1 != v2

    def test_challenge_s256(self):
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        assert compute_pkce_challenge(verifier, "S256") == expected

    def test_challenge_plain(self):
        verifier = "test_verifier_123"
        assert compute_pkce_challenge(verifier, "plain") == verifier

    def test_challenge_unsupported_method(self):
        with pytest.raises(ValueError):
            compute_pkce_challenge("v", "MD5")

    def test_challenge_no_padding(self):
        verifier = generate_pkce_verifier()
        challenge = compute_pkce_challenge(verifier)
        assert "=" not in challenge
        assert "+" not in challenge
        assert "/" not in challenge

    def test_challenge_is_base64url_sha256(self):
        verifier = "test_verifier"
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        assert compute_pkce_challenge(verifier) == expected


# ---------------------------------------------------------------------------
# State nonce tests
# ---------------------------------------------------------------------------

class TestStateNonce:
    def test_state_is_string(self):
        s = generate_state_nonce()
        assert isinstance(s, str)

    def test_state_sufficient_length(self):
        s = generate_state_nonce()
        # token_urlsafe(32) produces ~43 chars
        assert len(s) >= 32

    def test_state_randomness(self):
        s1 = generate_state_nonce()
        s2 = generate_state_nonce()
        assert s1 != s2

    def test_state_urlsafe(self):
        s = generate_state_nonce()
        # Should be URL-safe base64 characters
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in s)


# ---------------------------------------------------------------------------
# OAuthServerConfig tests
# ---------------------------------------------------------------------------

class TestOAuthServerConfig:
    def test_parse_full_config(self):
        config = make_config()
        assert config.name == "io.salesforce/salesforce-mcp"
        assert config.client_id == "3MVG9...public..."
        assert config.authorize_endpoint == "https://login.salesforce.com/services/oauth2/authorize"
        assert config.token_endpoint == "https://login.salesforce.com/services/oauth2/token"
        assert config.auth_server_url == "https://login.salesforce.com"
        assert config.scopes == ["api", "refresh_token", "offline_access"]
        assert config.pkce_required is True
        assert config.secret_handling == "server_side"
        assert config.ui_resource_uri == "ui://oauth/login"
        assert config.ui_csp == ["https://login.salesforce.com"]

    def test_idp_origin(self):
        config = make_config()
        assert config.idp_origin == "https://login.salesforce.com"

    def test_flattened_config(self):
        # Some configs might not nest under "oauth"
        flat = {
            "name": "test-server",
            "app_registration": {
                "client_id": "cid",
                "authorize_endpoint": "https://idp.example.com/authorize",
            },
        }
        config = OAuthServerConfig(flat)
        assert config.client_id == "cid"
        assert config.authorize_endpoint == "https://idp.example.com/authorize"


# ---------------------------------------------------------------------------
# URL construction tests
# ---------------------------------------------------------------------------

class TestBuildAuthorizeUrl:
    def test_basic_url_construction(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")
        config = make_config()
        url, state, verifier, redirect_uri = handler.build_authorize_url(config)

        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)

        assert parsed.scheme == "https"
        assert parsed.netloc == "login.salesforce.com"
        assert parsed.path == "/services/oauth2/authorize"
        assert params["response_type"] == ["code"]
        assert params["client_id"] == ["3MVG9...public..."]
        assert params["state"] == [state]
        assert params["code_challenge_method"] == ["S256"]
        assert "code_challenge" in params
        assert len(verifier) >= 43

    def test_pkce_verifier_matches_challenge(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")
        config = make_config()
        url, state, verifier, redirect_uri = handler.build_authorize_url(config)

        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        challenge = params["code_challenge"][0]
        expected = compute_pkce_challenge(verifier)
        assert challenge == expected

    def test_no_pkce_when_not_required(self):
        config = make_config()
        config.pkce_required = False
        handler = OAuthFlowHandler("https://registry.pharos.dev")
        url, state, verifier, redirect_uri = handler.build_authorize_url(config)

        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert "code_challenge" not in params
        assert "code_challenge_method" not in params
        assert verifier == ""

    def test_custom_state(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")
        config = make_config()
        custom_state = "my-custom-state-123"
        url, state, _, _ = handler.build_authorize_url(config, state=custom_state)
        assert state == custom_state
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert params["state"] == [custom_state]

    def test_custom_redirect_uri(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")
        config = make_config()
        custom_redirect = "http://127.0.0.1:9999/callback"
        url, _, _, redirect_uri = handler.build_authorize_url(config, redirect_uri=custom_redirect)
        assert redirect_uri == custom_redirect
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert params["redirect_uri"] == [custom_redirect]

    def test_scopes_in_url(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")
        config = make_config()
        url, _, _, _ = handler.build_authorize_url(config)
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert params["scope"] == ["api refresh_token offline_access"]

    def test_extra_params(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")
        config = make_config()
        url, _, _, _ = handler.build_authorize_url(
            config, extra_params={"access_type": "offline", "prompt": "consent"}
        )
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert params["access_type"] == ["offline"]
        assert params["prompt"] == ["consent"]

    def test_missing_authorize_endpoint_raises(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")
        config = OAuthServerConfig({"oauth": {"app_registration": {"client_id": "x"}}})
        with pytest.raises(OAuthError, match="missing_authorize_endpoint"):
            handler.build_authorize_url(config)

    def test_missing_client_id_raises(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")
        config = OAuthServerConfig({
            "oauth": {"app_registration": {"authorize_endpoint": "https://idp.example.com/auth"}}
        })
        with pytest.raises(OAuthError, match="missing_client_id"):
            handler.build_authorize_url(config)


# ---------------------------------------------------------------------------
# Token exchange tests (mocked HTTP)
# ---------------------------------------------------------------------------

class TestExchangeCode:
    @pytest.mark.anyio
    async def test_successful_exchange(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_CALLBACK_RESPONSE

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await handler.exchange_code("state123", "code456", "verifier789")

        assert isinstance(result, OAuthResult)
        assert result.authorized is True
        assert result.access_token == "00D...token..."
        assert result.token_type == "Bearer"
        assert result.expires_in == 3600
        assert result.refresh_token == "5Aep...."
        assert result.scope == ["api", "refresh_token", "offline_access"]
        assert result.acquired_via == "oauth"
        assert result.auth_held_by == "mcp_server"
        assert result.confirmation_jwt == "eyJhbG...eyJ..."

    @pytest.mark.anyio
    async def test_exchange_http_error(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="exchange_failed"):
                await handler.exchange_code("state123", "code456")

    @pytest.mark.anyio
    async def test_exchange_network_error(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="exchange_failed"):
                await handler.exchange_code("state123", "code456")

    @pytest.mark.anyio
    async def test_exchange_returns_error_result(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": "invalid_grant", "scope": "api"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await handler.exchange_code("state123", "code456")

        assert result.authorized is False
        assert result.error == "invalid_grant"


# ---------------------------------------------------------------------------
# get_server_config tests (mocked HTTP)
# ---------------------------------------------------------------------------

class TestGetServerConfig:
    @pytest.mark.anyio
    async def test_fetch_config_success(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_CONFIG_RESPONSE

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            config = await handler.get_server_config("io.salesforce/salesforce-mcp")

        assert config.name == "io.salesforce/salesforce-mcp"
        assert config.client_id == "3MVG9...public..."

    @pytest.mark.anyio
    async def test_fetch_config_not_found(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="server_not_configured"):
                await handler.get_server_config("nonexistent/server")


# ---------------------------------------------------------------------------
# Full connect() flow tests (mocked renderer + HTTP)
# ---------------------------------------------------------------------------

class TestConnectFlow:
    @pytest.mark.anyio
    async def test_connect_success(self):
        """Test the full connect() flow with a mock renderer."""
        handler = OAuthFlowHandler("https://registry.pharos.dev")

        # Mock renderer that returns a fake callback URL
        class MockRenderer:
            def render(self, authorize_url, config):
                # Simulate the IdP redirecting with code+state
                # Parse the authorize_url to get the state
                params = urllib.parse.parse_qs(urllib.parse.urlparse(authorize_url).query)
                state = params["state"][0]
                return f"http://127.0.0.1:12345/callback?code=AUTH_CODE_123&state={state}"

        handler.renderer = MockRenderer()

        # Mock config fetch
        config_response = MagicMock()
        config_response.status_code = 200
        config_response.json.return_value = SAMPLE_CONFIG_RESPONSE

        # Mock callback exchange
        callback_response = MagicMock()
        callback_response.status_code = 200
        callback_response.json.return_value = SAMPLE_CALLBACK_RESPONSE

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "/v1/oauth/servers/" in url:
                return config_response
            return callback_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = mock_get
            mock_client_cls.return_value = mock_client

            result = await handler.connect("io.salesforce/salesforce-mcp")

        assert result.authorized is True
        assert result.access_token == "00D...token..."
        assert result.scope == ["api", "refresh_token", "offline_access"]

    @pytest.mark.anyio
    async def test_connect_state_mismatch(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")

        class MockRenderer:
            def render(self, authorize_url, config):
                # Return a DIFFERENT state than what was sent
                return f"http://127.0.0.1:12345/callback?code=AUTH_CODE&state=WRONG_STATE"

        handler.renderer = MockRenderer()

        config_response = MagicMock()
        config_response.status_code = 200
        config_response.json.return_value = SAMPLE_CONFIG_RESPONSE

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=config_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="state_mismatch"):
                await handler.connect("io.salesforce/salesforce-mcp")

    @pytest.mark.anyio
    async def test_connect_error_callback(self):
        handler = OAuthFlowHandler("https://registry.pharos.dev")

        class MockRenderer:
            def render(self, authorize_url, config):
                return f"http://127.0.0.1:12345/callback?error=access_denied&error_description=user+cancelled"

        handler.renderer = MockRenderer()

        config_response = MagicMock()
        config_response.status_code = 200
        config_response.json.return_value = SAMPLE_CONFIG_RESPONSE

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=config_response)
            mock_client_cls.return_value = mock_client

            result = await handler.connect("io.salesforce/salesforce-mcp")

        assert result.authorized is False
        assert result.error == "access_denied"
        assert result.cancel_reason == "user cancelled"


# ---------------------------------------------------------------------------
# BrowserOAuthRenderer tests
# ---------------------------------------------------------------------------

class TestBrowserOAuthRenderer:
    def test_render_html_contains_iframe(self):
        renderer = BrowserOAuthRenderer()
        config = make_config()
        html = renderer.render_html("https://login.salesforce.com/auth?client_id=x", config)
        assert "<iframe" in html
        assert 'src="https://login.salesforce.com/auth?client_id=x"' in html
        assert "sandbox=" in html
        assert "allow-scripts" in html

    def test_render_html_contains_csp(self):
        renderer = BrowserOAuthRenderer()
        config = make_config()
        html = renderer.render_html("https://login.salesforce.com/auth", config)
        assert "Content-Security-Policy" in html
        assert "frame-src" in html
        assert "https://login.salesforce.com" in html

    def test_render_html_contains_postmessage_listener(self):
        renderer = BrowserOAuthRenderer()
        config = make_config()
        html = renderer.render_html("https://login.salesforce.com/auth", config)
        assert "postMessage" in html
        assert "addEventListener" in html
        assert "oauth/result" in html
        assert "jsonrpc" in html

    def test_render_html_shows_idp_name(self):
        renderer = BrowserOAuthRenderer()
        config = make_config()
        html = renderer.render_html("https://login.salesforce.com/auth", config)
        assert "login.salesforce.com" in html

    def test_render_raises_without_host(self):
        renderer = BrowserOAuthRenderer()
        config = make_config()
        with pytest.raises(OAuthError, match="browser_renderer_not_connected"):
            renderer.render("https://login.salesforce.com/auth", config)


# ---------------------------------------------------------------------------
# TerminalOAuthRenderer tests
# ---------------------------------------------------------------------------

class TestTerminalOAuthRenderer:
    def test_find_free_port(self):
        renderer = TerminalOAuthRenderer()
        port = renderer._find_free_port()
        assert 1024 <= port <= 65535

    def test_init_default_timeout(self):
        renderer = TerminalOAuthRenderer()
        assert renderer.timeout == 300.0

    def test_init_custom_timeout(self):
        renderer = TerminalOAuthRenderer(timeout=60)
        assert renderer.timeout == 60


# ---------------------------------------------------------------------------
# PharosClient.oauth property tests
# ---------------------------------------------------------------------------

class TestClientOAuthProperty:
    @pytest.mark.anyio
    async def test_oauth_property_returns_handler(self):
        from pharos_discovery.client import PharosClient

        client = PharosClient("https://registry.pharos.dev")
        handler = client.oauth
        assert isinstance(handler, OAuthFlowHandler)
        # Same instance on second access (lazy init)
        assert client.oauth is handler

    @pytest.mark.anyio
    async def test_oauth_handler_uses_client_registry_url(self):
        from pharos_discovery.client import PharosClient

        client = PharosClient("https://getpharos.dev", api_key="test-key")
        handler = client.oauth
        assert handler._registry_url == "https://getpharos.dev"
