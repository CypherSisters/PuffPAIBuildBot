"""Read (or mint) the dashboard's pairing code, so the wizard can print it.

WHY THE WIZARD NEEDS THIS
-------------------------
The dashboard only answers devices that have been paired, and the pairing code
is deliberately not reachable over HTTP by an unpaired client -- that is the
whole point of the gate. So the code has to arrive out of band, and the terminal
the owner is already sitting in front of is the one out-of-band channel that is
guaranteed to exist on a self-hosted appliance.

Each build keeps its pairing state in its OWN state folder:
``<state>/pairing/pairing.json`` (and the paired-device list beside it,
``devices.json``). It used to live in the code copy's
``dashboard/data/pairing.json``, so every build run from one code copy shared
one pairing secret: a device paired to one bot was paired to all of them.
The dashboard finds the same folder through
``PUFFPAI_STATE_DIR`` (which the installer's launchers, ``puffpai start`` and
the install pointer all provide) and resolves it with the same rules as
:func:`state_path`; ``tests/test_per_build_secrets.py`` runs the Python and
the Node resolvers over the same folders.

The wizard finishes BEFORE the dashboard has ever been started, so this module
creates the state itself when it is missing, using exactly the format
``dashboard/app/_lib/server/pairing.ts`` reads:

    {"version": 1, "secret": <64 hex>, "code": <8 chars>,
     "createdAt": <iso>, "codeRotatedAt": <iso>}

The format is duplicated here rather than shared, because the two runtimes have
no common library. The duplication is not left to trust: the dashboard's
``pairing-cookie.test.mjs`` mints a state with the Node helper and redeems it
through the TypeScript module, and a Python test does the same round trip in
``tests/test_wizard_device_pairing_code.py``.

EXISTING INSTALLS KEEP THEIR PAIRED DEVICES
-------------------------------------------
A code copy that already has the old ``dashboard/data/pairing.json`` keeps it
working for ONE build: the first build that asks for it after this change
records itself as its owner in ``dashboard/data/pairing-owner.json`` (written
whole to a temporary file and then linked into place, which fails if the file
already exists: two builds starting at once cannot both win, and nobody ever
reads a half-written record), and
from then on that build keeps using the old file where it is, so its paired
devices stay paired and nothing is copied. Every other build gets its own
state. A single-build install (the installer's layout, or a checkout with one
bot) therefore sees no change at all. To give that build a fresh secret of its
own, move the old file aside; its devices then pair again once.

WHAT THIS MODULE MUST NEVER DO
------------------------------
Print, return, or log the secret. It returns the CODE and its expiry, nothing
else. And it never raises into the wizard: a box where the dashboard directory
does not exist yet, or where the data directory is not writable by the account
running setup, still gets a finished wizard and a printed instruction telling it
where to get the code later.

puff puff pass
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

# Keep in sync with CODE_ALPHABET in dashboard/app/_lib/server/pairing.ts.
# Digits + uppercase, minus the confusables 0/O/1/I.
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 8
SECRET_BYTES = 32
# Keep in sync with PAIR_CODE_TTL_SEC.
CODE_TTL = timedelta(hours=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def random_code() -> str:
    """Eight characters, uniform over the alphabet.

    ``secrets.choice`` is used rather than a modulo over random bytes: it is the
    stdlib's own uniform choice and there is no rejection bias to reason about.
    """
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def format_code(code: str) -> str:
    """``7XK4P2M9`` -> ``7XK4-P2M9``."""
    return f"{code[:4]}-{code[4:]}" if len(code) == CODE_LENGTH else code


#: The folder, inside a build's state folder, that holds its pairing state.
PAIRING_DIR = "pairing"
#: The old shared file's owner record, beside it in ``dashboard/data``.
OWNER_FILE = "pairing-owner.json"


def per_build_path(state: Optional[str] = None) -> str:
    """``<state>/pairing/pairing.json`` for this build (or *state*)."""
    from puffpai.security import build_identity
    return os.path.join(build_identity.state_dir(state), PAIRING_DIR,
                        "pairing.json")


def _dashboard_dirs(start: Optional[str] = None) -> list[str]:
    return [
        os.path.join(start or os.getcwd(), "dashboard"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "dashboard"),
    ]


def legacy_path(start: Optional[str] = None) -> Optional[str]:
    """The code copy's old shared file, ``<dashboard>/data/pairing.json``.

    None when there is no dashboard folder at all (a headless install).
    """
    for base in _dashboard_dirs(start):
        if os.path.isdir(base):
            return os.path.join(base, "data", "pairing.json")
    return None


#: An owner record read back as EMPTY and younger than this is still being
#: written by another process (an older writer that creates, then writes).
CLAIM_STALE_SECONDS = 30.0
#: How long a reader waits for such a record to be finished.
CLAIM_WAIT_SECONDS = 2.0
#: :func:`_read_owner`'s answer for "another process is writing it now".
IN_PROGRESS = "in-progress"


def _read_owner(owner_file: str) -> Optional[str]:
    """The build id in an owner record; '' for a damaged one; None if absent;
    :data:`IN_PROGRESS` for an empty one that was just created."""
    try:
        with open(owner_file, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None
    except OSError:
        return ""
    if not raw.strip():
        try:
            age = time.time() - os.stat(owner_file).st_mtime
        except OSError:
            return ""
        return IN_PROGRESS if age < CLAIM_STALE_SECONDS else ""
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    build = data.get("build") if isinstance(data, dict) else None
    return build if isinstance(build, str) and len(build) == 16 else ""


def _read_owner_settled(owner_file: str) -> Optional[str]:
    """:func:`_read_owner`, waiting briefly while a claim is being written."""
    deadline = time.monotonic() + CLAIM_WAIT_SECONDS
    owner = _read_owner(owner_file)
    while owner == IN_PROGRESS and time.monotonic() < deadline:
        time.sleep(0.05)
        owner = _read_owner(owner_file)
    return owner


class ClaimInProgress(Exception):
    """Another process of a build is recording itself as the old file's owner."""


def _claim(owner_file: str, record: str) -> bool:
    """Create *owner_file* holding *record*, whole, only if it does not exist.

    The record is written to a temporary file first and linked into place, so
    the owner file never exists half-written. Where links are not possible
    (some file systems) it falls back to an exclusive create and one write;
    readers then wait out the moment the file is empty
    (:func:`_read_owner_settled`). True when this call created it.
    """
    folder = os.path.dirname(owner_file)
    tmp = os.path.join(folder, f".{OWNER_FILE}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(record)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        try:
            os.link(tmp, owner_file)
            return True
        except FileExistsError:
            return False
        except (OSError, NotImplementedError, AttributeError):
            pass  # no links here: the exclusive create below
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    fd = os.open(owner_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(record)
    return True


def keeps_legacy(legacy: str, state: Optional[str] = None) -> bool:
    """May this build keep using the code copy's old pairing file?

    Yes when it already owns it, or when nobody does yet and it becomes the
    owner now. The owner record appears whole or not at all (:func:`_claim`),
    so of two builds starting at the same moment exactly one wins. A damaged
    owner record answers no for everybody: fail closed, the build gets its
    own state. A record another process is still writing is waited for
    briefly; if it is still not finished, :class:`ClaimInProgress` is raised
    rather than guessing (otherwise a second process of the SAME build reads
    the empty file, makes its own state and abandons the old one).
    """
    from puffpai.security import build_identity
    me = build_identity.build_id(state)
    owner_file = os.path.join(os.path.dirname(legacy), OWNER_FILE)
    owner = _read_owner_settled(owner_file)
    if owner is None:
        record = json.dumps({"version": 1, "build": me,
                             "claimedAt": _now().isoformat()}, indent=2)
        try:
            if _claim(owner_file, record):
                return True
        except FileExistsError:
            pass
        except OSError:
            return False
        owner = _read_owner_settled(owner_file)
    if owner == IN_PROGRESS:
        raise ClaimInProgress()
    return owner == me


def state_path(start: Optional[str] = None,
               state: Optional[str] = None) -> Optional[str]:
    """Where THIS build's pairing state lives, or None if we cannot tell.

    Resolution order, most explicit first:

    1. ``PUFF_PAIRING_STATE`` -- the same override the dashboard itself
       honours, so a deployment that moved the file stays consistent.
    2. ``<state>/pairing/pairing.json`` when it exists: this build's own.
    3. the code copy's old ``dashboard/data/pairing.json``, only for the one
       build that owns it (see :func:`keeps_legacy`), so an existing install
       keeps its paired devices.
    4. ``<state>/pairing/pairing.json``, to be created: every other build.

    Returns None when no dashboard directory can be found, which is a real
    situation (a headless install of the Python side only) and not an error,
    and also while another process of this build is still recording itself
    as the old file's owner: nothing is created then, so the build's state is
    never split in two.

    Records the build's id for the dashboard on the way
    (:func:`puffpai.security.build_identity.record`).
    ``start`` is where to look for the dashboard folder (default: the current
    folder, then the one beside the package); ``state`` overrides the state
    folder for tests.
    """
    override = os.environ.get("PUFF_PAIRING_STATE")
    if override:
        return override
    legacy = legacy_path(start)
    if legacy is None:
        return None
    from puffpai.security import build_identity
    build_identity.record(state)
    own = per_build_path(state)
    if os.path.isfile(own):
        return own
    try:
        if os.path.isfile(legacy) and keeps_legacy(legacy, state):
            return legacy
    except ClaimInProgress:
        return None
    return own


def _write_state(path: str, state: dict) -> None:
    """Atomic 0600 write, matching writeState() in pairing.ts.

    The secret lives in this file and is the only thing standing between a
    device on the network and the appliance's API key, so a truncated or
    world-readable intermediate is not acceptable even for the instant a rename
    would take. The 0700 on the directory and 0600 on the file are no-ops on
    Windows and real everywhere else.
    """
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass  # Windows: chmod is a no-op / may throw. Not fatal.
    os.replace(tmp, path)


def _valid(state: object) -> bool:
    if not isinstance(state, dict):
        return False
    secret = state.get("secret")
    code = state.get("code")
    return (
        isinstance(secret, str)
        and len(secret) == 64
        and all(c in "0123456789abcdef" for c in secret)
        and isinstance(code, str)
        and len(code) == CODE_LENGTH
    )


def ensure_code(path: Optional[str] = None) -> Optional[dict]:
    """Return ``{"code", "expires_at", "created", "rotated", "path"}`` or None.

    Creates the state file if it does not exist, and rotates an expired code so
    that what the wizard prints is always redeemable. Never touches the secret:
    a rotation preserves every other field verbatim, so any device that is
    already paired stays paired.

    Returns None -- rather than raising -- for every failure: no dashboard
    directory, an unwritable data directory, a malformed existing file. A wizard
    that crashes on its last screen over a display convenience would be a worse
    outcome than an owner running one extra command.

    A MALFORMED existing file is deliberately left alone. Overwriting it would
    mint a fresh secret and silently un-pair every device on the appliance, which
    is never the right way to recover from a parse error.
    """
    target = path or state_path()
    if not target:
        return None

    created = False
    rotated = False
    state = None
    try:
        with open(target, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except FileNotFoundError:
        state = None
    except (OSError, ValueError):
        return None

    if state is not None and not _valid(state):
        return None

    now = _now()
    if state is None:
        stamp = now.isoformat()
        state = {
            "version": 1,
            "secret": secrets.token_hex(SECRET_BYTES),
            "code": random_code(),
            "createdAt": stamp,
            "codeRotatedAt": stamp,
        }
        created = True
    else:
        try:
            rotated_at = datetime.fromisoformat(
                str(state.get("codeRotatedAt", "")).replace("Z", "+00:00")
            )
            if rotated_at.tzinfo is None:
                rotated_at = rotated_at.replace(tzinfo=timezone.utc)
        except ValueError:
            # Unparseable rotation time counts as expired. Fail closed, and it
            # self-heals right here.
            rotated_at = now - CODE_TTL
        if now >= rotated_at + CODE_TTL:
            state = dict(state)
            state["code"] = random_code()
            state["codeRotatedAt"] = now.isoformat()
            rotated = True

    if created or rotated:
        try:
            _write_state(target, state)
        except OSError:
            return None

    try:
        expires = datetime.fromisoformat(
            str(state["codeRotatedAt"]).replace("Z", "+00:00")
        )
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        expires = expires + CODE_TTL
    except ValueError:
        return None

    return {
        "code": format_code(str(state["code"])),
        "expires_at": expires,
        "created": created,
        "rotated": rotated,
        "path": target,
    }
