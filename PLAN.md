# Pharos Discovery — Development Plan

## Project: pharos-discovery
Dual-language SDK (Python + TypeScript) for discovering, approving, and connecting to MCP servers.

- **Python**: `pharos_discovery` (PyPI), pydantic v2, anyio async-first, httpx
- **TypeScript**: `@pharos/discovery` (npm), zod schemas, ESM+CJS dual build
- **Spec**: SPEC.md v0.4.0
- **Guides**: .guides/backend/PYTHON_GUIDE.md, .guides/backend/TYPESCRIPT_GUIDE.md

---

## Phase 1: Core SDK (Python + TypeScript in parallel)

- [x] T1: Bootstrap monorepo structure
- [ ] T2: Python SDK core types (pydantic models: Publisher, AuthSpec, ServerCard, ApprovalRequest/Response/Token, OAuthResult, RevocationResult, PlanApproval)
- [ ] T3: TypeScript SDK core types (zod schemas: same types as Python)
- [ ] T4: Python errors + cache layer
- [ ] T5: TypeScript errors + cache layer
- [ ] T6: Python registry adapter (PharosRegistryAdapter — search + get server card)
- [ ] T7: TypeScript registry adapter (same)
- [ ] T8: Python PharosClient (search, request_approval, connect, revoke)
- [ ] T9: TypeScript PharosClient (same)
- [ ] T10: Python approval engine + token signing (ed25519)
- [ ] T11: TypeScript approval engine + token signing (Web Crypto)
- [ ] T12: Python connection manager (HTTP+SSE + streamable-http transports)
- [ ] T13: TypeScript connection manager (same)
- [ ] T14: End-to-end integration tests (both SDKs)

## Phase 2: Advanced Features

- [ ] T15: MCP Registry adapter (mcp_official — client-side semantic re-rank)
- [ ] T16: Blocklist + key pinning (security/)
- [ ] T17: SSE events subscriber (/v1/events)
- [ ] T18: Consent store (append-only, signed)
- [ ] T19: Headless mode + static fallback servers
- [ ] T20: Plan approval (batch multi-server)

## Phase 3: Polish & Publish

- [ ] T21: Conformance test fixtures (SPEC §8.6 golden fixtures)
- [ ] T22: Python: build + publish to PyPI
- [ ] T23: TypeScript: build + publish to npm
- [ ] T24: Documentation site
