import { describe, it, expect } from "vitest";
import { Blocklist } from "../../src/security/blocklist.js";

describe("Blocklist — basic operations", () => {
  it("add + isBlocked", () => {
    const bl = new Blocklist();
    bl.add("server-1", "malicious");
    expect(bl.isBlocked("server-1")).toBe(true);
  });

  it("not blocked by default", () => {
    const bl = new Blocklist();
    expect(bl.isBlocked("server-1")).toBe(false);
  });

  it("remove", () => {
    const bl = new Blocklist();
    bl.add("server-1", "spam");
    bl.remove("server-1");
    expect(bl.isBlocked("server-1")).toBe(false);
  });

  it("remove nonexistent is no-op", () => {
    const bl = new Blocklist();
    bl.remove("nope");
    expect(bl.size).toBe(0);
  });

  it("add overwrites reason", () => {
    const bl = new Blocklist();
    bl.add("server-1", "a");
    bl.add("server-1", "b");
    expect(bl.list()["server-1"]).toBe("b");
    expect(bl.size).toBe(1);
  });

  it("list returns a copy", () => {
    const bl = new Blocklist();
    bl.add("s1", "x");
    const snap = bl.list();
    snap["s2"] = "y";
    expect(bl.isBlocked("s2")).toBe(false);
  });

  it("size", () => {
    const bl = new Blocklist();
    bl.add("a", "1");
    bl.add("b", "2");
    expect(bl.size).toBe(2);
  });
});

describe("Blocklist — default reason", () => {
  it("uses 'blocked' when reason omitted", () => {
    const bl = new Blocklist();
    bl.add("server-1");
    expect(bl.list()["server-1"]).toBe("blocked");
  });
});

describe("Blocklist — JSON / persistence", () => {
  it("toJSON + fromJSON roundtrip", () => {
    const bl = new Blocklist();
    bl.add("s1", "r1");
    bl.add("s2", "r2");
    const json = bl.toJSON();
    const loaded = Blocklist.fromJSON(json);
    expect(loaded.isBlocked("s1")).toBe(true);
    expect(loaded.isBlocked("s2")).toBe(true);
    expect(loaded.list()["s1"]).toBe("r1");
  });

  it("fromJSON with malformed json returns empty", () => {
    const loaded = Blocklist.fromJSON("{not json");
    expect(loaded.size).toBe(0);
  });

  it("save + load roundtrip (fs)", async () => {
    const { writeFile, readFile, unlink } = await import("node:fs/promises");
    const path = "test-blocklist-tmp.json";
    const bl = new Blocklist();
    bl.add("s1", "malware");
    bl.add("s2", "abuse");
    await bl.save(path);
    const loaded = await Blocklist.load(path);
    expect(loaded.isBlocked("s1")).toBe(true);
    expect(loaded.isBlocked("s2")).toBe(true);
    expect(loaded.list()["s1"]).toBe("malware");
    await unlink(path);
  });

  it("load missing file returns empty", async () => {
    const loaded = await Blocklist.load("nonexistent-blocklist-xyz.json");
    expect(loaded.size).toBe(0);
  });
});
