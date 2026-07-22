"""Tests for T19 — HeadlessApprovalHandler."""

from __future__ import annotations

import logging

import pytest

from pharos_discovery.approval import HeadlessApprovalHandler, HeadlessPolicy
from pharos_discovery.consent import ConsentStore
from pharos_discovery.models import ApprovalRequest, ServerCard


def make_card(server_id: str = "urn:pharos:server-001") -> ServerCard:
    return ServerCard(
        id=server_id,
        display_name="Test Server",
        description="A test server",
        publisher={"id": "did:web:example.com", "name": "TestPub"},
        version="1.0.0",
        transport=["http+sse"],
        capabilities=["search"],
        tools_count=3,
        auth={"type": "none"},
        availability="native",
        source_registry="https://registry.pharos.dev",
        published_at="2026-07-01T00:00:00Z",
        updated_at="2026-07-01T00:00:00Z",
        status="active",
    )


def make_request(server_id: str = "urn:pharos:server-001") -> ApprovalRequest:
    return ApprovalRequest(
        server=make_card(server_id),
        purpose="test",
        requested_scopes=["search"],
        requested_capabilities=["search"],
        duration="session",
        render_id="render-001",
        selection_rationale="testing",
    )


class TestHeadlessPolicy:
    def test_policy_values(self):
        assert HeadlessPolicy.ALLOW_ALL.value == "allow_all"
        assert HeadlessPolicy.DENY_ALL.value == "deny_all"
        assert HeadlessPolicy.ALLOW_TRUSTED_ONLY.value == "allow_trusted_only"
        assert HeadlessPolicy.ALLOW_IF_PRE_APPROVED.value == "allow_if_pre_approved"


class TestCanHandle:
    def test_can_handle_returns_true(self):
        handler = HeadlessApprovalHandler(policy=HeadlessPolicy.DENY_ALL)
        assert handler.can_handle() is True

    def test_can_handle_all_policies(self):
        for policy in HeadlessPolicy:
            handler = HeadlessApprovalHandler(policy=policy)
            assert handler.can_handle() is True


class TestAllowAll:
    def test_approves_request(self):
        handler = HeadlessApprovalHandler(policy=HeadlessPolicy.ALLOW_ALL)
        resp = handler.request_approval(make_request())
        assert resp.approved is True
        assert resp.approved_scopes == ["search"]

    def test_records_decision_in_store(self):
        store = ConsentStore()
        handler = HeadlessApprovalHandler(
            policy=HeadlessPolicy.ALLOW_ALL,
            consent_store=store,
        )
        handler.request_approval(make_request())
        records = store.list_all()
        assert len(records) == 1
        assert records[0].decision == "approved"


class TestDenyAll:
    def test_denies_request(self):
        handler = HeadlessApprovalHandler(policy=HeadlessPolicy.DENY_ALL)
        resp = handler.request_approval(make_request())
        assert resp.approved is False
        assert resp.approved_scopes == []
        assert resp.deny_reason == "other"

    def test_records_denial_in_store(self):
        store = ConsentStore()
        handler = HeadlessApprovalHandler(
            policy=HeadlessPolicy.DENY_ALL,
            consent_store=store,
        )
        handler.request_approval(make_request())
        records = store.list_all()
        assert len(records) == 1
        assert records[0].decision == "denied"


class TestAllowTrustedOnly:
    def test_approves_trusted_server(self):
        handler = HeadlessApprovalHandler(
            policy=HeadlessPolicy.ALLOW_TRUSTED_ONLY,
            trusted_server_ids={"urn:pharos:server-001"},
        )
        resp = handler.request_approval(make_request("urn:pharos:server-001"))
        assert resp.approved is True

    def test_denies_untrusted_server(self):
        handler = HeadlessApprovalHandler(
            policy=HeadlessPolicy.ALLOW_TRUSTED_ONLY,
            trusted_server_ids={"urn:pharos:other"},
        )
        resp = handler.request_approval(make_request("urn:pharos:server-001"))
        assert resp.approved is False
        assert "not in trusted set" in (resp.user_note or "")

    def test_empty_trusted_set_denies_all(self):
        handler = HeadlessApprovalHandler(
            policy=HeadlessPolicy.ALLOW_TRUSTED_ONLY,
        )
        resp = handler.request_approval(make_request())
        assert resp.approved is False


class TestAllowIfPreApproved:
    def test_approves_when_pre_approved(self):
        store = ConsentStore()
        store.record("urn:pharos:server-001", ["search"], "approved")
        handler = HeadlessApprovalHandler(
            policy=HeadlessPolicy.ALLOW_IF_PRE_APPROVED,
            consent_store=store,
        )
        resp = handler.request_approval(make_request())
        assert resp.approved is True
        assert "pre-approved" in (resp.user_note or "")

    def test_denies_when_not_pre_approved(self):
        store = ConsentStore()
        handler = HeadlessApprovalHandler(
            policy=HeadlessPolicy.ALLOW_IF_PRE_APPROVED,
            consent_store=store,
        )
        resp = handler.request_approval(make_request())
        assert resp.approved is False
        assert "not pre-approved" in (resp.user_note or "")

    def test_denies_when_pre_approved_but_scope_mismatch(self):
        store = ConsentStore()
        store.record("urn:pharos:server-001", ["read"], "approved")
        handler = HeadlessApprovalHandler(
            policy=HeadlessPolicy.ALLOW_IF_PRE_APPROVED,
            consent_store=store,
        )
        resp = handler.request_approval(make_request())  # requests "search"
        assert resp.approved is False

    def test_denies_when_pre_approved_but_expired(self):
        store = ConsentStore()
        store.record("urn:pharos:server-001", ["search"], "approved", ttl=-1)
        handler = HeadlessApprovalHandler(
            policy=HeadlessPolicy.ALLOW_IF_PRE_APPROVED,
            consent_store=store,
        )
        resp = handler.request_approval(make_request())
        assert resp.approved is False


class TestAuditLog:
    def test_logs_decision(self, caplog):
        caplog.set_level(logging.INFO, logger="pharos_discovery.approval.headless")
        handler = HeadlessApprovalHandler(policy=HeadlessPolicy.ALLOW_ALL)
        handler.request_approval(make_request())
        assert any("headless approval approved" in r.message for r in caplog.records)

    def test_logs_denial(self, caplog):
        caplog.set_level(logging.INFO, logger="pharos_discovery.approval.headless")
        handler = HeadlessApprovalHandler(policy=HeadlessPolicy.DENY_ALL)
        handler.request_approval(make_request())
        assert any("headless approval denied" in r.message for r in caplog.records)


class TestDropInReplacement:
    def test_returns_approval_response(self):
        from pharos_discovery.models import ApprovalResponse

        handler = HeadlessApprovalHandler(policy=HeadlessPolicy.ALLOW_ALL)
        resp = handler.request_approval(make_request())
        assert isinstance(resp, ApprovalResponse)

    def test_default_policy_is_deny_all(self):
        handler = HeadlessApprovalHandler()
        assert handler.policy is HeadlessPolicy.DENY_ALL

    def test_creates_own_store_if_none_given(self):
        handler = HeadlessApprovalHandler(policy=HeadlessPolicy.ALLOW_ALL)
        handler.request_approval(make_request())
        # Decision should still be recorded in the internal store
        assert len(handler._store.list_all()) == 1
