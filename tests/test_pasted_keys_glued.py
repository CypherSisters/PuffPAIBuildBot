"""A key glued onto a run of letters in the same word is caught.

Found in an audit-log sweep: ``"x" * 10 + KEY + "x" * 10`` and
``"Supercalifragilisticexpialidocious" + KEY`` read as ordinary words, because
the whole word was judged at once (the run of letters made it look far less
random than the key in it) and the well-known key shapes need a break before
them. Now each word is also judged with the runs of letters at its two ends
taken off, and with a long run (8 or more) of one case taken off its ends
when what is left is shaped like a made-up key. The key shapes keep their
break: "sk-" and "BSA" occur inside ordinary words and slugs. Ordinary long
words, names, file names, slugs and words followed by a number are still
left alone, on their own and in a sentence.
"""
from __future__ import annotations

import random
import string

import pytest

from puffpai import web_search

K = "q8ZrT2vLx9KmW4pNc7Hs1YdQ3"
H = "3f9a1c7e5b2d8046af13c9e7d5b20a64"
B = "BSAq8ZrT2vLx9KmW4pNc7Hs1Yd"
S = "sk-q8ZrT2vLx9KmW4pNc7Hs1YdQ3"
SC = "Supercalifragilisticexpialidocious"
AD = "antidisestablishmentarianism"
TQ = "thequickbrownfoxjumpsoverthelazydog"

GLUED = {
    "x10+K+x10": "x" * 10 + K + "x" * 10,
    "x20+K": "x" * 20 + K,
    "K+x20": K + "x" * 20,
    "a30+K": "a" * 30 + K,
    "SC+K": SC + K,
    "K+SC": K + SC,
    "AD+K+AD": AD + K + AD,
    "TQ+K+TQ": TQ + K + TQ,
    "x10+H": "x" * 10 + H,
    "H+x10": H + "x" * 10,
    "TQ+H": TQ + H,
    "AD+H": AD + H,
    "apikey+H+apikey": "apikey" + H + "apikey",
    "x10+B+x10": "x" * 10 + B + "x" * 10,
    "SC+B": SC + B,
    "x20+S+x20": "x" * 20 + S + "x" * 20,
    "SC+S+SC": SC + S + SC,
    # Short glues were caught before; they stay caught.
    "abc+K+xyz": "abc" + K + "xyz",
    # The same, inside a sentence.
    "sentence x20+K": "here it is " + "x" * 20 + K + " thanks",
    "sentence SC+H": "the code " + SC + H + " ok",
}

#: Aimed at the change: long words, runs of one letter, names with digits in
#: them, identifiers, file names, slugs with "sk-" and "BSA" inside words.
STILL_LEFT_ALONE = [
    "Donaudampfschifffahrtsgesellschaftskapitaen",
    "pneumonoultramicroscopicsilicovolcanoconiosis",
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "hahahahahahahahahahahahahahaha",
    "#ThrowbackThursdayVibes2026",
    "Windows11ProInsiderPreview2026",
    "iPhone15ProMax256GB",
    "RTX4090FoundersEdition",
    "getUserProfileSettingsByAccountId2",
    "IMG_20260924_143012.jpg",
    "report-final-v2-2026-09-24.pdf",
    "desk-organizer-ideas-for-small-rooms",
    "task-management-app-for-busy-families",
    "risk-assessment-template-2026",
    "whisk-and-bowl-baking-set-for-beginners",
    "ABSAbsorbentKitchenTowels2026",
    "ACGTTGCAAGCTTACGGATCCGATAGCTAGCTAGGCTAACGT",
    "Order2026092414301200001Confirmed",
    "COVID19VaccinationScheduleForFamilies2026",
    "Supercalifragilisticexpialidocious2026",
    "antidisestablishmentarianism1999tothe2026edition",
    "my taskId is 42 and the folder is C:/Users/you/Documents/garden_2026",
    "thequickbrownfoxjumpsoverthelazydog",
    "PuffPAIBuildBotFactory2026SeptemberRelease",
    # CamelCase and capitals: a long run of one case at an end is trimmed too.
    "getElementsByClassName",
    "HTTPServerErrorHandlerFactory",
    "XMLHttpRequestEventTarget",
    "UIApplicationDidBecomeActiveNotification",
    "EXTREMELYLOUDANDINCREDIBLYCLOSE2011",
    "SupercalifragilisticexpialidociousHTTPRequest",
    # Taking one word off a run of words
    # left a part that read less like words than the whole.
    "exponentialRampToValueAtTime",  # dashboard/public/alarm-app.html
    "linearRampToValueAtTime",
    "AuthenticatedGrandchildren15016084",
    "ConfigurableTransparently10686145",
    "MeaningfullySynchronously2218640",
    "AchievementsYscrollcommand94444291",
    "ContextmanagerCryptographically11017876",
    "PalegoldenrodYscrollcommand83089524",
    "DiscriminatingTypescriptlang2439787",
]


@pytest.mark.parametrize("name", sorted(GLUED))
def test_a_key_glued_onto_letters_is_caught(name):
    assert web_search.looks_like_pasted_key(GLUED[name]), name


@pytest.mark.parametrize("text", STILL_LEFT_ALONE)
def test_long_words_and_names_are_still_left_alone(text):
    assert not web_search.looks_like_pasted_key(text), text


@pytest.mark.parametrize("text", STILL_LEFT_ALONE)
def test_long_words_and_names_are_left_alone_in_a_sentence(text):
    sentence = "please see " + text + " for details"
    assert not web_search.looks_like_pasted_key(sentence), sentence


def test_nearly_every_random_key_glued_onto_a_long_word_is_caught():
    """Random keys glued onto long words, before, after and both."""
    rng = random.Random(20260924)
    alnum = string.ascii_letters + string.digits
    words = [SC, AD, TQ, "x" * 20, "XXXXXXXXXXXXXXX",
             "Donaudampfschifffahrtsgesellschaft"]
    missed, total = [], 0
    for _ in range(100):
        key = "".join(rng.choice(alnum) for _ in range(32))
        word = rng.choice(words)
        for text in (word + key, key + word, word + key + word):
            total += 1
            if not web_search.looks_like_pasted_key(text):
                missed.append(text)
    # A random draw can read like words now and then (a key with almost no
    # digits in it); all but a few percent.
    assert len(missed) <= 0.03 * total, missed[:5]
