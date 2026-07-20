# TypeScript API Reference — `@pharos/discovery`

**Spec reference:** `SPEC.md` v0.4.0, §8.2 (TS surface), §8.3 (core types), §8.5 (config), §9 (transport), §10 (security), §11 (adapters), §17 (OAuth).
**Import root:** `@pharos/discovery`
**Node:** 20+; **browsers:** modern (ESM). `camelCase` throughout.

This is the complete public API of the TypeScript SDK. All types are IDL-generated (TS interfaces + zod schemas) from `idl/typespec/` unless marked *hand-written*. See `.guides/backend/TYPESCRIPT_GUIDE.md` for patterns and conventions.

---

## 1. Top-level imports

```typescript
import {
  // Client
  PharosClient,
  // Approval
  ApprovalRequest, ApprovalResponse, ApprovalToken, ApprovalResult,
  PlanApprovalRequest, PlanApprovalResponse, PlanApprovalToken,
  // Connection
  MCPClient, Tool, ToolResult, Resource, Prompt,
  // OAuth (Phase 2)
  OAuthFlowHandler, OAuthResult, RevocationResult, DefaultOAuthFlowHandler,
  // Models
  ServerCard, Publisher, AuthSpec, AppRegistration, PricingSpec, RatingSpec, TrustSpec,
  // Search
  SearchQuery, QueryBuilder, CapabilityGap,
  // Adapters
  RegistryAdapter, PharosRegistryAdapter, MCPRegistryAdapter, ARDAdapter,
  // Errors
  PharosError, RegistryUnavailable, NoServersFound, HeadlessApprovalRequired,
  ConnectionFailed, ConnectionLost, ScopeNotApproved, CapabilityMismatch,
  OAuthUnavailable, RetryableOAuthFailure, ConsentFatigueWarning,
  PublisherKeyStale, DiscoveryDegraded, UnsupportedFilter,
} from "@pharos/discovery";
```

---

## 2. `PharosClient` — the entry point

```typescript
class PharosClient {
  constructor(opts: {
    registryUrls: string[];
    agentId: string;
    apiKey?: string;
    consentStore?: string;                 // default "~/.pharos/consent.json"
    blocklistUrl?: string;
    blocklistCacheTtlSeconds?: number;     // default 60 (H9)
    cacheTtlSeconds?: number;              // default 300
    cacheConditional?: boolean;            // default true (ETag / If-None-Match)
    eventsEndpoint?: string;               // SSE /v1/events
    federationMode?: "auto" | "referrals" | "none";  // default "auto"
    maxReferralDepth?: number;             // default 2
    // Timeouts (seconds — SPEC §8.5, H8)
    searchTimeout?: number;                // default 10
    getTimeout?: number;                   // default 10
    connectTimeout?: number;               // default 10
    oauthTimeout?: number;                 // default 120 (Phase 2)
    approvalTimeout?: number;              // default 300
    initializeTimeout?: number;            // default 10
    toolCallTimeout?: number;              // default 30
    heartbeatInterval?: number;            // default 30 (HTTP/SSE only)
    healthCheckInterval?: number;          // default 60
    // Security (SPEC §10)
    verifySignatures?: boolean;            // default true
    allowUnverified?: boolean;             // default false
    keyPinTtlSeconds?: number;             // default 86400
    // Headless (SPEC §7.5)
    headlessMode?: boolean;                // default false
    headlessAllowServers?: string[];       // required when headlessMode: true
    headlessAllowScopes?: string[];
    // Privacy (SPEC §10.8)
    privacyMode?: boolean;                 // default false
    // Callbacks
    onToolUse?: (e: ToolUsageEvent) => void;
    onCapabilityGap?: (ctx: Record<string, unknown>) => CapabilityGap | null;
    // Fallback (SPEC §8.5)
    staticFallbackServers?: ServerCard[];
  });

  // --- Search (SPEC §6.2) ---
  async search(opts: {
    text?: string;
    filter?: Record<string, unknown>;       // SPEC §6.3.1 filter paths
    limit?: number;                         // default 10
    cursor?: string;
    federation?: "auto" | "referrals" | "none";
    queryEmbedding?: number[];              // blindedSearch (§10.8)
  }): Promise<ServerCard[]>;

  async get(serverId: string): Promise<ServerCard>;

  // --- Approval (SPEC §7) ---
  async requestApproval(opts: {
    server: ServerCard;
    purpose: string;
    requestedScopes: string[];
    requestedCapabilities: string[];
    duration: "once" | "session" | "persistent" | "trust_on_use";
    selectionRationale: string;             // MANDATORY (§7.1.1)
    render: (req: ApprovalRequest) => Promise<ApprovalResponse>;
    oauthConsentDefaultsOverride?: string[];  // Phase 2
  }): Promise<ApprovalResult>;

  async requestPlanApproval(opts: {
    planSummary: string;
    steps: ApprovalRequest[];
    render: (req: PlanApprovalRequest) => Promise<PlanApprovalResponse>;
  }): Promise<PlanApprovalResponse>;

  async requestApprovalNext(currentServerId: string): Promise<ServerCard | null>;

  // --- Connection (SPEC §8.3, §9) ---
  async connect(approval: ApprovalToken): Promise<MCPClient>;

  revoke(approval: ApprovalToken): void;
  revokeServer(serverId: string): void;

  // --- Reporting (SPEC §6.9, §10.3) ---
  async reportServer(serverId: string, reason: string): Promise<void>;
  async submitFeedback(serverId: string, rating: number, opts?: { comment?: string }): Promise<void>;

  // --- Cache & events ---
  async invalidateCache(serverId: string): Promise<void>;
  async close(): Promise<void>;
}
```

