"""Tests for T20 — PlanReviewer and PlanApproval."""

from __future__ import annotations

import pytest

from pharos_discovery.plan import PlanApproval, PlanReviewer
from pharos_discovery.models import ServerCard


def make_card(
    server_id: str = "urn:pharos:s1",
    capabilities: list[str] | None = None,
) -> ServerCard:
    return ServerCard(
        id=server_id,
        display_name="Test Server",
        description="A test server",
        publisher={"id": "did:web:example.com", "name": "TestPub"},
        version="1.0.0",
        transport=["http+sse"],
        capabilities=capabilities if capabilities is not None else ["search"],
        tools_count=3,
        auth={"type": "none"},
        availability="native",
        source_registry="https://registry.pharos.dev",
        published_at="2026-07-01T00:00:00Z",
        updated_at="2026-07-01T00:00:00Z",
        status="active",
    )


class TestAssessRisk:
    @pytest.mark.parametrize(
        "caps,expected",
        [
            (["search"], "low"),
            (["read", "tools"], "low"),
            (["network"], "medium"),
            (["http"], "medium"),
            (["file_read"], "medium"),
            (["filesystem"], "high"),
            (["file_write"], "high"),
            (["process"], "high"),
            (["database"], "high"),
            (["secrets"], "critical"),
            (["credentials"], "critical"),
            (["shell"], "critical"),
            (["exec"], "critical"),
            ([], "low"),
        ],
    )
    def test_capability_risk_mapping(self, caps, expected):
        reviewer = PlanReviewer()
        assert reviewer.assess_risk(make_card(capabilities=caps)) == expected

    def test_highest_risk_wins(self):
        reviewer = PlanReviewer()
        card = make_card(capabilities=["search", "filesystem", "secrets"])
        assert reviewer.assess_risk(card) == "critical"

    def test_case_insensitive(self):
        reviewer = PlanReviewer()
        card = make_card(capabilities=["FILESYSTEM", "Search"])
        assert reviewer.assess_risk(card) == "high"

    def test_unknown_capability_is_low(self):
        reviewer = PlanReviewer()
        card = make_card(capabilities=["custom_cap"])
        assert reviewer.assess_risk(card) == "low"


class TestCreatePlan:
    def test_create_plan_returns_install_plan(self):
        from pharos_discovery.models import InstallPlan

        reviewer = PlanReviewer()
        card = make_card()
        plan = reviewer.create_plan([(card, ["search"], ["search"])])
        assert isinstance(plan, InstallPlan)
        assert len(plan.entries) == 1
        assert plan.entries[0].server.id == "urn:pharos:s1"
        assert plan.entries[0].scopes == ["search"]
        assert plan.entries[0].risk == "low"

    def test_create_plan_multiple_servers(self):
        reviewer = PlanReviewer()
        plan = reviewer.create_plan([
            (make_card("s1", ["search"]), ["search"], ["search"]),
            (make_card("s2", ["filesystem"]), ["read"], ["filesystem"]),
        ])
        assert len(plan.entries) == 2
        assert plan.entries[0].risk == "low"
        assert plan.entries[1].risk == "high"

    def test_create_plan_has_unique_id(self):
        reviewer = PlanReviewer()
        card = make_card()
        plan1 = reviewer.create_plan([(card, [], [])])
        plan2 = reviewer.create_plan([(card, [], [])])
        assert plan1.id != plan2.id

    def test_create_plan_id_format(self):
        reviewer = PlanReviewer()
        plan = reviewer.create_plan([])
        assert plan.id.startswith("plan_")

    def test_create_plan_empty(self):
        reviewer = PlanReviewer()
        plan = reviewer.create_plan([])
        assert plan.entries == []
        assert plan.approved_server_ids == set()
        assert plan.rejected is False


