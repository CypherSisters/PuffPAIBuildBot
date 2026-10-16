"""Roster definition + ``build_team`` factory for the seven-agent product team.

The roster lists, for each agent, its class,
secret scope, model class, workflow family, and which embryo agent it evolves
from. :func:`build_team` constructs the bus, instantiates all seven, registers
them, and designates Sentinel as the policy reviewer (the veto).

The count is **seven**: the original six-agent team
plus the Toolsmith, WF-7, the generic tool tier. Nothing in this repo should hard-code
that number — ``len(ROSTER)`` is the source, and the crew card, the "which
agents do I have?" answer and ``crew_sizing`` all derive it from here.

Agents are constructed cheaply — heavy engines (the HA and media bridges, RAG,
the workflow client) are built lazily on first use inside each agent — so
building the team is safe to do at startup behind a flag without paying for the
whole stack.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from dataclasses import dataclass

from puffpai.logging_config import get_logger
from puffpai.security.audit_log import AuditLog
from puffpai.security.secrets_provider import AgentScope, SecretsProvider

from .archivist import ArchivistAgent
from .base import BaseAgent
from .bus import AgentBus
from .caretaker import CaretakerAgent
from .concierge import ConciergeAgent
from .majordomo import MajordomoAgent
from .sentinel_agent import SentinelAgent
from .steward import StewardAgent
from .toolsmith import ToolsmithAgent

logger = get_logger(__name__)


@dataclass(frozen=True)
class RosterEntry:
    """One row of the roster."""

    name: str
    cls: type[BaseAgent]
    scope: AgentScope
    model_class: str
    workflow: str
    evolves_from: str
    #: What a person is shown. The internal id (``name``) is the key every
    #: route, envelope, log line and config file uses and it never changes;
    #: this is the only string a human should ever read. They differ for
    #: exactly one agent: the reviewer's id is ``sentinel`` and its name is
    #: Drawbridge. Renaming the id to match would
    #: break every stored envelope and every saved config; showing the id
    #: made the product contradict its own README and its own screenshot.
    display_name: str = ""
    #: The short job title a person is shown next to the display name.
    role: str = ""
    #: One plain sentence about what this agent does, written to be read aloud.
    #: The spoken and written answers to "what agents do I have?" are built from
    #: these (see :func:`roster_blurbs`) so the roster cannot drift out of the
    #: product's own description of itself again.
    blurb: str = ""


# The authoritative roster, in order.
ROSTER: tuple[RosterEntry, ...] = (
    RosterEntry("concierge", ConciergeAgent, AgentScope.CONCIERGE,
                "7-8B resident + 70B escalation", "WF-1", "Cortex",
                "Concierge", "Front desk, routing",
                "Concierge is the one you talk to. It answers directly when it "
                "can and hands the rest to whichever agent owns it."),
    RosterEntry("steward", StewardAgent, AgentScope.STEWARD,
                "7-8B tool-calling", "WF-2", "Bridge", "Steward",
                "Home and tools",
                "Steward runs the house. It is the only agent holding the Home "
                "Assistant and media credentials, so lights, climate, locks and "
                "playback go through it."),
    RosterEntry("sentinel", SentinelAgent, AgentScope.SENTINEL,
                "small classifier + 70B incident analysis", "WF-3",
                "(the original veto agent)", "Drawbridge",
                "Privacy and security",
                "Drawbridge is the guard. It reviews anything that changes the "
                "system before it happens, and runs privacy scans, audits and "
                "network inventory."),
    RosterEntry("archivist", ArchivistAgent, AgentScope.ARCHIVIST,
                "embedder + 70B synthesis", "WF-4", "(new — split from Cortex)",
                "Archivist", "Archive and knowledge",
                "Archivist keeps your memory and documents. It is the only way "
                "into the Vault, and it answers from what you have stored."),
    RosterEntry("majordomo", MajordomoAgent, AgentScope.MAJORDOMO,
                "70B planning + cheap task-workers", "WF-5", "(new — n8n layer)",
                "Majordomo", "Orchestration, scheduling",
                "Majordomo does the work you are not watching: briefings, "
                "reminders, calendar sync and saved workflows."),
    RosterEntry("caretaker", CaretakerAgent, AgentScope.CARETAKER,
                "mostly no LLM; small model for summaries", "WF-6",
                "(new — watchdog)", "Caretaker", "Health and wellbeing",
                "Caretaker watches the machine itself (services, disk, "
                "temperature) and keeps working even when the model stack is "
                "down."),
    RosterEntry("toolsmith", ToolsmithAgent, AgentScope.TOOLSMITH,
                "no model of its own; called by the planner", "WF-7",
                "(new — the generic tool tier)", "Toolsmith",
                "Generic tools and the workspace",
                "Toolsmith is the one that reaches for a tool: it reads a web "
                "page when you allow it, keeps working notes in its own "
                "sandbox, and holds no keys to anything else."),
)

REVIEWER_NAME = "sentinel"


def display_name(agent_id: str) -> str:
    """The name to show a person for an internal agent id.

    Falls back to a capitalised id for anything not on the roster, so a new
    agent shows up readably instead of blank.
    """
    for entry in ROSTER:
        if entry.name == agent_id:
            return entry.display_name or entry.name.capitalize()
    return (agent_id or "").capitalize()


def roster_blurbs() -> tuple[tuple[str, str, str], ...]:
    """``(display name, role, one-sentence blurb)`` for every agent, in order.

    The single source the product's own "which agents do I have?" answers are
    written from, so a retired name can never come back through hand-written
    copy.
    """
    return tuple(
        (entry.display_name or entry.name.capitalize(), entry.role, entry.blurb)
        for entry in ROSTER
    )


def build_team(config=None, *, secrets: SecretsProvider | None = None,
               audit: AuditLog | None = None, engines: dict | None = None) -> AgentBus:
    """Construct the bus + all seven agents and return the wired bus.

    Args:
        config: optional PuffPAIConfig (passed to the Caretaker for probe targets).
        secrets: optional shared :class:`SecretsProvider` (else each agent uses the
            module default). Scope enforcement is identical either way.
        audit: optional shared :class:`AuditLog` for the bus + security agents.
        engines: optional dict of pre-built engines for injection/testing, keyed
            by ``{"ha_bridge", "media_bridge", "rag", "vault", "n8n_client",
            "device_monitor", "concierge_engine"}``. Anything omitted is built
            lazily by the owning agent on first use.

    Returns:
        A :class:`~puffpai.agents.bus.AgentBus` with all seven agents registered
        and Sentinel designated as reviewer.
    """
    engines = engines or {}
    audit = audit or AuditLog()
    bus = AgentBus(audit=audit, reviewer_name=REVIEWER_NAME)

    agents: list[BaseAgent] = [
        ConciergeAgent(secrets=secrets, engine=engines.get("concierge_engine")),
        StewardAgent(secrets=secrets, ha_bridge=engines.get("ha_bridge"),
                     media_bridge=engines.get("media_bridge")),
        SentinelAgent(secrets=secrets, audit=audit,
                      device_monitor=engines.get("device_monitor")),
        ArchivistAgent(secrets=secrets, rag=engines.get("rag"),
                       vault=engines.get("vault"), audit=audit),
        MajordomoAgent(secrets=secrets, n8n_client=engines.get("n8n_client")),
        CaretakerAgent(secrets=secrets, config=config),
        # No engine to inject and nothing to configure: the Toolsmith's two
        # prerequisites — the egress door and the bubblewrap binary — are both
        # resolved at call time, so it cannot be built into a state that
        # disagrees with the machine it is running on.
        ToolsmithAgent(secrets=secrets),
    ]
    for agent in agents:
        bus.register(agent)
    bus.set_reviewer(REVIEWER_NAME)

    logger.info("product-agent team built: %s (reviewer=%s)",
                ", ".join(a.name for a in agents), REVIEWER_NAME)
    return bus


def roster_summary() -> list[dict]:
    """Serializable roster (for /agents/status and docs)."""
    return [
        {
            "name": e.name,
            "display_name": e.display_name or e.name.capitalize(),
            "scope": e.scope.value,
            "model_class": e.model_class,
            "workflow": e.workflow,
            "evolves_from": e.evolves_from,
        }
        for e in ROSTER
    ]


__all__ = ["ROSTER", "RosterEntry", "REVIEWER_NAME", "build_team",
           "roster_summary", "display_name"]
