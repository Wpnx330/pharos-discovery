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
import uuid
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

APPROVAL_HTML_TEMPLATE = """<!DOCTYPE html>
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
  html, body { height: 100%; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 16px;
    display: flex;
    flex-direction: column;
    min-height: 100vh;
  }
  .header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-shrink: 0; }
  .logo { font-size: 20px; }
  .title { font-size: 16px; font-weight: 600; }
  .purpose {
    background: rgba(88, 166, 255, 0.05);
    border-left: 3px solid var(--accent);
    padding: 10px 12px;
    border-radius: 0 6px 6px 0;
    margin-bottom: 12px;
    font-size: 13px;
    flex-shrink: 0;
  }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 12px;
    flex-shrink: 0;
  }
  .server-name { font-size: 18px; font-weight: 700; margin-bottom: 2px; }
  .server-desc { font-size: 13px; color: var(--text-muted); margin-bottom: 12px; }
  .detail-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .detail-cell {
    background: var(--card-bg);
    padding: 8px 10px;
  }
  .detail-label { color: var(--text-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
  .detail-value { font-size: 13px; font-weight: 500; margin-top: 2px; }
  .badge {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 600;
    margin-left: 4px;
    vertical-align: middle;
  }
  .badge-verified { background: rgba(63, 185, 80, 0.15); color: var(--success); }
  .badge-warning { background: rgba(248, 81, 73, 0.15); color: var(--danger); }
  .badge-cap { background: rgba(88, 166, 255, 0.1); color: var(--accent); }
  .tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
  .tag { background: rgba(88, 166, 255, 0.08); color: var(--accent); padding: 2px 8px; border-radius: 4px; font-size: 11px; }
  .scopes { margin-top: 10px; flex-shrink: 0; }
  .scope-label { color: var(--text-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
  .scope-chip {
    display: inline-block;
    background: rgba(88, 166, 255, 0.1);
    color: var(--accent);
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    margin: 2px 2px 0 0;
  }
  .actions { display: flex; gap: 10px; margin-top: auto; padding-top: 12px; flex-shrink: 0; }
  .btn {
    flex: 1;
    padding: 10px 20px;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.15s;
  }
  .btn:hover { opacity: 0.85; }
  .btn-approve { background: var(--success); color: #fff; }
  .btn-deny { background: var(--card-bg); color: var(--text); border: 1px solid var(--border); }
  #status { text-align: center; margin-top: 8px; font-size: 13px; color: var(--text-muted); }
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
    <div class="server-desc" id="server-desc"></div>
    <div class="detail-grid" id="details"></div>
    <div class="tags" id="tags"></div>
  </div>
  <div class="scopes" id="scopes"></div>
  <div class="actions">
    <button class="btn btn-deny" id="deny">Deny</button>
    <button class="btn btn-approve" id="approve">Approve Connection</button>
  </div>
  <div id="status"></div>
<script>
  // Data injected by server — works even if host doesn't send postMessage
  const TOOL_DATA = __APPROVAL_DATA__;

  function render() {
    const data = TOOL_DATA;
    if (!data || !data.server) return;
    const s = data.server;

    document.getElementById("purpose").textContent = "Purpose: " + (data.purpose || "User request");
    document.getElementById("server-name").textContent = s.display_name || s.name || s.id;
    document.getElementById("server-desc").textContent = s.description || "";

    // Build detail grid
    const details = document.getElementById("details");
    const rows = [
      ["Publisher", s.publisher?.name || "unknown", s.publisher?.verified],
      ["Version", s.version || "N/A"],
      ["Transport", (s.transport || []).join(", ") || "N/A"],
      ["Endpoint", s.endpoint || "N/A"],
      ["Tools", String(s.tools_count || 0)],
      ["Pricing", s.pricing || "free"],
    ];
    rows.forEach(([label, value, verified]) => {
      const cell = document.createElement("div");
      cell.className = "detail-cell";
      const lbl = document.createElement("div");
      lbl.className = "detail-label";
      lbl.textContent = label;
      cell.appendChild(lbl);
      const val = document.createElement("div");
      val.className = "detail-value";
      val.textContent = value;
      if (verified === true) {
        const badge = document.createElement("span");
        badge.className = "badge badge-verified";
        badge.textContent = "verified";
        val.appendChild(badge);
      } else if (verified === false && label === "Publisher") {
        const badge = document.createElement("span");
        badge.className = "badge badge-warning";
        badge.textContent = "unverified";
        val.appendChild(badge);
      }
      cell.appendChild(val);
      details.appendChild(cell);
    });

    // Tags
    const tagsEl = document.getElementById("tags");
    (s.tags || []).forEach(t => {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = t;
      tagsEl.appendChild(tag);
    });

    // Capabilities as badges
    if (s.capabilities && s.capabilities.length) {
      const capRow = document.createElement("div");
      capRow.style.marginTop = "8px";
      s.capabilities.forEach(c => {
        const badge = document.createElement("span");
        badge.className = "badge badge-cap";
        badge.textContent = c;
        capRow.appendChild(badge);
      });
      tagsEl.appendChild(capRow);
    }

    // Scopes
    const scopesEl = document.getElementById("scopes");
    const scopes = data.scopes || [];
    if (scopes.length) {
      const label = document.createElement("div");
      label.className = "scope-label";
      label.textContent = "Requested Access";
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
    document.getElementById("status").textContent = approved ? "Approved" : "Denied";

    // Send result to host via postMessage (if supported)
    window.parent.postMessage({
      jsonrpc: "2.0",
      method: "notifications/tool_result",
      params: {
        approved: approved,
        approval_token: TOOL_DATA?.approval_token || "",
        approval_nonce: TOOL_DATA?.approval_nonce || "",
        timestamp: new Date().toISOString()
      }
    }, "*");
  }

  render();

  // Also listen for postMessage from host (dual-mode: works with or without)
  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (!msg || msg.jsonrpc !== "2.0") return;
    if (msg.method === "notifications/tool_result" && msg.params) {
      // Host is sending data — re-render with it
      if (msg.params.server) {
        Object.assign(TOOL_DATA, msg.params);
        render();
      }
    }
  });

  document.getElementById("approve").addEventListener("click", () => sendResponse(true));
  document.getElementById("deny").addEventListener("click", () => sendResponse(false));
</script>
</body>
</html>"""

