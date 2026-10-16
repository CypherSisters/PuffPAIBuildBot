"""A capability refusal needs evidence. A route category is not evidence.

A GardenBot build, asked to "summarise my garden so far", answered with a
straight face:

    I do not have comms.timers. This build was set up for GardenBot, so that
    surface was never loaded.

Nobody mentioned timers. ``router.get_route_category`` sorts free text into broad
families, that sentence fell into ``app`` on a goal word, and the refusal
reported the FIRST surface of that family's tuple as though it had been
requested. Two doctrines are in play and the bucket broke both: **evidence or
silence** (say "I do not have X" only when X was asked for) and **absence is not
a limitation** (a surface nobody named is not a wall).

So: the refusal must fire on the sentence's own words, and a real ask for a
missing surface must still be refused. Both halves are here, because fixing the
first one by refusing nothing would be the same lie pointing the other way.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import pytest

from puffpai import api_server
from puffpai import build_interview as bi
from puffpai import hardware_census as hc
from puffpai import router
from puffpai.build_manifest import assemble_manifest

#: The build, as the wizard derived it: a garden bot with the house
#: surfaces, the library and its own notes - and no comms.timers.
GOAL = "a greenhouse bot for my garden"


@pytest.fixture(scope="module")
def census():
    return hc.run_census()


#: The words the scaffolded GardenBot app contributes to the router's `app`
#: bucket. On a real install ``rulepacks._apply_app_manifests`` puts an app's own
#: vocabulary into that pack, which is why a sentence with "garden" in it
#: routes to ``app`` on a real install and to ``rag`` in a bare checkout. The
#: bug only exists with them, so the test installs them.
GARDEN_WORDS = ["gardenbot", "plantings", "harvest log", "greenhouse", "garden"]


@pytest.fixture
def garden_bot(census, monkeypatch):
    """Install the GardenBot manifest as the one self_knowledge reads."""
    from puffpai import build_manifest

    monkeypatch.setattr(router, "_APP_KEYWORDS",
                        list(router._APP_KEYWORDS) + GARDEN_WORDS)
    manifest = assemble_manifest(
        census,
        surfaces=bi.surfaces_manifest_block(bi.derive_surfaces(bi.propose(GOAL))),
        identity={"name": "GardenBot"},
    )
    monkeypatch.setattr(build_manifest, "read_manifest",
                        lambda *a, **k: manifest)
    return manifest


@pytest.fixture
def fake_backend(monkeypatch):
    """A model that answers, so "it reached the model" is observable."""
    seen: list[str] = []

    def _answer(user_message, *a, **kw):
        seen.append(user_message)
        return {"response": "here is what your garden looks like",
                "sources": [], "suggestions": []}

    monkeypatch.setattr(api_server, "_query_ollama_direct", _answer)
    return seen


def test_the_garden_bot_had_no_timers_on_its_allowlist(garden_bot):
    """The precondition. Without it the test below proves nothing."""
    allow = set((garden_bot.get("surfaces") or {}).get("allowlist") or [])
    assert "comms.timers" not in allow
    assert "house.climate" in allow


# ── the misroute ────────────────────────────────────────────────────────────

def test_summarise_my_garden_reaches_the_model(garden_bot, fake_backend):
    result = api_server._process_chat_query("summarise my garden so far")

    assert fake_backend == ["summarise my garden so far"], (
        "the sentence was answered by a canned refusal instead of the model")
    assert "comms.timers" not in result["response"]


@pytest.mark.parametrize("message", [
    "summarise my garden so far",
    "how is my garden doing?",
    "tell me about my greenhouse",
    "what should I plant next month?",
    "is my harvest any good this year",
])
def test_ordinary_sentences_about_the_build_are_never_refused(garden_bot,
                                                              message):
    assert api_server._maybe_capability_refusal(message) is None, message


def test_a_bucket_is_not_evidence(garden_bot):
    """The sentence routes to `app`, and still names no surface of it."""
    assert router.get_route_category("summarise my garden so far") == "app"
    assert router.surfaces_asked_for("summarise my garden so far") == ()


# ── the other half: a real ask for a missing surface is still refused ───────

def test_a_real_timers_ask_is_still_refused_on_a_build_without_timers(
        garden_bot, fake_backend):
    reply = api_server._maybe_capability_refusal("set a timer for ten minutes")

    assert reply is not None and "timers and alarms" in reply
    assert "comms.timers" not in reply       # plain words, never the id
    assert "setup" in reply.lower()          # a fact plus a door
    assert fake_backend == []                # and the model never ran


@pytest.mark.parametrize("message,surface,words", [
    ("set a timer for ten minutes", "comms.timers", "timers and alarms"),
    ("put the school play on my calendar", "comms.calendar",
     "calendar and events"),
    ("what is in the pantry", "daily.kitchen", "pantry and meals"),
    ("log today's workout", "daily.fitness", "fitness tracking"),
])
def test_each_named_surface_is_refused_by_its_own_name(garden_bot, message,
                                                       surface, words):
    reply = api_server._maybe_capability_refusal(message)
    assert reply is not None and words in reply, (message, reply)
    assert surface not in reply, (message, reply)


def test_the_refusal_still_names_only_what_was_asked_for(garden_bot):
    """One surface named, one surface refused - not the whole family."""
    reply = api_server._maybe_capability_refusal("set a timer for ten minutes")
    assert reply is not None
    for other in ("comms.calendar", "daily.kitchen", "daily.fitness",
                  "daily.games"):
        assert other not in reply


# ── absence is not a limitation ─────────────────────────────────────────────

def test_a_named_surface_this_build_does_have_is_not_refused(garden_bot):
    """house.climate IS on the allowlist, so a climate question dispatches."""
    assert api_server._maybe_capability_refusal(
        "what is the greenhouse temperature") is None
