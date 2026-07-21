from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from pharos_discovery.models.server_card import ServerCard


class ApprovalRequest(BaseModel):
    server: ServerCard
    purpose: str
    requested_scopes: list[str]
    requested_capabilities: list[str]
    duration: Literal["once", "session", "persistent", "trust_on_use"]
    render_id: str
    selection_rationale: str


class ApprovalResponse(BaseModel):
    approved: bool
    approved_scopes: list[str]
    duration: str
    user_note: str | None = None
    deny_reason: (
        Literal[
            "untrusted_publisher",
            "excessive_scopes",
            "wrong_server",
            "cost",
            "other",
        ]
        | None
    ) = None


class ApprovalToken(BaseModel):
    token_id: str
    server_id: str
    approved_scopes: list[str]
    approved_capabilities: list[str]
    approved_oauth_scopes: list[str]
    duration: str
    approved_at: str
    expires_at: str
    signature: str


class PlanApprovalRequest(BaseModel):
    plan_summary: str
    steps: list[ApprovalRequest]
    render_id: str


class PlanApprovalResponse(BaseModel):
    approved: bool
    per_step: list[ApprovalResponse]
    deny_reason: str | None = None
