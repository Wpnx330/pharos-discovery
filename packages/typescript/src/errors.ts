export class PharosError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PharosError";
  }
}

export class RegistryUnavailable extends PharosError {
  constructor(public url: string, public status?: number, public detail?: string) {
    let msg = `Registry unavailable: ${url}`;
    if (status) msg += ` (HTTP ${status})`;
    if (detail) msg += `: ${detail}`;
    super(msg);
    this.name = "RegistryUnavailable";
  }
}

export class NoServersFound extends PharosError {
  constructor(public query: string = "") {
    super(query ? `No servers found for query: ${query}` : "No servers found");
    this.name = "NoServersFound";
  }
}

export class ApprovalDenied extends PharosError {
  constructor(public serverId: string, public denyReason?: string) {
    let msg = `Approval denied for ${serverId}`;
    if (denyReason) msg += `: ${denyReason}`;
    super(msg);
    this.name = "ApprovalDenied";
  }
}

export class ScopeNotApproved extends PharosError {
  constructor(public scope: string, public serverId: string) {
    super(`Scope '${scope}' not approved for server ${serverId}`);
    this.name = "ScopeNotApproved";
  }
}

export class ConnectionFailed extends PharosError {
  constructor(public serverId: string, public detail: string) {
    super(`Connection failed to ${serverId}: ${detail}`);
    this.name = "ConnectionFailed";
  }
}

export class DiscoveryDegraded extends PharosError {
  constructor() {
    super("Discovery degraded: all registries unavailable and no cache");
    this.name = "DiscoveryDegraded";
  }
}

export class SignatureVerificationFailed extends PharosError {
  constructor(public detail: string = "Invalid signature") {
    super(detail);
    this.name = "SignatureVerificationFailed";
  }
}

export class HeadlessApprovalRequired extends PharosError {
  constructor(public serverId: string) {
    super(`Headless mode requires pre-approved server: ${serverId}`);
    this.name = "HeadlessApprovalRequired";
  }
}

export class ConsentFatigueWarning extends PharosError {
  constructor(public count: number) {
    super(`Consent fatigue: ${count} novel servers approved this session`);
    this.name = "ConsentFatigueWarning";
  }
}

export class OAuthError extends PharosError {
  constructor(public error: string, public detail?: string) {
    let msg = `OAuth error: ${error}`;
    if (detail) msg += `: ${detail}`;
    super(msg);
    this.name = "OAuthError";
  }
}

export class TransportError extends PharosError {
  constructor(public transport: string, public detail: string) {
    super(`Transport error (${transport}): ${detail}`);
    this.name = "TransportError";
  }
}
