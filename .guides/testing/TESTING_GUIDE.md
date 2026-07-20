# Testing Guide — PHAROS Discovery

**Audience:** AI agents and contributors writing or running tests for either SDK.
**Source of truth:** `SPEC.md` v0.4.0, §8.6 (IDL + conformance), §7 (approval flow), §9 (transport), §10 (security), §13.5 (rate limiting).
**Companion:** `.guides/backend/PYTHON_GUIDE.md`, `.guides/backend/TYPESCRIPT_GUIDE.md`, `.guides/deployment/DEPLOYMENT_GUIDE.md`.

---

## 1. Testing strategy (three layers)

1. **Unit tests** — per-package, fast, hermetic. Mock the HTTP layer and transports. Cover happy path + error cases + edge cases for every public method.
2. **Integration tests** — per-package, against a **mock MCP registry** + **mock MCP server** (both in-process). Exercise the full search → approve → connect → call-a-tool flow without real network.
3. **Conformance tests** — **shared** between both SDKs, run from `conformance/` at repo root. Golden JSON fixtures + behavioral assertions. Both SDKs MUST pass. (SPEC §8.6)

**Phase 1 exit criterion (SPEC §15):** demo scripts in BOTH languages that search, approve, and call a tool on a **real** remote MCP server, end-to-end; conformance suite passes for both SDKs.

---

## 2. Python testing — pytest + pytest-asyncio

### 2.1 Setup

`packages/python/pyproject.toml`:
```toml
[project.optional-dependencies]
dev = [
  "pytest>=8",
  "pytest-asyncio>=0.23",
  "pytest-httpx>=0.30",   # mock httpx.AsyncClient
  "pytest-cov>=5",
  "ruff",
  "mypy",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"          # auto: async def tests run without @pytest.mark.asyncio
testpaths = ["tests"]
addopts = "--strict-markers --strict-config"
markers = [
  "conformance: shared conformance suite (SPEC §8.6)",
  "integration: integration tests against mock registry + mock MCP server",
  "slow: slow tests (real network or heavy compute)",
]
```

Run:
```bash
cd packages/python
uv run pytest                          # all tests
uv run pytest -m "not slow"            # skip slow
uv run pytest -m conformance           # conformance only
uv run pytest tests/unit/              # unit only
uv run pytest --cov=pharos_discovery --cov-report=term-missing
```

### 2.2 Async test patterns

`asyncio_mode = "auto"` means every `async def test_*` runs on the event loop automatically — no decorator needed. Use `anyio` primitives inside tests to match production code.

```python
import pytest
from pharos_discovery import PharosClient
from pharos_discovery.errors import ScopeNotApproved

async def test_search_returns_ranked_cards(mock_registry):
    pharos = PharosClient(registry_urls=[mock_registry.url], agent_id="test/0.1")
    results = await pharos.search(text="book a flight", limit=5)
    assert len(results) <= 5
    assert all(r.pharos_score is not None or r.source_score is not None for r in results)
    # pharos_score is relevance, NOT trust (SPEC §6.3)
    assert results[0].pharos_score >= results[-1].pharos_score if len(results) > 1 else True

async def test_connect_without_approval_raises(mock_registry, sample_card):
    pharos = PharosClient(registry_urls=[mock_registry.url], agent_id="test/0.1")
    with pytest.raises(ValueError, match="ApprovalToken"):
        await pharos.connect(approval=None)  # no bypass (SPEC §4.2, §10.7.1)

async def test_call_tool_outside_scope_raises(mock_mcp_server, approved_client):
    # approved_client has approved_scopes=["flight_search"] only
    with pytest.raises(ScopeNotApproved):
        await approved_client.call_tool("flight_book", {"origin": "NYC"})
```

### 2.3 Mocking HTTP — `pytest-httpx`

```python
import pytest
from pytest_httpx import HTTPXMock

async def test_search_429_falls_back_to_cache(httpx_mock: HTTPXMock, cached_card):
    httpx_mock.add_response(
        url="https://registry.pharos.dev/v1/search",
        status_code=429,
        headers={"Retry-After": "1"},
    )
    pharos = PharosClient(
        registry_urls=["https://registry.pharos.dev"],
        agent_id="test/0.1",
        cache_ttl_seconds=300,
    )
    # pre-seed cache
    await pharos._cache.put(cached_card.id, cached_card)

    results = await pharos.search(text="flight", limit=5)
    # falls back to cache, surfaces registry_degraded (SPEC §13.5)
    assert len(results) == 1
    assert results[0].id == cached_card.id
```

### 2.4 Mock MCP registry + mock MCP server (in-process)

Build small `anyio`-based fixtures that speak the §6 registry API and the MCP 2025-03-26 wire protocol (JSON-RPC 2.0 over stdio/HTTP+SSE). Reuse across integration tests.

