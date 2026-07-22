/**
 * Consent store — records user approval/denial decisions for MCP servers. (T18)
 *
 * In-memory implementation with optional JSON file persistence (async, like Blocklist).
 * JS is single-threaded so no explicit locking is needed.
 */

export type Decision = "approved" | "denied";

export interface ConsentRecord {
  server_id: string;
  scopes: string[];
  decision: Decision;
  timestamp: number;
  expires_at: number | null;
}

export function isExpired(record: ConsentRecord): boolean {
  if (record.expires_at === null) return false;
  return Date.now() / 1000 >= record.expires_at;
}

/** Dynamic fs loader — avoids static `node:` imports for browser compat. */
async function loadFs(): Promise<{
  writeFile(path: string, data: string, encoding: string): Promise<void>;
  readFile(path: string, encoding: string): Promise<string>;
}> {
  const mod = "node:fs/promises";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return await (import(mod) as Promise<any>);
}

export class ConsentStore {
  private records: Map<string, ConsentRecord> = new Map();
  private persistPath: string | null;

  constructor(persistPath: string | null = null) {
    this.persistPath = persistPath;
  }

  record(
    serverId: string,
    scopes: string[],
    decision: Decision,
    ttl: number | null = null,
  ): ConsentRecord {
    const now = Date.now() / 1000;
    const rec: ConsentRecord = {
      server_id: serverId,
      scopes: [...scopes],
      decision,
      timestamp: now,
      expires_at: ttl !== null ? now + ttl : null,
    };
    this.records.set(serverId, rec);
    return rec;
  }

  check(serverId: string, scopes: string[]): ConsentRecord | null {
    const rec = this.records.get(serverId);
    if (rec === undefined) return null;
    if (isExpired(rec)) return null;
    if (rec.decision !== "approved") return null;
    const recordScopes = new Set(rec.scopes);
    for (const s of scopes) {
      if (!recordScopes.has(s)) return null;
    }
    return rec;
  }

  revoke(serverId: string): boolean {
    return this.records.delete(serverId);
  }

  listAll(): ConsentRecord[] {
    return [...this.records.values()];
  }

  isValid(record: ConsentRecord): boolean {
    return !isExpired(record);
  }

  /** Persist the store to disk as JSON (Node only). */
  async save(): Promise<void> {
    if (this.persistPath === null) return;
    const data: Record<string, ConsentRecord> = {};
    for (const [sid, rec] of this.records) {
      data[sid] = rec;
    }
    const fs = await loadFs();
    await fs.writeFile(this.persistPath, JSON.stringify(data, null, 2), "utf-8");
  }

  /** Load records from disk (returns empty store if missing/unreadable). */
  async load(): Promise<void> {
    if (this.persistPath === null) return;
    try {
      const fs = await loadFs();
      const raw = await fs.readFile(this.persistPath, "utf-8");
      const data = JSON.parse(raw) as Record<string, ConsentRecord>;
      for (const [sid, rec] of Object.entries(data)) {
        this.records.set(sid, rec);
      }
    } catch {
      // missing or unreadable — keep in-memory state
    }
  }

  /** In-memory load from a JSON string (useful for tests / browser). */
  static fromJSON(json: string): ConsentStore {
    const inst = new ConsentStore();
    try {
      const data = JSON.parse(json) as Record<string, ConsentRecord>;
      for (const [sid, rec] of Object.entries(data)) {
        inst.records.set(sid, rec);
      }
    } catch {
      // ignore parse errors
    }
    return inst;
  }

  /** Serialise to a JSON string. */
  toJSON(): string {
    const data: Record<string, ConsentRecord> = {};
    for (const [sid, rec] of this.records) {
      data[sid] = rec;
    }
    return JSON.stringify(data, null, 2);
  }
}
