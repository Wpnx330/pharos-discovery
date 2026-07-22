/**
 * Headless approval handler — drop-in replacement for interactive approval (T19).
 *
 * Resolves decisions automatically based on a HeadlessPolicy and a ConsentStore.
 * Implements the same `requestApproval` interface as the existing ApprovalHandler.
 */

import type { ApprovalRequest, ApprovalResponse } from "../models/index.js";
import { ConsentStore } from "../consent/store.js";

export enum HeadlessPolicy {
  ALLOW_ALL = "allow_all",
  DENY_ALL = "deny_all",
  ALLOW_TRUSTED_ONLY = "allow_trusted_only",
  ALLOW_IF_PRE_APPROVED = "allow_if_pre_approved",
}

/** Minimal logger interface — defaults to console. */
export interface HeadlessLogger {
  info(message: string): void;
}

export class HeadlessApprovalHandler {
  readonly policy: HeadlessPolicy;
  private store: ConsentStore;
  private trusted: Set<string>;
  private logger: HeadlessLogger;

  constructor(
    policy: HeadlessPolicy = HeadlessPolicy.DENY_ALL,
    options: {
      consentStore?: ConsentStore;
      trustedServerIds?: Set<string> | string[];
      logger?: HeadlessLogger;
    } = {},
  ) {
    this.policy = policy;
    this.store = options.consentStore ?? new ConsentStore();
    const trustedInput = options.trustedServerIds;
    this.trusted = trustedInput
      ? new Set(Array.isArray(trustedInput) ? trustedInput : [...trustedInput])
      : new Set();
    this.logger = options.logger ?? console;
  }

  /** Always returns true — this handler always operates headlessly. */
  canHandle(): boolean {
    return true;
  }

  /** Resolve an approval request without user interaction. */
  async requestApproval(serverInfo: ApprovalRequest): Promise<ApprovalResponse> {
    const serverId = serverInfo.server.id;
    const scopes = serverInfo.requested_scopes;

    let approved = false;
    let reason = "";

    switch (this.policy) {
      case HeadlessPolicy.ALLOW_ALL:
        approved = true;
        reason = "Headless policy: allow_all";
        break;
      case HeadlessPolicy.DENY_ALL:
        approved = false;
        reason = "Headless policy: deny_all";
        break;
      case HeadlessPolicy.ALLOW_TRUSTED_ONLY:
        if (this.trusted.has(serverId)) {
          approved = true;
          reason = `Headless policy: ${serverId} is trusted`;
        } else {
          approved = false;
          reason = `Headless policy: ${serverId} not in trusted set`;
        }
        break;
      case HeadlessPolicy.ALLOW_IF_PRE_APPROVED: {
        const record = this.store.check(serverId, scopes);
        if (record !== null) {
          approved = true;
          reason = `Headless policy: ${serverId} pre-approved`;
        } else {
          approved = false;
          reason = `Headless policy: ${serverId} not pre-approved`;
        }
        break;
      }
    }

    // Audit log
    this.logger.info(
      `headless approval ${approved ? "approved" : "denied"} for server=${serverId} scopes=${JSON.stringify(scopes)} reason=${reason}`,
    );

    // Persist decision for audit trail
    this.store.record(serverId, scopes, approved ? "approved" : "denied");

    return {
      approved,
      approved_scopes: approved ? scopes : [],
      duration: serverInfo.duration,
      user_note: reason,
      deny_reason: approved ? undefined : "other",
    };
  }
}
