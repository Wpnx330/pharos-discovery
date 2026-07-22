import { describe, it, expect, vi, afterEach } from "vitest";
import { EventSubscriber, EVENT_TYPES, type SSEEvent } from "../../src/events/subscriber.js";

/** Build a ReadableStream from string chunks. */
function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

function makeEvent(event: string, data: string, id?: string): string {
  let out = "";
  if (id !== undefined) out += `id: ${id}\n`;
  out += `event: ${event}\n`;
  out += `data: ${data}\n`;
  out += "\n";
  return out;
}

/** Stub global fetch to return `body` (string) with status 200. */
function stubFetch(body: string, status = 200): void {
  globalThis.fetch = vi.fn(async () =>
    new Response(streamFromChunks([body]), {
      status,
      headers: { "Content-Type": "text/event-stream" },
    }),
  ) as unknown as typeof fetch;
}

/** Stub fetch with a custom handler. */
function stubFetchFn(fn: () => Promise<Response>): void {
  globalThis.fetch = vi.fn(fn) as unknown as typeof fetch;
}

const FAST_OPTS = { backoff: [999] }; // effectively no reconnect during tests

describe("EventSubscriber — constants", () => {
  it("exports 4 event types", () => {
    expect(EVENT_TYPES).toHaveLength(4);
    expect(EVENT_TYPES).toContain("package.published");
    expect(EVENT_TYPES).toContain("package.deprecated");
    expect(EVENT_TYPES).toContain("package.yanked");
    expect(EVENT_TYPES).toContain("advisory.published");
  });
});

describe("EventSubscriber — parsing", () => {
  afterEach(() => vi.restoreAllMocks());

  it("dispatches a single event", async () => {
    stubFetch(makeEvent("package.published", '{"id":"pkg-1"}'));
    const sub = new EventSubscriber("http://test/sse", FAST_OPTS);
    const received: SSEEvent[] = [];
    sub.on("package.published", (e) => { received.push(e); });
    await sub.connect();
    await vi.waitFor(() => expect(received.length).toBe(1));
    expect(received[0].event).toBe("package.published");
    expect(received[0].data).toBe('{"id":"pkg-1"}');
    await sub.disconnect();
  });

  it("ignores comment lines", async () => {
    stubFetch(": a comment\nevent: package.yanked\ndata: hi\n\n");
    const sub = new EventSubscriber("http://test/sse", FAST_OPTS);
    const received: SSEEvent[] = [];
    sub.on("package.yanked", (e) => { received.push(e); });
    await sub.connect();
    await vi.waitFor(() => expect(received.length).toBe(1));
    expect(received[0].data).toBe("hi");
    await sub.disconnect();
  });

  it("handles multi-line data", async () => {
    stubFetch("event: package.published\ndata: line1\ndata: line2\n\n");
    const sub = new EventSubscriber("http://test/sse", FAST_OPTS);
    const received: SSEEvent[] = [];
    sub.on("package.published", (e) => { received.push(e); });
    await sub.connect();
    await vi.waitFor(() => expect(received.length).toBe(1));
    expect(received[0].data).toBe("line1\nline2");
    await sub.disconnect();
  });

  it("captures id field", async () => {
    stubFetch("id: 99\nevent: advisory.published\ndata: x\n\n");
    const sub = new EventSubscriber("http://test/sse", FAST_OPTS);
    const received: SSEEvent[] = [];
    sub.on("advisory.published", (e) => { received.push(e); });
    await sub.connect();
    await vi.waitFor(() => expect(received.length).toBe(1));
    expect(received[0].id).toBe("99");
    await sub.disconnect();
  });

  it("default event type is message", async () => {
    stubFetch("data: hello\n\n");
    const sub = new EventSubscriber("http://test/sse", FAST_OPTS);
    const received: SSEEvent[] = [];
    sub.on("message", (e) => { received.push(e); });
    await sub.connect();
    await vi.waitFor(() => expect(received.length).toBe(1));
    expect(received[0].event).toBe("message");
    await sub.disconnect();
  });

  it("parses multiple events in one stream", async () => {
    stubFetch(
      makeEvent("package.published", "a") +
        makeEvent("package.yanked", "b") +
        makeEvent("advisory.published", "c"),
    );
    const sub = new EventSubscriber("http://test/sse", FAST_OPTS);
    const received: SSEEvent[] = [];
    sub.on("package.published", (e) => { received.push(e); });
    sub.on("package.yanked", (e) => { received.push(e); });
    sub.on("advisory.published", (e) => { received.push(e); });
    await sub.connect();
    await vi.waitFor(() => expect(received.length).toBe(3));
    expect(received.map((e) => e.event)).toEqual([
      "package.published",
      "package.yanked",
      "advisory.published",
    ]);
    await sub.disconnect();
  });
});

