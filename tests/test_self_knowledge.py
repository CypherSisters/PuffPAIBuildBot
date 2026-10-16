"""Tests for puffpai.self_knowledge - the build's honest account of itself.

The thing under test is not a feature so much as a refusal to lie. Two lies are
possible and both are covered here:

* Claiming a capability the build does not have (hallucinated compliance).
* Claiming a limitation the build does not actually have, because a question
  was never answered. An unconfigured manifest carries empty defaults, and
  reading those as limits makes a healthy build describe itself as crippled.

The second is the subtler one, and it is where this module's own bugs were.
"""

from __future__ import annotations

import pytest

from puffpai import build_interview as bi
from puffpai import hardware_census as hc
from puffpai import self_knowledge as sk
from puffpai.build_manifest import assemble_manifest


@pytest.fixture(scope="module")
def census():
    return hc.run_census()


@pytest.fixture(scope="module")
def space_surfaces():
    return bi.surfaces_manifest_block(
        bi.derive_surfaces(bi.propose("a space bot for my telescopes")))


# ── absence is not a limitation ──────────────────────────────────────────────

def test_untrimmed_build_does_not_refuse_everything(census):
    """An empty allowlist means 'not trimmed', never 'nothing allowed'."""
    stock = assemble_manifest(census)
    for surface in ("house.cameras", "media.video", "knowledge.library"):
        assert sk.refusal(surface, stock) is None, surface
    # Pages an installed app stands behind are a real yes.
    for surface in ("comms.timers", "daily.kitchen"):
        ok, reason = sk.can(surface, stock)
        assert ok, f"stock build falsely refused {surface}: {reason}"


def test_unconfigured_crew_is_not_reported_as_a_hardware_limit(census):
    """max_specialists defaults to 0 before crew sizing runs. That is a
    question nobody answered, not a limit the owner has."""
    stock = assemble_manifest(census)
    assert stock["crew"]["master_core"] is None
    joined = " ".join(sk.limits_summary(stock)).lower()
    assert "no room" not in joined
    assert "core team only" not in joined


def test_crew_room_is_not_claimed_as_an_ability(census):
    """Room for specialists changes nothing at runtime (no specialist is ever
    built), so the self-description does not offer it as something it has."""
    sized = assemble_manifest(census, crew={"master_core": 6,
                                            "max_specialists": 5,
                                            "selected_specialists": []})
    assert not any("specialist" in ln for ln in sk.limits_summary(sized))


def test_no_manifest_says_it_is_not_set_up_yet():
    assert sk.limits_summary({}) == [sk.why(sk.CAUSE_NOT_CONFIGURED)]


# ── real limitations are reported, with a cause and a door ───────────────────

def test_trimmed_build_refuses_a_surface_it_never_loaded(census, space_surfaces):
    space = assemble_manifest(census, surfaces=space_surfaces)
    ok, reason = sk.can("house.cameras", space)
    assert not ok
    assert "security cameras" in reason   # the owner's words, never the id
    assert "house.cameras" not in reason
    assert "setup again" in reason        # the way out
    assert sk.refusal("house.cameras", space) == reason


def test_trimmed_build_keeps_the_surfaces_its_goal_earned(census, space_surfaces,
                                                         monkeypatch):
    """On the list means not refused. It does not mean it works: the telescope
    page has nothing behind it, so it is not claimed; the library page works
    exactly when the library does."""
    from puffpai import build_abilities as ba
    space = assemble_manifest(census, surfaces=space_surfaces)
    for surface in ("instruments.telescope", "knowledge.library"):
        assert sk.refusal(surface, space) is None
    ok, reason = sk.can("instruments.telescope", space)
    assert not ok and "telescope" in reason and "instruments." not in reason
    library = ba.row("knowledge_library", "Look things up", ba.AVAILABLE)
    monkeypatch.setattr(ba, "abilities", lambda *a, **k: [library])
    assert sk.can("knowledge.library", space)[0]
    library["status"] = ba.NOT_SET_UP
    assert not sk.can("knowledge.library", space)[0]


def test_sealed_build_cannot_reach_anything(census):
    sealed = assemble_manifest(
        census, posture={"build_network": "online", "run_egress": "sealed"})
    for behaviour in ("reach_wan", "reach_lan"):
        ok, reason = sk.can(behaviour, sealed)
        assert not ok and "outside this computer" in reason.lower()


