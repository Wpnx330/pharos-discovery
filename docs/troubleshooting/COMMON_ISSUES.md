# Common Issues — PHAROS Discovery

**Spec reference:** `SPEC.md` v0.4.0 (section references throughout).
**Audience:** Developers using either SDK who hit problems.

---

## Search

### "No servers found" but I know one exists

**Cause:** Your filters are too narrow, or the registry doesn't support the filter path you used.

**Fix:**
1. Broaden filters. `publisher_verified: True` + `min_rating: 4.0` + `pricing_tier: ["free"]` can easily exclude everything.
2. Check the registry supports your filter path. Unsupported paths return `400 UNSUPPORTED_FILTER` (§6.13). Catch it:
   ```python
   try:
       results = await pharos.search(filter={"data_residency": ["EU"]}, limit=10)
   except UnsupportedFilter as e:
       print(f"Registry doesn't support: {e}")
   ```
3. Drop `text` and use only structured filters, or vice versa. The registry may have limited semantic search.
4. One broadened retry is automatic (§7.1.1). If still empty, the host decides next steps.

### `RegistryUnavailable` error

**Cause:** All `registry_urls` are unhealthy (503/504/timeout) AND no cached `ServerCard` results are available (§13.5).

**Fix:**
1. Check your network.
2. Add more `registry_urls` for failover (§8.5 H7).
3. Set `static_fallback_servers` for critical infrastructure (approval gate still enforced).
4. Wait — registries are marked unhealthy for 60s before re-probe.

### `DiscoveryDegraded` warning

**Cause:** All registries unhealthy, SDK is falling back to cache or `static_fallback_servers` (§8.5). This is a warning, not a fatal error — you may get stale results.

**Fix:** Non-blocking. Results may be stale. If unacceptable, treat as `RegistryUnavailable`.

### Search results are ranked strangely / `pharos_score` is `None`

**Cause:** For ARD-sourced results, `pharos_score` is intentionally `None` (§11.4). The original score is in `source_score`. Cross-registry score comparison is forbidden by spec — different ranking functions.

**Fix:** Sort by `source_score` within each `source_registry` separately, OR use `pharos_score` where present and accept `None` as "no comparable score." Do NOT normalize `source_score` into `pharos_score`.

### 429 Too Many Requests

**Cause:** Registry rate-limiting (§13.5).

**Fix:** The SDK handles this automatically — honors `Retry-After`, exponential backoff with full jitter, falls back to cached results with `registry_degraded=True`. If you're still hitting 429, you're calling `search()` too frequently; cache results client-side and reduce call frequency.

---

## Approval

### `ValueError: selection_rationale is required`

**Cause:** `request_approval()` requires `selection_rationale` (§7.1.1). Empty string is rejected unless `headless_mode=True`.

**Fix:** Provide a non-empty rationale explaining why this server was chosen:
```python
approval = await pharos.request_approval(
    ...,
    selection_rationale="ranked #1 for flight_search; verified publisher; supports bookings:write",
)
```

### User denies approval — how do I get the next result?

**Fix:** Use `request_approval_next()` (§7.7). Returns the next-ranked result without re-searching:
```python
next_server = await pharos.request_approval_next(current_server_id=best.id)
if next_server is None:
    # No more results — re-search with broadened query
    results = await pharos.search(text=broadened_query, limit=10)
```

### `HeadlessApprovalRequired` error

**Cause:** `headless_mode=True` and the server is NOT on `headless_allow_servers` (§7.5). Headless mode is **scoped, not blanket** — novel servers are refused.

**Fix:**
1. Add the server ID to `headless_allow_servers` if you trust it.
2. OR disable `headless_mode` and use interactive approval.
3. OR use `trust_on_use` instead (mutually exclusive with `headless_mode`) — auto-approves after one successful interactive use.

### `ConsentFatigueWarning`

**Cause:** More than 5 novel-server approvals in a single session (§7.3). This is advisory, not blocking.

**Fix:** Consider using `request_plan_approval()` for multi-server plans (one consent act for multiple servers). Or accept the warning — it's just a signal that the user is approving a lot.

### Approval times out after 300s

**Cause:** `approval_timeout` (default 300s) elapsed without the `render` callback returning (§7.1).

**Fix:** The `render` callback returned `{ approved: false, deny_reason: "timeout" }`. Either increase `approval_timeout`, or make your UI more responsive.

---

## Connection

### `ConnectionFailed: initialize_timeout`

**Cause:** MCP `initialize` handshake didn't complete within `initialize_timeout` (default 10s) (§9.1 H8).

**Fix:**
1. Check the server's `endpoint` is reachable and TLS 1.2+ is working.
2. The server may be down or slow. Try a different transport if available.
3. Increase `initialize_timeout` if the server is known to be slow (not recommended — 10s is already generous).

### `ConnectionLost` after successful connect

