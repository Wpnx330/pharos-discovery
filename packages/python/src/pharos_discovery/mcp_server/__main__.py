"""Entry point for running the PHAROS Discovery MCP server as a module.

Usage:
    python3 -m pharos_discovery.mcp_server

    # Or with environment variables:
    PHAROS_MCP_TRANSPORT=sse PHAROS_MCP_PORT=8766 python3 -m pharos_discovery.mcp_server
"""
from pharos_discovery.mcp_server.server import main

if __name__ == "__main__":
    main()
