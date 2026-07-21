import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { PharosClient } from "../../src/client.js";
import { ConnectionManager } from "../../src/connection/manager.js";
import { ApprovalEngine } from "../../src/approval/engine.js";
import type { ServerCard, ApprovalRequest, ApprovalResponse } from "../../src/models/index.js";
import {
  ApprovalDenied, HeadlessApprovalRequired, ScopeNotApproved,
  NoServersFound, RegistryUnavailable, DiscoveryDegraded,
} from "../../src/errors.js";

function makeServerCard(serverId = "urn:pharos:server-001", name = "Flight Search MCP"): ServerCard {
  return {
    id: serverId,
    display_name: name,
    description: "Search and book flights",
    publisher: { id: "did:web:flights.example.com", name: "FlightCo", verified: true },
    version: "2.1.0",
    transport: ["http+sse"],
    endpoint: "https://mcp.flights.example.com/sse",
    capabilities: ["search", "book"],
    tools_count: 5,
    auth: { type: "oauth", scopes: ["flights:search", "flights:book"] },
    availability: "native",
    source_registry: "https://registry.pharos.dev",
    published_at: "2026-06-15T00:00:00Z",
    updated_at: "2026-07-10T00:00:00Z",
    status: "active",
    tags: ["travel", "flights"],
  } as ServerCard;
}

class RecordingApprovalHandler {
  autoApprove: boolean;
  approvedScopes: string[];
  calls: ApprovalRequest[] = [];

  constructor(autoApprove = true, approvedScopes = ["flights:search"]) {
    this.autoApprove = autoApprove;
    this.approvedScopes = approvedScopes;
  }

  async requestApproval(req: ApprovalRequest): Promise<ApprovalResponse> {
    this.calls.push(req);
    return {
      approved: this.autoApprove,
      approved_scopes: this.autoApprove ? this.approvedScopes : [],
      duration: "session",
      deny_reason: this.autoApprove ? undefined : "excessive_scopes",
    };
  }
}

