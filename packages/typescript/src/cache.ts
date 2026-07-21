export interface CacheEntry<T> {
  card: T;
  etag: string | null;
  cachedAt: number;
}

export class ServerCardCache<T = unknown> {
  private store = new Map<string, CacheEntry<T>>();
  private ttl: number;
  private _conditional: boolean;

  constructor(ttlSeconds: number = 300, conditional: boolean = true) {
    this.ttl = ttlSeconds * 1000;
    this._conditional = conditional;
  }

  get(serverId: string): { card: T; etag: string | null } | null {
    const entry = this.store.get(serverId);
    if (!entry) return null;
    if (Date.now() - entry.cachedAt > this.ttl) {
      this.store.delete(serverId);
      return null;
    }
    return { card: entry.card, etag: entry.etag };
  }

  put(serverId: string, card: T, etag: string | null = null): void {
    this.store.set(serverId, { card, etag, cachedAt: Date.now() });
  }

  invalidate(serverId: string): void {
    this.store.delete(serverId);
  }

  clear(): void {
    this.store.clear();
  }

  cleanupExpired(): number {
    const now = Date.now();
    let removed = 0;
    for (const [key, entry] of this.store) {
      if (now - entry.cachedAt > this.ttl) {
        this.store.delete(key);
        removed++;
      }
    }
    return removed;
  }

  get size(): number {
    return this.store.size;
  }

  get conditional(): boolean {
    return this._conditional;
  }
}
