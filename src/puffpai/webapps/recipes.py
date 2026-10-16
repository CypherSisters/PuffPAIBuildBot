"""Where an app's compiled actions are kept: ``<state>/apps/<id>/recipes.json``.

Generated only. Every recipe here was made by Toolsmith's compiler (or reader)
on a throwaway copy and carries its provenance and Drawbridge's verdict.

Why a seal. Provenance, fingerprints and the stored verdict are all public
fields: anyone who can write the state folder could recompute them for a
recipe they wrote by hand. So :func:`save` adds a seal
to each checked recipe, a keyed SHA-256 hash (HMAC) of the whole recipe with
its verdict, bound to the app id, under a key that is NOT kept in the state
folder. On every load each recipe is held again to:

* the seal (a recipe without it, or edited after it was sealed, or sealed on
  another computer or for another install, is left out);
* R12 (made by the compiler or reader, the current compiler version, the
  current app files) and its stored ``allow`` for this exact recipe;
* a complete set of passing Drawbridge rows whose fingerprint matches;
* R1-R6, R8 and R9 run again on the recipe itself;
* the effect floor: the stored effect is never less strict than the declared
  one or than what the recipe's own steps and words say
  (:func:`puffpai.webapps.recipe_check.static_effect_floor`).

Anything else is left out: its actions do not exist.

Where the seal key lives, in order (the first that is set wins):

1. ``$PUFFPAI_WEBAPPS_SEAL_KEY_FILE``: an absolute path to a key file (tests,
   dev boxes, Linux service installs). Created with 32 random bytes when it
   does not exist yet; refused when it is a link, sits inside the downloaded
   apps' folder or the state folder, or (POSIX) is readable by group or others. When it is set
   but refused, the password store is NOT tried instead.
2. The operating system's own password store through ``keyring`` (on Windows
   the Credential Manager, this Windows user only), under a name bound to this
   install's state folder. Refused when the backend is not a real OS store.
3. Neither: there is no key, nothing is sealed and no action is offered
   (fail closed). Apps still open; the bot just cannot use them by itself.

The key is never logged or printed.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import threading
from typing import Any, Optional

from puffpai import webapps
from puffpai.webapps import recipe_check as rc

SCHEMA = "puffpai_web_recipes/v1"
FILE_NAME = "recipes.json"
MAX_BYTES = 1024 * 1024

SEAL_ENV = "PUFFPAI_WEBAPPS_SEAL_KEY_FILE"
_SEAL_DOMAIN = b"puffpai-web-recipe-seal/v1"
_KEYRING_SERVICE = "puffpai-buildbot"
#: keyring backends that are not a real OS store: never hold the seal key.
_WEAK_KEYRING_MODULES = ("keyring.backends.fail", "keyring.backends.null", "keyrings.alt")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_LOCK = threading.Lock()
_sha_cache: dict[str, tuple[tuple, str]] = {}
_key_cache: dict[tuple, bytes] = {}


def path_for(app_id: str) -> str:
    return os.path.join(webapps.apps_state_dir(app_id), FILE_NAME)


def _signature(root: str) -> tuple:
    sig = []
    for dirpath, dirnames, files in webapps.walk(root):
        dirnames.sort()
        for fn in sorted(files):
            p = os.path.join(dirpath, fn)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            sig.append((os.path.relpath(p, root), st.st_size, st.st_mtime_ns))
    return tuple(sig)


def current_app_sha(app_id: str) -> str:
    """Fingerprint of the app's served files now (cached by names, sizes, times)."""
    root = webapps.web_dir(app_id)
    if not os.path.isdir(root):
        return ""
    sig = _signature(root)
    with _LOCK:
        hit = _sha_cache.get(app_id)
        if hit and hit[0] == sig:
            return hit[1]
    value = rc.app_sha(root)
    with _LOCK:
        _sha_cache[app_id] = (sig, value)
    return value


# ---------------------------------------------------------------------------
# the seal key (kept outside the state folder)
# ---------------------------------------------------------------------------

def _within(root: str, path: str) -> bool:
    try:
        root_n = os.path.normcase(os.path.realpath(root))
        path_n = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([root_n, path_n]) == root_n
    except (OSError, ValueError):
        return True            # cannot tell: treat as inside (refused)


