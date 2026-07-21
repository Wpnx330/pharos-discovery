import { z } from "zod";

export const oauthResultSchema = z.object({
  authorized: z.boolean(),
  access_token: z.string().nullable().optional(),
  token_type: z.string().nullable().optional(),
  expires_in: z.number().nullable().optional(),
  refresh_token: z.string().nullable().optional(),
  scope: z.array(z.string()),
  acquired_via: z.string(),
  auth_held_by: z.string(),
  confirmed_at: z.string(),
  confirmation_jwt: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
  cancel_reason: z.string().nullable().optional(),
});

export const oauthStatusSchema = z.object({
  valid: z.boolean(),
  expires_at: z.string().nullable().optional(),
  scopes: z.array(z.string()).default([]),
});

export const revocationResultSchema = z.object({
  revoked: z.boolean(),
  revocation_confirmed: z.boolean(),
  revocation_proof: z.string().nullable().optional(),
  fallback_revoke_url: z.string().nullable().optional(),
});

export type OAuthResult = z.infer<typeof oauthResultSchema>;
export type OAuthStatus = z.infer<typeof oauthStatusSchema>;
export type RevocationResult = z.infer<typeof revocationResultSchema>;
