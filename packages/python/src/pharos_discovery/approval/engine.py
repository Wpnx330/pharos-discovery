from __future__ import annotations

import hashlib
import hmac
import json
import time

from pharos_discovery.models import (
    ApprovalRequest,
    ApprovalResponse,
    ApprovalToken,
)


class ApprovalEngine:
    """Creates and signs approval tokens.

    Uses HMAC-SHA256 for signing (pure stdlib, no external deps).
    The signing key is derived from a client secret + server ID.

    In production, this would use ed25519 for asymmetric signing.
    """

    def __init__(self, client_secret: str):
        if not client_secret:
            raise ValueError("client_secret must not be empty")
        self._secret = client_secret.encode("utf-8")

    def create_token(
        self,
        request: ApprovalRequest,
        response: ApprovalResponse,
        token_ttl_seconds: int = 3600,
    ) -> ApprovalToken:
        """Create a signed approval token from a request/response pair."""
        now = int(time.time())
        token_id = self._generate_token_id(request.server.id, now)

        token = ApprovalToken(
            token_id=token_id,
            server_id=request.server.id,
            approved_scopes=response.approved_scopes,
            approved_capabilities=request.requested_capabilities,
            approved_oauth_scopes=[],
            duration=response.duration,
            approved_at=str(now),
            expires_at=str(now + token_ttl_seconds),
            signature="",  # Will be set after signing
        )

        signature = self._sign(token)
        token.signature = signature
        return token

    def verify_token(self, token: ApprovalToken) -> bool:
        """Verify a token's signature. Returns True if the signature is valid.

        Note: this only checks the cryptographic signature, NOT expiration.
        Callers must also call ``is_expired`` (or use ``is_valid``) to ensure
        the token has not expired. Checking signature and expiry separately
        allows callers to distinguish a forged token from a stale one.
        """
        expected_sig = self._sign(token)
        return hmac.compare_digest(token.signature, expected_sig)

    def is_valid(self, token: ApprovalToken) -> bool:
        """Return True only if the token's signature is valid AND it is not expired."""
        return self.verify_token(token) and not self.is_expired(token)

    def is_expired(self, token: ApprovalToken) -> bool:
        """Check if a token has expired. Returns True if expired or unparseable."""
        try:
            expires = int(token.expires_at)
            return time.time() >= expires
        except (ValueError, TypeError):
            return True

    def _sign(self, token: ApprovalToken) -> str:
        """Sign token fields using HMAC-SHA256."""
        # Sign all fields except the signature itself
        payload = json.dumps(
            {
                "token_id": token.token_id,
                "server_id": token.server_id,
                "approved_scopes": token.approved_scopes,
                "approved_capabilities": token.approved_capabilities,
                "approved_oauth_scopes": token.approved_oauth_scopes,
                "duration": token.duration,
                "approved_at": token.approved_at,
                "expires_at": token.expires_at,
            },
            sort_keys=True,
        )
        return hmac.new(
            self._secret,
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _generate_token_id(self, server_id: str, timestamp: int) -> str:
        """Generate a unique token ID."""
        raw = f"{server_id}:{timestamp}:{time.monotonic_ns()}"
        return "tok_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
