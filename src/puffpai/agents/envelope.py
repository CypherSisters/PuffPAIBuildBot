"""Message envelope + budget primitives for the PuffPAI product-agent team.

Every request that moves between agents travels as an :class:`Envelope`. The
envelope carries *who is asking* (``requester`` — a user/profile identity, not
an agent), *which agent minted it* (``origin_agent``), the ``intent`` + free-form
``payload``, and a :class:`Budget` that bounds how much work the request may do.

Two invariants live here so that no caller can forget them:

* ``state_changing`` is an explicit flag on the envelope, not something the bus
  has to infer. Anything that mutates the house, the vault, the network, or a
  service must set it — that is what triggers Sentinel's structural veto in
  ``bus.py``.
* Budgets *decrement as actions execute* (:meth:`Budget.consume`). Exceeding the
  action count, the wall-clock limit, or requesting egress the budget forbids
  raises :class:`BudgetExceeded`, which the bus turns into an audited abort.

``Envelope.audit_view`` deliberately emits payload **keys only** — never values —
so the audit ledger can record what flowed without leaking PII or secrets.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class BudgetExceeded(RuntimeError):
    """Raised when an envelope's action/time/egress budget is exhausted.

    The bus catches this and converts it into an audited ``aborted`` reply,
    so a runaway or misbehaving handler is contained rather than allowed to
    keep spending.
    """


@dataclass
class Budget:
    """Bounds the work a single envelope may perform.

    Args:
        max_actions: hard cap on the number of side-effecting steps.
        max_seconds: wall-clock ceiling from the first :meth:`consume`.
        allow_egress: whether the request may reach outside the appliance.
            Defaults to *False* (fail-closed) — the more restrictive choice.
    """

    max_actions: int = 1
    max_seconds: float = 30.0
    allow_egress: bool = False
    actions_used: int = 0
    _started_at: float | None = field(default=None, repr=False)

    def start(self) -> None:
        """Begin the wall-clock timer (idempotent)."""
        if self._started_at is None:
            self._started_at = time.monotonic()

    @property
    def elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        return time.monotonic() - self._started_at

    @property
    def actions_remaining(self) -> int:
        return max(0, self.max_actions - self.actions_used)

    def consume(self, n: int = 1, *, egress: bool = False) -> None:
        """Charge *n* actions against the budget.

        Raises :class:`BudgetExceeded` if this would overrun the action count,
        if the time limit has already been passed, or if ``egress`` is requested
        but not permitted. Fail-closed: the check happens *before* the action is
        counted as spent, so an over-budget action never executes.
        """
        self.start()
        if egress and not self.allow_egress:
            raise BudgetExceeded("egress requested but budget.allow_egress is False")
        if self.elapsed > self.max_seconds:
            raise BudgetExceeded(
                f"time budget exhausted ({self.elapsed:.2f}s > {self.max_seconds}s)"
            )
        if self.actions_used + n > self.max_actions:
            raise BudgetExceeded(
                f"action budget exhausted ({self.actions_used + n} > {self.max_actions})"
            )
        self.actions_used += n

    def snapshot(self) -> dict:
        """Non-sensitive view for audit entries."""
        return {
            "max_actions": self.max_actions,
            "actions_used": self.actions_used,
            "max_seconds": self.max_seconds,
            "elapsed": round(self.elapsed, 3),
            "allow_egress": self.allow_egress,
        }


@dataclass
class Envelope:
    """A single inter-agent request.

    ``requester`` is a *human/profile* identity (e.g. ``"owner"``, ``"child"``,
    ``"guest"``) used for consent + privacy enforcement — it is never an agent
    name. ``origin_agent`` records which agent put the envelope on the bus.
    """

    intent: str
    payload: dict = field(default_factory=dict)
    requester: str = "system"
    origin_agent: str = "system"
    budget: Budget = field(default_factory=Budget)
    state_changing: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=_utcnow)

    def audit_view(self) -> dict:
        """Redacted view for the audit ledger — payload keys only, no values."""
        try:
            payload_keys = sorted(str(k) for k in (self.payload or {}).keys())
        except Exception:
            payload_keys = []
        return {
            "id": self.id,
            "intent": self.intent,
            "requester": self.requester,
            "origin_agent": self.origin_agent,
            "state_changing": self.state_changing,
            "payload_keys": payload_keys,
            "budget": self.budget.snapshot(),
            "created_at": self.created_at,
        }


@dataclass
class ReviewVerdict:
    """Result of Sentinel's policy review of an envelope.

    ``allowed`` is what the bus enforces structurally (fail-closed). ``verdict``
    mirrors the consent-gate vocabulary (``allow`` | ``flag`` | ``deny``) for the
    audit ledger; a ``flag`` is allowed-but-noted.
    """

    allowed: bool
    reason: str = ""
    verdict: str = "allow"


__all__ = ["Budget", "BudgetExceeded", "Envelope", "ReviewVerdict"]
