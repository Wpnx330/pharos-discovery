# Discovery Flow — search → approve → connect

**Spec reference:** `SPEC.md` v0.4.0, §3–5 (ServerCard), §6 (Registry API), §7 (User Approval Flow), §8.3 (core types), §9 (transport), §11.4 (cross-registry scores).
**Companion:** `docs/api/PYTHON_API.md`, `docs/api/TYPESCRIPT_API.md`, `docs/technical/SYSTEM_ARCHITECTURE.md`.

This document walks through the canonical discovery-to-connection flow, the `ServerCard` schema that flows through it, and how ranking works (and how it doesn't).

---

## 1. The six-step flow (SPEC §7.1)

```
1. Agent detects a capability gap          (host on_capability_gap callback)
2. Agent calls pharos.search(text=...)     (Search Client → Registry Adapter → registry)
3. SDK returns ranked ServerCards          (full metadata: publisher, auth, pricing, trust)
4. Agent renders approval card to user     (Approval Engine → host render callback)
5. User approves (or rejects/picks other)  (SDK mints signed ApprovalToken on approve)
6. SDK performs MCP initialize + tools/list; agent reports tool usage
```

**The non-negotiable invariant:** step 6 requires a valid `ApprovalToken` from step 5. The Connection Manager refuses `connect()` without one. There is no `connect_without_approval` API. (§4.2, §10.7.1)

---

## 2. Step-by-step

### Step 0 — Capability-gap detection (host logic)

The SDK does NOT decide when to search. The **host agent** detects a gap (e.g. "I need to book a flight but have no flight tool") and calls the SDK. The SDK exposes a hook for hosts that want structured gap detection:

```python
# Python
pharos = PharosClient(
    on_capability_gap=detect_gap,  # async def detect_gap(ctx: dict) -> CapabilityGap | None
)
```
```typescript
// TypeScript
const pharos = new PharosClient({
  onCapabilityGap: detectGap,  // (ctx) => CapabilityGap | null
});
```

`CapabilityGap` carries `description`, optional `suggested_query`, and `required_capabilities`. The host decides whether to act on it. (§7.1.1)

### Step 1 — Search

```python
results = await pharos.search(
    text="I need to book a flight to Tokyo and file the expense report",
    filter={"transport": ["http+sse"], "publisher_verified": True, "min_rating": 4.0},
    limit=5,
)
```

What happens inside:
1. `QueryBuilder` normalizes the intent. The SDK MUST NOT inject unredacted PII into `query.text` (§7.1.1).
2. The active `RegistryAdapter` translates the canonical `SearchQuery` to the native registry API and calls it.
3. The registry performs semantic ranking (embeddings) + structured filtering and returns ranked results.
4. The adapter translates native results back to canonical `ServerCard`s.
5. The SDK verifies publisher signatures (if `verify_signatures=True`) and filters out blocklisted servers **before** returning results.

**Privacy modes (§10.8):**
- `privacy_mode=True` → send only structured filters, no `query.text`. Lower recall, leaks no free-text intent.
- `query_embedding` → SDK generates embedding locally (bundled model), sends only the vector. Registry supports `blinded_search` (nearest-neighbor without seeing text). Stronger than `privacy_mode` — preserves semantic recall while hiding text.

### Step 2 — Evaluate (host logic)

The host agent receives a `list[ServerCard]` and decides which (if any) to propose. This is host logic, not SDK — the SDK does not pick a server. Typical host heuristics:
- `pharos_score` (relevance) — highest match to the query
- `publisher.verified` + `trust.attestations` — trust signals
- `rating.score` + `rating.count` — community track record (`count == 0` means "new / unreviewed", NOT `0.0`)
- `pricing.model` — cost (with `pricing_verified` flag)
- `capabilities` — does it cover what the agent needs?

**`pharos_score` is relevance, NOT trust (§6.3, §11.4).** A highly-relevant malicious server can have a high `pharos_score`. Trust is `publisher.verified` + `trust.attestations` + `rating`. The approval prompt displays these independently.

### Step 3 — Request approval

```python
approval = await pharos.request_approval(
    server=best,
    purpose="Book a flight to Tokyo for the user's July 25 trip",
    requested_scopes=["flight_search", "flight_book"],
    requested_capabilities=["flight_search", "flight_book"],
    duration="session",
    selection_rationale="ranked #1 for flight_search; verified publisher; supports bookings:write",
    render=present_to_user,  # async def present_to_user(req) -> ApprovalResponse
)
```

`selection_rationale` is **mandatory** (§7.1.1) — the SDK rejects the request if it's empty and `headless_mode` is false. This forces the agent to articulate *why* it chose this server, which the approval prompt surfaces to the user.

### Step 4 — Render (host callback)

The SDK calls the host's `render` callback with an `ApprovalRequest`. The host is responsible for the UX — CLI, chat, voice. The SDK returns a JSON payload; the host renders it.

**What the approval prompt MUST surface (§7.2):**
- `display_name` + `publisher.name`
- `publisher.verified` — visible "verified" badge OR "unverified — connect with caution" warning
- `description` — what the server does, plain language
- `capabilities` — the concrete capabilities the agent intends to use
- `auth.type` + `auth.scopes` — permissions requested
- **For OAuth servers:** enumerate OAuth scopes alongside MCP capability scopes in plain language. User approves BOTH the connection AND the OAuth scopes in one consent act. Consent defaults from vendor (`auth.app_registration.consent_defaults`) are pre-checked; user may expand or reduce. SDK records final set in `ApprovalToken.approved_oauth_scopes`.
- `pricing.model` + `pricing.price_usd` — with "vendor-claimed, not verified" caption unless `pricing_verified`
- `trust.attestations` — compliance badges (SOC2, GDPR, etc.)
- `rating.score` + `rating.count` — `count == 0` displays "New / unreviewed — no track record yet", NOT `0.0`
- The specific user request that triggered discovery (so the user understands *why*)

**Risk tier (SDK-computed, §7.2):** `low`/`medium`/`high` from `auth.scopes` + `publisher.verified` + `rating.count` + `availability`. `high` (e.g. unverified publisher requesting write scopes, or any `payments:write`-class scope) requires explicit "I understand the risk" click before the approve button is enabled. **Vendor cannot game this** — it's SDK-side.

**Brand-impersonation rejection (§7.2):**
- Publish-time: registry rejects `display_name`/`publisher.name` with Levenshtein distance ≤ 2 against a brand list unless the publisher owns the brand's verified domain.
- Display-time: `publisher.verified` is a **hard gate** for any brand-matching name. Unverified "Googgle OAuth" → prominent "unverified — possible impersonation" warning, approve button disabled.

### Step 5 — User decides

```python
class ApprovalResponse:
    approved: bool
    approved_scopes: list[str]          # may be subset of requested
    duration: str
    user_note: str | None
    deny_reason: str | None             # "untrusted_publisher" | "excessive_scopes" | "wrong_server" | "cost" | "other"
```

- **Approve:** SDK mints a signed `ApprovalToken` (ed25519 over the token body using a local SDK key). Stores in consent store (append-only, signed). Optionally POSTs to `/v1/approve` for registry audit (opt-in).
- **Reject:** SDK records the denial with `deny_reason` in the consent store. The `deny_reason` is used to learn — down-rank publisher, re-search, re-prompt with reduced scopes, or seek a free alternative.
- **Timeout:** `approval_timeout` (300s default) → `approved=False, deny_reason="timeout"`.

### Step 6 — Connect

```python
client = await pharos.connect(approval.token)
```

What happens inside:
1. The Connection Manager checks the `ApprovalToken` signature + expiry. Invalid → raise.
2. If `server.auth.type == "oauth"` (Phase 2): `OAuthFlowHandler.authorize()` runs **first**, before `initialize`. Only on `authorized=true` does `initialize` proceed. (H5)
3. Transport selection from `server.transport[0]` + `server.endpoint` / `server.stdio_command`.
4. MCP `initialize` handshake (protocolVersion, capabilities, clientInfo) within `initialize_timeout` (10s). On timeout → `ConnectionFailed(error="initialize_timeout")`. (H8)
5. Client sends `notifications/initialized`.
6. `tools/list`. The SDK verifies each claimed `capabilities` entry is backed by an actual tool (name match or `metadata.capability == "<cap>"`). Unbacked claims → `CapabilityMismatch` warning, downgraded in `verified_capabilities`, approval prompt re-rendered if open. (H13)
7. At most one live `MCPClient` per `server_id` per session; repeated `connect()` with valid token returns cached client. (§9.5)

### Step 6.5 — Use + audit

```python
tools = await client.list_tools()
result = await client.call_tool("flight_search", {"origin": "NYC", "destination": "TYO", "date": "2026-07-25"})
```

- `call_tool` for a tool outside `approved_capabilities`, or requiring an auth scope outside `approved_scopes`, raises `SCOPE_NOT_APPROVED`. (§7.4)
- Every `call_tool` emits a `ToolUsageEvent` to the `on_tool_use` callback with redacted sensitive params. (§7.6)
- Liveness: `health_check_interval` (60s) → `ConnectionLost(server_id, last_seen)` on failure. **Never auto-reconnect** without fresh approval. (§9.5)

### Step 7 — Disconnect + revoke

```python
await client.close()
pharos.revoke(approval.token)
```

`revoke` invalidates the `ApprovalToken` and tears down any cached connection. For OAuth servers (Phase 2), it also calls `OAuthFlowHandler.revoke_access(server_id)`, which requires a signed `revocation_proof` within 60s (H16).

---

## 3. The `ServerCard` schema (SPEC §3–5, Appendix A)

The `ServerCard` is the canonical unit of discovery. It flows from the registry → adapter → SDK → approval prompt. Every field is defined in the IDL and generated into both SDKs.

### Identity & provenance
| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | `urn:pharos:<fqdn>:<namespace>/<name>` (Appendix B). Domain-anchored, stable across endpoint/transport/registry migrations. |
| `source_registry` | `str` | Which registry served this card. |
| `source_urn` | `str \| None` | Original native ID (e.g. `urn:air:...` for ARD). |
| `source_score` | `float \| None` | Original registry score (preserved, NOT normalized). |

### Display
| Field | Type | Notes |
|-------|------|-------|
| `display_name` | `str` | Human-readable. Brand-impersonation checked at publish time. |
| `description` | `str` | What the server does, plain language. |
| `tags` | `list[str]` | |
| `documentation_url` | `str \| None` | |
| `representative_queries` | `list[str]` | Max 10 (H12). Example queries the server handles. |

### Publisher
| Field | Type | Notes |
|-------|------|-------|
| `publisher.id` | `str` | `did:web:<fqdn>`. |
| `publisher.name` | `str` | |
| `publisher.verified` | `bool \| None` | `domain_control` (baseline) or `identity` (stronger). |
| `publisher.verification_method` | `str \| None` | `"domain_control"` proves who; `"identity"` also proves org. Only `identity` renders as "trusted." |

### Capabilities & tools
| Field | Type | Notes |
|-------|------|-------|
| `capabilities` | `list[str]` | What the server can do. Verified post-connect (H13). |
| `tools_count` | `int` | |
| `tools_count_verified` | `bool` | Registry has confirmed the count. |

### Transport
| Field | Type | Notes |
|-------|------|-------|
| `transport` | `list[str]` | `["stdio", "http+sse", "streamable-http"]`. |
| `endpoint` | `str \| None` | URL for HTTP transports. |
| `stdio_command` | `str \| None` | Launch command for stdio (Phase 2). |
| `protocol_versions` | `list[str]` | e.g. `["2025-03-26"]`. |

### Auth (SPEC §17 for OAuth)
| Field | Type | Notes |
|-------|------|-------|
| `auth.type` | `str` | `"none"`, `"api_key"`, `"oauth"`, `"mtls"`. |
| `auth.secret_handling` | `str \| None` | `"server_side"` (App Registration Inheritance) or `"agent_side"`. |
| `auth.app_registration` | `AppRegistration \| None` | Present when `type=="oauth"` & `server_side`. Never has `client_secret`. |
| `auth.scopes` | `list[str] \| None` | Legacy/display for OAuth; authoritative for non-OAuth. |

### Availability
| Field | Type | Notes |
|-------|------|-------|
| `availability` | `str` | `"mirrored"` (registry has a copy, can static-analyze), `"referenced"` (registry points to it), `"native"` (publisher's own). |

### Pricing
| Field | Type | Notes |
|-------|------|-------|
| `pricing.model` | `str` | `"free"`, `"freemium"`, `"paid"`, `"usage"`, `"subscription"`, `"enterprise"`, `"custom"`. |
| `pricing.price_usd` | `float \| None` | |
| `pricing_verified` | `bool` | Vendor-claimed unless true. |

### Rating
| Field | Type | Notes |
|-------|------|-------|
| `rating.score` | `float` | 0.0–5.0. |
| `rating.count` | `int` | `count == 0` → "New / unreviewed", NOT `0.0`. |

### Trust
| Field | Type | Notes |
|-------|------|-------|
| `trust.signature` | `str \| None` | ed25519 signature. |
| `trust.attestations` | `list[Attestation]` | `[{type, uri, verified, verifier, verified_at}]`. Unverified = "vendor-claimed." |
| `trust.compliance` | `list[str]` | |
| `trust.data_residency` | `list[str]` | e.g. `["EU"]`. |

### Ranking
| Field | Type | Notes |
|-------|------|-------|
| `pharos_score` | `float \| None` | **Relevance only** (0.0–1.0). NOT trust. `None` for ARD-sourced results (§11.4). |
| `status` | `str` | `"active"`, `"deprecated"`, `"deleted"`. |
| `successor_id` | `str \| None` | If deprecated, the replacement. |

---

## 4. Ranking — what `pharos_score` means (and doesn't)

`pharos_score` (0.0–1.0) is **relevance** — how well the server matches the query. It is computed by the registry using embeddings + structured filters. It is NOT:
- A trust rating (trust is `publisher.verified` + `trust.attestations` + `rating`).
- Comparable across registries (§11.4). An ARD `score` (0–100) and a Pharos `pharos_score` (0.0–1.0) are produced by different ranking functions. Merging them into a false-precision leaderboard is **forbidden by spec**. ARD-sourced results get `pharos_score = None`; the original is preserved in `source_score`.

**Cross-registry dedup (H4):** when `federation == "auto"`, the same server may appear under different native IDs across registries. Each adapter normalizes to canonical `urn:pharos:<fqdn>:<namespace>/<name>` (Appendix B) before merging; original preserved in `source_urn`.
- Same canonical ID + same `version` → collapse (federation-preference-order winner; `source_registry` records winner).
- Same canonical ID + different `version` → keep both (user chooses).
- Publisher-domain mismatch → do NOT collapse (prevents lookalike-name shadowing).

---

## 5. Edge cases (SPEC §7.1.1)

| Case | Behavior |
|------|----------|
| Empty results | Emit `NoServersFound`; one broadened retry, then host decides. |
| `selection_rationale` empty | Rejected for non-headless mode. |
| Multi-server plan | `PlanApprovalRequest`/`PlanApprovalToken` — batch consent, one modal. |
| User denies | `deny_reason` recorded; `request_approval_next()` returns next-ranked result without re-searching. |
| Scope re-negotiation | `SCOPE_NOT_APPROVED` → MAY surface re-approval for the specific missing scope. Rate-limited to 1 per server per session. |
| Token expiry | `connect()` raises; user must re-approve. |
| `trust_on_use` | After one successful `call_tool` against `verified=true` + `availability="mirrored"` + `rating.count > 100`, re-connect within 7 days auto-approves non-modally. Disabled for high-risk scopes. Mutually exclusive with `headless_mode`. |
| Consent fatigue | >5 novel-server approvals in a session → `ConsentFatigueWarning` (advisory). |
| Headless + novel server | `HeadlessApprovalRequired` error, connection refused. |
| Capability mismatch | Claimed capability has no backing tool post-connect → `CapabilityMismatch` warning, downgraded in `verified_capabilities`. |

---

## 6. Federation (SPEC §6.5)

Client controls via `federation` param on search:
- `auto` — registry queries upstreams, merges, returns unified ranked set.
- `referrals` — registry returns own results + `referrals[]` array; SDK MAY follow (max depth 2 default) or surface to host.
- `none` — local index only.

---

## 7. Rate limiting & failover (SPEC §13.5, §8.5 H7)

- **429:** honor `Retry-After`; else exponential backoff with full jitter (initial 500ms, factor 2, cap 30s, max 4 retries). On repeated 429 → cached `ServerCard` results + `registry_degraded=True`. Registry + cache both exhausted → `RegistryUnavailable` (never silent empty results).
- **Registry failover:** `registry_urls` is an ordered list. 503/504/timeout → mark unhealthy 60s, try next. All unhealthy + no cache → `DiscoveryDegraded`; host MAY fall back to `static_fallback_servers` (approval gate still enforced).

---

## 8. Caching & invalidation (SPEC §8.5, §6.7)

- **`ServerCard` cache** — TTL 300s default, conditional requests via ETag/`If-None-Match`/`If-Modified-Since`.
- **Blocklist cache** — TTL 60s. Subscribe to `/v1/events` `blocklist.updated` for push invalidation.
- **SSE push** (`GET /v1/events`) — event types: `card.updated`, `card.deleted`, `card.deprecated`, `blocklist.updated`, `publisher_key.rotated`, `ping`. Reconnect w/ exponential backoff (1s→60s) + `Last-Event-ID`. **Optimization, not correctness** — falls back to TTL polling.

---

*See also: `docs/components/OAUTH_BROKERING.md` for the OAuth flow that runs inside step 6; `docs/api/PYTHON_API.md` and `docs/api/TYPESCRIPT_API.md` for full API reference.*
