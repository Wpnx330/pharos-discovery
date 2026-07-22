import { describe, it, expect } from "vitest";
import { PlanReviewer, PlanApproval } from "../../src/plan/reviewer.js";
import type { ServerCard } from "../../src/models/serverCard.js";
import type { InstallPlan } from "../../src/models/plan.js";

function makeCard(
  serverId: string = "urn:pharos:s1",
  capabilities: string[] = ["search"],
): ServerCard {
  return {
    id: serverId,
    display_name: "Test Server",
    description: "A test server",
    publisher: { id: "did:web:example.com", name: "TestPub" },
    version: "1.0.0",
    transport: ["http+sse"],
    capabilities,
    tools_count: 3,
    auth: { type: "none" },
    availability: "native",
    source_registry: "https://registry.pharos.dev",
    published_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    status: "active",
  } as ServerCard;
}

describe("PlanReviewer.assessRisk", () => {
  const cases: [string[], string][] = [
    [["search"], "low"],
    [["read", "tools"], "low"],
    [["network"], "medium"],
    [["http"], "medium"],
    [["file_read"], "medium"],
    [["filesystem"], "high"],
    [["file_write"], "high"],
    [["process"], "high"],
    [["database"], "high"],
    [["secrets"], "critical"],
    [["credentials"], "critical"],
    [["shell"], "critical"],
    [["exec"], "critical"],
    [[], "low"],
  ];

  for (const [caps, expected] of cases) {
    it(`${caps.join(",") || "[]"} → ${expected}`, () => {
      const reviewer = new PlanReviewer();
      expect(reviewer.assessRisk(makeCard("s1", caps))).toBe(expected);
    });
  }

  it("highest risk wins", () => {
    const reviewer = new PlanReviewer();
    expect(reviewer.assessRisk(makeCard("s1", ["search", "filesystem", "secrets"]))).toBe("critical");
  });

  it("case insensitive", () => {
    const reviewer = new PlanReviewer();
    expect(reviewer.assessRisk(makeCard("s1", ["FILESYSTEM", "Search"]))).toBe("high");
  });

  it("unknown capability is low", () => {
    const reviewer = new PlanReviewer();
    expect(reviewer.assessRisk(makeCard("s1", ["custom_cap"]))).toBe("low");
  });
});

describe("PlanReviewer.createPlan", () => {
  it("returns an InstallPlan with entries", () => {
    const reviewer = new PlanReviewer();
    const plan = reviewer.createPlan([
      { server: makeCard("s1", ["search"]), scopes: ["search"], capabilities: ["search"] },
    ]);
    expect(plan.entries).toHaveLength(1);
    expect(plan.entries[0].server.id).toBe("s1");
    expect(plan.entries[0].scopes).toEqual(["search"]);
    expect(plan.entries[0].risk).toBe("low");
  });

  it("assesses risk for each server", () => {
    const reviewer = new PlanReviewer();
    const plan = reviewer.createPlan([
      { server: makeCard("s1", ["search"]) },
      { server: makeCard("s2", ["filesystem"]) },
    ]);
    expect(plan.entries[0].risk).toBe("low");
    expect(plan.entries[1].risk).toBe("high");
  });

  it("generates unique plan IDs", () => {
    const reviewer = new PlanReviewer();
    const p1 = reviewer.createPlan([{ server: makeCard() }]);
    const p2 = reviewer.createPlan([{ server: makeCard() }]);
    expect(p1.id).not.toBe(p2.id);
  });

  it("plan ID starts with plan_", () => {
    const reviewer = new PlanReviewer();
    const plan = reviewer.createPlan([]);
    expect(plan.id.startsWith("plan_")).toBe(true);
  });

  it("empty plan", () => {
    const reviewer = new PlanReviewer();
    const plan = reviewer.createPlan([]);
    expect(plan.entries).toEqual([]);
    expect(plan.approved_server_ids).toEqual([]);
    expect(plan.rejected).toBe(false);
  });

  it("defaults scopes/capabilities to empty", () => {
    const reviewer = new PlanReviewer();
    const plan = reviewer.createPlan([{ server: makeCard() }]);
    expect(plan.entries[0].scopes).toEqual([]);
    expect(plan.entries[0].capabilities).toEqual([]);
  });
});

