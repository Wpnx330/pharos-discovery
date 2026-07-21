import { ConnectionFailed, TransportError } from "../errors.js";
import type { ServerCard, ApprovalToken } from "../models/index.js";

export interface MCPTransport {
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  send(message: Record<string, unknown>): Promise<Record<string, unknown>>;
  isAlive(): Promise<boolean>;
  readonly endpoint?: string;
  readonly lastActivity: number | null;
}

export class HttpSSETransport implements MCPTransport {
  private connected = false;
  private _lastActivity: number | null = null;

  constructor(
    private _endpoint: string,
    private _token: ApprovalToken,
    private _timeout: number = 30000,
  ) {}

  get endpoint(): string {
    return this._endpoint;
  }
  get token(): ApprovalToken {
    return this._token;
  }
  get timeout(): number {
    return this._timeout;
  }
  get lastActivity(): number | null {
    return this._lastActivity;
  }

  async connect(): Promise<void> {
    this.connected = true;
    this._lastActivity = Date.now();
  }

  async disconnect(): Promise<void> {
    this.connected = false;
    this._lastActivity = null;
  }

  async send(message: Record<string, unknown>): Promise<Record<string, unknown>> {
    if (!this.connected) throw new TransportError("http+sse", "Not connected");
    this._lastActivity = Date.now();
    return { status: "ok", id: (message.id as string) ?? "unknown" };
  }

  async isAlive(): Promise<boolean> {
    return this.connected;
  }
}

export class StreamableHTTPTransport implements MCPTransport {
  private connected = false;
  private _lastActivity: number | null = null;

  constructor(
    private _endpoint: string,
    private _token: ApprovalToken,
    private _timeout: number = 30000,
  ) {}

  get endpoint(): string {
    return this._endpoint;
  }
  get token(): ApprovalToken {
    return this._token;
  }
  get timeout(): number {
    return this._timeout;
  }
  get lastActivity(): number | null {
    return this._lastActivity;
  }

  async connect(): Promise<void> {
    this.connected = true;
    this._lastActivity = Date.now();
  }

  async disconnect(): Promise<void> {
    this.connected = false;
    this._lastActivity = null;
  }

  async send(message: Record<string, unknown>): Promise<Record<string, unknown>> {
    if (!this.connected) throw new TransportError("streamable-http", "Not connected");
    this._lastActivity = Date.now();
    return { status: "ok", id: (message.id as string) ?? "unknown" };
  }

  async isAlive(): Promise<boolean> {
    return this.connected;
  }
}

export class StdioTransport implements MCPTransport {
  private connected = false;
  private _lastActivity: number | null = null;

  constructor(
    private _command: string,
    private _token: ApprovalToken,
    private _timeout: number = 30000,
  ) {}

  get command(): string {
    return this._command;
  }
  get token(): ApprovalToken {
    return this._token;
  }
  get timeout(): number {
    return this._timeout;
  }
  get lastActivity(): number | null {
    return this._lastActivity;
  }

  async connect(): Promise<void> {
    this.connected = true;
    this._lastActivity = Date.now();
  }

  async disconnect(): Promise<void> {
    this.connected = false;
    this._lastActivity = null;
  }

  async send(message: Record<string, unknown>): Promise<Record<string, unknown>> {
    if (!this.connected) throw new TransportError("stdio", "Not connected");
    this._lastActivity = Date.now();
    return { status: "ok", id: (message.id as string) ?? "unknown" };
  }

  async isAlive(): Promise<boolean> {
    return this.connected;
  }
}

export class ConnectionManager {
  private connections: Map<string, MCPTransport> = new Map();
  private retryCount: Map<string, number> = new Map();

  constructor(
    private _healthCheckInterval: number = 60000,
    private maxRetries: number = 3,
  ) {}

  get healthCheckInterval(): number {
    return this._healthCheckInterval;
  }

  get activeCount(): number {
    return this.connections.size;
  }

  get connectionIds(): string[] {
    return [...this.connections.keys()];
  }

  async connect(card: ServerCard, token: ApprovalToken): Promise<MCPTransport> {
    const existing = this.connections.get(card.id);
    if (existing && (await existing.isAlive())) {
      return existing;
    }

    const transport = this.createTransport(card, token);
    let lastError: Error | null = null;

    for (let attempt = 0; attempt < this.maxRetries; attempt++) {
      try {
        await transport.connect();
        this.connections.set(card.id, transport);
        this.retryCount.set(card.id, 0);
        return transport;
      } catch (exc) {
        lastError = exc as Error;
        this.retryCount.set(card.id, attempt + 1);
        if (attempt < this.maxRetries - 1) {
          await new Promise((r) => setTimeout(r, 100 * (attempt + 1)));
        }
      }
    }

    throw new ConnectionFailed(
      card.id,
      `Failed after ${this.maxRetries} retries: ${lastError?.message}`,
    );
  }

  async disconnect(serverId: string): Promise<void> {
    const transport = this.connections.get(serverId);
    if (transport) {
      await transport.disconnect();
      this.connections.delete(serverId);
    }
    this.retryCount.delete(serverId);
  }

  async disconnectAll(): Promise<void> {
    const ids = [...this.connections.keys()];
    await Promise.all(ids.map((id) => this.disconnect(id)));
  }

  async send(
    serverId: string,
    message: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const transport = this.connections.get(serverId);
    if (!transport) {
      throw new ConnectionFailed(serverId, "Not connected");
    }
    return transport.send(message);
  }

  async healthCheck(serverId: string): Promise<boolean> {
    const transport = this.connections.get(serverId);
    if (!transport) return false;
    return transport.isAlive();
  }

  async healthCheckAll(): Promise<Record<string, boolean>> {
    const results: Record<string, boolean> = {};
    for (const [id, transport] of this.connections) {
      results[id] = await transport.isAlive();
    }
    return results;
  }

  getTransport(serverId: string): MCPTransport | null {
    return this.connections.get(serverId) ?? null;
  }

  private createTransport(card: ServerCard, token: ApprovalToken): MCPTransport {
    const transports = card.transport;
    if (!transports || transports.length === 0) {
      throw new ConnectionFailed(card.id, "No transport types specified");
    }

    if (transports.includes("streamable-http") && card.endpoint) {
      return new StreamableHTTPTransport(card.endpoint, token);
    }
    if (transports.includes("http+sse") && card.endpoint) {
      return new HttpSSETransport(card.endpoint, token);
    }
    if (transports.includes("stdio") && card.stdio_command) {
      return new StdioTransport(card.stdio_command!, token);
    }

    throw new ConnectionFailed(card.id, `No usable transport from ${transports.join(", ")}`);
  }
}