def test_island_build_reaches_the_lan_but_not_the_internet(census):
    island = assemble_manifest(
        census, posture={"build_network": "online", "run_egress": "island"})
    assert sk.can("reach_lan", island)[0]
    ok, reason = sk.can("reach_wan", island)
    assert not ok and "internet" in reason.lower()


@pytest.mark.parametrize("model,can_plan", [
    ("qwen3:30b-a3b", True),
    ("llama3.2:1b", False),
    ("qwen2.5:1.5b-instruct", False),
])
def test_planning_follows_the_model_profile(census, model, can_plan):
    man = assemble_manifest(census, brain={"model": model})
    assert sk.can("plan", man)[0] is can_plan


def test_the_vault_always_needs_the_pin(census):
    ok, reason = sk.can("read_vault", assemble_manifest(census))
    assert not ok and "pin" in reason.lower()


# ── voice: a fact plus a door, never an apology ──────────────────────────────

@pytest.mark.parametrize("cause", [
    sk.CAUSE_SURFACE_NOT_LOADED, sk.CAUSE_EGRESS_SEALED,
    sk.CAUSE_EGRESS_LAN_ONLY, sk.CAUSE_NO_PLANNING, sk.CAUSE_VAULT_SEALED,
    sk.CAUSE_NO_CREW, sk.CAUSE_CONFIG_DEGRADED, sk.CAUSE_NOT_CONFIGURED,
])
def test_no_reason_apologises(cause):
    text = sk.why(cause).lower()
    for grovel in ("sorry", "apolog", "unfortunately", "i'm afraid",
                   "unable to help", "i can't help"):
        assert grovel not in text, f"{cause} apologises: {text}"


@pytest.mark.parametrize("cause", [
    sk.CAUSE_SURFACE_NOT_LOADED, sk.CAUSE_EGRESS_SEALED,
    sk.CAUSE_EGRESS_LAN_ONLY, sk.CAUSE_NO_PLANNING, sk.CAUSE_VAULT_SEALED,
    sk.CAUSE_NO_CREW, sk.CAUSE_CONFIG_DEGRADED, sk.CAUSE_NOT_CONFIGURED,
])
def test_every_reason_is_ascii_and_sayable(cause):
    text = sk.why(cause)
    assert text.isascii(), f"{cause} would garble the Windows console"
    assert len(text.split()) >= 5


def test_summary_states_capability_alongside_limitation(census, space_surfaces):
    """A list of only limits reads as an apology; only features reads as a
    brochure. The hardest build must still say both."""
    worst = assemble_manifest(
        census, surfaces=space_surfaces, brain={"model": "llama3.2:1b"},
        posture={"build_network": "offline", "run_egress": "sealed"})
    lines = sk.limits_summary(worst)
    joined = " ".join(lines)
    assert joined.isascii()
    assert any("language model" in ln for ln in lines)
    assert any("leaves your computer" in ln for ln in lines)
    assert any("can't do yet" in ln for ln in lines)
    # Plain words: no internal ids, no jargon.
    from puffpai import plain_words
    assert plain_words.is_plain(joined, also=("instruments.", "web_fetch")), \
        plain_words.check(joined, also=("instruments.", "web_fetch"))


# ── the dispatch contract ────────────────────────────────────────────────────

def test_explain_refusal_never_returns_empty(census, space_surfaces):
    space = assemble_manifest(census, surfaces=space_surfaces)
    for intent in ("house.cameras", "instruments.telescope", "plan", "nonsense"):
        assert sk.explain_refusal(intent, space).strip()


def test_explain_refusal_owns_the_fault_when_the_build_is_capable(census):
    """If we CAN do it and it still failed, that is our bug - say so, rather
    than inventing a limitation to hide behind."""
    text = sk.explain_refusal("comms.timers",
                              assemble_manifest(census)).lower()
    assert "fault on my side" in text
    assert "you did wrong" in text


def test_can_never_raises_on_garbage():
    for junk in (None, "", "   ", "not.a.surface", "../../etc/passwd", "\x00"):
        ok, reason = sk.can(junk)          # type: ignore[arg-type]
        assert isinstance(ok, bool) and isinstance(reason, str)


def test_capabilities_partitions_the_taxonomy(census, space_surfaces):
    space = assemble_manifest(census, surfaces=space_surfaces)
    caps = sk.capabilities(space)
    assert caps["trimmed"] is True
    # On the list, but nothing stands behind it: not claimed.
    assert "instruments.telescope" in caps["unavailable"]
    assert "house.cameras" in caps["unavailable"]
    assert not set(caps["available"]) & set(caps["unavailable"])
