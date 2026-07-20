# Python SDK Guide — `pharos-discovery`

**Audience:** AI agents and contributors working on the Python SDK.
**Source of truth:** `SPEC.md` v0.4.0, §8.1 (Python surface), §8.3 (core types), §8.5 (config), §8.6 (IDL), §9 (transport), §10 (security), §11 (adapters).
**Companion:** `.guides/architecture/OVERVIEW.md` for cross-cutting design; `docs/api/PYTHON_API.md` for the full public API reference.

---

## 1. Package identity & layout

- **Name:** `pharos-discovery` (PyPI)
- **Import root:** `pharos_discovery`
- **Python:** 3.10+ (3.11+ recommended). Uses PEP 604 unions (`str | None`), PEP 612 `ParamSpec`-style typing where helpful.
- **Layout:** `src/` layout (avoids accidental imports from cwd).

```
packages/python/
├── pyproject.toml
├── src/pharos_discovery/
│   ├── __init__.py            # re-exports public API
│   ├── client.py              # PharosClient
│   ├── search/                # Search Client layer
│   │   ├── query.py           # SearchQuery, QueryBuilder
│   │   └── ranking.py
│   ├── approval/              # Approval Engine layer
│   │   ├── engine.py          # request_approval, PlanApproval
│   │   ├── token.py           # ApprovalToken (signed, ed25519)
│   │   └── renderers/
│   │       └── cli.py         # CLI approval renderer (Phase 1 default)
│   ├── connection/            # Connection Manager layer
│   │   ├── manager.py         # connect(), MCPClient
│   │   ├── transports/
│   │   │   ├── stdio.py       # Phase 2
│   │   │   ├── http_sse.py    # Phase 1
│   │   │   └── streamable.py  # Phase 1
│   │   └── oauth/             # Phase 2
│   │       ├── handler.py     # OAuthFlowHandler, DefaultOAuthFlowHandler
│   │       └── result.py      # OAuthResult, OAuthStatus, RevocationResult
│   ├── adapters/              # Registry Adapter layer
│   │   ├── base.py            # RegistryAdapter (abstract)
│   │   ├── pharos.py          # PharosRegistryAdapter (native)
│   │   ├── mcp_official.py    # MCPRegistryAdapter + client-side re-rank
│   │   └── ard.py             # Phase 2
│   ├── models/                # IDL-generated pydantic models
│   │   ├── server_card.py
│   │   ├── approval.py
│   │   └── oauth.py
│   ├── security/
│   │   ├── signatures.py      # ed25519 verify
│   │   ├── blocklist.py
│   │   ├── key_pin.py         # .well-known/pharos-pubkey.json fetch + TTL
│   │   └── consent_store.py   # append-only, signed
│   ├── cache.py               # ServerCard cache, ETag/If-None-Match
│   ├── events.py              # SSE /v1/events subscriber
│   ├── errors.py              # typed exceptions
│   └── _version.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── conformance/           # shared golden fixtures (SPEC §8.6)
└── README.md
```

> The `models/` directory is **IDL-generated** (TypeSpec → pydantic v2). Do not hand-edit. Regenerate via the IDL pipeline (see `.guides/deployment/DEPLOYMENT_GUIDE.md`).

---

## 2. Dependencies (minimal surface — SPEC §4.8)

Required:
- `anyio` — async primitives (async-first; works with asyncio + trio backends)
- `httpx` — async HTTP client (registry calls, SSE)
- `pydantic` >= 2.0 — IDL-generated models, validation
- `cryptography` — ed25519 signature verification, key parsing
- `anyio` >= 4

Optional (feature-gated):
- `onnxruntime` + `pharos-discovery-embeddings` — bundled `all-MiniLM-L6-v2` (~22 MB ONNX, 384-dim, MIT) for MCP-Registry-adapter client-side semantic re-ranking (SPEC §11.3). Absent → fall back to substring-only (labeled "non-semantic" in UX). **Never route discovery ranking through a third-party LLM** (privacy + determinism regression; SPEC §11.3).

