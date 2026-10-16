"""The personal-details detector: what it must catch, and what it must leave alone.

The "must catch" set is the 13 inputs from the privacy probe (a child's
address, school, birthday ...) plus adult inputs (VIN, licence plate, the last
four digits of a card, bank and id numbers). The "must leave alone" set is
ordinary talk from six very different bots (space, dinosaurs, a car, a garden,
knitting, music) - a detector that fires on those breaks every build, not one.
"""

from __future__ import annotations

import pytest

from puffpai.security.data_classifier import (
    PLAIN_WORDS,
    REDACTION_MARKERS,
    DataClassifier,
)
from puffpai.security.pii_classifier import PIIClassifier

C = DataClassifier()

MUST_CATCH = [
    ("I live at 42 Maple Street, Springfield IL 62704", "address", "42 Maple Street"),
    ("my mom's number is 555-867-5309", "phone", "555-867-5309"),
    ("email me at leo.kid@example.com", "email", "leo.kid@example.com"),
    ("I go to Lincoln Elementary, Mrs. Garcia's 4th grade class", "school", "Lincoln Elementary"),
    ("I have asthma and take albuterol, and I'm allergic to peanuts", "health.condition", "peanuts"),
    ("My birthday is March 3 2016, I'm 10", "date_of_birth", "March 3 2016"),
    ("My name is Leo Marlow", "name", "Leo Marlow"),
    ("my dad's SSN is 123-45-6789", "ssn", "123-45-6789"),
    ("card 4111 1111 1111 1111 exp 09/28", "credit_card", "4111 1111 1111 1111"),
    ("our wifi password is Sunflower2024!", "password", "Sunflower2024"),
    ("we are at 39.7817, -89.6501 tonight", "location", "39.7817"),
    ("my VIN is 1HGCM82633A004352", "vin", "1HGCM82633A004352"),
    ("recall check for 1HGCM82633A004352 please", "vin", "1HGCM82633A004352"),
    ("my license plate is 7ABC123", "license_plate", "7ABC123"),
    ("the card ending in 4821 was charged twice", "account_last4", "4821"),
    ("last four digits of my checking are 0093", "account_last4", "0093"),
    ("my checking account number is 000123456789", "bank_account", "000123456789"),
    ("routing number 021000021", "bank_account", "021000021"),
    ("my passport number is X12345678", "government_id", "X12345678"),
    ("driver's license D1234-5678-9012", "government_id", "D1234-5678-9012"),
]

MUST_LEAVE_ALONE = [
    # the probe's two false-positive traps
    "The launch is on 2026-09-20 at 1200 hours",
    "My scope serial is 8005551234",
    # G1 space
    "Point it at RA 05.5881, Dec -05.3911 for the Orion Nebula",
    "The ISS orbits at 51.64 degrees inclination, 408 km up",
    "M42 is magnitude 4.0 and 1344 light years away",
    # G2 dinosaurs
    "T. rex lived 68 to 66 million years ago and was 12.3 m long",
    "Stegosaurus had plates on its back, about 17 of them",
    # G3 car
    "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos",
    "NHTSA campaign 19V-182000 covers the airbag inflator; oil change every 5000 miles",
    "The part number is 15400-PLM-A02 for the oil filter",
    # G4 garden
    "A garden helper for my vegetable beds in Ohio, zone 6b, plant 3 rows 18 inches apart",
    "Tomatoes need 6 to 8 hours of sun; my plate of beans is ready",
    # G5 knitting
    "Use US size 8 needles (5 mm) and cast on 120 stitches for the hat",
    "Pattern row 12: k2, p2 repeat to end; worsted weight 200 yards",
    # G6 music
    "Taylor Swift released 1989 in 2014 and Pink Floyd released The Wall in 1979",
    "The band played 42 shows on the 2019 tour",
    "Which album has track 5 ending in a long guitar solo?",
    # plain
    "What is the tallest mountain on Mars?",
    "the year ending in 2024 was warm",
]


