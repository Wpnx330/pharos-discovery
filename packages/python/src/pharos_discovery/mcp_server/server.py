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
from pharos_discovery.models import ServerCard, ApprovalRequest, ApprovalResponse, ApprovalToken
from pharos_discovery.connection.manager import ConnectionManager
from pharos_discovery.errors import (
    NoServersFound,
    RegistryUnavailable,
    ApprovalDenied,
    ConnectionFailed,
    HeadlessApprovalRequired,
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
<title>PHAROS Approval Required</title>
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
  <div class="header">
    <div class="header-text">
      <div class="title">Approval Required</div>
      <div class="subtitle" id="subtitle"></div>
    </div>
    <div class="header-actions" id="header-actions"></div>
  </div>
  <div class="approval-card" id="approval-card"></div>
  <div id="status"></div>
<script>
  const DATA = __DATA__;

  function render() {
    const s = DATA.server;
    if (!s) return;
    const card = document.getElementById("approval-card");
    const subtitle = document.getElementById("subtitle");
    const headerActions = document.getElementById("header-actions");
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
  .actions { display: flex; gap: 8px; margin-top: 16px; }
</style>
</head>
<body>
  <div class="header">
    <div class="title">Removal Confirmation</div>
    <div class="subtitle" id="subtitle"></div>
  </div>
  <div class="removal-card" id="removal-card"></div>
  <div id="status"></div>
<script>
  const DATA = __DATA__;

  function render() {
    const card = document.getElementById("removal-card");
    const subtitle = document.getElementById("subtitle");
    subtitle.textContent = "id: " + (DATA.server_id || "unknown");

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
      showStatus("Removal cancelled.", "ok");
    });
    actions.appendChild(cancelBtn);

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
          window.parent.postMessage({
            jsonrpc: "2.0",
            method: "notifications/tool_result",
            params: { approved: true, action: "removed", server_id: DATA.server_id }
          }, "*");
        }
      };
      window.addEventListener("message", removeHandler);
    });
    actions.appendChild(removeBtn);
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
        <th>Source</th>
        <th>Installed</th>
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

    tbody.innerHTML = "";
    servers.forEach((s) => {
      const tr = document.createElement("tr");

      const cellId = document.createElement("td");
      cellId.className = "mono";
      cellId.textContent = s.server_id || s.id || "unknown";
      tr.appendChild(cellId);

      const cellStatus = document.createElement("td");
      const status = (s.status || "unknown").toLowerCase();
      const dot = document.createElement("span");
      dot.className = "status-dot status-" + (status === "running" ? "running" : status === "error" ? "error" : "stopped");
      cellStatus.appendChild(dot);
      cellStatus.appendChild(document.createTextNode(s.status || "unknown"));
      tr.appendChild(cellStatus);

      const cellTr = document.createElement("td");
      cellTr.className = "mono";
      const transport = Array.isArray(s.transport) ? s.transport.join(", ") : (s.transport || "unknown");
      cellTr.textContent = transport;
      tr.appendChild(cellTr);

      const cellSrc = document.createElement("td");
      cellSrc.textContent = s.source || "unknown";
      tr.appendChild(cellSrc);

      const cellDate = document.createElement("td");
      cellDate.className = "mono";
      const installed = s.installed_at || "";
      cellDate.textContent = installed ? installed.substring(0, 19).replace("T", " ") : "N/A";
      tr.appendChild(cellDate);

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
        remote_transports = ("sse", "streamable-http", "http", "http+sse")
        if transport and any(t in remote_transports for t in transport):
            transport_str = ", ".join(transport)
            if not endpoint:
                return json.dumps({
                    "error": f"Server '{server_id}' has transport '{transport_str}' but no endpoint URL",
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
                "message": "Remote server registered.",
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
        results = []

        # 1. Report in-memory remote registrations
        for sid, meta in _installed_servers.items():
            results.append({
                "server_id": sid,
                "transport": meta.get("transport", "unknown"),
                "endpoint": meta.get("endpoint"),
                "installed_at": meta.get("installed_at"),
                "source": "mcp_registered",
            })

        # 2. Query the pharos CLI for locally installed servers
        cli = _get_pharos_cli()
        try:
            proc = await asyncio.create_subprocess_exec(
                cli, "list", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            return json.dumps({"error": "pharos list timed out", "installed": results})
        except FileNotFoundError:
            # CLI not installed — return only in-memory registrations
            return json.dumps({"installed": results, "count": len(results),
                               "note": "pharos CLI not found; only showing MCP-registered servers."})
        except Exception as e:
            return json.dumps({"error": f"pharos list failed: {e}", "installed": results})

        # Parse CLI output
        cli_output = ""
        if proc.returncode == 0 and stdout:
            cli_output = stdout.decode().strip()
            try:
                # Try JSON parse first (--json flag)
                parsed = json.loads(cli_output)
                if isinstance(parsed, list):
                    for entry in parsed:
                        sid = entry.get("name") or entry.get("id") or "unknown"
                        results.append({
                            "server_id": sid,
                            "transport": entry.get("transport", "unknown"),
                            "status": entry.get("status", "unknown"),
                            "source": "cli",
                        })
                elif isinstance(parsed, dict) and "servers" in parsed:
                    for entry in parsed["servers"]:
                        sid = entry.get("name") or entry.get("id") or "unknown"
                        results.append({
                            "server_id": sid,
                            "transport": entry.get("transport", "unknown"),
                            "status": entry.get("status", "unknown"),
                            "source": "cli",
                        })
            except json.JSONDecodeError:
                # CLI may not support --json; parse text output
                for line in cli_output.splitlines():
                    line = line.strip()
                    if line and not line.startswith("NAME") and not line.startswith("-"):
                        parts = line.split()
                        if parts:
                            results.append({
                                "server_id": parts[0],
                                "transport": parts[1] if len(parts) > 1 else "unknown",
                                "status": parts[2] if len(parts) > 2 else "unknown",
                                "source": "cli",
                            })

        return json.dumps({"installed": results, "count": len(results)})


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
    ) -> str:
        """Search the PHAROS registry for MCP servers (Apps mode).

        Renders search results as an interactive iframe table with per-server
        install buttons. The user can browse results and request installation
        visually.

        Args:
            query: Natural-language search query
            limit: Maximum number of results (default 10, max 50)
            remote_only: If True, only return remote-transport servers

        Returns:
            JSON with status, results array, search_id, and an html field
            containing the rendered results table for the iframe.
        """
        global _current_search_id

        client = _get_client()
        limit = min(max(limit, 1), 50)

        filters: dict[str, Any] = {}
        if remote_only:
            filters["transport"] = ["sse", "streamable-http", "http"]

        try:
            results = await client.search(text=query, filters=filters or None, limit=limit)
        except NoServersFound:
            search_id = f"sr-{int(time.time())}-{os.urandom(4).hex()}"
            _search_results_cache[search_id] = []
            _current_search_id = search_id
            no_results_data = {"query": query, "results": [], "error": None}
            safe_json = json.dumps(no_results_data).replace("<", "\\u003c").replace(">", "\\u003e")
            html = SEARCH_APPS_TEMPLATE.replace("__DATA__", safe_json)
            return json.dumps({
                "status": "no_results",
                "results": [],
                "count": 0,
                "search_id": search_id,
                "html": html,
            })
        except RegistryUnavailable as e:
            error_html_data = {"query": query, "results": [], "error": str(e)}
            safe_json = json.dumps(error_html_data).replace("<", "\\u003c").replace(">", "\\u003e")
            html = SEARCH_APPS_TEMPLATE.replace("__DATA__", safe_json)
            return json.dumps({
                "status": "error",
                "error": f"Registry unavailable: {e}",
                "results": [],
            })

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
            source_registry = getattr(card, "source_registry", None) or raw.get("source_registry")

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
        search_id = f"sr-{int(time.time())}-{os.urandom(4).hex()}"
        _search_results_cache[search_id] = output
        _current_search_id = search_id
        if len(_search_results_cache) > _MAX_CACHE_SIZE:
            oldest = next(iter(_search_results_cache))
            del _search_results_cache[oldest]

        # Build HTML with data injected
        html_data = {"query": query, "results": output}
        safe_json = json.dumps(html_data).replace("<", "\\u003c").replace(">", "\\u003e")
        html = SEARCH_APPS_TEMPLATE.replace("__DATA__", safe_json)

        return json.dumps({
            "status": "ok",
            "count": len(output),
            "search_id": search_id,
            "results": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "version": r["version"],
                    "transport": r["transport"],
                    "publisher": r["publisher"]["name"],
                    "verified": r["publisher"]["verified"],
                    "tools_count": r["tools_count"],
                    "pricing": r["pricing"],
                }
                for r in output
            ],
        })


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
        # Try cached card first (from a prior search), then fetch from registry
        card = _server_cards.get(server_id)
        if card is None:
            client = _get_client()
            try:
                card = await client.get_server(server_id)
                _server_cards[server_id] = card
            except Exception as e:
                error_html_data = {
                    "server": {"id": server_id, "display_name": server_id},
                    "error": str(e),
                }
                safe_json = json.dumps(error_html_data).replace("<", "\\u003c").replace(">", "\\u003e")
                html = INFO_APPS_TEMPLATE.replace("__DATA__", safe_json)
                return json.dumps({
                    "status": "error",
                    "error": f"Failed to get server info: {e}",
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
        safe_json = json.dumps(html_data).replace("<", "\\u003c").replace(">", "\\u003e")
        html = INFO_APPS_TEMPLATE.replace("__DATA__", safe_json)

        # Cache for UI resource rendering
        _info_data_cache[server_id] = html_data
        global _current_info_id
        _current_info_id = server_id
        if len(_info_data_cache) > _MAX_CACHE_SIZE:
            oldest = next(iter(_info_data_cache))
            del _info_data_cache[oldest]

        return json.dumps({
            "status": "ok",
            "server_id": server_id,
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


    @mcp.tool(meta={"ui": {"resourceUri": "ui://pharos/approval"}})
    async def pharos_install_apps(
        server_id: str,
        purpose: str = "User request",
        ctx: Context = None,
    ) -> str:
        """Install an MCP server with visual approval flow (Apps mode).

        Creates a pending approval connection and renders an approval card in
        the iframe. The tool BLOCKS until the user clicks Approve or Deny,
        or until the approval timeout expires. The approval_nonce is injected
        into the HTML but is NOT included in the JSON response the AI sees.

        Args:
            server_id: The server ID to install
            purpose: Why the installation is being requested (shown to user)

        Returns:
            JSON with status:
            - "pending_approval" → (this is the initial state; tool blocks waiting)
            - "installed" → user approved, server connected, tools available
            - "denied" → user clicked Deny
            - "timeout" → user did not respond within PHAROS_APPROVAL_TIMEOUT seconds
            - "error" → server not found or too many pending connections
        """
        # Get the server card (from cache or registry)
        card = _server_cards.get(server_id)
        if card is None:
            client = _get_client()
            try:
                card = await client.get_server(server_id)
                _server_cards[server_id] = card
            except Exception as e:
                return json.dumps({
                    "status": "error",
                    "error": f"Cannot install: server '{server_id}' not found: {e}",
                    "server_id": server_id,
                })

        # Determine endpoint from the card
        endpoint = getattr(card, "endpoint", None)

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
        safe_json = json.dumps(html_data).replace("<", "\\u003c").replace(">", "\\u003e")
        html = APPROVAL_APPS_TEMPLATE.replace("__DATA__", safe_json)

        # Cache for UI resource rendering
        _approval_data_cache[token] = html_data
        global _current_approval_token
        _current_approval_token = token
        if len(_approval_data_cache) > _MAX_CACHE_SIZE:
            oldest = next(iter(_approval_data_cache))
            del _approval_data_cache[oldest]

        # Create an asyncio.Event so this tool can block until the user approves
        approval_event = asyncio.Event()
        _approval_events[token] = {
            "event": approval_event,
            "result": None,  # Will be set by /approve endpoint: "approved" or "denied"
        }

        # Block until the user clicks Approve/Deny or the timeout expires.
        # The iframe renders the approval card from the resource URI in the
        # tool metadata (_meta.ui.resourceUri). LibreChat fetches that resource
        # HTML immediately when the tool call starts — before the tool returns.
        # So the card is visible while we block here.
        #
        # We poll in a loop (10s intervals) instead of a single wait_for so we
        # can send MCP progress notifications. LibreChat sets
        # resetTimeoutOnProgress: true on the MCP client, so each progress
        # notification resets the 130s client-side timeout. Without this, a
        # long approval wait would cause the MCP client to time out and abort
        # the tool call before the user has a chance to click.
        deadline = time.time() + _PHAROS_APPROVAL_TIMEOUT
        timed_out = False
        while not approval_event.is_set():
            remaining = deadline - time.time()
            if remaining <= 0:
                timed_out = True
                break
            wait_time = min(10, remaining)
            try:
                await asyncio.wait_for(approval_event.wait(), timeout=wait_time)
            except asyncio.TimeoutError:
                # Send a progress notification to reset the MCP client timeout
                if ctx is not None:
                    try:
                        elapsed = _PHAROS_APPROVAL_TIMEOUT - (deadline - time.time())
                        progress_pct = int((elapsed / _PHAROS_APPROVAL_TIMEOUT) * 100)
                        await ctx.report_progress(
                            progress=progress_pct,
                            total=100,
                            message=f"Waiting for user approval ({int(remaining)}s remaining)",
                        )
                    except Exception:
                        pass  # Progress notifications are best-effort
                continue

        if timed_out:
            _approval_events.pop(token, None)
            _pending_connections.pop(token, None)
            return json.dumps({
                "status": "timeout",
                "server_id": server_id,
                "message": f"Approval timed out after {_PHAROS_APPROVAL_TIMEOUT}s. "
                           f"The user did not respond in time. Ask if they want to try again.",
            })

        # Event was set — check the result
        event_data = _approval_events.pop(token, {})
        result = event_data.get("result", "unknown")

        if result == "denied":
            _pending_connections.pop(token, None)
            return json.dumps({
                "status": "denied",
                "server_id": server_id,
                "message": f"User denied installation of {card.display_name}.",
            })
        elif result == "approved":
            # The /approve endpoint already connected and installed the server.
            # Get the tools list to return to the AI.
            tools = await _list_server_tools(server_id)
            return json.dumps({
                "status": "installed",
                "server_id": server_id,
                "tools_count": len(tools),
                "tools": tools,
                "message": f"{card.display_name} approved and installed. "
                           f"{len(tools)} tools available.",
            })
        else:
            _pending_connections.pop(token, None)
            return json.dumps({
                "status": "error",
                "server_id": server_id,
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
        removal_token = f"rm-{server_id}-{int(time.time())}-{os.urandom(4).hex()}"
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
        safe_json = json.dumps(html_data).replace("<", "\\u003c").replace(">", "\\u003e")
        html = REMOVAL_APPS_TEMPLATE.replace("__DATA__", safe_json)

        # Cache for UI resource rendering
        _removal_data_cache[removal_token] = html_data
        global _current_removal_token
        _current_removal_token = removal_token
        if len(_removal_data_cache) > _MAX_CACHE_SIZE:
            oldest = next(iter(_removal_data_cache))
            del _removal_data_cache[oldest]

        # Return JSON to AI — nonce is NOT here
        return json.dumps({
            "status": "pending_removal",
            "removal_token": removal_token,
            "server_id": server_id,
            "message": f"Removal confirmation required for {server_name}. "
                       f"Tell the user to click Remove in the card above.",
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
        results: list[dict] = []

        # 1. Report in-memory remote registrations (same logic as pharos_list)
        for sid, meta in _installed_servers.items():
            results.append({
                "server_id": sid,
                "transport": meta.get("transport", "unknown"),
                "endpoint": meta.get("endpoint"),
                "installed_at": meta.get("installed_at"),
                "source": "mcp_registered",
                "status": "registered",
            })

        # 1b. Report connected servers not already in results
        for sid, conn in _connections.items():
            if not any(r.get("server_id") == sid for r in results):
                results.append({
                    "server_id": sid,
                    "transport": "connected",
                    "endpoint": None,
                    "installed_at": None,
                    "source": "mcp_connected",
                    "status": "connected",
                })

        # 2. Query the pharos CLI for locally installed servers
        cli = _get_pharos_cli()
        try:
            proc = await asyncio.create_subprocess_exec(
                cli, "list", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except Exception:
            # CLI not available or timed out — return only in-memory registrations
            pass
        else:
            cli_output = stdout.decode().strip() if stdout else ""
            if proc.returncode == 0 and cli_output:
                try:
                    parsed = json.loads(cli_output)
                    entries = parsed if isinstance(parsed, list) else parsed.get("servers", [])
                    for entry in entries:
                        sid = entry.get("name") or entry.get("id") or "unknown"
                        results.append({
                            "server_id": sid,
                            "transport": entry.get("transport", "unknown"),
                            "status": entry.get("status", "unknown"),
                            "source": "cli",
                        })
                except json.JSONDecodeError:
                    for line in cli_output.splitlines():
                        line = line.strip()
                        if line and not line.startswith("NAME") and not line.startswith("-"):
                            parts = line.split()
                            if parts:
                                results.append({
                                    "server_id": parts[0],
                                    "transport": parts[1] if len(parts) > 1 else "unknown",
                                    "status": parts[2] if len(parts) > 2 else "unknown",
                                    "source": "cli",
                                })

        # Build HTML with data injected
        html_data = {"servers": results}
        safe_json = json.dumps(html_data).replace("<", "\\u003c").replace(">", "\\u003e")
        html = INSTALLED_APPS_TEMPLATE.replace("__DATA__", safe_json)

        # Cache for UI resource rendering
        installed_key = f"inst-{int(time.time())}-{os.urandom(4).hex()}"
        _installed_data_cache[installed_key] = results
        global _current_installed_key
        _current_installed_key = installed_key
        if len(_installed_data_cache) > _MAX_CACHE_SIZE:
            oldest = next(iter(_installed_data_cache))
            del _installed_data_cache[oldest]

        return json.dumps({
            "status": "ok",
            "count": len(results),
            "servers": [
                {
                    "id": s.get("id", s.get("server_id", "unknown")),
                    "name": s.get("name", s.get("display_name", "unknown")),
                    "version": s.get("version", "unknown"),
                    "transport": s.get("transport", "unknown"),
                    "status": s.get("status", "unknown"),
                }
                for s in results
            ],
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
        publish_token = f"pb-{server_data['id']}-{int(time.time())}-{os.urandom(4).hex()}"
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
        safe_json = json.dumps(html_data).replace("<", "\\u003c").replace(">", "\\u003e")
        html = PUBLISH_APPS_TEMPLATE.replace("__DATA__", safe_json)

        # Cache for UI resource rendering
        _publish_data_cache[publish_token] = html_data
        global _current_publish_token
        _current_publish_token = publish_token
        if len(_publish_data_cache) > _MAX_CACHE_SIZE:
            oldest = next(iter(_publish_data_cache))
            del _publish_data_cache[oldest]

        # Return JSON to AI — nonce is NOT here
        return json.dumps({
            "status": "pending_publish",
            "publish_token": publish_token,
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

    # Clean up the pending token (one-time use)
    del _pending_connections[approval_token]

    # Check if already connected (race condition guard)
    if server_id in _connections:
        return JSONResponse({
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

        # Also register as installed so pharos_list_apps finds it
        _installed_servers[server_id] = {
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "transport": getattr(card, "transport", None),
            "endpoint": endpoint,
            "source": "approved",
        }

        # List initial tools
        tools = await _list_server_tools(server_id)

        # Signal the waiting tool that approval succeeded
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
        return JSONResponse({"error": f"Connection failed: {e}", "server_id": server_id})
    except Exception as e:
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

    # Signal the waiting tool
    if token in _approval_events:
        _approval_events[token]["result"] = "denied"
        _approval_events[token]["event"].set()

    # Clean up
    _pending_connections.pop(token, None)

    return JSONResponse({"status": "denied"})


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


@mcp.resource("ui://pharos/approval", mime_type=MCP_APP_MIME)
def approval_resource() -> str:
    """Approval UI card (MCP Apps). Rendered when user must approve a server connection."""
    # Use token-scoped data if available, fall back to most recent
    token = _current_approval_token
    data = _approval_data_cache.get(token, {}) if token else {}
    # Escape < > to prevent </script> breakout (XSS safe JSON-in-HTML)
    safe_json = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e")
    return APPROVAL_APPS_TEMPLATE.replace("__DATA__", safe_json)


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
    return RESULTS_APPS_TEMPLATE.replace("__DATA__", safe_json)


@mcp.resource("ui://pharos/info", mime_type=MCP_APP_MIME)
def info_resource() -> str:
    """Server info card UI (MCP Apps). Rendered after pharos_info_apps."""
    server_id = _current_info_id
    data = _info_data_cache.get(server_id, {}) if server_id else {}
    safe_json = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e")
    return INFO_APPS_TEMPLATE.replace("__DATA__", safe_json)


@mcp.resource("ui://pharos/removal", mime_type=MCP_APP_MIME)
def removal_resource() -> str:
    """Removal confirmation UI (MCP Apps). Rendered after pharos_remove_apps."""
    token = _current_removal_token
    data = _removal_data_cache.get(token, {}) if token else {}
    safe_json = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e")
    return REMOVAL_APPS_TEMPLATE.replace("__DATA__", safe_json)


@mcp.resource("ui://pharos/installed", mime_type=MCP_APP_MIME)
def installed_resource() -> str:
    """Installed servers table UI (MCP Apps). Rendered after pharos_list_apps."""
    key = _current_installed_key
    servers = _installed_data_cache.get(key, []) if key else []
    data = {"servers": servers}
    safe_json = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e")
    return INSTALLED_APPS_TEMPLATE.replace("__DATA__", safe_json)


@mcp.resource("ui://pharos/publish", mime_type=MCP_APP_MIME)
def publish_resource() -> str:
    """Publish confirmation UI (MCP Apps). Rendered after pharos_publish_apps."""
    token = _current_publish_token
    data = _publish_data_cache.get(token, {}) if token else {}
    safe_json = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e")
    return PUBLISH_APPS_TEMPLATE.replace("__DATA__", safe_json)


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