### `PharosClient.search` examples

```typescript
// Natural-language search
const results = await pharos.search({ text: "I need to book a flight to Tokyo", limit: 5 });

// Structured filters (SPEC §6.3.1)
const results = await pharos.search({
  filter: {
    capabilities: ["flight_search"],
    transport: ["http+sse", "streamable-http"],
    publisherVerified: true,
    minRating: 4.0,
    pricingTier: "free",
    availability: ["mirrored", "native"],
  },
  limit: 10,
});

// Privacy mode: filters only, no text (§10.8)
const results = await pharos.search({ filter: { capabilities: ["flight_search"] }, federation: "none" });

// Blinded search: send local embedding, no text (§10.8)
const embedding = await pharos.embedLocally("book a flight to Tokyo");
const results = await pharos.search({ queryEmbedding: embedding, limit: 5 });
```

### `PharosClient.requestApproval` example

```typescript
const approval = await pharos.requestApproval({
  server: results[0],
  purpose: "Book a flight to Tokyo for the user's July 25 trip",
  requestedScopes: ["flight_search", "flight_book"],
  requestedCapabilities: ["flight_search", "flight_book"],
  duration: "session",
  selectionRationale: "ranked #1 for flight_search; verified publisher",
  render: async (req) => {
    const ok = confirm(`Connect to ${req.server.displayName} (${req.server.publisher.name})?\nScopes: ${req.requestedScopes.join(", ")}`);
    return {
      approved: ok,
      approvedScopes: ok ? req.requestedScopes : [],
      duration: req.duration,
    };
  },
});
```

---

## 3. Core models (IDL-generated — SPEC §8.3, Appendix A)

### `ServerCard`

```typescript
interface ServerCard {
  id: string;                              // ^urn:pharos:
  displayName: string;
  description: string;
  publisher: Publisher;
  version: string;
  transport: ("stdio" | "http+sse" | "streamable-http")[];
  endpoint: string | null;
  stdioCommand: string | null;
  capabilities: string[];
  toolsCount: number;
  toolsCountVerified: boolean;
  auth: AuthSpec;
  availability: "mirrored" | "referenced" | "native";
  pricing: PricingSpec | null;
  pricingVerified: boolean;
  rating: RatingSpec | null;
  trust: TrustSpec | null;
  representativeQueries: string[];         // max 10 (H12)
  pharosScore: number | null;              // 0.0–1.0; NOT trust; null for ARD (§11.4)
  sourceRegistry: string;
  sourceScore: number | null;
  sourceUrn: string | null;
  documentationUrl: string | null;
  tags: string[];
  publishedAt: string;                     // ISO8601
  updatedAt: string;                       // ISO8601
  status: "active" | "deprecated" | "deleted";
  successorId: string | null;
  privacyPolicyUrl: string | null;
  termsUrl: string | null;
  dataResidency: string[];
  rateLimits: Record<string, number> | null;
  healthEndpoint: string | null;
  protocolVersions: string[];
}
```

### `Publisher`

```typescript
interface Publisher {
  id: string;                       // did:web:<fqdn>
  name: string;
  verified?: boolean;
  verificationMethod?: "domain_control" | "identity";
  contact?: string | null;
}
```

### `AuthSpec`

