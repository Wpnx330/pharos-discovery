"""
Entry point for the pharos-mcp command and `python -m pharos_mcp`.

This launches the PHAROS Discovery MCP Server, which exposes 5 tools
(pharos_search, pharos_install, pharos_connect, pharos_list_tools,
pharos_call_tool) over stdio/SSE/streamable-http.

The actual server implementation lives in pharos_discovery.mcp_server.
This package wraps it and ensures the bundled CLI binary is on PATH.
"""
import os
import sys
import shutil
import platform
from pathlib import Path


def _bundled_cli_path() -> Path | None:
    """Return the path to the bundled pharos CLI binary for this platform, or None."""
    pkg_dir = Path(__file__).parent / "binaries"
    system = platform.system().lower()
    machine = platform.machine().lower()

    # Map platform to binary name
    if system == "linux" and machine in ("x86_64", "amd64"):
        name = "pharos-linux-amd64"
    elif system == "darwin" and machine == "arm64":
        name = "pharos-darwin-arm64"
    elif system == "darwin" and machine in ("x86_64", "amd64"):
        name = "pharos-darwin-amd64"
    elif system == "windows" and machine in ("x86_64", "amd64"):
        name = "pharos-windows-amd64.exe"
    else:
        return None

    path = pkg_dir / name
    return path if path.exists() else None


def _ensure_cli_on_path():
    """Make the bundled CLI binary available as 'pharos' on PATH."""
    cli = _bundled_cli_path()
    if cli is None:
        return

    # Set PHAROS_CLI env var so the MCP server finds it
    os.environ.setdefault("PHAROS_CLI", str(cli))

    # Also copy/symlink to a directory on PATH (user-local)
    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "pharos"

    if not target.exists():
        try:
            # Symlink is preferred (no duplication)
            target.symlink_to(cli)
        except OSError:
            # Fall back to copy on platforms that don't support symlinks
            shutil.copy2(cli, target)
        # Ensure executable
        target.chmod(0o755)


def main():
    """Launch the PHAROS Discovery MCP Server."""
    _ensure_cli_on_path()

    # Import and run the actual MCP server
    # The server module lives in pharos_discovery.mcp_server
    try:
        from pharos_discovery.mcp_server.server import main as server_main
    except ImportError:
        print(
            "ERROR: pharos-discovery package not found.\n"
            "Install it with: pip install pharos-discovery\n"
            "Or install the full bundle: pip install pharos-mcp[full]",
            file=sys.stderr,
        )
        sys.exit(1)

    server_main()


if __name__ == "__main__":
    main()
