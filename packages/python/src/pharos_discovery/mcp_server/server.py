"""
PHAROS Discovery MCP Server — main server module.

Wraps PharosClient (the discovery SDK) and exposes its operations as MCP tools.
Implements MCP Apps (2026-01-26) for visual approval and OAuth flows.

Two operating modes are selected by the PHAROS_MCP_APPS environment variable:

CLI mode (default, PHAROS_MCP_APPS unset/false):
    Registers pharos_search, pharos_info, pharos_install, pharos_remove,
    pharos_list, pharos_publish as direct tools — no iframe, no approval UI.

Apps mode (PHAROS_MCP_APPS=true/1/yes):
    Registers pharos_search_apps, pharos_info_apps, pharos_install_apps,
    pharos_remove_apps, pharos_list_apps, pharos_publish_apps with
    meta={"ui": {"resourceUri": "ui://pharos/<resource>"}} annotations so
    the host renders results/approval in a sandboxed iframe.

Non-A/B tools (pharos_start, pharos_stop, daemon tools, etc.) are
registered unconditionally in both modes.

The /approve HTTP endpoint (POST) handles approval confirmations from the
iframe UI. It is NOT exposed as an MCP tool — the AI cannot see or call it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server.fastmcp import FastMCP, Context
from mcp.types import Resource, TextResourceContents

from pharos_discovery.client import PharosClient
from pharos_discovery.adapters.registry import SearchResults
from pharos_discovery.models import ServerCard, ApprovalRequest, ApprovalResponse, ApprovalToken
from pharos_discovery.connection.manager import ConnectionManager, MCPConnection
from pharos_discovery.errors import (
    NoServersFound,
    RegistryUnavailable,
    ApprovalDenied,
    ConnectionFailed,
    HeadlessApprovalRequired,
)
from pharos_discovery.install_kind import (
    classify_install_kind,
    remote_only_blocks,
    launch_command,
)

# ─── Mode Detection ────────────────────────────────────────────────────────────

# When PHAROS_MCP_APPS is set to true/1/yes, the server registers _apps variants
# (pharos_search_apps, pharos_install_apps, etc.) with UI resource annotations
# instead of the direct CLI-mode tools. This enables A/B switching between a
# plain CLI experience and a full MCP Apps iframe experience.
MCP_APPS_MODE = os.environ.get("PHAROS_MCP_APPS", "").lower() in ("true", "1", "yes")


import re as _re


from markdown_it import MarkdownIt

_md_parser = MarkdownIt("commonmark", {"html": False, "linkify": True, "breaks": True})

def _render_markdown(text: str) -> str:
    """Parse GitHub-flavored markdown into safe HTML for iframe display.

    Uses markdown-it-py (already installed). Renders badges as <img>, links as
    clickable <a>, headers, lists, code blocks — full formatting. HTML input
    is disabled (html=False) so raw HTML tags in READMEs are escaped, not
    injected. Links get target=_blank rel=noopener via a render rule.
    """
    if not text or not isinstance(text, str):
        return ""

    # Add target=_blank to all links
    def _link_open(self, tokens, idx, options, env):
        tokens[idx].attrSet("target", "_blank")
        tokens[idx].attrSet("rel", "noopener noreferrer")
        return self.renderToken(tokens, idx, options, env)

    _md_parser.add_render_rule("link_open", _link_open)

    html = _md_parser.render(text)
    return html.strip()

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

RESULTS_APPS_TEMPLATE = """<!DOCTYPE html>
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
    max-height: 3.6em;
    line-height: 1.4;
  }
  .result-card.expanded .result-desc {
    max-height: 400px;
    overflow-y: auto;
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
  .badge-version { background: rgba(88, 166, 255, 0.08); color: var(--accent); }
  .badge-transport { background: rgba(139, 148, 158, 0.12); color: var(--text-muted); font-family: var(--font-mono, monospace); }
  .badge-publisher { background: rgba(139, 148, 158, 0.12); color: var(--text-muted); }
  .badge-downloads { background: rgba(63, 185, 80, 0.1); color: var(--success); }
  .badge-category { background: rgba(139, 148, 158, 0.08); color: var(--text-muted); }
  .badge-pricing { background: rgba(248, 81, 73, 0.1); color: var(--danger); }
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

  /* Markdown rendering in result-desc */
  .result-desc img { max-height: 20px; width: auto; vertical-align: middle; display: inline-block; margin: 0 2px; opacity: 0.9; }
  .result-desc a { color: var(--accent); text-decoration: none; }
  .result-desc a:hover { text-decoration: underline; }
  .result-desc code { font-family: 'SF Mono', 'Consolas', monospace; font-size: 0.9em; background: var(--card-bg); padding: 1px 4px; border-radius: 3px; }
  .result-desc pre { font-family: 'SF Mono', 'Consolas', monospace; font-size: 12px; background: var(--card-bg); padding: 8px; border-radius: 6px; overflow-x: auto; margin: 6px 0; }
  .result-desc ul, .result-desc ol { margin: 4px 0; padding-left: 18px; }
  .result-desc ul { list-style: disc; }
  .result-desc ol { list-style: decimal; }
  .result-desc p { margin: 2px 0; }
  .result-desc h1 { font-size: 16px; font-weight: 700; margin: 6px 0 2px; }
  .result-desc h2 { font-size: 15px; font-weight: 600; margin: 6px 0 2px; }
  .result-desc h3 { font-size: 14px; font-weight: 600; margin: 4px 0 2px; }
  .result-desc blockquote { border-left: 2px solid var(--border); padding-left: 10px; color: var(--text-muted); margin: 4px 0; }
  .result-desc table { border-collapse: collapse; margin: 4px 0; }
  .result-desc th, .result-desc td { border: 1px solid var(--border); padding: 4px 8px; font-size: 11px; }
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
  const TOOL_DATA = __DATA__;

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
      desc.innerHTML = r.description || "<em>No description available.</em>";
      summary.appendChild(desc);

      // Meta line (always visible) — metadata bar
      const meta = document.createElement("div");
      meta.className = "result-meta";

      // Version badge
      const ver = document.createElement("span");
      ver.className = "badge badge-version";
      const verStr = r.version || "v?";
      ver.textContent = (verStr === "0.0.0") ? "v unknown" : "v" + verStr;
      meta.appendChild(ver);

      // Transport badge
      const tr = document.createElement("span");
      tr.className = "badge badge-transport";
      tr.textContent = Array.isArray(r.transport) ? r.transport.join(", ") : (r.transport || "unknown");
      meta.appendChild(tr);

      // Publisher / source
      const pub = document.createElement("span");
      pub.className = "badge badge-publisher";
      pub.textContent = r.publisher?.name || r.publisher?.id || "unknown";
      meta.appendChild(pub);

      if (r.publisher?.verified) {
        const vBadge = document.createElement("span");
        vBadge.className = "badge badge-verified";
        vBadge.textContent = "verified";
        meta.appendChild(vBadge);
      }

      // Tools count
      if (r.tools_count) {
        const tb = document.createElement("span");
        tb.className = "badge badge-tools";
        tb.textContent = String(r.tools_count) + " tools";
        meta.appendChild(tb);
      }

      // Downloads
      if (r.downloads !== null && r.downloads !== undefined) {
        const dl = document.createElement("span");
        dl.className = "badge badge-downloads";
        dl.textContent = "\u2193 " + r.downloads;
        meta.appendChild(dl);
      }

      // Category
      if (r.category) {
        const cat = document.createElement("span");
        cat.className = "badge badge-category";
        cat.textContent = r.category;
        meta.appendChild(cat);
      }

      // Pricing
      if (r.pricing && r.pricing !== "free") {
        const pr = document.createElement("span");
        pr.className = "badge badge-pricing";
        pr.textContent = r.pricing;
        meta.appendChild(pr);
      }

      // Tags (compact, first 5)
      if (r.tags && r.tags.length) {
        r.tags.slice(0, 5).forEach(t => {
          const tag = document.createElement("span");
          tag.className = "badge badge-tag";
          tag.textContent = t;
          meta.appendChild(tag);
        });
      }

      summary.appendChild(meta);
      header.appendChild(summary);
      card.appendChild(header);

      // --- Expandable details ---
      const details = document.createElement("div");
      details.className = "card-details";

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

      if (r.capabilities && r.capabilities.length) {
        const dr = document.createElement("div");
        dr.className = "detail-row";
        dr.innerHTML = '<span class="detail-label">Capabilities</span><span class="detail-value">' + r.capabilities.map(escapeHtml).join(", ") + '</span>';
        details.appendChild(dr);
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
    if (s === null || s === undefined) return "";
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }

  // Error display — MUST be registered BEFORE renderResults() call,
  // otherwise a synchronous throw in renderResults is silently swallowed
  // and the #results div stays empty with no visible error.
  window.addEventListener('error', function(e) {
    var d = document.getElementById('results');
    if (d) d.innerHTML = '<div style="color:#f85149;padding:12px;font-size:12px">Render error: ' + (e.message || 'unknown') + ' at ' + (e.filename || '?') + ':' + (e.lineno || '?') + '</div>';
  });

  // Also listen for postMessage from host (dual-mode)
  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (!msg || msg.jsonrpc !== "2.0") return;
    if (msg.method === "notifications/tool_result") {
      try { renderResults(msg.params); } catch(err) {
        var d = document.getElementById('results');
        if (d) d.innerHTML = '<div style="color:#f85149;padding:12px;font-size:12px">Render error (postMessage): ' + (err.message || String(err)) + '</div>';
      }
    }
  });

  // Render — wrapped in try/catch as belt-and-suspenders. The window
  // 'error' listener above catches errors from this call, but an
  // explicit try/catch ensures we can display the error even in
  // browsers that don't fire 'error' for same-script exceptions.
  try {
    renderResults(TOOL_DATA);
  } catch(err) {
    var d = document.getElementById('results');
    if (d) d.innerHTML = '<div style="color:#f85149;padding:12px;font-size:12px">Render error: ' + (err.message || String(err)) + '<br><pre style="margin-top:8px;white-space:pre-wrap;font-size:11px">' + (err.stack || '') + '</pre></div>';
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


# ─── Apps-Mode HTML Templates (Phase 3) ───────────────────────────────────────
#
# Six full HTML documents for the _apps tool variants. Each is a complete
# <!DOCTYPE html> page designed for rendering inside a sandboxed iframe by
# the MCP Apps host (LibreChat). Design follows the anti-AI-slop rules:
# dark GitHub-style theme, IBM Plex Sans/Mono, monospace for data, tables
# for list data, no purple gradients, no glassmorphism, no emoji headers.
#
# All templates use a __DATA__ placeholder that is replaced with
# json.dumps(data).replace("<", "\\u003c") at render time (XSS-safe).
#
# Shared CSS variables (repeated in each template for iframe isolation):
#   --bg: #0d1117, --surface: #161b22, --border: #30363d,
#   --text: #e6edf3, --text-muted: #8b949e, --accent: #58a6ff,
#   --danger: #f85149, --success: #3fb950


_APPS_BASE_CSS = """
  :root {
    --bg: #0d1117;
    --surface: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --text-muted: #8b949e;
    --accent: #58a6ff;
    --danger: #f85149;
    --success: #3fb950;
    --font-body: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --font-mono: 'IBM Plex Mono', 'JetBrains Mono', 'SF Mono', 'Consolas', monospace;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    font-family: var(--font-body);
    background: var(--bg);
    color: var(--text);
    font-size: 13px;
    line-height: 1.5;
    padding: 16px;
  }
  .header { margin-bottom: 16px; }
  .title { font-size: 22px; font-weight: 700; color: var(--text); letter-spacing: -0.01em; }
  .subtitle { font-size: 12px; color: var(--text-muted); margin-top: 2px; font-family: var(--font-mono); }
  .section-header {
    font-size: 16px;
    font-weight: 600;
    color: var(--text);
    margin: 16px 0 8px 0;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border);
  }
  .label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
  }
  .mono { font-family: var(--font-mono); }
  .muted { color: var(--text-muted); }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    font-family: var(--font-mono);
  }
  .badge-accent { background: rgba(88, 166, 255, 0.15); color: var(--accent); }
  .badge-success { background: rgba(63, 185, 80, 0.15); color: var(--success); }
  .badge-danger { background: rgba(248, 81, 73, 0.15); color: var(--danger); }
  .badge-muted { background: rgba(139, 148, 158, 0.15); color: var(--text-muted); }
  .btn {
    display: inline-block;
    padding: 8px 16px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    font-family: var(--font-body);
    cursor: pointer;
    background: var(--surface);
    color: var(--text);
    transition: background 0.12s, border-color 0.12s;
  }
  .btn:hover { background: var(--border); }
  .btn-primary { background: var(--accent); color: #0d1117; border-color: var(--accent); }
  .btn-primary:hover { opacity: 0.9; background: var(--accent); }
  .btn-danger { background: var(--danger); color: #fff; border-color: var(--danger); }
  .btn-danger:hover { opacity: 0.9; background: var(--danger); }
  .btn-success { background: var(--success); color: #0d1117; border-color: var(--success); }
  .btn-success:hover { opacity: 0.9; background: var(--success); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  #status { margin-top: 12px; font-size: 13px; padding: 8px 12px; border-radius: 6px; display: none; }
  #status.visible { display: block; }
  #status.ok { background: rgba(63, 185, 80, 0.1); color: var(--success); }
  #status.err { background: rgba(248, 81, 73, 0.1); color: var(--danger); }

  /* Rendered markdown inside description fields */
  .result-desc img, .info-desc img, .approval-desc img,
  .removal-desc img, .publish-desc img, .srv-desc img,
  .detail-value img {
    max-height: 20px; width: auto; vertical-align: middle; display: inline-block;
    margin: 0 2px; opacity: 0.9;
  }
  .result-card.expanded .result-desc img,
  .detail-value img { max-height: 28px; }
  .result-desc a, .info-desc a, .approval-desc a,
  .removal-desc a, .publish-desc a, .srv-desc a,
  .detail-value a {
    color: var(--accent); text-decoration: none;
  }
  .result-desc a:hover, .info-desc a:hover, .approval-desc a:hover,
  .removal-desc a:hover, .publish-desc a:hover, .srv-desc a:hover,
  .detail-value a:hover { text-decoration: underline; }
  .result-desc code, .info-desc code, .approval-desc code,
  .removal-desc code, .publish-desc code, .srv-desc code,
  .detail-value code {
    font-family: var(--font-mono); font-size: 0.9em;
    background: var(--surface); padding: 1px 4px; border-radius: 3px;
  }
  .result-desc pre, .info-desc pre, .approval-desc pre,
  .removal-desc pre, .publish-desc pre, .srv-desc pre {
    font-family: var(--font-mono); font-size: 12px;
    background: var(--surface); padding: 8px; border-radius: 6px;
    overflow-x: auto; margin: 6px 0;
  }
  .result-desc ul, .info-desc ul, .approval-desc ul,
  .removal-desc ul, .publish-desc ul, .srv-desc ul {
    margin: 4px 0; padding-left: 18px; list-style: disc;
  }
  .result-desc p, .info-desc p, .approval-desc p,
  .removal-desc p, .publish-desc p, .srv-desc p {
    margin: 2px 0;
  }
  .result-desc h1, .info-desc h1, .approval-desc h1,
  .removal-desc h1, .publish-desc h1, .srv-desc h1 {
    font-size: 16px; font-weight: 700; margin: 6px 0 2px;
  }
  .result-desc h2, .info-desc h2, .approval-desc h2,
  .removal-desc h2, .publish-desc h2, .srv-desc h2 {
    font-size: 15px; font-weight: 600; margin: 6px 0 2px;
  }
  .result-desc h3, .info-desc h3, .approval-desc h3,
  .removal-desc h3, .publish-desc h3, .srv-desc h3 {
    font-size: 14px; font-weight: 600; margin: 4px 0 2px;
  }
  .result-desc blockquote, .info-desc blockquote, .approval-desc blockquote,
  .removal-desc blockquote, .publish-desc blockquote, .srv-desc blockquote {
    border-left: 2px solid var(--border); padding-left: 10px;
    color: var(--text-muted); margin: 4px 0;
  }
"""

SEARCH_APPS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PHAROS Search Results</title>
<style>""" + _APPS_BASE_CSS + """
  .toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
  .toolbar .count { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); }
  .results-table { width: 100%; border-collapse: collapse; }
  .results-table th {
    text-align: left;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }
  .results-table td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  .results-table tr:hover td { background: var(--surface); }
  .srv-name { font-weight: 600; font-size: 13px; color: var(--text); }
  .srv-desc { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
  .srv-id { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); }
  .empty { text-align: center; padding: 32px; color: var(--text-muted); font-size: 13px; }
