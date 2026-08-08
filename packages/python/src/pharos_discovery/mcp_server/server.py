"""
PHAROS Discovery MCP Server — main server module.

Wraps PharosClient (the discovery SDK) and exposes its operations as MCP tools.
Implements MCP Apps (2026-01-26) for visual approval and OAuth flows.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import Resource, TextResourceContents

from pharos_discovery.client import PharosClient
from pharos_discovery.models import ServerCard, ApprovalRequest, ApprovalResponse, ApprovalToken
from pharos_discovery.connection.manager import ConnectionManager
from pharos_discovery.errors import (
    NoServersFound,
    RegistryUnavailable,
    ApprovalDenied,
    ConnectionFailed,
    HeadlessApprovalRequired,
)

# ─── UI Resources (MCP Apps) ──────────────────────────────────────────────────

# The MCP Apps extension (io.modelcontextprotocol/ui, stable 2026-01-26) lets
# servers attach a UI resource to a tool via _meta.ui.resourceUri. The host
# fetches the resource via resources/read and renders it in a sandboxed iframe.
# The iframe communicates with the host via JSON-RPC over postMessage.
#
# We serve three UI resources:
#   ui://pharos/approval  — server approval card (Approve/Deny buttons)
#   ui://pharos/oauth     — OAuth consent screen
#   ui://pharos/results   — search results gallery (clickable cards)

APPROVAL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PHAROS — Server Approval</title>
<style>
  :root {
    --bg: #0d1117;
    --card-bg: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --text-muted: #8b949e;
    --accent: #58a6ff;
    --danger: #f85149;
    --success: #3fb950;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 24px;
    min-height: 100vh;
  }
  .header { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
  .logo { font-size: 24px; }
  .title { font-size: 18px; font-weight: 600; }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 16px;
  }
  .server-name { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
  .server-id { font-size: 13px; color: var(--text-muted); margin-bottom: 16px; }
  .detail-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--border); }
  .detail-label { color: var(--text-muted); font-size: 13px; }
  .detail-value { font-size: 13px; font-weight: 500; }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
  }
  .badge-verified { background: rgba(63, 185, 80, 0.15); color: var(--success); }
  .badge-warning { background: rgba(248, 81, 73, 0.15); color: var(--danger); }
  .scopes { margin-top: 12px; }
  .scope-chip {
    display: inline-block;
    background: rgba(88, 166, 255, 0.1);
    color: var(--accent);
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 12px;
    margin: 2px;
  }
  .actions { display: flex; gap: 12px; margin-top: 20px; }
  .btn {
    flex: 1;
    padding: 12px 24px;
    border: none;
    border-radius: 8px;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.2s;
  }
  .btn:hover { opacity: 0.85; }
  .btn-approve { background: var(--success); color: #fff; }
  .btn-deny { background: var(--card-bg); color: var(--text); border: 1px solid var(--border); }
  .purpose { background: rgba(88, 166, 255, 0.05); border-left: 3px solid var(--accent); padding: 12px; border-radius: 0 8px 8px 0; margin-bottom: 16px; font-size: 14px; }
  #status { text-align: center; margin-top: 12px; font-size: 14px; color: var(--text-muted); }
</style>
</head>
<body>
  <div class="header">
    <span class="logo">🔒</span>
    <span class="title">PHAROS Discovery — Approval Required</span>
  </div>

  <div class="purpose" id="purpose"></div>
  <div class="card">
    <div class="server-name" id="server-name"></div>
    <div class="server-id" id="server-id"></div>
    <div class="detail-row">
      <span class="detail-label">Publisher</span>
      <span class="detail-value" id="publisher"></span>
    </div>
    <div class="detail-row">
      <span class="detail-label">Version</span>
      <span class="detail-value" id="version"></span>
    </div>
    <div class="detail-row">
      <span class="detail-label">Transport</span>
      <span class="detail-value" id="transport"></span>
    </div>
    <div class="detail-row">
      <span class="detail-label">Endpoint</span>
      <span class="detail-value" id="endpoint"></span>
    </div>
    <div class="scopes" id="scopes"></div>
  </div>

  <div class="actions">
    <button class="btn btn-deny" id="deny">Deny</button>
    <button class="btn btn-approve" id="approve">Approve Connection</button>
  </div>
  <div id="status"></div>

<script>
  // MCP Apps bridge — communicate with host via postMessage JSON-RPC
  const MCP_APP_MIME = "text/html;profile=mcp-app";

  // The host injects tool result data into the iframe via a JSON-RPC
  // "notifications/tool_result" message. We render it.
  let toolData = null;

  window.addEventListener("message", async (event) => {
    const msg = event.data;
    if (!msg || msg.jsonrpc !== "2.0") return;

    // Handle tool result notification from host
    if (msg.method === "notifications/tool_result") {
      toolData = msg.params;
      renderApproval(toolData);
    }
  });

  // Request the tool result from the host on load
  window.addEventListener("load", () => {
    // Send ui/initialize handshake
    event.source?.postMessage({
      jsonrpc: "2.0",
      id: crypto.randomUUID(),
      method: "ui/initialize",
      params: { capabilities: {} }
    }, "*");

    // Request current tool data
    event.source?.postMessage({
      jsonrpc: "2.0",
      id: crypto.randomUUID(),
      method: "ui/getToolResult",
      params: {}
    }, "*");
  });

  function renderApproval(data) {
    if (!data) return;
    const s = data.server || {};
    document.getElementById("purpose").textContent = "Purpose: " + (data.purpose || "User request");
    document.getElementById("server-name").textContent = s.display_name || s.name || s.id;
    document.getElementById("server-id").textContent = s.id;
    // Use textContent for user-controlled data, innerHTML only for static HTML structure
    const pubEl = document.getElementById("publisher");
    pubEl.textContent = s.publisher?.name || "unknown";
    if (s.publisher?.verified) {
      const badge = document.createElement("span");
      badge.className = "badge badge-verified";
      badge.textContent = "✓ verified";
      pubEl.appendChild(document.createTextNode(" "));
      pubEl.appendChild(badge);
    } else {
      const badge = document.createElement("span");
      badge.className = "badge badge-warning";
      badge.textContent = "unverified";
      pubEl.appendChild(document.createTextNode(" "));
      pubEl.appendChild(badge);
    }
    document.getElementById("version").textContent = s.version || "N/A";
    document.getElementById("transport").textContent = (s.transport || []).join(", ") || "N/A";
    document.getElementById("endpoint").textContent = s.endpoint || "N/A";

    const scopesEl = document.getElementById("scopes");
    const scopes = data.scopes || s.scopes || [];
    if (scopes.length) {
      // Build scopes safely with DOM APIs to prevent XSS
      const label = document.createElement("div");
      label.className = "detail-label";
      label.style.marginBottom = "6px";
      label.textContent = "Requested Scopes";
      scopesEl.innerHTML = "";
      scopesEl.appendChild(label);
      scopes.forEach(sc => {
        const chip = document.createElement("span");
        chip.className = "scope-chip";
        chip.textContent = sc;
        scopesEl.appendChild(chip);
      });
    }
  }

  function sendResponse(approved) {
    document.getElementById("approve").disabled = true;
    document.getElementById("deny").disabled = true;
    document.getElementById("status").textContent = approved ? "✅ Approved" : "❌ Denied";

    // Send result back to host via JSON-RPC
    window.parent.postMessage({
      jsonrpc: "2.0",
      method: "notifications/tool_result",
      params: {
        approved: approved,
        timestamp: new Date().toISOString()
      }
    }, "*");
  }

  document.getElementById("approve").addEventListener("click", () => sendResponse(true));
  document.getElementById("deny").addEventListener("click", () => sendResponse(false));
</script>
</body>
</html>"""

