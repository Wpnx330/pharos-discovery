"""
PHAROS Discovery MCP Server

Exposes PHAROS discovery operations as MCP tools that any MCP-compatible
client (Claude Desktop, LibreChat, VS Code, etc.) can call.

Implements the MCP Apps extension (2026-01-26) for visual approval/OAuth UIs
rendered as sandboxed iframes in the host's chat interface.

Tools:
    pharos_search(query)         — search the registry for MCP servers
    pharos_install(server_id)    — install a server locally
    pharos_connect(server_id)    — request a connection (returns pending token)
    pharos_approve(token)        — approve a pending connection (completes it)
    pharos_list_tools(server_id) — list tools available on a connected server
    pharos_call_tool(server_id, tool_name, args) — call a tool on a server

Resources:
    ui://pharos/approval         — approval UI (MCP Apps)
    ui://pharos/oauth            — OAuth consent UI (MCP Apps)
    ui://pharos/results          — search results UI (MCP Apps)
"""
