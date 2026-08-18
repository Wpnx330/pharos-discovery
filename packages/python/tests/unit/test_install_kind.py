"""Install-kind classifier — fixtures F1–F7 must match Go / INSTALL_KINDS.md."""

from __future__ import annotations

import inspect

import pytest

from pharos_discovery.install_kind import (
    classify_install_kind,
    launch_command,
    remote_only_blocks,
)
from pharos_discovery.models.server_card import AuthSpec, Publisher, ServerCard


def _publisher() -> Publisher:
    return Publisher(id="did:web:example.com", name="TestPub")


def _auth() -> AuthSpec:
    return AuthSpec(type="none")


def _server_card(**overrides) -> ServerCard:
    defaults = {
        "id": "urn:pharos:server-001",
        "display_name": "Test Server",
        "description": "fixture",
        "publisher": _publisher(),
        "version": "1.0.0",
        "transport": ["stdio"],
        "capabilities": ["echo"],
        "tools_count": 1,
        "auth": _auth(),
        "availability": "native",
        "source_registry": "https://registry.pharos.dev",
        "published_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
        "status": "active",
    }
    defaults.update(overrides)
    return ServerCard(**defaults)


# ---------------------------------------------------------------------------
# Shared fixtures (F1–F6) — dicts match raw registry / Go test cards
# ---------------------------------------------------------------------------

F1_STREAMABLE_REMOTE = {
    "id": "com.invokera/world-time",
    "transport": "streamable-http",
    "endpoint": "https://world-time.example/mcp",
}

F2_ECHO_0_2_5 = {
    "id": "test-echo-server",
    "version": "0.2.5",
    "transport": "http-sse",
    "endpoint": "http://host.docker.internal:8765",
    "bin": "npx -y test-echo-server",
}

F3_ECHO_0_2_4 = {
    "id": "test-echo-server",
    "version": "0.2.4",
    "transport": "http-sse",
    "bin": "npx -y test-echo-server",
}

F4_STDIO_TARBALL = {
    "id": "ev4nv-models",
    "transport": "stdio",
    "availability": "native",
    "bin": "./mcp-server",
    "tarball": "https://registry.example/ev4nv-models-1.0.0.tgz",
}

F5_NPX_NO_TARBALL = {
    "id": "io.mcp/npx-server",
    "transport": "stdio",
    "command": "npx -y @scope/mcp-server",
}

F5_RUNTIME_PACKAGE = {
    "id": "io.mcp/npx-server",
    "transport": "stdio",
    "runtime": "npx",
    "package": "@scope/mcp-server",
}

F6_TRANSPORT_ONLY = {
    "id": "broken.server",
    "transport": "streamable-http",
}


FIXTURE_KIND_CASES = [
    ("F1", F1_STREAMABLE_REMOTE, 1),
    ("F2", F2_ECHO_0_2_5, 1),
    ("F3", F3_ECHO_0_2_4, 2),
    ("F4", F4_STDIO_TARBALL, 3),
    ("F5", F5_NPX_NO_TARBALL, 3),
    ("F5-runtime", F5_RUNTIME_PACKAGE, 3),
    ("F6", F6_TRANSPORT_ONLY, None),
]


@pytest.mark.parametrize("fid,card,expected", FIXTURE_KIND_CASES)
def test_classify_fixtures_f1_f6(fid: str, card: dict, expected: int | None) -> None:
    assert classify_install_kind(card) == expected, fid


def test_f2_endpoint_plus_bin_tiebreak_is_kind_1() -> None:
    """test-echo 0.2.5: endpoint + bin is Kind 1, not Kind 2."""
    assert classify_install_kind(F2_ECHO_0_2_5) == 1
    assert classify_install_kind(F2_ECHO_0_2_5) != 2


def test_classify_accepts_server_card() -> None:
    card = _server_card(
        id="com.invokera/world-time",
        transport=["streamable-http"],
        endpoint="https://world-time.example/mcp",
        stdio_command=None,
    )
    assert classify_install_kind(card) == 1
    assert card.install_kind is None


def test_classify_server_card_stdio_command_is_kind_3() -> None:
    card = _server_card(transport=["stdio"], stdio_command="npx -y @demo/echo")
    assert classify_install_kind(card) == 3


def test_classify_http_plus_sse_alias_with_bin_is_kind_2() -> None:
    assert (
        classify_install_kind({"transport": "http+sse", "bin": "npx -y echo"}) == 2
    )


def test_classify_empty_transport_defaults_to_stdio() -> None:
    assert classify_install_kind({"bin": "./server"}) == 3
    assert classify_install_kind({"transport": [], "command": "uvx foo"}) == 3


