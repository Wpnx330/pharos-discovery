/**
 * Plan review + approval — two-phase install approval (T20).
 */

import type { ServerCard } from "../models/serverCard.js";
import type {
  InstallPlan,
  PlanReview,
  RiskLevel,
  ServerInstallEntry,
} from "../models/plan.js";

const RISK_ORDER: Record<RiskLevel, number> = {
  low: 0,
  medium: 1,
  high: 2,
  critical: 3,
};

const CAPABILITY_RISK: Record<string, RiskLevel> = {
  filesystem: "high",
  file_write: "high",
  file_read: "medium",
  network: "medium",
  http: "medium",
  secrets: "critical",
  secret_access: "critical",
  credentials: "critical",
  shell: "critical",
  exec: "critical",
  process: "high",
  env: "high",
  environment: "high",
  database: "high",
  db: "high",
  search: "low",
  read: "low",
  tools: "low",
  prompts: "low",
  resources: "low",
};

function randomId(): string {
  const bytes = new Uint8Array(6);
  globalThis.crypto.getRandomValues(bytes);
  let hex = "";
  for (const b of bytes) hex += b.toString(16).padStart(2, "0");
  return "plan_" + hex;
}

export interface CreatePlanInput {
  server: ServerCard;
  scopes?: string[];
  capabilities?: string[];
}

export class PlanReviewer {
  assessRisk(serverCard: ServerCard): RiskLevel {
    const caps = serverCard.capabilities.map((c) => c.toLowerCase());
    let highest: RiskLevel = "low";
    for (const cap of caps) {
      const level = CAPABILITY_RISK[cap] ?? "low";
      if (RISK_ORDER[level] > RISK_ORDER[highest]) {
        highest = level;
      }
    }
    return highest;
  }

  createPlan(servers: CreatePlanInput[]): InstallPlan {
    const entries: ServerInstallEntry[] = servers.map((s) => ({
      server: s.server,
      scopes: s.scopes ?? [],
      capabilities: s.capabilities ?? [],
      risk: this.assessRisk(s.server),
    }));

    return {
      id: randomId(),
      entries,
      created_at: Date.now() / 1000,
      approved_server_ids: [],
      rejected: false,
      reject_reason: undefined,
    };
  }

  reviewPlan(plan: InstallPlan): PlanReview {
    const summary: Record<string, number> = {
      low: 0,
      medium: 0,
      high: 0,
      critical: 0,
    };
    let highest: RiskLevel = "low";
    const recs: string[] = [];

    for (const entry of plan.entries) {
      summary[entry.risk]++;
      if (RISK_ORDER[entry.risk] > RISK_ORDER[highest]) {
        highest = entry.risk;
      }

      if (entry.risk === "critical") {
        recs.push(
          `Server ${entry.server.id} has critical risk — manual review strongly recommended before approval.`,
        );
      } else if (entry.risk === "high") {
        recs.push(
          `Server ${entry.server.id} has high risk — verify scopes are minimal.`,
        );
      }

      const capsLower = entry.capabilities.map((c) => c.toLowerCase());
      if (capsLower.includes("secrets") || capsLower.includes("credentials")) {
        recs.push(
          `Server ${entry.server.id} requests secret access — ensure credentials are stored securely.`,
        );
      }
    }

    if ((highest === "critical" || highest === "high") && recs.length === 0) {
      recs.push("High-risk plan — review carefully before approving.");
    }

    return {
      plan_id: plan.id,
      risk_summary: summary as Record<RiskLevel, number>,
      highest_risk: highest,
      recommendations: recs,
      overall_risk: highest,
    };
  }
}

export class PlanApproval {
  approve(plan: InstallPlan, serverIds?: Set<string> | string[]): InstallPlan {
    plan.rejected = false;
    plan.reject_reason = undefined;

    if (serverIds === undefined) {
      plan.approved_server_ids = plan.entries.map((e) => e.server.id);
    } else {
      const idSet = Array.isArray(serverIds) ? new Set(serverIds) : serverIds;
      const validIds = new Set(plan.entries.map((e) => e.server.id));
      const unknown: string[] = [];
      for (const id of idSet) {
        if (!validIds.has(id)) unknown.push(id);
      }
      if (unknown.length > 0) {
        throw new Error(`Unknown server IDs in approval: ${unknown.join(", ")}`);
      }
      plan.approved_server_ids = [...idSet];
    }

    return plan;
  }

  reject(plan: InstallPlan, reason: string): InstallPlan {
    plan.rejected = true;
    plan.reject_reason = reason;
    plan.approved_server_ids = [];
    return plan;
  }

  isApproved(plan: InstallPlan, serverId: string): boolean {
    if (plan.rejected) return false;
    return plan.approved_server_ids.includes(serverId);
  }
}
