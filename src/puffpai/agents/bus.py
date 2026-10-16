"""AgentBus — in-process hub-and-spoke message bus with a structural veto.

The bus is the *only* way agents talk to each other. That single choke point is
where two guarantees are enforced so no caller can skip them:

1. **Sentinel veto (structural).** Every ``state_changing`` envelope routed to
   any agent is first handed to the registered policy reviewer (Sentinel). If
   the reviewer denies it, the envelope is **never delivered** and the denial is
   audited. This is not advisory: unlike the legacy consent-gate dry-run
   (``PUFFPAI_ENFORCE_CONSENT``), the in-process veto always blocks on a deny —
   the more restrictive, fail-closed choice. If no reviewer is registered, every
   state-changing send is denied (you cannot lose the veto by forgetting to wire
   Sentinel).

2. **Audit trail.** Every send, veto, budget abort, and handler error is written
   to the :class:`~puffpai.security.audit_log.AuditLog` with a redacted envelope
   view (keys, not values).

The seam is deliberately transport-shaped: ``send(envelope, to)`` could become an
IPC/HTTP call between systemd units without any agent changing, because agents
already never hold references to one another — only their ``name`` on the bus.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from typing import Any

from puffpai.logging_config import get_logger
from puffpai.security.audit_log import AuditLog

from .envelope import BudgetExceeded, Envelope, ReviewVerdict

logger = get_logger(__name__)

DEFAULT_REVIEWER = "sentinel"


class AgentBus:
    """Registry + router for the product-agent team."""

    def __init__(self, audit: AuditLog | None = None,
                 reviewer_name: str = DEFAULT_REVIEWER) -> None:
        self._agents: dict[str, Any] = {}
        self._audit = audit or AuditLog()
        self._reviewer_name = reviewer_name

    # ── registration ────────────────────────────────────────────────
    def register(self, agent) -> object:
        """Add an agent to the bus and wire the bus back into it."""
        name = getattr(agent, "name", None)
        if not name:
            raise ValueError("agent must have a non-empty .name")
        if name in self._agents:
            raise ValueError(f"agent name already registered: {name!r}")
        self._agents[name] = agent
        binder = getattr(agent, "bind_bus", None)
        if callable(binder):
            binder(self)
        return agent

    def set_reviewer(self, name: str) -> None:
        """Designate which registered agent performs policy review (the veto)."""
        self._reviewer_name = name

    def get(self, name: str):
        return self._agents.get(name)

    def agents(self) -> dict[str, object]:
        return dict(self._agents)

    # ── routing ─────────────────────────────────────────────────────
    def send(self, envelope: Envelope, to: str) -> dict:
        """Route *envelope* to agent *to*, enforcing veto + audit + budget."""
        agent = self._agents.get(to)
        if agent is None:
            self._audit.log(
                action="bus_send", user=envelope.requester, result="error",
                details=f"unknown agent {to!r}", extra=envelope.audit_view(),
            )
            return {"ok": False, "error": f"unknown agent: {to}"}

        # ── Sentinel structural veto for state-changing envelopes ──
        if envelope.state_changing and to != self._reviewer_name:
            verdict = self._review(envelope, to)
            if not verdict.allowed:
                self._audit.log(
                    action="bus_veto", user=envelope.requester, result="denied",
                    details=f"{envelope.origin_agent}->{to}:{envelope.intent} "
                            f"[{verdict.verdict}] {verdict.reason}",
                    extra=envelope.audit_view(),
                )
                return {
                    "ok": False,
                    "denied": True,
                    "by": self._reviewer_name,
                    "verdict": verdict.verdict,
                    "reason": verdict.reason,
                }

        self._audit.log(
            action="bus_send", user=envelope.requester, result="success",
            details=f"{envelope.origin_agent}->{to}:{envelope.intent}",
            extra=envelope.audit_view(),
        )

        try:
            reply = agent.handle(envelope)
        except BudgetExceeded as exc:
            self._audit.log(
                action="bus_budget_abort", user=envelope.requester, result="aborted",
                details=f"{envelope.origin_agent}->{to}:{envelope.intent}: {exc}",
                extra=envelope.audit_view(),
            )
            return {"ok": False, "aborted": "budget", "reason": str(exc)}
        except Exception as exc:  # a crashing handler must not take down the bus
            logger.exception("agent %s failed handling %s", to, envelope.intent)
            self._audit.log(
                action="bus_error", user=envelope.requester, result="error",
                details=f"{envelope.origin_agent}->{to}:{envelope.intent}: {exc}",
                extra=envelope.audit_view(),
            )
            return {"ok": False, "error": str(exc)}

        if not isinstance(reply, dict):
            return {"ok": True, "result": reply}
        return reply

    # ── policy review ───────────────────────────────────────────────
    def _review(self, envelope: Envelope, to: str) -> ReviewVerdict:
        """Ask the registered reviewer to rule on a state-changing envelope.

        Fail-closed: a missing reviewer or a crashing review is a *deny*.
        """
        reviewer = self._agents.get(self._reviewer_name)
        review_fn = getattr(reviewer, "review", None) if reviewer is not None else None
        if not callable(review_fn):
            return ReviewVerdict(
                allowed=False, verdict="deny",
                reason=f"no policy reviewer {self._reviewer_name!r} registered",
            )
        try:
            verdict = review_fn(envelope, target=to)
        except Exception as exc:
            logger.exception("policy reviewer crashed")
            return ReviewVerdict(
                allowed=False, verdict="deny", reason=f"review error: {exc}"
            )
        if isinstance(verdict, ReviewVerdict):
            return verdict
        if isinstance(verdict, dict):
            allowed = bool(verdict.get("allowed", False))
            return ReviewVerdict(
                allowed=allowed,
                verdict=verdict.get("verdict", "allow" if allowed else "deny"),
                reason=str(verdict.get("reason", "")),
            )
        # Unrecognized return type -> fail closed.
        return ReviewVerdict(allowed=False, verdict="deny",
                             reason="reviewer returned unrecognized verdict")

    # ── health ──────────────────────────────────────────────────────
    def health(self) -> dict:
        """Roster + wiring snapshot for /agents/status. No LLM required."""
        reviewer_ok = callable(getattr(self._agents.get(self._reviewer_name), "review", None))
        return {
            "ok": bool(self._agents) and reviewer_ok,
            "agent_count": len(self._agents),
            "reviewer": self._reviewer_name,
            "reviewer_wired": reviewer_ok,
            "agents": sorted(self._agents.keys()),
        }


__all__ = ["AgentBus", "DEFAULT_REVIEWER"]