**No hard dependency on any LLM client SDK.** The Python SDK works with the Anthropic SDK, OpenAI SDK, raw HTTP, or a custom agent loop (SPEC §8.4).

---

## 3. Public surface (canonical usage — SPEC §8.1)

```python
from pharos_discovery import PharosClient, ApprovalRequest, ApprovalResponse

pharos = PharosClient(
    registry_urls=["https://registry.pharos.dev"],
    agent_id="my-agent/0.1.0",
    consent_store="~/.pharos/consent.json",
)

# 1. Search
results = await pharos.search(
    text="I need to book a flight to Tokyo and file the expense report",
    filter={
        "transport": ["http+sse"],
        "publisher_verified": True,
        "min_rating": 4.0,
    },
    limit=5,
)

# 2. Evaluate (host-agent logic — outside the SDK)
best = results[0]

# 3. Request approval (callback-based UX)
approval = await pharos.request_approval(
    server=best,
    purpose="Book a flight to Tokyo for the user's July 25 trip",
    requested_scopes=["flight_search", "flight_book"],
    requested_capabilities=["flight_search", "flight_book"],
    duration="session",
    selection_rationale="ranked #1 for flight_search; verified publisher; supports bookings:write",
    render=present_to_user,  # async def present_to_user(req: ApprovalRequest) -> ApprovalResponse
)
if not approval.approved:
    return

# 4. Connect (requires the ApprovalToken; OAuth authorize() runs first if auth.type == oauth)
client = await pharos.connect(approval)

# 5. Use
tools = await client.list_tools()
result = await client.call_tool("flight_search", {"origin": "NYC", "destination": "TYO", "date": "2026-07-25"})

# 6. Disconnect + optionally revoke
await client.close()
pharos.revoke(approval)
```

**Naming:** `snake_case` throughout (SPEC §8.1). `pharos.search(text=..., filter=...)`, `request_approval(...)`, `call_tool(...)`.

---

## 4. Core types (IDL-generated pydantic v2 — SPEC §8.3)

All of the following are generated from the TypeSpec IDL into `pharos_discovery/models/`. They are `pydantic.BaseModel` subclasses with strict config. Field types mirror SPEC §8.3 / Appendix A exactly.

```python
# pharos_discovery/models/server_card.py
class Publisher(BaseModel):
    id: str                          # did:web:<fqdn>
    name: str
    verified: bool | None = None
    verification_method: Literal["domain_control", "identity"] | None = None
    contact: str | None = None

class AuthSpec(BaseModel):
    type: Literal["none", "api_key", "oauth", "mtls"]
    secret_handling: Literal["server_side", "agent_side"] | None = None
    app_registration: AppRegistration | None = None   # present when type==oauth & server_side
    ui: OAuthUI | None = None
    scopes: list[str] | None = None                    # legacy/display for OAuth; authoritative for non-OAuth
    # ... legacy fields (auth_url, dcr_support, cimd_support, etc.) per Appendix A

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
    pharos_score: float | None = None                 # 0.0–1.0; NOT a trust rating; None for ARD
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

```python
# pharos_discovery/models/approval.py
class ApprovalRequest(BaseModel):
    server: ServerCard
    purpose: str
    requested_scopes: list[str]
    requested_capabilities: list[str]
    duration: Literal["once", "session", "persistent", "trust_on_use"]
    render_id: str
    selection_rationale: str        # MANDATORY (§7.1.1); empty → rejected for non-headless

class ApprovalResponse(BaseModel):
    approved: bool
    approved_scopes: list[str]      # may be subset
    duration: str
    user_note: str | None = None
    deny_reason: Literal["untrusted_publisher","excessive_scopes","wrong_server","cost","other"] | None = None

class ApprovalToken(BaseModel):
    token_id: str
    server_id: str
    approved_scopes: list[str]
    approved_capabilities: list[str]
    approved_oauth_scopes: list[str]   # empty if auth.type != oauth
    duration: str
    approved_at: str
    expires_at: str
    signature: str                     # ed25519 over token body
