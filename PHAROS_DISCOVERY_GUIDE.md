# PHAROS Discovery Development Guide for AI Agents

**Version:** 1.0
**Last Updated:** 2026-07-19
**Purpose:** Master guide for AI agents working on PHAROS Discovery — routes to specific guides as needed

---

## 🎯 Overview

This guide helps you navigate PHAROS Discovery's documentation efficiently. **This file should be attached to every session** where an AI agent is helping develop this project. From here, fetch specific guides as needed to save tokens.

PHAROS Discovery is a **provider-agnostic, embeddable client SDK** that lets any AI agent (Claude, GPT, DeepSeek, Gemini, xAI, Zap, custom) discover, evaluate, and connect to MCP (Model Context Protocol) servers at runtime — with user approval baked in as a first-class, non-bypassable client-side contract. It ships as **two parallel first-party libraries** from a single TypeSpec IDL: `pharos-discovery` (Python 3.10+, PyPI) and `@pharos/discovery` (TypeScript, Node 20+ and browser, npm). The canonical spec is `SPEC.md` v0.4.0 (2127 lines); this guide is the navigation layer on top of it.

---

## ⚡ First Action: Read the Repo

Before doing anything else, read this repository to get the latest instructions,
flow, and best coding practices. The `.guides/` and `docs/` directories contain
the current standards — they may have been updated since this file was last
modified. The codebase itself is the source of truth for patterns and conventions.

> **This entire guide is a starting point.** The `.guides/` and `docs/` directories
> contain the actual, up-to-date standards. Always check them before assuming
> this file's contents are current.

**Primary source of truth:** `SPEC.md` (v0.4.0). Every class signature, field, flow, and decision in this project traces to a SPEC section. Reference SPEC section numbers (e.g. "See SPEC §7.4") when implementing.

---

## 📋 Project Structure

```
pharos-discovery/
├── PHAROS_DISCOVERY_GUIDE.md   # ← YOU ARE HERE (master orchestrator)
├── SPEC.md                      # Canonical spec (v0.4.0, 2127 lines) — source of truth
├── template_master_guide.md     # Template this guide followed
│
├── .guides/                     # Your decision-making guides (cascade system)
│   ├── architecture/
│   │   └── OVERVIEW.md          # Dual-SDK architecture, component relationships, discovery flow
│   ├── backend/
│   │   ├── PYTHON_GUIDE.md      # Python SDK: src layout, asyncio, pydantic, httpx
│   │   └── TYPESCRIPT_GUIDE.md  # TypeScript SDK: package structure, ESM/CJS dual build, fetch
│   ├── deployment/
│   │   └── DEPLOYMENT_GUIDE.md  # uv/hatch → PyPI, tsup → npm, GitHub Actions dual-publish
│   ├── security/
│   │   └── SECURITY_GUIDE.md    # OAuth brokering, consent, PKCE, token storage, approval contract
│   └── testing/
│       └── TESTING_GUIDE.md     # pytest + pytest-asyncio, vitest, mock MCP servers
│
├── docs/                        # Technical reference documentation
│   ├── technical/
│   │   └── SYSTEM_ARCHITECTURE.md  # ADR-style decisions, dual-SDK diagram, flows
│   ├── api/
│   │   ├── PYTHON_API.md        # Python public API: classes, methods, types, examples
│   │   └── TYPESCRIPT_API.md    # TypeScript public API: classes, methods, types, examples
│   ├── components/
│   │   ├── DISCOVERY_FLOW.md    # search → approve → connect flow, ServerCard schema, ranking
│   │   └── OAUTH_BROKERING.md   # App Registration Inheritance, PKCE, token lifecycle
│   ├── examples/
│   │   ├── QUICKSTART_PYTHON.md
│   │   └── QUICKSTART_TYPESCRIPT.md
│   └── troubleshooting/
│       └── COMMON_ISSUES.md
│
├── packages/                    # (planned) monorepo with two SDK packages
│   ├── python/                  # pharos-discovery (PyPI)
│   │   ├── src/pharos_discovery/
│   │   ├── tests/
│   │   └── pyproject.toml
│   └── typescript/              # @pharos/discovery (npm)
│       ├── src/
│       ├── test/
│       └── package.json
│
└── idl/                         # TypeSpec IDL — single source for both SDKs (§8.6)
    └── typespec/                # .tsp files defining ServerCard, search, approval, OAuth
```

> **Note:** The `packages/` and `idl/` directories are the target layout. Until the repo is bootstrapped, treat SPEC §8 (SDK Design) and the `.guides/backend/` guides as the authoritative description of intended structure.

