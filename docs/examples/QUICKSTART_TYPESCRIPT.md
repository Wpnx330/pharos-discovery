# Quickstart — TypeScript SDK

**Spec reference:** `SPEC.md` v0.4.0, §8.2 (TS surface), §7 (approval flow), §8.5 (config).
**Package:** `@pharos/discovery` on npm. **Node:** 20+; also works in modern browsers.

This walkthrough takes you from zero to a working agent that searches for an MCP server, gets user approval, connects, and calls a tool — in about 50 lines of TypeScript.

---

## 1. Install

```bash
npm install @pharos/discovery
# or
pnpm add @pharos/discovery
# or
yarn add @pharos/discovery
```

Optional: semantic re-ranking for the MCP Registry adapter (SPEC §11.3):
```bash
npm install onnxruntime-web
```

---

## 2. Minimal search

```typescript
import { PharosClient } from "@pharos/discovery";

async function main() {
  const pharos = new PharosClient({
    registryUrls: ["https://registry.pharos.dev"],
    agentId: "my-agent/0.1.0",
  });

  const results = await pharos.search({ text: "book a flight to Tokyo", limit: 5 });
  for (const card of results) {
    console.log(`${card.displayName} — ${card.publisher.name} ` +
      `(verified=${card.publisher.verified}, score=${card.pharosScore})`);
  }
}

main();
```

Expected output:
```
Acme Flights — Acme Inc. (verified=true, score=0.95)
FlyRight MCP — FlyRight (verified=true, score=0.87)
...
```

---

## 3. Full flow: search → approve → connect → call → disconnect

```typescript
import {
  PharosClient,
  ApprovalRequest, ApprovalResponse,
} from "@pharos/discovery";

// Host-supplied approval UX. The SDK calls this; you render it.
async function renderCli(req: ApprovalRequest): Promise<ApprovalResponse> {
  console.log("\n" + "=".repeat(60));
  console.log(`  Connect to ${req.server.displayName}?`);
  console.log(`  Publisher: ${req.server.publisher.name} ` +
    `(verified=${req.server.publisher.verified})`);
  console.log(`  Purpose:   ${req.purpose}`);
  console.log(`  Scopes:    ${req.requestedScopes.join(", ")}`);
  const pricing = req.server.pricing;
  console.log(`  Pricing:   ${pricing?.model ?? "unknown"}` +
    `${pricing ? (req.server.pricingVerified ? " (verified)" : " (vendor-claimed)") : ""}`);
  const rating = req.server.rating;
  console.log(`  Rating:    ${rating?.score ?? "n/a"} (${rating?.count ?? 0} reviews)`);
  console.log("=".repeat(60));

  // In a real app, render a UI card. Here we use a simple prompt.
  const ok = await askQuestion("Approve? [y/N]: ");
  const approved = ok.toLowerCase() === "y" || ok.toLowerCase() === "yes";
  return {
    approved,
    approvedScopes: approved ? req.requestedScopes : [],
    duration: req.duration,
  };
}

// Simple stdin prompt helper (Node)
import * as readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
function askQuestion(q: string): Promise<string> {
  const rl = readline.createInterface({ input, output });
  return new Promise((resolve) => rl.question(q, (ans) => { rl.close(); resolve(ans); }));
}

async function main() {
  const pharos = new PharosClient({
    registryUrls: ["https://registry.pharos.dev"],
    agentId: "my-agent/0.1.0",
  });

  // 1. Search
  console.log("Searching for flight-booking MCP servers...");
  const results = await pharos.search({
    text: "book a flight to Tokyo",
    filter: {
      transport: ["http+sse", "streamable-http"],
      publisherVerified: true,
    },
    limit: 5,
  });
  if (results.length === 0) {
    console.log("No servers found.");
    return;
  }

  const best = results[0];
  console.log(`Top result: ${best.displayName} (score=${best.pharosScore})`);

  // 2. Request approval (SDK calls our renderCli callback)
  const approval = await pharos.requestApproval({
    server: best,
    purpose: "Book a flight to Tokyo for the user's July 25 trip",
    requestedScopes: ["flight_search"],
    requestedCapabilities: ["flight_search"],
    duration: "session",
    selectionRationale: "ranked #1 for flight_search; verified publisher",
    render: renderCli,
  });

  if (!approval.approved || !approval.token) {
    console.log("User declined. Exiting.");
    return;
  }

  // 3. Connect (ApprovalToken required — no bypass)
  const client = await pharos.connect(approval.token);
  console.log(`Connected to ${best.displayName}.`);

  // 4. Use
  const tools = await client.listTools();
  console.log(`Available tools: ${tools.map((t) => t.name).join(", ")}`);

  const result = await client.callTool("flight_search", {
    origin: "NYC",
    destination: "TYO",
    date: "2026-07-25",
  });
  console.log(`Result: ${JSON.stringify(result.content)}`);

  // 5. Disconnect
  await client.close();
  pharos.revoke(approval.token);
  console.log("Disconnected.");
}

main();
```