```python
# tests/integration/conftest.py
import pytest
from tests.helpers.mock_registry import MockRegistry
from tests.helpers.mock_mcp_server import MockMCPServer

@pytest.fixture
async def mock_registry():
    reg = MockRegistry(port=0)
    await reg.start()
    yield reg
    await reg.stop()

@pytest.fixture
async def mock_mcp_server():
    srv = MockMCPServer(port=0, tools=[
        {"name": "flight_search", "description": "Search flights", "inputSchema": {...}},
        {"name": "flight_book", "description": "Book a flight", "inputSchema": {...}},
    ])
    await srv.start()
    yield srv
    await srv.stop()

@pytest.fixture
def auto_approve_render():
    """Host render callback that auto-approves with requested scopes (test only)."""
    async def render(req):
        from pharos_discovery import ApprovalResponse
        return ApprovalResponse(
            approved=True,
            approved_scopes=req.requested_scopes,
            duration=req.duration,
        )
    return render
```

### 2.5 Full integration flow (Python)

```python
async def test_full_discovery_flow(mock_registry, mock_mcp_server, auto_approve_render):
    mock_registry.add_server_card(card_for(mock_mcp_server))
    pharos = PharosClient(registry_urls=[mock_registry.url], agent_id="test/0.1")

    results = await pharos.search(text="book a flight", limit=5)
    assert len(results) == 1

    approval = await pharos.request_approval(
        server=results[0],
        purpose="test",
        requested_scopes=["flight_search"],
        requested_capabilities=["flight_search"],
        duration="session",
        selection_rationale="only result; verified publisher",
        render=auto_approve_render,
    )
    assert approval.approved

    client = await pharos.connect(approval)
    tools = await client.list_tools()
    assert any(t.name == "flight_search" for t in tools)

    result = await client.call_tool("flight_search", {"origin": "NYC", "destination": "TYO"})
    assert result.is_error is False

    await client.close()
    pharos.revoke(approval)
```

---

## 3. TypeScript testing — vitest

### 3.1 Setup

`packages/typescript/package.json`:
```json
{
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest",
    "test:coverage": "vitest run --coverage"
  },
  "devDependencies": {
    "vitest": "^1",
    "@vitest/coverage-v8": "^1",
    "msw": "^2"
  }
}
```

`vitest.config.ts`:
```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["test/**/*.test.ts"],
    coverage: { provider: "v8", reporter: ["text", "html"], exclude: ["test/**", "dist/**"] },
    setupFiles: ["test/setup.ts"],
  },
});
```

Run:
```bash
cd packages/typescript
pnpm test
pnpm test -- --grep conformance
pnpm test:coverage
```

### 3.2 Async test patterns

```ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { PharosClient } from "../src";
import { ScopeNotApproved } from "../src/errors";
import { mockRegistry, mockMcpServer, autoApproveRender } from "./helpers";

describe("PharosClient.search", () => {
  it("returns ranked ServerCards", async () => {
    const reg = await mockRegistry();
    try {
      const pharos = new PharosClient({ registryUrls: [reg.url], agentId: "test/0.1" });
      const results = await pharos.search({ text: "book a flight", limit: 5 });
      expect(results.length).toBeLessThanOrEqual(5);
      for (const r of results) {
        expect(r.pharosScore ?? r.sourceScore).toBeDefined();
      }
    } finally {
      await reg.stop();
    }
  });

  it("connect() without ApprovalToken throws", async () => {
    const pharos = new PharosClient({ registryUrls: ["https://example.test"], agentId: "test/0.1" });
    await expect(pharos.connect(null as any)).rejects.toThrow(/ApprovalToken/);
  });
});

describe("MCPClient.callTool scope enforcement", () => {
  it("throws ScopeNotApproved for out-of-scope tool", async () => {
    const client = await approvedClient({ approvedScopes: ["flight_search"] });
    await expect(client.callTool("flight_book", { origin: "NYC" })).rejects.toThrow(ScopeNotApproved);
  });
});
```

### 3.3 Mocking HTTP — MSW (Mock Service Worker)

```ts
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

export const registryHandlers = [
  http.post("https://registry.pharos.dev/v1/search", async ({ request }) => {
    const body = await request.json() as any;
    return HttpResponse.json({
      results: [sampleCard(body.query.text)],
      referrals: [],
      pagination: { next_cursor: null, has_more: false },
    });
  }),
  http.get("https://registry.pharos.dev/v1/servers/:id", ({ params }) =>
    HttpResponse.json(sampleCardById(params.id as string)),
  ),
];

export const mockRegistryServer = () => setupServer(...registryHandlers);
```

