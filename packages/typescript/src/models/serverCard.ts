import { z } from "zod";

export const appRegistrationSchema = z.object({
  client_id: z.string().nullable().optional(),
  consent_defaults: z.array(z.string()).default([]),
  token_storage: z.enum(["server_side", "agent_side"]).default("server_side"),
});

export const oauthUISchema = z.object({
  type: z.enum(["inline", "redirect", "embedded"]).default("inline"),
  url: z.string().nullable().optional(),
});

export const authSpecSchema = z.object({
  type: z.enum(["none", "api_key", "oauth", "mtls"]),
  secret_handling: z.enum(["server_side", "agent_side"]).nullable().optional(),
  app_registration: appRegistrationSchema.nullable().optional(),
  ui: oauthUISchema.nullable().optional(),
  scopes: z.array(z.string()).nullable().optional(),
  auth_url: z.string().nullable().optional(),
  dcr_support: z.boolean().nullable().optional(),
  cimd_support: z.boolean().nullable().optional(),
});

export const publisherSchema = z.object({
  id: z.string(),
  name: z.string(),
  verified: z.boolean().nullable().optional(),
  verification_method: z.enum(["domain_control", "identity"]).nullable().optional(),
  contact: z.string().nullable().optional(),
});

export const pricingSpecSchema = z.object({
  model: z.enum(["free", "freemium", "paid", "usage"]),
  price_usd: z.number().nullable().optional(),
  unit: z.string().nullable().optional(),
  free_tier_limit: z.string().nullable().optional(),
});

export const ratingSpecSchema = z.object({
  score: z.number(),
  count: z.number(),
  distribution: z.record(z.string(), z.number()).nullable().optional(),
});

export const trustSpecSchema = z.object({
  attestations: z.array(z.string()).default([]),
  certifications: z.array(z.string()).default([]),
});

export const serverCardSchema = z.object({
  id: z.string(),
  display_name: z.string(),
  description: z.string(),
  publisher: publisherSchema,
  version: z.string(),
  transport: z.array(z.enum(["stdio", "http+sse", "streamable-http"])),
  endpoint: z.string().nullable().optional(),
  stdio_command: z.string().nullable().optional(),
  capabilities: z.array(z.string()),
  tools_count: z.number(),
  tools_count_verified: z.boolean().default(false),
  auth: authSpecSchema,
  availability: z.enum(["mirrored", "referenced", "native"]),
  pricing: pricingSpecSchema.nullable().optional(),
  pricing_verified: z.boolean().default(false),
  rating: ratingSpecSchema.nullable().optional(),
  trust: trustSpecSchema.nullable().optional(),
  representative_queries: z.array(z.string()).default([]),
  pharos_score: z.number().min(0).max(1).nullable().optional(),
  source_registry: z.string(),
  source_score: z.number().nullable().optional(),
  source_urn: z.string().nullable().optional(),
  documentation_url: z.string().nullable().optional(),
  tags: z.array(z.string()).default([]),
  published_at: z.string(),
  updated_at: z.string(),
  status: z.enum(["active", "deprecated", "deleted"]),
  successor_id: z.string().nullable().optional(),
  privacy_policy_url: z.string().nullable().optional(),
  terms_url: z.string().nullable().optional(),
  data_residency: z.array(z.string()).default([]),
  rate_limits: z.record(z.string(), z.unknown()).nullable().optional(),
  health_endpoint: z.string().nullable().optional(),
  protocol_versions: z.array(z.string()).default([]),
});

// Type exports
export type AppRegistration = z.infer<typeof appRegistrationSchema>;
export type OAuthUI = z.infer<typeof oauthUISchema>;
export type AuthSpec = z.infer<typeof authSpecSchema>;
export type Publisher = z.infer<typeof publisherSchema>;
export type PricingSpec = z.infer<typeof pricingSpecSchema>;
export type RatingSpec = z.infer<typeof ratingSpecSchema>;
export type TrustSpec = z.infer<typeof trustSpecSchema>;
export type ServerCard = z.infer<typeof serverCardSchema>;
