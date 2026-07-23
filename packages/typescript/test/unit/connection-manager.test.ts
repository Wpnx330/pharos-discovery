import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  ConnectionManager,
  HttpSSETransport,
  StreamableHTTPTransport,
  StdioTransport,
} from "../../src/connection/manager.js";
import { ConnectionFailed } from "../../src/errors.js";
import type { ServerCard, ApprovalToken } from "../../src/models/index.js";

function makeCard(
  transport: "stdio" | "http+sse" | "streamable-http" = "http+sse",
  endpoint: string | undefined = "https://server.example.com/mcp",
  stdioCmd?: string,
): ServerCard {
  return {
    id: "urn:pharos:server-001",
    display_name: "Test Server",
    description: "A test server",
    publisher: { id: "did:web:example.com", name: "TestPub" },
    version: "1.0.0",
    transport: [transport],
    endpoint,
    stdio_command: stdioCmd,
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

function makeToken(): ApprovalToken {
  return {
    token_id: "tok-001",
    server_id: "urn:pharos:server-001",
    approved_scopes: ["search"],
    approved_capabilities: ["search"],
    approved_oauth_scopes: [],
    duration: "session",
    approved_at: "2026-07-01T00:00:00Z",
    expires_at: "9999999999",
    signature: "signed",
  };
}

describe("ConnectionManager", () => {
  let manager: ConnectionManager;

  beforeEach(() => {
    manager = new ConnectionManager(60000, 3);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("transport creation", () => {
    it("creates HttpSSETransport", () => {
      const card = makeCard("http+sse");
      const transport = manager["createTransport"](card, makeToken());
      expect(transport).toBeInstanceOf(HttpSSETransport);
    });

    it("creates StreamableHTTPTransport", () => {
      const card = makeCard("streamable-http");
      const transport = manager["createTransport"](card, makeToken());
      expect(transport).toBeInstanceOf(StreamableHTTPTransport);
    });

    it("creates StdioTransport", () => {
      const card = makeCard("stdio", undefined, "python -m myserver");
      const transport = manager["createTransport"](card, makeToken());
      expect(transport).toBeInstanceOf(StdioTransport);
    });

    it("prefers streamable-http over http+sse", () => {
      const card = makeCard("streamable-http");
      card.transport = ["http+sse", "streamable-http"];
      const transport = manager["createTransport"](card, makeToken());
      expect(transport).toBeInstanceOf(StreamableHTTPTransport);
    });

    it("throws when no usable transport", () => {
      const card = makeCard("http+sse");
      card.endpoint = undefined;
      expect(() => manager["createTransport"](card, makeToken())).toThrow(ConnectionFailed);
    });
  });

  describe("connect", () => {
    it("connects successfully", async () => {
      const card = makeCard("http+sse");
      const transport = await manager.connect(card, makeToken());
      expect(await transport.isAlive()).toBe(true);
      expect(manager.activeCount).toBe(1);
    });

    it("returns existing connection", async () => {
      const card = makeCard("http+sse");
      const t1 = await manager.connect(card, makeToken());
      const t2 = await manager.connect(card, makeToken());
      expect(t1).toBe(t2);
      expect(manager.activeCount).toBe(1);
    });

    it("retries on failure then succeeds", async () => {
      const card = makeCard("http+sse");
      let callCount = 0;
      vi.spyOn(HttpSSETransport.prototype, "connect").mockImplementation(
        async function (this: HttpSSETransport) {
          callCount++;
          if (callCount < 3) throw new Error("connection refused");
          (this as any).connected = true;
        },
      );

      const transport = await manager.connect(card, makeToken());
      expect(callCount).toBe(3);
      expect(await transport.isAlive()).toBe(true);
    });

    it("fails after max retries", async () => {
      const mgr = new ConnectionManager(60000, 2);
      const card = makeCard("http+sse");
      vi.spyOn(HttpSSETransport.prototype, "connect").mockRejectedValue(
        new Error("refused"),
      );
      await expect(mgr.connect(card, makeToken())).rejects.toThrow(ConnectionFailed);
    });
  });

  describe("disconnect", () => {
    it("disconnects a connection", async () => {
      const card = makeCard("http+sse");
      await manager.connect(card, makeToken());
      expect(manager.activeCount).toBe(1);
      await manager.disconnect(card.id);
      expect(manager.activeCount).toBe(0);
    });

    it("disconnectAll removes all", async () => {
      const card1 = makeCard("http+sse");
      const card2 = makeCard("streamable-http");
      card2.id = "urn:pharos:server-002";
      await manager.connect(card1, makeToken());
      await manager.connect(card2, makeToken());
      await manager.disconnectAll();
      expect(manager.activeCount).toBe(0);
    });

    it("disconnect nonexistent does not throw", async () => {
      await expect(manager.disconnect("urn:pharos:nonexistent")).resolves.toBeUndefined();
    });
  });

  describe("send", () => {
    it("sends a message", async () => {
      const card = makeCard("http+sse");
      await manager.connect(card, makeToken());
      // Mock the HTTP POST that the real transport now performs.
      const mockFetch = vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ jsonrpc: "2.0", id: 1, result: { ok: true } }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
      const original = globalThis.fetch;
      globalThis.fetch = mockFetch as any;
      try {
        const response = await manager.send(card.id, { id: "req-1", method: "search" });
        expect((response as any).result.ok).toBe(true);
      } finally {
        globalThis.fetch = original;
      }
    });

    it("throws when not connected", async () => {
      await expect(manager.send("urn:pharos:unknown", {})).rejects.toThrow(ConnectionFailed);
    });
  });

  describe("healthCheck", () => {
    it("returns true for alive connection", async () => {
      const card = makeCard("http+sse");
      await manager.connect(card, makeToken());
      expect(await manager.healthCheck(card.id)).toBe(true);
    });

    it("returns false for nonexistent", async () => {
      expect(await manager.healthCheck("urn:pharos:unknown")).toBe(false);
    });

    it("healthCheckAll returns all", async () => {
      const card1 = makeCard("http+sse");
      const card2 = makeCard("streamable-http");
      card2.id = "urn:pharos:server-002";
      await manager.connect(card1, makeToken());
      await manager.connect(card2, makeToken());
      const results = await manager.healthCheckAll();
      expect(Object.keys(results)).toHaveLength(2);
      expect(Object.values(results).every(Boolean)).toBe(true);
    });
  });

  describe("getTransport", () => {
    it("returns transport", async () => {
      const card = makeCard("http+sse");
      await manager.connect(card, makeToken());
      const transport = manager.getTransport(card.id);
      expect(transport).not.toBeNull();
      expect(transport).toBeInstanceOf(HttpSSETransport);
    });

    it("returns null for nonexistent", () => {
      expect(manager.getTransport("urn:pharos:unknown")).toBeNull();
    });
  });
});
