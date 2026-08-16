"""
PHAROS Discovery MCP Server

Exposes PHAROS discovery operations as MCP tools that any MCP-compatible
client (Claude Desktop, LibreChat, VS Code, etc.) can call.

Two operating modes are selected by the PHAROS_MCP_APPS environment variable:

CLI mode (default — PHAROS_MCP_APPS unset or false):
    Registers pharos_search, pharos_install, pharos_list, pharos_remove
    as direct tools. No iframe, no approval UI — the AI calls them and
    gets JSON results immediately.

Apps mode (PHAROS_MCP_APPS=true/1/yes):
    Registers pharos_search_apps, pharos_info_apps, pharos_install_apps,
    pharos_remove_apps, pharos_list_apps, pharos_publish_apps with
    meta={"ui": {"resourceUri": "ui://pharos/<resource>"}} annotations.
    The host renders results/approval in a sandboxed iframe (MCP Apps,
    2026-01-26).

Non-A/B tools (pharos_list_tools, pharos_call_tool, pharos_daemon_status,
etc.) are registered unconditionally in both modes.

The /approve HTTP endpoint (POST) handles approval confirmations from the
iframe UI. It is NOT an MCP tool — the AI cannot see or call it.

Resources:
    ui://pharos/approval  — approval UI (MCP Apps)
    ui://pharos/oauth     — OAuth consent UI (MCP Apps)
    ui://pharos/results   — search results UI (MCP Apps)
"""
