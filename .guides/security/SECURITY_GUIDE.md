# Security Guide — PHAROS Discovery

**Audience:** AI agents and contributors touching approval, OAuth, storage, keys, or egress.
**Source of truth:** `SPEC.md` v0.4.0, §4.2 (consent principle), §7 (approval flow), §10 (security model), §17 (OAuth via App Registration Inheritance).
**Companion:** `docs/components/OAUTH_BROKERING.md`, `docs/components/DISCOVERY_FLOW.md`.

> **Read this entire guide before touching approval, OAuth, consent storage, publisher keys, or egress.** The security model is non-trivial and several invariants are easy to break silently.

---

## 1. The non-negotiable invariants

These are the security properties the SPEC mandates. Any change that weakens one is a blocker.

1. **Consent is non-negotiable for conformant SDK-using agents.** No connection to a discovered server without an explicit user approval event. There is NO `connect_without_approval` API. (SPEC §4.2, §7)
2. **`client_secret` NEVER appears in the registry, agent, or SDK.** It lives only in the MCP server's server-side config. `ServerCard.auth.app_registration` carries metadata only — never the secret. (SPEC §10.5, §17.2)
3. **Under App Registration Inheritance (Phase 2), the agent NEVER holds an OAuth token.** The MCP server brokers the flow server-side and proxies tool calls. `OAuthResult.access_token` / `refresh_token` are `None` when `secret_handling == "server_side"`. (SPEC §8.3, §17.4)
4. **The approval gate is a client-side contract.** It is non-bypassable for conformant SDK-using agents, but a non-SDK agent can bypass it by connecting directly. Server-side enforcement of `ApprovalToken` is a FUTURE protocol extension — do not hack it in. (SPEC §10.7.1)
5. **`query.text` is NEVER logged at user level.** Aggregate anonymized logging only. `privacy_mode` and `query.embedding` are the stronger-privacy paths. (SPEC §10.8)
6. **Publisher keys are TTL-bound, not held forever.** WHOIS registrant/nameserver change triggers immediate re-verification of `domain_control`. Failed re-fetch after TTL → server quarantined. (SPEC §9.3, §10.9)
7. **`headless_mode` is NOT a consent bypass.** It requires an explicit server allow-list + pre-approved scope set; refuses novel servers; is loudly logged; is mutually exclusive with `trust_on_use`. (SPEC §7.5)

---

## 2. The approval flow & consent contract (SPEC §7)

### 2.1 The six-step flow (SPEC §7.1)

```
1. Agent detects capability gap   (host on_capability_gap callback)
2. Agent calls pharos.search()
3. SDK returns ranked ServerCards
4. Agent renders approval card    (host render callback)
5. User approves/rejects           (SDK mints signed ApprovalToken on approve)
6. SDK performs MCP initialize + tools/list
```

**The invariant:** step 6 requires a valid `ApprovalToken` from step 5. The Connection Manager refuses `connect()` without one. There is no code path from `pharos.connect()` to `MCPClient.initialize()` that skips the token check. (SPEC §10.7.1)

### 2.2 The `ApprovalToken` (SPEC §7.4)

```python
class ApprovalToken:
    token_id: str
    server_id: str
    approved_scopes: list[str]              # MCP capability scopes
    approved_capabilities: list[str]
    approved_oauth_scopes: list[str]         # OAuth scopes (empty if auth.type != oauth)
    duration: str                            # "once" | "session" | "persistent" | "trust_on_use"
    approved_at: str
    expires_at: str
    signature: str                           # ed25519 over token body (local SDK key)
```

- Signed with a **local SDK key** (ed25519). The signature lets the SDK detect tampering with stored tokens.
- `approved_scopes` is enforced by the Connection Manager on every `tools/call`. Out-of-scope → `SCOPE_NOT_APPROVED`.
- For OAuth servers, `approved_oauth_scopes` is what the `OAuthFlowHandler` passes to the MCP server — **never** the full vendor-advertised set. (§17.4 scope minimization)

### 2.3 What the approval prompt MUST surface (SPEC §7.2)