def _key_from_file(path: str) -> Optional[bytes]:
    if not path or not os.path.isabs(path):
        return None
    p = os.path.abspath(path)
    if _within(webapps.files_root(), p):
        return None            # the downloaded apps' folder is served to apps
    from puffpai.config import get_state_dir
    if _within(get_state_dir(), p):
        return None            # the state folder holds what the key seals: never the key too
    if os.path.islink(p):
        return None
    if not os.path.lexists(p):
        parent = os.path.dirname(p)
        if not os.path.isdir(parent):
            return None
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            pass
        except OSError:
            return None
        else:
            with os.fdopen(fd, "w", encoding="ascii") as fh:
                fh.write(secrets.token_hex(32))
    try:
        st = os.lstat(p)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    if sys.platform != "win32" and st.st_mode & 0o077:
        return None            # readable by group or others: refused (the vault's rule)
    cache_key = ("file", os.path.normcase(p), st.st_mtime_ns, st.st_size, st.st_ino)
    with _LOCK:
        hit = _key_cache.get(cache_key)
    if hit is not None:
        return hit
    try:
        with open(p, encoding="ascii") as fh:
            value = fh.read(200).strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not _HEX64.fullmatch(value):
        return None
    key = bytes.fromhex(value)
    with _LOCK:
        _key_cache[cache_key] = key
    return key


def _usable_keyring():
    """The ``keyring`` module when its backend is a real OS store, else None."""
    try:
        import keyring  # noqa: PLC0415 - optional, and slow to import
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001
        return None
    module = type(backend).__module__
    if any(module.startswith(weak) for weak in _WEAK_KEYRING_MODULES):
        return None
    if getattr(backend, "priority", 1) <= 0:
        return None
    return keyring


def _keyring_user() -> str:
    from puffpai.config import get_state_dir
    digest = hashlib.sha256(os.path.abspath(get_state_dir()).encode("utf-8"))
    return "webapps-recipe-seal:" + digest.hexdigest()[:12]


def _key_from_keyring() -> Optional[bytes]:
    kr = _usable_keyring()
    if kr is None:
        return None
    user = _keyring_user()
    cache_key = ("keyring", user)
    with _LOCK:
        hit = _key_cache.get(cache_key)
    if hit is not None:
        return hit
    try:
        value = kr.get_password(_KEYRING_SERVICE, user)
        if value is None:
            kr.set_password(_KEYRING_SERVICE, user, secrets.token_hex(32))
            value = kr.get_password(_KEYRING_SERVICE, user)
    except Exception:  # noqa: BLE001 - no store, no key (fail closed)
        return None
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        return None
    key = bytes.fromhex(value)
    with _LOCK:
        _key_cache[cache_key] = key
    return key


def seal_key() -> Optional[bytes]:
    """This install's seal key, or None when there is no safe place for one."""
    env = os.environ.get(SEAL_ENV, "").strip()
    if env:
        return _key_from_file(env)
    return _key_from_keyring()


def sealing_available() -> bool:
    return seal_key() is not None


def _seal_of(app_id: str, recipe: dict, key: bytes) -> str:
    body = copy.deepcopy(recipe)
    checked = body.get("checked")
    if isinstance(checked, dict):
        checked.pop("seal", None)
    msg = b"\0".join((_SEAL_DOMAIN, app_id.encode("ascii"), rc.canonical(body).encode("utf-8")))
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# making and saving
# ---------------------------------------------------------------------------

def stamp(recipe: dict, app_sha: str, by: str = "compiler") -> dict:
    """Add provenance to a freshly compiled recipe (the only way one gets it)."""
    if by not in rc.PROVENANCE_BY:
        raise ValueError("recipes are made by the compiler or the reader only")
    body = {k: v for k, v in recipe.items() if k not in ("provenance", "checked")}
    body["provenance"] = {"by": by, "compiler_version": rc.COMPILER_VERSION,
                          "app_sha": app_sha, "recipe_sha": rc.recipe_sha(body)}
    return body


def save(app_id: str, app_sha: str, recipes: list[dict]) -> str:
    """Write the checked recipes, sealing each one that passes every load check.

    A recipe that would not load anyway (blocked, unchecked, inconsistent) is
    kept for the "being checked" count but never sealed. Returns the file path.
    """
    if not webapps.valid_app_id(app_id):
        raise ValueError("not an app id")
    key = seal_key()
    out: list = []
    for r in recipes or []:
        if not isinstance(r, dict):
            continue
        r = copy.deepcopy(r)
        checked = r.get("checked")
        if isinstance(checked, dict):
            checked.pop("seal", None)
            if key is not None and _unsealed_ok(r, app_sha)[0]:
                checked["seal"] = _seal_of(app_id, r, key)
        out.append(r)
    p = path_for(app_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    doc = {"schema": SCHEMA, "app_id": app_id, "app_sha": app_sha, "recipes": out}
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def _read(app_id: str) -> Optional[dict]:
    p = path_for(app_id)
    try:
        if os.path.getsize(p) > MAX_BYTES:
            return None
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or doc.get("app_id") != app_id:
        return None
    return doc


# ---------------------------------------------------------------------------
# accepting
# ---------------------------------------------------------------------------

#: The rules that read the recipe alone and are run again on every load (R7
#: needs the storage keys seen on the check copy; the seal covers it).
_RERUN_RULES = frozenset({"R1", "R2", "R3", "R4", "R5", "R6", "R8", "R9"})


def _unsealed_ok(recipe: Any, app_sha: str) -> tuple[bool, str]:
    """Every load check except the seal."""
    if not isinstance(recipe, dict):
        return False, "not a recipe"
    row = rc.provenance_row(recipe, app_sha)
    if row["result"] != "pass":
        return False, row["why"]
    checked = recipe.get("checked")
    if not isinstance(checked, dict) or checked.get("verdict") != "allow":
        return False, "not allowed by Drawbridge"
    if checked.get("recipe_sha") != rc.recipe_sha(recipe) or checked.get("app_sha") != app_sha:
        return False, "being checked"
    effect = checked.get("effect")
    if effect not in rc.EFFECT_ORDER:
        return False, "no proven effect"
    declared = recipe.get("effect") if recipe.get("effect") in rc.EFFECT_ORDER else "removes"
    if rc.stricter(effect, declared) != effect \
            or rc.stricter(effect, rc.static_effect_floor(recipe)) != effect:
        return False, "the effect is less strict than the steps allow"
    proven = checked.get("effect_proven")
    if proven not in rc.EFFECT_ORDER or rc.stricter(effect, proven) != effect:
        return False, "no proven effect"
    if checked.get("undoable") and effect != "changes":
        return False, "only a change can be undone"
    rows = checked.get("rows")
    if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
        return False, "no Drawbridge rows"
    passed = {x.get("rule") for x in rows if x.get("result") == "pass"}
    if any(x.get("result") != "pass" for x in rows) or not rc.REQUIRED_RULES <= passed:
        return False, "not allowed by Drawbridge"
    if checked.get("rows_sha") != hashlib.sha256(rc.canonical(rows).encode("utf-8")).hexdigest():
        return False, "not allowed by Drawbridge"
    again = [x for x in rc.static_rules(recipe, set())
             if x["rule"] in _RERUN_RULES and x["result"] != "pass"]
    if again:
        return False, "Drawbridge's rules fail: " + ", ".join(x["rule"] for x in again)
    return True, ""


def accepted(recipe: Any, app_sha: str, *, app_id: str) -> tuple[bool, str]:
    """R12, the stored verdict, the rules again and the seal, for this app and these files."""
    ok, why = _unsealed_ok(recipe, app_sha)
    if not ok:
        return False, why
    if not webapps.valid_app_id(app_id):
        return False, "not an app id"
    key = seal_key()
    if key is None:
        return False, "no seal key on this computer"
    given = (recipe.get("checked") or {}).get("seal")
    if not isinstance(given, str) or not hmac.compare_digest(
            given.encode("ascii", "replace"), _seal_of(app_id, recipe, key).encode("ascii")):
        return False, "not sealed by this computer"
    return True, ""


def load(app_id: str) -> list[dict]:
    """Only the recipes that pass every check above, sealed on this computer."""
    doc = _read(app_id)
    if doc is None:
        return []
    sha = current_app_sha(app_id)
    if not sha:
        return []
    out = []
    for r in doc.get("recipes") or []:
        ok, _why = accepted(r, sha, app_id=app_id)
        if ok:
            out.append(r)
    return out


def status(app_id: str) -> dict:
    """{"allowed": n, "held_back": n} for the ability row ("being checked")."""
    doc = _read(app_id)
    if doc is None:
        return {"allowed": 0, "held_back": 0}
    total = len(doc.get("recipes") or [])
    allowed = len(load(app_id))
    return {"allowed": allowed, "held_back": total - allowed}


__all__ = ["path_for", "save", "load", "stamp", "accepted", "current_app_sha", "status",
           "seal_key", "sealing_available", "SEAL_ENV"]
