# Architecture Overview — PHAROS Discovery

**Audience:** AI agents and contributors making cross-cutting or structural decisions.
**Source of truth:** `SPEC.md` v0.4.0, especially §5 (Architecture), §6 (Discovery Protocol), §8 (SDK Design), §9 (Transport), §11 (Compatibility).

---

## 1. What PHAROS Discovery is — and is not

PHAROS Discovery is a **provider-agnostic, embeddable client SDK**. An AI agent runtime (Claude, GPT, Cursor, DeepSeek, Gemini, xAI, Zap, custom) imports it and uses it to **discover, evaluate, approve, and connect to MCP servers** at runtime. It is:

- A **client**, not a server. There is no `pharosd` daemon. (SPEC §8.4)
- **Two parallel first-party libraries** generated from a single TypeSpec IDL:
  - `pharos-discovery` — Python 3.10+ (PyPI)
  - `@pharos/discovery` — TypeScript, Node 20+ & browser (npm)
- **Registry-agnostic.** Any registry implementing the §6 HTTP API is a valid backend. The **Pharos Registry** (sister project, Rust) is the reference implementation, not a dependency.
- **Consent-first.** For conformant SDK-using agents, no connection is established without an explicit user approval event. No `connect_without_approval` API exists. (SPEC §4.2, §7, §10.7.1)

It is **not**: a registry, a search engine, an MCP server, an LLM client, or a walled-garden marketplace adapter (those are adapters, not the core).

---

## 2. The four layers of the SDK

The SDK is internally structured into four layers (SPEC §5.2). Every feature maps onto one of them:

| Layer | Responsibility | Key types |
|-------|----------------|-----------|
| **1. Search Client** | Build queries (NL text + structured filters), call the registry, return ranked `ServerCard`s. | `PharosClient.search()`, `SearchQuery`, `ServerCard`, `QueryBuilder` |
| **2. Approval Engine** | Take ranked results, render a user-facing approval prompt via host callback, record consent, mint a signed `ApprovalToken`. | `ApprovalRequest`, `ApprovalResponse`, `ApprovalToken`, `PlanApprovalRequest`/`Response` |
| **3. Connection Manager** | Take an approved `ServerCard` + `ApprovalToken`, select transport (stdio / HTTP+SSE / streamable-http), perform MCP `initialize`, cache the live `MCPClient`, expose `tools/list` + `tools/call`. Enforces `approved_scopes`/`approved_capabilities`. | `MCPClient`, `OAuthFlowHandler`, `OAuthResult` |
| **4. Registry Adapter Layer** | Translate between the canonical §6 API and native registry wire formats. | `RegistryAdapter` (interface), `PharosRegistryAdapter`, `MCPRegistryAdapter`, `ARDAdapter` |

```
┌─────────────────────────────────────────────────────────────┐
│                    AI AGENT RUNTIME                          │
│  ┌────────────────────────────────────────────────────────┐  │
│  │              Pharos Discovery SDK                       │  │
│  │  ┌──────────┐  ┌──────────┐  ┌────────────────────┐    │  │
│  │  │ Search   │→ │ Approval │→ │ Connection Mgr     │    │  │
│  │  │ Client   │  │ Engine   │  │ (MCP lifecycle)    │    │  │
│  │  └────┬─────┘  └──────────┘  └─────────┬──────────┘    │  │
│  │       └────────────┬───────────────────┘                │  │
│  │              ┌─────▼──────────────┐                     │  │
│  │              │ Registry Adapters  │                     │  │
│  │              │ Pharos│MCP│ARD│... │                     │  │
│  │              └────────────────────┘                     │  │
│  └────────────────────────────────────────────────────────┘  │
│                          │ HTTPS                              │
└──────────────────────────┼───────────────────────────────────┘
                           ▼
        ┌──────────────────────────────────────────┐
        │  PHAROS REGISTRY (ref) / any compatible   │
        └──────────────────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────────┐
        │     DISCOVERED MCP SERVERS                │
        │   (stdio subprocess + remote HTTP/SSE)    │
        └──────────────────────────────────────────┘
```
(Adapted from SPEC §5.1.)

---

## 3. What lives where — client vs. registry

A common source of bugs is putting registry-side work in the client or vice versa. The split (SPEC §5.3):

| Concern | Client (SDK) | Registry |
|---|---|---|
| NL query construction | ✅ | |
| Semantic ranking / embeddings | | ✅ |
| Structured filtering | ✅ (query side) | ✅ (evaluation) |
| Publisher verification (signatures, attestations) | ✅ (verification) | ✅ (issuance) |
| User approval UX | ✅ | |
| Consent logging | ✅ local (+ optional registry audit) | |
| MCP `initialize` handshake | ✅ | |
| Transport selection (stdio vs HTTP/SSE) | ✅ | |
| `tools/list`, `tools/call` | ✅ | |
| Business publishing, reviews, pricing storage | | ✅ |
| Malicious-server blocklists | ✅ (consumer) | ✅ (source) |

**Rule of thumb:** if it requires an index, embeddings, or multi-tenant storage, it belongs in the registry. If it requires the user's presence (consent, rendering, transport), it belongs in the SDK.

---

## 4. The discovery-to-connection flow (six steps)

The canonical happy path (SPEC §7.1). Every layer participates:

```
1. Agent detects a capability gap          (host-supplied on_capability_gap callback)
2. Agent calls pharos.search(text=...)     (Search Client → Registry Adapter → registry)
3. SDK returns ranked ServerCards          (full metadata: publisher, auth, pricing, trust)
4. Agent renders approval card to user     (Approval Engine → host render callback)
5. User approves (or rejects/picks other)  (SDK mints signed ApprovalToken on approve)
6. SDK performs MCP initialize + tools/list; agent reports tool usage
```

**Non-negotiable invariant:** step 6 requires a valid `ApprovalToken` from step 5. The Connection Manager refuses `connect()` without one. (SPEC §7, §10.7.1)

**Edge cases every implementer must handle** (SPEC §7.1.1):
- Step 0: capability-gap detection is **host logic**, not SDK. SDK exposes `on_capability_gap(context) -> CapabilityGap | None`.
- Step 2.5: `QueryBuilder` normalizes intent; SDK MUST NOT inject unredacted PII into `query.text`.
- Step 3.5: `ApprovalRequest.selection_rationale` is **mandatory** (why this server was chosen).
- Step 3→4 empty results: emit `NoServersFound`; one broadened retry, then host decides.
- Step 4.5: multi-server plans use `PlanApprovalRequest`/`PlanApprovalToken` (batch consent, one modal).
- Step 6.5: post-call audit via `ToolUsageEvent` log (enforced for conformant SDK-using agents).

---

## 5. Dual-SDK architecture (IDL-first)

**The two SDKs are generated from a single TypeSpec IDL, not hand-written in parallel.** The IDL is the source of truth; the SDKs are codegen output. (SPEC §8.6)

```
                  ┌─────────────────────┐
                  │   TypeSpec IDL      │  typespec/
                  │  (canonical types)  │  ServerCard, SearchQuery,
                  └─────────┬───────────┘  ApprovalToken, OAuthFlowHandler, ...
                            │
              ┌─────────────┼─────────────┐
              ▼                           ▼
   ┌────────────────────┐       ┌────────────────────┐
   │ Python emitter     │       │ TypeScript emitter │
   │ → pydantic v2      │       │ → TS types + zod   │
   │ → pharos-discovery │       │ → @pharos/discovery│
   └─────────┬──────────┘       └──────────┬─────────┘
             │                             │
   ┌─────────▼──────────┐       ┌──────────▼─────────┐
   │ Hand-written:      │       │ Hand-written:      │
   │ • anyio transport  │       │ • Node async       │
   │ • keychain shim    │       │ • browser fetch    │
   │ • file paths       │       │ • Web Crypto shim  │
   └────────────────────┘       └────────────────────┘
```

**Why IDL-first:** the v0.2 code samples already diverged (Python took `text`/`filter` as separate kwargs; TS took a single options object) and the spec had no mechanism to detect or prevent it. The IDL + conformance suite (SPEC §8.6) makes drift impossible by construction — two SDKs that both pass the conformance suite are interoperable.

**What is generated:** `ServerCard` schema (Appendix A), `POST /v1/search` request/response, `GET /v1/servers/{id}`, `POST /v1/approve`, `POST /v1/feedback/*`, the `ApprovalRequest`/`ApprovalResponse`/`ApprovalToken`/`OAuthResult` types, and the `OAuthFlowHandler` interface.

**What is hand-written:** transport adapters (anyio subprocess/stdin-stdout vs Node child_process/Web streams), platform shims (keychain, file paths, crypto backend), and the approval renderers (CLI/chat/voice — host-supplied anyway).

**Breaking-change policy:** the IDL is versioned (`service.version`). Minor bump = additive fields with defaults; major bump = breaking, requires coordinated SDK release across both languages. `X-Pharos-Version` header advertises wire-protocol version (SPEC §6.1).

---

## 6. Registry adapter layer

Each adapter implements the canonical `ServerCard` schema + search/approval contract and translates to/from a native registry API. (SPEC §11.1)

```python
class RegistryAdapter:
    name: str                              # "pharos" | "mcp-official" | "ard" | ...
    capabilities: set[str]                 # {"semantic_search", "filter_search", "reviews",
                                           #  "pricing", "federation", "publish", "report",
                                           #  "blinded_search", "push_events", "key_pinning"}
    async def search(query: SearchQuery) -> list[ServerCard]: ...
    async def get(server_id: str) -> ServerCard: ...
    async def publish(card: ServerCard) -> str: ...
    async def report(server_id: str, reason: str) -> None: ...
    def to_canonical(native: dict) -> ServerCard: ...
    def from_canonical(card: ServerCard) -> dict: ...
```

The `capabilities` set lets the SDK **degrade gracefully** — an adapter without `semantic_search` gets substring-only behavior; one without `pricing` returns `pricing=None` without the SDK probing. (SPEC §11.1)

**Adapters by phase:**
- **Phase 1:** `PharosRegistryAdapter` (native, no translation) + `MCPRegistryAdapter` (official MCP Registry `/v0.1/servers` → canonical, with client-side semantic re-ranking via bundled `all-MiniLM-L6-v2` ONNX).
- **Phase 2:** `ARDAdapter` (ARD `POST /search` → canonical; `urn:air:` ↔ `urn:pharos:` ID conversion; `score` preserved as `source_score`, NOT normalized into `pharos_score`).
- **Phase 3:** `A2AAdapter` (discovery-only — maps `AgentCard` skills → capabilities; returns card to host, does NOT connect), `AGNTCYAdapter`, walled-garden bridges (require documented vendor API; no scraping).

**Cross-registry score comparison is illegal by spec.** An ARD `score` (0–100) and a Pharos `pharos_score` (0.0–1.0) are produced by different ranking functions; never merge into a false-precision leaderboard. ARD-sourced results get `pharos_score = None`; `source_score` carries the original. (SPEC §11.4)

---

## 7. Transport handling

After approval, the Connection Manager establishes a live MCP session. Pharos does NOT reinvent the MCP wire protocol — it speaks standard MCP 2025-03-26 over JSON-RPC 2.0. (SPEC §9)

**MCP lifecycle (handled internally):**
1. Client sends `initialize` (protocolVersion, capabilities, clientInfo)
2. Server responds (chosen protocolVersion, capabilities, serverInfo, instructions)
3. Client sends `notifications/initialized`
4. Operational phase: `tools/list`, `tools/call`, `resources/read`, `prompts/list`...
5. Shutdown (transport-specific teardown)

**Transports:**
- **stdio** (Phase 2) — `ServerCard.stdio_command` carries launch command (e.g. `npx -y @acme/flights-mcp`). SDK spawns subprocess, newline-delimited JSON-RPC over stdin/stdout. Highest-trust *if* publisher verified + command audited; highest-risk otherwise. `allow_stdio=True` default, disable for privacy-conscious hosts. Sandboxing hooks: `{"mode": "docker"|"firejail"|"nsjail"|"custom", "command": ...}`.
- **Streamable HTTP** (Phase 1) — MCP's recommended HTTP transport; single endpoint, POST JSON-RPC, server may upgrade to SSE.
- **HTTP+SSE** (Phase 1, legacy) — dedicated SSE endpoint + POST endpoint.

The SDK negotiates automatically based on `ServerCard.transport` and `endpoint`. All remote connections use TLS 1.2+. Publisher public key is pinned when `trust.signature` present and `verify_signatures=True`.

**Bounded timeouts (H8) — a dead server must never hang the agent:**
- `initialize_timeout` 10s (covers steps 1–3; on expiry → `ConnectionFailed(error="initialize_timeout")`)
- `tool_call_timeout` 30s per `tools/call` (overridable per-call via `call_tool(name, args, timeout=...)`)
- `heartbeat_interval` 30s (HTTP/SSE only; JSON-RPC `ping`/`pong` within `initialize_timeout`)
- `health_check_interval` 60s → `ConnectionLost(server_id, last_seen)` on failure

**Connection pooling (SPEC §9.5):** at most one live `MCPClient` per `server_id` per session. Repeated `connect()` with a valid, non-expired `ApprovalToken` returns the cached client. Teardown on `client.close()`, token expiry, `pharos.revoke()`, process exit (best-effort). **Never auto-reconnect after teardown** without a fresh approval — even for `persistent`-duration servers.

