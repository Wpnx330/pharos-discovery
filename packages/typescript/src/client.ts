import { PharosRegistryAdapter, type SearchResult, type SearchFilters } from "./adapters/registry.js";
import { ServerCardCache } from "./cache.js";
import {
  ApprovalDenied,
  ConnectionFailed,
  ConsentFatigueWarning,
  DiscoveryDegraded,
  HeadlessApprovalRequired,
  NoServersFound,
  PharosError,
  RegistryUnavailable,
  ScopeNotApproved,
} from "./errors.js";
import type {
  ServerCard,
  ApprovalRequest,
  ApprovalResponse,
  ApprovalToken,
} from "./models/index.js";

export interface ApprovalHandler {
  requestApproval(request: ApprovalRequest): Promise<ApprovalResponse>;
}

export interface ConnectionHandler {
  connect(card: ServerCard, token: ApprovalToken): Promise<unknown>;
  disconnect(serverId: string): Promise<void>;
}

export interface PharosClientOptions {
  apiKey?: string;
  cacheTtl?: number;
  headless?: boolean;
  maxNovelApprovals?: number;
  approvalHandler?: ApprovalHandler;
  connectionHandler?: ConnectionHandler;
}

export class PharosClient {
  private adapter: PharosRegistryAdapter;
  private cache: ServerCardCache<ServerCard>;
  private headlessMode: boolean;
  private maxNovel: number;
  private approvalHandler: ApprovalHandler | null;
  private connectionHandler: ConnectionHandler | null;
  private novelCount: number = 0;
  private approvedServers: Map<string, ApprovalToken> = new Map();
  private connections: Map<string, unknown> = new Map();
  private blocklist: Set<string> | null = null;

  constructor(registryUrl: string, options: PharosClientOptions = {}) {
    this.adapter = new PharosRegistryAdapter(registryUrl, { apiKey: options.apiKey });
    this.cache = new ServerCardCache<ServerCard>(options.cacheTtl ?? 300);
    this.headlessMode = options.headless ?? false;
    this.maxNovel = options.maxNovelApprovals ?? 5;
    this.approvalHandler = options.approvalHandler ?? null;
    this.connectionHandler = options.connectionHandler ?? null;
  }

  get isHeadless(): boolean {
    return this.headlessMode;
  }

  get cacheInstance(): ServerCardCache<ServerCard> {
    return this.cache;
  }

  async search(
    text?: string,
    filters?: SearchFilters,
    limit: number = 20,
  ): Promise<SearchResult[]> {
    try {
      const results = await this.adapter.search(text, filters, limit);
      for (const r of results) {
        this.cache.put(r.card.id, r.card);
      }
      return results;
    } catch (exc) {
      if (exc instanceof NoServersFound) throw exc;
      if (exc instanceof RegistryUnavailable) {
        // Try cache fallback
        if (this.cache.size > 0) {
          const cached: SearchResult[] = [];
          const store = (this.cache as any).store as Map<string, any>;
          for (const [, entry] of store) {
            cached.push({ card: entry.card, score: null });
          }
          if (cached.length > 0) {
            return cached.slice(0, limit);
          }
        }
        throw new DiscoveryDegraded();
      }
      throw exc;
    }
  }

  async getServer(serverId: string): Promise<ServerCard> {
    const cached = this.cache.get(serverId);
    const etag = cached?.etag ?? null;
    const { card, etag: newEtag } = await this.adapter.getServerCard(serverId, etag);

    if (card !== null) {
      this.cache.put(serverId, card, newEtag);
      return card;
    }

    // 304 Not Modified
    if (cached?.card) {
      return cached.card;
    }

    throw new RegistryUnavailable(this.adapter.url, 404, "Server not found and no cache");
  }

