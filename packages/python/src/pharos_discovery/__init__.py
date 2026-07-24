"""Pharos Discovery SDK — search, approve, and connect to MCP servers."""

__version__ = "0.1.0"

from pharos_discovery.security import Blocklist, KeyPinStore
from pharos_discovery.events import EVENT_TYPES, SSEEvent, EventSubscriber
from pharos_discovery.consent import ConsentRecord, ConsentStore
from pharos_discovery.approval import (
    ApprovalEngine,
    HeadlessApprovalHandler,
    HeadlessPolicy,
)
from pharos_discovery.plan import PlanApproval, PlanReviewer
from pharos_discovery.connection.oauth import (
    BrowserOAuthRenderer,
    OAuthFlowHandler,
    OAuthRenderer,
    TerminalOAuthRenderer,
)
from pharos_discovery.models import (
    InstallPlan,
    PlanReview,
    RiskLevel,
    ServerInstallEntry,
)

__all__ = [
    "ApprovalEngine",
    "Blocklist",
    "BrowserOAuthRenderer",
    "ConsentRecord",
    "ConsentStore",
    "EVENT_TYPES",
    "EventSubscriber",
    "HeadlessApprovalHandler",
    "HeadlessPolicy",
    "InstallPlan",
    "KeyPinStore",
    "OAuthFlowHandler",
    "OAuthRenderer",
    "PlanApproval",
    "PlanReview",
    "PlanReviewer",
    "RiskLevel",
    "SSEEvent",
    "ServerInstallEntry",
    "TerminalOAuthRenderer",
]
