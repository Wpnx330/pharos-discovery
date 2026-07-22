"""Plan review + approval — two-phase install approval (T20)."""

from __future__ import annotations

import hashlib
import time

from pharos_discovery.models.plan import (
    InstallPlan,
    PlanReview,
    RiskLevel,
    ServerInstallEntry,
)
from pharos_discovery.models.server_card import ServerCard

# Risk weights for ordering
_RISK_ORDER: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

# Capability → risk level mapping
_CAPABILITY_RISK: dict[str, RiskLevel] = {
    "filesystem": "high",
    "file_write": "high",
    "file_read": "medium",
    "network": "medium",
    "http": "medium",
    "secrets": "critical",
    "secret_access": "critical",
    "credentials": "critical",
    "shell": "critical",
    "exec": "critical",
    "process": "high",
    "env": "high",
    "environment": "high",
    "database": "high",
    "db": "high",
    "search": "low",
    "read": "low",
    "tools": "low",
    "prompts": "low",
    "resources": "low",
}


class PlanReviewer:
    """Creates and reviews :class:`InstallPlan` objects with risk assessment."""

    def assess_risk(self, server_card: ServerCard) -> RiskLevel:
        """Assess the risk level of a server based on its capabilities.

        Rules (highest wins):
        - ``secrets`` / ``credentials`` / ``shell`` / ``exec`` → ``critical``
        - ``filesystem`` / ``file_write`` / ``process`` / ``database`` → ``high``
        - ``network`` / ``http`` / ``file_read`` → ``medium``
        - everything else → ``low``
        """
        caps = [c.lower() for c in server_card.capabilities]
        highest: RiskLevel = "low"
        for cap in caps:
            level = _CAPABILITY_RISK.get(cap, "low")
            if _RISK_ORDER[level] > _RISK_ORDER[highest]:
                highest = level
        return highest

    def create_plan(
        self,
        servers: list[tuple[ServerCard, list[str], list[str]]],
    ) -> InstallPlan:
        """Create an :class:`InstallPlan` from a list of ``(card, scopes, capabilities)`` tuples.

        Each server's risk is assessed via :meth:`assess_risk`.
        """
        entries: list[ServerInstallEntry] = []
        raw = f"plan:{time.time_ns()}"
        plan_id = "plan_" + hashlib.sha256(raw.encode()).hexdigest()[:12]

        for card, scopes, capabilities in servers:
            entries.append(
                ServerInstallEntry(
                    server=card,
                    scopes=list(scopes),
                    capabilities=list(capabilities),
                    risk=self.assess_risk(card),
                )
            )

        return InstallPlan(
            id=plan_id,
            entries=entries,
            created_at=time.time(),
        )

    def review_plan(self, plan: InstallPlan) -> PlanReview:
        """Review an :class:`InstallPlan` and produce a :class:`PlanReview`."""
        summary: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        highest: RiskLevel = "low"
        recs: list[str] = []

        for entry in plan.entries:
            summary[entry.risk] += 1
            if _RISK_ORDER[entry.risk] > _RISK_ORDER[highest]:
                highest = entry.risk

            # Recommendations
            if entry.risk == "critical":
                recs.append(
                    f"Server {entry.server.id} has critical risk — "
                    "manual review strongly recommended before approval."
                )
            elif entry.risk == "high":
                recs.append(
                    f"Server {entry.server.id} has high risk — "
                    "verify scopes are minimal."
                )
            caps_lower = {c.lower() for c in entry.capabilities}
            if "secrets" in caps_lower or "credentials" in caps_lower:
                recs.append(
                    f"Server {entry.server.id} requests secret access — "
                    "ensure credentials are stored securely."
                )

        if highest in ("critical", "high") and not recs:
            recs.append("High-risk plan — review carefully before approving.")

        return PlanReview(
            plan_id=plan.id,
            risk_summary=summary,
            highest_risk=highest,
            recommendations=recs,
            overall_risk=highest,
        )


class PlanApproval:
    """Approves or rejects :class:`InstallPlan` objects."""

    def approve(
        self,
        plan: InstallPlan,
        server_ids: set[str] | None = None,
    ) -> InstallPlan:
        """Approve *plan*. If *server_ids* is given, approve only those servers.

        Raises ``ValueError`` if *server_ids* references servers not in the plan.
        """
        plan.rejected = False
        plan.reject_reason = None

        if server_ids is None:
            plan.approved_server_ids = {e.server.id for e in plan.entries}
        else:
            valid_ids = {e.server.id for e in plan.entries}
            unknown = server_ids - valid_ids
            if unknown:
                raise ValueError(
                    f"Unknown server IDs in approval: {', '.join(sorted(unknown))}"
                )
            plan.approved_server_ids = set(server_ids)

        return plan

    def reject(self, plan: InstallPlan, reason: str) -> InstallPlan:
        """Reject *plan* with a reason."""
        plan.rejected = True
        plan.reject_reason = reason
        plan.approved_server_ids = set()
        return plan

    def is_approved(self, plan: InstallPlan, server_id: str) -> bool:
        """Return ``True`` if *server_id* is approved in *plan*."""
        if plan.rejected:
            return False
        return server_id in plan.approved_server_ids
