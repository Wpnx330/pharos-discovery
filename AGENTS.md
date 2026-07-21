# AGENTS.md — Pharos Discovery Project

> Layers on top of the global AGENTS.md at ~/.config/opencode/AGENTS.md

## Project Overview

This is a dual-language SDK monorepo for MCP server discovery:
- **Python SDK** at `packages/python/` — pydantic v2, anyio, httpx
- **TypeScript SDK** at `packages/typescript/` — zod, ESM+CJS, Web Crypto

## Build Commands

### Python
```bash
cd packages/python
python -m py_compile src/pharos_discovery/models/*.py  # syntax check
pytest tests/ -v                                         # run tests
ruff check src/                                          # lint
```

### TypeScript
```bash
cd packages/typescript
npx tsc --noEmit          # type check
npx vitest run            # run tests
npx tsup                  # build
```

## Code Conventions

### Python
- Python 3.10+ (PEP 604 unions: `str | None`)
- `src/` layout — imports from `pharos_discovery.*`
- pydantic v2 `BaseModel` for all data types
- `anyio` for async (NOT asyncio directly)
- `httpx` for HTTP calls
- snake_case throughout

### TypeScript
- TypeScript 5.x, `strict: true`
- ESM-first, CJS via tsup
- `zod` for runtime validation
- Web Crypto API for signatures (SubtleCrypto)
- camelCase for functions/variables, PascalCase for types

## Key Types (SPEC §8.3)

All types must match the spec exactly. Field names, types, and optionality
must match the ServerCard, ApprovalRequest, ApprovalResponse, ApprovalToken,
OAuthResult, and RevocationResult definitions in SPEC.md §8.3.

## Do NOT
- Do not install packages from the internet (air-gapped environment)
- Do not create IDL pipeline files — models are hand-written for now
- Do not modify files in the other language's package (Python agent stays in packages/python/, TS agent stays in packages/typescript/)
