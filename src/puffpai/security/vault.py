"""PuffPAI Vault - password-based encrypted store (public reference build).

This module is the **open reference implementation** of the PuffPAI vault. It
gives the public build a real, working encrypted-at-rest store for a user's
private secrets and small documents, using only well-documented, standard
primitives from the ``cryptography`` library. The shipped appliance uses a
more sophisticated vault; this reference is deliberately simple and honest, and
exposes the same public interface so the two are drop-in compatible.

Design
------
* **Envelope encryption.** A random **data key** encrypts the vault contents.
  That data key is then stored twice, wrapped under two independent secrets: the
  owner's PIN and a one-time recovery code. Either secret unwraps the same data
  key; neither wrapped copy reveals the other secret. This is what makes a
  recovery code possible at all - the old single-secret format had no second
  unwrap path, so a forgotten PIN meant the data was gone.
* **Encryption:** Fernet (AES-128-CBC + HMAC-SHA256, authenticated) from
  ``cryptography.fernet``, for both the contents and the two key wraps. A wrong
  secret fails authentication and is rejected cleanly, so a bad PIN can never
  silently return garbage.
* **Key derivation:** each wrapping key is derived with PBKDF2-HMAC-SHA256 from
  its own secret and its **own random salt**. The iteration count follows the
  public OWASP Password Storage Cheat Sheet recommendation, and the recovery
  wrap is never cheaper than the PIN wrap.
* **On disk, and nothing else:** the encrypted contents, the two wrapped copies
  of the data key, and their salts. **The PIN and the recovery code are never
  written anywhere, in any form - not plaintext, not hashed, not salted-and-
  hashed.** There is nothing on disk to "look up" a secret from.
* **Session model:** :meth:`Vault.unlock` unwraps the data key, holds it in
  memory for the life of the session, and :meth:`Vault.lock` drops it.
* **It locks itself when left alone (server side).** Every open vault locks
  after :data:`IDLE_LOCK_SECONDS` without use, wherever it was opened: the
  Vault page, a chat, voice, the terminal. A watchdog thread in this module
  does it, so it never depends on a page being open, and every read checks
  the time as well. Reading a sign-up key for an automatic job does not count
  as use; the owner opening, adding, reading or deleting something does.
* **One vault, in one place.** The folder the vault was made in is
  remembered (``vault_place.json`` in the state folder). If that folder or
  its drive is missing, the vault says "Your vault drive is not plugged in"
  and refuses to make a new one: never a silent second, empty vault.

Recovery, and what it costs
---------------------------
:meth:`Vault.redeem_recovery_code` is a **recovery, not a login**. A code that
has been read off a piece of paper is assumed spent, so redeeming it requires
setting a new PIN, re-keys the vault contents under a fresh data key, and issues
a fresh recovery code - shown once. Afterwards the old PIN, the old recovery
code, and the old data key all open nothing.

Unlock throttling
-----------------
Deriving a PBKDF2 key at 600,000 iterations costs the *server* real CPU, so an
unthrottled unlock endpoint is a CPU-amplification lever as much as it is a
guessing surface. The throttle here therefore rejects **before** any key
derivation runs, and never sleeps a worker thread. See :meth:`Vault._check_throttle`
for the full reasoning, including why it cannot be used to lock an owner out.

Format and migration
--------------------
Vaults written by the original single-secret format (a ``vault.salt`` file plus
``vault.enc`` encrypted directly under the PIN-derived key) still open with
their PIN. The first successful unlock transparently upgrades them to the
envelope format; see :meth:`Vault._upgrade_legacy` for the crash-safety
argument. A legacy vault cannot gain a recovery code until it has been unlocked
once, because that is the only moment the contents are available to re-key.

The vault directory (default ``vault_data/``) is gitignored and never contains
plaintext secrets.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
import sys
import secrets as _secrets
import threading
import time
import weakref
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# OWASP Password Storage Cheat Sheet recommends at least 600,000 iterations for
# PBKDF2-HMAC-SHA256 (see: OWASP Password Storage Cheat Sheet, "PBKDF2"). Stated
# here explicitly so the public build's use of a public best-practice parameter
# is auditable.
_PBKDF2_ITERATIONS = 600_000

# The recovery code carries ~116 bits of entropy, so its wrap is not the weak
# link either way - but it is never made cheaper than the PIN's wrap, so that
# the recovery path can never become the easier one to attack.
_RECOVERY_PBKDF2_ITERATIONS = _PBKDF2_ITERATIONS

_KEY_LENGTH = 32          # bytes - Fernet expects a 32-byte urlsafe-b64 key
_SALT_LENGTH = 16         # bytes - random per-wrap salt

# Envelope format.
META_SCHEMA = "puffpai_vault/v2"
_META_FILENAME = "vault.meta.json"

# Original single-secret format, still readable.
_SALT_FILENAME = "vault.salt"
_DATA_FILENAME = "vault.enc"

# Unlock throttling. See Vault._check_throttle for why these shapes were chosen.
_FREE_ATTEMPTS = 5              # typo budget before any cooldown applies
_BACKOFF_BASE_SECONDS = 5.0     # first cooldown, doubling from there
_BACKOFF_MAX_SECONDS = 300.0    # hard ceiling: an owner never waits over 5 min

# Minimum strength the vault itself will accept for a new unlocking secret.
# This is a *floor*, not the wizard's policy: puffpai.zone_vault.validate_pin is
# stricter and is what the setup flow applies. The floor deliberately allows
# spaces so that passphrases ("correct horse battery staple") remain possible -
# banning them would push owners toward short secrets, which is the opposite of
# what this is for.
_MIN_SECRET_LENGTH = 8
_COMMON_SECRETS = frozenset({
    "password", "passw0rd", "password1", "12345678", "123456789", "1234567890",
    "87654321", "qwertyui", "iloveyou", "trustno1", "letmein1", "welcome1",
    "abcd1234", "11111111", "00000000", "aaaaaaaa", "changeme", "adminadmin",
    "1qaz2wsx", "1q2w3e4r", "1q2w3e4r5t", "zaq12wsx", "qazwsxedc",
    "qwertyuiop", "asdfghjkl", "zxcvbnm1",
})

# How long an open vault may sit unused before it locks itself. Server side:
# the watchdog below locks it whether or not any page is open.
IDLE_LOCK_SECONDS = 5 * 60
_IDLE_LOCK_MIN_SECONDS = 0.05     # a test's short timer; never 0
_IDLE_LOCK_MAX_SECONDS = 60 * 60  # no caller may leave it open for hours

# Where the vault should be (see :func:`remember_place`).
_PLACE_FILENAME = "vault_place.json"
_PLACE_SCHEMA = "puffpai_vault_place/v1"
VAULT_DRIVE_MISSING = "Your vault drive is not plugged in."
#: What the place record's fingerprint is made from (see
#: :meth:`Vault.place_fingerprint`).
_PLACE_FP_CONTEXT = b"puffpai vault place fingerprint v1"


def idle_lock_minutes(seconds: float = IDLE_LOCK_SECONDS) -> int:
    """The idle time in whole minutes (at least 1), for plain words."""
    return max(1, int(round(float(seconds) / 60.0)))


def idle_lock_line(seconds: float = IDLE_LOCK_SECONDS) -> str:
    """The sentence a page shows about the vault locking itself."""
    minutes = idle_lock_minutes(seconds)
    unit = "minute" if minutes == 1 else "minutes"
    return (f"Your vault locks itself after {minutes} {unit} without use, "
            f"wherever it was opened: this page, a chat or voice. It locks "
            f"even when this page is closed.")


# -- patterns a guessing program tries first --------------------------------
#
# Runs are read on rings (digits and letters wrap around: 7890123, xyzab) and
# along the keyboard's rows. "1234567890" passed the old check, because 9 -> 0
# is not a step of one; on the digit ring it is.
_RINGS = ("0123456789", "abcdefghijklmnopqrstuvwxyz")
_ROWS = ("1234567890-=", "qwertyuiop[]\\", "asdfghjkl;'", "zxcvbnm,./",
         "qwertyuiopasdfghjklzxcvbnm")


def _steps(a: str, b: str) -> set:
    """``(sequence, step)`` pairs under which *b* follows *a* by one."""
    found = set()
    for ring in _RINGS:
        i, j = ring.find(a), ring.find(b)
        if i >= 0 and j >= 0:
            if (i + 1) % len(ring) == j:
                found.add((ring, 1))
            if (i - 1) % len(ring) == j:
                found.add((ring, -1))
    for row in _ROWS:
        i, j = row.find(a), row.find(b)
        if i >= 0 and j >= 0 and abs(i - j) == 1:
            found.add((row, j - i))
    return found


def _run_segments(text: str) -> list[int]:
    """Lengths of the runs *text* splits into, greedily from the left."""
    out: list[int] = []
    i = 0
    while i < len(text):
        best = 1
        if i + 1 < len(text):
            for kind in _steps(text[i], text[i + 1]):
                j = i + 1
                while j + 1 < len(text) and kind in _steps(text[j], text[j + 1]):
                    j += 1
                best = max(best, j - i + 1)
        out.append(best)
        i += best
    return out


# Phone keypad and number-pad columns, top to bottom and bottom to top
# (1470258369 and 2580147369 are three column walks).
_PADS = ("147", "2580", "369", "741", "8520", "963")

# Keypad shapes as a class: the two diagonals (the same digits on
# both pads) and two loops, the diamond through the middle of each side (2, 6,
# 8, 4) and the four corners (1, 3, 9, 7), either way round from any key.
# With paths of neighbouring keys (:data:`_KING_RUN`) they count only when the
# WHOLE secret is cut into such pieces (:func:`_keypad_shape`): the X and the
# diamond drawn in another order (9517538426) are a shape; a random PIN with
# one of them inside it is not.
_KEYPAD_LINES = ("159", "357")
_LOOPS = ("2684", "1397")

# Shortest run that counts, by kind. A step of one (on a ring, along a row or
# down a keypad column) counts from three characters. Any other fixed step
# (24680, 97531, acegi, and 7777 as a step of nothing) counts from four: three
# characters counting by some step turn up by chance in one random place in
# ten, which would refuse honest random PINs (about 5% of random
# ten-digit PINs, mostly as "a pattern with a character or two added").
_UNIT_RUN = 3
_STEPPED_RUN = 4


def _cover_steps(a: str, b: str) -> set:
    """Every way *b* can follow *a* inside a run.

    :func:`_steps` (one up or down a ring, one along a row) and one along a
    keypad column, plus ``("step", ring, k)`` for any other fixed step on a
    ring, step 0 included. Only the ``"step"`` kinds need :data:`_STEPPED_RUN`
    characters.
    """
    found = set(_steps(a, b))
    for r, ring in enumerate(_RINGS):
        i, j = ring.find(a), ring.find(b)
        if i >= 0 and j >= 0:
            k = (j - i) % len(ring)
            if k not in (1, len(ring) - 1):
                found.add(("step", r, k))
    for pad in _PADS:
        i, j = pad.find(a), pad.find(b)
        if i >= 0 and j >= 0 and abs(i - j) == 1:
            found.add((pad, j - i))
    return found


def _keypad_steps(a: str, b: str) -> set:
    """The keypad kinds of :func:`_keypad_shape`: a diagonal, a loop (four
    long or more) or a path of neighbouring keys (:data:`_KING_RUN` long)."""
    found = set()
    for line in _KEYPAD_LINES:
        i, j = line.find(a), line.find(b)
        if i >= 0 and j >= 0 and abs(i - j) == 1:
            found.add((line, j - i))
    for loop in _LOOPS:
        i, j = loop.find(a), loop.find(b)
        if i >= 0 and j >= 0 and (j - i) % 4 in (1, 3):
            found.add(("loop", loop, (j - i) % 4))
    return found | _king_steps(a, b)


#: A path on a keypad: each move to a key right next to the last one, across,
#: up, down or corner to corner, turning any way. Seven keys or
#: more count as a run when the whole secret is cut into runs with nothing
#: left over; a path of the whole secret is :func:`_keypad_path`. Seven, not
#: six: the same shapes are caught and fewer random PINs are (measured
#: on 20,000 random PINs: ten digits 1.31% refused in all, twelve digits
#: 0.18%).
_KING_RUN = 7


def _king_steps(a: str, b: str) -> set:
    """``("king", pad)`` for every keypad on which *b* is right next to *a*."""
    found = set()
    for n, pad in enumerate(_KEYPADS):
        if a in pad and b in pad:
            (r1, c1), (r2, c2) = pad[a], pad[b]
            if (r1, c1) != (r2, c2) and max(abs(r1 - r2), abs(c1 - c2)) == 1:
                found.add(("king", n))
    return found


def _cover_and_king(a: str, b: str, king: bool) -> set:
    """:func:`_cover_steps`, plus the keypad kinds (:func:`_keypad_steps`)
    when *king*: only ever for a whole secret, never a piece of one."""
    found = _cover_steps(a, b)
    return found | _keypad_steps(a, b) if king else found


def _keypad_shape(text: str) -> bool:
    """Eight characters or more drawing keypad shapes from end to end: a
    path of neighbouring keys (:func:`_keypad_path`), or cut with nothing
    left over into runs, diagonals, loops and paths (9517538426: the X and
    the diamond, in another order)."""
    if len(text) < 8:
        return False
    return _keypad_path(text) or _run_cover(text, king=True)[0] == 0


def _keypad_path(text: str) -> bool:
    """Eight digits or more, every move to a key right next to the last one
    on one keypad (any turns): a path through neighbouring keys. About 0.2%
    of random ten-digit PINs are one (measured)."""
    if len(text) < 8 or not text.isdigit():
        return False
    kinds = None
    for a, b in zip(text, text[1:]):
        here = {k for k in _king_steps(a, b)}
        kinds = here if kinds is None else kinds & here
        if not kinds:
            return False
    return True


def _run_cover(text: str, king: bool = False) -> tuple[int, int]:
    """How few characters are left over when *text* is cut into runs.

    A run is characters each following the last by the same kind of step
    (:func:`_cover_steps`), at least :data:`_UNIT_RUN` long for a step of one
    and :data:`_STEPPED_RUN` long for any other step. Returns
    ``(left, left_with_long)``: the fewest characters not in any run, and the
    fewest when at least one run is one character longer than its shortest (a
    large number when there is no such cut). Exact, not greedy, so a run
    cannot hide by lending its first character to the run before it.
    """
    n = len(text)
    never = n + 1000
    left = [never] * (n + 1)
    left_long = [never] * (n + 1)
    left[0] = 0
    for i in range(n):
        if left[i] >= never:
            continue
        left[i + 1] = min(left[i + 1], left[i] + 1)
        left_long[i + 1] = min(left_long[i + 1], left_long[i] + 1)
        if i + 1 >= n:
            continue
        for kind in _cover_and_king(text[i], text[i + 1], king):
            shortest = (_KING_RUN if kind[0] == "king" else _STEPPED_RUN
                        if kind[0] in ("step", "loop") else _UNIT_RUN)
            j = i + 1
            while j + 1 < n and kind in _cover_and_king(text[j], text[j + 1], king):
                j += 1
                end = j + 1  # text[i:end] is a run of length end - i
                if end - i < shortest:
                    continue
                left[end] = min(left[end], left[i])
                left_long[end] = min(left_long[end], left_long[i])
                # A path of neighbouring keys is not "a run longer than its
                # shortest" (too many random PINs hold one with two over).
                if end - i > shortest and kind[0] != "king":
                    left_long[end] = min(left_long[end], left[i])
    return left[n], left_long[n]


# Where each digit sits on a phone keypad (123 on top, 0 under 8) and on a
# computer's number pad (789 on top, the wide 0 under 2 and under 1).
_KEYPADS = (
    {"1": (0, 0), "2": (0, 1), "3": (0, 2), "4": (1, 0), "5": (1, 1),
     "6": (1, 2), "7": (2, 0), "8": (2, 1), "9": (2, 2), "0": (3, 1)},
    {"7": (0, 0), "8": (0, 1), "9": (0, 2), "4": (1, 0), "5": (1, 1),
     "6": (1, 2), "1": (2, 0), "2": (2, 1), "3": (2, 2), "0": (3, 1)},
    {"7": (0, 0), "8": (0, 1), "9": (0, 2), "4": (1, 0), "5": (1, 1),
     "6": (1, 2), "1": (2, 0), "2": (2, 1), "3": (2, 2), "0": (3, 0)},
)


def _keypad_strokes(text: str, pad: dict) -> int:
    """How many straight strokes draw *text* on *pad*, or 0 when none do.

    Each move goes to a key in the same row, column or diagonal, at most two
    keys away (5 to 0 on a phone passes over 8). A stroke is moves in one
    direction; the corner key ends one stroke and starts the next.
    """
    directions = []
    for a, b in zip(text, text[1:]):
        (r1, c1), (r2, c2) = pad[a], pad[b]
        dr, dc = r2 - r1, c2 - c1
        if (dr, dc) == (0, 0) or max(abs(dr), abs(dc)) > 2:
            return 0
        if dr and dc and abs(dr) != abs(dc):
            return 0
        directions.append(((dr > 0) - (dr < 0), (dc > 0) - (dc < 0)))
    return 1 + sum(1 for x, y in zip(directions, directions[1:]) if x != y)


def _keypad_walk(text: str, part: bool = False) -> bool:
    """True when the digits draw a shape on a phone keypad or number pad.

    Snakes, spirals, the square around the edge, an X (1478963250,
    1236987410, 1593574862): every move to a key in line with the last, in
    at most ``len - 3`` strokes (so at least two moves carry straight on)
    and never more than seven. Six digits or more. About 0.2% of random
    ten-digit PINs draw such a shape and almost no longer ones (tested),
    against about 4% of ten-digit PINs if any number of turns were allowed.

    *part* (a PIN's middle, once a character or two is taken off): eight
    digits or more and one stroke fewer, so a shape with a character added
    (1478963250x) is still seen while random PINs almost never are (about
    0.03% more of them).
    """
    shortest, slack, most = (8, 4, 6) if part else (6, 3, 7)
    if len(text) < shortest or not text.isdigit():
        return False
    limit = min(len(text) - slack, most)
    for pad in _KEYPADS:
        strokes = _keypad_strokes(text, pad)
        if strokes and strokes <= limit:
            return True
    return False


#: The patterns the vault's own floor refuses (every path to a new secret
#: comes through it): one run, runs back to back, digits counting by a fixed
#: step, or a shape drawn on the keypad.
FLOOR_PATTERNS = frozenset({"run", "runs", "digit_step", "keypad"})


def weak_pattern(secret: str, strict: bool = False) -> str:
    """The pattern that makes *secret* guessable, or '' when there is none.

    ``run``: one run up or down a ring or a keyboard row (1234567890,
    0987654321, 7890123456, xyzabcde, qwertyuiop). ``runs``: the whole
    secret cut into runs, three or more long counting by one on a ring,
    along a keyboard row or down a keypad column, or four or more long
    counting by any other fixed step (abcd1234, 1234554321, 123abc456,
    123456123456123456, qwertqwertqwert, 1234512345123, 2468013579,
    8642097531, 1470258369, 2580147369, 7418529630, acegi13579), or with at
    most two characters left over when one run is longer than its shortest
    (abcdefgh1, 6210401234). ``repeat``: a block of one to three characters
    said again and again, a part-block at the end included (abcabcabc,
    121212, abcabcab). ``digit_step``: digits counting by any fixed step,
    wrapping (1357913579, 9630741852). ``keypad``: digits drawing a shape on
    a phone keypad or number pad (:func:`_keypad_walk`). Case does not
    matter. A passphrase with spaces is never one of these.

    *strict* is for a part of a PIN (the middle once a character or two is
    taken off, or every other character): there the whole part must be the
    pattern, with no characters left over and a keypad shape held to fewer
    turns, because a few random characters around something that is only
    nearly a pattern make no pattern at all.

    The vault's own floor refuses :data:`FLOOR_PATTERNS`; the wizard's PIN
    rule refuses every kind.
    """
    text = str(secret or "").lower()
    if len(text) < 3:
        return ""
    length = len(text)
    for block in range(1, min(3, length // 2) + 1):
        if all(text[k] == text[k % block] for k in range(length)):
            return "repeat"
    if text.isdigit() and length >= 6:
        step = (int(text[1]) - int(text[0])) % 10
        if all((int(text[k + 1]) - int(text[k])) % 10 == step
               for k in range(length - 1)):
            return "digit_step"
    segments = _run_segments(text)
    if len(segments) == 1:
        return "run"
    left, left_long = _run_cover(text)
    if left == 0 or (not strict and left_long <= 2):
        return "runs"
    if _keypad_walk(text, part=strict):
        return "keypad"
    if not strict:
        if _keypad_shape(text) or _keypad_with_marks(text):
            return "keypad"
        block = _said_twice_block(text)
        if block:
            kind = weak_pattern(block)
            if kind in FLOOR_PATTERNS:
                return kind
    return ""


def _keypad_with_marks(text: str) -> bool:
    """A keypad shape with one or two other characters put in anywhere.

    Without this, 1478963k250, 14789k63250,
    123698k7410 and 15935k74862 make real vaults. The digits are read on
    their own when there are at least six of them and at most two other
    characters, by the same shape rule as a PIN of digits only (so a random
    PIN is caught about as rarely as a random PIN of digits).
    """
    digits = "".join(ch for ch in text if ch.isdigit())
    others = len(text) - len(digits)
    if not 0 < others <= 2 or len(digits) < 6:
        return False
    # Any keypad shape, drawn in any order (9517538426q)
    return _keypad_walk(digits) or _keypad_shape(digits)


def _said_twice_block(text: str) -> str:
    """The block of four or more that *text* says again and again, or ''."""
    n = len(text)
    for block in range(4, n // 2 + 1):
        if all(text[k] == text[k % block] for k in range(n)):
            return text[:block]
    return ""


#: Secret names with this prefix belong to the vault itself (the write-only
#: inbox key, see :mod:`puffpai.security.vault_inbox`). No public method reads,
#: writes, lists or deletes them, so no API route can hand one out.
_RESERVED_PREFIX = "__puffpai."


def _is_reserved(name: object) -> bool:
    return str(name).startswith(_RESERVED_PREFIX)


#: Sign-up key records (:mod:`puffpai.security.keys`).
#: Reserved like the inbox key, so every public method and every generic vault
#: route refuses or hides them; on top of that ONLY the key broker may read,
#: write or delete one (:func:`_require_key_broker`).
_KEY_PREFIX = _RESERVED_PREFIX + "key."
_KEY_BROKER_MODULE = "puffpai.security.keys"


def _is_key_name(name: object) -> bool:
    return str(name).startswith(_KEY_PREFIX)


def _require_key_broker(depth: int = 2) -> None:
    """Refuse a key-record call from anywhere but the key broker.

    A guard against accident and against a plugin or model-driven call site,
    not a sandbox: code already running in this process can patch anything.
    """
    caller = sys._getframe(depth).f_globals.get("__name__", "")
    if caller != _KEY_BROKER_MODULE:
        raise VaultError("sign-up key records belong to the key broker")


# ── The unattended tier: ported from HomeBotBuild ────────────────────────────
#
# Same format as HomeBotBuild ``security/vault.py`` SERVICE_SECRET_FILES, ported
# rather than re-invented, and RESTRICTED
# BY POLICY: in BuildBot the only records that may live here are sign-up keys
# for free services that need no payment card, and only when the owner ticked
# "keep working when the vault is locked" for that key
# (:func:`puffpai.security.keys.background_file_allowed`). A key that was
# given with a payment card (Brave) can never be written here.
#
# The format, exactly as in HomeBotBuild:
#   * a separate random 32-byte Service Data Key (SDK) encrypts ONLY the
#     allowlisted records (AES-256-GCM, nonce || ciphertext, one file each);
#   * the SDK is wrapped twice, in the metadata file next to the PIN wraps:
#       "service"        -- under the machine secret (scrypt KEK, AES-GCM,
#                           AAD b"puffpai-vault-sdk-v2"): the locked path;
#       "service_by_dek" -- under the vault's data key (AES-GCM, AAD
#                           b"puffpai-vault-sdk-by-dek-v2"): any open vault
#                           can read and rewrite the records;
#   * the data key is NEVER wrapped under anything a machine can supply.
#
# One BuildBot difference, stated: BuildBot's data key is a Fernet key, not a
# raw AES key, so the "service_by_dek" wrap uses an HKDF-SHA256 subkey of it
# ("kdf": "hkdf-sha256") instead of the key itself ("kdf": "raw" in HBB). Using
# a Fernet key's bytes directly as an AES-GCM key would be the same key under
# two algorithms.
#
# Where the machine secret lives, in resolution order (HBB 1-3, plus 4):
#   1. $CREDENTIALS_DIRECTORY/puffpai-vault-service-key (systemd LoadCredential)
#   2. $PUFFPAI_VAULT_SERVICE_KEY_FILE (explicit override: tests, dev boxes)
#   3. /etc/puffpai/vault_service.key (POSIX; readable only by its owner)
#   4. BuildBot: the operating system's own password store through ``keyring``
#      -- on Windows the Credential Manager (DPAPI, this Windows user only) --
#      under a name bound to this vault folder, so two installs never share
#      one. Refused when keyring's backend is not a real OS store.
# A key FILE readable by group or others is refused on POSIX (the HBB rule).

#: HomeBotBuild's allowlist had the Home Assistant token. BuildBot has no boot
#: secret of its own; its records come only from the key policy.
SERVICE_SECRET_FILES: frozenset = frozenset()

_SERVICE_KEY_CRED_NAME = "puffpai-vault-service-key"
_SERVICE_KEY_ENV = "PUFFPAI_VAULT_SERVICE_KEY_FILE"
_SERVICE_KEY_DEFAULT_PATH = "/etc/puffpai/vault_service.key"
_SERVICE_WRAP_KEY = "service"
_SERVICE_DEK_WRAP_KEY = "service_by_dek"
_SDK_LENGTH = 32
_NONCE_LENGTH = 12
_SDK_WRAP_AAD = b"puffpai-vault-sdk-v2"          # machine-secret wrap of the SDK
_SDK_DEK_WRAP_AAD = b"puffpai-vault-sdk-by-dek-v2"  # DEK wrap of the SDK
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEYRING_SERVICE = "puffpai-buildbot"
#: keyring backends that are not a real OS store: never hold the machine key.
_WEAK_KEYRING_MODULES = ("keyring.backends.fail", "keyring.backends.null",
                         "keyrings.alt")
_service_key_warned: set = set()


def _keyring_user(vault_dir: str) -> str:
    digest = hashlib.sha256(os.path.abspath(vault_dir).encode("utf-8"))
    return "vault-service-key:" + digest.hexdigest()[:12]


def _usable_keyring():
    """The ``keyring`` module when its backend is a real OS store, else None."""
    try:
        import keyring  # noqa: PLC0415 - optional, and slow to import
        backend = keyring.get_keyring()
    except Exception:
        return None
    module = type(backend).__module__
    if any(module.startswith(weak) for weak in _WEAK_KEYRING_MODULES):
        return None
    if getattr(backend, "priority", 1) <= 0:
        return None
    return keyring


def service_key_candidates() -> list:
    """The machine-secret FILE paths to try, in order. Never reads them."""
    out = []
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY", "").strip()
    if cred_dir:
        out.append(("systemd-credential", Path(cred_dir) / _SERVICE_KEY_CRED_NAME))
    override = os.environ.get(_SERVICE_KEY_ENV, "").strip()
    if override:
        out.append((_SERVICE_KEY_ENV, Path(override)))
    if os.name != "nt":
        out.append(("etc", Path(_SERVICE_KEY_DEFAULT_PATH)))
    return out


def read_service_key(vault_dir: Optional[str] = None):
    """Return ``(secret, source_label)`` for the machine-bound service key.

    ``(None, reason)`` when no usable key is present. Never raises, never logs
    or returns the key material in a message.
    """
    reason = "no machine key found"
    for label, path in service_key_candidates():
        try:
            st = os.stat(path)
        except OSError:
            continue
        mode = stat.S_IMODE(st.st_mode)
        # POSIX only: on Windows the mode bits carry no ACL information.
        if os.name != "nt" and mode & 0o077:
            reason = (f"machine key at {label} is readable beyond its owner "
                      f"(mode {mode:04o}); refused")
            if reason not in _service_key_warned:
                _service_key_warned.add(reason)
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                secret = fh.read().strip()
        except OSError:
            reason = f"machine key at {label} is unreadable"
            continue
        if len(secret) < 32:
            reason = f"machine key at {label} is too short to be a machine key"
            continue
        return secret, label
    if vault_dir:
        store = _usable_keyring()
        if store is not None:
            try:
                secret = store.get_password(_KEYRING_SERVICE,
                                            _keyring_user(vault_dir))
            except Exception:
                secret = None
            if isinstance(secret, str) and len(secret) >= 32:
                return secret, "os-password-store"
    return None, reason


def create_machine_key(vault_dir: str):
    """Make the machine key in the OS password store, if there is none.

    Returns ``(True, label)`` when a usable machine key exists afterwards,
    ``(False, reason)`` otherwise. Only the OS store is ever written here: the
    file sources belong to whoever runs the computer.
    """
    secret, source = read_service_key(vault_dir)
    if secret is not None:
        return True, source
    store = _usable_keyring()
    if store is None:
        return False, "this computer has no password store BuildBot can use"
    try:
        store.set_password(_KEYRING_SERVICE, _keyring_user(vault_dir),
                           _secrets.token_hex(32))
    except Exception as exc:
        return False, f"the password store refused ({type(exc).__name__})"
    secret, source = read_service_key(vault_dir)
    return (secret is not None), source


def forget_machine_key(vault_dir: str) -> None:
    """Remove this vault's machine key from the OS password store. Never raises."""
    store = _usable_keyring()
    if store is None:
        return
    try:
        store.delete_password(_KEYRING_SERVICE, _keyring_user(vault_dir))
    except Exception:
        pass


def _aesgcm():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM


def _scrypt_kek(secret: str, salt: bytes, wrap: dict) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    kdf = Scrypt(salt=salt, length=32, n=int(wrap.get("n", _SCRYPT_N)),
                 r=int(wrap.get("r", _SCRYPT_R)), p=int(wrap.get("p", _SCRYPT_P)))
    return kdf.derive(secret.encode("utf-8"))


def _dek_subkey(data_key: bytes) -> bytes:
    """A 32-byte AES key derived from the Fernet data key (see note above)."""
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    raw = base64.urlsafe_b64decode(data_key)
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=_SDK_DEK_WRAP_AAD).derive(raw)


