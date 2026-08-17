# Pharos Discovery SDK — Python

Discovery SDK for MCP servers — search, approve, connect.

## Installation

```bash
cd packages/python

# Create a virtual environment (required on Ubuntu 24+ / Debian 12+ / PEP 668)
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .
```

## Quick start

```python
from pharos_discovery import ApprovalEngine, RiskLevel

# Review an install plan before running it
engine = ApprovalEngine()
review = engine.review(plan)
print(review.risk)   # RiskLevel.LOW | MEDIUM | HIGH
```

## MCP Tools

The PHAROS MCP server (`pharos_discovery.mcp_server.server`) exposes MCP tools
for discovering, installing, and managing MCP servers. It supports two operating
modes selected at startup by the `PHAROS_MCP_APPS` environment variable:

- **CLI mode** (default) — direct JSON tools, no iframe, no approval UI
- **Apps mode** — `_apps` variants that return HTML for sandboxed iframe
  rendering with visual approval flows

Non-A/B tools (daemon, list_tools, call_tool, etc.) are registered
unconditionally in both modes.

### CLI Mode (default — `PHAROS_MCP_APPS` unset or false)

| Tool | Description |
|------|-------------|
| `pharos_search` | Search the registry for MCP servers |
| `pharos_info` | Get detailed server info |
| `pharos_install` | Install an MCP server |
| `pharos_remove` | Remove an installed server |
| `pharos_list` | List installed servers |
| `pharos_publish` | Publish a server card to the registry |
| `pharos_start` | Start a server |
| `pharos_stop` | Stop a server |
| `pharos_daemon_start` | Start the Pharos daemon |
| `pharos_daemon_stop` | Stop the Pharos daemon |
| `pharos_daemon_restart` | Restart the Pharos daemon |
| `pharos_daemon_status` | Check daemon status |
| `pharos_daemon_log` | Get daemon log output |
| `pharos_daemon_autostart` | Enable daemon autostart on boot |
| `pharos_list_tools` | List tools on a connected server |
| `pharos_call_tool` | Call a tool on a connected server |
| `pharos_unpublish` | Unpublish from registry |
| `pharos_health` | Check registry health |
| `pharos_doctor` | Run diagnostics |
| `pharos_whoami` | Show authenticated user |
| `pharos_version` | Show CLI version |
| `pharos_audit` | Security audit |
| `pharos_lock` | Lock dependencies |
| `pharos_update` | Update a server |
| `pharos_purge` | Purge a server |
| `pharos_import` | Import config |
| `pharos_config` | Get/set config |
| `pharos_configure` | Configure OAuth |
| `pharos_add_client` | Add an MCP client |
| `pharos_remove_client` | Remove an MCP client |
| `pharos_list_clients` | List configured MCP clients |

### Apps Mode (`PHAROS_MCP_APPS=true`)

The 6 `_apps` variants return compact JSON. HTML for the sandboxed iframe is served from MCP resources (`ui://pharos/results/{token}`, `ui://pharos/approval/{token}`, etc.) — not inlined in the tool result (that overflowed host context windows).

Approve is **not** an MCP tool. `POST /approve` is a custom HTTP route. In LibreChat the iframe is sandboxed (`allow-scripts`, no same-origin) so the button `postMessage`s the host, which proxies the approve/deny call. The model never sees `approval_nonce`.

| Tool | Description |
|------|-------------|
| `pharos_search_apps` | Search results as interactive iframe gallery |
| `pharos_info_apps` | Server details card in iframe |
| `pharos_install_apps` | Install with visual approval flow (replaces `pharos_connect`) |
| `pharos_remove_apps` | Remove with visual confirmation |
| `pharos_list_apps` | Installed servers table in iframe |
| `pharos_publish_apps` | Publish with confirmation card |

Plus the same non-A/B tools as CLI mode (daemon, `list_tools`, `call_tool`,
etc.).

### Approval Endpoint

`POST /approve` is a custom HTTP route, **not** an MCP tool — it is invisible
to AI agents and does not appear in `tools/list`.

- Called by the iframe Approve button via `fetch()`
- Requires `approval_token` + `approval_nonce` in the JSON body
- The `approval_nonce` is a UUID4 (122-bit entropy) with a 5-minute validity
  window
- The nonce is injected into the HTML card data only; it is never returned
  in the AI-visible JSON response, preventing the AI from bypassing physical
  approval

### Environment Variables

| Variable | Description |
|----------|-------------|
| `PHAROS_MCP_APPS` | Set to `true`, `1`, or `yes` to enable Apps mode |
| `PHAROS_MCP_TRANSPORT` | `stdio` (default), `http+sse`, or `streamable-http` |
| `PHAROS_MCP_HOST` | Host for HTTP transports (default `0.0.0.0`) |
| `PHAROS_MCP_PORT` | Port for HTTP transports (default `8766`) |

## Optional: embeddings

```bash
pip install -e ".[embeddings]"
```

See the root [README](../../README.md) for full project details.
