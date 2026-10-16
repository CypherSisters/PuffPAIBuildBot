"""The self-knowledge gate on the dispatch path.

Reading self_knowledge is not the point; consulting it *before* dispatch is.
Once a message reaches the model, the model has already answered - and a small
model asked for a surface this build never loaded answers "Sure, I turned on
the lights". So every refusal test here also proves the model was never called.

The opposite failure gets equal weight: a build with no manifest, or a surface
outside the taxonomy, must refuse nothing. Absence is not a limitation.
"""

from __future__ import annotations

import pytest

from puffpai import api_server
from puffpai import build_interview as bi
from puffpai import hardware_census as hc
from puffpai import router
from puffpai.build_manifest import assemble_manifest


@pytest.fixture(scope="module")
def census():
    return hc.run_census()


def _manifest(census, goal=None, **kw):
    if goal is not None:
        kw["surfaces"] = bi.surfaces_manifest_block(
            bi.derive_surfaces(bi.propose(goal)))
    return assemble_manifest(census, **kw)


@pytest.fixture
def installed(monkeypatch):
    """Install a manifest as the one self_knowledge reads, or None for 'absent'."""
    from puffpai import build_manifest

    def _install(manifest):
        monkeypatch.setattr(build_manifest, "read_manifest",
                            lambda *a, **k: manifest)
    return _install


@pytest.fixture
def no_llm(monkeypatch):
    """Make every model call a test failure, and record that it did not fire."""
    calls: list[str] = []

    def _boom(user_message, *a, **kw):
        calls.append(user_message)
        raise AssertionError(
            "the model was called for a request the build cannot serve")

    monkeypatch.setattr(api_server, "_query_ollama_direct", _boom)
    monkeypatch.setattr(api_server, "_stream_ollama_tokens", _boom)
    return calls


# ── the map cannot silently rot ─────────────────────────────────────────────

def test_map_covers_every_category_get_route_category_can_return():
    """Every category the router emits has an explicit row - gated or not."""
    emitted = {"briefing", "home", "security", "media", "app", "action",
               "library", "rag"}
    assert emitted <= set(router.CATEGORY_SURFACES), (
        "unmapped route categories: "
        + ", ".join(sorted(emitted - set(router.CATEGORY_SURFACES))))


def test_every_mapped_surface_exists_in_the_live_taxonomy():
    taxonomy = set(bi.surface_taxonomy())
    mapped = {s for row in router.CATEGORY_SURFACES.values() for s in row}
    assert mapped <= taxonomy, (
        "surfaces named in CATEGORY_SURFACES but absent from the taxonomy: "
        + ", ".join(sorted(mapped - taxonomy)))


def test_talking_categories_are_never_gated():
    """rag and briefing are the bot describing what it knows, not a surface."""
    for category in ("rag", "briefing"):
        assert router.surfaces_for_category(category) == ()


# ── a trimmed build refuses honestly, and the model never runs ──────────────

def test_space_bot_asked_for_a_camera_refuses_without_calling_the_model(
        census, installed, no_llm):
    installed(_manifest(census, goal="a space bot for my telescopes"))

    result = api_server._process_chat_query("turn on the front door camera")

    assert "security cameras" in result["response"]
    assert "house.cameras" not in result["response"]
    assert "running setup again" in result["response"].lower()
    assert no_llm == []


def test_the_refusal_names_the_surface_that_was_asked_for(census, installed,
                                                          no_llm):
    installed(_manifest(census, goal="a space bot for my telescopes"))
    reply = api_server._maybe_self_knowledge_answer("turn on the kitchen lights")
    assert reply is not None and "lights and switches" in reply
    assert no_llm == []


def test_the_refusal_is_a_fact_plus_a_door_not_an_apology(census, installed):
    installed(_manifest(census, goal="a space bot for my telescopes"))
    reply = api_server._maybe_self_knowledge_answer("play a movie")
    assert reply is not None
    assert not reply.lower().startswith(("sorry", "i'm sorry", "unfortunately"))
    assert "setup" in reply.lower()


