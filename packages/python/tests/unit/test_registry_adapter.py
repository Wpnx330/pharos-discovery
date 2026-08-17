from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pharos_discovery.adapters.registry import (
    PharosRegistryAdapter,
    SearchResult,
    _slugify,
)
from pharos_discovery.errors import NoServersFound, RegistryUnavailable


def _mock_card():
    return {
        "id": "urn:pharos:server-001",
        "display_name": "Test Server",
        "description": "A test MCP server",
        "publisher": {"id": "did:web:example.com", "name": "TestPub"},
        "version": "1.0.0",
        "transport": ["http+sse"],
        "capabilities": ["search"],
        "tools_count": 3,
        "auth": {"type": "none"},
        "availability": "native",
        "source_registry": "https://registry.pharos.dev",
        "published_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
        "status": "active",
    }


@pytest.fixture
def adapter():
    return PharosRegistryAdapter("https://registry.pharos.dev")


@pytest.fixture
def auth_adapter():
    return PharosRegistryAdapter("https://registry.pharos.dev", api_key="secret-key")


class TestSearch:
    @pytest.mark.anyio
    async def test_search_success(self, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [_mock_card()]}
        mock_response.text = ""

        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock_response)):
            results = await adapter.search(text="test", limit=5)

        assert len(results) == 1
        assert isinstance(results[0], SearchResult)
        assert results[0].card.id == "urn:pharos:server-001"

    @pytest.mark.anyio
    async def test_search_no_results(self, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}
        mock_response.text = ""

        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock_response)):
            with pytest.raises(NoServersFound):
                await adapter.search(text="nonexistent")

    @pytest.mark.anyio
    async def test_search_http_error(self, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"

        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock_response)):
            with pytest.raises(RegistryUnavailable) as exc_info:
                await adapter.search(text="test")
            assert exc_info.value.status == 503

    @pytest.mark.anyio
    async def test_search_network_error(self, adapter):
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            with pytest.raises(RegistryUnavailable):
                await adapter.search(text="test")

    @pytest.mark.anyio
    async def test_search_with_filters(self, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [_mock_card()]}
        mock_response.text = ""

        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock_response)) as mock_get:
            await adapter.search(
                text="flights",
                filters={"transport": ["http+sse"], "publisher_verified": True},
                limit=10,
            )
            call_kwargs = mock_get.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert params.get("text") == "flights"
            assert params.get("limit") == 10


class TestGetServerCard:
    @pytest.mark.anyio
    async def test_get_success(self, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _mock_card()
        mock_response.headers = {"ETag": "abc123"}

        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock_response)):
            card, etag = await adapter.get_server_card("urn:pharos:server-001")

        assert card.id == "urn:pharos:server-001"
        assert etag == "abc123"

    @pytest.mark.anyio
    async def test_get_not_modified(self, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 304
        mock_response.headers = {}

        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock_response)):
            card, etag = await adapter.get_server_card("urn:pharos:server-001", etag="abc123")

        assert card is None
        assert etag == "abc123"

    @pytest.mark.anyio
    async def test_get_http_error(self, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock_response)):
            with pytest.raises(RegistryUnavailable) as exc_info:
                await adapter.get_server_card("urn:pharos:nonexistent")
            assert exc_info.value.status == 404


class TestGetBlocklist:
    @pytest.mark.anyio
    async def test_get_blocklist_success(self, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"blocked": ["urn:pharos:bad-1", "urn:pharos:bad-2"]}

        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock_response)):
            blocked = await adapter.get_blocklist()

        assert len(blocked) == 2
        assert "urn:pharos:bad-1" in blocked


class TestAuth:
    @pytest.mark.anyio
    async def test_api_key_in_headers(self, auth_adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [_mock_card()]}
        mock_response.text = ""

        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock_response)) as mock_get:
            await auth_adapter.search(text="test")
            call_kwargs = mock_get.call_args
            headers = call_kwargs.kwargs.get("headers", {})
            assert headers.get("Authorization") == "Bearer secret-key"

    def test_base_url_stripped(self):
        adapter = PharosRegistryAdapter("https://reg.example.com/")
        assert adapter.base_url == "https://reg.example.com"


