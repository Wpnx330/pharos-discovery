from __future__ import annotations

import math
import re
from typing import Any

import httpx

from pharos_discovery.adapters.registry import SearchResult
from pharos_discovery.errors import NoServersFound, RegistryUnavailable
from pharos_discovery.models import (
    AuthSpec,
    Publisher,
    ServerCard,
)

_DEFAULT_REGISTRY = "https://registry.modelcontextprotocol.io/v1"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase + extract alphanumeric tokens."""
    return _TOKEN_RE.findall(text.lower())


def _build_corpus_tokens(
    name: str,
    description: str,
    capabilities: list[str],
    tags: list[str],
) -> list[str]:
    """Combine the searchable text fields into a single token list."""
    parts = [name, description, " ".join(capabilities), " ".join(tags)]
    return _tokenize(" ".join(parts))


def _compute_tfidf(
    documents: list[list[str]],
) -> list[dict[str, float]]:
    """Compute TF-IDF vectors for a list of tokenized documents."""
    n_docs = len(documents)
    if n_docs == 0:
        return []

    # Document frequency
    df: dict[str, int] = {}
    for tokens in documents:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1

    # IDF with smoothing
    idf: dict[str, float] = {
        term: math.log((1 + n_docs) / (1 + count)) + 1.0
        for term, count in df.items()
    }

    vectors: list[dict[str, float]] = []
    for tokens in documents:
        tf: dict[str, int] = {}
        for tok in tokens:
            tf[tok] = tf.get(tok, 0) + 1
        total = len(tokens) if tokens else 1
        vec: dict[str, float] = {}
        for term, count in tf.items():
            vec[term] = (count / total) * idf.get(term, 0.0)
        vectors.append(vec)
    return vectors


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two sparse TF-IDF vectors."""
    if not a or not b:
        return 0.0
    # Iterate over the smaller vector
    if len(a) > len(b):
        a, b = b, a
    dot = sum(weight * b.get(term, 0.0) for term, weight in a.items())
    norm_a = math.sqrt(sum(w * w for w in a.values()))
    norm_b = math.sqrt(sum(w * w for w in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class MCPOfficialAdapter:
    """Adapter for the official MCP registry with client-side semantic re-ranking.

    Fetches server packages from the official MCP registry API
    (https://registry.modelcontextprotocol.io/v1/), normalizes the MCP
    registry format into pharos ``ServerCard`` objects, and applies
    client-side TF-IDF cosine-similarity re-ranking on
    name + description + capabilities + tags.

    This lets agents search the official MCP registry using the same
    pharos discovery interface they use for Pharos-native registries.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_REGISTRY,
        *,
        search_timeout: float = 10.0,
        get_timeout: float = 10.0,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._search_timeout = search_timeout
        self._get_timeout = get_timeout
        self._api_key = api_key
        self._client = client

    @property
    def base_url(self) -> str:
        return self._base_url

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[SearchResult]:
        """Search the MCP registry and re-rank results by semantic similarity.

        Args:
            query: Free-text search query (matched against name, description,
                capabilities, and tags).
            limit: Maximum number of results to return after re-ranking.

        Returns:
            List of ``SearchResult`` objects sorted by descending
            ``relevanceScore`` (TF-IDF cosine similarity to the query).
            Each result's ``score`` field holds the relevance score.

        Raises:
            NoServersFound: When the registry returns zero packages.
            RegistryUnavailable: On HTTP/network errors.
        """
        raw_items = await self._fetch_packages(query)

        if not raw_items:
            raise NoServersFound(query)

        # Build corpus: one "document" per MCP package
        parsed: list[tuple[ServerCard, list[str]]] = []
        for item in raw_items:
            card = self._normalize(item)
            tokens = _build_corpus_tokens(
                card.display_name,
                card.description,
                card.capabilities,
                card.tags,
            )
            parsed.append((card, tokens))

        # Build the query vector
        query_tokens = _tokenize(query)
        all_docs = [tokens for _, tokens in parsed]
        all_docs.append(query_tokens)
        tfidf_vectors = _compute_tfidf(all_docs)
        query_vec = tfidf_vectors[-1]

        # Score each document
        scored: list[SearchResult] = []
        for idx, (card, _) in enumerate(parsed):
            relevance = _cosine_similarity(query_vec, tfidf_vectors[idx])
            scored.append(SearchResult(card=card, score=relevance))

        # Sort by relevance descending
        scored.sort(key=lambda r: r.score or 0.0, reverse=True)
        return scored[:limit]

    async def get(self, name: str) -> ServerCard:
        """Fetch a single MCP server package by name.

        Args:
            name: The MCP registry package name (e.g. ``@modelcontextprotocol/server-github``).

        Returns:
            A normalized ``ServerCard``.

        Raises:
            RegistryUnavailable: On HTTP errors or when the package is not found.
        """
        headers = self._auth_headers()
        url = f"{self._base_url}/servers/{name}"

        try:
            resp = await self._do_get(url, headers, self._get_timeout)
        except httpx.HTTPError as exc:
            raise RegistryUnavailable(self._base_url, detail=str(exc)) from exc

        if resp.status_code != 200:
            raise RegistryUnavailable(
                self._base_url,
                status=resp.status_code,
                detail=resp.text,
            )

        return self._normalize(resp.json())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_packages(self, query: str) -> list[dict[str, Any]]:
        """Fetch raw package list from the MCP registry."""
        headers = self._auth_headers()
        url = f"{self._base_url}/servers"
        params: dict[str, Any] = {}
        if query:
            params["q"] = query

        try:
            resp = await self._do_get(url, headers, self._search_timeout, params=params)
        except httpx.HTTPError as exc:
            raise RegistryUnavailable(self._base_url, detail=str(exc)) from exc

        if resp.status_code != 200:
            raise RegistryUnavailable(
                self._base_url,
                status=resp.status_code,
                detail=resp.text,
            )

        data = resp.json()
        # The MCP registry API returns {"servers": [...]} or {"packages": [...]}
        # Fall back to a bare list for robustness.
        if isinstance(data, list):
            return data
        return data.get("servers", data.get("packages", []))

    async def _do_get(
        self,
        url: str,
        headers: dict[str, str],
        timeout: float,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, headers=headers, params=params, timeout=timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(url, headers=headers, params=params)

    def _normalize(self, raw: dict[str, Any]) -> ServerCard:
        """Convert an MCP registry package dict into a pharos ``ServerCard``.

        The official MCP registry format differs from the Pharos ServerCard
        schema. This method maps the fields that have equivalents and fills
        sensible defaults for the rest.
        """
        server_id = raw.get("id") or raw.get("name", "unknown")
        name = raw.get("name") or raw.get("display_name") or server_id
        description = raw.get("description") or raw.get("summary") or ""
        version = raw.get("version") or "0.0.0"

        # Publisher
        pub_raw = raw.get("publisher") or {}
        publisher = Publisher(
            id=pub_raw.get("id") or pub_raw.get("name") or "unknown",
            name=pub_raw.get("name") or pub_raw.get("id") or "unknown",
            verified=pub_raw.get("verified"),
            verification_method=pub_raw.get("verification_method"),
            contact=pub_raw.get("contact"),
        )

        # Transport — MCP registry may store as "transports" or "transport"
        raw_transports = raw.get("transports") or raw.get("transport") or []
        if isinstance(raw_transports, str):
            raw_transports = [raw_transports]
        transports = [t for t in raw_transports if t in ("stdio", "http+sse", "streamable-http")]
        if not transports:
            transports = ["http+sse"]

        # Capabilities
        capabilities = raw.get("capabilities") or raw.get("tools") or []

        # Auth
        raw_auth = raw.get("auth") or {}
        auth_type = raw_auth.get("type", "none")
        if auth_type not in ("none", "api_key", "oauth", "mtls"):
            auth_type = "none"
        auth = AuthSpec(type=auth_type)  # type: ignore[arg-type]

        # Status
        raw_status = raw.get("status", "active")
        if raw_status not in ("active", "deprecated", "deleted"):
            raw_status = "active"

        # Endpoint
        endpoint = raw.get("endpoint") or raw.get("url")
        stdio_command = raw.get("stdio_command") or raw.get("command")

        # Timestamps
        published_at = raw.get("published_at") or raw.get("created_at") or ""
        updated_at = raw.get("updated_at") or raw.get("modified_at") or ""
        if not published_at:
            published_at = "1970-01-01T00:00:00Z"
        if not updated_at:
            updated_at = published_at

        # Tags
        tags = raw.get("tags") or []

        # Tools count
        tools_count = raw.get("tools_count", len(capabilities) if capabilities else 0)

        # Source registry
        source_registry = raw.get("source_registry") or self._base_url

        # Availability
        availability = raw.get("availability", "native")
        if availability not in ("mirrored", "referenced", "native"):
            availability = "native"

        return ServerCard(
            id=server_id,
            display_name=name,
            description=description,
            publisher=publisher,
            version=version,
            transport=transports,  # type: ignore[arg-type]
            endpoint=endpoint,
            stdio_command=stdio_command,
            capabilities=capabilities,
            tools_count=tools_count,
            auth=auth,
            availability=availability,  # type: ignore[arg-type]
            tags=tags,
            source_registry=source_registry,
            published_at=published_at,
            updated_at=updated_at,
            status=raw_status,  # type: ignore[arg-type]
        )

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers
