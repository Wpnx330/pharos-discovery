import { describe, it, expect, beforeEach } from "vitest";
import { ApprovalEngine } from "../../src/approval/engine.js";
import type { ApprovalRequest, ApprovalResponse, ApprovalToken, ServerCard } from "../../src/models/index.js";

function makeCard(): ServerCard {
  return {
    id: "urn:pharos:server-001",
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

function makeRequest(): ApprovalRequest {
  return {
    server: makeCard(),
    purpose: "Test purpose",
    requested_scopes: ["search"],
    requested_capabilities: ["search"],
    duration: "session",
    render_id: "render-001",
    selection_rationale: "testing",
  };
}

function makeResponse(): ApprovalResponse {
  return {
    approved: true,
    approved_scopes: ["search"],
    duration: "session",
  };
}

describe("ApprovalEngine", () => {
  let engine: ApprovalEngine;

  beforeEach(() => {
    engine = new ApprovalEngine("test-secret-key");
  });

  describe("createToken", () => {
    it("creates a valid token", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse());
      expect(token.token_id.startsWith("tok_")).toBe(true);
      expect(token.server_id).toBe("urn:pharos:server-001");
      expect(token.approved_scopes).toEqual(["search"]);
      expect(token.signature).toBeTruthy();
      expect(token.signature).not.toBe("unsigned");
    });

    it("sets expiry based on TTL", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse(), 1800);
      const approvedAt = Math.floor(new Date(token.approved_at).getTime() / 1000);
      const expiresAt = parseInt(token.expires_at, 10);
      expect(expiresAt - approvedAt).toBe(1800);
    });

    it("generates unique token IDs", async () => {
      const t1 = await engine.createToken(makeRequest(), makeResponse());
      const t2 = await engine.createToken(makeRequest(), makeResponse());
      expect(t1.token_id).not.toBe(t2.token_id);
    });
  });

  describe("verifyToken", () => {
    it("verifies a valid token", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse());
      expect(await engine.verifyToken(token)).toBe(true);
    });

    it("rejects tampered scope", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse());
      token.approved_scopes = ["admin"];
      expect(await engine.verifyToken(token)).toBe(false);
    });

    it("rejects tampered expiry", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse());
      token.expires_at = String(parseInt(token.expires_at, 10) + 999999);
      expect(await engine.verifyToken(token)).toBe(false);
    });

    it("rejects tampered server_id", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse());
      token.server_id = "urn:pharos:evil";
      expect(await engine.verifyToken(token)).toBe(false);
    });

    it("rejects tampered capabilities", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse());
      token.approved_capabilities = ["admin"];
      expect(await engine.verifyToken(token)).toBe(false);
    });

    it("rejects tampered duration", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse());
      token.duration = "persistent";
      expect(await engine.verifyToken(token)).toBe(false);
    });

    it("rejects with different secret", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse());
      const other = new ApprovalEngine("different-secret");
      expect(await other.verifyToken(token)).toBe(false);
    });

    it("same secret cross-engine verifies", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse());
      const twin = new ApprovalEngine("test-secret-key");
      expect(await twin.verifyToken(token)).toBe(true);
    });
  });

  describe("isExpired", () => {
    it("not expired with future expiry", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse(), 3600);
      expect(engine.isExpired(token)).toBe(false);
    });

    it("expired with past expiry", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse(), -1);
      expect(engine.isExpired(token)).toBe(true);
    });

    it("expired with zero TTL", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse(), 0);
      expect(engine.isExpired(token)).toBe(true);
    });

    it("expired with invalid expiry", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse());
      token.expires_at = "invalid";
      expect(engine.isExpired(token)).toBe(true);
    });
  });

  describe("isValid", () => {
    it("fresh valid token", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse(), 3600);
      expect(await engine.isValid(token)).toBe(true);
    });

    it("expired token not valid", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse(), -1);
      expect(await engine.isValid(token)).toBe(false);
    });

    it("tampered token not valid", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse());
      token.approved_scopes = ["admin"];
      expect(await engine.isValid(token)).toBe(false);
    });
  });

  describe("EmptySecret", () => {
    it("empty secret rejected", () => {
      expect(() => new ApprovalEngine("")).toThrow();
    });

    it("null/undefined secret rejected", () => {
      expect(() => new ApprovalEngine(null as any)).toThrow();
      expect(() => new ApprovalEngine(undefined as any)).toThrow();
    });
  });

  describe("SignatureFormat", () => {
    it("signature is hex", async () => {
      const token = await engine.createToken(makeRequest(), makeResponse());
      expect(token.signature.length).toBe(64);
      expect(/^[0-9a-f]+$/.test(token.signature)).toBe(true);
    });

    it("signature is deterministic", async () => {
      const t1 = await engine.createToken(makeRequest(), makeResponse());
      // Manually sign the same fields to verify determinism
      const payload = JSON.stringify({
        token_id: t1.token_id,
        server_id: t1.server_id,
        approved_scopes: t1.approved_scopes,
        approved_capabilities: t1.approved_capabilities,
        approved_oauth_scopes: t1.approved_oauth_scopes,
        duration: t1.duration,
        approved_at: t1.approved_at,
        expires_at: t1.expires_at,
      });
      // Verify via verifyToken which recomputes the signature
      expect(await engine.verifyToken(t1)).toBe(true);
    });
  });
});
