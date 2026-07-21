import { serverCardSchema, type ServerCard } from "../models/index.js";
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
    if (text) params.text = text;
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
      const card = serverCardSchema.parse(item);
      results.push({ card, score: item._score ?? null });
    }

    return results;
  }

  async getServerCard(
    serverId: string,
    etag?: string | null,
  ): Promise<{ card: ServerCard | null; etag: string | null }> {
    const headers = this.authHeaders();
    if (etag) {
      headers["If-None-Match"] = etag;
    }

    let resp: Response;
    try {
      resp = await fetch(`${this.baseUrl}/v1/servers/${encodeURIComponent(serverId)}`, {
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

    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new RegistryUnavailable(this.baseUrl, resp.status, detail);
    }

    const newEtag = resp.headers.get("ETag");
    const body = await resp.json();
    const card = serverCardSchema.parse(body);
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