</style>
</head>
<body>
  <div class="header">
    <div class="title">Search Results</div>
    <div class="subtitle" id="subtitle"></div>
  </div>
  <div class="toolbar">
    <span class="count" id="count"></span>
  </div>
  <table class="results-table" id="results-table">
    <thead>
      <tr>
        <th>Server</th>
        <th>Transport</th>
        <th>Tools</th>
        <th>Publisher</th>
        <th></th>
      </tr>
    </thead>
    <tbody id="results-body"></tbody>
  </table>
  <div class="empty" id="empty" style="display:none">No servers found. Try a different query.</div>
  <div id="status"></div>
<script>
  const DATA = __DATA__;

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }

  function render() {
    const results = DATA.results || [];
    const countEl = document.getElementById("count");
    const emptyEl = document.getElementById("empty");
    const tbody = document.getElementById("results-body");
    const subtitle = document.getElementById("subtitle");

    if (DATA.query) subtitle.textContent = "query: " + DATA.query;

    if (!results.length) {
      document.getElementById("results-table").style.display = "none";
      emptyEl.style.display = "block";
      countEl.textContent = "";
      return;
    }

    countEl.textContent = results.length + " result" + (results.length > 1 ? "s" : "");
    tbody.innerHTML = "";

    results.forEach((r) => {
      const tr = document.createElement("tr");

      // Server cell (name + description + id)
      const cellSrv = document.createElement("td");
      const nameDiv = document.createElement("div");
      nameDiv.className = "srv-name";
      nameDiv.textContent = r.name || r.id;
      cellSrv.appendChild(nameDiv);
      const descDiv = document.createElement("div");
      descDiv.className = "srv-desc";
      descDiv.innerHTML = (r.description || "<em>No description.</em>").substring(0, 300);
      cellSrv.appendChild(descDiv);
      const idDiv = document.createElement("div");
      idDiv.className = "srv-id";
      idDiv.textContent = r.id;
      cellSrv.appendChild(idDiv);
      tr.appendChild(cellSrv);

      // Transport cell
      const cellTr = document.createElement("td");
      const transport = Array.isArray(r.transport) ? r.transport.join(", ") : (r.transport || "unknown");
      const trBadge = document.createElement("span");
      trBadge.className = "badge badge-muted mono";
      trBadge.textContent = transport;
      cellTr.appendChild(trBadge);
      tr.appendChild(cellTr);

      // Tools cell
      const cellTools = document.createElement("td");
      const toolsBadge = document.createElement("span");
      toolsBadge.className = "badge badge-accent";
      toolsBadge.textContent = String(r.tools_count || 0);
      cellTools.appendChild(toolsBadge);
      tr.appendChild(cellTools);

      // Publisher cell
      const cellPub = document.createElement("td");
      const pubName = document.createElement("div");
      pubName.textContent = (r.publisher && r.publisher.name) || "unknown";
      cellPub.appendChild(pubName);
      if (r.publisher && r.publisher.verified) {
        const vBadge = document.createElement("span");
        vBadge.className = "badge badge-success";
        vBadge.textContent = "verified";
        cellPub.appendChild(vBadge);
      }
      tr.appendChild(cellPub);

      // Install button cell
      const cellBtn = document.createElement("td");
      const btn = document.createElement("button");
      btn.className = "btn btn-primary";
      btn.textContent = "Install";
      btn.dataset.serverId = r.id;
      btn.addEventListener("click", () => {
        window.parent.postMessage({
          jsonrpc: "2.0",
          method: "notifications/tool_result",
          params: { action: "install", server_id: r.id }
        }, "*");
        showStatus("To install " + r.id + ", ask the AI to call pharos_install_apps with server_id: " + r.id, "ok");
      });
      cellBtn.appendChild(btn);
      tr.appendChild(cellBtn);

      tbody.appendChild(tr);
    });
  }

  function showStatus(msg, kind) {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = "visible " + (kind || "ok");
  }

  window.addEventListener("error", function(e) {
    showStatus("Render error: " + (e.message || "unknown"), "err");
  });

  try { render(); } catch(err) {
    showStatus("Render error: " + (err.message || String(err)), "err");
  }
</script>
</body>
</html>"""


INFO_APPS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PHAROS Server Info</title>
<style>""" + _APPS_BASE_CSS + """
  .info-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
  }
  .info-name { font-size: 18px; font-weight: 700; color: var(--text); }
  .info-desc { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
  .detail-table { width: 100%; border-collapse: collapse; margin-top: 12px; }
  .detail-table td {
    padding: 6px 12px 6px 0;
    font-size: 13px;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  .detail-table td:first-child {
    width: 120px;
    color: var(--text-muted);
  }
  .detail-table td:last-child {
    font-family: var(--font-mono);
    font-size: 12px;
  }
  .tags-row { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
  .actions { display: flex; gap: 8px; margin-top: 16px; }
</style>
</head>
<body>
  <div class="header">
    <div class="title">Server Details</div>
    <div class="subtitle" id="subtitle"></div>
  </div>
  <div class="info-card" id="info-card"></div>
  <div id="status"></div>
<script>
  const DATA = __DATA__;

  function render() {
    const s = DATA.server;
    if (!s) return;
    const card = document.getElementById("info-card");
    const subtitle = document.getElementById("subtitle");
    subtitle.textContent = "id: " + (s.id || "unknown");

    const nameEl = document.createElement("div");
    nameEl.className = "info-name";
    nameEl.textContent = s.display_name || s.name || s.id;
    card.appendChild(nameEl);

    const descEl = document.createElement("div");
    descEl.className = "info-desc";
    descEl.innerHTML = s.description || "<em>No description available.</em>";
    card.appendChild(descEl);

    const table = document.createElement("table");
    table.className = "detail-table";
    const rows = [
      ["Version", s.version || "N/A"],
      ["Publisher", (s.publisher && s.publisher.name) || "unknown"],
      ["Verified", s.publisher && s.publisher.verified ? "yes" : "no"],
      ["Transport", Array.isArray(s.transport) ? s.transport.join(", ") : (s.transport || "N/A")],
      ["Endpoint", s.endpoint || "N/A"],
      ["Tools", String(s.tools_count || 0)],
      ["Pricing", s.pricing || "free"],
      ["Capabilities", Array.isArray(s.capabilities) ? s.capabilities.join(", ") : "none"],
    ];
    rows.forEach(([label, value]) => {
      const tr = document.createElement("tr");
      const td1 = document.createElement("td");
      td1.textContent = label;
      const td2 = document.createElement("td");
      td2.textContent = value;
      tr.appendChild(td1);
      tr.appendChild(td2);
      table.appendChild(tr);
    });
    card.appendChild(table);

    if (Array.isArray(s.tags) && s.tags.length) {
      const tagsLabel = document.createElement("div");
      tagsLabel.className = "label";
      tagsLabel.style.marginTop = "12px";
      tagsLabel.textContent = "Tags";
      card.appendChild(tagsLabel);
      const tagsRow = document.createElement("div");
      tagsRow.className = "tags-row";
      s.tags.forEach(t => {
        const tag = document.createElement("span");
        tag.className = "badge badge-muted";
        tag.textContent = t;
        tagsRow.appendChild(tag);
      });
      card.appendChild(tagsRow);
    }

    const actions = document.createElement("div");
    actions.className = "actions";
    const btn = document.createElement("button");
    btn.className = "btn btn-primary";
    btn.textContent = "Install";
    btn.addEventListener("click", () => {
      window.parent.postMessage({
        jsonrpc: "2.0",
        method: "notifications/tool_result",
        params: { action: "install", server_id: s.id }
      }, "*");
      showStatus("To install " + s.id + ", ask the AI to call pharos_install_apps.", "ok");
    });
    actions.appendChild(btn);
    card.appendChild(actions);
  }

  function showStatus(msg, kind) {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = "visible " + (kind || "ok");
  }

  window.addEventListener("error", function(e) {
    showStatus("Render error: " + (e.message || "unknown"), "err");
  });

  try { render(); } catch(err) {
    showStatus("Render error: " + (err.message || String(err)), "err");
  }
</script>
</body>
</html>"""


APPROVAL_APPS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PHAROS</title>
<style>""" + _APPS_BASE_CSS + """
  .approval-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
  }
  .approval-name { font-size: 18px; font-weight: 700; color: var(--text); }
  .approval-desc { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
  .purpose-box {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px;
    margin: 12px 0;
  }
  .purpose-box .label { margin-bottom: 4px; }
  .purpose-box .purpose-text { font-size: 13px; color: var(--text); }
  .detail-table { width: 100%; border-collapse: collapse; margin-top: 12px; }
  .detail-table td {
    padding: 6px 12px 6px 0;
    font-size: 13px;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  .detail-table td:first-child { width: 120px; color: var(--text-muted); }
  .detail-table td:last-child { font-family: var(--font-mono); font-size: 12px; }
  .header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 16px;
  }
  .header-text { flex: 1; min-width: 0; }
  .header-actions {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }
  .btn-danger {
    background: var(--danger);
    color: white;
    border: 1px solid var(--danger);
  }
  .btn-danger:hover {
    background: rgba(248, 81, 73, 0.8);
    border-color: rgba(248, 81, 73, 0.8);
  }
  .btn-static {
    cursor: default;
    opacity: 0.85;
  }
  .btn-static:hover {
    filter: none;
  }
</style>
</head>
<body>
  <!-- Neutral empty page when DATA.server is missing. Hosts may still
       fetch ui://pharos/approval on install errors because of a static
       tool decorator; do not render a fake approval card in that case. -->
  <div id="approval-root"></div>
  <div id="status"></div>
<script>
  const DATA = __DATA__;

  function render() {
    if (!DATA || !DATA.server) return;
    const s = DATA.server;
    document.title = "PHAROS Approval Required";

    const root = document.getElementById("approval-root");
    const header = document.createElement("div");
    header.className = "header";
    const headerText = document.createElement("div");
    headerText.className = "header-text";
    const titleEl = document.createElement("div");
    titleEl.className = "title";
    titleEl.textContent = "Approval Required";
    const subtitle = document.createElement("div");
    subtitle.className = "subtitle";
    subtitle.id = "subtitle";
    headerText.appendChild(titleEl);
    headerText.appendChild(subtitle);
    const headerActions = document.createElement("div");
    headerActions.className = "header-actions";
    headerActions.id = "header-actions";
    header.appendChild(headerText);
    header.appendChild(headerActions);
    const card = document.createElement("div");
    card.className = "approval-card";
    card.id = "approval-card";
    root.appendChild(header);
    root.appendChild(card);
    subtitle.textContent = "id: " + (s.id || DATA.server_id || "unknown");

    // Buttons in header (right-aligned, visible without scrolling)
    const denyBtn = document.createElement("button");
    denyBtn.className = "btn btn-danger";
    denyBtn.textContent = "Deny";
    denyBtn.addEventListener("click", () => {
      const denyId = Math.floor(Math.random() * 1000000);
      window.parent.postMessage({
        jsonrpc: "2.0",
        id: denyId,
        method: "ui/deny",
        params: {
          approval_token: DATA.approval_token || "",
          approval_nonce: DATA.approval_nonce || "",
        }
      }, "*");

      const denyHandler = (event) => {
        if (!event.data || event.data.jsonrpc !== "2.0" || event.data.id !== denyId) return;
        window.removeEventListener("message", denyHandler);
        headerActions.innerHTML = "";
        const deniedStatic = document.createElement("button");
        deniedStatic.className = "btn btn-danger btn-static";
        deniedStatic.textContent = "Denied";
        deniedStatic.disabled = true;
        headerActions.appendChild(deniedStatic);
      };
      window.addEventListener("message", denyHandler);
    });
    headerActions.appendChild(denyBtn);

    const approveBtn = document.createElement("button");
    approveBtn.className = "btn btn-success";
    approveBtn.textContent = "Approve";
    approveBtn.id = "approve-btn";
    approveBtn.addEventListener("click", () => {
      approveBtn.disabled = true;
      denyBtn.disabled = true;
      showStatus("Sending approval...", "ok");

      const approveId = Math.floor(Math.random() * 1000000);
      window.parent.postMessage({
        jsonrpc: "2.0",
        id: approveId,
        method: "ui/approve",
        params: {
          approval_token: DATA.approval_token || "",
          approval_nonce: DATA.approval_nonce || "",
        }
      }, "*");

      const approveHandler = (event) => {
        if (!event.data || event.data.jsonrpc !== "2.0" || event.data.id !== approveId) return;
        window.removeEventListener("message", approveHandler);
        if (event.data.error) {
          const errMsg = event.data.error.message || "Unknown error";
          showStatus("Error: " + errMsg, "err");
          approveBtn.disabled = false;
          denyBtn.disabled = false;
        } else {
          const body = event.data.result || {};
          showStatus("Approved. " + (body.tools_count || 0) + " tools available.", "ok");
          headerActions.innerHTML = "";
          const approvedStatic = document.createElement("button");
          approvedStatic.className = "btn btn-success btn-static";
          approvedStatic.textContent = "Approved";
          approvedStatic.disabled = true;
          headerActions.appendChild(approvedStatic);
          window.parent.postMessage({
            jsonrpc: "2.0",
            method: "notifications/tool_result",
            params: { approved: true, server_id: body.server_id }
          }, "*");
        }
      };
      window.addEventListener("message", approveHandler);
    });
    headerActions.appendChild(approveBtn);

    // Card content
    const nameEl = document.createElement("div");
    nameEl.className = "approval-name";
    nameEl.textContent = s.display_name || s.name || s.id;
    card.appendChild(nameEl);

    const descEl = document.createElement("div");
    descEl.className = "approval-desc";
    descEl.innerHTML = s.description || "<em>No description available.</em>";
    card.appendChild(descEl);

    // Purpose box
    const purposeBox = document.createElement("div");
    purposeBox.className = "purpose-box";
    const purposeLabel = document.createElement("div");
    purposeLabel.className = "label";
    purposeLabel.textContent = "Purpose";
    purposeBox.appendChild(purposeLabel);
    const purposeText = document.createElement("div");
    purposeText.className = "purpose-text";
    purposeText.textContent = DATA.purpose || "User request";
    purposeBox.appendChild(purposeText);
    card.appendChild(purposeBox);

    // Detail table
    const table = document.createElement("table");
    table.className = "detail-table";
    const rows = [
      ["Version", s.version || "N/A"],
      ["Publisher", (s.publisher && s.publisher.name) || "unknown"],
      ["Transport", Array.isArray(s.transport) ? s.transport.join(", ") : (s.transport || "N/A")],
      ["Endpoint", s.endpoint || "N/A"],
      ["Tools", String(s.tools_count || 0)],
      ["Pricing", s.pricing || "free"],
    ];
    rows.forEach(([label, value]) => {
      const tr = document.createElement("tr");
      const td1 = document.createElement("td");
      td1.textContent = label;
      const td2 = document.createElement("td");
      td2.textContent = value;
      tr.appendChild(td1);
      tr.appendChild(td2);
      table.appendChild(tr);
    });
    card.appendChild(table);
  }

  function showStatus(msg, kind) {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = "visible " + (kind || "ok");
  }

  window.addEventListener("error", function(e) {
    showStatus("Render error: " + (e.message || "unknown"), "err");
  });

  try { render(); } catch(err) {
    showStatus("Render error: " + (err.message || String(err)), "err");
  }
</script>
</body>
</html>"""


REMOVAL_APPS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PHAROS Removal Confirmation</title>
<style>""" + _APPS_BASE_CSS + """
  .removal-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
  }
  .removal-name { font-size: 18px; font-weight: 700; color: var(--text); }
  .removal-desc { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
  .warning-box {
    background: rgba(248, 81, 73, 0.08);
    border: 1px solid rgba(248, 81, 73, 0.3);
    border-radius: 6px;
    padding: 12px;
    margin: 12px 0;
    font-size: 13px;
    color: var(--text);
  }
  .header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 16px;
  }
  .header-text { flex: 1; min-width: 0; }
  .header-actions {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }
  .btn-danger {
    background: var(--danger);
    color: white;
    border: 1px solid var(--danger);
  }
  .btn-danger:hover {
    background: rgba(248, 81, 73, 0.8);
    border-color: rgba(248, 81, 73, 0.8);
  }
  .btn-static {
    cursor: default;
    opacity: 0.85;
  }
  .btn-static:hover {
    filter: none;
  }
</style>
</head>
<body>
  <div class="header">
    <div class="header-text">
      <div class="title">Removal Confirmation</div>
      <div class="subtitle" id="subtitle"></div>
    </div>
    <div class="header-actions" id="header-actions"></div>
  </div>
  <div class="removal-card" id="removal-card"></div>
  <div id="status"></div>
<script>
  const DATA = __DATA__;

  function render() {
    const card = document.getElementById("removal-card");
    const subtitle = document.getElementById("subtitle");
    const headerActions = document.getElementById("header-actions");
    subtitle.textContent = "id: " + (DATA.server_id || "unknown");

    // Cancel button in header (secondary)
    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn";
    cancelBtn.textContent = "Cancel";
    cancelBtn.addEventListener("click", () => {
      const cancelId = Math.floor(Math.random() * 1000000);
      window.parent.postMessage({
        jsonrpc: "2.0",
        id: cancelId,
        method: "ui/deny",
        params: {
          approval_token: DATA.removal_token || "",
          approval_nonce: DATA.approval_nonce || "",
        }
      }, "*");

      const cancelHandler = (event) => {
        if (!event.data || event.data.jsonrpc !== "2.0" || event.data.id !== cancelId) return;
        window.removeEventListener("message", cancelHandler);
        headerActions.innerHTML = "";
        const cancelledStatic = document.createElement("button");
        cancelledStatic.className = "btn btn-static";
        cancelledStatic.textContent = "Cancelled";
        cancelledStatic.disabled = true;
        headerActions.appendChild(cancelledStatic);
        showStatus("Removal cancelled.", "ok");
      };
      window.addEventListener("message", cancelHandler);
    });
    headerActions.appendChild(cancelBtn);

    // Remove button in header (destructive, red)
    const removeBtn = document.createElement("button");
    removeBtn.className = "btn btn-danger";
    removeBtn.textContent = "Remove";
    removeBtn.id = "remove-btn";
    removeBtn.addEventListener("click", () => {
      removeBtn.disabled = true;
      cancelBtn.disabled = true;
      showStatus("Sending removal confirmation...", "ok");

      const removeId = Math.floor(Math.random() * 1000000);
      window.parent.postMessage({
        jsonrpc: "2.0",
        id: removeId,
        method: "ui/approve",
        params: {
          approval_token: DATA.removal_token || "",
          approval_nonce: DATA.approval_nonce || "",
        }
      }, "*");

      const removeHandler = (event) => {
        if (!event.data || event.data.jsonrpc !== "2.0" || event.data.id !== removeId) return;
        window.removeEventListener("message", removeHandler);
        if (event.data.error) {
          const errMsg = event.data.error.message || "Unknown error";
          showStatus("Error: " + errMsg, "err");
          removeBtn.disabled = false;
          cancelBtn.disabled = false;
        } else {
          showStatus("Server removed.", "ok");
          headerActions.innerHTML = "";
          const removedStatic = document.createElement("button");
          removedStatic.className = "btn btn-danger btn-static";
          removedStatic.textContent = "Removed";
          removedStatic.disabled = true;
          headerActions.appendChild(removedStatic);
          window.parent.postMessage({
            jsonrpc: "2.0",
            method: "notifications/tool_result",
            params: { approved: true, action: "removed", server_id: DATA.server_id }
          }, "*");
        }
      };
      window.addEventListener("message", removeHandler);
    });
    headerActions.appendChild(removeBtn);

    // Card content
    const nameEl = document.createElement("div");
    nameEl.className = "removal-name";
    nameEl.textContent = DATA.server_name || DATA.server_id || "Unknown Server";
    card.appendChild(nameEl);

    const descEl = document.createElement("div");
    descEl.className = "removal-desc";
    descEl.innerHTML = DATA.server_description || "<em>No description.</em>";
    card.appendChild(descEl);

    const warning = document.createElement("div");
    warning.className = "warning-box";
    warning.textContent = "This will remove the server and disconnect any active sessions. This action cannot be undone from the UI.";
    card.appendChild(warning);
  }

  function showStatus(msg, kind) {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = "visible " + (kind || "ok");
  }

  window.addEventListener("error", function(e) {
    showStatus("Render error: " + (e.message || "unknown"), "err");
  });

  try { render(); } catch(err) {
    showStatus("Render error: " + (err.message || String(err)), "err");
  }
</script>
</body>
</html>"""


INSTALLED_APPS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PHAROS Installed Servers</title>
<style>""" + _APPS_BASE_CSS + """
  .installed-table { width: 100%; border-collapse: collapse; }
  .installed-table th {
    text-align: left;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }
  .installed-table td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
    vertical-align: top;
  }
  .installed-table tr:hover td { background: var(--surface); }
  .installed-table td.mono { font-family: var(--font-mono); font-size: 12px; }
  .empty { text-align: center; padding: 32px; color: var(--text-muted); font-size: 13px; }
  .status-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 6px;
    vertical-align: middle;
  }
  .status-running { background: var(--success); }
  .status-stopped { background: var(--text-muted); }
  .status-error { background: var(--danger); }
  .row-action {
    font-size: 12px;
    color: var(--accent);
    cursor: pointer;
    text-decoration: none;
  }
  .row-action:hover { text-decoration: underline; }
