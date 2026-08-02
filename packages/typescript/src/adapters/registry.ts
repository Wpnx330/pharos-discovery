import { serverCardSchema, type ServerCard, type AuthSpec, type Publisher } from "../models/index.js";
import { RegistryUnavailable, NoServersFound } from "../errors.js";

export interface SearchResult {
  card: ServerCard;
  score: number | null;
}

export interface SearchFilters {
  transport?: string[];
  publisher_verified?: boolean;
  min_rating?: number;
  [key: string]: unknown;
}

/**
 * Normalize a raw registry item into a ServerCard.
 *
 * Handles two shapes:
 * 1. **Pharos-native** — already has `id` and `display_name` (pass-through).
 * 2. **Live registry** (getpharos.dev) — has `name`, `title`,
 *    `capabilities` as a dict with `tools`/`auth` keys, and `publisher`
 *    with a `namespace` field.
 */
function normalizeToServerCard(item: Record<string, any>, sourceRegistry: string): ServerCard {
  // --- Pharos-native shape (existing tests / other registries) -----------
  if (item.id && item.display_name) {
    return serverCardSchema.parse(item);
  }

  // --- Live registry shape (getpharos.dev /v1/search & /v1/packages) -----
  const name = item.name ?? item.id ?? "unknown";
  const title = item.title ?? item.display_name ?? name;
  const description = item.description ?? item.summary ?? "";
  let version = item.version ?? "0.0.0";

  // Publisher
  const pubRaw = item.publisher ?? {};
  const publisher: Publisher = {
    id: pubRaw.namespace ?? pubRaw.id ?? "unknown",
    name: pubRaw.namespace ?? pubRaw.name ?? "unknown",
    verified: pubRaw.verified ?? undefined,
    verification_method: pubRaw.verification_method ?? undefined,
    contact: pubRaw.contact ?? undefined,
  };

  // Capabilities
  const capsRaw = item.capabilities;
  let capabilities: string[] = [];
  let authType: string = "none";

  if (capsRaw && typeof capsRaw === "object" && !Array.isArray(capsRaw)) {
    if (capsRaw.tools) capabilities.push("tools");
    if (capsRaw.resources) capabilities.push("resources");
    if (capsRaw.prompts) capabilities.push("prompts");
    const authRaw = capsRaw.auth ?? {};
    authType = authRaw.type || "none";
  } else if (Array.isArray(capsRaw)) {
    capabilities = capsRaw.map(String);
    const authRaw = item.auth ?? {};
    authType = (typeof authRaw === "object" && authRaw.type) || "none";
  }

  if (!["none", "api_key", "oauth", "mtls"].includes(authType)) {
    authType = "none";
  }
  const auth: AuthSpec = { type: authType as "none" | "api_key" | "oauth" | "mtls" };

  // Transport — accept both "http-sse" (registry API) and "http+sse" (MCP spec)
  let rawTransports = item.transport ?? item.transports ?? [];
  if (typeof rawTransports === "string") rawTransports = [rawTransports];
  let transports = (rawTransports as unknown[])
    .filter((t) => ["stdio", "http+sse", "http-sse", "streamable-http"].includes(t as string))
    .map((t) => (t === "http-sse" ? "http+sse" : t)) as ("stdio" | "http+sse" | "streamable-http")[];
  if (transports.length === 0) transports = ["stdio"];

  // Endpoint / stdio command
  let endpoint = item.endpoint ?? item.url ?? undefined;
  let stdioCommand = item.stdio_command ?? item.command ?? undefined;

  // For package-detail responses, extract from latest version's manifest
  const versions = item.versions;
  if (Array.isArray(versions) && versions.length > 0) {
    const distTags = item.dist_tags ?? {};
    const latestTag = distTags.latest;
    let latestEntry = latestTag
      ? versions.find((v) => v.version === latestTag)
      : undefined;
    if (!latestEntry) latestEntry = versions[versions.length - 1];

    const manifest = latestEntry.manifest ?? {};
    if (!endpoint) endpoint = manifest.endpoint;
    if (!stdioCommand) stdioCommand = manifest.command ?? manifest.stdio_command;

    const manifestCaps = manifest.capabilities;
    if (Array.isArray(manifestCaps)) {
      capabilities = manifestCaps.map(String);
    }

    const manifestTransport = manifest.transport;
    if (typeof manifestTransport === "string") {
      const normalised = manifestTransport === "http-sse" ? "http+sse" : manifestTransport;
      if (["stdio", "http+sse", "streamable-http"].includes(normalised)) {
        transports = [normalised as "stdio" | "http+sse" | "streamable-http"];
      }
    }

    if (latestEntry.version && version === "0.0.0") {
      version = latestEntry.version;
    }
  }

  // Timestamps
  let publishedAt = item.published_at ?? item.created_at ?? "";
  let updatedAt = item.updated_at ?? item.modified_at ?? "";
  if (!publishedAt) publishedAt = "1970-01-01T00:00:00Z";
  if (!updatedAt) updatedAt = publishedAt;

  const cardData = {
    id: name,
    display_name: title,
    description,
    publisher,
    version,
    transport: transports,
    endpoint,
    stdio_command: stdioCommand,
    capabilities,
    tools_count: item.tools_count ?? (capabilities.length || 0),
    auth,
    availability: "native" as const,
    tags: item.tags ?? [],
    source_registry: sourceRegistry,
    published_at: publishedAt,
    updated_at: updatedAt,
    status: "active" as const,
  };

  return serverCardSchema.parse(cardData);
}

