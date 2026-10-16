"""The card contract: every refusal, and the secret that is never anywhere.

A card is what both renderers draw, so a card that validates loosely is a
question the CLI and the dashboard can disagree about. Each test below is one
refusal in ``puffpai.wizard.cards``, plus the answer checks, plus the rule that
a ``secret_entry`` value exists in exactly one place -- the argument handed to
a skill's ``apply`` -- and in no card, no log and no API reply.

Run:
    pytest tests/test_wizard_cards.py
"""
from __future__ import annotations

import pytest

from puffpai.wizard import cards, render_cli, stale


def _card(**over) -> dict:
    card = {
        "skill": "discover", "section": "machine",
        "title": "What this machine is", "decided": "32 GB, one disk",
        "status": "ready", "consequential": False,
        "estimate": {"bytes": 0, "seconds": 1, "basis": "measured"},
        "fields": [
            {"id": "memory_gb", "kind": "number", "label": "Memory, in GB",
             "min": 1, "max": 4096, "current": 32},
        ],
    }
    card.update(over)
    return card


def _field(**over) -> dict:
    field = {"id": "thing", "kind": "text", "label": "A thing"}
    field.update(over)
    return _card(fields=[field])


# -- the card itself ----------------------------------------------------------

def test_a_good_card_validates_and_comes_back_canonical():
    card = cards.validate_card(_card())
    assert card["schema"] == cards.SCHEMA
    assert card["gaps"] == []
    assert card["fields"][0]["proposed"] == 32


@pytest.mark.parametrize("over,why", [
    ({"skill": "Discover"}, "an id is lowercase"),
    ({"section": ""}, "a card names the section it writes"),
    ({"status": "fine"}, "a status is one of the five"),
    ({"title": ""}, "a card has a title in the owner's words"),
    ({"estimate": {"bytes": 0, "seconds": 1, "basis": "guessed"}},
     "a basis is measured, calibrated or unknown"),
    ({"estimate": {"bytes": -1, "seconds": 1, "basis": "measured"}},
     "bytes are not negative"),
    ({"consequential": True}, "a consequential row says why"),
    ({"consequential_reasons": ["heavy_bytes"]},
     "reasons without the flag is a half-stated row"),
    ({"consequential": True, "consequential_reasons": ["it is big"]},
     "a reason is one of the four codes"),
    ({"surprise": 1}, "a card carries no key a renderer was not taught"),
])
def test_a_card_that_is_not_one_is_refused(over, why):
    with pytest.raises(cards.CardError):
        cards.validate_card(_card(**over))


def test_a_card_is_not_a_string_and_fields_are_not_strings():
    with pytest.raises(cards.CardError):
        cards.validate_card("run this")
    with pytest.raises(cards.CardError):
        cards.validate_card(_card(fields=["type your PIN"]))


@pytest.mark.parametrize("over", [
    {"kind": "sudo"},
    {"id": "Memory"},
    {"label": ""},
    {"kind": "choice"},
    {"kind": "choice", "options": ["a"], "current": "b"},
    {"kind": "choice", "options": ["a"], "free_text": True},
    {"kind": "multi_choice", "options": ["a"], "free_text": True},
    {"kind": "text", "max_length": 0},
    {"kind": "text", "pattern": "([unclosed"},
    {"kind": "toggle"},
    {"kind": "number"},
    {"kind": "number", "min": 1, "max": 4, "current": 9},
    {"extra": "no"},
])
def test_a_field_that_is_not_one_is_refused(over):
    with pytest.raises(cards.CardError):
        cards.validate_card(_field(**over))


def test_two_fields_may_not_claim_the_same_id():
    field = {"id": "thing", "kind": "text", "label": "A thing"}
    with pytest.raises(cards.CardError):
        cards.validate_card(_card(fields=[field, dict(field)]))


def test_control_characters_are_dropped_from_what_is_shown():
    card = cards.validate_card(_field(label="A \x1b[2Jthing", help="a\nb"))
    assert "\x1b" not in card["fields"][0]["label"]
    # Dropped, not escaped and not turned into a space: the character does not
    # survive at all.
    assert card["fields"][0]["help"] == "ab"


def test_an_unknown_basis_carries_no_seconds_to_sum():
    card = cards.validate_card(_card(
        estimate={"bytes": 5, "seconds": 900, "basis": "unknown"}))
    assert card["estimate"]["seconds"] is None
    total = cards.total_estimate([card, cards.validate_card(_card())])
    assert total["bytes"] == 5
    assert total["seconds"] is None and total["basis"] == "unknown"


def test_a_total_over_measured_rows_is_a_number():
    total = cards.total_estimate([cards.validate_card(_card()),
                                  cards.validate_card(_card())])
    assert total == {"bytes": 0, "seconds": 2, "basis": "measured"}


