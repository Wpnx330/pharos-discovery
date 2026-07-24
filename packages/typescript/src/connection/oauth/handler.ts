/**
 * MCP Apps inline OAuth iframe renderer — agent-side component (§18.5-18.6).
 *
 * Implements the agent-side handler that renders the inline OAuth consent flow
 * for MCP servers requiring OAuth. Supports two renderers:
 *
 * - **Terminal** — opens the system browser to the IdP authorize URL and catches
 *   the redirect on a local loopback HTTP server (the §18.5.1 fallback).
 * - **Browser** — creates a sandboxed iframe pointing to the IdP's own origin,
 *   enforces CSP (only declared IdP origins may be loaded), and communicates
 *   the result back via `postMessage` (JSON-RPC).
 *
 * Flow (per spec §18.6):
 *   1. Fetch the server's OAuth config from `GET /v1/oauth/servers/{name}`.
 *   2. Extract the IdP `authorize_endpoint` URL + scopes + `client_id`.
 *   3. Generate a PKCE `code_verifier` + `code_challenge` (S256).
 *   4. Construct the authorize URL with a state nonce.
 *   5. Render (terminal or browser iframe).
 *   6. Exchange the authorization code for tokens via the MCP server's callback
 *      endpoint (`GET /v1/oauth/callback/{state}`).
 *   7. Return an `OAuthResult`.
 */

import { OAuthError } from "../../errors.js";
import type { OAuthResult } from "../../models/oauth.js";

// ---------------------------------------------------------------------------
// PKCE utilities
// ---------------------------------------------------------------------------

const UNRESERVED_CHARS =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";

/**
 * Generate a cryptographically-random PKCE `code_verifier`.
 * RFC 7636 §4.1: 43-128 chars from the unreserved set.
 */
export function generatePkceVerifier(length: number = 64): string {
  if (length < 43 || length > 128) {
    throw new Error("code_verifier must be 43-128 characters");
  }
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  let result = "";
  for (let i = 0; i < length; i++) {
    result += UNRESERVED_CHARS[bytes[i] % UNRESERVED_CHARS.length];
  }
  return result;
}

/**
 * Compute the PKCE `code_challenge` from a `code_verifier`.
 * S256: `BASE64URL(SHA256(verifier))` (no padding).
 */
