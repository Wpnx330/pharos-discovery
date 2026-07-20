# OAuth Brokering — App Registration Inheritance

**Spec reference:** `SPEC.md` v0.4.0, §17 (OAuth via App Registration Inheritance), §8.3 (`OAuthResult`), §10.5 (secret isolation), §10.7 (threat model), §10.9 (key rotation).
**Phase:** Designed in Phase 0. **Implemented in Phase 2.** Phase 1 ships `auth.type: "none"` and `"api_key"` only; the data model is forward-compatible.
**Companion:** `.guides/security/SECURITY_GUIDE.md`, `docs/components/DISCOVERY_FLOW.md`.

This document walks through how OAuth works under App Registration Inheritance (ARI) — the model PHAROS Discovery uses instead of standard Dynamic Client Registration (DCR) or agent-held-token redirect flows.

---

## 1. The problem (SPEC §17.1)

MCP adopted OAuth 2.1, but standard DCR has four problems at scale:
1. **Unbounded DB growth** on authorization servers (every agent install calls `/register`).
2. **Client-expiry black hole** (ephemeral clients expire; no cleanup story).
3. **Per-instance client ID proliferation** (hard to audit, hard to revoke).
4. **`/register` DoS** (unauthenticated endpoint by spec).

On top of that, the standard redirect flow is a poor fit for agents:
5. An **agent holding tokens** is a high-value target (token theft).
6. **Leaving the chat** to log in breaks the agentic UX.

---

## 2. The two levels of registration (SPEC §17.2)

### Level 1 — Agent Provider Registration (CIMD)

Agent providers (OpenAI, Anthropic, Cursor, DeepSeek, Gemini, xAI, Zap, custom) register **once** with the Pharos Registry. The registry hosts the provider's **Client ID Metadata Document (CIMD)** at a stable signed URL:

```
https://registry.pharos.dev/v1/agents/{provider_id}/cimd
```

This establishes the **agent provider's verified identity**. It is used for:
- Agent auth to the registry (the agent presents its CIMD when calling `/v1/search` etc.).
- Vendor-side agent allow-listing (a vendor MAY restrict their MCP server to specific agent providers).

**The CIMD is NOT the `client_id` used against each MCP server's authorization server.** It is a separate identity document for the agent provider.

### Level 2 — Vendor App Registration Inheritance

MCP server vendors (Salesforce, Stripe, Acme) pre-register an OAuth app with their own IdP and **bundle that registration into `pharos.json`**:

```json
{
  "auth": {
    "type": "oauth",
    "secret_handling": "server_side",
    "app_registration": {
      "client_id": "acme-mcp-flights-prod",
      "auth_server_url": "https://auth.acme.com",
      "grant_types": ["authorization_code", "refresh_token"],
      "scopes": [
        {"name": "flights:read", "description": "Search flights"},
        {"name": "flights:write", "description": "Book flights"},
        {"name": "expenses:read", "description": "Read expense reports"}
      ],
      "consent_defaults": ["flights:read"],
      "redirect_uri_pattern": "https://*.pharos.dev/oauth/callback",
      "app_management_url": "https://auth.acme.com/apps/acme-mcp-flights-prod",
      "endpoints": {
        "authorization": "https://auth.acme.com/oauth/authorize",
        "token": "https://auth.acme.com/oauth/token",
        "revocation": "https://auth.acme.com/oauth/revoke",
        "jwks": "https://auth.acme.com/.well-known/jwks.json"
      }
    },
    "ui": {
      "resource_uri": "https://auth.acme.com/oauth/inline",
      "csp": "default-src 'self' auth.acme.com"
    }
  }
}
```

**`client_secret` is NEVER in `pharos.json`.** It lives only in the MCP server's server-side configuration, bundled at build time. When an agent installs the MCP server, it **inherits** the vendor's app registration — no user creates a new app registration, no agent calls `/register`.

### Net effect

- Agent providers register once for identity.
- Vendors register once per MCP server with their own IdP.
- Every agent install inherits the vendor's app registration.
- No per-instance client IDs. No `/register` calls. No `client_secret` in registry, agent, or SDK.

---

## 3. CIMD signing (H15 — critical)

CIMD documents are **signed by the agent provider**, NOT by the Pharos Registry. The registry serves them as **opaque signed blobs** at the stable CIMD URL — it is a content host, not an issuer.

