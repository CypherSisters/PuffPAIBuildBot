"""The bot's personality (persona.json): its shape, the fallback, a refresh
after a new install, the chat loop's system note, and the wake-up shown once."""
from __future__ import annotations

import json
import os

import pytest

from puffpai import brain_loop, persona

KEY = "0123456789abcdef" * 4
AUTH = {"Authorization": f"Bearer {KEY}"}


@pytest.fixture(autouse=True)
def _wedding_bot(monkeypatch):
    from puffpai import bot_identity
    monkeypatch.setattr(bot_identity, "bot_name", lambda *a, **k: "Juniper")
    monkeypatch.setattr(bot_identity, "bot_goal",
                        lambda: "Help me plan my wedding next June")
    monkeypatch.setattr(persona, "brain_ask", lambda *a, **k: None)


def _add_skill(name: str, description: str = "Helps with things.") -> None:
    from puffpai import skills
    folder = os.path.join(skills.skills_dir(create=True), name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n")


def _model_says(payload: dict):
    return lambda messages: json.dumps(payload)


SHAPE = {"name", "kind", "traits", "tone", "greeting", "intro", "icon", "accent"}


def test_fallback_without_a_model_has_the_whole_shape():
    _add_skill("wedding-planner")
    p = persona.make(ask=None)
    assert SHAPE <= set(p)
    assert p["name"] == "Juniper"
    assert "wedding" in p["kind"]
    assert len(p["traits"]) == 3
    assert p["icon"] in persona.ICONS
    assert p["accent"] in persona.ACCENTS.values()
    assert "wedding planner" in p["intro"]
    assert p["intro"].endswith("Let me show you around.")
    assert p["made_with"] == "fallback"
    on_disk = json.load(open(persona.path(), encoding="utf-8"))
    assert SHAPE <= set(on_disk)


def test_model_answer_is_used_when_it_fits():
    p = persona.make(ask=_model_says({
        "kind": "Wedding Planner", "traits": ["warm", "organized", "cheerful"],
        "tone": "Bright and calm — like a friend with a clipboard.",
        "greeting": "Hello, I'm Juniper and I love a good plan!",
        "intro": "I'm Juniper. I can plan your big day.",
        "icon": "favorite", "accent": "rose"}))
    assert p["made_with"] == "brain"
    assert p["kind"] == "wedding planner"
    assert p["traits"] == ["warm", "organized", "cheerful"]
    assert "—" not in p["tone"]
    assert p["accent"] == persona.ACCENTS["rose"]
    assert p["intro"].endswith("Let me show you around.")


def test_intro_names_every_thing_and_never_the_owners_own_words():
    """The model's intro named jobs nothing
    installed could do, left the things out, and spoke the owner's story."""
    _add_skill("wedding-planner")
    _add_skill("party-guest-optimizer")
    p = persona.make(ask=_model_says({
        "kind": "wedding planner", "intro": "Help me plan my wedding next June. "
        "I can track themes from pictures.", "greeting": "Help me plan my wedding next June!"}))
    assert p["intro"].startswith("Hi, I'm Juniper, your wedding planner.")
    assert "wedding planner" in p["intro"] and "party guest optimizer" in p["intro"]
    assert "pictures" not in p["intro"] and "next June" not in p["intro"]
    assert "next June" not in p["greeting"]
    assert p["intro"].endswith("Let me show you around.")
    # Its own first sentence is kept, and a new thing is named after a refresh.
    say = _model_says({"kind": "wedding planner",
                       "intro": "I'm Juniper, your cheerful wedding sidekick! I do photos."})
    p = persona.make(ask=say)
    assert p["intro"].startswith("I'm Juniper, your cheerful wedding sidekick!")
    assert "photos" not in p["intro"]
    _add_skill("wedding-seat")
    assert "wedding seat" in persona.refresh(ask=say)["intro"]
    assert persona.get()["intro"].startswith("I'm Juniper, your cheerful wedding sidekick!")


def test_bad_model_answer_falls_back():
    p = persona.make(ask=_model_says({"icon": "not_an_icon", "accent": "#zzz"}))
    assert p["icon"] in persona.ICONS
    assert p["accent"] in persona.ACCENTS.values()
    p = persona.make(ask=lambda m: "no json here")
    assert p["made_with"] == "fallback"


def test_intro_stays_current_after_a_new_install():
    persona.make(ask=None)
    assert "Guest Optimizer" not in persona.get()["intro"]
    _add_skill("party-guest-optimizer")
    p = persona.get()
    assert "party guest optimizer" in p["intro"]
    assert "party guest optimizer" in p["things"]
    # A full refresh (the brain) keeps it current too.
    p = persona.refresh(ask=None)
    assert "party guest optimizer" in p["intro"]


def test_system_prompt_carries_tone_and_traits():
    assert brain_loop.system_notes([]) == [brain_loop.TOOL_NOTE]
    persona.make(ask=_model_says({
        "kind": "wedding planner", "traits": ["warm", "organized", "cheerful"],
        "tone": "Bright and calm, like a friend with a clipboard.",
        "greeting": "Hi, I'm Juniper!", "intro": "I'm Juniper, here to help."}))
    notes = brain_loop.system_notes([])
    assert notes[0] == brain_loop.TOOL_NOTE
    joined = "\n".join(notes)
    assert "warm, organized, cheerful" in joined
    assert "Bright and calm, like a friend with a clipboard." in joined


def test_chat_greeting_is_the_persona_hello():
    assert persona.chat_greeting("default") == "default"
    persona.make(ask=None)
    line = persona.chat_greeting("default")
    assert line.startswith("Hi! I'm Juniper")
    assert "Nothing leaves your computer without your consent" in line


def test_wake_up_is_shown_once_per_build(monkeypatch):
    from puffpai import api_server
    api_server.app.config.update(TESTING=True)
    monkeypatch.setattr(api_server, "_API_KEY", KEY)
    client = api_server.app.test_client()
    assert client.get("/bot/persona").status_code == 401
    got = client.get("/bot/persona", headers=AUTH).get_json()
    assert SHAPE <= set(got)
    assert got["wake"] is True
    assert client.post("/bot/persona/woke", headers=AUTH).get_json()["wake"] is False
    assert client.get("/bot/persona", headers=AUTH).get_json()["wake"] is False
    # A refresh keeps the record; a new build wakes up again.
    assert client.post("/bot/persona/refresh", headers=AUTH).get_json()["wake"] is False
    monkeypatch.setattr(persona, "build_key", lambda: "another-build")
    assert client.get("/bot/persona", headers=AUTH).get_json()["wake"] is True
    # The build itself (handoff) starts a fresh record.
    persona.mark_woke()
    assert persona.public(persona.make(ask=None, keep_woke=False))["wake"] is True


def test_a_new_thing_keeps_one_identity():
    """After a new install the icon, kind and greeting changed.
    Only the list of what it can do (and the intro naming it) may change."""
    _add_skill("wedding-planner")
    first = persona.make(ask=_model_says({
        "kind": "wedding planner", "traits": ["warm", "organized", "cheerful"],
        "tone": "Bright and calm.", "greeting": "Hello, I'm Juniper!",
        "intro": "I'm Juniper, your wedding sidekick.", "icon": "palette", "accent": "rose"}))
    _add_skill("wedding-seat")
    again = persona.refresh(ask=_model_says({
        "kind": "party helper", "traits": ["loud", "fast", "bold"], "tone": "Shouty.",
        "greeting": "Yo, Juniper here!", "icon": "celebration", "accent": "plum"}))
    for key in ("name", "kind", "icon", "accent", "traits", "tone", "greeting", "opener"):
        assert again[key] == first[key], key
    assert "wedding seat" in again["things"] and "wedding seat" in again["intro"]


def test_who_are_you_lists_only_its_real_things():
    """Before this, "who are you" claimed "wedding themes from pictures",
    which none of its things do."""
    _add_skill("wedding-planner", "Plan weddings with budgets.")
    assert persona.about_me() == ""                     # no personality yet
    persona.make(ask=_model_says({"intro": "I'm Juniper, your wedding sidekick."}))
    for asked in ("Hi! Who are you, and what can you help me with?", "What can you do?",
                  "who are you", "What are your skills?"):
        assert persona.asks_about_me(asked), asked
    for asked in ("What can you do about my budget?", "who are you and what is a veil?",
                  "Plan my seating chart"):
        assert not persona.asks_about_me(asked), asked
    said = persona.about_me()
    assert said.startswith("I'm Juniper, your wedding sidekick.")
    assert "**wedding-planner**: Plan weddings with budgets." in said
    assert "pictures" not in said
    note = persona.prompt_note()
    assert "wedding-planner (Plan weddings with budgets.)" in note
    assert "Never claim a job none of them does." in note
    from puffpai import api_server
    assert api_server._loop_front("Hi! Who are you, and what can you help me with?") == said


def test_intro_names_each_thing_once_and_kind_keeps_its_capitals():
    """PrintingBot check (screen 10): the opener named "Filament Vault and
    Troubleshooting Guide", then the list named them again; the kind read
    "your 3d printing assistant"."""
    have = ["Filament Vault", "3D Printer Troubleshooting Guide (Full Edition)"]
    opener = ("I am Juniper here to help you build amazing 3D creations with our new "
              "Filament Vault and Troubleshooting Guide.")
    intro = persona.intro_for("Juniper", "3D printing assistant", have, opener)
    assert intro.count("Filament Vault") == 1
    assert intro.count("Troubleshooting Guide") == 1
    assert intro.startswith("Hi, I'm Juniper, your 3D printing assistant.")
    kept = persona.intro_for("Juniper", "helper", have, "I'm Juniper, ready to print.")
    assert kept.startswith("I'm Juniper, ready to print.")
    _add_skill("filament-vault")
    p = persona.make(ask=_model_says({"kind": "3D Printing Assistant",
                                      "intro": "I'm Juniper, your print pal."}))
    assert p["kind"] == "3D printing assistant"
