"""PuffPAI product-agent team (seven agents, hub-and-spoke + policy sidecar).

The seven agents are Concierge (WF-1), Steward (WF-2), Sentinel (WF-3),
Archivist (WF-4), Majordomo (WF-5), Caretaker (WF-6) and Toolsmith (WF-7).
Agents compose the underlying engines (the HA and media bridges, RAG, vault,
user memory, workflow client) rather than replacing them; those integrations
arrive in later phases and are injected or reported as unavailable until then.

The Toolsmith is the generic tool tier and the one agent that handles content
an attacker wrote, so it deliberately holds no credential of any kind. Its two
capabilities are gated at call time, not at build time: ``web_fetch`` rides the
``web_fetch`` egress door (shut by default) and its shell tool exists only
where bubblewrap does. See :mod:`puffpai.agents.toolsmith`.

Public entry point::

    from puffpai.agents import build_team
    bus = build_team()
    reply = bus.send(envelope, to="concierge")

Every state-changing envelope is structurally veto-reviewed by Sentinel inside
the bus, and every send is audited. See :mod:`puffpai.agents.bus`.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from .base import BaseAgent
from .bus import AgentBus
from .envelope import Budget, BudgetExceeded, Envelope, ReviewVerdict
from .registry import (ROSTER, REVIEWER_NAME, RosterEntry, build_team,
                       display_name, roster_summary)

__all__ = [
    "AgentBus",
    "BaseAgent",
    "Budget",
    "BudgetExceeded",
    "Envelope",
    "ReviewVerdict",
    "ROSTER",
    "REVIEWER_NAME",
    "RosterEntry",
    "build_team",
    "roster_summary",
    "display_name",
]
