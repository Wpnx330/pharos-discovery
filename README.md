# Pharos Discovery SDK

Universal agent discovery framework for [MCP (Model Context Protocol)](https://modelcontextprotocol.io) servers — search, approve, and connect to MCP servers from any registry.

## Features

- **Registry Client** — Search and browse MCP server packages from any Pharos-compatible registry
- **MCP Adapter** — Normalize server cards from MCP official registry, npm, and custom registries
- **Approval Engine** — HMAC-signed token approval system with configurable handlers
- **Connection Manager** — Multi-transport connections (streamable-http, http+sse, stdio) with retry and reconnection
- **Security** — Blocklist (hash-based) and key pinning (TOFU) for safe server connections
- **SSE Events** — Subscribe to registry events (package.published, package.unpublished, etc.) with auto-reconnect
- **Consent Store** — Persistent user consent records with TTL and revocation
- **Headless Mode** — CI/CD-friendly approval policies (allow_all, deny_all, allow_trusted_only)
- **Plan Approval** — Two-phase install: review risk-assessed plan, then approve execution
- **Caching** — TTL-based cache with LRU eviction for registry responses

## Installation

### Python

```bash
pip install pharos-discovery
```

### TypeScript

```bash
npm install @pharos/discovery
```

## Quick Start

### Python

```python
from pharos_discovery import RegistryClient, ApprovalEngine

client = RegistryClient("https://getpharos.dev")
results = await client.search("filesystem")

# Approve and connect
engine = ApprovalEngine(secret="your-hmac-secret")
token = engine.sign_token(server_id="fs-server", scopes=["read"])
```

### TypeScript

```typescript
import { RegistryClient, ApprovalEngine } from "@pharos/discovery";

const client = new RegistryClient("https://getpharos.dev");
const results = await client.search("filesystem");

const engine = new ApprovalEngine({ secret: "your-hmac-secret" });
const token = engine.signToken({ serverId: "fs-server", scopes: ["read"] });
```

## Architecture

The SDK is a monorepo with parallel implementations:

- **`packages/python/`** — Python 3.10+ with pydantic v2, httpx, anyio
- **`packages/typescript/`** — Node 20+ with zod, dual ESM/CJS output

Both implementations share the same API surface and are feature-complete.

## Testing

### Python
```bash
cd packages/python
PYTHONPATH=src python3 -m pytest tests/ -q
```

### TypeScript
```bash
cd packages/typescript
npx vitest run
```

## License

MIT © Chris Wykel