class VaultError(Exception):
    """Base class for vault errors."""


class AuthenticationError(VaultError):
    """Raised when a password is wrong or the vault cannot be decrypted."""


class VaultLocked(VaultError):
    """Raised when an operation needs an unlocked vault but none is open."""


class VaultThrottled(VaultError):
    """Raised when too many unlock attempts have failed too recently.

    Carries :attr:`retry_after`, the whole seconds remaining. Distinct from
    :class:`AuthenticationError` on purpose: a caller must be able to tell "your
    PIN is wrong" from "you are going too fast", because reporting the second as
    the first is how an owner ends up believing they have lost their vault.
    """

    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(0, int(round(retry_after)))
        super().__init__(
            f"too many failed attempts; try again in {self.retry_after} seconds"
        )


class WeakSecretError(VaultError):
    """Raised when a proposed PIN/passphrase is below the vault's floor."""


class VaultMissing(VaultError):
    """The vault should be in a folder (or on a drive) that is not there.

    ``str(exc)`` is written to be shown to a person as it is. Nothing is
    created while this stands: a second, empty vault is never made in the
    missing one's place.
    """


@dataclass
class VaultSession:
    """A live, unlocked vault session.

    Returned by :meth:`Vault.unlock`. Holds no key material itself - the key
    lives inside the :class:`Vault` - so the object is safe to pass around and
    to log by ``session_id``.
    """

    session_id: str
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _derive_key(password: str, salt: bytes, iterations: int = _PBKDF2_ITERATIONS) -> bytes:
    """Derive a Fernet key from ``password`` and ``salt`` via PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEY_LENGTH,
        salt=salt,
        iterations=iterations,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def normalize_recovery_code(code: str) -> str:
    """Reduce a recovery code to the exact form the key derivation consumes.

    Uppercase, letters and digits only. Hyphens, spaces and line breaks are
    dropped so that a person copying the code off a card is not punished for
    where they put the dashes. This must stay identical to the normalization in
    :mod:`puffpai.zone_vault`; a test asserts the two agree, because a drift
    between them would lock owners out of their own vaults.
    """
    if not isinstance(code, str):
        return ""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def validate_vault_secret(secret: str) -> tuple[bool, str]:
    """The vault's own floor for an unlocking secret. Returns ``(ok, reason)``.

    Deliberately weaker than :func:`puffpai.zone_vault.validate_pin`, which is
    the policy the wizard applies. This is the backstop that makes a four-digit
    vault impossible no matter which code path creates it, while still allowing
    a spaced passphrase, which is a genuinely strong choice.
    """
    if not isinstance(secret, str) or not secret:
        return False, "a vault password is required"
    if len(secret) < _MIN_SECRET_LENGTH:
        return False, (
            f"a vault password must be at least {_MIN_SECRET_LENGTH} characters; "
            f"a short numeric PIN can be worked through offline"
        )
    if len(set(secret)) == 1:
        return False, "a vault password cannot be one character repeated"
    if secret.lower() in _COMMON_SECRETS:
        return False, "that password is on every published list of common passwords"
    if len(secret) >= 3:
        step = ord(secret[1]) - ord(secret[0])
        if step in (1, -1) and all(
            ord(secret[i + 1]) - ord(secret[i]) == step for i in range(len(secret) - 1)
        ):
            return False, "a vault password cannot be a straight run of characters"
    if weak_pattern(secret) in FLOOR_PATTERNS:
        return False, ("a vault password cannot count up or down, by ones or "
                       "by bigger steps, run along the keyboard or keypad, "
                       "even when it wraps around, or draw a shape on the "
                       "keypad")
    return True, ""


def _check_secret_or_raise(secret: str) -> None:
    """Apply the wizard's PIN policy when available, else the vault's floor.

    The wizard's :func:`puffpai.zone_vault.validate_pin` is the stricter of the
    two, but it is written for a *PIN* - something typed back on a kiosk - and so
    forbids spaces. Applying it to every vault password would reject
    "correct horse battery staple", which is a better secret than anything it
    would accept. So the strict policy is enforced at the wizard boundary
    (:func:`puffpai.zone_vault.seal_vault`) and the floor is enforced here, where
    every path has to come through.
    """
    ok, reason = validate_vault_secret(secret)
    if not ok:
        raise WeakSecretError(reason)


class Vault:
    """A password-protected, encrypted-at-rest store of secrets and documents.

    Typical use::

        vault = Vault(vault_dir="vault_data")
        session, code = vault.create_with_recovery("correct horse battery staple")
        # show `code` to the owner exactly once, then forget it
        vault.store("HA_TOKEN", "eyJ...")
        vault.retrieve("HA_TOKEN")                      # -> "eyJ..."
        vault.lock()

    All read/write operations require an unlocked session and raise
    :class:`VaultLocked` otherwise. A wrong password raises
    :class:`AuthenticationError`; too many wrong passwords too quickly raises
    :class:`VaultThrottled`.
    """

    def __init__(self, vault_dir: Optional[str] = None,
                 audit_log: object | None = None, *,
                 idle_lock_seconds: Optional[float] = None) -> None:
        """Create a vault handle.

        Args:
            vault_dir: directory that holds the metadata and encrypted data.
                Defaults to the ``vault_data`` directory under the configured
                data dir.
            audit_log: optional object with a ``log(...)`` method; unlock/lock
                and access events are recorded through it when present.
            idle_lock_seconds: how long the open vault may sit unused before
                it locks itself (default :data:`IDLE_LOCK_SECONDS`, never more
                than an hour, never zero).
        """
        default_dir = _default_vault_dir()
        self._vault_dir = os.path.abspath(vault_dir) if vault_dir else default_dir
        # Only the install's own vault (the configured folder) is remembered
        # and guarded against a missing drive; a vault a caller opens in some
        # other folder on purpose is its own business.
        self._tracked = (os.path.normcase(os.path.abspath(self._vault_dir))
                         == os.path.normcase(os.path.abspath(default_dir)))
        self._audit = audit_log
        wanted = IDLE_LOCK_SECONDS if idle_lock_seconds is None else float(idle_lock_seconds)
        self._idle_lock_seconds = min(_IDLE_LOCK_MAX_SECONDS,
                                      max(_IDLE_LOCK_MIN_SECONDS, wanted))
        #: The clock idle time is measured on. Tests replace it.
        self._clock: Callable[[], float] = time.monotonic
        self._last_used = 0.0
        self._state_lock = threading.RLock()
        self._lock_listeners: list[Callable[[str], None]] = []
        self._data_key: Optional[bytes] = None
        self._session: Optional[VaultSession] = None
        # In-memory plaintext store, populated on unlock.
        self._data: dict[str, dict[str, str]] = {"secrets": {}, "documents": {}}
        # Which on-disk file the current session's contents live in.
        self._active_data_path: Optional[str] = None
        # Failed-attempt state, per secret kind. In memory only, on purpose.
        self._attempts: dict[str, tuple[int, float]] = {}
        self._attempt_lock = threading.Lock()
        # Chat PIN screenings (screen_pin): their own bucket, in memory.
        self._screens: list[float] = []
        # Sign-up key records as last seen on disk, and the ones this handle
        # changed since: the three-way merge in _merge_key_records_from_disk.
        self._key_seen: dict[str, str] = {}
        self._key_dirty: set[str] = set()
        # An install whose vault was made before its place was remembered
        # has no record yet: remember it the first time it is seen here, not
        # only at its next unlock, so a drive missing later is still known.
        self._adopt_place()

    # -- paths ------------------------------------------------------------
    @property
    def _meta_path(self) -> str:
        return os.path.join(self._vault_dir, _META_FILENAME)

    @property
    def _salt_path(self) -> str:
        """Legacy single-secret salt file. Read for migration; never written."""
        return os.path.join(self._vault_dir, _SALT_FILENAME)

    @property
    def _data_path(self) -> str:
        """Legacy single-secret data file. Read for migration; never written."""
        return os.path.join(self._vault_dir, _DATA_FILENAME)

    def _new_data_path(self) -> str:
        """A fresh, randomly named data file.

        Every re-key writes to a new name so that the metadata swap is the single
        commit point for a format change; see :meth:`_upgrade_legacy`.
        """
        return os.path.join(self._vault_dir, f"vault.{_secrets.token_hex(8)}.enc")

    # -- state ------------------------------------------------------------
    @property
    def is_legacy(self) -> bool:
        """True when only the original single-secret files are present."""
        return (not os.path.isfile(self._meta_path)
                and os.path.isfile(self._salt_path)
                and os.path.isfile(self._data_path))

    @property
    def is_initialized(self) -> bool:
        """True once a vault exists here, in either format."""
        return os.path.isfile(self._meta_path) or self.is_legacy

    @property
    def is_unlocked(self) -> bool:
        """True while a session is open (a data key is held in memory).

        Also False, and the key dropped, when another handle has re-keyed the
        vault since this one opened it (``puffpai vault recover`` in a
        terminal while the server holds the vault open). The key in this
        handle's memory is then the OLD key: it would keep serving the old
        contents to a session opened under the old PIN, and its next write
        would go to a file the vault no longer reads. Fail closed: lock, and
        let the owner open it again under the new PIN.
        """
        with self._state_lock:
            if self._data_key is None or self._session is None:
                return False
            if self._idle_expired():
                self._lock(reason="idle")
                return False
            if self._rekeyed_elsewhere():
                self._lock(reason="rekeyed")
                return False
            return True

    # -- locking itself when left alone -----------------------------------
    @property
    def idle_lock_seconds(self) -> float:
        """How long this vault may sit unused, open, before it locks itself."""
        return self._idle_lock_seconds

    def _touch(self) -> None:
        """The owner used the open vault: start its idle time again."""
        self._last_used = self._clock()

    def _idle_expired(self) -> bool:
        return (self._session is not None
                and self._clock() - self._last_used >= self._idle_lock_seconds)

    def idle_seconds_left(self) -> Optional[float]:
        """Seconds until the open vault locks itself; None when it is locked.

        Locks it now if the time is already up. Reading this is not use.
        """
        with self._state_lock:
            if self._data_key is None or self._session is None:
                return None
            left = self._idle_lock_seconds - (self._clock() - self._last_used)
            if left <= 0:
                self._lock(reason="idle")
                return None
            return left

    def add_lock_listener(self, callback: Callable[[str], None]) -> None:
        """Call ``callback(reason)`` whenever this vault locks (``"idle"``,
        ``"lock"`` or ``"rekeyed"``). A listener's error never stops a lock."""
        if callable(callback):
            with self._state_lock:
                self._lock_listeners.append(callback)

    # -- where the vault should be ---------------------------------------
    def missing_reason(self) -> str:
        """Plain words when this vault's folder or drive is missing, else ''.

        Only for the install's own vault, and only once a vault was made (or
        opened) here before: see :func:`remember_place`. A fresh install has
        nothing remembered and nothing missing.
        """
        if not self._tracked:
            return ""
        if self.is_initialized:
            self._adopt_place()
            return ""
        recorded = read_place()
        if not recorded:
            return ""
        here = os.path.normcase(os.path.abspath(self._vault_dir))
        there = os.path.normcase(os.path.abspath(recorded))
        if here != there and _vault_files_present(recorded):
            return (f"Your vault is in {recorded}, but BuildBot is set to use "
                    f"{self._vault_dir}, where there is none. It did not make "
                    f"a second vault. Set it back to {recorded} to use your "
                    f"vault.")
        return (f"{VAULT_DRIVE_MISSING} Your vault was made in {recorded}, "
                f"and that folder is not there now. Plug in the drive that "
                f"holds it and try again. BuildBot will not make a new, empty "
                f"vault in its place. If that drive is gone for good and you "
                f"want to start a new, empty vault, delete {_PLACE_FILENAME} "
                f"from BuildBot's state folder first (puffpai where shows "
                f"that folder).")

    def _refuse_if_missing(self) -> None:
        why = self.missing_reason()
        if why:
            self._log("vault_missing", "refused")
            raise VaultMissing(why)

    def _remember_place(self) -> None:
        if self._tracked:
            remember_place(self._vault_dir,
                           fingerprint=self.place_fingerprint() or None)

    def place_fingerprint(self) -> str:
        """Which vault this is, as only its PIN can show; ``""`` when locked.

        An HMAC of a fixed text under the open vault's data key. It says
        nothing about the key, and nobody without the key can make a vault
        that gives the same answer: a copy of ``vault.meta.json`` is not
        enough. Kept in ``vault_place.json`` so that, while the remembered
        vault's drive is missing, "the same vault, now under another drive
        letter" can be told apart from "some other vault" (see
        :func:`puffpai.security.vault_place.use_existing`).
        """
        with self._state_lock:
            key = self._data_key
        if not key:
            return ""
        raw = key if isinstance(key, bytes) else str(key).encode("utf-8")
        return hmac.new(raw, _PLACE_FP_CONTEXT, hashlib.sha256).hexdigest()

    def _adopt_place(self) -> None:
        """Remember an existing vault's folder when nothing is remembered yet.

        Never overwrites a record (a record pointing elsewhere is exactly
        what :meth:`missing_reason` reports) and never raises.
        """
        try:
            if self._tracked and self.is_initialized and read_place() is None:
                remember_place(self._vault_dir)
        except Exception:  # remembering is a help, never a new way to fail
            pass

    def _rekeyed_elsewhere(self) -> bool:
        """True when the metadata on disk names a different contents file
        than the one this handle opened: another handle re-keyed the vault.

        Only a positive mismatch counts. Unreadable or absent metadata (a
        legacy vault, a write in progress) is not taken as a re-key.
        """
        if self._active_data_path is None:
            return False
        meta = self._read_meta()
        if not meta:
            return False
        current = str(meta.get("data_file") or "")
        return bool(current) and current != os.path.basename(self._active_data_path)

    @property
    def vault_dir(self) -> str:
        return self._vault_dir

    @property
    def has_recovery_code(self) -> bool:
        """True when a recovery code can currently open this vault.

        False for a legacy vault and for an envelope vault that has not been
        given a code yet. The wizard reads this rather than assuming.
        """
        meta = self._read_meta()
        if not meta:
            return False
        return bool((meta.get("wraps") or {}).get("recovery"))

    # -- lifecycle --------------------------------------------------------
    def create(self, password: str) -> VaultSession:
        """Initialize a brand-new vault protected by ``password``.

        Generates a random data key, wraps it under ``password``, writes the
        encrypted empty store, and leaves the vault unlocked. Refuses to
        overwrite an existing vault (fail-closed) and refuses a secret below the
        vault's floor (:func:`validate_vault_secret`).

        No recovery code is issued here; call :meth:`create_with_recovery` or
        :meth:`add_recovery_code` for that.
        """
        session, _ = self._create(password, with_recovery=False)
        return session

    def create_with_recovery(self, password: str) -> tuple[VaultSession, str]:
        """Initialize a vault and issue its recovery code in one step.

        Returns ``(session, recovery_code)``. The code is returned for exactly
        one screen and is not stored anywhere - not in the vault, not in the
        metadata, not in a log. If the caller loses it, it is gone, which is the
        property the whole ceremony depends on.
        """
        return self._create(password, with_recovery=True)

    def _create(self, password: str, *, with_recovery: bool) -> tuple[VaultSession, str]:
        if not password:
            raise VaultError("a vault password is required")
        if self.is_initialized:
            raise VaultError(
                f"a vault already exists in {self._vault_dir!r}; refusing to overwrite"
            )
        # Before anything is made: a vault that should be on a drive that is
        # not plugged in is never replaced by a new, empty one here.
        self._refuse_if_missing()
        _check_secret_or_raise(password)

        os.makedirs(self._vault_dir, exist_ok=True)
        data_key = Fernet.generate_key()
        self._data = {"secrets": {}, "documents": {}}
        data_path = self._new_data_path()
        self._atomic_write(data_path, Fernet(data_key).encrypt(self._plaintext()))

        code = ""
        wraps = {"pin": self._wrap(data_key, password, _PBKDF2_ITERATIONS)}
        if with_recovery:
            code = _generate_recovery_code()
            wraps["recovery"] = self._wrap(
                data_key, normalize_recovery_code(code), _RECOVERY_PBKDF2_ITERATIONS
            )

        self._write_meta({
            "schema": META_SCHEMA,
            "data_file": os.path.basename(data_path),
            "kdf": {"algorithm": "pbkdf2-hmac-sha256", "length": _KEY_LENGTH},
            "wraps": wraps,
            "created_at": time.time(),
            "recovery_rotated_at": time.time() if with_recovery else None,
        })

        self._data_key = data_key
        self._active_data_path = data_path
        self._open_session()
        self._remember_key_records()
        self._log("vault_create", "success",
                  "with_recovery" if with_recovery else "pin_only")
        self._remember_place()
        self._after_open()
        return self._require_session(), code

    def unlock(self, password: str) -> VaultSession:
        """Open a session by verifying ``password`` against the stored data.

        Returns a :class:`VaultSession`. Raises :class:`AuthenticationError` on a
        wrong password (or a corrupt/absent vault), and :class:`VaultThrottled`
        when attempts are coming too fast - the throttle is checked *before* any
        key derivation runs.

        A legacy single-secret vault is transparently upgraded to the envelope
        format on the first successful unlock.
        """
        if not self.is_initialized:
            self._refuse_if_missing()
            raise VaultError(f"no vault found in {self._vault_dir!r}; create one first")

        self._check_throttle("pin")

        if self.is_legacy:
            return self._unlock_legacy(password)

        meta = self._read_meta()
        if not meta:
            raise VaultError("the vault metadata could not be read")
        data_key = self._unwrap(meta, "pin", password, _PBKDF2_ITERATIONS)
        if data_key is None:
            self._note_failure("pin")
            self._log("vault_auth", "failed")
            raise AuthenticationError("incorrect vault password")

        self._load_with(data_key, meta)
        self._note_success("pin")
        self._log("vault_auth", "success")
        self._remember_place()
        self._after_open()
        return self._require_session()

    def check_pin(self, password: str) -> bool:
        """Is ``password`` this vault's PIN? Never opens the vault.

        For the changes that need the PIN as a yes (moving the vault, using
        another one) without opening anything. It counts against the SAME
        unlock throttle as :meth:`unlock` (the ``pin`` bucket), checked before
        any key derivation, so it is never a faster way to guess. The data
        key it recovers on a match is dropped here.

        Raises :class:`VaultMissing` for a vault whose drive is not plugged
        in, :class:`VaultThrottled` in a cooldown, and :class:`VaultError`
        for no vault, or an older single-secret vault (which has to be opened
        once, so it is brought up to date, before it can be checked).
        """
        if not self.is_initialized:
            self._refuse_if_missing()
            raise VaultError(f"no vault found in {self._vault_dir!r}")
        self._check_throttle("pin")
        if self.is_legacy:
            raise VaultError("this is an older vault: open it once with your "
                             "PIN first, then try again")
        meta = self._read_meta()
        if not meta:
            raise VaultError("the vault metadata could not be read")
        key = self._unwrap(meta, "pin", password, _PBKDF2_ITERATIONS)
        matched = key is not None
        key = None  # noqa: F841 - never kept
        if not matched:
            self._note_failure("pin")
            self._log("vault_auth", "failed", "pin check")
            return False
        self._note_success("pin")
        self._log("vault_auth", "success", "pin check")
        return True

    def authenticate(self, password: str) -> VaultSession:
        """Alias for :meth:`unlock`.

        The appliance vault names this operation ``authenticate``. This module
        promises the two are drop-in compatible, so the name is honoured here
        rather than left as an ``AttributeError`` for a caller to discover in
        production.
        """
        return self.unlock(password)

    #: Screenings allowed per window, and the window.
    SCREEN_LIMIT = 6
    SCREEN_WINDOW_SECONDS = 60.0

    def screen_pin(self, text: str) -> Optional[bool]:
        """Is ``text`` this vault's PIN? For chat, which must never echo,
        send or keep the PIN, whatever shape the PIN rule let it have
        ("marmalade-otter"). Never opens the vault, never touches the unlock
        throttle (an ordinary word is no failed unlock).

        Returns True or False, or None when it cannot say: no vault, an old
        single-secret vault, or more than :attr:`SCREEN_LIMIT` screenings in
        :attr:`SCREEN_WINDOW_SECONDS` (its own bucket, so screening is never
        a fast way to guess). The caller treats None as "could be the PIN"
        (fail closed). The data key it recovers on a match is dropped here.
        """
        if not text or not self.is_initialized or self.is_legacy:
            return None
        now = time.monotonic()
        with self._attempt_lock:
            stamps = [t for t in getattr(self, "_screens", [])
                      if now - t < self.SCREEN_WINDOW_SECONDS]
            if len(stamps) >= self.SCREEN_LIMIT:
                self._screens = stamps
                return None
            stamps.append(now)
            self._screens = stamps
        meta = self._read_meta()
        if not meta:
            return None
        key = self._unwrap(meta, "pin", text, _PBKDF2_ITERATIONS)
        matched = key is not None
        key = None  # noqa: F841 - never kept
        return matched

    def redeem_recovery_code(
        self, code: str, new_password: str, *,
        policy: Optional[Callable[[str], tuple[bool, str]]] = None,
    ) -> tuple[VaultSession, str]:
        """Recover access with the recovery code, setting a new PIN.

        Returns ``(session, new_recovery_code)``. This is a recovery, not a
        login: a code that has been read off paper is treated as spent, so the
        vault is re-keyed under a fresh data key, the old PIN stops working, the
        old code stops working, and a new code is issued and shown once.

        Raises :class:`AuthenticationError` if the code is wrong or no recovery
        code was ever issued, :class:`VaultThrottled` if attempts are coming too
        fast, and :class:`WeakSecretError` if ``new_password`` is below the
        floor - checked *before* the re-key, so a rejected new PIN leaves the
        vault exactly as it was.

        ``policy`` is an optional stricter rule for the new PIN (the setup
        wizard's, :func:`puffpai.setup_ops.validate_vault_pin`), returning
        ``(ok, reason)``. It runs in the same place as the floor: after the
        cooldown check and before any key derivation, so a caller in cooldown
        is told about the cooldown, and a refused PIN costs no derivation.
        """
        if not self.is_initialized:
            self._refuse_if_missing()
            raise VaultError(f"no vault found in {self._vault_dir!r}")
        if self.is_legacy:
            raise VaultError(
                "this vault predates recovery codes; unlock it with its password "
                "once to upgrade it, then add a recovery code"
            )

        # The recovery counter is deliberately separate from the PIN counter, so
        # someone hammering the PIN cannot also block the owner's way back in.
        self._check_throttle("recovery")
        if policy is not None:
            ok, reason = policy(new_password)
            if not ok:
                raise WeakSecretError(reason or "that PIN is not allowed")
        _check_secret_or_raise(new_password)

        meta = self._read_meta()
        if not meta:
            raise VaultError("the vault metadata could not be read")
        if not (meta.get("wraps") or {}).get("recovery"):
            raise AuthenticationError("this vault has no recovery code")

        data_key = self._unwrap(
            meta, "recovery", normalize_recovery_code(code), _RECOVERY_PBKDF2_ITERATIONS
        )
        if data_key is None:
            self._note_failure("recovery")
            self._log("vault_recovery", "failed")
            raise AuthenticationError("that recovery code does not match this vault")

        self._load_with(data_key, meta)
        self._note_success("recovery")
        self._note_success("pin")

        # Re-key: a fresh data key, a fresh PIN wrap, a fresh code. Everything
        # the old secrets could open is left behind.
        new_code = self._rekey(new_password)
        self._log("vault_recovery", "success", "rekeyed")
        self._remember_place()
        self._after_open()
        return self._require_session(), new_code

    def add_recovery_code(self, code: Optional[str] = None) -> str:
        """Issue a recovery code for an already-unlocked vault. Returns it once.

        Requires an unlocked session, because the data key only exists in memory
        while the vault is open - that is the honest answer to how an existing
        vault gains recovery: its owner opens it with the password they still
        have, and we wrap the same data key a second time.

        Replaces any existing recovery code. The returned string is not stored
        anywhere by this method.
        """
        self._require_unlocked()
        meta = self._read_meta()
        if not meta:
            raise VaultError(
                "this vault has not been upgraded to the recovery-capable format"
            )
        issued = code or _generate_recovery_code()
        wraps = dict(meta.get("wraps") or {})
        assert self._data_key is not None
        wraps["recovery"] = self._wrap(
            self._data_key, normalize_recovery_code(issued), _RECOVERY_PBKDF2_ITERATIONS
        )
        meta["wraps"] = wraps
        meta["recovery_rotated_at"] = time.time()
        self._write_meta(meta)
        self._log("vault_recovery_issue", "success")
        return issued

    def lock(self) -> None:
        """Close the session and drop the key and plaintext from memory."""
        self._lock(reason="lock")

    def _lock(self, *, reason: str) -> None:
        """Lock, saying why (``lock``, ``idle`` or ``rekeyed``) to listeners."""
        # The raw fields, not is_unlocked: is_unlocked calls lock() itself
        # when it finds the vault re-keyed elsewhere.
        with self._state_lock:
            was_open = self._data_key is not None and self._session is not None
            self._data_key = None
            self._session = None
            self._active_data_path = None
            self._data = {"secrets": {}, "documents": {}}
            self._key_seen = {}
            self._key_dirty = set()
            listeners = list(self._lock_listeners) if was_open else []
        if was_open:
            self._log("vault_lock", "success", "idle" if reason == "idle" else "")
        for callback in listeners:
            try:
                callback(reason)
            except Exception:  # a listener never keeps the vault open
                pass

    # -- secrets ----------------------------------------------------------
    def store(self, name: str, value: str) -> None:
        """Store (or replace) a named secret string. Requires an unlocked vault."""
        self._require_unlocked()
        if _is_reserved(name):
            raise VaultError("that name is reserved for the vault itself")
        self._data["secrets"][str(name)] = str(value)
        self._flush()
        self._log("vault_store_secret", "success", name)

    def retrieve(self, name: str) -> Optional[str]:
        """Return a named secret, or ``None`` if it is not present."""
        self._require_unlocked()
        if _is_reserved(name):
            return None
        return self._data["secrets"].get(str(name))

    def list_secrets(self) -> list[str]:
        """Return the sorted names of stored secrets (names only, never values)."""
        self._require_unlocked()
        return sorted(k for k in self._data["secrets"] if not _is_reserved(k))

    def delete(self, name: str) -> bool:
        """Delete a named secret. Returns True if it existed."""
        self._require_unlocked()
        if _is_reserved(name):
            return False
        existed = self._data["secrets"].pop(str(name), None) is not None
        if existed:
            self._flush()
            self._log("vault_delete_secret", "success", name)
        return existed

    # -- documents (small blobs) ------------------------------------------
    def store_document(self, name: str, data: bytes) -> None:
        """Store (or replace) a small binary document under ``name``."""
        self._require_unlocked()
        encoded = base64.b64encode(bytes(data)).decode("ascii")
        self._data["documents"][str(name)] = encoded
        self._flush()
        self._log("vault_store_document", "success", name)

    def retrieve_document(self, name: str) -> Optional[bytes]:
        """Return a stored document as bytes, or ``None`` if absent."""
        self._require_unlocked()
        encoded = self._data["documents"].get(str(name))
        if encoded is None:
            return None
        return base64.b64decode(encoded.encode("ascii"))

    def list_documents(self) -> list[str]:
        """Return the sorted names of stored documents."""
        self._require_unlocked()
        return sorted(self._data["documents"].keys())

    # -- status reads: never count as use ----------------------------------
    #
    # A page that polls how the vault is doing (the home page and the Device
    # page ask every 30 s) is not the owner using the vault. Reading through
    # list_secrets()/list_documents() restarts the idle time on every poll,
    # so an open home page would keep the vault open for good. These readers
    # answer from the open vault without touching the idle time, and say
    # nothing (None) when it is locked.
    def item_count(self) -> Optional[int]:
        """How many secrets and documents the open vault holds; None when
        locked. Reading this is not use: the idle time is not restarted."""
        with self._state_lock:
            try:
                self._require_unlocked(touch=False)
            except VaultLocked:
                return None
            secrets_n = sum(1 for k in self._data["secrets"] if not _is_reserved(k))
            return secrets_n + len(self._data["documents"])

    def holds_documents(self) -> Optional[bool]:
        """True/False whether the open vault holds a document; None when it is
        locked (the names are inside the encrypted file, so nobody can tell
        without the PIN). Reading this is not use."""
        with self._state_lock:
            try:
                self._require_unlocked(touch=False)
            except VaultLocked:
                return None
            return bool(self._data["documents"])

    def session_is(self, session_id: object) -> bool:
        """True when the vault is open under *session_id* right now.

        False once it has locked (by hand, when left alone, or re-keyed
        elsewhere): a chat that opened it cannot keep using it after that.
        Asking is not use.
        """
        if not isinstance(session_id, str) or not session_id:
            return False
        with self._state_lock:
            if not self.is_unlocked or self._session is None:
                return False
            return _secrets.compare_digest(self._session.session_id, session_id)

    # -- secrets-provider integration -------------------------------------
    def get_service_secret(self, name: str) -> Optional[str]:
        """Return a stored secret for the secrets-provider chain.

        Contract required by :mod:`puffpai.security.secrets_provider`: return the
        secret when the vault is unlocked and the name is present, otherwise
        ``None``. Never raises - a locked vault or a missing name simply falls
        through to the next provider (keyring, then environment).
        """
        if not self.is_unlocked or _is_reserved(name):
            return None
        try:
            return self._data["secrets"].get(str(name))
        except Exception:
            return None

    # -- reserved items (the vault's own keys) ---------------------------
    def _reserved_get(self, name: str) -> Optional[str]:
        """Read a reserved item. Internal: for :mod:`vault_inbox` and, for
        ``__puffpai.key.*`` names only, :mod:`puffpai.security.keys`."""
        # An automatic job reading a key is not the owner using the vault.
        self._require_unlocked(touch=False)
        if not _is_reserved(name):
            raise VaultError("not a reserved name")
        if _is_key_name(name):
            _require_key_broker()
            # Another process (the terminal) may have added or removed a key
            # since this handle opened: read what is on disk now.
            self._merge_key_records_from_disk()
        return self._data["secrets"].get(str(name))

    def _reserved_set(self, name: str, value: str) -> None:
        """Write a reserved item. Internal: for :mod:`vault_inbox` and, for
        ``__puffpai.key.*`` names only, :mod:`puffpai.security.keys`."""
        self._require_unlocked()
        if not _is_reserved(name):
            raise VaultError("not a reserved name")
        if _is_key_name(name):
            _require_key_broker()
            self._key_dirty.add(str(name))
        self._data["secrets"][str(name)] = str(value)
        self._flush()
        self._log("vault_store_secret", "success", "reserved")

    def _reserved_delete(self, name: str) -> bool:
        """Delete a sign-up key record. Internal: for the key broker only."""
        self._require_unlocked()
        if not _is_key_name(name):
            raise VaultError("only a sign-up key record can be deleted here")
        _require_key_broker()
        self._merge_key_records_from_disk()
        existed = self._data["secrets"].pop(str(name), None) is not None
        self._key_dirty.add(str(name))
        self._flush()
        if existed:
            self._log("vault_delete_secret", "success", "reserved")
        return existed

    def _reserved_key_names(self) -> list[str]:
        """Names (never values) of the sign-up key records. Broker only."""
        self._require_unlocked(touch=False)
        _require_key_broker()
        self._merge_key_records_from_disk()
        return sorted(n for n in self._data["secrets"] if _is_key_name(n))

    # -- sign-up key records: one writer at a time, many processes ---------
    #
    # The server keeps its vault open in memory; `puffpai keys add` in a
    # terminal opens its own handle on the same files. Without this, the
    # server's next flush wrote its stale copy back and silently undid the
    # terminal's add -- or brought back a key the owner had removed. Key
    # records are merged three ways on every flush and every key read:
    # a record this handle changed wins; otherwise what is on disk now wins.
    # Only key records are merged; everything else keeps the old behaviour.

    def _disk_secrets(self) -> Optional[dict[str, str]]:
        """The secrets section as it is on disk now, or None if unreadable
        with this handle's key (for example after a recovery re-key)."""
        if self._data_key is None or self._active_data_path is None:
            return None
        meta = self._read_meta()
        if meta and os.path.basename(self._active_data_path) != str(
                meta.get("data_file") or ""):
            return None
        try:
            with open(self._active_data_path, "rb") as fh:
                token = fh.read()
            raw = json.loads(Fernet(self._data_key).decrypt(token).decode("utf-8"))
        except (OSError, InvalidToken, ValueError):
            return None
        return self._normalize(raw)["secrets"]

    def _merge_key_records_from_disk(self) -> None:
        disk = self._disk_secrets()
        if disk is None:
            return
        names = {n for n in disk if _is_key_name(n)} | set(self._key_seen)
        for name in names:
            if name in self._key_dirty:
                continue
            on_disk = disk.get(name)
            if on_disk != self._key_seen.get(name):
                if on_disk is None:
                    self._data["secrets"].pop(name, None)
                else:
                    self._data["secrets"][name] = on_disk
            if on_disk is None:
                self._key_seen.pop(name, None)
            else:
                self._key_seen[name] = on_disk

    def _remember_key_records(self) -> None:
        self._key_seen = {n: v for n, v in self._data["secrets"].items()
                          if _is_key_name(n)}
        self._key_dirty = set()

    def _after_open(self) -> None:
        """Prepare and empty the write-only inbox. Never raises.

        Runs after every create, unlock and recovery, so a vault opened by the
        wizard, the CLI or the API gains an inbox key the same way, and items
        sealed while it was locked are filed the moment the owner opens it.
        """
        try:
            from puffpai.security import vault_inbox
            vault_inbox.on_vault_opened(self)
        except Exception:  # pragma: no cover - never block opening the vault
            pass
        try:
            # Sign-up keys removed while it was locked are cleared now.
            from puffpai.security import keys as _keys
            _keys.on_vault_opened(self)
        except Exception:  # pragma: no cover - never block opening the vault
            pass
        try:
            # A Toolsmith run waiting for the vault goes on from the same app.
            from puffpai.security import toolsmith_cloud as _tc
            _tc.on_vault_opened(self)
        except Exception:  # pragma: no cover - never block opening the vault
            pass
        try:
            # Files waiting for the vault (knowledge/my_data.py) are finished
            # on the watcher's next look: wake it now.
            from puffpai.knowledge import my_data as _my_data
            _my_data.poke()
        except Exception:  # pragma: no cover - never block opening the vault
            pass

    # -- the unattended tier (ported from HomeBotBuild, see module note) ---
    def _service_secret_path(self, filename: str) -> str:
        safe = os.path.basename(str(filename or ""))
        if safe != filename or not safe or ".." in safe:
            raise VaultError("not a service-tier vault record")
        if safe not in SERVICE_SECRET_FILES:
            try:
                from puffpai.security import keys as _keys
                allowed = _keys.background_file_allowed(safe)
            except Exception:
                allowed = False
            if not allowed:
                raise VaultError("not a service-tier vault record: " + safe)
        return os.path.join(self._vault_dir, safe)

    @property
    def has_service_wrap(self) -> bool:
        """True if the metadata carries a machine-bound wrap of the SDK."""
        meta = self._read_meta() or {}
        return isinstance(meta.get(_SERVICE_WRAP_KEY), dict)

    def _sdk_from_session(self, *, create: bool) -> bytes:
        """Recover (or mint) the Service Data Key with the open vault."""
        self._require_unlocked()
        assert self._data_key is not None
        meta = self._read_meta()
        if not meta:
            raise VaultError("the vault must be in the recovery-capable format "
                             "before keys can work while it is locked")
        wrap = meta.get(_SERVICE_DEK_WRAP_KEY)
        subkey = _dek_subkey(self._data_key)
        if isinstance(wrap, dict):
            try:
                return _aesgcm()(subkey).decrypt(
                    bytes.fromhex(wrap["nonce"]), bytes.fromhex(wrap["ct"]),
                    _SDK_DEK_WRAP_AAD)
            except Exception as exc:
                raise VaultError("service key wrap is unreadable") from exc
        if not create:
            raise VaultError("no service key in this vault")
        sdk = _secrets.token_bytes(_SDK_LENGTH)
        nonce = _secrets.token_bytes(_NONCE_LENGTH)
        meta[_SERVICE_DEK_WRAP_KEY] = {
            "kdf": "hkdf-sha256", "nonce": nonce.hex(),
            "ct": _aesgcm()(subkey).encrypt(nonce, sdk, _SDK_DEK_WRAP_AAD).hex()}
        self._write_meta(meta)
        self._log("vault_service_key_create", "success")
        return sdk

    def _sdk_from_machine_key(self) -> bytes:
        """Recover the SDK with the machine-bound secret. No PIN."""
        wrap = (self._read_meta() or {}).get(_SERVICE_WRAP_KEY)
        if not isinstance(wrap, dict):
            raise VaultError("no service wrap enrolled")
        secret, source = read_service_key(self._vault_dir)
        if secret is None:
            raise VaultError("machine service key unavailable: " + str(source))
        try:
            kek = _scrypt_kek(secret, bytes.fromhex(wrap["salt"]), wrap)
            return _aesgcm()(kek).decrypt(bytes.fromhex(wrap["nonce"]),
                                          bytes.fromhex(wrap["ct"]),
                                          _SDK_WRAP_AAD)
        except Exception as exc:
            raise VaultError("service wrap did not open with this machine "
                             "key") from exc

    def enroll_service_wrap(self, *, make_machine_key: bool = False) -> bool:
        """Add the machine-bound wrap of the SDK. Requires an open vault.

        Idempotent. Returns False (never raises) when the vault is locked or
        no machine key is available. ``make_machine_key`` lets BuildBot make
        one in the operating system's password store first.
        """
        if not self.is_unlocked:
            return False
        if make_machine_key:
            create_machine_key(self._vault_dir)
        secret, _source = read_service_key(self._vault_dir)
        if secret is None:
            return False
        try:
            sdk = self._sdk_from_session(create=True)
            meta = self._read_meta() or {}
            salt = _secrets.token_bytes(32)
            wrap = {"kdf": "scrypt", "n": _SCRYPT_N, "r": _SCRYPT_R,
                    "p": _SCRYPT_P, "salt": salt.hex()}
            kek = _scrypt_kek(secret, salt, wrap)
            nonce = _secrets.token_bytes(_NONCE_LENGTH)
            wrap["nonce"] = nonce.hex()
            wrap["ct"] = _aesgcm()(kek).encrypt(nonce, sdk, _SDK_WRAP_AAD).hex()
            wrap["created"] = time.time()
            meta[_SERVICE_WRAP_KEY] = wrap
            self._write_meta(meta)
        except Exception:
            self._log("vault_service_wrap_enroll", "error")
            return False
        self._log("vault_service_wrap_enroll", "success")
        return True

    def remove_service_wrap(self) -> bool:
        """Drop the machine-bound wrap: nothing works while locked any more.

        Deliberately needs NO open vault: switching the locked path off is a
        one-step "off", and it only ever removes a way in.
        """
        meta = self._read_meta()
        if not meta or _SERVICE_WRAP_KEY not in meta:
            return True
        meta.pop(_SERVICE_WRAP_KEY, None)
        try:
            self._write_meta(meta)
        except Exception:
            return False
        self._log("vault_service_wrap_remove", "success")
        return True

    def write_service_secret(self, filename: str, data: Any) -> None:
        """Encrypt one allowlisted record under the SDK. Requires an open vault."""
        path = self._service_secret_path(filename)
        if os.path.basename(path).startswith("key_"):
            _require_key_broker()
        sdk = self._sdk_from_session(create=True)
        plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
        nonce = _secrets.token_bytes(_NONCE_LENGTH)
        self._atomic_write(path, nonce + _aesgcm()(sdk).encrypt(nonce, plaintext, None))
        self._log("vault_service_secret_write", "success", os.path.basename(path))

    def _decrypt_service_record(self, path: str, sdk: bytes) -> Any:
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            raw = fh.read()
        if len(raw) < _NONCE_LENGTH + 16:
            raise VaultError("service record is too short")
        try:
            plain = _aesgcm()(sdk).decrypt(raw[:_NONCE_LENGTH], raw[_NONCE_LENGTH:], None)
            return json.loads(plain.decode("utf-8"))
        except Exception as exc:
            raise VaultError("cannot decrypt service record") from exc

    def read_service_secret(self, filename: str) -> Any:
        """Read one allowlisted record with the open vault."""
        path = self._service_secret_path(filename)
        if os.path.basename(path).startswith("key_"):
            _require_key_broker()
        return self._decrypt_service_record(path, self._sdk_from_session(create=False))

    def read_service_secret_unattended(self, filename: str) -> Any:
        """Read one allowlisted record with the machine key alone. No PIN.

        Returns None (never raises, never logs the value) when there is no
        wrap, no machine key, no record, or the record does not authenticate.
        Reaches ONLY the allowlist: the SDK is not the data key.
        """
        try:
            path = self._service_secret_path(filename)
            if os.path.basename(path).startswith("key_"):
                _require_key_broker()
            if not os.path.isfile(path):
                return None  # cheap exit BEFORE the key derivation
            return self._decrypt_service_record(path, self._sdk_from_machine_key())
        except Exception:
            return None

    def delete_service_secret(self, filename: str) -> bool:
        """Remove one service-tier record. Needs no key: removing is "off"."""
        safe = os.path.basename(str(filename or ""))
        if safe != filename or not safe.startswith("key_") or ".." in safe:
            raise VaultError("not a service-tier key record")
        _require_key_broker()
        path = os.path.join(self._vault_dir, safe)
        existed = os.path.isfile(path)
        _quiet_unlink(path)
        if existed:
            self._log("vault_service_secret_delete", "success", safe)
        return existed

    # -- throttling -------------------------------------------------------
    def _check_throttle(self, kind: str) -> None:
        """Raise :class:`VaultThrottled` if ``kind`` is in cooldown.

        Called before any PBKDF2 work. Four properties make this a throttle
        rather than a denial-of-service lever of its own:

        * **It rejects before the key derivation runs.** A flood of wrong
          guesses costs a dictionary lookup, not 600,000 SHA-256 rounds, so the
          expensive path cannot be used as CPU amplification.
        * **It never sleeps.** The caller is told how long to wait and the
          worker thread is released immediately, so an attacker cannot pin the
          request pool by holding threads in a delay.
        * **The state is in memory and per-process.** Nothing is written to
          disk, so a remote attacker cannot make a lockout durable, and a
          service restart always clears it. The owner is never locked out for
          longer than the cooldown, which is capped at five minutes.
        * **PIN and recovery are counted separately.** Hammering the PIN cannot
          put the recovery path into cooldown, so the owner's way back in stays
          available even while someone is attacking the front door.

        The residual nuisance is real and worth stating: someone who can reach
        the unlock endpoint can keep the *PIN* path in cooldown indefinitely.
        That is why the recovery path does not share the counter.
        """
        with self._attempt_lock:
            failures, blocked_until = self._attempts.get(kind, (0, 0.0))
            remaining = blocked_until - time.monotonic()
        if remaining > 0:
            raise VaultThrottled(remaining)

    def _note_failure(self, kind: str) -> None:
        """Record a failed attempt and extend the cooldown geometrically."""
        with self._attempt_lock:
            failures, _ = self._attempts.get(kind, (0, 0.0))
            failures += 1
            if failures <= _FREE_ATTEMPTS:
                blocked_until = 0.0
            else:
                delay = min(
                    _BACKOFF_BASE_SECONDS * (2 ** (failures - _FREE_ATTEMPTS - 1)),
                    _BACKOFF_MAX_SECONDS,
                )
                blocked_until = time.monotonic() + delay
            self._attempts[kind] = (failures, blocked_until)

    def _note_success(self, kind: str) -> None:
        """Clear the cooldown for ``kind`` after a correct secret."""
        with self._attempt_lock:
            self._attempts.pop(kind, None)

    # -- envelope internals -----------------------------------------------
    def _wrap(self, data_key: bytes, secret: str, iterations: int) -> dict[str, Any]:
        """Wrap ``data_key`` under ``secret``, returning a JSON-safe record.

        The record holds a random salt, the iteration count, and the wrapped key
        - never the secret, and nothing derived from it that could be compared
        against a guess without doing the full derivation.
        """
        salt = os.urandom(_SALT_LENGTH)
        kek = _derive_key(secret, salt, iterations)
        return {
            "salt": base64.b64encode(salt).decode("ascii"),
            "iterations": iterations,
            "wrapped_key": Fernet(kek).encrypt(data_key).decode("ascii"),
        }

    def _unwrap(self, meta: dict[str, Any], kind: str, secret: str,
                default_iterations: int) -> Optional[bytes]:
        """Recover the data key from one wrap, or None if ``secret`` is wrong."""
        record = (meta.get("wraps") or {}).get(kind)
        if not isinstance(record, dict) or not secret:
            return None
        try:
            salt = base64.b64decode(str(record.get("salt", "")).encode("ascii"))
            iterations = int(record.get("iterations", default_iterations))
            token = str(record["wrapped_key"]).encode("ascii")
        except (KeyError, ValueError, TypeError):
            return None
        if not salt:
            return None
        kek = _derive_key(secret, salt, iterations)
        try:
            return Fernet(kek).decrypt(token)
        except InvalidToken:
            # Wrong secret, or tampered metadata - indistinguishable, and both
            # must be rejected. Do not leak which.
            return None

    def _load_with(self, data_key: bytes, meta: dict[str, Any]) -> None:
        """Decrypt the contents with ``data_key`` and open a session."""
        data_path = os.path.join(
            self._vault_dir, str(meta.get("data_file") or _DATA_FILENAME)
        )
        try:
            with open(data_path, "rb") as fh:
                token = fh.read()
        except OSError as exc:
            raise VaultError(f"could not read vault contents: {exc}") from exc
        try:
            plaintext = Fernet(data_key).decrypt(token)
        except InvalidToken as exc:
            raise VaultError(
                "the vault contents did not authenticate against their key"
            ) from exc
        self._data_key = data_key
        self._active_data_path = data_path
        self._data = self._normalize(json.loads(plaintext.decode("utf-8")))
        self._open_session()
        self._remember_key_records()

    def _rekey(self, new_password: str) -> str:
        """Re-encrypt the contents under a fresh data key. Returns a new code.

        Used by recovery. Writes the new ciphertext to a **new** file and makes
        the metadata swap the single commit point, so a crash part-way through
        leaves the previous key, the previous file and the previous metadata all
        intact and consistent.
        """
        self._require_unlocked()
        old_path = self._active_data_path
        # The unattended tier's key is wrapped under the data key too; carry
        # it over, or every "keep working when locked" key would be orphaned.
        try:
            sdk: Optional[bytes] = self._sdk_from_session(create=False)
        except VaultError:
            sdk = None
        data_key = Fernet.generate_key()
        new_path = self._new_data_path()
        self._atomic_write(new_path, Fernet(data_key).encrypt(self._plaintext()))

        code = _generate_recovery_code()
        meta = self._read_meta() or {}
        if sdk is not None:
            nonce = _secrets.token_bytes(_NONCE_LENGTH)
            meta[_SERVICE_DEK_WRAP_KEY] = {
                "kdf": "hkdf-sha256", "nonce": nonce.hex(),
                "ct": _aesgcm()(_dek_subkey(data_key)).encrypt(
                    nonce, sdk, _SDK_DEK_WRAP_AAD).hex()}
        meta.update({
            "schema": META_SCHEMA,
            "data_file": os.path.basename(new_path),
            "kdf": {"algorithm": "pbkdf2-hmac-sha256", "length": _KEY_LENGTH},
            "wraps": {
                "pin": self._wrap(data_key, new_password, _PBKDF2_ITERATIONS),
                "recovery": self._wrap(
                    data_key, normalize_recovery_code(code), _RECOVERY_PBKDF2_ITERATIONS
                ),
            },
            "recovery_rotated_at": time.time(),
        })
        self._write_meta(meta)   # <- commit point

        self._data_key = data_key
        self._active_data_path = new_path
        if old_path and old_path != new_path:
            _quiet_unlink(old_path)
        return code

    def _unlock_legacy(self, password: str) -> VaultSession:
        """Open an original single-secret vault, then upgrade it in place."""
        try:
            with open(self._salt_path, "rb") as fh:
                salt = fh.read()
            with open(self._data_path, "rb") as fh:
                token = fh.read()
        except OSError as exc:
            raise VaultError(f"could not read vault files: {exc}") from exc

        key = _derive_key(password, salt, _PBKDF2_ITERATIONS)
        try:
            plaintext = Fernet(key).decrypt(token)
        except InvalidToken:
            self._note_failure("pin")
            self._log("vault_auth", "failed")
            raise AuthenticationError("incorrect vault password")

        self._data_key = key
        self._active_data_path = self._data_path
        self._data = self._normalize(json.loads(plaintext.decode("utf-8")))
        self._open_session()
        self._remember_key_records()
        self._note_success("pin")
        self._log("vault_auth", "success", "legacy")
        self._upgrade_legacy(password)
        self._after_open()
        return self._require_session()

    def _upgrade_legacy(self, password: str) -> None:
        """Migrate a just-opened legacy vault to the envelope format.

        Crash safety comes from never touching the legacy files until the new
        ones are committed. The order is: write the new ciphertext under a new
        name, then write the metadata - the metadata's existence is what makes
        the vault "v2", so it is the single commit point - and only then remove
        the legacy salt and data files.

        * Crash before the metadata is written: the legacy files are untouched
          and still open with the same password. The stray new file is inert.
        * Crash after: the envelope vault is complete and correct; leftover
          legacy files are ignored and cleaned up on the next unlock.

        Failure here is deliberately non-fatal. If the disk is full or read-only
        the owner still has their unlocked vault; refusing to open a vault we
        just successfully decrypted would be a worse outcome than staying on the
        old format for another day.
        """
        try:
            plaintext = self._plaintext()
            data_key = Fernet.generate_key()
            new_path = self._new_data_path()
            self._atomic_write(new_path, Fernet(data_key).encrypt(plaintext))
            self._write_meta({
                "schema": META_SCHEMA,
                "data_file": os.path.basename(new_path),
                "kdf": {"algorithm": "pbkdf2-hmac-sha256", "length": _KEY_LENGTH},
                "wraps": {"pin": self._wrap(data_key, password, _PBKDF2_ITERATIONS)},
                "created_at": time.time(),
                "recovery_rotated_at": None,
                "upgraded_from": "puffpai_vault/v1",
            })
            # Committed. The old derivation path must not survive, or the old
            # password plus the old salt would still reach the contents after a
            # later recovery rotated the PIN.
            _quiet_unlink(self._salt_path)
            _quiet_unlink(self._data_path)
            self._data_key = data_key
            self._active_data_path = new_path
            self._log("vault_upgrade", "success")
        except Exception as exc:  # pragma: no cover - disk-level failure
            self._log("vault_upgrade", "failed", str(exc)[:80])

    # -- internals --------------------------------------------------------
    def _plaintext(self) -> bytes:
        """The in-memory store, serialized for encryption."""
        return json.dumps(self._data).encode("utf-8")

    def _read_meta(self) -> Optional[dict[str, Any]]:
        try:
            with open(self._meta_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def _write_meta(self, meta: dict[str, Any]) -> None:
        os.makedirs(self._vault_dir, exist_ok=True)
        self._atomic_write(
            self._meta_path, json.dumps(meta, indent=2).encode("utf-8")
        )

    def _open_session(self) -> None:
        with self._state_lock:
            self._session = VaultSession(session_id=_secrets.token_hex(16))
            self._touch()
        _watch(self)

    def _require_session(self) -> VaultSession:
        if self._session is None:  # pragma: no cover - defensive
            raise VaultLocked("no active vault session")
        return self._session

    def _require_unlocked(self, *, touch: bool = True) -> None:
        with self._state_lock:
            if not self.is_unlocked:
                raise VaultLocked("the vault is locked; call unlock() first")
            if touch:
                self._touch()

    @staticmethod
    def _normalize(raw: object) -> dict[str, dict[str, str]]:
        """Coerce decrypted JSON into the expected two-section shape."""
        data: dict[str, dict[str, str]] = {"secrets": {}, "documents": {}}
        if isinstance(raw, dict):
            for section in ("secrets", "documents"):
                part = raw.get(section)
                if isinstance(part, dict):
                    data[section] = {str(k): str(v) for k, v in part.items()}
        return data

    def _flush(self) -> None:
        """Encrypt the in-memory store and write it to disk atomically."""
        if self._data_key is None or self._active_data_path is None:  # pragma: no cover
            raise VaultLocked("cannot flush a locked vault")
        # Sign-up key records another process changed are kept, not undone.
        self._merge_key_records_from_disk()
        token = Fernet(self._data_key).encrypt(self._plaintext())
        self._atomic_write(self._active_data_path, token)
        self._remember_key_records()

    @staticmethod
    def _atomic_write(path: str, data: bytes) -> None:
        """Write ``data`` to ``path`` via a temp file + replace (no partial writes)."""
        tmp = f"{path}.tmp-{_secrets.token_hex(4)}"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _log(self, action: str, result: str, details: str = "") -> None:
        """Record an event through the injected audit log, if any. Never raises.

        Only the secret *name* is ever recorded, never its value, and never the
        PIN or the recovery code.
        """
        logger = getattr(self._audit, "log", None)
        if not callable(logger):
            return
        try:
            from puffpai.web_search import key_safe_text
            # Checked whole for a key before the cut, never cut first.
            logger(action=action, user="vault", result=result,
                   details=key_safe_text(details, 100))
        except Exception:
            pass


def _generate_recovery_code() -> str:
    """Generate a recovery code, delegating to the wizard's canonical generator.

    :mod:`puffpai.zone_vault` owns the alphabet, the grouping and the documented
    entropy. It is imported lazily here so that importing the vault does not drag
    in the wizard. If it cannot be imported we raise rather than fall back to a
    second, weaker generator - a recovery code from an undocumented source is
    exactly the kind of quiet downgrade this build should not make.
    """
    try:
        from puffpai.zone_vault import generate_recovery_code
    except Exception as exc:  # pragma: no cover - packaging failure
        raise VaultError(
            "cannot generate a recovery code: puffpai.zone_vault is unavailable"
        ) from exc
    return generate_recovery_code()


def _quiet_unlink(path: str) -> None:
    """Remove a file if present; never raise."""
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# the watchdog: every open vault locks itself when left alone
# ---------------------------------------------------------------------------
#
# One daemon thread for the whole process, started the first time a vault
# opens. It holds open vaults weakly (a vault nobody refers to any more is
# simply dropped) and sleeps until the soonest one is due, at most 30 s.

_WATCH_LOCK = threading.Lock()
_WATCHED: "weakref.WeakSet[Vault]" = weakref.WeakSet()
_WATCH_WAKE = threading.Event()
_watch_thread: Optional[threading.Thread] = None
_WATCH_MAX_SLEEP = 30.0


def _watch(vault: "Vault") -> None:
    """Put an open vault under the watchdog (starting it if needed)."""
    global _watch_thread
    with _WATCH_LOCK:
        _WATCHED.add(vault)
        if _watch_thread is None or not _watch_thread.is_alive():
            _watch_thread = threading.Thread(
                target=_watch_loop, name="puffpai-vault-autolock", daemon=True)
            _watch_thread.start()
    _WATCH_WAKE.set()


def watch_once() -> float:
    """Lock every watched vault whose idle time is up. Returns how long the
    watchdog may sleep before the next one is due. Never raises."""
    with _WATCH_LOCK:
        vaults = list(_WATCHED)
    wait = _WATCH_MAX_SLEEP
    for vault in vaults:
        try:
            left = vault.idle_seconds_left()
        except Exception:  # pragma: no cover - a broken handle: drop it
            left = None
        if left is None:
            with _WATCH_LOCK:
                _WATCHED.discard(vault)
        else:
            wait = min(wait, left)
    return max(_IDLE_LOCK_MIN_SECONDS, wait)


def _watch_loop() -> None:  # pragma: no cover - timing; watch_once is tested
    while True:
        wait = watch_once()
        _WATCH_WAKE.wait(wait)
        _WATCH_WAKE.clear()


# ---------------------------------------------------------------------------
# where the vault should be
# ---------------------------------------------------------------------------

def place_record_path() -> Optional[str]:
    """``<state>/vault_place.json``, or None when there is no state folder."""
    try:
        from puffpai.config import get_state_dir
        return os.path.join(get_state_dir(), _PLACE_FILENAME)
    except Exception:
        return None


def read_place() -> Optional[str]:
    """The folder the install's vault was made (or last opened) in, or None.

    A record that cannot be read is read as "somewhere, unknown": the
    install's own folder is then treated as missing rather than as fresh, so
    a damaged record can never lead to a silent second vault: the configured
    folder is returned in that case.
    """
    path = place_record_path()
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        folder = data.get("vault_dir") if isinstance(data, dict) else None
        if isinstance(folder, str) and folder.strip() and os.path.isabs(folder):
            return folder
    except (OSError, ValueError):
        pass
    return _default_vault_dir()


def remember_place(vault_dir: str, *, fingerprint: Optional[str] = None) -> bool:
    """Record where the install's vault is, and (when known) which vault it
    is (:meth:`Vault.place_fingerprint`). Never raises; True when written.

    A record for the same folder keeps its fingerprint when none is given. A
    record for another folder never inherits one: a fingerprint is only ever
    written by a caller that has the vault open.
    """
    path = place_record_path()
    if not path:
        return False
    folder = os.path.abspath(vault_dir)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if (isinstance(data, dict) and data.get("vault_dir") == folder
                    and (not fingerprint or data.get("fingerprint") == fingerprint)):
                return False
    except (OSError, ValueError):
        pass
    record: dict[str, Any] = {"schema": _PLACE_SCHEMA, "vault_dir": folder,
                              "at": time.time()}
    if fingerprint:
        record["fingerprint"] = str(fingerprint)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Vault._atomic_write(path, json.dumps(record, indent=2).encode("utf-8"))
        return True
    except OSError:
        return False


def remembered_fingerprint() -> str:
    """The remembered vault's fingerprint, or ``""`` when none is known."""
    path = place_record_path()
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        fp = data.get("fingerprint") if isinstance(data, dict) else None
        return fp if isinstance(fp, str) else ""
    except (OSError, ValueError):
        return ""


def forget_place() -> bool:
    """Stop waiting for the remembered vault: delete the record (never the
    vault). Only for a choice made at this computer's command line. True
    when a record was removed."""
    path = place_record_path()
    if not path or not os.path.exists(path):
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def missing_words(vault: Any) -> str:
    """The owner's words when *vault*'s folder or drive is missing, else ''.

    For the places that say "there is no vault yet": when the vault is on a
    drive that is not plugged in, they say that instead, and never offer to
    make a new one. Never raises.
    """
    try:
        why = getattr(vault, "missing_reason", None)
        return str(why() or "") if callable(why) else ""
    except Exception:
        return ""


def _vault_files_present(folder: str) -> bool:
    return (os.path.isfile(os.path.join(folder, _META_FILENAME))
            or (os.path.isfile(os.path.join(folder, _SALT_FILENAME))
                and os.path.isfile(os.path.join(folder, _DATA_FILENAME))))


def _default_vault_dir() -> str:
    """Resolve the default vault directory from configuration.

    Falls back to ``vault_data`` under the current working directory if config
    is unavailable (e.g. in isolated unit tests).
    """
    try:
        from puffpai.config import get_config
        cfg = get_config()
        vd = cfg.paths.vault_data_dir
        return vd if os.path.isabs(vd) else os.path.join(cfg.DATA_DIR, vd)
    except Exception:
        return os.path.abspath("vault_data")


__all__ = [
    "META_SCHEMA",
    "Vault",
    "VaultSession",
    "VaultError",
    "AuthenticationError",
    "VaultLocked",
    "VaultThrottled",
    "WeakSecretError",
    "VaultMissing",
    "IDLE_LOCK_SECONDS",
    "VAULT_DRIVE_MISSING",
    "idle_lock_line",
    "idle_lock_minutes",
    "normalize_recovery_code",
    "missing_words",
    "forget_place",
    "read_place",
    "remember_place",
    "remembered_fingerprint",
    "validate_vault_secret",
    "watch_once",
    "weak_pattern",
]