Vendors verify the CIMD signature against the provider's **pinned public key**, fetched from the provider's own `.well-known/agent-provider-keys` endpoint (provider-controlled), NOT from registry data.

**Why this matters:** a compromised or malicious registry cannot mint a fake agent-provider identity. Even if the registry serves a forged CIMD blob, the vendor's signature check against the provider-pinned key fails. (SPEC §10.7 threat H15.)

---

## 4. The `OAuthFlowHandler` — coordinates, does not run a redirect flow (SPEC §17.4)

Under App Registration Inheritance, the `OAuthFlowHandler` **coordinates** rather than running a standard OAuth redirect. Five steps:

```
1. Agent discovers server → ServerCard.auth includes app_registration + ui config
2. SDK presents vendor consent_defaults to user (pre-checked; user may expand or reduce)
   → SDK records approved_oauth_scopes in ApprovalToken
3. Agent installs/enables MCP server (MCP initialize)
4. OAuthFlowHandler triggers MCP server's inline OAuth UI (MCP Apps sandboxed iframe)
   → MCP server handles OAuth SERVER-SIDE:
     • uses inherited client_id (no /register call)
     • holds client_secret server-side
     • exchanges auth code for token itself
     • stores token server-side
5. MCP server sends SIGNED CONFIRMATION (not the token):
   → JWT from vendor's IdP attesting { user_sub, scope, exp, client_id }
   → SDK MUST verify via app_registration.endpoints.jwks before trusting authorized=true
   → on failure: OAuthResult.authorized=false, error="invalid_jwt"; connection torn down
   → token stays with MCP server, which proxies all tool calls
```

### Sequencing (H5 — critical)

`pharos.connect(approval)` calls `OAuthFlowHandler.authorize()` **FIRST**, before MCP `initialize`. Only on `authorized=true` does `initialize` proceed. This prevents a half-connected server that hasn't authenticated.

### Crash/disconnect handling (H10)

If the iframe errors, the MCP server disconnects, or `oauth_timeout` (120s) fires:
1. Invalidate the `ApprovalToken` (cannot be reused).
2. Emit `OAuthResult.authorized=false, error="server_lost"` (or `"timeout"`).
3. Tear down the connection (close iframe, abort in-flight `initialize`).
4. Surface `RetryableOAuthFailure(server_id, reason)` — host re-prompts. SDK does NOT auto-retry (server-side state indeterminate).

---

## 5. The `OAuthResult` (SPEC §8.3)

```python
class OAuthResult:
    authorized: bool
    access_token: str | None      # None when secret_handling == "server_side"
    token_type: str | None        # "Bearer" when token returned; None when server-side
    expires_in: int | None
    refresh_token: str | None     # None when server-side
    scope: list[str]              # actually granted (may be subset of approved_oauth_scopes)
    acquired_via: str             # "app_registration_inheritance" | "cimd" | "dcr" | "api_key" | "static" | "server_brokered_redirect"
    auth_held_by: str             # "mcp_server" | "agent" — under §17, always "mcp_server"
    confirmed_at: str             # ISO8601 of MCP server's auth-completed confirmation
    confirmation_jwt: str | None  # SIGNED IdP assertion; SDK MUST verify via endpoints.jwks
    error: str | None             # "timeout" | "server_lost" | "cancelled" | "invalid_jwt" | "idp_error"
    cancel_reason: str | None     # when user dismissed the iframe
```

### JWT verification (mandatory)