```

See `docs/api/PYTHON_API.md` for the full `MCPClient`, `OAuthFlowHandler`, `OAuthResult`, `RevocationResult`, `PlanApprovalRequest`/`Response` signatures.

---

## 5. `PharosClient` configuration (SPEC §8.5)

```python
PharosClient(
    registry_urls=["https://registry.pharos.dev", "https://registry-eu.pharos.dev"],  # H7 failover list
    agent_id="my-agent/0.1.0",
    api_key=None,
    consent_store="~/.pharos/consent.json",
    blocklist_url="https://registry.pharos.dev/v1/blocklist",
    blocklist_cache_ttl_seconds=60,   # H9: was implicit 300s
    cache_ttl_seconds=300,
    cache_conditional=True,           # ETag / If-None-Match
    events_endpoint="https://registry.pharos.dev/v1/events",  # SSE push
    federation_mode="auto",           # auto | referrals | none
    max_referral_depth=2,
    search_timeout=10,
    get_timeout=10,
    connect_timeout=10,
    oauth_timeout=120,                # inline OAuth UI (Phase 2)
    approval_timeout=300,             # host render callback
    initialize_timeout=10,            # H8
    tool_call_timeout=30,             # H8
    heartbeat_interval=30,            # HTTP/SSE only
    health_check_interval=60,
    verify_signatures=True,
    allow_unverified=False,
    headless_mode=False,
    headless_allow_servers=[],        # required when headless_mode=True
    headless_allow_scopes=[],
    privacy_mode=False,               # §10.8: filters only, no query.text
    on_tool_use=None,                 # callback: (ToolUsageEvent) -> None
    on_capability_gap=None,           # callback: (dict) -> CapabilityGap | None
    static_fallback_servers=[],        # /etc/hosts-style fallback (preserves approval gate)
)
```

**Registry failover (H7):** on 503/504/timeout from `registry_urls[0]`, mark unhealthy 60s, try `[1]`, etc. All unhealthy + no cache → `DiscoveryDegraded`; host MAY use `static_fallback_servers` (approval gate still enforced).

---

## 6. Async model — anyio first

- Use `anyio` for ALL async primitives: `anyio.sleep`, `anyio.Event`, `anyio.create_task_group()`, `anyio.move_on_after()`, `anyio.Lock`. Do **not** reach for `asyncio` directly — it breaks trio compatibility and couples the SDK to one backend. (SPEC §8.4)
- Every public I/O method is `async def`. No sync wrappers in the public API.
- Timeouts use `anyio.fail_after(delay)` / `anyio.move_on_after(delay)`, not `asyncio.wait_for`.
- Subprocess (Phase 2 stdio) uses `anyio.open_process()`; newline-delimited JSON-RPC over stdin/stdout.

---

## 7. HTTP & SSE — httpx

- `httpx.AsyncClient` for all registry calls. Set `X-Pharos-Version` header (SPEC §6.1), `Accept: application/json`.
- **429 handling (SPEC §13.5):** honor `Retry-After`; else exponential backoff with full jitter (initial 500ms, factor 2, cap 30s, max 4 retries). On repeated 429, fall back to cached `ServerCard` results + surface `registry_degraded=True`. If registry + cache both exhausted → raise `RegistryUnavailable` (never silent empty results).
- **Conditional cache:** `cache_conditional=True` sends `If-None-Match` / `If-Modified-Since`; 304 → reuse cached card.
- **SSE (`/v1/events`, SPEC §6.7):** `httpx` streaming response; parse `event:`/`data:` lines. Reconnect w/ exponential backoff (1s→60s) + `Last-Event-ID`. Treat missed `ping` beyond `health_check_interval` as dead stream. SSE is an **optimization** — fall back to TTL polling on failure.

---

## 8. The approval flow in Python (SPEC §7)

```python
async def request_approval(
    self,
    server: ServerCard,
    purpose: str,
    requested_scopes: list[str],
    requested_capabilities: list[str],
    duration: str,
    selection_rationale: str,          # MANDATORY (§7.1.1)
    render: Callable[[ApprovalRequest], Awaitable[ApprovalResponse]],
    oauth_consent_defaults_override: list[str] | None = None,  # Phase 2
) -> ApprovalResult:
    ...
