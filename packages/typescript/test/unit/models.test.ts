import { describe, it, expect } from "vitest";
import {
  serverCardSchema,
  approvalRequestSchema,
  approvalResponseSchema,
  approvalTokenSchema,
  oauthResultSchema,
  planApprovalRequestSchema,
} from "../../src/models/index.js";
import type { ServerCard, ApprovalRequest } from "../../src/models/index.js";

const fullServerCard = {
  id: "srv_weather",
  display_name: "Weather MCP",
  description: "Provides current weather and forecasts.",
  publisher: {
    id: "pub_acme",
    name: "Acme Corp",
    verified: true,
    verification_method: "domain_control",
    contact: "support@acme.example",
  },
  version: "1.2.0",
  transport: ["http+sse", "streamable-http"],
  endpoint: "https://mcp.acme.example/sse",
  stdio_command: null,
  capabilities: ["tools", "resources"],
  tools_count: 12,
  tools_count_verified: true,
  auth: {
    type: "oauth",
    secret_handling: "server_side",
    app_registration: {
      client_id: "abc123",
      consent_defaults: ["read:weather"],
      token_storage: "server_side",
    },
    ui: { type: "redirect", url: "https://acme.example/auth" },
    scopes: ["read:weather", "read:forecast"],
    auth_url: "https://acme.example/oauth/authorize",
    dcr_support: true,
    cimd_support: false,
  },
  availability: "native",
  pricing: { model: "freemium", price_usd: 0.0, unit: "request", free_tier_limit: "1000/day" },
  pricing_verified: true,
  rating: { score: 4.6, count: 128, distribution: { "5": 80, "4": 30, "3": 10, "2": 5, "1": 3 } },
  trust: { attestations: ["sigstore"], certifications: ["SOC2"] },
  representative_queries: ["weather in Paris", "forecast for tomorrow"],
  pharos_score: 0.82,
  source_registry: "pharos",
  source_score: 0.9,
  source_urn: "urn:pharos:srv_weather",
  documentation_url: "https://docs.acme.example/mcp",
  tags: ["weather", "forecast"],
  published_at: "2025-01-15T00:00:00Z",
  updated_at: "2025-07-01T00:00:00Z",
  status: "active",
  successor_id: null,
  privacy_policy_url: "https://acme.example/privacy",
  terms_url: "https://acme.example/terms",
  data_residency: ["US", "EU"],
  rate_limits: { per_minute: 60, per_day: 10000 },
  health_endpoint: "https://mcp.acme.example/health",
  protocol_versions: ["2025-06-18"],
};

describe("ServerCard", () => {
  it("valid full object passes validation", () => {
    const parsed = serverCardSchema.parse(fullServerCard);
    expect(parsed.id).toBe("srv_weather");
    expect(parsed.tools_count_verified).toBe(true);
    expect(parsed.auth.type).toBe("oauth");
    expect(parsed.transport).toEqual(["http+sse", "streamable-http"]);
  });

  it("missing required field fails", () => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { id, ...missingId } = fullServerCard;
    expect(() => serverCardSchema.parse(missingId)).toThrow();
  });

  it("minimal fields pass with defaults applied", () => {
    const minimal = {
      id: "srv_min",
      display_name: "Minimal",
      description: "Bare minimum server.",
      publisher: { id: "pub_min", name: "Min Pub" },
      version: "0.1.0",
      transport: ["stdio"],
      capabilities: [],
      tools_count: 0,
      auth: { type: "none" },
      availability: "referenced",
      source_registry: "pharos",
      published_at: "2025-01-01T00:00:00Z",
      updated_at: "2025-01-01T00:00:00Z",
      status: "active",
    };
    const parsed = serverCardSchema.parse(minimal);
    expect(parsed.tools_count_verified).toBe(false);
    expect(parsed.pricing_verified).toBe(false);
    expect(parsed.tags).toEqual([]);
    expect(parsed.representative_queries).toEqual([]);
    expect(parsed.data_residency).toEqual([]);
    expect(parsed.protocol_versions).toEqual([]);
    expect(parsed.trust).toBeUndefined();
  });
});

