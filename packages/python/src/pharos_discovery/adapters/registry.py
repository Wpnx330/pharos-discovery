from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from pharos_discovery.errors import NoServersFound, RegistryUnavailable
from pharos_discovery.models import AuthSpec, Publisher, ServerCard


def _as_command(value: Any) -> str | None:
    """Normalize a registry command / bin field to a single command string."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, list):
        parts = [str(part).strip() for part in value if str(part).strip()]
        return " ".join(parts) if parts else None
    return None


def _encode_server_id(server_id: str) -> str:
    """Percent-encode a server id so spaces and non-ASCII stay one path segment."""
    return quote(str(server_id), safe="-._~")


class SearchResult:
    """Wrapper for a search result item."""

    def __init__(
        self,
        card: ServerCard,
        score: float | None = None,
        raw_item: dict[str, Any] | None = None,
    ):
        self.card = card
        self.score = score
        self.raw_item = raw_item or {}


def _normalize_to_server_card(
    item: dict[str, Any],
    source_registry: str,
) -> ServerCard:
    """Normalize a raw registry item into a :class:`ServerCard`.

    Handles two shapes:
    1. **Pharos-native** — already has ``id`` and ``display_name`` (pass-through).
    2. **Live registry** (getpharos.dev) — has ``name``, ``title``,
       ``capabilities`` as a dict with ``tools``/``auth`` keys, and
       ``publisher`` with a ``namespace`` field.
    """
    # --- Pharos-native shape (existing tests / other registries) -----------
    if "id" in item and "display_name" in item:
        return ServerCard(**item)

    # --- Live registry shape (getpharos.dev /v1/search & /v1/packages) -----
    # Prefer a stable machine id when the registry provides one. Display names
    # (spaces, emojis) belong in title, not in the URL path.
    name = item.get("id") or item.get("name") or "unknown"
    title = item.get("title") or item.get("display_name") or item.get("name") or name
    description = item.get("description") or item.get("summary") or ""
    version = item.get("version") or "0.0.0"

    # Publisher — live registry uses {namespace, verified}
    pub_raw = item.get("publisher") or {}
    _pub_namespace = pub_raw.get("namespace") or pub_raw.get("id") or "unknown"
    _pub_name = pub_raw.get("namespace") or pub_raw.get("name") or "unknown"

    # Map "official-sync" to a user-friendly source label
    _SOURCE_DISPLAY = {
        "modelcontextprotocol.io": "mcp.io",
        "mcp.so": "mcp.so",
        "glama.ai": "glama.ai",
        "smithery": "smithery",
        "pharos": "pharos",
    }
    if _pub_name == "official-sync" and source_registry != "pharos":
        display_source = _SOURCE_DISPLAY.get(source_registry, source_registry)
        _pub_name = f"synced {display_source}"

    publisher = Publisher(
        id=_pub_namespace,
        name=_pub_name,
        verified=pub_raw.get("verified"),
        verification_method=pub_raw.get("verification_method"),
        contact=pub_raw.get("contact"),
    )

    # Capabilities — live registry uses {"tools": bool, "auth": {"type": str}}
    caps_raw = item.get("capabilities")
    if isinstance(caps_raw, dict):
        capabilities: list[str] = []
        if caps_raw.get("tools"):
            capabilities.append("tools")
        if caps_raw.get("resources"):
            capabilities.append("resources")
        if caps_raw.get("prompts"):
            capabilities.append("prompts")
        auth_raw = caps_raw.get("auth") or {}
        auth_type = auth_raw.get("type") if auth_raw.get("type") else "none"
    elif isinstance(caps_raw, list):
        capabilities = [str(c) for c in caps_raw]
        auth_raw = item.get("auth") or {}
        auth_type = auth_raw.get("type", "none") if isinstance(auth_raw, dict) else "none"
    else:
        capabilities = []
        auth_raw = item.get("auth") or {}
        auth_type = auth_raw.get("type", "none") if isinstance(auth_raw, dict) else "none"

    if auth_type not in ("none", "api_key", "oauth", "mtls"):
        auth_type = "none"
    auth = AuthSpec(type=auth_type)  # type: ignore[arg-type]

    # Transport — accept both "http-sse" (registry API format) and
    # "http+sse" (MCP spec format) as equivalent.
    raw_transports = item.get("transport") or item.get("transports") or []
    if isinstance(raw_transports, str):
        raw_transports = [raw_transports]
    _VALID = ("stdio", "http+sse", "http-sse", "streamable-http")
    transports = [t for t in raw_transports if t in _VALID]
    # Normalise http-sse → http+sse for internal consistency.
    transports = ["http+sse" if t == "http-sse" else t for t in transports]
    if not transports:
        transports = ["stdio"]

    # Endpoint / stdio command. Synced catalogs often store the launcher as
    # ``bin`` instead of ``command``.
    endpoint = item.get("endpoint") or item.get("url")
    stdio_command = (
        _as_command(item.get("stdio_command"))
        or _as_command(item.get("command"))
        or _as_command(item.get("bin"))
    )

    # For package-detail responses, extract from latest version's manifest
    versions = item.get("versions")
    if isinstance(versions, list) and versions:
        dist_tags = item.get("dist_tags") or {}
        latest_tag = dist_tags.get("latest")
        latest_entry = None
        if latest_tag:
            latest_entry = next((v for v in versions if v.get("version") == latest_tag), None)
        if latest_entry is None:
            latest_entry = versions[-1]
        manifest = latest_entry.get("manifest") or {}
        if not endpoint:
            endpoint = manifest.get("endpoint")
        if not stdio_command:
            stdio_command = (
                _as_command(manifest.get("command"))
                or _as_command(manifest.get("stdio_command"))
                or _as_command(manifest.get("bin"))
            )
        manifest_caps = manifest.get("capabilities")
        if isinstance(manifest_caps, list):
            capabilities = [str(c) for c in manifest_caps]
        manifest_transport = manifest.get("transport")
        if isinstance(manifest_transport, str):
            # Normalise http-sse → http+sse.
            if manifest_transport == "http-sse":
                manifest_transport = "http+sse"
            if manifest_transport in ("stdio", "http+sse", "streamable-http"):
                transports = [manifest_transport]
        # Use the latest version string if the top-level version was a default.
        if latest_entry.get("version") and version == "0.0.0":
            version = latest_entry["version"]

    # Timestamps
    published_at = item.get("published_at") or item.get("created_at") or ""
    updated_at = item.get("updated_at") or item.get("modified_at") or ""
    if not published_at:
        published_at = "1970-01-01T00:00:00Z"
    if not updated_at:
        updated_at = published_at

    return ServerCard(
        id=name,
        display_name=title,
        description=description,
        publisher=publisher,
        version=version,
        transport=transports,  # type: ignore[arg-type]
        endpoint=endpoint,
        stdio_command=stdio_command,
        capabilities=capabilities,
        tools_count=item.get("tools_count", len(capabilities) if capabilities else 0),
        auth=auth,
        availability="native",
        tags=item.get("tags") or [],
        source_registry=source_registry,
        published_at=published_at,
        updated_at=updated_at,
        status="active",
    )


class PharosRegistryAdapter:
    """HTTP adapter for a Pharos registry instance.

    Handles search queries and server card retrieval with:
    - ETag-based conditional requests
    - Configurable timeouts
    - Proper error mapping
    """

    def __init__(
        self,
        base_url: str,
        search_timeout: float = 10.0,
        get_timeout: float = 10.0,
        api_key: str | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._search_timeout = search_timeout
        self._get_timeout = get_timeout
        self._api_key = api_key
        self._etag_cache: dict[str, str] = {}

    @property
    def base_url(self) -> str:
        return self._base_url

    async def search(
        self,
        text: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        """Search the registry for MCP servers.

        Args:
            text: Full-text search query
            filters: Dict of filter criteria (transport, publisher_verified, etc.)
            limit: Max results (default 20)

        Returns:
            List of SearchResult objects, sorted by relevance

        Raises:
            RegistryUnavailable: On HTTP errors or timeouts
            NoServersFound: When search returns zero results
        """
        params: dict[str, Any] = {"limit": limit}
        if text:
            # Prefer "q" (live registry) but also send "text" for compatibility
            params["q"] = text
            params["text"] = text
        if filters:
            for key, value in filters.items():
                if isinstance(value, list):
                    params[key] = ",".join(str(v) for v in value)
                else:
                    params[key] = value

        headers = self._auth_headers()

        try:
            async with httpx.AsyncClient(timeout=self._search_timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/v1/search",
                    params=params,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise RegistryUnavailable(self._base_url, detail=str(exc)) from exc

        if resp.status_code != 200:
            raise RegistryUnavailable(
                self._base_url,
                status=resp.status_code,
                detail=resp.text,
            )

        data = resp.json()
        items = data.get("results", [])

        if not items:
            raise NoServersFound(text or "")

        results: list[SearchResult] = []
        for item in items:
            card = _normalize_to_server_card(item, self._base_url)
            score = item.get("_score") or item.get("score")
            results.append(SearchResult(card=card, score=score, raw_item=item))

        return results

    async def get_server_card(
        self,
        server_id: str,
        etag: str | None = None,
    ) -> tuple[ServerCard | None, str | None]:
        """Fetch a single server card by ID.

        Tries ``/v1/servers/{id}`` first (Pharos-native spec), then falls back
        to ``/v1/packages/{id}`` (live getpharos.dev registry).

        Args:
            server_id: The server's identifier (URN or package name).
            etag: Optional ETag for conditional request.

        Returns:
            Tuple of (ServerCard, new_etag_or_None)

        Raises:
            RegistryUnavailable: On HTTP errors
        """
        # Try /v1/servers/{id} (Pharos-native spec endpoint).
        encoded_id = _encode_server_id(server_id)
        try:
            card, new_etag = await self._try_get_card(
                f"{self._base_url}/v1/servers/{encoded_id}", etag
            )
            if card is not None or new_etag is not None:
                return card, new_etag
        except RegistryUnavailable:
            # Non-404 error on primary endpoint — propagate.
            raise

        # Primary returned 404 (card and etag both None) — try live fallback.
        card, new_etag = await self._try_get_card(
            f"{self._base_url}/v1/packages/{encoded_id}", etag
        )
        if card is None and new_etag is None:
            # Both endpoints returned 404 — server not found.
            raise RegistryUnavailable(
                self._base_url, status=404, detail="Server not found"
            )
        return card, new_etag

    async def _try_get_card(
        self,
        url: str,
        etag: str | None,
    ) -> tuple[ServerCard | None, str | None]:
        headers = self._auth_headers()
        if etag:
            headers["If-None-Match"] = etag

        try:
            async with httpx.AsyncClient(timeout=self._get_timeout) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise RegistryUnavailable(self._base_url, detail=str(exc)) from exc

        if resp.status_code == 304:
            return None, etag

        if resp.status_code == 404:
            # Signal caller to try the fallback endpoint by returning None/None.
            # (Distinguishable from a 304 which returns None/etag.)
            return None, None

        if resp.status_code != 200:
            raise RegistryUnavailable(
                self._base_url,
                status=resp.status_code,
                detail=resp.text,
            )

        new_etag = resp.headers.get("ETag")
        body = resp.json()
        card = _normalize_to_server_card(body, self._base_url)
        return card, new_etag

    async def get_blocklist(self) -> list[str]:
        """Fetch the registry's blocklist of banned server IDs.

        Returns:
            List of blocked server URNs

        Raises:
            RegistryUnavailable: On HTTP errors
        """
        headers = self._auth_headers()

        try:
            async with httpx.AsyncClient(timeout=self._get_timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/v1/blocklist",
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise RegistryUnavailable(self._base_url, detail=str(exc)) from exc

        if resp.status_code == 404:
            # Live registry may not implement /v1/blocklist — return empty.
            return []

        if resp.status_code != 200:
            raise RegistryUnavailable(
                self._base_url,
                status=resp.status_code,
                detail=resp.text,
            )

        return resp.json().get("blocked", [])

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers
