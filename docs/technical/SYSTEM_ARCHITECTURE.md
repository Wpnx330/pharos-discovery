# System Architecture — PHAROS Discovery

**Spec reference:** `SPEC.md` v0.4.0 (§1–5 overview, §8 SDK design, §9 transport, §11 adapters, §13 registry ecosystem, §15 roadmap, §17 OAuth).
**Audience:** Architects, contributors making cross-cutting decisions. For implementation patterns see `.guides/architecture/OVERVIEW.md`.

This document records the architectural decisions behind PHAROS Discovery in ADR (Architecture Decision Record) style. Each ADR states the decision, the context that drove it, the alternatives considered, and the consequences.

---

## ADR-001: PHAROS Discovery is a client, not a server

**Status:** Accepted (SPEC §1, §8.4)

**Context.** Early sketches considered a `pharosd` daemon that the agent runtime would call over a local socket. A daemon would allow agent-agnostic state (consent store, connection pool, blocklist cache) to be shared across multiple agent processes and would centralize transport handling.

**Decision.** PHAROS Discovery ships as an **embeddable client library** — `pharos-discovery` (Python) and `@pharos/discovery` (TypeScript). No daemon. State is per-process (consent store on disk, connection pool in memory, blocklist cache in memory with TTL).

**Alternatives considered.**
- Local daemon (`pharosd`): rejected because (a) it adds an install/ops burden for every agent that wants discovery, (b) it creates a single point of failure for the agent's tooling, (c) consent state is inherently per-user-per-agent — a shared daemon blurs accountability, (d) the MCP connection lifecycle is tightly coupled to the agent's request lifecycle and doesn't benefit from externalization.

**Consequences.**
- ✅ Zero ops: `pip install` / `npm install` and import.
- ✅ Consent state is unambiguously owned by the embedding agent.
- ✅ No IPC boundary between agent and discovery — the approval callback is a direct function call.
- ⚠️ Multiple agent processes on the same machine do not share a connection pool. Acceptable: MCP connections are cheap and per-server-id pooling within a process covers the common case.
- ⚠️ The consent store is a file (`~/.pharos/consent.json`). Concurrent writers must use file locking; the SDK handles this internally.

---

## ADR-002: Two first-class SDKs generated from a single TypeSpec IDL

**Status:** Accepted (SPEC §8.6)

**Context.** The project must ship in **both** Python and TypeScript in Phase 1 — the two dominant agent-runtime languages. Hand-writing two SDKs in parallel invites drift: the v0.2 code samples already diverged (Python took `text`/`filter` as separate kwargs; TypeScript took a single options object) and there was no mechanism to detect or prevent it. Drift between language SDKs is the single most reliable way to fragment an ecosystem.

**Decision.** A single **TypeSpec IDL** (`idl/typespec/`) is the source of truth for the public surface. Both SDKs are **codegen output**:
- TypeSpec → pydantic v2 models → `packages/python/src/pharos_discovery/models/`
- TypeSpec → TypeScript interfaces + zod schemas → `packages/typescript/src/models/`

Hand-written code extends (never edits) generated models. CI regenerates both on every change and fails if generated output differs from committed output (**drift detection**). A shared **conformance suite** (`conformance/`) of golden JSON fixtures + behavioral assertions runs against both SDKs; both must pass.

**Alternatives considered.**
- Hand-write both in parallel with a style guide: rejected — drift is undetectable until a user hits it.
- Generate one from the other (e.g. pydantic → TS via `pydantic2ts`): rejected — couples the two languages' release cadences and makes one a second-class citizen.
- JSON Schema as the IDL: rejected — JSON Schema describes shape but not behavior, naming conventions, or error semantics. TypeSpec gives us HTTP bindings + per-language naming transforms.

**Consequences.**
- ✅ Drift is impossible by construction: two SDKs that pass the conformance suite are interoperable.
- ✅ Breaking changes are visible at the IDL level before any SDK code changes.
- ✅ Naming conventions (`snake_case` Python, `camelCase` TS) are emitter config, not per-file convention.
- ⚠️ Contributors must learn TypeSpec. Mitigated: the IDL is small (one `ServerCard`, a handful of request/response types, the `OAuthFlowHandler` interface).
- ⚠️ Generated code is not hand-tunable. Hand-written extensions live in separate files.

