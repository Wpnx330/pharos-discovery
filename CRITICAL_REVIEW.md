# Critical Review — Pharos Discovery SPEC.md v0.2.0

**Reviewer:** Senior systems architect / security engineer
**Subject:** `/mnt/c/Users/chris/Documents/TRON/Projects/Pharos/pharos-discovery/SPEC.md` (1677 lines, v0.2.0 Draft)
**Date:** July 19, 2026
**Charter:** Ruthless critical review. Find every weakness, failure point, adoption barrier, risk factor, roadblock, and security flaw. Propose specific, actionable changes. This document does NOT modify the spec.

---

## Executive verdict

The spec is ambitious, well-written, and internally coherent at the narrative level. However, it has **systemic problems** that will block adoption or cause security failures if shipped as written:

1. **The consent gate is advisory, not enforceable.** The spec claims "no bypass API" (§7, §10.7) but any agent can simply not embed the SDK and connect directly. The enforcement model only constrains conformant agents — which is precisely the set of agents that don't need constraining.
2. **The MCP Apps dependency is load-bearing and brittle.** The entire §17 OAuth model collapses if the host doesn't support MCP Apps. The "fallback" (§17.5 last paragraph) is one sentence pointing at Phase 1's redirect flow — which §17.1 argues is fundamentally wrong. This is a contradiction, not a fallback.
3. **The two biggest adoption wins (Level 1 CIMD, vendor app-registration inheritance) require the two parties with the *least* incentive (OpenAI, Salesforce) to do work for Pharos's benefit.** The incentive structure is inverted.
4. **The "neutral middle" positioning is also the weakest strategic position.** ARD has Google + Microsoft + Hugging Face. The official MCP Registry has Anthropic. Pharos has none of the above.
5. **Privacy leakage of search queries to the registry is unaddressed.** Every `POST /v1/search` sends the user's natural-language intent ("I need to book a flight to Tokyo") to a third party. §10 is silent on this.
6. **The spec conflates three orthogonal problems** (discovery, consent, OAuth brokering) and treats them as one. The coupling makes partial adoption impossible — an agent that wants only discovery still pulls in the consent and OAuth machinery.

The document below enumerates 50+ specific issues across six categories, each with a severity and a concrete proposed change referencing spec sections.

---

## 1. Design Weaknesses

### 1.1 The 6-step discovery flow — gaps

**Issue:** The flow (§7.1) assumes a linear happy path: detect gap → search → results → approve → connect → use. It omits:
- **Step 0: capability-gap detection.** How does the agent know it lacks a capability? The spec hand-waves "agent detects a capability gap" as step 1 but never specifies the mechanism. This is the hardest part of agentic discovery and it's treated as a black box.
- **Step 2.5: query construction.** `pharos.search(text=...)` takes raw NL text, but who constructs it — the LLM? The agent runtime? There's no guidance on prompt engineering, query normalization, or how to avoid leaking PII into the query (see §6.3 privacy issue).
- **Step 3.5: re-ranking / selection.** §8.1 line 583 says `best = results[0]  # agent ranks by pharos_score + its own reasoning` — this is a comment, not a contract. There's no API for the agent to express its selection logic, no requirement that the agent justify its choice to the user, and no guardrail against the agent always picking rank-0.
- **Step 4.5: multi-server approval.** The flow shows one server. What if the user's request ("book a flight *and* file an expense report") needs two servers? The spec mentions `diversify_by_publisher` (§6.3) but the approval flow is per-server. There's no batch-approval or "approve this plan that uses 3 servers" UX.
- **Step 6.5: post-call audit.** §7.6 says the agent "MUST report" tool usage, but there's no enforcement. A non-conformant agent just... doesn't.
- **Failure between steps 3 and 4.** What if `results` is empty? The spec covers `RegistryUnavailable` (§13.5) but not "the registry returned zero results." Does the agent retry with a broader query? Surface "no servers found" to the user? Give up? This is unspecified and will produce divergent agent behavior.

**Severity:** High
**Proposed Change:** Add a §7.1.1 "Flow variants and edge cases" subsection covering: empty-result handling, multi-server plan approval (batch `ApprovalToken` or a `PlanApproval` type), capability-gap detection as an explicit host-supplied callback (not a black box), and a mandatory `selection_rationale` field on `ApprovalRequest` so the agent must justify its pick to the user.

---

### 1.2 ServerCard data model — what's missing

**Issue:** The `ServerCard` (§8.3, Appendix A) is missing several fields that matter for real adoption:

- **`updated_at` / `published_at` / `indexed_at`** — mentioned in §7.2 "recommended" but absent from the schema in Appendix A. You cannot do cache invalidation (§13.5) without timestamps.
- **`status`** — §10.7 threat table references a `status` field (`active`/`deprecated`/`deleted`) but it is **not in the Appendix A schema**. This is a direct contradiction.
- **`deprecated` / `successor_id`** — no deprecation migration path. When `acme-flights` v2 is deprecated in favor of v3, how does the agent learn the successor?
- **`homepage_url` / `privacy_policy_url` / `terms_url`** — §7.2 recommends a `documentationUrl` but there's no privacy-policy URL. For a consent flow (§7.2) that asks users to approve OAuth scopes, the absence of a link to the vendor's privacy policy is a compliance gap (GDPR, CCPA).
- **`data_residency` / `regions`** — for enterprise adoption (§10.6), where the server actually runs matters. EU customers cannot approve a US-region server without a data-residency signal.
- **`rate_limits`** — the server's own rate limits (not the registry's, §13.5) are unspecified. The agent has no way to know "this server allows 100 calls/min" before connecting.
- **`health_endpoint` / `uptime_sla`** — no liveness signal. The registry indexes the server; there's no documented health-check URL.
- **`sdk_compatibility`** — which MCP protocol versions the server speaks. Appendix C shows `protocolVersion: "2025-03-26"` but the ServerCard doesn't surface the server's supported versions.
- **`auth.scopes` vs `auth.app_registration.scopes`** — Appendix A has *both* a top-level `auth.scopes` and a nested `auth.app_registration.scopes`. The relationship is unclear; §7.2 references "`auth.scopes`" but the OAuth path uses `app_registration.scopes`. This will cause implementer confusion.
- **`redirect_uri_pattern` is a string, not a regex type.** Is it a glob? A regex? An exact match? §17.2 says "scoped to their own MCP server" but the matching semantics are undefined.
- **No `contact` in the publisher object in Appendix A** (it appears in §13.2's publish payload but not the canonical schema). Inconsistent.

**Severity:** High
**Proposed Change:** Add to Appendix A: `updated_at`, `published_at`, `status` (enum: `active`/`deprecated`/`deleted`), `successor_id` (nullable), `privacy_policy_url`, `terms_url`, `data_residency` (array of region codes), `rate_limits` (object), `health_endpoint`, `protocol_versions` (array). Resolve the `auth.scopes` / `app_registration.scopes` duplication with explicit precedence rules. Specify `redirect_uri_pattern` matching semantics (recommend: exact-match or RFC 3986 URI-template, *not* regex).

---

### 1.3 Consent as protocol primitive — enforceability

**Issue:** §4.2 and §7 call consent "non-negotiable" and claim "the SDK exposes no `connect_without_approval` escape hatch." This is **only true inside the SDK**. The threat model (§10.7) lists "Agent silently connects → Approval gate is enforced in SDK; no bypass API" — but a malicious or modified agent simply **doesn't use the SDK**. It takes the `endpoint` from the search result and opens a raw MCP connection. The consent gate is therefore **advisory**, not enforceable. The spec's repeated use of "enforced" (§7, §10.7, §12 differentiator #2) overstates the guarantee.

This is the single most important conceptual weakness: the document's framing implies a security property the architecture cannot deliver. Consent is a **UX convention** and a **conformant-agent behavior**, not a protocol-level invariant. The moment a major agent vendor decides consent is friction (see §5), they ship a non-conformant client and the "guarantee" evaporates.

**Severity:** Critical
**Proposed Change:**
- Reword §4.2, §7, §10.7, §12 to replace "enforced" with "enforced for conformant SDK-using agents" throughout. Stop claiming consent is a protocol primitive; call it what it is: a client-side contract.
- Add a §10.7.1 "Out-of-SDK threats" subsection explicitly acknowledging that a non-SDK agent can bypass consent, and define the mitigation: **server-side enforcement**. The strongest mitigation is for *MCP servers* (or the Pharos Registry, for `mirrored` servers) to require a valid `ApprovalToken` to be presented at `initialize` time — i.e., make the token a server-checked credential, not just a client-side gate. This requires a protocol extension to MCP `initialize` (e.g., a `pharos_approval` capability in the `initialize` params). Without this, consent is cosmetic.
- If server-side enforcement is out of scope (likely — it requires MCP-server cooperation), say so explicitly and downgrade the claim.

---

### 1.4 Registry-agnostic adapters — will they actually work?

**Issue:** §11 asserts registry-agnosticism via adapters, but the adapters have serious fidelity problems:

- **MCP Registry adapter (§11.3)** does "client-side semantic re-ranking (using a small local embedding model or the host's LLM)." This is a *huge* hidden dependency. §4.8 says "minimal dependencies." A local embedding model is not minimal. Using "the host's LLM" for re-ranking couples discovery to LLM availability and cost — and means discovery quality varies by agent. The spec doesn't specify the model, its size, or a fallback when neither a local model nor LLM is available.
- **ARD adapter (§11.4)** converts `urn:air:` to `urn:pharos:` and back. But ARD's `score` (0–100) → `pharos_score` (0.0–1.0) is a linear rescale that is **semantically wrong** if the two registries use different ranking distributions. A 50/100 in ARD is not necessarily 0.5 in Pharos. The spec calls both "informational" but users will compare scores across results from mixed registries (§6.5 federation `auto` mode merges them). This produces misleading rankings.
- **A2A adapter (§11.6)** maps A2A `skill` → canonical `capability`, but A2A agents are *not* MCP servers. §11.6 says "the approval flow noting that the connection is to an A2A agent (JSON-RPC 2.0 over HTTP) rather than an MCP server" — but the entire Connection Manager (§9) is built around the MCP lifecycle (`initialize`, `tools/list`, `tools/call`). A2A has `tasks/send`, not `tools/call`. The adapter claims to surface A2A agents as discoverable, but **the connection layer cannot actually talk to them**. This is a promise the spec makes and cannot keep without a second connection implementation.
- **AGNTCY adapter (§11.5)** is "planned for Phase 3" with one sentence. No fidelity analysis.
- **Walled-garden bridges (§11.7)** are "read-only and best-effort… scrape or use vendor-provided listing APIs where terms permit." Scraping Claude Connectors or Copilot's store will violate their ToS. §11.7 says "If a vendor's ToS forbids programmatic listing, that bridge is not shipped" — which means, in practice, *none* of the major bridges will ship, because all major vendor marketplaces forbid scraping in their ToS. This makes the "federate across walled gardens" claim (§3.2) largely aspirational.
- **Adapter interface (§11.1)** has `publish()` and `report()` methods, but the MCP Registry and ARD adapters are read-only. Calling `publish()` on a read-only adapter is unspecified — does it raise? No-op? The interface doesn't distinguish RO from RW adapters.

**Severity:** High
**Proposed Change:**
- §11.3: Specify the embedding model (recommend `all-MiniLM-L6-v2` or similar, ~22MB, onnx runtime) or make client-side re-ranking an explicit *optional* plugin with a graceful substring-only fallback. Remove "or the host's LLM" — that's an anti-pattern for a library claiming minimal deps.
- §11.4: Make cross-registry score comparison illegal by spec — when federation merges results from heterogeneous registries, drop `pharos_score` and use only rank order, or normalize per-source. Add a `source_score` field preserving the original.
- §11.6: Either (a) remove A2A from the connection path and make it discovery-only (the SDK returns an A2A `AgentCard` but the host, not the SDK, connects), or (b) add a full §9.6 "A2A transport" specifying `tasks/send` lifecycle. As written it's a dangling promise.
- §11.7: Delete the scraping language or replace with "bridges require a documented vendor API; scraping is out of scope." Be honest that walled-garden coverage depends on vendor cooperation that may not come.
- §11.1: Add `capabilities: set[AdapterCapability]` (e.g. `{SEARCH, GET, PUBLISH, REPORT}`) to the interface so callers know what's supported.

---

### 1.5 `urn:pharos:` identifiers — collision and resolution risks

**Issue:** Appendix B defines `urn:pharos:<publisher-domain>:<namespace>:<server-name>`. Problems:

- **Domain ownership changes.** `acme.com` can be sold, expired-and-reregistered, or transferred. The URN is "stable" but the trust anchor (the domain) is not. A lapsed domain picked up by an attacker inherits all prior `urn:pharos:acme.com:...` IDs. The spec has no key rotation / domain-transfer revocation story. (§14.2 lists "Revocation protocol — push-based" as a *future* feature — meaning until Phase 4, there is no revocation at all.)
- **No collision resolution for same-domain different publishers.** If two teams inside Acme both publish `urn:pharos:acme.com:travel:bookings`, who wins? The spec doesn't say.
- **`<publisher-domain>` extraction is ambiguous.** `urn:pharos:acme.com:travel:flight-booking` — is the publisher `acme.com` or `acme.com:travel`? The grammar is ambiguous because `<namespace>` is "optional hierarchical segments." You cannot reliably parse publisher vs. namespace without a registry lookup, which defeats the "decentralized trust" claim.
- **Cross-registry ID collision.** §11.4 converts `urn:air:acme.com:travel:flight-booking` to `urn:pharos:acme.com:travel:flight-booking` and preserves the original in `source_urn`. But if Acme publishes the *same* server to both the Pharos Registry and an ARD registry, federation `auto` mode (§6.5) will return two results with the *same* `urn:pharos:` ID from two registries. Dedup is unspecified.
- **No URN resolution protocol.** Given a `urn:pharos:...`, how does the SDK find the registry that owns it? §6.4 `GET /v1/servers/{id}` assumes you already know the registry base URL. There's no `URN → registry URL` resolution step. ARD has the same gap; "mirrors ARD" is not a defense.

**Severity:** High
**Proposed Change:**
- Appendix B: Add a `publisher_domain` as a *fixed-length single segment* (the FQDN) and require `namespace` to be non-empty or use a sentinel (`_`). Make the grammar unambiguous: `urn:pharos:<fqdn>:<namespace>/<name>` with `/` as the namespace/name separator.
- Add a §6.4.1 "URN resolution" subsection: the SDK resolves `urn:pharos:` IDs via a configurable registry list (try each in order) or a future NAPTR-style DNS record. Acknowledge that without resolution, the ID is only meaningful within a known registry.
- Add a §10.8 "Key rotation and domain transfer" subsection: publisher keys must be re-validated on a TTL (default 24h); domain WHOIS change triggers re-verification; stale cards are quarantined, not served.
- §6.5: Specify that federation `auto` mode MUST deduplicate by canonical ID and merge metadata, not concatenate.

---

### 1.6 OAuthFlowHandler coordination model — end-to-end correctness

**Issue:** §17.4 lays out a 5-step flow. Several steps are underspecified or internally inconsistent:

- **Step 3 ordering.** The flow says "Agent installs / enables the MCP server (MCP initialize)" *before* "OAuthFlowHandler triggers the MCP server's inline OAuth UI." But §9.4 step 3 says the SDK delegates to `OAuthFlowHandler` for `auth.type == "oauth"`. So does `pharos.connect()` call `initialize` first and then OAuth, or OAuth first and then `initialize`? The MCP `initialize` handshake (§9.1) doesn't require auth in-band — but some MCP servers may refuse `tools/call` before OAuth. The sequencing is ambiguous.
- **What does "the MCP server returns an MCP Apps HTML segment" mean before auth?** §17.4 step 4 says the SDK "calls the MCP server requesting the `ui://oauth/login` resource." But this is a *tool call* or a *resource read*? MCP Apps returns HTML from *tools*, per the MCP Apps spec. So is `ui://oauth/login` a tool? A resource? The `resource_uri` naming suggests a resource, but MCP Apps is tool-based. This is a protocol confusion.
- **The confirmation in step 5 (`{ authorized: true, scope: [...] }`) has no integrity protection.** A malicious MCP server can return `authorized: true` without actually completing an OAuth flow, then proxy tool calls to steal user data. The agent has no way to verify the server really authenticated the user. There's no signed assertion, no IdP-issued token reference the agent can check.
- **`OAuthFlowHandler.refresh()` (§8.3) is defined but §17.4 says refresh is "performed server-side by the MCP server."** So what does the SDK's `refresh()` do? It can't refresh a token it doesn't hold. The method is vestigial.
- **`OAuthFlowHandler.revoke_access()` is "a request to the MCP server."** What if the MCP server ignores it? The SDK has no leverage — it doesn't hold the token. Revocation is *best-effort*. §10.5 calls this "tears down its server-side session" but that's a description of cooperation, not a guarantee. A malicious or buggy server retains the token.
- **No timeout on the inline OAuth UI.** §17.5 doesn't specify how long the SDK waits for `auth_completed`. If the MCP server's iframe hangs, the agent is stuck.
- **No cancellation protocol.** If the user closes the iframe mid-login, how does the SDK tell the MCP server to abort? `postMessage` events are listed (`auth_started`, `auth_completed`, `auth_error`) but no `auth_cancelled`.

**Severity:** Critical
**Proposed Change:**
- §9.4 / §17.4: Add an explicit sequence diagram. Recommended order: (1) `pharos.connect()` calls `OAuthFlowHandler.authorize()` *first*; (2) handler triggers inline OAuth via MCP Apps; (3) on confirmation, SDK performs MCP `initialize`; (4) operational phase. Document why this order (auth before initialize lets the server refuse uninitialized callers).
- §17.4 step 4: Clarify that `ui://oauth/login` is delivered via an MCP Apps *tool call* (e.g. `tools/call name="_pharos_oauth_login"`) returning an HTML `Resource` per the MCP Apps spec, not a `resources/read`. Align the `resource_uri` naming.
- §17.4 step 5: Require the MCP server's confirmation to include a **signed assertion** (JWT from the vendor's IdP, verifiable via `app_registration.endpoints.jwks`) attesting `{user_sub, scope, exp}`. The SDK MUST verify this JWT before trusting `authorized: true`. This closes the "malicious server lies about auth" hole.
- §8.3: Remove `OAuthFlowHandler.refresh()` or redefine it as `refresh_status()` (polls the server's `OAuthStatus`, doesn't refresh a token).
- §17.4: Define revocation as *best-effort* and add a server-side requirement: the MCP server MUST call `auth.app_registration.endpoints.revocation` within 60s of `revoke_access()`. The SDK MAY verify revocation by polling the IdP's token-introspection endpoint if available. Add a `revocation_confirmed` boolean to the revoke response.
- §17.5: Add `auth_timeout` (default 120s) and `auth_cancelled` postMessage event. Specify that on timeout/cancel, the SDK tears down the iframe and emits `OAuthResult.authorized=false, error="timeout"`.

---

### 1.7 Python + TypeScript SDK split — divergence risk

**Issue:** §8 promises "identical surfaces" in Python and TS. In practice:

- **The code samples already diverge.** §8.1 Python uses `pharos.search(text=..., filter={...})`; §8.2 TS uses `pharos.search({ text, filter: { transport, publisherVerified, minRating } })`. The TS uses camelCase, Python snake_case — fine, but the *parameter structure* differs (Python takes `text` and `filter` as separate kwargs; TS takes a single object). This is not "identical surfaces"; it's two idiomatic surfaces. The claim is misleading and will create a conformance-testing nightmare.
- **No conformance test suite is in scope until Phase 4** (§15). So for Phases 1–3, the two SDKs can drift freely. By Phase 4, divergence will be baked in.
- **Async model mismatch.** Python uses `anyio` (§8.4); TS uses native `async/await` + what event loop? Browser? Node? The spec doesn't say how the same approval callback contract works across both.
- **Type sharing.** There's no mention of a shared schema (JSON Schema, protobuf, TypeSpec) as the single source of truth. Appendix A is JSON Schema for `ServerCard` only, not for the full API. Without a contract-first IDL, the two SDKs will diverge.
- **The TypeScript SDK is Phase 2** (§14.1, §15). So the entire TS ecosystem — which dominates the agent-tooling world (LangChain.js, Vercel AI SDK, Mastra, etc.) — gets nothing for 6+ weeks. By the time it ships, JS-first devs will have moved on or built alternatives.

**Severity:** Medium
**Proposed Change:**
- Add a §8.6 "Contract-first design" subsection: define the API via a single IDL (recommend TypeSpec or JSON Schema with codegen for both languages). Make the IDL the source of truth; SDKs are generated.
- Move the conformance test suite to Phase 1 (not Phase 4). It's far cheaper to build it alongside the first SDK than to retrofit it across two diverged implementations.
- §8.2: Align the TS surface with Python — both should take either separate args or a single options object, consistently. Pick one.
- Reconsider the Phase 1 Python-only decision. The JS/TS ecosystem is where agent adoption is happening fastest (Cursor, Vercel, Cloudflare, etc.). Shipping Python-first actively alienates the highest-velocity segment.

---

## 2. Failure Points

### 2.1 Registry unreachable during discovery

**Issue:** §13.5 covers `429 RATE_LIMITED` (backoff + cache fallback + `RegistryUnavailable`). But:
- The cache fallback only works if a prior search was cached. **First-run users with no cache get a hard failure.** The spec doesn't address the cold-start case.
- There's no multi-registry failover at the client level. §6.5 federation is *registry-initiated* (the registry federates upstream); the SDK doesn't try a second registry if the first is down. The `PharosClient` takes one `registry_url` (§8.5). If `registry.pharos.dev` is down, discovery is dead.
- `REGISTRY_UNAVAILABLE` (503, §6.10) handling is "SDK should retry or fail over" — but fail over to *what*? There's no secondary registry configured.

**Severity:** High
**Proposed Change:**
- §8.5: Change `registry_url` to `registry_urls: list[str]` with failover semantics (try in order, health-check, cache the live one).
- §13.5: Add a cold-start fallback — if no cache and registry down, the SDK SHOULD surface a `DiscoveryDegraded` event and allow the host to fall back to a configured static server list (analogous to `/etc/hosts` for MCP). Document that this bypasses search but preserves the approval gate.
- §6.10: Define failover explicitly: on 503/504/timeout, mark the registry unhealthy for 60s and try the next.

---

### 2.2 MCP server unreachable during connection

**Issue:** §9 covers the happy-path lifecycle. It does not cover:
- **Connection timeout.** No default timeout on `initialize`. An agent could hang indefinitely on a dead server.
- **Mid-handshake failure.** If `initialize` succeeds but `notifications/initialized` gets no response, what state is the client in?
- **Server crash after connect.** §9.5 says "connections are torn down on `client.close()`" — but what tears them down when the *server* disappears? There's no keepalive, no heartbeat, no reconnect policy (and §9.5 explicitly says "never reconnects automatically"). So a long-lived session using a `persistent` approval (§7.3) silently has a dead client.
- **DNS / TLS failures.** §9.3 says "TLS 1.2+" but doesn't specify cert validation behavior, pinning failures, or what happens when the endpoint's cert doesn't match the publisher's pinned key.

**Severity:** High
**Proposed Change:**
- §9.1: Add `initialize_timeout` (default 10s), `tool_call_timeout` (default 30s), and a heartbeat/keepalive interval (default 30s for HTTP/SSE; N/A for stdio).
- §9.5: Add "liveness monitoring" — the SDK MUST detect a dead connection within `health_check_interval` and emit a `ConnectionLost` event. For `persistent` approvals, the SDK MUST require a fresh approval to reconnect (per §9.5's no-auto-reconnect rule), but it should surface this to the host rather than silently holding a dead client.
- §9.3: Specify cert validation: full chain validation required; pinning failure is a hard error when `verify_signatures=True`; document the pinned-key rotation story.

---

### 2.3 MCP Apps not supported by the host — CRITICAL

**Issue:** This is the load-bearing dependency of the entire §17 model. §17.5's last paragraph: "Hosts that do not yet support MCP Apps fall back to the legacy redirect flow (Phase 1 behavior, §9.4) or refuse OAuth-protected servers."

This is broken in three ways:
1. **§17.1 argues the redirect flow is *fundamentally wrong* for agents** (reasons 5 and 6). Falling back to it concedes the entire thesis. The spec can't simultaneously claim "redirects are wrong" and "redirects are our fallback."
2. **"Refuse OAuth-protected servers"** means a huge fraction of real MCP servers (anything with auth) is simply unavailable on non-MCP-Apps hosts. That's not a fallback; it's a feature loss.
3. **MCP Apps support is not universal.** §17.5 lists Claude, ChatGPT, VS Code, Goose. Notably absent: **Cursor** (the largest IDE agent), **Copilot** (the largest enterprise agent), most custom CLI agents, and every headless/voice agent (§7.5 pattern 3). For these, §17's model is dead on arrival.

The spec's central differentiator (§12, differentiator #5) is "OAuth via App Registration Inheritance… the user never leaves the chat." On a host without MCP Apps, the user *does* leave the chat, or the server *doesn't work*. The differentiator evaporates for a large share of the market.

**Severity:** Critical
**Proposed Change:**
- Promote this from a one-sentence aside to a first-class §17.5.1 "Host capability negotiation" section. The SDK MUST probe MCP Apps support at agent startup (via host capability flags) and:
  - If supported: use the inline flow (§17.5).
  - If not supported AND the host supports system browser: use a **redirect flow with PKCE, brokered server-side by the MCP server** (the agent opens a browser to the MCP server's `/oauth/authorize` URL with the agent's CIMD as `client_id`; the server completes the flow and the agent polls or receives a callback). This is *not* the legacy agent-handles-token redirect — it's server-brokered, preserving the "agent never sees the token" property. Document this as a distinct mode, not a "legacy fallback."
  - If neither: the SDK surfaces `OAuthUnavailable` and the host may fall back to API-key auth if the server supports it, or refuse.
- §12: Soften differentiator #5 to "inline OAuth on MCP-Apps hosts; server-brokered redirect elsewhere — agent never handles tokens in either mode."
- §17.1: Acknowledge that reason 6 ("leaving the chat breaks UX") is *partially* solved — inline where possible, redirect where necessary. Don't claim redirects are eliminated.

---

### 2.4 Consent denied — recovery path

**Issue:** §7.3 says approval is "revocable" and §8.1 shows `if not approval.approved: return`. But:
- **No "why" captured.** The `ApprovalResponse.user_note` field (§8.3) is optional. If the user denies, the agent has no structured reason. It can't learn ("this publisher is untrusted" vs. "wrong scopes" vs. "wrong server").
- **No retry-with-different-server flow.** §7.1 step 5 says "User approves (or rejects / picks a different server)" but the API has no "pick a different server" path. `request_approval` takes one `server`. To pick another, the agent re-runs the whole search. There's no "show me the next-best result" API.
- **No "approve with reduced scopes" round-trip.** The user can narrow scopes in the approval prompt (§7.2), but if they narrow so much that the agent can't accomplish the task, what happens? The agent gets a token with insufficient scopes, calls fail with `SCOPE_NOT_APPROVED`, and then…? There's no protocol for "the agent reports it needs more scopes and re-prompts."

**Severity:** Medium
**Proposed Change:**
- §8.3: Make `ApprovalResponse.deny_reason` an enum (`untrusted_publisher`, `excessive_scopes`, `wrong_server`, `cost`, `other`) when `approved=false`.
- Add a `pharos.request_approval_next(current_server_id, ...)` convenience that returns the next-ranked result, avoiding full re-search.
- §7: Add a "scope re-negotiation" flow — if a tool call fails with `SCOPE_NOT_APPROVED`, the SDK MAY surface a re-approval prompt requesting the specific missing scope, with the agent explaining why it's needed. Rate-limit re-prompts (max 1 per server per session) to avoid nagging.

---

### 2.5 MCP server crashes mid-OAuth-flow

**Issue:** §17.4 step 4: the user is entering credentials in the inline iframe; the MCP server crashes. The iframe goes blank or errors. What does the SDK do?
- No detection of server death during OAuth.
- No cleanup of partial server-side state (the IdP may have a pending auth code).
- The `ApprovalToken` is already minted (§7.4) but now points at a dead server. Is it revoked? Reusable?

**Severity:** High
**Proposed Change:**
- §17.4: Add step 4b "Failure handling" — on iframe error, server disconnect, or timeout during OAuth, the SDK MUST: (a) invalidate the `ApprovalToken`, (b) emit `OAuthResult.authorized=false, error="server_lost"`, (c) tear down any partial MCP connection, (d) surface a `RetryableOAuthFailure` event so the host can re-search or retry. Require the MCP server to document its IdP-side cleanup (auth-code TTL) in `app_registration`.

---

### 2.6 Stale ServerCard cache

**Issue:** §8.5 `cache_ttl_seconds=300` (5 min). §13.5 says the SDK "SHOULD cache `ServerCard` responses locally." But:
- **No cache invalidation signal.** There's no `ETag`/`If-None-Match` support documented in §6.4. No webhook/SSE for card updates. A server can be revoked, deprecated, or turn malicious, and the SDK will serve the cached "trusted" card for 5 minutes.
- **No `Last-Modified` / `updated_at` on the card** (see §1.2) — so the SDK can't even tell if its cache is stale.
- **Pinned public keys go stale.** §9.3 "pins the publisher's public key when `trust.signature` is present." If the publisher rotates keys (compromise response), the SDK's pinned key now blocks the legitimate server. No rotation story.
- **Blocklist cache.** §10.3 "fetches and caches a registry-provided blocklist." How often? A malicious server listed 4 minutes ago is still connectable.

**Severity:** High
**Proposed Change:**
- §6.4: Add `ETag` and `Last-Modified` headers; SDK uses conditional GET.
- Add §6.7 "Push invalidation" (Phase 1 minimum): a Server-Sent Events endpoint `/v1/events` the SDK subscribes to for card-invalidation and blocklist-update events. Even a poll-based `/v1/blocklist?since=<cursor>` would help.
- §9.3: Key pinning MUST honor a TTL and a pin-update mechanism (fetch the publisher's `.well-known/pharos-pubkey.json` at most every 24h; allow rotation).
- §10.3: Blocklist cache TTL default 60s, not 300s. Document this.

---

### 2.7 Network timeout handling in the discovery flow

**Issue:** §8.5 has `request_timeout_seconds=10` but:
- Is that per-request or total-for-search? Federated search (§6.5 `auto`) may fan out and take longer.
- No timeout on the approval UX. If the host's `render` callback never returns (user walked away), the agent hangs forever.
- No timeout on `pharos.connect()` end-to-end (initialize + OAuth + tools/list).
- §6.10 lists `UPSTREAM_TIMEOUT` (504) but the SDK behavior on 504 from a federated upstream is unspecified — does the registry return partial results, or fail the whole query?

**Severity:** Medium
**Proposed Change:**
- §8.5: Split into `search_timeout`, `get_timeout`, `connect_timeout`, `oauth_timeout`, `approval_timeout` with documented defaults.
- §6.5: Specify that federated `auto` mode returns partial results on upstream timeout, with a `degraded_sources` field listing timed-out registries.
- §7: Add `approval_timeout` (default 300s) — on expiry, the pending `request_approval` resolves to `approved=false, reason="timeout"`.

---

## 3. Adoption Barriers

### 3.1 Why would an agent provider integrate Pharos instead of building their own?

**Issue:** This is the existential question and the spec doesn't answer it convincingly. The actual incentives:
- **OpenAI / Anthropic / Google** already have or are building walled-garden discovery (Claude Connectors, Copilot connectors, ARD). They *want* the walled garden — it's a moat. Adopting Pharos erodes their moat. There is no business reason for them to integrate.
- **The spec asks agent providers to do Level 1 CIMD registration** (§17.3) — register once with the Pharos Registry, host keys. This is *work for Pharos's benefit*, not theirs. What do they get? Access to servers they could also get by scraping the official MCP Registry.
- **The consent gate is friction** the agent providers don't want. OpenAI's "Operator" and Anthropic's agents aim for seamless operation. A mandatory approval prompt before every new connection is a UX tax these vendors will resist.
- **There is no network effect yet.** Pharos has no users → no vendors publish → no agents integrate → no users. The spec doesn't address bootstrapping.

The spec's only leverage is "neutrality," but neutrality is a *user* value, not a *vendor* value. Vendor adoption requires vendor incentives.

**Severity:** Critical (existential)
**Proposed Change:**
- Add a §2.3 "Adoption strategy" section that honestly confronts this. Concrete levers to specify:
  - **Target the long tail first.** Don't pitch OpenAI; pitch the 500 CLI/IDE/custom agents (Hermes, Goose, Cline, Aider, Continue, etc.) that can't afford to build their own discovery. The SDK is a 10x win for *them*. Get 50 small agents before approaching one big one.
  - **Make Level 1 CIMD registration free and beneficial to the provider.** The CIMD gives the provider a verified identity that *vendors* allow-list — meaning registered providers get *better access* to vendor MCP servers than unregistered ones. Frame it as "Pharos CIMD = your agent's verified passport." Make registration one HTTP call.
  - **Offer the SDK as a drop-in for an existing pain.** Cursor currently uses `~/.cursor/mcp.json`. Ship a Pharos-backed "MCP marketplace" that Cursor can embed in 100 lines. Solve *their* config-file problem first; consent and OAuth come along for the ride.
  - **Bootstrap the registry with a bulk import.** Phase 0 should include a one-shot import of the entire official MCP Registry + npm packages tagged `mcp-server` into the Pharos Registry, so day-one there are thousands of servers. The spec doesn't mention this.
- §17.3: Reduce Level 1 friction to absolute minimum — no software statement required for public providers; only require SEP-1032 signing for enterprise-tier providers.

---

### 3.2 Why would a user trust the consent flow?

**Issue:** §7.2 lists what the approval card must show. But:
- **Users can't evaluate OAuth scopes.** "bookings:write" vs "payments:write" — most users don't read these. The consent UI is security theater for non-experts. The spec relies on "plain language" descriptions (§7.2) but those are vendor-written and unverified.
- **No reputation signal beyond `rating.score`.** A new malicious server has no rating (not a bad rating). `rating.count=0` looks the same as "unreviewed" vs "reviewed and mediocre."
- **`publisher.verified` verifies domain control, not intent.** A malicious actor who buys `acme-travel-deals.com` gets `verified=true` by DNS. The badge misleads.
- **Consent fatigue.** If every connection needs approval (§4.2), users click "yes" reflexively after the third prompt. The spec's `persistent` duration (§7.3) is a second confirmation — *more* friction, not less. There's no "trust this publisher for these scopes permanently" model.

**Severity:** High
**Proposed Change:**
- §7.2: Add a `risk_tier` computed by the SDK (not the vendor) from `scopes + publisher.verified + rating.count + availability`. Display as Low/Medium/High. Require explicit "I understand the risk" click for High tier.
- §10.1: Distinguish `verified=domain_control` from `verified=identity` (e.g., business verification via LEI, KYC). Display them as different badges. Stop implying `verified=true` means safe.
- §7.3: Add a "publisher trust" model — after N successful tool calls with a publisher, the SDK MAY offer "trust all servers from `<publisher>` for `<scope-set>`" to reduce fatigue. Gate behind explicit user action and revocable.
- §7.2: For `rating.count == 0`, display "New/unreviewed — no track record yet" explicitly, distinct from a numeric score.

---

### 3.3 The MCP Apps dependency — what if a major host doesn't support it?

**Issue:** Already covered in §2.3 above. The adoption angle: if Cursor (the dominant coding agent) doesn't support MCP Apps, then every OAuth-protected MCP server discovered via Pharos on Cursor either redirects (contradicting §17.1) or fails. Cursor users — the most likely early adopters of an MCP discovery tool — get a degraded experience. This is a go-to-market blocker, not just a failure point.

**Severity:** Critical
**Proposed Change:** Same as §2.3, plus: explicitly name the non-MCP-Apps hosts in §17.5 and document the tested path for each. If Cursor lacks MCP Apps, ship the server-brokered redirect flow as a first-class supported mode for Cursor on day one.

---

### 3.4 Python SDK first — does it alienate the TS/JS ecosystem?

**Issue:** §14.1 ships Python in Phase 1, TS in Phase 2. The JS/TS ecosystem is where agent *tooling* innovation is fastest: LangChain (JS + Py), Vercel AI SDK (JS), Mastra (JS), Cloudflare Agents (JS), Cursor (TS), Cline (TS), Continue (TS), Aider (Py but moving multi). Shipping Python-first tells the JS world "wait 6 weeks." In a fast-moving ecosystem, 6 weeks is a generation. Many JS-first devs will evaluate Pharos once, see no SDK, and never return.

**Severity:** High
**Proposed Change:**
- Either: ship both SDKs in Phase 1 (the TS SDK is mostly mechanical given the spec; the cost is real but the adoption cost of not shipping it is higher).
- Or: ship a **thin JS wrapper over a WASM build of the Python SDK** as a stopgap, with the native TS SDK in Phase 2. Document this honestly.
- §15: Reorder so TS lands no later than week 4, not week 7.

---

### 3.5 Coexistence with Claude Connectors and MS Copilot connectors

**Issue:** §11.7 says walled-garden bridges are "best-effort" and depend on ToS-permitted scraping (which won't exist). So in practice, Pharos *does not* coexist with Claude Connectors or Copilot connectors — it runs *alongside* them, with no shared state. An agent using Pharos discovers server X; the same server is also a Claude Connector. Are they the same connection? The same consent? The spec doesn't say. Users will end up with duplicated connections, duplicated consents, and duplicated OAuth tokens (one via Pharos, one via Claude's native flow). This is the exact fragmentation §3 complains about, recreated inside the user's agent.

**Severity:** Medium
**Proposed Change:**
- Add §11.8 "Coexistence with vendor-native connectors." Specify: (a) the SDK's `consent_store` is the source of truth for Pharos-managed connections; (b) the SDK SHOULD expose a `pharos.detect_native_connector(server_id)` API that asks the host whether a native connector already exists for a server, to avoid double-connection; (c) if a native connector exists, the SDK defers to it and does not establish a parallel connection. Acknowledge that without vendor cooperation this is best-effort.

---

### 3.6 Developer experience — SDK integration difficulty

**Issue:** §8.4 says "importable as a library; async-first." But integrating the SDK into an agent runtime requires the host to:
1. Implement a `render` callback (§7.5) — non-trivial for every UI paradigm (CLI, web, voice).
2. Implement a `credential_provider` callback (§9.4).
3. Implement an `OAuthFlowHandler` (§8.3) — the spec says "Agent providers implement this ONCE" but the interface has 4 methods including `authorize`, `refresh`, `revoke_access`, `status`. That's a real implementation burden.
4. Wire `on_tool_use` (§8.5) into the agent's transparency log.
5. Decide federation mode, cache TTL, sandbox config, egress allowlist, headless mode…

This is not "embed and go." It's a multi-day integration. The spec doesn't provide a reference integration in Phase 1 (Phase 2 lists "reference integration with one open-source agent"). Without a copy-pasteable example, DX is theoretical.

**Severity:** Medium
**Proposed Change:**
- §8: Add a `DefaultOAuthFlowHandler` the SDK ships out-of-the-box, so hosts don't implement the interface unless they need custom behavior. The "implement once" framing should be "override only if needed."
- Phase 1 exit criteria: add "reference integration with one open-source CLI agent" (e.g. a Hermes skill or a Cline plugin). DX is proven by doing it, not by specifying it.
- Ship a `pharos-discovery-quickstart` repo with 3 minimal integrations (CLI, Express web, headless) before Phase 2.

---

## 4. Risk Factors

### 4.1 Official MCP Registry adds discovery → Pharos redundant

**Issue:** §3.1 notes the official MCP Registry is "intentionally simple… case-insensitive substring search… for more advanced searching, use a subregistry." The MCP team has explicitly left the door open to richer discovery. If the official registry adds semantic search + auth metadata (a likely 12-month evolution), Pharos's primary differentiator (§12 #1 "it is a client, not a server") survives, but its *registry* value (§13 "publish once, found everywhere") collapses — businesses publish to the official registry directly. Pharos becomes a client-only wrapper.

The spec's positioning as "complementary superset to the official MCP Registry" (§1, §2) is strategically sound but operationally fragile: it depends on the official registry *not* adding the three layers Pharos adds (discovery, consent, OAuth). If Anthropic adds any one of them natively, Pharos's marginal value drops.

**Severity:** High (strategic)
**Proposed Change:**
- §2: Add an explicit "what if the official registry adds discovery?" contingency. Position Pharos's defensible assets as: (a) the *client SDK* with consent baked in (hard for a registry to own), (b) cross-registry federation (the official registry will likely *not* federate to ARD/AGNTCY), (c) the OAuth/consent UX layer. Make these the stated core, not the registry.
- §15: Accelerate the consent + OAuth layers (the parts the official registry is least likely to add) ahead of the search layer if possible. The search layer is the most displacable.

---

### 4.2 MCP Apps evolves or breaks compatibility

**Issue:** §17 depends on MCP Apps (referenced as "live January 2026" — very new). MCP Apps is an extension, not the core spec. Extensions evolve, break, get renamed, get absorbed. Pharos's entire OAuth model is built on a 6-month-old extension's specific behavior (sandboxed iframe, `postMessage`, HTML tool returns). If MCP Apps v2 changes the rendering model, the security model, or the postMessage protocol, Pharos breaks.

**Severity:** High
**Proposed Change:**
- §17.5: Pin to a specific MCP Apps spec version. Add a §17.5.2 "Versioning and compatibility" stating the SDK tests against MCP Apps ≤vX and degrades gracefully on newer versions (fall back to redirect). Add the MCP Apps version to the host-capability probe (§2.3 proposed change).
- Diversify: don't make MCP Apps the *only* inline-UI path. Specify a `dialog`/`popup` alternative for hosts that have a native windowing system but not MCP Apps.

---

### 4.3 Major agent provider builds a walled garden

**Issue:** §3.1 already lists Claude Connectors, Copilot connectors, and ARD (Google/MS/HF). If any one of these achieves dominance, Pharos is the "open alternative" that lost. The spec's "next Google" thesis (§2) cuts both ways: there *will* be a Google of agent discovery, and if it's not Pharos, Pharos is DuckDuckGo — noble, small, surviving.

**Severity:** High (existential)
**Proposed Change:**
- §2: Be honest about the spectrum of outcomes (dominant / coexist / marginal) and what determines each. Add a §2.4 "Failure modes and triggers to pivot" — e.g., "if ARD reaches v1.0 with consent + OAuth, Pharos folds its client into an ARD reference implementation rather than competing."
- Avoid framing that implies inevitable dominance. "Next Google" rhetoric will age badly if Pharos ends up niche.

---

### 4.4 Consent fatigue

**Issue:** §4.2 "Consent is non-negotiable… agents MUST NOT establish a connection without explicit user approval." For a power user issuing 20 tool calls across 8 servers in a session, that's 8 approval prompts. After the 3rd, users approve without reading. The consent gate becomes a rubber stamp and provides no real security — only friction. The spec's `once`/`session`/`persistent` durations (§7.3) help, but `persistent` requires a *second* confirmation (more friction), and there's no "auto-approve for verified publishers" path.

**Severity:** High
**Proposed Change:**
- §7.3: Add a `trust_on_use` duration — after one successful `tools/call` with a `verified=true`, `availability=mirrored`, `rating.count>100` server, subsequent connections to the *same* server within 7 days auto-approve at the originally approved scope set, with a non-blocking notification ("reconnected to Acme Flights — click to review"). This preserves transparency without the modal.
- §7.2: Allow a "batch plan" approval where the agent presents a *plan* ("I'll use Acme Flights + Acme Expenses to complete your trip") and the user approves the plan once, not each server. Implement via a `PlanApproval` type.
- §7: Define a "consent fatigue budget" — the SDK warns the host if a user has approved >5 novel servers in a session, suggesting a pause.

---

### 4.5 Privacy — does discovery leak user intent to the registry?

**Issue:** This is a serious gap. Every `POST /v1/search` (§6.3) sends `query.text` — the user's natural-language need — to the registry. Examples from the spec: "I need to book a flight to Tokyo and file the expense report." This tells the registry:
- The user is planning a Tokyo trip.
- The user has expense reports to file.
- Possibly PII embedded in the query (names, dates, destinations).

The registry is a third party. §10 (Security Model) is silent on query privacy. §6.2 auth modes include "Anonymous — public read-only search (default)." Anonymous to the *registry*, but the registry still sees the query text and the caller's IP. For a framework that makes consent a first-class principle (§4.2), ignoring query privacy is a glaring inconsistency. A user who won't approve a server without consent is having their intent shipped to that server's *indexer* without consent.

§13.6 ("Discovery as the new SEO") makes it worse: it explicitly says businesses author `representative_queries` to rank higher — codifying that the registry's job is to match user intent to business offerings, which requires the registry to *read* user intent.

**Severity:** Critical
**Proposed Change:**
- Add a §10.8 "Query privacy" section. Required content:
  - The SDK MUST warn the user (in a one-time disclosure) that search queries are sent to the registry.
  - The SDK SHOULD support a `privacy_mode` that sends only structured `filter` fields (transport, capabilities, tags) and *no* `query.text`, degrading to keyword-only search. Document the recall tradeoff.
  - The registry SHOULD support a `blinded_search` mode where the SDK generates the embedding *locally* and sends only the vector (§14.2 "on-device embedding model" is already a future feature — promote it). With local embeddings, the registry never sees plaintext intent.
  - The registry MUST NOT log `query.text` at the user level; aggregate-only logging is permitted. This is a registry-side contract the SDK SHOULD verify via a `/v1/privacy` policy endpoint.
- §6.3: Add a `query.embedding` field (float array) as an alternative to `query.text`, so privacy-preserving clients can search by vector.
- §13.6: Reconcile "discovery as SEO" with privacy — businesses optimize `representative_queries` against the *registry's* embedding model, but queries can still be sent as vectors. The two are compatible; document how.

---

## 5. Potential Roadblocks

### 5.1 Agent provider adoption — incentive for OpenAI to register (Level 1 CIMD)

**Issue:** §17.3 requires agent providers to `POST /v1/agents/register` with a signed software statement (SEP-1032). OpenAI's incentive to do this:
- Pro: their agent gets verified identity → vendors allow-list them → better MCP server access.
- Con: they register with a neutral registry they don't control, implement SEP-1032 signing, and hand Pharos a list of their redirect URIs.

For OpenAI, the "pro" is marginal (they already get access by being OpenAI); the "con" is ceding discovery to a third party. There is no scenario in which OpenAI prioritizes this. Same for Anthropic. Level 1 adoption will come from smaller providers first — which is fine, but the spec shouldn't imply (§17.2 diagram) that "OpenAI / Cursor / Anthropic" register on day one.

**Severity:** High
**Proposed Change:**
- §17.2/§17.3: Reframe Level 1 as *progressive*. Tier 1: anonymous (no registration, works but no vendor allow-listing). Tier 2: self-asserted identity (one HTTP call, no SEP-1032). Tier 3: verified (SEP-1032 signed, full CIMD). Most providers start at Tier 2; enterprises go to Tier 3. Don't gate the core flow on Tier 3.
- §17.3: Add "CIMD self-hosting" as an alternative — a provider can host their CIMD at their own `/.well-known/oauth-client-metadata` and just *register the URL* with Pharos, avoiding handing Pharos their keys. Pharos becomes a directory, not a key custodian.

---

### 5.2 Vendor adoption — incentive for Salesforce to pre-register their OAuth app

**Issue:** §17.2 Level 2 requires vendors (Salesforce, Stripe, SAP) to:
1. Pre-register an OAuth app with their IdP.
2. Bundle the registration into `pharos.json`.
3. Publish a ServerCard to the Pharos Registry.
4. Implement MCP Apps inline OAuth in their MCP server.

This is real engineering work for a vendor whose customers mostly *don't* use Pharos yet. The vendor's incentive is "reach agents using Pharos" — but there are near-zero such agents at launch. The spec provides no answer to the chicken-and-egg. Vendors will wait for agent adoption; agents will wait for vendor catalogs.

**Severity:** High
**Proposed Change:**
- §13 / §17: Add a §17.8 "Vendor bootstrap program" — the Pharos team ships reference MCP-server wrappers for the top 20 vendors (Salesforce, Stripe, GitHub, Slack, Notion, etc.) that *they* can fork, pre-configured with App Registration Inheritance. Reduce vendor work to "paste your client_id."
- §15 Phase 0: Add "hand-author ServerCards for the top 20 MCP servers in the official registry, with OAuth configs filled from public docs, and submit PRs to those projects." Bootstrap the catalog by doing the work for vendors.
- Offer vendors a quid pro quo: publishing to Pharos also publishes to the official MCP Registry via the sync adapter (§13.3) — one publish, two registries. Make Pharos *the easiest way* to publish to the official registry.

---

### 5.3 MCP Apps support fragmentation

**Issue:** Already covered (§2.3, §3.3). Adding to the roadblock framing: even among hosts that *support* MCP Apps, support will be inconsistent (different iframe sandbox policies, different postMessage origin handling, different CSP enforcement). The spec says hosts "SHOULD enforce" the CSP (§17.5) — SHOULD, not MUST. A host that doesn't enforce CSP is insecure, and the SDK has no way to know. Fragmentation means the same server works safely on Claude and unsafely on SomeOtherHost.

**Severity:** High
**Proposed Change:**
- §17.5: Add a host-capability attestation. The SDK queries the host at init: `supports_mcp_apps: bool`, `enforces_csp: bool`, `sandbox_attributes: list[str]`. If the host reports `enforces_csp=false`, the SDK refuses inline OAuth for that host and falls back to redirect. Make the security floor enforced, not advisory.

---

### 5.4 Consent gate as friction for seamless-operation agents

**Issue:** §4.2 "no silent connections, ever." But the entire industry trend (OpenAI Operator, Anthropic computer-use, Devin) is toward *more* autonomous operation, not less. A consent gate before every new connection is a decelerator. Agents that win on "it just did the thing for me" will skip Pharos or ship it with `headless_mode=true` (§7.5), which is a flag that *bypasses the prompt* — meaning the spec already contains the escape hatch it claims not to have.

`headless_mode=true` (§7.5, §8.5) is a contradiction with §4.2's "no bypass." It exists for pipelines, but any agent vendor who finds consent annoying will run in `headless_mode=true`. The "no bypass" claim is false by the spec's own admission.

**Severity:** High
**Proposed Change:**
- §7.5 / §8.5: Either remove `headless_mode` (truly no bypass — but then automated pipelines break) or explicitly acknowledge it as a *scoped* bypass: `headless_mode` requires a *pre-approved scope set in config* (not "approve everything"), is logged prominently, and the SDK refuses novel servers in headless mode. Tighten the definition so it's not a blanket opt-out.
- §4.2: Reword to "no silent connections to *novel* servers" — allow pre-configured allow-lists to connect without a per-connection prompt. This preserves the security intent while permitting automation.

---

### 5.5 Standardization conflicts — MCP defines its own discovery

**Issue:** If MCP (the protocol) defines a discovery protocol natively — which is a natural evolution — Pharos is obsolete *unless* it has already achieved adoption. The spec positions Pharos as complementary (§1), but complementarity is a temporary state. The window to become indispensable is short.

**Severity:** High
**Proposed Change:**
- §15: Compress the roadmap. Phase 3 (federation, A2A, AGNTCY) is where Pharos's cross-protocol value becomes defensible. If Phase 3 slips past ~6 months from Phase 1, the standardization risk dominates. Consider parallelizing Phase 2 and Phase 3.
- §2: Engage explicitly with the MCP standards process. Add "Pharos will contribute the consent + OAuth layer upstream to the MCP spec if the working group adopts it; Pharos remains the reference implementation." Make being absorbed a *win*, not a loss.

---

## 6. Security Flaws

### 6.1 Consent flow bypass by a modified agent

**Issue:** Covered in §1.3. Restating in security framing: the consent gate is a client-side check. A modified agent (or one that never embedded the SDK) reads the `endpoint` from a `ServerCard` and connects directly. The `ApprovalToken` (§7.4) is minted *by the SDK* and checked *by the SDK* — no external verifier. The threat model (§10.7) claims "Agent silently connects → Approval gate is enforced in SDK; no bypass API" — this is **false** for the actual threat (a non-conformant or hostile agent).

**Severity:** Critical
**Proposed Change:** As in §1.3: either (a) make the `ApprovalToken` a server-verifiable credential presented at MCP `initialize` (requires MCP extension + server cooperation — hard), or (b) downgrade the claim to "enforced for conformant agents" and document that non-SDK agents are out of scope. Stop listing "Agent silently connects" as mitigated in §10.7; it is not.

---

### 6.2 Gaming search results

**Issue:** §13.6 explicitly invites vendors to author `representative_queries` to rank higher — "the agentic analog of SEO keywords." This is an open invitation to keyword-stuff. A malicious vendor authors 500 `representative_queries` spanning popular intents ("book a flight", "file taxes", "send email") and ranks for everything. The spec has no anti-gaming mechanism:
- No query-document relevance audit.
- No penalty for over-broad `representative_queries`.
- No `click-through` signal (agents don't "click" — they call tools; but `tool/call` success rate could be a signal).
- `rating.score` is gameable by sybil reviews (§6.8 allows reviews; no anti-sybil mechanism specified).
- `pharos_score` is registry-computed and opaque — the spec doesn't define the ranking algorithm, so gaming resistance is unspecified.

**Severity:** High
**Proposed Change:**
- §13.6: Add anti-gaming rules: (a) `representative_queries` capped at 10 per server; (b) the registry MAY penalize servers whose queries are low-specificity or span unrelated capabilities; (c) `pharos_score` SHOULD incorporate post-connection signals (tool-call success rate, user reports) fed back via the SDK; (d) reviews require verified publisher-of-reviewer or a minimum `agent_id` reputation; (e) the registry SHOULD detect review bombing via velocity + IP clustering.
- §10.3: Add "search-result poisoning" to the threat model with the above mitigations.
- §6.8: Add review-auth requirements (authenticated, rate-limited, sybil-resistant via proof-of-work or verified agent identity).

---

### 6.3 ServerCard poisoning — misleading claims

**Issue:** §10.1 says attestations are "claims the publisher makes, linked to URIs" and "NOT treated as proof." Good. But:
- The publisher writes `display_name`, `description`, `capabilities`, `pricing`, `trust.attestations`. A malicious publisher can claim `"attestations": ["SOC2-Type2", "HIPAA-Audit", "PCI-DSS"]` with forged URI links. The spec says the registry "may independently verify attestations and mark them `registry_verified`" — but the `ServerCard` schema (Appendix A) has `trust.attestations` as an array of *strings*, not objects with a `verified` flag. So `registry_verified` is mentioned in §10.1 but **absent from the schema**. Contradiction.
- `capabilities` are free-form strings. A server can claim `capabilities: ["payment_refund"]` and not implement it. The SDK doesn't verify capabilities against `tools/list` post-connect. §9.1 doesn't require capability-to-tool mapping.
- `pricing` is vendor-asserted. A server claims `free_tier: "100 calls/month"` and charges per call anyway. No billing enforcement.
- `tools_count` is vendor-asserted and could be inflated to look more capable.

**Severity:** High
**Proposed Change:**
- Appendix A: Change `trust.attestations` from `array<string>` to `array<object>` with `{type, uri, verified: bool, verifier: string, verified_at: string}`. Reflect §10.1's `registry_verified` in the schema.
- §9.1: After `tools/list`, the SDK MUST verify that `server.capabilities` are backed by actual tools. Define a `capability → tool` mapping convention (e.g. capability `flight_search` is backed by a tool `flight_search` or a tool with `metadata.capability: "flight_search"`). Mismatch downgrades the card and emits a warning.
- §13.2: `tools_count` and `capabilities` SHOULD be registry-verified by the registry running `tools/list` on a sampled basis. Mark unverified counts as `null` or `tools_count_verified: false`.
- §7.2: Display `pricing` as "vendor-claimed, not verified" unless the registry provides a `pricing_verified` flag.

---

### 6.4 OAuth via App Registration Inheritance — residual risks

**Issue:** §10.5 is a strong security section, but residual holes remain:

#### 6.4.1 MCP server abuses the token (scope creep, data exfiltration)

The agent "never sees the token" (§10.5) — but the MCP server holds it and proxies all calls. A malicious MCP server can:
- Call the vendor's API with the user's token for any purpose within the granted scope, not just the agent's requested tool call. The user approved `bookings:write` for "book a flight"; the server uses `bookings:write` to cancel all the user's bookings.
- Exfiltrate user data from the vendor's API to a third-party endpoint. The SDK's `egress_allowlist` (§10.2) only covers *agent* egress, not *MCP server* egress. The server can phone home freely.

The spec's threat model (§10.7) lists "Exfiltration via tool args → Local egress allowlist" — but that's *agent-side* egress. Server-side exfiltration by a malicious MCP server is **not in the threat model**.

**Severity:** Critical
**Proposed Change:**
- §10.5: Add "Server-side exfiltration" to the threat model. Mitigations: (a) the inline OAuth UI SHOULD display "This server will be able to call <vendor API> on your behalf for any purpose within the approved scopes" — make the over-broad risk explicit to the user; (b) vendors SHOULD use their IdP's fine-grained scopes (not `bookings:write` but `bookings:write:flight_only`) and the SDK SHOULD surface scope granularity in the approval prompt; (c) for `mirrored`/`native` servers, the Pharos Registry MAY run a static analysis on the server's tarball to detect suspicious egress endpoints and flag them. Acknowledge that server-side abuse is fundamentally out of the SDK's control and consent is the only mitigation.

#### 6.4.2 Inline OAuth UI (MCP Apps iframe) — phishing

§17.5 says the iframe is sandboxed and CSP'd. But:
- The login form is vendor-controlled HTML. A malicious MCP server returns a login form that *looks like* Google's login and posts credentials to `auth.attacker.com/oauth/authorize` (which is in their declared `endpoints`). The user, expecting to log in to "Acme Flights," types their Google password into the attacker's form. The CSP `frame-ancestors 'self'` doesn't prevent this — the form is served same-origin with the iframe.
- The approval prompt (§7.2) shows `display_name` and `publisher.name` — both vendor-written. A server named `Googgle OAuth` with `publisher.name: "Google LLC"` (unverified) will fool some users.
- There is no visual continuity guarantee between the approval card (rendered by the host) and the iframe (rendered by the server). A user can't tell they're "still in the same flow."

**Severity:** Critical
**Proposed Change:**
- §17.5: The host (not the server) MUST render a non-spoofable chrome around the iframe: the publisher's verified domain, the OAuth endpoints being contacted, and a "do not enter your password if this domain doesn't match your IdP" warning. The iframe content MUST NOT be able to draw outside its bounds (sandbox attribute includes `allow-popups: false`).
- §7.2: Reject `display_name` / `publisher.name` that are confusingly similar to well-known brands (Levenshtein distance check at publish time). Surface `publisher.verified` as a hard gate for brand-impersonating names.
- §17.5: Recommend that hosts display the iframe's effective URL (the `src`) in the chrome, so the user can see they're logging in to `auth.acme.com`, not `auth.attacker.com`.
- §10.5: Add "Inline OAuth phishing" to the threat model with the above mitigations.

#### 6.4.3 Impersonation of a vendor's `app_registration.client_id`

The `ServerCard.auth.app_registration.client_id` is public (it's in the registry). A malicious MCP server can copy Acme's `client_id` and claim to be Acme. When the user authenticates, the malicious server's inline OAuth form redirects to `auth.acme.com/oauth/authorize` — but the malicious server doesn't have Acme's `client_secret`, so it can't complete the token exchange. So far so good: the secret isolates the attacker.

BUT: the attacker can still harvest user credentials if their inline form *impersonates* Acme's login UI and posts to the attacker's server before the OAuth redirect. The user types their Acme password into the attacker's form. The `client_id` impersonation lends credibility. This is §6.4.2 amplified by `client_id` reuse.

Also: a malicious server can copy Acme's `client_id` and Acme's `endpoints` *exactly*, then proxy the real OAuth flow to Acme's IdP — harvesting the authorization code and exchanging it (if they also have the secret — which they don't, so this fails) OR relaying the user's session to exfiltrate data post-login. The secret protects the token exchange; it does not protect the user from UI impersonation during the flow.

**Severity:** High
**Proposed Change:**
- §10.1: Bind `app_registration.client_id` to the publisher's verified domain at registry-publish time. A server publishing `client_id: "acme-mcp-flights-prod"` MUST have `publisher.id: did:web:acme.com` and the registry MUST verify that the `client_id` was first registered by the same publisher. Reusing another publisher's `client_id` is a publish-time rejection.
- §17.5: The host chrome (per §6.4.2 proposed change) displays the *publisher's verified domain* alongside the iframe, not the server's self-declared name. The user's trust anchor is the domain, not the `display_name`.

#### 6.4.4 CIMD compromise — Pharos mints a fake agent identity

§17.3: the Pharos Registry hosts the CIMD and verifies the software statement. If the Pharos Registry is compromised, an attacker mints a fake CIMD for "OpenAI" and impersonates OpenAI to vendor MCP servers. Vendors allow-list "OpenAI" and the attacker gets access. The spec's mitigation (§10.7 "Compromised registry → Signatures verified against publisher's own published keys, not the registry's") applies to *publisher* signatures on ServerCards, not to *agent provider* CIMDs — which *are* hosted by the registry. CIMD compromise is a registry-compromise failure mode that the threat model doesn't address.

**Severity:** High
**Proposed Change:**
- §17.3: Require CIMD documents to be *signed by the agent provider* (using the provider's own key, attested in a transparency log) and the registry to serve them as opaque signed blobs. Vendors verify the provider's signature against a pinned provider key (fetched from the provider's own `/.well-known/agent-provider-keys`, not the registry). This makes a registry compromise insufficient to forge a provider identity — the attacker also needs the provider's key.
- §10.7: Add "Registry mints fake agent identity" to the threat model with the above mitigation.

#### 6.4.5 Token revocation on disconnect — does the MCP server actually delete the token?

§17.4: "Revocation is a request from the SDK to the MCP server (`OAuthFlowHandler.revoke_access`), which tears down its server-side session." This is a *request*. The MCP server can:
- Ignore it and keep the token.
- Claim to revoke but retain the token (intentionally or via bug).
- Lose the revocation request (network failure) and keep the token.

The SDK has no way to verify the token was actually revoked — it doesn't hold the token and can't introspect it at the IdP. A user who "disconnects" believes they're safe; the MCP server may still be calling the vendor API on their behalf.

**Severity:** High
**Proposed Change:**
- §17.4: Require the MCP server to return a `revocation_proof` — a signed assertion that it called `endpoints.revocation` with the token, or a token-introspection response showing `active: false`. The SDK verifies this against `endpoints.jwks`.
- If the IdP supports token introspection (RFC 7662), the SDK SHOULD independently verify revocation by asking the IdP (using a public introspection endpoint, if available, or via the MCP server as a proxy with proof).
- §10.5: Add "Revocation not honored" to the threat model. Mitigation: on `revoke_access`, the SDK marks the server as "revocation unconfirmed" after 60s without proof and surfaces a warning to the user: "Acme Flights may still have access to your account. Revoke directly at <vendor's app-management URL>." Expose `endpoints.revocation` and the vendor's "manage app access" URL in the `ServerCard` so the user can revoke at the IdP directly.

---

### 6.6 Privacy — search query leakage

Covered in §4.5. Adding the security framing: sending `query.text` to a third-party registry is a **data minimization failure** under GDPR Art. 5(1)(c) and a potential "processing of personal data" if the query contains PII. The spec doesn't mention GDPR once. For a framework aiming at enterprise adoption (§10.6), this is a compliance gap.

**Severity:** Critical
**Proposed Change:** As in §4.5: add §10.8 "Query privacy," support `query.embedding` for vector-only search, add `privacy_mode`, document data flows in a GDPR-compatible privacy notice. Add a `/v1/privacy` registry endpoint publishing the registry's logging and retention policy.

---

### 6.7 Rate limiting on discovery — server enumeration

**Issue:** §13.5 says search is "generous" and "burst-tolerant." An attacker can enumerate the entire registry by issuing broad queries (`text: "*"`, pagination through `cursor`). With `limit: 50` and generous rate limits, scraping the whole catalog is trivial. This:
- Exposes every publisher's metadata to a competitor.
- Lets an attacker map the capability landscape for free.
- Enables sybil-review attacks at scale (create agents, review every server).
- §6.8 reviews are "authenticated, lower limits" but `POST /v1/search` is anonymous (§6.2) — so enumeration is unauthenticated.

The spec treats rate limiting as abuse prevention for *traffic*, not for *enumeration*. There's no mention of pagination caps, total-result caps, or suspicious-pagination detection.

**Severity:** Medium
**Proposed Change:**
- §13.5: Add enumeration defenses: (a) cap total paginated results per query-hash per IP per hour (e.g. 500); (b) require authentication for `pagination.cursor` beyond page 5; (c) detect enumeration patterns (sequential cursors, empty-filter queries) and rate-limit aggressively; (d) the registry SHOULD offer a bulk download endpoint (`GET /v1/export`) with a published dump (like npm's couchdb changes feed) so legitimate crawlers don't need to scrape search.
- §6.2: Require auth for any query with an empty `text` and empty `filter` (pure enumeration).

---

## 7. Proposed Changes — Consolidated Index

Below is the full list of proposed changes, each with severity, section reference, and rationale. (Item-level detail appears in §1–6 above; this index is for the spec author to triage.)

### Critical (ship-blockers)

| # | Issue | Section | Proposed Change (summary) | Rationale |
|---|---|---|---|---|
| C1 | Consent gate is advisory, not enforceable | §4.2, §7, §10.7 | Reword "enforced" → "enforced for conformant SDK agents"; add §10.7.1 out-of-SDK threats; pursue server-side `ApprovalToken` verification at `initialize` as the real fix | The spec's central security claim is false against non-SDK agents |
| C2 | MCP Apps dependency is load-bearing with no real fallback | §17.5 | Add §17.5.1 host-capability negotiation; ship server-brokered redirect (not agent-handles-token) as a first-class mode for non-MCP-Apps hosts | §17.1 argues redirects are wrong; §17.5 falls back to redirects — contradiction. Cursor/Copilot/headless agents have no path |
| C3 | Search query leaks user intent to registry; GDPR risk | §6.3, §10 (absent) | Add §10.8 Query Privacy; support `query.embedding` for vector-only search; add `privacy_mode`; `/v1/privacy` policy endpoint | Consent is a first-class principle but query privacy is ignored — inconsistency + compliance gap |
| C4 | OAuth confirmation has no integrity protection | §17.4 step 5 | Require the MCP server's `authorized: true` to carry a signed IdP JWT attesting `{user_sub, scope, exp}`, verified via `endpoints.jwks` | A malicious MCP server can lie about auth and proxy calls to steal data |
| C5 | Inline OAuth iframe enables credential phishing | §17.5 | Host MUST render non-spoofable chrome (verified domain, OAuth endpoints, URL display) around the iframe; reject brand-similar `display_name`s at publish time | Vendor-controlled HTML + vendor-written names = classic phishing setup |
| C6 | `headless_mode` is a bypass the spec claims not to have | §4.2, §7.5, §8.5 | Tighten `headless_mode` to require pre-approved scope config, refuse novel servers, log prominently; reword §4.2 to "no silent connections to novel servers" | The spec contradicts its own "no bypass" claim |
| C7 | MCP server can abuse the held token (server-side exfiltration) | §10.5, §10.7 (absent) | Add "server-side exfiltration" to threat model; surface over-broad-scope risk in the approval UI; promote fine-grained scopes; static egress analysis for mirrored servers | The agent never sees the token, but the server holds it and can misuse it |
| C8 | Adoption has no answer to the chicken-and-egg / inverted incentives | §2, §17 | Add §2.3 Adoption Strategy (long tail first, free CIMD, bulk-import registry, reference vendor wrappers); add §17.8 Vendor Bootstrap Program | Existential risk; the parties with least incentive are asked to do the most work |

### High (must fix before v1.0)

| # | Issue | Section | Proposed Change (summary) |
|---|---|---|---|
| H1 | 6-step flow gaps (empty results, multi-server, no selection rationale) | §7.1 | Add §7.1.1 edge cases; `PlanApproval`; `selection_rationale` field |
| H2 | ServerCard missing 11 fields | §8.3, App. A | Add `updated_at`, `status`, `successor_id`, `privacy_policy_url`, `data_residency`, `rate_limits`, `health_endpoint`, `protocol_versions`; resolve `auth.scopes` dup; define `redirect_uri_pattern` semantics |
| H3 | Adapters have fidelity problems (A2A can't connect, ARD score rescale wrong, MCP re-rank needs LLM) | §11.3–§11.6 | Specify embedding model; make re-ranking optional; per-source score normalization; make A2A discovery-only or add A2A transport; RO/RW adapter capabilities |
| H4 | URN collisions, domain transfer, no resolution protocol | App. B, §6.4 | Fix grammar; add §6.4.1 URN resolution; §10.8 key rotation/TTL; dedup in federation |
| H5 | OAuthFlowHandler sequencing ambiguous, `refresh()` vestigial, no timeout/cancel | §9.4, §17.4 | Add sequence diagram; remove/redefine `refresh()`; add `auth_timeout`, `auth_cancelled`; signed confirmation (C4) |
| H6 | SDK divergence (no IDL, conformance in Phase 4, TS Phase 2) | §8 | Contract-first IDL (TypeSpec); conformance in Phase 1; ship TS sooner or WASM stopgap |
| H7 | Registry unreachable = no cold-start fallback, no failover | §8.5, §13.5 | `registry_urls: list`; cold-start static fallback; explicit 503/504 failover |
| H8 | MCP server unreachable = no timeouts, no liveness, no cert handling | §9 | Add `initialize_timeout`, heartbeat, `ConnectionLost`; cert validation rules |
| H9 | Stale cache, no invalidation, key pinning blocks rotation | §6.4, §9.3, §10.3 | `ETag`/conditional GET; `/v1/events` SSE invalidation; key TTL; blocklist 60s |
| H10 | MCP server crash mid-OAuth has no cleanup | §17.4 | Add step 4b failure handling; invalidate `ApprovalToken`; `RetryableOAuthFailure` |
| H11 | Consent fatigue → rubber-stamping | §7.3 | `trust_on_use` auto-reapprove; `PlanApproval`; fatigue budget warning |
| H12 | Search-result gaming / sybil reviews | §13.6, §6.8 | Cap `representative_queries`; penalize low-specificity; review sybil-resistance; post-connect signal feedback |
| H13 | ServerCard poisoning (attestations are strings, capabilities unchecked, pricing unverified) | §10.1, App. A, §9.1 | `attestations` → objects with `verified`; capability→tool mapping check post-connect; registry-sampled `tools_count` verification |
| H14 | `client_id` impersonation enables phishing amplification | §10.1, §17.5 | Bind `client_id` to verified publisher at publish time; host chrome shows verified domain |
| H15 | CIMD forgeable on registry compromise | §17.3, §10.7 | Provider-signed CIMD; vendor verifies against provider-pinned key; add to threat model |
| H16 | Revocation is best-effort with no verification | §17.4, §10.5 | Require `revocation_proof` (signed); independent introspection; surface "revoke at vendor" URL on unconfirmed revocation |
| H17 | Python-first alienates the JS ecosystem | §14.1, §15 | Ship TS in Phase 1 or WASM stopgap; move TS to week 4 |
| H18 | Coexistence with vendor connectors unaddressed | §11.7 | Add §11.8 Coexistence; `detect_native_connector` API; defer to native |
| H19 | MCP Apps versioning fragility | §17.5 | Pin MCP Apps version; host-capability attestation; `dialog` fallback |
| H20 | Enumeration of registry via generous search | §13.5, §6.2 | Pagination caps; auth for deep pagination; bulk `/v1/export` endpoint; empty-query auth requirement |

### Medium (fix before broad adoption)

| # | Issue | Section | Proposed Change (summary) |
|---|---|---|---|
| M1 | Consent denial has no structured reason or re-prompt | §7, §8.3 | `deny_reason` enum; `request_approval_next`; scope re-negotiation flow |
| M2 | Timeout model too coarse | §8.5, §6.5 | Split timeouts by phase; federated partial results with `degraded_sources` |
| M3 | DX burden (4+ host callbacks, no reference integration in P1) | §8, §15 | Ship `DefaultOAuthFlowHandler`; P1 reference integration; quickstart repo |
| M4 | `auth.scopes` vs `app_registration.scopes` ambiguity | App. A | Explicit precedence rules |
| M5 | `publisher.verified` conflates domain control with trust | §10.1 | Distinguish `domain_control` vs `identity` verification; different badges |

### Low (polish)

| # | Issue | Section | Proposed Change |
|---|---|---|---|
| L1 | `representative_queries` SEO analogy invites gaming | §13.6 | Capped + relevance-audited (folded into H12) |
| L2 | Inconsistent `contact` field (in publish payload, not schema) | App. A | Add `contact` to schema or remove from §13.2 |
| L3 | "Next Google" rhetoric may age badly | §2 | Soften; add failure-mode triggers |

---

## 8. Summary of Highest-Priority Actions

If the spec author fixes only five things before v0.3, fix these:

1. **C1 + C6 — Stop overstating consent enforcement.** Reword every "enforced" claim; tighten `headless_mode`; acknowledge non-SDK agents are out of scope. The spec's credibility depends on not claiming a security property it can't deliver.
2. **C2 — Build the real non-MCP-Apps fallback.** A server-brokered redirect flow (agent still never sees the token) for Cursor/Copilot/headless. Without this, the OAuth model is unusable on the hosts most likely to adopt early.
3. **C3 — Add query privacy.** `query.embedding` support, `privacy_mode`, a `/v1/privacy` policy, and a user disclosure. This is a GDPR blocker for enterprise adoption (§10.6) and a consistency blocker with the consent-first principle.
4. **C4 + C5 + C7 — Close the OAuth integrity holes.** Signed confirmation JWT, non-spoofable iframe chrome, server-side-exfiltration in the threat model. The §17 model's security story is currently incomplete in three distinct ways.
5. **C8 — Write the adoption strategy.** Name the chicken-and-egg, target the long tail, bootstrap the catalog with a bulk import, ship vendor reference wrappers. The tech is only half the battle; the spec currently has no go-to-market.

---

**End of Critical Review — Pharos Discovery SPEC.md v0.2.0**