```typescript
interface AppRegistration {
  clientId: string;
  authServerUrl: string;
  grantTypes: string[];
  scopes: { name: string; description: string }[];
  consentDefaults: string[];
  redirectUriPattern: string;
  appManagementUrl?: string;
  endpoints: {
    authorization: string;
    token: string;
    revocation: string;
    jwks: string;
  };
  // NOTE: no clientSecret — never present (SPEC §10.5, §17.2)
}

interface OAuthUI {
  resourceUri: string;
  csp: string;
}

interface AuthSpec {
  type: "none" | "api_key" | "oauth" | "mtls";
  secretHandling?: "server_side" | "agent_side";
  appRegistration?: AppRegistration;     // present when type=="oauth" && server_side
  ui?: OAuthUI;
  scopes?: string[];                      // legacy/display for OAuth; authoritative for non-OAuth
  // ... legacy fields per Appendix A
}
```

### `PricingSpec`, `RatingSpec`, `TrustSpec`

```typescript
interface PricingSpec {
  model: "free" | "freemium" | "paid" | "usage" | "subscription" | "enterprise" | "custom";
  priceUsd?: number;
  unit?: string;                          // "per_call", "per_month", etc.
  freeTierLimit?: string;
  termsUrl?: string;
}

interface RatingSpec {
  score: number;                          // 0.0–5.0
  count: number;                          // number of ratings
  lastUpdated?: string;
}

interface TrustSpec {
  signature?: string;                     // ed25519 signature
  attestations: Attestation[];
  compliance: string[];
  dataResidency: string[];
}

interface Attestation {
  type: string;                           // "SOC2", "GDPR", "HIPAA", etc.
  uri: string;
  verified: boolean;
  verifier?: string;
  verifiedAt?: string;
}
```

---

## 4. Approval types (SPEC §7)

```typescript
interface ApprovalRequest {
  server: ServerCard;
  purpose: string;
  requestedScopes: string[];
  requestedCapabilities: string[];
  duration: "once" | "session" | "persistent" | "trust_on_use";
  renderId: string;
  selectionRationale: string;             // MANDATORY (§7.1.1)
}

interface ApprovalResponse {
  approved: boolean;
  approvedScopes: string[];               // may be subset
  duration: string;
  userNote?: string | null;
  denyReason?: "untrusted_publisher" | "excessive_scopes" | "wrong_server" | "cost" | "other" | null;
}

interface ApprovalToken {
  tokenId: string;
  serverId: string;
  approvedScopes: string[];
  approvedCapabilities: string[];
  approvedOauthScopes: string[];          // [] if auth.type != "oauth"
  duration: string;
  approvedAt: string;
  expiresAt: string;
  signature: string;                      // ed25519 over token body (local SDK key)
}

interface ApprovalResult {
  approved: boolean;
  token?: ApprovalToken;
  denyReason?: string;
}

interface PlanApprovalRequest {
  planSummary: string;
  steps: ApprovalRequest[];
  planId: string;
}

interface PlanApprovalResponse {
  approved: boolean;
  perStep: ApprovalResponse[];
  token?: PlanApprovalToken;
}

interface PlanApprovalToken extends ApprovalToken {
  planId: string;
  stepTokens: ApprovalToken[];
}
```

---

## 5. `MCPClient` — live connection (SPEC §8.3, §9)

```typescript
class MCPClient {
  readonly server: ServerCard;
  readonly approval: ApprovalToken;
  readonly protocolVersion: string;
  readonly serverCapabilities: Record<string, unknown>;
  readonly verifiedCapabilities: string[];   // post-connect verified (H13)

  async listTools(): Promise<Tool[]>;
  async callTool(name: string, args: Record<string, unknown>, opts?: { timeout?: number }): Promise<ToolResult>;
  async listResources(): Promise<Resource[]>;
  async readResource(uri: string): Promise<string>;
  async listPrompts(): Promise<Prompt[]>;
  async close(): Promise<void>;
}

interface Tool {
  name: string;
  description?: string;
  inputSchema: Record<string, unknown>;
}

interface ToolResult {
  content: Array<{ type: string; text?: string; [k: string]: unknown }>;
  isError: boolean;
}

interface Resource {
  uri: string;
  name?: string;
  mimeType?: string;
}

interface Prompt {
  name: string;
  description?: string;
}
```

**Scope enforcement:** `callTool` for a tool outside `approvedCapabilities`, or requiring an auth scope outside `approvedScopes`, throws `ScopeNotApproved`. (§7.4)

**Connection pooling:** at most one live `MCPClient` per `serverId` per session. Repeated `connect()` with a valid, non-expired `ApprovalToken` returns the cached client. (§9.5)

**Never auto-reconnect** after teardown without a fresh approval — even for `persistent`-duration servers. (§9.5)

---

## 6. OAuth (Phase 2 — SPEC §17)