**Cause:** The liveness probe (`health_check_interval`, default 60s) failed post-connect (§9.5). The connection is dead.

**Fix:** The SDK does NOT auto-reconnect (by design — requires fresh approval). Re-search and re-approve if the user still needs the server:
```python
try:
    result = await client.call_tool("flight_search", {...})
except ConnectionLost:
    print("Connection lost. Re-approve to reconnect.")
    # Re-search, re-approve, re-connect
```

### `CapabilityMismatch` warning

**Cause:** After `tools/list`, the SDK found a claimed `capabilities` entry with no backing tool (§9.1 H13). The capability is downgraded in `verified_capabilities`.

**Fix:** Non-blocking. The server claimed a capability it doesn't actually expose. The approval prompt would have been re-rendered if still open. If you already connected, check `client.verified_capabilities` and adjust your expectations.

### `SCOPE_NOT_APPROVED` error on `call_tool`

**Cause:** The tool requires a scope or capability the user didn't approve (§7.4).

**Fix:**
1. Re-request approval with the additional scope (rate-limited to 1 per server per session, §7.7):
   ```python
   approval = await pharos.request_approval(
       ...,
       requested_scopes=["flight_search", "flight_book"],  # add the missing scope
       ...
   )
   ```
2. OR use a different tool that's within the approved scopes.

### `connect()` raises "ApprovalToken required"

**Cause:** You passed `None` or an invalid token to `connect()`. There is no bypass (§4.2, §10.7.1).

**Fix:** Always go through `request_approval()` first:
```python
approval = await pharos.request_approval(...)
if approval.approved and approval.token:
    client = await pharos.connect(approval.token)
```

---

## OAuth (Phase 2)

### `OAuthUnavailable` error

**Cause:** The server requires OAuth but the host doesn't support MCP Apps (inline iframe) and doesn't have a system browser (§17.5.1).

**Fix:**
1. Use a host that supports MCP Apps (inline OAuth in chat), OR
2. Run on a system with a browser (server-brokered redirect with PKCE), OR
3. Fall back to a server with `auth.type: "api_key"` if available.

### `RetryableOAuthFailure`

**Cause:** Inline OAuth iframe errored, MCP server disconnected, or `oauth_timeout` (120s) elapsed (§17.4 H10).

**Fix:** The `ApprovalToken` has been invalidated. Re-approve and retry:
```python
try:
    client = await pharos.connect(approval.token)
except RetryableOAuthFailure as e:
    print(f"OAuth failed ({e.reason}). Re-approving.")
    approval = await pharos.request_approval(...)  # fresh approval
    client = await pharos.connect(approval.token)
```
The SDK does NOT auto-retry (server-side state indeterminate).

### `OAuthResult.authorized=false, error="invalid_jwt"`

**Cause:** The MCP server's signed confirmation JWT failed verification (§17.4 step 5). Either the signature didn't match `endpoints.jwks`, `exp` was in the past, or `client_id` didn't match the inherited `app_registration.client_id`.

**Fix:** The connection was torn down. This indicates either a misconfigured MCP server or a potential attack. Do NOT retry blindly. Report the server:
```python
await pharos.report_server(server_id, reason="invalid OAuth confirmation JWT")
```

### Revocation warning: "may still have access to your account"

**Cause:** `revoke_access()` was called but the MCP server didn't return a `revocation_proof` within 60s (H16). The server is marked `revocation_unconfirmed`.

**Fix:** Revoke directly at the vendor's IdP using the URL from the `ServerCard`:
```python
card = await pharos.get(server_id)
print(f"Revoke at: {card.auth.app_registration.app_management_url}")
# or
print(f"Revoke endpoint: {card.auth.app_registration.endpoints.revocation}")
```

---

## Publisher verification

### `PublisherKeyStale` warning

**Cause:** The publisher's public key failed to refresh after `key_pin_ttl_seconds` (default 86400s = 24h) (§10.9). The server is quarantined (not connectable) until re-validated.

**Fix:** Non-blocking. The server will become available again once the key refreshes. If it persists, the publisher may have rotated keys without updating `.well-known/pharos-pubkey.json` — report the server.

### Publisher shows "unverified — connect with caution"

**Cause:** `publisher.verified` is `False` or `None`. The publisher proved domain control (`domain_control`) but not organizational identity (`identity`) — or proved nothing at all (§10.1).

**Fix:** This is by design. Only `verification_method: "identity"` renders as "trusted." If you want to allow unverified publishers (local dev), set `allow_unverified=True` (logged). For production, keep `allow_unverified=False`.

### Brand-impersonation warning on approval prompt

**Cause:** `display_name` or `publisher.name` is within Levenshtein distance ≤ 2 of a known brand, and `publisher.verified` is `False` (§7.2). The approve button is disabled.