export class PharosRegistryAdapter {
  private baseUrl: string;
  private searchTimeout: number;
  private getTimeout: number;
  private apiKey: string | null;

  constructor(
    baseUrl: string,
    options: {
      searchTimeout?: number;
      getTimeout?: number;
      apiKey?: string;
    } = {},
  ) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.searchTimeout = options.searchTimeout ?? 10000;
    this.getTimeout = options.getTimeout ?? 10000;
    this.apiKey = options.apiKey ?? null;
  }

  get url(): string {
    return this.baseUrl;
  }

  async search(
    text?: string,
    filters?: SearchFilters,
    limit: number = 20,
  ): Promise<SearchResult[]> {
    const params: Record<string, string> = { limit: String(limit) };
    if (text) {
      // Prefer "q" (live registry) but also send "text" for compatibility
      params.q = text;
      params.text = text;
    }
    if (filters) {
      for (const [key, value] of Object.entries(filters)) {
        if (Array.isArray(value)) {
          params[key] = value.join(",");
        } else {
          params[key] = String(value);
        }
      }
    }

    const url = new URL(`${this.baseUrl}/v1/search`);
    for (const [k, v] of Object.entries(params)) {
      url.searchParams.set(k, v);
    }

    let resp: Response;
    try {
      resp = await fetch(url.toString(), {
        method: "GET",
        headers: this.authHeaders(),
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
    const items = data.results ?? [];

    if (items.length === 0) {
      throw new NoServersFound(text ?? "");
    }

    const results: SearchResult[] = [];
    for (const item of items) {
      const card = normalizeToServerCard(item, this.baseUrl);
      results.push({ card, score: item._score ?? item.score ?? null });
    }

    return results;
  }

  async getServerCard(
    serverId: string,
    etag?: string | null,
  ): Promise<{ card: ServerCard | null; etag: string | null }> {
    // Try /v1/servers/{id} (Pharos-native spec endpoint).
    const primary = await this._tryGetCard(
      `${this.baseUrl}/v1/servers/${encodeURIComponent(serverId)}`,
      etag,
    );
    if (primary.card !== null || primary.etag !== null) {
      return primary;
    }

    // Primary returned 404 — try live fallback /v1/packages/{name}.
    const fallback = await this._tryGetCard(
      `${this.baseUrl}/v1/packages/${encodeURIComponent(serverId)}`,
      etag,
    );
    if (fallback.card === null && fallback.etag === null) {
      throw new RegistryUnavailable(this.baseUrl, 404, "Server not found");
    }
    return fallback;
  }

  private async _tryGetCard(
    url: string,
    etag?: string | null,
  ): Promise<{ card: ServerCard | null; etag: string | null }> {
    const headers = this.authHeaders();
    if (etag) {
      headers["If-None-Match"] = etag;
    }

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

    if (resp.status === 304) {
      return { card: null, etag: etag ?? null };
    }

    if (resp.status === 404) {
      // Signal caller to try the fallback endpoint.
      return { card: null, etag: null };
    }

    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new RegistryUnavailable(this.baseUrl, resp.status, detail);
    }

    const newEtag = resp.headers.get("ETag");
    const body = await resp.json();
    const card = normalizeToServerCard(body, this.baseUrl);
    return { card, etag: newEtag };
  }

  async getBlocklist(): Promise<string[]> {
    let resp: Response;
    try {
      resp = await fetch(`${this.baseUrl}/v1/blocklist`, {
        method: "GET",
        headers: this.authHeaders(),
        signal: AbortSignal.timeout(this.getTimeout),
      });
    } catch (exc) {
      throw new RegistryUnavailable(this.baseUrl, undefined, String(exc));
    }

    if (resp.status === 404) {
      // Live registry may not implement /v1/blocklist — return empty.
      return [];
    }

    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new RegistryUnavailable(this.baseUrl, resp.status, detail);
    }

    const data = await resp.json();
    return data.blocked ?? [];
  }

  private authHeaders(): Record<string, string> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (this.apiKey) {
      headers["Authorization"] = `Bearer ${this.apiKey}`;
    }
    return headers;
  }
}