```ts
// test/setup.ts
import { beforeAll, afterAll, afterEach } from "vitest";
import { mockRegistryServer } from "./helpers/msw";

const server = mockRegistryServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
```

### 3.4 Full integration flow (TypeScript)

```ts
import { describe, it, expect } from "vitest";
import { PharosClient } from "../src";

describe("full discovery flow", () => {
  it("search → approve → connect → callTool → close", async () => {
    const pharos = new PharosClient({ registryUrls: ["https://registry.pharos.dev"], agentId: "test/0.1" });
    const results = await pharos.search({ text: "book a flight", limit: 5 });
    expect(results.length).toBe(1);

    const approval = await pharos.requestApproval({
      server: results[0],
      purpose: "test",
      requestedScopes: ["flight_search"],
      requestedCapabilities: ["flight_search"],
      duration: "session",
      selectionRationale: "only result; verified publisher",
      render: autoApproveRender,
    });
    expect(approval.approved).toBe(true);

    const client = await pharos.connect(approval);
    const tools = await client.listTools();
    expect(tools.some((t) => t.name === "flight_search")).toBe(true);

    const result = await client.callTool("flight_search", { origin: "NYC", destination: "TYO" });
    expect(result.isError).toBe(false);

    await client.close();
    pharos.revoke(approval);
  });
});
```

---

## 4. Mock MCP servers (shared design)

Both SDKs need mock MCP servers for integration tests. The mock must speak MCP 2025-03-26 over JSON-RPC 2.0:

- **HTTP+SSE** (Phase 1): a small HTTP server that accepts POST with JSON-RPC bodies, responds to `initialize` + `notifications/initialized` + `tools/list` + `tools/call`, and exposes an SSE endpoint for server-to-client messages.
- **Streamable HTTP** (Phase 1): single endpoint, POST JSON-RPC, inline or SSE-upgraded response.
- **stdio** (Phase 2): a subprocess reading newline-delimited JSON-RPC from stdin, writing to stdout.

**Mock MCP server contract (implement identically in both helpers):**

```
initialize       → { protocolVersion: "2025-03-26", capabilities: {tools:{}}, serverInfo: {name, version} }
notifications/initialized → {} (no response)
tools/list       → { tools: [{ name, description, inputSchema }, ...] }
tools/call       → { content: [{type:"text", text:"..."}], isError: false }
ping             → {} (pong)
```

Provide fixtures for:
- A server with `flight_search` + `flight_book` tools (happy path)
- A server that claims `payment_refund` capability but doesn't expose the tool (H13 `CapabilityMismatch`)
- A server that disconnects mid-`initialize` (H8 `ConnectionFailed`)
- A server with `auth.type: "oauth"` + `app_registration` block (Phase 2 OAuth flow; mock returns a signed `confirmation_jwt`)

---

## 5. Conformance suite (shared — SPEC §8.6)

`conformance/` at repo root:

```
conformance/
├── fixtures/
│   ├── server_card_acme_flight.json      # canonical ServerCard
│   ├── server_card_ard_sourced.json       # ARD-sourced (pharos_score: null, source_score: 87)
│   ├── server_card_deprecated.json        # status: deprecated, successor_id set
│   ├── search_request_basic.json
│   ├── search_response_basic.json
│   ├── approval_token_basic.json
│   ├── oauth_result_server_side.json      # access_token: null, auth_held_by: "mcp_server"
│   └── ...
├── assertions/
│   ├── 01_search.yaml                     # given request, expect response shape
│   ├── 02_approval_gate.yaml              # connect() without token raises
│   ├── 03_scope_enforcement.yaml          # call_tool outside approved_scopes raises
│   ├── 04_capability_mismatch.yaml        # claimed cap with no backing tool → warning
│   ├── 05_headless_refuses_novel.yaml     # headless_mode + novel server → HeadlessApprovalRequired
│   ├── 06_ard_score_not_normalized.yaml   # ARD score preserved as source_score, pharos_score null
│   ├── 07_oauth_confirmation_jwt.yaml     # SDK verifies confirmation_jwt via endpoints.jwks
│   └── ...
├── python/                                # pytest runner
│   └── test_conformance.py
└── typescript/                            # vitest runner
    └── conformance.test.ts
```

**Assertion format (shared, YAML):**

```yaml
# conformance/assertions/03_scope_enforcement.yaml
name: scope_enforcement
spec_ref: "§7.4"
given:
  approval_token:
    approved_scopes: ["flight_search"]
    approved_capabilities: ["flight_search"]
when:
  call_tool:
    name: "flight_book"
    args: {origin: "NYC"}
then:
  raises: "SCOPE_NOT_APPROVED"
```

**Python runner** (`conformance/python/test_conformance.py`): parametrized over `assertions/*.yaml`, loads fixtures, exercises the SDK, asserts.

