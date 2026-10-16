"""Sentinel — WF-3 privacy & security enforcement (the veto + audit sidecar).

Sentinel is the only agent with veto authority. The bus hands it every
``state_changing`` envelope *before* delivery; :meth:`review` returns a
:class:`~puffpai.agents.envelope.ReviewVerdict` and the bus blocks on a deny.
That structural veto — and the fail-closed behaviour when no reviewer is wired —
is the public guarantee: no state change happens silently.

The content-policy part of the verdict is delegated to
``puffpai.security.consent_gate`` rather than re-implemented here:

* ``payload["targets"]`` — a resolved entity list → :func:`gate_targets`.
* ``payload["automation"]`` — an LLM-authored automation dict → :func:`sentinel_review`.
* ``payload["egress"]`` truthy but ``budget.allow_egress`` false → deny.
* any target flagged as quarantined by the optional monitoring probe → deny.

The ``check_addition`` intent is Drawbridge's check of one addition (a live
card or a book) before it is installed: the envelope is reviewed and audited
like any state change, then :func:`puffpai.drawbridge.check_addition` applies
the one block/warn/allow policy and records the verdict.

In this build ``consent_gate`` is a reference placeholder that records requests
to a consent ledger and returns *pending*; the per-device policy engine ships in
a later phase. The hard, structural checks in this file (unbudgeted egress,
quarantine) are enforced regardless. Device monitoring is an optional capability
that is simply reported as unavailable when its probe is not present in this
build.

Credentials: the audit-ledger write key (``AgentScope.SENTINEL``). No
outbound/cloud credentials.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from puffpai.logging_config import get_logger
from puffpai.security.audit_log import AuditLog
from puffpai.security.consent_gate import gate_targets, sentinel_review
from puffpai.security.secrets_provider import AgentScope, SecretsProvider

from .base import BaseAgent
from .envelope import Envelope, ReviewVerdict

logger = get_logger(__name__)


class SentinelAgent(BaseAgent):
    """Policy reviewer + security-state reporter."""

    name = "sentinel"
    scope = AgentScope.SENTINEL

    def __init__(self, *, secrets: SecretsProvider | None = None,
                 audit: AuditLog | None = None, device_monitor=None) -> None:
        super().__init__(secrets=secrets)
        self._audit = audit or AuditLog()
        # Optional live device monitor. May be injected (e.g. in tests); this
        # build ships none, so it stays None and monitoring reports unavailable.
        self._device_monitor = device_monitor

    # ── policy review (the veto) ────────────────────────────────────
    def review(self, envelope: Envelope, target: str | None = None) -> ReviewVerdict:
        """Rule on a state-changing envelope. Called by the bus, not via send().

        Returns allowed=False to structurally block. Every verdict is audited.
        """
        payload = envelope.payload or {}
        reasons: list[str] = []
        verdict = "allow"

        # 1. Resolved target entities → consent gate.
        targets = payload.get("targets") or []
        if isinstance(targets, str):
            targets = [targets]
        targets = [t for t in targets if t]
        if targets:
            gate = gate_targets(
                list(targets),
                name=str(payload.get("name", envelope.intent)),
                # The whole text: the ledger checks it for a key and only
                # then cuts it (cutting here could keep a piece of a key).
                task_text=str(payload.get("task_text", "")),
                authored_by=envelope.origin_agent,
            )
            if gate["action"] == "deny":
                verdict = "deny"
                reasons.extend(gate["reasons"])
            elif gate["action"] == "flag":
                if verdict != "deny":
                    verdict = "flag"
                reasons.extend(gate["reasons"])

            quarantined = self._quarantined_entities()
            hit = [t for t in targets if t in quarantined]
            if hit:
                verdict = "deny"
                reasons.append(f"target(s) quarantined by the device monitor: {hit[:5]}")

        # 2. LLM-authored automation dict → the schema-aware review.
        auto = payload.get("automation")
        if isinstance(auto, dict):
            r = sentinel_review(auto, payload.get("states"),
                                task_text=str(payload.get("task_text", "")))
            if r["action"] == "deny":
                verdict = "deny"
                reasons.extend(r["reasons"])
            elif r["action"] == "flag" and verdict != "deny":
                verdict = "flag"
                reasons.extend(r["reasons"])

        # 3. Egress must be budgeted.
        if payload.get("egress") and not envelope.budget.allow_egress:
            verdict = "deny"
            reasons.append("egress requested but envelope budget forbids egress")

        allowed = verdict != "deny"
        self._audit.log(
            action="sentinel_review",
            user=envelope.requester,
            result=("allow" if allowed else "deny"),
            details=f"{envelope.origin_agent}->{target}:{envelope.intent}",
            extra={"verdict": verdict, "reasons": reasons,
                   "envelope": envelope.audit_view()},
        )
        return ReviewVerdict(allowed=allowed, verdict=verdict,
                             reason="; ".join(reasons) or "ok")

    # ── request handling (non-veto) ─────────────────────────────────
    def handle(self, envelope: Envelope) -> dict:
        intent = envelope.intent
        if intent in ("device_status", "status", "security_status"):
            return {"ok": True, "status": self._sentinel_status()}
        if intent in ("alerts", "list_alerts"):
            return {"ok": True, "alerts": self._sentinel_alerts()}
        if intent == "review":
            v = self.review(envelope, target="sentinel")
            return {"ok": True, "allowed": v.allowed, "verdict": v.verdict,
                    "reason": v.reason}
        if intent == "check_addition":
            return self._check_addition(envelope)
        return {"ok": False, "error": f"sentinel: unknown intent {intent!r}"}

    # ── Drawbridge's check of one addition ──────────────────────────
    def _check_addition(self, envelope: Envelope) -> dict:
        """Check one addition with :func:`puffpai.drawbridge.check_addition`.

        The envelope itself is reviewed first, the same way the bus reviews
        every state change (a check writes to the checks record), and the
        review is audited. A denied envelope is not checked. The draft rides
        in ``payload["draft"]``; nothing about it is written to the audit
        ledger except the verdict and its reason codes.
        """
        v = self.review(envelope, target="sentinel")
        if not v.allowed:
            return {"ok": False, "denied": True, "verdict": v.verdict,
                    "reason": v.reason}
        draft = (envelope.payload or {}).get("draft")
        from puffpai import drawbridge
        result = drawbridge.check_addition(draft, by="drawbridge")
        self._audit.log(
            action="drawbridge_check_addition",
            user=envelope.requester,
            result=result["verdict"],
            details=f"{envelope.origin_agent}:{result.get('kind') or 'unknown'}",
            extra={"reasons": result.get("reasons"),
                   "draft_sha": result.get("draft_sha"),
                   "envelope": envelope.audit_view()},
        )
        return {"ok": True, **result}

    # ── device monitor wrapper (optional, guarded) ──────────────────
    def _monitor(self):
        # No bundled monitor in this build; return whatever was injected
        # (None by default) rather than importing an absent module.
        return self._device_monitor

    def _sentinel_status(self) -> dict:
        ds = self._monitor()
        if ds is None:
            return {"available": False}
        try:
            status = ds.status()
            status["available"] = True
            return status
        except Exception as exc:
            return {"available": False, "error": str(exc)}

    def _sentinel_alerts(self) -> list:
        ds = self._monitor()
        if ds is None:
            return []
        try:
            return ds.list_alerts()
        except Exception:
            return []

    def _quarantined_entities(self) -> set[str]:
        ds = self._monitor()
        if ds is None:
            return set()
        found: set[str] = set()
        try:
            status = ds.status() or {}
            for key in ("quarantined", "quarantine", "blocked"):
                vals = status.get(key)
                if isinstance(vals, (list, tuple, set)):
                    found.update(str(v) for v in vals)
        except Exception:
            pass
        return found

    def health(self) -> dict:
        base = super().health()
        base["reviewer"] = True
        base["monitoring_available"] = self._monitor() is not None
        return base


__all__ = ["SentinelAgent"]
