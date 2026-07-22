/**
 * Server-Sent Events subscriber for the Pharos registry event stream.
 *
 * Uses `fetch` + `ReadableStream` (available in Node 18+ and browsers).
 * Auto-reconnects with exponential backoff.  Dispatches parsed events to
 * registered callbacks via a simple EventEmitter pattern.
 */

export const EVENT_TYPES = [
  "package.published",
  "package.deprecated",
  "package.yanked",
  "advisory.published",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

const DEFAULT_BACKOFF = [1, 2, 4, 8, 16, 30] as const;
const MAX_BACKOFF = 30;

export interface SSEEvent {
  event: string;
  data: string;
  id?: string;
  retry?: number;
}

type Callback = (event: SSEEvent) => void | Promise<void>;

function parseJSON(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return data;
  }
}

/** Extend SSEEvent with a JSON helper. */
export interface ParsedEvent extends SSEEvent {
  json(): unknown;
}

function makeEvent(e: SSEEvent): ParsedEvent {
  return {
    ...e,
    json: () => parseJSON(e.data),
  };
}

export class EventSubscriber {
  readonly url: string;
  private headers: Record<string, string>;
  private backoff: readonly number[];
  private maxRetries: number | undefined;
  private callbacks = new Map<string, Callback[]>();
  private connected = false;
  private shouldReconnect = true;
  private retryCount = 0;
  private lastEventId: string | undefined;
  private abortController: AbortController | null = null;
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  private runPromise: Promise<void> | null = null;
  private eventQueue: ParsedEvent[] = [];
  private waiters: Array<(e: ParsedEvent | null) => void> = [];

  constructor(
    url: string,
    opts: {
      headers?: Record<string, string>;
      backoff?: readonly number[];
      maxRetries?: number;
    } = {},
  ) {
    this.url = url;
    this.headers = { Accept: "text/event-stream", ...opts.headers };
    this.backoff = opts.backoff ?? DEFAULT_BACKOFF;
    this.maxRetries = opts.maxRetries;
  }

  get isConnected(): boolean {
    return this.connected;
  }

  /** Register `callback` for `eventType` (use `*` for wildcard). */
  on(eventType: string, callback: Callback): void {
    const list = this.callbacks.get(eventType) ?? [];
    list.push(callback);
    this.callbacks.set(eventType, list);
  }

  /** Remove all callbacks for `eventType`. */
  off(eventType: string): void {
    this.callbacks.delete(eventType);
  }

  /** Remove every callback. */
  offAll(): void {
    this.callbacks.clear();
  }

  /** Open the SSE stream and begin reading in the background. */
  connect(): Promise<void> {
    if (this.runPromise) return Promise.resolve();
    this.shouldReconnect = true;
    this.abortController = new AbortController();
    this.runPromise = this.run().catch(() => {});
    return Promise.resolve();
  }

  /** Close the connection and stop reconnecting. */
  async disconnect(): Promise<void> {
    this.shouldReconnect = false;
    if (this.abortController) this.abortController.abort();
    await this.closeReader();
    // Resolve any pending event waiters so they don't hang.
    while (this.waiters.length) {
      const w = this.waiters.shift();
      w?.(null);
    }
    if (this.runPromise) {
      try {
        await Promise.race([
          this.runPromise,
          new Promise((r) => setTimeout(r, 200)),
        ]);
      } catch {
        // ignore
      }
      this.runPromise = null;
    }
    this.connected = false;
  }

  /** Synchronously drain one queued event, or `null` if none. */
  poll(): ParsedEvent | null {
    return this.eventQueue.shift() ?? null;
  }

  /** Wait for the next event (resolves `null` on disconnect/timeout). */
  async nextEvent(timeoutMs?: number): Promise<ParsedEvent | null> {
    const existing = this.eventQueue.shift();
    if (existing) return existing;
    return new Promise<ParsedEvent | null>((resolve) => {
      let settled = false;
      const finish = (v: ParsedEvent | null) => {
        if (settled) return;
        settled = true;
        const idx = this.waiters.indexOf(finish);
        if (idx >= 0) this.waiters.splice(idx, 1);
        resolve(v);
      };
      this.waiters.push(finish);
      if (timeoutMs !== undefined) {
        setTimeout(() => finish(null), timeoutMs);
      }
    });
  }

