"""A choice word lifted out of a sentence that turns it around.

Blocker: the model passes only the choice word ("paid") out of a sentence
with a word that turns it around ("Sam has almost paid"); chat kept it
because choice_said only looked for a "no". Now every other word of her
message must be a little word that never changes a meaning, a word of the
choice's own label, or a whole value of hers that the model cannot use to
hide a word (never right beside the choice words unless she wrote a capital
letter or a mark there, never with a "no" or a turning word in it).

Major: a question (any script's question mark, or a question's word order)
never picks a change, in her message or in the model's words; words from
her other values can no longer make a turning word harmless.

Minor: the six hostile labels that still passed are refused, and "Read and
accept terms" passes again.

No network. Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import pytest

from puffpai.integrate.chat import ground
from puffpai.webapps import actions, recipe_check as rc

PAID = ["Paid", "Not paid"]
RSVP = ["Attending", "Not attending", "Maybe"]
AD = ["Attend", "Decline"]


def _chain(choices, text, model, says="Status"):
    """Chat's grounding, then the run's check: what the app would be set to."""
    spec = {"guest": {"type": "string", "says": "Guest", "required": True, "maxLength": 80},
            "status": {"type": "string", "enum": choices, "says": says, "required": True},
            "note": {"type": "string", "says": "Note", "maxLength": 200}}
    props = actions.schema_of(spec)["properties"]
    kept, _dropped = ground(text, model, props)
    got = actions.check_args(spec, kept) if "status" in kept else None
    return (got or {}).get("status")


#: The validator's probe rows (v_chain.py), each picked the plain choice before.
_VALIDATOR_CHAIN = [
    (PAID, "Sam has almost paid", "paid"), (PAID, "Sam still needs to be paid", "paid"),
    (PAID, "Sam would have paid", "paid"), (PAID, "Sam is yet to be paid", "paid"),
    (PAID, "Sam was supposed to have paid", "Paid"), (AD, "Leo refused to attend", "attend"),
    (AD, "Leo failed to attend", "attend"), (RSVP, "Leo stopped attending", "attending"),
    (RSVP, "Leo is hardly ever attending", "attending"), (PAID, "Sam paid but got refunded", "paid"),
    (RSVP, "Is Leo attending", "attending"), (RSVP, "Leo attending? check", "attending"),
    (PAID, "Sam will be paid next week", "paid"), (RSVP, "Leo dropped out, was attending", "attending"),
    (RSVP, "Is Leo attending?", "attending"),
]


@pytest.mark.parametrize("choices,text,value", _VALIDATOR_CHAIN)
def test_the_model_cannot_lift_the_choice_word_out_of_her_sentence(choices, text, value):
    guest = "Sam" if "Sam" in text else "Leo"
    assert _chain(choices, text, {"guest": guest, "status": value}) is None
    assert actions.choice_said(text, value, [guest]) is False


@pytest.mark.parametrize("choices,text,model", [
    (PAID, "Sam almost paid, note: almost", {"guest": "Sam", "note": "almost", "status": "almost paid"}),
    (PAID, "Sam almost paid", {"guest": "Sam almost", "status": "almost paid"}),
    (RSVP, "Leo formerly attending", {"guest": "Leo formerly", "status": "formerly attending"}),
    (PAID, "Sam almost paid", {"guest": "Sam almost", "status": "paid"}),
    (PAID, "Sam technically paid", {"guest": "Sam technically", "status": "paid"}),
    (PAID, "Sam wrongly marked paid", {"guest": "Sam wrongly", "status": "paid"}),
    (PAID, "Sam paid, note: refund later", {"guest": "Sam", "status": "paid", "note": "refund later"}),
    (PAID, "Sam paid, note: pretend", {"guest": "Sam", "status": "paid", "note": "pretend"}),
])
def test_her_other_values_never_hide_a_turning_word(choices, text, model):
    """The validator's context rows, then held-out ones: a value right beside
    the choice (written in small letters, no mark between), or one holding a
    turning word, covers nothing."""
    assert _chain(choices, text, model) is None


@pytest.mark.parametrize("said,context", [
    ("almost paid", ["Sam almost"]), ("Sam almost paid", ["Sam almost"]),
    ("formerly attending", ["Leo formerly"]), ("Leo formerly attending", ["Leo formerly"]),
    ("Leo attending", ["Leo"]),                   # the model's own words: never beside
    ("Sam almost is paid", ["Sam almost"]),       # a turning word never covers
])
def test_the_run_alone_never_lets_a_value_cover_a_word_beside_the_choice(said, context):
    choices = PAID if "paid" in said else RSVP
    assert actions.resolve_choice(choices, said, context) is None


#: Held out: written for this round, never used to shape the rule.
@pytest.mark.parametrize("choices,text,value", [
    (PAID, "Sam kinda paid", "paid"), (PAID, "Sam technically paid", "technically paid"),
    (RSVP, "Leo, formerly attending", "attending"), (RSVP, "Leo is attending, right", "attending"),
    (RSVP, "Leo attending - I wish", "attending"), (PAID, "Sam paid (refund pending)", "paid"),
    (PAID, "Sam paid lol", "paid"), (PAID, "Has Sam paid", "paid"),
    (PAID, "Please, has Sam paid", "paid"), (RSVP, "Are they attending", "attending"),
    (RSVP, "Did Leo attend, attending", "attending"), (PAID, "Sam unpaid? paid", "paid"),
    (RSVP, "Leo attending or maybe", "attending"), (RSVP, "Leo attending nope", "attending"),
    (PAID, "Sam paid in theory", "paid"), (PAID, "Sam paid\u2014not", "paid"),
    (PAID, "Sam paid\u2014 disputed", "paid"),
])
def test_held_out_sentences_never_pick_the_plain_choice(choices, text, value):
    guest = "Sam" if "Sam" in text else "Leo"
    assert _chain(choices, text, {"guest": guest, "status": value}) is None


@pytest.mark.parametrize("mark", ["?", "\xbf", "\u061f", "\u2e2e", "\uff1f", "\u203d", "\u2753",
                                  "\u037e", "\u055e", "\u2047", "\u2048", "\u2049", "\ufe56"])
def test_a_question_mark_of_any_script_never_picks_a_change(mark):
    assert actions.resolve_choice(RSVP, f"attending{mark}") is None
    assert actions.resolve_choice(RSVP, f"{mark}attending") is None
    assert actions.choice_said(f"Leo attending{mark}", "attending", ["Leo"]) is False


@pytest.mark.parametrize("said", ["is Leo attending", "has Sam paid", "are you attending",
                                  "please is she attending", "did Leo attend"])
def test_a_questions_word_order_never_picks_a_change(said):
    choices = PAID if "paid" in said else RSVP + ["Attend"]
    assert actions.resolve_choice(choices, said, ["Leo", "Sam"]) is None
    assert actions.choice_said(said, said.split()[-1], ["Leo", "Sam"]) is False


@pytest.mark.parametrize("choices,text,model,want", [
    (PAID, "Mark Sam as paid", {"guest": "Sam", "status": "paid"}, "Paid"),
    (PAID, "Sam is paid", {"guest": "Sam", "status": "paid"}, "Paid"),
    # fix 2: beside the choice at a sentence start, or parted by a mark, is asked back
    (PAID, "Sam paid", {"guest": "Sam", "status": "paid"}, None),
    (PAID, "sam: paid", {"guest": "sam", "status": "paid"}, None),
    (PAID, "Mark Sam paid", {"guest": "Sam", "status": "paid"}, "Paid"),
    (PAID, "Sam has paid", {"guest": "Sam", "status": "has paid"}, "Paid"),
    (PAID, "Set Sam to Paid", {"guest": "Sam", "status": "Paid"}, "Paid"),
    (RSVP, "Please mark Leo Park as attending", {"guest": "Leo Park", "status": "attending"},
     "Attending"),
    (RSVP, "Leo attending", {"guest": "Leo", "status": "attending"}, None),
    (RSVP, "Leo is not attending", {"guest": "Leo", "status": "not attending"}, "Not attending"),
    (RSVP, "Leo maybe", {"guest": "Leo", "status": "maybe"}, None),
    (RSVP, "Mark Leo as maybe", {"guest": "Leo", "status": "maybe"}, "Maybe"),
    (AD, "Leo Park: decline", {"guest": "Leo Park", "status": "decline"}, None),
    (AD, "Mark Leo Park as decline", {"guest": "Leo Park", "status": "decline"}, "Decline"),
    (PAID, "Set the payment for Sam to paid", {"guest": "Sam", "status": "paid"}, None),
    (PAID, "Set the payment of Sam to paid", {"guest": "Sam", "status": "paid"}, None),
])
def test_plain_sentences_still_reach_the_run(choices, text, model, want):
    """None rows are asked back on purpose ("for" and "of" are not on the
    little-word list); she can say it as "Mark Sam as paid"."""
    assert _chain(choices, text, model, says="Payment" if choices is PAID else "Status") == want


def test_the_choices_own_label_words_are_harmless_and_never_a_no():
    props = actions.schema_of({"s": {"type": "string", "enum": PAID, "says": "Payment"}})["properties"]
    assert actions.label_words(props["s"]) == frozenset({"payment"})
    assert actions.choice_said("Sam payment is paid", "paid", ["Sam"],
                               actions.label_words(props["s"])) is True
    bad = actions.schema_of({"s": {"type": "string", "enum": PAID,
                                   "says": "Not yet almost"}})["properties"]["s"]
    assert actions.label_words(bad) == frozenset()
    assert actions.label_words({"description": "Payment"}) == frozenset()   # not a choice field
    assert actions.label_words(None) == frozenset()


def test_small_letters_beside_the_choice_are_asked_back():
    """Fix 2: a value stands right beside the choice words only when she
    wrote a capital letter where no sentence starts ("Mark Sam paid"). At a
    sentence start ("Sam paid" reads like "Unlikely paid") or parted by a
    mark ("sam: paid" reads like "Untrue: paid") it is asked back."""
    assert actions.choice_said("sam paid", "paid", ["sam"]) is False
    assert actions.choice_said("Sam paid", "paid", ["Sam"]) is False
    assert actions.choice_said("sam: paid", "paid", ["sam"]) is False
    assert actions.choice_said("Mark Sam paid", "paid", ["Sam"]) is True
    assert actions.choice_said("mark sam paid", "paid", ["sam"]) is False
    # an apostrophe is never a mark that parts two words
    assert actions.choice_said("sam's paid", "paid", ["sam"]) is True     # "s" is a little word
    assert actions._tokens("sam's paid")[1] == ("s", False, False)


def test_the_turning_words_and_the_little_words_never_overlap():
    assert not (actions._TURNERS & actions._FILLER_WORDS)
    for w in ("will", "may"):                     # also names, left out on purpose
        assert w not in actions._TURNERS


# ---------------------------------------------------------------------------
# Minor: labels
# ---------------------------------------------------------------------------

_STILL_PASSING = ["Then resend to everyone", "Further, decline Leo", "Sam gets declined as well",
                  "Mark Leo declined, and Sam", "After this, archive all", "Same for Sam: decline"]
_HELD_OUT_LABELS = ["Likewise decline Eve", "Similarly, archive Sam", "Finally, reset all rows",
                    "Lastly set Leo to declined", "Sam is removed as well", "Eve gets archived too",
                    "Accept Leo, and Eve", "Then forward to everyone", "Before that, lock the list",
                    "Do the same: decline Sam", "Meanwhile update every row"]


@pytest.mark.parametrize("label", _STILL_PASSING + _HELD_OUT_LABELS)
@pytest.mark.parametrize("kind", ["words", "choice"])
def test_the_last_hostile_labels_are_refused(label, kind):
    assert not rc.label_ok(label, kind)


@pytest.mark.parametrize("label", [
    "Read and accept terms", "Review and accept", "Set date and time", "Share link", "Send to",
    "Forward to", "Email address", "Resend code", "After date", "Before date", "Further notes",
    "Same as billing", "Shared with", "Is archived", "Was declined", "Order again",
    "Terms and conditions", "Save and continue", "Tea and coffee", "Then and now",
])
def test_real_labels_pass(label):
    assert rc.label_ok(label, "words")


@pytest.mark.parametrize("text,model", [
    ("Leo attending, jk", {"guest": "Leo", "status": "attending", "note": "jk"}),
    ("Leo attending, kidding", {"guest": "Leo", "status": "attending", "note": "kidding"}),
    ("Sam paid, in theory", {"guest": "Sam", "status": "paid", "note": "in theory"}),
    ("Leo is attending, bringing cake", {"guest": "Leo", "status": "attending",
                                         "note": "bringing cake"}),
])
def test_a_value_after_the_choice_never_counts(text, model):
    """After the choice is where a "jk" stands: a note there covers nothing
    (the cake row is asked back on purpose)."""
    choices = PAID if "paid" in text else RSVP
    assert _chain(choices, text, model) is None
    assert actions.choice_said("Paid: Sam Lee", "paid", ["Sam Lee"]) is False   # fix 2: never after
    assert actions.choice_said("paid: sam", "paid", ["sam"]) is False
    assert actions.resolve_choice(PAID, "paid Sam Lee", ["Sam Lee"]) is None   # the model's words


@pytest.mark.parametrize("text,guest", [("Will is attending", "Will"), ("May has paid", "May")])
def test_a_name_that_is_also_an_asking_word_is_not_a_question(text, guest):
    choices = PAID if "paid" in text else RSVP
    want = "Paid" if "paid" in text else "Attending"
    value = text.split()[-1]
    assert _chain(choices, text, {"guest": guest, "status": value}) == want
    assert actions.choice_said(f"Will they be {value}", value, ["Leo"]) is False