def _live_registry_item(**overrides):
    item = {
        "name": "🌦️ Weather MCP Demo",
        "title": "Weather MCP Demo",
        "description": "Demo weather server",
        "version": "1.2.3",
        "publisher": {"namespace": "official-sync", "verified": False},
        "capabilities": {"tools": True},
        "transport": ["stdio"],
        "published_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    item.update(overrides)
    return item


class TestLiveRegistryNormalization:
    def test_prefers_stable_id_over_display_name(self):
        from pharos_discovery.adapters.registry import _normalize_to_server_card

        card = _normalize_to_server_card(
            _live_registry_item(id="weather-mcp-demo", name="🌦️ Weather MCP Demo"),
            "https://getpharos.dev",
        )
        assert card.id == "weather-mcp-demo"
        assert card.display_name == "Weather MCP Demo"

    def test_generates_slug_when_only_name_exists(self):
        from pharos_discovery.adapters.registry import _normalize_to_server_card

        card = _normalize_to_server_card(_live_registry_item(), "https://getpharos.dev")
        assert card.display_name == "Weather MCP Demo"
        assert card.id == "weather-mcp-demo"
        assert "🌦️" not in card.id

    def test_extracts_bin_as_stdio_command(self):
        from pharos_discovery.adapters.registry import _normalize_to_server_card

        card = _normalize_to_server_card(
            _live_registry_item(bin="npx -y @demo/weather"),
            "https://getpharos.dev",
        )
        assert card.stdio_command == "npx -y @demo/weather"

    def test_search_hit_with_endpoint_and_command(self):
        """Live search JSON now includes install fields; they must map onto ServerCard."""
        from pharos_discovery.adapters.registry import _normalize_to_server_card

        card = _normalize_to_server_card(
            _live_registry_item(
                name="test-echo-server",
                title="Test Echo Server",
                transport=["http-sse"],
                endpoint="http://host.docker.internal:8765",
                command="npx -y @demo/echo",
            ),
            "https://getpharos.dev",
        )
        assert card.endpoint == "http://host.docker.internal:8765"
        assert card.stdio_command == "npx -y @demo/echo"

    def test_extracts_manifest_bin_list(self):
        from pharos_discovery.adapters.registry import _normalize_to_server_card

        card = _normalize_to_server_card(
            _live_registry_item(
                versions=[{
                    "version": "1.2.3",
                    "manifest": {"bin": ["npx", "-y", "@demo/weather"]},
                }],
                dist_tags={"latest": "1.2.3"},
            ),
            "https://getpharos.dev",
        )
        assert card.stdio_command == "npx -y @demo/weather"


class TestGetServerCardEncoding:
    @pytest.mark.anyio
    async def test_encodes_spaces_and_emoji_in_path(self, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _mock_card()
        mock_response.headers = {"ETag": "abc123"}

        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock_response)) as mock_get:
            await adapter.get_server_card("🌦️ Weather MCP Demo")

        requested = mock_get.call_args.args[0]
        assert " " not in requested
        assert "🌦️" not in requested
        assert "%20" in requested or "%F0%9F" in requested


class TestSlugify:
    def test_removes_emojis_and_lowercases(self):
        assert _slugify("🌦️ Weather MCP Demo") == "weather-mcp-demo"

    def test_replaces_spaces_with_hyphens(self):
        assert _slugify("Hello World Server") == "hello-world-server"

    def test_collapses_multiple_hyphens(self):
        assert _slugify("A  --  B") == "a-b"

    def test_strips_leading_trailing_hyphens(self):
        assert _slugify("--hello--") == "hello"

    def test_preserves_safe_special_chars(self):
        assert _slugify("my@server.v1") == "my@server.v1"

    def test_all_emoji_falls_back_to_hash(self):
        slug = _slugify("🌦️⛈️🌈")
        assert slug.startswith("server-")
        assert len(slug) == len("server-") + 8

    def test_all_emoji_hash_is_deterministic(self):
        assert _slugify("🌦️⛈️🌈") == _slugify("🌦️⛈️🌈")

    def test_different_emoji_names_produce_different_hashes(self):
        assert _slugify("🌦️") != _slugify("⛈️")

    def test_empty_string_falls_back_to_hash(self):
        slug = _slugify("")
        assert slug.startswith("server-")

    def test_parentheses_removed(self):
        result = _slugify("Weather MCP (Model Context Protocol) Demo")
        assert result == "weather-mcp-model-context-protocol-demo"