---

## 🧭 When to Use Which Guide

### Quick Decision Tree

```
User/TRON gives a task
    ↓
Read THIS file (PHAROS_DISCOVERY_GUIDE.md)
    ↓
Read SPEC.md (or the relevant §section) + the repo to check for updates
    ↓
Is this a development task (not just a question)?
    YES → Determine task type:
    ├─ Architecture / cross-cutting decision? → .guides/architecture/OVERVIEW.md
    ├─ Python SDK work?                       → .guides/backend/PYTHON_GUIDE.md
    ├─ TypeScript SDK work?                   → .guides/backend/TYPESCRIPT_GUIDE.md
    ├─ BOTH SDKs must change (IDL-driven)?    → .guides/architecture/ + BOTH backend guides
    ├─ Build / publish / CI?                  → .guides/deployment/DEPLOYMENT_GUIDE.md
    ├─ OAuth / consent / security model?      → .guides/security/SECURITY_GUIDE.md
    └─ Tests / conformance suite?             → .guides/testing/TESTING_GUIDE.md
```

### Guide Reference Table

| Working On | Fetch This Guide | What You'll Learn |
|------------|------------------|-------------------|
| 🏗️ Architecture | `.guides/architecture/OVERVIEW.md` | Dual-SDK design, four SDK layers, discovery flow, what lives where (client vs registry) |
| 🐍 Python SDK | `.guides/backend/PYTHON_GUIDE.md` | `src/` layout, asyncio + anyio, pydantic models, httpx, type hints, embedding model |
| 🟦 TypeScript SDK | `.guides/backend/TYPESCRIPT_GUIDE.md` | Package structure, ESM/CJS dual build via tsup, fetch API, type defs, browser compat |
| 🐋 Deployment | `.guides/deployment/DEPLOYMENT_GUIDE.md` | uv/hatch → PyPI, tsup → npm, GitHub Actions dual-publish, IDL codegen in CI |
| 🔒 Security | `.guides/security/SECURITY_GUIDE.md` | OAuth App Registration Inheritance, PKCE, token isolation, approval contract, blocklist |
| 🧪 Testing | `.guides/testing/TESTING_GUIDE.md` | pytest + pytest-asyncio, vitest, mock MCP servers, conformance suite (§8.6) |

### Deep-dive docs (fetch from `docs/` when needed)

| Topic | Doc |
|-------|-----|
| ADR-style architecture decisions, diagrams | `docs/technical/SYSTEM_ARCHITECTURE.md` |
| Python public API reference | `docs/api/PYTHON_API.md` |
| TypeScript public API reference | `docs/api/TYPESCRIPT_API.md` |
| search → approve → connect flow, ServerCard schema, ranking | `docs/components/DISCOVERY_FLOW.md` |
| OAuth brokering, App Registration Inheritance, PKCE, token lifecycle | `docs/components/OAUTH_BROKERING.md` |
| Step-by-step Python usage | `docs/examples/QUICKSTART_PYTHON.md` |
| Step-by-step TypeScript usage | `docs/examples/QUICKSTART_TYPESCRIPT.md` |
| Common problems and solutions | `docs/troubleshooting/COMMON_ISSUES.md` |

---

## 🏗️ Tech Stack

