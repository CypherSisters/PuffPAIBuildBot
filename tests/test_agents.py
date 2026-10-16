"""Tests for the seven-agent team: construction, the structural Sentinel veto,
normal routing, and Concierge's empty credential scope.

These exercise the public guarantees of the agent layer without any of the
later-phase integrations (HA, RAG, vault, workflows): the bus routes every
state-changing envelope through Sentinel and fails closed, and the Concierge
holds no secrets.

Run:
    pytest tests/test_agents.py
"""
from __future__ import annotations

import pytest

from puffpai.agents import (
    AgentBus,
    Budget,
    Envelope,
    ReviewVerdict,
    ROSTER,
    build_team,
)
from puffpai.agents.base import BaseAgent
from puffpai.security.audit_log import AuditLog
from puffpai.security.secrets_provider import AgentScope, SecretAccessDenied


# ── helpers ─────────────────────────────────────────────────────────────────

class _RecordingAgent(BaseAgent):
    """Minimal agent that records whether its handler was reached."""

    def __init__(self, name: str) -> None:
        super().__init__(name=name, scope=AgentScope.SYSTEM)
        self.calls: list[str] = []

    def handle(self, envelope: Envelope) -> dict:
        self.calls.append(envelope.intent)
        return {"ok": True, "handled": envelope.intent}


@pytest.fixture
def audit(tmp_path) -> AuditLog:
    """An isolated audit log so tests never touch the shared security log."""
    return AuditLog(log_file=str(tmp_path / "audit.jsonl"))


# ── construction ────────────────────────────────────────────────────────────

def test_build_team_constructs_seven_agents(audit) -> None:
    bus = build_team(audit=audit)
    agents = bus.agents()
    assert len(agents) == 7
    assert set(agents) == {
        "concierge", "steward", "sentinel", "archivist", "majordomo", "caretaker",
        "toolsmith",
    }
    # Roster and the wired bus agree on names.
    assert {e.name for e in ROSTER} == set(agents)


def test_build_team_wires_sentinel_as_reviewer(audit) -> None:
    bus = build_team(audit=audit)
    health = bus.health()
    assert health["reviewer"] == "sentinel"
    assert health["reviewer_wired"] is True
    assert health["ok"] is True


# ── the structural Sentinel veto ────────────────────────────────────────────

def test_state_changing_blocked_without_reviewer(audit) -> None:
    """Fail-closed: a state-changing envelope is denied when no reviewer is
    wired, and the target handler is never reached."""
    bus = AgentBus(audit=audit, reviewer_name="sentinel")  # no reviewer registered
    target = _RecordingAgent("steward")
    bus.register(target)

    env = Envelope(intent="control_device", origin_agent="test",
                   state_changing=True)
    reply = bus.send(env, to="steward")

    assert reply.get("ok") is False
    assert reply.get("denied") is True
    assert target.calls == []  # handler never ran


def test_state_changing_egress_vetoed_by_sentinel(audit) -> None:
    """The real Sentinel hard-denies unbudgeted egress on a state change."""
    bus = build_team(audit=audit)
    env = Envelope(
        intent="run_workflow",
        payload={"egress": True},
        origin_agent="test",
        state_changing=True,
        budget=Budget(max_actions=1, allow_egress=False),
    )
    reply = bus.send(env, to="majordomo")
    assert reply.get("denied") is True
    assert reply.get("by") == "sentinel"
    assert reply.get("verdict") == "deny"

    # The veto is audited (no silent block).
    vetoes = audit.query(action="bus_veto")
    assert any("run_workflow" in v.get("details", "") for v in vetoes)


def test_state_changing_approved_is_delivered(audit) -> None:
    """A benign state change Sentinel allows is delivered to the handler."""
    bus = build_team(audit=audit)
    env = Envelope(
        intent="enqueue",
        payload={"task_intent": "nightly_backup", "target": "caretaker"},
        origin_agent="test",
        state_changing=True,
        budget=Budget(max_actions=2),
    )
    reply = bus.send(env, to="majordomo")
    assert reply.get("ok") is True
    assert reply.get("queued") is True


# ── normal (non-state-changing) routing ─────────────────────────────────────

def test_non_state_changing_routes_normally(audit) -> None:
    """A read-only envelope skips the veto and reaches its handler."""
    bus = build_team(audit=audit)
    reply = bus.send(Envelope(intent="health", origin_agent="test"),
                     to="caretaker")
    assert reply.get("ok") is True
    assert "healthy" in reply  # caretaker health report shape
    # It routed as a normal send, not a veto.
    assert not audit.query(action="bus_veto")
    assert audit.query(action="bus_send")


def test_concierge_routes_read_only_through_bus(audit) -> None:
    """Concierge classifies a message and delegates through the bus."""
    bus = build_team(audit=audit)
    env = Envelope(
        intent="chat",
        payload={"text": "give me my daily briefing"},
        requester="owner",
        origin_agent="test",
    )
    reply = bus.send(env, to="concierge")
    assert reply.get("ok") is True
    assert reply.get("category") == "briefing"
    assert reply.get("delegated_to") == "majordomo"
    assert (reply.get("delegate_reply") or {}).get("ok") is True


# ── credential scoping ──────────────────────────────────────────────────────

def test_concierge_holds_no_credentials(audit) -> None:
    """The Concierge's scope is empty: it cannot even ask for a secret."""
    bus = build_team(audit=audit)
    concierge = bus.get("concierge")
    assert concierge.scope is AgentScope.CONCIERGE

    with pytest.raises(SecretAccessDenied):
        concierge.secret("HA_TOKEN")

    health = concierge.health()
    assert health["holds_secrets"] is False


def test_steward_scope_cannot_read_workflow_key(audit) -> None:
    """Scope is enforced per agent: the Steward is denied Majordomo's key."""
    bus = build_team(audit=audit)
    steward = bus.get("steward")
    with pytest.raises(SecretAccessDenied):
        steward.secret("N8N_API_KEY")


# ── reviewer contract ───────────────────────────────────────────────────────

def test_reviewer_returning_deny_blocks_delivery(audit) -> None:
    """A reviewer that denies structurally blocks the envelope."""

    class _DenyAll(BaseAgent):
        def handle(self, envelope: Envelope) -> dict:
            return {"ok": True}

        def review(self, envelope: Envelope, target=None) -> ReviewVerdict:
            return ReviewVerdict(allowed=False, verdict="deny", reason="nope")

    bus = AgentBus(audit=audit, reviewer_name="reviewer")
    bus.register(_DenyAll(name="reviewer", scope=AgentScope.SENTINEL))
    target = _RecordingAgent("worker")
    bus.register(target)
    bus.set_reviewer("reviewer")

    reply = bus.send(Envelope(intent="do", origin_agent="test",
                              state_changing=True), to="worker")
    assert reply.get("denied") is True
    assert reply.get("reason") == "nope"
    assert target.calls == []