def test_classify_https_endpoint_without_transport_is_kind_1() -> None:
    assert classify_install_kind({"endpoint": "HTTPS://api.example/mcp"}) == 1


def test_classify_non_http_endpoint_is_not_kind_1() -> None:
    assert classify_install_kind({"endpoint": "ftp://files.example/mcp", "bin": "x"}) == 3


def test_server_card_install_kind_field_optional() -> None:
    card = _server_card()
    assert card.install_kind is None
    tagged = _server_card(install_kind=2)
    assert tagged.install_kind == 2


# ---------------------------------------------------------------------------
# F7 — PHAROS_REMOTE_ONLY rejects kinds 2 and 3
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,enabled",
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("TRUE", True),
        ("Yes", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("", False),
    ],
)
def test_remote_only_blocks_env_values(
    raw: str, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHAROS_REMOTE_ONLY", raw)
    assert remote_only_blocks(2) is enabled
    assert remote_only_blocks(3) is enabled
    assert remote_only_blocks(1) is False
    assert remote_only_blocks(None) is False


def test_remote_only_unset_allows_all_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHAROS_REMOTE_ONLY", raising=False)
    assert remote_only_blocks(1) is False
    assert remote_only_blocks(2) is False
    assert remote_only_blocks(3) is False


def test_f7_remote_only_rejects_f3_and_f4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHAROS_REMOTE_ONLY", "true")
    assert classify_install_kind(F3_ECHO_0_2_4) == 2
    assert classify_install_kind(F4_STDIO_TARBALL) == 3
    assert remote_only_blocks(classify_install_kind(F3_ECHO_0_2_4)) is True
    assert remote_only_blocks(classify_install_kind(F4_STDIO_TARBALL)) is True
    assert remote_only_blocks(classify_install_kind(F1_STREAMABLE_REMOTE)) is False


def test_mcp_apps_flag_does_not_enable_remote_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PHAROS_REMOTE_ONLY", raising=False)
    monkeypatch.setenv("PHAROS_MCP_APPS", "true")
    assert remote_only_blocks(2) is False
    assert remote_only_blocks(3) is False


# ---------------------------------------------------------------------------
# launch_command — command / bin / runtime+package → stdio string
# ---------------------------------------------------------------------------

def test_launch_command_prefers_command_over_bin() -> None:
    assert (
        launch_command({"command": "python -m src.server", "bin": "ignored"})
        == "python -m src.server"
    )


def test_launch_command_uses_bin() -> None:
    assert launch_command({"bin": ["npx", "-y", "@demo/weather"]}) == "npx -y @demo/weather"


def test_launch_command_uses_stdio_command_on_card() -> None:
    card = _server_card(stdio_command="uvx mcp-server-git")
    assert launch_command(card) == "uvx mcp-server-git"


@pytest.mark.parametrize(
    "runtime,package,expected",
    [
        ("npx", "@scope/server", "npx -y @scope/server"),
        ("uvx", "mcp-server-git", "uvx mcp-server-git"),
        ("docker", "myimg:latest", "docker run -i --rm myimg:latest"),
        ("python", "src.server", "python3 src.server"),
        ("binary", "bin/server", "bin/server"),
    ],
)
def test_launch_command_runtime_package(
    runtime: str, package: str, expected: str
) -> None:
    assert launch_command({"runtime": runtime, "package": package}) == expected


def test_launch_command_binary_prefers_bin_path() -> None:
    assert (
        launch_command({"runtime": "binary", "package": "pkg", "bin": "bin/server"})
        == "bin/server"
    )


def test_launch_command_none_without_launch_data() -> None:
    assert launch_command(F1_STREAMABLE_REMOTE) is None
    assert launch_command(F6_TRANSPORT_ONLY) is None


def test_launch_command_f5_matches_npx_line() -> None:
    assert launch_command(F5_NPX_NO_TARBALL) == "npx -y @scope/mcp-server"
    assert launch_command(F5_RUNTIME_PACKAGE) == "npx -y @scope/mcp-server"


# ---------------------------------------------------------------------------
# Security: classifier is pure — no I/O, no eval
# ---------------------------------------------------------------------------

def test_classifier_source_is_pure() -> None:
    source = inspect.getsource(classify_install_kind)
    source += inspect.getsource(launch_command)
    source += inspect.getsource(remote_only_blocks)
    forbidden = ("httpx", "urllib", "requests", "eval(", "exec(", "urlopen")
    for token in forbidden:
        assert token not in source, token
