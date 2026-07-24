import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  generatePkceVerifier,
  computePkceChallenge,
  generateStateNonce,
  OAuthServerConfig,
  OAuthFlowHandler,
  TerminalOAuthRenderer,
  BrowserOAuthRenderer,
  type RawOAuthConfig,
  type OAuthRenderer,
} from "../../src/connection/oauth/handler.js";
import { PharosClient } from "../../src/client.js";
import { OAuthError } from "../../src/errors.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRawConfig(): RawOAuthConfig {
  return {
    name: "io.salesforce/salesforce-mcp",
    version: "1.0.0",
    oauth: {
      app_registration: {
        client_id: "client-abc-123",
        auth_server_url: "https://login.salesforce.com",
        grant_type: "authorization_code",
        pkce_required: true,
        scopes: ["api", "refresh_token"],
        redirect_uri_pattern: "https://getpharos.dev/v1/oauth/callback/{state}",
        authorize_endpoint: "https://login.salesforce.com/services/oauth2/authorize",
        token_endpoint: "https://login.salesforce.com/services/oauth2/token",
      },
      ui: {
        resource_uri: "ui://oauth/login",
        csp: ["https://login.salesforce.com"],
        description: "Connect your Salesforce account",
      },
      secret_handling: "server_side",
    },
  };
}

class MockRenderer implements OAuthRenderer {
  redirectUri: string;
  calls: Array<{ url: string }> = [];
  constructor(redirectUri: string) {
    this.redirectUri = redirectUri;
  }
  async render(authorizeUrl: string): Promise<string> {
    this.calls.push({ url: authorizeUrl });
    return this.redirectUri;
  }
}

// ---------------------------------------------------------------------------
// PKCE tests
// ---------------------------------------------------------------------------

describe("PKCE", () => {
  it("generates verifier of correct length", () => {
    const v = generatePkceVerifier(64);
    expect(v).toHaveLength(64);
  });

  it("supports custom lengths within RFC 7636 range", () => {
    expect(generatePkceVerifier(43)).toHaveLength(43);
    expect(generatePkceVerifier(128)).toHaveLength(128);
  });

  it("rejects lengths outside 43-128", () => {
    expect(() => generatePkceVerifier(42)).toThrow();
    expect(() => generatePkceVerifier(129)).toThrow();
  });

  it("uses only unreserved characters", () => {
    const v = generatePkceVerifier(100);
    expect(v).toMatch(/^[A-Za-z0-9\-._~]+$/);
  });

  it("generates different values each time", () => {
    const a = generatePkceVerifier();
    const b = generatePkceVerifier();
    expect(a).not.toEqual(b);
  });

  it("computes S256 challenge correctly", async () => {
    const verifier = generatePkceVerifier();
    const challenge = await computePkceChallenge(verifier, "S256");
    // S256 = base64url(SHA256(verifier)) without padding
    expect(challenge).not.toEqual(verifier);
    expect(challenge).toMatch(/^[A-Za-z0-9\-_]+$/); // base64url, no padding
    expect(challenge).not.toContain("=");
  });

  it("returns verifier unchanged for plain method", async () => {
    const verifier = generatePkceVerifier();
    const challenge = await computePkceChallenge(verifier, "plain");
    expect(challenge).toEqual(verifier);
  });

  it("challenge is deterministic for same verifier", async () => {
    const verifier = "test-verifier-for-deterministic-output-1234567890";
    const c1 = await computePkceChallenge(verifier, "S256");
    const c2 = await computePkceChallenge(verifier, "S256");
    expect(c1).toEqual(c2);
  });
});

// ---------------------------------------------------------------------------
// State nonce tests
// ---------------------------------------------------------------------------

describe("StateNonce", () => {
  it("generates a string", () => {
    const s = generateStateNonce();
    expect(typeof s).toBe("string");
  });

  it("generates sufficient length (>= 32 chars base64url)", () => {
    const s = generateStateNonce();
    expect(s.length).toBeGreaterThanOrEqual(32);
  });

  it("generates different values each time", () => {
    const a = generateStateNonce();
    const b = generateStateNonce();
    expect(a).not.toEqual(b);
  });

  it("uses URL-safe characters only", () => {
    const s = generateStateNonce();
    expect(s).toMatch(/^[A-Za-z0-9\-_]+$/);
  });
});