**Versioning.** The IDL carries `service.version`. Minor bumps are additive (fields with defaults); major bumps are breaking and require a coordinated release across both languages. The `X-Pharos-Version` header advertises the wire-protocol version on every registry call (SPEC §6.1).

---

## ADR-003: Consent is a client-side contract, not a wire-level invariant

**Status:** Accepted (SPEC §4.2, §10.7.1)

**Context.** The core safety promise of PHAROS Discovery is that the user approves every connection to a discovered server. The question is *where* that guarantee is enforced.

**Decision.** The approval gate is enforced **in the SDK's Connection Manager**. `pharos.connect()` requires a valid signed `ApprovalToken`; there is no `connect_without_approval` API. This is a **client-side contract**: it is non-bypassable for any agent that uses a conformant SDK, but a non-SDK agent (one that reads a `ServerCard` and connects directly via raw MCP) can bypass it.

**Alternatives considered.**
- Wire-level enforcement (MCP server refuses `initialize` without an `ApprovalToken`): rejected for Phase 1 because (a) it requires MCP protocol changes that are out of scope for a discovery project, (b) it would make the SDK dependent on server-side cooperation that not all MCP servers would implement, (c) it couples discovery to a specific MCP protocol version.
- Registry-level enforcement (registry issues short-lived connection tickets): rejected — adds a round-trip and a failure mode, and the registry is not in the trust path for a connection.

**Consequences.**
- ✅ The SDK works against any MCP server (2025-03-26) with no server-side changes.
- ✅ The approval UX is fully under the host agent's control (CLI, chat, voice).
- ⚠️ A non-SDK agent can bypass the gate by connecting directly. This is acknowledged in the threat model (§10.7.1, threat C1). The mitigation is ecosystem pressure: conformant SDK-using agents are the easy path, and the consent log + tool-usage audit make bypass detectable after the fact.
- 🔮 **Server-side enforcement of `ApprovalToken` is a FUTURE protocol extension** (§10.7.1). The Phase 1 data model is forward-compatible: `ApprovalToken` is already signed and structured so a future MCP protocol version could require it on `initialize`. Do not hack this into Phase 1.

---

## ADR-004: Thin client, fat registry

**Status:** Accepted (SPEC §4.3)

**Context.** Discovery requires semantic ranking (embeddings), structured filtering, publisher verification data, reviews, pricing, and blocklists. These need an index and multi-tenant storage.

**Decision.** The **registry** owns the index, embeddings, ranking, and multi-tenant storage. The **SDK** is a thin client: it builds queries, calls the registry, verifies publisher signatures, manages the approval + connection lifecycle, and enforces scopes. The SDK does **not** run a local embedding model for ranking (except as a client-side re-rank fallback for the MCP-Registry adapter, §11.3).

**Alternatives considered.**
- Fat client with local index: rejected — the index would be stale, large, and duplicated across every agent install. Ranking quality would lag.
- Hybrid (local cache + registry): the SDK does cache `ServerCard`s (TTL 300s) and a blocklist (TTL 60s), but ranking is always registry-side.

**Consequences.**
- ✅ Ranking quality is centralized and improves for everyone at once.
- ✅ The SDK stays small (no bundled model weights except the optional MCP-Registry re-rank).
- ⚠️ Discovery requires network. Offline search is not supported. `static_fallback_servers` covers air-gapped fallback but is not "search."
- ⚠️ Registry availability affects every agent. Mitigated by failover (ADR-007) and `static_fallback_servers`.

---

## ADR-005: Registry-agnostic via adapter layer

**Status:** Accepted (SPEC §4.5, §11)

**Context.** The MCP ecosystem already has multiple registries: the official MCP Registry, ARD (Agent Registry Directory), and walled-garden vendor registries. PHAROS Discovery should not be locked to the Pharos Registry.

**Decision.** A **`RegistryAdapter` interface** normalizes each registry's native API to the canonical `ServerCard` schema + search/approval contract. Adapters declare a `capabilities` set so the SDK can degrade gracefully (an adapter without `semantic_search` gets substring-only; one without `pricing` returns `pricing=None` without probing).

**Adapters by phase (SPEC §11, §15):**
- **Phase 1:** `PharosRegistryAdapter` (native) + `MCPRegistryAdapter` (official MCP Registry `/v0.1/servers` → canonical, with optional client-side semantic re-rank via bundled `all-MiniLM-L6-v2` ONNX).
- **Phase 2:** `ARDAdapter` (`POST /search` → canonical; `urn:air:` ↔ `urn:pharos:` ID conversion).
- **Phase 3:** `A2AAdapter` (discovery-only — maps `AgentCard` skills → capabilities, returns card to host, does NOT connect), `AGNTCYAdapter`, walled-garden bridges (require documented vendor API; no scraping).

