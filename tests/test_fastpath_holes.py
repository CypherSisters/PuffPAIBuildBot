"""The fastpath asks instead of dropping, and `puffpai chat` can reach it.

Two holes found in review.

**The holes.** When a candidate app or list matched but the verb or the list did
not resolve - "add basil", "I planted tomatoes today", "delete the last one" - the
module returned None and the sentence went to the model, which on a brainless
build meant it went nowhere. It now asks back, but only on EVIDENCE that the
sentence is about a declared app, so ordinary chat is never hijacked.

**`puffpai chat` never reached the fastpath at all.** It exited 1 on a build with
no model before reading a single message, so the one command a brainless build
could have used to write a tomato down refused to open.

No model is called anywhere in this file.

Run:
    pytest tests/test_fastpath_holes.py
"""
from __future__ import annotations

import pytest

from puffpai import (
    app_data as app_data_mod,
    app_fastpath,
    app_registry,
    brain_health,
    cli,
    persona_scaffold,
    setup_ops,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    app_registry.rescan()
    yield
    app_registry.rescan()


@pytest.fixture
def garden(tmp_path, monkeypatch):
    """One owner persona: GardenBot, with plantings and a harvest log."""
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(app_data_mod, "_DATA_DIR", str(tmp_path / "app_data"))
    app_registry.rescan()
    persona_scaffold.scaffold("gardenbot", name="GardenBot",
                              goal="a garden and greenhouse bot",
                              lists="plantings, harvest log")
    app_registry.rescan()
    return tmp_path


def _say(text: str) -> str | None:
    return app_fastpath.handle(text, app_data_mod.AppData(),
                               app_registry.current_manifests())


def _plantings() -> list[str]:
    document = app_data_mod.AppData().get_data("gardenbot")
    return [app_fastpath._row_text(r) for r in document.get("plantings", [])]


# ---------------------------------------------------------------------------
# "add basil" - a verb, no list, one persona
# ---------------------------------------------------------------------------

def test_add_with_no_list_asks_which_one(garden):
    reply = _say("add basil")
    assert reply is not None
    assert "Which list" in reply
    assert "plantings" in reply and "harvest log" in reply
    assert _plantings() == [], "an ask-back must not write anything"


def test_remove_with_no_list_asks_which_one(garden):
    assert "Which list" in (_say("delete basil") or "")


def test_a_show_verb_with_no_list_is_still_not_guessed_at(garden):
    """Only add and remove are unambiguous enough. "what is it" is not a list."""
    assert _say("what is it") is None


def test_ordinary_chat_is_not_hijacked(garden):
    for sentence in ("what is the capital of France",
                    "tell me about photosynthesis",
                    "how do I take a screenshot",
                    "who are you"):
        assert _say(sentence) is None, sentence


def test_a_handwritten_apps_sentence_is_still_never_answered_here(garden):
    """"add milk to the shopping list" is FreshTable's, ask-backs included."""
    assert _say("add milk to the shopping list") is None
    assert _say("add milk to my groceries") is None


# ---------------------------------------------------------------------------
# a list named, no verb
# ---------------------------------------------------------------------------

def test_a_named_list_with_no_verb_asks_what_to_do_with_it(garden):
    reply = _say("basil for the plantings list")
    assert reply is not None
    assert "I keep the plantings list" in reply
    assert "add tomatoes to the plantings list" in reply
    assert _plantings() == []


def test_two_named_lists_and_no_verb_is_still_not_guessed_at(garden):
    assert _say("the plantings and the harvest log") is None


# ---------------------------------------------------------------------------
# "I planted tomatoes today"
# ---------------------------------------------------------------------------

def test_i_planted_x_writes_to_the_list_that_shares_the_stem(garden):
    reply = _say("I planted tomatoes today")
    assert reply is not None
    assert "Added tomatoes to the plantings list" in reply
    assert _plantings() == ["tomatoes"]


def test_the_trailing_when_is_not_part_of_the_item(garden):
    _say("I planted rocket this morning")
    assert _plantings() == ["rocket"]


def test_a_past_tense_verb_with_no_matching_list_is_left_to_the_model(garden):
    """"I watered the tomatoes" has no `watering` list, so it is not ours."""
    assert _say("I watered the tomatoes") is None
    assert _say("I called the vet") is None
    assert _plantings() == []


def test_the_stem_rule_is_the_gate_not_the_tense(garden):
    # Third person, and a question, are not somebody recording what they did.
    assert _say("she planted tomatoes") is None
    assert _say("should I plant tomatoes") is None
    assert _plantings() == []


# ---------------------------------------------------------------------------
# "delete the last one"
# ---------------------------------------------------------------------------

def test_delete_the_last_one_names_the_row_and_asks_for_the_sentence(garden):
    _say("add tomatoes to the plantings list")
    _say("add basil to the plantings list")
    reply = _say("delete the last one from the plantings list")
    assert reply is not None
    assert "The most recent thing on the plantings list is basil" in reply
    assert "remove basil from the plantings list" in reply
    # Nothing was deleted on a guess - there is no session for a "yes" to live in.
    assert _plantings() == ["tomatoes", "basil"]
    # And the sentence it named does work.
    assert "Took basil off the plantings list" in (
        _say("remove basil from the plantings list") or "")
    assert _plantings() == ["tomatoes"]


def test_delete_the_last_one_on_an_empty_list_says_so(garden):
    reply = _say("delete the last one from the plantings list")
    assert "The plantings list is empty" in (reply or "")


# ---------------------------------------------------------------------------
# `puffpai chat` reaches all of it, with no model at all
# ---------------------------------------------------------------------------

@pytest.fixture
def brainless(monkeypatch):
    """A build whose model is simply not there. The fastpath must still work."""
    monkeypatch.setattr(setup_ops, "brain_status", lambda: {
        "status": brain_health.UNREACHABLE,
        "message": "Nothing is answering at the Ollama address, so your bot "
                   "cannot think yet.",
    })

    def _no_backend():
        raise RuntimeError("no model on this box")

    monkeypatch.setattr("puffpai.config.get_llm_backend", _no_backend)
    monkeypatch.setattr(cli, "_maybe_autoindex", lambda *a, **k: None)


def test_chat_once_writes_a_tomato_down_with_no_model_and_exits_zero(
        garden, brainless, capsys):
    assert cli.main(["chat", "--once", "add tomatoes to the plantings list"]) == 0
    assert "Added tomatoes to the plantings list" in capsys.readouterr().out
    assert _plantings() == ["tomatoes"]


def test_chat_once_reads_the_list_back_with_no_model(garden, brainless, capsys):
    _say("add tomatoes to the plantings list")
    assert cli.main(["chat", "--once", "what is on the plantings list"]) == 0
    assert "tomatoes" in capsys.readouterr().out


def test_chat_once_removes_with_no_model(garden, brainless, capsys):
    _say("add tomatoes to the plantings list")
    assert cli.main(["chat", "--once",
                     "remove tomatoes from the plantings list"]) == 0
    assert "Took tomatoes off the plantings list" in capsys.readouterr().out
    assert _plantings() == []


def test_a_model_needing_line_gets_the_honest_sentence_and_exit_zero(
        garden, brainless, capsys):
    assert cli.main(["chat", "--once", "what is the capital of France"]) == 0
    out = capsys.readouterr().out
    assert "no language model backend on this build" in out
    assert "puffpai setup" in out
    assert "Traceback" not in out


def test_an_unreachable_model_says_which_address_and_still_exits_zero(
        garden, monkeypatch, capsys):
    """The commoner shape: a backend object exists, nothing answers behind it."""
    from puffpai.inference.ollama_backend import OllamaBackend
    # The backend object, made without asking a real Ollama whether it is up.
    monkeypatch.setattr("puffpai.config.get_llm_backend",
                        lambda *a, **k: OllamaBackend(url="http://localhost:11434",
                                                      model="qwen2.5:7b-instruct"))
    monkeypatch.setattr(setup_ops, "brain_status", lambda: {
        "status": brain_health.UNREACHABLE,
        "message": "Nothing is answering at the Ollama address, so your bot "
                   "cannot think yet.",
    })
    monkeypatch.setattr(cli, "_maybe_autoindex", lambda *a, **k: None)
    assert cli.main(["chat", "--once", "what is the capital of France"]) == 0
    out = capsys.readouterr().out
    assert "Nothing is answering at the Ollama address" in out
    assert "Traceback" not in out


def test_the_interactive_loop_opens_on_a_brainless_build(garden, brainless,
                                                        monkeypatch, capsys):
    """It used to refuse to open at all, which is the defect in one line."""
    lines = iter(["add basil to the plantings list", "/quit"])
    monkeypatch.setattr("builtins.input", lambda *a: next(lines))
    assert cli.main(["chat"]) == 0
    out = capsys.readouterr().out
    assert "No model is answering yet" in out
    assert "Added basil to the plantings list" in out
    assert _plantings() == ["basil"]
