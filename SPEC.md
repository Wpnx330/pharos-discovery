# Pharos Discovery — Technical Specification

**Version:** 0.2.0 (Draft)
**Status:** Pre-implementation
**Date:** July 19, 2026
**License:** MIT
**Repository:** https://github.com/Wpnx330/pharos-discovery

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Vision: The "Next Google" Thesis](#2-vision-the-next-google-thesis)
3. [Problem Statement: The Fragmented Landscape](#3-problem-statement-the-fragmented-landscape)
4. [Design Principles](#4-design-principles)
5. [Architecture Overview](#5-architecture-overview)
6. [The Discovery Protocol](#6-the-discovery-protocol)
7. [User Approval Flow Specification](#7-user-approval-flow-specification)
8. [Agent SDK Design](#8-agent-sdk-design)
9. [Transport Handling](#9-transport-handling)
10. [Security Model](#10-security-model)
11. [Compatibility Layer](#11-compatibility-layer)
12. [Comparison With Existing Approaches](#12-comparison-with-existing-approaches)
13. [Business Discovery: Getting Found by Agents](#13-business-discovery-getting-found-by-agents)
14. [MVP Scope vs. Future Features](#14-mvp-scope-vs-future-features)
15. [Development Roadmap](#15-development-roadmap)
16. [Appendices](#16-appendices)
17. [OAuth via App Registration Inheritance](#17-oauth-via-app-registration-inheritance)

---

## 1. Executive Summary

Pharos Discovery is a **provider-agnostic, embeddable client framework** that lets any AI agent — Claude, GPT, DeepSeek, Gemini, xAI, Zap, or a custom build — discover, evaluate, and connect to MCP (Model Context Protocol) services at runtime.

Today, agent providers are each building their own walled-garden discovery channels: Anthropic ships **Claude Connectors**, Microsoft ships **dynamic tool discovery** for M365 Copilot, and Google/Microsoft/Hugging Face have proposed the **ARD (Agentic Resource Discovery)** spec. Meanwhile the official **MCP Registry** exists as a thin community catalog with only case-insensitive substring search. The result is a fragmented landscape where a business that wants its MCP service to be discoverable by agents must publish to half a dozen incompatible directories, and agents must hard-code integrations with each.

Pharos Discovery replaces this fragmentation with **one thin, open, embeddable layer** that any agent can import and any compatible registry can serve. It is:

- **Provider-neutral** — not aligned with ARD, AGNTCY, or any single vendor's discovery vision; capable of federating across all of them. We are a **complementary superset to the official MCP Registry**: we read `server.json` and registry entries, and add discovery, consent, and OAuth via App Registration Inheritance (§17) on top.
- **Consent-first** — agents **never** connect to a discovered service without explicit user approval. No silent connections, ever.
- **Registry-agnostic** — ships with first-class support for the **Pharos Registry** (sister project, built in Rust) but speaks a documented HTTP API that any compliant registry can implement. Bridges to the official MCP Registry, ARD catalogs, and walled gardens are provided as adapters.
- **Thin and embeddable** — a Python and TypeScript client library, not a server. Agents embed it; registries serve it. (The Pharos Registry is a separate Rust project; the discovery SDKs just speak HTTP to the registry API, so the SDK language choice is independent of the registry implementation language.)

This document specifies the architecture, the discovery protocol agents call, the user-approval UX contract, the SDK surface, transport handling for stdio and HTTP/SSE MCP servers, the security model, the compatibility layer, and a phased roadmap.

---

## 2. Vision: The "Next Google" Thesis

> **MCP discovery is the next Google. Businesses will be found by agents, not by humans typing queries into search boxes.**

The web search economy was built on humans searching for documents. The agentic economy will be built on **agents searching for capabilities on behalf of humans**. A business that exposes its services as an MCP server is the agentic equivalent of a business with a website in 1998 — discoverable by a new class of automated client. A business that *isn't* discoverable by agents is invisible to the next generation of commerce.

For this economy to function, three things must exist:

1. **A neutral discovery layer** that no single vendor controls. If Google's ARD becomes the de-facto standard, discovery is captured by one company — repeating the search-engine monopoly. If Anthropic's Claude Connectors win, discovery is captured by one agent vendor. Pharos Discovery is built to be the **open, vendor-neutral alternative** that keeps the agentic web open.
2. **A way for businesses to publish** their MCP services once and be found by every agent — not ten times across ten walled gardens. The companion **Pharos Registry** project provides this; Pharos Discovery is the client side that agents embed.
3. **A consent layer** so that agents don't silently connect to arbitrary services on the user's behalf. Discovery without consent is a surveillance and security hazard. Pharos Discovery bakes user approval into the protocol — it is a first-class flow, not an afterthought.

We are deliberately **not adopting Google's ARD spec** as our north star. ARD is a strong technical proposal and we implement a compatibility adapter for it, but Pharos stays neutral — we want the agentic web to be multi-vendor, not a Google-led re-run of the open web's capture.

**Dual positioning.** Pharos Discovery is:
- **Neutral on ARD** — we implement an adapter (§11.4), not a commitment. ARD catalogs are federation peers.
- **A complementary superset to the official MCP Registry.** We consume the official registry's `server.json` / `/v0.1/servers` entries unchanged and add three layers the official registry deliberately omits: semantic + structured discovery, a consent/approval gate, and OAuth via App Registration Inheritance (§17). We do not compete with the official registry for the canonical record of which servers exist; we make that record discoverable, approvable, and connectable.

---

## 3. Problem Statement: The Fragmented Landscape

### 3.1 How agents discover MCP servers today

| Channel | Who owns it | How discovery works | Limitation |
|---|---|---|---|
| **Claude Connectors** | Anthropic | Remote MCP servers connected via the Messages API `connectors` parameter or admin-configured connectors. Anthropic curates a marketplace. | Walled garden; only Claude; only servers Anthropic permits. |
| **M365 Copilot dynamic tool discovery** | Microsoft | Declarative agents in Copilot resolve MCP server tools at runtime via a plugin manifest; tools are kept current without republishing the agent. Connector framework provides DLP zoning and managed auth. | Microsoft-ecosystem only; governed by M365 DLP policy. |
| **Cursor / IDE MCP config** | Cursor, others | Users manually add MCP servers to a JSON config file (`~/.cursor/mcp.json` or project `.mcp.json`). No runtime discovery. | Manual; no search; no business discovery. |
| **Official MCP Registry** | MCP community (Anthropic-hosted) | `GET /v0.1/servers` with a **case-insensitive substring `search` on server names** only. Registry explicitly states: "This is intentionally simple. For more advanced searching, use a subregistry." | No semantic search; no tool-level search; no auth/pricing metadata surfaced in the list API. |
| **mcp-gateway-registry (agentic-community)** | Open source (AWS-backed) | FAISS + sentence-transformer semantic search at `/api/search/semantic` over registered MCP servers, tools, and A2A agents. Sub-100ms similarity queries. | Server-side infra, not an embeddable client; gateway + registry control-plane model. |
| **ARD catalogs** | Google, Microsoft, Hugging Face | Publishers host `/.well-known/ai-catalog.json`; registries crawl and expose `POST /search` with semantic text + structured filters. v0.9 draft. | Proposal-stage; tied to ARD's URN identifier scheme and ai-catalog data model. |

The core problem: **a business must publish to all of these to be universally discoverable, and an agent must integrate with all of these to be universally capable.** This does not scale.

### 3.2 The gaps Pharos Discovery closes

- **One embeddable client, many registries.** Agents embed Pharos once and can search any compatible registry, including the Pharos Registry, the official MCP Registry (via adapter), ARD catalogs (via adapter), and walled gardens (via documented bridges).
- **Semantic + structured search at the client.** Agents query in natural language ("I need to book a flight and file an expense report") and get ranked, evaluated results — without each agent vendor reimplementing retrieval.
- **Consent as a protocol primitive.** Discovery returns enough metadata for the user to make an informed decision; the SDK enforces an approval gate before any connection is established.
- **Business discovery, not just tool discovery.** Pharos surfaces publisher identity, pricing, reviews, and capability manifests so businesses can be *found and chosen*, not just invoked.

---

## 4. Design Principles

1. **Provider-agnostic by construction.** The framework must work with any agent runtime and any registry that implements the Pharos Discovery API. No code path may assume a specific agent vendor.
2. **Consent is non-negotiable.** Agents MUST NOT establish an MCP connection to a discovered service without an explicit user approval event. The SDK exposes no `connect_without_approval` escape hatch.
3. **Thin client, fat registry.** Pharos Discovery is a client library. Ranking, embeddings, indexing, and publisher verification live in the registry (the Pharos Registry or any compatible one). The client is concerned with querying, presenting, approving, and connecting.
4. **Neutrality over allegiance.** We implement adapters for ARD, the official MCP Registry, and walled gardens, but we do not adopt any of them as canonical. The Pharos Discovery API is the canonical surface for agents.
5. **Registry-agnostic via a documented API.** Any registry implementing the Pharos Discovery HTTP API (§6) is a valid backend. The Pharos Registry is the reference implementation, not a dependency.
6. **Transport-agnostic MCP.** After discovery, the client connects to stdio and HTTP/SSE MCP servers using the MCP protocol's standard lifecycle. The discovery layer does not reinvent connection.
7. **Transparency by default.** Tool usage, connection events, and consent decisions are logged and surfaced to the user. Agents cannot silently use discovered services.
8. **Minimal dependencies.** The Python and TypeScript SDKs target a small dependency surface so they can be embedded in constrained agent runtimes (browser, edge, embedded LLM hosts).

---

## 5. Architecture Overview

### 5.1 Components

```
┌────────────────────────────────────────────────────────────────────┐
│                         AI AGENT RUNTIME                           │
│  (Claude / GPT / DeepSeek / Gemini / xAI / Zap / custom)           │
│                                                                    │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │                 Pharos Discovery SDK                         │ │
│   │  (Python `pharos-discovery` / TS `@pharos/discovery`)        │ │
│   │                                                              │ │
│   │  ┌────────────┐  ┌──────────────┐  ┌────────────────────┐   │ │
│   │  │  Search     │  │  Approval     │  │  Connection        │   │ │
│   │  │  Client     │  │  Engine       │  │  Manager           │   │ │
│   │  │             │→ │  (consent     │→ │  (MCP lifecycle)   │   │ │
│   │  │  query,     │  │   gate, UX    │  │  stdio + HTTP/SSE  │   │ │
│   │  │  rank,      │  │   callbacks)  │  │  initialize,       │   │ │
│   │  │  evaluate)  │  │              │  │  tools/list, call   │   │ │
│   │  └─────┬──────┘  └──────────────┘  └─────────┬──────────┘   │ │
│   │        │                                   │               │ │
│   │  ┌─────▼───────────────────────────────────▼──────────┐    │ │
│   │  │            Registry Adapter Layer                  │    │ │
│   │  │  PharosRegistry │ MCPRegistry │ ARD │ WalledGarden │    │ │
│   │  └────────────────────────────────────────────────────┘    │ │
│   └──────────────────────────────────────────────────────────────┘ │
│                              │                                     │
└──────────────────────────────┼─────────────────────────────────────┘
                               │ HTTPS
                               ▼
        ┌──────────────────────────────────────────────────┐
        │            PHAROS REGISTRY (reference)            │
        │  (sister project — or any compatible registry)    │
        │                                                  │
        │   ┌───────────┐  ┌──────────┐  ┌──────────────┐ │
        │   │  Search   │  │ Publisher│  │ Trust/Verify  │ │
        │   │  Index    │  │ API      │  │ (pub keys,    │ │
        │   │ (semantic │  │ (publish,│  │  attestations)│ │
        │   │  + filter)│  │  review) │  │               │ │
        │   └───────────┘  └──────────┘  └──────────────┘ │
        └──────────────────────────────────────────────────┘
                               │
                               ▼
        ┌──────────────────────────────────────────────────┐
        │          DISCOVERED MCP SERVERS                  │
        │   (stdio subprocesses + remote HTTP/SSE)         │
        └──────────────────────────────────────────────────┘
```

### 5.2 The four layers of the SDK

1. **Search Client** — builds queries (natural language text + structured filters), calls the registry, returns ranked `ServerCard` results with full metadata.
2. **Approval Engine** — takes ranked results, renders a user-facing approval prompt (callback-based so the host agent can style it), records the consent decision, and emits a signed `ApprovalToken` that the Connection Manager requires.
3. **Connection Manager** — takes an approved `ServerCard`, selects the transport (stdio / HTTP+SSE / streamable HTTP), performs the MCP `initialize` handshake, caches the live `Client` object, and exposes `tools/list` and `tools/call` to the agent.
4. **Registry Adapter Layer** — translates between the canonical Pharos Discovery API (§6) and the wire formats of specific registries: the Pharos Registry (native), the official MCP Registry (`/v0.1/servers`), ARD registries (`POST /search` with ai-catalog entries), and walled-garden bridges (vendor-specific, documented per adapter).

### 5.3 What lives where

| Concern | Lives in Pharos Discovery (client) | Lives in the registry |
|---|---|---|
| Natural-language query construction | ✅ | |
| Semantic ranking / embeddings | | ✅ |
| Structured filtering | ✅ (query side) | ✅ (evaluation) |
| Publisher verification (signatures, attestations) | ✅ (verification) | ✅ (issuance) |
| User approval UX | ✅ | |
| Consent logging | ✅ (local) + ✅ (registry audit, optional) | |
| MCP `initialize` handshake | ✅ | |
| Transport selection (stdio vs HTTP/SSE) | ✅ | |
| `tools/list`, `tools/call` | ✅ | |
| Business publishing workflow | | ✅ |
| Reviews, pricing metadata storage | | ✅ |
| Malicious-server blocklists | ✅ (consumer) | ✅ (source) |

---

## 6. The Discovery Protocol

The Pharos Discovery API is the canonical HTTP surface that agents call through the SDK. Any registry implementing these endpoints is a valid backend. The reference implementation is the Pharos Registry; adapters translate to/from the official MCP Registry and ARD.

### 6.1 Base URL & versioning

- The registry base URL is configurable per-client (`PharosClient(registry_url=...)`).
- All endpoints are prefixed `/v1/`. The SDK negotiates version via the `X-Pharos-Version` header.
- Content type: `application/json` for request/response bodies.

### 6.2 Authentication

The discovery API supports three auth modes, selected by the registry:

- **Anonymous** — public read-only search (default for the public Pharos Registry).
- **API key** — `Authorization: Bearer <key>`, for rate-limited or metered access.
- **OAuth 2.0 / OIDC** — for enterprise registries with per-user identity (enables per-user consent audit).

Auth is **only** for the discovery API. Authentication to a *discovered MCP server* is handled by the MCP server itself (see §9.4).

### 6.3 `POST /v1/search` — search for MCP servers

The primary discovery endpoint. Accepts a natural-language query and optional structured filters, returns ranked `ServerCard` results.

**Request:**

```json
{
  "query": {
    "text": "I need to book a flight to Tokyo and file the expense report",
    "filter": {
      "transport": ["stdio", "http+sse", "streamable-http"],
      "auth_required": ["none", "oauth"],
      "publisher_verified": true,
      "min_rating": 4.0,
      "tags": ["travel", "expense"],
      "capabilities": ["flight_search", "expense_filing"]
    }
  },
  "ranking": {
    "mode": "relevance",
    "diversify_by_publisher": true
  },
  "pagination": {
    "limit": 10,
    "cursor": null
  },
  "federation": "auto"
}
```

**Field reference:**

| Field | Type | Required | Description |
|---|---|---|---|
| `query.text` | string | yes | Natural-language description of the need. Used for semantic ranking. |
| `query.filter` | object | no | Structured constraints (see §6.3.1). |
| `ranking.mode` | enum | no | `relevance` (default), `popularity`, `verified_first`, `newest`. |
| `ranking.diversify_by_publisher` | bool | no | If true, collapse near-duplicate servers from the same publisher. Default `true`. |
| `pagination.limit` | int | no | Max results per page (default 10, max 50). |
| `pagination.cursor` | string | no | Opaque cursor for pagination. |
| `federation` | enum | no | `auto` (default), `referrals`, `none`. See §6.5. |

**Response:**

```json
{
  "results": [
    {
      "id": "urn:pharos:acme.com:travel:flight-booking",
      "display_name": "Acme Flight Booking",
      "description": "Search and book flights across 400+ airlines with live pricing.",
      "publisher": {
        "id": "did:web:acme.com",
        "name": "Acme Corp",
        "verified": true,
        "verification_method": "dns+signature"
      },
      "version": "2.1.0",
      "transport": ["http+sse"],
      "endpoint": "https://mcp.acme.com/flights/sse",
      "capabilities": ["flight_search", "flight_book", "itinerary_manage"],
      "tools_count": 7,
      "auth": {
        "type": "oauth",
        "secret_handling": "server_side",
        "app_registration": {
          "client_id": "acme-mcp-flights-prod",
          "auth_server_url": "https://auth.acme.com",
          "grant_types": ["authorization_code", "refresh_token"],
          "scopes": [
            {"name": "bookings:write", "description": "Create and modify bookings"},
            {"name": "profile:read", "description": "Read profile data"}
          ],
          "consent_defaults": ["bookings:write", "profile:read"],
          "redirect_uri_pattern": "https://mcp.acme.com/oauth/callback",
          "endpoints": {
            "authorization": "https://auth.acme.com/oauth/authorize",
            "token": "https://auth.acme.com/oauth/token",
            "revocation": "https://auth.acme.com/oauth/revoke",
            "jwks": "https://auth.acme.com/.well-known/jwks.json"
          }
        },
        "ui": {
          "resource_uri": "ui://oauth/login",
          "csp": "default-src 'self'; script-src 'self'; frame-ancestors 'self'"
        }
      },
      "availability": "mirrored",
      "pricing": {
        "model": "per_call",
        "price_usd": 0.002,
        "free_tier": "100 calls/month"
      },
      "rating": {
        "score": 4.6,
        "count": 1284
      },
      "trust": {
        "signature": "eyJ...",
        "attestations": ["SOC2-Type2", "GDPR"]
      },
      "representative_queries": [
        "book a one-way flight from NYC to Tokyo",
        "find the cheapest flight next Friday"
      ],
      "pharos_score": 0.94,
      "source_registry": "https://registry.pharos.dev"
    }
  ],
  "referrals": [],
  "pagination": {
    "next_cursor": "eyJ...",
    "has_more": true
  }
}
```

**`pharos_score`** is a 0.0–1.0 relevance score. Like ARD's `score`, it is **strictly an informational relevance metric** and MUST NOT be interpreted as a trust, safety, or compliance rating. Trust is evaluated independently via the `trust` and `publisher.verified` fields (see §10).

#### 6.3.1 Filter keys

Filters compose with AND across keys and OR within a key. Field paths are dot-separated for nested fields (e.g. `trust.attestations`). Registries SHOULD support the standard fields below; support for arbitrary `metadata.*` paths is registry-defined.

| Filter key | Type | Matches |
|---|---|---|
| `transport` | array | Any of `stdio`, `http+sse`, `streamable-http` |
| `publisher_verified` | bool | Publisher signature verification status |
| `publisher.id` | array | Publisher identifiers (DID, domain) |
| `auth_required` | array | Any of `none`, `api_key`, `oauth`, `mtls` |
| `min_rating` | number | Servers with rating ≥ value |
| `tags` | array | Any tag matches |
| `capabilities` | array | Any capability matches |
| `trust.attestations` | array | Any attestation type matches |
| `pricing.model` | array | Any of `free`, `per_call`, `subscription`, `revenue_share` |
| `availability` | array | Any of `mirrored`, `referenced`, `native` (§13.4) |
| `metadata.*` | array | Custom publisher metadata |

If a registry does not support a requested filter path, it returns `400` with `UNSUPPORTED_FILTER`. The SDK falls back to client-side filtering on the returned results where possible.

### 6.4 `GET /v1/servers/{id}` — get a single server card

Fetches the full `ServerCard` for a known ID (e.g. after the user selects from search results, or to refresh metadata before connection).

**Path param:** `id` — URL-encoded `urn:pharos:...` identifier.

**Query params:**
- `include_tools` (bool, default `false`) — include the full `tools/list` output if the registry has cached it.
- `include_reviews` (bool, default `false`) — include a sample of reviews.

**Response:** a single `ServerCard` object (same shape as a search result entry), optionally enriched.

### 6.5 Federation

Registries MAY federate. The client controls federation via the `federation` parameter:

- **`auto`** — the registry queries upstream registries, merges, and returns a unified ranked set. Client sees one result list.
- **`referrals`** — the registry returns its own results plus a `referrals` array of other registries the client may query. The SDK MAY follow referrals automatically (with a max depth, default 2) or surface them to the host agent.
- **`none`** — search only the registry's own index.

This mirrors ARD's federation model (§7.2 of the ARD spec) for compatibility. The difference: Pharos Discovery treats ARD registries, the official MCP Registry, and walled-garden bridges as **federation peers**, not as a canonical hierarchy.

### 6.6 `POST /v1/approve` — record consent (optional, registry-side)

When the host agent's approval UX completes, the SDK emits a local `ApprovalToken` (§7.4). Optionally, the SDK also POSTs the consent event to the registry for audit:

```json
{
  "server_id": "urn:pharos:acme.com:travel:flight-booking",
  "user_id_hash": "sha256:...",
  "agent_id": "claude-code/1.2.3",
  "approved_at": "2026-07-19T08:42:11Z",
  "approved_scopes": ["flight_search", "flight_book"],
  "approval_duration": "session"
}
```

The registry returns an `audit_id`. This is **opt-in per host agent** — privacy-preserving agents may keep consent purely local. The SDK supports both modes.

### 6.7 `GET /v1/servers/{id}/oauth` — OAuth metadata (Phase 2)

Returns the full OAuth/authorization configuration for a server, used by the SDK's `OAuthFlowHandler` (§17) to present the vendor's consent defaults and trigger the MCP server's inline OAuth UI (via MCP Apps). This endpoint is an optimization — the same fields are already embedded in the `ServerCard.auth` object (§6.3) — but it allows a client to refresh just the OAuth config (which may rotate, e.g. `endpoints.jwks` key rolls) without re-fetching the whole card.

**Response:**

```json
{
  "server_id": "urn:pharos:acme.com:travel:flight-booking",
  "auth": {
    "type": "oauth",
    "secret_handling": "server_side",
    "app_registration": {
      "client_id": "acme-mcp-flights-prod",
      "auth_server_url": "https://auth.acme.com",
      "grant_types": ["authorization_code", "refresh_token"],
      "scopes": [
        {"name": "bookings:write", "description": "Create and modify bookings"},
        {"name": "profile:read", "description": "Read profile data"}
      ],
      "consent_defaults": ["bookings:write", "profile:read"],
      "redirect_uri_pattern": "https://mcp.acme.com/oauth/callback",
      "endpoints": {
        "authorization": "https://auth.acme.com/oauth/authorize",
        "token": "https://auth.acme.com/oauth/token",
        "revocation": "https://auth.acme.com/oauth/revoke",
        "jwks": "https://auth.acme.com/.well-known/jwks.json"
      }
    },
    "ui": {
      "resource_uri": "ui://oauth/login",
      "csp": "default-src 'self'; script-src 'self'; frame-ancestors 'self'"
    }
  },
  "pharos_cimd_url": "https://registry.pharos.dev/v1/agents/{agent_provider_id}/cimd",
  "fetched_at": "2026-07-19T08:42:11Z",
  "expires_at": "2026-07-19T09:42:11Z"
}
```

The `pharos_cimd_url` field is the stable URL where the agent provider's Client ID Metadata Document (CIMD, §17.3) is hosted by the Pharos Registry. The CIMD establishes the *agent provider's* verified identity (used for agent authentication to the registry and for vendor-side agent allow-listing); it is **not** the `client_id` used against the MCP server's authorization server. Under App Registration Inheritance, the `client_id` for the per-server OAuth flow is the vendor's pre-registered `app_registration.client_id`, which the MCP server inherits and uses server-side.

### 6.8 `POST /v1/feedback` — reviews & reports

- `POST /v1/feedback/review` — submit a star rating + text review for a server.
- `POST /v1/feedback/report` — report a malicious or misbehaving server (feeds the registry's trust system and the SDK's local blocklist).

### 6.9 `POST /v1/publish` — business discovery (publisher-side)

Used by businesses to register their MCP service so it can be discovered. See §13.

### 6.10 Error codes

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `INVALID_ARGUMENT` | Malformed query or unsupported filter |
| 401 | `UNAUTHENTICATED` | Missing or invalid credentials |
| 403 | `PERMISSION_DENIED` | Authenticated but not allowed |
| 404 | `NOT_FOUND` | Unknown server ID |
| 429 | `RATE_LIMITED` | Too many requests |
| 503 | `REGISTRY_UNAVAILABLE` | Registry down; SDK should retry or fail over |
| 504 | `UPSTREAM_TIMEOUT` | Federated upstream timed out |

---

## 7. User Approval Flow Specification

The approval flow is the heart of Pharos Discovery's privacy and security model. **An agent must never connect to a discovered MCP server without an explicit user approval event.** This is enforced at the SDK level: the Connection Manager requires an `ApprovalToken` (§7.4) and refuses to proceed without one.

### 7.1 The six-step discovery-to-connection flow

```
 User request
      │
      ▼
1. Agent detects a capability gap
      │
      ▼
2. Agent calls pharos.search(text=...)
      │
      ▼
3. SDK returns ranked ServerCards with full metadata
      │
      ▼
4. Agent renders approval card to user (via host UX callback)
      │
      ▼
5. User approves (or rejects / picks a different server)
      │
      ▼
6. SDK performs MCP initialize + tools/list; agent reports tool usage
```

### 7.2 What the agent presents to the user

The approval card MUST surface — at minimum — the following fields from the `ServerCard`. The SDK provides a default renderer; host agents MAY override it.

**Required on every approval prompt:**

- `display_name` and `publisher.name`
- `publisher.verified` — a visible "verified" badge or "unverified — connect with caution" warning
- `description` — what the server does, in plain language
- `capabilities` — the concrete capabilities the agent intends to use (not necessarily all of them)
- `auth.type` and `auth.scopes` — what permissions the server is requesting
- **OAuth scope approval (when `auth.type == "oauth"`).** The approval prompt MUST enumerate the OAuth scopes being requested alongside the MCP capability scopes, in plain language. The user approves *two* things in a single consent act: (a) the connection to the server, and (b) the OAuth scopes that the MCP server will broker on the user's behalf. **Consent defaults come from the vendor** (`auth.app_registration.consent_defaults`) and are presented pre-checked; the user MAY expand or reduce the scope set before approving. The SDK records the final approved OAuth scope set in the `ApprovalToken.approved_oauth_scopes` field and passes only those scopes to the `OAuthFlowHandler` (§17). The handler MUST NOT request scopes the user did not approve. If the user narrows the OAuth scope set, the handler performs scope minimization (§17.4) before triggering the MCP server's inline OAuth UI. **Under App Registration Inheritance, the user never creates an OAuth app registration** — the vendor pre-registered the app and bundled it in `pharos.json`; the MCP server inherits that registration and brokers the flow server-side (§17).
- `pricing.model` and `pricing.price_usd` — what it will cost, if anything
- `trust.attestations` — compliance claims (SOC2, GDPR, etc.), shown as badges
- `rating.score` and `rating.count` — community signal
- The specific user request that triggered the discovery (so the user understands *why* the agent is asking)

**Recommended:**

- A link to the server's `documentationUrl`
- The last-known `version` and `updated_at`
- Any `representative_queries` so the user can sanity-check the server's purpose
- Whether the connection will persist for this session, this request, or indefinitely

### 7.3 Consent mechanics

Approval is a **specific, scoped, revocable** event:

- **Specific** — the user approves *one* server for *one* stated purpose, not "all future discovery."
- **Scoped** — the user sees the `auth.scopes` and `capabilities` being requested and can approve a subset. The SDK records the approved scope set in the `ApprovalToken`; the Connection Manager refuses tool calls outside the approved scopes. For OAuth servers, this applies *both* to MCP capability scopes *and* to OAuth scopes (§17.4) — the user may narrow either independently.
- **Revocable** — the user can revoke approval at any time via `pharos.revoke(server_id)`. The SDK tears down the connection and invalidates the token.
- **Duration-bound** — approval defaults to `session` scope. The user may choose `once` (single tool call) or `persistent` (remembered across sessions, encrypted locally). `persistent` requires a second confirmation.

### 7.4 The `ApprovalToken`

On approval, the SDK mints a local, signed `ApprovalToken`:

```json
{
  "token_id": "uuid",
  "server_id": "urn:pharos:acme.com:travel:flight-booking",
  "approved_scopes": ["flight_search", "flight_book"],
  "approved_capabilities": ["flight_search", "flight_book"],
  "approved_oauth_scopes": ["bookings:write", "profile:read"],
  "duration": "session",
  "approved_at": "2026-07-19T08:42:11Z",
  "expires_at": "2026-07-19T10:42:11Z",
  "user_id_hash": "sha256:...",
  "agent_id": "claude-code/1.2.3",
  "signature": "ed25519:..."
}
```

The Connection Manager requires this token before `initialize`. Tool calls outside `approved_scopes` are rejected with `SCOPE_NOT_APPROVED`.

### 7.5 UX patterns

The SDK exposes the approval flow as a **callback** so the host agent controls rendering. Three reference patterns are supported:

1. **CLI / terminal agents** (Claude Code, Cursor, custom CLIs) — the SDK renders an inline text card and prompts `[y/N/scope:...]`. Default for `stdio` agents.
2. **Chat / web agents** (ChatGPT, Claude.ai, Gemini web) — the SDK returns a JSON approval payload; the host renders a rich card with buttons. The host calls `pharos.resolve_approval(payload)` with the user's choice.
3. **Voice / headless agents** — the SDK reads a short spoken summary and requires a verbal "yes, approve <server name>" confirmation. Headless pipelines may provide a pre-approved scope set via config (with an explicit `headless_mode=true` flag that is logged).

### 7.6 What the agent reports back

After a successful connection and tool call, the agent MUST report to the user:

- Which server was connected
- Which tool(s) were called and with what arguments (tool-usage transparency)
- The result summary
- Any errors or scope denials

This is enforced via the SDK's `ToolUsageEvent` log, which the host agent surfaces in its output. Agents that suppress this log are non-conformant.

---

## 8. Agent SDK Design

Pharos Discovery ships as two first-party libraries, with identical surfaces:

- **Python**: `pharos-discovery` (PyPI) — Python 3.10+
- **TypeScript**: `@pharos/discovery` (npm) — Node 20+, browser-compatible build

### 8.1 Python surface

```python
from pharos_discovery import PharosClient, ApprovalRequest

# Initialize with the default public Pharos Registry
pharos = PharosClient(
    registry_url="https://registry.pharos.dev",
    agent_id="my-agent/0.1.0",
    # Optional: local consent store, blocklist, cache
    consent_store="~/.pharos/consent.json",
)

# 1. Search
results = pharos.search(
    text="I need to book a flight to Tokyo and file the expense report",
    filter={
        "transport": ["http+sse"],
        "publisher_verified": True,
        "min_rating": 4.0,
    },
    limit=5,
)

# 2. Evaluate (host-agent logic, outside the SDK)
best = results[0]  # agent ranks by pharos_score + its own reasoning

# 3. Request approval (callback-based UX)
approval = pharos.request_approval(
    server=best,
    purpose="Book a flight to Tokyo for the user's July 25 trip",
    requested_scopes=["flight_search", "flight_book"],
    requested_capabilities=["flight_search", "flight_book"],
    duration="session",
    # The host provides the renderer:
    render=present_to_user,  # async def present_to_user(req: ApprovalRequest) -> ApprovalResponse
)
if not approval.approved:
    return  # user said no

# 4. Connect (requires the ApprovalToken)
client = pharos.connect(approval)  # performs MCP initialize + capabilities negotiation

# 5. Use
tools = await client.list_tools()
result = await client.call_tool("flight_search", {
    "origin": "NYC", "destination": "TYO", "date": "2026-07-25"
})

# 6. Disconnect (and optionally revoke)
await client.close()
pharos.revoke(approval)  # invalidates the token
```

### 8.2 TypeScript surface

```typescript
import { PharosClient, ApprovalRequest } from "@pharos/discovery";

const pharos = new PharosClient({
  registryUrl: "https://registry.pharos.dev",
  agentId: "my-agent/0.1.0",
  consentStore: "~/.pharos/consent.json",
});

// 1. Search
const results = await pharos.search({
  text: "I need to book a flight to Tokyo and file the expense report",
  filter: {
    transport: ["http+sse"],
    publisherVerified: true,
    minRating: 4.0,
  },
  limit: 5,
});

// 2. Evaluate
const best = results[0];

// 3. Request approval
const approval = await pharos.requestApproval({
  server: best,
  purpose: "Book a flight to Tokyo for the user's July 25 trip",
  requestedScopes: ["flight_search", "flight_book"],
  requestedCapabilities: ["flight_search", "flight_book"],
  duration: "session",
  render: presentToUser,  // (req: ApprovalRequest) => Promise<ApprovalResponse>
});
if (!approval.approved) return;

// 4. Connect
const client = await pharos.connect(approval);

// 5. Use
const tools = await client.listTools();
const result = await client.callTool("flight_search", {
  origin: "NYC", destination: "TYO", date: "2026-07-25",
});

// 6. Disconnect
await client.close();
pharos.revoke(approval);
```

### 8.3 Core types

```python
# ServerCard (search result entry)
class ServerCard:
    id: str                       # urn:pharos:<publisher>:<ns>:<name>
    display_name: str
    description: str
    publisher: Publisher           # {id, name, verified, verification_method}
    version: str
    transport: list[str]           # ["stdio" | "http+sse" | "streamable-http"]
    endpoint: str | None           # URL for HTTP transports; None for stdio
    stdio_command: str | None      # e.g. "npx -y @acme/flights-mcp"
    capabilities: list[str]
    tools_count: int
    auth: AuthSpec                 # expanded OAuth config; see §17 and Appendix A
    availability: str              # "mirrored" | "referenced" | "native" (see §17.5)
    pricing: PricingSpec | None
    rating: RatingSpec | None
    trust: TrustSpec | None
    representative_queries: list[str]
    pharos_score: float            # 0.0–1.0 relevance; NOT a trust rating
    source_registry: str

# ApprovalRequest (handed to the host's render callback)
class ApprovalRequest:
    server: ServerCard
    purpose: str                   # why the agent is asking
    requested_scopes: list[str]
    requested_capabilities: list[str]
    duration: str                  # "once" | "session" | "persistent"
    render_id: str                 # for correlating async UX

# ApprovalResponse (returned by the host's render callback)
class ApprovalResponse:
    approved: bool
    approved_scopes: list[str]     # may be a subset of requested
    duration: str
    user_note: str | None

# ApprovalToken (issued by the SDK on approval; required by connect())
class ApprovalToken:
    token_id: str
    server_id: str
    approved_scopes: list[str]
    approved_capabilities: list[str]
    approved_oauth_scopes: list[str]   # OAuth scopes the user approved (§17.4); empty if auth.type != oauth
    duration: str
    approved_at: str
    expires_at: str
    signature: str                 # ed25519 over the token body

# MCPClient (returned by connect())
class MCPClient:
    server: ServerCard
    approval: ApprovalToken
    protocol_version: str
    server_capabilities: dict       # from initialize response
    async def list_tools() -> list[Tool]: ...
    async def call_tool(name: str, args: dict) -> ToolResult: ...
    async def list_resources() -> list[Resource]: ...
    async def read_resource(uri: str) -> str: ...
    async def list_prompts() -> list[Prompt]: ...
    async def close() -> None: ...

# OAuthFlowHandler — coordinates inline OAuth via App Registration Inheritance (§17).
# Agent providers implement this ONCE; it works for every MCP server.
# NOTE: Under App Registration Inheritance, the handler does NOT run a standard
# OAuth redirect flow. It reads the ServerCard.auth config, presents the vendor's
# consent defaults to the user, triggers the MCP server's inline OAuth UI (via
# MCP Apps), waits for the MCP server to complete the OAuth flow server-side,
# and receives a CONFIRMATION (not the token itself). The token stays with the
# MCP server, which proxies tool calls.
class OAuthFlowHandler:
    async def authorize(
        self,
        server: ServerCard,
        approval: ApprovalToken,
    ) -> OAuthResult: ...
    async def refresh(self, server: ServerCard) -> OAuthResult: ...
    def revoke_access(self, server_id: str) -> None: ...   # asks MCP server to revoke its server-side token
    def status(self, server_id: str) -> OAuthStatus: ...    # is the MCP server's server-side auth still valid?

# OAuthResult — returned by OAuthFlowHandler.authorize().
# Under App Registration Inheritance this carries a CONFIRMATION, not a token.
# The access_token / refresh_token fields are None when secret_handling == "server_side".
class OAuthResult:
    authorized: bool              # True if the MCP server completed the OAuth flow server-side
    access_token: str | None      # None when secret_handling == "server_side" (token stays in MCP server)
    token_type: str | None        # "Bearer" when token returned; None when server-side
    expires_in: int | None
    refresh_token: str | None     # None when server-side
    scope: list[str]              # scopes actually granted (may be a subset of approved_oauth_scopes)
    acquired_via: str             # "app_registration_inheritance" | "cimd" | "dcr" | "api_key" | "static"
    auth_held_by: str             # "mcp_server" | "agent"  — under §17, always "mcp_server"
    confirmed_at: str             # ISO8601 timestamp of the MCP server's auth-completed confirmation
```

### 8.4 Embedding model

The SDK is designed to be embedded in any agent runtime, not run as a sidecar:

- **Python**: importable as a library; async-first (anyio); no hard dependency on a specific LLM client library. Works with the Anthropic SDK, OpenAI SDK, raw HTTP, or a custom agent loop.
- **TypeScript**: ESM + CJS dual build; works in Node 20+ and modern browsers; no DOM dependency (the approval UX is host-supplied).
- **No daemon required.** The SDK is a library. There is no `pharosd` process. The only network calls are to the registry and to discovered MCP servers.

### 8.5 Configuration

```python
PharosClient(
    registry_url="https://registry.pharos.dev",
    agent_id="my-agent/0.1.0",
    api_key=None,                   # for metered registries
    consent_store="~/.pharos/consent.json",
    blocklist_url="https://registry.pharos.dev/v1/blocklist",
    cache_ttl_seconds=300,          # cache ServerCards locally
    federation_mode="auto",         # auto | referrals | none
    max_referral_depth=2,
    request_timeout_seconds=10,
    verify_signatures=True,         # verify publisher signatures (§10)
    allow_unverified=False,         # gate: refuse unverified publishers
    headless_mode=False,            # for automated pipelines
    on_tool_use=None,               # callback for tool-usage transparency
)
```

---

## 9. Transport Handling

After approval, the Connection Manager establishes a live MCP session with the discovered server. Pharos Discovery supports all standard MCP transports and handles the lifecycle itself; it does not reinvent the MCP wire protocol.

### 9.1 The MCP connection lifecycle

Per the MCP specification, every connection proceeds:

1. **Client sends `initialize`** with its `protocolVersion`, `capabilities`, and `clientInfo`.
2. **Server responds** with its chosen `protocolVersion`, `capabilities`, `serverInfo`, and optional `instructions`.
3. **Client sends `notifications/initialized`** — the handshake is complete.
4. **Operational phase** — `tools/list`, `tools/call`, `resources/read`, `resources/list`, `prompts/list`, `prompts/get`, etc., over JSON-RPC 2.0.
5. **Shutdown** — transport-specific teardown.

Pharos Discovery handles steps 1–3 internally and exposes the operational phase via `MCPClient` (§8.3).

### 9.2 Transport: stdio

For local MCP servers launched as subprocesses:

- The `ServerCard.stdio_command` field carries the launch command (e.g. `npx -y @acme/filesystem-mcp /Users/chris`).
- The SDK spawns the subprocess, writes JSON-RPC 2.0 messages to its stdin (newline-delimited), and reads responses from stdout.
- **Security**: stdio servers run with the user's privileges. The SDK logs every `tools/call` and enforces the `approved_scopes` from the `ApprovalToken`. The approval prompt MUST clearly state that a stdio server executes locally with the user's permissions.
- Stdio is the highest-trust transport *if* the publisher is verified and the command is audited; it is the highest-risk transport otherwise. The SDK's default `allow_stdio=True` can be disabled by privacy-conscious hosts.

### 9.3 Transport: Streamable HTTP and HTTP+SSE

For remote MCP servers:

- **Streamable HTTP** (MCP's recommended HTTP transport, 2025-03-26 spec) — a single endpoint accepting POST requests with JSON-RPC bodies; the server may respond inline or upgrade to SSE for streaming.
- **HTTP+SSE** (legacy) — a dedicated SSE endpoint for server-to-client messages plus a POST endpoint for client-to-server.
- The SDK negotiates automatically based on the `ServerCard.transport` and `endpoint` fields. No host-agent code required.
- **Security**: all remote connections use TLS 1.2+. The SDK pins the publisher's public key when `trust.signature` is present and `verify_signatures=True`.

### 9.4 Per-server authentication

Discovery returns the server's auth requirements in `ServerCard.auth`. The SDK does **not** store credentials. Auth flow:

1. `auth.type == "none"` — connect directly.
2. `auth.type == "api_key"` — the SDK calls the host's `credential_provider` callback (host-supplied) to fetch the key, then sets the appropriate header.
3. `auth.type == "oauth"` — the SDK delegates to the `OAuthFlowHandler` (§17). Under **App Registration Inheritance**, the handler does NOT run a standard OAuth redirect flow. Instead it: (a) retrieves the OAuth config from `ServerCard.auth.app_registration`; (b) presents the vendor's `consent_defaults` to the user (overridable — the user may expand or reduce the scope set); (c) triggers the MCP server's inline OAuth UI via MCP Apps (the MCP server returns an HTML login segment at `auth.ui.resource_uri`, rendered in a sandboxed iframe in the chat); (d) waits for the MCP server to complete the OAuth flow server-side — the MCP server holds the `client_secret` and exchanges the authorization code for a token itself; (e) receives a **confirmation** that auth succeeded (not the token). The token never reaches the agent or the SDK; the MCP server proxies all subsequent tool calls, attaching its server-side token. The `OAuthResult` returned to the agent carries `authorized=true` and `auth_held_by="mcp_server"` with `access_token=None`.
4. `auth.type == "mtls"` — the SDK uses a client certificate from the host's credential store.

**The approval prompt (§7.2) MUST display the requested auth scopes before the user approves.** Connecting a server that requests `profile:read` is a different consent decision than connecting one that requests `profile:read` + `payments:write`.

### 9.5 Connection pooling & lifecycle

- The SDK maintains at most one live `MCPClient` per `server_id` per session. Repeated `connect()` calls with a valid, non-expired `ApprovalToken` return the cached client.
- Connections are torn down on `client.close()`, on token expiry, on `pharos.revoke(token)`, and on process exit (best-effort).
- The SDK never reconnects automatically after a teardown without a fresh approval event.

---

## 10. Security Model

Discovery introduces a new attack surface: an agent connects to arbitrary internet services based on registry results. Pharos Discovery treats this as a first-class security problem.

### 10.1 Publisher verification

Every `ServerCard` carries a `publisher` object and an optional `trust` object. The SDK verifies:

1. **Domain anchoring** — the publisher's claimed domain (extracted from the `urn:pharos:<publisher>:...` ID) must match the domain in the publisher's DID (`did:web:acme.com` → `acme.com`).
2. **Signature** — if `trust.signature` is present, the SDK verifies it against the publisher's published public key (fetched from `https://<publisher>/.well-known/pharos-pubkey.json` or the registry's cached key). A failed signature check downgrades the card to `verified=false`.
3. **Attestations** — `trust.attestations` (e.g. `SOC2-Type2`, `HIPAA-Audit`) are displayed to the user but NOT treated as proof; they are claims the publisher makes, linked to URIs. The registry may independently verify attestations and mark them `registry_verified`.

**Default policy**: `verify_signatures=True`, `allow_unverified=False`. Hosts that want to allow unverified servers (e.g. local development) must explicitly set `allow_unverified=True`, which is logged.

### 10.2 Sandboxing

Pharos Discovery does not impose a specific sandbox, but it provides hooks for host-imposed isolation:

- **stdio servers** — the SDK accepts a `sandbox` config: `{"mode": "none" | "docker" | "firejail" | "nsjail" | "custom", "command": ...}`. When set, the stdio command is wrapped in the chosen sandbox before execution.
- **HTTP servers** — the SDK supports an `egress_allowlist` to restrict which hosts the agent may connect to (defense against SSRF-style abuse of discovered endpoints).
- **Tool-call scope enforcement** — the Connection Manager rejects `tools/call` for tools outside `approved_capabilities` and for auth scopes outside `approved_scopes`.

### 10.3 Malicious-server defense

- **Local blocklist** — the SDK fetches and caches a registry-provided blocklist of known-malicious server IDs. Connections to listed servers are refused before any network call.
- **Behavioral logging** — every `tools/call` is logged locally (with arguments, by default redacted for sensitive params). Anomalously large argument payloads, repeated calls to the same tool, or calls to tools not declared in `tools/list` trigger warnings surfaced via `on_tool_use`.
- **Report pipeline** — `pharos.report_server(server_id, reason)` submits a report to the registry and adds the server to the local blocklist for the session.

### 10.4 User consent logging

- Every approval, rejection, and revocation is recorded in the local consent store with a timestamp, the server ID, the approved scopes, and the `agent_id`.
- The store is append-only and signed with a local key so tampering is detectable.
- Hosts may opt to mirror consent events to the registry (`POST /v1/approve`, §6.6) for cross-device audit, with `user_id_hash` only (never raw user IDs).

### 10.5 OAuth security (Phase 2, see §17)

OAuth under App Registration Inheritance has a fundamentally smaller attack surface than a redirect-flow model because the agent and SDK **never handle tokens or secrets**. The MCP server brokers everything server-side. The SDK mitigates the residual surface:

- **Secret isolation (key security property).** The vendor's `client_secret` is NEVER present in the registry, the agent, or the SDK. It lives only in the MCP server's server-side configuration, bundled via `pharos.json` at build time and never serialized into a `ServerCard`. The `ServerCard.auth` object carries the `app_registration` metadata (`client_id`, endpoints, scopes, consent defaults) but MUST NOT carry `client_secret`. The agent never sees the secret; the SDK never sees the secret; the registry never sees the secret. Compromise of the agent or SDK cannot leak OAuth credentials.
- **Token isolation.** Because the MCP server runs the OAuth flow server-side and proxies tool calls, the access token and refresh token never reach the agent runtime. There is no in-memory token store in the SDK to attack, no OS keychain entry to exfiltrate, and no token in logs. The `OAuthResult` returned to the agent is a boolean confirmation plus the granted scope set — never the token. Revocation is a request to the MCP server (`OAuthFlowHandler.revoke_access`), which tears down its server-side session.
- **Inline OAuth UI security (MCP Apps).** The inline login form is rendered in a **sandboxed iframe** per the MCP Apps extension spec. All communication between the host agent and the iframe is **JSON-RPC over `postMessage`** with an explicit origin check. The `auth.ui.csp` field in the `ServerCard` declares the vendor's Content Security Policy; hosts SHOULD enforce it and MAY block any inline UI whose effective CSP is more permissive than declared, or that attempts network access outside `auth.app_registration.endpoints`. Hosts MAY refuse to render inline OAuth UI at all (falling back to a "connect in vendor's own app" prompt) for high-security deployments.
- **Agent identity verification before OAuth.** Before any OAuth flow begins, Pharos CIMD (§17.3) establishes the *agent provider's* verified identity. Vendors MAY configure their MCP server to accept OAuth flows only from specific agent providers (an allow-list checked against the CIMD-verified provider ID), and MAY revoke access for a provider globally. This means a malicious agent cannot trigger an OAuth flow against a vendor's IdP without first presenting a verifiable provider identity.
- **SSRF prevention when fetching CIMD metadata.** When the MCP server (or, in legacy paths, the SDK) fetches an agent's Client ID Metadata Document (§17.3), the fetcher MUST NOT issue requests to internal/loopback/link-local addresses. The SDK validates fetched URLs against an egress allowlist (the same `egress_allowlist` used for §10.2) before any HTTP call. Redirect chains are followed with a max depth of 3 and each hop is re-validated.
- **CIMD metadata integrity.** The Pharos Registry serves CIMD documents over HTTPS with a stable, signed URL. The SDK MUST verify the TLS certificate chain and pin the registry's public key when `verify_signatures=True`. CIMD documents are cached locally with a short TTL (default 1 hour); stale cache is rejected if the registry signals key rotation.
- **Scope minimization.** The `OAuthFlowHandler` passes to the MCP server only the scopes in `ApprovalToken.approved_oauth_scopes` — never the full set advertised by the vendor. If the authorization server grants a narrower set than requested, the MCP server reports the *actual* granted scopes back to the agent, and the Connection Manager enforces tool calls against those, not the requested set.
- **DCR hygiene (legacy fallback only).** App Registration Inheritance is the preferred path; DCR is a fallback for vendors who did not pre-register an app. When DCR is used, the MCP server (not the agent) generates a fresh PKCE verifier per flow, discards the registered `client_id` after the session unless the user opts into `persistent` duration, and rate-limits DCR calls (max 1 per server per 5 minutes) to avoid the unbounded-DB-growth problem that motivated CIMD.
- **Token leak prevention.** Because tokens are server-side, the agent has no token to leak. The `on_tool_use` callback receives redacted auth headers. `OAuthResult` objects are not serializable into logs by default and carry no secret material.

### 10.6 Security for business adoption

Businesses (MCP providers like Salesforce, Stripe, SAP) will not expose their services via Pharos unless it is safe. The following properties are designed for enterprise adoption:

- **Secret isolation.** `client_secret` never in registry, agent, or SDK — always server-side in the MCP server (§10.5). This is the single most important property for business trust: a business can publish a ServerCard without exposing any credential material.
- **Audit trail.** Every discovery, approval, connection, OAuth authorization, and tool call is logged. Local logs (SDK-side) are append-only and signed; registry-side audit (opt-in, §6.6) records approval events with `user_id_hash` only. Vendors receive connection and tool-call events from their MCP server.
- **Agent identity verification.** Pharos CIMD (§17.3) establishes verified agent provider identity *before* any OAuth flow begins. A vendor's MCP server can refuse to start an OAuth flow for an unverified or non-allow-listed agent provider.
- **Vendor control.** Vendors set `consent_defaults` (the pre-checked OAuth scopes), MAY require specific agent providers via an allow-list, MAY scope `redirect_uri_pattern` to their own MCP server, and CAN revoke a provider's access globally without a client-side update. Vendors retain full control of their IdP app registration.
- **Inline OAuth security.** MCP Apps sandboxes the login UI in iframes with JSON-RPC-over-`postMessage` communication and a declared CSP. Hosts can block suspicious UI, and high-security deployments can refuse inline OAuth entirely and fall back to the vendor's native app.

### 10.7 Threat model (summary)

| Threat | Mitigation |
|---|---|
| Malicious server listed in registry | Publisher signature verification + blocklist + user approval gate |
| Typosquatting publisher names | Domain-anchored URN IDs + `publisher_verified` badge in UX |
| Agent silently connects | Approval gate is enforced in SDK; no bypass API |
| Tool calls outside consent | `approved_scopes` enforced in Connection Manager |
| Exfiltration via tool args | Local egress allowlist + tool-call logging + redaction |
| Compromised registry | Signatures verified against publisher's own published keys, not the registry's |
| Stale/revoked servers | Registry `status` field (`active`/`deprecated`/`deleted`); SDK re-checks before connect |
| OAuth scope creep | Scopes shown in approval prompt; vendor `consent_defaults` pre-checked but user may reduce; only approved scopes passed to MCP server |
| OAuth SSRF via CIMD/metadata fetch | Egress allowlist enforced on all OAuth metadata fetches; redirect depth capped at 3 |
| OAuth token theft | Tokens stay server-side in the MCP server; agent/SDK never receive the token; nothing to exfiltrate |
| OAuth `client_secret` leak | Secret is never in registry, agent, or SDK — only in the MCP server's server-side config; `ServerCard.auth` MUST NOT carry `client_secret` |
| Per-instance client ID proliferation | Vendor pre-registers one app via App Registration Inheritance; all installs of the MCP server inherit the same `client_id` |
| Malicious agent triggers OAuth | Pharos CIMD verifies agent provider identity first; vendors MAY allow-list providers and refuse unverified agents |
| DCR endpoint DoS / DB growth (legacy fallback) | DCR is a fallback path only; MCP server rate-limits DCR; ephemeral client IDs; App Registration Inheritance avoids `/register` entirely |

---

## 11. Compatibility Layer

Pharos Discovery is registry-agnostic via adapters. Each adapter implements the canonical `ServerCard` schema (§8.3) and the search/approval contract, translating to and from the native registry API.

### 11.1 Adapter interface

```python
class RegistryAdapter:
    name: str                              # "pharos" | "mcp-official" | "ard" | "claude-connectors"
    async def search(query: SearchQuery) -> list[ServerCard]: ...
    async def get(server_id: str) -> ServerCard: ...
    async def publish(card: ServerCard) -> str: ...
    async def report(server_id: str, reason: str) -> None: ...
    def to_canonical(native: dict) -> ServerCard: ...
    def from_canonical(card: ServerCard) -> dict: ...
```

### 11.2 Native: Pharos Registry

The reference adapter. No translation; speaks the §6 API natively. Supports federation, publisher verification, reviews, and pricing metadata out of the box.

### 11.3 Official MCP Registry adapter

Translates between the canonical `ServerCard` and the official registry's `/v0.1/servers` schema.

- `GET /v0.1/servers?search=<text>` → the adapter maps the substring search to the canonical `query.text` and performs **client-side semantic re-ranking** (using a small local embedding model or the host's LLM) since the official registry explicitly does not provide semantic search.
- `GET /v0.1/servers/{name}/versions/{version}` → maps to `GET /v1/servers/{id}`.
- Missing fields (pricing, reviews, ratings) are returned as `None`; the SDK degrades gracefully and labels such results as "limited metadata" in the approval UX.
- Publisher verification uses the official registry's namespace-based auth (GitHub OAuth for `io.github.*`, DNS for domain namespaces) mapped to the `publisher.verified` field.

### 11.4 ARD adapter

Translates between canonical `ServerCard`s and ARD catalog entries (`application/mcp-server-card+json`).

- ARD `POST /search` (per §7.2 of the ARD spec) → the adapter sends the ARD query shape and maps results back, converting `urn:air:<publisher>:<ns>:<name>` identifiers to `urn:pharos:...` canonical IDs (preserving the original via a `source_urn` field).
- ARD's `score` (0–100) is normalized to `pharos_score` (0.0–1.0). As in ARD, this is a relevance metric, not a trust rating.
- ARD `trustManifest` (identity, attestations, provenance) maps directly to the canonical `trust` and `publisher` objects.
- ARD federation (`auto`/`referrals`/`none`) passes through unchanged — Pharos and ARD share the same federation model by design.
- ARD's `representativeQueries` field maps to `representative_queries`.

This adapter makes Pharos Discovery a **superset client** of ARD: any ARD-compliant registry is searchable via Pharos, but Pharos adds the approval-gated connection layer and the cross-registry federation that ARD leaves to orchestrators.

### 11.5 AGNTCY adapter (planned)

AGNTCY (Linux Foundation Internet of Agents) provides discovery, identity, messaging, and observability for multi-agent systems. The AGNTCY adapter maps AGNTCY's agent registry schema to canonical `ServerCard`s, treating AGNTCY-registered agents as discoverable MCP-compatible services where applicable. Planned for Phase 3 (§15).

### 11.6 A2A adapter (planned)

Agent2Agent (A2A) publishes `AgentCard` JSON documents at `/.well-known/agent-card.json` describing an agent's `name`, `description`, `version`, `url`, `skills`, `defaultInputModes`, `defaultOutputModes`, and `authentication`. The A2A adapter:

- Crawls/queries A2A agent cards and maps each `skill` to a canonical `capability`.
- Surfaces A2A agents as discoverable resources, with the approval flow noting that the connection is to an A2A agent (JSON-RPC 2.0 over HTTP) rather than an MCP server.
- This is the bridge that makes Pharos Discovery a **unified discovery layer for both MCP tools and A2A agents**, not just MCP.

### 11.7 Walled-garden bridges

For vendor registries that do not expose a public search API (Claude Connectors marketplace, MS Copilot connector store):

- Bridges are **read-only** and **best-effort**. They scrape or use vendor-provided listing APIs where terms permit.
- Results are labeled `"source": "claude-connectors"` (etc.) and marked `limited_metadata=true`.
- The SDK does not attempt to bypass authentication or terms of service. If a vendor's ToS forbids programmatic listing, that bridge is not shipped.
- The long-term bet is that vendors adopt open discovery (ARD or Pharos) and bridges become unnecessary. Until then, bridges extend coverage without being a dependency.

---

## 12. Comparison With Existing Approaches

| Dimension | **Pharos Discovery** | **ARD (Google/MS/HF)** | **AGNTCY** | **A2A** | **Claude Connectors** | **M365 Copilot dynamic discovery** | **Official MCP Registry** | **mcp-gateway-registry** |
|---|---|---|---|---|---|---|---|---|
| What it is | Embeddable client SDK | Discovery spec + manifest format | Open infra stack (IoA) | Agent-to-agent protocol | Vendor marketplace | Vendor plugin runtime | Public catalog | Gateway + registry server |
| Scope | MCP + A2A discovery + connection | Discovery only (pre-invocation) | Discovery + identity + messaging + observability | Agent interop (not discovery) | MCP for Claude | MCP for Copilot | MCP catalog | MCP gateway/control plane |
| Provider-agnostic | ✅ (core goal) | ✅ (spec is neutral) | ✅ | ✅ | ❌ Claude only | ❌ M365 only | ✅ | ✅ |
| Embeddable client | ✅ Python + TS | ❌ (spec only) | Partial | ❌ (protocol) | ❌ | ❌ | ❌ | ❌ (server-side) |
| Semantic search | Via registry | Via registry | Via registry | N/A | ❌ | ❌ | ❌ (substring only) | ✅ FAISS |
| User approval gate | ✅ enforced in SDK | ❌ (out of scope) | ❌ | ❌ | Vendor-managed | Vendor-managed (DLP) | ❌ | ❌ |
| Consent logging | ✅ local + optional registry | ❌ | ❌ | ❌ | Vendor-managed | Vendor-managed | ❌ | ❌ |
| Publisher verification | ✅ signatures + attestations | ✅ trustManifest | ✅ identity layer | ✅ Agent Card | Vendor-curated | Vendor-curated | Namespace auth | Configured |
| Federation | ✅ auto/referrals/none | ✅ same model | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Business discovery (publish-once) | ✅ via Pharos Registry | ✅ via `ai-catalog.json` | ✅ | ❌ | ❌ | ❌ | ✅ (publish to registry) | ✅ |
| Pricing/reviews metadata | ✅ first-class | Via Schema.org ext | ❌ | ❌ | Vendor-specific | ❌ | ❌ | ❌ |
| Transport handling (stdio + HTTP/SSE) | ✅ | ❌ (pre-invocation) | ❌ | ❌ | ✅ (Claude-managed) | ✅ (Copilot-managed) | ❌ | ✅ (gateway) |
| Inline OAuth via MCP Apps (no chat-leaving redirect) | ✅ via App Registration Inheritance + MCP Apps (§17) | ❌ | ❌ | ❌ | Per-server, Anthropic-managed | Per-server, MS-managed | ❌ | ❌ |
| OAuth brokering (no per-server app registration by user/agent) | ✅ via App Registration Inheritance — vendor pre-registers, MCP server inherits (§17) | ❌ | ❌ | ❌ | Per-server, Anthropic-managed | Per-server, MS-managed | ❌ | ❌ |
| Status | Pre-implementation | v0.9 draft | Active | v1.x | Shipping | Shipping | Shipping | Shipping |

**Key differentiators of Pharos Discovery:**

1. **It is a client, not a spec or a server.** ARD is a specification (someone else implements it); the official MCP Registry is a server (someone else queries it); mcp-gateway-registry is server infra. Pharos Discovery is the **embeddable client** that agents actually import.
2. **Consent is in the protocol, not out of scope.** ARD explicitly scopes itself to "before invocation." Claude Connectors and Copilot handle consent vendor-side. Pharos bakes the approval gate into the SDK with no bypass.
3. **Neutrality by design.** ARD is Google-led; AGNTCY is Cisco/Linux Foundation; Claude Connectors is Anthropic. Pharos is positioned as the neutral middle — implementing adapters for all of them, canonicalizing none.
4. **Business metadata is first-class.** Pricing, reviews, and publisher identity are core fields, not Schema.org extensions. This reflects the "next Google" thesis: businesses are being discovered, not just tools.
5. **OAuth via App Registration Inheritance solves the MCP auth bootstrap problem — without the agent ever handling a token.** MCP adopted OAuth 2.1, but every agent provider currently must implement OAuth flows for *every* MCP server, each potentially using a different authorization server, and each requiring a per-server app registration or a DCR dance. This does not scale. Pharos Discovery's model (§17) has two levels: (a) agent providers register *once* with the Pharos Registry to establish a verified CIMD identity, and (b) **MCP server vendors pre-register an OAuth app with their IdP and bundle that registration into `pharos.json`** — so when an agent installs the MCP server, it *inherits* the app registration. No user creates a new app registration. The MCP server (not the agent) then runs the OAuth flow server-side, holding the `client_secret` and the resulting token, and proxies tool calls. The login UI is rendered **inline in the chat** via the MCP Apps extension (sandboxed iframe, JSON-RPC over `postMessage`) — the user never leaves the chat. The agent and SDK never see the token or the secret. This is the single largest differentiator against Claude Connectors and M365 Copilot, both of which handle OAuth per-server on the vendor side and require leaving the chat for login.

---

## 13. Business Discovery: Getting Found by Agents

For the agentic economy to work, businesses must be able to publish their MCP services once and be found by every agent. Pharos Discovery defines the client side; the **Pharos Registry** (sister project) defines the publishing side. The interface between them is `POST /v1/publish` (§6.8).

### 13.1 The publish flow

```
Business (MCP server operator)
   │
   │  1. Build an MCP server exposing their service
   │  2. Author a ServerCard (JSON)
   │  3. Sign it with their publisher private key
   │  4. POST /v1/publish to the Pharos Registry
   │     (or: host /.well-known/pharos-catalog.json for crawl-based ingestion)
   ▼
Pharos Registry
   │
   │  5. Verify publisher domain (DNS challenge or did:web)
   │  6. Verify signature
   │  7. Index capabilities + representative_queries for semantic search
   │  8. Surface in search results to all agents using Pharos Discovery
   ▼
Agents (everywhere)
   │
   │  9. Agent receives a user request → searches → finds the business
   │ 10. Presents the business's ServerCard to the user
   │ 11. User approves → agent connects → business serves the request
   ▼
User gets the capability they needed; business got found by an agent.
```

### 13.2 The publish payload

```json
{
  "id": "urn:pharos:acme.com:travel:flight-booking",
  "display_name": "Acme Flight Booking",
  "description": "Search and book flights across 400+ airlines with live pricing.",
  "publisher": {
    "id": "did:web:acme.com",
    "name": "Acme Corp",
    "contact": "api@acme.com"
  },
  "version": "2.1.0",
  "transport": ["http+sse"],
  "endpoint": "https://mcp.acme.com/flights/sse",
  "capabilities": ["flight_search", "flight_book", "itinerary_manage"],
  "auth": {
    "type": "oauth",
    "secret_handling": "server_side",
    "app_registration": {
      "client_id": "acme-mcp-flights-prod",
      "auth_server_url": "https://auth.acme.com",
      "grant_types": ["authorization_code", "refresh_token"],
      "scopes": [
        {"name": "bookings:write", "description": "Create and modify bookings"},
        {"name": "profile:read", "description": "Read profile data"}
      ],
      "consent_defaults": ["bookings:write", "profile:read"],
      "redirect_uri_pattern": "https://mcp.acme.com/oauth/callback",
      "endpoints": {
        "authorization": "https://auth.acme.com/oauth/authorize",
        "token": "https://auth.acme.com/oauth/token",
        "revocation": "https://auth.acme.com/oauth/revoke",
        "jwks": "https://auth.acme.com/.well-known/jwks.json"
      }
    },
    "ui": {
      "resource_uri": "ui://oauth/login",
      "csp": "default-src 'self'; script-src 'self'; frame-ancestors 'self'"
    }
  },
  "availability": "mirrored",
  "pricing": {
    "model": "per_call",
    "price_usd": 0.002,
    "free_tier": "100 calls/month",
    "billing_url": "https://acme.com/billing"
  },
  "representative_queries": [
    "book a one-way flight from NYC to Tokyo",
    "find the cheapest flight next Friday to SFO"
  ],
  "documentation_url": "https://docs.acme.com/mcp",
  "tags": ["travel", "flights", "booking"],
  "trust": {
    "signature": "ed25519:...",
    "attestations": [
      {"type": "SOC2-Type2", "uri": "https://trust.acme.com/soc2.pdf"}
    ]
  }
}
```

### 13.3 The "publish once, found everywhere" guarantee

A business that publishes to the Pharos Registry is discoverable by:

- Any agent embedding the Pharos Discovery SDK (native).
- Any ARD-compliant orchestrator, via the registry's ARD-compatible `/search` endpoint (the Pharos Registry exposes an ARD facade).
- The official MCP Registry, via an optional sync adapter that mirrors published servers to `registry.modelcontextprotocol.io`.
- Any downstream registry that federates with Pharos (via the `referrals` model).

This is the core value proposition for businesses: **one publish, every agent.**

### 13.4 Availability & tarball mirroring

The Pharos Registry mirrors npm and PyPI tarballs for the MCP servers it indexes. This matters for discovery because an agent or user discovering a server needs to trust that the server will still be available when they go to connect — especially for stdio servers launched from a package (`npx -y @acme/flights-mcp`), where an unpublished or yanked upstream package means a discovered server silently vanishes.

The `ServerCard.availability` field (Appendix A) captures this:

| Value | Meaning | Discovery implication |
|---|---|---|
| `mirrored` | The Pharos Registry holds a copy of the server's tarball (npm/PyPI) or a cached HTTP snapshot. Guaranteed retrievable. | Highest availability. Agents can install/connect even if upstream disappears. Shown with a "mirrored" trust badge. |
| `referenced` | The registry indexes the server but points at the upstream package/endpoint. Availability depends on upstream. | Standard. The card links to upstream; if upstream vanishes, the card is marked `status: deleted` on next re-index. |
| `native` | The server is published directly to the Pharos Registry (first-party), not via npm/PyPI. | The registry is the source of truth; availability is the registry's own SLA. |

The SDK surfaces `availability` in the approval prompt so the user can see whether they're connecting to a mirrored, guaranteed-available server or one that may disappear with upstream changes. The filter `availability` (array) is supported in `POST /v1/search` (§6.3.1).

**Highest-trust combination.** A `mirrored` server whose `auth` includes an `app_registration` block (vendor pre-registered OAuth, §17) is the highest-trust combination Pharos surfaces: the server's package is guaranteed retrievable by the Pharos Registry, and the OAuth flow requires no per-server app registration by the user or agent — the vendor pre-registered, the MCP server inherits, and the `client_secret` never leaves the server side. A `referenced` server with only DCR support (legacy fallback) is the lowest-trust OAuth path: the server may vanish upstream, and the connection requires an ephemeral DCR registration. The SDK surfaces both signals so the user can make an informed consent decision.

### 13.5 Rate limiting (registry-side)

The Pharos Registry (sister project) implements npm/PyPI-style rate limiting designed to keep discovery free and open while protecting the service from abuse:

- **Reads (search, `GET /v1/servers/{id}`, OAuth metadata) — generous and CDN-cached.** Unauthenticated read traffic is served from the CDN edge wherever possible and subject to generous per-IP limits. The SDK SHOULD cache `ServerCard` responses locally (`cache_ttl_seconds`, default 300s) to minimize repeat reads.
- **Search — generous limits.** Search is the primary read path and gets the most generous limits, with burst tolerance. Abuse is mitigated via the CDN + per-IP token bucket, not by gating legitimate agent traffic.
- **Publishes — authenticated, fair-use.** `POST /v1/publish` and `POST /v1/agents/register` require authentication and are subject to fair-use per-publisher limits to prevent registry spam.
- **Feedback (reviews/reports) — authenticated, lower limits** to prevent review bombing.

**SDK behavior on `429 RATE_LIMITED`.** The Discovery SDK MUST handle `429` responses gracefully:

1. Honor the `Retry-After` header when present; otherwise use exponential backoff with full jitter (initial 500ms, factor 2, cap 30s, max 4 retries).
2. On repeated `429` for the same query, fall back to **cached results** from the local `ServerCard` cache (`cache_ttl_seconds`) and surface a `registry_degraded` flag to the host agent so the user can be told results may be stale.
3. NEVER fail a discovery flow silently — if both the registry and the cache are exhausted, raise a `RegistryUnavailable` error to the host rather than returning empty results as if none exist.

### 13.6 Discovery as the new SEO

Pharos Discovery treats `representative_queries` as the agentic analog of SEO keywords. Businesses that author high-quality representative queries get ranked higher for relevant natural-language agent searches. The registry uses these (plus the description and capabilities) to build semantic embeddings. Businesses that omit them will be under-discovered — the agentic equivalent of a page with no `<title>`.

---

## 14. MVP Scope vs. Future Features

### 14.1 MVP (Phase 1)

The MVP delivers the core discovery-to-connection loop for a single agent vendor, against the Pharos Registry, for HTTP/SSE MCP servers.

**In scope:**
- Python SDK (TypeScript follows in Phase 2)
- `PharosClient.search()` against the Pharos Registry
- `ServerCard` schema with publisher, capabilities, auth, pricing, rating, trust
- Approval flow with CLI renderer (callback-based)
- `ApprovalToken` issuance and local consent store
- Connection Manager for **HTTP+SSE and Streamable HTTP** transports
- MCP `initialize` → `tools/list` → `tools/call` lifecycle
- Publisher signature verification (ed25519 + did:web)
- Local blocklist fetch
- Tool-usage logging + `on_tool_use` callback
- Official MCP Registry adapter (read-only, client-side re-ranking)

**Explicitly out of MVP:**
- stdio transport (added in Phase 2)
- TypeScript SDK (Phase 2)
- ARD adapter (Phase 2)
- **OAuth via App Registration Inheritance / `OAuthFlowHandler` / MCP Apps inline OAuth (Phase 2 — §17).** The expanded `auth` schema (`app_registration` + `ui` + `secret_handling`), the OAuth metadata endpoint (§6.7), and the `OAuthFlowHandler` interface are **designed for in Phase 0** (this spec) so the data model and approval flow are forward-compatible, but the implementation lands in Phase 2 after basic search + approve + connect works. Phase 1 ships with the simple OAuth flow described in §9.4 (launch at a vendor-provided `auth_url`, in-memory token).
- Federation / referrals (Phase 3)
- A2A and AGNTCY adapters (Phase 3)
- Reviews and pricing surfaces beyond display (Phase 3)
- Walled-garden bridges (Phase 3+)
- Sandboxing hooks (Phase 2)
- Registry-side consent audit (Phase 3)

### 14.2 Future features (post-MVP)

- **Sandbox execution** for stdio servers (Docker/firejail/nsjail wrappers)
- **OAuth via App Registration Inheritance** (§17): `OAuthFlowHandler` coordinating MCP Apps inline OAuth, CIMD hosting via Pharos Registry (for agent provider identity), vendor app-registration inheritance via `pharos.json`, scope minimization, one-time agent provider registration, MCP Apps sandboxed-iframe integration
- **Cross-registry federation** with automatic referral following
- **A2A agent discovery** (treating A2A Agent Cards as discoverable capabilities)
- **AGNTCY integration** (Linux Foundation IoA)
- **ARD full compatibility** (bi-directional: Pharos Registry exposes an ARD facade; Pharos client consumes ARD registries)
- **Business dashboard** for publishers (analytics on discovery impressions, approvals, tool calls)
- **Revenue model**: optional revenue-share pricing tier where businesses pay per agent-mediated connection (the "AdWords for agents" layer)
- **On-device embedding model** for privacy-preserving client-side re-ranking when querying substring-only registries
- **Revocation protocol** — push-based revocation of publisher keys and server cards
- **Multi-agent approval** — when multiple agents share a session, quorum-based approval for high-risk connections
- **Voice-first approval UX** — full spoken confirmation flow with TTS/STT hooks

---

## 15. Development Roadmap

### Phase 0 — Specification & Spike (this document)
**Goal:** ratify the spec, validate the protocol against the official MCP Registry and one ARD registry.
- ✅ This `SPEC.md`
- Spike: hand-craft a `ServerCard` for a real MCP server, exercise the MCP `initialize` → `tools/call` flow against it from a Python script.
- Spike: call the official MCP Registry's `GET /v0.1/servers?search=filesystem` and map the response to a `ServerCard`.
- Spike: call an ARD registry's `POST /search` and map the response.
- **Design review: OAuth via App Registration Inheritance (§17) data model.** Confirm the expanded `auth` schema (`app_registration` + `ui` + `secret_handling`), the `OAuthFlowHandler` interface (which coordinates rather than runs a redirect flow), the CIMD hosting plan for agent provider identity, and the MCP Apps inline-OAuth integration are implementable against at least one real MCP server that bundles an OAuth app registration in its `pharos.json` and supports the MCP Apps `ui://oauth/login` resource.
- **Exit criteria:** the spec's data model round-trips through all three sources without loss, AND the §17 design is validated as implementable (no implementation yet).

### Phase 1 — Python MVP (weeks 1–6)
**Goal:** a working `pharos-discovery` Python package that an agent can embed to search the Pharos Registry, get approval, and connect to an HTTP/SSE MCP server.
- `pharos_discovery.PharosClient` with `search`, `request_approval`, `connect`, `revoke`
- `ServerCard`, `ApprovalToken`, `MCPClient` types
- CLI approval renderer
- HTTP+SSE + Streamable HTTP Connection Manager
- Publisher signature verification (ed25519 + did:web)
- Local consent store (append-only, signed)
- Official MCP Registry adapter (read-only)
- Tool-usage logging
- Integration tests against a local mock registry + a real public MCP server
- **Exit criteria:** a demo script that searches, approves, and calls a tool on a real remote MCP server, end-to-end.

### Phase 2 — TypeScript + stdio + ARD + OAuth via App Registration Inheritance (weeks 7–12)
- `@pharos/discovery` TypeScript package (parity with Python MVP)
- stdio transport (subprocess launch, sandbox hooks)
- ARD adapter (consume ARD registries; Pharos Registry exposes an ARD-compatible `/search`)
- **OAuth via App Registration Inheritance (§17):**
  - `OAuthFlowHandler` implementation (Python + TS) coordinating the inline-OAuth flow: retrieve `app_registration` from the `ServerCard`, present vendor `consent_defaults` (user-overridable), trigger the MCP server's inline OAuth UI via MCP Apps, wait for server-side auth confirmation
  - `GET /v1/servers/{id}/oauth` endpoint on the Pharos Registry (§6.7)
  - One-time agent provider registration: `POST /v1/agents/register` on the Pharos Registry to host CIMD metadata at `https://registry.pharos.dev/v1/agents/{provider_id}/cimd` (establishes verified agent provider identity; NOT the per-server `client_id`)
  - Expanded `auth` schema in `ServerCard` (Appendix A) populated by registry publishers from vendor `pharos.json`
  - MCP Apps inline OAuth UI: sandboxed-iframe rendering of `ui://oauth/login`, JSON-RPC over `postMessage`, CSP enforcement
  - OAuth scope approval integrated into the §7 consent gate (`approved_oauth_scopes` in `ApprovalToken`); consent defaults pre-checked, user may expand or reduce
  - Scope minimization (only `approved_oauth_scopes` passed to the MCP server), server-side token revocation (`OAuthFlowHandler.revoke_access`)
  - SSRF prevention on CIMD/metadata fetches (egress allowlist, redirect depth cap)
  - `429 RATE_LIMITED` handling: exponential backoff with full jitter + fallback to cached `ServerCard` results (§13.5)
- Sandboxing config (Docker/firejail)
- Egress allowlist for HTTP transports
- Host-agent integration guide + reference integration with one open-source agent (e.g. a Hermes Agent skill)
- **Exit criteria:** a second agent runtime embeds the TS SDK and performs an end-to-end discovery-to-connection flow against an OAuth-protected MCP server using App Registration Inheritance and MCP Apps inline OAuth — the agent never handles a token, the user never leaves the chat, and no per-server app registration is created by the user or agent.

### Phase 3 — Federation + A2A + AGNTCY (weeks 13–20)
- Cross-registry federation (`auto` and `referrals` modes, max depth, referral following)
- A2A adapter (discover A2A agents via Agent Cards)
- AGNTCY adapter (discover agents in AGNTCY registries)
- Reviews and pricing as first-class interactive surfaces (submit review, compare pricing)
- Registry-side consent audit (opt-in)
- Walled-garden read-only bridges (Claude Connectors listing, where ToS permits)
- Publisher dashboard v1 (analytics)
- **Exit criteria:** a single agent search federates across Pharos Registry + official MCP Registry + one ARD registry + one A2A directory and returns merged, ranked results.

### Phase 4 — Scale & business layer (weeks 21+)
- Revenue-share pricing tier (per-connection billing for businesses)
- On-device embedding model for client-side re-ranking
- Push-based revocation protocol
- Voice-first approval UX
- Multi-agent quorum approval
- Formal conformance test suite (modeled on ARD's conformance CLI)
- Governance: Pharos Discovery working group, neutral home (OSI/LF?), spec stabilization toward v1.0

---

## 16. Appendices

### Appendix A: `ServerCard` JSON Schema (canonical)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "ServerCard",
  "type": "object",
  "required": ["id", "display_name", "publisher", "version", "transport", "capabilities", "auth", "availability"],
  "properties": {
    "id": {"type": "string", "pattern": "^urn:pharos:"},
    "display_name": {"type": "string"},
    "description": {"type": "string"},
    "publisher": {
      "type": "object",
      "required": ["id", "name"],
      "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "verified": {"type": "boolean"},
        "verification_method": {"type": "string"}
      }
    },
    "version": {"type": "string"},
    "transport": {"type": "array", "items": {"enum": ["stdio", "http+sse", "streamable-http"]}},
    "endpoint": {"type": ["string", "null"]},
    "stdio_command": {"type": ["string", "null"]},
    "capabilities": {"type": "array", "items": {"type": "string"}},
    "tools_count": {"type": "integer"},
    "availability": {"enum": ["mirrored", "referenced", "native"]},
    "auth": {
      "type": "object",
      "required": ["type"],
      "properties": {
        "type": {"enum": ["none", "api_key", "oauth", "mtls"]},
        "secret_handling": {"enum": ["server_side", "agent_side"]},
        "app_registration": {
          "type": "object",
          "description": "Vendor pre-registered OAuth app, bundled in pharos.json and inherited by the MCP server. Required when type == oauth and secret_handling == server_side.",
          "properties": {
            "client_id": {"type": "string"},
            "auth_server_url": {"type": "string"},
            "grant_types": {"type": "array", "items": {"type": "string"}},
            "scopes": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "name": {"type": "string"},
                  "description": {"type": "string"}
                }
              }
            },
            "consent_defaults": {"type": "array", "items": {"type": "string"}, "description": "OAuth scopes pre-checked in the approval prompt; the user may expand or reduce."},
            "redirect_uri_pattern": {"type": "string"},
            "endpoints": {
              "type": "object",
              "properties": {
                "authorization": {"type": "string"},
                "token": {"type": "string"},
                "revocation": {"type": "string"},
                "jwks": {"type": "string"}
              }
            }
          }
        },
        "ui": {
          "type": "object",
          "description": "MCP Apps inline OAuth UI descriptor (§17.6). The MCP server returns an HTML login segment at resource_uri, rendered in a sandboxed iframe in the chat.",
          "properties": {
            "resource_uri": {"type": "string", "description": "e.g. ui://oauth/login"},
            "csp": {"type": "string", "description": "Content Security Policy the host SHOULD enforce on the inline iframe."}
          }
        },
        "scopes": {"type": "array", "items": {"type": "string"}, "description": "Flat scope list for non-OAuth or legacy auth types; OAuth servers use app_registration.scopes instead."},
        "auth_url": {"type": "string", "description": "Legacy: authorization URL for the Phase 1 simple OAuth flow (§9.4). Deprecated in favor of app_registration.endpoints.authorization."},
        "auth_server_url": {"type": "string", "description": "Legacy top-level copy; prefer app_registration.auth_server_url."},
        "authorization_endpoint": {"type": "string", "description": "Legacy; prefer app_registration.endpoints.authorization."},
        "token_endpoint": {"type": "string", "description": "Legacy; prefer app_registration.endpoints.token."},
        "grant_types": {"type": "array", "items": {"type": "string"}, "description": "Legacy top-level copy; prefer app_registration.grant_types."},
        "pkce_required": {"type": "boolean"},
        "dcr_support": {"type": "boolean", "description": "Legacy fallback: true only when the vendor did NOT pre-register an app and DCR is required."},
        "dcr_endpoint": {"type": ["string", "null"]},
        "cimd_support": {"type": "boolean", "description": "Whether the server's authorization server supports Client ID Metadata Documents for agent identity verification (§17.3). Independent of app_registration, which governs the per-server OAuth client."},
        "jwks_url": {"type": "string", "description": "Legacy; prefer app_registration.endpoints.jwks."},
        "token_auth_method": {"type": "string"}
      }
    },
    "pricing": {
      "type": ["object", "null"],
      "properties": {
        "model": {"enum": ["free", "per_call", "subscription", "revenue_share"]},
        "price_usd": {"type": "number"},
        "free_tier": {"type": "string"},
        "billing_url": {"type": "string"}
      }
    },
    "rating": {
      "type": ["object", "null"],
      "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 5},
        "count": {"type": "integer"}
      }
    },
    "trust": {
      "type": ["object", "null"],
      "properties": {
        "signature": {"type": "string"},
        "attestations": {"type": "array", "items": {"type": "string"}}
      }
    },
    "representative_queries": {"type": "array", "items": {"type": "string"}},
    "pharos_score": {"type": "number", "minimum": 0, "maximum": 1},
    "source_registry": {"type": "string"}
  }
}
```

### Appendix B: Identifier format

Pharos Discovery uses domain-anchored URN identifiers, isomorphic to ARD's `urn:air:` scheme but in the `urn:pharos:` namespace:

```
urn:pharos:<publisher-domain>:<namespace>:<server-name>
```

- `<publisher-domain>` — a verifiable FQDN (e.g. `acme.com`). Acts as the trust anchor.
- `<namespace>` — optional hierarchical segments (e.g. `travel`, `finance:trading`).
- `<server-name>` — the terminal short name.

Rationale mirrors ARD Appendix C: the URN is a stable logical noun decoupled from physical endpoints, domain-anchored for decentralized trust, and globally unique without a central registrar. Pharos and ARD identifiers are trivially convertible (`urn:air:` ↔ `urn:pharos:`) via the ARD adapter.

### Appendix C: MCP protocol cheat sheet

For implementers. Pharos Discovery handles this internally; it is documented here for clarity.

**`initialize` request (client → server):**
```json
{
  "jsonrpc": "2.0", "id": 1, "method": "initialize",
  "params": {
    "protocolVersion": "2025-03-26",
    "capabilities": {"roots": {"listChanged": true}},
    "clientInfo": {"name": "pharos-discovery", "version": "0.1.0"}
  }
}
```

**`initialize` response (server → client):**
```json
{
  "jsonrpc": "2.0", "id": 1,
  "result": {
    "protocolVersion": "2025-03-26",
    "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
    "serverInfo": {"name": "acme-flights", "version": "2.1.0"},
    "instructions": "Use flight_search for availability, flight_book to ticket."
  }
}
```

**`notifications/initialized` (client → server):** `{}` — handshake complete.

**`tools/list`:**
```json
{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
```

**`tools/call`:**
```json
{
  "jsonrpc": "2.0", "id": 3, "method": "tools/call",
  "params": {
    "name": "flight_search",
    "arguments": {"origin": "NYC", "destination": "TYO", "date": "2026-07-25"}
  }
}
```

### Appendix D: References

- **MCP Specification** — https://modelcontextprotocol.io/specification/2025-03-26 (architecture, lifecycle, transports, tools)
- **Official MCP Registry API** — https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/api/official-registry-api.md
- **ARD Specification v0.9** — https://agenticresourcediscovery.org/spec/ (authors: Junjie Bu — Google, R.V. Guha — Microsoft, Shaun Smith — Hugging Face)
- **ARD spec repository** — https://github.com/ards-project/ard-spec
- **AGNTCY** — https://agntcy.org/ , https://docs.agntcy.org/ (Linux Foundation Internet of Agents)
- **Agent2Agent (A2A) Protocol** — https://a2a-protocol.org/latest/ , https://github.com/a2aproject/A2A
- **A2A Agent Card** — https://agent2agent.info/docs/concepts/agentcard/
- **mcp-gateway-registry** — https://github.com/agentic-community/mcp-gateway-registry (FAISS semantic search, `/api/search/semantic`)
- **AWS MCP Gateway & Registry** — https://aws.amazon.com/blogs/opensource/governing-ai-assets-at-scale-with-mcp-gateway-and-registry/
- **Claude MCP Connector** — https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
- **M365 Copilot dynamic tool discovery** — https://github.com/MicrosoftDocs/m365copilot-docs/blob/main/docs/plugin-dynamic-tool-discovery.md
- **MCP Transports** — https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
- **MCP Apps (extension)** — https://modelcontextprotocol.io/extensions/apps/overview (the first official MCP extension; tools return interactive HTML rendered in-chat via sandboxed iframes; used by Pharos for inline OAuth login forms; supported by Claude, ChatGPT, VS Code, Goose, and more)
- **MCP OAuth 2.1 & Client Registration** — https://blog.modelcontextprotocol.io/posts/client_registration/ (background on MCP's adoption of OAuth 2.1 and the Dynamic Client Registration problem)
- **Client ID Metadata Documents (CIMD, SEP-991)** — clients host a metadata URL as their `client_id`; authorization servers fetch metadata at authorization time. No `/register` endpoint needed. Eliminates unbounded DB growth, client-expiry black hole, per-instance client ID proliferation, and DCR DoS.
- **Software Statements (SEP-1032)** — signed JWTs for desktop client identity, layered on top of DCR or CIMD.
- **Bluesky (AT Protocol) CIMD implementation** — reference implementation of client-hosted metadata documents for OAuth.

---

## 17. OAuth via App Registration Inheritance

This section specifies Pharos Discovery's solution to the **MCP OAuth bootstrap problem**: the fact that agent providers (Claude, Cursor, etc.) currently must implement OAuth flows for *every* MCP server, each potentially using a different authorization server, and each historically requiring a per-server app registration or an unbounded Dynamic Client Registration (DCR) dance. This does not scale.

Pharos Discovery's model — **App Registration Inheritance** combined with **MCP Apps inline OAuth** — is a fundamentally different approach. The agent and SDK **never handle OAuth tokens or `client_secret`s**. The MCP server brokers the entire OAuth flow server-side, and the user authenticates **inline in the chat** via the MCP Apps extension — they never leave the conversation to log in.

### 17.1 The problem (and why the redirect-flow model is wrong for agents)

MCP adopted OAuth 2.1 as its auth framework. Per the MCP team's own analysis (blog.modelcontextprotocol.io/posts/client_registration/), the standard OAuth Dynamic Client Registration (DCR) flow has four serious problems when applied to MCP at scale:

1. **Unbounded DB growth on authorization servers.** Every agent instance that connects to a server triggers a `/register` call, creating a client record that lives forever. Across thousands of MCP servers and millions of agent installs, this is an unbounded storage cost borne by *server operators*.
2. **Client expiry black hole.** DCR-registered clients have no natural expiry story. Authorization servers can't tell which clients are still active, so they keep them all.
3. **Per-instance confusion.** The same agent app installed on two machines gets two different `client_id`s. Tokens, logs, and revocation all become per-instance, not per-app. There is no stable identity for "Claude Desktop" across installs.
4. **DoS vulnerability on `/register`.** An open `/register` endpoint is trivially abuseable. A malicious client can flood it with registrations, exhausting the server's DB.

On top of the DCR problems, the **standard OAuth redirect flow is a poor fit for agentic discovery** for two more reasons:

5. **The agent shouldn't handle tokens.** An agent runtime that holds OAuth access tokens and `client_secret`s is a high-value target. A compromised agent leaks every connected service's credentials. The standard model concentrates secret material in the least-defensible part of the stack.
6. **Leaving the chat to log in breaks the agentic UX.** A user who asks an agent to "book a flight" should not be bounced to a browser tab, asked to log in to a third party, and then return to the chat. Discovery-to-action should be one continuous flow.

App Registration Inheritance solves (1)–(4) by eliminating per-instance registration entirely: the vendor pre-registers *one* app, and every install of the MCP server inherits it. Moving the OAuth flow server-side into the MCP server solves (5). MCP Apps inline OAuth solves (6).

### 17.2 Two levels of registration

App Registration Inheritance is a **two-level** model.

**Level 1 — Agent Provider Registration (CIMD).** Agent providers (OpenAI, Anthropic, Cursor, etc.) register *once* with the Pharos Registry. The registry verifies the provider's identity and hosts the provider's Client ID Metadata Document (CIMD) at a stable, signed URL. This establishes the **agent provider's verified identity** — used for agent authentication to the registry and for vendor-side agent allow-listing (§17.3). It is *not* the `client_id` used against each MCP server's authorization server.

**Level 2 — Vendor App Registration Inheritance.** MCP server vendors (e.g. Salesforce, Stripe, Acme) pre-register an OAuth app with their own Identity Provider (IdP) and **bundle that registration into their `pharos.json`**. The bundled registration includes `client_id`, `auth_server_url`, `grant_types`, `scopes`, `consent_defaults`, `redirect_uri_pattern`, and `endpoints` — but **never `client_secret`** (the secret stays server-side in the MCP server's own configuration, not in `pharos.json` and not in the `ServerCard`). When an agent installs or enables the MCP server, the MCP server **inherits** the vendor's app registration. No user creates a new app registration. No agent calls `/register`.

```
┌─────────────────────────────────────────────────────────────────────┐
│  LEVEL 1 — Agent Provider Registration (once per provider, ever)     │
│                                                                      │
│  OpenAI / Cursor / Anthropic / ...                                   │
│      │                                                               │
│      │  POST /v1/agents/register  (to Pharos Registry)               │
│      │  → registry hosts CIMD at                                      │
│      │    https://registry.pharos.dev/v1/agents/{provider_id}/cimd   │
│      │  → establishes VERIFIED AGENT PROVIDER IDENTITY               │
│      ▼                                                               │
│  Used for: agent auth to registry, vendor allow-list checks          │
│  NOT used as: the per-server OAuth client_id                         │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  LEVEL 2 — Vendor App Registration Inheritance (once per MCP server) │
│                                                                      │
│  Vendor (e.g. Acme)                                                  │
│      │                                                               │
│      │  1. Registers an OAuth app with their IdP (auth.acme.com)     │
│      │     → gets client_id + client_secret from IdP                 │
│      │  2. Bundles the app registration into pharos.json:            │
│      │       auth.app_registration = { client_id, endpoints,         │
│      │         scopes, consent_defaults, redirect_uri_pattern, ... } │
│      │     (client_secret stays in the MCP server's server-side      │
│      │      config — NEVER in pharos.json, NEVER in the ServerCard)  │
│      │  3. Publishes ServerCard to Pharos Registry                   │
│      ▼                                                               │
│  Agent (any provider) discovers the server via Pharos search         │
│      │                                                               │
│      │  → ServerCard.auth.app_registration carries the inherited     │
│      │    app registration metadata                                  │
│      │  → MCP server INHERITS the vendor's client_id                 │
│      │  → MCP server holds the client_secret server-side             │
│      │  → No user/agent ever creates a new app registration          │
└─────────────────────────────────────────────────────────────────────┘
```

**Net effect.** Agent providers register once for identity. Vendors register once per MCP server with their own IdP. Every agent install inherits the vendor's app registration. No per-instance client IDs. No `/register` calls. No `client_secret` in the registry, agent, or SDK.

### 17.3 Agent provider registration & CIMD hosting (Level 1)

```
Agent provider (e.g. "Cursor")
   │
   │  1. Register once with the Pharos Registry:
   │     POST /v1/agents/register
   │       { "provider_id": "cursor", "client_name": "Cursor",
   │         "redirect_uris": [...], "jwks": {...},
   │         "software_statement": "<signed JWT, SEP-1032>" }
   ▼
Pharos Registry
   │
   │  2. Verifies the software statement (signed JWT, SEP-1032).
   │  3. Hosts the provider's CIMD at a stable, signed URL:
   │     https://registry.pharos.dev/v1/agents/cursor/cimd
   │  4. Returns that URL to the provider.
   ▼
Vendor MCP servers (at OAuth time, server-side)
   │
   │  5. MCP server receives an OAuth request that includes the
   │     agent provider's CIMD URL (passed by the SDK).
   │  6. MCP server fetches the CIMD document from that URL to
   │     VERIFY the agent provider's identity.
   │  7. If the vendor allow-lists this provider, the MCP server
   │     proceeds with the OAuth flow using ITS OWN (inherited)
   │     client_id — NOT the CIMD URL.
   ▼
User logs in inline (MCP Apps); MCP server exchanges the
authorization code for a token SERVER-SIDE and keeps it.
```

**Important distinction from the prior spec.** Under the old model, the CIMD URL *was* the `client_id` used against every MCP server's authorization server. Under App Registration Inheritance, the CIMD URL is used **only for agent provider identity verification**. The `client_id` used against the vendor's IdP is the vendor's own pre-registered `app_registration.client_id`, inherited by the MCP server. This keeps the OAuth client relationship between the vendor and their IdP — where it belongs — while still giving the vendor a verified agent identity to allow-list.

The provider registers *once*, ever. If the provider rotates keys, they update the registry; the URL stays stable. If the provider ships a new version, the version is reflected in the CIMD document, not in a new registration.

### 17.4 The `OAuthFlowHandler` — coordinates, does not run a redirect flow

Under App Registration Inheritance, the `OAuthFlowHandler` **no longer runs a standard OAuth redirect flow**. It coordinates. The five-step flow:

```
1. Agent discovers server via Pharos search
   → ServerCard.auth includes app_registration + ui config

2. SDK's OAuthFlowHandler presents consent defaults to the user
   → vendor's consent_defaults pre-checked in the approval prompt
   → user MAY expand or reduce the OAuth scope set
   → SDK records approved_oauth_scopes in the ApprovalToken

3. Agent installs / enables the MCP server (MCP initialize)

4. OAuthFlowHandler triggers the MCP server's inline OAuth UI
   → SDK calls the MCP server requesting the ui://oauth/login resource
   → MCP server returns an MCP Apps HTML segment (a login form)
   → host renders it INLINE in the chat via a sandboxed iframe
   → user enters credentials in the inline UI
   → MCP server handles the OAuth flow SERVER-SIDE:
       • uses its inherited client_id
       • holds the client_secret (never sent to agent/SDK)
       • exchanges the authorization code for a token itself
       • stores the token server-side

5. MCP server sends the agent an auth-completed CONFIRMATION
   → NOT the token — just { authorized: true, scope: [...] }
   → token stays with the MCP server, which proxies all tool calls
```

**The handler's flow selection.** When `pharos.connect(approval)` is called for a server with `auth.type == "oauth"`, the `OAuthFlowHandler.authorize()` inspects the `ServerCard.auth` config:

| Server `auth` config | Flow | `client_id` source | Token holder |
|---|---|---|---|
| `app_registration` present, `secret_handling == "server_side"` | **App Registration Inheritance** (preferred) | Vendor's pre-registered `app_registration.client_id`, inherited by the MCP server. No `/register` call. | MCP server |
| `app_registration` absent, `dcr_support == true` | **DCR fallback** (legacy) | Dynamically registered via `auth.dcr_endpoint` by the MCP server. Ephemeral `client_id`. Rate-limited (§10.5). | MCP server (preferred) or agent (legacy) |
| `app_registration` absent, `dcr_support == false`, static client configured | **Static client credentials** (legacy) | Pre-registered `client_id` / `client_secret` from the host's credential store. | Agent |
| `auth.type == "api_key"` | **API key prompt** | The handler surfaces a credential prompt to the user via the host's `credential_provider` callback. | Agent |

**Scope minimization.** The handler passes to the MCP server *only* the scopes in `ApprovalToken.approved_oauth_scopes` — never the full set advertised by the vendor. The MCP server requests only those scopes from its IdP. If the IdP grants a narrower set, the MCP server reports the *actual* granted scopes back to the agent in the confirmation, and the Connection Manager enforces tool calls against the granted set, not the requested set.

**Token lifecycle.** Under App Registration Inheritance the token lifecycle is managed **entirely by the MCP server**, not the SDK. The SDK tracks only an `OAuthStatus` (auth valid / expired / revoked). Refresh, when supported, is performed server-side by the MCP server using its stored `refresh_token`. Revocation is a request from the SDK to the MCP server (`OAuthFlowHandler.revoke_access()`), which tears down its server-side session and invalidates its token with the IdP. See §10.5 for security details.

### 17.5 MCP Apps integration — inline OAuth UI

**MCP Apps** is the first official MCP extension (live January 2026; reference: https://modelcontextprotocol.io/extensions/apps/overview). It allows MCP tools to return interactive HTML that renders **inline in the chat** via sandboxed iframes. Pharos uses MCP Apps for inline OAuth login forms — the user never leaves the chat to authenticate.

**How it works in the Pharos flow:**

1. The `ServerCard.auth.ui` object declares an inline-OAuth resource (conventionally `ui://oauth/login`) and a Content Security Policy.
2. After the user approves the connection and the OAuth scope set (§7.2), the SDK requests the `ui://oauth/login` resource from the MCP server.
3. The MCP server returns an MCP Apps HTML segment — a login form bound to its own IdP (using its inherited `client_id` and server-side `client_secret`).
4. The host agent renders the HTML segment in a **sandboxed iframe** inside the chat. The iframe has no access to the host's DOM, cookies, or storage.
5. All communication between the host and the iframe is **JSON-RPC 2.0 over `postMessage`** with an explicit origin check. The iframe posts login events (e.g. `auth_started`, `auth_completed`, `auth_error`) to the host; the host posts user choices (e.g. selected account) to the iframe.
6. The user enters credentials directly in the inline UI. The form posts to the MCP server (not the host), which performs the OAuth authorization-code exchange **server-side**.
7. On success, the MCP server posts an `auth_completed` JSON-RPC message to the host. The host closes the iframe and the SDK records `OAuthResult.authorized = true`. The token never leaves the MCP server.

**Sandboxed-iframe security properties:**

- The iframe is served with a `sandbox` attribute that withholds same-origin access; the host's cookies, localStorage, and DOM are inaccessible to the inline UI.
- The `auth.ui.csp` field declares the vendor's Content Security Policy. Hosts SHOULD enforce it and MAY block any inline UI whose effective CSP is more permissive than declared, or that attempts network access outside `auth.app_registration.endpoints`.
- Hosts MAY refuse to render inline OAuth UI at all (falling back to a "connect in the vendor's own app" prompt) for high-security deployments.
- Hosts MAY block suspicious UI (e.g. login forms that attempt to exfiltrate credentials to a non-declared endpoint) and report the server via `POST /v1/feedback/report`.

**Host support.** MCP Apps is supported by Claude, ChatGPT, VS Code, Goose, and more. Hosts that do not yet support MCP Apps fall back to the legacy redirect flow (Phase 1 behavior, §9.4) or refuse OAuth-protected servers.

### 17.6 `availability` field, trust, and OAuth

The `availability` field (§13.4) is orthogonal to auth but both contribute to the trust signal in the approval prompt. The **highest-trust combination** Pharos surfaces is:

> A `mirrored` server whose `auth` includes an `app_registration` block (vendor pre-registered OAuth, §17.2).

Why this is the highest-trust combination:
- The server's package is guaranteed retrievable by the Pharos Registry (mirrored).
- The OAuth flow requires no per-server app registration by the user or agent — the vendor pre-registered, the MCP server inherits.
- The `client_secret` never leaves the MCP server's server-side config (secret isolation, §10.5).
- The user authenticates inline in the chat (MCP Apps) — no redirect to a third-party tab.
- The agent provider's identity is CIMD-verified before the flow starts, and the vendor MAY allow-list providers.

The lowest-trust OAuth path is a `referenced` server with only DCR support (legacy fallback): the server may vanish upstream, and the connection requires an ephemeral DCR registration. The SDK surfaces both signals in the approval prompt so the user can make an informed consent decision.

### 17.7 Why this beats the redirect-flow model

| Property | Redirect-flow model (old spec / per-server OAuth) | App Registration Inheritance + MCP Apps (new spec) |
|---|---|---|
| Per-server app registration by user/agent | Required (or DCR) | None — vendor pre-registers; MCP server inherits |
| `client_secret` exposure | Lives in agent or is DCR-registered | Never in registry, agent, or SDK — server-side only |
| OAuth token exposure | Lives in agent runtime (in-memory or keychain) | Never reaches agent — MCP server proxies tool calls |
| User UX | Redirect to browser tab; return to chat | Inline in chat via sandboxed iframe; never leave |
| Agent provider identity | Per-instance client IDs | CIMD-verified provider identity, allow-listable by vendor |
| Compromised agent leaks | All connected services' OAuth tokens | Nothing — agent holds no tokens |
| Vendor control | Limited (per-server client managed by agent) | Full — vendor owns the app registration, consent defaults, and provider allow-list |
| DCR DB growth on IdPs | Unbounded | Zero (no DCR in the preferred path) |

---

**End of SPEC.md — Pharos Discovery v0.2.0 (Draft)**
