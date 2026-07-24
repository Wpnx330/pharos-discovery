"""MCP Apps inline OAuth iframe renderer — agent-side component (§18.5-18.6).

Implements the agent-side handler that renders the inline OAuth consent flow
for MCP servers requiring OAuth.  Supports two renderers:

* **Terminal** — opens the system browser to the IdP authorize URL and catches
  the redirect on a local loopback HTTP server (the §18.5.1 fallback).
* **Browser** — creates a sandboxed iframe pointing to the IdP's own origin,
  enforces CSP (only declared IdP origins may be loaded), and communicates
  the result back via ``postMessage`` (JSON-RPC).

Flow (per spec §18.6):
  1. Fetch the server's OAuth config from ``GET /v1/oauth/servers/{name}``.
  2. Extract the IdP ``authorize_endpoint`` URL + scopes + ``client_id``.
  3. Generate a PKCE ``code_verifier`` + ``code_challenge`` (S256).
  4. Construct the authorize URL with a state nonce.
  5. Render (terminal or browser iframe).
  6. Exchange the authorization code for tokens via the MCP server's callback
     endpoint (``GET /v1/oauth/callback/{state}``).
  7. Return an :class:`OAuthResult`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import socket
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Protocol

import httpx

from pharos_discovery.errors import OAuthError
from pharos_discovery.models import OAuthResult

logger = logging.getLogger("pharos_discovery.connection.oauth")


# ---------------------------------------------------------------------------
# PKCE utilities
# ---------------------------------------------------------------------------

def generate_pkce_verifier(length: int = 64) -> str:
    """Generate a cryptographically-random PKCE ``code_verifier``.

    RFC 7636 §4.1: 43-128 chars from the unreserved set.
    """
    if length < 43 or length > 128:
        raise ValueError("code_verifier must be 43-128 characters")
    unreserved = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789"
        "-._~"
    )
    return "".join(secrets.choice(unreserved) for _ in range(length))


def compute_pkce_challenge(verifier: str, method: str = "S256") -> str:
    """Compute the PKCE ``code_challenge`` from a ``code_verifier``.

    S256: ``BASE64URL(SHA256(verifier))`` (no padding).
    """
    if method == "plain":
        return verifier
    if method != "S256":
        raise ValueError(f"Unsupported challenge method: {method}")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state_nonce() -> str:
    """Generate a high-entropy, single-use state nonce (§18.6)."""
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# OAuth server config model
# ---------------------------------------------------------------------------

class OAuthServerConfig:
    """Parsed OAuth configuration returned by ``GET /v1/oauth/servers/{name}``.

    The registry returns a JSON blob with an ``oauth`` key containing
    ``app_registration`` (client_id, authorize_endpoint, scopes, etc.) and
    a ``ui`` key (CSP allowlist, description).
    """

    def __init__(self, raw: dict[str, Any]):
        self._raw = raw
        oauth = raw.get("oauth") or raw  # tolerate flattened shape
        reg = oauth.get("app_registration") or {}
        ui = oauth.get("ui") or {}

        self.name: str = raw.get("name") or oauth.get("name") or ""
        self.version: str = raw.get("version") or oauth.get("version") or ""
        self.client_id: str = reg.get("client_id") or ""
        self.authorize_endpoint: str = reg.get("authorize_endpoint") or ""
        self.token_endpoint: str = reg.get("token_endpoint") or ""
        self.auth_server_url: str = reg.get("auth_server_url") or ""
        self.scopes: list[str] = reg.get("scopes") or []
        self.redirect_uri_pattern: str = reg.get("redirect_uri_pattern") or ""
        self.pkce_required: bool = reg.get("pkce_required", True)
        self.grant_type: str = reg.get("grant_type") or "authorization_code"
        self.consent_defaults: dict[str, Any] = reg.get("consent_defaults") or {}

        # UI / CSP
        self.ui_resource_uri: str = ui.get("resource_uri") or "ui://oauth/login"
        self.ui_csp: list[str] = ui.get("csp") or []
        self.ui_description: str = ui.get("description") or ""
        self.secret_handling: str = oauth.get("secret_handling") or "server_side"

    @property
    def idp_origin(self) -> str:
        """Extract the origin (scheme://host) from ``authorize_endpoint``."""
        parsed = urllib.parse.urlparse(self.authorize_endpoint)
        return f"{parsed.scheme}://{parsed.netloc}"


# ---------------------------------------------------------------------------
# Renderer protocol
# ---------------------------------------------------------------------------

class OAuthRenderer(Protocol):
    """Protocol for OAuth flow renderers."""

    def render(self, authorize_url: str, config: OAuthServerConfig) -> str:
        """Render the OAuth flow and return the redirect URI (with code+state).

        Args:
            authorize_url: The fully-constructed IdP authorize URL.
            config: The parsed OAuth server config (for CSP, display info).

        Returns:
            The full redirect URI string (the callback URL with ``code``
            and ``state`` query parameters), or raises ``OAuthError``.
        """
        ...


# ---------------------------------------------------------------------------
# Terminal renderer — opens system browser, catches callback on localhost
# ---------------------------------------------------------------------------

class TerminalOAuthRenderer:
    """Render the OAuth flow in the terminal.

    Opens the system browser to the IdP authorize URL and spins up a
    local HTTP server on a random port to catch the redirect callback.
    This is the §18.5.1 browser-redirect fallback and the default renderer.
    """

    def __init__(self, timeout: float = 300.0):
        self.timeout = timeout
        self._callback_url: str | None = None
        self._error: str | None = None

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def render(self, authorize_url: str, config: OAuthServerConfig) -> str:
        port = self._find_free_port()
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        # If the authorize_url already has a redirect_uri param, we need to
        # replace it with our local callback.  But in the spec flow, the
        # redirect_uri is the registry's /v1/oauth/callback/{state} — the
        # IdP redirects there, and the registry forwards.  However, for the
        # terminal fallback (§18.5.1), we use the local loopback callback.
        #
        # Replace the redirect_uri in the URL with our local one.
        parsed = urllib.parse.urlparse(authorize_url)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        params["redirect_uri"] = [redirect_uri]
        new_query = urllib.parse.urlencode(params, doseq=True)
        local_authorize_url = urllib.parse.urlunparse(
            parsed._replace(query=new_query)
        )

        print(f"\n  🔐 OAuth Login Required")
        if config.ui_description:
            print(f"     {config.ui_description}")
        print(f"     IdP: {config.auth_server_url or config.idp_origin}")
        print(f"     Opening browser to:\n     {local_authorize_url[:100]}...")
        print(f"\n     Waiting for callback on {redirect_uri} (timeout: {self.timeout}s)...")

        # Start local server
        result_holder: dict[str, str | None] = {"url": None, "error": None}
        done = threading.Event()

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self, *_args: Any) -> None:  # noqa: N802
                parsed_path = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed_path.query)

                if "error" in params:
                    result_holder["error"] = params["error"][0]
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html><body><h2>OAuth failed.</h2></body></html>")
                elif "code" in params:
                    result_holder["url"] = self.path
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body><h2>OAuth complete. You can close this tab.</h2></body></html>"
                    )
                else:
                    self.send_response(404)
                    self.end_headers()
                done.set()

            def log_message(self, *_args: Any) -> None:
                pass  # suppress log noise

        server = HTTPServer(("127.0.0.1", port), CallbackHandler)
        server.timeout = 1

        # Open browser
        try:
            webbrowser.open(local_authorize_url)
        except Exception as exc:
            logger.warning("Failed to open browser: %s", exc)

        # Wait for callback
        import time
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            server.handle_request()
            if done.is_set():
                break

        server.server_close()

        if result_holder["error"]:
            raise OAuthError(result_holder["error"], "IdP returned an error")
        if result_holder["url"] is None:
            raise OAuthError("timeout", f"No callback received within {self.timeout}s")

        full_redirect = redirect_uri + result_holder["url"]
        return full_redirect