**TS runner** (`conformance/typescript/conformance.test.ts`): same YAML files, same fixtures, same assertions via `vitest`.

Both runners MUST pass on every PR (CI: `conformance.yml`). A fixture change is a coordinated change across both SDKs.

---

## 6. Test categories & required coverage

For every public method, cover:

| Category | Examples |
|----------|----------|
| **Happy path** | search returns results; approve → connect → call_tool succeeds |
| **Error cases** | `RegistryUnavailable`, `NoServersFound`, `ConnectionFailed`, `SCOPE_NOT_APPROVED`, `HeadlessApprovalRequired`, `OAuthUnavailable`, `RetryableOAuthFailure`, `UNSUPPORTED_FILTER` |
| **Edge cases** | empty results (one broadened retry, then `NoServersFound`); token expiry; pin mismatch → quarantine; WHOIS change → re-verify; `trust_on_use` decay after 7 days; consent fatigue >5 novel; `PlanApproval` partial deny |
| **Security invariants** | no `connect_without_approval` path; `client_secret` never in any type/log; `query.text` never logged at user level; OAuth `confirmation_jwt` verified before `authorized=true` |
| **Transport** | HTTP+SSE + Streamable HTTP (Phase 1); stdio (Phase 2); capability mismatch post-connect (H13); heartbeat/health-check → `ConnectionLost` |
| **Rate limiting** | 429 honors `Retry-After`; exponential backoff with full jitter; fallback to cache + `registry_degraded`; both exhausted → `RegistryUnavailable` (never silent empty) |
| **Federation** | `auto` dedup by canonical ID; `referrals` max depth; cross-registry score NOT compared (ARD `source_score` preserved, `pharos_score` null) |

---

## 7. Property-based / fuzz tests (recommended)

For the `ServerCard` parser and IDL-generated models, use property-based tests to catch schema drift:

**Python (`hypothesis`):**
```python
from hypothesis import given, strategies as st
from pharos_discovery.models import ServerCard

@given(st.from_type(ServerCard))
def test_server_card_roundtrip(card):
    # serialize → parse → equal
    parsed = ServerCard.model_validate_json(card.model_dump_json())
    assert parsed == card
```

**TypeScript (`fast-check`):**
```ts
import { fc, testProp } from "fast-check";
import { ServerCardSchema } from "../src/models/serverCard";

testProp("ServerCard roundtrip", [fc.sample(ServerCardSchema.arbitrary)], (card) => {
  const parsed = ServerCardSchema.parse(JSON.parse(JSON.stringify(card)));
  return parsed.id === card.id;
});
```

---

## 8. End-to-end against a real server (Phase 1 exit)

SPEC §15 exit criteria require demo scripts in BOTH languages that search, approve, and call a tool on a **real remote MCP server**. Keep these in a `pharos-discovery-quickstart` repo:

```
pharos-discovery-quickstart/
├── python/
│   └── demo.py      # search → approve (CLI render) → connect → call_tool → print result
└── typescript/
    └── demo.ts      # same flow
```

These are NOT unit tests — they hit the real Pharos Registry + a real public MCP server. Mark `@pytest.mark.slow` / `it.skip.ci` so CI doesn't run them on every push; run nightly or on tag.

---

## 9. CI integration (SPEC §8.6 — see `.guides/deployment/DEPLOYMENT_GUIDE.md`)

- `ci.yml`: matrix test both packages (Python 3.10/3.11/3.12 × Node 20/22).
- `conformance.yml`: shared conformance suite against both SDKs — both MUST pass.
- `idl-drift` job: regenerate from IDL, fail if `git diff --exit-code packages/` shows drift.
- Coverage: target meaningful coverage, not 100% line coverage for its own sake (SPEC PHAROS_DISCOVERY_GUIDE §9). Focus on public API + security invariants.

---

## 10. Test conventions checklist

- [ ] Every new public method has unit tests in BOTH packages.
- [ ] Error cases covered (typed exceptions/errors, not just happy path).
- [ ] Edge cases from §7.1.1 covered (empty results, token expiry, pin mismatch, WHOIS change).
- [ ] Security invariants have explicit tests (`connect` without token raises; `client_secret` grep; `query.text` not logged; `confirmation_jwt` verified).
- [ ] Conformance fixtures updated when a public-surface shape changes.
- [ ] Integration tests use mock registry + mock MCP server, not real network.
- [ ] E2E against real server is separate (slow, nightly), not in PR CI.
- [ ] Property-based tests for IDL-generated models catch schema drift.

---

*Next: `.guides/deployment/DEPLOYMENT_GUIDE.md` for CI wiring; `docs/troubleshooting/COMMON_ISSUES.md` for common test failures.*