# -- answers ------------------------------------------------------------------

def test_an_answer_is_checked_against_the_card_that_asked():
    card = cards.validate_card(_card())
    assert cards.validate_answers(card, {"memory_gb": 16}) == {"memory_gb": 16}
    for bad in ({"ram": 16}, {"memory_gb": "lots"}, {"memory_gb": 99999},
                "16", {"memory_gb": True}):
        with pytest.raises(cards.CardError):
            cards.validate_answers(card, bad)


def test_an_info_line_is_not_a_question():
    card = cards.validate_card(_field(kind="info", current="what was found"))
    with pytest.raises(cards.CardError):
        cards.validate_answers(card, {"thing": "changed"})


def test_a_choice_takes_its_options_and_a_typed_one_only_if_allowed():
    card = cards.validate_card(_field(
        kind="choice", options=["a", "b"], current="a"))
    assert cards.validate_answers(card, {"thing": "b"}) == {"thing": "b"}
    with pytest.raises(cards.CardError):
        cards.validate_answers(card, {"thing": "c"})

    typed = cards.validate_card(_field(
        kind="choice", options=["a"], current="a", free_text=True,
        pattern=r"^[a-z]+$"))
    assert cards.validate_answers(typed, {"thing": "zebra"}) == {"thing": "zebra"}
    with pytest.raises(cards.CardError):
        cards.validate_answers(typed, {"thing": "NOT A WORD!"})


def test_text_is_capped_and_pattern_checked():
    card = cards.validate_card(_field(max_length=4))
    with pytest.raises(cards.CardError):
        cards.validate_answers(card, {"thing": "far too long"})
    card = cards.validate_card(_field(pattern=r"^\d+$"))
    with pytest.raises(cards.CardError):
        cards.validate_answers(card, {"thing": "abc"})


def test_multi_choice_takes_a_list_of_its_own_options():
    card = cards.validate_card(_field(kind="multi_choice", options=["a", "b"],
                                      current=["a"]))
    assert cards.validate_answers(card, {"thing": ["b", "a", "b"]}) == {
        "thing": ["b", "a"]}
    with pytest.raises(cards.CardError):
        cards.validate_answers(card, {"thing": "b"})


# -- the secret ---------------------------------------------------------------

def test_a_card_may_not_carry_a_secret_value():
    with pytest.raises(cards.CardError):
        cards.validate_card(_field(kind="secret_entry", current="1234"))
    card = cards.validate_card(_field(kind="secret_entry", label="Vault PIN"))
    assert card["fields"][0]["current"] == ""


def test_a_secret_answer_reaches_apply_and_no_log():
    card = cards.validate_card(_field(kind="secret_entry", label="Vault PIN"))
    answers = cards.validate_answers(card, {"thing": "hunter2-and-then-some"})
    assert answers == {"thing": "hunter2-and-then-some"}
    logged = cards.redact_answers(card, answers)
    assert "hunter2" not in repr(logged)
    assert logged["thing"] == "(entered, not recorded)"


def test_a_secret_is_never_printed_by_the_console_renderer():
    card = cards.validate_card(_field(kind="secret_entry", label="Vault PIN"))
    lines = render_cli.card_lines(card)
    assert "Vault PIN: not set" in lines


# -- staleness ----------------------------------------------------------------

def test_a_changed_field_makes_only_what_depends_on_it_stale():
    assert stale.stale_for(["machine.memory"]) == ["brain", "prove", "handoff"]
    assert stale.stale_for(["persona.avatar"]) == []
    assert stale.stale_for([]) == []


def test_prove_and_handoff_are_downstream_of_everything():
    for key in stale.FIELD_STALENESS:
        assert stale.stale_for([key])[-2:] == ["prove", "handoff"]


def test_staleness_from_a_card_never_lists_the_card_itself():
    card = cards.validate_card(_card(fields=[
        {"id": "ollama_url", "kind": "text", "label": "Where Ollama answers",
         "current": "http://x:1"}]))
    assert stale.stale_for_answers(card, {"ollama_url": "http://y:2"}) == [
        "integrate", "brain", "prove", "handoff"]


# -- the word this wizard does not use ----------------------------------------

def test_the_wizard_never_uses_that_word():
    """One word is banned in shipped strings; it is spelled nowhere, here too."""
    import os

    banned = "".join(chr(c) for c in (104, 97, 116, 99, 104))
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "puffpai", "wizard")
    offenders = []
    for base, _dirs, names in os.walk(root):
        for name in names:
            if not name.endswith((".py", ".md", ".json")):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as handle:
                if banned in handle.read().lower():
                    offenders.append(path)
    assert offenders == []