```

- Build `ApprovalRequest` (reject if `selection_rationale` empty and not `headless_mode`).
- Call the host `render` callback; on `approval_timeout` → `approved=False, deny_reason="timeout"`.
- On approve: mint a signed `ApprovalToken` (ed25519 over the token body using a local SDK key). Store in consent store (append-only, signed). Optionally POST to `/v1/approve` for registry audit (opt-in).
- **`trust_on_use` (§7.3):** after one successful `tools/call` against a server meeting `verified=true` + `availability="mirrored"` + `rating.count > 100`, subsequent connections within 7 days auto-approve non-modally. Disabled for high-risk scopes (`payments:write`, `admin:*`). Mutually exclusive with `headless_mode`.
- **`PlanApproval` (§7.1.1):** `request_plan_approval(plan_summary, steps: list[ApprovalRequest])` → `PlanApprovalResponse` with `per_step` responses; mints `PlanApprovalToken` (batch sharing a `plan_id`).

**Headless mode (§7.5) — scoped, not blanket:**
```python
pharos = PharosClient(
    headless_mode=True,
    headless_allow_servers=["urn:pharos:acme.com:travel/flight-booking"],
    headless_allow_scopes=["flight_search"],
)
# Novel server NOT on allow-list → HeadlessApprovalRequired error, connection refused.
# Every headless connection logged prominently + on_tool_use tagged headless=True.
```

---

## 9. Connection Manager — `MCPClient` (SPEC §8.3, §9)

```python
class MCPClient:
    server: ServerCard
    approval: ApprovalToken
    protocol_version: str
    server_capabilities: dict

    async def list_tools(self) -> list[Tool]: ...
    async def call_tool(self, name: str, args: dict, *, timeout: int | None = None) -> ToolResult: ...
    async def list_resources(self) -> list[Resource]: ...
    async def read_resource(self, uri: str) -> str: ...
    async def list_prompts(self) -> list[Prompt]: ...
    async def close(self) -> None: ...
```

- `connect(approval)` selects transport from `server.transport[0]` + `server.endpoint`/`stdio_command`.
- If `server.auth.type == "oauth"` (Phase 2): `OAuthFlowHandler.authorize()` runs **first**, before `initialize`. Only on `authorized=True` does `initialize` proceed.
- After `tools/list`: verify claimed `capabilities` backed by actual tools (H13). Unbacked → `CapabilityMismatch`, downgraded in `verified_capabilities`.
- Enforce `approved_scopes` / `approved_capabilities` on every `call_tool`. Out-of-scope → `SCOPE_NOT_APPROVED` (SPEC §7.4).
- At most one live `MCPClient` per `server_id` per session; repeated `connect()` with valid token returns cached client.
- Liveness: `health_check_interval` → `ConnectionLost(server_id, last_seen)`. **Never auto-reconnect** without fresh approval.

---

## 10. Security in Python (SPEC §10 — see `.guides/security/SECURITY_GUIDE.md`)

- **ed25519 verify** via `cryptography`: publisher signatures against `https://<publisher>/.well-known/pharos-pubkey.json` (or registry-cached key). Failed check → `verified=False`.
- **Key pin TTL** (`key_pin_ttl_seconds`, default 86400): re-fetch on TTL; WHOIS registrant/nameserver change → immediate re-verify `domain_control`. Failed re-fetch after TTL → server quarantined (not connectable). (§10.9)
- **Blocklist:** TTL 60s; subscribe to `/v1/events` `blocklist.updated` for push. Connections to listed servers refused before any network call.
- **Consent store** (`~/.pharos/consent.json`): append-only, signed with local SDK key. Tampering detectable. Every approve/reject/revoke recorded with timestamp, server ID, scopes, `agent_id`.
- **`client_secret` NEVER appears in any Python type.** `AuthSpec.app_registration` carries `client_id`, endpoints, scopes, consent_defaults — never the secret. (§10.5)
- **Query privacy (§10.8):** one-time install disclosure that queries are sent to registry. `privacy_mode=True` → send only filters, no `query.text`. `query.embedding` (locally computed) → `blinded_search` without text leaving the device. NEVER log `query.text` at user level.
- **Egress allowlist** (Phase 2, §10.2): `egress_allowlist` restricts which hosts the agent connects to (SSRF defense on CIMD/metadata fetches; redirect depth cap 3, each hop re-validated).

