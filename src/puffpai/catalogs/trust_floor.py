"""The trust floor: may a found project be offered at all, and may it be pre-ticked?

Goal-agnostic and run before any scoring. It reads only facts in the
``found/v1`` record (search-result fields, the licence file read at the pinned
commit, page facts) plus the awesome-selfhosted snapshot shipped with the build.
It never fetches.

Common to every code row: not a fork, not archived, and a name and description
free of the spam pattern and of anything that reads like an order to a model or
a browser. Then the first path that holds:

* ``stars``: 5 or more stars and a licence reported or read, of an open
  family. Real use counts even when the project is old: one not changed
  within 3 years still passes, with the reason ``"old"`` (the plan says
  "Last changed 2019."), and is never pre-ticked;
* ``listed``: its address is in the awesome-selfhosted snapshot, with a licence;
* ``static_small``: a page that opens as it is (``static_web``), nothing to
  build, the LICENSE **file read at the pinned commit** is open (a reported
  licence alone is not enough), it is a working tool (it keeps its own data,
  or it has at least :data:`STATIC_TOOL_MIN_CODE` bytes of its own script: a
  timer or a game board stores nothing and is still a tool), changed within
  24 months.

Only ``stars`` (when not old) and ``listed`` may ever be pre-ticked. A row that fails only on
its licence returns ``(False, "", ["license"])`` so the plan can say why.

A licence file read at the pinned
commit that names a holder other than the owner's account itself
(``repo.license_check``, see ``where_to_look.licence_to_check``) passes with
the reason ``"check_licence"``: the plan says "check this licence yourself",
and it is never pre-ticked. Such a name may be a real one ("Hiroshi Tanaka")
or a restriction written in a language no list has ("Bu Mai"); it is never
refused.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import datetime
import importlib
import re
from typing import Any, Callable, Optional

from puffpai import artifact_review

SPAM_RE = re.compile(r"crack|keygen|serial key|free download|torrent|apk mod|cheat", re.I)

STARS_MIN = 5
STARS_MAX_AGE_DAYS = 3 * 365
STATIC_MAX_AGE_DAYS = 730           # 24 months
#: A static page with at least this much of its own script is a working tool
#: even when it stores nothing (a timer, a dice roller, a game board).
STATIC_TOOL_MIN_CODE = 4096
#: A reason that does not stop a find, only its pre-tick: shown as its year.
OLD = "old"
#: A reason that does not stop a find, only its pre-tick: "check this licence yourself".
CHECK_LICENCE = "check_licence"
NPM_MIN_MONTHLY = 100

PRETICK_PATHS = frozenset({"stars", "listed"})
CODE_SOURCES = frozenset({"github", "codeberg", "gitlab", "awesome_selfhosted", "hacs"})


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


def _listed_default(url: str) -> Optional[dict[str, Any]]:
    """The awesome-selfhosted snapshot row for *url*, or None (no snapshot: None)."""
    mod = _optional("puffpai.catalogs.searchers.awesome_selfhosted")
    fn = getattr(mod, "listed", None) if mod is not None else None
    return fn(url) if callable(fn) else None


#: Replaceable in tests; production reads the shipped snapshot.
listed_lookup: Callable[[str], Optional[dict[str, Any]]] = _listed_default


def _today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def _age_days(when: Any) -> Optional[int]:
    if not isinstance(when, str) or len(when) < 10:
        return None
    try:
        day = datetime.date.fromisoformat(when[:10])
    except ValueError:
        return None
    return (_today() - day).days


def _open(license_id: Any) -> bool:
    """An open family: usable words exist and it is not personal-use only."""
    from puffpai.catalogs import where_to_look
    words = where_to_look.license_words(str(license_id)) if license_id else None
    return bool(words) and "personal use only" not in words


def _effective_license(repo: dict[str, Any]) -> Optional[str]:
    """The licence file read at the pinned commit wins; else the reported one."""
    read = repo.get("license_read")
    if read:
        return str(read)
    reported = repo.get("license_reported")
    if reported and str(reported).upper() not in ("NOASSERTION", "NONE", "OTHER"):
        return str(reported)
    return None


def _common(found: dict[str, Any]) -> list[str]:
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    pkg = found.get("package") if isinstance(found.get("package"), dict) else {}
    reasons = []
    if repo.get("fork"):
        reasons.append("fork")
    if repo.get("archived"):
        reasons.append("archived")
    texts = [str(found.get("name") or ""), str(found.get("what_it_is") or ""),
             str(found.get("friendly") or ""), str(pkg.get("name") or "")]
    if any(SPAM_RE.search(t) for t in texts):
        reasons.append("spam")
    if any(artifact_review.reads_like_orders(t) for t in texts if t):
        reasons.append("orders")
    return reasons


def _paths(found: dict[str, Any], *, ignore_license: bool) -> tuple[str, list[str]]:
    """The first code path that holds, or ("", reasons it did not)."""
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    details = found.get("details") if isinstance(found.get("details"), dict) else {}
    lic = _effective_license(repo)
    lic_ok = ignore_license or _open(lic)
    why: list[str] = []

    stars = repo.get("stars")
    age = _age_days(repo.get("pushed_at"))
    if isinstance(stars, int) and not isinstance(stars, bool) and stars >= STARS_MIN and lic_ok:
        if age is not None and age <= STARS_MAX_AGE_DAYS:
            return "stars", []
        return "stars", [OLD]            # real use, licensed: shown with its year, never pre-ticked
    why.append("few_stars" if not (isinstance(stars, int) and stars >= STARS_MIN) else "license")

    url = str(found.get("url") or "")
    row = listed_lookup(url) if url else None
    if isinstance(row, dict):
        listed_lic = row.get("license") or row.get("licenses")
        if isinstance(listed_lic, list):
            listed_lic = listed_lic[0] if listed_lic else None
        # The licence BuildBot read itself always outranks the list's (a
        # find read as custom at its pin must not pass and be pre-ticked on
        # the listing's GPL-3.0). A read that
        # is not open (custom, personal use only) is never made free by a list.
        own_read = repo.get("license_read")
        if ignore_license or (_open(listed_lic) and (not own_read or _open(own_read))):
            return "listed", []
        why.append("license")
    else:
        why.append("not_listed")

    read = repo.get("license_read") if repo.get("pinned_commit") else None
    read_ok = ignore_license or _open(read)
    static_age = _age_days(repo.get("pushed_at"))
    code_bytes = details.get("code_bytes")
    is_tool = details.get("keeps_data") is True or (
        isinstance(code_bytes, int) and not isinstance(code_bytes, bool)
        and code_bytes >= STATIC_TOOL_MIN_CODE)
    if found.get("mode_guess") == "static_web" and details.get("has_build_script") is False \
            and is_tool and read_ok \
            and static_age is not None and static_age <= STATIC_MAX_AGE_DAYS:
        return "static_small", []
    why.append("not_small_static")
    return "", why


def _check_npm(found: dict[str, Any]) -> tuple[bool, str, list[str]]:
    pkg = found.get("package") if isinstance(found.get("package"), dict) else {}
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    reasons = _common(found)
    if not found.get("url") and not repo.get("name"):
        reasons.append("no_repository")
    if pkg.get("repo_name_matches") is not True:
        reasons.append("name_mismatch")
    monthly = pkg.get("downloads_monthly")
    if not (isinstance(monthly, int) and monthly >= NPM_MIN_MONTHLY):
        reasons.append("few_downloads")
    if reasons:
        return False, "", reasons
    if not _open(_effective_license(repo)):
        return False, "", ["license"]
    return True, "", []


def _check_picture(found: dict[str, Any]) -> tuple[bool, str, list[str]]:
    from puffpai.catalogs import where_to_look
    pic = found.get("picture") if isinstance(found.get("picture"), dict) else {}
    reasons = [r for r in _common(found) if r in ("spam", "orders")]
    if pic.get("mature") is True:
        reasons.append("mature")
    if reasons:
        return False, "", reasons
    if not pic.get("license") or where_to_look.license_words(str(pic["license"])) is None:
        return False, "", ["license"]
    return True, "", []


def _check_dataset(found: dict[str, Any]) -> tuple[bool, str, list[str]]:
    """A data set from a data host: a readable licence (non-commercial is labelled), no spam."""
    from puffpai.catalogs import where_to_look
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    reasons = [r for r in _common(found) if r in ("spam", "orders")]
    if reasons:
        return False, "", reasons
    lic = repo.get("license_reported")
    if not lic or where_to_look.license_words(str(lic)) is None:
        return False, "", ["license"]
    return True, "", []


def check(found: dict[str, Any]) -> tuple[bool, str, list[str]]:
    """``(ok, path, reasons)``; path is ``"stars"``, ``"listed"``, ``"static_small"`` or ``""``.

    npm and picture rows that pass return ``(True, "", [])``: they pass their
    own floor but none of the three code paths, so they are never pre-ticked.
    """
    if not isinstance(found, dict):
        return False, "", ["not_a_find"]
    source = found.get("source")
    if source in ("npm", "pypi"):
        return _check_npm(found)
    if source == "huggingface":
        return _check_dataset(found)
    if found.get("part") == "pictures":
        return _check_picture(found)
    common = _common(found)
    if common:
        return False, "", common
    path, why = _paths(found, ignore_license=False)
    if path:
        return True, path, list(why) + ([CHECK_LICENCE] if licence_to_check(found) else [])
    loose, _ = _paths(found, ignore_license=True)
    if loose:
        return False, "", ["license"]
    return False, "", sorted(set(why))


def licence_to_check(found: dict[str, Any]) -> bool:
    """Is the find's licence one BuildBot read at the pinned commit but could
    not confirm, so she should check it herself? (``github.read_licence``)"""
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    return bool(repo.get("license_check") is True and repo.get("license_read")
                and repo.get("pinned_commit"))


#: What to check, in one short line after "check this licence yourself"
#: (without it, the plan says to check it and never what, or why). Keys
#: are ``where_to_look.CHECK_WHY``; "the author" is the account that shared it.
CHECK_WHY_WORDS = {
    "condition": "the name in it has words that could limit how you use it",
    "stranger": "it names someone other than the author",
    "added": "the name in it adds words to the author's name",
    "address": "it adds an address next to the author's name",
    "general": "it names no one by name, only general words",
    "unsure": "BuildBot could not match it to the author",
}


def licence_check_words(found: dict[str, Any]) -> str:
    """The short reason to show after "check this licence yourself", or ''
    when the licence is not marked to check."""
    if not licence_to_check(found):
        return ""
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    why = repo.get("license_check_why")
    return CHECK_WHY_WORDS.get(why if isinstance(why, str) else "", CHECK_WHY_WORDS["unsure"])


def _pretick(ok: bool, path: str, reasons: list[str]) -> bool:
    return (bool(ok) and path in PRETICK_PATHS and OLD not in reasons
            and CHECK_LICENCE not in reasons)


def pretick_allowed(found: dict[str, Any]) -> bool:
    """True only for ``stars`` (when not old) and ``listed`` (recomputed, never read back)."""
    ok, path, reasons = check(found)
    return _pretick(ok, path, reasons)


def trust_record(found: dict[str, Any]) -> dict[str, Any]:
    """The ``trust`` block of a ``found/v1`` record."""
    ok, path, reasons = check(found)
    return {"floor_ok": ok, "path": path, "pretick_ok": _pretick(ok, path, reasons),
            "reasons": reasons}


__all__ = ["CHECK_LICENCE", "CHECK_WHY_WORDS", "CODE_SOURCES", "OLD", "PRETICK_PATHS",
           "SPAM_RE", "STATIC_TOOL_MIN_CODE", "check", "licence_check_words",
           "licence_to_check", "listed_lookup",
           "pretick_allowed", "trust_record"]