  // ----------------------------------------------------------- internals
  private async closeReader(): Promise<void> {
    if (this.reader) {
      try {
        await this.reader.cancel();
      } catch {
        // ignore
      }
      this.reader = null;
    }
  }

  private nextBackoff(): number {
    const idx = Math.min(this.retryCount, this.backoff.length - 1);
    return Math.min(this.backoff[idx] ?? MAX_BACKOFF, MAX_BACKOFF);
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => {
      if (ms <= 0) {
        resolve();
        return;
      }
      const t = setTimeout(resolve, ms);
      if (this.abortController?.signal) {
        this.abortController.signal.addEventListener(
          "abort",
          () => {
            clearTimeout(t);
            resolve();
          },
          { once: true },
        );
      }
    });
  }

  private async run(): Promise<void> {
    while (this.shouldReconnect) {
      try {
        await this.connectOnce();
        this.retryCount = 0;
      } catch {
        if (!this.shouldReconnect) break;
        this.retryCount += 1;
        if (this.maxRetries !== undefined && this.retryCount > this.maxRetries) {
          break;
        }
      } finally {
        await this.closeReader();
        this.connected = false;
      }
      // Wait before reconnecting (applies to both normal disconnects and
      // errors) so we don't tight-loop when the stream ends immediately.
      if (this.shouldReconnect) {
        await this.sleep(this.nextBackoff() * 1000);
      }
    }
  }

  private async connectOnce(): Promise<void> {
    if (!this.abortController) this.abortController = new AbortController();
    const reqHeaders: Record<string, string> = { ...this.headers };
    if (this.lastEventId !== undefined) reqHeaders["Last-Event-ID"] = this.lastEventId;
    const res = await fetch(this.url, {
      method: "GET",
      headers: reqHeaders,
      signal: this.abortController.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    if (!res.body) throw new Error("No response body");
    this.connected = true;
    this.reader = res.body.getReader();
    await this.readStream(this.reader);
  }

  private async readStream(
    reader: ReadableStreamDefaultReader<Uint8Array>,
  ): Promise<void> {
    const decoder = new TextDecoder();
    let buffer = "";
    let eventFields: Record<string, string> = {};
    let dataLines: string[] = [];

    const dispatch = () => {
      if (dataLines.length > 0 || "event" in eventFields) {
        const ev = makeEvent({
          event: eventFields.event ?? "message",
          data: dataLines.join("\n"),
          id: eventFields.id,
          retry: eventFields.retry ? Number(eventFields.retry) : undefined,
        });
        this.dispatchEvent(ev);
      }
      eventFields = {};
      dataLines = [];
    };

    for (;;) {
      if (!this.shouldReconnect) break;
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, "");
        if (line === "") {
          dispatch();
          continue;
        }
        if (line.startsWith(":")) continue;
        let field: string;
        let val: string;
        const colon = line.indexOf(":");
        if (colon === -1) {
          field = line;
          val = "";
        } else {
          field = line.slice(0, colon);
          val = line.slice(colon + 1);
          if (val.startsWith(" ")) val = val.slice(1);
        }
        if (field === "event") eventFields.event = val;
        else if (field === "data") dataLines.push(val);
        else if (field === "id") {
          eventFields.id = val;
          this.lastEventId = val;
        } else if (field === "retry") {
          if (/^\d+$/.test(val)) eventFields.retry = val;
        }
      }
    }
    // flush trailing buffered content
    if (buffer !== "") {
      const line = buffer.replace(/\r$/, "");
      if (line === "") dispatch();
    }
  }

  private dispatchEvent(ev: ParsedEvent): void {
    // Give priority to an active waiter; otherwise queue for polling.
    const waiter = this.waiters.shift();
    if (waiter) {
      waiter(ev);
    } else {
      this.eventQueue.push(ev);
    }
    const handlers = [
      ...(this.callbacks.get(ev.event) ?? []),
      ...(this.callbacks.get("*") ?? []),
    ];
    for (const cb of handlers) {
      try {
        const r = cb(ev);
        if (r && typeof (r as Promise<void>).catch === "function") {
          (r as Promise<void>).catch(() => {});
        }
      } catch {
        // swallow callback errors
      }
    }
  }
}
