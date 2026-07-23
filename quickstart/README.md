# Pharos Discovery SDK — Quickstart

This directory contains end-to-end demos for both the Python and TypeScript
Pharos Discovery SDK packages.  Each demo:

1. **Searches** the live Pharos registry at `https://getpharos.dev`.
2. **Displays** the `ServerCard` objects returned.
3. **Runs the approval flow** (auto-approve for demo purposes).
4. **Connects** to a local mock MCP server.
5. **Executes the MCP lifecycle**: `initialize` → `tools/list` → `tools/call`.

## Prerequisites

### Python demo

```bash
cd ../../packages/python
pip install -e .
```

> If `pip install -e .` fails because of a missing README, use
> `PYTHONPATH=src python quickstart/python/demo.py` instead.

### TypeScript demo

```bash
cd ../../packages/typescript
npm install
npm run build
```

## Step 1 — Start the mock MCP server

The demos connect to a minimal MCP server that implements the JSON-RPC
protocol over HTTP with a single `echo` tool.  Start it in a separate
terminal:

```bash
python quickstart/mock_mcp_server.py
```

You should see:

```
Mock MCP server listening on http://127.0.0.1:8765/mcp
```

## Step 2 — Run the Python demo

```bash
python quickstart/python/demo.py
```

Expected output (abridged):

```
============================================================
  Pharos Discovery SDK — Python Quickstart Demo
============================================================

1. Searching registry at https://getpharos.dev for 'flight'...

   Found 3 result(s):
   [0] flight-mcp-server
       Name:        Flight MCP Server
       Version:     1.0.0
       Transport:   stdio
       ...

2. Selected ServerCard: flight-mcp-server

3. Running approval flow...
  [Approval] → AUTO-APPROVED
   ✓ Approved!
   ✓ Connected to http://127.0.0.1:8765/mcp

4. MCP Lifecycle:
   → initialize()
   ← Server: mock-mcp-server v1.0.0
   → tools/list()
   ← 1 tool(s):
      • echo: Echo back the provided message.
   → tools/call(echo, {message: 'Hello from Pharos!'})
   ← Echo: Hello from Pharos!

5. Disconnecting...
   ✓ Disconnected.
```

## Step 3 — Run the TypeScript demo

```bash
cd quickstart/typescript
npx tsx demo.ts
```

The output mirrors the Python demo.

> If `tsx` is not installed, install it first: `npm install -g tsx`
> or use `npx tsx@latest demo.ts`.

## How it works

```
┌─────────────────┐     search      ┌──────────────────┐
│   Demo Script   │ ──────────────→ │  getpharos.dev   │
│                 │ ←────────────── │   /v1/search     │
│  PharosClient   │   ServerCards   └──────────────────┘
│                 │
│                 │     connect     ┌──────────────────┐
│  Connection     │ ──────────────→ │  Mock MCP Server │
│  Manager        │ ←────────────── │  127.0.0.1:8765  │
│                 │  MCP responses  └──────────────────┘
└─────────────────┘
```

## Files

| File | Description |
|------|-------------|
| `mock_mcp_server.py` | Minimal MCP server (HTTP/JSON-RPC) with an `echo` tool |
| `python/demo.py` | Python SDK quickstart demo |
| `typescript/demo.ts` | TypeScript SDK quickstart demo |

## Troubleshooting

**`NoServersFound` error** — The live registry at `getpharos.dev` may be
temporarily unavailable.  Check `https://getpharos.dev/v1/search?q=flight`
in a browser.

**`fetch failed` / `Connection refused`** — The mock MCP server is not
running.  Start it with `python quickstart/mock_mcp_server.py`.

**`pip install -e .` fails** — The Python package's `pyproject.toml`
references a README that lives at the repo root.  Either create a symlink
or run with `PYTHONPATH=src` as noted above.
