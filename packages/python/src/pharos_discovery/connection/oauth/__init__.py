"""MCP Apps inline OAuth iframe renderer — agent-side component (§18.5-18.6)."""

from pharos_discovery.connection.oauth.handler import (
    BrowserOAuthRenderer,
    OAuthFlowHandler,
    OAuthRenderer,
    OAuthServerConfig,
    TerminalOAuthRenderer,
    compute_pkce_challenge,
    generate_pkce_verifier,
    generate_state_nonce,
)

__all__ = [
    "BrowserOAuthRenderer",
    "OAuthFlowHandler",
    "OAuthRenderer",
    "OAuthServerConfig",
    "TerminalOAuthRenderer",
    "compute_pkce_challenge",
    "generate_pkce_verifier",
    "generate_state_nonce",
]
