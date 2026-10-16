"""Zone 1, the Vault - the step where the owner sets their own lock.

Step 4 of the wizard. The vault is one of the three zones the pitch is built on,
and the pitch is only true if the owner sets it themselves. So this step is
explicit: they choose a PIN, we generate a recovery code, we show it once, and
they type it back before the wizard moves on. Typing it back is the whole point;
a checkbox does not make anybody write anything down.

What this module is careful about
---------------------------------
* **No second crypto path.** Sealing goes through the reference
  :class:`puffpai.security.vault.Vault` (PBKDF2-HMAC-SHA256 + Fernet envelope),
  either directly or via :func:`puffpai.setup_ops.create_vault`, which is a thin
  wrapper over the same class. Nothing here derives a key, wraps a key, hashes a
  PIN, or invents a format.
* **Secrets flow through, never into.** The PIN is handed to the vault and goes
  out of scope. The recovery code is returned to the caller for one screen. This
  module writes neither to disk, to a log, or to the manifest, and
  :func:`vault_manifest_block` does not even accept them as arguments - it
  structurally cannot record what it is never given.
* **Randomness comes from :mod:`secrets`.** Never :mod:`random`.

How the recovery code is actually redeemed
------------------------------------------
``RECOVERY_ESCROW_AVAILABLE`` is **True**: :mod:`puffpai.security.vault` stores
the vault's data key wrapped twice, once under the PIN and once under this code,
with independent salts. Either secret unwraps the same data key, and neither
wrapped copy reveals the other secret. Redeeming the code is a *recovery, not a
login* - :meth:`puffpai.security.vault.Vault.redeem_recovery_code` requires a new
PIN, re-keys the contents, and issues a fresh code, because a code that has been
read off paper should be assumed spent.

:data:`RECOVERY_UNWIRED_WARNING` is kept for the case where that mechanism is
ever absent again. :func:`recovery_notice_lines` adds it automatically whenever
the flag is False, which is why callers should use that function rather than
hand-rolling the screen.

All strings here are ASCII: they reach a Windows console.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import math
import re
import secrets
import time
from typing import Any, Optional

# ---------------------------------------------------------------------------
# PIN policy
# ---------------------------------------------------------------------------
#
# The PIN is the only secret protecting the vault, and the vault files sit on a
# disk somebody could walk off with, so the realistic attack is offline guessing
# against PBKDF2. The iteration count buys time; the PIN has to buy the rest.
# Four digits does not, which is why the floor here is deliberately above the
# 4-16 character range api_server accepts when *recognising* a typed PIN. That
# range is a recogniser for an existing PIN; this is the policy for setting a
# new one, and setting policy is where the strictness belongs.

# The wizard's floor must never be LOOSER than the vault's own floor
# (`security.vault._MIN_SECRET_LENGTH`), or the prompt accepts a PIN that the
# seal then rejects - the owner picks something, is told it is fine, and hits a
# WeakSecretError a screen later. Keep these in step; the wizard may be
# stricter, never laxer.
_MIN_PIN_LENGTH = 8           # mixed characters; matches the vault floor
_MIN_NUMERIC_PIN_LENGTH = 10  # digits only: a smaller alphabet needs more length
_MAX_PIN_LENGTH = 128         # bound the input at the trust boundary

# Well-known choices. Most short ones are already excluded by the length floors;
# these catch the longer classics that would otherwise sail through.
_COMMON_PINS = frozenset({
    "000000", "00000000", "111111", "11111111", "123456", "1234567", "12345678",
    "123456789", "654321", "87654321", "112233", "121212", "123123", "123321",
    "696969", "abc123", "abcd1234", "password", "passw0rd", "letmein", "qwerty",
    "qwertyui", "iloveyou", "trustno1", "welcome", "monkey", "dragon", "sunshine",
    "puffpai", "buildbot", "homebot",
    # keyboard columns, which no run check sees
    "1qaz2wsx", "1q2w3e4r", "1q2w3e4r5t", "zaq12wsx", "qazwsxedc",
    # famous numbers (pi, e, the golden ratio)
    "3141592653", "31415926535", "2718281828", "27182818284", "1618033988",
    "16180339887",
})


def _is_all_same(text: str) -> bool:
    """True when every character is identical (0000, aaaaaa)."""
    return len(set(text)) == 1


def _is_sequential_run(text: str) -> bool:
    """True when the whole string steps by exactly +1 or -1 (1234, 4321, abcd)."""
    if len(text) < 3:
        return False
    step = ord(text[1]) - ord(text[0])
    if step not in (1, -1):
        return False
    return all(ord(text[i + 1]) - ord(text[i]) == step for i in range(len(text) - 1))


def _is_repeated_block(text: str) -> bool:
    """True when the string is a short block repeated (121212, 123123, abcabc)."""
    length = len(text)
    for block in range(1, 4):
        if block < length and length % block == 0 and text == text[:block] * (length // block):
            return True
    return False


def validate_pin(pin: str) -> tuple[bool, str]:
    """Check a proposed vault PIN. Returns ``(ok, reason)``.

    ``reason`` is empty when the PIN is acceptable, and otherwise a plain,
    non-judgemental sentence saying what to change and why it matters. Nothing
    about the PIN is logged, stored, or echoed back inside the reason.
    """
    candidate = pin if isinstance(pin, str) else ""

    if not candidate:
        return False, (
            "Your vault needs a PIN. This is the one thing that stands between "
            "your private data and anyone who picks up this machine."
        )
    if any(ch.isspace() for ch in candidate):
        return False, (
            "Leave spaces out of the PIN. Spaces are easy to lose track of when "
            "you type it back later, so we do not allow them."
        )
    if any(not (32 < ord(ch) < 127) for ch in candidate):
        return False, (
            "Use characters you can type on any keyboard: letters, digits, or "
            "punctuation. Anything fancier can be impossible to enter on a "
            "different device."
        )
    if len(candidate) > _MAX_PIN_LENGTH:
        return False, (
            f"That is longer than {_MAX_PIN_LENGTH} characters. Anything past "
            f"that adds no real protection and is easy to mistype."
        )
    if candidate.isdigit() and len(candidate) < _MIN_NUMERIC_PIN_LENGTH:
        return False, (
            f"A digits-only PIN needs at least {_MIN_NUMERIC_PIN_LENGTH} digits. "
            f"There are only ten digits to choose from, so a short one can be "
            f"worked through quickly by a machine. Adding a letter lets you use "
            f"{_MIN_PIN_LENGTH}."
        )
    if len(candidate) < _MIN_PIN_LENGTH:
        return False, (
            f"That is {len(candidate)} characters. Use at least "
            f"{_MIN_PIN_LENGTH} characters so it is not quick to guess."
        )
    if _is_all_same(candidate):
        return False, (
            "That is the same character repeated. It is one of the first things "
            "anyone would try, so pick something with some variety in it."
        )
    if _is_sequential_run(candidate):
        return False, (
            "That runs straight up or down the keyboard. Guessing programs start "
            "with runs like that, so it would not hold for long."
        )
    if _is_repeated_block(candidate):
        return False, (
            "That is a short pattern repeated. It is shorter than it looks to "
            "anything trying to guess it, so mix it up a bit."
        )
    if candidate.lower() in _COMMON_PINS:
        return False, (
            "That one shows up near the top of every published list of common "
            "choices, which is the first place a guessing program looks. Almost "
            "anything else is a better pick."
        )
    # Runs that wrap around (1234567890, 7890123456), runs along the keyboard
    # (qwertyuiop), two or more runs back to back (abcd1234, 12341234,
    # 123456123456123456, qwertqwertqwert, 1234512345123), a short block
    # repeated with a part-block at the end (abcabcab) and digits counting by
    # a fixed step (1357913579), fixed-step runs back to back (2468013579),
    # keypad column walks (1470258369) and shapes drawn on the keypad
    # (1478963250): the vault's own floor and more, said in the wizard's
    # words.
    from puffpai.security.vault import weak_pattern
    kind = weak_pattern(candidate)
    if kind == "repeat":
        return False, (
            "That is a short pattern repeated. It is shorter than it looks to "
            "anything trying to guess it, so mix it up a bit."
        )
    if kind == "keypad":
        return False, (
            "That draws a shape on the number pad, like a snake, a spiral or "
            "a square. Guessing programs try shapes like that, so pick "
            "something with no pattern in it."
        )
    if kind:
        return False, (
            "That counts up or down, by ones or by bigger steps, or runs along "
            "the keyboard or keypad, even if it wraps around. Guessing programs "
            "start with patterns like that, so pick something with no pattern "
            "in it."
        )
    shape = _guessable_shape(candidate)
    if shape:
        return False, _SHAPE_REASONS[shape]
    return True, ""


# ---------------------------------------------------------------------------
# shapes a guessing program tries next
# ---------------------------------------------------------------------------
#
# The checks above see one pattern at a time. These see the usual ways a
# pattern is dressed up: marks between the characters (12-34-56-78), two runs
# woven together (1a2b3c4d5e, 0192837465), a pattern with one or two
# characters stuck on (0000000001, 12121212a), doubled characters (112233445566),
# keyboard columns (1qazxsw2, qazwsxedc1) and a common password with digits or
# look-alike characters (p@ssw0rd1, iloveyou1, letmein12). The PIN rule only:
# the vault's own floor (which allows spaced passphrases) is unchanged.

_SHAPE_REASONS = {
    "marks": ("Dashes, dots and other marks do not hide a pattern: without "
              "them that is still easy to guess. Pick something with no "
              "pattern in it."),
    "woven": ("That weaves two patterns together, like 1a2b3c. Guessing "
              "programs try that early, so pick something with no pattern in "
              "it."),
    "few": ("That uses only a few different characters, mostly repeated. "
            "Guessing programs start there, so mix it up more."),
    "padded": ("That is a pattern with a character or two added. Guessing "
               "programs try that early, so pick something with no pattern in "
               "it."),
    "keyboard": ("That runs along the keyboard, down the columns or across the "
                 "rows. Guessing programs start with patterns like that, so "
                 "pick something with no pattern in it."),
    "common": ("That is a common password with a few characters changed or "
               "added. Guessing programs try those first, so pick something "
               "that is not a common word."),
    "twice": ("That is the same thing typed twice. It is no harder to guess "
              "than typing it once, so pick something with no pattern in it."),
    "date": ("That looks like a date. Birthdays and other dates are among the "
             "first things guessing programs try, so pick something that is "
             "not a date."),
    "phone": ("That looks like a phone number. Guessing programs try those, "
              "and people who know you may know it, so pick something else."),
    "sequence": ("That is a well-known run of numbers, like the start of pi or "
                 "the Fibonacci numbers. Guessing programs try those, so pick "
                 "something with no pattern in it."),
    "word_number": ("That is a word or a name with a number or a year on the "
                    "end. Guessing programs try every word that way, so mix "
                    "letters, numbers and marks all through it."),
}

#: The keyboard's columns, top to bottom (1qaz, 2wsx, ...).
_COLUMNS = ("1qaz", "2wsx", "3edc", "4rfv", "5tgb", "6yhn", "7ujm", "8ik,",
            "9ol.", "0p;/")

#: Common passwords and words people build a PIN around.
_COMMON_WORDS = frozenset({
    "password", "passwort", "letmein", "iloveyou", "trustno", "welcome",
    "monkey", "dragon", "sunshine", "princess", "football", "baseball",
    "soccer", "hockey", "master", "shadow", "superman", "batman", "admin",
    "login", "hello", "freedom", "whatever", "starwars", "secret", "qwerty",
    "changeme", "puffpai", "buildbot", "homebot", "michael", "jennifer",
    "charlie", "computer", "internet", "summer", "winter", "flower", "cookie",
    "pokemon", "matrix", "mustang", "access", "family", "loveme", "abc",
}) | frozenset({
    # Months, seasons and weekdays are handled the same way (without
    # this, Summer2024 is refused and January2020 is not).
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "jan", "feb", "mar",
    "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "spring", "summer", "autumn", "fall", "winter",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday",
    # and the words a vault PIN is built around
    "vault", "mypin", "mysecret", "mypassword",
}) | frozenset({
    # holidays and the days people mark, the same way as the seasons (so
    # Christmas2026 is refused like Summer2024)
    "christmas", "xmas", "easter", "halloween", "thanksgiving", "hanukkah",
    "chanukah", "diwali", "ramadan", "kwanzaa", "passover", "valentine",
    "valentines", "newyear", "newyears", "birthday", "anniversary",
    "wedding", "holiday", "holidays", "vacation", "juneteenth",
    "mothersday", "fathersday", "stpatricks",
    # words near the top of every published list of choices
    "jesus", "christ", "blessed", "angel", "purple", "orange", "yellow",
    "silver", "golden", "roblox", "minecraft", "fortnite", "unicorn",
    "rainbow", "butterfly", "sunflower", "chocolate", "banana", "cheese",
    "ninja", "spiderman", "babygirl",
    # well-known passphrases, word for word (the comic's own example)
    "correcthorsebatterystaple", "correcthorse", "batterystaple",
    "horsebatterystaple", "troubador", "troubadour", "pinnumber",
    "mypinnumber", "passcode", "mypasscode", "pincode", "mypincode",
})

#: The given names parents pick most (published baby-name lists), for a
#: name or two names with a year (LiamNoah2015). Only read with a
#: number or marks next to them, never on their own.
_NAMES = frozenset((
    "liam noah oliver james elijah william henry lucas benjamin theodore "
    "mateo levi sebastian daniel jack alexander owen asher samuel ethan leo "
    "jacob logan jackson david joseph john luke ezra matthew olivia emma "
    "charlotte amelia sophia mia isabella ava evelyn luna harper sofia "
    "camila eleanor elizabeth violet scarlett emily hazel lily chloe grace "
    "ella aria nora zoey riley madison abigail sarah jessica ashley anna "
    "maria jordan taylor morgan").split())

#: How a phrase PIN often starts (ilovemykids, mypinnumber, letmeinplease).
_PHRASE_STARTS = ("ilove", "iluv", "mypin", "mypass", "mysecret", "letmein",
                  "opensesame")

#: Month names, whole and short, for a date written with one (Dec2020abc).
_MONTH_NAMES = ("january", "february", "march", "april", "may", "june",
                "july", "august", "september", "october", "november",
                "december", "sept", "jan", "feb", "mar", "apr", "jun", "jul",
                "aug", "sep", "oct", "nov", "dec")

#: Numbers known from songs and films, wherever they sit in a PIN.
_FAMOUS_NUMBERS = ("8675309",)

#: Well-known runs of numbers (Fibonacci, pi, e, the golden ratio, root two,
#: squares, cubes, primes, powers of two).
_SEQUENCES = (
    "0112358132134558914423337761098715972584418167651094617711",
    "31415926535897932384626433832795028841971",
    "27182818284590452353602874713526624977572",
    "16180339887498948482045868343656381177203",
    "14142135623730950488016887242096980785696",
    "0149162536496481100121144169196225256289324361400",
    "0182764125216343512729100013311728219727443375",
    "235711131719232931374143475359616771737983899710110310710911",
    "1248163264128256512102420484096819216384",
)

#: Marks a word may sit between ("Jordan2026!", "!Minecraft1").
_EDGE_MARKS = "!@#$%^&*()_-+=.,?~"


def _date_at(digits: str) -> bool:
    """True when 8 digits read as YYYYMMDD, MMDDYYYY or DDMMYYYY (1920-2039),
    or 6 digits as MMDDYY, DDMMYY or YYMMDD."""
    def md(m: int, d: int) -> bool:
        return 1 <= m <= 12 and 1 <= d <= 31

    if len(digits) == 8:
        if 1920 <= int(digits[:4]) <= 2039 and md(int(digits[4:6]), int(digits[6:])):
            return True
        a, b = int(digits[:2]), int(digits[2:4])
        return 1920 <= int(digits[4:]) <= 2039 and (md(a, b) or md(b, a))
    if len(digits) == 6:
        a, b, c = int(digits[:2]), int(digits[2:4]), int(digits[4:])
        return md(a, b) or md(b, a) or md(b, c)
    if len(digits) == 7 and 1920 <= int(digits[3:]) <= 2039:
        # A one-digit month or day (3142015 is 3/14/2015)
        one, two = int(digits[0]), int(digits[1:3])
        first, last = int(digits[:2]), int(digits[2])
        return md(one, two) or md(two, one) or md(first, last) or md(last, first)
    return False


def _date_shape(core: str) -> bool:
    """A date, at the start or the end, with at most two characters more
    (12-25-1990, 0611199001, 19850611xx, 25121990ab)."""
    for width in (8, 7, 6):
        if len(core) < width or len(core) - width > 2:
            continue
        for window in {core[:width], core[len(core) - width:]}:
            if window.isdigit() and _date_at(window):
                return True
    return False


def _word_date_shape(core: str) -> bool:
    """A name or a word with a date (Mia03142015, 3142015xx), or a date
    written with the month's name and at most three letters more
    (Dec2020abc, march1415).

    Letters on one side only. Eight digits are a date with any letters; six
    or seven only beside a word or a name, or one or two letters (a random
    eight-character PIN often holds six digits that read as a date)."""
    got = re.fullmatch(r"([a-z]*)([0-9]+)([a-z]*)", core)
    if not got:
        return False
    head, digits, tail = got.groups()
    letters = head or tail
    if letters and not (head and tail) and 6 <= len(digits) <= 8 \
            and _date_at(digits) and (len(digits) == 8 or len(letters) <= 2
                                      or _words_or_names(letters)):
        return True
    for word, other in ((head, tail), (tail, head)):
        if len(other) > 3 or word not in _MONTH_NAMES:
            continue
        if len(digits) <= 2 and 1 <= int(digits) <= 31:
            return True
        if len(digits) == 4 and (1900 <= int(digits) <= 2039 or (
                1 <= int(digits[:2]) <= 31)):
            return True
        if len(digits) in (5, 6) and 1 <= int(digits[:len(digits) - 4]) <= 31 \
                and 1900 <= int(digits[-4:]) <= 2039:
            return True
    return False


#: A phone number written as one: (614) 555-0199, 614-555-0199, +1 614 555 0199.
_PHONE_WRITTEN = re.compile(r"\+?1?[ .\-]?\(?\d{3}\)?[ .\-]?\d{3}[ .\-]\d{4}")
#: ...or with the marks only round the area code: (555)8675309.
_PHONE_LOOSE = re.compile(r"\+?1?[ .\-]?\(?\d{3}\)?[ .\-]?\d{3}[ .\-]?\d{4}")


def _phone_shape(text: str, core: str) -> bool:
    """Ten digits (eleven with a leading 1) written as a phone number, with
    a pattern after the area code (5551234567), or a number everybody knows
    (8675309, anywhere). Also the area code 555 and the 555-01
    numbers films use (5558675309), and marks only round the area code
    ((555)8675309)."""
    if any(n in core for n in _FAMOUS_NUMBERS):
        return True
    digits = core[1:] if len(core) == 11 and core.startswith("1") else core
    if len(digits) != 10 or not digits.isdigit():
        return False
    if core != text and (_PHONE_WRITTEN.fullmatch(text) or (
            _PHONE_LOOSE.fullmatch(text) and any(m in text for m in "()+.-"))):
        return True
    if digits[:3] == "555" or digits[3:8] == "55501":
        return True
    return _is_weak_core(digits[3:], part=True)


def _sequence_shape(core: str) -> bool:
    """Eight digits or more from a well-known run, a character or two either side."""
    if not core.isdigit():
        return False
    for head in range(3):
        for tail in range(3):
            middle = core[head:len(core) - tail]
            if len(middle) >= 8 and any(middle in s for s in _SEQUENCES):
                return True
    return False


_WORD_THEN_NUMBER = re.compile(r"([a-z]{3,})[!@#$%^&*()_\-+=.,?~]?([0-9]{1,6})")
_NUMBER_THEN_WORD = re.compile(r"([0-9]{1,6})[!@#$%^&*()_\-+=.,?~]?([a-z]{3,})")


#: Letter pairs and runs that words almost never have.
_UNSAYABLE = re.compile(r"q(?!u)|[jqxzvwk]{2}|(.)\1\1")

#: The letter pairs English words use most (the 262 most frequent, by
#: published bigram counts). A word is nearly all of these; random letters
#: are not.
_COMMON_PAIRS = frozenset((
    "th he in er an re on at en nd ti es or te of ed is it al ar st to nt ng "
    "se ha as ou io le ve co me de hi ri ro ic ne ea ra ce li ch ll be ma si "
    "om ur ca el ta la ns di fo ho pe ec pr no ct us ac ot il tr ly nc et ut "
    "ss so rs un lo wa ge ie wh ee wi em ad ol rt po we na ul ni ts mo ow pa "
    "im mi ai sh ir su id os iv ia am fi ci vi pl ig tu ev ld ry mp fe bl ab "
    "gh ty op wo sa ay ex ke fr oo av ag if ap gr od bo sp rd do uc bu ei ov "
    "by rm ep tt oc fa ef cu rn sc gi da yo cr cl du ga qu ue ff ba ey ls va "
    "um pp ua up lu go ht ru ug ds lt pi rc rr eg au ck ew mu br bi pt ak pu "
    "ui rg ib tl ny ki rk ys ob mm fu ph og ms ye ud mb ip ub oi rl gu dr hr "
    "cc tw ft wn nu af hu nn eo vo rv nf xp gn sm fl iz ok nl my gl aw ju oa "
    "eq sy sl ps jo lf nv je nk kn gs dy hy ze ks xt bs ik dd cy rp sk").split())


def _reads_as_a_word(letters: str) -> bool:
    """Letters that read as a word or a name.

    A quarter to seven tenths of them vowels (y counts), no more than three
    other letters or two vowels in a row, no pair words almost never have (a
    q with no u, qx, a letter three times), and every pair of letters next to
    each other one that words use often (one exception allowed from seven
    letters up). Measured: 80 of 93 common words and names pass
    (Minecraft, Liverpool, Jordan, vaultpin), and about 2 in 1000 random
    eight-character PINs of letters then digits.
    """
    vowels = sum(1 for ch in letters if ch in "aeiouy")
    if not (4 * vowels >= len(letters) and 10 * vowels <= 7 * len(letters)):
        return False
    consonants = in_a_row = 0
    for ch in letters:
        if ch in "aeiouy":
            in_a_row, consonants = in_a_row + 1, 0
        else:
            in_a_row, consonants = 0, consonants + 1
        if consonants > 3 or in_a_row > 2:
            return False
    if _UNSAYABLE.search(letters):
        return False
    rare = sum(1 for k in range(len(letters) - 1)
               if letters[k:k + 2] not in _COMMON_PAIRS)
    return rare <= (1 if len(letters) >= 7 else 0)


def _a_word_or_name(letters: str) -> bool:
    return (letters in _COMMON_WORDS or letters in _NAMES
            or _reads_as_a_word(letters))


def _known(letters: str) -> bool:
    return letters in _NAMES or letters in _COMMON_WORDS


def _words_or_names(letters: str) -> bool:
    """A word or a name, or two run together (liamnoah): two known names or
    common words, or one of them and a word of four letters or more. Two
    pieces that only read as words are not enough (too many random letters
    split that way)."""
    if _a_word_or_name(letters):
        return True
    for k in range(3, len(letters) - 2):
        a, b = letters[:k], letters[k:]
        if (_known(a) and (_known(b) or (len(b) >= 4 and _reads_as_a_word(b)))) \
                or (_known(b) and len(a) >= 4 and _reads_as_a_word(a)):
            return True
    return False


def _a_number_people_add(digits: str) -> bool:
    """One or two digits, a year from 1900 to 2039, or digits that are a
    pattern on their own (1234, 12345, 777777, 121212). Without this,
    jesus12345, purple1234 and Chelsea1905 are taken."""
    if len(digits) <= 2 or (len(digits) == 4 and 1900 <= int(digits) <= 2039):
        return True
    return (_is_all_same(digits) or _is_sequential_run(digits)
            or _is_repeated_block(digits))


def _word_number_shape(text: str) -> bool:
    """A word or a name with a number or a year on the end or in front
    (Minecraft1, Liverpool1, Jordan2026, 2026jordan, Morgan1!), or with only
    marks round it (Morgan!!!!)."""
    bare = text.strip(_EDGE_MARKS)
    if bare != text and bare.isalpha() and bare.isascii() and len(bare) >= 4 \
            and _reads_as_a_word(bare):
        return True
    got = _WORD_THEN_NUMBER.fullmatch(bare)
    if got:
        letters, digits = got.group(1), got.group(2)
    else:
        got = _NUMBER_THEN_WORD.fullmatch(bare)
        if not got:
            return False
        digits, letters = got.group(1), got.group(2)
    return _a_number_people_add(digits) and _words_or_names(letters)


def _common_inside(text: str) -> bool:
    """Letters only, at least half of them one common word (mysecretpin)."""
    bare = text.strip(_EDGE_MARKS)
    if not bare.isalpha() or not bare.isascii():
        return False
    if bare.startswith(_PHRASE_STARTS):
        return True
    return any(len(w) >= 5 and w in bare and 2 * len(w) >= len(bare)
               for w in _COMMON_WORDS)


def _said_twice(core: str) -> bool:
    """One block of four or more said again (a part-block at the end too)."""
    n = len(core)
    return any(all(core[k] == core[k % block] for k in range(n))
               for block in range(4, n // 2 + 1))


def _pattern_with_insert(core: str) -> bool:
    """A pattern with one or two characters of the other kind put in
    anywhere: a letter among digits (1478963k250, 14789k63250) or a digit
    among letters (qwer7tyuiop). Those one or two are set aside and the rest
    is read as a pattern on its own."""
    digits = "".join(ch for ch in core if ch.isdigit())
    letters = "".join(ch for ch in core if not ch.isdigit())
    for rest, put_in in ((digits, letters), (letters, digits)):
        if 0 < len(put_in) <= 2 and len(rest) >= 6 and _is_weak_core(rest, part=True):
            return True
    return False

#: Look-alike characters, each with every letter it may stand for.
_LOOKALIKES = {"@": "a", "4": "a", "3": "e", "1": "il", "!": "i", "0": "o",
               "$": "s", "5": "s", "7": "t", "+": "t", "8": "b", "9": "g"}


def _is_weak_core(text: str, part: bool = False) -> bool:
    """True when *text* on its own is a pattern or a common choice.

    *part* is for a piece of the PIN (every other character, the middle with
    a character or two taken off, doubled characters said once): the piece
    must be a pattern all the way through (``weak_pattern(strict=True)``).
    Letting a piece also have characters left over refused about one random
    ten-digit PIN in twenty.
    """
    if len(text) < 3:
        return False
    from puffpai.security.vault import _COMMON_SECRETS, weak_pattern
    return bool(_is_all_same(text) or _is_sequential_run(text)
                or _is_repeated_block(text) or weak_pattern(text, strict=part)
                or text in _COMMON_PINS or text in _COMMON_SECRETS)


def _column_steps(a: str, b: str) -> set:
    found = set()
    for col in _COLUMNS:
        i, j = col.find(a), col.find(b)
        if i >= 0 and j >= 0 and abs(i - j) == 1:
            found.add((col, j - i))
    return found


def _keyboard_segments(text: str) -> list[int]:
    """Lengths of the runs *text* splits into, along rings, rows or columns."""
    from puffpai.security.vault import _steps

    def steps(a: str, b: str) -> set:
        return _steps(a, b) | _column_steps(a, b)

    out: list[int] = []
    i = 0
    while i < len(text):
        best = 1
        if i + 1 < len(text):
            for kind in steps(text[i], text[i + 1]):
                j = i + 1
                while j + 1 < len(text) and kind in steps(text[j], text[j + 1]):
                    j += 1
                best = max(best, j - i + 1)
        out.append(best)
        i += best
    return out


def _spellings(text: str) -> list[str]:
    """*text* with look-alike characters read back as letters (a few ways)."""
    out = [""]
    for ch in text:
        options = _LOOKALIKES.get(ch, ch)
        out = [s + o for s in out for o in options][:16]
    return out


def _guessable_shape(pin: str) -> str:
    """The dressed-up pattern *pin* is, or '' when there is none."""
    text = pin.lower()
    core = "".join(ch for ch in text if ch.isalnum())
    # 1. marks between the characters
    if core != text and _is_weak_core(core):
        return "marks"
    # 2. two patterns woven together
    evens, odds = core[0::2], core[1::2]
    if len(evens) >= 3 and len(odds) >= 3 and all(
            _is_weak_core(half, part=True) for half in (evens, odds)):
        return "woven"
    # 3. only a few different characters
    if len(set(core)) <= 3:
        return "few"
    # 3b. the same thing typed twice (14789632501478963250, hunter2hunter2)
    if _said_twice(core):
        return "twice"
    # 4. a pattern with one or two characters added, or with each character
    #    doubled
    for head in range(3):
        for tail in range(3):
            middle = core[head:len(core) - tail]
            if (head or tail) and len(middle) >= 6 and _is_weak_core(
                    middle, part=True):
                return "padded"
    collapsed = "".join(ch for k, ch in enumerate(core)
                        if k == 0 or core[k - 1] != ch)
    if collapsed != core and len(collapsed) >= 3 and _is_weak_core(
            collapsed, part=True):
        return "padded"
    # 4b. ...or put in anywhere, not only at the start or the end
    #     (1478963k250, 14789k63250)
    if _pattern_with_insert(core):
        return "padded"
    # 4c. dates, phone numbers and well-known runs of numbers
    if _date_shape(core):
        return "date"
    if _phone_shape(text, core):
        return "phone"
    if _sequence_shape(core):
        return "sequence"
    # 5. keyboard runs, columns included, with at most two characters over
    segments = _keyboard_segments(text)
    long_runs = [n for n in segments if n >= 3]
    left_over = sum(n for n in segments if n < 3)
    if long_runs and left_over <= 2 and (len(segments) == 1
                                         or sum(long_runs) >= 6):
        return "keyboard"
    # 6. a common password with digits or marks around it, or look-alikes in it
    stripped = text.strip("0123456789!@#$%^&*()_-+=.,?~")
    for body in {stripped, text}:
        if len(body) < 3:
            continue
        for spelled in _spellings(body):
            word = spelled.strip("0123456789!@#$%^&*()_-+=.,?~")
            if word in _COMMON_WORDS or word in _COMMON_PINS:
                return "common"
    if _common_inside(text):
        return "common"
    # 7. a word or a name with a number or a year on the end
    if _word_number_shape(text):
        return "word_number"
    # 8. a name or a word with a date, or a month by name
    if _word_date_shape(core):
        return "date"
    return ""


# ---------------------------------------------------------------------------
# recovery code
# ---------------------------------------------------------------------------
#
# Alphabet: digits 2-9 plus consonants. In every pair that gets confused in
# handwriting the letter is the one that goes and the digit stays, so 0/O and
# 1/I disappear entirely, and S, Z, G and B are dropped to leave 5, 2, 6 and 8
# unambiguous. Dropping all five vowels does double duty: it takes out I, O and
# U, and it makes it impossible for a generated code to spell a word - nobody
# has to hand a stranger a recovery code with an accidental obscenity in it.
RECOVERY_ALPHABET = "23456789CDFHJKLMNPQRTVWXY"   # 25 symbols
RECOVERY_GROUPS = 5
RECOVERY_GROUP_LENGTH = 5
RECOVERY_CODE_LENGTH = RECOVERY_GROUPS * RECOVERY_GROUP_LENGTH   # 25 characters

# 25 characters drawn uniformly from 25 symbols: 25 ** 25, which is about 116
# bits. The number is chosen against the thing it actually protects. The vault's
# Fernet key is AES-128, so 128 bits is the ceiling on what any amount of extra
# recovery-code entropy could buy; at ~116 bits the code sits just under that
# ceiling, which means the cipher is the weak link and the code is not. Going
# higher would add transcription errors and protect nothing. Going meaningfully
# lower would make the handwritten code the easiest way into the vault, which is
# exactly the mistake this format exists to avoid. Five groups of five is also
# about the most a person will copy onto a card without losing their place.
RECOVERY_CODE_ENTROPY_BITS = round(
    RECOVERY_CODE_LENGTH * math.log2(len(RECOVERY_ALPHABET)), 1
)

# True once the reference vault carries a second, recovery-wrapped copy of the
# data key. The wizard reads this to decide whether it is allowed to describe the
# code as a way back in. It is the gate that stops the ceremony ever shipping
# without the mechanism behind it.
RECOVERY_ESCROW_AVAILABLE = True


def generate_recovery_code() -> str:
    """Generate a one-time vault recovery code.

    Format is ``XXXXX-XXXXX-XXXXX-XXXXX-XXXXX`` in uppercase, drawn from an
    alphabet with no visually ambiguous characters and no vowels, so it survives
    being written on paper and read back by somebody else.

    Entropy is :data:`RECOVERY_CODE_ENTROPY_BITS` (about 116 bits): 25 characters
    from a 25-symbol alphabet. Every character comes from
    :func:`secrets.choice`, which draws uniformly from the sequence and so
    carries no modulo bias.

    The returned value is a secret. It is shown once and never persisted by this
    module; a caller that writes it anywhere has broken the contract this step
    makes with the owner.
    """
    groups = [
        "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_GROUP_LENGTH))
        for _ in range(RECOVERY_GROUPS)
    ]
    return "-".join(groups)


def _normalize_recovery_code(text: str) -> str:
    """Reduce a code to comparable form: uppercase, letters and digits only.

    Hyphens, spaces, line breaks and stray punctuation are dropped, because a
    person copying twenty-five characters off a card should not be failed for
    where they put the dashes.
    """
    if not isinstance(text, str):
        return ""
    return "".join(ch for ch in text.upper() if ch.isalnum())


def verify_recovery_confirmation(shown: str, typed: str) -> bool:
    """True when ``typed`` is the code we ``shown``, allowing for handwriting.

    Case, spacing and hyphenation are normalized away first; the comparison
    itself uses :func:`secrets.compare_digest` so it does not leak how much of
    the code was correct through timing. Fails closed: an empty or unusable
    value on either side is never a match.
    """
    left = _normalize_recovery_code(shown)
    right = _normalize_recovery_code(typed)
    if not left or not right:
        return False
    return secrets.compare_digest(left, right)


# ---------------------------------------------------------------------------
# wizard copy (ASCII - this reaches a Windows console)
# ---------------------------------------------------------------------------

RECOVERY_NOTICE_TITLE = "Write this code down before you go any further."

RECOVERY_NOTICE_LINES: tuple[str, ...] = (
    "This code is the only way back into your vault if you forget your PIN.",
    "We cannot recover it for you. It is not sent anywhere and no copy of it is kept.",
    "There is no reset email, because there is no us. Nobody is holding a spare.",
    "Write it on paper and keep the paper where you would keep a spare house key.",
    "You will be asked to type it back on the next screen. That is on purpose.",
)

RECOVERY_SHOWN_ONCE_WARNING = (
    "This code is shown once. Once you leave this screen it is gone from the "
    "screen and from memory, and we cannot show it to you again."
)

RECOVERY_CONFIRM_PROMPT = (
    "Type the recovery code back in. Capitals and dashes do not matter."
)

RECOVERY_CONFIRM_FAILED = (
    "That does not match the code on the screen. Check what you wrote down and "
    "try it again - this is exactly the mistake it is better to catch now."
)

# Shown while RECOVERY_ESCROW_AVAILABLE is False. Says the true thing rather
# than the reassuring thing.
RECOVERY_UNWIRED_WARNING = (
    "Heads up: on this build the recovery code cannot be redeemed yet, because "
    "the vault seals under your PIN alone. Keep the code, but treat your PIN as "
    "the only way in for now, and do not lose it."
)

PIN_INTRO_LINES: tuple[str, ...] = (
    "Your vault holds the things you would not hand to a stranger.",
    "You set the PIN. It is not sent anywhere, and it is never stored as text.",
    "Even your bot has to ask before it can read what is inside.",
)


def recovery_notice_lines() -> list[str]:
    """The full 'write this down' screen, in order, ready to print.

    Assembles the notice, the shown-once warning, and - while
    :data:`RECOVERY_ESCROW_AVAILABLE` is False - the warning that says the code
    cannot be redeemed on this build yet. Callers should use this rather than
    picking constants individually, so the honest caveat cannot be dropped by
    accident.
    """
    lines = [RECOVERY_NOTICE_TITLE, ""]
    lines.extend(RECOVERY_NOTICE_LINES)
    lines.append(RECOVERY_SHOWN_ONCE_WARNING)
    if not RECOVERY_ESCROW_AVAILABLE:
        lines.append(RECOVERY_UNWIRED_WARNING)
    return lines


# ---------------------------------------------------------------------------
# manifest block
# ---------------------------------------------------------------------------

_VAULT_METHODS = frozenset({"pin", "passphrase"})

# Descriptive, non-secret facts about how the reference vault protects things.
# Stating the primitives makes a build auditable; none of this is derived from
# the PIN or the code, and no salt, hash, or ciphertext appears here.
_KEY_DERIVATION = "pbkdf2-hmac-sha256"
_CIPHER = "fernet-aes128-cbc-hmac-sha256"


def vault_manifest_block(
    *,
    configured: bool = True,
    sealed: bool = True,
    method: str = "pin",
    recovery_issued: bool = False,
    recovery_confirmed: bool = False,
    configured_at: Optional[float] = None,
) -> dict[str, Any]:
    """Record that a vault exists and is sealed. Status flags only.

    This function takes **no PIN and no recovery code**, by design. It cannot
    write the PIN, the code, a hash of either, a salt, or anything derived from
    them, because it is never handed them in the first place. That is a stronger
    guarantee than a promise not to, and it is what the test asserts.

    ``configured_at`` defaults to now when the vault is configured, and is None
    when it is not. Never raises.
    """
    chosen_method = str(method).strip().lower()
    if chosen_method not in _VAULT_METHODS:
        chosen_method = "unspecified"

    if configured:
        when: Optional[float] = configured_at if configured_at is not None else time.time()
    else:
        when = None

    return {
        "configured": bool(configured),
        "sealed": bool(sealed and configured),
        "method": chosen_method,
        "recovery_issued": bool(recovery_issued),
        "recovery_confirmed": bool(recovery_confirmed),
        "recovery_escrow": "wrapped-key" if RECOVERY_ESCROW_AVAILABLE else "none",
        "configured_at": when,
        "key_derivation": _KEY_DERIVATION,
        "cipher": _CIPHER,
    }


# ---------------------------------------------------------------------------
# sealing (hand the PIN over, keep nothing)
# ---------------------------------------------------------------------------

def seal_vault(pin: str, *, vault: Any = None) -> dict[str, Any]:
    """Create the vault under ``pin`` and return the manifest block.

    Validates the PIN first and refuses a weak one (fail closed), then hands the
    PIN straight to :func:`puffpai.setup_ops.create_vault`, which owns the only
    crypto path in the build. The PIN is not copied, cached, logged, or included
    in the return value; it goes out of scope when this function returns.

    Raises :class:`ValueError` if the PIN does not pass :func:`validate_pin`, and
    lets :class:`puffpai.security.vault.VaultError` propagate if a vault already
    exists - refusing to overwrite an existing vault is the vault's own rule and
    this step does not soften it.
    """
    ok, reason = validate_pin(pin)
    if not ok:
        raise ValueError(reason)

    from puffpai.setup_ops import create_vault

    create_vault(pin, vault=vault)
    return vault_manifest_block(configured=True, sealed=True, method="pin")


def seal_vault_with_recovery(pin: str, *, vault: Any = None) -> tuple[dict[str, Any], str]:
    """Create the vault under ``pin`` and issue its recovery code.

    This is the wizard's path: it is what makes the "write this down" screen mean
    something. Returns ``(manifest_block, recovery_code)`` - the code is a
    **separate return value on purpose**, never a field inside the block, so that
    a caller which persists the manifest cannot persist the code by accident.

    Show the code once, have the owner type it back
    (:func:`verify_recovery_confirmation`), then drop it. Neither the PIN nor the
    code is retained here, and the vault is left sealed.

    Same failure modes as :func:`seal_vault`: :class:`ValueError` for a PIN that
    does not pass :func:`validate_pin`, and
    :class:`puffpai.security.vault.VaultError` if a vault already exists.
    """
    ok, reason = validate_pin(pin)
    if not ok:
        raise ValueError(reason)

    from puffpai.security.vault import Vault

    # Resolve the vault directory the same way seal_vault does (via setup_ops)
    # rather than letting Vault() pick its own default. Without this, a wizard
    # run pointed at a scratch PUFFPAI_DATA_DIR would seal the DEFAULT
    # install's vault instead - in the worst case, a real owner's vault during
    # a test run. setup_ops is already this module's one allowed dependency;
    # importing config directly here would widen a deliberately narrow surface.
    if vault is not None:
        handle = vault
    else:
        from puffpai.setup_ops import _vault_dir
        handle = Vault(vault_dir=_vault_dir())
    _, code = handle.create_with_recovery(pin)
    # Do not leave a dangling unlocked session; the normal flow re-unlocks.
    handle.lock()
    block = vault_manifest_block(
        configured=True, sealed=True, method="pin", recovery_issued=True,
    )
    return block, code


# ---------------------------------------------------------------------------
# display
# ---------------------------------------------------------------------------

def vault_summary_lines(block: dict[str, Any]) -> list[str]:
    """Plain-language lines describing a vault block, for the CLI/API."""
    lines: list[str] = []
    if not block.get("configured"):
        lines.append("Vault: not set up yet. Nothing is protected until you set a PIN.")
        return lines

    state = "sealed" if block.get("sealed") else "unlocked"
    lines.append(f"Vault: set up and {state}, opened by your {block.get('method', 'pin')}.")
    if block.get("recovery_confirmed"):
        lines.append("  Recovery code: generated, and you typed it back to confirm it.")
    elif block.get("recovery_issued"):
        lines.append("  Recovery code: generated, but not confirmed. Worth redoing.")
    else:
        lines.append("  Recovery code: none issued.")
    if block.get("recovery_escrow") == "none":
        lines.append("  Note: this build cannot redeem a recovery code yet. Your PIN is the way in.")
    return lines


__all__ = [
    "RECOVERY_ALPHABET",
    "RECOVERY_CODE_ENTROPY_BITS",
    "RECOVERY_CODE_LENGTH",
    "RECOVERY_CONFIRM_FAILED",
    "RECOVERY_CONFIRM_PROMPT",
    "RECOVERY_ESCROW_AVAILABLE",
    "RECOVERY_GROUPS",
    "RECOVERY_GROUP_LENGTH",
    "RECOVERY_NOTICE_LINES",
    "RECOVERY_NOTICE_TITLE",
    "RECOVERY_SHOWN_ONCE_WARNING",
    "RECOVERY_UNWIRED_WARNING",
    "PIN_INTRO_LINES",
    "generate_recovery_code",
    "recovery_notice_lines",
    "seal_vault",
    "seal_vault_with_recovery",
    "validate_pin",
    "vault_manifest_block",
    "vault_summary_lines",
    "verify_recovery_confirmation",
]
