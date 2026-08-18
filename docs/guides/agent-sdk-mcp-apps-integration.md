# Agent SDK / MCP Apps host integration

**STATUS: draft until T6 E2E is green**

Audience: ChatGPT, Gemini, Claude, and other agent-host teams that want PHAROS
discovery inside an end-user chat product.

LibreChat (nazq fork, `librechat-mcp-apps`) is the **reference host**, not the
product. Copy the protocol and security invariants. Do not treat LibreChat
branding, routes, or Docker layout as required.

PHAROS indexes MCP servers **synced from mcp.io, mcp.so, and Smithery**. It is
**not** an official catalog of those sites.

---

## 1. What you are integrating

PHAROS Discovery is an MCP server (`pharos_discovery.mcp_server`) that wraps
the discovery SDK. Hosts talk to it over MCP (stdio, SSE, or streamable-http).

Two flags, never the same thing:

| Env | Meaning | Surfaces |
|---|---|---|
| `PHAROS_MCP_APPS=true\|1\|yes` | **UI.** Register `_apps` tools + `ui://pharos/...` HTML. Physical click for consent. | Chat hosts with iframes |
| `PHAROS_REMOTE_ONLY=true\|1\|yes` | **Capability.** Kind 1 only (publisher URL). Reject kinds 2 and 3. Search requires an endpoint. | Mobile / no-local-process |

CLI and MCP-without-Apps have **no iframe**. Do not set `PHAROS_MCP_APPS` there.

There is **no** `pharos_connect` or `pharos_approve` MCP tool. Consent is
`POST /approve` (FastMCP `@mcp.custom_route`, invisible in `tools/list`).

---

## 2. What we changed on nazq (reference host)

These are the LibreChat nazq patches that made MCP Apps + PHAROS work. Port
the *behavior*, not the file paths.

### 2.1 Per-call UI URI — `extractPerCallResourceUri`

File: `librechat-mcp-apps/packages/api/src/mcp/parsers.ts`

FastMCP tool definitions advertise a **static** `_meta.ui.resourceUri`
(e.g. `ui://pharos/approval`). If the host keys iframe fetches on that
string, every later install/search shows the first card.

PHAROS `_apps` tools put a unique URI in the **compact JSON** result:

```json
{
  "status": "pending_approval",
  "approval_token": "…",
  "ui_resource_uri": "ui://pharos/approval/{token}",
  "server_id": "com.example/server"
}
```

nazq `extractPerCallResourceUri(result)`:

1. Scan `result.content[]` text items. If a item is JSON with
   `ui_resource_uri` starting with `ui://`, use it.
2. Else use `result._meta.ui.resourceUri` if it is a `ui://` string.
3. Else keep the static tool-definition URI.

`formatToolContent` then sets the MCP App artifact:

```
resourceUri: extractPerCallResourceUri(result) ?? toolUiMeta.resourceUri
```

Tests live in `packages/api/src/mcp/__tests__/parsers.test.ts`.

**Host requirement:** after every `_apps` tool call, re-`resources/read` the
**per-call** URI. Do not cache HTML under the static `ui://pharos/approval`
key.

### 2.2 Dockerfile must run `npm run build:api`

File: `librechat-mcp-apps/Dockerfile`

```dockerfile
RUN \
    npm run build:api; \
    NODE_OPTIONS="--max-old-space-size=${NODE_MAX_OLD_SPACE_SIZE}" npm run frontend; \
    …
```

`packages/api` is a rollup bundle. A image that only builds the React client
ships the **old** parser and ignores `ui_resource_uri`. If your host compiles
API packages separately, include that step in the production image.

### 2.3 Iframe `postMessage` JSON-RPC → host proxy → `POST /approve`

Sandboxed iframes **cannot** `fetch()` the MCP server’s `/approve` route
(opaque origin, no `allow-same-origin`). The card posts to the parent; the
host proxies.

**Iframe → parent** (JSON-RPC 2.0 `postMessage`):

```json
{
  "jsonrpc": "2.0",
  "id": 123456,
  "method": "ui/approve",
  "params": {
    "approval_token": "<token from card DATA>",
    "approval_nonce": "<nonce from card DATA, never from the model>"
  }
}
```

Deny uses `method: "ui/deny"` with the same params.

nazq wiring:

| Layer | Role |
|---|---|
| `MCPAppBridge.ts` | Accept `ui/approve` / `ui/deny` from the iframe only (`event.source === iframe.contentWindow`) |
| `MCPAppContainer.tsx` | `POST /api/mcp/approve` or `/api/mcp/deny` with `{ serverName, approval_token, approval_nonce }` |
| `api/server/routes/mcp.js` | Auth + MCP-use permission, then proxy |