---

## 4. Browser usage

The SDK works in modern browsers. Use a UI-based approval render:

```typescript
const pharos = new PharosClient({
  registryUrls: ["https://registry.pharos.dev"],
  agentId: "web-agent/0.1.0",
});

const results = await pharos.search({ text: "book a flight", limit: 5 });

const approval = await pharos.requestApproval({
  server: results[0],
  purpose: "Book a flight",
  requestedScopes: ["flight_search"],
  requestedCapabilities: ["flight_search"],
  duration: "session",
  selectionRationale: "top result",
  render: async (req) => {
    // Render a card in your UI; resolve when user clicks Approve/Reject
    return new Promise((resolve) => {
      showApprovalCard(req, {
        onApprove: () => resolve({
          approved: true,
          approvedScopes: req.requestedScopes,
          duration: req.duration,
        }),
        onReject: () => resolve({
          approved: false,
          approvedScopes: [],
          duration: req.duration,
          denyReason: "other",
        }),
      });
    });
  },
});
```

**Browser notes:**
- `fetch` streaming is used for SSE (`/v1/events`). Fallback to `EventSource` for older browsers.
- `crypto.subtle` (Web Crypto) is used for ed25519 signature verification.
- stdio transport (Phase 2) is Node-only and lazy-imported, so the browser build doesn't pull in `child_process`.
- Consent store uses IndexedDB-backed storage instead of filesystem (feature-detected).

---

## 5. Structured filters (SPEC §6.3.1)

```typescript
const results = await pharos.search({
  filter: {
    capabilities: ["flight_search", "flight_book"],
    transport: ["http+sse", "streamable-http"],
    publisherVerified: true,
    minRating: 4.0,
    pricingTier: ["free", "freemium"],
    availability: ["mirrored", "native"],
    dataResidency: ["EU"],
    protocolVersions: ["2025-03-26"],
  },
  limit: 10,
});
```

---

## 6. Privacy mode (SPEC §10.8)

```typescript
// Filters only, no text — lower recall, leaks no free-text intent
const pharos = new PharosClient({
  registryUrls: ["https://registry.pharos.dev"],
  agentId: "my-agent/0.1.0",
  privacyMode: true,
});
const results = await pharos.search({ filter: { capabilities: ["flight_search"] } });
```

Or blinded search (local embedding, no text leaves the device):
```typescript
const embedding = await pharos.embedLocally("book a flight to Tokyo");
const results = await pharos.search({ queryEmbedding: embedding, limit: 5 });
```

---

## 7. Headless mode (SPEC §7.5)

For pipelines/CI where no user is present. **Scoped, not blanket** — novel servers are refused.

