"""Every owner-facing string uses plain words.

The owner reads these. The list is the design's owner-words rule (section 8)
and the spec: never "door" or "doors" (say "outside service" or "the
Bridge"), no em dash, no "beta", no "warts", no invented or technical words
("sandbox", "egress", "repo", "API" on screen, "SPDX", "manifest", "MiniLM",
"embedding", "seccomp", "bwrap", "WSL2", "little app", "parts)", "0 parts",
"recipe check", "recipe file"), no commit hashes, and never her sister's name.

Read, each only when it is in this build:

* the ``wording.py`` of the wizard, Toolsmith (``integrate``), the locked box
  (``box``) and the app host (``webapps``): every string constant and every
  string inside a dict or list constant;
* the string literals and JSX text of ``NeedsStep.tsx``, ``PlanGroups.tsx``,
  ``BuildingLines.tsx``, ``dashboard/app/dashboard/apps/[id]/*.tsx`` and
  ``dashboard/app/dashboard/add/page.tsx``;
* every ``tests/fixtures/ui_strings/*.txt`` (one string per line).

Two readings of the list are narrower than a plain substring search, on
purpose: words are matched whole ("outdoor" is not "door"), and a commit
hash is a run of 7 to 40 hex characters with at least one digit and at
least one letter, so "1234567890" in the PIN advice is not a hash.

Run:
    pytest tests/test_plain_words_in_ui_strings.py
"""
from __future__ import annotations

import glob
import importlib
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(ROOT, "dashboard", "app")

WORDING_MODULES = ("puffpai.wizard.wording", "puffpai.integrate.wording",
                   "puffpai.box.wording", "puffpai.webapps.wording")
TSX_FILES = (
    os.path.join(DASH, "onboarding", "NeedsStep.tsx"),
    os.path.join(DASH, "onboarding", "PlanGroups.tsx"),
    os.path.join(DASH, "onboarding", "BuildingLines.tsx"),
    *sorted(glob.glob(os.path.join(DASH, "dashboard", "apps", "[[]id[]]", "*.tsx"))),
    os.path.join(DASH, "dashboard", "add", "page.tsx"),
    # The pages that had em dashes and
    # jargon on (Apps page, Chat, the home page and its tiles, the wizard's
    # top line, the Developer overview, the floating Puff).
    os.path.join(DASH, "dashboard", "apps", "_components", "AppsIndex.tsx"),
    os.path.join(DASH, "dashboard", "apps", "_lib", "appsIndex.ts"),
    os.path.join(DASH, "dashboard", "chat", "page.tsx"),
    os.path.join(DASH, "_components", "Front", "FrontPage.tsx"),
    os.path.join(DASH, "_components", "Front", "BotAppsGrid.tsx"),
    os.path.join(DASH, "_components", "Widgets", "JetsonTile.tsx"),
    os.path.join(DASH, "onboarding", "ui.tsx"),
    os.path.join(DASH, "dashboard", "developer", "page.tsx"),
    os.path.join(DASH, "dashboard", "developer", "overviewWords.ts"),
    os.path.join(DASH, "dashboard", "developer", "_components", "AddYourOwn.tsx"),
    os.path.join(DASH, "dashboard", "_components", "FloatingPuff.tsx"),
)
OWNER_STRING_FILES = sorted(glob.glob(os.path.join(
    ROOT, "tests", "fixtures", "ui_strings", "*.txt")))

#: (name, pattern). Case-insensitive unless the name says otherwise.
BANNED: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pattern, flags)) for name, pattern, flags in (
        ("door", r"(?<![A-Za-z])doors?(?![A-Za-z])", re.I),
        ("em dash", "—", 0),
        ("beta", r"(?<![A-Za-z])beta(?![A-Za-z])", re.I),
        ("wart", r"(?<![A-Za-z])warts?(?![A-Za-z])", re.I),
        ("sandbox", r"sandbox", re.I),
        ("egress", r"egress", re.I),
        ("repo ", r"(?<![A-Za-z])repo ", re.I),
        ("SPDX", r"spdx", re.I),
        ("manifest", r"manifest", re.I),
        ("MiniLM", r"minilm", re.I),
        ("embedding", r"embedding", re.I),
        ("seccomp", r"seccomp", re.I),
        ("bwrap", r"bwrap", re.I),
        ("WSL2", r"wsl2", re.I),
        ("little app", r"little app", re.I),
        ("parts)", r"parts\)", re.I),
        ("0 parts", r"(?<![0-9])0 parts", re.I),
        ("recipe check", r"recipe check", re.I),
        ("recipe file", r"recipe file", re.I),
    ))
#: "API" on screen: case-sensitive, whole word; a token with a dot in it
#: (a host name like api.github.com) is not a word on screen.
_TOKEN = re.compile(r"[A-Za-z0-9_.\-/:]+")
_HEX = re.compile(r"(?<![A-Za-z0-9_])[0-9A-Fa-f]{7,40}(?![A-Za-z0-9_])")


def banned_in(text: str) -> list[str]:
    """The banned words and marks found in *text*."""
    found = [name for name, pattern in BANNED if pattern.search(text)]
    for token in _TOKEN.findall(text):
        if "." in token:
            continue
        if re.search(r"(?<![A-Za-z])API(?![A-Za-z])", token):
            found.append("API")
            break
    for run in _HEX.findall(text):
        if re.search(r"[0-9]", run) and re.search(r"[A-Fa-f]", run):
            found.append(f"commit hash {run}")
    return found


