from __future__ import annotations

from typing import Any

import httpx

from pharos_discovery.errors import NoServersFound, RegistryUnavailable
from pharos_discovery.models import ServerCard


class SearchResult:
    """Wrapper for a search result item."""

    def __init__(self, card: ServerCard, score: float | None = None):
        self.card = card
        self.score = score


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
            card = ServerCard(**item)
            score = item.get("_score")
            results.append(SearchResult(card=card, score=score))

        return results

    async def get_server_card(
        self,
        server_id: str,
        etag: str | None = None,
    ) -> tuple[ServerCard, str | None]:
        """Fetch a single server card by ID.

        Args:
            server_id: The server's URN identifier
            etag: Optional ETag for conditional request

        Returns:
            Tuple of (ServerCard, new_etag_or_None)

        Raises:
            RegistryUnavailable: On HTTP errors
        """
        headers = self._auth_headers()
        if etag:
            headers["If-None-Match"] = etag

        try:
            async with httpx.AsyncClient(timeout=self._get_timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/v1/servers/{server_id}",
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise RegistryUnavailable(self._base_url, detail=str(exc)) from exc

        if resp.status_code == 304:
            # Not modified - caller should use cached version
            return None, etag  # type: ignore[return-value]

        if resp.status_code != 200:
            raise RegistryUnavailable(
                self._base_url,
                status=resp.status_code,
                detail=resp.text,
            )

        new_etag = resp.headers.get("ETag")
        card = ServerCard(**resp.json())
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