describe("PlanReviewer.reviewPlan", () => {
  it("produces risk summary", () => {
    const reviewer = new PlanReviewer();
    const plan = reviewer.createPlan([
      { server: makeCard("s1", ["search"]) },
      { server: makeCard("s2", ["filesystem"]) },
      { server: makeCard("s3", ["secrets"]) },
    ]);
    const review = reviewer.reviewPlan(plan);
    expect(review.risk_summary.low).toBe(1);
    expect(review.risk_summary.high).toBe(1);
    expect(review.risk_summary.critical).toBe(1);
    expect(review.highest_risk).toBe("critical");
  });

  it("recommendations for critical risk", () => {
    const reviewer = new PlanReviewer();
    const plan = reviewer.createPlan([{ server: makeCard("s1", ["secrets"]) }]);
    const review = reviewer.reviewPlan(plan);
    expect(review.recommendations.some((r) => r.includes("critical risk"))).toBe(true);
  });

  it("recommendations for secret access", () => {
    const reviewer = new PlanReviewer();
    const plan = reviewer.createPlan([
      { server: makeCard("s1", ["credentials"]), capabilities: ["credentials"] },
    ]);
    const review = reviewer.reviewPlan(plan);
    expect(review.recommendations.some((r) => r.includes("secret access"))).toBe(true);
  });

  it("recommendations for high risk", () => {
    const reviewer = new PlanReviewer();
    const plan = reviewer.createPlan([{ server: makeCard("s1", ["filesystem"]) }]);
    const review = reviewer.reviewPlan(plan);
    expect(review.recommendations.some((r) => r.includes("high risk"))).toBe(true);
  });

  it("no recommendations for low risk", () => {
    const reviewer = new PlanReviewer();
    const plan = reviewer.createPlan([{ server: makeCard("s1", ["search"]) }]);
    const review = reviewer.reviewPlan(plan);
    expect(review.recommendations).toEqual([]);
    expect(review.overall_risk).toBe("low");
  });

  it("empty plan review", () => {
    const reviewer = new PlanReviewer();
    const plan = reviewer.createPlan([]);
    const review = reviewer.reviewPlan(plan);
    expect(review.risk_summary).toEqual({ low: 0, medium: 0, high: 0, critical: 0 });
    expect(review.highest_risk).toBe("low");
  });

  it("review plan_id matches", () => {
    const reviewer = new PlanReviewer();
    const plan = reviewer.createPlan([]);
    const review = reviewer.reviewPlan(plan);
    expect(review.plan_id).toBe(plan.id);
  });
});

describe("PlanApproval.approve", () => {
  it("approves all servers", () => {
    const reviewer = new PlanReviewer();
    const approval = new PlanApproval();
    const plan = reviewer.createPlan([
      { server: makeCard("s1") },
      { server: makeCard("s2") },
    ]);
    approval.approve(plan);
    expect(plan.rejected).toBe(false);
    expect(new Set(plan.approved_server_ids)).toEqual(new Set(["s1", "s2"]));
  });

  it("approves specific servers", () => {
    const reviewer = new PlanReviewer();
    const approval = new PlanApproval();
    const plan = reviewer.createPlan([
      { server: makeCard("s1") },
      { server: makeCard("s2") },
    ]);
    approval.approve(plan, new Set(["s1"]));
    expect(plan.approved_server_ids).toEqual(["s1"]);
  });

  it("approves specific servers via array", () => {
    const reviewer = new PlanReviewer();
    const approval = new PlanApproval();
    const plan = reviewer.createPlan([
      { server: makeCard("s1") },
      { server: makeCard("s2") },
    ]);
    approval.approve(plan, ["s1"]);
    expect(plan.approved_server_ids).toEqual(["s1"]);
  });

  it("throws on unknown server ID", () => {
    const reviewer = new PlanReviewer();
    const approval = new PlanApproval();
    const plan = reviewer.createPlan([{ server: makeCard("s1") }]);
    expect(() => approval.approve(plan, new Set(["unknown"]))).toThrow("Unknown server IDs");
  });

  it("clears previous rejection", () => {
    const reviewer = new PlanReviewer();
    const approval = new PlanApproval();
    const plan = reviewer.createPlan([{ server: makeCard("s1") }]);
    approval.reject(plan, "nope");
    approval.approve(plan);
    expect(plan.rejected).toBe(false);
    expect(plan.reject_reason).toBeUndefined();
  });
});

describe("PlanApproval.reject", () => {
  it("sets rejected flag and reason", () => {
    const reviewer = new PlanReviewer();
    const approval = new PlanApproval();
    const plan = reviewer.createPlan([{ server: makeCard("s1") }]);
    approval.reject(plan, "too risky");
    expect(plan.rejected).toBe(true);
    expect(plan.reject_reason).toBe("too risky");
    expect(plan.approved_server_ids).toEqual([]);
  });
});

describe("PlanApproval.isApproved", () => {
  it("returns true for approved server", () => {
    const reviewer = new PlanReviewer();
    const approval = new PlanApproval();
    const plan = reviewer.createPlan([{ server: makeCard("s1") }]);
    approval.approve(plan);
    expect(approval.isApproved(plan, "s1")).toBe(true);
  });

  it("returns false when not in approved set", () => {
    const reviewer = new PlanReviewer();
    const approval = new PlanApproval();
    const plan = reviewer.createPlan([
      { server: makeCard("s1") },
      { server: makeCard("s2") },
    ]);
    approval.approve(plan, new Set(["s1"]));
    expect(approval.isApproved(plan, "s2")).toBe(false);
  });

  it("returns false when rejected", () => {
    const reviewer = new PlanReviewer();
    const approval = new PlanApproval();
    const plan = reviewer.createPlan([{ server: makeCard("s1") }]);
    approval.approve(plan);
    approval.reject(plan, "changed mind");
    expect(approval.isApproved(plan, "s1")).toBe(false);
  });

  it("returns false for unknown server", () => {
    const reviewer = new PlanReviewer();
    const approval = new PlanApproval();
    const plan = reviewer.createPlan([{ server: makeCard("s1") }]);
    approval.approve(plan);
    expect(approval.isApproved(plan, "unknown")).toBe(false);
  });

  it("returns false when not approved yet", () => {
    const reviewer = new PlanReviewer();
    const approval = new PlanApproval();
    const plan = reviewer.createPlan([{ server: makeCard("s1") }]);
    expect(approval.isApproved(plan, "s1")).toBe(false);
  });
});
