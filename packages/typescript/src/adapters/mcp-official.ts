import { serverCardSchema, type ServerCard, type AuthSpec, type Publisher } from "../models/index.js";
import { RegistryUnavailable, NoServersFound } from "../errors.js";
import type { SearchResult } from "./registry.js";

export { SearchResult };

const DEFAULT_REGISTRY = "https://registry.modelcontextprotocol.io/v1";

const TOKEN_RE = /[a-z0-9]+/g;

function tokenize(text: string): string[] {
  return text.toLowerCase().match(TOKEN_RE) ?? [];
}

function buildCorpusTokens(
  name: string,
  description: string,
  capabilities: string[],
  tags: string[],
): string[] {
  const combined = [name, description, capabilities.join(" "), tags.join(" ")].join(" ");
  return tokenize(combined);
}

function computeTfidf(documents: string[][]): Map<string, number>[] {
  const nDocs = documents.length;
  if (nDocs === 0) return [];

  // Document frequency
  const df = new Map<string, number>();
  for (const tokens of documents) {
    const unique = new Set(tokens);
    for (const term of unique) {
      df.set(term, (df.get(term) ?? 0) + 1);
    }
  }

  // IDF with smoothing
  const idf = new Map<string, number>();
  for (const [term, count] of df) {
    idf.set(term, Math.log((1 + nDocs) / (1 + count)) + 1.0);
  }

  // TF-IDF vectors
  const vectors: Map<string, number>[] = [];
  for (const tokens of documents) {
    const tf = new Map<string, number>();
    for (const tok of tokens) {
      tf.set(tok, (tf.get(tok) ?? 0) + 1);
    }
    const total = tokens.length || 1;
    const vec = new Map<string, number>();
    for (const [term, count] of tf) {
      vec.set(term, (count / total) * (idf.get(term) ?? 0));
    }
    vectors.push(vec);
  }
  return vectors;
}

function cosineSimilarity(a: Map<string, number>, b: Map<string, number>): number {
  if (a.size === 0 || b.size === 0) return 0.0;
  // Iterate over the smaller map
  const [small, large] = a.size <= b.size ? [a, b] : [b, a];
  let dot = 0.0;
  for (const [term, weight] of small) {
    dot += weight * (large.get(term) ?? 0.0);
  }
  let normA = 0.0;
  for (const w of a.values()) normA += w * w;
  let normB = 0.0;
  for (const w of b.values()) normB += w * w;
  normA = Math.sqrt(normA);
  normB = Math.sqrt(normB);
  if (normA === 0.0 || normB === 0.0) return 0.0;
  return dot / (normA * normB);
}

export interface MCPOfficialAdapterOptions {
  searchTimeout?: number;
  getTimeout?: number;
  apiKey?: string;
}

export class MCPOfficialAdapter {
  private baseUrl: string;
  private searchTimeout: number;
  private getTimeout: number;
  private apiKey: string | null;

