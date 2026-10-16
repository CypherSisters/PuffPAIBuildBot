"""Write-only vault inbox: personal details can be filed while the vault is locked.

The problem
-----------
The vault is locked most of the time (it is locked at process start, and only
the owner's PIN opens it). Chat notices personal details at any moment. Before
this module a detected phone number or address was either written into the
vault (only if it happened to be unlocked) or dropped. Dropping loses the
owner's data; writing it in plain text breaks the vault's promise.

The design
----------
A sealed-box inbox, the same shape as libsodium's ``crypto_box_seal``, built
from the ``cryptography`` package the vault already depends on:

* When the vault is open, :func:`ensure_inbox` makes sure an X25519 key pair
  exists. The **private** key is stored *inside* the vault, under a reserved
  secret name, so it is encrypted by the vault's data key (Fernet) and is
  re-keyed with everything else on recovery. The **public** key is written in
  the clear beside the vault (``<vault_dir>.inbox/inbox.pub.json``).
* :func:`deposit` needs only the public key. For each item it makes a fresh
  ephemeral X25519 key, does ECDH with the inbox public key, derives a 32-byte
  key with HKDF-SHA256 (bound to both public keys), and encrypts the JSON item
  with Fernet (AES-128-CBC + HMAC-SHA256, the vault's own primitive). Only the
  ephemeral public key and the Fernet token are written. The plain item never
  touches disk, and nothing on disk can open a deposit without the private key.
* :func:`drain` runs when the vault is opened: it opens every deposit with the
  private key, appends the items to the vault's ``auto_detected_pii`` record,
  and deletes each deposit only after the vault write succeeded.

What this protects against, and what it does not
------------------------------------------------
* Protects: someone who copies the disk while the vault is locked cannot read
  what was deposited (they would need the PIN or the recovery code).
* Key swap: someone who can WRITE beside the vault could swap the
  public key so later deposits go to a key they hold. Three things now stand
  in the way:

  - the vault records the hash of its inbox public key INSIDE itself
    (reserved name :data:`PUBLIC_HASH_NAME`), and every open writes a second
    copy of that hash into the vault's own folder
    (``<vault_dir>/inbox_key_check.json``);
  - while locked, :func:`deposit_status` seals only when the public key's hash
    matches that second copy. If it cannot be confirmed (missing, different,
    unreadable) nothing is sealed: the item is HELD in this process's memory,
    the caller keeps only a placeholder, and the owner is told in plain words.
    Held items are filed at the next unlock in this process; a restart before
    that loses them, and the notice says so;
  - every sealed box names the key it was sealed to (the public key's hash).
    At unlock, a box sealed to any other key, or one that will not open, is
    set aside and the owner is told how many, in plain words (an announcement
    and :func:`owner_notices`), never only a log line.

  What this still does not stop: someone who can write BOTH the public key and
  its second copy (the same user account can) gets later deposits sealed to
  their key until the next unlock, which then reports them. The inbox is a
  guard against a careless or partial tamper, not against a same-user attacker.
* Does not prevent: anyone who can read the public key can seal a deposit (that
  is what makes the inbox write-only). Fake deposits are filed as "noticed in
  chat" records; they are data only and are never acted on. Each one is
  size-checked again when it is opened, and at most ``MAX_PENDING`` can wait,
  so a flood fills neither the disk nor the vault.
* Leaks: the number of waiting items and the time each was sealed (from the
  file names). Never what is in them.
* If the vault was created before this module existed, it has no inbox key
  until it is opened once. Until then :func:`deposit` returns False and the
  caller must tell the person the detail was not kept. A vault opened before
  the second copy existed has an unconfirmed key until its next open, so it
  holds instead of sealing (fails closed).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

SCHEMA = "puffpai_vault_inbox/v1"
#: Reserved secret name inside the vault that holds the inbox private key.
PRIVATE_KEY_NAME = "__puffpai.inbox_private_key"
#: Reserved name inside the vault that holds the hash of the inbox public key.
PUBLIC_HASH_NAME = "__puffpai.inbox_public_sha256"
#: The second copy of that hash, written by the vault into its own folder.
CONFIRM_FILENAME = "inbox_key_check.json"
#: Plain-words notices for the owner (counts and times only, never values).
NOTICES_FILENAME = "owner_notices.json"
MAX_NOTICES = 20
#: Items held in memory while the inbox key cannot be confirmed.
MAX_HELD = 200

SEALED, HELD, NO_KEY, TOO_BIG, FULL = "sealed", "held", "no_key", "too_big", "full"
#: The vault record that detected personal details are appended to.
PII_RECORD_NAME = "auto_detected_pii"
_PUB_FILENAME = "inbox.pub.json"
_BOX_SUFFIX = ".box"
_UNREADABLE_DIR = "unreadable"
_HKDF_INFO = b"puffpai-vault-inbox/v1"
#: Upper bound on one deposit, so a runaway caller cannot fill the disk.
_MAX_ITEM_BYTES = 64 * 1024
#: A sealed box on disk is the item plus base64 and Fernet overhead.
_MAX_BOX_BYTES = 2 * _MAX_ITEM_BYTES + 4096
#: Most items that may wait for the next unlock. Past this, deposit refuses
#: (and the caller tells the person the detail was not kept).
MAX_PENDING = 2000


def inbox_dir(vault_dir: str) -> str:
    """The inbox folder that sits beside ``vault_dir``."""
    return os.path.abspath(vault_dir).rstrip("\\/") + ".inbox"


def _raw_public(key: X25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw,
                            serialization.PublicFormat.Raw)


def _raw_private(key: X25519PrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.Raw,
                             serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())


def _fernet_key(shared: bytes, eph_pub: bytes, recipient_pub: bytes) -> bytes:
    derived = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=_HKDF_INFO + eph_pub + recipient_pub,
    ).derive(shared)
    return base64.urlsafe_b64encode(derived)


def _atomic_write(path: str, data: bytes) -> None:
    tmp = f"{path}.tmp-{secrets.token_hex(4)}"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows ignores most mode bits
        pass


def public_sha256(raw_pub: bytes) -> str:
    """The hash that names an inbox public key."""
    return hashlib.sha256(bytes(raw_pub)).hexdigest()


def _confirm_path(vault_dir: str) -> str:
    return os.path.join(os.path.abspath(vault_dir), CONFIRM_FILENAME)


def _read_confirm(vault_dir: str) -> Optional[str]:
    try:
        with open(_confirm_path(vault_dir), "r", encoding="utf-8") as fh:
            record = json.load(fh)
        value = record.get("public_sha256") if isinstance(record, dict) else None
        return value if isinstance(value, str) and len(value) == 64 else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _write_confirm(vault_dir: str, digest: str) -> None:
    os.makedirs(os.path.abspath(vault_dir), exist_ok=True)
    _atomic_write(_confirm_path(vault_dir), json.dumps({
        "schema": SCHEMA, "public_sha256": digest,
        "written_at": time.time()}).encode("utf-8"))


def key_status(vault_dir: str) -> str:
    """``"ok"``, ``"no_key"`` or ``"unconfirmed"`` for the inbox key on disk.

    ``ok`` only when the public key beside the vault hashes to the copy the
    vault itself wrote into its own folder.
    """
    raw = _read_public(vault_dir)
    if raw is None:
        return NO_KEY
    confirm = _read_confirm(vault_dir)
    return "ok" if confirm is not None and confirm == public_sha256(raw) \
        else "unconfirmed"


def _read_public(vault_dir: str) -> Optional[bytes]:
    path = os.path.join(inbox_dir(vault_dir), _PUB_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            record = json.load(fh)
        if not isinstance(record, dict) or record.get("schema") != SCHEMA:
            return None
        raw = base64.b64decode(str(record.get("public_key", "")))
        return raw if len(raw) == 32 else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _write_public(vault_dir: str, raw_pub: bytes) -> None:
    folder = inbox_dir(vault_dir)
    os.makedirs(folder, exist_ok=True)
    _atomic_write(os.path.join(folder, _PUB_FILENAME), json.dumps({
        "schema": SCHEMA,
        "public_key": base64.b64encode(raw_pub).decode("ascii"),
        "written_at": time.time(),
    }).encode("utf-8"))


def ensure_inbox(vault) -> bool:
    """Make sure an open vault has an inbox key pair. Returns True when ready.

    Requires an unlocked vault. Creates the key pair the first time; on later
    opens it re-derives the public key from the stored private key and rewrites
    the public file if it is missing or does not match (a swapped key is logged
    as a warning, never trusted).
    """
    if vault is None or not getattr(vault, "is_unlocked", False):
        return False
    stored = vault._reserved_get(PRIVATE_KEY_NAME)
    if stored:
        try:
            private = X25519PrivateKey.from_private_bytes(
                base64.b64decode(stored))
        except (ValueError, TypeError):
            logger.warning("Vault inbox: stored inbox key is unreadable; "
                           "issuing a new one")
            stored = None
    if not stored:
        private = X25519PrivateKey.generate()
        vault._reserved_set(PRIVATE_KEY_NAME, base64.b64encode(
            _raw_private(private)).decode("ascii"))
    expected = _raw_public(private.public_key())
    digest = public_sha256(expected)
    if vault._reserved_get(PUBLIC_HASH_NAME) != digest:
        vault._reserved_set(PUBLIC_HASH_NAME, digest)
    on_disk = _read_public(vault.vault_dir)
    confirm = _read_confirm(vault.vault_dir)
    changed = False
    if on_disk != expected:
        if on_disk is not None:
            changed = True
            logger.warning("Vault inbox: the public key beside the vault did "
                           "not match the vault's own key; it was replaced")
        _write_public(vault.vault_dir, expected)
    if confirm != digest:
        if confirm is not None:
            changed = True
            logger.warning("Vault inbox: the key check beside the vault did "
                           "not match the vault's own record; it was replaced")
        _write_confirm(vault.vault_dir, digest)
    if changed:
        _notify(vault.vault_dir, "key_replaced", KEY_REPLACED_TEXT)
    return True


def can_accept(vault_dir: str) -> bool:
    """True when a public key is in place, so :func:`deposit` can work."""
    return _read_public(vault_dir) is not None


def deposit(vault_dir: str, item: dict[str, Any]) -> bool:
    """Seal ``item`` to the inbox. Needs no PIN. True only when it was sealed.

    Kept for older callers; :func:`deposit_status` says what happened instead
    (an item HELD in memory returns False here).
    """
    return deposit_status(vault_dir, item) == SEALED


def deposit_status(vault_dir: str, item: dict[str, Any]) -> str:
    """Seal ``item`` to the inbox if the inbox key can be trusted.

    Returns :data:`SEALED`, :data:`HELD` (the key could not be confirmed, so
    the item is kept in this process's memory until the next unlock),
    :data:`NO_KEY`, :data:`TOO_BIG` or :data:`FULL`. Never writes the item in
    the clear.
    """
    plaintext = json.dumps(item, ensure_ascii=False).encode("utf-8")
    if len(plaintext) > _MAX_ITEM_BYTES:
        return TOO_BIG
    status = key_status(vault_dir)
    if status == NO_KEY:
        return NO_KEY
    if status != "ok":
        # The public key beside the vault is not the one the vault wrote down:
        # never seal to it. Hold the item where only this process can see it.
        if not _hold(vault_dir, item):
            return FULL
        _notify(vault_dir, "unconfirmed", UNCONFIRMED_TEXT, once=True)
        return HELD
    recipient = _read_public(vault_dir)
    if recipient is None:  # pragma: no cover - removed between two reads
        return NO_KEY
    if pending_count(vault_dir) >= MAX_PENDING:
        logger.warning("Vault inbox: %d items already waiting; not taking more "
                       "until the vault is opened", MAX_PENDING)
        return FULL
    ephemeral = X25519PrivateKey.generate()
    eph_pub = _raw_public(ephemeral.public_key())
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(recipient))
    token = Fernet(_fernet_key(shared, eph_pub, recipient)).encrypt(plaintext)
    folder = inbox_dir(vault_dir)
    os.makedirs(folder, exist_ok=True)
    name = f"{int(time.time() * 1000):013d}-{secrets.token_hex(6)}{_BOX_SUFFIX}"
    _atomic_write(os.path.join(folder, name), json.dumps({
        "schema": SCHEMA,
        "ephemeral_public_key": base64.b64encode(eph_pub).decode("ascii"),
        "token": token.decode("ascii"),
        # Which key this was sealed to, so an unlock can tell a swap apart.
        "to": public_sha256(recipient),
    }).encode("utf-8"))
    return SEALED


def pending_count(vault_dir: str) -> int:
    """How many sealed items are waiting. Reads file names only, never contents."""
    try:
        return sum(1 for n in os.listdir(inbox_dir(vault_dir))
                   if n.endswith(_BOX_SUFFIX))
    except OSError:
        return 0


def append_pii_record(vault, record: dict[str, Any]) -> None:
    """Append one record to the vault's ``auto_detected_pii`` list."""
    try:
        existing = vault.retrieve(PII_RECORD_NAME)
        records = json.loads(existing) if existing else []
        if not isinstance(records, list):
            records = []
    except Exception:
        records = []
    records.append(record)
    vault.store(PII_RECORD_NAME, json.dumps(records, ensure_ascii=False))


def drain(vault) -> dict[str, int]:
    """Open every waiting item into the (open) vault. Returns counts.

    ``{"filed", "unreadable", "wrong_key", "held_filed"}``. Each deposit is
    deleted only after the vault write that holds it has succeeded. A deposit
    that will not open, or that names a key other than this vault's, is moved
    to ``unreadable/`` rather than deleted, and the owner is told (see
    :func:`on_vault_opened`). Items held in memory while the key could not be
    confirmed are filed too.
    """
    counts = {"filed": 0, "unreadable": 0, "wrong_key": 0, "held_filed": 0}
    if vault is None or not getattr(vault, "is_unlocked", False):
        return counts
    stored = vault._reserved_get(PRIVATE_KEY_NAME)
    if not stored:
        return counts
    try:
        private = X25519PrivateKey.from_private_bytes(base64.b64decode(stored))
    except (ValueError, TypeError):
        return counts
    recipient = _raw_public(private.public_key())
    ours = public_sha256(recipient)
    for item in _take_held(vault.vault_dir):
        item.setdefault("filed_from", "held_while_locked")
        append_pii_record(vault, item)
        counts["held_filed"] += 1
    folder = inbox_dir(vault.vault_dir)
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(_BOX_SUFFIX))
    except OSError:
        return counts
    for name in names:
        path = os.path.join(folder, name)
        try:
            if os.path.getsize(path) > _MAX_BOX_BYTES:
                raise ValueError("sealed item is larger than any real one")
            with open(path, "r", encoding="utf-8") as fh:
                box = json.load(fh)
            if box.get("to") not in (None, ours):
                counts["wrong_key"] += 1
                raise ValueError("sealed to a key that is not this vault's")
            eph_pub = base64.b64decode(str(box["ephemeral_public_key"]))
            shared = private.exchange(X25519PublicKey.from_public_bytes(eph_pub))
            plain = Fernet(_fernet_key(shared, eph_pub, recipient)).decrypt(
                str(box["token"]).encode("ascii"))
            item = json.loads(plain.decode("utf-8"))
            if not isinstance(item, dict):
                raise ValueError("not a record")
        except (OSError, KeyError, ValueError, TypeError, InvalidToken,
                json.JSONDecodeError):
            counts["unreadable"] += 1
            _move_aside(folder, name)
            continue
        item.setdefault("filed_from", "locked_inbox")
        append_pii_record(vault, item)
        try:
            os.remove(path)
        except OSError:  # pragma: no cover - the record is already filed
            pass
        counts["filed"] += 1
    if counts["filed"] or counts["unreadable"] or counts["held_filed"]:
        logger.info("Vault inbox: filed %d waiting item(s), %d held, %d "
                    "unreadable (%d for another key)", counts["filed"],
                    counts["held_filed"], counts["unreadable"],
                    counts["wrong_key"])
    return counts