</style>
</head>
<body>
  <div class="header">
    <div class="title">Installed Servers</div>
    <div class="subtitle" id="subtitle"></div>
  </div>
  <table class="installed-table" id="installed-table">
    <thead>
      <tr>
        <th>Server ID</th>
        <th>Status</th>
        <th>Transport</th>
        <th>Endpoint</th>
        <th>Source</th>
        <th>Installed</th>
        <th>Port</th>
        <th>Size</th>
        <th>Memory</th>
        <th>Uptime</th>
        <th></th>
      </tr>
    </thead>
    <tbody id="installed-body"></tbody>
  </table>
  <div class="empty" id="empty" style="display:none">No servers installed.</div>
  <div id="status"></div>
<script>
  const DATA = __DATA__;

  function render() {
    const servers = DATA.servers || [];
    const tbody = document.getElementById("installed-body");
    const emptyEl = document.getElementById("empty");
    const subtitle = document.getElementById("subtitle");

    subtitle.textContent = servers.length + " server" + (servers.length !== 1 ? "s" : "");

    if (!servers.length) {
      document.getElementById("installed-table").style.display = "none";
      emptyEl.style.display = "block";
      return;
    }

    function dash(v) {
      if (v === null || v === undefined || v === "") return "—";
      return String(v);
    }

    tbody.innerHTML = "";
    servers.forEach((s) => {
      const tr = document.createElement("tr");

      const cellId = document.createElement("td");
      cellId.className = "mono";
      cellId.textContent = s.server_id || s.id || "unknown";
      tr.appendChild(cellId);

      const cellStatus = document.createElement("td");
      const status = (s.status || "unknown").toLowerCase();
      const live = status === "running" || status === "connected";
      const dot = document.createElement("span");
      dot.className = "status-dot status-" + (live ? "running" : status === "error" ? "error" : "stopped");
      cellStatus.appendChild(dot);
      cellStatus.appendChild(document.createTextNode(s.status || "unknown"));
      tr.appendChild(cellStatus);

      const cellTr = document.createElement("td");
      cellTr.className = "mono";
      const transport = Array.isArray(s.transport) ? s.transport.join(", ") : (s.transport || "unknown");
      cellTr.textContent = transport;
      tr.appendChild(cellTr);

      const cellEp = document.createElement("td");
      cellEp.className = "mono";
      cellEp.textContent = dash(s.endpoint);
      tr.appendChild(cellEp);

      const cellSrc = document.createElement("td");
      cellSrc.textContent = s.source || "unknown";
      tr.appendChild(cellSrc);

      const cellDate = document.createElement("td");
      cellDate.className = "mono";
      const installed = s.installed_at || "";
      cellDate.textContent = installed ? installed.substring(0, 19).replace("T", " ") : "—";
      tr.appendChild(cellDate);

      for (const key of ["port", "size", "memory", "uptime"]) {
        const cell = document.createElement("td");
        cell.className = "mono";
        cell.textContent = dash(s[key]);
        tr.appendChild(cell);
      }

      const cellAction = document.createElement("td");
      const removeLink = document.createElement("a");
      removeLink.className = "row-action";
      removeLink.textContent = "Remove";
      removeLink.addEventListener("click", () => {
        window.parent.postMessage({
          jsonrpc: "2.0",
          method: "notifications/tool_result",
          params: { action: "remove", server_id: s.server_id || s.id }
        }, "*");
        showStatus("To remove " + (s.server_id || s.id) + ", ask the AI to call pharos_remove_apps.", "ok");
      });
      cellAction.appendChild(removeLink);
      tr.appendChild(cellAction);

      tbody.appendChild(tr);
    });
  }

  function showStatus(msg, kind) {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = "visible " + (kind || "ok");
  }

  window.addEventListener("error", function(e) {
    showStatus("Render error: " + (e.message || "unknown"), "err");
  });

  try { render(); } catch(err) {
    showStatus("Render error: " + (err.message || String(err)), "err");
  }
</script>
</body>
</html>"""


PUBLISH_APPS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PHAROS Publish Confirmation</title>
<style>""" + _APPS_BASE_CSS + """
  .publish-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
  }
  .publish-name { font-size: 18px; font-weight: 700; color: var(--text); }
  .publish-desc { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
  .detail-table { width: 100%; border-collapse: collapse; margin-top: 12px; }
  .detail-table td {
    padding: 6px 12px 6px 0;
    font-size: 13px;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  .detail-table td:first-child { width: 120px; color: var(--text-muted); }
  .detail-table td:last-child { font-family: var(--font-mono); font-size: 12px; }
  .tags-row { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
  .actions { display: flex; gap: 8px; margin-top: 16px; }
</style>
</head>
<body>
  <div class="header">
    <div class="title">Publish Confirmation</div>
    <div class="subtitle" id="subtitle"></div>
  </div>
  <div class="publish-card" id="publish-card"></div>
  <div id="status"></div>
<script>
  const DATA = __DATA__;

  function render() {
    const s = DATA.server;
    if (!s) return;
    const card = document.getElementById("publish-card");
    const subtitle = document.getElementById("subtitle");
    subtitle.textContent = "path: " + (DATA.server_card_path || "current dir");

    const nameEl = document.createElement("div");
    nameEl.className = "publish-name";
    nameEl.textContent = s.display_name || s.name || s.id || "Unknown";
    card.appendChild(nameEl);

    const descEl = document.createElement("div");
    descEl.className = "publish-desc";
    descEl.innerHTML = s.description || "<em>No description available.</em>";
    card.appendChild(descEl);

    const table = document.createElement("table");
    table.className = "detail-table";
    const rows = [
      ["Server ID", s.id || "N/A"],
      ["Version", s.version || "N/A"],
      ["Publisher", (s.publisher && s.publisher.name) || "unknown"],
      ["Transport", Array.isArray(s.transport) ? s.transport.join(", ") : (s.transport || "N/A")],
      ["Tools", String(s.tools_count || 0)],
    ];
    rows.forEach(([label, value]) => {
      const tr = document.createElement("tr");
      const td1 = document.createElement("td");
      td1.textContent = label;
      const td2 = document.createElement("td");
      td2.textContent = value;
      tr.appendChild(td1);
      tr.appendChild(td2);
      table.appendChild(tr);
    });
    card.appendChild(table);

    if (Array.isArray(s.tags) && s.tags.length) {
      const tagsLabel = document.createElement("div");
      tagsLabel.className = "label";
      tagsLabel.style.marginTop = "12px";
      tagsLabel.textContent = "Tags";
      card.appendChild(tagsLabel);
      const tagsRow = document.createElement("div");
      tagsRow.className = "tags-row";
      s.tags.forEach(t => {
        const tag = document.createElement("span");
        tag.className = "badge badge-muted";
        tag.textContent = t;
        tagsRow.appendChild(tag);
      });
      card.appendChild(tagsRow);
    }

    const actions = document.createElement("div");
    actions.className = "actions";
    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn";
    cancelBtn.textContent = "Cancel";
    cancelBtn.addEventListener("click", () => {
      window.parent.postMessage({
        jsonrpc: "2.0",
        method: "notifications/tool_result",
        params: { approved: false }
      }, "*");
      showStatus("Publishing cancelled.", "ok");
    });
    actions.appendChild(cancelBtn);

    const publishBtn = document.createElement("button");
    publishBtn.className = "btn btn-primary";
    publishBtn.textContent = "Publish";
    publishBtn.id = "publish-btn";
    publishBtn.addEventListener("click", () => {
      publishBtn.disabled = true;
      cancelBtn.disabled = true;
      showStatus("Sending publish confirmation...", "ok");

      const publishId = Math.floor(Math.random() * 1000000);
      window.parent.postMessage({
        jsonrpc: "2.0",
        id: publishId,
        method: "ui/approve",
        params: {
          approval_token: DATA.publish_token || "",
          approval_nonce: DATA.approval_nonce || "",
        }
      }, "*");

      const publishHandler = (event) => {
        if (!event.data || event.data.jsonrpc !== "2.0" || event.data.id !== publishId) return;
        window.removeEventListener("message", publishHandler);
        if (event.data.error) {
          const errMsg = event.data.error.message || "Unknown error";
          showStatus("Error: " + errMsg, "err");
          publishBtn.disabled = false;
          cancelBtn.disabled = false;
        } else {
          showStatus("Server published successfully.", "ok");
          window.parent.postMessage({
            jsonrpc: "2.0",
            method: "notifications/tool_result",
            params: { approved: true, action: "published", server_id: s.id }
          }, "*");
        }
      };
      window.addEventListener("message", publishHandler);
    });
    actions.appendChild(publishBtn);
    card.appendChild(actions);
  }

  function showStatus(msg, kind) {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = "visible " + (kind || "ok");
  }

  window.addEventListener("error", function(e) {
    showStatus("Render error: " + (e.message || "unknown"), "err");
  });

  try { render(); } catch(err) {
    showStatus("Render error: " + (err.message || String(err)), "err");
  }
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
_info_data_cache: dict[str, dict] = {}  # server_id → info display data
_removal_data_cache: dict[str, dict] = {}  # removal_token → data
_installed_data_cache: dict[str, list] = {}  # cache_key → installed list
_publish_data_cache: dict[str, dict] = {}  # publish_token → data
_current_search_id: str | None = None  # most recent (for resource handler)
_current_approval_token: str | None = None  # most recent (for resource handler)
_current_info_id: str | None = None  # most recent info server_id
_current_removal_token: str | None = None  # most recent removal token
_current_installed_key: str | None = None  # most recent installed cache key
_current_publish_token: str | None = None  # most recent publish token

# Max cache entries before auto-cleanup of oldest
_MAX_CACHE_SIZE = 20


def _encode_search_cursor(offset: int) -> str:
    """Encode an integer offset as the live registry's opaque cursor.

    Must match CLI ``encodeCursor``: base64.StdEncoding of the decimal offset.
    """
    return base64.b64encode(str(offset).encode("ascii")).decode("ascii")


def _page_cursor(page: int, limit: int) -> str:
    """Map 1-based page to a cursor. Page <= 1 (or non-positive offset) omits it."""
    if page <= 1 or limit <= 0:
        return ""
    offset = (page - 1) * limit
    if offset <= 0:
        return ""
    return _encode_search_cursor(offset)


def _search_filters(
    *,
    remote_only: bool = False,
    transport: str = "",
    registry: str = "",
    page: int = 1,
    limit: int = 10,
) -> dict[str, Any] | None:
    """Build GET /v1/search filters matching the CLI flags.

    Empty / whitespace transport and registry are omitted. ``page`` becomes
    ``cursor`` (never ``page=``, which the live API ignores). Explicit
    ``transport`` is not replaced by ``remote_only``; remote_only only
    supplies the remote transport list when transport is unset.
    """
    filters: dict[str, Any] = {}
    transport = (transport or "").strip()
    registry = (registry or "").strip()
    if transport:
        filters["transport"] = transport
    elif remote_only:
        filters["transport"] = ["sse", "streamable-http", "http"]
    if registry:
        filters["registry"] = registry
    cursor = _page_cursor(page, limit)
    if cursor:
        filters["cursor"] = cursor
    return filters or None


def _result_source_registry(result: Any) -> str | None:
    raw = getattr(result, "raw_item", None) or {}
    if isinstance(raw, dict):
        raw_src = raw.get("source_registry")
        if isinstance(raw_src, str) and raw_src:
            return raw_src
    card = getattr(result, "card", None)
    if card is None:
        return None
    source = getattr(card, "source_registry", None)
    if isinstance(source, str) and source:
        return source
    return None


def _search_page_meta(results: Any) -> dict[str, Any]:
    """Include nextCursor/total when the adapter/API returned them."""
    meta: dict[str, Any] = {}
    next_cursor = getattr(results, "next_cursor", None)
    if isinstance(next_cursor, str) and next_cursor:
        meta["nextCursor"] = next_cursor
    total = getattr(results, "total", None)
    if isinstance(total, int):
        meta["total"] = total
    return meta


def _new_ui_token(prefix: str) -> str:
    """URL-safe per-call token used in ui:// resource URIs."""
    return f"{prefix}-{int(time.time())}-{os.urandom(8).hex()}"


