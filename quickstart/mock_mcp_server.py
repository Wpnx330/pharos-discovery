#!/usr/bin/env python3
"""Minimal MCP server over HTTP/SSE for quickstart testing.

Implements the Model Context Protocol JSON-RPC lifecycle:
  initialize → tools/list → tools/call

Exposes a single ``echo`` tool that returns whatever message it receives.

Usage:
    python mock_mcp_server.py [--host 127.0.0.1] [--port 8765]

Once running, the SDK's HttpSSETransport can POST to:
    http://127.0.0.1:8765/mcp
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class MCPHandler(BaseHTTPRequestHandler):
    """Handle MCP JSON-RPC requests over plain HTTP POST."""

    # Silence default per-request logging to keep output clean.
    def log_message(self, format, *args):  # noqa: A002
        pass

    # -- helpers -----------------------------------------------------------

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw)

    # -- routing -----------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        try:
            msg = self._read_body()
        except (json.JSONDecodeError, ValueError):
            return self._send_json(400, {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
            })

        method = msg.get("method", "")
        req_id = msg.get("id")
        params = msg.get("params", {})

        # --- notifications (no id → no response) --------------------------
        if req_id is None:
            return  # e.g. notifications/initialized

        # --- standard MCP methods -----------------------------------------
        if method == "initialize":
            return self._send_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": "mock-mcp-server",
                        "version": "1.0.0",
                    },
                },
            })

        if method == "tools/list":
            return self._send_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo back the provided message.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "message": {
                                        "type": "string",
                                        "description": "The message to echo.",
                                    },
                                },
                                "required": ["message"],
                            },
                        },
                    ],
                },
            })

        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {})
            if name == "echo":
                message = args.get("message", "")
                return self._send_json(200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Echo: {message}",
                            },
                        ],
                    },
                })
            return self._send_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Unknown tool: {name}",
                },
            })

        # Unknown method
        return self._send_json(200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        })

    def do_GET(self) -> None:  # noqa: N802
        """Simple health-check endpoint."""
        if self.path == "/health":
            return self._send_json(200, {"status": "ok"})
        return self._send_json(404, {"error": "not found"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock MCP server for quickstart")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8765, help="Listen port")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), MCPHandler)
    print(f"Mock MCP server listening on http://{args.host}:{args.port}/mcp")
    print(f"  Health check: http://{args.host}:{args.port}/health")
    print("  Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()