  async connectAndApprove(
    card: ServerCard,
    purpose: string,
    options: {
      requestedScopes?: string[];
      requestedCapabilities?: string[];
      duration?: string;
    } = {},
  ): Promise<{ token: ApprovalToken; connection: unknown }> {
    // Check blocklist
    await this.ensureBlocklist();
    if (this.blocklist!.has(card.id)) {
      throw new ApprovalDenied(card.id, "Server is blocklisted");
    }

    // Check existing approval
    const existingToken = this.approvedServers.get(card.id);
    if (existingToken && !this.isTokenExpired(existingToken)) {
      const connection = await this.connect(card, existingToken);
      return { token: existingToken, connection };
    }

    // Build approval request
    const scopes = options.requestedScopes ?? card.auth.scopes ?? [];
    const capabilities = options.requestedCapabilities ?? card.capabilities;
    const duration = options.duration ?? "session";

    const request: ApprovalRequest = {
      server: card,
      purpose,
      requested_scopes: scopes,
      requested_capabilities: capabilities,
      duration: duration as "once" | "session" | "persistent" | "trust_on_use",
      render_id: `pharos-${card.id}-${Date.now()}`,
      selection_rationale: `User requested: ${purpose}`,
    };

    // Handle approval
    if (this.headlessMode) {
      throw new HeadlessApprovalRequired(card.id);
    }

    if (!this.approvalHandler) {
      throw new PharosError("No approval handler configured");
    }

    const response = await this.approvalHandler.requestApproval(request);

    if (!response.approved) {
      throw new ApprovalDenied(card.id, response.deny_reason ?? undefined);
    }

    // Track novel approvals
    if (!this.approvedServers.has(card.id)) {
      this.novelCount++;
      if (this.novelCount > this.maxNovel) {
        throw new ConsentFatigueWarning(this.novelCount);
      }
    }

    // Create approval token
    const now = Math.floor(Date.now() / 1000);
    const token: ApprovalToken = {
      token_id: `tok-${card.id}-${Date.now()}`,
      server_id: card.id,
      approved_scopes: response.approved_scopes,
      approved_capabilities: capabilities,
      approved_oauth_scopes: [],
      duration: response.duration,
      approved_at: new Date().toISOString(),
      expires_at: String(now + 3600),
      signature: "unsigned",
    };

    this.approvedServers.set(card.id, token);

    // Connect
    const connection = await this.connect(card, token);
    return { token, connection };
  }

  async revoke(serverId: string): Promise<void> {
    if (this.connections.has(serverId) && this.connectionHandler) {
      await this.connectionHandler.disconnect(serverId);
      this.connections.delete(serverId);
    }
    this.approvedServers.delete(serverId);
  }

  async checkScope(serverId: string, scope: string): Promise<void> {
    const token = this.approvedServers.get(serverId);
    if (!token) {
      throw new ScopeNotApproved(scope, serverId);
    }
    if (!token.approved_scopes.includes(scope)) {
      throw new ScopeNotApproved(scope, serverId);
    }
  }

  async close(): Promise<void> {
    if (this.connectionHandler) {
      for (const serverId of this.connections.keys()) {
        try {
          await this.connectionHandler.disconnect(serverId);
        } catch {
          // ignore
        }
      }
    }
    this.connections.clear();
    this.approvedServers.clear();
  }

  private async connect(card: ServerCard, token: ApprovalToken): Promise<unknown> {
    if (!this.connectionHandler) {
      throw new ConnectionFailed(card.id, "No connection handler configured");
    }
    const connection = await this.connectionHandler.connect(card, token);
    this.connections.set(card.id, connection);
    return connection;
  }

  private async ensureBlocklist(): Promise<void> {
    if (this.blocklist === null) {
      try {
        const blocked = await this.adapter.getBlocklist();
        this.blocklist = new Set(blocked);
      } catch {
        this.blocklist = new Set();
      }
    }
  }

  private isTokenExpired(token: ApprovalToken): boolean {
    try {
      const expires = parseFloat(token.expires_at);
      return Date.now() / 1000 > expires;
    } catch {
      return false;
    }
  }
}