Proxy URL rule (nazq): take the MCP server URL, strip a trailing `/mcp`,
append `/approve` (or `/deny`):

```
http://pharos-mcp:8766/mcp  →  http://pharos-mcp:8766/approve
```

Upstream body is only `{ approval_token, approval_nonce }`.

Your host may use different internal routes. The **MCP-server** contract is
`POST /approve` and `POST /deny` on the same HTTP origin as the streamable
MCP endpoint.

### 2.4 Sandbox: scripts, not same-origin

nazq iframe:

```html
sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"
```

**Do not** add `allow-same-origin`. Without it the iframe has an opaque
origin and cannot read host cookies or call `/approve` itself. That is the
point: consent must go through the host, which already authenticated the
user.

`allow-popups` is only for `ui/open-link` (http/https, new tab,
`noopener,noreferrer`). It is not required for Approve/Deny.

---

## 3. Compact tool JSON vs HTML on `ui://pharos/...`

Inlining HTML in tool results overflowed host context windows. Split:

| Channel | Who sees it | Contents |
|---|---|---|
| Tool result (text/JSON) | Model + host | Compact status, ids, `ui_resource_uri`. **No** `approval_nonce`. **No** HTML. |
| MCP resource | Host iframe only | `text/html;profile=mcp-app`. Nonce is injected into card `DATA`. |

### 3.1 Tool → static `_meta` → per-call URI

Apps tools declare a **static** URI on the tool:

```
@mcp.tool(meta={"ui": {"resourceUri": "ui://pharos/results"}})
```

Each call returns `ui_resource_uri: "ui://pharos/{kind}/{token}"`.

Host sequence:

1. `tools/list` — see `_meta.ui.resourceUri`.
2. Call the `_apps` tool.
3. Parse compact JSON; prefer `ui_resource_uri`.
4. `resources/read` that URI.
5. Render HTML in the sandboxed iframe.

### 3.2 Resources PHAROS actually serves

MIME for all: `text/html;profile=mcp-app`.

| Static URI (tool `_meta`) | Per-call URI | Tool |
|---|---|---|
| `ui://pharos/results` | `ui://pharos/results/{token}` | `pharos_search_apps` |
| `ui://pharos/info` | `ui://pharos/info/{token}` | `pharos_info_apps` |
| `ui://pharos/approval` | `ui://pharos/approval/{token}` | `pharos_install_apps` |
| `ui://pharos/removal` | `ui://pharos/removal/{token}` | `pharos_remove_apps` |
| `ui://pharos/installed` | `ui://pharos/installed/{token}` | `pharos_list_apps` |
| `ui://pharos/publish` | `ui://pharos/publish/{token}` | `pharos_publish_apps` |
| `ui://pharos/oauth` | (static) | OAuth-requiring servers |

Static URIs still exist (legacy hosts). A fetch of `ui://pharos/approval`
with no pending card returns a **blank** page so install *errors* do not
look like a consent shell.

### 3.3 Apps-mode tools (model-visible)

Registered only when `PHAROS_MCP_APPS` is `true` / `1` / `yes`:

| Tool | Compact JSON (typical) | Next step |
|---|---|---|
| `pharos_search_apps` | `status`, `results` (`transport`, `source_registry`), `ui_resource_uri`, optional `nextCursor`/`total` | Same filters as CLI: `transport`, `registry`, `page` (1-based → cursor). User clicks a card or model calls install |
| `pharos_info_apps` | `server` summary + `ui_resource_uri` | Optional |
| `pharos_install_apps` | `status: pending_approval`, `approval_token`, `ui_resource_uri` | Model polls `pharos_check_approval` |
| `pharos_check_approval` | `pending` / `installed` / `denied` / `timeout` / `error` | After install |
| `pharos_remove_apps` | pending removal + `ui_resource_uri` | Physical Remove click |
| `pharos_list_apps` | list + `ui_resource_uri` | — |
| `pharos_publish_apps` | pending publish + `ui_resource_uri` | Physical Publish click |

Non-A/B tools (`pharos_list_tools`, `pharos_call_tool`, daemon helpers, …)
register in **both** modes.

### 3.4 Example: install then poll

```json
// pharos_install_apps result (model-visible)
{
  "status": "pending_approval",
  "approval_token": "<token>",
  "ui_resource_uri": "ui://pharos/approval/<token>",
  "server_id": "com.invokera/world-time",
  "server_name": "World Time",
  "message": "Approval card rendered … Call pharos_check_approval …"
}
```

`pharos_check_approval(approval_token, wait_seconds=25)` blocks up to 30s
and returns `pending` if the user has not clicked. Call again.

`POST /approve` body (host → PHAROS, **not** a tool):