Required on every prompt:
- `display_name` + `publisher.name`
- `publisher.verified` — visible "verified" badge OR "unverified — connect with caution" warning
- `description` — what the server does, plain language
- `capabilities` — the concrete capabilities the agent intends to use
- `auth.type` + `auth.scopes` — permissions requested
- **For OAuth servers:** enumerate OAuth scopes alongside MCP capability scopes in plain language. User approves BOTH the connection AND the OAuth scopes in one consent act. Consent defaults from vendor (`auth.app_registration.consent_defaults`) are pre-checked; user may expand or reduce. SDK records final set in `ApprovalToken.approved_oauth_scopes`.
- `pricing.model` + `pricing.price_usd` — with "vendor-claimed, not verified" caption unless `pricing_verified`
- `trust.attestations` — compliance badges (SOC2, GDPR, etc.)
- `rating.score` + `rating.count` — `count == 0` displays "New / unreviewed — no track record yet", NOT `0.0`
- The specific user request that triggered discovery (so the user understands *why*)

**Risk tier (SDK-computed):** `low`/`medium`/`high` from `auth.scopes` + `publisher.verified` + `rating.count` + `availability`. `high` (e.g. unverified publisher requesting write scopes, or any `payments:write`-class scope) requires explicit "I understand the risk" click before the approve button is enabled. **Vendor cannot game this** — it's SDK-side. (§7.2)

**Brand-impersonation rejection (§7.2):**
- Publish-time: registry rejects `display_name`/`publisher.name` with Levenshtein distance ≤ 2 against a brand list unless the publisher owns the brand's verified domain.
- Display-time: `publisher.verified` is a **hard gate** for any brand-matching name. Unverified "Googgle OAuth" → prominent "unverified — possible impersonation" warning, approve button disabled. Trust anchor is the **verified domain** shown in host chrome (§17.5.3), not the self-declared name.

### 2.4 Consent mechanics (SPEC §7.3)

- **Specific** — one server for one stated purpose.
- **Scoped** — user sees `auth.scopes` + `capabilities` and may approve a subset. For OAuth, user may narrow MCP scopes and OAuth scopes independently.
- **Revocable** — `pharos.revoke(server_id)` tears down connection + invalidates token.
- **Duration-bound** — `session` (default), `once` (single tool call), `persistent` (remembered across sessions, encrypted locally; requires second confirmation).
- **`trust_on_use`** — after one successful `tools/call` against `verified=true` + `availability="mirrored"` + `rating.count > 100`, subsequent connections within 7 days auto-approve non-modally (non-blocking notification, not modal). Decays after 7 days or on any `report_server`. Host MAY disable for high-risk scope classes (`payments:write`, `admin:*`). **Mutually exclusive with `headless_mode`.**
- **Plan approval** — `PlanApprovalRequest`/`PlanApprovalToken` for multi-server plans (one consent act, not N modals). Mitigates consent fatigue.
- **Consent fatigue budget** — >5 novel-server approvals in a session → `ConsentFatigueWarning` (advisory, not blocking).

### 2.5 Denial & re-negotiation (SPEC §7.7)

- `ApprovalResponse.deny_reason` (required when `approved=false`): `untrusted_publisher` | `excessive_scopes` | `wrong_server` | `cost` | `other`. Used to learn — down-rank publisher, re-search, re-prompt with reduced scopes, or seek a free alternative.
- `pharos.request_approval_next(current_server_id)` — returns next-ranked result without re-searching. Returns `None` → host falls back to fresh `search()` with broadened query.
- **Scope re-negotiation:** `SCOPE_NOT_APPROVED` failure → MAY surface re-approval prompt for the specific missing scope. Rate-limited to **at most 1 per server per session**. After one re-prompt, further failures are non-blocking errors.
- Denials recorded in consent store with `deny_reason`.

### 2.6 Headless mode (SPEC §7.5) — scoped, NOT blanket

```python
PharosClient(
    headless_mode=True,
    headless_allow_servers=["urn:pharos:acme.com:travel/flight-booking"],  # REQUIRED
    headless_allow_scopes=["flight_search"],                                # REQUIRED
)
```

