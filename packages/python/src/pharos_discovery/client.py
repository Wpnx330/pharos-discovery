from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pharos_discovery.adapters.registry import PharosRegistryAdapter, SearchResult
from pharos_discovery.cache import ServerCardCache
from pharos_discovery.errors import (
    ApprovalDenied,
    ConnectionFailed,
    ConsentFatigueWarning,
    DiscoveryDegraded,
    HeadlessApprovalRequired,
    NoServersFound,
    PharosError,
    RegistryUnavailable,
    ScopeNotApproved,
)
from pharos_discovery.models import (
    ApprovalRequest,
    ApprovalResponse,
    ApprovalToken,
    OAuthResult,
    ServerCard,
)


class ApprovalHandler(Protocol):
    """Protocol for approval UI handlers."""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        ...


class ConnectionHandler(Protocol):
    """Protocol for connection lifecycle handlers."""

    async def connect(self, card: ServerCard, token: ApprovalToken) -> Any:
        ...

    async def disconnect(self, server_id: str) -> None:
        ...


class PharosClient:
    """High-level discovery client orchestrating search, approval, and connection.

    This is the main entry point for applications using Pharos Discovery.
    It coordinates between the registry adapter (search/fetch), approval
    handlers (user consent), and connection handlers (MCP transport).

    Usage:
        client = PharosClient("https://registry.pharos.dev")
        results = await client.search("flight booking")
        connection = await client.connect_and_approve(results[0], "Book a flight")
    """

    def __init__(
        self,
        registry_url: str,
        *,
        api_key: str | None = None,
        cache_ttl: int = 300,
        headless: bool = False,
        max_novel_approvals: int = 5,
        approval_handler: ApprovalHandler | None = None,
        connection_handler: ConnectionHandler | None = None,
    ):
        self._adapter = PharosRegistryAdapter(registry_url, api_key=api_key)
        self._cache = ServerCardCache(ttl_seconds=cache_ttl)
        self._headless = headless
        self._max_novel = max_novel_approvals
        self._approval_handler = approval_handler
        self._connection_handler = connection_handler
        self._novel_count = 0
        self._approved_servers: dict[str, ApprovalToken] = {}
        self._connections: dict[str, Any] = {}
        self._blocklist: set[str] | None = None

    @property
    def headless(self) -> bool:
        return self._headless

    @property
    def cache(self) -> ServerCardCache:
        return self._cache

    async def search(
        self,
        text: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        """Search for MCP servers. Returns results from cache if available."""
        try:
            results = await self._adapter.search(text, filters, limit)
            for r in results:
                self._cache.put(r.card.id, r.card)
            return results
        except NoServersFound:
            raise
        except RegistryUnavailable:
            # Try cache fallback
            if self._cache.size > 0:
                cached = []
                for sid in list(self._cache._store.keys()):
                    card, _ = self._cache.get(sid)
                    if card:
                        cached.append(SearchResult(card=card, score=None))
                if cached:
                    return cached[:limit]
            raise DiscoveryDegraded() from None

    async def get_server(self, server_id: str) -> ServerCard:
        """Fetch a single server card, using cache + ETag."""
        cached_card, etag = self._cache.get(server_id)
        card, new_etag = await self._adapter.get_server_card(server_id, etag=etag)
        if card is not None:
            self._cache.put(server_id, card, new_etag)
            return card
        # 304 Not Modified — use cached
        if cached_card is not None:
            return cached_card
        raise RegistryUnavailable(self._adapter.base_url, status=404, detail="Server not found and no cache")

    async def connect_and_approve(
        self,
        card: ServerCard,
        purpose: str,
        requested_scopes: list[str] | None = None,
        requested_capabilities: list[str] | None = None,
        duration: str = "session",
    ) -> tuple[ApprovalToken, Any]:
        """Approve and connect to a server in one call.

        1. Check blocklist
        2. Check if already approved (return cached token)
        3. Build ApprovalRequest
        4. Call approval handler (or raise HeadlessApprovalRequired)
        5. If approved, create ApprovalToken and connect
        """
        # Check blocklist
        await self._ensure_blocklist()
        if card.id in self._blocklist:
            raise ApprovalDenied(card.id, "Server is blocklisted")

        # Check existing approval
        if card.id in self._approved_servers:
            token = self._approved_servers[card.id]
            if not self._is_token_expired(token):
                connection = await self._connect(card, token)
                return token, connection

        # Build approval request
        scopes = requested_scopes or card.auth.scopes or []
        capabilities = requested_capabilities or card.capabilities

        request = ApprovalRequest(
            server=card,
            purpose=purpose,
            requested_scopes=scopes,
            requested_capabilities=capabilities,
            duration=duration,
            render_id=f"pharos-{card.id}-{int(time.time())}",
            selection_rationale=f"User requested: {purpose}",
        )

        # Handle approval
        if self._headless:
            raise HeadlessApprovalRequired(card.id)

        if self._approval_handler is None:
            raise PharosError("No approval handler configured")

        response = await self._approval_handler.request_approval(request)

        if not response.approved:
            raise ApprovalDenied(card.id, response.deny_reason)

        # Track novel approvals
        if card.id not in self._approved_servers:
            self._novel_count += 1
            if self._novel_count > self._max_novel:
                raise ConsentFatigueWarning(self._novel_count)

        # Create approval token
        token = ApprovalToken(
            token_id=f"tok-{card.id}-{int(time.time())}",
            server_id=card.id,
            approved_scopes=response.approved_scopes,
            approved_capabilities=capabilities,
            approved_oauth_scopes=[],
            duration=response.duration,
            approved_at=datetime.now(timezone.utc).isoformat(),
            expires_at=str(int(time.time()) + 3600),
            signature="unsigned",  # Real signing in T10
        )

        self._approved_servers[card.id] = token

        # Connect
        connection = await self._connect(card, token)
        return token, connection

    async def revoke(self, server_id: str) -> None:
        """Revoke approval and disconnect from a server."""
        if server_id in self._connections and self._connection_handler:
            await self._connection_handler.disconnect(server_id)
            del self._connections[server_id]

        if server_id in self._approved_servers:
            del self._approved_servers[server_id]

    async def check_scope(self, server_id: str, scope: str) -> None:
        """Verify a scope is approved for a server. Raises ScopeNotApproved if not."""
        token = self._approved_servers.get(server_id)
        if token is None:
            raise ScopeNotApproved(scope, server_id)
        if scope not in token.approved_scopes:
            raise ScopeNotApproved(scope, server_id)

    async def _connect(self, card: ServerCard, token: ApprovalToken) -> Any:
        if self._connection_handler is None:
            raise ConnectionFailed(card.id, "No connection handler configured")
        connection = await self._connection_handler.connect(card, token)
        self._connections[card.id] = connection
        return connection

    async def _ensure_blocklist(self) -> None:
        if self._blocklist is None:
            try:
                self._blocklist = set(await self._adapter.get_blocklist())
            except RegistryUnavailable:
                self._blocklist = set()

    @staticmethod
    def _is_token_expired(token: ApprovalToken) -> bool:
        try:
            expires = float(token.expires_at)
            return time.time() > expires
        except (ValueError, TypeError):
            return False

    async def close(self) -> None:
        """Clean up all connections."""
        if self._connection_handler:
            for server_id in list(self._connections.keys()):
                try:
                    await self._connection_handler.disconnect(server_id)
                except Exception:
                    pass
        self._connections.clear()
        self._approved_servers.clear()
