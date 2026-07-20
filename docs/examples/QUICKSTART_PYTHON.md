# Quickstart — Python SDK

**Spec reference:** `SPEC.md` v0.4.0, §8.1 (Python surface), §7 (approval flow), §8.5 (config).
**Package:** `pharos-discovery` on PyPI. **Python:** 3.10+ (3.11+ recommended).

This walkthrough takes you from zero to a working agent that searches for an MCP server, gets user approval, connects, and calls a tool — in about 50 lines of Python.

---

## 1. Install

```bash
pip install pharos-discovery
```

Or with `uv`:
```bash
uv pip install pharos-discovery
```

Optional: semantic re-ranking for the MCP Registry adapter (SPEC §11.3):
```bash
pip install "pharos-discovery[embeddings]"
```

---

## 2. Minimal search

```python
import asyncio
from pharos_discovery import PharosClient

async def main():
    pharos = PharosClient(
        registry_urls=["https://registry.pharos.dev"],
        agent_id="my-agent/0.1.0",
    )

    results = await pharos.search("book a flight to Tokyo", limit=5)
    for card in results:
        print(f"{card.display_name} — {card.publisher.name} "
              f"(verified={card.publisher.verified}, score={card.pharos_score})")

asyncio.run(main())
```

Expected output:
```
Acme Flights — Acme Inc. (verified=True, score=0.95)
FlyRight MCP — FlyRight (verified=True, score=0.87)
...
```

---

## 3. Full flow: search → approve → connect → call → disconnect

```python
import asyncio
from pharos_discovery import (
    PharosClient, ApprovalRequest, ApprovalResponse, ApprovalToken,
)

async def render_cli(req: ApprovalRequest) -> ApprovalResponse:
    """Host-supplied approval UX. The SDK calls this; you render it."""
    print("\n" + "=" * 60)
    print(f"  Connect to {req.server.display_name}?")
    print(f"  Publisher: {req.server.publisher.name} "
          f"(verified={req.server.publisher.verified})")
    print(f"  Purpose:   {req.purpose}")
    print(f"  Scopes:    {req.requested_scopes}")
    print(f"  Pricing:   {req.server.pricing.model if req.server.pricing else 'unknown'}"
          f"{' (verified)' if req.server.pricing_verified else ' (vendor-claimed)'}")
    print(f"  Rating:    {req.server.rating.score if req.server.rating else 'n/a'}"
          f" ({req.server.rating.count if req.server.rating else 0} reviews)")
    print("=" * 60)
    choice = input("Approve? [y/N]: ").strip().lower()
    approved = choice in ("y", "yes")
    return ApprovalResponse(
        approved=approved,
        approved_scopes=req.requested_scopes if approved else [],
        duration=req.duration,
    )

async def main():
    pharos = PharosClient(
        registry_urls=["https://registry.pharos.dev"],
        agent_id="my-agent/0.1.0",
    )

    # 1. Search
    print("Searching for flight-booking MCP servers...")
    results = await pharos.search(
        text="book a flight to Tokyo",
        filter={"transport": ["http+sse", "streamable-http"], "publisher_verified": True},
        limit=5,
    )
    if not results:
        print("No servers found.")
        return

    best = results[0]
    print(f"Top result: {best.display_name} (score={best.pharos_score})")

    # 2. Request approval (SDK calls our render_cli callback)
    approval = await pharos.request_approval(
        server=best,
        purpose="Book a flight to Tokyo for the user's July 25 trip",
        requested_scopes=["flight_search"],
        requested_capabilities=["flight_search"],
        duration="session",
        selection_rationale="ranked #1 for flight_search; verified publisher",
        render=render_cli,
    )

    if not approval.approved or not approval.token:
        print("User declined. Exiting.")
        return

    # 3. Connect (ApprovalToken required — no bypass)
    client = await pharos.connect(approval.token)
    print(f"Connected to {best.display_name}.")

    # 4. Use
    tools = await client.list_tools()
    print(f"Available tools: {[t.name for t in tools]}")

    result = await client.call_tool(
        "flight_search",
        {"origin": "NYC", "destination": "TYO", "date": "2026-07-25"},
    )
    print(f"Result: {result.content}")

    # 5. Disconnect
    await client.close()
    pharos.revoke(approval.token)
    print("Disconnected.")

asyncio.run(main())
```

---

## 4. Structured filters (SPEC §6.3.1)

```python
results = await pharos.search(filter={
    "capabilities": ["flight_search", "flight_book"],
    "transport": ["http+sse", "streamable-http"],
    "publisher_verified": True,
    "min_rating": 4.0,
    "pricing_tier": ["free", "freemium"],
    "availability": ["mirrored", "native"],
    "data_residency": ["EU"],
    "protocol_versions": ["2025-03-26"],
}, limit=10)
```