def _move_aside(folder: str, name: str) -> None:
    target = os.path.join(folder, _UNREADABLE_DIR)
    try:
        os.makedirs(target, exist_ok=True)
        os.replace(os.path.join(folder, name), os.path.join(target, name))
    except OSError:  # pragma: no cover
        pass


def on_vault_opened(vault) -> None:
    """Hook the vault calls after every create / unlock / recovery. Never raises."""
    try:
        ensure_inbox(vault)
        counts = drain(vault)
        _report_open(vault.vault_dir, counts)
    except Exception:  # pragma: no cover - must never block opening the vault
        logger.exception("Vault inbox: could not prepare or empty the inbox")


# ---------------------------------------------------------------------------
# holding in memory (the key could not be confirmed)
# ---------------------------------------------------------------------------

_HELD: dict[str, list[dict[str, Any]]] = {}
_HELD_LOCK = threading.Lock()


def _hold(vault_dir: str, item: dict[str, Any]) -> bool:
    key = os.path.abspath(vault_dir)
    with _HELD_LOCK:
        queue = _HELD.setdefault(key, [])
        if len(queue) >= MAX_HELD:
            return False
        queue.append(dict(item))
    return True


def _take_held(vault_dir: str) -> list[dict[str, Any]]:
    with _HELD_LOCK:
        return _HELD.pop(os.path.abspath(vault_dir), [])


