import type { ApprovalRequest, ApprovalResponse, ApprovalToken } from "../models/index.js";

const subtle = globalThis.crypto.subtle;

function toHex(bytes: ArrayBuffer): string {
  const view = new Uint8Array(bytes);
  let hex = "";
  for (let i = 0; i < view.length; i++) {
    hex += view[i].toString(16).padStart(2, "0");
  }
  return hex;
}

function encode(text: string): ArrayBuffer {
  return new TextEncoder().encode(text).buffer as ArrayBuffer;
}

async function importHmacKey(secret: string): Promise<CryptoKey> {
  return subtle.importKey(
    "raw",
    encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
}

async function hmacHex(key: CryptoKey, message: string): Promise<string> {
  const sig = await subtle.sign("HMAC", key, encode(message));
  return toHex(sig);
}

function randomHex(byteLength: number): string {
  const bytes = new Uint8Array(byteLength);
  globalThis.crypto.getRandomValues(bytes);
  let hex = "";
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, "0");
  }
  return hex;
}

export class ApprovalEngine {
  private keyPromise: Promise<CryptoKey>;

  constructor(secret: string) {
    if (!secret || secret.length === 0) {
      throw new Error("ApprovalEngine requires a non-empty secret");
    }
    this.keyPromise = importHmacKey(secret);
  }

  async createToken(
    request: ApprovalRequest,
    response: ApprovalResponse,
    tokenTtlSeconds: number = 3600,
  ): Promise<ApprovalToken> {
    const now = Math.floor(Date.now() / 1000);
    const tokenId = await this.generateTokenId(request.server.id, now);

    const token: ApprovalToken = {
      token_id: tokenId,
      server_id: request.server.id,
      approved_scopes: response.approved_scopes,
      approved_capabilities: request.requested_capabilities,
      approved_oauth_scopes: [],
      duration: response.duration,
      approved_at: new Date().toISOString(),
      expires_at: String(now + tokenTtlSeconds),
      signature: "",
    };

    token.signature = await this.sign(token);
    return token;
  }

  async verifyToken(token: ApprovalToken): Promise<boolean> {
    const expectedSig = await this.sign(token);
    return timingSafeEqual(token.signature, expectedSig);
  }

  async isValid(token: ApprovalToken): Promise<boolean> {
    return !this.isExpired(token) && (await this.verifyToken(token));
  }

  isExpired(token: ApprovalToken): boolean {
    try {
      const expires = parseInt(token.expires_at, 10);
      if (Number.isNaN(expires)) return true;
      return Date.now() / 1000 >= expires;
    } catch {
      return true;
    }
  }

  private async sign(token: ApprovalToken): Promise<string> {
    const payload = JSON.stringify({
      token_id: token.token_id,
      server_id: token.server_id,
      approved_scopes: token.approved_scopes,
      approved_capabilities: token.approved_capabilities,
      approved_oauth_scopes: token.approved_oauth_scopes,
      duration: token.duration,
      approved_at: token.approved_at,
      expires_at: token.expires_at,
    });
    const key = await this.keyPromise;
    return hmacHex(key, payload);
  }

  private async generateTokenId(serverId: string, timestamp: number): Promise<string> {
    const raw = `${serverId}:${timestamp}:${randomHex(8)}`;
    const key = await this.keyPromise;
    return "tok_" + (await hmacHex(key, raw)).slice(0, 16);
  }
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}
