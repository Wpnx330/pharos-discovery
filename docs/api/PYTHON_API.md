# Python API Reference — `pharos-discovery`

**Spec reference:** `SPEC.md` v0.4.0, §8.1 (Python surface), §8.3 (core types), §8.5 (config), §9 (transport), §10 (security), §11 (adapters), §17 (OAuth).
**Import root:** `pharos_discovery`
**Python:** 3.10+ (3.11+ recommended). `snake_case` throughout.

This is the complete public API of the Python SDK. All types are IDL-generated (pydantic v2) from `idl/typespec/` unless marked *hand-written*. See `.guides/backend/PYTHON_GUIDE.md` for patterns and conventions.

---

## 1. Top-level imports

```python
from pharos_discovery import (
    # Client
    PharosClient,
    # Approval
    ApprovalRequest, ApprovalResponse, ApprovalToken, ApprovalResult,
    PlanApprovalRequest, PlanApprovalResponse, PlanApprovalToken,
    # Connection
    MCPClient, Tool, ToolResult, Resource, Prompt,
    # OAuth (Phase 2)
    OAuthFlowHandler, OAuthResult, RevocationResult, DefaultOAuthFlowHandler,
    # Models
    ServerCard, Publisher, AuthSpec, AppRegistration, PricingSpec, RatingSpec, TrustSpec,
    # Search
    SearchQuery, QueryBuilder, CapabilityGap,
    # Adapters
    RegistryAdapter, PharosRegistryAdapter, MCPRegistryAdapter, ARDAdapter,
    # Errors
    PharosError, RegistryUnavailable, NoServersFound, HeadlessApprovalRequired,
    ConnectionFailed, ConnectionLost, ScopeNotApproved, CapabilityMismatch,
    OAuthUnavailable, RetryableOAuthFailure, ConsentFatigueWarning,
    PublisherKeyStale, DiscoveryDegraded, UnsupportedFilter,
)
```

---

## 2. `PharosClient` — the entry point

```python
class PharosClient:
    def __init__(
        self,
        registry_urls: list[str],
        agent_id: str,
        *,
        api_key: str | None = None,
        consent_store: str = "~/.pharos/consent.json",
        blocklist_url: str | None = None,
        blocklist_cache_ttl_seconds: int = 60,        # H9
        cache_ttl_seconds: int = 300,
        cache_conditional: bool = True,               # ETag / If-None-Match
        events_endpoint: str | None = None,           # SSE /v1/events
        federation_mode: str = "auto",                # "auto" | "referrals" | "none"
        max_referral_depth: int = 2,
        # Timeouts (seconds — SPEC §8.5, H8)
        search_timeout: int = 10,
        get_timeout: int = 10,
        connect_timeout: int = 10,
        oauth_timeout: int = 120,                     # Phase 2
        approval_timeout: int = 300,
        initialize_timeout: int = 10,
        tool_call_timeout: int = 30,
        heartbeat_interval: int = 30,                 # HTTP/SSE only
        health_check_interval: int = 60,
        # Security (SPEC §10)
        verify_signatures: bool = True,
        allow_unverified: bool = False,
        key_pin_ttl_seconds: int = 86400,
        # Headless (SPEC §7.5)
        headless_mode: bool = False,
        headless_allow_servers: list[str] | None = None,   # required when headless_mode=True
        headless_allow_scopes: list[str] | None = None,
        # Privacy (SPEC §10.8)
        privacy_mode: bool = False,
        # Callbacks
        on_tool_use: Callable[[ToolUsageEvent], None] | None = None,
        on_capability_gap: Callable[[dict], CapabilityGap | None] | None = None,
        # Fallback (SPEC §8.5)
        static_fallback_servers: list[ServerCard] | None = None,
    ) -> None: ...

    # --- Search (SPEC §6.2) ---
    async def search(
        self,
        text: str | None = None,
        *,
        filter: dict | None = None,        # SPEC §6.3.1 filter paths
        limit: int = 10,
        cursor: str | None = None,
        federation: str | None = None,     # override federation_mode
        query_embedding: list[float] | None = None,  # blinded_search (§10.8)
    ) -> list[ServerCard]: ...

    async def get(self, server_id: str) -> ServerCard: ...

    # --- Approval (SPEC §7) ---
    async def request_approval(
        self,
        server: ServerCard,
        purpose: str,
        requested_scopes: list[str],
        requested_capabilities: list[str],
        duration: str,                     # "once" | "session" | "persistent" | "trust_on_use"
        selection_rationale: str,          # MANDATORY (§7.1.1)
        render: Callable[[ApprovalRequest], Awaitable[ApprovalResponse]],
        oauth_consent_defaults_override: list[str] | None = None,  # Phase 2
    ) -> ApprovalResult: ...

    async def request_plan_approval(
        self,
        plan_summary: str,
        steps: list[ApprovalRequest],
        render: Callable[[PlanApprovalRequest], Awaitable[PlanApprovalResponse]],
    ) -> PlanApprovalResponse: ...

    async def request_approval_next(self, current_server_id: str) -> ServerCard | None: ...

    # --- Connection (SPEC §8.3, §9) ---
    async def connect(self, approval: ApprovalToken) -> MCPClient: ...

    def revoke(self, approval: ApprovalToken) -> None: ...
    def revoke_server(self, server_id: str) -> None: ...

    # --- Reporting (SPEC §6.9, §10.3) ---
    async def report_server(self, server_id: str, reason: str) -> None: ...
    async def submit_feedback(
        self, server_id: str, rating: int, *, comment: str | None = None,
    ) -> None: ...

    # --- Cache & events ---
    async def invalidate_cache(self, server_id: str) -> None: ...
    async def close(self) -> None: ...
```