class TestSlugNormalization:
    def test_emoji_name_gets_sanitized_slug_as_id(self):
        from pharos_discovery.adapters.registry import _normalize_to_server_card

        item = _live_registry_item(name="⛈️ Weather MCP (Model Context Protocol) Demo")
        card = _normalize_to_server_card(item, "https://getpharos.dev")
        assert card.id == "weather-mcp-model-context-protocol-demo"
        assert card.display_name == "Weather MCP Demo"
        assert "⛈️" not in card.id

    def test_proper_id_field_kept_as_is(self):
        from pharos_discovery.adapters.registry import _normalize_to_server_card

        card = _normalize_to_server_card(
            _live_registry_item(id="com.example.weather", name="🌦️ Weather"),
            "https://getpharos.dev",
        )
        assert card.id == "com.example.weather"

    def test_all_emoji_name_gets_hash_fallback_as_id(self):
        from pharos_discovery.adapters.registry import _normalize_to_server_card

        card = _normalize_to_server_card(
            _live_registry_item(name="🌦️⛈️", title="Weather"),
            "https://getpharos.dev",
        )
        assert card.id.startswith("server-")
        assert len(card.id) == len("server-") + 8
        assert card.display_name == "Weather"

    def test_slug_map_populated_during_normalization(self):
        from pharos_discovery.adapters.registry import _normalize_to_server_card

        slug_map: dict[str, str] = {}
        item = _live_registry_item(name="🌦️ Weather MCP Demo")
        card = _normalize_to_server_card(item, "https://getpharos.dev", slug_map)
        assert slug_map == {card.id: "🌦️ Weather MCP Demo"}

    def test_slug_map_not_populated_when_id_exists(self):
        from pharos_discovery.adapters.registry import _normalize_to_server_card

        slug_map: dict[str, str] = {}
        _normalize_to_server_card(
            _live_registry_item(id="weather-mcp", name="🌦️ Weather"),
            "https://getpharos.dev",
            slug_map,
        )
        assert slug_map == {}


class TestSlugReverseLookup:
    @pytest.mark.anyio
    async def test_get_server_card_resolves_slug_to_original_name(self):
        """After search(), get_server_card(slug) should look up the original name."""
        adapter = PharosRegistryAdapter("https://getpharos.dev")

        # Simulate search() populating the slug map via normalization.
        from pharos_discovery.adapters.registry import _normalize_to_server_card
        _normalize_to_server_card(
            _live_registry_item(name="🌦️ Weather MCP Demo"),
            "https://getpharos.dev",
            adapter._slug_to_original,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _mock_card()
        mock_response.headers = {"ETag": "abc123"}

        with patch.object(
            httpx.AsyncClient, "get", new=AsyncMock(return_value=mock_response)
        ) as mock_get:
            await adapter.get_server_card("weather-mcp-demo")

        requested_url = mock_get.call_args.args[0]
        # The original name should have been URL-encoded into the path,
        # NOT the slug.
        assert "weather-mcp-demo" not in requested_url
        assert "%F0%9F%8C%A6" in requested_url or "%F0%9F" in requested_url

    @pytest.mark.anyio
    async def test_get_server_card_uses_raw_id_when_no_slug_mapping(self):
        """When no slug mapping exists, server_id is used as-is."""
        adapter = PharosRegistryAdapter("https://getpharos.dev")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _mock_card()
        mock_response.headers = {"ETag": "abc123"}

        with patch.object(
            httpx.AsyncClient, "get", new=AsyncMock(return_value=mock_response)
        ) as mock_get:
            await adapter.get_server_card("urn:pharos:server-001")

        requested_url = mock_get.call_args.args[0]
        assert "urn%3Apharos%3Aserver-001" in requested_url
