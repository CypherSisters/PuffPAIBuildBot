"""Closing the claims that a turning word can move out of a choice.

Blocker and major: through chat.ground then check_args, the model could move
a turning word that is not on the turning-word list ("Untrue", "doubt",
"pretends", "KIDDING") out of the choice and into a note or the guest's
name, and the plain choice ran. The fix is a shape rule, not a longer list:
at most ONE value of hers may cover words, standing once before the choice,
joined to it only by a closed set of link words ("is", "has", "as", "to"),
with no written mark between, and a name of more than one word only where
her capital letters show it (never at a sentence start). The list stays as a
second wall.

Minor: labels that hand things out with no "to" ("Then also export all
rows", "Additionally email every guest", "Mark paid, then text everyone")
are refused.

No network. Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import pytest

from puffpai.integrate.chat import ground
from puffpai.webapps import actions, recipe_check as rc

PAID = ["Paid", "Not paid"]
RSVP = ["Attending", "Not attending", "Maybe"]
AD = ["Attend", "Decline"]


def _chain(choices, text, model):
    """Chat's grounding, then the run's check: what the app would be set to."""
    spec = {"guest": {"type": "string", "says": "Guest", "required": True, "maxLength": 80},
            "status": {"type": "string", "enum": choices, "says": "Status", "required": True},
            "note": {"type": "string", "says": "Note", "maxLength": 200}}
    props = actions.schema_of(spec)["properties"]
    kept, _dropped = ground(text, model, props)
    got = actions.check_args(spec, kept) if "status" in kept else None
    return (got or {}).get("status")


def _note_only(choices, text, model):
    """An app with no name to pick: only the choice and a free note."""
    spec = {"status": {"type": "string", "enum": choices, "says": "Status", "required": True},
            "note": {"type": "string", "says": "Note", "maxLength": 200}}
    props = actions.schema_of(spec)["properties"]
    kept, _dropped = ground(text, model, props)
    got = actions.check_args(spec, kept) if "status" in kept else None
    return (got or {}).get("status")


#: The validator's p_chain.py rows, verbatim: each ran the plain choice at 3c96258.
_VALIDATOR_ROWS = [
    (PAID, "Untrue: Sam has paid", {"guest": "Sam", "note": "Untrue", "status": "paid"}),
    (PAID, "False, Sam has paid", {"guest": "Sam", "note": "False", "status": "paid"}),
    (RSVP, "I doubt Leo is attending", {"guest": "Leo", "note": "doubt", "status": "attending"}),
    (RSVP, "Leo wishes he is attending", {"guest": "Leo", "note": "wishes", "status": "attending"}),
    (RSVP, "Wrong that Leo is attending", {"guest": "Leo", "note": "Wrong that",
                                           "status": "attending"}),
    (PAID, "Sam claims he has paid", {"guest": "Sam", "note": "claims", "status": "paid"}),
    (PAID, "Sam pretends he has paid", {"guest": "Sam", "note": "pretends", "status": "paid"}),
    (RSVP, "Leo lied, he is attending", {"guest": "Leo", "note": "lied", "status": "attending"}),
    (RSVP, "Unlikely Leo is attending", {"guest": "Leo", "note": "Unlikely",
                                         "status": "attending"}),
    (AD, "Leo hates to attend", {"guest": "Leo", "note": "hates", "status": "attend"}),
    (PAID, "Sam paid. KIDDING", {"guest": "Sam", "note": "KIDDING", "status": "paid"}),
    (PAID, "Sam paid, Refund Pending", {"guest": "Sam", "note": "Refund Pending", "status": "paid"}),
    (PAID, "Sam paid, Joking", {"guest": "Sam", "note": "Joking", "status": "paid"}),
    (RSVP, "Leo Doubtful is attending", {"guest": "Leo Doubtful", "status": "attending"}),
]


@pytest.mark.parametrize("choices,text,model", _VALIDATOR_ROWS)
def test_the_validators_rows_never_run_the_plain_choice(choices, text, model):
    assert _chain(choices, text, model) is None


#: Held out: written for this round before the rule was run on them, never
#: used to shape it. Most use words that are on NO list, so only the shape
#: rule can refuse them.
_HELD_OUT = [
    (RSVP, "Hypothetically, Leo is attending", {"guest": "Leo", "note": "Hypothetically",
                                                "status": "attending"}),
    (PAID, "Dream on, Sam has paid", {"guest": "Sam", "note": "Dream on", "status": "paid"}),
    (RSVP, "In your dreams Leo is attending", {"guest": "Leo", "note": "In your dreams",
                                               "status": "attending"}),
    (PAID, "Ha Sam has paid", {"guest": "Sam", "note": "Ha", "status": "paid"}),
    (PAID, "Sam has paid, sarcastically", {"guest": "Sam", "note": "sarcastically",
                                           "status": "paid"}),
    (RSVP, "Mark Leo as attending, psych", {"guest": "Leo", "note": "psych",
                                            "status": "attending"}),
    (RSVP, "Mark Leo as attending. PSYCH", {"guest": "Leo", "note": "PSYCH",
                                            "status": "attending"}),
    (RSVP, "Rumour has it Leo is attending", {"guest": "Leo", "note": "Rumour has it",
                                              "status": "attending"}),
    (PAID, "Sam Owes has paid", {"guest": "Sam Owes", "status": "paid"}),
    (PAID, "Mark sam unpaid as paid", {"guest": "sam unpaid", "status": "paid"}),
    (RSVP, "Please, Bogus Leo is attending", {"guest": "Bogus Leo", "status": "attending"}),
    (RSVP, "Doubtful he is attending", {"guest": "Doubtful", "status": "attending"}),
    (RSVP, "Leo, supposedly, is attending", {"guest": "Leo", "status": "attending"}),
    (PAID, "Sam has paid Leo", {"guest": "Leo", "status": "paid"}),
    (AD, "Leo cannot wait to attend", {"guest": "Leo", "status": "attend"}),
    (AD, "Leo dreads having to attend", {"guest": "Leo", "note": "dreads having",
                                         "status": "attend"}),
    # the model passes her whole sentence as the choice words
    (PAID, "Untrue: Sam has paid", {"guest": "Sam", "note": "Untrue",
                                    "status": "Untrue: Sam has paid"}),
    (RSVP, "Leo Doubtful is attending", {"guest": "Leo Doubtful",
                                         "status": "Leo Doubtful is attending"}),
    (RSVP, "Unlikely Leo is attending", {"guest": "Unlikely Leo",
                                         "status": "Unlikely Leo is attending"}),
]


@pytest.mark.parametrize("choices,text,model", _HELD_OUT)
def test_held_out_sentences_never_run_the_plain_choice(choices, text, model):
    assert _chain(choices, text, model) is None


@pytest.mark.parametrize("text,model", [
    ("Untrue: paid", {"note": "Untrue", "status": "paid"}),
    ("Unlikely attending", {"note": "Unlikely", "status": "attending"}),
    ("Hypothetically, attending", {"note": "Hypothetically", "status": "attending"}),
    ("Attending. KIDDING", {"note": "KIDDING", "status": "attending"}),
    ("attending, jest", {"note": "jest", "status": "attending"}),
])
def test_an_app_with_only_a_note_never_hides_a_turning_word_in_it(text, model):
    choices = PAID if "paid" in text else RSVP
    assert _note_only(choices, text, model) is None


@pytest.mark.parametrize("choices,text,model,want", [
    (PAID, "Sam has paid", {"guest": "Sam", "status": "paid"}, "Paid"),
    (PAID, "Sam has paid", {"guest": "Sam", "status": "has paid"}, "Paid"),
    (RSVP, "Leo is attending", {"guest": "Leo", "status": "attending"}, "Attending"),
    (RSVP, "Leo is not attending", {"guest": "Leo", "status": "not attending"}, "Not attending"),
    (PAID, "Mark Sam as paid", {"guest": "Sam", "status": "paid"}, "Paid"),
    (PAID, "Mark Sam paid", {"guest": "Sam", "status": "paid"}, "Paid"),
    (PAID, "Set Sam to Paid", {"guest": "Sam", "status": "Paid"}, "Paid"),
    (PAID, "Mark Sam as paid now", {"guest": "Sam", "status": "paid"}, "Paid"),
    (PAID, "Sam has been paid", {"guest": "Sam", "status": "paid"}, "Paid"),
    (RSVP, "Please mark Leo Park as attending", {"guest": "Leo Park", "status": "attending"},
     "Attending"),
    (RSVP, "Will is attending", {"guest": "Will", "status": "attending"}, "Attending"),
    (RSVP, "attending", {"status": "attending"}, None),     # the guest is required
])
def test_plain_sentences_still_run(choices, text, model, want):
    assert _chain(choices, text, model) == want


def test_one_value_only_and_never_after_the_choice():
    assert actions.choice_said("Sam has paid", "paid", ["Sam"]) is True
    assert actions.choice_said("Sam's status is paid", "paid", ["Sam"], frozenset({"status"})) is True
    assert actions.choice_said("Untrue: Sam has paid", "paid", ["Sam", "Untrue"]) is False
    assert actions.choice_said("Sam has paid Leo", "paid", ["Sam", "Leo"]) is False
    assert actions.choice_said("Paid: Sam", "paid", ["Sam"]) is False
    # only link words join the value to the choice, never a new clause
    assert actions.choice_said("Doubtful he is attending", "attending", ["Doubtful"]) is False
    assert actions.choice_said("Leo, is attending", "attending", ["Leo"]) is False


def test_a_name_of_more_words_needs_her_capitals_where_no_sentence_starts():
    assert actions.choice_said("Mark Leo Park as attending", "attending", ["Leo Park"]) is True
    assert actions.choice_said("Leo Park is attending", "attending", ["Leo Park"]) is False
    assert actions.choice_said("Mark Leo park as attending", "attending", ["Leo park"]) is False
    assert actions.choice_said("Mark: Leo Park as attending", "attending", ["Leo Park"]) is False
    # the model's words alone carry no capital letters of hers
    assert actions.resolve_choice(RSVP, "Mark Leo Park as attending", ["Leo Park"]) is None
    assert actions.resolve_choice(RSVP, "Mark Leo as attending", ["Leo"]) == "Attending"


def test_the_turning_word_wall_reads_plain_endings():
    for w in ("pretends", "claimed", "joking", "kidding", "doubts", "lied", "hates"):
        assert actions._turns(w), w
    for w in ("sam", "leo", "emily", "will", "may", "is", "has", "paid", "attending"):
        assert not actions._turns(w), w
    assert not (actions._TURNERS & actions._FILLER_WORDS)
    assert not (actions._TURNERS & actions._LINK_WORDS)


# ---------------------------------------------------------------------------
# Minor: labels that hand things out
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", [
    "Then also export all rows", "Additionally email every guest", "Mark paid, then text everyone",
    # held out
    "Then share the list", "Afterwards email all guests", "Also export every row",
    "Finally text everyone", "Next, post the whole list", "Paid, and then message all guests",
    "Likewise print every record", "Also sync all contacts", "Mark paid and email everyone",
])
@pytest.mark.parametrize("kind", ["words", "choice"])
def test_labels_that_hand_things_out_are_refused(label, kind):
    assert not rc.label_ok(label, kind)


@pytest.mark.parametrize("label", [
    "Name and email", "Also send a copy", "Email address", "Share link", "Text message",
    "Send reminder", "Copy link", "Post code", "Phone and text", "Mark as paid", "Accept invite",
    "Save and close", "Set date and time", "Print", "Export",
])
def test_real_labels_still_pass(label):
    assert rc.label_ok(label, "words")