RESULTS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PHAROS — Search Results</title>
<style>
  :root {
    --bg: #0d1117;
    --card-bg: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --text-muted: #8b949e;
    --accent: #58a6ff;
    --success: #3fb950;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 24px;
  }
  .header { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
  .logo { font-size: 24px; }
  .title { font-size: 18px; font-weight: 600; }
  .result-card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 12px;
    cursor: pointer;
    transition: border-color 0.2s;
  }
  .result-card:hover { border-color: var(--accent); }
  .result-name { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
  .result-desc { font-size: 13px; color: var(--text-muted); margin-bottom: 8px; }
  .result-meta { display: flex; gap: 12px; font-size: 12px; color: var(--text-muted); }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
  }
  .badge-verified { background: rgba(63, 185, 80, 0.15); color: var(--success); }
  .badge-tools { background: rgba(88, 166, 255, 0.1); color: var(--accent); }
  #empty { text-align: center; padding: 40px; color: var(--text-muted); }
</style>
</head>
<body>
  <div class="header">
    <span class="logo">🔍</span>
    <span class="title">PHAROS Search Results</span>
  </div>
  <div id="results"></div>
  <div id="empty" style="display:none">No servers found. Try a different query.</div>

<script>
  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (!msg || msg.jsonrpc !== "2.0") return;
    if (msg.method === "notifications/tool_result") {
      renderResults(msg.params);
    }
  });

  window.addEventListener("load", () => {
    window.parent.postMessage({
      jsonrpc: "2.0",
      id: crypto.randomUUID(),
      method: "ui/getToolResult",
      params: {}
    }, "*");
  });

  function renderResults(data) {
    const results = data?.results || [];
    const container = document.getElementById("results");
    if (!results.length) {
      document.getElementById("empty").style.display = "block";
      return;
    }
    // Build result cards with DOM APIs to prevent XSS from registry data
    container.innerHTML = "";
    results.forEach((r, i) => {
      const card = document.createElement("div");
      card.className = "result-card";
      card.dataset.id = r.id;
      card.dataset.index = i;

      const name = document.createElement("div");
      name.className = "result-name";
      name.textContent = r.display_name || r.name || r.id;
      card.appendChild(name);

      const desc = document.createElement("div");
      desc.className = "result-desc";
      desc.textContent = r.description || "";
      card.appendChild(desc);

      const meta = document.createElement("div");
      meta.className = "result-meta";

      const ver = document.createElement("span");
      ver.textContent = r.version || "v?";
      meta.appendChild(ver);

      const tr = document.createElement("span");
      tr.textContent = (r.transport || []).join(", ");
      meta.appendChild(tr);

      const pub = document.createElement("span");
      pub.textContent = r.publisher?.name || "unknown";
      if (r.publisher?.verified) {
        const badge = document.createElement("span");
        badge.className = "badge badge-verified";
        badge.textContent = "✓";
        pub.appendChild(document.createTextNode(" "));
        pub.appendChild(badge);
      }
      meta.appendChild(pub);

      if (r.tools_count) {
        const tb = document.createElement("span");
        tb.className = "badge badge-tools";
        tb.textContent = r.tools_count + " tools";
        meta.appendChild(tb);
      }
      card.appendChild(meta);
      container.appendChild(card);
    });

    document.querySelectorAll(".result-card").forEach(card => {
      card.addEventListener("click", () => {
        window.parent.postMessage({
          jsonrpc: "2.0",
          method: "notifications/tool_result",
          params: {
            action: "select",
            server_id: card.dataset.id,
            index: parseInt(card.dataset.index)
          }
        }, "*");
      });
    });
  }