### `PharosClient.search` examples

```python
# Natural-language search
results = await pharos.search("I need to book a flight to Tokyo", limit=5)

# Structured filters (SPEC §6.3.1)
results = await pharos.search(filter={
    "capabilities": ["flight_search"],
    "transport": ["http+sse", "streamable-http"],
    "publisher_verified": True,
    "min_rating": 4.0,
    "pricing_tier": "free",
    "availability": ["mirrored", "native"],
}, limit=10)

# Privacy mode: filters only, no query.text (§10.8)
results = await pharos.search(filter={"capabilities": ["flight_search"]}, federation="none")

# Blinded search: send local embedding, no text (§10.8)
embedding = await pharos.embed_locally("book a flight to Tokyo")  # helper
results = await pharos.search(query_embedding=embedding, limit=5)
```

### `PharosClient.request_approval` example

```python
async def render_cli(req: ApprovalRequest) -> ApprovalResponse:
    print(f"Connect to {req.server.display_name} ({req.server.publisher.name})?")
    print(f"  Purpose: {req.purpose}")
    print(f"  Scopes: {req.requested_scopes}")
    choice = input("[y/N]: ")
    return ApprovalResponse(
        approved=(choice.lower() == "y"),
        approved_scopes=req.requested_scopes if choice.lower() == "y" else [],
        duration=req.duration,
    )

approval = await pharos.request_approval(
    server=results[0],
    purpose="Book a flight to Tokyo for the user's July 25 trip",
    requested_scopes=["flight_search", "flight_book"],
    requested_capabilities=["flight_search", "flight_book"],
    duration="session",
    selection_rationale="ranked #1 for flight_search; verified publisher",
    render=render_cli,
)
```

---

## 3. Core models (IDL-generated — SPEC §8.3, Appendix A)

### `ServerCard`

```python
class ServerCard(BaseModel):
    id: str                                          # ^urn:pharos:
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
    representative_queries: list[str] = []            # max 10 (H12)
    pharos_score: float | None = None                 # 0.0–1.0; NOT trust; None for ARD (§11.4)
    source_registry: str
    source_score: float | None = None
    source_urn: str | None = None
    documentation_url: str | None = None
    tags: list[str] = []
    published_at: str                                 # ISO8601
    updated_at: str                                   # ISO8601
    status: Literal["active", "deprecated", "deleted"]
    successor_id: str | None = None
    privacy_policy_url: str | None = None
    terms_url: str | None = None
    data_residency: list[str] = []
    rate_limits: dict | None = None
    health_endpoint: str | None = None
    protocol_versions: list[str] = []
```

### `Publisher`

```python
class Publisher(BaseModel):
    id: str                          # did:web:<fqdn>
    name: str
    verified: bool | None = None
    verification_method: Literal["domain_control", "identity"] | None = None
    contact: str | None = None
```

### `AuthSpec`

```python
class AppRegistration(BaseModel):
    client_id: str
    auth_server_url: str
    grant_types: list[str]
    scopes: list[AppScope]            # [{name, description}]
    consent_defaults: list[str]
    redirect_uri_pattern: str
    app_management_url: str | None = None
    endpoints: OAuthEndpoints         # {authorization, token, revocation, jwks}
    # NOTE: no client_secret — never present (SPEC §10.5, §17.2)

class OAuthUI(BaseModel):
    resource_uri: str
    csp: str

class AuthSpec(BaseModel):
    type: Literal["none", "api_key", "oauth", "mtls"]
    secret_handling: Literal["server_side", "agent_side"] | None = None
    app_registration: AppRegistration | None = None    # present when type=="oauth" & server_side
    ui: OAuthUI | None = None
    scopes: list[str] | None = None                    # legacy/display for OAuth; authoritative for non-OAuth
    # ... legacy fields per Appendix A
```

