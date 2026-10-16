"""Which skills a changed field invalidates.

**Staleness follows changed fields, not re-run skills.** Re-running Purpose and
changing only the avatar must not invalidate the prompt, so the table below is
keyed on ``<section>.<field>`` -- the field a skill actually wrote -- and a
skill goes stale only when something it depends on moved.

This is a data table on purpose. Converting another skill adds its rows
here and adds nothing else: no code decides staleness, so no code can quietly
decide differently for one skill.

**Prove is downstream of everything** (B.4, last row). Any change in this table
invalidates the proof, and Handoff after it, because a build whose proof is
stale has to say so rather than show the last verdict as if it still held.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from typing import Iterable

#: ``<section>.<field>`` -> the skills that go stale when it changes. Straight
#: from the spec's table; a field not listed here makes nothing stale, which is
#: the point of keying on fields.
FIELD_STALENESS: dict[str, tuple[str, ...]] = {
    "machine.memory": ("brain",),
    "machine.memory_gb": ("brain",),
    "machine.gpu": ("brain",),
    "machine.free_space": ("integrate", "brain"),
    "machine.ollama_url": ("integrate", "brain"),
    # ``purpose`` is in these two rows because Purpose asks two questions: the
    # id a place is scaffolded under is derived from the name and the goal, so
    # changing either makes ``purpose:place`` -- the sibling card, not the one
    # just answered -- stale.
    "persona.goal": ("purpose", "sources", "context", "surfaces"),
    "persona.name": ("purpose", "sources", "context", "surfaces"),
    "persona.id": ("surfaces", "handoff"),
    "persona.lists": ("context",),
    "persona.personality": ("context",),
    "surfaces.allowlist": ("context",),
    "brain.model": ("context", "crew"),
    "brain.routing_tier": ("context", "crew"),
    "sources.selected": ("tools", "integrate"),
    "tools.available": ("crew", "context", "secure"),
    "knowledge.status": ("context",),
    "crew.ownership": ("secure",),
    "crew.ceiling": ("secure",),
    "posture.run_egress": ("integrate", "tools", "surfaces"),
    "posture.doors": ("integrate", "tools", "surfaces"),
    # Added as the twelve were converted: a card's field id is the key, so
    # these are the fields the cards actually ask for beyond the spec's table.
    "posture.build_network": ("brain", "sources", "integrate", "tools"),
    # Zone 3. The bridge posture and the three facts the recommendation is made
    # from land in the same place, so they invalidate the same things. More
    # staleness than strictly necessary is the safe direction: a banner costs a
    # recheck, a stale proof costs the owner a wrong answer.
    "posture.bridge_posture": ("integrate", "tools", "surfaces"),
    "posture.home_assistant": ("integrate", "tools", "surfaces"),
    "posture.external_firewall": ("integrate", "tools", "surfaces"),
    "posture.managed_lan": ("integrate", "tools", "surfaces"),
    "persona.scaffold": ("surfaces", "context"),
    "crew.specialists": ("context", "secure"),
    "tools.connect_hub": ("crew", "context", "secure"),
    "tools.hub_url": ("crew", "context", "secure"),
    "knowledge.zim_dir": ("integrate", "prove"),
    "knowledge.auto_index": ("integrate",),
    "knowledge.fetch_now": ("context",),
    "knowledge.own_data": ("context",),
}

#: Answerable fields that deliberately make nothing stale, each with the reason
#: it is here rather than in the table. The list is explicit so that "this
#: field has no row" is a decision somebody made and a test can check, rather
#: than a row nobody got round to writing.
#:
#: * ``posture.pin`` / ``posture.pin_again`` -- the vault PIN. Sealing a vault
#:   changes no input any other skill reads; it is also a secret, and a secret
#:   never reaches this table at all.
#: * ``tools.hub_token`` -- the same, for a Home Assistant token.
#: * ``brain.download_now`` -- an action, not a recorded decision. What
#:   downstream skills read is ``brain.model``, which already has a row.
MAKES_NOTHING_STALE: frozenset[str] = frozenset({
    "posture.pin", "posture.pin_again", "tools.hub_token",
    "brain.download_now",
    # The age band only changes how the wizard words its questions; nothing
    # the build decided depends on it (there is no kid mode).
    "persona.age",
})

#: Downstream of every row above, in this order.
ALWAYS_STALE: tuple[str, ...] = ("prove", "handoff")


def stale_for(keys: Iterable[str]) -> list[str]:
    """The skills that need rechecking after those ``section.field`` changes.

    A key nobody depends on makes nothing stale -- and makes Prove nothing
    either, because "any of the above" in the spec's table means the rows
    above, not any change at all.
    """
    out: list[str] = []
    matched = False
    for key in keys or ():
        for skill in FIELD_STALENESS.get(key, ()):
            matched = True
            if skill not in out:
                out.append(skill)
    if matched:
        # Always last, always in this order, even when a row named one of them
        # directly: Prove is downstream of everything and Handoff of Prove, and
        # a list that says so in a different order every time is a list nobody
        # reads twice.
        out = [skill for skill in out if skill not in ALWAYS_STALE]
        out.extend(ALWAYS_STALE)
    return out


def stale_for_answers(card: dict, answers: dict) -> list[str]:
    """The same thing, for the fields an owner just changed on one card.

    A card's field ids are the field names of the section its skill writes, so
    the key is ``<section>.<field id>``. A skill whose card field does not map
    onto a written field simply has no row in the table, and changes nothing.

    **A skill does not make itself stale -- unless it asks more than one
    question.** Answering a card and being told to recheck that same card is
    noise. But Purpose's place card is proposed *from* the identity card's
    answers, so a skill with parts may legitimately appear: what went stale is
    the sibling question, not the one just answered.
    """
    section = (card or {}).get("section") or ""
    keys = [f"{section}.{field_id}" for field_id in (answers or {})]
    stale = stale_for(keys)
    own = (card or {}).get("skill")
    if (card or {}).get("part"):
        return list(stale)
    return [skill for skill in stale if skill != own]


__all__ = ["ALWAYS_STALE", "FIELD_STALENESS", "MAKES_NOTHING_STALE",
           "stale_for", "stale_for_answers"]
