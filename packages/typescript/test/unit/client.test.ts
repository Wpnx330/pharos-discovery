import { describe, it, expect, vi, beforeEach } from "vitest";
import { PharosClient } from "../../src/client.js";
import { PharosRegistryAdapter, type SearchResult } from "../../src/adapters/registry.js";
import type { ServerCard, ApprovalRequest, ApprovalResponse, ApprovalToken } from "../../src/models/index.js";
import {
  ApprovalDenied, ConnectionFailed, ConsentFatigueWarning,
  DiscoveryDegraded, HeadlessApprovalRequired, NoServersFound,
  RegistryUnavailable, ScopeNotApproved,
} from "../../src/errors.js";

function makeCard(serverId = "urn:pharos:server-001"): ServerCard {
  return {
    id: serverId,
    display_name: "Test Server",
    description: "A test server",
    publisher: { id: "did:web:example.com", name: "TestPub" },
    version: "1.0.0",
    transport: ["http+sse"],
    capabilities: ["search"],
    tools_count: 3,
    auth: { type: "none" },
    availability: "native",
    source_registry: "https://registry.pharos.dev",
    published_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    status: "active",
  } as ServerCard;
}

function makeApprovalResponse(approved = true, scopes = ["search"]): ApprovalResponse {
  return {
    approved,
    approved_scopes: scopes,
    duration: "session",
    deny_reason: approved ? undefined : "excessive_scopes",
  };
}

class MockApprovalHandler {
  response: ApprovalResponse;
  calls: ApprovalRequest[] = [];
  constructor(response?: ApprovalResponse) {
    this.response = response ?? makeApprovalResponse();
  }
  async requestApproval(req: ApprovalRequest): Promise<ApprovalResponse> {
    this.calls.push(req);
    return this.response;
  }
}

class MockConnectionHandler {
  connectCalls: Array<{ card: ServerCard; token: ApprovalToken }> = [];
  disconnectCalls: unknown[] = [];
  async connect(card: ServerCard, token: ApprovalToken): Promise<unknown> {
    this.connectCalls.push({ card, token });
    return { id: `conn-${card.id}` };
  }
  async disconnect(conn: unknown): Promise<void> {
    this.disconnectCalls.push(conn);
  }
}

function makeClient(opts: { headless?: boolean; maxNovel?: number; approved?: boolean } = {}) {
  const approvalHandler = new MockApprovalHandler(
    opts.approved === false ? makeApprovalResponse(false) : makeApprovalResponse()
  );
  const connectionHandler = new MockConnectionHandler();
  const client = new PharosClient("https://registry.pharos.dev", {
    approvalHandler,
    connectionHandler,
    headless: opts.headless,
    maxNovelApprovals: opts.maxNovel ?? 5,
  });
  return { client, approvalHandler, connectionHandler };
}