### Core (both SDKs)
- **IDL:** [TypeSpec](https://typespec.io/) — single source of truth for `ServerCard`, `POST /v1/search`, `GET /v1/servers/{id}`, `POST /v1/approve`, `ApprovalRequest`/`ApprovalResponse`/`ApprovalToken`/`OAuthResult`, `OAuthFlowHandler` (SPEC §8.6). JSON Schema fallback acceptable for Phase 0.
- **Wire protocol:** MCP (Model Context Protocol) 2025-03-26 over JSON-RPC 2.0 — stdio, HTTP+SSE, Streamable HTTP transports (SPEC §9).
- **Registry API:** Pharos Discovery HTTP API (`/v1/...`, `application/json`, `X-Pharos-Version` header) — SPEC §6.
- **Auth:** OAuth 2.1 via App Registration Inheritance + MCP Apps inline OAuth (Phase 2, SPEC §17); `none` + `api_key` in Phase 1.

### Python SDK (`pharos-discovery`)
- **Language:** Python 3.10+ (3.11+ recommended)
- **Async:** `anyio` (async-first; no hard LLM-client dependency)
- **Models:** `pydantic` v2 (generated from IDL)
- **HTTP:** `httpx` (async client)
- **Crypto:** `cryptography` (ed25519 signature verification)
- **Embedding (MCP-Registry adapter):** `all-MiniLM-L6-v2` ONNX (~22 MB, 384-dim, MIT) for client-side re-ranking (SPEC §11.3)
- **Build/pack:** `uv` + `hatchling` → PyPI
- **Tests:** `pytest` + `pytest-asyncio`

### TypeScript SDK (`@pharos/discovery`)
- **Language:** TypeScript 5.x, strict
- **Runtime:** Node 20+ and modern browsers (no DOM dependency; approval UX is host-supplied)
- **Build:** `tsup` — ESM + CJS dual build
- **HTTP:** `fetch` (web standard; Node 20+ native undici)
- **Models:** IDL-generated TS types + zod for runtime validation
- **Crypto:** Web Crypto API (`SubtleCrypto`) for ed25519 verification; Node `crypto` fallback
- **Build/pack:** `tsup` + `tsc --emitDeclarationOnly` → npm
- **Tests:** `vitest`

### Infrastructure
- **Monorepo:** single repo, two packages (`packages/python`, `packages/typescript`)
- **CI/CD:** GitHub Actions — matrix builds (Python 3.10/3.11/3.12 × Node 20/22), IDL codegen step, dual publish on tag
- **Conformance:** shared conformance test suite (golden fixtures + behavioral assertions) runs against both SDKs (SPEC §8.6)
- **Quickstart repo:** `pharos-discovery-quickstart` — Python + TS example agents doing end-to-end search → approve → connect → call-a-tool

---

## 🚀 Development Workflow for AI Agents

Follow this workflow for ALL development tasks:

### 1. Understand the Request

**Read the prompt carefully:**
- What is the specific task?
- Which SDK (Python, TypeScript, or both via IDL)?
- Which SPEC section governs it?
- What is the phase? (Phase 1 = MVP; Phase 2 = stdio + ARD + OAuth; etc. — SPEC §15)

### 2. Read the Repo for Latest Standards

Check `.guides/` and `docs/` and `SPEC.md` for the governing section. The codebase may have evolved — always work from the latest patterns. **SPEC.md is the single source of truth** for behavior; the guides are navigation.

### 3. Determine Task Type & Which SDK(s)

- **IDL-driven change** (touches `ServerCard`, request/response shapes, new methods) → change the TypeSpec IDL first, regenerate both SDKs, update both `.guides/backend/` guides, update both `docs/api/` docs. **Never hand-edit one SDK's public surface without the other** (SPEC §8.6 — this is how v0.2 drifted).
- **Python-only implementation** (transport shim, keychain, file paths) → `.guides/backend/PYTHON_GUIDE.md`.
- **TypeScript-only implementation** (browser shim, DOM-less fetch, tsup config) → `.guides/backend/TYPESCRIPT_GUIDE.md`.
- **Cross-cutting** (approval flow, OAuth, security) → start at `.guides/architecture/OVERVIEW.md`, then the relevant backend + security guides.

### 4. Load the Appropriate Guide

Based on the task type, load the corresponding guide from `.guides/`. The guide will tell you the specific patterns, conventions, and rules to follow.

### 5. Read Technical Docs (As Needed)

- ADR-style decisions & diagrams → `docs/technical/SYSTEM_ARCHITECTURE.md`
- Public API surfaces → `docs/api/PYTHON_API.md`, `docs/api/TYPESCRIPT_API.md`
- Component behavior → `docs/components/DISCOVERY_FLOW.md`, `docs/components/OAUTH_BROKERING.md`
- Worked examples → `docs/examples/`

### 6. Examine Existing Code

Before proposing a solution, look at existing patterns in the codebase:
- How are similar features implemented in the *other* SDK? (mirror the structure)
- What naming conventions are used? (snake_case Python, camelCase TS — SPEC §8.1/§8.2)
- Are IDL-generated types being extended or hand-written?

### 7. Security Review (CRITICAL)

Before writing any code, evaluate whether the change introduces security risks. The Pharos security model is non-trivial — **read `.guides/security/SECURITY_GUIDE.md` and the relevant SPEC §10 / §17 sections first.**

- [ ] Does this change touch the **approval gate**? (SPEC §7, §10.7.1 — must remain non-bypassable for conformant SDK-using agents; no `connect_without_approval` escape hatch)
- [ ] Does this change touch **OAuth**? (SPEC §17 — secret isolation: `client_secret` NEVER in registry/agent/SDK; tokens NEVER reach agent under App Registration Inheritance)
- [ ] Does this change touch **token / consent storage**? (SPEC §10.4 — append-only, signed consent store)
- [ ] Does this change touch **publisher key pinning**? (SPEC §9.3, §10.9 — TTL-bound, WHOIS-change-triggered re-verification)
- [ ] Does this change touch **query privacy**? (SPEC §10.8 — `query.text` MUST NOT be logged at user level; `privacy_mode` and `query.embedding` paths)
- [ ] Does this change introduce new attack surface? (new endpoints, new egress, new user input)
- [ ] Does this change handle sensitive data? (PII in queries, tokens, secrets, `user_id_hash`)

**If YES to any of the above:**
- Design the solution to preserve the SPEC's security properties exactly.
- For OAuth/consent changes, reference SPEC §17 and §10.5 line-by-line.
- Flag the finding to Christopher and get express approval before implementation if it changes a security invariant.
- Do NOT proceed with an insecure implementation and "fix it later."

### 8. Implement

Follow the patterns you found. Adhere to the guides and the IDL. **For any public-surface change, edit the IDL and regenerate — do not hand-diverge the two SDKs.** Only transport/platform shims are hand-written (SPEC §8.6).

### 9. Write Tests

- **Unit tests are required** for all new functionality, in BOTH SDKs.
- Follow `.guides/testing/TESTING_GUIDE.md` (pytest + pytest-asyncio for Python; vitest for TS).
- **Conformance suite:** any change to a golden fixture or behavioral assertion must update the shared conformance suite (SPEC §8.6) — both SDKs must still pass.
- Cover: happy path, error cases (`SCOPE_NOT_APPROVED`, `ConnectionFailed`, `RegistryUnavailable`, `HeadlessApprovalRequired`), edge cases (empty results, token expiry, pin mismatch, WHOIS change).
- Run the existing test suite in both packages to verify nothing broke.

### 10. Commit

Conventional Commits:

```
<type>(<scope>): <short summary>

<optional body with details>
```

| Type | When |
|------|------|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Code change with no behavior change |
| `test` | Adding or fixing tests |
| `docs` | Documentation only |
| `idl` | TypeSpec IDL change (regenerates both SDKs) |
| `chore` | Build, CI, tooling |

**Scope** is typically `python`, `typescript`, `idl`, `conformance`, `docs`, or `ci`. Example: `idl(approval): add PlanApproval type (SPEC §7.1.1)`.

### 11. Verify

- [ ] Does it build in both packages? (`uv run hatch build` for Python; `pnpm build` for TS)
- [ ] Do tests pass in both packages?
- [ ] Does the conformance suite pass for both SDKs?
- [ ] Is the diff clean? (no debug code, no unrelated changes, no hand-divergence between SDKs)
- [ ] Does it follow the IDL-first pattern? (public surface generated, not hand-written)
- [ ] Was the security review completed? (approval gate intact, secret isolation intact, etc.)
- [ ] Is documentation updated? (SPEC section referenced; `docs/api/` and relevant `docs/components/` updated if behavior changed)

---

## 🔐 Key Conventions

- **IDL-first.** The TypeSpec IDL is the source of truth for both SDKs' public surfaces. Hand-written code is limited to transport adapters (anyio vs Node async) and platform shims (keychain, file paths). (SPEC §8.6)
- **Naming:** `snake_case` for Python (e.g. `pharos.search(text=...)`, `request_approval`), `camelCase` for TypeScript (e.g. `pharos.search({ text })`, `requestApproval`). The IDL defines the canonical field names; emitters produce language-idiomatic variants.
- **Async-first.** Both SDKs are async-first: `async def` in Python (anyio), `async`/`await` + `Promise` in TS. No sync wrappers in the public API.
- **Consent is non-negotiable (for conformant SDK-using agents).** `pharos.connect()` requires a valid `ApprovalToken`; there is no bypass API. `headless_mode` is a scoped, allow-listed, loudly-logged mode — NOT a blanket opt-out (SPEC §4.2, §7.5).
- **Secret isolation.** `client_secret` NEVER appears in the registry, agent, or SDK. `ServerCard.auth` MUST NOT carry `client_secret`. Under App Registration Inheritance, tokens NEVER reach the agent — the MCP server proxies tool calls (SPEC §10.5, §17).
- **Error handling:** typed errors / exceptions with SPEC error codes (`SCOPE_NOT_APPROVED`, `HeadlessApprovalRequired`, `RegistryUnavailable`, `ConnectionFailed`, `OAuthUnavailable`, `UNSUPPORTED_FILTER`, etc. — SPEC §6.13, §7, §9.5, §17.5.1).
- **Logging:** structured; every `tools/call` logged locally with redacted sensitive params; consent store is append-only and signed (SPEC §7.6, §10.4).
- **Caching:** `ServerCard` cache TTL 300s (default); blocklist TTL 60s; conditional requests via ETag/`If-None-Match` (SPEC §8.5, §10.3).
- **Registry failover:** `registry_urls` is an ordered failover list; 503/504/timeout → next registry, 60s blackout, re-probe (SPEC §8.5 H7).
- **Timeouts (H8):** `initialize_timeout` 10s, `tool_call_timeout` 30s, `heartbeat_interval` 30s (HTTP/SSE), `health_check_interval` 60s, `oauth_timeout` 120s, `approval_timeout` 300s (SPEC §8.5).

---

## ⚠️ Common Pitfalls

- **Hand-diverging the two SDKs.** The v0.2 code samples already drifted (Python took `text`/`filter` as separate kwargs; TS took a single options object). Any public-surface change MUST go through the IDL. If you find yourself editing one SDK's public API without the IDL, stop. (SPEC §8.6)
- **Forgetting to @-reference the GUIDE file.** Every agent prompt must include `@PHAROS_DISCOVERY_GUIDE.md` or the agent loses project context.
- **Treating `headless_mode` as a consent bypass.** It is not. It requires an explicit `headless_allow_servers` list and `headless_allow_scopes` set; it refuses novel servers; it is loudly logged; it is mutually exclusive with `trust_on_use`. (SPEC §7.5)
- **Adding a `connect_without_approval` escape hatch.** Do not. The approval gate is a client-side contract for conformant SDK-using agents (SPEC §4.2, §10.7.1). Server-side enforcement of `ApprovalToken` is a *future* protocol extension, not something to hack in now.
- **Putting `client_secret` in a `ServerCard` or `pharos.json`.** Never. It lives only in the MCP server's server-side config. `ServerCard.auth.app_registration` carries metadata only. (SPEC §10.5, §17.2)
- **Letting the agent see an OAuth token.** Under App Registration Inheritance the `OAuthResult` carries a signed *confirmation* (JWT), not a token. `access_token`/`refresh_token` are `None` when `secret_handling == "server_side"`. (SPEC §8.3, §17.4)
- **Skipping security review.** Always run through the security checklist before implementing anything that touches approval, OAuth, storage, keys, or egress. Fixing a vulnerability after it's shipped is 10x harder.
- **No tests for new code.** Unit tests are not optional, and they must be written for BOTH SDKs. If existing tests don't cover the new path, add tests.
- **Comparing `pharos_score` across registries.** It is illegal by spec to compare an ARD `score` to a Pharos `pharos_score` — different ranking functions. ARD-sourced results have `pharos_score = None`. (SPEC §11.4)
- **Logging `query.text` at user level.** Forbidden. Aggregate anonymized logging only. Use `privacy_mode` or `query.embedding` for stronger privacy. (SPEC §10.8)

---

## 📍 Phase Awareness

Know which phase a feature belongs to before implementing it. Implementing a Phase 2/3 feature in a Phase 1 PR causes scope creep and review friction.

| Phase | Timeline | Scope (SPEC §15) |
|-------|----------|------------------|
| **Phase 0** | spec only | This SPEC.md; spikes against real MCP/ARD registries; §17 design review |
| **Phase 1** | weeks 1–6 | Both SDKs in parallel: `search`, `request_approval`, `connect`, `revoke`; `ServerCard`; CLI approval renderer; HTTP+SSE + Streamable HTTP; publisher sig verification (ed25519 + did:web); local consent store; official MCP Registry adapter (read-only); tool-usage logging; quickstart repo. **No stdio, no OAuth, no federation.** `auth.type` limited to `none` + `api_key`. |
| **Phase 2** | weeks 7–12 | stdio transport; ARD adapter; **OAuth via App Registration Inheritance (§17)**; `OAuthFlowHandler`; MCP Apps inline OAuth; CIMD hosting; scope minimization; sandboxing hooks; egress allowlist |
| **Phase 3** | weeks 13–20 | Federation (`auto`/`referrals`); A2A adapter (discovery-only); AGNTCY adapter; reviews/pricing surfaces; registry-side consent audit; walled-garden bridges |
| **Phase 4** | weeks 21+ | Revenue-share tier; on-device embedding model; push revocation; voice-first approval; multi-agent quorum; formal conformance CLI; governance |

---

*This guide should be updated as the project evolves. Keep it current. SPEC.md is the canonical source of truth — when this guide and SPEC.md disagree, SPEC.md wins.*
