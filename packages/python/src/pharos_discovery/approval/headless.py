"""Headless approval handler — drop-in replacement for interactive approval (T19).

Implements the same interface as a user-facing approval handler but resolves
decisions automatically based on a :class:`HeadlessPolicy` and a
:class:`~pharos_discovery.consent.store.ConsentStore`.
"""

from __future__ import annotations

import enum
import logging
from typing import Protocol

from pharos_discovery.consent import ConsentStore
from pharos_discovery.models import (
    ApprovalRequest,
    ApprovalResponse,
)

logger = logging.getLogger("pharos_discovery.approval.headless")


class HeadlessPolicy(str, enum.Enum):
    """Policy that determines how the headless handler resolves approvals."""

    ALLOW_ALL = "allow_all"
    DENY_ALL = "deny_all"
    ALLOW_TRUSTED_ONLY = "allow_trusted_only"
    ALLOW_IF_PRE_APPROVED = "allow_if_pre_approved"


class ApprovalHandlerProtocol(Protocol):
    """The interface every approval handler must satisfy."""

    def can_handle(self) -> bool: ...

    def request_approval(self, server_info: ApprovalRequest) -> ApprovalResponse: ...


class HeadlessApprovalHandler:
    """Headless approval handler — no user interaction required.

    Parameters
    ----------
    policy:
        The :class:`HeadlessPolicy` governing decisions.
    consent_store:
        A :class:`~pharos_discovery.consent.store.ConsentStore` used for
        ``ALLOW_IF_PRE_APPROVED`` and to persist decisions.
    trusted_server_ids:
        Set of server IDs considered trusted (used by ``ALLOW_TRUSTED_ONLY``).
    """

    def __init__(
        self,
        policy: HeadlessPolicy = HeadlessPolicy.DENY_ALL,
        consent_store: ConsentStore | None = None,
        trusted_server_ids: set[str] | None = None,
    ) -> None:
        self.policy = policy
        self._store = consent_store or ConsentStore()
        self._trusted = trusted_server_ids or set()

    # ------------------------------------------------------------------ #
    # ApprovalHandler interface
    # ------------------------------------------------------------------ #

    def can_handle(self) -> bool:
        """Always returns ``True`` — this handler always operates headlessly."""
        return True

    def request_approval(self, server_info: ApprovalRequest) -> ApprovalResponse:
        """Resolve an approval request without user interaction."""
        server_id = server_info.server.id
        scopes = server_info.requested_scopes

        approved = False
        reason = ""

        if self.policy is HeadlessPolicy.ALLOW_ALL:
            approved = True
            reason = "Headless policy: allow_all"

        elif self.policy is HeadlessPolicy.DENY_ALL:
            approved = False
            reason = "Headless policy: deny_all"

        elif self.policy is HeadlessPolicy.ALLOW_TRUSTED_ONLY:
            if server_id in self._trusted:
                approved = True
                reason = f"Headless policy: {server_id} is trusted"
            else:
                approved = False
                reason = f"Headless policy: {server_id} not in trusted set"

        elif self.policy is HeadlessPolicy.ALLOW_IF_PRE_APPROVED:
            record = self._store.check(server_id, scopes)
            if record is not None:
                approved = True
                reason = f"Headless policy: {server_id} pre-approved"
            else:
                approved = False
                reason = f"Headless policy: {server_id} not pre-approved"

        # Audit log
        logger.info(
            "headless approval %s for server=%s scopes=%s reason=%s",
            "approved" if approved else "denied",
            server_id,
            scopes,
            reason,
        )

        # Persist decision for audit trail
        self._store.record(
            server_id,
            scopes,
            "approved" if approved else "denied",
        )

        return ApprovalResponse(
            approved=approved,
            approved_scopes=scopes if approved else [],
            duration=server_info.duration,
            user_note=reason,
            deny_reason=None if approved else "other",
        )