- **Refuses novel servers** — any server NOT on the allow-list → `HeadlessApprovalRequired` error, connection NOT made. No silent connection to a server the user has never seen.
- **Loudly logged** — console warning + `on_tool_use` event tagged `headless=true`. An operator auditing logs cannot miss that an automated approval occurred.
- **Mutually exclusive with `trust_on_use`** — automated trust propagation is too dangerous in a pipeline.

### 2.7 UX patterns (SPEC §7.5)

The SDK exposes the flow as a **callback** so the host controls rendering:
1. **CLI/terminal** — inline text card, `[y/N/scope:...]` prompt. Default for stdio agents.
2. **Chat/web** — SDK returns JSON approval payload; host renders rich card with buttons; host calls `pharos.resolve_approval(payload)`.
3. **Voice/headless** — short spoken summary + verbal "yes, approve <server name>" confirmation. Headless pipelines use the scoped `headless_mode` above.

---

## 3. OAuth via App Registration Inheritance (SPEC §17 — Phase 2)

> Phase 1 ships with `auth.type: "none"` and `"api_key"` only. The OAuth surface is **designed for in Phase 0** (data model forward-compatible) but **implemented in Phase 2**. Read this section now so Phase 1 data shapes don't paint Phase 2 into a corner.

### 3.1 The problem it solves (SPEC §17.1)

MCP adopted OAuth 2.1, but standard Dynamic Client Registration (DCR) has four problems at scale: unbounded DB growth on authorization servers, client-expiry black hole, per-instance client ID proliferation, and `/register` DoS. On top of that, the standard redirect flow is a poor fit for agents: (5) an agent holding tokens is a high-value target, and (6) leaving the chat to log in breaks the agentic UX.

### 3.2 Two levels of registration (SPEC §17.2)

**Level 1 — Agent Provider Registration (CIMD).** Agent providers (OpenAI, Anthropic, Cursor, ...) register ONCE with the Pharos Registry. The registry hosts the provider's Client ID Metadata Document (CIMD) at a stable signed URL (`https://registry.pharos.dev/v1/agents/{provider_id}/cimd`). This establishes the **agent provider's verified identity** — used for agent auth to the registry and for vendor-side agent allow-listing. It is **NOT** the `client_id` used against each MCP server's authorization server.

