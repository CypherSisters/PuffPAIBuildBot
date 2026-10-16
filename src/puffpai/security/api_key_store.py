"""Where THIS build's app API key is stored. One key per build, never shared.

The bug this fixes: with no installer key, every build on
one computer read and minted the same password-store entry, ``puffpai`` /
``PUFFPAI_API_KEY``. Six bots built in six state folders printed one key.

Now, after an explicitly set ``PUFFPAI_API_KEY`` (the installer's settings
file, which stays first and is handled in ``api_server``), a build looks in:

1. the vault's service-secret tier, entry ``PUFFPAI_API_KEY`` -- but only when
   an unlocked vault has been handed to the secrets provider
   (``secrets_provider.install_unlocked_vault``). The server does not do that
   when it starts, so in normal running this tier is empty and step 2 decides.
   The vault does NOT live in the build's state folder: it is in the DATA
   folder (``paths.vault_data_dir`` under ``PUFFPAI_DATA_DIR``, by default the
   code copy's own ``vault_data``), and the entry name is not per build, so
   builds that share a data folder would share this entry,
2. its OWN password-store entry, ``puffpai`` / ``PUFFPAI_API_KEY.<label>.<id>``
   where ``<id>`` is :func:`puffpai.security.build_identity.build_id`, a hash of
   the build's state folder, and ``<label>`` is that folder's name. Both come
   from the folder's CANONICAL path (junctions, links and short 8.3 names
   resolved), so one build reached by several spellings has one entry name.
   An entry made before that, under the name the folder was reached by (the
   same id, an older label), is still found, and moved to the one name
   (:func:`move_old_entry`) instead of a new key being made,
3. the old machine-wide entry, ``puffpai`` / ``PUFFPAI_API_KEY`` -- ONLY for
   the build in the per-user default state folder, which is the one build that
   could have been using it as its own key. Every other build never reads it,
4. its own key file, ``<state>/api_key.txt``.

A new key is only ever written under the build's own entry name (or its own
file when there is no password store). The machine-wide name is never written
again. Nothing here prints or logs a key.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
from typing import Optional

SERVICE = "puffpai"
#: The old machine-wide entry. Read (never written) for the default build only.
LEGACY_ENTRY = "PUFFPAI_API_KEY"
KEY_FILE = "api_key.txt"


def entry_name(state: Optional[str] = None) -> str:
    """This build's own password-store entry name. Not a secret."""
    from puffpai.security import build_identity as bi
    return f"{LEGACY_ENTRY}.{bi.build_label(state)}.{bi.build_id(state)}"


def old_entry_names(state: Optional[str] = None) -> list[str]:
    """Names this build's entry may have had before the label was canonical:
    the SAME build id, an older label. Not secrets."""
    from puffpai.security import build_identity as bi
    me = bi.build_id(state)
    return [f"{LEGACY_ENTRY}.{label}.{me}" for label in bi.old_labels(state)]


def _old_entry(state: Optional[str] = None) -> tuple[str, str]:
    """``(entry name, key)`` of this build's key under an older name."""
    try:
        names = old_entry_names(state)
    except Exception:
        return "", ""
    for name in names:
        value = _get(name)
        if value:
            return name, value
    return "", ""


def move_old_entry(state: Optional[str] = None) -> str:
    """Give a key kept under an older name this build's one entry name.

    When this build has no key under :func:`entry_name` but has one under an
    older name for the SAME build (see :func:`old_entry_names`), the key is
    written under the one name, read back, and only then the old entry is
    removed. Returns one plain line saying what happened, or '' when there
    was nothing to move. Never prints or returns the key.
    """
    ring = _keyring()
    if ring is None or _get(entry_name(state)):
        return ""
    old, value = _old_entry(state)
    if not value:
        return ""
    new = entry_name(state)
    try:
        ring.set_password(SERVICE, new, value)
    except Exception:
        return (f"This bot's key is kept under an older name in the password "
                f"store ('{old}'). It could not be given its new name, so it "
                f"stays there and keeps working.")
    if _get(new) != value:
        return (f"This bot's key is kept under an older name in the password "
                f"store ('{old}'). Giving it its new name did not work, so it "
                f"stays there and keeps working.")
    try:
        ring.delete_password(SERVICE, old)
    except Exception:
        pass
    if _get(old):
        return (f"This bot's key now has one name in the password store "
                f"('{new}'), but the copy under its older name ('{old}') "
                f"could not be removed. Remove that one by hand.")
    return (f"This bot's key was kept under an older name in the password "
            f"store. It now has one name ('{new}'), and the old one was "
            f"removed.")


def key_file(state: Optional[str] = None) -> str:
    from puffpai.security import build_identity as bi
    return os.path.join(bi.state_dir(state), KEY_FILE)


def _keyring():
    """The ``keyring`` module the secrets provider uses, or None."""
    from puffpai.security import secrets_provider
    return secrets_provider.keyring if secrets_provider._HAS_KEYRING else None  # noqa: SLF001


def _get(entry: str) -> str:
    ring = _keyring()
    if ring is None:
        return ""
    try:
        return (ring.get_password(SERVICE, entry) or "").strip()
    except Exception:  # a locked or missing store is "nothing stored"
        return ""


def _vault_value() -> str:
    try:
        from puffpai.security.secrets_provider import get_default_provider
        provider = get_default_provider()
        return (provider._vault_lookup(LEGACY_ENTRY) or "").strip()  # noqa: SLF001
    except Exception:
        return ""


def stored(state: Optional[str] = None) -> tuple[str, str]:
    """``(where, key)`` of this build's stored key, or ``("", "")``.

    Mints and writes nothing: this answers "what does this build have".
    ``where`` names the store, never the key.
    """
    from puffpai.security import build_identity as bi
    value = _vault_value()
    if value:
        return "the vault", value
    value = _get(entry_name(state))
    if value:
        return "this bot's own entry in the password store", value
    _old, value = _old_entry(state)
    if value:
        return ("this bot's own entry in the password store, under its "
                "older name"), value
    if bi.is_default_build(state):
        value = _get(LEGACY_ENTRY)
        if value:
            return ("the password store's older shared entry (this computer's "
                    "first bot keeps using it)"), value
    path = key_file(state)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError:
        value = ""
    if value:
        return f"this bot's key file ({path})", value
    return "", ""


def store_new(key: str, state: Optional[str] = None, *,
              write_file=None) -> str:
    """Keep a newly made *key* for this build only. Returns where it went.

    The password store first, under this build's own entry name; the key file
    only when there is no working store. ``write_file(path, key)`` is the
    owner-only file writer (``api_server._write_key_file``).
    """
    ring = _keyring()
    if ring is not None:
        try:
            ring.set_password(SERVICE, entry_name(state), key)
        except Exception:
            pass
        else:
            # A file copy from before would now be a stale second copy.
            try:
                os.remove(key_file(state))
            except OSError:
                pass
            return f"the password store (service '{SERVICE}', entry '{entry_name(state)}')"
    if write_file is None:
        raise RuntimeError("no password store and no file writer")
    path = key_file(state)
    write_file(path, key)
    return path


__all__ = ["KEY_FILE", "LEGACY_ENTRY", "SERVICE", "entry_name", "key_file",
           "move_old_entry", "old_entry_names", "store_new", "stored"]