class TestReviewPlan:
    def test_review_plan_risk_summary(self):
        reviewer = PlanReviewer()
        plan = reviewer.create_plan([
            (make_card("s1", ["search"]), [], ["search"]),
            (make_card("s2", ["filesystem"]), [], ["filesystem"]),
            (make_card("s3", ["secrets"]), [], ["secrets"]),
        ])
        review = reviewer.review_plan(plan)
        assert review.risk_summary["low"] == 1
        assert review.risk_summary["high"] == 1
        assert review.risk_summary["critical"] == 1
        assert review.highest_risk == "critical"

    def test_review_plan_recommendations_for_critical(self):
        reviewer = PlanReviewer()
        plan = reviewer.create_plan([
            (make_card("s1", ["secrets"]), [], ["secrets"]),
        ])
        review = reviewer.review_plan(plan)
        assert len(review.recommendations) >= 1
        assert any("critical risk" in r for r in review.recommendations)

    def test_review_plan_recommendations_for_secret_access(self):
        reviewer = PlanReviewer()
        plan = reviewer.create_plan([
            (make_card("s1", ["credentials"]), [], ["credentials"]),
        ])
        review = reviewer.review_plan(plan)
        assert any("secret access" in r for r in review.recommendations)

    def test_review_plan_recommendations_for_high(self):
        reviewer = PlanReviewer()
        plan = reviewer.create_plan([
            (make_card("s1", ["filesystem"]), [], ["filesystem"]),
        ])
        review = reviewer.review_plan(plan)
        assert any("high risk" in r for r in review.recommendations)

    def test_review_plan_low_risk_no_recommendations(self):
        reviewer = PlanReviewer()
        plan = reviewer.create_plan([
            (make_card("s1", ["search"]), [], ["search"]),
        ])
        review = reviewer.review_plan(plan)
        assert review.recommendations == []
        assert review.overall_risk == "low"

    def test_review_plan_empty(self):
        reviewer = PlanReviewer()
        plan = reviewer.create_plan([])
        review = reviewer.review_plan(plan)
        assert review.risk_summary == {"low": 0, "medium": 0, "high": 0, "critical": 0}
        assert review.highest_risk == "low"

    def test_review_plan_id_matches(self):
        reviewer = PlanReviewer()
        plan = reviewer.create_plan([])
        review = reviewer.review_plan(plan)
        assert review.plan_id == plan.id


class TestPlanApprovalApprove:
    def test_approve_all(self):
        reviewer = PlanReviewer()
        approval = PlanApproval()
        plan = reviewer.create_plan([
            (make_card("s1"), [], []),
            (make_card("s2"), [], []),
        ])
        approval.approve(plan)
        assert plan.rejected is False
        assert plan.approved_server_ids == {"s1", "s2"}

    def test_approve_specific_servers(self):
        reviewer = PlanReviewer()
        approval = PlanApproval()
        plan = reviewer.create_plan([
            (make_card("s1"), [], []),
            (make_card("s2"), [], []),
        ])
        approval.approve(plan, server_ids={"s1"})
        assert plan.approved_server_ids == {"s1"}

    def test_approve_unknown_server_raises(self):
        reviewer = PlanReviewer()
        approval = PlanApproval()
        plan = reviewer.create_plan([(make_card("s1"), [], [])])
        with pytest.raises(ValueError, match="Unknown server IDs"):
            approval.approve(plan, server_ids={"unknown"})

    def test_approve_clears_previous_rejection(self):
        reviewer = PlanReviewer()
        approval = PlanApproval()
        plan = reviewer.create_plan([(make_card("s1"), [], [])])
        approval.reject(plan, "nope")
        approval.approve(plan)
        assert plan.rejected is False
        assert plan.reject_reason is None


class TestPlanApprovalReject:
    def test_reject_sets_flag(self):
        reviewer = PlanReviewer()
        approval = PlanApproval()
        plan = reviewer.create_plan([(make_card("s1"), [], [])])
        approval.reject(plan, "too risky")
        assert plan.rejected is True
        assert plan.reject_reason == "too risky"
        assert plan.approved_server_ids == set()


class TestPlanApprovalIsApproved:
    def test_is_approved_true(self):
        reviewer = PlanReviewer()
        approval = PlanApproval()
        plan = reviewer.create_plan([(make_card("s1"), [], [])])
        approval.approve(plan)
        assert approval.is_approved(plan, "s1") is True

    def test_is_approved_false_when_not_in_set(self):
        reviewer = PlanReviewer()
        approval = PlanApproval()
        plan = reviewer.create_plan([
            (make_card("s1"), [], []),
            (make_card("s2"), [], []),
        ])
        approval.approve(plan, server_ids={"s1"})
        assert approval.is_approved(plan, "s2") is False

    def test_is_approved_false_when_rejected(self):
        reviewer = PlanReviewer()
        approval = PlanApproval()
        plan = reviewer.create_plan([(make_card("s1"), [], [])])
        approval.approve(plan)
        approval.reject(plan, "changed mind")
        assert approval.is_approved(plan, "s1") is False

    def test_is_approved_false_for_unknown_server(self):
        reviewer = PlanReviewer()
        approval = PlanApproval()
        plan = reviewer.create_plan([(make_card("s1"), [], [])])
        approval.approve(plan)
        assert approval.is_approved(plan, "unknown") is False

    def test_is_approved_false_when_not_approved_yet(self):
        reviewer = PlanReviewer()
        approval = PlanApproval()
        plan = reviewer.create_plan([(make_card("s1"), [], [])])
        # Never approved or rejected
        assert approval.is_approved(plan, "s1") is False