```typescript
abstract class OAuthFlowHandler {
  abstract authorize(opts: {
    server: ServerCard;
    approval: ApprovalToken;
    consentDefaults: string[];
  }): Promise<OAuthResult>;

  abstract revokeAccess(serverId: string): Promise<RevocationResult>;
}

interface OAuthResult {
  authorized: boolean;
  accessToken?: string | null;           // null when secretHandling == "server_side" (§17.4)
  tokenType?: string | null;
  expiresIn?: number | null;
  refreshToken?: string | null;
  scope: string[];                        // actually granted (may be subset)
  acquiredVia: "app_registration_inheritance" | "cimd" | "dcr" | "api_key" | "static" | "server_brokered_redirect";
  authHeldBy: "mcp_server" | "agent";     // under §17, always "mcp_server"
  confirmedAt?: string | null;
  confirmationJwt?: string | null;        // SIGNED IdP assertion; SDK MUST verify via endpoints.jwks
  error?: "timeout" | "server_lost" | "cancelled" | "invalid_jwt" | "idp_error" | null;
  cancelReason?: string | null;
}

interface RevocationResult {
  revoked: boolean;
  revocationProof?: string | null;        // signed assertion; required within 60s (H16)
  error?: string | null;
}

class DefaultOAuthFlowHandler extends OAuthFlowHandler {
  constructor(opts: { supportsMcpApps: boolean; hasSystemBrowser: boolean });
}
```

**Sequencing (H5):** `pharos.connect(approval)` calls `OAuthFlowHandler.authorize()` **first**, before MCP `initialize`. Only on `authorized === true` does `initialize` proceed.

**JWT verification:** the SDK MUST verify `confirmationJwt` (signature against `appRegistration.endpoints.jwks`, `exp` not in past, `clientId` matching inherited `appRegistration.clientId`) before treating `authorized === true`. On failure → `authorized = false, error = "invalid_jwt"`, connection torn down. (§17.4 step 5)

---

## 7. Search types (SPEC §6.2, §6.3)

```typescript
interface SearchQuery {
  text?: string;
  filter?: Record<string, unknown>;       // SPEC §6.3.1 filter paths
  capabilities?: string[];
  transport?: string[];
  availability?: string[];
  pricingTier?: string[];
  publisherVerified?: boolean;
  minRating?: number;
  limit?: number;
  cursor?: string;
  federation?: "auto" | "referrals" | "none";
  queryEmbedding?: number[];
}

class QueryBuilder {
  text(t: string): this;
  capability(c: string): this;
  transport(t: string): this;
  publisherVerified(v?: boolean): this;
  minRating(r: number): this;
  pricingTier(p: string): this;
  limit(n: number): this;
  build(): SearchQuery;
}

interface CapabilityGap {
  description: string;
  suggestedQuery?: string;
  requiredCapabilities: string[];
}
```

### Filter paths (SPEC §6.3.1)

| Filter | Values | Notes |
|--------|--------|-------|
| `capabilities` | `string[]` | server exposes these capabilities |
| `transport` | `("stdio" \| "http+sse" \| "streamable-http")[]` | |
| `availability` | `("mirrored" \| "referenced" \| "native")[]` | |
| `pricingTier` | `("free" \| "freemium" \| "paid" \| ...)[]` | |
| `publisherVerified` | `boolean` | |
| `minRating` | `number` | 0.0–5.0 |
| `dataResidency` | `string[]` | e.g. `["EU"]` |
| `protocolVersions` | `string[]` | e.g. `["2025-03-26"]` |

Unsupported filter paths return `400 UNSUPPORTED_FILTER` (§6.13).

---

## 8. Registry adapters (SPEC §11)

```typescript
interface RegistryAdapter {
  readonly name: "pharos" | "mcp-official" | "ard" | string;
  readonly capabilities: Set<AdapterCapability>;
  search(query: SearchQuery): Promise<ServerCard[]>;
  get(serverId: string): Promise<ServerCard>;
  publish?(card: ServerCard): Promise<string>;
  report?(serverId: string, reason: string): Promise<void>;
  toCanonical(native: Record<string, unknown>): ServerCard;
  fromCanonical?(card: ServerCard): Record<string, unknown>;
}

type AdapterCapability =
  | "semantic_search" | "filter_search" | "reviews" | "pricing"
  | "federation" | "publish" | "report" | "blinded_search"
  | "push_events" | "key_pinning";
```

| Adapter | Phase | Native API | Notes |
|---------|-------|-----------|-------|
| `PharosRegistryAdapter` | 1 | §6 (native) | Full capabilities |
| `MCPRegistryAdapter` | 1 | `GET /v0.1/servers` | Client-side re-rank via bundled ONNX (§11.3) |
| `ARDAdapter` | 2 | `POST /search` | `urn:air:` ↔ `urn:pharos:`; `score` → `sourceScore`; `pharosScore = null` (§11.4) |

