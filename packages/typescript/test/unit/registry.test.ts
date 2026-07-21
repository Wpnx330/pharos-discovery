import { describe, it, expect, vi, beforeEach } from "vitest";
import { PharosRegistryAdapter } from "../../src/adapters/registry.js";
import { RegistryUnavailable, NoServersFound } from "../../src/errors.js";
import type { ServerCard } from "../../src/models/index.js";

const mockCard = {
  id: "urn:pharos:server-001",
  display_name: "Test Server",
  description: "A test MCP server",
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
};

function mockFetch(data: any, status = 200, headers: Record<string, string> = {}) {
  global.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: new Map(Object.entries(headers)) as any,
  }) as any;
}

describe("PharosRegistryAdapter", () => {
  it("strips trailing slashes from base URL", () => {
    const adapter = new PharosRegistryAdapter("https://reg.example.com/");
    expect(adapter.url).toBe("https://reg.example.com");
  });

  describe("search", () => {
    it("returns results on success", async () => {
      mockFetch({ results: [mockCard] });
      const adapter = new PharosRegistryAdapter("https://reg.example.com");
      const results = await adapter.search("test", undefined, 5);
      expect(results).toHaveLength(1);
      expect(results[0].card.id).toBe("urn:pharos:server-001");
    });

    it("throws NoServersFound when empty", async () => {
      mockFetch({ results: [] });
      const adapter = new PharosRegistryAdapter("https://reg.example.com");
      await expect(adapter.search("nonexistent")).rejects.toThrow(NoServersFound);
    });

    it("throws RegistryUnavailable on HTTP error", async () => {
      mockFetch({ error: "unavailable" }, 503);
      const adapter = new PharosRegistryAdapter("https://reg.example.com");
      await expect(adapter.search("test")).rejects.toThrow(RegistryUnavailable);
    });

    it("throws RegistryUnavailable on network error", async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error("connection refused"));
      const adapter = new PharosRegistryAdapter("https://reg.example.com");
      await expect(adapter.search("test")).rejects.toThrow(RegistryUnavailable);
    });

    it("passes filters as query params", async () => {
      const fetchSpy = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ results: [mockCard] }),
        text: async () => "",
        headers: new Map(),
      });
      global.fetch = fetchSpy as any;

      const adapter = new PharosRegistryAdapter("https://reg.example.com");
      await adapter.search("flights", { transport: ["http+sse"], publisher_verified: true }, 10);

      const calledUrl = fetchSpy.mock.calls[0][0];
      expect(calledUrl).toContain("text=flights");
      expect(calledUrl).toContain("limit=10");
      expect(calledUrl).toContain("transport=http%2Bsse");
    });
  });

  describe("getServerCard", () => {
    it("returns card with etag", async () => {
      mockFetch(mockCard, 200, { ETag: "abc123" });
      const adapter = new PharosRegistryAdapter("https://reg.example.com");
      const { card, etag } = await adapter.getServerCard("urn:pharos:server-001");
      expect(card).not.toBeNull();
      expect(card!.id).toBe("urn:pharos:server-001");
      expect(etag).toBe("abc123");
    });

    it("returns null card on 304 Not Modified", async () => {
      mockFetch({}, 304);
      const adapter = new PharosRegistryAdapter("https://reg.example.com");
      const { card, etag } = await adapter.getServerCard("urn:pharos:server-001", "abc123");
      expect(card).toBeNull();
      expect(etag).toBe("abc123");
    });

    it("throws RegistryUnavailable on 404", async () => {
      mockFetch({ error: "not found" }, 404);
      const adapter = new PharosRegistryAdapter("https://reg.example.com");
      await expect(adapter.getServerCard("urn:pharos:nonexistent")).rejects.toThrow(RegistryUnavailable);
    });
  });

  describe("getBlocklist", () => {
    it("returns blocked IDs", async () => {
      mockFetch({ blocked: ["urn:pharos:bad-1", "urn:pharos:bad-2"] });
      const adapter = new PharosRegistryAdapter("https://reg.example.com");
      const blocked = await adapter.getBlocklist();
      expect(blocked).toHaveLength(2);
      expect(blocked).toContain("urn:pharos:bad-1");
    });
  });

  describe("auth", () => {
    it("sends Authorization header with API key", async () => {
      const fetchSpy = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ results: [mockCard] }),
        text: async () => "",
        headers: new Map(),
      });
      global.fetch = fetchSpy as any;

      const adapter = new PharosRegistryAdapter("https://reg.example.com", { apiKey: "secret-key" });
      await adapter.search("test");

      const callOpts = fetchSpy.mock.calls[0][1];
      expect(callOpts.headers["Authorization"]).toBe("Bearer secret-key");
    });
  });
});