# -- where the strings come from -------------------------------------------------

def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None
        raise


def _strings_in(value, where: str):
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield from _strings_in(inner, f"{where}[{key!r}]")
    elif isinstance(value, (list, tuple, set, frozenset)):
        for number, inner in enumerate(value):
            yield from _strings_in(inner, f"{where}[{number}]")


def module_strings(modname: str) -> list[tuple[str, str]]:
    """Every public string constant of a wording module, and every string in
    its dict, list and tuple constants."""
    module = _optional(modname)
    if module is None:
        return []
    out: list[tuple[str, str]] = []
    for name, value in vars(module).items():
        if name.startswith("__") or callable(value) or isinstance(value, type):
            continue
        if getattr(value, "__module__", None) not in (None, modname) \
                and not isinstance(value, (str, dict, list, tuple)):
            continue
        out.extend(_strings_in(value, f"{modname}.{name}"))
    return out


_TS_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'|`((?:[^`\\]|\\.)*)`')
_JSX_TEXT = re.compile(r">([^<>{}]*[A-Za-z][^<>{}]*)<")
_IMPORT = re.compile(r"^\s*(import|export \*|export \{[^}]*\} from)\b.*$", re.M)
_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\"'])//[^\n]*", re.S)
#: An element attribute's name (``setAttribute("sandbox", ...)``) is markup,
#: never on screen; its value is still read (merge, milestone F).
_SET_ATTR_NAME = re.compile(r"""setAttribute\(\s*(["'])[A-Za-z-]+\1\s*,""")


def tsx_strings(path: str) -> list[tuple[str, str]]:
    """String literals and JSX text of one page, without imports and
    comments (those are not on screen)."""
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    source = _IMPORT.sub("", _COMMENT.sub("", source))
    source = _SET_ATTR_NAME.sub("setAttribute(", source)
    out: list[tuple[str, str]] = []
    for match in _TS_STRING.finditer(source):
        text = next(g for g in match.groups() if g is not None)
        if text.strip():
            out.append((os.path.relpath(path, ROOT), text))
    for match in _JSX_TEXT.finditer(source):
        text = " ".join(match.group(1).split())
        if text:
            out.append((os.path.relpath(path, ROOT), text))
    return out


def fixture_strings(path: str) -> list[tuple[str, str]]:
    with open(path, encoding="utf-8") as handle:
        return [(f"{os.path.relpath(path, ROOT)}:{number}", line.rstrip("\n"))
                for number, line in enumerate(handle, 1) if line.strip()]


def every_owner_string() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for modname in WORDING_MODULES:
        out.extend(module_strings(modname))
    for path in TSX_FILES:
        if os.path.isfile(path):
            out.extend(tsx_strings(path))
    for path in OWNER_STRING_FILES:
        out.extend(fixture_strings(path))
    return out


# -- the tests --------------------------------------------------------------------

@pytest.mark.parametrize("text,bad", [
    ("Open the door for it", "door"),
    ("Two DOORS", "door"),
    ("It is a beta", "beta"),
    ("No warts here", "wart"),
    ("Runs in the sandbox", "sandbox"),
    ("Its egress rules", "egress"),
    ("The repo is small", "repo "),
    ("Uses the API", "API"),
    ("SPDX id", "SPDX"),
    ("the manifest file", "manifest"),
    ("MiniLM reads it", "MiniLM"),
    ("an embedding", "embedding"),
    ("seccomp rules", "seccomp"),
    ("bwrap runs it", "bwrap"),
    ("needs WSL2", "WSL2"),
    ("a little app", "little app"),
    ("(3 parts)", "parts)"),
    ("found 0 parts", "0 parts"),
    ("the recipe check", "recipe check"),
    ("the recipe file", "recipe file"),
    ("A — B", "em dash"),
])
def test_the_list_catches_each_banned_word(text, bad):
    found = banned_in(text)
    assert any(name.startswith(bad) for name in found), (text, found)


def test_a_commit_hash_is_caught_and_plain_numbers_are_not():
    assert banned_in("the version f011a568 from June")
    assert banned_in("pinned at 3b9f2c1d0e4a5b6c7d8e9f00112233445566aa77")
    assert not banned_in("Do not use easy ones like 1234567890.")
    assert not banned_in("It was defaced.")


@pytest.mark.parametrize("text", [
    "api.github.com answers searches",
    "Outdoor games",
    "Betamax tapes",
    "Stewart's list",
    "the rapid reporting",
    "It uses an app, and apis.guru lists it",
])
def test_ordinary_words_and_host_names_pass(text):
    assert banned_in(text) == [], (text, banned_in(text))


def test_the_sources_are_read():
    strings = every_owner_string()
    wheres = {where.split("[")[0].rsplit(".", 1)[0] for where, _ in strings}
    assert "puffpai.wizard.wording" in wheres
    assert any(where.endswith("NeedsStep.tsx") for where, _ in strings)
    assert any(where.endswith("BuildingLines.tsx") for where, _ in strings)
    assert any("ui_strings" in where and "needs_progress.txt" in where
               for where, _ in strings)
    assert len(strings) > 500


def test_every_owner_string_uses_plain_words():
    bad = [(where, text, banned_in(text)) for where, text in every_owner_string()
           if banned_in(text)]
    assert not bad, "\n".join(f"{where}: {found}: {text[:120]!r}"
                              for where, text, found in bad[:60])