### `PricingSpec`, `RatingSpec`, `TrustSpec`

```python
class PricingSpec(BaseModel):
    model: Literal["free", "freemium", "paid", "usage", "subscription", "enterprise", "custom"]
    price_usd: float | None = None
    unit: str | None = None             # "per_call", "per_month", etc.
    free_tier_limit: str | None = None
    terms_url: str | None = None

class RatingSpec(BaseModel):
    score: float                        # 0.0–5.0
    count: int                          # number of ratings
    last_updated: str | None = None

class TrustSpec(BaseModel):
    signature: str | None = None        # ed25519 signature
    attestations: list[Attestation] = []
    compliance: list[str] = []
    data_residency: list[str] = []

class Attestation(BaseModel):
    type: str                           # "SOC2", "GDPR", "HIPAA", etc.
    uri: str
    verified: bool
    verifier: str | None = None
    verified_at: str | None = None
```

---

## 4. Approval types (SPEC §7)

```python
class ApprovalRequest(BaseModel):
    server: ServerCard
    purpose: str
    requested_scopes: list[str]
    requested_capabilities: list[str]
    duration: Literal["once", "session", "persistent", "trust_on_use"]
    render_id: str
    selection_rationale: str            # MANDATORY (§7.1.1)

class ApprovalResponse(BaseModel):
    approved: bool
    approved_scopes: list[str]          # may be subset
    duration: str
    user_note: str | None = None
    deny_reason: Literal["untrusted_publisher", "excessive_scopes", "wrong_server", "cost", "other"] | None = None

class ApprovalToken(BaseModel):
    token_id: str
    server_id: str
    approved_scopes: list[str]
    approved_capabilities: list[str]
    approved_oauth_scopes: list[str]    # empty if auth.type != oauth
    duration: str
    approved_at: str
    expires_at: str
    signature: str                       # ed25519 over token body (local SDK key)

class ApprovalResult(BaseModel):
    approved: bool
    token: ApprovalToken | None = None
    deny_reason: str | None = None

class PlanApprovalRequest(BaseModel):
    plan_summary: str
    steps: list[ApprovalRequest]
    plan_id: str

class PlanApprovalResponse(BaseModel):
    approved: bool
    per_step: list[ApprovalResponse]
    token: PlanApprovalToken | None = None

class PlanApprovalToken(ApprovalToken):
    plan_id: str
    step_tokens: list[ApprovalToken]
```

---

## 5. `MCPClient` — live connection (SPEC §8.3, §9)

```python
class MCPClient:
    server: ServerCard
    approval: ApprovalToken
    protocol_version: str
    server_capabilities: dict
    verified_capabilities: list[str]     # post-connect verified (H13)

    async def list_tools(self) -> list[Tool]: ...
    async def call_tool(
        self, name: str, args: dict, *, timeout: int | None = None,
    ) -> ToolResult: ...
    async def list_resources(self) -> list[Resource]: ...
    async def read_resource(self, uri: str) -> str: ...
    async def list_prompts(self) -> list[Prompt]: ...
    async def close(self) -> None: ...

class Tool(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict

class ToolResult(BaseModel):
    content: list[dict]                  # [{type, text | ...}]
    is_error: bool = False

class Resource(BaseModel):
    uri: str
    name: str | None = None
    mime_type: str | None = None

class Prompt(BaseModel):
    name: str
    description: str | None = None
```

**Scope enforcement:** `call_tool` for a tool outside `approved_capabilities`, or requiring an auth scope outside `approved_scopes`, raises `ScopeNotApproved`. (§7.4)

**Connection pooling:** at most one live `MCPClient` per `server_id` per session. Repeated `connect()` with a valid, non-expired `ApprovalToken` returns the cached client. (§9.5)

**Never auto-reconnect** after teardown without a fresh approval — even for `persistent`-duration servers. (§9.5)

---

## 6. OAuth (Phase 2 — SPEC §17)