describe("ApprovalRequest", () => {
  it("selection_rationale is required", () => {
    const base = {
      server: fullServerCard,
      purpose: "fetch weather",
      requested_scopes: ["read:weather"],
      requested_capabilities: ["tools"],
      duration: "session",
      render_id: "rnd_001",
    } as const;
    // Without selection_rationale
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { ...noRationale } = base;
    expect(() => approvalRequestSchema.parse(noRationale)).toThrow();

    const valid: ApprovalRequest = approvalRequestSchema.parse({
      ...base,
      selection_rationale: "User asked for current weather; server provides it natively.",
    });
    expect(valid.selection_rationale).toContain("weather");
  });
});

describe("ApprovalResponse", () => {
  it("deny_reason enum validation", () => {
    const approved = approvalResponseSchema.parse({
      approved: true,
      approved_scopes: ["read:weather"],
      duration: "session",
    });
    expect(approved.approved).toBe(true);
    expect(approved.deny_reason).toBeUndefined();

    const denied = approvalResponseSchema.parse({
      approved: false,
      approved_scopes: [],
      duration: "once",
      deny_reason: "excessive_scopes",
    });
    expect(denied.deny_reason).toBe("excessive_scopes");

    // Invalid enum value must fail
    expect(() =>
      approvalResponseSchema.parse({
        approved: false,
        approved_scopes: [],
        duration: "once",
        deny_reason: "not_a_real_reason",
      }),
    ).toThrow();
  });
});

describe("ApprovalToken", () => {
  it("all fields are required", () => {
    const valid = {
      token_id: "tok_001",
      server_id: "srv_weather",
      approved_scopes: ["read:weather"],
      approved_capabilities: ["tools"],
      approved_oauth_scopes: ["read:weather"],
      duration: "session",
      approved_at: "2025-07-21T10:00:00Z",
      expires_at: "2025-07-21T12:00:00Z",
      signature: "base64sig==",
    };
    const parsed = approvalTokenSchema.parse(valid);
    expect(parsed.token_id).toBe("tok_001");

    // Remove each required field and expect failure
    for (const key of Object.keys(valid) as (keyof typeof valid)[]) {
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { [key]: _omitted, ...rest } = valid;
      expect(() => approvalTokenSchema.parse(rest)).toThrow();
    }
  });
});

describe("OAuthResult", () => {
  it("server_side — access_token may be undefined", () => {
    const serverSide = oauthResultSchema.parse({
      authorized: true,
      access_token: undefined,
      token_type: undefined,
      expires_in: undefined,
      refresh_token: undefined,
      scope: ["read:weather"],
      acquired_via: "dcr",
      auth_held_by: "server_side",
      confirmed_at: "2025-07-21T10:00:00Z",
    });
    expect(serverSide.authorized).toBe(true);
    expect(serverSide.access_token).toBeUndefined();
    expect(serverSide.auth_held_by).toBe("server_side");
  });
});

describe("PlanApprovalRequest", () => {
  it("supports multiple steps", () => {
    const step1 = {
      server: fullServerCard,
      purpose: "fetch weather",
      requested_scopes: ["read:weather"],
      requested_capabilities: ["tools"],
      duration: "session",
      render_id: "rnd_a",
      selection_rationale: "Primary weather lookup.",
    };
    const step2 = {
      server: { ...fullServerCard, id: "srv_geo" },
      purpose: "geocode city name",
      requested_scopes: ["read:geo"],
      requested_capabilities: ["tools"],
      duration: "once",
      render_id: "rnd_b",
      selection_rationale: "Resolve city to coordinates.",
    };
    const plan = planApprovalRequestSchema.parse({
      plan_summary: "Get weather for a city.",
      steps: [step1, step2],
      render_id: "plan_001",
    });
    expect(plan.steps).toHaveLength(2);
    expect(plan.steps[1].server.id).toBe("srv_geo");
    expect(plan.steps[0].selection_rationale).toContain("Primary");
  });
});