---

## 11. Registry adapters in Python (SPEC §11)

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

- **`PharosRegistryAdapter`** (Phase 1): no translation; speaks §6 API natively. `capabilities = {"semantic_search","filter_search","reviews","pricing","federation","publish","report","blinded_search","push_events","key_pinning"}`.
- **`MCPRegistryAdapter`** (Phase 1): `GET /v0.1/servers?search=<text>` → canonical. Client-side semantic re-rank via bundled `all-MiniLM-L6-v2` ONNX (if `onnxruntime` installed); else substring-only, labeled "non-semantic". Missing fields (pricing, reviews) → `None`, labeled "limited metadata".
- **`ARDAdapter`** (Phase 2): `POST /search` → canonical; `urn:air:` → `urn:pharos:` (preserve original as `source_urn`); `score` (0–100) → `source_score`; `pharos_score = None`. **Never normalize ARD score into `pharos_score`** (§11.4).

---

## 12. Errors (SPEC §6.13, §7, §9.5, §17.5.1)

Typed exceptions in `pharos_discovery/errors.py`:

| Exception | When |
|-----------|------|
| `RegistryUnavailable` | All registries unhealthy + no cache (§13.5) |
| `NoServersFound` | Search returns zero results (distinct from `RegistryUnavailable`) |
| `HeadlessApprovalRequired` | `headless_mode` + novel server not on allow-list (§7.5) |
| `ConnectionFailed` | `initialize` timeout / failure; carries `error="initialize_timeout"` etc. (§9.1) |
| `ConnectionLost` | Liveness probe failed post-connect (§9.5) |
| `SCOPE_NOT_APPROVED` | `call_tool` outside `approved_scopes`/`approved_capabilities` (§7.4) |
| `CapabilityMismatch` | Claimed capability has no backing tool (§9.1 H13) |
| `OAuthUnavailable` | Host lacks MCP Apps + system browser (§17.5.1) |
| `RetryableOAuthFailure` | Inline OAuth iframe/MCP server lost or timed out (§17.4 H10) |
| `ConsentFatigueWarning` | >5 novel-server approvals in a session (advisory, §7.3) |
| `PublisherKeyStale` | Publisher key failed to refresh after TTL (§10.9) |
| `DiscoveryDegraded` | All registries unhealthy, falling back to cache/static list |
| `UNSUPPORTED_FILTER` (400) | Registry doesn't support a requested filter path (§6.3.1) |

---

## 13. Conventions checklist (Python)

- [ ] `snake_case` everywhere (SPEC §8.1).
- [ ] `async def` for all I/O; `anyio` primitives only (no bare `asyncio`).
- [ ] Pydantic v2 models from IDL; never hand-edit `models/`.
- [ ] Type hints on every public symbol; `str | None` not `Optional[str]`.
- [ ] No `client_secret` in any model, log, or error message.
- [ ] Every `tools/call` logged with redacted sensitive params (§7.6).
- [ ] Bounded timeouts via `anyio.fail_after` — no unbounded awaits (H8).
- [ ] Consent store append-only + signed.
- [ ] Public surface change → IDL first, regenerate, update `docs/api/PYTHON_API.md`.
- [ ] Tests: pytest + pytest-asyncio; conformance fixtures shared with TS (§8.6).

---

*Next: `docs/api/PYTHON_API.md` for the full class/method reference; `.guides/testing/TESTING_GUIDE.md` for test patterns; `.guides/security/SECURITY_GUIDE.md` for the security model.*
