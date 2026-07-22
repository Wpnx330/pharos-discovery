import { describe, it, expect, beforeEach } from "vitest";
import { HeadlessApprovalHandler, HeadlessPolicy } from "../../src/approval/headless.js";
import { ConsentStore } from "../../src/consent/store.js";
import type { ApprovalRequest, ServerCard } from "../../src/models/index.js";

function makeCard(serverId: string = "urn:pharos:server-001"): ServerCard {
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

function makeRequest(serverId: string = "urn:pharos:server-001"): ApprovalRequest {
  return {
    server: makeCard(serverId),
    purpose: "test",
    requested_scopes: ["search"],
    requested_capabilities: ["search"],
    duration: "session",
    render_id: "render-001",
    selection_rationale: "testing",
  };
}

describe("HeadlessPolicy", () => {
  it("has all four values", () => {
    expect(HeadlessPolicy.ALLOW_ALL).toBe("allow_all");
    expect(HeadlessPolicy.DENY_ALL).toBe("deny_all");
    expect(HeadlessPolicy.ALLOW_TRUSTED_ONLY).toBe("allow_trusted_only");
    expect(HeadlessPolicy.ALLOW_IF_PRE_APPROVED).toBe("allow_if_pre_approved");
  });
});

describe("HeadlessApprovalHandler.canHandle", () => {
  it("returns true", () => {
    expect(new HeadlessApprovalHandler(HeadlessPolicy.DENY_ALL).canHandle()).toBe(true);
  });

  it("returns true for all policies", () => {
    for (const policy of Object.values(HeadlessPolicy)) {
      expect(new HeadlessApprovalHandler(policy).canHandle()).toBe(true);
    }
  });
});

describe("HeadlessApprovalHandler ALLOW_ALL", () => {
  it("approves requests", async () => {
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.ALLOW_ALL);
    const resp = await handler.requestApproval(makeRequest());
    expect(resp.approved).toBe(true);
    expect(resp.approved_scopes).toEqual(["search"]);
  });

  it("records decision in store", async () => {
    const store = new ConsentStore();
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.ALLOW_ALL, { consentStore: store });
    await handler.requestApproval(makeRequest());
    expect(store.listAll()).toHaveLength(1);
    expect(store.listAll()[0].decision).toBe("approved");
  });
});

describe("HeadlessApprovalHandler DENY_ALL", () => {
  it("denies requests", async () => {
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.DENY_ALL);
    const resp = await handler.requestApproval(makeRequest());
    expect(resp.approved).toBe(false);
    expect(resp.approved_scopes).toEqual([]);
    expect(resp.deny_reason).toBe("other");
  });

  it("records denial in store", async () => {
    const store = new ConsentStore();
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.DENY_ALL, { consentStore: store });
    await handler.requestApproval(makeRequest());
    expect(store.listAll()[0].decision).toBe("denied");
  });
});

describe("HeadlessApprovalHandler ALLOW_TRUSTED_ONLY", () => {
  it("approves trusted servers", async () => {
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.ALLOW_TRUSTED_ONLY, {
      trustedServerIds: new Set(["urn:pharos:server-001"]),
    });
    const resp = await handler.requestApproval(makeRequest("urn:pharos:server-001"));
    expect(resp.approved).toBe(true);
  });

  it("denies untrusted servers", async () => {
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.ALLOW_TRUSTED_ONLY, {
      trustedServerIds: new Set(["urn:pharos:other"]),
    });
    const resp = await handler.requestApproval(makeRequest("urn:pharos:server-001"));
    expect(resp.approved).toBe(false);
    expect(resp.user_note).toContain("not in trusted set");
  });

  it("accepts array of trusted IDs", async () => {
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.ALLOW_TRUSTED_ONLY, {
      trustedServerIds: ["urn:pharos:server-001"],
    });
    const resp = await handler.requestApproval(makeRequest("urn:pharos:server-001"));
    expect(resp.approved).toBe(true);
  });

  it("empty trusted set denies all", async () => {
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.ALLOW_TRUSTED_ONLY);
    const resp = await handler.requestApproval(makeRequest());
    expect(resp.approved).toBe(false);
  });
});

describe("HeadlessApprovalHandler ALLOW_IF_PRE_APPROVED", () => {
  it("approves when pre-approved", async () => {
    const store = new ConsentStore();
    store.record("urn:pharos:server-001", ["search"], "approved");
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.ALLOW_IF_PRE_APPROVED, { consentStore: store });
    const resp = await handler.requestApproval(makeRequest());
    expect(resp.approved).toBe(true);
    expect(resp.user_note).toContain("pre-approved");
  });

  it("denies when not pre-approved", async () => {
    const store = new ConsentStore();
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.ALLOW_IF_PRE_APPROVED, { consentStore: store });
    const resp = await handler.requestApproval(makeRequest());
    expect(resp.approved).toBe(false);
    expect(resp.user_note).toContain("not pre-approved");
  });

  it("denies when pre-approved but scope mismatch", async () => {
    const store = new ConsentStore();
    store.record("urn:pharos:server-001", ["read"], "approved");
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.ALLOW_IF_PRE_APPROVED, { consentStore: store });
    const resp = await handler.requestApproval(makeRequest()); // requests "search"
    expect(resp.approved).toBe(false);
  });

  it("denies when pre-approved but expired", async () => {
    const store = new ConsentStore();
    store.record("urn:pharos:server-001", ["search"], "approved", -1);
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.ALLOW_IF_PRE_APPROVED, { consentStore: store });
    const resp = await handler.requestApproval(makeRequest());
    expect(resp.approved).toBe(false);
  });
});

describe("HeadlessApprovalHandler audit log", () => {
  it("logs decisions via custom logger", async () => {
    const messages: string[] = [];
    const logger = { info: (msg: string) => messages.push(msg) };
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.ALLOW_ALL, { logger });
    await handler.requestApproval(makeRequest());
    expect(messages.length).toBe(1);
    expect(messages[0]).toContain("headless approval approved");
  });

  it("logs denials via custom logger", async () => {
    const messages: string[] = [];
    const logger = { info: (msg: string) => messages.push(msg) };
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.DENY_ALL, { logger });
    await handler.requestApproval(makeRequest());
    expect(messages[0]).toContain("headless approval denied");
  });
});

describe("HeadlessApprovalHandler defaults", () => {
  it("default policy is DENY_ALL", () => {
    const handler = new HeadlessApprovalHandler();
    expect(handler.policy).toBe(HeadlessPolicy.DENY_ALL);
  });

  it("creates own store if none given", async () => {
    const handler = new HeadlessApprovalHandler(HeadlessPolicy.ALLOW_ALL);
    await handler.requestApproval(makeRequest());
    // No crash — internal store used
  });
});
