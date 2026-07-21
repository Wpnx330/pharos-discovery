from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AppRegistration(BaseModel):
    client_id: str | None = None
    consent_defaults: list[str] = Field(default_factory=list)
    token_storage: Literal["server_side", "agent_side"] = "server_side"


class OAuthUI(BaseModel):
    type: Literal["inline", "redirect", "embedded"] = "inline"
    url: str | None = None


class AuthSpec(BaseModel):
    type: Literal["none", "api_key", "oauth", "mtls"]
    secret_handling: Literal["server_side", "agent_side"] | None = None
    app_registration: AppRegistration | None = None
    ui: OAuthUI | None = None
    scopes: list[str] | None = None
    auth_url: str | None = None
    dcr_support: bool | None = None
    cimd_support: bool | None = None


class Publisher(BaseModel):
    id: str
    name: str
    verified: bool | None = None
    verification_method: Literal["domain_control", "identity"] | None = None
    contact: str | None = None


class PricingSpec(BaseModel):
    model: Literal["free", "freemium", "paid", "usage"]
    price_usd: float | None = None
    unit: str | None = None
    free_tier_limit: str | None = None


class RatingSpec(BaseModel):
    score: float
    count: int
    distribution: dict[str, int] | None = None


class TrustSpec(BaseModel):
    attestations: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)


class ServerCard(BaseModel):
    id: str
    display_name: str
    description: str
    publisher: Publisher
    version: str
    transport: list[Literal["stdio", "http+sse", "streamable-http"]]
    endpoint: str | None = None
    stdio_command: str | None = None
    capabilities: list[str]
    tools_count: int
    tools_count_verified: bool = False
    auth: AuthSpec
    availability: Literal["mirrored", "referenced", "native"]
    pricing: PricingSpec | None = None
    pricing_verified: bool = False
    rating: RatingSpec | None = None
    trust: TrustSpec | None = None
    representative_queries: list[str] = Field(default_factory=list)
    pharos_score: float | None = None
    source_registry: str
    source_score: float | None = None
    source_urn: str | None = None
    documentation_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    published_at: str
    updated_at: str
    status: Literal["active", "deprecated", "deleted"]
    successor_id: str | None = None
    privacy_policy_url: str | None = None
    terms_url: str | None = None
    data_residency: list[str] = Field(default_factory=list)
    rate_limits: dict | None = None
    health_endpoint: str | None = None
    protocol_versions: list[str] = Field(default_factory=list)