</script>
</body>
</html>"""

OAUTH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PHAROS — OAuth Consent</title>
<style>
  :root {
    --bg: #0d1117;
    --card-bg: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --text-muted: #8b949e;
    --accent: #58a6ff;
    --success: #3fb950;
    --danger: #f85149;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 24px;
  }
  .header { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
  .logo { font-size: 24px; }
  .title { font-size: 18px; font-weight: 600; }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 16px;
  }
  .server-name { font-size: 20px; font-weight: 700; margin-bottom: 8px; }
  .info { font-size: 14px; color: var(--text-muted); margin-bottom: 16px; }
  .scopes-list { list-style: none; }
  .scope-item {
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
    font-size: 14px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .scope-icon { color: var(--accent); }
  .actions { display: flex; gap: 12px; margin-top: 20px; }
  .btn {
    flex: 1;
    padding: 12px 24px;
    border: none;
    border-radius: 8px;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
  }
  .btn-approve { background: var(--success); color: #fff; }
  .btn-deny { background: var(--card-bg); color: var(--text); border: 1px solid var(--border); }
</style>
</head>
<body>
  <div class="header">
    <span class="logo">🔐</span>
    <span class="title">PHAROS — OAuth Authorization</span>
  </div>
  <div class="card">
    <div class="server-name" id="server-name"></div>
    <div class="info" id="info"></div>
    <ul class="scopes-list" id="scopes"></ul>
  </div>
  <div class="actions">
    <button class="btn btn-deny" id="deny">Cancel</button>
    <button class="btn btn-approve" id="authorize">Authorize</button>
  </div>

<script>
  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (!msg || msg.jsonrpc !== "2.0") return;
    if (msg.method === "notifications/tool_result") {
      const d = msg.params;
      document.getElementById("server-name").textContent = d.server_name || "Server";
      document.getElementById("info").textContent = d.server_name + " is requesting access to your account.";
      const scopes = d.scopes || [];
      const scopesEl = document.getElementById("scopes");
      scopesEl.innerHTML = "";
      scopes.forEach(s => {
        const li = document.createElement("li");
        li.className = "scope-item";
        const icon = document.createElement("span");
        icon.className = "scope-icon";
        icon.textContent = "→";
        li.appendChild(icon);
        li.appendChild(document.createTextNode(" " + s));
        scopesEl.appendChild(li);
      });
    }
  });

  window.addEventListener("load", () => {
    window.parent.postMessage({
      jsonrpc: "2.0",
      id: crypto.randomUUID(),
      method: "ui/getToolResult",
      params: {}
    }, "*");
  });

  function respond(approved) {
    window.parent.postMessage({
      jsonrpc: "2.0",
      method: "notifications/tool_result",
      params: { approved, action: "oauth" }
    }, "*");
  }
  document.getElementById("authorize").addEventListener("click", () => respond(true));
  document.getElementById("deny").addEventListener("click", () => respond(false));
</script>
</body>
</html>"""