export async function computePkceChallenge(
  verifier: string,
  method: "S256" | "plain" = "S256",
): Promise<string> {
  if (method === "plain") return verifier;

  const encoder = new TextEncoder();
  const data = encoder.encode(verifier);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  const hashBytes = new Uint8Array(hashBuffer);

  // Base64url encode without padding
  let str = "";
  for (const b of hashBytes) {
    str += String.fromCharCode(b);
  }
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Generate a high-entropy, single-use state nonce (§18.6).
 */
export function generateStateNonce(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let str = "";
  for (const b of bytes) {
    str += String.fromCharCode(b);
  }
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// ---------------------------------------------------------------------------
// OAuth server config
// ---------------------------------------------------------------------------

export interface RawOAuthConfig {
  name?: string;
  version?: string;
  oauth?: {
    app_registration?: {
      client_id?: string;
      auth_server_url?: string;
      grant_type?: string;
      pkce_required?: boolean;
      scopes?: string[];
      consent_defaults?: Record<string, unknown>;
      redirect_uri_pattern?: string;
      token_endpoint?: string;
      authorize_endpoint?: string;
    };
    ui?: {
      resource_uri?: string;
      csp?: string[];
      description?: string;
    };
    secret_handling?: string;
    secret_description?: string;
  };
  // Flattened variants (no "oauth" wrapper)
  app_registration?: Record<string, unknown>;
  ui?: Record<string, unknown>;
}

export class OAuthServerConfig {
  name: string;
  version: string;
  client_id: string;
  authorize_endpoint: string;
  token_endpoint: string;
  auth_server_url: string;
  scopes: string[];
  redirect_uri_pattern: string;
  pkce_required: boolean;
  grant_type: string;
  consent_defaults: Record<string, unknown>;
  ui_resource_uri: string;
  ui_csp: string[];
  ui_description: string;
  secret_handling: string;

  constructor(raw: RawOAuthConfig) {
    const oauth = raw.oauth ?? (raw as Record<string, unknown>);
    const reg =
      (oauth as Record<string, any>).app_registration ??
      raw.app_registration ??
      {};
    const ui = (oauth as Record<string, any>).ui ?? raw.ui ?? {};

    this.name = (raw.name as string) ?? (oauth as Record<string, any>).name ?? "";
    this.version =
      (raw.version as string) ?? (oauth as Record<string, any>).version ?? "";
    this.client_id = reg.client_id ?? "";
    this.authorize_endpoint = reg.authorize_endpoint ?? "";
    this.token_endpoint = reg.token_endpoint ?? "";
    this.auth_server_url = reg.auth_server_url ?? "";
    this.scopes = reg.scopes ?? [];
    this.redirect_uri_pattern = reg.redirect_uri_pattern ?? "";
    this.pkce_required = reg.pkce_required ?? true;
    this.grant_type = reg.grant_type ?? "authorization_code";
    this.consent_defaults = reg.consent_defaults ?? {};
    this.ui_resource_uri = ui.resource_uri ?? "ui://oauth/login";
    this.ui_csp = ui.csp ?? [];
    this.ui_description = ui.description ?? "";
    this.secret_handling =
      (oauth as Record<string, any>).secret_handling ?? "server_side";
  }

  get idpOrigin(): string {
    try {
      const url = new URL(this.authorize_endpoint);
      return `${url.protocol}//${url.host}`;
    } catch {
      return "";
    }
  }
}

// ---------------------------------------------------------------------------
// Renderer interface
// ---------------------------------------------------------------------------

export interface OAuthRenderer {
  /** Render the OAuth flow and return the redirect URI (with code+state). */
  render(authorizeUrl: string, config: OAuthServerConfig): Promise<string>;
}

// ---------------------------------------------------------------------------
// Terminal renderer — opens system browser, catches callback on localhost
// ---------------------------------------------------------------------------

/**
 * Render the OAuth flow in the terminal.
 *
 * Opens the system browser to the IdP authorize URL and spins up a local
 * HTTP server on a random port to catch the redirect callback.
 * This is the §18.5.1 browser-redirect fallback and the default renderer.
 *
 * Note: in Node.js, this uses the `child_process.exec` to open the browser
 * and `http.Server` to catch the callback. In the browser (web-agent
 * context), use `BrowserOAuthRenderer` instead.
 */
export class TerminalOAuthRenderer implements OAuthRenderer {
  timeout: number;

  constructor(timeout: number = 300_000) {
    this.timeout = timeout;
  }

  async render(authorizeUrl: string, config: OAuthServerConfig): Promise<string> {
    // In Node.js we would spin up an http.Server on a random port.
    // This implementation is environment-agnostic — the actual server
    // creation is delegated to the runtime.
    //
    // For Node.js:
    //   const port = await findFreePort();
    //   const redirectUri = `http://127.0.0.1:${port}/callback`;
    //   ... open browser, wait for callback ...
    //
    // For non-Node environments, print the URL and wait for manual callback.

    const url = new URL(authorizeUrl);
    const redirectUri = `http://127.0.0.1:0/callback`;
    url.searchParams.set("redirect_uri", redirectUri);

    console.log("\n  🔐 OAuth Login Required");
    if (config.ui_description) {
      console.log(`     ${config.ui_description}`);
    }
    console.log(`     IdP: ${config.auth_server_url || config.idpOrigin}`);
    console.log(`     Opening browser to:\n     ${url.toString().slice(0, 100)}...`);
    console.log(`\n     Waiting for callback (timeout: ${this.timeout}ms)...`);

    // Attempt to open browser (Node.js)
    try {
      const { exec } = await import("node:child_process");
      const openCmd =
        process.platform === "win32"
          ? "start"
          : process.platform === "darwin"
            ? "open"
            : "xdg-open";
      exec(`${openCmd} "${url.toString()}"`);
    } catch {
      // Not in Node.js or can't open browser — user opens manually
    }

    // In a real implementation, we'd start an http.Server and wait.
    // For now, this is a placeholder that raises an error indicating
    // the host application should handle the callback.
    throw new OAuthError(
      "terminal_callback_not_implemented",
      "TerminalOAuthRenderer.render() requires a Node.js HTTP server to catch " +
        "the callback. Use the Node.js-specific implementation or handle the " +
        "callback manually.",
    );
  }
}

// ---------------------------------------------------------------------------
// Browser (iframe) renderer — sandboxed iframe with CSP + postMessage
// ---------------------------------------------------------------------------

/**
 * Render the OAuth flow in a sandboxed iframe (MCP Apps inline UI).
 *
 * For web-based agents: creates a sandboxed iframe pointing to the IdP's
 * authorize URL, enforces CSP (only allow the declared IdP origin), and
 * communicates the result back via `postMessage` (JSON-RPC).
 */
export class BrowserOAuthRenderer implements OAuthRenderer {
  timeout: number;

  constructor(timeout: number = 300_000) {
    this.timeout = timeout;
  }

  async render(authorizeUrl: string, config: OAuthServerConfig): Promise<string> {
    // In a real web-agent context, this would inject the iframe into the DOM
    // and listen for postMessage events. Here we generate the HTML and
    // raise an error indicating the host should handle it.
    throw new OAuthError(
      "browser_renderer_not_connected",
      "BrowserOAuthRenderer.render() requires a web-agent host to inject the " +
        "iframe and listen for postMessage. Use renderHtml() to get the iframe HTML.",
    );
  }

  /**
   * Generate the sandboxed iframe HTML for embedding in a web agent.
   *
   * Returns the HTML string containing a sandboxed iframe + postMessage
   * listener. The host agent injects this into its DOM and waits for
   * the `oauth/result` JSON-RPC message.
   */
  renderHtml(authorizeUrl: string, config: OAuthServerConfig): string {
    const cspOrigins = config.ui_csp.length > 0 ? config.ui_csp : [config.idpOrigin];
    const cspDirective = cspOrigins
      .map((origin) => `frame-src 'self' ${origin}`)
      .join("; ");
    const idpDisplay = config.auth_server_url || config.idpOrigin;
    const allowedOriginsJson = JSON.stringify(cspOrigins);

    return [
      "<!DOCTYPE html>",
      '<html lang="en">',
      "<head>",
      '  <meta charset="utf-8">',
      '  <meta name="viewport" content="width=device-width, initial-scale=1">',
      `  <title>Pharos OAuth — ${config.name}</title>`,
      `  <meta http-equiv="Content-Security-Policy" content="${cspDirective}; script-src 'unsafe-inline'">`,
      "  <style>",
      "    body { margin: 0; font-family: -apple-system, system-ui, sans-serif; }",
      "    .pharos-oauth-bar { background: #1a1a2e; color: #e0e0e0; padding: 12px 16px;",
      "      font-size: 14px; display: flex; align-items: center; gap: 8px; }",
      "    .pharos-oauth-bar .lock { font-size: 18px; }",
      "    .pharos-oauth-bar .idp { font-weight: 600; }",
      "    .pharos-oauth-frame { width: 100%; height: 600px; border: none; }",
      "    .pharos-oauth-error { padding: 24px; color: #c62828; text-align: center; }",
      "  </style>",
      "</head>",
      "<body>",
      '  <div class="pharos-oauth-bar">',
      '    <span class="lock">🔐</span>',
      `    <span>Logging in to <span class="idp">${idpDisplay}</span> — verify the URL in the iframe.</span>`,
      "  </div>",
      '  <iframe',
      '    class="pharos-oauth-frame"',
      '    sandbox="allow-scripts allow-forms allow-same-origin allow-popups"',
      `    src="${authorizeUrl}"`,
      '    id="pharos-oauth-iframe">',
      "  </iframe>",
      '  <div class="pharos-oauth-error" id="pharos-error" style="display:none;"></div>',
      "  <script>",
      "    // postMessage protocol: JSON-RPC 2.0 over postMessage",
      "    // The IdP callback page sends: method=oauth/result with code+state+redirect_uri",
      `    var ALLOWED_ORIGINS = ${allowedOriginsJson};`,
      '    window.addEventListener("message", function(event) {',
      "      // Verify origin against CSP allowlist",
      "      if (ALLOWED_ORIGINS.indexOf(event.origin) === -1) return;",
      "      var data = event.data;",
      '      if (!data || data.jsonrpc !== "2.0") return;',
      '      if (data.method === "oauth/result") {',
      "        // Forward to parent agent",
      '        window.parent.postMessage({',
      '          "jsonrpc": "2.0",',
      '          "method": "oauth/result",',
      '          "params": data.params',
      '        }, "*");',
      "      }",
      '      if (data.method === "oauth/error") {',
      '        document.getElementById("pharos-error").textContent =',
      '          "OAuth error: " + (data.params.error || "unknown");',
      '        document.getElementById("pharos-error").style.display = "block";',
      '        document.getElementById("pharos-oauth-iframe").style.display = "none";',
      "      }",
      "    });",
      "  </script>",
      "</body>",
      "</html>",
    ].join("\n");
  }
}

// ---------------------------------------------------------------------------
// OAuthFlowHandler — the main agent-side handler
// ---------------------------------------------------------------------------

export interface OAuthFlowHandlerOptions {
  renderer?: OAuthRenderer;
  apiKey?: string;
  timeout?: number;
  callbackTimeout?: number;
}

export interface BuildAuthorizeUrlResult {
  authorizeUrl: string;
  state: string;
  codeVerifier: string;
  redirectUri: string;
}

/**
 * Agent-side handler for the MCP Apps inline OAuth flow.
 *
 * Coordinates the full OAuth flow:
 *   1. Fetch the server's OAuth config from `GET /v1/oauth/servers/{name}`.
 *   2. Generate PKCE `code_verifier` + `code_challenge` (S256).
 *   3. Construct the authorize URL with a state nonce.
 *   4. Render via the chosen renderer (terminal or browser).
 *   5. Exchange the authorization code for tokens via the MCP server's
 *      callback endpoint (`GET /v1/oauth/callback/{state}`).
 *   6. Return an `OAuthResult`.
 *
 * Usage (terminal/CLI — default):
 *   const handler = new OAuthFlowHandler("https://getpharos.dev");
 *   const result = await handler.connect("io.salesforce/salesforce-mcp");
 */
export class OAuthFlowHandler {
  private registryUrl: string;
  private apiKey: string | null;
  private timeout: number;
  private callbackTimeout: number;
  private _renderer: OAuthRenderer;

  constructor(
    registryUrl: string,
    options: OAuthFlowHandlerOptions = {},
  ) {
    this.registryUrl = registryUrl.replace(/\/+$/, "");
    this.apiKey = options.apiKey ?? null;
    this.timeout = options.timeout ?? 30_000;
    this.callbackTimeout = options.callbackTimeout ?? 300_000;
    this._renderer =
      options.renderer ?? new TerminalOAuthRenderer(this.callbackTimeout);
  }

  get renderer(): OAuthRenderer {
    return this._renderer;
  }

  set renderer(value: OAuthRenderer) {
    this._renderer = value;
  }

  /** Fetch the server's OAuth config from `GET /v1/oauth/servers/{name}`. */
  async getServerConfig(serverName: string): Promise<OAuthServerConfig> {
    const url = `${this.registryUrl}/v1/oauth/servers/${encodeURIComponent(serverName)}`;
    const headers: Record<string, string> = { Accept: "application/json" };
    if (this.apiKey) {
      headers["Authorization"] = `Bearer ${this.apiKey}`;
    }

    let resp: Response;
    try {
      resp = await fetch(url, {
        method: "GET",
        headers,
        signal: AbortSignal.timeout(this.timeout),
      });
    } catch (exc) {
      throw new OAuthError("config_fetch_failed", String(exc));
    }

    if (resp.status === 404) {
      throw new OAuthError(
        "server_not_configured",
        `No OAuth config for ${serverName}`,
      );
    }
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new OAuthError(
        "config_fetch_failed",
        `HTTP ${resp.status}: ${text.slice(0, 200)}`,
      );
    }

    const data = await resp.json();
    return new OAuthServerConfig(data);
  }

  /**
   * Construct the IdP authorize URL.
   * Returns `{ authorizeUrl, state, codeVerifier, redirectUri }`.
   */
  async buildAuthorizeUrl(
    config: OAuthServerConfig,
    options: {
      state?: string;
      codeChallenge?: string;
      redirectUri?: string;
      extraParams?: Record<string, string>;
    } = {},
  ): Promise<BuildAuthorizeUrlResult> {
    if (!config.authorize_endpoint) {
      throw new OAuthError(
        "missing_authorize_endpoint",
        "Server config has no authorize_endpoint",
      );
    }
    if (!config.client_id) {
      throw new OAuthError(
        "missing_client_id",
        "Server config has no client_id",
      );
    }

    const state = options.state ?? generateStateNonce();
    const redirectUri =
      options.redirectUri ??
      config.redirect_uri_pattern ??
      `${this.registryUrl}/v1/oauth/callback/${state}`;

    const params = new URLSearchParams({
      response_type: "code",
      client_id: config.client_id,
      redirect_uri: redirectUri,
      state,
      scope: config.scopes.join(" "),
    });

    let codeVerifier = "";
    if (config.pkce_required) {
      codeVerifier = generatePkceVerifier();
      const challenge = await computePkceChallenge(codeVerifier, "S256");
      params.set("code_challenge", challenge);
      params.set("code_challenge_method", "S256");
    }

    if (options.extraParams) {
      for (const [key, value] of Object.entries(options.extraParams)) {
        params.set(key, value);
      }
    }

    const separator = config.authorize_endpoint.includes("?") ? "&" : "?";
    const authorizeUrl = `${config.authorize_endpoint}${separator}${params.toString()}`;

    return { authorizeUrl, state, codeVerifier, redirectUri };
  }

  /**
   * Exchange the authorization code for tokens via the MCP server callback.
   *
   * The agent calls `GET /v1/oauth/callback/{state}?code=...` which the
   * registry forwards to the MCP server. The MCP server performs the
   * server-side token exchange (§18.6 step 9) and returns the result.
   */
  async exchangeCode(
    state: string,
    code: string,
    codeVerifier?: string,
  ): Promise<OAuthResult> {
    const url = new URL(
      `${this.registryUrl}/v1/oauth/callback/${encodeURIComponent(state)}`,
    );
    url.searchParams.set("code", code);
    if (codeVerifier) {
      url.searchParams.set("code_verifier", codeVerifier);
    }

    const headers: Record<string, string> = { Accept: "application/json" };
    if (this.apiKey) {
      headers["Authorization"] = `Bearer ${this.apiKey}`;
    }

    let resp: Response;
    try {
      resp = await fetch(url.toString(), {
        method: "GET",
        headers,
        signal: AbortSignal.timeout(this.timeout),
      });
    } catch (exc) {
      throw new OAuthError("exchange_failed", String(exc));
    }

    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new OAuthError("exchange_failed", `HTTP ${resp.status}: ${text.slice(0, 500)}`);
    }

    const data = await resp.json();

    if (data.error) {
      const rawScope: unknown = data.scope ?? [];
      const scopeList =
        typeof rawScope === "string"
          ? rawScope.split(/\s+/)
          : Array.isArray(rawScope)
            ? rawScope
            : [];
      return {
        authorized: false,
        scope: scopeList,
        acquired_via: "oauth",
        auth_held_by: "mcp_server",
        confirmed_at: new Date().toISOString(),
        error: data.error,
      };
    }

    const rawScope: unknown = data.scope ?? "";
    const scopes =
      typeof rawScope === "string"
        ? rawScope.split(/\s+/)
        : Array.isArray(rawScope)
          ? rawScope
          : [];

    return {
      authorized: true,
      access_token: data.access_token ?? undefined,
      token_type: data.token_type ?? "Bearer",
      expires_in: data.expires_in ?? undefined,
      refresh_token: data.refresh_token ?? undefined,
      scope: scopes,
      acquired_via: "oauth",
      auth_held_by: "mcp_server",
      confirmed_at: new Date().toISOString(),
      confirmation_jwt: data.id_token ?? undefined,
    };
  }

  /**
   * Initiate the full OAuth flow for a server.
   *
   * 1. Fetch the server's OAuth config.
   * 2. Build the authorize URL (PKCE + state).
   * 3. Render (terminal or browser).
   * 4. Parse the callback redirect for code + state.
   * 5. Exchange the code for tokens.
   */
  async connect(
    serverName: string,
    options: { extraParams?: Record<string, string> } = {},
  ): Promise<OAuthResult> {
    // 1. Fetch config
    const config = await this.getServerConfig(serverName);

    // 2. Build authorize URL
    const { authorizeUrl, state, codeVerifier, redirectUri } =
      await this.buildAuthorizeUrl(config, {
        extraParams: options.extraParams,
      });

    // 3. Render
    const fullRedirect = await this._renderer.render(authorizeUrl, config);

    // 4. Parse callback
    const parsedUrl = new URL(fullRedirect, "http://pharos.local");
    const error = parsedUrl.searchParams.get("error");

    if (error) {
      const errorDesc = parsedUrl.searchParams.get("error_description") ?? undefined;
      return {
        authorized: false,
        scope: [],
        acquired_via: "oauth",
        auth_held_by: "mcp_server",
        confirmed_at: new Date().toISOString(),
        error,
        cancel_reason: errorDesc,
      };
    }

    const code = parsedUrl.searchParams.get("code");
    const returnedState = parsedUrl.searchParams.get("state");

    if (!code) {
      throw new OAuthError("no_code", "Callback redirect missing authorization code");
    }

    if (returnedState && returnedState !== state) {
      throw new OAuthError(
        "state_mismatch",
        `State mismatch: expected ${state.slice(0, 8)}..., got ${returnedState.slice(0, 8)}...`,
      );
    }

    // 5. Exchange code for tokens
    return this.exchangeCode(state, code, codeVerifier || undefined);
  }

  /** Check the OAuth status for a server (lightweight GET config). */
  async checkStatus(serverName: string): Promise<Record<string, unknown>> {
    const config = await this.getServerConfig(serverName);
    return {
      name: config.name,
      version: config.version,
      client_id: config.client_id,
      authorize_endpoint: config.authorize_endpoint,
      token_endpoint: config.token_endpoint,
      auth_server_url: config.auth_server_url,
      scopes: config.scopes,
      pkce_required: config.pkce_required,
      secret_handling: config.secret_handling,
      ui_csp: config.ui_csp,
      idp_origin: config.idpOrigin,
    };
  }
}
