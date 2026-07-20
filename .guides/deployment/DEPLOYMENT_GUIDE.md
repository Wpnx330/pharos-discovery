# Deployment Guide — PHAROS Discovery (dual SDK)

**Audience:** AI agents and contributors building, publishing, and wiring CI for the dual SDK.
**Source of truth:** `SPEC.md` v0.4.0, §8.6 (IDL + conformance), §15 (roadmap).
**Companion:** `.guides/backend/PYTHON_GUIDE.md`, `.guides/backend/TYPESCRIPT_GUIDE.md`, `.guides/testing/TESTING_GUIDE.md`.

---

## 1. Repository layout (monorepo, two packages + IDL)

```
pharos-discovery/
├── .github/workflows/
│   ├── ci.yml              # matrix: test both packages on every PR
│   ├── conformance.yml     # shared conformance suite against both SDKs
│   └── release.yml         # dual publish on tag
├── idl/
│   └── typespec/           # .tsp files (canonical source for both SDKs)
├── packages/
│   ├── python/             # pharos-discovery → PyPI
│   └── typescript/         # @pharos/discovery → npm
├── conformance/            # shared golden fixtures + behavioral assertions (§8.6)
├── SPEC.md
├── PHAROS_DISCOVERY_GUIDE.md
└── ...
```

**Rule:** the `packages/python` and `packages/typescript` public surfaces are **generated from `idl/typespec/`**. CI regenerates both on every change and fails if generated output differs from committed output (drift detection). (SPEC §8.6)

---

## 2. Python package — `pharos-discovery` → PyPI

### 2.1 Build tooling: `uv` + `hatchling`

`packages/python/pyproject.toml` (key fields):
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "pharos-discovery"
version = "0.1.0"
description = "Provider-agnostic, embeddable client SDK for MCP server discovery, approval, and connection."
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
authors = [{ name = "Pharos Discovery Contributors" }]
keywords = ["mcp", "model-context-protocol", "agent", "discovery", "oauth"]
classifiers = [
  "Development Status :: 3 - Alpha",
  "Intended Audience :: Developers",
  "License :: OSI Approved :: MIT License",
  "Programming Language :: Python :: 3.10",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Framework :: AsyncIO",
  "Typing :: Typed",
]
dependencies = [
  "anyio>=4.0",
  "httpx>=0.27",
  "pydantic>=2.0",
  "cryptography>=42",
]

[project.optional-dependencies]
embeddings = ["onnxruntime>=1.17"]   # all-MiniLM-L6-v2 for MCP-Registry adapter re-rank (§11.3)
dev = ["pytest>=8", "pytest-asyncio>=0.23", "pytest-httpx>=0.30", "ruff", "mypy"]

[project.urls]
Homepage = "https://github.com/Wpnx330/pharos-discovery"
Documentation = "https://github.com/Wpnx330/pharos-discovery/tree/main/docs"