describe("EventSubscriber — callbacks", () => {
  afterEach(() => vi.restoreAllMocks());

  it("off removes callbacks", async () => {
    stubFetch(makeEvent("package.published", "x"));
    const sub = new EventSubscriber("http://test/sse", FAST_OPTS);
    const received: SSEEvent[] = [];
    const cb = (e: SSEEvent) => { received.push(e); };
    sub.on("package.published", cb);
    sub.off("package.published");
    await sub.connect();
    await new Promise((r) => setTimeout(r, 100));
    expect(received.length).toBe(0);
    await sub.disconnect();
  });

  it("wildcard callback receives all events", async () => {
    stubFetch(
      makeEvent("package.published", "a") + makeEvent("advisory.published", "b"),
    );
    const sub = new EventSubscriber("http://test/sse", FAST_OPTS);
    const received: SSEEvent[] = [];
    sub.on("*", (e) => { received.push(e); });
    await sub.connect();
    await vi.waitFor(() => expect(received.length).toBe(2));
    await sub.disconnect();
  });

  it("callback errors are swallowed", async () => {
    stubFetch(makeEvent("package.published", "x"));
    const sub = new EventSubscriber("http://test/sse", FAST_OPTS);
    sub.on("package.published", () => {
      throw new Error("boom");
    });
    await sub.connect();
    // Give it time to process; no throw should escape.
    await new Promise((r) => setTimeout(r, 100));
    await sub.disconnect();
  });

  it("offAll removes all callbacks", () => {
    const sub = new EventSubscriber("http://test/sse");
    sub.on("a", () => {});
    sub.on("b", () => {});
    sub.offAll();
    expect(sub.isConnected).toBe(false);
  });
});

describe("EventSubscriber — queue / polling", () => {
  afterEach(() => vi.restoreAllMocks());

  it("nextEvent drains queued event", async () => {
    stubFetch(makeEvent("package.published", "q1"));
    const sub = new EventSubscriber("http://test/sse", FAST_OPTS);
    await sub.connect();
    const ev = await sub.nextEvent(2000);
    expect(ev).not.toBeNull();
    expect(ev!.event).toBe("package.published");
    expect(ev!.data).toBe("q1");
    await sub.disconnect();
  });

  it("poll returns null when empty", () => {
    const sub = new EventSubscriber("http://test/sse");
    expect(sub.poll()).toBeNull();
  });

  it("nextEvent times out when no event", async () => {
    const sub = new EventSubscriber("http://test/sse");
    const ev = await sub.nextEvent(50);
    expect(ev).toBeNull();
  });
});

/** Build a ReadableStream that stays open (never closes) — for reconnect tests. */
function openStream(): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(enc.encode(makeEvent("package.published", "ok")));
      // intentionally do NOT close — stays open until reader.cancel()
    },
  });
}

describe("EventSubscriber — reconnection", () => {
  afterEach(() => vi.restoreAllMocks());

  it("retries on HTTP error then succeeds", async () => {
    let calls = 0;
    stubFetchFn(async () => {
      calls++;
      if (calls === 1) return new Response("", { status: 500 });
      return new Response(openStream(), {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    });

    const sub = new EventSubscriber("http://test/sse", {
      backoff: [0.01],
      maxRetries: 3,
    });
    const received: SSEEvent[] = [];
    sub.on("package.published", (e) => { received.push(e); });
    await sub.connect();
    await vi.waitFor(() => expect(received.length).toBe(1), { timeout: 3000 });
    expect(received[0].data).toBe("ok");
    await sub.disconnect();
  });

  it("stops after max retries", async () => {
    let calls = 0;
    stubFetchFn(async () => {
      calls++;
      return new Response("", { status: 500 });
    });

    const sub = new EventSubscriber("http://test/sse", {
      backoff: [0.01],
      maxRetries: 2,
    });
    await sub.connect();
    await vi.waitFor(() => expect(calls).toBeGreaterThanOrEqual(2), { timeout: 3000 });
    await sub.disconnect();
  });
});

describe("EventSubscriber — lifecycle", () => {
  afterEach(() => vi.restoreAllMocks());

  it("connect is idempotent", async () => {
    stubFetch(makeEvent("package.published", "x"));
    const sub = new EventSubscriber("http://test/sse", FAST_OPTS);
    await sub.connect();
    await sub.connect(); // should not double-run
    await sub.disconnect();
  });

  it("disconnect stops reconnection", async () => {
    let calls = 0;
    stubFetchFn(async () => {
      calls++;
      return new Response(openStream(), {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    });
    const sub = new EventSubscriber("http://test/sse", { backoff: [0.01] });
    await sub.connect();
    await vi.waitFor(() => expect(calls).toBeGreaterThanOrEqual(1), { timeout: 2000 });
    await sub.disconnect();
    const callsAfterDisconnect = calls;
    await new Promise((r) => setTimeout(r, 150));
    expect(calls).toBe(callsAfterDisconnect); // no more calls
  });
});