RESULTS_HTML_TEMPLATE = """<!DOCTYPE html>
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
  html, body { height: 100%; overflow: hidden; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    display: flex;
    flex-direction: column;
    height: 100vh;
  }
  .header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 16px;
    flex-shrink: 0;
    border-bottom: 1px solid var(--border);
  }
  .logo { font-size: 20px; }
  .title { font-size: 16px; font-weight: 600; }
  .result-count { font-size: 12px; color: var(--text-muted); margin-left: auto; }
  #results {
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px;
  }
  .result-card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    margin-bottom: 6px;
    overflow: hidden;
    transition: border-color 0.15s;
  }
  .result-card:hover { border-color: var(--accent); }
  .result-card.expanded { border-color: var(--accent); }
  .card-header {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 10px 12px;
    cursor: pointer;
    user-select: none;
  }
  .expand-icon {
    font-size: 12px;
    color: var(--text-muted);
    flex-shrink: 0;
    margin-top: 2px;
    transition: transform 0.2s;
  }
  .result-card.expanded .expand-icon { transform: rotate(90deg); }
  .card-summary { flex: 1; min-width: 0; }
  .result-name { font-size: 14px; font-weight: 600; margin-bottom: 2px; }
  .result-desc {
    font-size: 12px;
    color: var(--text-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .result-card.expanded .result-desc {
    white-space: normal;
    overflow: visible;
    text-overflow: clip;
  }
  .result-meta {
    display: flex;
    gap: 6px;
    font-size: 11px;
    color: var(--text-muted);
    flex-wrap: wrap;
    align-items: center;
    margin-top: 4px;
  }
  .badge {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 600;
  }
  .badge-verified { background: rgba(63, 185, 80, 0.15); color: var(--success); }
  .badge-tools { background: rgba(88, 166, 255, 0.1); color: var(--accent); }
  .badge-tag { background: rgba(139, 148, 158, 0.1); color: var(--text-muted); }
  .card-details {
    display: none;
    padding: 0 12px 12px 32px;
    font-size: 12px;
    color: var(--text-muted);
    line-height: 1.6;
  }
  .result-card.expanded .card-details { display: block; }
  .detail-row { display: flex; gap: 6px; margin-bottom: 2px; }
  .detail-label { color: var(--text); font-weight: 500; min-width: 80px; }
  .detail-value { word-break: break-word; }
  .tag-list { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
  #empty { text-align: center; padding: 30px; color: var(--text-muted); font-size: 13px; }
</style>
</head>
<body>
  <div class="header">
    <span class="logo">🔍</span>
    <span class="title">PHAROS Search Results</span>
    <span class="result-count" id="count"></span>
  </div>
  <div id="results"></div>
  <div id="empty" style="display:none">No servers found. Try a different query.</div>
<script>
  // Data injected by server — works even if host doesn't send postMessage
  const TOOL_DATA = __RESULTS_DATA__;

  function renderResults(data) {
    const results = data?.results || [];
    const container = document.getElementById("results");
    const countEl = document.getElementById("count");
    if (!results.length) {
      document.getElementById("empty").style.display = "block";
      countEl.textContent = "";
      return;
    }
    countEl.textContent = results.length + " server" + (results.length > 1 ? "s" : "");
    container.innerHTML = "";
    results.forEach((r, i) => {
      const card = document.createElement("div");
      card.className = "result-card";
      card.dataset.id = r.id;
      card.dataset.index = i;

      // --- Collapsible header ---
      const header = document.createElement("div");
      header.className = "card-header";

      const icon = document.createElement("span");
      icon.className = "expand-icon";
      icon.textContent = "▶";
      header.appendChild(icon);

      const summary = document.createElement("div");
      summary.className = "card-summary";

      const name = document.createElement("div");
      name.className = "result-name";
      name.textContent = r.display_name || r.name || r.id;
      summary.appendChild(name);

      const desc = document.createElement("div");
      desc.className = "result-desc";
      desc.textContent = r.description || "No description available.";
      summary.appendChild(desc);

      // Meta line (always visible)
      const meta = document.createElement("div");
      meta.className = "result-meta";

      const ver = document.createElement("span");
      ver.textContent = r.version || "v?";
      meta.appendChild(ver);

      const sep1 = document.createElement("span");
      sep1.textContent = "·";
      meta.appendChild(sep1);

      const tr = document.createElement("span");
      tr.textContent = Array.isArray(r.transport) ? r.transport.join(", ") : (r.transport || "unknown");
      meta.appendChild(tr);

      const sep2 = document.createElement("span");
      sep2.textContent = "·";
      meta.appendChild(sep2);

      const pub = document.createElement("span");
      pub.textContent = r.publisher?.name || r.publisher?.id || "unknown";
      if (r.publisher?.verified) {
        const badge = document.createElement("span");
        badge.className = "badge badge-verified";
        badge.textContent = "✓ verified";
        pub.appendChild(document.createTextNode(" "));
        pub.appendChild(badge);
      }
      meta.appendChild(pub);

      if (r.tools_count) {
        const sep3 = document.createElement("span");
        sep3.textContent = "·";
        meta.appendChild(sep3);
        const tb = document.createElement("span");
        tb.className = "badge badge-tools";
        tb.textContent = r.tools_count + " tools";
        meta.appendChild(tb);
      }
      summary.appendChild(meta);
      header.appendChild(summary);
      card.appendChild(header);

      // --- Expandable details ---
      const details = document.createElement("div");
      details.className = "card-details";

      if (r.description && r.description.length > 60) {
        const dr = document.createElement("div");
        dr.className = "detail-row";
        dr.innerHTML = '<span class="detail-label">Description</span><span class="detail-value">' + escapeHtml(r.description) + '</span>';
        details.appendChild(dr);
      }

      if (r.id) {
        const dr = document.createElement("div");
        dr.className = "detail-row";
        dr.innerHTML = '<span class="detail-label">Server ID</span><span class="detail-value">' + escapeHtml(r.id) + '</span>';
        details.appendChild(dr);
      }

      if (r.endpoint) {
        const dr = document.createElement("div");
        dr.className = "detail-row";
        dr.innerHTML = '<span class="detail-label">Endpoint</span><span class="detail-value">' + escapeHtml(r.endpoint) + '</span>';
        details.appendChild(dr);
      }

      if (r.license) {
        const dr = document.createElement("div");
        dr.className = "detail-row";
        dr.innerHTML = '<span class="detail-label">License</span><span class="detail-value">' + escapeHtml(r.license) + '</span>';
        details.appendChild(dr);
      }

      if (r.pricing) {
        const dr = document.createElement("div");
        dr.className = "detail-row";
        dr.innerHTML = '<span class="detail-label">Pricing</span><span class="detail-value">' + escapeHtml(r.pricing) + '</span>';
        details.appendChild(dr);
      }

      if (r.capabilities && r.capabilities.length) {
        const dr = document.createElement("div");
        dr.className = "detail-row";
        dr.innerHTML = '<span class="detail-label">Capabilities</span><span class="detail-value">' + r.capabilities.map(escapeHtml).join(", ") + '</span>';
        details.appendChild(dr);
      }

      if (r.tags && r.tags.length) {
        const tagLabel = document.createElement("div");
        tagLabel.className = "detail-row";
        tagLabel.innerHTML = '<span class="detail-label">Tags</span>';
        const tagList = document.createElement("div");
        tagList.className = "tag-list";
        r.tags.forEach(t => {
          const tag = document.createElement("span");
          tag.className = "badge badge-tag";
          tag.textContent = t;
          tagList.appendChild(tag);
        });
        tagLabel.appendChild(tagList);
        details.appendChild(tagLabel);
      }

      card.appendChild(details);

      // Toggle expand/collapse on header click
      header.addEventListener("click", (e) => {
        e.stopPropagation();
        card.classList.toggle("expanded");
      });

      // Double-click selects the server (sends postMessage to host)
      card.addEventListener("dblclick", () => {
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

      container.appendChild(card);
    });
  }

  function escapeHtml(s) {
    if (!s) return "";
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }

  renderResults(TOOL_DATA);

  // Also listen for postMessage from host (dual-mode)
  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (!msg || msg.jsonrpc !== "2.0") return;
    if (msg.method === "notifications/tool_result") {
      renderResults(msg.params);
    }
  });
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

# Last tool result data for UI resource rendering. The MCP Apps spec says the
# host fetches the resource via resources/read and renders it in an iframe.
# Some hosts (like LibreChat nazq fork) don't reliably send postMessage with
# tool data, so we inject the data directly into the HTML as a JS variable.
# Token-keyed caches prevent race conditions when multiple calls happen
# concurrently — each call stores its data under its own unique key.
_search_results_cache: dict[str, list[dict]] = {}  # search_id → results
_approval_data_cache: dict[str, dict] = {}  # approval_token → data
_current_search_id: str | None = None  # most recent (for resource handler)
_current_approval_token: str | None = None  # most recent (for resource handler)

# Max cache entries before auto-cleanup of oldest
_MAX_CACHE_SIZE = 20

# Physical approval mode — when set, pharos_approve requires a UI-originated
# token that the AI agent cannot generate. This prevents the AI from
# auto-approving connections in end-user chatbot scenarios (scenario 3).
# Set PHAROS_REQUIRE_PHYSICAL_APPROVAL=true to enable physical approval
# (prevents AI from calling pharos_approve directly). Default: false for CLI/dev,
# set to true in Docker compose for end-user chatbot deployments.
_REQUIRE_PHYSICAL_APPROVAL = os.environ.get(
    "PHAROS_REQUIRE_PHYSICAL_APPROVAL", "false"
).lower() in ("true", "1", "yes")

# Signing key for pending connection tokens (HMAC-SHA256).
# In production this would be a proper server secret; for local MCP it's
# derived from the process ID + a static component.
_PENDING_SECRET = f"pharos-pending-{os.getpid()}-local".encode("utf-8")


def _get_client() -> PharosClient:
    """Get or create the global PharosClient instance."""
    global _client
    if _client is None:
        registry_url = os.environ.get("PHAROS_REGISTRY_URL", "https://api.getpharos.dev")
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
            (sse, streamable-http, http). Only set this to True when the
            environment cannot install local binaries (e.g. mobile agents,
            cloud-only). For desktop environments with local CLI access,
            leave as False (default) to get all available servers.

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

    # Store for UI resource rendering (token-scoped to prevent race conditions)
    search_id = f"sr-{int(time.time())}-{os.urandom(4).hex()}"
    _search_results_cache[search_id] = output
    global _current_search_id
    _current_search_id = search_id
    # Prune oldest entries
    if len(_search_results_cache) > _MAX_CACHE_SIZE:
        oldest = next(iter(_search_results_cache))
        del _search_results_cache[oldest]

    return json.dumps({"results": output, "count": len(output), "search_id": search_id})


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

    # Security: cap pending connections to prevent memory exhaustion.
    # Clean up expired entries first, then enforce a hard limit.
    now = int(time.time())
    expired = [k for k, v in _pending_connections.items() if now >= v["expires_at"]]
    for k in expired:
        del _pending_connections[k]
    if len(_pending_connections) >= 50:
        return json.dumps({
            "error": "Too many pending connections. Wait for existing tokens to expire (5 min) and try again.",
        })

    # Generate a per-token approval nonce (UUID4).
    # This nonce is injected into the HTML approval card but NEVER included
    # in the tool response JSON. The AI cannot see it, cannot guess it,
    # and cannot pass it to pharos_approve. Only the physical button click
    # in the UI card sends it back.
    approval_nonce = str(uuid.uuid4())

    # Store pending connection details
    _pending_connections[raw_token] = {
        "server_id": server_id,
        "card": card,
        "endpoint": endpoint,
        "purpose": purpose,
        "signature": signature,
        "expires_at": expires_at,
        "approval_nonce": approval_nonce,
    }

    # Build approval data for the UI (includes nonce — but this dict is
    # only used for the HTML resource, NOT returned to the AI)
    approval_data = {
        "server": {
            "id": str(card.id),
            "display_name": str(card.display_name),
            "description": str(card.description),
            "version": str(card.version),
            "transport": list(card.transport) if card.transport else [],
            "publisher": {
                "name": str(card.publisher.name) if card.publisher else "unknown",
                "verified": bool(card.publisher.verified) if card.publisher else False,
            },
            "endpoint": str(endpoint) if endpoint else "N/A",
            "capabilities": list(card.capabilities) if card.capabilities else [],
            "tools_count": int(getattr(card, "tools_count", 0) or 0),
            "pricing": "free",
            "tags": list(card.tags) if card.tags else [],
            "documentation_url": str(card.documentation_url) if card.documentation_url else None,
        },
        "purpose": purpose,
        "scopes": ["tools:call"],
        "approval_token": raw_token,
        "approval_nonce": approval_nonce,  # UI-only — never returned to AI
    }

    # Store for UI resource rendering (token-scoped to prevent race conditions)
    _approval_data_cache[raw_token] = approval_data
    global _current_approval_token
    _current_approval_token = raw_token
    # Prune oldest entries
    if len(_approval_data_cache) > _MAX_CACHE_SIZE:
        oldest = next(iter(_approval_data_cache))
        del _approval_data_cache[oldest]

    # Return to AI — NO nonce field. AI only gets the token.
    return json.dumps({
        "status": "pending_approval",
        "server_id": server_id,
        "approval_token": raw_token,
        "expires_in": 300,
        "message": f"Connection to '{card.display_name}' is pending user "
                    "approval. An approval card has been rendered above. "
                    "DO NOT call pharos_approve yourself — the user must "
                    "physically click the Approve button. Tell the user: "
                    "'Please click Approve in the PHAROS approval card to "
                    "connect to this server.'",
    })


@mcp.tool()
async def pharos_approve(approval_token: str, approval_nonce: str = "") -> str:
    """Approve a pending MCP server connection.

    After pharos_connect returns a pending approval token, the user must
    confirm they want to connect. This tool completes the connection.

    In end-user mode (PHAROS_REQUIRE_PHYSICAL_APPROVAL=true), this tool
    requires a valid approval_nonce that was generated server-side and
    injected into the HTML approval card. The nonce is never returned to
    the AI in the tool response, so the AI cannot guess it (UUID4).
    Only the physical button click in the UI card sends the nonce.

    The AI should tell the user to click the Approve button. Do not
    attempt to call this tool directly — it will fail without the nonce.

    Args:
        approval_token: The token returned by pharos_connect
        approval_nonce: Set by the UI approval card only. The AI does not
            have access to this value and should not set it.

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

    # Physical approval enforcement — verify the per-token nonce.
    # The nonce is a UUID4 generated server-side in pharos_connect.
    # It's injected into the HTML card data but never returned to the AI
    # in the tool response JSON. The AI cannot see it, cannot guess it
    # (122 bits of entropy), and therefore cannot bypass physical approval.
    if _REQUIRE_PHYSICAL_APPROVAL:
        stored_nonce = pending.get("approval_nonce")
        if not stored_nonce or approval_nonce != stored_nonce:
            return json.dumps({
                "error": "Physical approval required. The user must click the "
                         "Approve button in the approval card. AI agents cannot "
                         "approve connections on behalf of users in this mode.",
                "hint": "Tell the user to click the Approve button in the "
                        "PHAROS approval card above. If no card is visible, "
                        "ask them to scroll up or expand the pharos tool result.",
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
    # Use token-scoped data if available, fall back to most recent
    token = _current_approval_token
    data = _approval_data_cache.get(token, {}) if token else {}
    # Escape < > to prevent </script> breakout (XSS safe JSON-in-HTML)
    safe_json = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e")
    return APPROVAL_HTML_TEMPLATE.replace("__APPROVAL_DATA__", safe_json)


@mcp.resource("ui://pharos/oauth", mime_type=MCP_APP_MIME)
def oauth_resource() -> str:
    """OAuth consent UI (MCP Apps). Rendered during OAuth flows."""
    return OAUTH_HTML


@mcp.resource("ui://pharos/results", mime_type=MCP_APP_MIME)
def results_resource() -> str:
    """Search results gallery UI (MCP Apps). Rendered after pharos_search."""
    # Use token-scoped data if available, fall back to most recent
    search_id = _current_search_id
    results = _search_results_cache.get(search_id, []) if search_id else []
    data = {"results": results}
    # Escape < > to prevent </script> breakout (XSS safe JSON-in-HTML)
    safe_json = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e")
    return RESULTS_HTML_TEMPLATE.replace("__RESULTS_DATA__", safe_json)


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