**Capability verification post-connect (H13):** after `tools/list`, the SDK verifies each claimed `ServerCard.capabilities` entry is backed by an actual tool (name match or `metadata.capability == "<cap>"`). Unbacked claims → `CapabilityMismatch` warning, downgraded in `verified_capabilities`, approval prompt re-rendered if open.

---

## 8. Federation model

Registries MAY federate. Client controls via `federation` param on search (SPEC §6.5):
- `auto` — registry queries upstreams, merges, returns unified ranked set.
- `referrals` — registry returns own results + `referrals[]` array; SDK MAY follow (max depth 2 default) or surface to host.
- `none` — local index only.

**Dedup by canonical ID (H4):** when `federation == "auto"`, the same server may appear under different native IDs across registries (e.g. `urn:air:...` in ARD, `urn:pharos:...` in Pharos). Each adapter normalizes to canonical `urn:pharos:<fqdn>:<namespace>/<name>` (Appendix B) before merging; original preserved in `source_urn`. Same canonical ID + same `version` → collapse (federation-preference-order winner; `source_registry` records winner). Same canonical ID + different `version` → keep both (user chooses). Publisher-domain mismatch → do NOT collapse (prevents lookalike-name shadowing).

---

## 9. Cache, push invalidation, and failover

- **`ServerCard` cache** — TTL 300s default (`cache_ttl_seconds`), conditional requests via ETag/`If-None-Match`/`If-Modified-Since` (`cache_conditional=True`). (SPEC §8.5)
- **Blocklist cache** — TTL 60s (`blocklist_cache_ttl_seconds`). Subscribe to `/v1/events` for push invalidation so a freshly-listed malicious server is blocked in seconds. (SPEC §10.3)
- **Publisher key pins** — re-fetch `.well-known/pharos-pubkey.json` at most every `key_pin_ttl_seconds` (default 86400s). WHOIS registrant/nameserver change → immediate re-verification of `domain_control`. Failed re-fetch after TTL → server quarantined (not connectable) until re-validated. (SPEC §9.3, §10.9)
- **SSE push** (`GET /v1/events`) — event types: `card.updated`, `card.deleted`, `card.deprecated`, `blocklist.updated`, `publisher_key.rotated`, `ping`. Reconnect w/ exponential backoff (1s→60s) + `Last-Event-ID`. **Optimization, not correctness requirement** — falls back to TTL polling. (SPEC §6.7)
- **Registry failover (H7)** — `registry_urls` is an ordered list. 503/504/timeout → mark unhealthy 60s, try next. Re-probe after blackout. All unhealthy + no cache → `DiscoveryDegraded` event; host MAY fall back to `static_fallback_servers`. (SPEC §8.5)

---

## 10. Key architectural decisions (summary)

Full ADR-style writeups in `docs/technical/SYSTEM_ARCHITECTURE.md`. Quick reference:

1. **Client, not server.** Embeddable library; no daemon. (SPEC §1, §8.4)
2. **Dual SDK from one IDL.** Prevents drift; both ship Phase 1. (SPEC §8.6)
3. **Consent is a client-side contract.** Non-bypassable for conformant SDK-using agents; out-of-SDK bypass acknowledged (§10.7.1); server-side enforcement is a *future* protocol extension. (SPEC §4.2, §10.7.1)
4. **Thin client, fat registry.** Ranking/embeddings/indexing live in the registry. (SPEC §4.3)
5. **Registry-agnostic via adapters.** No single registry is canonical. (SPEC §4.5, §11)
6. **OAuth via App Registration Inheritance (Phase 2).** Agent never handles tokens or `client_secret`; MCP server brokers server-side; inline OAuth via MCP Apps. (SPEC §17)
7. **`pharos_score` is relevance, not trust.** Trust is `publisher.verified` + `trust.attestations`; cross-registry score comparison is illegal. (SPEC §6.3, §11.4)
8. **Domain-anchored URN IDs.** `urn:pharos:<fqdn>:<namespace>/<name>`; stable across endpoint/transport/registry migrations. (SPEC §6.4.1, Appendix B)

---

*For deeper dives: `docs/technical/SYSTEM_ARCHITECTURE.md` (ADR-style), `docs/components/DISCOVERY_FLOW.md`, `docs/components/OAUTH_BROKERING.md`, `docs/api/PYTHON_API.md`, `docs/api/TYPESCRIPT_API.md`.*
