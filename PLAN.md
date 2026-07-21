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
- [x] T2: Python SDK core types (pydantic models, 25 tests)
- [x] T3: TypeScript SDK core types (zod schemas, 8 tests)
- [x] T4: Python errors + cache layer (TRON-direct, 39 tests)
- [x] T5: TypeScript errors + cache layer (OpenCode, 25 tests)
- [x] T6: Python registry adapter (OpenCode + TRON verify, 11 tests)
- [x] T7: TypeScript registry adapter (OpenCode, 11 tests)
- [x] T8: Python PharosClient (search, connect_and_approve, revoke, check_scope) — 15 tests
- [x] T9: TypeScript PharosClient (same) — 15 tests
- [x] T10: Python approval engine + token signing (HMAC-SHA256, @reviewer invoked) — 24 tests
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
