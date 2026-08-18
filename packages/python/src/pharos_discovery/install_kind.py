"""Install-kind classifier (INSTALL_KINDS.md).

Pure functions only — no network, no eval. T2b should import this module
instead of re-implementing classification in ``mcp_server/server.py``.
"""

from __future__ import annotations

import os
from typing import Any, Literal

InstallKind = Literal[1, 2, 3]

KNOWN_INSTALL_RUNTIMES = frozenset({"npx", "uvx", "docker", "python", "binary"})
HTTP_TRANSPORTS = frozenset({
    "http",
    "http-sse",
    "http+sse",
    "sse",
    "streamable-http",
})
_REMOTE_ONLY_TRUTHY = frozenset({"true", "1", "yes"})
_LAUNCH_FIELD_NAMES = ("command", "bin", "stdio_command")


def classify_install_kind(card: Any) -> InstallKind | None:
    """Return 1, 2, 3, or ``None`` (not installable).

    Rules (tie-break: endpoint + bin is Kind 1):

    - http(s):// endpoint → Kind 1
    - HTTP/SSE transport + launch data → Kind 2
    - stdio (or empty, defaulting to stdio) + launch data → Kind 3
    """
    data = _as_mapping(card)
    if _is_http_endpoint(_field(data, "endpoint")):
        return 1

    transports = _transports(data)
    has_launch = _has_launch_data(data)

    if has_launch and any(t in HTTP_TRANSPORTS for t in transports):
        return 2

    stdio_or_default = (
        not transports
        or any(t == "stdio" for t in transports)
    )
    if has_launch and stdio_or_default:
        return 3

    return None


def remote_only_blocks(kind: InstallKind | None) -> bool:
    """True when ``PHAROS_REMOTE_ONLY`` is set and *kind* is 2 or 3.

    Truthy env values: ``true``, ``1``, ``yes`` (case-insensitive).
    ``PHAROS_MCP_APPS`` is a different flag and is ignored here.
    """
    if not _remote_only_enabled():
        return False
    return kind in (2, 3)


def launch_command(card: Any) -> str | None:
    """Map command / bin / runtime+package onto a stdio command string."""
    data = _as_mapping(card)
    explicit = _first_str(data, *_LAUNCH_FIELD_NAMES)
    if explicit:
        return explicit
    return _runtime_launch_line(data)


def _remote_only_enabled() -> bool:
    raw = os.environ.get("PHAROS_REMOTE_ONLY", "")
    return raw.strip().lower() in _REMOTE_ONLY_TRUTHY


def _as_mapping(card: Any) -> dict[str, Any]:
    if card is None:
        return {}
    if isinstance(card, dict):
        return card
    if hasattr(card, "model_dump") and callable(card.model_dump):
        dumped = card.model_dump()
        if isinstance(dumped, dict):
            extra: dict[str, Any] = dict(dumped)
            for name in (*_LAUNCH_FIELD_NAMES, "runtime", "package", "endpoint", "transport"):
                if _field(extra, name):
                    continue
                value = getattr(card, name, None)
                if value not in (None, "", [], ()):
                    extra[name] = value
            return extra
    if hasattr(card, "__dict__"):
        return dict(vars(card))
    return {}


def _field(data: dict[str, Any], name: str) -> Any:
    return data.get(name)


def _as_command(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        parts = [str(part).strip() for part in value if str(part).strip()]
        return " ".join(parts)
    return ""


def _first_str(data: dict[str, Any], *names: str) -> str:
    for name in names:
        text = _as_command(_field(data, name))
        if text:
            return text
    return ""


def _is_http_endpoint(raw: Any) -> bool:
    if not isinstance(raw, str):
        return False
    value = raw.strip().lower()
    return value.startswith("https://") or value.startswith("http://")


def _transports(data: dict[str, Any]) -> list[str]:
    raw = _field(data, "transport")
    if raw is None:
        raw = _field(data, "transports")
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = []
    return [str(item).strip().lower() for item in items if str(item).strip()]


def _has_launch_data(data: dict[str, Any]) -> bool:
    if _first_str(data, *_LAUNCH_FIELD_NAMES):
        return True
    runtime = _first_str(data, "runtime").lower()
    package = _first_str(data, "package")
    return runtime in KNOWN_INSTALL_RUNTIMES and bool(package)


def _runtime_launch_line(data: dict[str, Any]) -> str | None:
    runtime = _first_str(data, "runtime").lower()
    package = _first_str(data, "package")
    if runtime not in KNOWN_INSTALL_RUNTIMES:
        return None
    if runtime == "binary":
        path = _first_str(data, "bin") or package
        return path or None
    if not package:
        return None
    if runtime == "npx":
        return f"npx -y {package}"
    if runtime == "uvx":
        return f"uvx {package}"
    if runtime == "docker":
        return f"docker run -i --rm {package}"
    if runtime == "python":
        return f"python3 {package}"
    return None