describe("PharosClient", () => {
  describe("search", () => {
    it("returns results and caches cards", async () => {
      const { client } = makeClient();
      const card = makeCard();
      vi.spyOn(client["adapter"], "search").mockResolvedValue([{ card, score: 1.0 }]);
      const results = await client.search("test");
      expect(results).toHaveLength(1);
      expect(results[0].card.id).toBe(card.id);
      expect(client.cacheInstance.size).toBe(1);
    });

    it("falls back to cache on RegistryUnavailable", async () => {
      const { client } = makeClient();
      const card = makeCard();
      client.cacheInstance.put(card.id, card);
      vi.spyOn(client["adapter"], "search").mockRejectedValue(new RegistryUnavailable("url", 503));
      const results = await client.search("test");
      expect(results).toHaveLength(1);
      expect(results[0].card.id).toBe(card.id);
    });

    it("throws DiscoveryDegraded when no cache", async () => {
      const { client } = makeClient();
      vi.spyOn(client["adapter"], "search").mockRejectedValue(new RegistryUnavailable("url", 503));
      await expect(client.search("test")).rejects.toThrow(DiscoveryDegraded);
    });

    it("propagates NoServersFound", async () => {
      const { client } = makeClient();
      vi.spyOn(client["adapter"], "search").mockRejectedValue(new NoServersFound("test"));
      await expect(client.search("test")).rejects.toThrow(NoServersFound);
    });
  });

  describe("connectAndApprove", () => {
    it("full flow returns token and connection", async () => {
      const { client, connectionHandler } = makeClient();
      vi.spyOn(client["adapter"], "getBlocklist").mockResolvedValue([]);
      const card = makeCard();
      const { token, connection } = await client.connectAndApprove(card, "test purpose");
      expect(token.server_id).toBe(card.id);
      expect(token.approved_scopes).toEqual(["search"]);
      expect(connection).toEqual({ id: `conn-${card.id}` });
      expect(connectionHandler.connectCalls).toHaveLength(1);
    });

    it("headless mode throws HeadlessApprovalRequired", async () => {
      const { client } = makeClient({ headless: true });
      const card = makeCard();
      await expect(client.connectAndApprove(card, "test")).rejects.toThrow(HeadlessApprovalRequired);
    });

    it("approval denied", async () => {
      const { client } = makeClient({ approved: false });
      vi.spyOn(client["adapter"], "getBlocklist").mockResolvedValue([]);
      const card = makeCard();
      await expect(client.connectAndApprove(card, "test")).rejects.toThrow(ApprovalDenied);
    });

    it("blocklisted server rejected", async () => {
      const { client } = makeClient();
      vi.spyOn(client["adapter"], "getBlocklist").mockResolvedValue(["urn:pharos:bad-1"]);
      const card = makeCard("urn:pharos:bad-1");
      await expect(client.connectAndApprove(card, "test")).rejects.toThrow(ApprovalDenied);
    });

    it("cached approval reused", async () => {
      const { client, connectionHandler } = makeClient();
      vi.spyOn(client["adapter"], "getBlocklist").mockResolvedValue([]);
      const card = makeCard();
      const { token: t1 } = await client.connectAndApprove(card, "test");
      const { token: t2 } = await client.connectAndApprove(card, "test");
      expect(t1.token_id).toBe(t2.token_id);
      // Connection handler called twice (two connect calls)
      expect(connectionHandler.connectCalls).toHaveLength(2);
    });

    it("consent fatigue after max novel", async () => {
      const { client } = makeClient({ maxNovel: 2 });
      vi.spyOn(client["adapter"], "getBlocklist").mockResolvedValue([]);
      for (let i = 0; i < 3; i++) {
        const card = makeCard(`urn:pharos:server-${i}`);
        if (i < 2) {
          await client.connectAndApprove(card, "test");
        } else {
          await expect(client.connectAndApprove(card, "test")).rejects.toThrow(ConsentFatigueWarning);
        }
      }
    });
  });

  describe("revoke", () => {
    it("removes approval and disconnects", async () => {
      const { client, connectionHandler } = makeClient();
      vi.spyOn(client["adapter"], "getBlocklist").mockResolvedValue([]);
      const card = makeCard();
      await client.connectAndApprove(card, "test");
      expect(client["approvedServers"].has(card.id)).toBe(true);
      await client.revoke(card.id);
      expect(client["approvedServers"].has(card.id)).toBe(false);
      expect(connectionHandler.disconnectCalls).toHaveLength(1);
    });
  });

  describe("checkScope", () => {
    it("passes when scope approved", async () => {
      const { client } = makeClient();
      vi.spyOn(client["adapter"], "getBlocklist").mockResolvedValue([]);
      const card = makeCard();
      await client.connectAndApprove(card, "test", { requestedScopes: ["search"] });
      await expect(client.checkScope(card.id, "search")).resolves.toBeUndefined();
    });

    it("throws ScopeNotApproved for unapproved scope", async () => {
      const { client } = makeClient();
      vi.spyOn(client["adapter"], "getBlocklist").mockResolvedValue([]);
      const card = makeCard();
      await client.connectAndApprove(card, "test", { requestedScopes: ["search"] });
      await expect(client.checkScope(card.id, "admin")).rejects.toThrow(ScopeNotApproved);
    });

    it("throws ScopeNotApproved when no token", async () => {
      const { client } = makeClient();
      await expect(client.checkScope("urn:pharos:unknown", "search")).rejects.toThrow(ScopeNotApproved);
    });
  });

  describe("close", () => {
    it("clears all state", async () => {
      const { client } = makeClient();
      vi.spyOn(client["adapter"], "getBlocklist").mockResolvedValue([]);
      const card = makeCard();
      await client.connectAndApprove(card, "test");
      await client.close();
      expect(client["connections"].size).toBe(0);
      expect(client["approvedServers"].size).toBe(0);
    });
  });
});
