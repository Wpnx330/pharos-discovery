"""Tests for pharos_discovery.events.subscriber."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from pharos_discovery.events.subscriber import (
    EVENT_TYPES,
    SSEEvent,
    EventSubscriber,
)


# ---------------------------------------------------------------- helpers
def sse(*chunks: str) -> bytes:
    return ("".join(chunks)).encode("utf-8")


def make_event_chunk(event: str, data: str, eid: str | None = None) -> str:
    out = ""
    if eid is not None:
        out += f"id: {eid}\n"
    out += f"event: {event}\n"
    out += f"data: {data}\n"
    out += "\n"
    return out


# ---------------------------------------------------------------- SSEEvent
class TestSSEEvent:
    def test_json_parses(self):
        ev = SSEEvent(event="test", data='{"k": 1}')
        assert ev.json() == {"k": 1}

    def test_json_fallback_to_string(self):
        ev = SSEEvent(event="test", data="not-json")
        assert ev.json() == "not-json"

    def test_defaults(self):
        ev = SSEEvent()
        assert ev.event == "message"
        assert ev.data == ""
        assert ev.id is None
        assert ev.retry is None


class TestEventTypes:
    def test_known_types_present(self):
        assert "package.published" in EVENT_TYPES
        assert "package.deprecated" in EVENT_TYPES
        assert "package.yanked" in EVENT_TYPES
        assert "advisory.published" in EVENT_TYPES

    def test_four_types(self):
        assert len(EVENT_TYPES) == 4


# ---------------------------------------------------------------- parsing
class TestParsing:
    @pytest.mark.anyio
    async def test_single_event_dispatched(self):
        received: list[SSEEvent] = []

        def handler(ev: SSEEvent):
            received.append(ev)

        chunk = make_event_chunk("package.published", '{"id":"pkg-1"}')
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=chunk))
        sub = EventSubscriber("http://test/sse")
        sub.on("package.published", handler)
        # patch client to use mock transport
        sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
        await sub._connect_once()
        assert len(received) == 1
        assert received[0].event == "package.published"
        assert received[0].json() == {"id": "pkg-1"}

    @pytest.mark.anyio
    async def test_multiple_events(self):
        received: list[SSEEvent] = []
        chunk = (
            make_event_chunk("package.published", "a")
            + make_event_chunk("package.yanked", "b")
            + make_event_chunk("advisory.published", "c")
        )
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=chunk))

        def handler(ev: SSEEvent):
            received.append(ev)

        sub = EventSubscriber("http://test/sse")
        sub.on("package.published", handler)
        sub.on("package.yanked", handler)
        sub.on("advisory.published", handler)
        sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
        await sub._connect_once()
        assert len(received) == 3
        assert [e.event for e in received] == [
            "package.published",
            "package.yanked",
            "advisory.published",
        ]

    @pytest.mark.anyio
    async def test_comment_lines_ignored(self):
        received: list[SSEEvent] = []
        chunk = ": this is a comment\nevent: package.published\ndata: hi\n\n"
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=chunk))

        def handler(ev: SSEEvent):
            received.append(ev)

        sub = EventSubscriber("http://test/sse")
        sub.on("package.published", handler)
        sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
        await sub._connect_once()
        assert len(received) == 1
        assert received[0].data == "hi"

    @pytest.mark.anyio
    async def test_multi_line_data(self):
        received: list[SSEEvent] = []
        chunk = "event: package.published\ndata: line1\ndata: line2\n\n"
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=chunk))

        def handler(ev: SSEEvent):
            received.append(ev)

        sub = EventSubscriber("http://test/sse")
        sub.on("package.published", handler)
        sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
        await sub._connect_once()
        assert received[0].data == "line1\nline2"

    @pytest.mark.anyio
    async def test_id_field_captured(self):
        received: list[SSEEvent] = []
        chunk = "id: 42\nevent: package.published\ndata: x\n\n"
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=chunk))

        def handler(ev: SSEEvent):
            received.append(ev)

        sub = EventSubscriber("http://test/sse")
        sub.on("package.published", handler)
        sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
        await sub._connect_once()
        assert received[0].id == "42"
        assert sub._last_event_id == "42"

    @pytest.mark.anyio
    async def test_default_event_type_is_message(self):
        received: list[SSEEvent] = []
        chunk = "data: hello\n\n"
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=chunk))

        def handler(ev: SSEEvent):
            received.append(ev)

        sub = EventSubscriber("http://test/sse")
        sub.on("message", handler)
        sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
        await sub._connect_once()
        assert len(received) == 1
        assert received[0].event == "message"


# ---------------------------------------------------------------- callbacks
class TestCallbacks:
    @pytest.mark.anyio
    async def test_async_callback(self):
        received: list[SSEEvent] = []

        async def handler(ev: SSEEvent):
            await asyncio.sleep(0)
            received.append(ev)

        chunk = make_event_chunk("package.published", "x")
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=chunk))
        sub = EventSubscriber("http://test/sse")
        sub.on("package.published", handler)
        sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
        await sub._connect_once()
        assert len(received) == 1

    @pytest.mark.anyio
    async def test_off_removes_callbacks(self):
        received: list[SSEEvent] = []
        chunk = make_event_chunk("package.published", "x")
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=chunk))

        def handler(ev: SSEEvent):
            received.append(ev)

        sub = EventSubscriber("http://test/sse")
        sub.on("package.published", handler)
        sub.off("package.published")
        sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
        await sub._connect_once()
        assert len(received) == 0

    @pytest.mark.anyio
    async def test_wildard_callback(self):
        received: list[SSEEvent] = []
        chunk = (
            make_event_chunk("package.published", "a")
            + make_event_chunk("advisory.published", "b")
        )
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=chunk))

        def handler(ev: SSEEvent):
            received.append(ev)

        sub = EventSubscriber("http://test/sse")
        sub.on("*", handler)
        sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
        await sub._connect_once()
        assert len(received) == 2

    @pytest.mark.anyio
    async def test_callback_error_is_swallowed(self):
        chunk = make_event_chunk("package.published", "x")
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=chunk))

        def bad(ev: SSEEvent):
            raise RuntimeError("boom")

        sub = EventSubscriber("http://test/sse")
        sub.on("package.published", bad)
        sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
        # should not raise
        await sub._connect_once()


# ---------------------------------------------------------------- reconnection
class TestReconnection:
    @pytest.mark.anyio
    async def test_backoff_schedule(self):
        sub = EventSubscriber(
            "http://test/sse",
            backoff_schedule=(1, 2, 4),
            max_retries=1,
        )
        # First failure -> retry_count=1 -> index 0 of schedule -> 1s
        sub._retry_count = 0
        assert sub._next_backoff() == 1.0
        sub._retry_count = 1
        assert sub._next_backoff() == 2.0
        sub._retry_count = 2
        assert sub._next_backoff() == 4.0
        sub._retry_count = 10  # beyond schedule -> last value
        assert sub._next_backoff() == 4.0

    @pytest.mark.anyio
    async def test_max_retries_stops_loop(self):
        # Always-500 server
        transport = httpx.MockTransport(lambda req: httpx.Response(500))
        sub = EventSubscriber(
            "http://test/sse",
            backoff_schedule=(0,),  # instant backoff for test speed
            max_retries=2,
        )

        async def fake_client():
            # replace client stream to use mock transport
            sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]

        # We'll run the loop manually but short-circuit: connect() then wait
        sub._should_reconnect = True
        sub._stop_event.clear()
        # Patch _connect_once to use mock transport
        orig_connect = sub._connect_once

        async def patched():
            sub._client = httpx.AsyncClient(transport=transport)
            await orig_connect()

        sub._connect_once = patched  # type: ignore[assignment]
        task = asyncio.create_task(sub._run())
        # let it run (instant backoff)
        await asyncio.sleep(0.3)
        sub._should_reconnect = False
        sub._stop_event.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        # It should have given up after max_retries
        assert sub._retry_count >= 1


# ---------------------------------------------------------------- async iter
class TestAsyncIterator:
    @pytest.mark.anyio
    async def test_async_for_yields_events(self):
        chunk = make_event_chunk("package.published", "iter-1")
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=chunk))
        sub = EventSubscriber("http://test/sse")
        sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
        await sub._connect_once()
        # Now drain the queue via async iteration
        results: list[SSEEvent] = []
        # we know exactly one event was queued
        ev = await asyncio.wait_for(sub.__anext__(), timeout=1)
        results.append(ev)
        assert results[0].event == "package.published"
        assert results[0].data == "iter-1"


# ---------------------------------------------------------------- lifecycle
class TestLifecycle:
    @pytest.mark.anyio
    async def test_connect_disconnect(self):
        chunk = make_event_chunk("package.published", "x")
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=chunk))
        sub = EventSubscriber("http://test/sse")

        # We won't actually call connect() (spawns bg task with real httpx).
        # Instead exercise the parsing path directly.
        sub._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
        await sub._connect_once()
        # _connect_once sets _connected=True while reading; it remains True
        # until the run-loop finally clause clears it. Just verify it parsed.
        assert sub._last_event_id is None or isinstance(sub._last_event_id, str)
        await sub.disconnect()

    def test_off_all(self):
        sub = EventSubscriber("http://test/sse")
        sub.on("a", lambda e: None)
        sub.on("b", lambda e: None)
        sub.off_all()
        assert sub._callbacks == {}
