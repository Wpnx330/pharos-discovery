import { describe, it, expect } from "vitest";
import { KeyPinStore } from "../../src/security/key-pinning.js";
import { SignatureVerificationFailed } from "../../src/errors.js";

async function sha256Hex(s: string): Promise<string> {
  const enc = new TextEncoder();
  const buf = await crypto.subtle.digest("SHA-256", enc.encode(s));
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

describe("KeyPinStore — hashing", () => {
  it("hashPublicKey matches manual sha256", async () => {
    const key = "my-public-key";
    const expected = await sha256Hex(key);
    const got = await KeyPinStore.hashPublicKey(key);
    expect(got).toBe(expected);
  });

  it("hash is 64-char hex", async () => {
    const h = await KeyPinStore.hashPublicKey("abc");
    expect(h.length).toBe(64);
    expect(/^[0-9a-f]+$/.test(h)).toBe(true);
  });

  it("different keys produce different hashes", async () => {
    const a = await KeyPinStore.hashPublicKey("a");
    const b = await KeyPinStore.hashPublicKey("b");
    expect(a).not.toBe(b);
  });
});

describe("KeyPinStore — pinning", () => {
  it("pin + isPinned", () => {
    const store = new KeyPinStore();
    store.pin("server-1", "deadbeef");
    expect(store.isPinned("server-1")).toBe(true);
  });

  it("unpin", () => {
    const store = new KeyPinStore();
    store.pin("s1", "h");
    store.unpin("s1");
    expect(store.isPinned("s1")).toBe(false);
  });

  it("unpin nonexistent is no-op", () => {
    const store = new KeyPinStore();
    store.unpin("nope");
    expect(store.size).toBe(0);
  });

  it("pin overwrites", () => {
    const store = new KeyPinStore();
    store.pin("s1", "a");
    store.pin("s1", "b");
    expect(store.getPin("s1")).toBe("b");
  });

  it("size", () => {
    const store = new KeyPinStore();
    store.pin("a", "1");
    store.pin("b", "2");
    expect(store.size).toBe(2);
  });

  it("pinKey computes hash", async () => {
    const store = new KeyPinStore();
    const key = "the-key";
    const hash = await store.pinKey("s1", key);
    expect(hash).toBe(await sha256Hex(key));
    expect(store.isPinned("s1")).toBe(true);
  });
});

describe("KeyPinStore — verify", () => {
  it("verify matching key returns true", async () => {
    const store = new KeyPinStore();
    const key = "public-key-123";
    const h = await KeyPinStore.hashPublicKey(key);
    store.pin("s1", h);
    expect(await store.verify("s1", key)).toBe(true);
  });

  it("verify mismatch throws", async () => {
    const store = new KeyPinStore();
    store.pin("s1", await KeyPinStore.hashPublicKey("real"));
    await expect(store.verify("s1", "wrong")).rejects.toThrow(SignatureVerificationFailed);
  });

  it("verify unpinned returns true", async () => {
    const store = new KeyPinStore();
    expect(await store.verify("s1", "any")).toBe(true);
  });

  it("verify exception message contains server id", async () => {
    const store = new KeyPinStore();
    store.pin("s1", "0".repeat(64));
    await expect(store.verify("s1", "wrong")).rejects.toThrow(/s1/);
  });

  it("multiple servers independent", async () => {
    const store = new KeyPinStore();
    const k1 = "key-one";
    const k2 = "key-two";
    store.pin("s1", await KeyPinStore.hashPublicKey(k1));
    store.pin("s2", await KeyPinStore.hashPublicKey(k2));
    expect(await store.verify("s1", k1)).toBe(true);
    expect(await store.verify("s2", k2)).toBe(true);
    await expect(store.verify("s1", k2)).rejects.toThrow(SignatureVerificationFailed);
  });
});

describe("KeyPinStore — persistence", () => {
  it("toJSON + fromJSON roundtrip", () => {
    const store = new KeyPinStore();
    store.pin("s1", "h1");
    const json = store.toJSON();
    const loaded = KeyPinStore.fromJSON(json);
    expect(loaded.isPinned("s1")).toBe(true);
    expect(loaded.getPin("s1")).toBe("h1");
  });

  it("save + load roundtrip (fs)", async () => {
    const { unlink } = await import("node:fs/promises");
    const path = "test-keypins-tmp.json";
    const store = new KeyPinStore();
    const key = "real-key";
    store.pin("s1", await KeyPinStore.hashPublicKey(key));
    await store.save(path);
    const loaded = await KeyPinStore.load(path);
    expect(loaded.isPinned("s1")).toBe(true);
    expect(await loaded.verify("s1", key)).toBe(true);
    await unlink(path);
  });

  it("load missing file returns empty", async () => {
    const loaded = await KeyPinStore.load("nonexistent-pins-xyz.json");
    expect(loaded.size).toBe(0);
  });
});