// ---------------------------------------------------------------------------
// OAuthServerConfig tests
// ---------------------------------------------------------------------------

describe("OAuthServerConfig", () => {
  it("parses a full nested config", () => {
    const config = new OAuthServerConfig(makeRawConfig());
    expect(config.name).toBe("io.salesforce/salesforce-mcp");
    expect(config.client_id).toBe("client-abc-123");
    expect(config.authorize_endpoint).toBe(
      "https://login.salesforce.com/services/oauth2/authorize",
    );
    expect(config.token_endpoint).toBe(
      "https://login.salesforce.com/services/oauth2/token",
    );
    expect(config.scopes).toEqual(["api", "refresh_token"]);
    expect(config.pkce_required).toBe(true);
    expect(config.ui_csp).toEqual(["https://login.salesforce.com"]);
    expect(config.ui_description).toBe("Connect your Salesforce account");
    expect(config.secret_handling).toBe("server_side");
  });

  it("extracts idp_origin from authorize_endpoint", () => {
    const config = new OAuthServerConfig(makeRawConfig());
    expect(config.idpOrigin).toBe("https://login.salesforce.com");
  });

  it("handles flattened config (no oauth wrapper)", () => {
    const flat: RawOAuthConfig = {
      name: "flat-server",
      app_registration: {
        client_id: "flat-client",
        authorize_endpoint: "https://idp.example.com/auth",
        scopes: ["read"],
      },
    };
    const config = new OAuthServerConfig(flat);
    expect(config.client_id).toBe("flat-client");
    expect(config.authorize_endpoint).toBe("https://idp.example.com/auth");
    expect(config.scopes).toEqual(["read"]);
  });
});

// ---------------------------------------------------------------------------
// buildAuthorizeUrl tests
// ---------------------------------------------------------------------------

describe("BuildAuthorizeUrl", () => {
  it("constructs a valid authorize URL", async () => {
    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    const config = new OAuthServerConfig(makeRawConfig());
    const result = await handler.buildAuthorizeUrl(config);

    expect(result.authorizeUrl).toContain(
      "https://login.salesforce.com/services/oauth2/authorize",
    );
    expect(result.authorizeUrl).toContain("response_type=code");
    expect(result.authorizeUrl).toContain("client_id=client-abc-123");
    expect(result.authorizeUrl).toContain(`state=${result.state}`);
    expect(result.authorizeUrl).toContain("code_challenge=");
    expect(result.authorizeUrl).toContain("code_challenge_method=S256");
  });

  it("generates a state nonce if not provided", async () => {
    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    const config = new OAuthServerConfig(makeRawConfig());
    const result = await handler.buildAuthorizeUrl(config);
    expect(result.state).toBeTruthy();
    expect(result.state.length).toBeGreaterThanOrEqual(32);
  });

  it("uses provided state", async () => {
    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    const config = new OAuthServerConfig(makeRawConfig());
    const result = await handler.buildAuthorizeUrl(config, {
      state: "my-custom-state",
    });
    expect(result.state).toBe("my-custom-state");
    expect(result.authorizeUrl).toContain("state=my-custom-state");
  });

  it("includes scopes in URL", async () => {
    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    const config = new OAuthServerConfig(makeRawConfig());
    const result = await handler.buildAuthorizeUrl(config);
    expect(result.authorizeUrl).toContain("scope=api+refresh_token");
  });

  it("includes code_verifier when PKCE is required", async () => {
    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    const config = new OAuthServerConfig(makeRawConfig());
    const result = await handler.buildAuthorizeUrl(config);
    expect(result.codeVerifier).toBeTruthy();
    expect(result.codeVerifier.length).toBeGreaterThanOrEqual(43);
  });

  it("omits PKCE params when not required", async () => {
    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    const raw = makeRawConfig();
    raw.oauth!.app_registration!.pkce_required = false;
    const config = new OAuthServerConfig(raw);
    const result = await handler.buildAuthorizeUrl(config);
    expect(result.codeVerifier).toBe("");
    expect(result.authorizeUrl).not.toContain("code_challenge");
  });

  it("raises on missing authorize_endpoint", async () => {
    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    const raw = makeRawConfig();
    raw.oauth!.app_registration!.authorize_endpoint = "";
    const config = new OAuthServerConfig(raw);
    await expect(handler.buildAuthorizeUrl(config)).rejects.toThrow(OAuthError);
  });

  it("raises on missing client_id", async () => {
    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    const raw = makeRawConfig();
    raw.oauth!.app_registration!.client_id = "";
    const config = new OAuthServerConfig(raw);
    await expect(handler.buildAuthorizeUrl(config)).rejects.toThrow(OAuthError);
  });

  it("supports extra params", async () => {
    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    const config = new OAuthServerConfig(makeRawConfig());
    const result = await handler.buildAuthorizeUrl(config, {
      extraParams: { prompt: "consent", access_type: "offline" },
    });
    expect(result.authorizeUrl).toContain("prompt=consent");
    expect(result.authorizeUrl).toContain("access_type=offline");
  });
});