def held_count(vault_dir: str) -> int:
    """How many details are held in memory for this vault (never their values)."""
    with _HELD_LOCK:
        return len(_HELD.get(os.path.abspath(vault_dir), []))


# ---------------------------------------------------------------------------
# telling the owner, in plain words
# ---------------------------------------------------------------------------

KEY_REPLACED_TEXT = (
    "The lock your bot uses for personal details it saves while your vault is "
    "locked had been changed by something on this computer. Your vault put its "
    "own lock back.")
UNCONFIRMED_TEXT = (
    "I could not confirm the lock your bot uses for personal details it saves "
    "while your vault is locked, so I am holding them in memory instead. Open "
    "your vault with your PIN to file them. If this computer restarts first, "
    "they are gone.")


def wrong_key_text(n: int) -> str:
    what = "1 personal detail was" if n == 1 else f"{n} personal details were"
    return (f"While your vault was locked, {what} locked with a key that is not "
            f"your vault's, so {'it' if n == 1 else 'they'} could not be opened "
            f"and {'was' if n == 1 else 'were'} set aside. Something on this "
            f"computer may have changed a file next to your vault, and whoever "
            f"did it may be able to read {'it' if n == 1 else 'them'}.")


def held_filed_text(n: int) -> str:
    what = "1 personal detail" if n == 1 else f"{n} personal details"
    return (f"I filed {what} in your vault that I was holding while it was "
            f"locked.")


