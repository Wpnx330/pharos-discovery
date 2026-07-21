"""Local fallback so ``@pytest.mark.anyio`` async tests run even when
pytest-anyio / pytest-asyncio are not installed (air-gapped environment).

When neither plugin is present, pytest would invoke the coroutine function,
receive an unawaited coroutine, and silently mark the test as "passed"
without ever executing the body. This shim detects such tests and drives them
to completion on a fresh event loop instead. If pytest-anyio is ever
installed, its handling takes precedence and this shim stays dormant.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "anyio: run async test via asyncio.run (local fallback shim)",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    """Replace the default call for async tests marked with `anyio`.

    Returning a non-None value from a first-result hook stops pytest from
    calling the test function itself (which would only produce a coroutine).
    """
    if config_plugin_active(item.config):
        return None

    marker = item.get_closest_marker("anyio")
    testfunction = getattr(item, "function", None)

    if (
        marker is None
        or testfunction is None
        or not inspect.iscoroutinefunction(testfunction)
    ):
        return None

    funcargs = {
        name: item.funcargs[name]
        for name in getattr(item, "fixturenames", [])
        if name in getattr(item, "funcargs", {})
    }
    asyncio.run(testfunction(**funcargs))
    return True  # truthy => call handled


def config_plugin_active(config) -> bool:
    """True if a real async test plugin is already driving anyio tests."""
    return config.pluginmanager.has_plugin("anyio") or bool(
        config.pluginmanager.get_plugin("asyncio")
    )