def _ui_resource_uri(kind: str, token: str) -> str:
    """Build a per-call MCP Apps resource URI.

    LibreChat keys iframe fetches on resourceUri. A static URI such as
    ``ui://pharos/approval`` is fetched once and then reused, so every later
    install/search/info card shows the first result. Including a unique token
    forces the host to refetch the matching cache entry.
    """
    return f"ui://pharos/{kind}/{token}"


def _inject_template(template: str, data: object) -> str:
    """Serialize *data* into an HTML template, XSS-safe for ``</script>``."""
    safe_json = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e")
    return template.replace("__DATA__", safe_json)


def _cache_put(cache: dict, key: str, value: object) -> None:
    cache[key] = value
    if len(cache) > _MAX_CACHE_SIZE:
        oldest = next(iter(cache))
        del cache[oldest]


def _is_http_endpoint(raw: object) -> bool:
    """True when *raw* is a usable http(s) MCP endpoint URL."""
    if not isinstance(raw, str):
        return False
    value = raw.strip().lower()
    return value.startswith("https://") or value.startswith("http://")


def _card_str_field(card: object, *names: str) -> str:
    """Return the first non-empty string (or joined list) attribute on *card*."""
    for name in names:
        value = getattr(card, name, None)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        elif isinstance(value, (list, tuple)):
            parts = [str(part).strip() for part in value if str(part).strip()]
            if parts:
                return " ".join(parts)
    return ""


def _card_transports(card: object) -> list[str]:
    raw = getattr(card, "transport", None)
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    return []


def _card_kind_input(card: object | None) -> dict[str, Any]:
    """Build a plain dict for ``classify_install_kind`` (MagicMock-safe)."""
    if card is None:
        return {}
    if isinstance(card, dict):
        return card
    data: dict[str, Any] = {}
    for name in (
        "endpoint", "transport", "transports",
        "command", "bin", "stdio_command", "runtime", "package",
    ):
        value = getattr(card, name, None)
        if value in (None, "", [], ()):
            continue
        if type(value).__name__ in {"MagicMock", "AsyncMock"}:
            continue
        data[name] = value
    return data


def _install_kind(card: object | None) -> int | None:
    """Classify via T2a — do not reimplement kind rules here."""
    return classify_install_kind(_card_kind_input(card))


def _card_connectable(card: object | None) -> bool:
    """True if *card* classifies as kind 1, 2, or 3."""
    return _install_kind(card) is not None


def _split_name_version(server_id: str) -> tuple[str, str | None]:
    """Split ``name@version``. Last ``@`` wins so scoped names stay intact."""
    if not server_id or "@" not in server_id:
        return server_id, None
    name, _, version = server_id.rpartition("@")
    if not name or not version:
        return server_id, None
    return name, version


def _cli_install_target(server_id: str, card: object | None) -> str:
    """Single argv for ``pharos install`` — allow ``name@version``."""
    name, version = _split_name_version(server_id)
    if version:
        return f"{name}@{version}"
    card_version = _card_str_field(card, "version") if card is not None else ""
    if card_version:
        return f"{server_id}@{card_version}"
    return server_id


def _canonical_server_id(server_id: str) -> str:
    name, _ = _split_name_version(server_id)
    return name or server_id


def _remote_only_env() -> bool:
    return os.environ.get("PHAROS_REMOTE_ONLY", "").strip().lower() in {
        "true", "1", "yes",
    }


def _remote_only_error(server_id: str) -> dict[str, str]:
    return {
        "status": "error",
        "error": (
            f"Server '{server_id}' requires a local install (kind 2/3), "
            "which is not available when PHAROS_REMOTE_ONLY is set."
        ),
        "server_id": server_id,
    }


_LIST_DASH = "—"


def _list_cell(value: object) -> str:
    if value in (None, "", [], ()):
        return _LIST_DASH
    text = str(value).strip()
    return text if text else _LIST_DASH


def _kind1_list_status(server_id: str) -> str:
    return "connected" if server_id in _connections else "registered"


def _apply_kind1_list_metrics(row: dict[str, Any]) -> dict[str, Any]:
    """Kind 1 never fakes running; SIZE/MEMORY/UPTIME/PORT are em-dashes."""
    row["status"] = _kind1_list_status(row["server_id"])
    row["port"] = _LIST_DASH
    row["size"] = _LIST_DASH
    row["memory"] = _LIST_DASH
    row["uptime"] = _LIST_DASH
    return row


def _blank_list_row(server_id: str) -> dict[str, Any]:
    return {
        "server_id": server_id,
        "name": server_id,
        "version": _LIST_DASH,
        "transport": "unknown",
        "kind": None,
        "status": "unknown",
        "endpoint": _LIST_DASH,
        "source": "unknown",
        "installed_at": _LIST_DASH,
        "port": _LIST_DASH,
        "size": _LIST_DASH,
        "memory": _LIST_DASH,
        "uptime": _LIST_DASH,
    }