# ─── MCP Server ───────────────────────────────────────────────────────────────

# Create the FastMCP server
mcp = FastMCP("pharos-discovery")

# Global state — one PharosClient per server lifetime
_client: PharosClient | None = None
_connections: dict[str, Any] = {}  # server_id → MCP connection
_installed_servers: dict[str, dict] = {}  # server_id → install metadata
_server_cards: dict[str, ServerCard] = {}  # server_id → cached card
_pending_connections: dict[str, dict] = {}  # token → pending connection details

# Signing key for pending connection tokens (HMAC-SHA256).
# In production this would be a proper server secret; for local MCP it's
# derived from the process ID + a static component.
_PENDING_SECRET = f"pharos-pending-{os.getpid()}-local".encode("utf-8")


def _get_client() -> PharosClient:
    """Get or create the global PharosClient instance."""
    global _client
    if _client is None:
        registry_url = os.environ.get("PHAROS_REGISTRY_URL", "https://getpharos.dev")
        _client = PharosClient(registry_url)
    return _client


def _get_pharos_cli() -> str:
    """Get the pharos CLI binary path."""
    return os.environ.get("PHAROS_CLI", "pharos")


# ─── MCP Tools ────────────────────────────────────────────────────────────────

@mcp.tool(meta={"ui": {"resourceUri": "ui://pharos/results"}})
async def pharos_search(
    query: str,
    limit: int = 10,
    remote_only: bool = False,
) -> str:
    """Search the PHAROS registry for MCP servers matching the query.

    Args:
        query: Natural-language search query (e.g. "echo", "flight search", "file system")
        limit: Maximum number of results to return (default 10, max 50)
        remote_only: If True, only return servers with remote transports
            (sse, streamable-http, http). Useful for environments that
            cannot install local binaries (e.g. mobile agents, cloud-only).

    Returns:
        JSON array of matching servers with id, name, description, version,
        transport, publisher, tools_count, and capabilities.
    """
    client = _get_client()
    limit = min(max(limit, 1), 50)

    filters: dict[str, Any] = {}
    if remote_only:
        filters["transport"] = ["sse", "streamable-http", "http"]

    try:
        results = await client.search(text=query, filters=filters or None, limit=limit)
    except NoServersFound:
        return json.dumps({"results": [], "message": "No servers found. Try a different query."})
    except RegistryUnavailable as e:
        return json.dumps({"error": f"Registry unavailable: {e}", "results": []})

    # Cache cards for later use
    for r in results:
        _server_cards[r.card.id] = r.card

    output = []
    for r in results:
        card = r.card
        output.append({
            "id": card.id,
            "name": card.display_name,
            "description": card.description,
            "version": card.version,
            "transport": card.transport,
            "publisher": {
                "name": card.publisher.name if card.publisher else "unknown",
                "verified": card.publisher.verified if card.publisher else False,
            },
            "tools_count": getattr(card, "tools_count", 0),
            "capabilities": card.capabilities,
            "endpoint": getattr(card, "endpoint", None),
        })

    return json.dumps({"results": output, "count": len(output)})


