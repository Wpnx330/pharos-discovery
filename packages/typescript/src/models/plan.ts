/**
 * Plan approval models — two-phase install plan + risk assessment (T20).
 */

import { z } from "zod";
import { serverCardSchema } from "./serverCard.js";

export const riskLevelSchema = z.enum(["low", "medium", "high", "critical"]);
export type RiskLevel = z.infer<typeof riskLevelSchema>;

export const serverInstallEntrySchema = z.object({
  server: serverCardSchema,
  scopes: z.array(z.string()).default([]),
  capabilities: z.array(z.string()).default([]),
  risk: riskLevelSchema.default("low"),
});
export type ServerInstallEntry = z.infer<typeof serverInstallEntrySchema>;

export const installPlanSchema = z.object({
  id: z.string(),
  entries: z.array(serverInstallEntrySchema).default([]),
  created_at: z.number(),
  approved_server_ids: z.array(z.string()).default([]),
  rejected: z.boolean().default(false),
  reject_reason: z.string().nullable().optional(),
});
export type InstallPlan = z.infer<typeof installPlanSchema>;

export const planReviewSchema = z.object({
  plan_id: z.string(),
  risk_summary: z
    .record(z.enum(["low", "medium", "high", "critical"]), z.number())
    .default({ low: 0, medium: 0, high: 0, critical: 0 }),
  highest_risk: riskLevelSchema.default("low"),
  recommendations: z.array(z.string()).default([]),
  overall_risk: riskLevelSchema.default("low"),
});
export type PlanReview = z.infer<typeof planReviewSchema>;