**Cross-registry score comparison is illegal by spec (§11.4).** An ARD `score` (0–100) and a Pharos `pharos_score` (0.0–1.0) are produced by different ranking functions; merging them into a false-precision leaderboard is forbidden. ARD-sourced results get `pharos_score = None`; the original is preserved in `source_score`.

**Consequences.**
- ✅ The SDK is not coupled to any single registry's roadmap.
- ✅ New registries are added without touching the SDK core.
- ⚠️ Adapter quality varies. The `capabilities` set + "limited metadata" labeling keeps the user informed.

---

## ADR-006: OAuth via App Registration Inheritance (Phase 2)

**Status:** Accepted (SPEC §17). Designed in Phase 0, implemented in Phase 2. Phase 1 ships `auth.type: "none"` and `"api_key"` only, but the data model is forward-compatible.

**Context.** MCP adopted OAuth 2.1, but standard Dynamic Client Registration (DCR) has problems at scale: unbounded DB growth on authorization servers, client-expiry black holes, per-instance client ID proliferation, and `/register` DoS. On top of that, the standard redirect flow is a poor fit for agents: an agent holding tokens is a high-value target, and leaving the chat to log in breaks the agentic UX.

**Decision.** Two levels of registration:
1. **Agent Provider Registration (CIMD).** Agent providers (OpenAI, Anthropic, Cursor, ...) register ONCE with the Pharos Registry. The registry hosts the provider's Client ID Metadata Document (CIMD) at a stable signed URL. This establishes the agent provider's verified identity. It is NOT the `client_id` used against each MCP server's authorization server.
2. **Vendor App Registration Inheritance.** MCP server vendors pre-register an OAuth app with their own IdP and bundle that registration into `pharos.json` (`client_id`, endpoints, scopes, `consent_defaults` — **never `client_secret`**). When an agent installs the MCP server, it **inherits** the vendor's app registration. No user creates a new app registration. No agent calls `/register`.

Under this model, the `OAuthFlowHandler` **coordinates** rather than running a standard redirect flow: the MCP server runs the OAuth flow **server-side** (holds `client_secret`, exchanges the auth code, stores the token), and sends the SDK a **signed confirmation JWT** (not the token). The agent never holds an OAuth token.

**Alternatives considered.**
- Standard OAuth 2.1 redirect flow with agent-held tokens: rejected — agent is a high-value token target; leaving chat breaks UX.
- DCR per agent install: rejected — unbounded IdP DB growth; per-instance client ID proliferation; `/register` DoS.
- Registry as OAuth broker (registry holds tokens): rejected — registry becomes a single point of compromise for all OAuth tokens.