[tool.hatch.build.targets.wheel]
packages = ["src/pharos_discovery"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### 2.2 Local build

```bash
cd packages/python
uv sync                       # install deps + dev deps
uv run ruff check .           # lint
uv run mypy src/              # type-check
uv run pytest                 # tests
uv run hatch build            # → dist/pharos_discovery-0.1.0-py3-none-any.whl + .tar.gz
```

### 2.3 Publish to PyPI (CI, on tag)

```bash
uv run hatch publish          # uses HATCH_INDEX_USER / HATCH_INDEX_TOKEN (trusted publishing preferred)
```

**Trusted publishing (recommended):** configure PyPI GitHub OIDC trusted publisher for `Wpnx330/pharos-discovery`. No long-lived API token. The `release.yml` workflow uses `pypa/gh-action-pypi-publish@release/v1` with `permissions: id-token: write`.

---

## 3. TypeScript package — `@pharos/discovery` → npm

### 3.1 Build tooling: `tsup` + `tsc --emitDeclarationOnly`

`packages/typescript/package.json` (key fields):
```json
{
  "name": "@pharos/discovery",
  "version": "0.1.0",
  "description": "Provider-agnostic, embeddable client SDK for MCP server discovery, approval, and connection.",
  "license": "MIT",
  "type": "module",
  "main": "./dist/index.cjs",
  "module": "./dist/index.js",
  "types": "./dist/index.d.ts",
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "import": "./dist/index.js",
      "require": "./dist/index.cjs"
    }
  },
  "engines": { "node": ">=20" },
  "files": ["dist", "README.md", "LICENSE"],
  "scripts": {
    "build": "tsup",
    "test": "vitest run",
    "test:watch": "vitest",
    "lint": "eslint src --ext .ts",
    "typecheck": "tsc --noEmit",
    "prepublishOnly": "pnpm run build"
  },
  "dependencies": {
    "zod": "^3.23"
  },
  "optionalDependencies": {
    "onnxruntime-web": "^1.17"
  },
  "devDependencies": {
    "typescript": "^5.4",
    "tsup": "^8",
    "vitest": "^1",
    "@types/node": "^20",
    "eslint": "^9"
  }
}
```

### 3.2 Local build

```bash
cd packages/typescript
pnpm install
pnpm typecheck
pnpm lint
pnpm test
pnpm build               # → dist/index.js (ESM), dist/index.cjs (CJS), dist/index.d.ts
```

### 3.3 Publish to npm (CI, on tag)

```bash
npm publish --access public    # uses NPM_TOKEN secret; scoped package defaults to restricted
```

**Provenance (recommended):** publish with `--provenance` for npm package signing via GitHub OIDC. Requires `id-token: write` permission and a public repo.

---

## 4. IDL codegen pipeline (SPEC §8.6)

The TypeSpec IDL in `idl/typespec/` is the single source of truth. Both SDKs are regenerated from it.

### 4.1 TypeSpec → Python (pydantic v2)

```bash
# Install TypeSpec + Python emitter
npm install -g @typespec/compiler
npm install -g @typespec/http   # HTTP + JSON bindings
pip install typespec-pydantic   # or the chosen Python emitter

# Regenerate
cd idl/typespec
tsp compile . --emit pydantic  # → packages/python/src/pharos_discovery/models/
```

The emitter produces `pydantic.BaseModel` subclasses in `packages/python/src/pharos_discovery/models/` matching SPEC §8.3 / Appendix A exactly. Hand-written code extends (not edits) these.

### 4.2 TypeSpec → TypeScript (types + zod)

```bash
tsp compile . --emit typescript-zod  # → packages/typescript/src/models/
```

Produces `src/models/*.ts` with TS interfaces + zod schemas for runtime validation. Field names are `camelCase`.

### 4.3 Drift detection in CI

`ci.yml` runs the codegen, then `git diff --exit-code packages/`. If generated output differs from committed output, CI fails — forcing the contributor to commit regenerated artifacts. This is the mechanism that prevents the v0.2-style divergence (SPEC §8.6).

---

## 5. GitHub Actions — dual CI + dual publish

### 5.1 `ci.yml` — matrix test on every PR

```yaml
name: ci
on:
  pull_request:
  push: { branches: [main] }

jobs:
  python:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - uses: actions/setup-python@v5
        with: { python-version: ${{ matrix.python } } }
      - run: uv sync --all-extras
        working-directory: packages/python
      - run: uv run ruff check .
        working-directory: packages/python
      - run: uv run mypy src/
        working-directory: packages/python
      - run: uv run pytest --cov
        working-directory: packages/python

  typescript:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        node: ["20", "22"]
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v3
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with: { node-version: ${{ matrix.node } }, cache: pnpm }
      - run: pnpm install --frozen-lockfile
        working-directory: packages/typescript
      - run: pnpm typecheck
        working-directory: packages/typescript
      - run: pnpm lint
        working-directory: packages/typescript
      - run: pnpm test --run
        working-directory: packages/typescript

  idl-drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm install -g @typespec/compiler @typespec/http
      - run: tsp compile idl/typespec --emit pydantic --output-dir packages/python/src/pharos_discovery/models
      - run: tsp compile idl/typespec --emit typescript-zod --output-dir packages/typescript/src/models
      - run: git diff --exit-code packages/   # fail if generated output drifted
```

### 5.2 `conformance.yml` — shared suite (SPEC §8.6)

```yaml
name: conformance
on:
  pull_request:
  push: { branches: [main] }

jobs:
  conformance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # Python
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --all-extras
        working-directory: packages/python
      - run: uv run pytest conformance/ -k conformance
        working-directory: packages/python
      # TypeScript
      - uses: pnpm/action-setup@v3
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: pnpm }
      - run: pnpm install --frozen-lockfile
        working-directory: packages/typescript
      - run: pnpm test --run conformance/
        working-directory: packages/typescript
```

The `conformance/` directory holds shared golden fixtures (canonical JSON for `ServerCard`, `ApprovalToken`, `OAuthResult`, search request/response pairs) and behavioral assertions (e.g. "connect() without ApprovalToken raises", "call_tool outside approved_scopes raises SCOPE_NOT_APPROVED"). **Both SDKs must pass.** (SPEC §8.6)

### 5.3 `release.yml` — dual publish on tag

```yaml
name: release
on:
  push:
    tags: ["v*.*.*"]

jobs:
  python-publish:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write       # trusted publishing
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --all-extras
        working-directory: packages/python
      - run: uv run hatch build
        working-directory: packages/python
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: packages/python/dist

  npm-publish:
    runs-on: ubuntu-latest
    environment: npm
    permissions:
      id-token: write
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v3
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: pnpm, registry-url: https://registry.npmjs.org }
      - run: pnpm install --frozen-lockfile
        working-directory: packages/typescript
      - run: pnpm build
        working-directory: packages/typescript
      - run: npm publish --access public --provenance
        working-directory: packages/typescript
        env:
          NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}
```

**Tag convention:** `v<major>.<minor>.<patch>` (e.g. `v0.1.0`). Both packages publish from the same tag. A major IDL bump (`service.version`) is a breaking change requiring coordinated release across both languages (SPEC §8.6).

---

## 6. Versioning & breaking-change policy (SPEC §8.6)

- The IDL is versioned via TypeSpec `service.version` (e.g. `0.1.0`).
- **Minor bump** = additive fields with defaults; non-breaking. Both SDKs release a minor bump.
- **Major bump** = breaking change (removed field, changed type, renamed method). Requires coordinated SDK release across both languages. `X-Pharos-Version` header advertises wire-protocol version (SPEC §6.1).
- Both SDKs share the same version number, derived from the IDL. `packages/python/_version.py` and `packages/typescript/src/version.ts` are both generated from `idl/typespec/package.json`.

---

## 7. Conformance suite packaging (SPEC §8.6)

`conformance/` contains:
- `fixtures/` — golden JSON for `ServerCard`, `SearchQuery`, search request/response, `ApprovalToken`, `OAuthResult`, etc.
- `assertions/` — behavioral rules in a shared format both SDKs can consume (e.g. "given ApprovalToken with approved_scopes=['flight_search'], call_tool('flight_book') raises SCOPE_NOT_APPROVED").
- Python runner: `pytest conformance/ -k conformance` (reads fixtures, applies assertions).
- TS runner: `vitest run conformance/` (same fixtures, same assertions).

**Phase 1 requirement (SPEC §15):** the conformance suite is built alongside the first SDK in Phase 1, not deferred. Exit criteria for Phase 1 explicitly require both SDKs to pass it.

---

## 8. Local developer loop (both packages)

```bash
# From repo root
uv sync --all-extras --directory packages/python
pnpm install --dir packages/typescript

# Run both test suites in parallel
( cd packages/python && uv run pytest -q ) &
( cd packages/typescript && pnpm test --run ) &
wait

# Regenerate IDL outputs (run this after editing idl/typespec/)
tsp compile idl/typespec --emit pydantic --output-dir packages/python/src/pharos_discovery/models
tsp compile idl/typespec --emit typescript-zod --output-dir packages/typescript/src/models

# Verify no drift
git diff --exit-code packages/
```

---

## 9. Security notes for the release pipeline

- **No secrets in artifacts.** `client_secret` must never appear in any published wheel/tarball (SPEC §10.5). CI runs a grep check: `! grep -r "client_secret" packages/*/dist/` before publish.
- **Trusted publishing preferred** for both PyPI and npm (OIDC, no long-lived tokens).
- **SBOM:** `cyclonedx` / `spdx` SBOMs attached to GitHub Release for both packages.
- **Provenance:** npm publish uses `--provenance`; PyPI trusted publishing records provenance automatically.

---

*Next: `.guides/testing/TESTING_GUIDE.md` for test patterns; `.guides/security/SECURITY_GUIDE.md` for the security model that CI must not violate.*