**Level 2 — Vendor App Registration Inheritance.** MCP server vendors (Salesforce, Stripe, Acme) pre-register an OAuth app with their own IdP and **bundle that registration into `pharos.json`**: `client_id`, `auth_server_url`, `grant_types`, `scopes`, `consent_defaults`, `redirect_uri_pattern`, `endpoints` — **never `client_secret`** (stays server-side in the MCP server's own config). When an agent installs the MCP server, it **inherits** the vendor's app registration. No user creates a new app registration. No agent calls `/register`.

**Net effect:** agent providers register once for identity. Vendors register once per MCP server with their own IdP. Every agent install inherits the vendor's app registration. No per-instance client IDs. No `/register` calls. No `client_secret` in registry, agent, or SDK.

### 3.3 CIMD signing (H15 — critical)

CIMD documents are **signed by the agent provider**, NOT by the Pharos Registry. The registry serves them as **opaque signed blobs** at the stable CIMD URL — it is a content host, not an issuer. Vendors verify the CIMD signature against the provider's **pinned public key**, fetched from the provider's own `.well-known/agent-provider-keys` (provider-controlled endpoint), NOT from registry data.

**Why this matters:** a compromised or malicious registry cannot mint a fake agent-provider identity. Even if the registry serves a forged CIMD blob, the vendor's signature check against the provider-pinned key fails. (SPEC §10.7 threat: "Registry mints fake agent identity (H15)".)

### 3.4 The `OAuthFlowHandler` — coordinates, does not run a redirect flow (SPEC §17.4)

Under App Registration Inheritance, the handler **coordinates** rather than running a standard OAuth redirect. Five steps:

```
1. Agent discovers server → ServerCard.auth includes app_registration + ui config
2. SDK presents vendor consent_defaults to user (pre-checked; user may expand or reduce)
   → SDK records approved_oauth_scopes in ApprovalToken
3. Agent installs/enables MCP server (MCP initialize)
4. OAuthFlowHandler triggers MCP server's inline OAuth UI (MCP Apps sandboxed iframe)
   → MCP server handles OAuth SERVER-SIDE: inherited client_id, holds client_secret,
     exchanges auth code for token itself, stores token server-side
5. MCP server sends SIGNED CONFIRMATION (not the token):
   → JWT from vendor's IdP attesting { user_sub, scope, exp, client_id }
   → SDK MUST verify via app_registration.endpoints.jwks before trusting authorized=true
   → on failure: OAuthResult.authorized=false, error="invalid_jwt"; connection torn down
   → token stays with MCP server, which proxies all tool calls
```

**Sequencing (H5 — critical):** `pharos.connect(approval)` calls `OAuthFlowHandler.authorize()` **FIRST**, before MCP `initialize`. Only on `authorized=true` does `initialize` proceed.

**Crash/disconnect handling (H10):** if the iframe errors, MCP server disconnects, or `auth_timeout` fires:
1. Invalidate the `ApprovalToken` (cannot be reused).
2. Emit `OAuthResult.authorized=false, error="server_lost"` (or `"timeout"`).
3. Tear down the connection (close iframe, abort in-flight `initialize`).
4. Surface `RetryableOAuthFailure(server_id, reason)` — host re-prompts. SDK does NOT auto-retry (server-side state indeterminate).

### 3.5 The `OAuthResult` (SPEC §8.3)

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

**The SDK MUST verify the `confirmation_jwt`** (signature against `endpoints.jwks`, `exp` not in past, `client_id` matching inherited `app_registration.client_id`) before treating `authorized=true`. On verification failure → `authorized=false, error="invalid_jwt"`, connection torn down. (§17.4 step 5)

### 3.6 Flow selection (SPEC §17.4 table)

| Server `auth` config | Flow | `client_id` source | Token holder |
|---|---|---|---|
| `app_registration` present, `secret_handling == "server_side"` | **App Registration Inheritance** (preferred) | Vendor's pre-registered `app_registration.client_id`, inherited. No `/register`. | MCP server |
| `app_registration` absent, `dcr_support == true` | **DCR fallback** (legacy) | Dynamically registered via `dcr_endpoint` by MCP server. Ephemeral. Rate-limited. | MCP server (preferred) or agent (legacy) |
| `app_registration` absent, `dcr_support == false`, static client configured | **Static client credentials** (legacy) | Pre-registered from host credential store. | Agent |
| `auth.type == "api_key"` | **API key prompt** | Host `credential_provider` callback. | Agent |

### 3.7 Host capability negotiation (SPEC §17.5.1)

At startup, the SDK probes the host runtime:

| Host capability | OAuth flow | Token holder |
|---|---|---|
| `supports_mcp_apps: true` | **Inline flow** (MCP Apps sandboxed iframe in chat) | MCP server |
| `supports_mcp_apps: false`, `has_system_browser: true` | **Server-brokered redirect with PKCE** (below) | MCP server |
| neither | **`OAuthUnavailable`** — SDK refuses to start OAuth; host MAY fall back to api_key if supported | n/a |

**Server-brokered redirect with PKCE (§17.5.1):** preserves "agent never sees the token" by keeping the auth-code exchange server-side:
1. MCP server generates PKCE verifier + challenge, returns short-lived broker session + IdP `/oauth/authorize` URL (with inherited `client_id`, PKCE challenge, `redirect_uri` → MCP server's `/oauth/callback`, `state`).
2. SDK opens system browser to that URL. User authenticates at vendor's real IdP (browser address bar + cert = trust anchor).
3. IdP redirects to MCP server's `/oauth/callback` with auth code. MCP server exchanges code **server-side** (stored `client_secret` + PKCE verifier), stores token server-side.
4. SDK polls MCP server's OAuth-status endpoint OR receives callback. MCP server returns **signed confirmation** (§17.4 step 5) — never the token.
5. `OAuthResult.acquired_via == "server_brokered_redirect"`.

### 3.8 Inline OAuth phishing defenses (SPEC §17.5.3)

Because the inline OAuth UI renders inside host chrome, a malicious MCP server could spoof a vendor login page. Defenses (host chrome is the non-spoofable trust anchor):

- **Host-rendered chrome (not server-rendered).** Host renders a border/chrome around the iframe displaying: publisher's **verified domain** (`publisher.id`, e.g. `acme.com`) + verification badge; OAuth `authorization`/`token` endpoints; warning "Do not enter your password if the domain shown in the iframe's address bar does not match your IdP." Iframe content cannot draw over this chrome.
- **Iframe navigates to the IdP's real authorize URL.** Host surfaces iframe's current URL/registrable domain in chrome. If iframe attempts to navigate outside `app_registration.endpoints` → host blocks navigation, aborts flow with hard error.
- **Brand-similarity rejection at publish time** (§7.2).
- **`client_id` binding to verified publisher (H14).** At publish time, `auth.app_registration.client_id` is bound to the publisher's verified domain. An attacker cannot copy Acme's `client_id` to a different domain.
- **Threat model entry:** "Inline OAuth phishing (C5)" in §10.7.

### 3.9 Revocation (SPEC §10.5, H16)

`OAuthFlowHandler.revoke_access(server_id)` is **best-effort as a request**, but the MCP server MUST return a `revocation_proof` within 60s — a signed assertion it called `endpoints.revocation` with the token, OR a token-introspection (RFC 7662) response showing `active: false`. SDK verifies the proof against `endpoints.jwks`.

If no proof within 60s → server marked `revocation_unconfirmed`, SDK surfaces warning: "Acme Flights may still have access to your account. Revoke directly at `<vendor app-management URL>`." The `ServerCard` exposes `auth.app_registration.app_management_url` and `auth.app_registration.endpoints.revocation` so the user can revoke at the IdP directly when the MCP server is unresponsive or malicious.

---

## 4. Secret & token isolation (SPEC §10.5)

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

**Revocation** is a request to the MCP server (`OAuthFlowHandler.revoke_access`), which tears down its server-side session. (§10.5)

---

## 5. Publisher verification (SPEC §10.1)

Every `ServerCard` carries a `publisher` object + optional `trust` object. SDK verifies:

1. **Domain anchoring** — publisher's claimed domain (from `urn:pharos:<fqdn>:...`) must match the domain in the publisher's DID (`did:web:acme.com` → `acme.com`).
2. **Signature** — if `trust.signature` present, SDK verifies against publisher's public key (`https://<publisher>/.well-known/pharos-pubkey.json` or registry-cached key). Failed check → `verified=False`.
3. **Attestations** — `{type, uri, verified, verifier, verified_at}` objects. Displayed to user but NOT treated as proof unless `verified=true` with a `verifier` identity + `verified_at`. Unverified → "vendor-claimed."

**Two verification levels (M5):**
- `verification_method: "domain_control"` — proved domain control (DNS or `did:web`). Baseline; proves *who* is publishing, not *that they are trustworthy*. A malicious actor who buys `acme-travel-deals.com` gets `domain_control`.
- `verification_method: "identity"` — additionally proved organizational identity (LEI, KYC, business registry). Stronger; displays as a distinct badge. `verified=true` with only `domain_control` MUST NOT render as "trusted"; only `identity` carries that implication.

**`client_id` binding (H14):** at publish time, `auth.app_registration.client_id` is bound to the publisher's verified domain. Reusing another publisher's `client_id` is a publish-time rejection. Prevents an attacker from copying Acme's `client_id` to lend credibility to a phishing inline-OAuth form.

**Default policy:** `verify_signatures=True`, `allow_unverified=False`. Hosts wanting unverified servers (local dev) must explicitly set `allow_unverified=True`, which is logged.

---

## 6. Key rotation & pin freshness (SPEC §9.3, §10.9)

Publisher keys pinned by the SDK are re-validated on a TTL, not held forever:

- **TTL.** Re-fetch `.well-known/pharos-pubkey.json` at most every `key_pin_ttl_seconds` (default 86400 = 24h). Successful re-fetch updates the pin; failed re-fetch after TTL → **quarantine** (connection refused) until re-validation succeeds.
- **WHOIS change triggers re-verification.** A change in the domain's WHOIS registrant or nameservers triggers immediate re-verification of `domain_control`, independent of TTL. A domain transferred to a new registrant is treated as a **new publisher**; the old `identity` verification does NOT carry over.
- **Stale-card quarantine.** A `ServerCard` whose publisher key fails to refresh after TTL is marked `stale` in local cache — not surfaced in search, not connectable — until re-validated or the card is updated.
- **Quarantine UX.** Non-blocking `PublisherKeyStale(server_id)` event so the host can explain why a previously-available server is unavailable.

**Pinning failure is a hard error.** Pin mismatch (the pinned key doesn't match the leaf or SPKI chain at handshake) when `verify_signatures=True` → connection refused. The SDK does NOT fall back to "regular" validation for a pinned server, because a pin mismatch signals either legitimate key rotation or interception. (§9.3)

---

## 7. Malicious-server defense (SPEC §10.3)

- **Local blocklist** — SDK fetches + caches registry-provided blocklist of known-malicious server IDs. Connections to listed servers refused **before any network call**. Blocklist cache TTL **60 seconds** (H9 — was implicitly 300s). Subscribe to `/v1/events` `blocklist.updated` for push invalidation so a freshly-listed server is blocked in seconds.
- **Behavioral logging** — every `tools/call` logged locally (args redacted for sensitive params by default). Anomalously large argument payloads, repeated calls to the same tool, or calls to tools not in `tools/list` → warnings surfaced via `on_tool_use`.
- **Report pipeline** — `pharos.report_server(server_id, reason)` submits to registry + adds server to local blocklist for the session.

---

## 8. Consent logging (SPEC §10.4)

- Every approval, rejection, and revocation recorded in local consent store with timestamp, server ID, approved scopes, `agent_id`.
- Store is **append-only and signed** with a local key so tampering is detectable.
- Hosts MAY mirror consent events to registry (`POST /v1/approve`, §6.6) for cross-device audit, with `user_id_hash` only (**never** raw user IDs).

---

## 9. Query privacy (SPEC §10.8)

- **One-time disclosure.** SDK warns user once per install (not per search) that queries are sent to the registry and may be logged for ranking/abuse detection. Non-blocking after first acknowledgment; surfaced in `--privacy` output.
- **`privacy_mode` (opt-in).** Sends only structured filter fields (`query.capabilities`, `query.transport`, `query.availability`, `query.pricing_tier`), omits `query.text`. Lower recall, leaks no free-text intent. Results labeled "filtered-only" in UX.
- **`query.embedding` alternative.** SDK generates embedding **locally** (bundled model, §8.4) and sends only the vector. Registry SHOULD support `blinded_search` (nearest-neighbor against vector index without seeing raw text). **Stronger than `privacy_mode`** — preserves semantic recall while hiding text.
- **Registry logging constraints.** Registry MUST NOT log `query.text` at user level. Aggregate anonymized logging only, hashed + bucketed so individual queries aren't reconstructable. `query.text` MUST NOT appear in any per-user analytics, retention, or shared-with-third-parties pipeline.
- **`/v1/privacy` endpoint.** Registry returns machine-readable policy: which fields are logged, retention, whether `blinded_search` is supported, data residency. SDK fetches on first search, surfaces in privacy disclosure.

---

## 10. Egress & SSRF (SPEC §10.2, §10.5)

- **`egress_allowlist`** restricts which hosts the agent may connect to (defense against SSRF-style abuse of discovered endpoints). Phase 2.
- **SSRF prevention on CIMD/metadata fetches.** When fetching an agent's CIMD (§17.3) or any OAuth metadata, the fetcher MUST NOT issue requests to internal/loopback/link-local addresses. Fetched URLs validated against `egress_allowlist` before any HTTP call. Redirect chains followed with max depth 3, each hop re-validated.
- **CIMD metadata integrity.** Registry serves CIMD over HTTPS; SDK verifies TLS chain + pins registry public key when `verify_signatures=True`. CIMD cached locally with short TTL (default 1 hour); stale cache rejected if registry signals key rotation.

---

## 11. Sandboxing hooks (SPEC §10.2)

- **stdio servers** — SDK accepts `sandbox` config: `{"mode": "none" | "docker" | "firejail" | "nsjail" | "custom", "command": ...}`. When set, stdio command is wrapped before execution. Phase 2.
- **HTTP servers** — `egress_allowlist` restricts agent egress.
- **Tool-call scope enforcement** — Connection Manager rejects `tools/call` for tools outside `approved_capabilities` and for auth scopes outside `approved_scopes`.

---

## 12. Threat model summary (SPEC §10.7)

| Threat | Mitigation |
|--------|-----------|
| Malicious server listed in registry | Publisher signature verification + blocklist + user approval gate |
| Typosquatting publisher names | Domain-anchored URN IDs + `publisher.verified` badge in UX |
| Agent silently connects | Approval gate enforced in SDK; no bypass API |
| Tool calls outside consent | `approved_scopes` enforced in Connection Manager |
| Exfiltration via tool args | Local egress allowlist + tool-call logging + redaction |
| Compromised registry | Signatures verified against publisher's own published keys, not registry's |
| Stale/revoked servers | Registry `status` field; SDK re-checks before connect |
| OAuth scope creep | Scopes shown in approval prompt; vendor `consent_defaults` pre-checked but user may reduce; only approved scopes passed to MCP server |
| OAuth SSRF via CIMD/metadata fetch | Egress allowlist; redirect depth cap 3 |
| OAuth token theft | Tokens stay server-side in MCP server; agent/SDK never receive token |
| OAuth `client_secret` leak | Secret never in registry, agent, or SDK — only MCP server server-side config |
| Per-instance client ID proliferation | Vendor pre-registers one app; all installs inherit same `client_id` |
| Malicious agent triggers OAuth | Pharos CIMD verifies agent provider identity first; vendors MAY allow-list providers |
| DCR endpoint DoS / DB growth | DCR is fallback only; MCP server rate-limits DCR; ephemeral client IDs; App Registration Inheritance avoids `/register` entirely |
| Server-side exfiltration (C7) | Token held by MCP server; server can call vendor API for any in-scope purpose. Mitigations partial: explicit "any purpose within scopes" consent text, fine-grained scopes, registry static analysis of mirrored servers. **Acknowledged residual risk.** |
| Revocation not honored (H16) | `revocation_proof` required within 60s; on failure, `revocation_unconfirmed` + user warning with vendor app-management URL |
| Registry mints fake agent identity (H15) | CIMD signed by agent provider (not registry); registry serves opaque signed blobs; vendors verify provider signature against pinned provider key from `.well-known/agent-provider-keys` |
| Inline OAuth phishing (C5) | Host (not server) renders non-spoofable chrome: publisher verified domain, OAuth endpoints, password-mismatch warning. Iframe navigates to IdP's real authorize URL. Brand-similarity rejection at publish time. |
| Non-SDK agent bypasses consent (C1, out-of-SDK) | SDK enforcement is client-side contract, not wire-level primitive. Server-side enforcement of `ApprovalToken` is a future protocol extension. (§10.7.1) |

---

## 13. Security checklist for any change

Run through this before implementing anything touching approval, OAuth, storage, keys, or egress:

- [ ] Does the change preserve the "no `connect_without_approval`" invariant?
- [ ] Does the change keep `client_secret` out of every type, log, and error message?
- [ ] Under App Registration Inheritance, does the change keep tokens out of the agent/SDK?
- [ ] Does the change verify the `confirmation_jwt` via `endpoints.jwks` before trusting `authorized=true`?
- [ ] Does the change enforce `approved_scopes` on every `tools/call`?
- [ ] Does the change log `query.text` at user level? (Must NOT.)
- [ ] Does the change hold publisher keys beyond TTL? (Must re-fetch on TTL.)
- [ ] Does the change treat WHOIS registrant/nameserver change as triggering re-verification?
- [ ] Does the change introduce new egress? (Validate against `egress_allowlist`; cap redirect depth 3.)
- [ ] Is `headless_mode` still scoped (allow-list required, novel servers refused, loudly logged)?
- [ ] Does the change weaken any §10.7 threat mitigation?

**If any answer is wrong, stop and fix the design before implementing.**

---

*Next: `docs/components/OAUTH_BROKERING.md` for the full OAuth flow walkthrough; `docs/components/DISCOVERY_FLOW.md` for the approval flow details.*
