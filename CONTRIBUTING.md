# Contributing to Pharos Discovery

Thanks for your interest in contributing! This repo contains the Pharos Discovery SDK — parallel Python and TypeScript implementations of the MCP server discovery, approval, and connection framework.

## Development Setup

### Prerequisites

- Python 3.10+
- Node.js 20+
- npm or pnpm

### Python

```bash
cd packages/python
pip install -e ".[dev]"
PYTHONPATH=src python3 -m pytest tests/ -q
```

### TypeScript

```bash
cd packages/typescript
npm install
npx tsc --noEmit
npx vitest run
```

## Testing

All tests must pass before a PR is merged. CI runs both Python and TypeScript jobs on every push and pull request.

### Python Tests

```bash
cd packages/python
PYTHONPATH=src python3 -m pytest tests/ -v --tb=short
```

### TypeScript Tests

```bash
cd packages/typescript
npx vitest run
```

### Type Checking

```bash
# Python
python3 -m py_compile src/pharos_discovery/*.py src/pharos_discovery/**/*.py

# TypeScript
npx tsc --noEmit
```

## Pull Request Process

1. **Fork** the repo and create a branch from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```

2. **Write tests** — both Python and TypeScript if the change affects both packages.

3. **Run all tests locally** (see commands above).

4. **Commit with clear messages** — use conventional commits:
   - `feat(python): add blocklist filtering to search`
   - `fix(ts): handle null redirect in OAuth handler`
   - `docs: update env var table`

5. **Open a PR** against `main`. Include:
   - What changed and why
   - Test results (both Python + TypeScript if applicable)
   - Any breaking changes

## Code Style

### Python
- Follow PEP 8 (enforced by ruff)
- Use type hints on all public functions
- Async functions for anything that does I/O
- Pydantic v2 for data models

### TypeScript
- Strict mode (`strict: true` in tsconfig)
- Zod for runtime validation
- No `any` types — use `unknown` and narrow
- ESM-first, CJS fallback via tsup

## Project Structure

```
pharos-discovery/
├── packages/
│   ├── python/          # Python SDK + MCP server
│   │   ├── src/pharos_discovery/
│   │   │   ├── mcp_server/   # MCP server (FastMCP)
│   │   │   ├── adapters/     # Registry adapters
│   │   │   ├── connection/   # Connection manager + OAuth
│   │   │   └── approval/     # Approval engine
│   │   └── tests/
│   ├── typescript/      # TypeScript SDK
│   │   ├── src/
│   │   │   ├── adapters/
│   │   │   ├── connection/
│   │   │   └── approval/
│   │   └── test/
│   └── pip/             # pip-installable MCP server package
├── quickstart/          # Demo scripts (Python + TS)
└── .github/workflows/
```

## Keeping Packages in Sync

Both Python and TypeScript implementations should have feature parity. If you add a feature to one, add it to the other. If you find a discrepancy, open an issue.

## Reporting Issues

Open a GitHub issue with:
- Which package (Python or TypeScript)
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your runtime versions (`python --version`, `node --version`)

## License

By contributing, you agree that your contributions are licensed under the MIT license.
