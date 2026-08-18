# PHAROS install kinds (contract)

See also `/mnt/c/Users/chris/Documents/TRON/temp/Pharos/INSTALL_KINDS.md` (overnight board copy).

Shared by CLI (Go) and MCP (Python). Fixtures F1–F7 must match in both test suites.

## Classifier

```
if endpoint is http(s):// → Kind 1
else if transport in {http, http-sse, http+sse, sse, streamable-http}
        and (bin or command or runtime+package) → Kind 2
else if transport is stdio (or empty defaulting to stdio)
        and (bin or command or runtime+package) → Kind 3
else → not installable
```

**Tie-break:** endpoint + bin (test-echo 0.2.5) is **Kind 1**.

## Kinds

| Kind | Name | Process lives | Install writes |
|---|---|---|---|
| 1 | Remote HTTP/SSE/streamable-http | Publisher URL | Bookmark + client URL. No tarball. |
| 2 | Local HTTP/SSE/streamable-http | We start on this machine | Tarball or launch line; spawn uses **`bin`**. Clients get `http://127.0.0.1:<port>`, type **http** unless transport is exactly `sse`. If `python` missing and `python3` exists, start uses python3 (same args). Desktop skip. |
| 3 | Local stdio | Child process | Tarball **or** npx/uvx/docker/python line (no Pharos tarball required) |

## Surfaces

| | Support |
|---|---|
| CLI | 1, 2, 3 |
| MCP no-Apps (`PHAROS_MCP_APPS` unset) | 1, 2, 3 |
| MCP Apps (`PHAROS_MCP_APPS=true`) | 1, 2, 3 unless remote-only |
| Mobile (`PHAROS_REMOTE_ONLY=true`) | Kind 1 only |

`PHAROS_MCP_APPS` is UI (iframe + physical click). `PHAROS_REMOTE_ONLY` is capability. Never the same flag.

## Search

Installable if endpoint URL **OR** command **OR** bin **OR** (runtime in {npx,uvx,docker,python,binary} AND package).
Do **not** hide packages only because they lack an endpoint.

## Fixtures

| id | fixture | kind |
|---|---|---|
| F1 | streamable-http + endpoint, no bin | 1 |
| F2 | http-sse + endpoint + bin (0.2.5) | 1 |
| F3 | http-sse + bin, no endpoint (0.2.6) | 2 |
| F4 | stdio + native tarball | 3 |
| F5 | stdio + `npx …` / runtime+package, no tarball | 3 |
| F6 | transport only, no launch data | not installable |
| F7 | REMOTE_ONLY + F3 or F4 | rejected |

## List / status

- Kind 1: `connected` if live session, else `registered`. Endpoint shown. SIZE/MEMORY/UPTIME/PORT = `—`.
- Kind 2: CLI running/stopped + port/size/memory/uptime.
- Kind 3: CLI idle unless a child is up.
- Never hardcode `registered` for every Apps-mode row.

## Env

- `PHAROS_REMOTE_ONLY=true|1|yes`
- MCP `server_id` may be `name@version`
- Persist: `~/.pharos/store`