_NOTIFIED_ONCE: set[tuple[str, str]] = set()


def _notices_path(vault_dir: str) -> str:
    return os.path.join(inbox_dir(vault_dir), NOTICES_FILENAME)


def owner_notices(vault_dir: str) -> list[dict[str, Any]]:
    """The owner's inbox notices, newest last: ``[{"kind", "text", "at"}]``."""
    try:
        with open(_notices_path(vault_dir), "r", encoding="utf-8") as fh:
            rows = json.load(fh)
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def _notify(vault_dir: str, kind: str, text: str, *, once: bool = False) -> None:
    """Record a plain notice and put it on the owner's card. Never raises."""
    key = (os.path.abspath(vault_dir), kind)
    if once and key in _NOTIFIED_ONCE:
        return
    _NOTIFIED_ONCE.add(key)
    try:
        rows = owner_notices(vault_dir)
        rows.append({"kind": kind, "text": text, "at": time.time()})
        folder = inbox_dir(vault_dir)
        os.makedirs(folder, exist_ok=True)
        _atomic_write(_notices_path(vault_dir),
                      json.dumps(rows[-MAX_NOTICES:]).encode("utf-8"))
    except Exception:  # pragma: no cover - a notice must never break a caller
        logger.warning("Vault inbox: could not record a notice for the owner")
    try:
        from puffpai import announcer
        announcer.announce(text, source="vault", title="Your vault")
    except Exception:  # pragma: no cover
        logger.warning("Vault inbox: could not announce a notice")