# ---------------------------------------------------------------------------
# Browser (iframe) renderer — sandboxed iframe with CSP + postMessage
# ---------------------------------------------------------------------------

class BrowserOAuthRenderer:
    """Render the OAuth flow in a sandboxed iframe (MCP Apps inline UI).

    For web-based agents: creates a sandboxed iframe pointing to the IdP's
    authorize URL, enforces CSP (only allow the declared IdP origin), and
    communicates the result back via ``postMessage`` (JSON-RPC).

    This renderer generates the HTML + JavaScript for embedding in a web
    agent.  The actual iframe DOM manipulation is the host application's
    responsibility — this class provides the configuration and message
    handling contract.
    """

    def __init__(self, timeout: float = 300.0):
        self.timeout = timeout

    def render(self, authorize_url: str, config: OAuthServerConfig) -> str:
        """Render the OAuth flow in a sandboxed iframe.

        In a real web-agent context, this would inject the iframe into the
        DOM and listen for postMessage events.  Here we generate the HTML
        snippet and the postMessage protocol contract.

        Returns:
            The redirect URI (with code+state) received via postMessage.
        """
        csp_origins = config.ui_csp or [config.idp_origin]
        csp_header = "; ".join(f"frame-src {origin}" for origin in csp_origins)

        html = self._generate_iframe_html(authorize_url, config, csp_header)

        print(f"\n  🔐 OAuth Login Required (Browser/iframe mode)")
        print(f"     CSP: {csp_header}")
        print(f"     IdP: {config.idp_origin}")
        print(f"\n  [Generated iframe HTML — {len(html)} bytes]")
        print(f"  [Waiting for postMessage result... timeout: {self.timeout}s]")

        # In a real implementation, the host web agent would:
        # 1. Inject this HTML into the DOM
        # 2. Listen for window.addEventListener('message', handler)
        # 3. The iframe's IdP redirects to the callback, which posts a
        #    JSON-RPC message back to the parent window.
        #
        # For non-browser contexts (CLI/agent), we fall back to the terminal
        # renderer.  The postMessage protocol contract is:
        #
        # { "jsonrpc": "2.0", "method": "oauth/result",
        #   "params": { "code": "...", "state": "...", "redirect_uri": "..." } }

        raise OAuthError(
            "browser_renderer_not_connected",
            "BrowserOAuthRenderer requires a web-agent host to inject the iframe "
            "and listen for postMessage. Use render_html() to get the iframe HTML, "
            "or use TerminalOAuthRenderer for CLI contexts.",
        )

    def render_html(self, authorize_url: str, config: OAuthServerConfig) -> str:
        """Generate the sandboxed iframe HTML for embedding in a web agent.

        Returns the HTML string containing a sandboxed iframe + postMessage
        listener.  The host agent injects this into its DOM and waits for
        the ``oauth/result`` JSON-RPC message.
        """
        csp_origins = config.ui_csp or [config.idp_origin]
        csp_directive = "; ".join(f"frame-src 'self' {origin}" for origin in csp_origins)
        return self._generate_iframe_html(authorize_url, config, csp_directive)

    def _generate_iframe_html(
        self,
        authorize_url: str,
        config: OAuthServerConfig,
        csp_directive: str,
    ) -> str:
        """Generate the iframe HTML with CSP enforcement + postMessage listener."""
        idp_display = config.auth_server_url or config.idp_origin
        allowed_origins_json = json.dumps(config.ui_csp or [config.idp_origin])

        # Build HTML without f-string to avoid brace-escaping issues in JS/JSON
        parts: list[str] = []
        parts.append("<!DOCTYPE html>")
        parts.append('<html lang="en">')
        parts.append("<head>")
        parts.append('  <meta charset="utf-8">')
        parts.append('  <meta name="viewport" content="width=device-width, initial-scale=1">')
        parts.append(f"  <title>Pharos OAuth — {config.name}</title>")
        parts.append(
            f'  <meta http-equiv="Content-Security-Policy" '
            f'content="{csp_directive}; script-src \'unsafe-inline\'">'
        )
        parts.append("  <style>")
        parts.append("    body { margin: 0; font-family: -apple-system, system-ui, sans-serif; }")
        parts.append("    .pharos-oauth-bar { background: #1a1a2e; color: #e0e0e0; padding: 12px 16px;")
        parts.append("      font-size: 14px; display: flex; align-items: center; gap: 8px; }")
        parts.append("    .pharos-oauth-bar .lock { font-size: 18px; }")
        parts.append("    .pharos-oauth-bar .idp { font-weight: 600; }")
        parts.append("    .pharos-oauth-frame { width: 100%; height: 600px; border: none; }")
        parts.append("    .pharos-oauth-error { padding: 24px; color: #c62828; text-align: center; }")
        parts.append("  </style>")
        parts.append("</head>")
        parts.append("<body>")
        parts.append('  <div class="pharos-oauth-bar">')
        parts.append('    <span class="lock">🔐</span>')
        parts.append(
            f'    <span>Logging in to <span class="idp">{idp_display}</span> '
            f'— verify the URL in the iframe.</span>'
        )
        parts.append("  </div>")
        parts.append('  <iframe')
        parts.append('    class="pharos-oauth-frame"')
        parts.append('    sandbox="allow-scripts allow-forms allow-same-origin allow-popups"')
        parts.append(f'    src="{authorize_url}"')
        parts.append('    id="pharos-oauth-iframe">')
        parts.append("  </iframe>")
        parts.append('  <div class="pharos-oauth-error" id="pharos-error" style="display:none;"></div>')
        parts.append("  <script>")
        parts.append("    // postMessage protocol: JSON-RPC 2.0 over postMessage")
        parts.append("    // The IdP callback page sends: method=oauth/result with code+state+redirect_uri")
        parts.append(f"    var ALLOWED_ORIGINS = {allowed_origins_json};")
        parts.append('    window.addEventListener("message", function(event) {')
        parts.append("      // Verify origin against CSP allowlist")
        parts.append("      if (ALLOWED_ORIGINS.indexOf(event.origin) === -1) return;")
        parts.append("      var data = event.data;")
        parts.append('      if (!data || data.jsonrpc !== \"2.0\") return;')
        parts.append('      if (data.method === "oauth/result") {')
        parts.append("        // Forward to parent agent")
        parts.append('        window.parent.postMessage({')
        parts.append('          "jsonrpc": "2.0",')
        parts.append('          "method": "oauth/result",')
        parts.append('          "params": data.params')
        parts.append('        }, "*");')
        parts.append("      }")
        parts.append('      if (data.method === "oauth/error") {')
        parts.append(
            '        document.getElementById("pharos-error").textContent =\n'
            '          "OAuth error: " + (data.params.error || "unknown");'
        )
        parts.append('        document.getElementById("pharos-error").style.display = "block";')
        parts.append('        document.getElementById("pharos-oauth-iframe").style.display = "none";')
        parts.append("      }")
        parts.append("    });")
        parts.append("  </script>")
        parts.append("</body>")
        parts.append("</html>")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# OAuthFlowHandler — the main agent-side handler