**Fix:** This is a security feature. Do NOT bypass it. If you're the legitimate brand owner, verify your domain via `did:web` and the registry will accept the name.

---

## Performance

### `search()` is slow

**Cause:** Network latency to the registry, or the registry is under load.

**Fix:**
1. Check `search_timeout` (default 10s). If your network is slow, you may be hitting the timeout.
2. Use `cache_conditional=True` (default) to leverage ETag/`If-None-Match` — repeated searches for the same query are faster.
3. Subscribe to `/v1/events` SSE for push invalidation so you can cache longer (TTL 300s) without staleness.
4. Use a registry closer to you (add it to `registry_urls`).

### Repeated `connect()` to the same server is slow

**Cause:** You're creating a new connection each time instead of reusing the pooled one (§9.5).

**Fix:** The SDK pools one `MCPClient` per `server_id` per session. Repeated `connect()` with a valid, non-expired `ApprovalToken` returns the cached client. Make sure you're passing the same `ApprovalToken` and not closing the client between calls.

---

## Python-specific

### `ImportError: cannot import name 'PharosClient'`

**Cause:** You installed the package but the import path is wrong, or you're on Python < 3.10.

**Fix:**
1. Check Python version: `python --version` (needs 3.10+).
2. Check install: `pip show pharos-discovery`.
3. Import: `from pharos_discovery import PharosClient` (underscore, not hyphen).

### `RuntimeError: This event loop is already running`

**Cause:** You're calling `asyncio.run()` from inside an already-running loop (e.g. Jupyter, another async framework).

**Fix:** Use `await` directly if you're already in an async context. The SDK uses `anyio`, so it works with asyncio and trio backends. In Jupyter:
```python
results = await pharos.search("book a flight", limit=5)  # works directly in Jupyter
```

### Semantic re-ranking not working with MCP Registry adapter

**Cause:** `onnxruntime` is not installed (§11.3). The adapter falls back to substring-only search (labeled "non-semantic").

**Fix:**
```bash
pip install "pharos-discovery[embeddings]"
```

---

## TypeScript-specific

### `TypeError: crypto.subtle is undefined`

**Cause:** Running in an environment without Web Crypto. Node 18 or earlier, or a non-secure context in the browser.

**Fix:**
1. Use Node 20+ (the SDK requires it).
2. In browsers, ensure the page is served over HTTPS (or localhost) — Web Crypto requires a secure context.
3. If you must use Node 18, import `crypto.webcrypto` explicitly (not recommended — upgrade to Node 20).

### `Error: Cannot find module 'child_process'`

**Cause:** You're trying to use the stdio transport (Phase 2) in a browser build.

**Fix:** stdio is Node-only (§9). The SDK lazy-imports it inside the stdio transport module, so this error means your bundler is eagerly importing it. Check your bundler config to ensure `child_process` is externalized/excluded for browser builds.

### Dual build: `"use strict"` banner appears twice in CJS

**Cause:** tsup config adds a `"use strict"` banner, and your source files also have it.

**Fix:** Remove `"use strict"` from source files — tsup's banner handles it for the CJS build. ESM doesn't need it.

### `AbortSignal.timeout is not a function`

**Cause:** Node < 17.3. The SDK uses `AbortSignal.timeout(ms)` for timeouts (§8.5 H8).

**Fix:** Use Node 20+ (the SDK requires it).

---

## Conformance / IDL drift

### CI fails on `idl-drift` job

**Cause:** The TypeSpec IDL was edited but the generated Python/TypeScript models were not regenerated (§8.6).

**Fix:**
```bash
# Regenerate both
tsp compile idl/typespec --emit pydantic --output-dir packages/python/src/pharos_discovery/models
tsp compile idl/typespec --emit typescript-zod --output-dir packages/typescript/src/models

# Commit the regenerated files
git add packages/python/src/pharos_discovery/models packages/typescript/src/models
git commit -m "chore: regenerate IDL models"
```

### Conformance tests pass in Python but fail in TypeScript (or vice versa)

**Cause:** One SDK's hand-written code diverged from the IDL-generated surface (§8.6).

**Fix:**
1. Check the failing assertion in `conformance/assertions/`.
2. Compare the hand-written code in the failing SDK against the IDL-generated models.
3. The IDL is the source of truth — fix the hand-written code, not the IDL.

---

## Getting help

- **Spec:** `SPEC.md` (v0.4.0) — the canonical reference. Section numbers cited throughout this doc.
- **Architecture:** `docs/technical/SYSTEM_ARCHITECTURE.md`
- **Security:** `.guides/security/SECURITY_GUIDE.md`
- **Python API:** `docs/api/PYTHON_API.md`
- **TypeScript API:** `docs/api/TYPESCRIPT_API.md`
- **Python guide:** `.guides/backend/PYTHON_GUIDE.md`
- **TypeScript guide:** `.guides/backend/TYPESCRIPT_GUIDE.md`