---

## 5. Privacy mode (SPEC §10.8)

```python
# Filters only, no query.text — lower recall, leaks no free-text intent
pharos = PharosClient(
    registry_urls=["https://registry.pharos.dev"],
    agent_id="my-agent/0.1.0",
    privacy_mode=True,
)
results = await pharos.search(filter={"capabilities": ["flight_search"]})
```

Or blinded search (local embedding, no text leaves the device):
```python
embedding = await pharos.embed_locally("book a flight to Tokyo")
results = await pharos.search(query_embedding=embedding, limit=5)
```

---

## 6. Headless mode (SPEC §7.5)

For pipelines/CI where no user is present. **Scoped, not blanket** — novel servers are refused.

```python
pharos = PharosClient(
    registry_urls=["https://registry.pharos.dev"],
    agent_id="ci-pipeline/0.1.0",
    headless_mode=True,
    headless_allow_servers=["urn:pharos:acme.com:travel/flight-booking"],
    headless_allow_scopes=["flight_search"],
)

# This works (server on allow-list):
results = await pharos.search(filter={"capabilities": ["flight_search"]})
approval = await pharos.request_approval(
    server=results[0],
    purpose="automated flight search",
    requested_scopes=["flight_search"],
    requested_capabilities=["flight_search"],
    duration="session",
    selection_rationale="allow-listed server for CI",
    render=auto_approve_render,  # auto-approves allow-listed servers
)

# A novel server NOT on the allow-list → HeadlessApprovalRequired error
```

---

## 7. Multi-server plans (SPEC §7.1.1)

```python
from pharos_discovery import PlanApprovalRequest, PlanApprovalResponse

# Search for two servers
flight_results = await pharos.search("book a flight", limit=1)
expense_results = await pharos.search("file an expense report", limit=1)

steps = []
for card in flight_results + expense_results:
    steps.append(ApprovalRequest(
        server=card,
        purpose="Book travel and file expense",
        requested_scopes=["flight_search"] if "flight" in card.id else ["expense_create"],
        requested_capabilities=["flight_search"] if "flight" in card.id else ["expense_create"],
        duration="session",
        render_id=f"step-{card.id}",
        selection_rationale="part of travel-and-expense plan",
    ))

plan_response = await pharos.request_plan_approval(
    plan_summary="Book your Tokyo flight and create the expense report",
    steps=steps,
    render=render_plan_cli,  # async def render_plan_cli(req: PlanApprovalRequest) -> PlanApprovalResponse
)
# One consent act for both servers — mitigates consent fatigue
```

---

## 8. Registry failover (SPEC §8.5, H7)

```python
pharos = PharosClient(
    registry_urls=[
        "https://registry.pharos.dev",
        "https://registry-eu.pharos.dev",
        "https://registry-backup.pharos.dev",
    ],
    agent_id="my-agent/0.1.0",
    static_fallback_servers=[...],  # /etc/hosts-style fallback; approval gate still enforced
)
# On 503/504/timeout from [0], mark unhealthy 60s, try [1], etc.
```

---

## 9. Error handling

```python
from pharos_discovery import (
    NoServersFound, RegistryUnavailable, HeadlessApprovalRequired,
    ConnectionFailed, ScopeNotApproved, DiscoveryDegraded,
)

try:
    results = await pharos.search("book a flight", limit=5)
except NoServersFound:
    print("No matching servers. Try broadening your query.")
except RegistryUnavailable:
    print("All registries unavailable and no cache. Try later.")
except DiscoveryDegraded:
    print("Registries degraded — using cached/static fallback.")

try:
    result = await client.call_tool("flight_book", {"flight_id": "ABC123"})
except ScopeNotApproved:
    print("User didn't approve the flight_book scope. Re-prompt?")
    # Scope re-negotiation: rate-limited to 1 per server per session (§7.7)
```

---

## 10. Next steps

- **Full API reference:** `docs/api/PYTHON_API.md`
- **Security model:** `.guides/security/SECURITY_GUIDE.md`
- **Architecture:** `docs/technical/SYSTEM_ARCHITECTURE.md`
- **Conventions:** `.guides/backend/PYTHON_GUIDE.md`
- **Troubleshooting:** `docs/troubleshooting/COMMON_ISSUES.md`

---

*Phase 1 ships with `auth.type: "none"` and `"api_key"` only. OAuth (App Registration Inheritance) arrives in Phase 2 — see `docs/components/OAUTH_BROKERING.md`.*