// ---------------------------------------------------------------------------
// exchangeCode tests
// ---------------------------------------------------------------------------

describe("ExchangeCode", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("exchanges code successfully", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          access_token: "tok-123",
          token_type: "Bearer",
          expires_in: 3600,
          scope: "api refresh_token",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    const result = await handler.exchangeCode("state123", "code456", "verifier789");

    expect(result.authorized).toBe(true);
    expect(result.access_token).toBe("tok-123");
    expect(result.token_type).toBe("Bearer");
    expect(result.expires_in).toBe(3600);
    expect(result.scope).toEqual(["api", "refresh_token"]);
    expect(result.acquired_via).toBe("oauth");

    expect(mockFetch).toHaveBeenCalledOnce();
    const callUrl = mockFetch.mock.calls[0][0] as string;
    expect(callUrl).toContain("/v1/oauth/callback/state123");
    expect(callUrl).toContain("code=code456");
    expect(callUrl).toContain("code_verifier=verifier789");
  });

  it("handles error response", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ error: "invalid_grant", scope: "api" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    const result = await handler.exchangeCode("state123", "code456");

    expect(result.authorized).toBe(false);
    expect(result.error).toBe("invalid_grant");
    expect(result.scope).toEqual(["api"]);
  });

  it("raises on HTTP error", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce(
      new Response("Internal Server Error", { status: 500 }),
    );

    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    await expect(handler.exchangeCode("state123", "code456")).rejects.toThrow(
      OAuthError,
    );
  });

  it("raises on network error", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockRejectedValueOnce(new Error("Network error"));

    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    await expect(handler.exchangeCode("state123", "code456")).rejects.toThrow(
      OAuthError,
    );
  });
});

// ---------------------------------------------------------------------------
// getServerConfig tests
// ---------------------------------------------------------------------------

describe("GetServerConfig", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches config successfully", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify(makeRawConfig()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    const config = await handler.getServerConfig("io.salesforce/salesforce-mcp");

    expect(config.name).toBe("io.salesforce/salesforce-mcp");
    expect(config.client_id).toBe("client-abc-123");
    expect(mockFetch).toHaveBeenCalledOnce();
  });

  it("raises on 404", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce(new Response("Not Found", { status: 404 }));

    const handler = new OAuthFlowHandler("https://registry.pharos.dev");
    await expect(
      handler.getServerConfig("nonexistent/server"),
    ).rejects.toThrow(OAuthError);
  });
});

// ---------------------------------------------------------------------------
// Connect flow tests
// ---------------------------------------------------------------------------

describe("ConnectFlow", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("completes the full connect flow", async () => {
    const mockFetch = vi.mocked(fetch);
    // First call: getServerConfig
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify(makeRawConfig()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    // Second call: exchangeCode (callback endpoint)
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          access_token: "tok-final",
          token_type: "Bearer",
          scope: "api",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    // Use a renderer that captures the state from the authorize URL and
    // returns a matching redirect with that same state.
    const statefulRenderer: OAuthRenderer = {
      async render(authorizeUrl: string): Promise<string> {
        const url = new URL(authorizeUrl);
        const state = url.searchParams.get("state") ?? "";
        return `https://getpharos.dev/v1/oauth/callback/${state}?code=authcode123&state=${state}`;
      },
    };

    const handler = new OAuthFlowHandler("https://registry.pharos.dev", {
      renderer: statefulRenderer,
    });

    const result = await handler.connect("io.salesforce/salesforce-mcp");

    expect(result.authorized).toBe(true);
    expect(result.access_token).toBe("tok-final");
  });

  it("detects state mismatch", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify(makeRawConfig()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const renderer = new MockRenderer(
      "https://getpharos.dev/callback?code=authcode&state=wrongstate",
    );
    const handler = new OAuthFlowHandler("https://registry.pharos.dev", {
      renderer,
    });

    await expect(handler.connect("io.salesforce/salesforce-mcp")).rejects.toThrow(
      OAuthError,
    );
  });

  it("returns error result on callback error param", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify(makeRawConfig()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const renderer = new MockRenderer(
      "https://getpharos.dev/callback?error=access_denied&error_description=user+cancelled",
    );
    const handler = new OAuthFlowHandler("https://registry.pharos.dev", {
      renderer,
    });

    const result = await handler.connect("io.salesforce/salesforce-mcp");
    expect(result.authorized).toBe(false);
    expect(result.error).toBe("access_denied");
  });
});