function mockFetch(data: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: new Map(),
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("E2E: Full Discovery Flow", () => {
  it("search → approve → connect → send → disconnect", async () => {
    const card = makeServerCard();
    const approvalHandler = new RecordingApprovalHandler();
    const connectionManager = new ConnectionManager();

    const client = new PharosClient("https://registry.pharos.dev", {
      approvalHandler,
      connectionHandler: connectionManager,
    });

    // Search
    global.fetch = mockFetch({ results: [card] });
    const results = await client.search("flights");
    expect(results).toHaveLength(1);
    expect(results[0].card.display_name).toBe("Flight Search MCP");

    // Approve + Connect (blocklist mock returns empty)
    global.fetch = mockFetch({ blocked: [] });
    const { token, connection } = await client.connectAndApprove(
      results[0].card,
      "Search for flights to Tokyo",
      { requestedScopes: ["flights:search"] },
    );

    expect(token.server_id).toBe("urn:pharos:server-001");
    expect(token.approved_scopes).toContain("flights:search");
    expect(approvalHandler.calls[0].purpose).toBe("Search for flights to Tokyo");
    expect(connectionManager.activeCount).toBe(1);

    // Send
    const response = await connectionManager.send("urn:pharos:server-001", {
      id: "req-1", method: "search", params: { destination: "Tokyo" },
    });
    expect(response.status).toBe("ok");

    // Check scope
    await expect(client.checkScope("urn:pharos:server-001", "flights:search")).resolves.toBeUndefined();

    // Revoke
    await client.revoke("urn:pharos:server-001");
    expect(connectionManager.activeCount).toBe(0);
  });

  it("cache fallback when registry goes down", async () => {
    const card = makeServerCard();
    const client = new PharosClient("https://registry.pharos.dev", {
      approvalHandler: new RecordingApprovalHandler(),
    });

    // First search succeeds and caches
    global.fetch = mockFetch({ results: [card] });
    const results = await client.search("flights");
    expect(results).toHaveLength(1);

    // Second search: registry down, cache provides results
    global.fetch = vi.fn().mockRejectedValue(new RegistryUnavailable("url", 503));
    const cached = await client.search("flights");
    expect(cached).toHaveLength(1);
    expect(cached[0].card.id).toBe(card.id);
  });
});

describe("E2E: Approval Flow Variations", () => {
  it("approval denied", async () => {
    const card = makeServerCard();
    const handler = new RecordingApprovalHandler(false);
    const client = new PharosClient("https://registry.pharos.dev", {
      approvalHandler: handler,
    });

    global.fetch = mockFetch({ blocked: [] });
    await expect(client.connectAndApprove(card, "test")).rejects.toThrow(ApprovalDenied);
  });

  it("headless mode blocks novel server", async () => {
    const card = makeServerCard();
    const client = new PharosClient("https://registry.pharos.dev", {
      headless: true,
    });

    await expect(client.connectAndApprove(card, "test")).rejects.toThrow(HeadlessApprovalRequired);
  });

  it("blocklisted server rejected", async () => {
    const card = makeServerCard("urn:pharos:malicious-1", "Evil MCP");
    const client = new PharosClient("https://registry.pharos.dev", {
      approvalHandler: new RecordingApprovalHandler(),
    });

    global.fetch = mockFetch({ blocked: ["urn:pharos:malicious-1"] });
    await expect(client.connectAndApprove(card, "test")).rejects.toThrow(ApprovalDenied);
  });

  it("scope checking enforces approved scopes", async () => {
    const card = makeServerCard();
    const handler = new RecordingApprovalHandler(true, ["flights:search"]);
    const connMgr = new ConnectionManager();
    const client = new PharosClient("https://registry.pharos.dev", {
      approvalHandler: handler,
      connectionHandler: connMgr,
    });

    global.fetch = mockFetch({ blocked: [] });
    await client.connectAndApprove(card, "search flights", { requestedScopes: ["flights:search"] });

    // Approved scope
    await expect(client.checkScope(card.id, "flights:search")).resolves.toBeUndefined();

    // Non-approved scope
    await expect(client.checkScope(card.id, "flights:book")).rejects.toThrow(ScopeNotApproved);
  });
});

describe("E2E: Approval Engine Integration", () => {
  it("create and verify token", async () => {
    const engine = new ApprovalEngine("integration-test-secret");

    const card = makeServerCard();
    const request: ApprovalRequest = {
      server: card,
      purpose: "test",
      requested_scopes: ["flights:search"],
      requested_capabilities: ["search"],
      duration: "session",
      render_id: "render-001",
      selection_rationale: "integration test",
    };
    const response: ApprovalResponse = {
      approved: true,
      approved_scopes: ["flights:search"],
      duration: "session",
    };

    const token = await engine.createToken(request, response);
    expect(await engine.verifyToken(token)).toBe(true);
    expect(engine.isExpired(token)).toBe(false);
  });

  it("tampered token rejected", async () => {
    const engine = new ApprovalEngine("integration-test-secret");

    const card = makeServerCard();
    const request: ApprovalRequest = {
      server: card,
      purpose: "test",
      requested_scopes: ["flights:search"],
      requested_capabilities: ["search"],
      duration: "session",
      render_id: "render-001",
      selection_rationale: "integration test",
    };
    const response: ApprovalResponse = {
      approved: true,
      approved_scopes: ["flights:search"],
      duration: "session",
    };

    const token = await engine.createToken(request, response);
    token.approved_scopes = ["admin"];
    expect(await engine.verifyToken(token)).toBe(false);
  });
});

describe("E2E: Multi-Server Flow", () => {
  it("two servers managed simultaneously", async () => {
    const card1 = makeServerCard("urn:pharos:server-001", "Flight Search");
    const card2 = makeServerCard("urn:pharos:server-002", "Hotel Search");
    card2.capabilities = ["search"];
    card2.auth.scopes = ["hotels:search"];

    const handler = new RecordingApprovalHandler(true, ["search"]);
    const connMgr = new ConnectionManager();
    const client = new PharosClient("https://registry.pharos.dev", {
      approvalHandler: handler,
      connectionHandler: connMgr,
      maxNovelApprovals: 10,
    });

    global.fetch = mockFetch({ blocked: [] });
    const { token: t1 } = await client.connectAndApprove(card1, "flights", { requestedScopes: ["search"] });
    const { token: t2 } = await client.connectAndApprove(card2, "hotels", { requestedScopes: ["search"] });

    expect(t1.server_id).toBe("urn:pharos:server-001");
    expect(t2.server_id).toBe("urn:pharos:server-002");
    expect(connMgr.activeCount).toBe(2);

    // Both alive
    const health = await connMgr.healthCheckAll();
    expect(Object.keys(health)).toHaveLength(2);
    expect(Object.values(health).every(Boolean)).toBe(true);

    // Close all
    await client.close();
    expect(connMgr.activeCount).toBe(0);
  });
});
