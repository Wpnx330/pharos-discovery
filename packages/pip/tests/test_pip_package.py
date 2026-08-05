"""
Unit tests for the pharos-mcp pip package.

Tests cover:
- _bundled_cli_path() platform detection
- _ensure_cli_on_path() symlink/copy logic
- _post_install client detection and config writing
"""
import os
import sys
import json
import platform
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# Import the package
from pharos_mcp.__main__ import _bundled_cli_path, _ensure_cli_on_path
from pharos_mcp import _post_install


class TestBundledCliPath:
    """Tests for _bundled_cli_path platform detection."""

    def test_returns_path_object(self):
        """_bundled_cli_path returns a Path or None, never crashes."""
        result = _bundled_cli_path()
        assert result is None or isinstance(result, Path)

    @patch("platform.system", return_value="Linux")
    @patch("platform.machine", return_value="x86_64")
    def test_linux_amd64(self, mock_machine, mock_system):
        """Linux x86_64 should look for pharos-linux-amd64."""
        with patch.object(Path, "exists", return_value=True):
            result = _bundled_cli_path()
            assert result is not None
            assert "pharos-linux-amd64" in str(result)

    @patch("platform.system", return_value="Darwin")
    @patch("platform.machine", return_value="arm64")
    def test_darwin_arm64(self, mock_machine, mock_system):
        """macOS Apple Silicon should look for pharos-darwin-arm64."""
        with patch.object(Path, "exists", return_value=True):
            result = _bundled_cli_path()
            assert result is not None
            assert "pharos-darwin-arm64" in str(result)

    @patch("platform.system", return_value="Windows")
    @patch("platform.machine", return_value="amd64")
    def test_windows_amd64(self, mock_machine, mock_system):
        """Windows x86_64 should look for pharos-windows-amd64.exe."""
        with patch.object(Path, "exists", return_value=True):
            result = _bundled_cli_path()
            assert result is not None
            assert "pharos-windows-amd64.exe" in str(result)

    @patch("platform.system", return_value="Linux")
    @patch("platform.machine", return_value="aarch64")
    def test_unsupported_platform_returns_none(self, mock_machine, mock_system):
        """Unsupported platform should return None."""
        result = _bundled_cli_path()
        assert result is None


class TestEnsureCliOnPath:
    """Tests for _ensure_cli_on_path."""

    def test_no_binary_does_nothing(self, tmp_path):
        """When no bundled binary exists, function returns without error."""
        with patch("pharos_mcp.__main__._bundled_cli_path", return_value=None):
            # Should not raise
            _ensure_cli_on_path()

    def test_sets_pharos_cli_env(self, tmp_path):
        """When binary exists, PHAROS_CLI env var is set."""
        fake_bin = tmp_path / "pharos-linux-amd64"
        fake_bin.write_text("#!/bin/sh\necho pharos")
        fake_bin.chmod(0o755)

        old_env = os.environ.pop("PHAROS_CLI", None)
        try:
            with patch("pharos_mcp.__main__._bundled_cli_path", return_value=fake_bin):
                with patch.object(Path, "symlink_to"):
                    with patch.object(Path, "exists", return_value=False):
                        _ensure_cli_on_path()
                        assert os.environ.get("PHAROS_CLI") == str(fake_bin)
        finally:
            if old_env is not None:
                os.environ["PHAROS_CLI"] = old_env
            else:
                os.environ.pop("PHAROS_CLI", None)


class TestPostInstallDetection:
    """Tests for _post_install client detection."""

    def test_detect_clients_empty(self, tmp_path):
        """No client directories → empty list."""
        with patch.dict(_post_install.CLIENT_CONFIGS, {
            "cursor": {"path": tmp_path / "nonexistent" / "mcp.json", "label": "Cursor"},
            "claude-desktop": {"path": tmp_path / "nonexistent2" / "config.json", "label": "Claude"},
            "vscode": {"path": tmp_path / "nonexistent3" / "settings.json", "label": "VS Code"},
        }):
            detected = _post_install._detect_clients()
            assert detected == []

    def test_detect_clients_cursor(self, tmp_path):
        """Cursor directory existing → detected."""
        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        cursor_path = cursor_dir / "mcp.json"

        with patch.dict(_post_install.CLIENT_CONFIGS, {
            "cursor": {"path": cursor_path, "label": "Cursor"},
            "claude-desktop": {"path": tmp_path / "nope" / "c.json", "label": "Claude"},
            "vscode": {"path": tmp_path / "nope2" / "s.json", "label": "VS Code"},
        }):
            detected = _post_install._detect_clients()
            assert len(detected) == 1
            assert detected[0][0] == "cursor"
            assert detected[0][1] == "Cursor"