@pytest.mark.parametrize("text,kind,value", MUST_CATCH)
def test_catches_and_redacts(text, kind, value):
    redacted, det = C.redact_pii(text, include_health=True)
    assert kind in det.pii_types, (text, det.pii_types)
    assert value not in redacted, redacted
    assert REDACTION_MARKERS[kind] in redacted


@pytest.mark.parametrize("text", MUST_LEAVE_ALONE)
def test_leaves_ordinary_talk_alone(text):
    redacted, det = C.redact_pii(text, include_health=True)
    assert not det.has_pii, (text, det.items())
    assert redacted == text


def test_every_type_has_plain_words_and_no_jargon():
    for kind in C.redact_pii(" ".join(t for t, _, _ in MUST_CATCH),
                             include_health=True)[1].pii_types:
        words = PLAIN_WORDS[kind]
        assert "_" not in words and "." not in words
        from puffpai import plain_words
        assert plain_words.is_plain(words), words
        assert REDACTION_MARKERS[kind] == f"[{words} kept private]"


def test_summary_never_contains_a_value():
    det = C.scan_for_pii("my dad's SSN is 123-45-6789, call 555-867-5309")
    assert "123-45-6789" not in det.summary()
    assert "555-867-5309" not in det.summary()


def test_severity_levels():
    assert C.scan_for_pii("SSN 123-45-6789").severity == "critical"
    assert C.scan_for_pii("my passport number is X12345678").severity == "critical"
    assert C.scan_for_pii("I live at 42 Maple Street").severity == "high"
    assert C.scan_for_pii("call 555-867-5309").severity == "medium"
    assert C.scan_for_pii("my VIN is 1HGCM82633A004352").severity == "medium"
    assert C.scan_for_pii("hello").severity == "none"


def test_health_detector_is_off_unless_asked():
    text = "I have asthma"
    assert not C.scan_for_pii(text).has_pii
    assert C.scan_for_pii(text, include_health=True).has_pii


def test_invalid_ssn_and_bad_luhn_are_not_flagged():
    assert not C.scan_for_pii("code 000-12-3456").ssns
    assert not C.scan_for_pii("card 4111 1111 1111 1112").credit_cards


def test_serial_with_separators_is_not_a_phone():
    assert not C.scan_for_pii("serial 800-555-1234").phones


def test_vin_without_cue_needs_the_check_digit():
    # same shape, wrong check digit, no VIN word: left alone
    assert not C.scan_for_pii("code 1HGCM82634A004352 here").vins
    assert C.scan_for_pii("vin 1HGCM82634A004352").vins


def test_rescan_shim_has_redact_pii():
    # /knowledge/rescan_pii calls PIIClassifier().redact_pii; it used to be missing.
    redacted, det = PIIClassifier().redact_pii("SSN 123-45-6789")
    assert det.has_pii and "123-45-6789" not in redacted


@pytest.mark.parametrize("text", [t for t, _, _ in MUST_CATCH])
def test_redacted_text_has_nothing_left_to_find(text):
    # Found live: re-scanning "password is [password kept private]" filed
    # "[password" as a new password. A marker is never a detail.
    redacted, _ = C.redact_pii(text, include_health=True)
    again = C.scan_for_pii(redacted, include_health=True)
    assert not again.has_pii, (redacted, again.items())


def test_birthday_redacts_only_the_date():
    redacted, det = C.redact_pii("My birthday is March 3 2016, I'm 10")
    assert redacted == "My birthday is [birthday kept private], I'm 10"
    assert det.dates_of_birth == ["March 3 2016"]


@pytest.mark.parametrize("text", [
    "our wifi password is Sunflower!22. Please greet me by name.",
    "My name is Casey Verifier Please say hi",
    "my license plate is 7ABC123 Thanks a lot",
    "my passport number is 123456789 Okay then",
])
def test_the_word_after_a_marker_is_never_a_new_detail(text):
    # Re-scanning "password is [password kept
    # private] Please" filed "Please" as a password, because the marker was
    # blanked to spaces and the cue reached the next word.
    redacted, _ = C.redact_pii(text, include_health=True)
    again = C.scan_for_pii(redacted, include_health=True)
    assert not again.has_pii, (redacted, again.items())
