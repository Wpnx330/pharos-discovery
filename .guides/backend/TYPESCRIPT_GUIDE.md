# TypeScript SDK Guide — `@pharos/discovery`

**Audience:** AI agents and contributors working on the TypeScript SDK.
**Source of truth:** `SPEC.md` v0.4.0, §8.2 (TS surface), §8.3 (core types), §8.5 (config), §8.6 (IDL), §9 (transport), §10 (security), §11 (adapters).
**Companion:** `.guides/architecture/OVERVIEW.md`; `docs/api/TYPESCRIPT_API.md`.

---

## 1. Package identity & layout

- **Name:** `@pharos/discovery` (npm)
- **Import root:** `@pharos/discovery`
- **Node:** 20+; **browsers:** modern (ESM). No DOM dependency — the approval UX is host-supplied.
- **TypeScript:** 5.x, `strict: true`.

```
packages/typescript/
├── package.json
├── tsconfig.json
├── tsup.config.ts
├── src/
│   ├── index.ts               # re-exports public API
│   ├── client.ts              # PharosClient
│   ├── search/
│   │   ├── query.ts           # SearchQuery, QueryBuilder
│   │   └── ranking.ts
│   ├── approval/
│   │   ├── engine.ts          # requestApproval, PlanApproval
│   │   ├── token.ts           # ApprovalToken (signed, ed25519 via Web Crypto)
│   │   └── renderers/
│   │       └── cli.ts         # Node CLI approval renderer (Phase 1 default)
│   ├── connection/
│   │   ├── manager.ts         # connect(), MCPClient
│   │   ├── transports/
│   │   │   ├── stdio.ts       # Phase 2 (Node child_process)
│   │   │   ├── httpSse.ts     # Phase 1 (fetch + ReadableStream)
│   │   │   └── streamable.ts  # Phase 1
│   │   └── oauth/             # Phase 2
│   │       ├── handler.ts     # OAuthFlowHandler, DefaultOAuthFlowHandler
│   │       └── result.ts      # OAuthResult, OAuthStatus, RevocationResult
│   ├── adapters/
│   │   ├── base.ts            # RegistryAdapter (abstract/interface)
│   │   ├── pharos.ts          # PharosRegistryAdapter
│   │   ├── mcpOfficial.ts     # MCPRegistryAdapter + client-side re-rank
│   │   └── ard.ts             # Phase 2
│   ├── models/                # IDL-generated TS types + zod schemas
│   │   ├── serverCard.ts
│   │   ├── approval.ts
│   │   └── oauth.ts
│   ├── security/
│   │   ├── signatures.ts      # Web Crypto SubtleCrypto ed25519 verify
│   │   ├── blocklist.ts
│   │   ├── keyPin.ts
│   │   └── consentStore.ts    # append-only, signed
│   ├── cache.ts               # ServerCard cache, ETag/If-None-Match
│   ├── events.ts              # SSE /v1/events subscriber
│   ├── errors.ts              # typed error classes
│   └── version.ts
├── test/                      # vitest
│   ├── unit/
│   ├── integration/
│   └── conformance/           # shared golden fixtures (SPEC §8.6)
├── dist/                      # build output (ESM + CJS + .d.ts)
└── README.md
```

> `src/models/` is **IDL-generated** (TypeSpec → TS types + zod). Do not hand-edit.

---

## 2. Dual build — ESM + CJS via tsup

`tsup.config.ts`:
```ts
import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm", "cjs"],
  dts: true,                           // tsc --emitDeclarationOnly under the hood
  sourcemap: true,
  clean: true,
  target: "node20",
  platform: "node",                    // browser build is a separate entry if needed
  treeshake: true,
  banner: { js: "\"use strict\";" },   // CJS banner
});
```

`package.json` (relevant fields):
```json
{
  "name": "@pharos/discovery",
  "version": "0.1.0",
  "type": "module",
  "main": "./dist/index.cjs",
  "module": "./dist/index.js",
  "types": "./dist/index.d.ts",
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "import": "./dist/index.js",
      "require": "./dist/index.cjs"
    }
  },
  "engines": { "node": ">=20" },
  "files": ["dist", "README.md", "LICENSE"]
}
```