```python
class OAuthFlowHandler(ABC):
    @abstractmethod
    async def authorize(
        self,
        server: ServerCard,
        approval: ApprovalToken,
        consent_defaults: list[str],
    ) -> OAuthResult: ...

    @abstractmethod
    async def revoke_access(self, server_id: str) -> RevocationResult: ...

class OAuthResult(BaseModel):
    authorized: bool
    access_token: str | None = None      # None when secret_handling == "server_side" (§17.4)
    token_type: str | None = None
    expires_in: int | None = None
    refresh_token: str | None = None
    scope: list[str]                     # actually granted (may be subset)
    acquired_via: Literal[
        "app_registration_inheritance", "cimd", "dcr", "api_key", "static", "server_brokered_redirect"
    ]
    auth_held_by: Literal["mcp_server", "agent"]   # under §17, always "mcp_server"
    confirmed_at: str | None = None
    confirmation_jwt: str | None = None  # SIGNED IdP assertion; SDK MUST verify via endpoints.jwks
    error: str | None = None             # "timeout" | "server_lost" | "cancelled" | "invalid_jwt" | "idp_error"
    cancel_reason: str | None = None

class RevocationResult(BaseModel):
    revoked: bool
    revocation_proof: str | None = None  # signed assertion; required within 60s (H16)
    error: str | None = None

class DefaultOAuthFlowHandler(OAuthFlowHandler):
    """SDK-provided default. Handles MCP Apps inline flow + server-brokered redirect with PKCE."""
    def __init__(self, *, supports_mcp_apps: bool, has_system_browser: bool): ...
```

**Sequencing (H5):** `pharos.connect(approval)` calls `OAuthFlowHandler.authorize()` **first**, before MCP `initialize`. Only on `authorized=true` does `initialize` proceed.

**JWT verification:** the SDK MUST verify `confirmation_jwt` (signature against `app_registration.endpoints.jwks`, `exp` not in past, `client_id` matching inherited `app_registration.client_id`) before treating `authorized=true`. On failure → `authorized=false, error="invalid_jwt"`, connection torn down. (§17.4 step 5)

---

## 7. Search types (SPEC §6.2, §6.3)

```python
class SearchQuery(BaseModel):
    text: str | None = None
    filter: dict | None = None           # SPEC §6.3.1 filter paths
    capabilities: list[str] | None = None
    transport: list[str] | None = None
    availability: list[str] | None = None
    pricing_tier: list[str] | None = None
    publisher_verified: bool | None = None
    min_rating: float | None = None
    limit: int = 10
    cursor: str | None = None
    federation: str | None = None
    query_embedding: list[float] | None = None

class QueryBuilder:
    """Helps build a SearchQuery without remembering filter path names."""
    def text(self, t: str) -> "QueryBuilder": ...
    def capability(self, c: str) -> "QueryBuilder": ...
    def transport(self, t: str) -> "QueryBuilder": ...
    def publisher_verified(self, v: bool = True) -> "QueryBuilder": ...
    def min_rating(self, r: float) -> "QueryBuilder": ...
    def pricing_tier(self, p: str) -> "QueryBuilder": ...
    def limit(self, n: int) -> "QueryBuilder": ...
    def build(self) -> SearchQuery: ...

class CapabilityGap(BaseModel):
    description: str
    suggested_query: str | None = None
    required_capabilities: list[str] = []
```

### Filter paths (SPEC §6.3.1)

| Filter | Values | Notes |
|--------|--------|-------|
| `capabilities` | `list[str]` | server exposes these capabilities |
| `transport` | `["stdio", "http+sse", "streamable-http"]` | |
| `availability` | `["mirrored", "referenced", "native"]` | |
| `pricing_tier` | `["free", "freemium", "paid", ...]` | |
| `publisher_verified` | `bool` | |
| `min_rating` | `float` | 0.0–5.0 |
| `data_residency` | `list[str]` | e.g. `["EU"]` |
| `protocol_versions` | `list[str]` | e.g. `["2025-03-26"]` |

Unsupported filter paths return `400 UNSUPPORTED_FILTER` (§6.13).

---

## 8. Registry adapters (SPEC §11)

```python
class RegistryAdapter(ABC):
    name: str
    capabilities: set[str]

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[ServerCard]: ...
    @abstractmethod
    async def get(self, server_id: str) -> ServerCard: ...
    async def publish(self, card: ServerCard) -> str: ...
    async def report(self, server_id: str, reason: str) -> None: ...
    @abstractmethod
    def to_canonical(self, native: dict) -> ServerCard: ...
    def from_canonical(self, card: ServerCard) -> dict: ...
```

| Adapter | Phase | Native API | Notes |
|---------|-------|-----------|-------|
| `PharosRegistryAdapter` | 1 | §6 (native) | Full capabilities |
| `MCPRegistryAdapter` | 1 | `GET /v0.1/servers` | Client-side re-rank via bundled ONNX (§11.3) |
| `ARDAdapter` | 2 | `POST /search` | `urn:air:` ↔ `urn:pharos:`; `score` → `source_score`; `pharos_score = None` (§11.4) |