def _report_open(vault_dir: str, counts: dict[str, int]) -> None:
    lost = int(counts.get("unreadable") or 0)
    if lost:
        _notify(vault_dir, "wrong_key", wrong_key_text(lost))
    if counts.get("held_filed"):
        _notify(vault_dir, "held_filed", held_filed_text(counts["held_filed"]))
    # A fresh unlock may need to warn again later.
    _NOTIFIED_ONCE.discard((os.path.abspath(vault_dir), "unconfirmed"))


# ---------------------------------------------------------------------------
# whole files (an upload with a key or a personal detail, vault locked)
# ---------------------------------------------------------------------------
#
# The same sealing as :func:`deposit_status` (a fresh X25519 key, HKDF bound
# to both public keys, Fernet), for a whole uploaded file, so its plain bytes
# need not stay on disk until the owner opens the vault. Kept apart from the
# detail boxes (``<inbox>/files/*.sealed``): :func:`drain` never files them
# into the details record; the caller opens them with :func:`open_sealed_file`
# once the vault is open and puts the file back (knowledge/my_data.py). Only
# when the inbox key is confirmed; otherwise nothing is sealed and the caller
# must not keep the file either.

_FILES_DIR = "files"
_FILE_SUFFIX = ".sealed"
_FILE_NAME = re.compile(r"^\d{13}-[0-9a-f]{12}\.sealed$")
#: Largest file sealed (uploads are capped at 25 MB before they get here).
MAX_FILE_BYTES = 64 * 1024 * 1024