def _row_from_installed_meta(server_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    card = _server_cards.get(server_id)
    kind = meta.get("install_kind")
    if kind not in (1, 2, 3):
        kind = _install_kind(card) if card is not None else None
        if kind is None and _is_http_endpoint(meta.get("endpoint")):
            kind = 1
    row = _blank_list_row(server_id)
    row["name"] = meta.get("name") or (
        str(getattr(card, "display_name", server_id)) if card is not None else server_id
    )
    row["version"] = _list_cell(meta.get("version") or getattr(card, "version", None))
    transport = meta.get("transport", getattr(card, "transport", None) if card else None)
    row["transport"] = transport if transport not in (None, "", []) else "unknown"
    row["kind"] = kind
    row["endpoint"] = _list_cell(meta.get("endpoint") or getattr(card, "endpoint", None))
    row["source"] = meta.get("source") or "mcp_registered"
    row["installed_at"] = _list_cell(meta.get("installed_at"))
    if kind == 1:
        return _apply_kind1_list_metrics(row)
    if server_id in _connections:
        row["status"] = "running" if kind == 3 else "connected"
    elif kind == 2:
        row["status"] = "stopped"
    elif kind == 3:
        row["status"] = "idle"
    else:
        row["status"] = "connected" if server_id in _connections else "registered"
    return row


def _row_from_cli_entry(entry: dict[str, Any]) -> dict[str, Any]:
    server_id = str(entry.get("name") or entry.get("id") or entry.get("server_id") or "unknown")
    kind = entry.get("kind")
    if kind not in (1, 2, 3):
        kind = _install_kind(entry)
    row = _blank_list_row(server_id)
    row["name"] = entry.get("display_name") or server_id
    row["version"] = _list_cell(entry.get("version"))
    row["transport"] = entry.get("transport") or "unknown"
    row["kind"] = kind
    row["status"] = str(entry.get("status") or "unknown")
    row["endpoint"] = _list_cell(entry.get("endpoint"))
    row["source"] = "cli"
    row["port"] = _list_cell(entry.get("port"))
    row["size"] = _list_cell(entry.get("size"))
    row["memory"] = _list_cell(entry.get("memory"))
    row["uptime"] = _list_cell(entry.get("uptime"))
    if kind == 1:
        return _apply_kind1_list_metrics(row)
    return row


def _merge_list_rows(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if value in (None, "", _LIST_DASH, "unknown") and merged.get(key) not in (None, "", _LIST_DASH):
            continue
        merged[key] = value
    kind = merged.get("kind")
    if kind not in (1, 2, 3):
        kind = existing.get("kind") or incoming.get("kind")
        merged["kind"] = kind
    if kind == 1:
        return _apply_kind1_list_metrics(merged)
    return merged


def _parse_cli_list_payload(raw: str) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("NAME") or line.startswith("-"):
                continue
            parts = line.split()
            if not parts:
                continue
            rows.append({
                "name": parts[0],
                "transport": parts[1] if len(parts) > 1 else "unknown",
                "status": parts[2] if len(parts) > 2 else "unknown",
            })
        return rows
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        servers = parsed.get("servers") or parsed.get("installed") or []
        if isinstance(servers, list):
            return [item for item in servers if isinstance(item, dict)]
    return []


async def _collect_installed_rows() -> tuple[list[dict[str, Any]], str | None]:
    """Merge in-memory registrations with ``pharos list --json``."""
    by_id: dict[str, dict[str, Any]] = {}
    note: str | None = None

    for sid, meta in _installed_servers.items():
        by_id[sid] = _row_from_installed_meta(sid, meta)

    for sid in _connections:
        if sid in by_id:
            kind = by_id[sid].get("kind")
            if kind == 1:
                _apply_kind1_list_metrics(by_id[sid])
            elif kind == 3:
                by_id[sid]["status"] = "running"
            else:
                by_id[sid]["status"] = "connected"
            continue
        extra = _blank_list_row(sid)
        extra["source"] = "mcp_connected"
        extra["status"] = "connected"
        extra["kind"] = 1 if _is_http_endpoint(getattr(_server_cards.get(sid), "endpoint", None)) else None
        if extra["kind"] == 1:
            extra["endpoint"] = _list_cell(getattr(_server_cards.get(sid), "endpoint", None))
            _apply_kind1_list_metrics(extra)
        by_id[sid] = extra

    cli = _get_pharos_cli()
    try:
        proc = await asyncio.create_subprocess_exec(
            cli, "list", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        return list(by_id.values()), "pharos list timed out"
    except FileNotFoundError:
        note = "pharos CLI not found; only showing MCP-registered servers."
    except Exception as exc:
        return list(by_id.values()), f"pharos list failed: {exc}"
    else:
        if proc.returncode == 0 and stdout:
            for entry in _parse_cli_list_payload(stdout.decode().strip()):
                row = _row_from_cli_entry(entry)
                sid = row["server_id"]
                if sid in by_id:
                    by_id[sid] = _merge_list_rows(by_id[sid], row)
                else:
                    by_id[sid] = row

    return list(by_id.values()), note


def _compact_list_server(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("server_id", "unknown"),
        "name": row.get("name") or row.get("server_id") or "unknown",
        "version": row.get("version") or _LIST_DASH,
        "transport": row.get("transport", "unknown"),
        "status": row.get("status", "unknown"),
        "kind": row.get("kind"),
        "endpoint": row.get("endpoint", _LIST_DASH),
        "port": row.get("port", _LIST_DASH),
        "size": row.get("size", _LIST_DASH),
        "memory": row.get("memory", _LIST_DASH),
        "uptime": row.get("uptime", _LIST_DASH),
    }


def _endpoint_from_cli_payload(raw: str) -> str | None:
    """Extract an http(s) endpoint from ``_run_pharos_cli`` JSON or stdout."""
    if not raw:
        return None

    def _from_obj(obj: object) -> str | None:
        if not isinstance(obj, dict):
            return None
        for key in ("endpoint", "url", "base_url"):
            value = obj.get(key)
            if _is_http_endpoint(value):
                return str(value).strip()
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        found = _from_obj(parsed)
        if found:
            return found
        stdout = parsed.get("stdout")
        if isinstance(stdout, str) and stdout.strip():
            try:
                inner = json.loads(stdout)
            except json.JSONDecodeError:
                inner = None
            found = _from_obj(inner)
            if found:
                return found
            for token in stdout.split():
                if _is_http_endpoint(token):
                    return token.strip().rstrip(",;")
    for token in raw.split():
        if _is_http_endpoint(token):
            return token.strip().rstrip(",;")
    return None


def _apply_launch_fields(card: object, *, endpoint: str | None = None) -> None:
    """Copy launch data onto *card* so ConnectionManager can connect."""
    if card is None:
        return
    if endpoint:
        for attr in ("endpoint", "local_endpoint"):
            try:
                setattr(card, attr, endpoint)
            except Exception:
                pass
    cmd = launch_command(_card_kind_input(card))
    if cmd and not _card_str_field(card, "stdio_command"):
        try:
            card.stdio_command = cmd
        except Exception:
            pass


def _register_kind1_in_memory(store_id: str, card: object | None) -> str:
    """Bookmark a kind-1 remote when the pharos CLI binary is missing."""
    transport = getattr(card, "transport", None) if card is not None else None
    endpoint = getattr(card, "endpoint", None) if card is not None else None
    _installed_servers[store_id] = {
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "transport": transport,
        "endpoint": endpoint,
        "install_kind": 1,
        "source": "mcp_registered",
        "name": (
            str(getattr(card, "display_name", store_id)) if card is not None else store_id
        ),
        "version": _card_str_field(card, "version") if card is not None else "",
    }
    return json.dumps({
        "status": "registered",
        "server_id": store_id,
        "transport": transport,
        "endpoint": endpoint,
        "install_kind": 1,
        "message": "Remote server registered.",
    })


async def _cli_install_and_maybe_start(
    server_id: str,
    card: object | None,
    kind: int | None,
) -> str:
    """Run ``pharos install {id}@{ver}``; kind 2 also runs ``pharos start``.

    Kind 1/2/3 all shell the CLI so client configs (Cursor ``{type,url}``)
    get written. Kind 1 and 3 do not start. If the binary is missing,
    kind 1 falls back to in-memory registration; kind 2/3 stay errors.
    """
    cli = _get_pharos_cli()
    target = _cli_install_target(server_id, card)
    store_id = _canonical_server_id(target)
    try:
        proc = await asyncio.create_subprocess_exec(
            cli, "install", target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        return json.dumps({"error": "Install timed out (120s)", "server_id": store_id})
    except FileNotFoundError:
        if kind == 1:
            return _register_kind1_in_memory(store_id, card)
        return json.dumps({
            "error": f"pharos CLI not found at '{cli}'",
            "server_id": store_id,
            "hint": (
                "For remote servers, use pharos_search(remote_only=True) to find "
                "servers that don't require local installation. To install stdio "
                "servers, install the pharos CLI first: pip install pharos-mcp"
            ),
        })

    if proc.returncode != 0:
        return json.dumps({
            "error": "Install failed",
            "stderr": stderr.decode() if stderr else "",
            "server_id": store_id,
        })

    _installed_servers[store_id] = {
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "transport": getattr(card, "transport", None) if card is not None else None,
        "endpoint": getattr(card, "endpoint", None) if card is not None else None,
        "install_kind": kind,
        "version": _split_name_version(target)[1] or (
            _card_str_field(card, "version") if card is not None else ""
        ),
        "name": (
            str(getattr(card, "display_name", store_id)) if card is not None else store_id
        ),
        "source": "cli",
    }

    payload: dict[str, Any] = {
        "status": "installed",
        "server_id": store_id,
        "install_kind": kind,
        "output": stdout.decode().strip() if stdout else "",
    }
    if kind == 2:
        payload["start"] = json.loads(await _run_pharos_cli("start", store_id))
    return json.dumps(payload)


async def _resolve_server_card(
    server_id: str,
    *,
    hydrate_if: Any = None,
) -> tuple[Any, Exception | None]:
    """Return a cached card, hydrating from the registry when needed.

    Cache-first stays in place for connectable cards (emoji / space names
    that 400 on ``/v1/packages/{name}``). Incomplete search cards — remote
    with no endpoint, or stdio with no command — are refreshed from package
    detail so install/info see the same fields as ``GET /v1/packages/{id}``.

    *hydrate_if*, when set, is ``callable(card) -> bool`` that decides
    whether a cached card should be refreshed. Default is
    ``not _card_connectable(card)``.
    """
    card = _server_cards.get(server_id)
    should_hydrate = hydrate_if if hydrate_if is not None else (
        lambda cached: not _card_connectable(cached)
    )
    if card is not None and not should_hydrate(card):
        return card, None

    try:
        fetched = await _get_client().get_server(server_id)
    except Exception as exc:
        return card, exc

    if fetched is None:
        return card, None

    if _card_connectable(fetched) or card is None:
        _server_cards[server_id] = fetched
        return fetched, None
    return card, None

# Physical approval mode — when set, pharos_approve requires a UI-originated
# token that the AI agent cannot generate. This prevents the AI from
# auto-approving connections in end-user chatbot scenarios (scenario 3).
# Set PHAROS_REQUIRE_PHYSICAL_APPROVAL=true to enable physical approval
# (prevents AI from calling pharos_approve directly). Default: false for CLI/dev,
# set to true in Docker compose for end-user chatbot deployments.
_REQUIRE_PHYSICAL_APPROVAL = os.environ.get(
    "PHAROS_REQUIRE_PHYSICAL_APPROVAL", "false"
).lower() in ("true", "1", "yes")

# Approval wait timeout (seconds). The tool will block for this long
# waiting for the user to click Approve/Deny in the iframe. If the user
# does not respond within this time, the install is auto-denied.
# Set via PHAROS_APPROVAL_TIMEOUT env var. Default: 120 seconds.
_PHAROS_APPROVAL_TIMEOUT = int(os.environ.get("PHAROS_APPROVAL_TIMEOUT", "120"))

# Per-token asyncio events for blocking approval flow.
# Key: approval_token, Value: dict with "event" (asyncio.Event), "result" (str|None)
_approval_events: dict[str, dict] = {}

# Per-token approval results for the non-blocking approval flow.
# Key: approval_token, Value: dict with "result", "tools_count", "tools", "error", "expires_at"
# The /approve and /deny endpoints set the "result" field. pharos_check_approval
# polls this dict to report the final status to the AI.
_approval_results: dict[str, dict] = {}

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


async def _run_pharos_cli(*args: str) -> str:
    """Run the pharos CLI with the given arguments and return a JSON result string.

    Spawns ``pharos <args>``, awaits completion with a 30-second timeout, and
    returns ``json.dumps({"status": "ok"|"error", "stdout": ..., "stderr": ...})``.
    On timeout returns an error dict with a ``"timeout"`` key; on binary-not-found
    returns an error dict with a hint to install the CLI.
    """
    cli = _get_pharos_cli()
    try:
        proc = await asyncio.create_subprocess_exec(
            cli, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        return json.dumps({
            "status": "error",
            "error": "Command timed out (30s)",
            "timeout": True,
        })
    except FileNotFoundError:
        return json.dumps({
            "status": "error",
            "error": f"pharos CLI not found at '{cli}'",
            "hint": "Install the pharos CLI to use this feature.",
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": f"Command failed: {e}",
        })

    stdout_str = stdout.decode().strip() if stdout else ""
    stderr_str = stderr.decode().strip() if stderr else ""
    status = "ok" if proc.returncode == 0 else "error"

    return json.dumps({
        "status": status,
        "stdout": stdout_str,
        "stderr": stderr_str,
        "returncode": proc.returncode,
    })


# ─── Pending Approval Helpers ─────────────────────────────────────────────────
# These functions were originally inside pharos_connect. They are kept here so
# that pharos_install_apps (Phase 3) can reuse the approval token/nonce logic.


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


def _create_pending_connection(card: Any, server_id: str, endpoint: str | None,
                                purpose: str) -> str | None:
    """Create a pending approval entry and cache the approval UI data.

    Returns the raw approval token on success, or None if the pending
    connections cap has been reached. The token is returned to the AI;
    the nonce is injected into the HTML card but never exposed to the AI.

    This logic was extracted from the removed pharos_connect tool so that
    pharos_install_apps (Phase 3) can reuse it.
    """
    # Generate a pending connection token (URL-safe — no raw server_id)
    raw_token = _new_ui_token("pc")
    signature = _sign_pending(raw_token, server_id)
    expires_at = int(time.time()) + 300  # 5-minute expiry

    # Security: cap pending connections to prevent memory exhaustion.
    # Clean up expired entries first, then enforce a hard limit.
    now = int(time.time())
    expired = [k for k, v in _pending_connections.items() if now >= v["expires_at"]]
    for k in expired:
        del _pending_connections[k]
    if len(_pending_connections) >= 50:
        return None

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

    return raw_token


# ─── MCP Tools ────────────────────────────────────────────────────────────────
#
# A/B tool registration: tools that have separate CLI and Apps variants are
# registered conditionally based on MCP_APPS_MODE. Non-A/B tools (daemon,
# list_tools, call_tool, etc.) are registered unconditionally below.

if not MCP_APPS_MODE:

    @mcp.tool()
    async def pharos_search(
        query: str,
        limit: int = 10,
        remote_only: bool = False,
        transport: str = "",
        registry: str = "",
        page: int = 1,
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
            transport: Filter by transport (stdio, http-sse, streamable-http,
                sse, http). Empty = no transport filter. Same values as
                ``pharos search --transport``. Combined with remote_only when
                both are set: explicit transport is sent, not replaced.
            registry: Filter by source catalog (mcp.io, mcp.so, pharos,
                smithery). Empty = no registry filter.
            page: 1-based page. Mapped to the registry cursor offset
                ``(page-1)*limit`` the same way the CLI does.

        Returns:
            JSON array of matching servers with id, name, description, version,
            transport, source_registry, publisher, tools_count, and capabilities.
            Includes nextCursor and total when the registry returns them.
        """
        client = _get_client()
        limit = min(max(limit, 1), 50)
        if page < 1:
            page = 1

        filters = _search_filters(
            remote_only=remote_only,
            transport=transport,
            registry=registry,
            page=page,
            limit=limit,
        )

        try:
            results = await client.search(text=query, filters=filters, limit=limit)
        except NoServersFound:
            return json.dumps({"results": [], "message": "No servers found. Try a different query."})
        except RegistryUnavailable as e:
            return json.dumps({"error": f"Registry unavailable: {e}", "results": []})

        # PHAROS_REMOTE_ONLY is capability, not UI: search requires an endpoint.
        if _remote_only_env():
            filtered = [
                r for r in results
                if _is_http_endpoint(getattr(r.card, "endpoint", None))
            ]
            if isinstance(results, SearchResults):
                results = SearchResults(
                    filtered,
                    next_cursor=results.next_cursor,
                    total=results.total,
                )
            else:
                results = filtered

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
                "source_registry": _result_source_registry(r),
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

        payload: dict[str, Any] = {
            "results": output,
            "count": len(output),
            "search_id": search_id,
        }
        payload.update(_search_page_meta(results))
        return json.dumps(payload)


    @mcp.tool()
    async def pharos_install(server_id: str) -> str:
        """Install an MCP server from the PHAROS registry to the local machine.

        Kinds 1, 2, and 3 shell ``pharos install {id}@{ver}`` so client
        configs are written (Cursor remotes need ``{type,url}``). Kind 2
        also runs ``pharos start``. If the CLI binary is missing, kind 1
        falls back to in-memory registration; kinds 2 and 3 return an error.

        Args:
            server_id: The server ID from search results (e.g. "test-echo-server")

        Returns:
            JSON with install status, version, and install path (stdio) or
            endpoint URL (remote).
        """
        # Cache-first when the card is already connectable (emoji/space names
        # 400 on /v1/packages/{name}). Incomplete search cards are hydrated
        # from package detail before we classify.
        card, _fetch_err = await _resolve_server_card(server_id)
        if card is None:
            name, _ver = _split_name_version(server_id)
            if name != server_id:
                card, _fetch_err = await _resolve_server_card(name)

        kind = _install_kind(card) if card is not None else None
        if remote_only_blocks(kind):
            return json.dumps(_remote_only_error(server_id))

        transport = getattr(card, "transport", None) if card is not None else None
        endpoint = getattr(card, "endpoint", None) if card is not None else None

        # Kind 1/2/3: shell ``pharos install {id}@{ver}``; kind 2 also starts.
        # Kind 1 FileNotFoundError falls back to in-memory register.
        if kind in (1, 2, 3):
            return await _cli_install_and_maybe_start(server_id, card, kind)

        # Unclassifiable: keep the previous remote-vs-CLI fallback.
        remote_transports = ("sse", "streamable-http", "http", "http+sse", "http-sse")
        transports = _card_transports(card) if card is not None else []
        if transport and any(t in remote_transports for t in transports):
            transport_str = ", ".join(transports)
            if not endpoint:
                return json.dumps({
                    "error": f"Server '{server_id}' has transport '{transport_str}' but no endpoint URL",
                    "server_id": server_id,
                })
            store_id = _canonical_server_id(server_id)
            _installed_servers[store_id] = {
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "transport": transport,
                "endpoint": endpoint,
                "install_kind": 1,
                "source": "mcp_registered",
            }
            return json.dumps({
                "status": "registered",
                "server_id": store_id,
                "transport": transport,
                "endpoint": endpoint,
                "message": "Remote server registered.",
            })

        return await _cli_install_and_maybe_start(server_id, card, kind)


    @mcp.tool()
    async def pharos_list() -> str:
        """List all MCP servers installed or registered on the local machine.

        Returns servers installed via pharos_install (remote registrations)
        as well as servers installed via the pharos CLI (by checking the
        ~/.pharos directory for installed server configs).

        Returns:
            JSON array of installed servers with id, transport, status,
            and install metadata.
        """
        results, note = await _collect_installed_rows()
        payload: dict[str, Any] = {"installed": results, "count": len(results)}
        if note:
            if "timed out" in note or "failed" in note:
                payload["error"] = note
            else:
                payload["note"] = note
        return json.dumps(payload)


    @mcp.tool()
    async def pharos_remove(server_id: str) -> str:
        """Remove an MCP server from the local machine.

        For remote-registered servers (installed via pharos_install), this
        removes the in-memory registration. For CLI-installed servers, this
        calls the pharos CLI to uninstall the server.

        Args:
            server_id: The server ID to remove

        Returns:
            JSON with removal status.
        """
        # Remove in-memory registration if present
        removed_local = False
        if server_id in _installed_servers:
            del _installed_servers[server_id]
            removed_local = True

        # Disconnect if connected
        if server_id in _connections:
            try:
                conn = _connections[server_id]
                if hasattr(conn, "close"):
                    await conn.close()
                del _connections[server_id]
            except Exception:
                pass

        # Call pharos CLI to uninstall
        cli = _get_pharos_cli()
        try:
            proc = await asyncio.create_subprocess_exec(
                cli, "uninstall", server_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            return json.dumps({"error": "Removal timed out", "server_id": server_id})
        except FileNotFoundError:
            if removed_local:
                return json.dumps({"status": "unregistered", "server_id": server_id,
                                   "message": "Removed MCP registration. pharos CLI not found for full uninstall."})
            return json.dumps({"error": "pharos CLI not found", "server_id": server_id})
        except Exception as e:
            return json.dumps({"error": f"Removal failed: {e}", "server_id": server_id})

        if proc.returncode != 0:
            err = stderr.decode().strip() if stderr else ""
            if removed_local:
                return json.dumps({"status": "partial", "server_id": server_id,
                                   "message": "Removed MCP registration, but CLI uninstall failed.",
                                   "cli_error": err})
            return json.dumps({"error": "Uninstall failed", "stderr": err, "server_id": server_id})

        return json.dumps({
            "status": "removed",
            "server_id": server_id,
            "output": stdout.decode().strip() if stdout else "",
        })


    @mcp.tool()
    async def pharos_info(server_id: str) -> str:
        """Get detailed information about a specific MCP server.

        Shells out to ``pharos info <server_id>`` to retrieve the full server
        card including publisher, capabilities, transport, and endpoint details.

        Args:
            server_id: The server ID to look up (e.g. "test-echo-server")

        Returns:
            JSON with server details from the registry, or an error if the
            server is not found or the CLI is unavailable.
        """
        return await _run_pharos_cli("info", server_id)


    @mcp.tool()
    async def pharos_publish(server_card_path: str = "") -> str:
        """Publish a server card to the PHAROS registry.

        Shells out to ``pharos publish [dir]`` to upload the server card from
        the given directory (or the current directory if no path is provided)
        to the registry. The publisher must be authenticated.

        Args:
            server_card_path: Path to the directory containing the server card
                JSON file. Defaults to the current directory if empty.

        Returns:
            JSON with publish status, including the published server ID and
            version, or an error if publication fails.
        """
        if server_card_path:
            return await _run_pharos_cli("publish", server_card_path)
        return await _run_pharos_cli("publish")

else:

    # ─── Apps Mode: _apps Variants with HTML Templates ──────────────────────
    #
    # These are registered with meta={"ui": {"resourceUri": ...}} annotations
    # so the host knows to render the tool result in a sandboxed iframe.
    # Each tool reuses the same backend logic as its CLI-mode counterpart,
    # but returns JSON with an "html" field containing a full HTML document.
    # The HTML is rendered in a sandboxed iframe by the MCP Apps host.

    @mcp.tool(meta={"ui": {"resourceUri": "ui://pharos/results"}})
    async def pharos_search_apps(
        query: str,
        limit: int = 10,
        remote_only: bool = False,
        transport: str = "",
        registry: str = "",
        page: int = 1,
    ) -> str:
        """Search the PHAROS registry for MCP servers (Apps mode).

        Renders search results as an interactive iframe table with per-server
        install buttons. The user can browse results and request installation
        visually.

        Args:
            query: Natural-language search query
            limit: Maximum number of results (default 10, max 50)
            remote_only: If True, only return remote-transport servers
            transport: Filter by transport (stdio, http-sse, streamable-http,
                sse, http). Empty = no transport filter.
            registry: Filter by source catalog (mcp.io, mcp.so, pharos,
                smithery). Empty = no registry filter.
            page: 1-based page mapped to registry cursor offset
                ``(page-1)*limit``.

        Returns:
            JSON with status, results array, search_id, and an html field
            containing the rendered results table for the iframe.
        """
        global _current_search_id

        client = _get_client()
        limit = min(max(limit, 1), 50)
        if page < 1:
            page = 1

        filters = _search_filters(
            remote_only=remote_only,
            transport=transport,
            registry=registry,
            page=page,
            limit=limit,
        )

        try:
            results = await client.search(text=query, filters=filters, limit=limit)
        except NoServersFound:
            search_id = _new_ui_token("sr")
            _cache_put(_search_results_cache, search_id, [])
            _current_search_id = search_id
            return json.dumps({
                "status": "no_results",
                "results": [],
                "count": 0,
                "search_id": search_id,
                "ui_resource_uri": _ui_resource_uri("results", search_id),
            })
        except RegistryUnavailable as e:
            return json.dumps({
                "status": "error",
                "error": f"Registry unavailable: {e}",
                "results": [],
            })

        if _remote_only_env():
            filtered = [
                r for r in results
                if _is_http_endpoint(getattr(r.card, "endpoint", None))
            ]
            if isinstance(results, SearchResults):
                results = SearchResults(
                    filtered,
                    next_cursor=results.next_cursor,
                    total=results.total,
                )
            else:
                results = filtered

        # Cache cards for later use (same logic as pharos_search)
        for r in results:
            _server_cards[r.card.id] = r.card

        output = []
        for r in results:
            card = r.card
            raw = getattr(r, "raw_item", {}) or {}

            # Extract extra fields from the raw registry item that are not
            # part of the ServerCard model (downloads, readme, etc.).
            downloads = (
                raw.get("downloads")
                or raw.get("download_count")
                or raw.get("weekly_downloads")
                or raw.get("install_count")
                or raw.get("downloads30d")
            )
            stars = raw.get("stars") or raw.get("github_stars")
            category = raw.get("category") or raw.get("categories")
            source_registry = raw.get("source_registry") or getattr(card, "source_registry", None)

            # Pricing — use the structured PricingSpec if present, otherwise
            # fall back to the raw registry field.
            pricing_label = "free"
            if card.pricing is not None:
                pricing_label = card.pricing.model
            elif raw.get("pricing"):
                if isinstance(raw["pricing"], str):
                    pricing_label = raw["pricing"]
                elif isinstance(raw["pricing"], dict) and raw["pricing"].get("model"):
                    pricing_label = raw["pricing"]["model"]

            # Score from search relevance (if the registry provides one)
            score = getattr(r, "score", None)

            output.append({
                "id": card.id,
                "name": card.display_name,
                "description": _render_markdown(card.description),
                "version": card.version,
                "transport": list(card.transport) if card.transport else [],
                "publisher": {
                    "name": card.publisher.name if card.publisher else "unknown",
                    "verified": bool(card.publisher.verified) if card.publisher else False,
                },
                "tools_count": int(getattr(card, "tools_count", 0) or 0),
                "capabilities": list(card.capabilities) if card.capabilities else [],
                "endpoint": getattr(card, "endpoint", None),
                "tags": list(getattr(card, "tags", []) or []),
                "pricing": pricing_label,
                "downloads": int(downloads) if downloads is not None else None,
                "stars": int(stars) if stars is not None else None,
                "source_registry": source_registry,
                "category": category if category else None,
                "score": float(score) if score is not None else None,
                "documentation_url": getattr(card, "documentation_url", None),
                "license": raw.get("license"),
            })

        # Cache for UI resource rendering (same pattern as pharos_search)
        search_id = _new_ui_token("sr")
        _cache_put(_search_results_cache, search_id, output)
        _current_search_id = search_id

        payload: dict[str, Any] = {
            "status": "ok",
            "count": len(output),
            "search_id": search_id,
            "ui_resource_uri": _ui_resource_uri("results", search_id),
            "results": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "version": r["version"],
                    "transport": r["transport"],
                    "source_registry": r["source_registry"],
                    "publisher": r["publisher"]["name"],
                    "verified": r["publisher"]["verified"],
                    "tools_count": r["tools_count"],
                    "pricing": r["pricing"],
                }
                for r in output
            ],
        }
        payload.update(_search_page_meta(results))
        return json.dumps(payload)


    @mcp.tool(meta={"ui": {"resourceUri": "ui://pharos/info"}})
    async def pharos_info_apps(server_id: str) -> str:
        """Get detailed information about an MCP server (Apps mode).

        Renders the server card in an iframe with publisher details,
        capabilities, transport, tools, pricing, and tags.

        Args:
            server_id: The server ID from search results

        Returns:
            JSON with status, server_id, and an html field containing the
            rendered server detail card for the iframe.
        """
        # Cache-first when connectable; hydrate incomplete search cards
        # so the details table shows the package-detail endpoint.
        card, fetch_err = await _resolve_server_card(server_id)
        if card is None:
            return json.dumps({
                "status": "error",
                "error": f"Failed to get server info: {fetch_err}",
                "server_id": server_id,
            })

        server_data = {
            "id": str(card.id),
            "display_name": str(card.display_name),
            "name": str(card.display_name),
            "description": str(card.description),
            "version": str(card.version),
            "transport": list(card.transport) if card.transport else [],
            "publisher": {
                "name": str(card.publisher.name) if card.publisher else "unknown",
                "verified": bool(card.publisher.verified) if card.publisher else False,
            },
            "endpoint": str(getattr(card, "endpoint", None) or "N/A"),
            "capabilities": list(card.capabilities) if card.capabilities else [],
            "tools_count": int(getattr(card, "tools_count", 0) or 0),
            "pricing": "free",
            "tags": list(getattr(card, "tags", []) or []),
        }

        html_data = {"server": server_data}

        # Cache under a unique token so consecutive info cards do not collide
        info_token = _new_ui_token("info")
        _cache_put(_info_data_cache, info_token, html_data)
        global _current_info_id
        _current_info_id = info_token

        return json.dumps({
            "status": "ok",
            "server_id": server_id,
            "ui_resource_uri": _ui_resource_uri("info", info_token),
            "server": {
                "id": server_data["id"],
                "name": server_data["display_name"],
                "version": server_data["version"],
                "transport": server_data["transport"],
                "publisher": server_data.get("publisher", {}).get("name", "unknown"),
                "verified": server_data.get("publisher", {}).get("verified", False),
                "tools_count": server_data.get("tools_count", 0),
                "pricing": server_data.get("pricing", "free"),
                "endpoint": server_data.get("endpoint"),
            },
        })


    @mcp.tool()
    async def pharos_install_apps(
        server_id: str,
        purpose: str = "User request",
    ) -> str:
        """Install an MCP server with visual approval flow (Apps mode).

        Creates a pending approval connection and renders an approval card in
        the iframe. This tool returns IMMEDIATELY with status "pending_approval"
        — it does NOT block waiting for the user. The iframe polls
        /approval/status to update its UI when the user clicks Approve/Deny.
        After calling this tool, call pharos_check_approval to poll the result.

        Args:
            server_id: The server ID to install
            purpose: Why the installation is being requested (shown to user)

        Returns:
            JSON with status "pending_approval" and the approval_token.
            Call pharos_check_approval with the approval_token to get the
            final result ("installed", "denied", "timeout", or "pending").
        """
        # Cache-first when the card is already connectable (emoji/space
        # names that 400 on /v1/packages/{name}). Incomplete search cards
        # (remote + no endpoint, stdio + no command) are hydrated from
        # package detail. Errors never advertise an approval UI.
        card, fetch_err = await _resolve_server_card(server_id)
        if card is None:
            name, _ver = _split_name_version(server_id)
            if name != server_id:
                card, fetch_err = await _resolve_server_card(name)
        if card is None:
            return json.dumps({
                "status": "error",
                "error": f"Cannot install: server '{server_id}' not found: {fetch_err}",
                "server_id": server_id,
            })

        kind = _install_kind(card)
        if remote_only_blocks(kind):
            return json.dumps(_remote_only_error(server_id))

        # Determine endpoint from the (possibly hydrated) card
        endpoint = getattr(card, "endpoint", None)

        # Kind 1 needs a publisher URL. Kind 2/3 are launched locally — do
        # not refuse them for a missing endpoint, and never show an
        # approval card for unclassifiable cards.
        if kind is None:
            transports = _card_transports(card)
            return json.dumps({
                "status": "error",
                "server_id": server_id,
                "error": (
                    f"Server '{server_id}' is not installable "
                    f"(transport {transports}, no endpoint or launch command)."
                ),
            })
        if kind == 1 and not _is_http_endpoint(endpoint):
            return json.dumps({
                "status": "error",
                "server_id": server_id,
                "error": (
                    f"Server '{server_id}' has no endpoint URL. "
                    f"The server cannot be connected to. Contact the publisher or try a "
                    f"different server."
                ),
            })

        # Create pending approval connection (reuses existing helper)
        token = _create_pending_connection(card, server_id, endpoint, purpose)
        if token is None:
            return json.dumps({
                "status": "error",
                "error": "Too many pending connections. Wait for existing approvals to expire.",
                "server_id": server_id,
            })

        # Retrieve the approval nonce from pending connections (UI-only)
        approval_nonce = _pending_connections[token]["approval_nonce"]

        # Build HTML data WITH the nonce (for the iframe button)
        html_data = {
            "server_id": server_id,
            "server": {
                "id": str(card.id),
                "display_name": str(card.display_name),
                "name": str(card.display_name),
                "description": str(card.description),
                "version": str(card.version),
                "transport": list(card.transport) if card.transport else [],
                "publisher": {
                    "name": str(card.publisher.name) if card.publisher else "unknown",
                    "verified": bool(card.publisher.verified) if card.publisher else False,
                },
                "endpoint": str(endpoint) if endpoint else "N/A",
                "tools_count": int(getattr(card, "tools_count", 0) or 0),
                "pricing": "free",
            },
            "purpose": purpose,
            "approval_token": token,
            "approval_nonce": approval_nonce,  # UI-only, NOT in AI-visible JSON
        }

        # Cache for UI resource rendering
        _cache_put(_approval_data_cache, token, html_data)
        global _current_approval_token
        _current_approval_token = token

        # Initialize the approval result tracker. The /approve and /deny
        # endpoints will set _approval_results[token] when the user acts.
        # pharos_check_approval polls this dict.
        _approval_results[token] = {
            "result": None,  # None=pending, "approved", "denied", "timeout"
            "tools_count": 0,
            "tools": [],
            "error": None,
            "expires_at": time.time() + _PHAROS_APPROVAL_TIMEOUT,
        }

        return json.dumps({
            "status": "pending_approval",
            "approval_token": token,
            "ui_resource_uri": _ui_resource_uri("approval", token),
            "server_id": server_id,
            "server_name": str(card.display_name),
            "message": (
                f"Approval card rendered for {card.display_name}. "
                f"Call pharos_check_approval with approval_token='{token}' "
                f"to wait for the user's response."
            ),
        })


    @mcp.tool()
    async def pharos_check_approval(approval_token: str, wait_seconds: int = 25) -> str:
        """Check the status of a pending approval (Apps mode).

        Polls the approval result for a token returned by pharos_install_apps.
        By default, BLOCKS for up to 25 seconds waiting for the user to click
        Approve or Deny. If the user acts during this time, returns the result
        immediately. If the wait expires, returns "pending" — call again to
        continue waiting.

        Args:
            approval_token: The token returned by pharos_install_apps
            wait_seconds: How long to block waiting for a result (default 25, max 30)

        Returns:
            JSON with status:
            - "pending" → user has not yet responded (call again to keep waiting)
            - "installed" → user approved, server connected, tools available
            - "denied" → user clicked Deny
            - "timeout" → approval expired without user response
            - "error" → invalid token or other error
        """
        wait_seconds = min(max(wait_seconds, 1), 30)

        result_data = _approval_results.get(approval_token)

        if result_data is None:
            return json.dumps({
                "status": "error",
                "error": "Invalid or unknown approval token.",
                "approval_token": approval_token,
            })

        # Check for timeout (expiry). Kept as a nested helper so it can be
        # invoked both before and during the blocking wait loop below.
        def _check_timeout():
            if result_data.get("result") is None:
                if time.time() >= result_data.get("expires_at", 0):
                    result_data["result"] = "timeout"
                    result_data["error"] = (
                        f"Approval timed out after {_PHAROS_APPROVAL_TIMEOUT}s. "
                        f"The user did not respond in time."
                    )

        _check_timeout()
        result = result_data.get("result")

        # If not yet resolved, block for up to wait_seconds polling once per
        # second. asyncio.sleep(1) yields control so other tasks (including
        # the HTTP /approve and /deny handlers that set the result) can run.
        # This is safe inside a regular MCP tool call.
        if result is None:
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                await asyncio.sleep(1)
                _check_timeout()
                result = result_data.get("result")
                if result is not None:
                    break

        if result is None:
            return json.dumps({
                "status": "pending",
                "approval_token": approval_token,
                "message": "User has not yet responded. Call this tool again with the same approval_token to continue waiting.",
            })
        elif result == "installed":
            tools = result_data.get("tools", [])
            return json.dumps({
                "status": "installed",
                "approval_token": approval_token,
                "server_id": result_data.get("server_id", ""),
                "tools_count": result_data.get("tools_count", len(tools)),
                "tools": tools,
                "message": f"Server approved and installed. {len(tools)} tools available.",
            })
        elif result == "denied":
            return json.dumps({
                "status": "denied",
                "approval_token": approval_token,
                "message": "User denied the installation.",
            })
        elif result == "timeout":
            # Clean up expired entry
            _approval_results.pop(approval_token, None)
            _pending_connections.pop(approval_token, None)
            return json.dumps({
                "status": "timeout",
                "approval_token": approval_token,
                "message": result_data.get("error", "Approval timed out."),
            })
        elif result == "error":
            _approval_results.pop(approval_token, None)
            _pending_connections.pop(approval_token, None)
            return json.dumps({
                "status": "error",
                "approval_token": approval_token,
                "error": result_data.get("error", "Unknown error during approval."),
                "message": result_data.get("error", "Unknown error during approval."),
            })
        else:
            return json.dumps({
                "status": "error",
                "approval_token": approval_token,
                "message": f"Unexpected approval result: {result}",
            })


    @mcp.tool(meta={"ui": {"resourceUri": "ui://pharos/removal"}})
    async def pharos_remove_apps(server_id: str) -> str:
        """Remove an MCP server with visual confirmation (Apps mode).

        Creates a pending removal token and renders a removal confirmation
        card in the iframe. The user must click Remove before the server is
        removed. The approval_nonce is injected into the HTML but is NOT
        included in the JSON response the AI sees.

        Args:
            server_id: The server ID to remove

        Returns:
            JSON with status "pending_removal", removal_token, and an html
            field containing the rendered removal card. The nonce is in the
            HTML only, never in the JSON.
        """
        # Look up server name from cards or installed servers
        server_name = server_id
        server_description = ""
        card = _server_cards.get(server_id)
        if card is not None:
            server_name = str(card.display_name)
            server_description = str(card.description)
        elif server_id in _installed_servers:
            meta = _installed_servers[server_id]
            server_name = meta.get("name", server_id)

        # Create a pending removal token (reuse the approval infrastructure)
        removal_nonce = str(uuid.uuid4())
        removal_token = _new_ui_token("rm")
        signature = _sign_pending(removal_token, server_id)
        expires_at = int(time.time()) + 300  # 5-minute expiry

        # Clean up expired entries first
        now = int(time.time())
        expired = [k for k, v in _pending_connections.items() if now >= v["expires_at"]]
        for k in expired:
            del _pending_connections[k]
        if len(_pending_connections) >= 50:
            return json.dumps({
                "status": "error",
                "error": "Too many pending operations. Try again shortly.",
                "server_id": server_id,
            })

        _pending_connections[removal_token] = {
            "server_id": server_id,
            "card": card,
            "endpoint": None,
            "purpose": "removal",
            "signature": signature,
            "expires_at": expires_at,
            "approval_nonce": removal_nonce,
        }

        # Build HTML data WITH the nonce (for the iframe button)
        html_data = {
            "server_id": server_id,
            "server_name": server_name,
            "server_description": server_description,
            "removal_token": removal_token,
            "approval_nonce": removal_nonce,  # UI-only, NOT in AI-visible JSON
        }

        # Cache for UI resource rendering
        _cache_put(_removal_data_cache, removal_token, html_data)
        global _current_removal_token
        _current_removal_token = removal_token

        # Initialize the removal result tracker (same as install).
        # The /approve endpoint will set result="removed" and /deny will set
        # result="cancelled" when the user acts. pharos_check_removal polls
        # this dict.
        _approval_results[removal_token] = {
            "result": None,  # None=pending, "removed", "cancelled", "timeout"
            "tools_count": 0,
            "tools": [],
            "error": None,
            "expires_at": time.time() + _PHAROS_APPROVAL_TIMEOUT,
        }

        # Return JSON to AI — nonce is NOT here
        return json.dumps({
            "status": "pending_removal",
            "removal_token": removal_token,
            "ui_resource_uri": _ui_resource_uri("removal", removal_token),
            "server_id": server_id,
            "message": (
                f"Removal confirmation card rendered for {server_name}. "
                f"Call pharos_check_removal with removal_token='{removal_token}' "
                f"to wait for the user's response."
            ),
        })


    @mcp.tool()
    async def pharos_check_removal(removal_token: str, wait_seconds: int = 25) -> str:
        """Check the status of a pending removal (Apps mode).

        Polls the removal result for a token returned by pharos_remove_apps.
        By default, BLOCKS for up to 25 seconds waiting for the user to click
        Remove or Cancel. If the user acts during this time, returns the result
        immediately. If the wait expires, returns "pending" — call again to
        continue waiting.

        Args:
            removal_token: The token returned by pharos_remove_apps
            wait_seconds: How long to block waiting for a result (default 25, max 30)

        Returns:
            JSON with status:
            - "pending" → user has not yet responded (call again to keep waiting)
            - "removed" → user confirmed removal, server disconnected and uninstalled
            - "cancelled" → user clicked Cancel
            - "timeout" → removal expired without user response
            - "error" → invalid token or other error
        """
        wait_seconds = min(max(wait_seconds, 1), 30)

        result_data = _approval_results.get(removal_token)

        if result_data is None:
            return json.dumps({
                "status": "error",
                "error": "Invalid or unknown removal token.",
                "removal_token": removal_token,
            })

        def _check_timeout():
            if result_data.get("result") is None:
                if time.time() >= result_data.get("expires_at", 0):
                    result_data["result"] = "timeout"
                    result_data["error"] = (
                        f"Removal timed out after {_PHAROS_APPROVAL_TIMEOUT}s. "
                        f"The user did not respond in time."
                    )

        _check_timeout()
        result = result_data.get("result")

        if result is None:
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                await asyncio.sleep(1)
                _check_timeout()
                result = result_data.get("result")
                if result is not None:
                    break

        if result is None:
            return json.dumps({
                "status": "pending",
                "removal_token": removal_token,
                "message": "User has not yet responded. Call this tool again with the same removal_token to continue waiting.",
            })
        elif result == "removed":
            return json.dumps({
                "status": "removed",
                "removal_token": removal_token,
                "server_id": result_data.get("server_id", ""),
                "message": "Server removed and disconnected successfully.",
            })
        elif result == "cancelled":
            return json.dumps({
                "status": "cancelled",
                "removal_token": removal_token,
                "message": "User cancelled the removal.",
            })
        elif result == "timeout":
            _approval_results.pop(removal_token, None)
            _pending_connections.pop(removal_token, None)
            return json.dumps({
                "status": "timeout",
                "removal_token": removal_token,
                "message": result_data.get("error", "Removal timed out."),
            })
        elif result == "error":
            _approval_results.pop(removal_token, None)
            _pending_connections.pop(removal_token, None)
            return json.dumps({
                "status": "error",
                "removal_token": removal_token,
                "error": result_data.get("error", "Unknown error during removal."),
                "message": result_data.get("error", "Unknown error during removal."),
            })
        else:
            return json.dumps({
                "status": "error",
                "removal_token": removal_token,
                "message": f"Unexpected removal result: {result}",
            })


    @mcp.tool(meta={"ui": {"resourceUri": "ui://pharos/installed"}})
    async def pharos_list_apps() -> str:
        """List installed MCP servers as an interactive table (Apps mode).

        Renders the installed servers in an iframe table with status
        indicators, transport, source, and per-server remove actions.

        Returns:
            JSON with status, servers array, and an html field containing
            the rendered installed servers table for the iframe.
        """
        results, _note = await _collect_installed_rows()

        # Cache for UI resource rendering
        installed_key = _new_ui_token("inst")
        _cache_put(_installed_data_cache, installed_key, results)
        global _current_installed_key
        _current_installed_key = installed_key

        return json.dumps({
            "status": "ok",
            "count": len(results),
            "ui_resource_uri": _ui_resource_uri("installed", installed_key),
            "servers": [_compact_list_server(s) for s in results],
        })


    @mcp.tool(meta={"ui": {"resourceUri": "ui://pharos/publish"}})
    async def pharos_publish_apps(server_card_path: str = "") -> str:
        """Publish a server card to the registry with approval flow (Apps mode).

        Reads the server card from the given path, creates a pending publish
        token, and renders a publishing confirmation card in the iframe showing
        the server card details. The user must click Publish before the card is
        uploaded. The approval_nonce is injected into the HTML but is NOT
        included in the JSON response the AI sees.

        Args:
            server_card_path: Path to the server card JSON file to publish

        Returns:
            JSON with status "pending_publish", publish_token, and an html
            field containing the rendered publish confirmation card. The
            nonce is in the HTML only, never in the JSON.
        """
        # Read and parse the server card from the path
        server_data: dict[str, Any]
        try:
            with open(server_card_path) as f:
                card_json = json.load(f)
            server_data = {
                "id": card_json.get("id", "unknown"),
                "display_name": card_json.get("display_name", card_json.get("id", "unknown")),
                "name": card_json.get("display_name", card_json.get("id", "unknown")),
                "description": card_json.get("description", "No description available."),
                "version": card_json.get("version", "N/A"),
                "transport": card_json.get("transport", []),
                "publisher": card_json.get("publisher", {"name": "unknown"}),
                "tools_count": card_json.get("tools_count", 0),
                "tags": card_json.get("tags", []),
            }
        except FileNotFoundError:
            return json.dumps({
                "status": "error",
                "error": f"Server card not found at path: {server_card_path}",
                "server_card_path": server_card_path,
            })
        except json.JSONDecodeError as e:
            return json.dumps({
                "status": "error",
                "error": f"Invalid JSON in server card: {e}",
                "server_card_path": server_card_path,
            })
        except Exception as e:
            return json.dumps({
                "status": "error",
                "error": f"Failed to read server card: {e}",
                "server_card_path": server_card_path,
            })

        # Create a pending publish token (reuse the approval infrastructure)
        publish_nonce = str(uuid.uuid4())
        publish_token = _new_ui_token("pb")
        signature = _sign_pending(publish_token, str(server_data["id"]))
        expires_at = int(time.time()) + 300  # 5-minute expiry

        # Clean up expired entries first
        now = int(time.time())
        expired = [k for k, v in _pending_connections.items() if now >= v["expires_at"]]
        for k in expired:
            del _pending_connections[k]
        if len(_pending_connections) >= 50:
            return json.dumps({
                "status": "error",
                "error": "Too many pending operations. Try again shortly.",
            })

        _pending_connections[publish_token] = {
            "server_id": str(server_data["id"]),
            "card": None,
            "endpoint": None,
            "purpose": "publish",
            "signature": signature,
            "expires_at": expires_at,
            "approval_nonce": publish_nonce,
        }

        # Build HTML data WITH the nonce (for the iframe button)
        html_data = {
            "server_card_path": server_card_path,
            "server": server_data,
            "publish_token": publish_token,
            "approval_nonce": publish_nonce,  # UI-only, NOT in AI-visible JSON
        }

        # Cache for UI resource rendering
        _cache_put(_publish_data_cache, publish_token, html_data)
        global _current_publish_token
        _current_publish_token = publish_token

        # Return JSON to AI — nonce is NOT here
        return json.dumps({
            "status": "pending_publish",
            "publish_token": publish_token,
            "ui_resource_uri": _ui_resource_uri("publish", publish_token),
            "server_id": str(server_data["id"]),
            "message": f"Publish confirmation required for {server_data['display_name']}. "
                       f"Tell the user to click Publish in the card above.",
        })


# ─── Approval HTTP Endpoint ────────────────────────────────────────────────────
#
# Converted from the former pharos_approve MCP tool. This is a custom HTTP route,
# NOT an MCP tool — it does not appear in tools/list and the AI cannot see or
# call it. Only the iframe UI (user button click) posts to this endpoint.

@mcp.custom_route("/approve", methods=["POST"])
async def approve_endpoint(request: Request) -> JSONResponse:
    """Handle approval confirmations from the iframe UI.

    Expects a JSON body with:
        approval_token: The token returned by the install/connect tool
        approval_nonce: The nonce injected into the HTML card (UI-only)

    Returns JSON with connection status and available tools, or an error.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    approval_token = body.get("approval_token", "")
    approval_nonce = body.get("approval_nonce", "")

    # Look up the pending connection
    pending = _pending_connections.get(approval_token)
    if pending is None:
        return JSONResponse({
            "error": "Invalid or unknown approval token. "
                     "Call the install/connect tool first to get a new token.",
        })

    # Physical approval enforcement — verify the per-token nonce.
    # The nonce is a UUID4 generated server-side in _create_pending_connection.
    # It's injected into the HTML card data but never returned to the AI
    # in the tool response JSON. The AI cannot see it, cannot guess it
    # (122 bits of entropy), and therefore cannot bypass physical approval.
    if _REQUIRE_PHYSICAL_APPROVAL:
        stored_nonce = pending.get("approval_nonce")
        if not stored_nonce or approval_nonce != stored_nonce:
            return JSONResponse({
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
        return JSONResponse({"error": "Invalid approval token signature."})

    # Check expiry
    if time.time() >= pending["expires_at"]:
        del _pending_connections[approval_token]
        return JSONResponse({
            "error": "Approval token expired. Request a new token.",
        })

    server_id = pending["server_id"]
    card = pending["card"]
    endpoint = pending["endpoint"]
    purpose = pending.get("purpose", "User request")

    # Clean up the pending token (one-time use)
    del _pending_connections[approval_token]

    # Handle removal approval — the removal card's Remove button sends
    # ui/approve (same as install's Approve), but there's nothing to
    # connect. Disconnect and uninstall instead.
    if purpose == "removal":
        if server_id in _connections:
            conn = _connections[server_id]
            try:
                await conn.disconnect()
            except Exception:
                pass
            del _connections[server_id]

        _installed_servers.pop(server_id, None)
        _server_cards.pop(server_id, None)

        if approval_token in _approval_results:
            _approval_results[approval_token]["result"] = "removed"
            _approval_results[approval_token]["server_id"] = server_id

        return JSONResponse({
            "status": "removed",
            "server_id": server_id,
            "message": "Server removed successfully.",
        })

    kind = _install_kind(card)
    if remote_only_blocks(kind):
        error_msg = _remote_only_error(server_id)["error"]
        if approval_token in _approval_results:
            _approval_results[approval_token]["result"] = "error"
            _approval_results[approval_token]["error"] = error_msg
        return JSONResponse({"error": error_msg, "server_id": server_id})

    # Kind 2: start the local process, then connect to the resolved URL.
    # Missing endpoint after start is "need start first", never
    # "publisher must provide an endpoint".
    if kind == 2:
        start_raw = await _run_pharos_cli("start", server_id)
        start_data = json.loads(start_raw)
        started_endpoint = _endpoint_from_cli_payload(start_raw)
        if not started_endpoint:
            started_endpoint = await _resolve_local_endpoint(server_id)
        if started_endpoint:
            endpoint = started_endpoint
            _apply_launch_fields(card, endpoint=started_endpoint)
        elif not _is_http_endpoint(endpoint):
            error_msg = (
                "Need to start this server first. "
                f"pharos start did not yield a local endpoint ({start_data.get('error') or start_data.get('stderr') or start_data.get('status')})."
            )
            if approval_token in _approval_results:
                _approval_results[approval_token]["result"] = "error"
                _approval_results[approval_token]["error"] = error_msg
            return JSONResponse({"error": error_msg, "server_id": server_id})
    elif kind == 3:
        _apply_launch_fields(card)
    elif kind == 1 and not _is_http_endpoint(endpoint):
        error_msg = "Server has no endpoint or launch command and cannot be connected to."
        if approval_token in _approval_results:
            _approval_results[approval_token]["result"] = "error"
            _approval_results[approval_token]["error"] = error_msg
        return JSONResponse({"error": error_msg, "server_id": server_id})
    elif kind is None and not _is_http_endpoint(endpoint) and not launch_command(_card_kind_input(card)):
        error_msg = "Server has no endpoint or launch command and cannot be connected to."
        if approval_token in _approval_results:
            _approval_results[approval_token]["result"] = "error"
            _approval_results[approval_token]["error"] = error_msg
        return JSONResponse({"error": error_msg, "server_id": server_id})

    # Check if already connected (race condition guard)
    if server_id in _connections:
        # Record the result for pharos_check_approval
        if approval_token in _approval_results:
            _approval_results[approval_token]["result"] = "installed"
            _approval_results[approval_token]["server_id"] = server_id
        return JSONResponse({
            "status": "already_connected",
            "server_id": server_id,
            "message": "Already connected. Use pharos_list_tools to see available tools.",
        })

    # Guard against double-approval: check if this token was already processed
    result_entry = _approval_results.get(approval_token)
    if result_entry is not None and result_entry.get("result") is not None:
        return JSONResponse({
            "status": result_entry["result"],
            "server_id": server_id,
            "message": "This approval token has already been processed.",
            "tools_count": result_entry.get("tools_count", 0),
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
        transport = await mgr.connect(card, token)
        # Wrap the raw transport in an MCPConnection so _list_server_tools,
        # pharos_list_tools, and pharos_call_tool can use the high-level
        # MCP protocol methods (initialize, tools/list, tools/call).
        mcp_conn = MCPConnection(transport, server_id)
        try:
            await mcp_conn.initialize()
        except Exception:
            # initialize() failure is non-fatal; tools/list may still work
            # for servers that don't require the handshake.
            pass
        _connections[server_id] = mcp_conn

        # Also register as installed so pharos_list_apps finds it
        _installed_servers[server_id] = {
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "transport": getattr(card, "transport", None),
            "endpoint": endpoint,
            "source": "approved",
            "install_kind": kind,
            "name": str(getattr(card, "display_name", server_id)) if card is not None else server_id,
        }

        # List initial tools
        tools = await _list_server_tools(server_id)

        # Record the result for pharos_check_approval
        if approval_token in _approval_results:
            _approval_results[approval_token]["result"] = "installed"
            _approval_results[approval_token]["server_id"] = server_id
            _approval_results[approval_token]["tools_count"] = len(tools)
            _approval_results[approval_token]["tools"] = tools

        # Also signal any legacy event-based waiters
        if approval_token in _approval_events:
            _approval_events[approval_token]["result"] = "approved"
            _approval_events[approval_token]["event"].set()

        return JSONResponse({
            "status": "connected",
            "server_id": server_id,
            "endpoint": endpoint,
            "tools_count": len(tools),
            "tools": tools,
        })
    except ConnectionFailed as e:
        detail = str(e)
        lowered = detail.lower()
        if "not ready" in lowered or ("local" in lowered and "endpoint" in lowered):
            detail = (
                "Need to start this server first. "
                "Local HTTP endpoint is not ready after pharos start."
            )
        error_msg = f"Connection failed: {detail}"
        if approval_token in _approval_results:
            _approval_results[approval_token]["result"] = "error"
            _approval_results[approval_token]["error"] = error_msg
        return JSONResponse({"error": error_msg, "server_id": server_id})
    except Exception as e:
        # Record the failure for pharos_check_approval
        if approval_token in _approval_results:
            _approval_results[approval_token]["result"] = "error"
            _approval_results[approval_token]["error"] = f"Connection error: {e}"
        return JSONResponse({"error": f"Connection error: {e}", "server_id": server_id})


@mcp.custom_route("/deny", methods=["POST"])
async def deny_endpoint(request: Request) -> JSONResponse:
    """Handle denial from the iframe UI.

    Expects a JSON body with:
        approval_token: The token returned by the install/connect tool
        approval_nonce: The nonce injected into the HTML card (UI-only)

    Signals the waiting pharos_install_apps tool that the user denied.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    token = body.get("approval_token", "")
    nonce = body.get("approval_nonce", "")

    pending = _pending_connections.get(token)
    if pending is None:
        return JSONResponse({"error": "Invalid or unknown token"})

    if _REQUIRE_PHYSICAL_APPROVAL:
        stored_nonce = pending.get("approval_nonce")
        if not stored_nonce or nonce != stored_nonce:
            return JSONResponse({"error": "Physical approval required"})

    # Signal the waiting tool (legacy event-based flow)
    if token in _approval_events:
        _approval_events[token]["result"] = "denied"
        _approval_events[token]["event"].set()

    # Record the result for pharos_check_approval / pharos_check_removal
    # (non-blocking flow). Removals use "cancelled" instead of "denied".
    if token in _approval_results:
        if pending.get("purpose") == "removal":
            _approval_results[token]["result"] = "cancelled"
        else:
            _approval_results[token]["result"] = "denied"

    # Clean up
    _pending_connections.pop(token, None)

    if pending.get("purpose") == "removal":
        return JSONResponse({"status": "cancelled"})

    return JSONResponse({"status": "denied"})


@mcp.custom_route("/approval/status", methods=["GET"])
async def approval_status_endpoint(request: Request) -> JSONResponse:
    """Check the status of a pending approval.

    Called by the iframe UI to poll whether the user has clicked Approve/Deny.
    This is a GET endpoint so the iframe can include the token as a query param.

    Query params:
        approval_token: The token returned by pharos_install_apps

    Returns JSON with the current approval status:
        "pending", "installed", "denied", "timeout", or "error"
    """
    from urllib.parse import parse_qs
    query = parse_qs(request.url.query)
    approval_token = query.get("approval_token", [""])[0]

    if not approval_token:
        return JSONResponse({"error": "Missing approval_token query parameter"}, status_code=400)

    result_data = _approval_results.get(approval_token)
    if result_data is None:
        return JSONResponse({"status": "error", "error": "Invalid or unknown approval token"})

    # Check for timeout
    if result_data.get("result") is None:
        if time.time() >= result_data.get("expires_at", 0):
            result_data["result"] = "timeout"

    result = result_data.get("result")
    if result is None:
        return JSONResponse({"status": "pending"})
    elif result == "installed":
        return JSONResponse({
            "status": "installed",
            "server_id": result_data.get("server_id", ""),
            "tools_count": result_data.get("tools_count", 0),
        })
    elif result == "denied":
        return JSONResponse({"status": "denied"})
    elif result == "timeout":
        return JSONResponse({"status": "timeout"})
    else:
        return JSONResponse({
            "status": "error",
            "error": result_data.get("error", "Unknown error"),
        })


# ─── Non-A/B Tools (registered unconditionally in both modes) ──────────────────

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
            "error": f"Not connected to '{server_id}'. Install and approve the server first.",
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
            "error": f"Not connected to '{server_id}'. Install and approve the server first.",
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


@mcp.tool()
async def pharos_daemon_status() -> str:
    """Check the status of the Pharos daemon.

    The Pharos daemon manages local MCP server processes, including
    auto-unload for idle servers and hot-reload of configurations.
    This tool queries the daemon status via the pharos CLI.

    Returns:
        JSON with daemon running status, PID, uptime, and managed servers.
    """
    cli = _get_pharos_cli()
    try:
        proc = await asyncio.create_subprocess_exec(
            cli, "daemon", "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    except asyncio.TimeoutError:
        return json.dumps({"error": "Daemon status timed out"})
    except FileNotFoundError:
        return json.dumps({"error": "pharos CLI not found",
                           "hint": "Install the pharos CLI to use daemon features."})
    except Exception as e:
        return json.dumps({"error": f"Status check failed: {e}"})

    output = stdout.decode().strip() if stdout else ""
    err = stderr.decode().strip() if stderr else ""

    # Parse common status outputs
    is_running = "running" in output.lower() and "not running" not in output.lower()

    return json.dumps({
        "running": is_running,
        "raw_output": output,
        "raw_stderr": err if err else None,
    })


@mcp.tool()
async def pharos_start(server_id: str) -> str:
    """Start a stopped MCP server.

    Shells out to ``pharos start <server_id>``. Stdio servers auto-start when
    an MCP client connects, so this is primarily for remote (SSE/streamable-http)
    servers that have been explicitly stopped.

    Args:
        server_id: The server ID to start

    Returns:
        JSON with start status, stdout, and stderr from the CLI.
    """
    # If the server is already connected via the approval flow, there's no
    # daemon process to start — the connection is live. Return immediately
    # so this works in Docker (no pharos CLI needed).
    if server_id in _connections:
        return json.dumps({
            "status": "already_running",
            "server_id": server_id,
            "message": "Server is already connected. Use pharos_list_tools to see available tools.",
        })

    return await _run_pharos_cli("start", server_id)


@mcp.tool()
async def pharos_stop(server_id: str) -> str:
    """Stop a running MCP server.

    Shells out to ``pharos stop <server_id>`` to gracefully stop a server
    process managed by the Pharos daemon.

    Args:
        server_id: The server ID to stop

    Returns:
        JSON with stop status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("stop", server_id)


@mcp.tool()
async def pharos_daemon_start() -> str:
    """Start the Pharos daemon.

    Shells out to ``pharos daemon start``. The daemon manages local MCP server
    processes, auto-unload for idle servers, and hot-reload of configurations.

    Returns:
        JSON with start status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("daemon", "start")


@mcp.tool()
async def pharos_daemon_stop() -> str:
    """Stop the Pharos daemon.

    Shells out to ``pharos daemon stop``. Stopping the daemon does not remove
    installed servers but will stop any daemon-managed server processes.

    Returns:
        JSON with stop status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("daemon", "stop")


@mcp.tool()
async def pharos_daemon_restart() -> str:
    """Restart the Pharos daemon.

    Shells out to ``pharos daemon restart``. Useful for picking up configuration
    changes or recovering from a wedged state without a full stop/start cycle.

    Returns:
        JSON with restart status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("daemon", "restart")


@mcp.tool()
async def pharos_daemon_log() -> str:
    """Get the Pharos daemon log output.

    Shells out to ``pharos daemon log`` to retrieve recent daemon log lines.
    Useful for debugging daemon or server lifecycle issues.

    Returns:
        JSON with the log text in stdout, plus status and stderr.
    """
    return await _run_pharos_cli("daemon", "log")


@mcp.tool()
async def pharos_daemon_autostart() -> str:
    """Enable Pharos daemon autostart on system boot.

    Shells out to ``pharos daemon autostart`` to configure the daemon to launch
    automatically when the system starts.

    Returns:
        JSON with autostart configuration status, stdout, and stderr.
    """
    return await _run_pharos_cli("daemon", "autostart")


@mcp.tool()
async def pharos_unpublish(server_id: str) -> str:
    """Unpublish a server from the PHAROS registry.

    Shells out to ``pharos unpublish <server_id>`` to remove a published server
    card from the registry. The server remains installed locally but will no
    longer be discoverable by other users.

    Args:
        server_id: The server ID to unpublish from the registry

    Returns:
        JSON with unpublish status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("unpublish", server_id)


@mcp.tool()
async def pharos_health() -> str:
    """Check the health of the PHAROS registry.

    Shells out to ``pharos health`` to verify that the registry endpoint is
    reachable and responding. Useful as a connectivity preflight check.

    Returns:
        JSON with health status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("health")


@mcp.tool()
async def pharos_doctor() -> str:
    """Run diagnostics on the local Pharos installation.

    Shells out to ``pharos doctor`` which checks the CLI version, daemon status,
    registry connectivity, and installed server integrity. Use this when
    something is not working to get a diagnostic report.

    Returns:
        JSON with diagnostic output in stdout, plus status and stderr.
    """
    return await _run_pharos_cli("doctor")


@mcp.tool()
async def pharos_whoami() -> str:
    """Show the currently authenticated PHAROS user.

    Shells out to ``pharos whoami`` to display the authenticated publisher
    identity. Use this to verify login status before publishing or unpublishing.

    Returns:
        JSON with user identity info in stdout, plus status and stderr.
    """
    return await _run_pharos_cli("whoami")


@mcp.tool()
async def pharos_version() -> str:
    """Show the installed Pharos CLI version.

    Shells out to ``pharos version`` to retrieve the CLI binary version string.
    Useful for debugging and compatibility checks.

    Returns:
        JSON with the version string in stdout, plus status and stderr.
    """
    return await _run_pharos_cli("version")


@mcp.tool()
async def pharos_audit() -> str:
    """Run a security audit on installed MCP servers.

    Shells out to ``pharos audit`` to scan installed servers for known
    vulnerabilities, outdated versions, and suspicious permissions. Run this
    periodically or after installing new servers.

    Returns:
        JSON with audit results in stdout, plus status and stderr.
    """
    return await _run_pharos_cli("audit")


@mcp.tool()
async def pharos_lock() -> str:
    """Lock MCP server dependencies to their current versions.

    Shells out to ``pharos lock`` to generate or update a lockfile that pins
    installed server versions. Prevents unexpected upgrades from breaking
    integrations.

    Returns:
        JSON with lock status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("lock")


@mcp.tool()
async def pharos_update(server_id: str) -> str:
    """Update an installed MCP server to its latest version.

    Shells out to ``pharos update <server_id>`` to download and install the
    latest published version of the specified server.

    Args:
        server_id: The server ID to update

    Returns:
        JSON with update status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("update", server_id)


@mcp.tool()
async def pharos_purge(server_id: str) -> str:
    """Purge a server and all its configuration from the local machine.

    Shells out to ``pharos purge <server_id>`` to remove the server binary,
    configuration files, and cached data. This is more thorough than
    ``pharos_remove`` and cannot be undone.

    Args:
        server_id: The server ID to purge

    Returns:
        JSON with purge status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("purge", server_id)


@mcp.tool()
async def pharos_import() -> str:
    """Import Pharos configuration from stdin.

    Shells out to ``pharos import`` to read a JSON configuration blob from
    stdin and merge it into the local Pharos config. Useful for restoring
    backups or sharing server configurations between machines.

    Returns:
        JSON with import status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("import")


@mcp.tool()
async def pharos_config(key: str, value: str = "") -> str:
    """Get or set a Pharos configuration value.

    Shells out to ``pharos config <key> [value]``. When only a key is provided,
    returns the current value. When both key and value are provided, sets the
    configuration value.

    Args:
        key: The configuration key to get or set
        value: The value to set. If empty (default), the current value is returned.

    Returns:
        JSON with the config value (on get) or set confirmation, plus status
        and stderr.
    """
    if value:
        return await _run_pharos_cli("config", key, value)
    return await _run_pharos_cli("config", key)


@mcp.tool()
async def pharos_configure(server_id: str) -> str:
    """Configure OAuth credentials for an MCP server.

    Shells out to ``pharos configure <server_id>`` to set up or update the
    OAuth client credentials needed to connect to servers requiring
    authentication.

    Args:
        server_id: The server ID to configure OAuth for

    Returns:
        JSON with configuration status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("configure", server_id)


@mcp.tool()
async def pharos_add_client(client_id: str) -> str:
    """Add an MCP client configuration to Pharos.

    Shells out to ``pharos add-client <client_id>`` to register a new MCP
    client (e.g. Claude Desktop, VS Code) with the Pharos daemon so it knows
    which clients to serve.

    Args:
        client_id: The client identifier to register

    Returns:
        JSON with add status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("add-client", client_id)


@mcp.tool()
async def pharos_remove_client(client_id: str) -> str:
    """Remove an MCP client configuration from Pharos.

    Shells out to ``pharos remove-client <client_id>`` to deregister an MCP
    client. The client will no longer be able to connect to daemon-managed
    servers.

    Args:
        client_id: The client identifier to remove

    Returns:
        JSON with removal status, stdout, and stderr from the CLI.
    """
    return await _run_pharos_cli("remove-client", client_id)


@mcp.tool()
async def pharos_list_clients() -> str:
    """List all configured MCP clients.

    Shells out to ``pharos list-clients`` to show all MCP clients registered
    with the Pharos daemon and their connection status.

    Returns:
        JSON with the client list in stdout, plus status and stderr.
    """
    return await _run_pharos_cli("list-clients")


# ─── MCP Resources (MCP Apps UI) ──────────────────────────────────────────────

MCP_APP_MIME = "text/html;profile=mcp-app"


# Neutral page for the static ui://pharos/approval URI when no pending
# install exists. Hosts may fetch this URI on install *errors* because of
# a leftover static decorator; it must not look like an approval card.
_EMPTY_APPROVAL_HTML = (
    "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">"
    "<title>PHAROS</title></head><body></body></html>"
)


def approval_resource(token: str | None = None) -> str:
    """Approval UI card (MCP Apps). *token* selects a specific install card.

    Empty cache / missing server payload renders a neutral blank page so
    hosts that still fetch the static ``ui://pharos/approval`` URI on
    install errors do not show a fake approval shell.
    """
    key = token or _current_approval_token
    data = _approval_data_cache.get(key, {}) if key else {}
    if not isinstance(data, dict) or not data.get("server"):
        return _EMPTY_APPROVAL_HTML
    return _inject_template(APPROVAL_APPS_TEMPLATE, data)


@mcp.resource("ui://pharos/approval", mime_type=MCP_APP_MIME)
def approval_resource_static() -> str:
    """Legacy static approval URI (most recent card)."""
    return approval_resource()


@mcp.resource("ui://pharos/approval/{token}", mime_type=MCP_APP_MIME)
def approval_resource_by_token(token: str) -> str:
    """Per-install approval card."""
    return approval_resource(token)


@mcp.resource("ui://pharos/oauth", mime_type=MCP_APP_MIME)
def oauth_resource() -> str:
    """OAuth consent UI (MCP Apps). Rendered during OAuth flows."""
    return OAUTH_HTML


def results_resource(token: str | None = None) -> str:
    """Search results gallery UI (MCP Apps)."""
    key = token or _current_search_id
    results = _search_results_cache.get(key, []) if key else []
    return _inject_template(RESULTS_APPS_TEMPLATE, {"results": results})


@mcp.resource("ui://pharos/results", mime_type=MCP_APP_MIME)
def results_resource_static() -> str:
    return results_resource()


@mcp.resource("ui://pharos/results/{token}", mime_type=MCP_APP_MIME)
def results_resource_by_token(token: str) -> str:
    return results_resource(token)


def info_resource(token: str | None = None) -> str:
    """Server info card UI (MCP Apps)."""
    key = token or _current_info_id
    data = _info_data_cache.get(key, {}) if key else {}
    return _inject_template(INFO_APPS_TEMPLATE, data)


@mcp.resource("ui://pharos/info", mime_type=MCP_APP_MIME)
def info_resource_static() -> str:
    return info_resource()


@mcp.resource("ui://pharos/info/{token}", mime_type=MCP_APP_MIME)
def info_resource_by_token(token: str) -> str:
    return info_resource(token)


def removal_resource(token: str | None = None) -> str:
    """Removal confirmation UI (MCP Apps)."""
    key = token or _current_removal_token
    data = _removal_data_cache.get(key, {}) if key else {}
    return _inject_template(REMOVAL_APPS_TEMPLATE, data)


@mcp.resource("ui://pharos/removal", mime_type=MCP_APP_MIME)
def removal_resource_static() -> str:
    return removal_resource()


@mcp.resource("ui://pharos/removal/{token}", mime_type=MCP_APP_MIME)
def removal_resource_by_token(token: str) -> str:
    return removal_resource(token)


def installed_resource(token: str | None = None) -> str:
    """Installed servers table UI (MCP Apps)."""
    key = token or _current_installed_key
    servers = _installed_data_cache.get(key, []) if key else []
    return _inject_template(INSTALLED_APPS_TEMPLATE, {"servers": servers})


@mcp.resource("ui://pharos/installed", mime_type=MCP_APP_MIME)
def installed_resource_static() -> str:
    return installed_resource()


@mcp.resource("ui://pharos/installed/{token}", mime_type=MCP_APP_MIME)
def installed_resource_by_token(token: str) -> str:
    return installed_resource(token)


def publish_resource(token: str | None = None) -> str:
    """Publish confirmation UI (MCP Apps)."""
    key = token or _current_publish_token
    data = _publish_data_cache.get(key, {}) if key else {}
    return _inject_template(PUBLISH_APPS_TEMPLATE, data)


@mcp.resource("ui://pharos/publish", mime_type=MCP_APP_MIME)
def publish_resource_static() -> str:
    return publish_resource()


@mcp.resource("ui://pharos/publish/{token}", mime_type=MCP_APP_MIME)
def publish_resource_by_token(token: str) -> str:
    return publish_resource(token)


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
        # MCPConnection.list_tools() returns a JSON-RPC response dict:
        #   {"result": {"tools": [{name, description, inputSchema}, ...]}}
        # Unwrap the result envelope to get the tools list.
        if isinstance(result, dict):
            result_body = result.get("result", result)
            raw_tools = result_body.get("tools", []) if isinstance(result_body, dict) else []
        else:
            raw_tools = getattr(result, "tools", [])

        tools = []
        for tool in raw_tools:
            if isinstance(tool, dict):
                tools.append({
                    "name": tool.get("name", ""),
                    "description": tool.get("description") or "",
                    "input_schema": tool.get("inputSchema", tool.get("input_schema", {})),
                })
            else:
                tools.append({
                    "name": getattr(tool, "name", ""),
                    "description": getattr(tool, "description", "") or "",
                    "input_schema": getattr(tool, "inputSchema", {}),
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
                           "pharos-mcp:*", "pharos-mcp-noapps:*", "host.docker.internal:*"],
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*",
                             "http://pharos-mcp:*", "http://pharos-mcp-noapps:*",
                             "http://host.docker.internal:*"],
        )

    # mypy/pyright: transport is str but run() wants a Literal; cast at runtime
    mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
