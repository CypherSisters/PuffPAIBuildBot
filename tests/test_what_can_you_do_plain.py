"""The chat answer to "what can you do?" has no commands and agrees with itself.

StargazerBot for a 10-year-old: the answer listed
"use the StargazerBot app: scaffolded from your goal: I want a space bot ...", four
shell commands, and said "offline library (none added yet)" while two books
were being learned (and listed the library twice).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from puffpai import build_abilities as ba
from puffpai import self_knowledge
from puffpai.knowledge import find_more

MANIFEST = {"build": {"name": "StargazerBot"}, "brain": {"model": "qwen2.5:7b-instruct"},
            "posture": {"run_egress": "enumerated"},
            "surfaces": {"goal": "a space bot"}}


def _rows():
    return [ba.row("app:stargazerbot", "Use the StargazerBot app", ba.AVAILABLE),
            ba.row("knowledge_library", ba.LIBRARY_LABEL, ba.NOT_SET_UP,
                   "puffpai knowledge sync", note="1 book is being learned now, "
                   "and it works as soon as that finishes"),
            ba.row("home", "Know your home town", ba.NOT_SET_UP,
                   'puffpai home set "Your Town"'),
            ba.row("web_search", "Search the web", ba.NOT_IN_THIS_BUILD)]


def test_the_chat_answer_has_no_commands():
    text = "\n".join(self_knowledge.limits_summary(MANIFEST, _rows(), for_chat=True))
    assert "puffpai " not in text
    assert 'Ask me "how do I add more to you?"' in text
    assert "being learned now" in text and "none added yet" not in text


def test_the_terminal_version_still_names_the_commands():
    text = "\n".join(self_knowledge.limits_summary(MANIFEST, _rows()))
    assert 'puffpai home set "Your Town"' in text


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def test_a_book_whose_learning_stopped_is_not_called_in_progress():
    rows = find_more.ability_rows([{"status": "indexing", "filename": "a.zim",
                                    "started_at": "2020-01-01T05:03:54+00:00"}])
    line = ba.absent_line(rows[0], with_how=False)
    assert "did not finish" in line and "being learned now" not in line


def test_a_book_being_learned_is_not_called_missing():
    rows = find_more.ability_rows([{"status": "indexing", "filename": "a.zim",
                                    "progress_at": _now()}])
    library = rows[0]
    assert library["id"] == "knowledge_library"
    assert "being learned now" in ba.absent_line(library)
    assert "none added yet" not in library["label"]


def test_the_library_is_one_row(monkeypatch):
    ba.register_provider(find_more.ability_rows)
    try:
        rows = ba.abilities("a space bot")
    finally:
        ba.unregister_provider(find_more.ability_rows)
    labels = [r["label"] for r in rows]
    assert sum("offline" in label.lower() for label in labels) == 1


def test_a_scaffold_app_is_not_described_by_its_goal_sentence(monkeypatch):
    app = {"id": "stargazerbot", "name": "StargazerBot", "surfaces": [],
           "blurb": "Scaffolded from your goal: I want a space bot that knows "
                    "everything about space.",
           "data": {"default": {"planets": []}}}
    rows = ba._app_rows({}, [app])
    assert rows[0]["label"] == ("Use the StargazerBot app: its own page and the "
                                "lists it keeps for you")


def test_a_scaffold_app_with_no_lists_does_not_claim_any(monkeypatch):
    """The wizard no longer makes lists nobody asked
    for, so "the lists it keeps for you" would be untrue."""
    app = {"id": "stargazerbot", "name": "StargazerBot", "surfaces": [],
           "blurb": "Scaffolded from your goal: I want a space bot.",
           "data": {"default": {}}}
    rows = ba._app_rows({}, [app])
    assert rows[0]["label"] == "Use the StargazerBot app: its own page"