# ---------------------------------------------------------------------------

class OAuthFlowHandler:
    """Agent-side handler for the MCP Apps inline OAuth flow.

    Coordinates the full OAuth flow:
      1. Fetch the server's OAuth config from ``GET /v1/oauth/servers/{name}``.
      2. Generate PKCE ``code_verifier`` + ``code_challenge`` (S256).
      3. Construct the authorize URL with a state nonce.
      4. Render via the chosen renderer (terminal or browser).
      5. Exchange the authorization code for tokens via the MCP server's
         callback endpoint (``GET /v1/oauth/callback/{state}``).
      6. Return an :class:`OAuthResult`.

    Usage (terminal/CLI — default):
        handler = OAuthFlowHandler("https://getpharos.dev")
        result = await handler.connect("io.salesforce/salesforce-mcp")

    Usage (browser/iframe):
        handler = OAuthFlowHandler("https://getpharos.dev", renderer="browser")
        html = handler.renderer.render_html(url, config)  # for web agents
    """

    def __init__(
        self,
        registry_url: str,
        *,
        renderer: OAuthRenderer | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        callback_timeout: float = 300.0,
    ):
        self._registry_url = registry_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._callback_timeout = callback_timeout
        self._renderer: OAuthRenderer = renderer or TerminalOAuthRenderer(
            timeout=callback_timeout
        )

    @property
    def renderer(self) -> OAuthRenderer:
        return self._renderer

    @renderer.setter
    def renderer(self, value: OAuthRenderer) -> None:
        self._renderer = value

    async def get_server_config(self, server_name: str) -> OAuthServerConfig:
        """Fetch the server's OAuth config from ``GET /v1/oauth/servers/{name}``."""
        url = f"{self._registry_url}/v1/oauth/servers/{urllib.parse.quote(server_name)}"
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise OAuthError("config_fetch_failed", str(exc)) from exc

        if resp.status_code == 404:
            raise OAuthError("server_not_configured", f"No OAuth config for {server_name}")
        if resp.status_code != 200:
            raise OAuthError(
                "config_fetch_failed",
                f"HTTP {resp.status_code}: {resp.text[:200]}",
            )

        return OAuthServerConfig(resp.json())

    def build_authorize_url(
        self,
        config: OAuthServerConfig,
        *,
        state: str | None = None,
        code_challenge: str | None = None,
        redirect_uri: str | None = None,
        extra_params: dict[str, str] | None = None,
    ) -> tuple[str, str, str, str]:
        """Construct the IdP authorize URL.

        Returns ``(authorize_url, state, code_verifier, redirect_uri)``.
        """
        if not config.authorize_endpoint:
            raise OAuthError("missing_authorize_endpoint", "Server config has no authorize_endpoint")
        if not config.client_id:
            raise OAuthError("missing_client_id", "Server config has no client_id")

        state = state or generate_state_nonce()
        redirect_uri = redirect_uri or config.redirect_uri_pattern or (
            f"{self._registry_url}/v1/oauth/callback/{state}"
        )

        params: dict[str, str] = {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": " ".join(config.scopes),
        }

        code_verifier: str | None = None
        if config.pkce_required:
            code_verifier = generate_pkce_verifier()
            code_challenge = compute_pkce_challenge(code_verifier)
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"

        if extra_params:
            params.update(extra_params)

        query_string = urllib.parse.urlencode(params, doseq=True)
        separator = "&" if "?" in config.authorize_endpoint else "?"
        authorize_url = f"{config.authorize_endpoint}{separator}{query_string}"

        return authorize_url, state, code_verifier or "", redirect_uri

    async def exchange_code(
        self,
        state: str,
        code: str,
        code_verifier: str | None = None,
    ) -> OAuthResult:
        """Exchange the authorization code for tokens via the MCP server callback.

        The agent calls ``GET /v1/oauth/callback/{state}?code=...`` which
        the registry forwards to the MCP server.  The MCP server performs
        the server-side token exchange (§18.6 step 9) and returns the
        result (or an error).
        """
        url = f"{self._registry_url}/v1/oauth/callback/{urllib.parse.quote(state)}"
        params: dict[str, str] = {"code": code}
        if code_verifier:
            params["code_verifier"] = code_verifier

        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise OAuthError("exchange_failed", str(exc)) from exc

        if resp.status_code != 200:
            body = resp.text[:500]
            raise OAuthError("exchange_failed", f"HTTP {resp.status_code}: {body}")

        data = resp.json()

        # The callback returns either a token bundle or an error
        if data.get("error"):
            raw_scope = data.get("scope", [])
            if isinstance(raw_scope, str):
                scope_list = raw_scope.split()
            else:
                scope_list = raw_scope or []
            return OAuthResult(
                authorized=False,
                scope=scope_list,
                acquired_via="oauth",
                auth_held_by="mcp_server",
                confirmed_at=datetime.now(timezone.utc).isoformat(),
                error=data["error"],
            )

        # Build OAuthResult from the callback response
        scope_raw = data.get("scope", "")
        scopes = scope_raw.split() if isinstance(scope_raw, str) else scope_raw

        return OAuthResult(
            authorized=True,
            access_token=data.get("access_token"),
            token_type=data.get("token_type", "Bearer"),
            expires_in=data.get("expires_in"),
            refresh_token=data.get("refresh_token"),
            scope=scopes,
            acquired_via="oauth",
            auth_held_by="mcp_server",
            confirmed_at=datetime.now(timezone.utc).isoformat(),
            confirmation_jwt=data.get("id_token"),
        )

    async def connect(
        self,
        server_name: str,
        *,
        extra_params: dict[str, str] | None = None,
    ) -> OAuthResult:
        """Initiate the full OAuth flow for a server.

        1. Fetch the server's OAuth config.
        2. Build the authorize URL (PKCE + state).
        3. Render (terminal or browser).
        4. Parse the callback redirect for code + state.
        5. Exchange the code for tokens.
        """
        # 1. Fetch config
        config = await self.get_server_config(server_name)

        # 2. Build authorize URL
        authorize_url, state, code_verifier, redirect_uri = self.build_authorize_url(
            config, extra_params=extra_params
        )

        logger.info("OAuth flow started for %s (state=%s)", server_name, state[:8] + "...")

        # 3. Render
        full_redirect = self._renderer.render(authorize_url, config)

        # 4. Parse callback
        parsed = urllib.parse.urlparse(full_redirect)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        if "error" in params:
            error = params["error"][0]
            error_desc = params.get("error_description", [None])[0]
            return OAuthResult(
                authorized=False,
                scope=[],
                acquired_via="oauth",
                auth_held_by="mcp_server",
                confirmed_at=datetime.now(timezone.utc).isoformat(),
                error=error,
                cancel_reason=error_desc,
            )

        code = params.get("code", [None])[0]
        returned_state = params.get("state", [None])[0]

        if not code:
            raise OAuthError("no_code", "Callback redirect missing authorization code")

        if returned_state and returned_state != state:
            raise OAuthError(
                "state_mismatch",
                f"State mismatch: expected {state[:8]}..., got {returned_state[:8]}...",
            )

        # 5. Exchange code for tokens
        return await self.exchange_code(
            state=state,
            code=code,
            code_verifier=code_verifier if code_verifier else None,
        )

    async def check_status(self, server_name: str) -> dict[str, Any]:
        """Check the OAuth status for a server (lightweight GET config).

        Returns the raw config dict for the caller to inspect.
        """
        config = await self.get_server_config(server_name)
        return {
            "name": config.name,
            "version": config.version,
            "client_id": config.client_id,
            "authorize_endpoint": config.authorize_endpoint,
            "token_endpoint": config.token_endpoint,
            "auth_server_url": config.auth_server_url,
            "scopes": config.scopes,
            "pkce_required": config.pkce_required,
            "secret_handling": config.secret_handling,
            "ui_csp": config.ui_csp,
            "idp_origin": config.idp_origin,
        }