@mcp.tool()
async def pharos_install(server_id: str) -> str:
    """Install an MCP server from the PHAROS registry to the local machine.

    For remote transports (sse, streamable-http, http), the server is registered
    as a remote endpoint without requiring the pharos CLI. For stdio servers,
    the pharos CLI must be installed locally to download and configure the package.

    Args:
        server_id: The server ID from search results (e.g. "test-echo-server")

    Returns:
        JSON with install status, version, and install path (stdio) or
        endpoint URL (remote).
    """
    client = _get_client()

    # Check transport from cached server card or fetch from registry
    transport = None
    endpoint = None
    if server_id in _server_cards:
        card = _server_cards[server_id]
        transport = getattr(card, "transport", None)
        endpoint = getattr(card, "endpoint", None)

    # If we don't have the card cached, fetch it
    if transport is None:
        try:
            card = await client.get_server(server_id)
            transport = getattr(card, "transport", None)
            endpoint = getattr(card, "endpoint", None)
            _server_cards[server_id] = card
        except Exception:
            pass  # Fall through to CLI install attempt

    # Remote transport: register endpoint without CLI
    if transport and transport.lower() in ("sse", "streamable-http", "http"):
        if not endpoint:
            return json.dumps({
                "error": f"Server '{server_id}' has transport '{transport}' but no endpoint URL",
                "server_id": server_id,
            })
        _installed_servers[server_id] = {
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "transport": transport,
            "endpoint": endpoint,
        }
        return json.dumps({
            "status": "registered",
            "server_id": server_id,
            "transport": transport,
            "endpoint": endpoint,
            "message": f"Remote server registered. Use pharos_connect to connect.",
        })

    # stdio transport: use pharos CLI
    cli = _get_pharos_cli()

    try:
        proc = await asyncio.create_subprocess_exec(
            cli, "install", server_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        return json.dumps({"error": "Install timed out (120s)", "server_id": server_id})
    except FileNotFoundError:
        return json.dumps({
            "error": f"pharos CLI not found at '{cli}'",
            "server_id": server_id,
            "hint": "For remote servers, use pharos_search(remote_only=True) to find servers that don't require local installation. To install stdio servers, install the pharos CLI first: pip install pharos-mcp",
        })

    if proc.returncode != 0:
        return json.dumps({
            "error": "Install failed",
            "stderr": stderr.decode() if stderr else "",
            "server_id": server_id,
        })

    _installed_servers[server_id] = {
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }

    return json.dumps({
        "status": "installed",
        "server_id": server_id,
        "output": stdout.decode().strip() if stdout else "",
    })


def _sign_pending(token: str, server_id: str) -> str:
    """Sign a pending connection token with HMAC-SHA256."""
    import hmac as _hmac
    import hashlib as _hashlib
    payload = f"{token}:{server_id}".encode("utf-8")
    return _hmac.new(_PENDING_SECRET, payload, _hashlib.sha256).hexdigest()


def _verify_pending(token: str, server_id: str, signature: str) -> bool:
    """Verify a pending connection token's signature."""
    import hmac as _hmac
    expected = _sign_pending(token, server_id)
    return _hmac.compare_digest(signature, expected)


@mcp.tool(meta={"ui": {"resourceUri": "ui://pharos/approval"}})
async def pharos_connect(server_id: str, purpose: str = "User request") -> str:
    """Request a connection to a running MCP server.

    Returns an approval UI showing the server details, publisher verification,
    requested scopes, and purpose. The user must review and approve the
    connection by calling pharos_approve with the returned token.

    This is a two-step flow:
    1. pharos_connect — returns server details + pending token (NOT connected yet)
    2. pharos_approve — user confirms, connection is established

    Args:
        server_id: The server ID to connect to
        purpose: Why the connection is being requested (shown to user)

    Returns:
        JSON with server details and a pending approval token.
    """
    client = _get_client()

    # Get the server card (from cache or registry)
    if server_id in _server_cards:
        card = _server_cards[server_id]
    else:
        try:
            card = await client.get_server(server_id)
            _server_cards[server_id] = card
        except Exception as e:
            return json.dumps({"error": f"Server not found: {server_id}", "detail": str(e)})

    # Check if already connected
    if server_id in _connections:
        return json.dumps({
            "status": "already_connected",
            "server_id": server_id,
            "message": "Already connected. Use pharos_list_tools to see available tools.",
        })

    # Resolve local endpoint if needed
    endpoint = getattr(card, "endpoint", None)
    if not endpoint and "http+sse" in (card.transport or []):
        endpoint = await _resolve_local_endpoint(server_id)
        if endpoint:
            card.endpoint = endpoint  # type: ignore

    # Generate a pending connection token
    raw_token = f"pc-{server_id}-{int(time.time())}-{os.urandom(4).hex()}"
    signature = _sign_pending(raw_token, server_id)
    expires_at = int(time.time()) + 300  # 5-minute expiry

    # Store pending connection details
    _pending_connections[raw_token] = {
        "server_id": server_id,
        "card": card,
        "endpoint": endpoint,
        "purpose": purpose,
        "signature": signature,
        "expires_at": expires_at,
    }

    # Build approval data for the UI
    approval_data = {
        "server": {
            "id": card.id,
            "display_name": card.display_name,
            "version": card.version,
            "transport": card.transport,
            "publisher": {
                "name": card.publisher.name if card.publisher else "unknown",
                "verified": card.publisher.verified if card.publisher else False,
            },
            "endpoint": endpoint or "N/A",
        },
        "purpose": purpose,
        "scopes": ["tools:call"],
        "capabilities": card.capabilities,
    }

    return json.dumps({
        "status": "pending_approval",
        "server_id": server_id,
        "approval_token": raw_token,
        "expires_in": 300,
        "message": f"Connection to '{card.display_name}' is pending. "
                    "Ask the user to approve, then call pharos_approve "
                    "with the approval_token to complete the connection.",
        "approval_data": approval_data,
    })


@mcp.tool()
async def pharos_approve(approval_token: str) -> str:
    """Approve a pending MCP server connection.

    After pharos_connect returns a pending approval token, the user must
    confirm they want to connect. This tool completes the connection.

    Args:
        approval_token: The token returned by pharos_connect

    Returns:
        JSON with connection status and available tools.
    """
    # Look up the pending connection
    pending = _pending_connections.get(approval_token)
    if pending is None:
        return json.dumps({
            "error": "Invalid or unknown approval token. "
                     "Call pharos_connect first to get a new token.",
        })

    # Verify signature
    if not _verify_pending(approval_token, pending["server_id"], pending["signature"]):
        del _pending_connections[approval_token]
        return json.dumps({"error": "Invalid approval token signature."})

    # Check expiry
    if time.time() >= pending["expires_at"]:
        del _pending_connections[approval_token]
        return json.dumps({
            "error": "Approval token expired. Call pharos_connect again for a new token.",
        })

    server_id = pending["server_id"]
    card = pending["card"]
    endpoint = pending["endpoint"]

    # Clean up the pending token (one-time use)
    del _pending_connections[approval_token]

    # Check if already connected (race condition guard)
    if server_id in _connections:
        return json.dumps({
            "status": "already_connected",
            "server_id": server_id,
            "message": "Already connected. Use pharos_list_tools to see available tools.",
        })

    # Build an approval token for the connection manager
    token = ApprovalToken(
        token_id=f"tok-{server_id}-{int(time.time())}",
        server_id=server_id,
        approved_scopes=["tools:call"],
        approved_capabilities=card.capabilities,
        approved_oauth_scopes=[],
        duration="session",
        approved_at=datetime.now(timezone.utc).isoformat(),
        expires_at=str(int(time.time()) + 3600),
        signature="unsigned",
    )

    # Connect to the server
    try:
        mgr = ConnectionManager()
        connection = await mgr.connect(card, token)
        _connections[server_id] = connection

        # List initial tools
        tools = await _list_server_tools(server_id)

        return json.dumps({
            "status": "connected",
            "server_id": server_id,
            "endpoint": endpoint,
            "tools_count": len(tools),
            "tools": tools,
        })
    except ConnectionFailed as e:
        return json.dumps({"error": f"Connection failed: {e}", "server_id": server_id})
    except Exception as e:
        return json.dumps({"error": f"Connection error: {e}", "server_id": server_id})


@mcp.tool()
async def pharos_list_tools(server_id: str) -> str:
    """List the available tools on a connected MCP server.

    Args:
        server_id: The server ID of a connected server

    Returns:
        JSON array of tools with name, description, and input schema.
    """
    if server_id not in _connections:
        return json.dumps({
            "error": f"Not connected to '{server_id}'. Use pharos_connect first.",
        })

    tools = await _list_server_tools(server_id)
    return json.dumps({"server_id": server_id, "tools": tools, "count": len(tools)})


@mcp.tool()
async def pharos_call_tool(
    server_id: str,
    tool_name: str,
    arguments: dict | None = None,
) -> str:
    """Call a tool on a connected MCP server.

    Args:
        server_id: The server ID of a connected server
        tool_name: The name of the tool to call
        arguments: Arguments to pass to the tool (as a JSON object)

    Returns:
        JSON with the tool call result.
    """
    if server_id not in _connections:
        return json.dumps({
            "error": f"Not connected to '{server_id}'. Use pharos_connect first.",
        })

    connection = _connections[server_id]
    arguments = arguments or {}

    try:
        # Call the tool on the remote MCP server
        result = await connection.call_tool(tool_name, arguments)
        return json.dumps({
            "server_id": server_id,
            "tool": tool_name,
            "result": result,
        })
    except Exception as e:
        return json.dumps({
            "error": f"Tool call failed: {e}",
            "server_id": server_id,
            "tool": tool_name,
        })


# ─── MCP Resources (MCP Apps UI) ──────────────────────────────────────────────

MCP_APP_MIME = "text/html;profile=mcp-app"


@mcp.resource("ui://pharos/approval", mime_type=MCP_APP_MIME)
def approval_resource() -> str:
    """Approval UI card (MCP Apps). Rendered when user must approve a server connection."""
    return APPROVAL_HTML


@mcp.resource("ui://pharos/oauth", mime_type=MCP_APP_MIME)
def oauth_resource() -> str:
    """OAuth consent UI (MCP Apps). Rendered during OAuth flows."""
    return OAUTH_HTML


@mcp.resource("ui://pharos/results", mime_type=MCP_APP_MIME)
def results_resource() -> str:
    """Search results gallery UI (MCP Apps). Rendered after pharos_search."""
    return RESULTS_HTML


# ─── Helper Functions ─────────────────────────────────────────────────────────

async def _resolve_local_endpoint(server_id: str) -> str | None:
    """Check if a server is running locally and return its endpoint."""
    import os.path

    # Check pharos run directory for PID file
    # Sanitize server_id to prevent path traversal (e.g. "../../etc/passwd")
    safe_id = os.path.basename(server_id)
    run_dir = os.path.expanduser("~/.pharos/run")
    pid_file = os.path.join(run_dir, f"{safe_id}.pid")

    if not os.path.exists(pid_file):
        return None

    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())

        # Check if process is alive
        os.kill(pid, 0)

        # Find listening port
        proc = await asyncio.create_subprocess_exec(
            "ss", "-tlnp",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        for line in stdout.decode().splitlines():
            if str(pid) in line:
                # Parse port from line like "LISTEN 0 4096 0.0.0.0:8765 0.0.0.0:*"
                parts = line.split()
                for part in parts:
                    if ":" in part and part.count(":") == 1:
                        port = part.split(":")[1]
                        if port.isdigit():
                            return f"http://127.0.0.1:{port}"
    except (ProcessLookupError, ValueError, OSError):
        pass

    return None


async def _list_server_tools(server_id: str) -> list[dict]:
    """List tools on a connected server."""
    if server_id not in _connections:
        return []

    connection = _connections[server_id]
    try:
        result = await connection.list_tools()
        tools = []
        for tool in result.tools:
            tools.append({
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
            })
        return tools
    except Exception:
        return []


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    """Run the PHAROS Discovery MCP server.

    Transport is selected by the PHAROS_MCP_TRANSPORT env var:
    - "stdio" (default) — for local use (Claude Desktop, VS Code)
    - "sse"              — for remote use (LibreChat, web clients)
    - "streamable-http"  — for newer MCP clients

    For SSE/streamable-http, set host/port via env vars:
        PHAROS_MCP_HOST=0.0.0.0 PHAROS_MCP_PORT=8766
    (These map to FastMCP's settings.host and settings.port)
    """
    transport = os.environ.get("PHAROS_MCP_TRANSPORT", "stdio")

    # Configure host/port for network transports via settings
    if transport in ("sse", "streamable-http"):
        mcp.settings.host = os.environ.get("PHAROS_MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("PHAROS_MCP_PORT", "8766"))

        # Configure DNS rebinding protection to allow container/Docker hostnames
        from mcp.server.transport_security import TransportSecuritySettings
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "0.0.0.0:*",
                           "pharos-mcp:*", "host.docker.internal:*"],
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*",
                             "http://pharos-mcp:*", "http://host.docker.internal:*"],
        )

    # mypy/pyright: transport is str but run() wants a Literal; cast at runtime
    mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