```typescript
const pharos = new PharosClient({
  registryUrls: ["https://registry.pharos.dev"],
  agentId: "ci-pipeline/0.1.0",
  headlessMode: true,
  headlessAllowServers: ["urn:pharos:acme.com:travel/flight-booking"],
  headlessAllowScopes: ["flight_search"],
});

// This works (server on allow-list):
const results = await pharos.search({ filter: { capabilities: ["flight_search"] } });
const approval = await pharos.requestApproval({
  server: results[0],
  purpose: "automated flight search",
  requestedScopes: ["flight_search"],
  requestedCapabilities: ["flight_search"],
  duration: "session",
  selectionRationale: "allow-listed server for CI",
  render: autoApproveRender,  // auto-approves allow-listed servers
});

// A novel server NOT on the allow-list → HeadlessApprovalRequired error
```

---

## 8. Multi-server plans (SPEC §7.1.1)

```typescript
import { PlanApprovalRequest, PlanApprovalResponse } from "@pharos/discovery";

const flightResults = await pharos.search({ text: "book a flight", limit: 1 });
const expenseResults = await pharos.search({ text: "file an expense report", limit: 1 });

const steps = [...flightResults, ...expenseResults].map((card) => ({
  server: card,
  purpose: "Book travel and file expense",
  requestedScopes: card.id.includes("flight") ? ["flight_search"] : ["expense_create"],
  requestedCapabilities: card.id.includes("flight") ? ["flight_search"] : ["expense_create"],
  duration: "session" as const,
  renderId: `step-${card.id}`,
  selectionRationale: "part of travel-and-expense plan",
}));

const planResponse = await pharos.requestPlanApproval({
  planSummary: "Book your Tokyo flight and create the expense report",
  steps,
  render: renderPlanCli,  // (req: PlanApprovalRequest) => Promise<PlanApprovalResponse>
});
// One consent act for both servers — mitigates consent fatigue
```

---

## 9. Registry failover (SPEC §8.5, H7)

```typescript
const pharos = new PharosClient({
  registryUrls: [
    "https://registry.pharos.dev",
    "https://registry-eu.pharos.dev",
    "https://registry-backup.pharos.dev",
  ],
  agentId: "my-agent/0.1.0",
  staticFallbackServers: [...],  // /etc/hosts-style fallback; approval gate still enforced
});
// On 503/504/timeout from [0], mark unhealthy 60s, try [1], etc.
```

---

## 10. Error handling

```typescript
import {
  NoServersFound, RegistryUnavailable, HeadlessApprovalRequired,
  ConnectionFailed, ScopeNotApproved, DiscoveryDegraded,
} from "@pharos/discovery";

try {
  const results = await pharos.search({ text: "book a flight", limit: 5 });
} catch (e) {
  if (e instanceof NoServersFound) {
    console.log("No matching servers. Try broadening your query.");
  } else if (e instanceof RegistryUnavailable) {
    console.log("All registries unavailable and no cache. Try later.");
  } else if (e instanceof DiscoveryDegraded) {
    console.log("Registries degraded — using cached/static fallback.");
  } else {
    throw e;
  }
}

try {
  const result = await client.callTool("flight_book", { flightId: "ABC123" });
} catch (e) {
  if (e instanceof ScopeNotApproved) {
    console.log("User didn't approve the flight_book scope. Re-prompt?");
    // Scope re-negotiation: rate-limited to 1 per server per session (§7.7)
  } else {
    throw e;
  }
}
```

---

## 11. Next steps

- **Full API reference:** `docs/api/TYPESCRIPT_API.md`
- **Security model:** `.guides/security/SECURITY_GUIDE.md`
- **Architecture:** `docs/technical/SYSTEM_ARCHITECTURE.md`
- **Conventions:** `.guides/backend/TYPESCRIPT_GUIDE.md`
- **Troubleshooting:** `docs/troubleshooting/COMMON_ISSUES.md`

---

*Phase 1 ships with `auth.type: "none"` and `"api_key"` only. OAuth (App Registration Inheritance) arrives in Phase 2 — see `docs/components/OAUTH_BROKERING.md`.*
