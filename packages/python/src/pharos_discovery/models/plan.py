"""Plan approval models — two-phase install plan + risk assessment (T20)."""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, Field

from pharos_discovery.models.server_card import ServerCard


RiskLevel = Literal["low", "medium", "high", "critical"]


class ServerInstallEntry(BaseModel):
    """A single server entry inside an :class:`InstallPlan`."""

    server: ServerCard
    scopes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    risk: RiskLevel = "low"


class InstallPlan(BaseModel):
    """A plan of servers to install with risk assessments."""

    id: str
    entries: list[ServerInstallEntry] = Field(default_factory=list)
    created_at: float
    approved_server_ids: set[str] = Field(default_factory=set)
    rejected: bool = False
    reject_reason: str | None = None

    model_config = {"arbitrary_types_allowed": True}


class PlanReview(BaseModel):
    """Result of reviewing an :class:`InstallPlan`."""

    plan_id: str
    risk_summary: dict[str, int] = Field(
        default_factory=lambda: {"low": 0, "medium": 0, "high": 0, "critical": 0}
    )
    highest_risk: RiskLevel = "low"
    recommendations: list[str] = Field(default_factory=list)
    overall_risk: RiskLevel = "low"