- **Browser compat:** no Node-only imports (`fs`, `child_process`, `crypto`) at the top level. Use feature detection: `typeof globalThis.crypto?.subtle === "object"` → Web Crypto; else Node `crypto.webcrypto`. stdio transport (Phase 2) is Node-only and lazy-imported.
- **No DOM dependency.** The approval UX is host-supplied; the SDK returns JSON payloads for the host to render.

---

## 3. Dependencies (minimal surface — SPEC §4.8)

Required:
- `zod` — runtime validation alongside IDL-generated types
- (that's it for runtime — `fetch`, `crypto.subtle`, `ReadableStream` are all platform-native in Node 20+ and browsers)

Optional (feature-gated):
- `onnxruntime-web` — bundled `all-MiniLM-L6-v2` for MCP-Registry-adapter client-side semantic re-ranking (SPEC §11.3). Absent → substring-only (labeled "non-semantic"). Never route discovery ranking through a third-party LLM.

Dev:
- `typescript` 5.x, `tsup`, `vitest`, `@types/node`

**No hard dependency on any LLM client SDK.** Works with the Anthropic SDK, OpenAI SDK, raw fetch, or a custom agent loop (SPEC §8.4).

---

## 4. Public surface (canonical usage — SPEC §8.2)

```typescript
import { PharosClient, ApprovalRequest, ApprovalResponse } from "@pharos/discovery";

const pharos = new PharosClient({
  registryUrls: ["https://registry.pharos.dev"],
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
  selectionRationale: "ranked #1 for flight_search; verified publisher; supports bookings:write",
  render: presentToUser,  // (req: ApprovalRequest) => Promise<ApprovalResponse>
});
if (!approval.approved) return;

// 4. Connect
const client = await pharos.connect(approval);

// 5. Use
const tools = await client.listTools();
const result = await client.callTool("flight_search", { origin: "NYC", destination: "TYO", date: "2026-07-25" });

// 6. Disconnect
await client.close();
pharos.revoke(approval);
```

**Naming:** `camelCase` throughout (SPEC §8.2). `pharos.search({ text, filter })`, `requestApproval(...)`, `callTool(...)`. The IDL defines canonical field names; the TS emitter produces `camelCase` variants while zod schemas accept both shapes for wire compat.

---

## 5. Core types (IDL-generated — SPEC §8.3)

```typescript
// src/models/serverCard.ts (IDL-generated)
export interface Publisher {
  id: string;                       // did:web:<fqdn>
  name: string;
  verified?: boolean;
  verificationMethod?: "domain_control" | "identity";
  contact?: string | null;
}

export interface AppRegistration {
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
  // NOTE: no client_secret — never present (SPEC §10.5, §17.2)
}

export interface AuthSpec {
  type: "none" | "api_key" | "oauth" | "mtls";
  secretHandling?: "server_side" | "agent_side";
  appRegistration?: AppRegistration;     // present when type=="oauth" && server_side
  ui?: { resourceUri: string; csp: string };
  scopes?: string[];                      // legacy/display for OAuth; authoritative for non-OAuth
  // ... legacy fields per Appendix A
}

export interface ServerCard {
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
  pharosScore: number | null;              // 0.0–1.0; NOT trust; null for ARD
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

```typescript
// src/models/approval.ts (IDL-generated)
export interface ApprovalRequest {
  server: ServerCard;
  purpose: string;
  requestedScopes: string[];
  requestedCapabilities: string[];
  duration: "once" | "session" | "persistent" | "trust_on_use";
  renderId: string;
  selectionRationale: string;   // MANDATORY (§7.1.1)
}

export interface ApprovalResponse {
  approved: boolean;
  approvedScopes: string[];
  duration: string;
  userNote?: string | null;
  denyReason?: "untrusted_publisher" | "excessive_scopes" | "wrong_server" | "cost" | "other" | null;
}

export interface ApprovalToken {
  tokenId: string;
  serverId: string;
  approvedScopes: string[];
  approvedCapabilities: string[];
  approvedOauthScopes: string[];   // [] if auth.type != "oauth"
  duration: string;
  approvedAt: string;
  expiresAt: string;
  signature: string;               // ed25519 over token body
}
```

See `docs/api/TYPESCRIPT_API.md` for the full `MCPClient`, `OAuthFlowHandler`, `OAuthResult`, `RevocationResult`, `PlanApprovalRequest`/`Response`.

---

## 6. `PharosClient` configuration (SPEC §8.5)

```typescript
new PharosClient({
  registryUrls: ["https://registry.pharos.dev", "https://registry-eu.pharos.dev"],  // H7 failover
  agentId: "my-agent/0.1.0",
  apiKey: undefined,
  consentStore: "~/.pharos/consent.json",
  blocklistUrl: "https://registry.pharos.dev/v1/blocklist",
  blocklistCacheTtlSeconds: 60,    // H9
  cacheTtlSeconds: 300,
  cacheConditional: true,          // ETag / If-None-Match
  eventsEndpoint: "https://registry.pharos.dev/v1/events",
  federationMode: "auto",
  maxReferralDepth: 2,
  searchTimeout: 10,
  getTimeout: 10,
  connectTimeout: 10,
  oauthTimeout: 120,               // Phase 2
  approvalTimeout: 300,
  initializeTimeout: 10,           // H8
  toolCallTimeout: 30,             // H8
  heartbeatInterval: 30,           // HTTP/SSE only
  healthCheckInterval: 60,
  verifySignatures: true,
  allowUnverified: false,
  headlessMode: false,
  headlessAllowServers: [],        // required when headlessMode: true
  headlessAllowScopes: [],
  privacyMode: false,              // §10.8
  onToolUse: undefined,            // (e: ToolUsageEvent) => void
  onCapabilityGap: undefined,      // (ctx: Record<string, unknown>) => CapabilityGap | null
  staticFallbackServers: [],
});
```

**Registry failover (H7):** on 503/504/timeout from `registryUrls[0]`, mark unhealthy 60s, try `[1]`, etc. All unhealthy + no cache → `DiscoveryDegraded`; host MAY use `staticFallbackServers` (approval gate still enforced).

---

## 7. Async model — Promise + AbortController

- All public I/O methods return `Promise<T>`. No callbacks-as-primary-API (the approval `render` callback is the one exception, returning a `Promise<ApprovalResponse>`).
- **Timeouts via `AbortController` + `AbortSignal.timeout(ms)`** (Node 20+ and browsers). Never `setTimeout` + manual reject — use the platform primitive. Compose with `AbortSignal.any([userSignal, AbortSignal.timeout(ms)])` when the caller also wants cancellation.
- **No `anyio` equivalent needed.** Node's event loop + `AbortController` covers the cases anyio covers in Python. For concurrent fan-out (e.g. querying multiple registry adapters in `federation: "auto"`), use `Promise.allSettled`.
- **stdio (Phase 2):** `child_process.spawn()`; newline-delimited JSON-RPC over stdin/stdout. Node-only — lazy-import inside the stdio transport module so the browser build doesn't pull in `child_process`.

---

## 8. HTTP & SSE — fetch + ReadableStream

- Use global `fetch` (Node 20+ undici, browser native). Set headers: `X-Pharos-Version`, `Accept: application/json`, `Content-Type: application/json` for POSTs.
- **429 handling (SPEC §13.5):** honor `Retry-After`; else exponential backoff with full jitter (initial 500ms, factor 2, cap 30s, max 4 retries). On repeated 429 → cached `ServerCard` results + `registryDegraded: true`. Registry + cache both exhausted → throw `RegistryUnavailable` (never silent empty).
- **Conditional cache:** `cacheConditional: true` → send `If-None-Match` / `If-Modified-Since`; 304 → reuse cached card.
- **SSE (`/v1/events`, SPEC §6.7):** `fetch` with `Accept: text/event-stream` → read `response.body` (ReadableStream) → parse `event:`/`data:` lines. Reconnect w/ exponential backoff (1s→60s) + `Last-Event-ID`. Missed `ping` beyond `healthCheckInterval` → dead stream, reconnect. **Optimization, not correctness** — fall back to TTL polling.
  - Browser note: `EventSource` is simpler but doesn't support custom headers and has limited reconnection control. Prefer `fetch` streaming for consistency between Node and browser; use `EventSource` only as a fallback for browsers that don't support fetch streaming.

---

## 9. The approval flow in TypeScript (SPEC §7)

```typescript
async requestApproval(opts: {
  server: ServerCard;
  purpose: string;
  requestedScopes: string[];
  requestedCapabilities: string[];
  duration: "once" | "session" | "persistent" | "trust_on_use";
  selectionRationale: string;   // MANDATORY
  render: (req: ApprovalRequest) => Promise<ApprovalResponse>;
  oauthConsentDefaultsOverride?: string[];  // Phase 2
}): Promise<ApprovalResult>
```

- Build `ApprovalRequest` (reject if `selectionRationale` empty and not `headlessMode`).
- Call host `render` callback; on `approvalTimeout` → `{ approved: false, denyReason: "timeout" }`.
- On approve: mint signed `ApprovalToken` (ed25519 via Web Crypto `SubtleCrypto.sign`). Store in consent store (append-only, signed). Optionally POST `/v1/approve` for registry audit (opt-in).
- **`trustOnUse` (§7.3):** after one successful `callTool` against `verified=true` + `availability="mirrored"` + `rating.count > 100`, re-connect within 7 days auto-approves non-modally. Disabled for high-risk scopes. Mutually exclusive with `headlessMode`.
- **`PlanApproval` (§7.1.1):** `requestPlanApproval({ planSummary, steps: ApprovalRequest[] })` → `PlanApprovalResponse` with `perStep` responses; mints `PlanApprovalToken` (batch sharing `planId`).

**Headless mode (§7.5):**
```typescript
const pharos = new PharosClient({
  headlessMode: true,
  headlessAllowServers: ["urn:pharos:acme.com:travel/flight-booking"],
  headlessAllowScopes: ["flight_search"],
});
// Novel server NOT on allow-list → throws HeadlessApprovalRequired.
```

---

## 10. Connection Manager — `MCPClient` (SPEC §8.3, §9)

```typescript
class MCPClient {
  readonly server: ServerCard;
  readonly approval: ApprovalToken;
  readonly protocolVersion: string;
  readonly serverCapabilities: Record<string, unknown>;

  async listTools(): Promise<Tool[]>;
  async callTool(name: string, args: Record<string, unknown>, opts?: { timeout?: number }): Promise<ToolResult>;
  async listResources(): Promise<Resource[]>;
  async readResource(uri: string): Promise<string>;
  async listPrompts(): Promise<Prompt[]>;
  async close(): Promise<void>;
}
```

- `connect(approval)` selects transport from `server.transport[0]` + `server.endpoint` / `server.stdioCommand`.
- If `server.auth.type === "oauth"` (Phase 2): `OAuthFlowHandler.authorize()` runs **first**, before `initialize`.
- After `tools/list`: verify claimed `capabilities` backed by actual tools (H13). Unbacked → `CapabilityMismatch`, downgrade in `verifiedCapabilities`.
- Enforce `approvedScopes` / `approvedCapabilities` on every `callTool`. Out-of-scope → `ScopeNotApproved` error.
- At most one live `MCPClient` per `serverId` per session; repeated `connect()` with valid token returns cached client.
- Liveness: `healthCheckInterval` → `ConnectionLost(serverId, lastSeen)`. **Never auto-reconnect** without fresh approval.

---

## 11. Crypto — Web Crypto + Node fallback

- **ed25519 verify** via `globalThis.crypto.subtle.verifyKey(...)` (Web Crypto; Node 20+ exposes `crypto.webcrypto.subtle`). Fallback: Node `crypto.verify` with ed25519 key objects.
- **Key pin TTL** (`keyPinTtlSeconds`, default 86400): re-fetch `.well-known/pharos-pubkey.json` on TTL; WHOIS change → immediate re-verify. Failed re-fetch after TTL → server quarantined. (§10.9)
- **`client_secret` NEVER appears in any TS type.** `AuthSpec.appRegistration` carries `clientId`, endpoints, scopes, consentDefaults — never the secret. (§10.5)
- **Consent store** (`~/.pharos/consent.json`): append-only, signed with local SDK key (ed25519). Browser builds use IndexedDB-backed storage instead of filesystem — guarded by feature detection.

---

## 12. Registry adapters in TypeScript (SPEC §11)

```typescript
export interface RegistryAdapter {
  readonly name: "pharos" | "mcp-official" | "ard" | string;
  readonly capabilities: Set<AdapterCapability>;
  search(query: SearchQuery): Promise<ServerCard[]>;
  get(serverId: string): Promise<ServerCard>;
  publish?(card: ServerCard): Promise<string>;
  report?(serverId: string, reason: string): Promise<void>;
  toCanonical(native: Record<string, unknown>): ServerCard;
  fromCanonical?(card: ServerCard): Record<string, unknown>;
}

export type AdapterCapability =
  | "semantic_search" | "filter_search" | "reviews" | "pricing"
  | "federation" | "publish" | "report" | "blinded_search"
  | "push_events" | "key_pinning";
```

- **`PharosRegistryAdapter`** (Phase 1): native §6 API; full capability set.
- **`MCPRegistryAdapter`** (Phase 1): `GET /v0.1/servers?search=<text>` → canonical; client-side re-rank via bundled ONNX (if `onnxruntime-web` installed); else substring-only.
- **`ARDAdapter`** (Phase 2): `POST /search` → canonical; `urn:air:` → `urn:pharos:` (preserve `sourceUrn`); `score` (0–100) → `sourceScore`; `pharosScore = null`. **Never normalize** (§11.4).

---

## 13. Errors (SPEC §6.13, §7, §9.5, §17.5.1)

Typed error classes in `src/errors.ts` (each carries the SPEC section for traceability):

```typescript
export class PharosError extends Error {
  constructor(message: string, public code: string, public specRef?: string) { super(message); this.name = this.constructor.name; }
}
export class RegistryUnavailable extends PharosError { /* §13.5 */ }
export class NoServersFound extends PharosError { /* §7.1.1 */ }
export class HeadlessApprovalRequired extends PharosError { /* §7.5 */ }
export class ConnectionFailed extends PharosError { /* §9.1 */ }   // code: "initialize_timeout" | ...
export class ConnectionLost extends PharosError { /* §9.5 */ }
export class ScopeNotApproved extends PharosError { /* §7.4 */ }
export class CapabilityMismatch extends PharosError { /* §9.1 H13 */ }
export class OAuthUnavailable extends PharosError { /* §17.5.1 */ }
export class RetryableOAuthFailure extends PharosError { /* §17.4 H10 */ }
export class ConsentFatigueWarning extends PharosError { /* §7.3 */ }
export class PublisherKeyStale extends PharosError { /* §10.9 */ }
export class DiscoveryDegraded extends PharosError { /* §8.5 */ }
export class UnsupportedFilter extends PharosError { /* §6.3.1 */ }
```

---

## 14. Conventions checklist (TypeScript)

- [ ] `camelCase` everywhere (SPEC §8.2).
- [ ] All I/O returns `Promise`; timeouts via `AbortController` / `AbortSignal.timeout`.
- [ ] IDL-generated types + zod; never hand-edit `src/models/`.
- [ ] `strict: true`; no `any` in public API; `string | null` not `string | undefined` for nullable model fields (match IDL).
- [ ] No `client_secret` in any type, log, or error.
- [ ] Every `callTool` logged with redacted sensitive params (§7.6).
- [ ] No unbounded awaits — every I/O has a timeout (H8).
- [ ] Dual build (ESM + CJS) via tsup; browser build free of Node-only imports.
- [ ] Public surface change → IDL first, regenerate, update `docs/api/TYPESCRIPT_API.md`.
- [ ] Tests: vitest; conformance fixtures shared with Python (§8.6).

---

*Next: `docs/api/TYPESCRIPT_API.md`; `.guides/testing/TESTING_GUIDE.md`; `.guides/security/SECURITY_GUIDE.md`.*
