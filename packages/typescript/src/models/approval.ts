import { z } from "zod";
import { serverCardSchema } from "./serverCard.js";

export const approvalRequestSchema = z.object({
  server: serverCardSchema,
  purpose: z.string(),
  requested_scopes: z.array(z.string()),
  requested_capabilities: z.array(z.string()),
  duration: z.enum(["once", "session", "persistent", "trust_on_use"]),
  render_id: z.string(),
  selection_rationale: z.string(),
});

export const approvalResponseSchema = z.object({
  approved: z.boolean(),
  approved_scopes: z.array(z.string()),
  duration: z.string(),
  user_note: z.string().nullable().optional(),
  deny_reason: z.enum(["untrusted_publisher", "excessive_scopes", "wrong_server", "cost", "other"]).nullable().optional(),
});

export const approvalTokenSchema = z.object({
  token_id: z.string(),
  server_id: z.string(),
  approved_scopes: z.array(z.string()),
  approved_capabilities: z.array(z.string()),
  approved_oauth_scopes: z.array(z.string()),
  duration: z.string(),
  approved_at: z.string(),
  expires_at: z.string(),
  signature: z.string(),
});

export const planApprovalRequestSchema = z.object({
  plan_summary: z.string(),
  steps: z.array(approvalRequestSchema),
  render_id: z.string(),
});

export const planApprovalResponseSchema = z.object({
  approved: z.boolean(),
  per_step: z.array(approvalResponseSchema),
  deny_reason: z.string().nullable().optional(),
});

export type ApprovalRequest = z.infer<typeof approvalRequestSchema>;
export type ApprovalResponse = z.infer<typeof approvalResponseSchema>;
export type ApprovalToken = z.infer<typeof approvalTokenSchema>;
export type PlanApprovalRequest = z.infer<typeof planApprovalRequestSchema>;
export type PlanApprovalResponse = z.infer<typeof planApprovalResponseSchema>;