def _files_dir(vault_dir: str) -> str:
    return os.path.join(inbox_dir(vault_dir), _FILES_DIR)


def seal_file(vault_dir: str, data: bytes, meta: dict[str, Any]) -> tuple[str, str]:
    """Seal ``data`` (and ``meta``, e.g. its name) without the PIN.

    Returns ``(status, sealed file name)``; status is :data:`SEALED`,
    :data:`NO_KEY`, :data:`TOO_BIG` or ``"unconfirmed"``
    (the public key beside the vault is not the one the vault wrote down:
    nothing is sealed to it). Never writes the file in the clear."""
    if len(data) > MAX_FILE_BYTES:
        return TOO_BIG, ""
    status = key_status(vault_dir)
    if status == NO_KEY:
        return NO_KEY, ""
    if status != "ok":
        _notify(vault_dir, "unconfirmed", UNCONFIRMED_TEXT, once=True)
        return "unconfirmed", ""
    recipient = _read_public(vault_dir)
    if recipient is None:  # pragma: no cover - removed between two reads
        return NO_KEY, ""
    plaintext = json.dumps(meta, ensure_ascii=False).encode("utf-8") + b"\n" + bytes(data)
    ephemeral = X25519PrivateKey.generate()
    eph_pub = _raw_public(ephemeral.public_key())
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(recipient))
    token = Fernet(_fernet_key(shared, eph_pub, recipient)).encrypt(plaintext)
    folder = _files_dir(vault_dir)
    os.makedirs(folder, exist_ok=True)
    name = f"{int(time.time() * 1000):013d}-{secrets.token_hex(6)}{_FILE_SUFFIX}"
    _atomic_write(os.path.join(folder, name), json.dumps({
        "schema": SCHEMA,
        "ephemeral_public_key": base64.b64encode(eph_pub).decode("ascii"),
        "token": token.decode("ascii"),
        "to": public_sha256(recipient),
    }).encode("utf-8"))
    return SEALED, name


def sealed_files(vault_dir: str) -> list[str]:
    """Names of the sealed files waiting (never their contents)."""
    try:
        return sorted(n for n in os.listdir(_files_dir(vault_dir)) if _FILE_NAME.match(n))
    except OSError:
        return []


def open_sealed_file(vault, name: str) -> Optional[tuple[dict[str, Any], bytes]]:
    """``(meta, data)`` for one sealed file, with the vault open; None when it
    cannot be opened (vault locked, another key, damaged)."""
    if not _FILE_NAME.match(str(name or "")) or not getattr(vault, "is_unlocked", False):
        return None
    stored = vault._reserved_get(PRIVATE_KEY_NAME)
    if not stored:
        return None
    try:
        private = X25519PrivateKey.from_private_bytes(base64.b64decode(stored))
        recipient = _raw_public(private.public_key())
        with open(os.path.join(_files_dir(vault.vault_dir), name), "r", encoding="utf-8") as fh:
            box = json.load(fh)
        if box.get("to") not in (None, public_sha256(recipient)):
            return None
        eph_pub = base64.b64decode(str(box["ephemeral_public_key"]))
        shared = private.exchange(X25519PublicKey.from_public_bytes(eph_pub))
        plain = Fernet(_fernet_key(shared, eph_pub, recipient)).decrypt(
            str(box["token"]).encode("ascii"))
        head, _, data = plain.partition(b"\n")
        meta = json.loads(head.decode("utf-8"))
        return (meta if isinstance(meta, dict) else {}), data
    except (OSError, KeyError, ValueError, TypeError, InvalidToken):
        return None


def remove_sealed_file(vault_dir: str, name: str) -> None:
    if _FILE_NAME.match(str(name or "")):
        try:
            os.remove(os.path.join(_files_dir(vault_dir), name))
        except OSError:
            pass


__all__ = [
    "seal_file", "sealed_files", "open_sealed_file", "remove_sealed_file",
    "MAX_FILE_BYTES",
    "SCHEMA", "PRIVATE_KEY_NAME", "PUBLIC_HASH_NAME", "PII_RECORD_NAME",
    "MAX_PENDING", "SEALED", "HELD", "NO_KEY", "TOO_BIG", "FULL", "inbox_dir",
    "ensure_inbox", "can_accept", "deposit", "deposit_status", "key_status",
    "held_count", "owner_notices", "pending_count", "drain",
    "append_pii_record", "on_vault_opened", "public_sha256",
]