```json
{ "approval_token": "<token>", "approval_nonce": "<uuid4 from HTML DATA>" }
```

Nonce is UUID4 (122-bit), 5-minute pending window (see
`PHAROS_APPROVAL_TIMEOUT`, default 120s for the poll tracker). It is
injected into HTML only.

---

## 4. Physical click vs CLI (no iframe)

### End-user chat hosts (ChatGPT / Gemini / Claude products)

- Set `PHAROS_MCP_APPS=true`.
- Set `PHAROS_REQUIRE_PHYSICAL_APPROVAL=true` (nazq compose does).
- Approve/Deny/Remove/Publish **must** be a real pointer event in the
  iframe. The model never receives `approval_nonce` and cannot guess it.
- Do not add an `approve` tool, slash-command, or function that posts
  `/approve` from model output.
- Bridge must ignore `postMessage` that is not from the iframe
  `contentWindow`.

### CLI / IDE / headless MCP (`PHAROS_MCP_APPS` unset)

- Tools are `pharos_search`, `pharos_install`, `pharos_list`, … — JSON
  only, no iframe, no approval card.
- There is nothing to click. Do not wait for `ui/approve`.
- `PHAROS_REQUIRE_PHYSICAL_APPROVAL` defaults to false in this mode.

### JSON-RPC methods the iframe may send

Implemented by nazq `MCPAppBridge` (host should implement the same set if
you render PHAROS cards):

| Method | Type | Action |
|---|---|---|
| `ui/initialize` | request | Handshake. nazq replies `protocolVersion: "2026-01-26"` |
| `ui/approve` | request | Proxy `POST /approve` |
| `ui/deny` | request | Proxy `POST /deny` |
| `tools/call` | request | Optional; iframe-initiated MCP tool call via host |
| `ui/open-link` | request | Open http(s) URL in a new tab |
| `notifications/message` | notification | Log |
| `ui/notifications/size-changed` | notification | Resize iframe |

Host → iframe: `ui/notifications/tool-result` after init.

---

## 5. `PHAROS_MCP_APPS` vs `PHAROS_REMOTE_ONLY`

**Never conflate these.** Apps is “show an iframe.” Remote-only is “this
device cannot start a local process.”

| | `PHAROS_MCP_APPS` unset | `PHAROS_MCP_APPS=true` |
|---|---|---|
| `PHAROS_REMOTE_ONLY` unset | CLI/IDE MCP: kinds 1, 2, 3. No iframe. | Chat host: kinds 1, 2, 3 + iframe + click |
| `PHAROS_REMOTE_ONLY=true` | Mobile/no-local MCP: kind 1 only. No iframe. | Mobile chat: kind 1 only + iframe + click |

Live LibreChat stack: `PHAROS_MCP_APPS=true`, **do not** set
`PHAROS_REMOTE_ONLY`. A throwaway compose with remote-only is for T6 only.

Search rule: a package is installable if it has an endpoint URL **or** a
command **or** a bin **or** (`runtime` in `{npx,uvx,docker,python,binary}`
**and** `package`). Do **not** hide packages that lack an endpoint.
`PHAROS_REMOTE_ONLY` search is the only filter that requires an endpoint.

---

## 6. Three install kinds

Contract: [`docs/INSTALL_KINDS.md`](../INSTALL_KINDS.md) (board copy:
`/mnt/c/Users/chris/Documents/TRON/temp/Pharos/INSTALL_KINDS.md`).

### Classifier

```
if endpoint is http(s):// → Kind 1
else if transport in {http, http-sse, http+sse, sse, streamable-http}
        and (bin or command or runtime+package) → Kind 2
else if transport is stdio (or empty defaulting to stdio)
        and (bin or command or runtime+package) → Kind 3
else → not installable
```

**Tie-break:** endpoint + bin (e.g. test-echo `0.2.5`) is **Kind 1**.

| Kind | Name | Process lives | Install writes |
|---|---|---|---|
| 1 | Remote HTTP/SSE/streamable-http | Publisher URL | Bookmark + client URL. No tarball. |
| 2 | Local HTTP/SSE/streamable-http | We start on this machine | Tarball or launch line; `pharos start`; port/size/memory |
| 3 | Local stdio | Child process | Tarball **or** npx/uvx/docker/python line (no Pharos tarball required) |

### Surfaces

| Surface | Kinds |
|---|---|
| CLI | 1, 2, 3 |
| MCP no-Apps (`PHAROS_MCP_APPS` unset) | 1, 2, 3 |
| MCP Apps (`PHAROS_MCP_APPS=true`) | 1, 2, 3 unless remote-only |
| Mobile (`PHAROS_REMOTE_ONLY=true`) | Kind 1 only |

