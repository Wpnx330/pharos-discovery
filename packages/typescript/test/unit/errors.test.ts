import { describe, it, expect } from "vitest";
import {
  PharosError, RegistryUnavailable, NoServersFound, ApprovalDenied,
  ScopeNotApproved, ConnectionFailed, DiscoveryDegraded,
  SignatureVerificationFailed, HeadlessApprovalRequired,
  ConsentFatigueWarning, OAuthError, TransportError,
} from "../../src/errors.js";

describe("RegistryUnavailable", () => {
  it("has url, status, detail", () => {
    const err = new RegistryUnavailable("https://reg.example.com", 503, "timeout");
    expect(err.url).toBe("https://reg.example.com");
    expect(err.status).toBe(503);
    expect(err.detail).toBe("timeout");
    expect(err.message).toContain("HTTP 503");
  });
  it("minimal", () => {
    const err = new RegistryUnavailable("https://reg.example.com");
    expect(err.status).toBeUndefined();
  });
});

describe("NoServersFound", () => {
  it("with query", () => {
    const err = new NoServersFound("flight search");
    expect(err.query).toBe("flight search");
    expect(err.message).toContain("flight search");
  });
  it("without query", () => {
    const err = new NoServersFound();
    expect(err.message).toContain("No servers found");
  });
});

describe("ApprovalDenied", () => {
  it("with deny reason", () => {
    const err = new ApprovalDenied("server-123", "excessive_scopes");
    expect(err.serverId).toBe("server-123");
    expect(err.denyReason).toBe("excessive_scopes");
  });
  it("without deny reason", () => {
    const err = new ApprovalDenied("server-123");
    expect(err.denyReason).toBeUndefined();
  });
});

describe("ScopeNotApproved", () => {
  it("has scope and serverId", () => {
    const err = new ScopeNotApproved("flight_book", "server-456");
    expect(err.scope).toBe("flight_book");
    expect(err.serverId).toBe("server-456");
  });
});

describe("ConnectionFailed", () => {
  it("has serverId and detail", () => {
    const err = new ConnectionFailed("server-789", "connection refused");
    expect(err.serverId).toBe("server-789");
    expect(err.detail).toBe("connection refused");
  });
});

describe("DiscoveryDegraded", () => {
  it("has message", () => {
    const err = new DiscoveryDegraded();
    expect(err.message).toContain("Discovery degraded");
  });
});

describe("SignatureVerificationFailed", () => {
  it("default message", () => {
    const err = new SignatureVerificationFailed();
    expect(err.detail).toBe("Invalid signature");
  });
  it("custom detail", () => {
    const err = new SignatureVerificationFailed("Bad key");
    expect(err.detail).toBe("Bad key");
  });
});

describe("HeadlessApprovalRequired", () => {
  it("has serverId", () => {
    const err = new HeadlessApprovalRequired("server-novel");
    expect(err.serverId).toBe("server-novel");
  });
});

describe("ConsentFatigueWarning", () => {
  it("has count", () => {
    const err = new ConsentFatigueWarning(7);
    expect(err.count).toBe(7);
    expect(err.message).toContain("7");
  });
});

describe("OAuthError", () => {
  it("with detail", () => {
    const err = new OAuthError("invalid_grant", "token expired");
    expect(err.error).toBe("invalid_grant");
    expect(err.detail).toBe("token expired");
  });
  it("without detail", () => {
    const err = new OAuthError("access_denied");
    expect(err.detail).toBeUndefined();
  });
});

describe("TransportError", () => {
  it("has transport and detail", () => {
    const err = new TransportError("http+sse", "stream closed");
    expect(err.transport).toBe("http+sse");
    expect(err.detail).toBe("stream closed");
  });
});

describe("inheritance", () => {
  it("all inherit from PharosError", () => {
    const errors = [
      new RegistryUnavailable("url"),
      new NoServersFound(),
      new ApprovalDenied("s"),
      new ScopeNotApproved("sc", "s"),
      new ConnectionFailed("s", "d"),
      new DiscoveryDegraded(),
      new SignatureVerificationFailed(),
      new HeadlessApprovalRequired("s"),
      new ConsentFatigueWarning(1),
      new OAuthError("e"),
      new TransportError("t", "d"),
    ];
    for (const err of errors) {
      expect(err).toBeInstanceOf(PharosError);
      expect(err).toBeInstanceOf(Error);
    }
  });
});
