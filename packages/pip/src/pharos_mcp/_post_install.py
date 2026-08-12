"""
Post-install hook for pharos-mcp.

After pip install, this script:
1. Makes the bundled CLI binary executable
2. Detects installed MCP clients (Cursor, Claude Desktop, VS Code)
3. Offers to configure PHAROS MCP for each detected client

This runs automatically via pip's post-install mechanism.
"""
import os
import sys
import json
import platform
from pathlib import Path


def _claude_config_path() -> Path:
    """Return the Claude Desktop config path for this platform."""
    system = platform.system().lower()
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "windows":
        return Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    else:
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


# Known MCP client config locations
CLIENT_CONFIGS = {
    "cursor": {
        "path": Path.home() / ".cursor" / "mcp.json",
        "label": "Cursor",
    },
    "claude-desktop": {
        "path": _claude_config_path(),
        "label": "Claude Desktop",
    },
    "vscode": {
        "path": Path.home() / ".vscode" / "settings.json",
        "label": "VS Code",
    },
}


def _make_cli_executable():
    """Ensure the bundled CLI binary is executable."""
    pkg_dir = Path(__file__).parent / "binaries"
    if not pkg_dir.exists():
        return
    for f in pkg_dir.iterdir():
        if f.is_file() and not f.name.endswith(".py"):
            try:
                f.chmod(0o755)
            except OSError:
                pass


def _detect_clients() -> list[tuple[str, str, Path]]:
    """Detect installed MCP clients. Returns list of (id, label, config_path)."""
    detected = []
    for cid, info in CLIENT_CONFIGS.items():
        if info["path"].parent.exists():
            detected.append((cid, info["label"], info["path"]))
    return detected


def _write_cursor_config(config_path: Path):
    """Write PHAROS MCP config for Cursor."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    servers = existing.get("mcpServers", {})
    servers["pharos"] = {
        "command": "pharos-mcp",
        "env": {
            "PHAROS_REGISTRY_URL": "https://api.getpharos.dev",
        },
    }
    existing["mcpServers"] = servers
    config_path.write_text(json.dumps(existing, indent=2))
    return config_path


def _write_claude_config(config_path: Path):
    """Write PHAROS MCP config for Claude Desktop."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    servers = existing.get("mcpServers", {})
    servers["pharos"] = {
        "command": "pharos-mcp",
        "env": {
            "PHAROS_REGISTRY_URL": "https://api.getpharos.dev",
        },
    }
    existing["mcpServers"] = servers
    config_path.write_text(json.dumps(existing, indent=2))
    return config_path


def run():
    """Run the post-install configuration."""
    _make_cli_executable()

    detected = _detect_clients()
    if not detected:
        print("\n✓ PHAROS MCP installed. No MCP clients detected for auto-configuration.")
        print("  Run 'pharos-mcp' to start the server, or add it manually to your client config.")
        return

    # In non-interactive mode (pip install), auto-configure all detected clients.
    # Users can remove entries later if they don't want them.
    print(f"\n✓ PHAROS MCP installed. {len(detected)} MCP client(s) detected:")
    for cid, label, _ in detected:
        print(f"  - {label}")

    if not sys.stdin.isatty():
        # Non-interactive: auto-configure all
        for cid, label, path in detected:
            try:
                if cid == "cursor":
                    _write_cursor_config(path)
                elif cid == "claude-desktop":
                    _write_claude_config(path)
                print(f"  ✓ Configured PHAROS MCP for {label}")
            except Exception as e:
                print(f"  ✗ Failed to configure {label}: {e}", file=sys.stderr)
    else:
        # Interactive: ask for each
        for cid, label, path in detected:
            try:
                response = input(f"\nConfigure PHAROS MCP for {label}? (Y/n): ").strip().lower()
                if response in ("", "y", "yes"):
                    if cid == "cursor":
                        _write_cursor_config(path)
                    elif cid == "claude-desktop":
                        _write_claude_config(path)
                    print(f"  ✓ Configured {label}")
                else:
                    print(f"  - Skipped {label}")
            except (EOFError, KeyboardInterrupt):
                break

    print("\nDone! You can now use PHAROS discovery tools in your MCP client.")


if __name__ == "__main__":
    run()
