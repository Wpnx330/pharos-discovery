/**
 * Blocklist — maintain a persistent set of blocked MCP server IDs.
 *
 * In-memory `Map` with JSON-file persistence (uses dynamic `node:fs` import
 * so the module stays importable in pure-browser contexts).
 */

const DEFAULT_REASON = "blocked";

/** Dynamic fs loader — avoids static `node:` imports for browser compat. */
async function loadFs(): Promise<{
  writeFile(path: string, data: string, encoding: string): Promise<void>;
  readFile(path: string, encoding: string): Promise<string>;
}> {
  const mod = "node:fs/promises";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return await (import(mod) as Promise<any>);
}

export class Blocklist {
  private entries = new Map<string, string>();

  /** Block `serverId` for `reason`. */
  add(serverId: string, reason: string = DEFAULT_REASON): void {
    this.entries.set(serverId, reason);
  }

  /** Unblock `serverId` (no-op if absent). */
  remove(serverId: string): void {
    this.entries.delete(serverId);
  }

  /** Whether `serverId` is currently blocked. */
  isBlocked(serverId: string): boolean {
    return this.entries.has(serverId);
  }

  /** Shallow copy of the blocklist as a plain object. */
  list(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [k, v] of this.entries) out[k] = v;
    return out;
  }

  /** Number of blocked servers. */
  get size(): number {
    return this.entries.size;
  }

  /** Persist the blocklist to `path` as JSON (Node only). */
  async save(path: string): Promise<void> {
    const data: Record<string, string> = this.list();
    const fs = await loadFs();
    await fs.writeFile(path, JSON.stringify(data, null, 2), "utf-8");
  }

  /** Load a blocklist from `path` (returns empty instance if missing). */
  static async load(path: string): Promise<Blocklist> {
    const inst = new Blocklist();
    try {
      const fs = await loadFs();
      const raw = await fs.readFile(path, "utf-8");
      const data = JSON.parse(raw) as Record<string, string>;
      for (const [k, v] of Object.entries(data)) inst.entries.set(k, v);
    } catch {
      // missing or unreadable — return empty
    }
    return inst;
  }

  /** In-memory load from a JSON string (useful for tests / browser). */
  static fromJSON(json: string): Blocklist {
    const inst = new Blocklist();
    try {
      const data = JSON.parse(json) as Record<string, string>;
      for (const [k, v] of Object.entries(data)) inst.entries.set(k, v);
    } catch {
      // ignore parse errors
    }
    return inst;
  }

  /** Serialise to a JSON string. */
  toJSON(): string {
    return JSON.stringify(this.list(), null, 2);
  }
}