**Consequences.**
- ✅ `client_secret` never appears in the registry, agent, or SDK (only the MCP server's server-side config).
- ✅ Agent never holds an OAuth token — no in-memory token store to attack, no keychain entry to exfiltrate.
- ✅ One client ID per vendor per MCP server, shared across all installs. No `/register` calls.
- ✅ Inline OAuth via MCP Apps sandboxed iframe keeps the user in the chat.
- ⚠️ Server-side token holding means the MCP server can call the vendor API for any in-scope purpose (threat C7). Mitigations: explicit "any purpose within scopes" consent text, fine-grained scopes, registry static analysis of mirrored servers. Residual risk acknowledged.
- ⚠️ Requires host to support MCP Apps (inline iframe) or a system browser. Hosts with neither get `OAuthUnavailable` and must fall back to `api_key` if the server supports it.
- ⚠️ Revocation is best-effort-as-a-request: the SDK asks the MCP server to revoke, requires a signed `revocation_proof` within 60s, and falls back to surfacing the vendor's app-management URL if the server is unresponsive (H16).

See `docs/components/OAUTH_BROKERING.md` for the full flow.

---

## ADR-007: Registry failover with blackout window

**Status:** Accepted (SPEC §8.5, H7)

**Context.** `registry_urls` is an ordered list. When the primary registry is unavailable, the SDK should fall through to the next. But naive immediate retry creates thundering-herd and prevents the primary from recovering.

**Decision.** On 503/504/timeout from `registry_urls[i]`, mark it unhealthy for a **60-second blackout**, try `registry_urls[i+1]`. Re-probe after the blackout. If all registries are unhealthy and no cache is available, emit `DiscoveryDegraded` and the host MAY fall back to `static_fallback_servers` (which still enforce the approval gate).

**Consequences.**
- ✅ A flapping primary doesn't cause retry storms.
- ✅ `static_fallback_servers` provides an `/etc/hosts`-style escape hatch for critical infrastructure (approval gate preserved).
- ⚠️ During the blackout window, the primary is not retried even if it recovers. 60s is short enough to be acceptable.

---

## ADR-008: Domain-anchored URN IDs

**Status:** Accepted (SPEC §6.4.1, Appendix B)

**Context.** Server IDs need to be stable across endpoint moves, transport changes (stdio → HTTP), and registry migrations. An ID tied to a registry or endpoint breaks on migration.

**Decision.** Canonical server IDs are URNs anchored to the publisher's domain: `urn:pharos:<fqdn>:<namespace>/<name>` (e.g. `urn:pharos:acme.com:travel/flight-booking`). The `<fqdn>` matches the publisher's `did:web` domain. Original registry-native IDs are preserved in `source_urn` (e.g. `urn:air:...` for ARD-sourced results).

**Consequences.**
- ✅ ID survives endpoint/transport/registry migration.
- ✅ Publisher identity is intrinsic to the ID — typosquatting is visible (`acme.com` vs `acme-travel-deals.com`).
- ⚠️ Publisher domain change (e.g. rebrand) requires a deliberate succession: the old card is marked `status: "deprecated"` with `successor_id` pointing to the new publisher's card.

---

## ADR-009: Bounded timeouts on every I/O path

**Status:** Accepted (SPEC §8, H8)

**Context.** A dead or slow MCP server must never hang the agent. Agents are interactive; a 30-second hang is a broken UX.

**Decision.** Every I/O path has a bounded timeout:
- `search_timeout` 10s, `get_timeout` 10s, `connect_timeout` 10s
- `initialize_timeout` 10s (covers MCP `initialize` + `notifications/initialized`)
- `tool_call_timeout` 30s per `tools/call` (overridable per-call)
- `heartbeat_interval` 30s (HTTP/SSE only)
- `health_check_interval` 60s → `ConnectionLost` on failure
- `oauth_timeout` 120s (Phase 2 inline OAuth UI)
- `approval_timeout` 300s (host render callback)

**Consequences.**
- ✅ No unbounded awaits. A dead server fails fast.
- ✅ Per-call `timeout` override on `call_tool` lets the agent extend for known-slow operations.
- ⚠️ Long-running tool calls (>30s) require the agent to set a per-call timeout or poll.

---

## ADR-010: `pharos_score` is relevance, not trust

**Status:** Accepted (SPEC §6.3, §11.4)

**Context.** A single "score" conflating relevance and trust is dangerous — a highly-relevant malicious server would look safe.

**Decision.** `pharos_score` (0.0–1.0) is **relevance only** — how well the server matches the query. Trust is expressed by separate fields: `publisher.verified`, `trust.attestations`, `rating`. The approval prompt MUST display these independently. Cross-registry score comparison is forbidden (different ranking functions); ARD-sourced results get `pharos_score = None` with the original preserved as `source_score`.

**Consequences.**
- ✅ Trust and relevance are visually and programmatically distinct.
- ✅ The approval prompt can't be gamed by a highly-relevant malicious server.
- ⚠️ Users must be educated that a high `pharos_score` does not mean "safe." The approval prompt's risk tier (SDK-computed from scopes + verified + rating) carries the safety signal.

---

## Dual-SDK architecture diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                       AI AGENT RUNTIME                            │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │                 Pharos Discovery SDK                        │   │
│  │  ┌──────────┐  ┌──────────┐  ┌────────────────────┐        │   │
│  │  │ Search   │→ │ Approval │→ │ Connection Mgr     │        │   │
│  │  │ Client   │  │ Engine   │  │ (MCP lifecycle)    │        │   │
│  │  └────┬─────┘  └──────────┘  └─────────┬──────────┘        │   │
│  │       └────────────┬───────────────────┘                    │   │
│  │              ┌─────▼──────────────┐                         │   │
│  │              │ Registry Adapters  │                         │   │
│  │              │ Pharos│MCP│ARD│... │                         │   │
│  │              └────────────────────┘                         │   │
│  └────────────────────────────────────────────────────────────┘   │
│                          │ HTTPS (X-Pharos-Version)                │
└──────────────────────────┼─────────────────────────────────────────┘
                           ▼
        ┌──────────────────────────────────────────────┐
        │  PHAROS REGISTRY (ref) / MCP Registry / ARD   │
        └──────────────────────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────────────┐
        │     DISCOVERED MCP SERVERS                    │
        │   (stdio subprocess + remote HTTP/SSE)        │
        └──────────────────────────────────────────────┘

IDL source of truth:
                  ┌─────────────────────┐
                  │   TypeSpec IDL      │  idl/typespec/
                  └─────────┬───────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼                           ▼
   ┌────────────────────┐       ┌────────────────────┐
   │ Python emitter     │       │ TypeScript emitter │
   │ → pydantic v2      │       │ → TS types + zod   │
   │ → pharos-discovery │       │ → @pharos/discovery│
   └────────────────────┘       └────────────────────┘
              │                           │
              └──────────┬────────────────┘
                         ▼
              ┌────────────────────┐
              │ Conformance suite  │  conformance/
              │ (shared fixtures)  │  both SDKs must pass
              └────────────────────┘
```

---

## Discovery + approval flow (canonical happy path — SPEC §7.1)

```
1. Agent detects a capability gap          (host on_capability_gap callback)
2. Agent calls pharos.search(text=...)     (Search Client → Registry Adapter → registry)
3. SDK returns ranked ServerCards          (full metadata: publisher, auth, pricing, trust)
4. Agent renders approval card to user     (Approval Engine → host render callback)
5. User approves (or rejects/picks other)  (SDK mints signed ApprovalToken on approve)
6. SDK performs MCP initialize + tools/list; agent reports tool usage
```

**Non-negotiable invariant:** step 6 requires a valid `ApprovalToken` from step 5. The Connection Manager refuses `connect()` without one.

**Edge cases (SPEC §7.1.1):**
- Step 0: capability-gap detection is host logic, not SDK. SDK exposes `on_capability_gap(context) -> CapabilityGap | None`.
- Step 2.5: `QueryBuilder` normalizes intent; SDK MUST NOT inject unredacted PII into `query.text`.
- Step 3.5: `ApprovalRequest.selection_rationale` is mandatory (why this server was chosen).
- Step 3→4 empty results: emit `NoServersFound`; one broadened retry, then host decides.
- Step 4.5: multi-server plans use `PlanApprovalRequest`/`PlanApprovalToken` (batch consent, one modal).
- Step 6.5: post-call audit via `ToolUsageEvent` log (enforced for conformant SDK-using agents).

See `docs/components/DISCOVERY_FLOW.md` for the full walkthrough.

---

## Phase roadmap (SPEC §15)

| Phase | Scope |
|-------|-------|
| **0 — Spec & Spike** | SPEC.md, spike implementations, IDL draft, conformance suite design |
| **1 — MVP** | Both SDKs (Python + TS), search, ServerCard, approval flow with CLI renderer, ApprovalToken, publisher sig verification, blocklist, tool-usage logging, MCP Registry adapter, quickstart repo, conformance suite passing |
| **2 — OAuth & stdio** | App Registration Inheritance (§17), `OAuthFlowHandler`, inline OAuth via MCP Apps, stdio transport with sandboxing hooks, `ARDAdapter`, `trust_on_use` |
| **3 — Federation & A2A** | Federation (`auto`/`referrals`), `A2AAdapter` (discovery-only), `AGNTCYAdapter`, walled-garden bridges, reviews, sandboxing |

---

## References

- `SPEC.md` — canonical spec (v0.4.0)
- `.guides/architecture/OVERVIEW.md` — implementation-focused architecture overview
- `.guides/backend/PYTHON_GUIDE.md`, `.guides/backend/TYPESCRIPT_GUIDE.md` — per-language guides
- `.guides/security/SECURITY_GUIDE.md` — security model + threat table
- `docs/components/DISCOVERY_FLOW.md` — search → approve → connect walkthrough
- `docs/components/OAUTH_BROKERING.md` — OAuth App Registration Inheritance walkthrough
- `docs/api/PYTHON_API.md`, `docs/api/TYPESCRIPT_API.md` — public API reference
