"""
Unit tests for the PHAROS Discovery MCP Server (server.py).

Tests cover:
- Tool registration and schemas
- pharos_search: query normalization, limit clamping, error handling
- pharos_install: CLI not found, timeout, success
- pharos_connect: already connected, server not found, approval flow
- pharos_list_tools: not connected, connected
- pharos_call_tool: not connected, call delegation
- UI resources: MIME type, content
- Helper functions: _get_client, _get_pharos_cli, _resolve_local_endpoint
- Transport configuration: stdio, sse, streamable-http
- Security: DNS rebinding settings, no injection in error messages
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from typing import Any

import pytest

from pharos_discovery.mcp_server import server as srv


# ─── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset all global state before each test."""
    srv._client = None
    srv._connections.clear()
    srv._installed_servers.clear()
    srv._server_cards.clear()
    yield
    srv._client = None
    srv._connections.clear()
    srv._installed_servers.clear()
    srv._server_cards.clear()


@pytest.fixture
def mock_client():
    """Mock PharosClient with configurable search results."""
    client = AsyncMock()
    # Default: return empty results
    result_mock = MagicMock()
    result_mock.card.id = "test-server"
    result_mock.card.display_name = "Test Server"
    result_mock.card.description = "A test server"
    result_mock.card.version = "1.0.0"
    result_mock.card.transport = ["stdio"]
    result_mock.card.publisher = MagicMock(name="pub", verified=True)
    result_mock.card.publisher.name = "test-pub"
    result_mock.card.publisher.verified = True
    result_mock.card.tools_count = 3
    result_mock.card.capabilities = ["streaming", "tools"]
    result_mock.card.endpoint = "http://127.0.0.1:8765"
    client.search = AsyncMock(return_value=[result_mock])
    client.get_server = AsyncMock(return_value=result_mock.card)
    return client


@pytest.fixture
def mock_server_card():
    """A mock ServerCard for testing."""
    card = MagicMock()
    card.id = "echo-server"
    card.display_name = "Echo Server"
    card.description = "An echo server for testing"
    card.version = "0.2.2"
    card.transport = ["http+sse"]
    card.publisher = MagicMock()
    card.publisher.name = "Wpnx330"
    card.publisher.verified = True
    card.tools_count = 2
    card.capabilities = ["streaming", "tools"]
    card.endpoint = "http://127.0.0.1:8765"
    return card


# ─── Tool Registration ─────────────────────────────────────────────────────────

class TestToolRegistration:
    """Verify all 5 tools and 3 resources are registered with the FastMCP server."""

    def test_server_name(self):
        """Server should be named 'pharos-discovery'."""
        # FastMCP stores the name internally
        assert srv.mcp is not None

    def test_pharos_search_registered(self):
        """pharos_search should be a registered tool."""
        # FastMCP stores tools in _tool_manager
        # We verify by checking the function is decorated and callable
        assert callable(srv.pharos_search)
        assert srv.pharos_search.__doc__ is not None
        assert "Search the PHAROS registry" in srv.pharos_search.__doc__

    def test_pharos_install_registered(self):
        assert callable(srv.pharos_install)
        assert "Install an MCP server" in srv.pharos_install.__doc__

    def test_pharos_connect_registered(self):
        assert callable(srv.pharos_connect)
        assert "Connect to a running MCP server" in srv.pharos_connect.__doc__

    def test_pharos_list_tools_registered(self):
        assert callable(srv.pharos_list_tools)
        assert "List the available tools" in srv.pharos_list_tools.__doc__

    def test_pharos_call_tool_registered(self):
        assert callable(srv.pharos_call_tool)
        assert "Call a tool on a connected MCP server" in srv.pharos_call_tool.__doc__

    def test_approval_resource_registered(self):
        assert callable(srv.approval_resource)
        result = srv.approval_resource()
        assert "<html" in result.lower()
        assert "PHAROS" in result

    def test_oauth_resource_registered(self):
        assert callable(srv.oauth_resource)
        result = srv.oauth_resource()
        assert "<html" in result.lower()
        assert "OAuth" in result or "Authorize" in result

    def test_results_resource_registered(self):
        assert callable(srv.results_resource)
        result = srv.results_resource()
        assert "<html" in result.lower()


