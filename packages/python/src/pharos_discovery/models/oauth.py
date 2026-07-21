from __future__ import annotations

from pydantic import BaseModel, Field


class OAuthResult(BaseModel):
    authorized: bool
    access_token: str | None = None
    token_type: str | None = None
    expires_in: int | None = None
    refresh_token: str | None = None
    scope: list[str]
    acquired_via: str
    auth_held_by: str
    confirmed_at: str
    confirmation_jwt: str | None = None
    error: str | None = None
    cancel_reason: str | None = None


class OAuthStatus(BaseModel):
    valid: bool
    expires_at: str | None = None
    scopes: list[str] = Field(default_factory=list)


class RevocationResult(BaseModel):
    revoked: bool
    revocation_confirmed: bool
    revocation_proof: str | None = None
    fallback_revoke_url: str | None = None