# ── a build that HAS the surface dispatches normally ────────────────────────

def test_a_build_with_the_surface_is_not_gated(census, installed):
    installed(_manifest(census, goal="a space bot for my telescopes"))
    # knowledge.library IS on the space bot's allowlist.
    assert api_server._maybe_capability_refusal(
        "what is in my offline library") is None


def test_positive_control_an_ungated_message_does_reach_the_model(
        census, installed, monkeypatch):
    """Without this, "the model was not called" could just mean the harness is
    broken. A telescope question on a space bot must get all the way through."""
    seen: list[str] = []
    monkeypatch.setattr(
        api_server, "_query_ollama_direct",
        lambda msg, *a, **kw: seen.append(msg) or {
            'response': 'ok', 'sources': [], 'suggestions': []})
    installed(_manifest(census, goal="a space bot for my telescopes"))

    api_server._process_chat_query("what is visible in the sky tonight")

    assert seen == ["what is visible in the sky tonight"]


def test_one_available_surface_keeps_the_whole_category_open(census, installed):
    """Audio but no video still answers "media" - the refusal happens at the
    specific request, not across the family."""
    installed(assemble_manifest(census, surfaces={
        "allowlist": ["media.audio"], "derived_from_goal": True}))
    assert api_server._maybe_capability_refusal("play some music") is None
    # ...but a video request, which names its own surface, is still refused.
    video = api_server._maybe_capability_refusal("play a movie")
    assert video is not None and "video library" in video


# ── absence is not a limitation ─────────────────────────────────────────────

def test_a_build_with_no_manifest_refuses_nothing(installed):
    installed(None)
    for message in ("turn on the front door camera", "turn on the lights",
                    "play a movie", "set a timer for ten minutes",
                    "what is in my library"):
        assert api_server._maybe_capability_refusal(message) is None, message


def test_an_untrimmed_build_refuses_nothing(census, installed):
    installed(assemble_manifest(census))
    for message in ("turn on the front door camera", "play a movie",
                    "set a timer for ten minutes"):
        assert api_server._maybe_capability_refusal(message) is None, message


def test_the_gate_fails_open_when_self_knowledge_raises(monkeypatch):
    from puffpai import self_knowledge

    def _explode(*a, **kw):
        raise RuntimeError("manifest on fire")

    monkeypatch.setattr(self_knowledge, "_can", _explode)
    assert api_server._maybe_capability_refusal(
        "turn on the front door camera") is None


# ── posture: a sealed build says so ─────────────────────────────────────────

def test_a_sealed_build_refuses_a_wan_request_honestly(census, installed):
    installed(assemble_manifest(census, posture={"run_egress": "sealed"}))
    from puffpai import self_knowledge
    ok, reason = self_knowledge.can("reach_wan")
    assert not ok
    assert "outside this computer" in reason.lower()
    assert not reason.lower().startswith("sorry")


# ── "what can you do?" comes from the manifest ──────────────────────────────

def test_what_can_you_do_answers_from_the_real_manifest(census, installed,
                                                        no_llm):
    installed(_manifest(census, goal="a space bot for my telescopes",
                        posture={"run_egress": "sealed"}))
    reply = api_server._maybe_self_knowledge_answer("what can you do?")
    assert reply is not None
    assert "telescope" in reply                   # the goal's thing, honestly
    assert "not connected yet" in reply
    assert "instruments." not in reply            # never an internal id
    assert "leaves your computer" in reply        # and the limit, same breath
    assert no_llm == []


def test_what_can_you_do_is_not_answered_by_an_unconfigured_build(installed):
    """No manifest means nobody answered the question. Do not invent limits."""
    installed(None)
    assert api_server._maybe_capability_summary("what can you do?") is None
