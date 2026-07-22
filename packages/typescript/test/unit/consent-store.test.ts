import { describe, it, expect, beforeEach } from "vitest";
import { promises as fs } from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import { ConsentStore, isExpired } from "../../src/consent/store.js";
import type { ConsentRecord } from "../../src/consent/store.js";

describe("isExpired", () => {
  it("returns false when expires_at is null", () => {
    const rec: ConsentRecord = {
      server_id: "s1",
      scopes: [],
      decision: "approved",
      timestamp: 0,
      expires_at: null,
    };
    expect(isExpired(rec)).toBe(false);
  });

  it("returns true when expired", () => {
    const rec: ConsentRecord = {
      server_id: "s1",
      scopes: [],
      decision: "approved",
      timestamp: 0,
      expires_at: Date.now() / 1000 - 1,
    };
    expect(isExpired(rec)).toBe(true);
  });

  it("returns false when not yet expired", () => {
    const rec: ConsentRecord = {
      server_id: "s1",
      scopes: [],
      decision: "approved",
      timestamp: 0,
      expires_at: Date.now() / 1000 + 100,
    };
    expect(isExpired(rec)).toBe(false);
  });
});

describe("ConsentStore.record", () => {
  let store: ConsentStore;
  beforeEach(() => { store = new ConsentStore(); });

  it("returns a consent record", () => {
    const rec = store.record("s1", ["search"], "approved");
    expect(rec.server_id).toBe("s1");
    expect(rec.scopes).toEqual(["search"]);
    expect(rec.decision).toBe("approved");
    expect(rec.timestamp).toBeGreaterThan(0);
    expect(rec.expires_at).toBeNull();
  });

  it("sets expires_at when ttl given", () => {
    const rec = store.record("s1", ["search"], "approved", 60);
    expect(rec.expires_at).not.toBeNull();
    expect(rec.expires_at! > rec.timestamp).toBe(true);
  });

  it("overwrites previous record", () => {
    store.record("s1", ["search"], "denied");
    store.record("s1", ["search"], "approved");
    expect(store.listAll()).toHaveLength(1);
    expect(store.listAll()[0].decision).toBe("approved");
  });

  it("copies scopes array", () => {
    const scopes = ["a", "b"];
    store.record("s1", scopes, "approved");
    scopes.push("c");
    const rec = store.check("s1", ["a", "b"]);
    expect(rec).not.toBeNull();
    expect(rec!.scopes).not.toContain("c");
  });
});

describe("ConsentStore.check", () => {
  let store: ConsentStore;
  beforeEach(() => { store = new ConsentStore(); });

  it("returns record when approved", () => {
    store.record("s1", ["search", "read"], "approved");
    expect(store.check("s1", ["search"])).not.toBeNull();
  });

  it("returns null when not found", () => {
    expect(store.check("s1", ["search"])).toBeNull();
  });

  it("returns null when denied", () => {
    store.record("s1", ["search"], "denied");
    expect(store.check("s1", ["search"])).toBeNull();
  });

  it("returns null when scope not covered", () => {
    store.record("s1", ["search"], "approved");
    expect(store.check("s1", ["write"])).toBeNull();
  });

  it("returns null when expired", () => {
    store.record("s1", ["search"], "approved", -1);
    expect(store.check("s1", ["search"])).toBeNull();
  });

  it("checks subset scopes", () => {
    store.record("s1", ["a", "b", "c"], "approved");
    expect(store.check("s1", ["a", "b"])).not.toBeNull();
    expect(store.check("s1", ["a", "b", "c"])).not.toBeNull();
    expect(store.check("s1", ["d"])).toBeNull();
  });

  it("empty scopes always matches", () => {
    store.record("s1", [], "approved");
    expect(store.check("s1", [])).not.toBeNull();
  });
});

describe("ConsentStore.revoke", () => {
  it("removes a record", () => {
    const store = new ConsentStore();
    store.record("s1", [], "approved");
    expect(store.revoke("s1")).toBe(true);
    expect(store.check("s1", [])).toBeNull();
  });

  it("returns false when not found", () => {
    const store = new ConsentStore();
    expect(store.revoke("s1")).toBe(false);
  });

  it("only affects target server", () => {
    const store = new ConsentStore();
    store.record("s1", [], "approved");
    store.record("s2", [], "approved");
    store.revoke("s1");
    expect(store.check("s1", [])).toBeNull();
    expect(store.check("s2", [])).not.toBeNull();
  });
});

describe("ConsentStore.listAll", () => {
  it("returns empty array when no records", () => {
    expect(new ConsentStore().listAll()).toEqual([]);
  });

  it("returns all records", () => {
    const store = new ConsentStore();
    store.record("s1", [], "approved");
    store.record("s2", [], "denied");
    expect(store.listAll()).toHaveLength(2);
  });

  it("includes expired records", () => {
    const store = new ConsentStore();
    store.record("s1", [], "approved", -1);
    expect(store.listAll()).toHaveLength(1);
  });
});

describe("ConsentStore.isValid", () => {
  it("returns true for non-expired", () => {
    const store = new ConsentStore();
    const rec = store.record("s1", [], "approved", 100);
    expect(store.isValid(rec)).toBe(true);
  });

  it("returns false for expired", () => {
    const store = new ConsentStore();
    const rec = store.record("s1", [], "approved", -1);
    expect(store.isValid(rec)).toBe(false);
  });

  it("returns true when no expiry", () => {
    const store = new ConsentStore();
    const rec = store.record("s1", [], "approved");
    expect(store.isValid(rec)).toBe(true);
  });
});

describe("ConsentStore persistence", () => {
  it("persists and reloads via save/load", async () => {
    const tmp = path.join(os.tmpdir(), `consent-${Date.now()}.json`);
    const store1 = new ConsentStore(tmp);
    store1.record("s1", ["search"], "approved", 3600);
    store1.record("s2", ["read"], "denied");
    await store1.save();

    const store2 = new ConsentStore(tmp);
    await store2.load();
    expect(store2.listAll()).toHaveLength(2);
    const rec = store2.check("s1", ["search"]);
    expect(rec).not.toBeNull();
    expect(rec!.decision).toBe("approved");

    await fs.rm(tmp, { force: true });
  });

  it("fromJSON loads records", () => {
    const json = JSON.stringify({
      "s1": {
        server_id: "s1",
        scopes: ["search"],
        decision: "approved",
        timestamp: 0,
        expires_at: null,
      },
    });
    const store = ConsentStore.fromJSON(json);
    expect(store.listAll()).toHaveLength(1);
    expect(store.check("s1", ["search"])).not.toBeNull();
  });

  it("fromJSON ignores invalid JSON", () => {
    const store = ConsentStore.fromJSON("{invalid");
    expect(store.listAll()).toHaveLength(0);
  });

  it("toJSON serialises records", () => {
    const store = new ConsentStore();
    store.record("s1", ["search"], "approved");
    const json = store.toJSON();
    const parsed = JSON.parse(json);
    expect(parsed["s1"].decision).toBe("approved");
  });

  it("load with no path is a no-op", async () => {
    const store = new ConsentStore();
    await store.load();
    expect(store.listAll()).toHaveLength(0);
  });

  it("load handles missing file gracefully", async () => {
    const tmp = path.join(os.tmpdir(), `nonexistent-${Date.now()}.json`);
    const store = new ConsentStore(tmp);
    await store.load();
    expect(store.listAll()).toHaveLength(0);
  });
});