  constructor(
    baseUrl: string = DEFAULT_REGISTRY,
    options: MCPOfficialAdapterOptions = {},
  ) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.searchTimeout = options.searchTimeout ?? 10000;
    this.getTimeout = options.getTimeout ?? 10000;
    this.apiKey = options.apiKey ?? null;
  }

  get url(): string {
    return this.baseUrl;
  }

  async search(query: string, limit: number = 20): Promise<SearchResult[]> {
    const rawItems = await this.fetchPackages(query);

    if (rawItems.length === 0) {
      throw new NoServersFound(query);
    }

    // Build corpus
    const parsed: { card: ServerCard; tokens: string[] }[] = [];
    for (const item of rawItems) {
      const card = this.normalize(item);
      const tokens = buildCorpusTokens(
        card.display_name,
        card.description,
        card.capabilities,
        card.tags,
      );
      parsed.push({ card, tokens });
    }

    // Build query vector
    const queryTokens = tokenize(query);
    const allDocs = parsed.map((p) => p.tokens);
    allDocs.push(queryTokens);
    const tfidfVectors = computeTfidf(allDocs);
    const queryVec = tfidfVectors[tfidfVectors.length - 1];

    // Score each document
    const scored: SearchResult[] = parsed.map((p, idx) => ({
      card: p.card,
      score: cosineSimilarity(queryVec, tfidfVectors[idx]),
    }));

    // Sort by relevance descending
    scored.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
    return scored.slice(0, limit);
  }

  async get(name: string): Promise<ServerCard> {
    const headers = this.authHeaders();
    const url = `${this.baseUrl}/servers/${encodeURIComponent(name)}`;

    let resp: Response;
    try {
      resp = await fetch(url, {
        method: "GET",
        headers,
        signal: AbortSignal.timeout(this.getTimeout),
      });
    } catch (exc) {
      throw new RegistryUnavailable(this.baseUrl, undefined, String(exc));
    }

    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new RegistryUnavailable(this.baseUrl, resp.status, detail);
    }

    const raw = await resp.json();
    return this.normalize(raw);
  }

  // ------------------------------------------------------------------
  // Internal helpers
  // ------------------------------------------------------------------

  private async fetchPackages(query: string): Promise<Record<string, unknown>[]> {
    const headers = this.authHeaders();
    const url = new URL(`${this.baseUrl}/servers`);
    if (query) {
      url.searchParams.set("q", query);
    }

    let resp: Response;
    try {
      resp = await fetch(url.toString(), {
        method: "GET",
        headers,
        signal: AbortSignal.timeout(this.searchTimeout),
      });
    } catch (exc) {
      throw new RegistryUnavailable(this.baseUrl, undefined, String(exc));
    }

    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new RegistryUnavailable(this.baseUrl, resp.status, detail);
    }

    const data = await resp.json();
    if (Array.isArray(data)) return data;
    return (data.servers ?? data.packages ?? []) as Record<string, unknown>[];
  }

  private normalize(raw: Record<string, any>): ServerCard {
    const serverId = raw.id ?? raw.name ?? "unknown";
    const name = raw.name ?? raw.display_name ?? serverId;
    const description = raw.description ?? raw.summary ?? "";
    const version = raw.version ?? "0.0.0";

    // Publisher
    const pubRaw = raw.publisher ?? {};
    const publisher: Publisher = {
      id: pubRaw.id ?? pubRaw.name ?? "unknown",
      name: pubRaw.name ?? pubRaw.id ?? "unknown",
      verified: pubRaw.verified ?? undefined,
      verification_method: pubRaw.verification_method ?? undefined,
      contact: pubRaw.contact ?? undefined,
    };

    // Transport
    const rawTransports: unknown[] = raw.transports ?? raw.transport ?? [];
    const validTransports = ["stdio", "http+sse", "streamable-http"] as const;
    let transports = (rawTransports as string[]).filter((t) =>
      validTransports.includes(t as (typeof validTransports)[number]),
    );
    if (transports.length === 0) {
      transports = ["http+sse"];
    }

    // Capabilities
    const capabilities: string[] = raw.capabilities ?? raw.tools ?? [];

    // Auth
    const rawAuth = raw.auth ?? {};
    const validAuthTypes = ["none", "api_key", "oauth", "mtls"] as const;
    const authType = validAuthTypes.includes(rawAuth.type as (typeof validAuthTypes)[number])
      ? rawAuth.type
      : "none";
    const auth: AuthSpec = { type: authType };

    // Status
    const validStatuses = ["active", "deprecated", "deleted"] as const;
    const status = validStatuses.includes(raw.status as (typeof validStatuses)[number])
      ? raw.status
      : "active";

    // Endpoint
    const endpoint = raw.endpoint ?? raw.url ?? undefined;
    const stdioCommand = raw.stdio_command ?? raw.command ?? undefined;

    // Timestamps
    let publishedAt = raw.published_at ?? raw.created_at ?? "";
    let updatedAt = raw.updated_at ?? raw.modified_at ?? "";
    if (!publishedAt) publishedAt = "1970-01-01T00:00:00Z";
    if (!updatedAt) updatedAt = publishedAt;

    // Tags
    const tags: string[] = raw.tags ?? [];

    // Tools count
    const toolsCount: number = raw.tools_count ?? (capabilities.length || 0);

    // Source registry
    const sourceRegistry: string = raw.source_registry ?? this.baseUrl;

    // Availability
    const validAvailabilities = ["mirrored", "referenced", "native"] as const;
    const availability = validAvailabilities.includes(
      raw.availability as (typeof validAvailabilities)[number],
    )
      ? raw.availability
      : "native";

    const cardData = {
      id: serverId,
      display_name: name,
      description,
      publisher,
      version,
      transport: transports,
      endpoint,
      stdio_command: stdioCommand,
      capabilities,
      tools_count: toolsCount,
      auth,
      availability,
      tags,
      source_registry: sourceRegistry,
      published_at: publishedAt,
      updated_at: updatedAt,
      status,
    };

    return serverCardSchema.parse(cardData);
  }

  private authHeaders(): Record<string, string> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (this.apiKey) {
      headers["Authorization"] = `Bearer ${this.apiKey}`;
    }
    return headers;
  }
}
