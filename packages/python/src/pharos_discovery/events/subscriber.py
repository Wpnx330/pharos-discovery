"""Server-Sent Events subscriber for the Pharos registry event stream.

Streams events of types ``package.published``, ``package.deprecated``,
``package.yanked`` and ``advisory.published`` over an SSE connection using
``httpx``.  Supports callback registration (``on``/``off``) and async-iteration
(``async for event in subscriber``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

# Known Pharos event types.
EVENT_TYPES: set[str] = {
    "package.published",
    "package.deprecated",
    "package.yanked",
    "advisory.published",
}

# Backoff schedule in seconds (capped at 30).
_BACKOFF_SCHEDULE: tuple[int, ...] = (1, 2, 4, 8, 16, 30)

Callback = Callable[["SSEEvent"], Awaitable[None] | None]


@dataclass
class SSEEvent:
    """A parsed SSE event."""

    event: str = "message"
    data: str = ""
    id: str | None = None
    retry: int | None = None

    def json(self) -> Any:
        """Decode ``data`` as JSON (returns raw string on failure)."""
        try:
            return json.loads(self.data)
        except (json.JSONDecodeError, ValueError):
            return self.data


class EventSubscriber:
    """Async SSE subscriber with auto-reconnect and backoff.

    Parameters
    ----------
    url:
        SSE endpoint URL.
    headers:
        Optional extra request headers.
    backoff_schedule:
        Optional override for the reconnection backoff schedule (seconds).
    max_retries:
        Maximum reconnection attempts before giving up (``None`` = infinite).
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        backoff_schedule: tuple[int, ...] | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.url = url
        self.headers: dict[str, str] = {"Accept": "text/event-stream"}
        if headers:
            self.headers.update(headers)
        self._backoff = backoff_schedule or _BACKOFF_SCHEDULE
        self._max_retries = max_retries
        self._callbacks: dict[str, list[Callback]] = {}
        self._connected = False
        self._should_reconnect = True
        self._client: httpx.AsyncClient | None = None
        self._response: httpx.Response | None = None
        self._event_queue: asyncio.Queue[SSEEvent] = asyncio.Queue()
        self._retry_count = 0
        self._last_event_id: str | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    # ----------------------------------------------------------- properties
    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------- callbacks
    def on(self, event_type: str, callback: Callback) -> None:
        """Register *callback* for *event_type* events."""
        self._callbacks.setdefault(event_type, []).append(callback)

    def off(self, event_type: str) -> None:
        """Remove all callbacks for *event_type*."""
        self._callbacks.pop(event_type, None)

    def off_all(self) -> None:
        """Remove every registered callback."""
        self._callbacks.clear()

    # ----------------------------------------------------------- lifecycle
    async def connect(self) -> None:
        """Open the SSE stream and begin reading in the background."""
        if self._connected:
            return
        self._should_reconnect = True
        self._stop_event.clear()
        self._reader_task = asyncio.create_task(self._run())

    async def disconnect(self) -> None:
        """Close the connection and stop reconnecting."""
        self._should_reconnect = False
        self._stop_event.set()
        await self._close_streams()
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        self._connected = False

    # ------------------------------------------------------------- internals
    async def _close_streams(self) -> None:
        if self._response is not None:
            await self._response.aclose()
            self._response = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _next_backoff(self) -> float:
        idx = min(self._retry_count, len(self._backoff) - 1)
        return float(self._backoff[idx])

    async def _run(self) -> None:
        """Main read loop with reconnection logic."""
        while self._should_reconnect:
            try:
                await self._connect_once()
                self._retry_count = 0
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.debug("SSE connection error: %s", exc)
                if not self._should_reconnect:
                    break
                self._retry_count += 1
                if self._max_retries is not None and self._retry_count > self._max_retries:
                    logger.warning("SSE max retries (%d) exceeded", self._max_retries)
                    break
                delay = self._next_backoff()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                    break  # stop signal received
                except asyncio.TimeoutError:
                    continue
            finally:
                await self._close_streams()
                self._connected = False

    async def _connect_once(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient()
        extra_headers: dict[str, str] = {}
        if self._last_event_id is not None:
            extra_headers["Last-Event-ID"] = self._last_event_id
        req_headers = {**self.headers, **extra_headers}
        # stream=True keeps the response body open for incremental reads.
        async with self._client.stream("GET", self.url, headers=req_headers) as response:
            self._response = response
            response.raise_for_status()
            self._connected = True
            await self._read_stream(response)
        self._response = None

    async def _read_stream(self, response: httpx.Response) -> None:
        """Parse the SSE byte stream into events."""
        event_fields: dict[str, str] = {}
        data_lines: list[str] = []

        async for raw_line in response.aiter_lines():
            if not self._should_reconnect:
                break
            line = raw_line.rstrip("\r\n")
            if line == "":
                # Blank line dispatches the accumulated event.
                if data_lines or "event" in event_fields:
                    event = self._build_event(event_fields, data_lines)
                    await self._dispatch(event)
                event_fields.clear()
                data_lines.clear()
                continue
            if line.startswith(":"):
                continue  # comment
            if ":" in line:
                field_name, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
            else:
                field_name, value = line, ""
            if field_name == "event":
                event_fields["event"] = value
            elif field_name == "data":
                data_lines.append(value)
            elif field_name == "id":
                event_fields["id"] = value
                self._last_event_id = value
            elif field_name == "retry":
                try:
                    event_fields["retry"] = str(int(value))
                except ValueError:
                    pass

    @staticmethod
    def _build_event(
        fields: dict[str, str], data_lines: list[str]
    ) -> SSEEvent:
        return SSEEvent(
            event=fields.get("event", "message"),
            data="\n".join(data_lines),
            id=fields.get("id"),
            retry=int(fields["retry"]) if "retry" in fields else None,
        )

    async def _dispatch(self, event: SSEEvent) -> None:
        """Queue the event for async iteration and fire callbacks."""
        await self._event_queue.put(event)
        handlers = list(self._callbacks.get(event.event, []))
        handlers += list(self._callbacks.get("*", []))  # wildcard
        for cb in handlers:
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.warning("SSE callback error for '%s': %s", event.event, exc)

    # -------------------------------------------------------- async iterator
    def __aiter__(self) -> "EventSubscriber":
        return self

    async def __anext__(self) -> SSEEvent:
        if not self._connected and self._event_queue.empty() and not self._should_reconnect:
            raise StopAsyncIteration
        try:
            return await self._event_queue.get()
        except asyncio.CancelledError:
            raise StopAsyncIteration from None
