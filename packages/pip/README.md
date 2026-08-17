# pharos-mcp

One-command install for PHAROS MCP server discovery.

```bash
pip install pharos-mcp
```

This gives you:

1. **PHAROS MCP Server** — `pharos-mcp` exposes search/install/list/call tools. Set `PHAROS_MCP_APPS=true` for iframe Apps mode (`pharos_search_apps`, `pharos_install_apps`, …). There is no `pharos_connect` / `pharos_approve` tool; approval is a physical click in the host UI.
2. **PHAROS CLI** — the `pharos` CLI binary, bundled as package data
3. **Auto-configuration** — post-install hook detects MCP clients (Cursor, Claude Desktop, VS Code) and configures them automatically

## Quick start

```bash
pip install pharos-mcp
# → MCP server installed
# → CLI binary available as 'pharos'
# → Post-install: "Configure PHAROS MCP for Cursor? (Y/n)"

# Use the CLI directly
pharos search "git"
pharos install mcp-git-server

# Or start the MCP server for an MCP client
pharos-mcp
```

## What is PHAROS?

PHAROS is a registry and discovery system for MCP (Model Context Protocol) servers. It lets you:

- **Search** a curated registry of MCP servers
- **Install** servers with automatic dependency resolution
- **Connect** with visual approval (MCP Apps)
- **Call tools** on connected servers

Visit [getpharos.dev](https://getpharos.dev) for more information.

## Transport modes

The MCP server supports three transports:

| Transport | Use Case | Environment |
|-----------|----------|-------------|
| stdio (default) | Local use — Claude Desktop, VS Code, Cursor | Client launches server as subprocess |
| SSE | Remote use — LibreChat, web clients | `PHAROS_MCP_TRANSPORT=sse PHAROS_MCP_PORT=8766` |
| streamable-http | Newer HTTP streaming clients | `PHAROS_MCP_TRANSPORT=streamable-http` |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PHAROS_REGISTRY_URL` | `https://getpharos.dev` | Registry API base URL |
| `PHAROS_CLI` | `pharos` | Path to CLI binary |
| `PHAROS_MCP_TRANSPORT` | `stdio` | Transport: stdio, sse, streamable-http |
| `PHAROS_MCP_HOST` | `0.0.0.0` | Bind host (network transports only) |
| `PHAROS_MCP_PORT` | `8766` | Bind port (network transports only) |

## License

MIT
