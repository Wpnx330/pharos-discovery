#!/usr/bin/env python3
"""Pharos Discovery SDK — Python quickstart demo.

This script demonstrates the full discovery lifecycle:

1. Search the live Pharos registry (https://api.getpharos.dev) for MCP servers.
2. Display the returned ServerCard.
3. Run the approval flow (auto-approve in this demo).
4. Connect to a local mock MCP server via the ConnectionManager.
5. Execute the MCP lifecycle: initialize → tools/list → tools/call.

Prerequisites:
    pip install -e ../../packages/python

Usage:
    # Terminal 1 — start the mock MCP server
    python ../mock_mcp_server.py

    # Terminal 2 — run this demo
    python demo.py
"""

from __future__ import annotations

import asyncio
import sys

from pharos_discovery.client import PharosClient
from pharos_discovery.connection.manager import ConnectionManager, MCPConnection
from pharos_discovery.models import (
    ApprovalRequest,
    ApprovalResponse,
    ServerCard,
)


# ---------------------------------------------------------------------------
# Auto-approve approval handler
# ---------------------------------------------------------------------------

class AutoApproveHandler:
    """Approval handler that approves every request (demo only)."""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        print(f"\n  [Approval] Server: {request.server.display_name}")
        print(f"  [Approval] Purpose: {request.purpose}")
        print(f"  [Approval] Scopes: {request.requested_scopes}")
        print("  [Approval] → AUTO-APPROVED")
        return ApprovalResponse(
            approved=True,
            approved_scopes=request.requested_scopes or ["*"],
            duration="session",
        )


# ---------------------------------------------------------------------------
# Main demo
# ---------------------------------------------------------------------------

REGISTRY_URL = "https://api.getpharos.dev"
MOCK_MCP_URL = "http://127.0.0.1:8765/mcp"


async def main() -> None:
    print("=" * 60)
    print("  Pharos Discovery SDK — Python Quickstart Demo")
    print("=" * 60)

    # ---- 1. Search the live registry ----------------------------------
    print(f"\n1. Searching registry at {REGISTRY_URL} for 'flight'...\n")
    client = PharosClient(
        REGISTRY_URL,
        approval_handler=AutoApproveHandler(),
        connection_handler=ConnectionManager(),
    )

    results = await client.search("flight", limit=5)
    print(f"   Found {len(results)} result(s):")
    for i, result in enumerate(results):
        card = result.card
        print(f"   [{i}] {card.id}")
        print(f"       Name:        {card.display_name}")
        print(f"       Version:     {card.version}")
        print(f"       Transport:   {card.transport}")
        print(f"       Capabilities:{card.capabilities}")
        print(f"       Publisher:   {card.publisher.id} (verified={card.publisher.verified})")
        print(f"       Description: {card.description[:80]}")
        print()

    # ---- 2. Pick the first result and show the ServerCard -------------
    card = results[0].card
    print(f"2. Selected ServerCard: {card.id}")
    print(f"   Full card: {card}")

    # ---- 3. Approval flow ---------------------------------------------
    print("\n3. Running approval flow...")
    # We'll construct a ServerCard that points at our mock MCP server
    # so the ConnectionManager can actually connect.
    mock_card = ServerCard(
        id="mock-mcp-server",
        display_name="Mock MCP Server (echo)",
        description="A minimal MCP server with an echo tool for testing.",
        publisher=card.publisher,  # reuse from registry
        version="1.0.0",
        transport=["http+sse"],
        endpoint=MOCK_MCP_URL,
        capabilities=["tools"],
        tools_count=1,
        auth={"type": "none"},
        availability="native",
        source_registry="local",
        published_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        status="active",
        tags=["test", "mock"],
    )

    token, connection = await client.connect_and_approve(
        mock_card,
        purpose="Demo: connect to mock MCP server and call echo tool",
        requested_scopes=["tools:call"],
    )
    print(f"   ✓ Approved! Token server_id={token.server_id}")
    print(f"   ✓ Connected to {mock_card.endpoint}")

    # ---- 4. MCP lifecycle: initialize → tools/list → tools/call -------
    print("\n4. MCP Lifecycle:")

    mcp = MCPConnection(connection, mock_card.id)

    # initialize
    print("   → initialize()")
    init_result = await mcp.initialize()
    server_info = init_result.get("result", {}).get("serverInfo", {})
    print(f"   ← Server: {server_info.get('name')} v{server_info.get('version')}")

    # tools/list
    print("   → tools/list()")
    tools_result = await mcp.list_tools()
    tools = tools_result.get("result", {}).get("tools", [])
    print(f"   ← {len(tools)} tool(s):")
    for tool in tools:
        print(f"      • {tool['name']}: {tool['description']}")

    # tools/call
    print("   → tools/call(echo, {message: 'Hello from Pharos!'})")
    call_result = await mcp.call_tool("echo", {"message": "Hello from Pharos!"})
    content = call_result.get("result", {}).get("content", [])
    for item in content:
        if item.get("type") == "text":
            print(f"   ← {item['text']}")

    # ---- 5. Disconnect -------------------------------------------------
    print("\n5. Disconnecting...")
    await client.close()
    print("   ✓ Disconnected.")

    print("\n" + "=" * 60)
    print("  Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        print("\nMake sure the mock MCP server is running:")
        print("  python ../mock_mcp_server.py")
        sys.exit(1)
