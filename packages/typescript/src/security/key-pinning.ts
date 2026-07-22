/**
 * Key pinning — verify server public keys against pinned SHA-256 hashes.
 *
 * Uses the Web Crypto API (`crypto.subtle.digest`) so it works in both
 * browser and Node 18+ environments without `node:` imports.
 */

import { SignatureVerificationFailed } from "../errors.js";

/** Dynamic fs loader — avoids static `node:` imports for browser compat. */
async function loadFs(): Promise<{
  writeFile(path: string, data: string, encoding: string): Promise<void>;
  readFile(path: string, encoding: string): Promise<string>;
}> {
  const mod = "node:fs/promises";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return await (import(mod) as Promise<any>);
}

export class KeyPinStore {
  private pins = new Map<string, string>();

  /** SHA-256 of `publicKey`, returned as hex. */
  static async hashPublicKey(publicKey: string): Promise<string> {
    const enc = new TextEncoder();
    const buf = await crypto.subtle.digest("SHA-256", enc.encode(publicKey));
    return Array.from(new Uint8Array(buf))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  }

  /** Pin a pre-computed hash for `serverId`. */
  pin(serverId: string, publicKeyHash: string): void {
    this.pins.set(serverId, publicKeyHash);
  }

  /** Pin the hash of `publicKey` for `serverId` (computes hash). */
  async pinKey(serverId: string, publicKey: string): Promise<string> {
    const hash = await KeyPinStore.hashPublicKey(publicKey);
    this.pins.set(serverId, hash);
    return hash;
  }

  /** Remove a pin (no-op if absent). */
  unpin(serverId: string): void {
    this.pins.delete(serverId);
  }

  /** Whether `serverId` has a pinned key. */
  isPinned(serverId: string): boolean {
    return this.pins.has(serverId);
  }

  /** Pinned hash for `serverId` (or `undefined`). */
  getPin(serverId: string): string | undefined {
    return this.pins.get(serverId);
  }

  get size(): number {
    return this.pins.size;
  }

  /**
   * Verify `publicKey` for `serverId`.
   *
   * Returns `true` if the key matches the pin, or if no pin exists (no pin =
   * no constraint).  Throws `SignatureVerificationFailed` on mismatch.
   */
  async verify(serverId: string, publicKey: string): Promise<boolean> {
    const pinned = this.pins.get(serverId);
    if (pinned === undefined) return true;
    const actual = await KeyPinStore.hashPublicKey(publicKey);
    if (actual !== pinned) {
      throw new SignatureVerificationFailed(
        `Public key hash mismatch for server '${serverId}': expected ${pinned}, got ${actual}`,
      );
    }
    return true;
  }

  /** Persist pins to `path` as JSON (Node only). */
  async save(path: string): Promise<void> {
    const data: Record<string, string> = {};
    for (const [k, v] of this.pins) data[k] = v;
    const fs = await loadFs();
    await fs.writeFile(path, JSON.stringify(data, null, 2), "utf-8");
  }

  /** Load pins from `path` (returns empty instance if missing). */
  static async load(path: string): Promise<KeyPinStore> {
    const inst = new KeyPinStore();
    try {
      const fs = await loadFs();
      const raw = await fs.readFile(path, "utf-8");
      const data = JSON.parse(raw) as Record<string, string>;
      for (const [k, v] of Object.entries(data)) inst.pins.set(k, v);
    } catch {
      // missing — return empty
    }
    return inst;
  }

  /** In-memory load from a JSON string. */
  static fromJSON(json: string): KeyPinStore {
    const inst = new KeyPinStore();
    try {
      const data = JSON.parse(json) as Record<string, string>;
      for (const [k, v] of Object.entries(data)) inst.pins.set(k, v);
    } catch {
      // ignore
    }
    return inst;
  }

  toJSON(): string {
    const data: Record<string, string> = {};
    for (const [k, v] of this.pins) data[k] = v;
    return JSON.stringify(data, null, 2);
  }
}
