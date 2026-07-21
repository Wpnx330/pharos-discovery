import { describe, it, expect } from "vitest";
import { ServerCardCache } from "../../src/cache.js";

describe("ServerCardCache", () => {
  it("put and get", () => {
    const cache = new ServerCardCache(300);
    const card = { id: "server-1", name: "Test" };
    cache.put("server-1", card);
    const result = cache.get("server-1");
    expect(result).not.toBeNull();
    expect(result!.card).toEqual(card);
    expect(result!.etag).toBeNull();
  });

  it("get missing returns null", () => {
    const cache = new ServerCardCache();
    expect(cache.get("nonexistent")).toBeNull();
  });

  it("expired entry returns null", () => {
    const cache = new ServerCardCache(0);
    cache.put("server-1", { id: "s1" });
    return new Promise<void>((resolve) => {
      setTimeout(() => {
        expect(cache.get("server-1")).toBeNull();
        resolve();
      }, 10);
    });
  });

  it("invalidate removes entry", () => {
    const cache = new ServerCardCache();
    cache.put("server-1", { id: "s1" });
    expect(cache.size).toBe(1);
    cache.invalidate("server-1");
    expect(cache.get("server-1")).toBeNull();
    expect(cache.size).toBe(0);
  });

  it("clear removes all", () => {
    const cache = new ServerCardCache();
    cache.put("s1", {});
    cache.put("s2", {});
    cache.put("s3", {});
    cache.clear();
    expect(cache.size).toBe(0);
  });

  it("cleanupExpired removes expired", () => {
    const cache = new ServerCardCache(300);
    cache.put("s1", {});
    cache.put("s2", {});
    cache.put("s3", {});
    // Backdate s1 and s2
    const entries = (cache as any).store as Map<string, any>;
    entries.get("s1").cachedAt = Date.now() - 301000;
    entries.get("s2").cachedAt = Date.now() - 301000;
    const removed = cache.cleanupExpired();
    expect(removed).toBe(2);
    expect(cache.size).toBe(1);
  });

  it("conditional property", () => {
    const cache1 = new ServerCardCache(300, true);
    expect(cache1.conditional).toBe(true);
    const cache2 = new ServerCardCache(300, false);
    expect(cache2.conditional).toBe(false);
  });

  it("etag support", () => {
    const cache = new ServerCardCache();
    cache.put("server-1", { id: "s1" }, "abc123");
    const result = cache.get("server-1");
    expect(result!.etag).toBe("abc123");
  });
});