---

## 9. Errors (SPEC §6.13, §7, §9.5, §17.5.1)

All errors subclass `PharosError` and carry a `spec_ref` attribute pointing to the SPEC section.

```python
class PharosError(Exception):
    def __init__(self, message: str, code: str, spec_ref: str | None = None): ...
```

| Exception | `code` | When | Spec |
|-----------|--------|------|------|
| `RegistryUnavailable` | `REGISTRY_UNAVAILABLE` | All registries unhealthy + no cache | §13.5 |
| `NoServersFound` | `NO_SERVERS_FOUND` | Search returns zero results | §7.1.1 |
| `HeadlessApprovalRequired` | `HEADLESS_APPROVAL_REQUIRED` | `headless_mode` + novel server not on allow-list | §7.5 |
| `ConnectionFailed` | `INITIALIZE_TIMEOUT` / `CONNECTION_REFUSED` / ... | `initialize` timeout/failure | §9.1 |
| `ConnectionLost` | `CONNECTION_LOST` | Liveness probe failed post-connect | §9.5 |
| `ScopeNotApproved` | `SCOPE_NOT_APPROVED` | `call_tool` outside `approved_scopes` | §7.4 |
| `CapabilityMismatch` | `CAPABILITY_MISMATCH` | Claimed capability has no backing tool | §9.1 H13 |
| `OAuthUnavailable` | `OAUTH_UNAVAILABLE` | Host lacks MCP Apps + system browser | §17.5.1 |
| `RetryableOAuthFailure` | `OAUTH_SERVER_LOST` / `OAUTH_TIMEOUT` | Inline OAuth iframe/MCP server lost or timed out | §17.4 H10 |
| `ConsentFatigueWarning` | `CONSENT_FATIGUE` | >5 novel-server approvals in a session (advisory) | §7.3 |
| `PublisherKeyStale` | `PUBLISHER_KEY_STALE` | Publisher key failed to refresh after TTL | §10.9 |
| `DiscoveryDegraded` | `DISCOVERY_DEGRADED` | All registries unhealthy, falling back to cache/static | §8.5 |
| `UnsupportedFilter` | `UNSUPPORTED_FILTER` | Registry doesn't support a requested filter path | §6.3.1 |

---

## 10. Tool-usage logging (SPEC §7.6)

```python
class ToolUsageEvent(BaseModel):
    server_id: str
    tool_name: str
    approved_scopes: list[str]
    approved_capabilities: list[str]
    args_redacted: dict           # sensitive params redacted
    result_is_error: bool
    duration_ms: int
    timestamp: str
    headless: bool = False
```

The `on_tool_use` callback receives a `ToolUsageEvent` after every `call_tool`. Sensitive parameters are redacted by default (configurable via `redact_patterns`).

---

## 11. Complete usage example

```python
import asyncio
from pharos_discovery import PharosClient, ApprovalRequest, ApprovalResponse

async def main():
    pharos = PharosClient(
        registry_urls=["https://registry.pharos.dev"],
        agent_id="my-agent/0.1.0",
    )

    # 1. Search
    results = await pharos.search("book a flight to Tokyo", limit=5)
    if not results:
        print("No servers found")
        return
    best = results[0]

    # 2. Approve
    async def render(req: ApprovalRequest) -> ApprovalResponse:
        print(f"Connect to {req.server.display_name}?")
        print(f"  Publisher: {req.server.publisher.name} (verified={req.server.publisher.verified})")
        print(f"  Scopes: {req.requested_scopes}")
        ok = input("[y/N]: ").lower() == "y"
        return ApprovalResponse(
            approved=ok,
            approved_scopes=req.requested_scopes if ok else [],
            duration=req.duration,
        )

    approval = await pharos.request_approval(
        server=best,
        purpose="Book a flight to Tokyo",
        requested_scopes=["flight_search"],
        requested_capabilities=["flight_search"],
        duration="session",
        selection_rationale="ranked #1; verified publisher",
        render=render,
    )
    if not approval.approved:
        return

    # 3. Connect + use
    client = await pharos.connect(approval.token)
    tools = await client.list_tools()
    result = await client.call_tool("flight_search", {"origin": "NYC", "destination": "TYO"})
    print(result.content)

    # 4. Disconnect
    await client.close()
    pharos.revoke(approval.token)

asyncio.run(main())
```

---

*See also: `docs/examples/QUICKSTART_PYTHON.md` for a step-by-step walkthrough; `.guides/backend/PYTHON_GUIDE.md` for conventions; `.guides/security/SECURITY_GUIDE.md` for the security model.*