---

## 9. Errors (SPEC §6.13, §7, §9.5, §17.5.1)

All errors extend `PharosError` and carry a `code` and optional `specRef`.

```typescript
class PharosError extends Error {
  readonly code: string;
  readonly specRef?: string;
  constructor(message: string, code: string, specRef?: string);
}
```

| Error class | `code` | When | Spec |
|-------------|--------|------|------|
| `RegistryUnavailable` | `REGISTRY_UNAVAILABLE` | All registries unhealthy + no cache | §13.5 |
| `NoServersFound` | `NO_SERVERS_FOUND` | Search returns zero results | §7.1.1 |
| `HeadlessApprovalRequired` | `HEADLESS_APPROVAL_REQUIRED` | `headlessMode` + novel server not on allow-list | §7.5 |
| `ConnectionFailed` | `INITIALIZE_TIMEOUT` / `CONNECTION_REFUSED` / ... | `initialize` timeout/failure | §9.1 |
| `ConnectionLost` | `CONNECTION_LOST` | Liveness probe failed post-connect | §9.5 |
| `ScopeNotApproved` | `SCOPE_NOT_APPROVED` | `callTool` outside `approvedScopes` | §7.4 |
| `CapabilityMismatch` | `CAPABILITY_MISMATCH` | Claimed capability has no backing tool | §9.1 H13 |
| `OAuthUnavailable` | `OAUTH_UNAVAILABLE` | Host lacks MCP Apps + system browser | §17.5.1 |
| `RetryableOAuthFailure` | `OAUTH_SERVER_LOST` / `OAUTH_TIMEOUT` | Inline OAuth iframe/MCP server lost or timed out | §17.4 H10 |
| `ConsentFatigueWarning` | `CONSENT_FATIGUE` | >5 novel-server approvals in a session (advisory) | §7.3 |
| `PublisherKeyStale` | `PUBLISHER_KEY_STALE` | Publisher key failed to refresh after TTL | §10.9 |
| `DiscoveryDegraded` | `DISCOVERY_DEGRADED` | All registries unhealthy, falling back to cache/static | §8.5 |
| `UnsupportedFilter` | `UNSUPPORTED_FILTER` | Registry doesn't support a requested filter path | §6.3.1 |

---

## 10. Tool-usage logging (SPEC §7.6)

```typescript
interface ToolUsageEvent {
  serverId: string;
  toolName: string;
  approvedScopes: string[];
  approvedCapabilities: string[];
  argsRedacted: Record<string, unknown>;   // sensitive params redacted
  resultIsError: boolean;
  durationMs: number;
  timestamp: string;
  headless: boolean;
}
```

The `onToolUse` callback receives a `ToolUsageEvent` after every `callTool`. Sensitive parameters are redacted by default (configurable via `redactPatterns`).

---

## 11. Complete usage example

```typescript
import { PharosClient, ApprovalRequest, ApprovalResponse } from "@pharos/discovery";

async function main() {
  const pharos = new PharosClient({
    registryUrls: ["https://registry.pharos.dev"],
    agentId: "my-agent/0.1.0",
  });

  // 1. Search
  const results = await pharos.search({ text: "book a flight to Tokyo", limit: 5 });
  if (results.length === 0) {
    console.log("No servers found");
    return;
  }
  const best = results[0];

  // 2. Approve
  const approval = await pharos.requestApproval({
    server: best,
    purpose: "Book a flight to Tokyo",
    requestedScopes: ["flight_search"],
    requestedCapabilities: ["flight_search"],
    duration: "session",
    selectionRationale: "ranked #1; verified publisher",
    render: async (req) => {
      const ok = confirm(`Connect to ${req.server.displayName}? Scopes: ${req.requestedScopes.join(", ")}`);
      return {
        approved: ok,
        approvedScopes: ok ? req.requestedScopes : [],
        duration: req.duration,
      };
    },
  });
  if (!approval.approved || !approval.token) return;

  // 3. Connect + use
  const client = await pharos.connect(approval.token);
  const tools = await client.listTools();
  const result = await client.callTool("flight_search", { origin: "NYC", destination: "TYO" });
  console.log(result.content);

  // 4. Disconnect
  await client.close();
  pharos.revoke(approval.token);
}

main();
```

---

*See also: `docs/examples/QUICKSTART_TYPESCRIPT.md` for a step-by-step walkthrough; `.guides/backend/TYPESCRIPT_GUIDE.md` for conventions; `.guides/security/SECURITY_GUIDE.md` for the security model.*
