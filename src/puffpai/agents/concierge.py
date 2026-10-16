"""Concierge — WF-1 conversation (fronts the user, holds no secrets).

The Concierge is the only agent the user talks to. It classifies each message
with the existing keyword :mod:`~puffpai.router`, then either answers directly
(via its LLM engine, a :class:`PuffPAIAgent`) or delegates to the agent that owns
the capability — Steward for the house, Archivist for memory/knowledge,
Majordomo for scheduled work — *through the bus*.

Crucially, its scope is empty (``AgentScope.CONCIERGE``): a prompt-injected or
misbehaving Concierge cannot read the HA token or the vault, because
:meth:`~puffpai.agents.base.BaseAgent.secret` would raise ``SecretAccessDenied``.
It can only ask. That is the whole point of the split.

Requester identity (``envelope.requester``) is forwarded on every delegated
envelope so the downstream agent can enforce consent + privacy mode (e.g. a kid
profile hitting the Archivist gets Zone-2 only).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from puffpai.logging_config import get_logger
from puffpai.router import RouteTarget, get_route_category, route_message
from puffpai.security.secrets_provider import AgentScope, SecretsProvider

from .base import BaseAgent
from .envelope import Budget, Envelope

logger = get_logger(__name__)

# Route-category → agent the Concierge delegates to.
_CATEGORY_TO_AGENT = {
    "home": "steward",
    "media": "steward",
    "app": "steward",
    "library": "archivist",
    "rag": "archivist",
    "briefing": "majordomo",
    "security": "sentinel",
}


class ConciergeAgent(BaseAgent):
    """User-facing conversation + delegation front."""

    name = "concierge"
    scope = AgentScope.CONCIERGE

    def __init__(self, *, secrets: SecretsProvider | None = None,
                 engine=None) -> None:
        super().__init__(secrets=secrets)
        # ``engine`` is an optional PuffPAIAgent-like object with ``.run(query)``.
        # It is NOT constructed here (empty scope, cheap startup); inject it or
        # leave it None to run in pure-routing mode.
        self._engine = engine

    # ── request handling ────────────────────────────────────────────
    def handle(self, envelope: Envelope) -> dict:
        intent = envelope.intent
        p = envelope.payload or {}
        if intent in ("chat", "user_message", "ask"):
            return self._converse(envelope)
        return {"ok": False, "error": f"concierge: unknown intent {intent!r}"}

    def _converse(self, envelope: Envelope) -> dict:
        p = envelope.payload or {}
        text = str(p.get("text", p.get("message", "")))
        mode = str(p.get("mode", "auto"))
        route = route_message(text, mode)
        category = get_route_category(text)
        target = _CATEGORY_TO_AGENT.get(category)

        delegated = None
        if target and self.bus is not None and self.bus.get(target) is not None:
            delegated = self._delegate(envelope, text, category, target)

        answer = None
        if self._engine is not None:
            try:
                answer = self._engine.run(text, p.get("conversation_history"))
            except Exception as exc:
                from puffpai.web_search import log_preview
                logger.warning("concierge engine failed: %s",
                               log_preview(str(exc), 200))
                answer = None

        return {
            "ok": True,
            "route": route.value if isinstance(route, RouteTarget) else str(route),
            "category": category,
            "delegated_to": target if delegated is not None else None,
            "delegate_reply": delegated,
            "answer": answer,
        }

    def _delegate(self, envelope: Envelope, text: str, category: str,
                  target: str) -> dict:
        """Build and send a delegated envelope to the owning agent."""
        if target == "steward":
            intent = "list_devices"
            payload: dict = {"query": text}
        elif target == "archivist":
            intent = "retrieve"
            payload = {"query": text, "profile": (envelope.payload or {}).get("profile")}
        elif target == "majordomo":
            intent = "list_queue"
            payload = {}
        elif target == "sentinel":
            intent = "security_status"
            payload = {}
        else:
            return {"ok": False, "error": f"no delegation mapping for {category}"}

        sub = Envelope(
            intent=intent,
            payload=payload,
            requester=envelope.requester,
            origin_agent=self.name,
            budget=Budget(max_actions=2, max_seconds=15.0, allow_egress=False),
            state_changing=False,
        )
        return self.ask(target, sub)

    def health(self) -> dict:
        base = super().health()
        base["engine_attached"] = self._engine is not None
        base["holds_secrets"] = False
        return base


__all__ = ["ConciergeAgent"]