// ---------------------------------------------------------------------------
// Client.oauth property tests
// ---------------------------------------------------------------------------

describe("ClientOAuthProperty", () => {
  it("returns an OAuthFlowHandler", () => {
    const client = new PharosClient("https://registry.pharos.dev");
    const handler = client.oauth;
    expect(handler).toBeInstanceOf(OAuthFlowHandler);
  });

  it("returns the same handler on subsequent calls (lazy singleton)", () => {
    const client = new PharosClient("https://registry.pharos.dev");
    const h1 = client.oauth;
    const h2 = client.oauth;
    expect(h1).toBe(h2);
  });
});

// ---------------------------------------------------------------------------
// BrowserOAuthRenderer tests
// ---------------------------------------------------------------------------

describe("BrowserOAuthRenderer", () => {
  it("renderHtml contains iframe element", () => {
    const renderer = new BrowserOAuthRenderer();
    const config = new OAuthServerConfig(makeRawConfig());
    const html = renderer.renderHtml("https://login.salesforce.com/auth?client_id=x", config);
    expect(html).toContain("<iframe");
    expect(html).toContain('id="pharos-oauth-iframe"');
  });

  it("renderHtml contains CSP meta tag", () => {
    const renderer = new BrowserOAuthRenderer();
    const config = new OAuthServerConfig(makeRawConfig());
    const html = renderer.renderHtml("https://login.salesforce.com/auth", config);
    expect(html).toContain("Content-Security-Policy");
    expect(html).toContain("frame-src");
    expect(html).toContain("https://login.salesforce.com");
  });

  it("renderHtml contains postMessage listener", () => {
    const renderer = new BrowserOAuthRenderer();
    const config = new OAuthServerConfig(makeRawConfig());
    const html = renderer.renderHtml("https://login.salesforce.com/auth", config);
    expect(html).toContain("addEventListener");
    expect(html).toContain("message");
    expect(html).toContain("oauth/result");
    expect(html).toContain("jsonrpc");
  });

  it("renderHtml shows IdP name", () => {
    const renderer = new BrowserOAuthRenderer();
    const config = new OAuthServerConfig(makeRawConfig());
    const html = renderer.renderHtml("https://login.salesforce.com/auth", config);
    expect(html).toContain("login.salesforce.com");
  });

  it("render() raises when no host connected", async () => {
    const renderer = new BrowserOAuthRenderer();
    const config = new OAuthServerConfig(makeRawConfig());
    await expect(
      renderer.render("https://login.salesforce.com/auth", config),
    ).rejects.toThrow(OAuthError);
  });

  it("iframe has sandbox attribute", () => {
    const renderer = new BrowserOAuthRenderer();
    const config = new OAuthServerConfig(makeRawConfig());
    const html = renderer.renderHtml("https://login.salesforce.com/auth", config);
    expect(html).toContain('sandbox="allow-scripts allow-forms allow-same-origin allow-popups"');
  });
});

// ---------------------------------------------------------------------------
// TerminalOAuthRenderer tests
// ---------------------------------------------------------------------------

describe("TerminalOAuthRenderer", () => {
  it("has default timeout of 300000ms", () => {
    const r = new TerminalOAuthRenderer();
    expect(r.timeout).toBe(300_000);
  });

  it("accepts custom timeout", () => {
    const r = new TerminalOAuthRenderer(60_000);
    expect(r.timeout).toBe(60_000);
  });
});