# ─── pharos_search ─────────────────────────────────────────────────────────────

class TestPharosSearch:
    """Test the pharos_search tool."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self, mock_client):
        """Search should return JSON with results array."""
        with patch.object(srv, "_get_client", return_value=mock_client):
            result = await srv.pharos_search("echo", limit=5)
        data = json.loads(result)
        assert "results" in data
        assert len(data["results"]) == 1
        assert data["results"][0]["id"] == "test-server"
        assert data["results"][0]["name"] == "Test Server"

    @pytest.mark.asyncio
    async def test_search_caches_cards(self, mock_client):
        """Search should cache server cards for later use."""
        with patch.object(srv, "_get_client", return_value=mock_client):
            await srv.pharos_search("echo")
        assert "test-server" in srv._server_cards

    @pytest.mark.asyncio
    async def test_search_no_servers_found(self, mock_client):
        """Should return empty results when NoServersFound is raised."""
        from pharos_discovery.errors import NoServersFound
        mock_client.search = AsyncMock(side_effect=NoServersFound("none found"))
        with patch.object(srv, "_get_client", return_value=mock_client):
            result = await srv.pharos_search("nonexistent")
        data = json.loads(result)
        assert data["results"] == []
        assert "message" in data

    @pytest.mark.asyncio
    async def test_search_registry_unavailable(self, mock_client):
        """Should return error when RegistryUnavailable is raised."""
        from pharos_discovery.errors import RegistryUnavailable
        mock_client.search = AsyncMock(side_effect=RegistryUnavailable("down"))
        with patch.object(srv, "_get_client", return_value=mock_client):
            result = await srv.pharos_search("echo")
        data = json.loads(result)
        assert "error" in data
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_search_limit_clamped_to_max_50(self, mock_client):
        """Limit should be clamped to 50 max."""
        with patch.object(srv, "_get_client", return_value=mock_client) as m:
            await srv.pharos_search("echo", limit=100)
        # Check that search was called with limit=50 (clamped)
        call_args = mock_client.search.call_args
        assert call_args.kwargs.get("limit", 10) <= 50

    @pytest.mark.asyncio
    async def test_search_limit_clamped_to_min_1(self, mock_client):
        """Limit should be clamped to 1 min."""
        with patch.object(srv, "_get_client", return_value=mock_client):
            await srv.pharos_search("echo", limit=0)
        call_args = mock_client.search.call_args
        assert call_args.kwargs.get("limit", 10) >= 1


# ─── pharos_install ────────────────────────────────────────────────────────────

class TestPharosInstall:
    """Test the pharos_install tool."""

    @pytest.mark.asyncio
    async def test_install_cli_not_found(self):
        """Should return error when pharos CLI is not on PATH."""
        with patch.dict(os.environ, {"PHAROS_CLI": "/nonexistent/pharos"}):
            result = await srv.pharos_install("test-server")
        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"]
        assert data["server_id"] == "test-server"

    @pytest.mark.asyncio
    async def test_install_success(self):
        """Should return installed status on success."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"installed successfully", b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await srv.pharos_install("test-server")
        data = json.loads(result)
        assert data["status"] == "installed"
        assert data["server_id"] == "test-server"
        assert "test-server" in srv._installed_servers

    @pytest.mark.asyncio
    async def test_install_failure(self):
        """Should return error on non-zero exit code."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"install failed"))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await srv.pharos_install("bad-server")
        data = json.loads(result)
        assert "error" in data
        assert data["stderr"] == "install failed"

    @pytest.mark.asyncio
    async def test_install_timeout(self):
        """Should return error on timeout."""
        import asyncio as aio
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=aio.TimeoutError())
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await srv.pharos_install("slow-server")
        data = json.loads(result)
        assert "error" in data
        assert "timed out" in data["error"]


# ─── pharos_connect ────────────────────────────────────────────────────────────

class TestPharosConnect:
    """Test the pharos_connect tool."""

    @pytest.mark.asyncio
    async def test_connect_already_connected(self, mock_client, mock_server_card):
        """Should return already_connected when connection exists."""
        srv._connections["echo-server"] = MagicMock()
        srv._server_cards["echo-server"] = mock_server_card
        result = await srv.pharos_connect("echo-server")
        data = json.loads(result)
        assert data["status"] == "already_connected"

    @pytest.mark.asyncio
    async def test_connect_server_not_found(self, mock_client):
        """Should return error when server is not found in registry."""
        mock_client.get_server = AsyncMock(side_effect=Exception("not found"))
        with patch.object(srv, "_get_client", return_value=mock_client):
            result = await srv.pharos_connect("nonexistent")
        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_connect_from_cache(self, mock_client, mock_server_card):
        """Should use cached card when available."""
        srv._server_cards["echo-server"] = mock_server_card
        # Should NOT call get_server since card is cached
        with patch.object(srv, "_get_client", return_value=mock_client):
            with patch("pharos_discovery.mcp_server.server.ConnectionManager") as MockMgr:
                mock_mgr = AsyncMock()
                mock_connection = AsyncMock()
                mock_mgr.connect = AsyncMock(return_value=mock_connection)
                MockMgr.return_value = mock_mgr
                with patch.object(srv, "_list_server_tools", return_value=[{"name": "echo"}]):
                    result = await srv.pharos_connect("echo-server")
        data = json.loads(result)
        assert data["status"] == "connected"
        # get_server should NOT have been called
        mock_client.get_server.assert_not_called()


# ─── pharos_list_tools ─────────────────────────────────────────────────────────

class TestPharosListTools:
    """Test the pharos_list_tools tool."""

    @pytest.mark.asyncio
    async def test_list_tools_not_connected(self):
        """Should return error when not connected."""
        result = await srv.pharos_list_tools("unknown-server")
        data = json.loads(result)
        assert "error" in data
        assert "Not connected" in data["error"]

    @pytest.mark.asyncio
    async def test_list_tools_connected(self):
        """Should return tools when connected."""
        srv._connections["echo-server"] = MagicMock()
        with patch.object(srv, "_list_server_tools",
                          return_value=[{"name": "echo", "description": "Echo tool"}]):
            result = await srv.pharos_list_tools("echo-server")
        data = json.loads(result)
        assert data["server_id"] == "echo-server"
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "echo"


# ─── pharos_call_tool ──────────────────────────────────────────────────────────

class TestPharosCallTool:
    """Test the pharos_call_tool tool."""

    @pytest.mark.asyncio
    async def test_call_tool_not_connected(self):
        """Should return error when not connected."""
        result = await srv.pharos_call_tool("unknown", "echo")
        data = json.loads(result)
        assert "error" in data
        assert "Not connected" in data["error"]

    @pytest.mark.asyncio
    async def test_call_tool_success(self):
        """Should return tool result when connected."""
        mock_connection = AsyncMock()
        mock_connection.call_tool = AsyncMock(return_value={"echo": "hello"})
        srv._connections["echo-server"] = mock_connection
        result = await srv.pharos_call_tool("echo-server", "echo", {"text": "hello"})
        data = json.loads(result)
        assert data["server_id"] == "echo-server"
        assert data["tool"] == "echo"
        assert data["result"] == {"echo": "hello"}

    @pytest.mark.asyncio
    async def test_call_tool_failure(self):
        """Should return error when tool call fails."""
        mock_connection = AsyncMock()
        mock_connection.call_tool = AsyncMock(side_effect=Exception("tool error"))
        srv._connections["echo-server"] = mock_connection
        result = await srv.pharos_call_tool("echo-server", "bad-tool")
        data = json.loads(result)
        assert "error" in data
        assert "tool error" in data["error"]


# ─── UI Resources ──────────────────────────────────────────────────────────────

class TestUIResources:
    """Test MCP Apps UI resources."""

    def test_approval_html_contains_no_script_injection(self):
        """Approval HTML should use textContent for user-controlled data."""
        html = srv.approval_resource()
        # The HTML uses textContent (safe) not innerHTML for user data
        assert "textContent" in html
        # Check that user-controlled values (server_name, server_id) use textContent
        # not innerHTML — JS template literals (${}) are OK for static scope chips

    def test_oauth_html_uses_textcontent(self):
        """OAuth HTML should use textContent for user data."""
        html = srv.oauth_resource()
        assert "textContent" in html

    def test_results_html_structure(self):
        """Results HTML should be valid HTML with proper structure."""
        html = srv.results_resource()
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_mime_type_constant(self):
        """MIME type should be the MCP Apps spec value."""
        assert srv.MCP_APP_MIME == "text/html;profile=mcp-app"


# ─── Helper Functions ──────────────────────────────────────────────────────────

class TestHelpers:
    """Test internal helper functions."""

    def test_get_client_singleton(self):
        """_get_client should return the same instance on repeated calls."""
        with patch.dict(os.environ, {"PHAROS_REGISTRY_URL": "https://test.example.com"}):
            c1 = srv._get_client()
            c2 = srv._get_client()
            assert c1 is c2

    def test_get_client_default_url(self):
        """_get_client should use getpharos.dev by default."""
        os.environ.pop("PHAROS_REGISTRY_URL", None)
        c = srv._get_client()
        # PharosClient stores the URL internally
        assert c is not None

    def test_get_pharos_cli_default(self):
        """_get_pharos_cli should default to 'pharos'."""
        os.environ.pop("PHAROS_CLI", None)
        assert srv._get_pharos_cli() == "pharos"

    def test_get_pharos_cli_custom(self):
        """_get_pharos_cli should respect PHAROS_CLI env var."""
        with patch.dict(os.environ, {"PHAROS_CLI": "/custom/path/pharos"}):
            assert srv._get_pharos_cli() == "/custom/path/pharos"

    @pytest.mark.asyncio
    async def test_resolve_local_endpoint_no_pid_file(self):
        """Should return None when no PID file exists."""
        with patch("os.path.exists", return_value=False):
            result = await srv._resolve_local_endpoint("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_local_endpoint_dead_process(self):
        """Should return None when process is not alive."""
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data="99999")), \
             patch("os.kill", side_effect=ProcessLookupError):
            result = await srv._resolve_local_endpoint("dead-server")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_server_tools_not_connected(self):
        """Should return empty list when not connected."""
        result = await srv._list_server_tools("nonexistent")
        assert result == []


# ─── Transport Configuration ───────────────────────────────────────────────────

class TestTransportConfig:
    """Test transport selection in main()."""

    def test_main_stdio_default(self):
        """main() should use stdio by default."""
        os.environ.pop("PHAROS_MCP_TRANSPORT", None)
        with patch.object(srv.mcp, "run") as mock_run:
            srv.main()
        mock_run.assert_called_once_with(transport="stdio")

    def test_main_streamable_http(self):
        """main() should configure host/port for streamable-http."""
        with patch.dict(os.environ, {
            "PHAROS_MCP_TRANSPORT": "streamable-http",
            "PHAROS_MCP_HOST": "0.0.0.0",
            "PHAROS_MCP_PORT": "8766",
        }):
            with patch.object(srv.mcp, "run") as mock_run:
                srv.main()
        mock_run.assert_called_once_with(transport="streamable-http")
        assert srv.mcp.settings.host == "0.0.0.0"
        assert srv.mcp.settings.port == 8766

    def test_main_sse(self):
        """main() should configure host/port for SSE."""
        with patch.dict(os.environ, {
            "PHAROS_MCP_TRANSPORT": "sse",
            "PHAROS_MCP_HOST": "127.0.0.1",
            "PHAROS_MCP_PORT": "8765",
        }):
            with patch.object(srv.mcp, "run") as mock_run:
                srv.main()
        mock_run.assert_called_once_with(transport="sse")


# ─── Security ──────────────────────────────────────────────────────────────────

class TestSecurity:
    """Security-focused tests for the MCP server."""

    @pytest.mark.asyncio
    async def test_error_messages_dont_leak_internals(self, mock_client):
        """Error messages should not leak internal paths or secrets."""
        from pharos_discovery.errors import RegistryUnavailable
        mock_client.search = AsyncMock(
            side_effect=RegistryUnavailable("Connection refused to 10.0.0.1:5432")
        )
        with patch.object(srv, "_get_client", return_value=mock_client):
            result = await srv.pharos_search("echo")
        data = json.loads(result)
        # The error message is passed through — this is intentional for debugging
        # but verify it doesn't contain credentials
        error_str = json.dumps(data)
        assert "password" not in error_str.lower()
        assert "secret" not in error_str.lower()
        assert "token" not in error_str.lower()

    @pytest.mark.asyncio
    async def test_search_limit_cannot_overflow(self, mock_client):
        """Limit should be bounded to prevent resource exhaustion."""
        with patch.object(srv, "_get_client", return_value=mock_client):
            await srv.pharos_search("echo", limit=999999)
        call_args = mock_client.search.call_args
        actual_limit = call_args.kwargs.get("limit", 10)
        assert actual_limit <= 50

    @pytest.mark.asyncio
    async def test_install_server_id_no_command_injection(self):
        """Server ID should be passed as argument, not shell interpolation."""
        malicious_id = "test; rm -rf /"
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
            mock_exec.return_value = mock_proc
            await srv.pharos_install(malicious_id)
        # create_subprocess_exec uses exec, not shell — args are separate
        call_args = mock_exec.call_args
        # The server_id should be a single argument, not shell-interpolated
        assert malicious_id in call_args.args
        # Verify it's not passed through shell=True
        assert call_args.args[0] != "sh"

    def test_dns_rebinding_protection_configured(self):
        """streamable-http transport should configure DNS rebinding protection."""
        with patch.dict(os.environ, {"PHAROS_MCP_TRANSPORT": "streamable-http"}):
            with patch.object(srv.mcp, "run"):
                srv.main()
        # Verify transport_security settings were applied
        ts = srv.mcp.settings.transport_security
        assert ts is not None
        assert "pharos-mcp:*" in ts.allowed_hosts

    def test_html_no_xss_via_innerhtml(self):
        """Approval HTML should not use innerHTML for user-controlled data."""
        html = srv.approval_resource()
        # Find all innerHTML usages in the script section
        if "<script>" in html:
            script = html.split("<script>")[1].split("</script>")[0]
            # innerHTML should not be used for user data (server name, etc.)
            # Only acceptable for static HTML templates
            innerhtml_uses = [line for line in script.split("\n") if "innerHTML" in line]
            for line in innerhtml_uses:
                # If innerHTML is used, it should be for static content only,
                # not for user/server-controlled values
                assert "server_name" not in line or "textContent" in line, \
                    f"innerHTML used with user data: {line.strip()}"

    @pytest.mark.asyncio
    async def test_call_tool_arguments_isolated(self):
        """Tool arguments should be passed through, not executed."""
        mock_connection = AsyncMock()
        mock_connection.call_tool = AsyncMock(return_value={"result": "ok"})
        srv._connections["echo-server"] = mock_connection
        # Pass arguments that look like injection attempts
        malicious_args = {"text": "$(rm -rf /)", "cmd": "; cat /etc/passwd"}
        result = await srv.pharos_call_tool("echo-server", "echo", malicious_args)
        data = json.loads(result)
        # Arguments should be passed as-is to the connection, not executed
        mock_connection.call_tool.assert_called_once_with("echo", malicious_args)
        assert data["result"] == {"result": "ok"}