The SDK MUST verify the `confirmation_jwt`:
1. **Signature** against `app_registration.endpoints.jwks` (vendor's IdP public keys).
2. **`exp`** not in the past.
3. **`client_id`** matches the inherited `app_registration.client_id`.

On verification failure → `authorized=false, error="invalid_jwt"`, connection torn down. (§17.4 step 5)

---

## 6. Flow selection (SPEC §17.4 table)

The SDK selects the OAuth flow based on the server's `auth` config:

| Server `auth` config | Flow | `client_id` source | Token holder |
|---|---|---|---|
| `app_registration` present, `secret_handling == "server_side"` | **App Registration Inheritance** (preferred) | Vendor's pre-registered `app_registration.client_id`, inherited. No `/register`. | MCP server |
| `app_registration` absent, `dcr_support == true` | **DCR fallback** (legacy) | Dynamically registered via `dcr_endpoint` by MCP server. Ephemeral. Rate-limited. | MCP server (preferred) or agent (legacy) |
| `app_registration` absent, `dcr_support == false`, static client configured | **Static client credentials** (legacy) | Pre-registered from host credential store. | Agent |
| `auth.type == "api_key"` | **API key prompt** | Host `credential_provider` callback. | Agent |

---

## 7. Host capability negotiation (SPEC §17.5.1)

At startup, the SDK probes the host runtime:

| Host capability | OAuth flow | Token holder |
|---|---|---|
| `supports_mcp_apps: true` | **Inline flow** (MCP Apps sandboxed iframe in chat) | MCP server |
| `supports_mcp_apps: false`, `has_system_browser: true` | **Server-brokered redirect with PKCE** (below) | MCP server |
| neither | **`OAuthUnavailable`** — SDK refuses to start OAuth; host MAY fall back to `api_key` if supported | n/a |

### Server-brokered redirect with PKCE (§17.5.1)

Preserves "agent never sees the token" by keeping the auth-code exchange server-side:

1. MCP server generates PKCE verifier + challenge, returns short-lived broker session + IdP `/oauth/authorize` URL (with inherited `client_id`, PKCE challenge, `redirect_uri` → MCP server's `/oauth/callback`, `state`).
2. SDK opens system browser to that URL. User authenticates at vendor's real IdP (browser address bar + cert = trust anchor).
3. IdP redirects to MCP server's `/oauth/callback` with auth code. MCP server exchanges code **server-side** (stored `client_secret` + PKCE verifier), stores token server-side.
4. SDK polls MCP server's OAuth-status endpoint OR receives callback. MCP server returns **signed confirmation** (§17.4 step 5) — never the token.
5. `OAuthResult.acquired_via == "server_brokered_redirect"`.

---

## 8. Inline OAuth phishing defenses (SPEC §17.5.3)

Because the inline OAuth UI renders inside host chrome, a malicious MCP server could spoof a vendor login page. Defenses (host chrome is the non-spoofable trust anchor):

- **Host-rendered chrome (not server-rendered).** Host renders a border/chrome around the iframe displaying:
  - Publisher's **verified domain** (`publisher.id`, e.g. `acme.com`) + verification badge.
  - OAuth `authorization`/`token` endpoints.
  - Warning: "Do not enter your password if the domain shown in the iframe's address bar does not match your IdP."
  - Iframe content cannot draw over this chrome.
- **Iframe navigates to the IdP's real authorize URL.** Host surfaces iframe's current URL/registrable domain in chrome. If iframe attempts to navigate outside `app_registration.endpoints` → host blocks navigation, aborts flow with hard error.
- **Brand-similarity rejection at publish time** (§7.2) — `display_name`/`publisher.name` with Levenshtein distance ≤ 2 against a brand list rejected unless publisher owns the brand's verified domain.
- **`client_id` binding to verified publisher (H14).** At publish time, `auth.app_registration.client_id` is bound to the publisher's verified domain. An attacker cannot copy Acme's `client_id` to a different domain.
- **Threat model entry:** "Inline OAuth phishing (C5)" in §10.7.

---

## 9. Secret & token isolation (SPEC §10.5)

**Key security property:** the vendor's `client_secret` is NEVER present in the registry, the agent, or the SDK. It lives only in the MCP server's server-side configuration, bundled via `pharos.json` at build time and never serialized into a `ServerCard`.

| Where | Has `client_secret`? | Has access/refresh token? |
|-------|---------------------|--------------------------|
| Vendor's IdP | ✅ (issuer) | ✅ (issuer) |
| MCP server server-side config (`pharos.json` build-time bundle) | ✅ | ✅ (after flow) |
| `pharos.json` (the bundled registration) | ❌ — metadata only | ❌ |
| `ServerCard.auth.app_registration` | ❌ — metadata only | ❌ |
| Pharos Registry | ❌ | ❌ |
| Agent runtime | ❌ | ❌ |
| SDK (either language) | ❌ | ❌ |

**Token isolation:** because the MCP server runs the OAuth flow server-side and proxies tool calls, the access token and refresh token never reach the agent runtime. There is no in-memory token store in the SDK to attack, no OS keychain entry to exfiltrate, no token in logs. The `OAuthResult` returned to the agent is a boolean confirmation plus the granted scope set — never the token. (§10.5)

---

## 10. Revocation (SPEC §10.5, H16)

`OAuthFlowHandler.revoke_access(server_id)` is **best-effort as a request**, but the MCP server MUST return a `revocation_proof` within 60s — a signed assertion it called `endpoints.revocation` with the token, OR a token-introspection (RFC 7662) response showing `active: false`. SDK verifies the proof against `endpoints.jwks`.

```python
class RevocationResult:
    revoked: bool
    revocation_proof: str | None   # signed assertion; required within 60s (H16)
    error: str | None
```

If no proof within 60s → server marked `revocation_unconfirmed`, SDK surfaces warning:

> "Acme Flights may still have access to your account. Revoke directly at `<vendor app-management URL>`."

The `ServerCard` exposes `auth.app_registration.app_management_url` and `auth.app_registration.endpoints.revocation` so the user can revoke at the IdP directly when the MCP server is unresponsive or malicious.

---

## 11. Comparison: ARI vs. redirect-flow model (SPEC §17.6)

| Concern | App Registration Inheritance | Standard redirect flow (agent holds token) |
|---------|------------------------------|--------------------------------------------|
| Client registration | Vendor pre-registers once; all installs inherit. No `/register`. | DCR per install, or static client per agent. |
| `client_secret` location | MCP server server-side only. | Agent or MCP server. |
| Token holder | MCP server (proxies tool calls). | Agent (passes token on every call). |
| Agent token attack surface | None — agent never sees token. | High — in-memory token store, keychain, logs. |
| UX | Inline OAuth in chat (MCP Apps iframe) or system browser. | System browser redirect (leaves chat). |
| DB growth on IdP | Bounded — one client per vendor per MCP server. | Unbounded under DCR. |
| Revocation | Request to MCP server + `revocation_proof` within 60s. | Agent deletes token (best-effort). |
| Per-instance audit | No per-instance client IDs. | Per-instance client IDs under DCR. |

---

## 12. Phase 1 forward-compatibility

Phase 1 ships with `auth.type: "none"` and `"api_key"` only. But the `AuthSpec` model already includes `app_registration`, `secret_handling`, `ui`, and the `OAuthFlowHandler` interface is defined in the IDL. This means:

- Phase 1 `ServerCard`s with `auth.type: "none"` or `"api_key"` work today.
- A Phase 1 SDK that encounters a `ServerCard` with `auth.type: "oauth"` will see the `app_registration` block and refuse to connect with a clear error ("OAuth support arrives in Phase 2"), rather than silently mishandling it.
- Phase 2 implementation adds the `OAuthFlowHandler` logic without changing the `ServerCard` schema.

**Do not** add OAuth handling to Phase 1 SDKs beyond the refusal-with-clear-error behavior. The full flow (inline iframe, JWT verification, revocation) is Phase 2.

---

## 13. Threat model entries (SPEC §10.7)

| Threat | Mitigation |
|--------|-----------|
| OAuth token theft | Tokens stay server-side in MCP server; agent/SDK never receive token. |
| OAuth `client_secret` leak | Secret never in registry, agent, or SDK — only MCP server server-side config. |
| Per-instance client ID proliferation | Vendor pre-registers one app; all installs inherit same `client_id`. |
| Malicious agent triggers OAuth | Pharos CIMD verifies agent provider identity first; vendors MAY allow-list providers. |
| DCR endpoint DoS / DB growth | DCR is fallback only; MCP server rate-limits DCR; ephemeral client IDs; ARI avoids `/register` entirely. |
| Server-side exfiltration (C7) | Token held by MCP server; server can call vendor API for any in-scope purpose. Mitigations partial: explicit "any purpose within scopes" consent text, fine-grained scopes, registry static analysis of mirrored servers. **Acknowledged residual risk.** |
| Revocation not honored (H16) | `revocation_proof` required within 60s; on failure, `revocation_unconfirmed` + user warning with vendor app-management URL. |
| Registry mints fake agent identity (H15) | CIMD signed by agent provider (not registry); registry serves opaque signed blobs; vendors verify provider signature against pinned provider key from `.well-known/agent-provider-keys`. |
| Inline OAuth phishing (C5) | Host (not server) renders non-spoofable chrome: publisher verified domain, OAuth endpoints, password-mismatch warning. Iframe navigates to IdP's real authorize URL. Brand-similarity rejection at publish time. |

---

*See also: `.guides/security/SECURITY_GUIDE.md` for the full security model; `docs/components/DISCOVERY_FLOW.md` for where OAuth fits in the connect step; `docs/api/PYTHON_API.md` / `docs/api/TYPESCRIPT_API.md` for `OAuthFlowHandler` signatures.*