class TestPostInstallWriteConfig:
    """Tests for config file writing."""

    def test_write_cursor_config_creates_file(self, tmp_path):
        """Writing Cursor config creates the file with pharos entry."""
        config_path = tmp_path / ".cursor" / "mcp.json"
        _post_install._write_cursor_config(config_path)

        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert "mcpServers" in data
        assert "pharos" in data["mcpServers"]
        assert data["mcpServers"]["pharos"]["command"] == "pharos-mcp"

    def test_write_cursor_config_preserves_existing(self, tmp_path):
        """Writing Cursor config preserves existing server entries."""
        config_path = tmp_path / ".cursor" / "mcp.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(json.dumps({
            "mcpServers": {
                "existing-server": {"command": "node", "args": ["server.js"]}
            }
        }))

        _post_install._write_cursor_config(config_path)

        data = json.loads(config_path.read_text())
        assert "existing-server" in data["mcpServers"]
        assert "pharos" in data["mcpServers"]

    def test_write_claude_config_creates_file(self, tmp_path):
        """Writing Claude Desktop config creates the file with pharos entry."""
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        _post_install._write_claude_config(config_path)

        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert "mcpServers" in data
        assert "pharos" in data["mcpServers"]

    def test_write_claude_config_preserves_existing(self, tmp_path):
        """Writing Claude config preserves existing entries."""
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(json.dumps({
            "mcpServers": {
                "other-mcp": {"command": "npx", "args": ["other"]}
            }
        }))

        _post_install._write_claude_config(config_path)

        data = json.loads(config_path.read_text())
        assert "other-mcp" in data["mcpServers"]
        assert "pharos" in data["mcpServers"]


class TestPostInstallRun:
    """Tests for the post-install run() entry point."""

    def test_run_no_clients(self, capsys, tmp_path):
        """run() with no detected clients prints info message."""
        with patch.object(_post_install, "_detect_clients", return_value=[]):
            with patch.object(_post_install, "_make_cli_executable"):
                _post_install.run()
                captured = capsys.readouterr()
                assert "installed" in captured.out.lower()

    def test_run_non_interactive_auto_configures(self, capsys, tmp_path):
        """run() in non-interactive mode auto-configures all detected clients."""
        config_path = tmp_path / ".cursor" / "mcp.json"
        detected = [("cursor", "Cursor", config_path)]

        with patch.object(_post_install, "_detect_clients", return_value=detected):
            with patch.object(_post_install, "_make_cli_executable"):
                with patch.object(sys.stdin, "isatty", return_value=False):
                    with patch.object(_post_install, "_write_cursor_config") as mock_write:
                        _post_install.run()
                        mock_write.assert_called_once_with(config_path)


class TestSecurity:
    """Security tests for the pip package."""

    def test_no_command_injection_in_config(self, tmp_path):
        """Config writing doesn't allow injection via server names."""
        config_path = tmp_path / ".cursor" / "mcp.json"
        _post_install._write_cursor_config(config_path)

        data = json.loads(config_path.read_text())
        # The command should be exactly "pharos-mcp", nothing injected
        assert data["mcpServers"]["pharos"]["command"] == "pharos-mcp"
        # No shell commands in the config
        config_text = config_path.read_text()
        assert "&&" not in config_text
        assert "|" not in config_text
        assert ";" not in config_text

    def test_no_arbitrary_file_write(self, tmp_path):
        """Config is only written to known client paths, not arbitrary paths."""
        # This is enforced by the CLIENT_CONFIGS dict — paths are hardcoded
        for cid, info in _post_install.CLIENT_CONFIGS.items():
            # All paths must be under the user's home directory
            assert str(info["path"]).startswith(str(Path.home())), \
                f"Client {cid} config path is outside home directory"
