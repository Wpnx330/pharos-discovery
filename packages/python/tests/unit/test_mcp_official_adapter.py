import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from pharos_discovery.adapters.mcp_official import MCPOfficialAdapter
from pharos_discovery.adapters.registry import SearchResult
from pharos_discovery.errors import NoServersFound, RegistryUnavailable


def _mock_mcp_package(
    name="server-github",
    description="GitHub integration for MCP",
    capabilities=None,
    tags=None,
):
    return {
        "id": name,
        "name": name,
        "description": description,
        "version": "1.2.0",
        "publisher": {"id": "github", "name": "GitHub", "verified": True},
        "transports": ["http+sse"],
        "capabilities": capabilities if capabilities is not None else ["search", "tools"],
        "auth": {"type": "api_key"},
        "status": "active",
        "tags": tags if tags is not None else ["github", "git"],
        "endpoint": "https://mcp.github.dev/sse",
        "published_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-06-01T00:00:00Z",
    }


def _mock_response(data, status=200, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    resp.text = text
    resp.headers = {}
    return resp


@pytest.fixture
def adapter():
    return MCPOfficialAdapter("https://registry.modelcontextprotocol.io/v1")


class TestSearch:
    @pytest.mark.anyio
    async def test_search_returns_results(self, adapter):
        packages = [_mock_mcp_package()]
        mock = _mock_response({"servers": packages})
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            results = await adapter.search("github", limit=10)
        assert len(results) == 1
        assert isinstance(results[0], SearchResult)
        assert results[0].card.id == "server-github"
        assert results[0].score is not None
        assert results[0].score > 0.0

    @pytest.mark.anyio
    async def test_search_reranking_order(self, adapter):
        """More relevant packages should rank higher."""
        pkg_relevant = _mock_mcp_package(
            name="github-server",
            description="GitHub tools and search for repos",
            capabilities=["search", "tools"],
            tags=["github", "git", "repos"],
        )
        pkg_irrelevant = _mock_mcp_package(
            name="weather-server",
            description="Weather forecasts and alerts",
            capabilities=["forecast"],
            tags=["weather", "climate"],
        )
        mock = _mock_response({"servers": [pkg_irrelevant, pkg_relevant]})
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            results = await adapter.search("github repos", limit=10)
        assert results[0].card.id == "github-server"
        assert results[0].score > results[1].score

    @pytest.mark.anyio
    async def test_search_respects_limit(self, adapter):
        packages = [
            _mock_mcp_package(name=f"srv-{i}", description=f"server {i} search")
            for i in range(5)
        ]
        mock = _mock_response({"servers": packages})
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            results = await adapter.search("search", limit=3)
        assert len(results) == 3

    @pytest.mark.anyio
    async def test_search_no_results_raises(self, adapter):
        mock = _mock_response({"servers": []})
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            with pytest.raises(NoServersFound):
                await adapter.search("nonexistent")

    @pytest.mark.anyio
    async def test_search_http_error_raises(self, adapter):
        mock = _mock_response({}, status=503, text="Service Unavailable")
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            with pytest.raises(RegistryUnavailable) as exc_info:
                await adapter.search("test")
            assert exc_info.value.status == 503

    @pytest.mark.anyio
    async def test_search_network_error_raises(self, adapter):
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            with pytest.raises(RegistryUnavailable):
                await adapter.search("test")

    @pytest.mark.anyio
    async def test_search_accepts_bare_list(self, adapter):
        """Registry may return a bare JSON array."""
        packages = [_mock_mcp_package()]
        mock = _mock_response(packages)
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            results = await adapter.search("github", limit=10)
        assert len(results) == 1

    @pytest.mark.anyio
    async def test_search_accepts_packages_key(self, adapter):
        """Registry may use a 'packages' key instead of 'servers'."""
        packages = [_mock_mcp_package()]
        mock = _mock_response({"packages": packages})
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            results = await adapter.search("github", limit=10)
        assert len(results) == 1


class TestGet:
    @pytest.mark.anyio
    async def test_get_success(self, adapter):
        mock = _mock_response(_mock_mcp_package())
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            card = await adapter.get("server-github")
        assert card.id == "server-github"
        assert card.display_name == "server-github"

    @pytest.mark.anyio
    async def test_get_not_found_raises(self, adapter):
        mock = _mock_response({}, status=404, text="Not Found")
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            with pytest.raises(RegistryUnavailable) as exc_info:
                await adapter.get("nonexistent")
            assert exc_info.value.status == 404

    @pytest.mark.anyio
    async def test_get_network_error_raises(self, adapter):
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            with pytest.raises(RegistryUnavailable):
                await adapter.get("server-github")


class TestNormalization:
    @pytest.mark.anyio
    async def test_normalize_maps_fields(self, adapter):
        raw = {
            "id": "my-server",
            "name": "My Server",
            "description": "Does cool things",
            "version": "2.0.0",
            "publisher": {"id": "pub-1", "name": "Publisher One", "verified": True},
            "transports": ["stdio", "http+sse"],
            "capabilities": ["search"],
            "auth": {"type": "oauth"},
            "status": "active",
            "tags": ["cool"],
            "endpoint": "https://example.com/sse",
            "published_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
        }
        mock = _mock_response(raw)
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            card = await adapter.get("my-server")
        assert card.display_name == "My Server"
        assert card.publisher.name == "Publisher One"
        assert card.publisher.verified is True
        assert "stdio" in card.transport
        assert "http+sse" in card.transport
        assert card.auth.type == "oauth"
        assert card.endpoint == "https://example.com/sse"
        assert card.tags == ["cool"]

    @pytest.mark.anyio
    async def test_normalize_defaults_unknown_transport(self, adapter):
        raw = _mock_mcp_package()
        raw["transports"] = ["weird-transport"]
        mock = _mock_response(raw)
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            card = await adapter.get("server-github")
        assert card.transport == ["http+sse"]

    @pytest.mark.anyio
    async def test_normalize_defaults_empty_description(self, adapter):
        raw = _mock_mcp_package()
        raw["description"] = ""
        mock = _mock_response(raw)
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            card = await adapter.get("server-github")
        assert card.description == ""

    @pytest.mark.anyio
    async def test_normalize_fills_missing_timestamps(self, adapter):
        raw = _mock_mcp_package()
        del raw["published_at"]
        del raw["updated_at"]
        mock = _mock_response(raw)
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            card = await adapter.get("server-github")
        assert card.published_at == "1970-01-01T00:00:00Z"
        assert card.updated_at == "1970-01-01T00:00:00Z"

    @pytest.mark.anyio
    async def test_normalize_unknown_auth_type_defaults_to_none(self, adapter):
        raw = _mock_mcp_package()
        raw["auth"] = {"type": "bizarre"}
        mock = _mock_response(raw)
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            card = await adapter.get("server-github")
        assert card.auth.type == "none"

    @pytest.mark.anyio
    async def test_normalize_unknown_status_defaults_to_active(self, adapter):
        raw = _mock_mcp_package()
        raw["status"] = "archived"
        mock = _mock_response(raw)
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            card = await adapter.get("server-github")
        assert card.status == "active"

    @pytest.mark.anyio
    async def test_normalize_unknown_availability_defaults_to_native(self, adapter):
        raw = _mock_mcp_package()
        raw["availability"] = "cloud"
        mock = _mock_response(raw)
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            card = await adapter.get("server-github")
        assert card.availability == "native"


class TestRelevanceScore:
    @pytest.mark.anyio
    async def test_exact_match_has_high_score(self, adapter):
        pkg = _mock_mcp_package(
            name="flight",
            description="flight booking",
            capabilities=["search"],
            tags=["flight"],
        )
        mock = _mock_response({"servers": [pkg]})
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            results = await adapter.search("flight booking search", limit=10)
        assert results[0].score is not None
        assert results[0].score > 0.5

    @pytest.mark.anyio
    async def test_no_overlap_has_zero_score(self, adapter):
        pkg = _mock_mcp_package(
            name="weather",
            description="weather forecasts climate",
            capabilities=["forecast"],
            tags=["weather"],
        )
        mock = _mock_response({"servers": [pkg]})
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=mock)):
            results = await adapter.search("flight booking", limit=10)
        assert results[0].score is not None
        assert results[0].score == 0.0


class TestBaseUrl:
    def test_base_url_stripped(self):
        adapter = MCPOfficialAdapter("https://registry.example.com/v1/")
        assert adapter.base_url == "https://registry.example.com/v1"
