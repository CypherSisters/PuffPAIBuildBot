"""BaseAgent — the ABC every product-team agent extends.

An agent is defined by three things:

* a ``name`` (its bus address and systemd-unit name in the target deployment),
* an :class:`~puffpai.security.secrets_provider.AgentScope` (which secrets it may
  even *ask* for — enforced by the secrets provider, not by convention), and
* a :meth:`handle` method that turns an :class:`~puffpai.agents.envelope.Envelope`
  into a reply ``dict``.

Agents never import one another's instances. They talk only through the bus
(``self.ask(to, envelope)``), so the seam is identical whether the master agents
share one process today or become separate systemd units tomorrow.

The :meth:`secret` helper fetches credentials with the agent's *own* scope only —
there is no way for an agent to widen its own scope, so a prompt-injected
Concierge (empty scope) is *structurally* unable to read the HA token or vault
keys; it can only ask the agent that holds them.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from puffpai.logging_config import get_logger
from puffpai.security.secrets_provider import (
    AgentScope,
    SecretsProvider,
    get_default_provider,
)

logger = get_logger(__name__)


class BaseAgent(ABC):
    """Abstract base for every agent on the bus.

    Subclasses set the class-level ``name`` and ``scope`` (or pass them to
    ``__init__``) and implement :meth:`handle`.
    """

    #: Bus address / systemd unit name. Overridden per subclass.
    name: str = "agent"
    #: Secret scope. Overridden per subclass; SYSTEM is unrestricted.
    scope: AgentScope = AgentScope.SYSTEM

    def __init__(
        self,
        *,
        name: str | None = None,
        scope: AgentScope | None = None,
        secrets: SecretsProvider | None = None,
    ) -> None:
        if name is not None:
            self.name = name
        if scope is not None:
            self.scope = scope
        self._secrets = secrets
        self.bus: Any = None  # AgentBus, set by AgentBus.register()

    # ── bus wiring ──────────────────────────────────────────────────
    def bind_bus(self, bus) -> None:
        """Called by :class:`~puffpai.agents.bus.AgentBus` on registration."""
        self.bus = bus

    def ask(self, to: str, envelope) -> dict:
        """Send *envelope* to agent *to* through the bus and return its reply.

        Routing through the bus (never a direct call) is what keeps Sentinel's
        veto and the audit trail in the path.
        """
        if self.bus is None:
            raise RuntimeError(
                f"agent {self.name!r} is not registered on a bus; cannot ask {to!r}"
            )
        return self.bus.send(envelope, to=to)

    # ── scoped secret access ────────────────────────────────────────
    def secret(
        self,
        name: str,
        *,
        required: bool = False,
        default: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve a secret with this agent's own scope.

        Raises ``SecretAccessDenied`` if the agent's scope is not allowed the
        named secret. Never logs the value.
        """
        provider = self._secrets or get_default_provider()
        return provider.get(name, scope=self.scope, default=default, required=required)

    # ── contract ────────────────────────────────────────────────────
    @abstractmethod
    def handle(self, envelope) -> dict:
        """Process *envelope* and return a reply dict.

        Convention: return ``{"ok": True, ...}`` on success and
        ``{"ok": False, "error": ...}`` on a handled failure. Raising
        ``BudgetExceeded`` is caught by the bus and turned into an audited abort.
        """
        raise NotImplementedError

    def health(self) -> dict:
        """Cheap liveness/roster info. Overridable; must not require the LLM."""
        return {"agent": self.name, "scope": self.scope.value, "ok": True}


__all__ = ["BaseAgent"]
