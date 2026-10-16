"""'play my records' gets the build's own honest line, like 'show me my telescope'.

The music bot's own record said "work with music and radio:
not connected yet", but "play my records" got small talk about turntables (a
fresh chat) or the previous answer word for word (the same chat). Playback
requests are now answered from the build record before any model is asked.
"""
from __future__ import annotations

import pytest

from puffpai import self_knowledge

MUSIC_BUILD = {"surfaces": {"allowlist": ["media.audio", "knowledge.library"],
                            "derived_from_goal": True},
               "posture": {"run_egress": "enumerated"}}
OTHER_BUILD = {"surfaces": {"allowlist": ["knowledge.library"],
                            "derived_from_goal": True,
                            "goal": "a bot about bands and albums"},
               "posture": {"run_egress": "enumerated"}}


@pytest.mark.parametrize("message", [
    "play my records", "Play some Fleetwood Mac", "can you put on my albums?",
    "let's listen to my vinyl", "please stream the radio",
])
def test_a_play_request_gets_the_not_connected_line(message):
    got = self_knowledge.play_refusal(message, MUSIC_BUILD)
    assert got == ("I can't work with music and radio in this build yet. "
                   "It is not connected yet; it can be added later.")


def test_a_build_without_the_page_says_it_was_left_out():
    got = self_knowledge.play_refusal("play my records", OTHER_BUILD)
    assert got and got.startswith("This build does not include music and radio")


@pytest.mark.parametrize("message", [
    "what should I play at a party?", "who played bass on Rumours?",
    "tell me about the album", "I played my records yesterday",
])
def test_questions_about_music_are_left_to_the_model(message):
    assert self_knowledge.play_refusal(message, MUSIC_BUILD) is None


def test_a_media_server_that_is_on_answers_it_itself(monkeypatch):
    from puffpai.config import get_config
    monkeypatch.setattr(get_config().media_server, "enabled", True)
    assert self_knowledge.play_refusal("play my records", MUSIC_BUILD) is None


def test_the_chat_route_says_it_before_the_model(monkeypatch):
    from puffpai import api_server, build_manifest
    monkeypatch.setattr(build_manifest, "read_manifest",
                        lambda *a, **k: dict(MUSIC_BUILD))
    answer = api_server._maybe_unconnected_thing_answer("play my records")
    assert answer and answer.startswith("I can't work with music and radio")