### List / status (do not lie)

- Kind 1: `connected` if live session, else `registered`. Endpoint shown.
  SIZE / MEMORY / UPTIME / PORT = `—`.
- Kind 2: running/stopped + port/size/memory/uptime.
- Kind 3: idle, or running if the child is up. Do not fake `running` for a
  remote URL.
- Do not hardcode `registered` for every Apps-mode row.

### Reference packages (T6 will freeze)

| Kind | Package |
|---|---|
| 1 | `com.invokera/world-time` |
| 2 | `test-echo-server@0.2.6` (Pharos fixture; pin version) |
| 3 | `io.github.malamutemayhem/openmeteo` (not `j0hanz/filesystem-mcp`) |

`server_id` may be `name@version`. Persist under `~/.pharos/store`.

### MCP no-Apps snippet (draft — T6 will paste a verified block)

```json
{
  "mcpServers": {
    "pharos": {
      "command": "python3",
      "args": ["-m", "pharos_discovery.mcp_server"],
      "env": {
        "PHAROS_REGISTRY_URL": "https://api.getpharos.dev"
      }
    }
  }
}
```

Leave `PHAROS_MCP_APPS` unset. For a remote PHAROS process (LibreChat-style):

```yaml
mcpServers:
  pharos:
    type: streamable-http
    url: "http://pharos-mcp:8766/mcp"
```

with the server started as:

```bash
PHAROS_MCP_TRANSPORT=streamable-http \
PHAROS_MCP_HOST=0.0.0.0 \
PHAROS_MCP_PORT=8766 \
PHAROS_MCP_APPS=true \
PHAROS_REQUIRE_PHYSICAL_APPROVAL=true \
python3 -m pharos_discovery.mcp_server
```

---

## 7. Host checklist (copy/paste)

Use this as an implementation punch list. nazq already does these.

```
MCP Apps host — PHAROS
======================

Catalog / copy
[ ] Describe PHAROS as synced from mcp.io / mcp.so / Smithery. Never "official".

Flags
[ ] PHAROS_MCP_APPS=true on iframe chat hosts only.
[ ] PHAROS_REMOTE_ONLY=true only on mobile / no-local-process. Kind 1 only.
[ ] Do not set REMOTE_ONLY because you enabled Apps.
[ ] PHAROS_REQUIRE_PHYSICAL_APPROVAL=true on end-user chat.
[ ] Search does not require an endpoint unless REMOTE_ONLY.

Tool / resource split
[ ] Honor tools/list _meta.ui.resourceUri (io.modelcontextprotocol/ui).
[ ] Prefer per-call ui_resource_uri in tool JSON, then result _meta.ui.resourceUri.
[ ] resources/read that ui:// URI (MIME text/html;profile=mcp-app).
[ ] Do not put HTML in the model context; keep tool JSON compact.
[ ] Never forward approval_nonce (or HTML DATA) to the model.

Iframe
[ ] sandbox includes allow-scripts.
[ ] sandbox does NOT include allow-same-origin.
[ ] JSON-RPC 2.0 postMessage: ui/initialize, ui/approve, ui/deny.
[ ] Accept messages only from the iframe contentWindow.
[ ] Approve is a physical click. No model-callable approve tool.

Host proxy
[ ] ui/approve → POST {mcpOrigin}/approve  {approval_token, approval_nonce}
[ ] ui/deny    → POST {mcpOrigin}/deny     {approval_token, approval_nonce}
[ ] If MCP URL ends in /mcp, strip it before appending /approve.
[ ] After pharos_install_apps → pending_approval, poll pharos_check_approval.

Kinds
[ ] Kind 1: bookmark publisher URL. No tarball. Status registered/connected.
[ ] Kind 2: local HTTP we start. pharos start. Real port/size/memory.
[ ] Kind 3: stdio child. Tarball or npx/uvx/docker/python. No fake running.
[ ] endpoint+bin is Kind 1 (test-echo 0.2.5). Pin 0.2.4 for Kind 2 tests.

Build (if you fork nazq or compile packages/api)
[ ] Production image runs `npm run build:api` so extractPerCallResourceUri ships.

CLI / IDE
[ ] PHAROS_MCP_APPS unset. No iframe. No postMessage. JSON tools only.
```

---

## 8. What this draft does not freeze

T6 owns `docs/e2e-kind-matrix.md`. Until that is green:

- Treat resource URIs, `/approve` body, and kind classifier as **current
  code**, not a published SDK guarantee.
- Do not edit `SPEC.md` from this track (T0 / TRON).
- nazq remains a reference implementation, not a required dependency.

After T6: remove the draft banner, paste the verified no-Apps config
snippet, and list any protocol diffs found in E2E.
