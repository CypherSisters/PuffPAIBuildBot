"""Licence verdicts: NOASSERTION, handles and custom terms.

Every "no licence" verdict of the wedding run (12 GitHub projects) and of
h_knitting (37) was checked against the project itself:
48 have no licence anywhere BuildBot may take one
from; 1 (a short Apache-2.0 notice GitHub calls "NOASSERTION") was a real
miss. The h_minecraft finds read as custom gave three more real misses. They
are fixed here, fail closed (no licence is still never usable, MIT+NC and
custom terms are still refused, the 222-file corpus loses nothing):

1. GitHub's "NOASSERTION" is a licence file it cannot name, not "no licence":
   such a find is looked at, and unread it says BuildBot ran out of time.
2. GNU licences written with "Copyright ©" (Markdown copies) read.
3. GPL-3.0 whose appendix is GitHub's template "{project} Copyright (C)
   {year} {fullname}" reads.
4. "All rights reserved." on its own line (BSD layout) may stand at the edges.
5. An e-mail address whose local part is one plain handle ("sandain") beside
   a holder no longer makes the licence custom; it is always marked to check.
6. A holder that is the owner account's public name ("Sahib Singh" of
   SubchiBeats) is the owner: no "names someone other than the author".
7. A licence that could not be confirmed only because the build's share of
   GitHub's keyless allowance was used up says so plainly, never "terms of
   its own" (still never usable).
8. A project read at its pin whose kind nothing decides is "other" (read),
   not "unknown" (never looked at); a big code find spends no core call on a
   tree count.
9. The 90 s margin: the brain's checks end by PLAN_AIM_S, looks of two jobs
   or more end earlier, and no look starts while the reading helper is behind.

The per-host pace (raw.githubusercontent.com 4 a second, every other host 1
a second) is pinned in tests/test_per_host_request_pace.py; one check here too.

No network: every read is from text in the test or from a fixture.
"""
from __future__ import annotations

import copy
import json
import lzma
import os
import sys
import time

import pytest

from puffpai import source_fetch
from puffpai.catalogs import code_mode, meaning, search_budget, trust_floor, where_to_look as w
from puffpai.catalogs.searchers import github

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import test_github_find_details as D  # noqa: E402
import test_licence_custom_terms_bypass as T  # noqa: E402
from test_licence_custom_terms_bypass import recent  # noqa: E402,F401 - a fixture

PROJECT = ("SubchiBeats", "wedding-planner")


def corpus() -> dict[str, dict]:
    with lzma.open(os.path.join(HERE, "fixtures", "real_licence_files.json.xz"), "rt",
                   encoding="utf-8") as fh:
        return {r["key"]: r for r in json.load(fh)["rows"]}


def gpl3() -> str:
    """A plain GPL-3.0 text from the corpus that the base already read."""
    rows = corpus()
    for key in sorted(rows):
        r = rows[key]
        if r["base_eb43935"] == "GPL-3.0" and "Copyright (C) 2007 Free Software" in r["text"] \
                and "<program>  Copyright (C) <year>  <name of author>" in r["text"]:
            return r["text"]
    raise AssertionError("no plain GPL-3.0 in the corpus")


BSD2 = """Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""


def bsd(head: str) -> str:
    return head + "\n\n" + BSD2


# ---------------------------------------------------------------------------
# 1. NOASSERTION is not "no licence"
# ---------------------------------------------------------------------------

def _gh(**repo):
    rec = {"id": "github:o/p", "source": "github", "part": "code-app",
           "url": "https://github.com/o/p",
           "repo": dict({"host": "github.com", "owner": "o", "name": "p", "stars": 21,
                         "pushed_at": "2026-08-17", "license_reported": None,
                         "license_read": None, "size_kb": 300}, **repo),
           "mode_guess": "unknown"}
    return rec


@pytest.mark.parametrize("reported", ["NOASSERTION", "noassertion", "other"])
def test_a_licence_github_cannot_name_is_read_not_refused(reported):
    rec = _gh(license_reported=reported)
    assert meaning.licence_unknown(rec) is True
    assert meaning.closer_look_tier(rec) == 1                  # looked at with the open ones
    assert meaning.note_reason(rec) == meaning.NOTE_NOT_READ   # unread: never "no licence"


def test_no_licence_file_at_all_is_still_no_licence():
    rec = _gh(license_reported=None)
    assert meaning.licence_unknown(rec) is False
    assert meaning.closer_look_tier(rec) == 2
    rec["mode_guess"] = "static_web"
    rec["integrable"] = True
    assert meaning.note_reason(rec) == meaning.NOTE_NO_LICENCE


def test_once_read_the_licence_read_decides():
    rec = _gh(license_reported="NOASSERTION", license_read=w.CUSTOM_LICENSE)
    assert meaning.licence_unknown(rec) is False
    rec["mode_guess"] = "static_web"
    rec["integrable"] = True
    assert meaning.note_reason(rec) == meaning.NOTE_CUSTOM_LICENCE


APACHE_NOTICE = """Copyright 2013 Marius Soutier

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""


def test_the_wedding_runs_noassertion_find_reads_apache_and_is_marked():
    """juanignaciosl/wedding-tables-planner-web: GitHub "NOASSERTION", the
    plan said "no licence". Its LICENSE is the Apache-2.0 notice by another
    holder: read, shown, and marked to check (never ticked by itself)."""
    ident = ("juanignaciosl", "wedding-tables-planner-web")
    assert w.license_from_text(APACHE_NOTICE, identity=ident) == "Apache-2.0"
    assert w.licence_check_why(APACHE_NOTICE, identity=ident) == "stranger"


def test_harness_counts_an_unread_noassertion_find_as_unread():
    import measure_goals as A
    f = {"id": "github:o/p", "source": "github", "part": "code-app", "mode": "unknown",
         "trust": {}, "meaning": {"fit": 0.6},
         "facts": {"stars": 21, "license_reported": "NOASSERTION", "license_read": None,
                   "pushed_at": "2026-08-17"}}
    assert A.proof_reason(f, set()) == "not looked at in time"
    f["facts"]["license_reported"] = None
    assert A.proof_reason(f, set()) == "no open licence"


# ---------------------------------------------------------------------------
# 2 and 3. GNU texts: "©" for "(C)", and GitHub's appendix template
# ---------------------------------------------------------------------------

def test_a_plain_gpl3_still_reads():
    assert w.license_from_text(gpl3()) == "GPL-3.0"


def test_copyright_sign_for_c_reads():
    text = gpl3().replace("Copyright (C) 2007 Free Software", "Copyright © 2007 Free Software")
    assert w.license_from_text(text) == "GPL-3.0"
    text = gpl3().replace("Copyright (C) 2007 Free Software", "Copyright 2007 Free Software")
    assert w.license_from_text(text) == "GPL-3.0"


@pytest.mark.parametrize("instead", ["Copyright (NC) 2007", "Copyright only 2007",
                                     "Copyright (C) (C) 2007", "Copyright c c 2007"])
def test_nothing_else_may_stand_there(instead):
    text = gpl3().replace("Copyright (C) 2007", instead, 1)
    assert w.license_from_text(text) is None


def test_githubs_appendix_template_reads():
    text = gpl3().replace("<program>  Copyright (C) <year>  <name of author>",
                          "{project}  Copyright (C) {year}  {fullname}")
    assert w.license_from_text(text) == "GPL-3.0"


@pytest.mark.parametrize("line", [
    "{project}  Copyright (C) {year}  {fullname} non-commercial",
    "{project}  Copyright (C) {year}  {fullname} Personal Use",
    "{project}  Copyright (C) {year}  {program}",
    "{program}  Copyright (C) {year}  {fullname}",
    "MyApp  Copyright (C) 2025  Jane Doe",             # a filled-in example: still custom
])
def test_only_the_exact_template_words_may_stand_in_the_appendix(line):
    text = gpl3().replace("<program>  Copyright (C) <year>  <name of author>", line)
    assert w.license_from_text(text) is None


def test_the_corpus_copies_of_both_forms_read():
    rows = corpus()
    for key in ("stefexec/Calyx", "johnfontaine/chess-tools", "khaosdoctor/my-notes"):
        r = rows[key]
        assert w.license_from_text(r["text"], identity=(r["owner"], r["name"])) == "GPL-3.0"


def test_the_c_rule_adds_no_word_anywhere():
    """Every long edition still has exactly the words it had, plus "c" made optional."""
    for _spdx, elements in w.load_long_editions():
        def walk(els):
            for kind, value in els:
                if kind == "opt" and value == (("w", ["c"]),):
                    yield "c?"
                elif kind == "w":
                    yield from value
                elif kind in ("opt", "alt"):
                    for v in (value if kind == "alt" else (value,)):
                        yield from walk(v or ())
        words = list(walk(elements))
        assert "c?" not in words or all(words[i - 1] == "copyright"
                                        for i, x in enumerate(words) if x == "c?")


# ---------------------------------------------------------------------------
# 4. "All rights reserved." on its own line
# ---------------------------------------------------------------------------

def test_a_bsd_rights_line_of_its_own_reads():
    text = bsd("Copyright (c) 2016-, Jan Hovancik\nAll rights reserved.")
    assert w.license_from_text(text, identity=("hovancik", "stretchly")) == "BSD-2-Clause"
    assert w.license_from_text(bsd("Copyright (c) 2016 Jan Hovancik\n\nALL RIGHTS RESERVED"),
                               identity=("hovancik", "x")) == "BSD-2-Clause"
    mit = T.mit("Copyright (c) 2025 Jane Doe") + "\nAll rights reserved.\n"
    assert w.license_from_text(mit, identity=("janedoe", "x")) == "MIT"


@pytest.mark.parametrize("line", [
    "All rights reserved. Non-commercial use only.",
    "All rights reserved, except commercial use.",
    "All rights reserved for companies.",
    "All rights reserved. Keine Firmen.",
    "Alle Rechte vorbehalten.",
    "All rights reserved; no resale.",
])
def test_anything_more_on_that_line_is_custom(line):
    assert w.license_from_text(bsd("Copyright (c) 2016 Jan Hovancik\n" + line),
                               identity=("hovancik", "x")) is None


def test_a_notice_file_may_hold_the_rights_line():
    assert w.notice_ok("Copyright (c) 2020 Jane Doe\nAll rights reserved.\n",
                       identity=("janedoe", "x")) is True
    assert w.notice_ok("Copyright (c) 2020 Jane Doe\nAll rights reserved, no resale.\n",
                       identity=("janedoe", "x")) is False


# ---------------------------------------------------------------------------
# 5. an e-mail handle beside a holder
# ---------------------------------------------------------------------------

MSCS = bsd("Copyright (c) 2011-2021  Jason M. Wood <sandain@mail.example>\n\nAll rights reserved.")


def test_the_minecraft_runs_bsd_reads_and_is_marked():
    ident = ("MinecraftServerControl", "mscs")
    assert w.license_from_text(MSCS, identity=ident) == "BSD-2-Clause"
    assert w.licence_to_check(MSCS, identity=ident) is True       # never ticked by itself


def test_the_owners_own_line_with_a_handle_is_still_marked():
    text = T.mit("Copyright (c) 2025 SubchiBeats <sahib@mail.example>")
    assert w.license_from_text(text, identity=PROJECT) == "MIT"
    assert w.licence_check_why(text, identity=PROJECT) == "address"


@pytest.mark.parametrize("address", [
    "nocorps@mail.example", "noncommercial@mail.example", "privateonly@mail.example",
    "onlyhome@mail.example", "personaluse@mail.example", "no.corps@mail.example",
    "no-companies@mail.example", "keinefirmen@mail.example", "jane@corporations-barred.example",
    "jane@nocorps.example", "termsapply@mail.example", "licensing@mail.example",
    "sellnot@mail.example", "paidseats@mail.example",
])
def test_a_handle_that_says_something_is_still_custom(address):
    text = T.mit(f"Copyright (c) 2025 Jane Doe <{address}>")
    assert w.license_from_text(text, identity=("janedoe", "x")) is None


@pytest.mark.parametrize("handle, ok", [
    ("sandain", True), ("jdoe", True), ("jane84", True), ("a", False), ("1abc", False),
    ("x" * 31, False), ("jane.doe", False), ("jane_doe", False), ("Jane", False),
    ("onlyme", False), ("nosale", False),
])
def test_what_a_handle_is(handle, ok):
    assert w._mail_handle(handle) is ok


# ---------------------------------------------------------------------------
# 6. the owner account's public name
# ---------------------------------------------------------------------------

SAHIB = T.MIT_LICENSE                     # "Copyright (c) 2026 Sahib Singh" (the recorded file)


def test_the_bunny_holder_is_a_stranger_by_text_alone():
    assert w.license_from_text(SAHIB, identity=PROJECT) == "MIT"
    assert w.licence_check_why(SAHIB, identity=PROJECT) == "stranger"


def test_the_accounts_public_name_makes_it_the_owner():
    ident = PROJECT + (w.OwnerName("Sahib Singh"),)
    assert w.license_from_text(SAHIB, identity=ident) == "MIT"
    assert w.licence_to_check(SAHIB, identity=ident) is False


@pytest.mark.parametrize("public, sure", [
    ("Sahib Singh", True),
    ("Sahib Singh Bhatia", True),     # each of its words is the owner's, as a login's words are
    ("Sahib", False), ("Singh", False),                # one word does not cover "Sahib Singh"
    ("Someone Else", False), ("Sahib Sing", False), ("Sahib Studio", False),
    ("SahibSingh", True),                              # the same words, run together
])
def test_only_the_names_own_words(public, sure):
    ident = PROJECT + (w.OwnerName(public),)
    assert w.licence_to_check(SAHIB, identity=ident) is (not sure)


@pytest.mark.parametrize("holder", ["Non Commercial", "Personal Use Only", "Kein Firmen"])
def test_a_public_name_with_a_condition_word_is_still_marked(holder):
    text = T.mit("Copyright (c) 2025 " + holder)
    ident = ("someorg", "tool", w.OwnerName(holder))
    assert w.license_from_text(text, identity=ident) is None \
        or w.licence_to_check(text, identity=ident) is True


@pytest.mark.parametrize("value, ok", [
    ("Sahib Singh", "Sahib Singh"), ("  Sahib   Singh ", "Sahib Singh"), ("Jean-Luc O'Neil", "Jean-Luc O'Neil"),
    ("Søren Kierkegaard", "Søren Kierkegaard"), ("", None), (None, None), (42, None),
    ("Sahib; no firms", None), ("Sahib\nSingh", "Sahib Singh"), ("x" * 61, None),
    ("名前", None), ("Sahib 🎵", None), ("<b>Sahib</b>", None), ("Sahib (no corps)", None),
])
def test_what_a_public_name_is(value, ok):
    got = w.OwnerName.from_host(value)
    assert (None if got is None else str(got)) == ok


def test_a_public_name_counts_only_as_an_identity_element_after_the_owner():
    """The plain string "Sahib Singh" given as the repository name does not make it the owner."""
    assert w.licence_to_check(SAHIB, identity=("SubchiBeats", "Sahib Singh")) is True


def _users(name="Sahib Singh", login="SubchiBeats") -> bytes:
    return json.dumps({"login": login, "name": name, "type": "User"}).encode()


def _archive(monkeypatch, files, users=None, *, budget=None, owner_file=False):
    T._fresh_cache()
    net = D._zip_net(D._zip(files))
    if users is not None:
        net.extra.append((r"^https://api\.github\.com/users/SubchiBeats$", users))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    it = copy.deepcopy(D.item())
    it["size"] = 300
    got = w.details(D.found_for(it), budget=budget or D.budget())
    return got, net


def test_end_to_end_the_bunny_line_has_no_false_warning(monkeypatch, recent):  # noqa: F811
    got, net = _archive(monkeypatch, dict(D.SUBCHI_FILES), _users())
    assert got["repo"]["license_read"] == "MIT"
    assert got["repo"]["license_check"] is False and got["repo"]["license_check_why"] == ""
    assert trust_floor.CHECK_LICENCE not in (got["trust"].get("reasons") or [])
    rec = copy.deepcopy(got)
    rec["repo"]["stars"] = 5000
    assert trust_floor.pretick_allowed(rec) is True
    asked = [c["url"] for c in net.calls if c["host"] == "api.github.com"]
    assert asked == ["https://api.github.com/users/SubchiBeats"]
    assert net.calls[-1]["max_bytes"] == github.ACCOUNT_MAX_BYTES


@pytest.mark.parametrize("answer", [
    _users(name="Someone Else"), _users(login="SomeoneElse"), _users(name=None),
    _users(name="Sahib; no firms"), b"not json", b"[]", b"{}",
])
def test_end_to_end_any_other_answer_keeps_the_mark(monkeypatch, recent, answer):  # noqa: F811
    got, _ = _archive(monkeypatch, dict(D.SUBCHI_FILES), answer)
    assert got["repo"]["license_read"] == "MIT"
    assert got["repo"]["license_check"] is True
    assert got["repo"]["license_check_why"] == "stranger"


def test_no_answer_keeps_the_mark(monkeypatch, recent):  # noqa: F811
    got, _ = _archive(monkeypatch, dict(D.SUBCHI_FILES))            # 404
    assert got["repo"]["license_check"] is True


def test_the_name_is_asked_once_a_build_per_account(monkeypatch, recent):  # noqa: F811
    b = D.budget()
    _, net1 = _archive(monkeypatch, dict(D.SUBCHI_FILES), _users(), budget=b)
    got, net2 = _archive(monkeypatch, dict(D.SUBCHI_FILES), _users(), budget=b)
    assert got["repo"]["license_check"] is False                      # remembered
    assert not [c for c in net2.calls if c["host"] == "api.github.com"]
    assert b.spent().get("github_core_name") == 1


def test_never_asked_for_a_find_buildbot_cannot_add(monkeypatch, recent):  # noqa: F811
    files = {"README.md": D.SUBCHI_FILES["README.md"], "LICENSE": D.SUBCHI_FILES["LICENSE"],
             "package.json": b'{"name": "x", "dependencies": {"express": "4"}}'}
    got, net = _archive(monkeypatch, files, _users())
    assert got["mode_guess"] == "server_app"
    assert got["repo"]["license_check"] is True
    assert not [c for c in net.calls if c["host"] == "api.github.com"]


def test_never_asked_for_the_owners_own_line(monkeypatch, recent):  # noqa: F811
    lic = T.MIT_LICENSE.replace("Sahib Singh", "SubchiBeats").encode()
    got, net = _archive(monkeypatch, dict(D.SUBCHI_FILES, LICENSE=lic), _users())
    assert got["repo"]["license_check"] is False
    assert not [c for c in net.calls if c["host"] == "api.github.com"]


def test_never_asked_when_the_core_allowance_is_low(monkeypatch, recent):  # noqa: F811
    caps = dict(search_budget.CAPS_WIZARD, github_core=github.NAME_CORE_RESERVE)
    got, net = _archive(monkeypatch, dict(D.SUBCHI_FILES), _users(), budget=D.budget(caps=caps))
    assert got["repo"]["license_check"] is True
    assert not [c for c in net.calls if c["host"] == "api.github.com"]


def test_the_name_pool_is_capped_per_build():
    assert search_budget.CAPS_WIZARD["github_core_name"] == 2
    assert search_budget.CAPS_ADD["github_core_name"] == 1
    assert github.NAME_CORE_RESERVE > github.LISTING_CORE_RESERVE


def test_a_name_never_changes_what_licence_is_read(monkeypatch, recent):  # noqa: F811
    """With a public name, a licence with a condition is still custom."""
    lic = (T.MIT_LICENSE + "\nNon-commercial use only.\n").encode()
    got, _ = _archive(monkeypatch, dict(D.SUBCHI_FILES, LICENSE=lic), _users())
    assert got["repo"]["license_read"] == w.CUSTOM_LICENSE
    assert got["trust"]["pretick_ok"] is False


# ---------------------------------------------------------------------------
# 7. the keyless allowance used up: "not checked", said plainly
# ---------------------------------------------------------------------------

def _file_by_file(monkeypatch, caps):
    T._fresh_cache()
    net = D.FakeNet(dict(D.SUBCHI_FILES), refs=D.read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    it = copy.deepcopy(D.item())
    it["size"] = github.ZIP_MAX_REPO_KB + 1
    return w.details(D.found_for(it), budget=D.budget(caps=caps)), net


def test_no_listing_for_want_of_allowance_is_not_checked(monkeypatch, recent):  # noqa: F811
    caps = dict(search_budget.CAPS_WIZARD, github_core=github.LISTING_CORE_RESERVE)
    got, net = _file_by_file(monkeypatch, caps)
    assert not [c for c in net.calls if "/git/trees/" in c["url"]]
    assert got["repo"]["license_read"] == w.UNCHECKED_LICENSE
    assert meaning.has_licence(got) is False                     # never usable
    assert got["trust"]["floor_ok"] is False and got["trust"]["pretick_ok"] is False
    got["integrable"] = True
    assert meaning.note_reason(got) == meaning.NOTE_LICENCE_NOT_CHECKED


def test_a_listing_that_fails_for_another_reason_is_still_custom(monkeypatch, recent):  # noqa: F811
    T._fresh_cache()
    net = D.FakeNet(dict(D.SUBCHI_FILES), refs=D.read("info_refs_subchibeats.pktline"),
                    listing=False)                                # the listing answers 404
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    it = copy.deepcopy(D.item())
    it["size"] = github.ZIP_MAX_REPO_KB + 1
    got = w.details(D.found_for(it), budget=D.budget())
    assert got["repo"]["license_read"] == w.CUSTOM_LICENSE


def test_the_plain_words_and_their_plural():
    from puffpai.wizard import find_parts
    assert "GitHub" in meaning.NOTE_LICENCE_NOT_CHECKED
    assert "terms" not in meaning.NOTE_LICENCE_NOT_CHECKED
    assert meaning.NOTE_LICENCE_NOT_CHECKED in find_parts.NOTE_REASONS_MANY
    for text in (meaning.NOTE_LICENCE_NOT_CHECKED,
                 find_parts.NOTE_REASONS_MANY[meaning.NOTE_LICENCE_NOT_CHECKED]):
        assert "—" not in text and "door" not in text.lower()


def test_a_server_app_says_server_not_the_allowance():
    """The plan names what keeps it out: a server app is a server app,
    whatever its licence; "not checked" only where the licence decides."""
    import measure_goals as A
    rec = _gh(license_reported="LGPL-3.0", license_read=w.UNCHECKED_LICENSE)
    rec["mode_guess"], rec["integrable"] = "server_app", False
    assert meaning.note_reason(rec) == meaning.NOTE_SERVER
    rec["mode_guess"] = "node_lib"
    assert meaning.note_reason(rec) == meaning.NOTE_LIBRARY
    f = {"id": "github:o/p", "source": "github", "part": "code-app", "mode": "server_app",
         "integrable": False, "trust": {}, "meaning": {"fit": 0.6},
         "facts": {"stars": 900, "license_reported": "LGPL-3.0",
                   "license_read": w.UNCHECKED_LICENSE}}
    assert A.proof_reason(f, set()) == "cannot be added in round 3 (server_app)"


def test_harness_names_the_allowance_and_does_not_call_it_proven():
    import measure_goals as A
    f = {"id": "github:o/p", "source": "github", "part": "code-app", "mode": "static_web",
         "integrable": True, "trust": {}, "meaning": {"fit": 0.6},
         "facts": {"stars": 900, "license_reported": "LGPL-3.0",
                   "license_read": w.UNCHECKED_LICENSE}}
    assert A.proof_reason(f, set()) == A.ALLOWANCE_REASON
    r = {"plan": {"items": []}, "needs": {"jobs": []}, "bar": {},
         "evidence": {"finds": [f]}}
    proof = A.failure_proof(r)
    assert proof["proven"] is False and proof["allowance_blocked"] == 1
    assert "allowance" in proof["verdict"] and "not a product failure" in proof["verdict"]


# ---------------------------------------------------------------------------
# 8. read but undecided is "other"; no core call on a big code find's tree
# ---------------------------------------------------------------------------

SHA = "a" * 40


def _read_rec(pinned):
    return {"source": "github", "part": "code-app",
            "repo": {"host": "github.com", "pinned_commit": pinned, "language": "HTML"},
            "details": dict(github.S.empty_details(), root_markers=["README.md"])}


def test_a_project_read_whose_kind_nothing_decides_is_other():
    assert code_mode.guess(_read_rec(SHA)) == "other"
    assert code_mode.integrable(_read_rec(SHA), "other") is False


@pytest.mark.parametrize("pinned", [None, "", "x" * 40, SHA.upper(), SHA[:39]])
def test_without_a_pin_it_stays_unknown(pinned):
    assert code_mode.guess(_read_rec(pinned)) == "unknown"


def test_a_read_cut_short_stays_unknown(monkeypatch, recent):  # noqa: F811
    """Probing stopped before the kind was decided: "unknown", not "other"."""
    T._fresh_cache()
    files = {"README.md": b"# x\n", "LICENSE": D.SUBCHI_FILES["LICENSE"]}
    net = D.FakeNet(files, refs=D.read("info_refs_subchibeats.pktline"), listing=False)
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    it = copy.deepcopy(D.item())
    it["size"] = github.ZIP_MAX_REPO_KB + 1
    it["language"] = "JavaScript"
    caps = dict(search_budget.CAPS_WIZARD, github_raw=3)
    got = w.details(D.found_for(it), budget=D.budget(caps=caps))
    assert got["repo"]["pinned_commit"] and got["mode_guess"] == "unknown"


def test_a_big_code_find_spends_no_core_call_on_its_tree(monkeypatch, recent):  # noqa: F811
    T._fresh_cache()
    files = {"README.md": b"# x\n", "LICENSE": D.SUBCHI_FILES["LICENSE"]}
    net = D.FakeNet(files, refs=D.read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    it = copy.deepcopy(D.item())
    it["size"] = github.ZIP_MAX_REPO_KB + 1
    b = D.budget()
    got = w.details(D.found_for(it), budget=b)
    assert not [c for c in net.calls if "recursive=1" in c["url"]]
    assert b.spent().get("github_core_tree", 0) == 0
    assert got["mode_guess"] == "other"


def test_a_big_data_find_still_gets_its_tree_count(monkeypatch, recent):  # noqa: F811
    T._fresh_cache()
    tree = json.dumps({"tree": [{"type": "blob", "path": f"d/{n}.csv"} for n in range(40)]
                       + [{"type": "blob", "path": "README.md"}]}).encode()
    files = {"README.md": b"# x\n", "LICENSE": D.SUBCHI_FILES["LICENSE"]}
    net = D.FakeNet(files, refs=D.read("info_refs_subchibeats.pktline"),
                    extra=[(r"recursive=1", tree)])
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    it = copy.deepcopy(D.item())
    it["size"] = github.ZIP_MAX_REPO_KB + 1
    got = w.details(D.found_for(it, part="data-template"), budget=D.budget())
    assert [c for c in net.calls if "recursive=1" in c["url"]]
    assert got["mode_guess"] == "data_template"


def test_harness_reads_other_as_read():
    import measure_goals as A
    f = {"id": "github:o/p", "source": "github", "part": "code-app", "mode": "other",
         "integrable": False, "trust": {}, "meaning": {"fit": 0.6},
         "facts": {"stars": 3, "license_reported": "MIT", "license_read": "MIT"}}
    assert A.proof_reason(f, set()) == "cannot be added in round 3 (other)"


def test_a_licence_written_twice_in_an_archive_is_never_read(monkeypatch, recent):  # noqa: F811
    """A clean LICENSE, then the same path again with a condition. Without
    this, the first is read and pre-ticked."""
    import io
    import warnings
    import zipfile
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for path, body in D.SUBCHI_FILES.items():
                z.writestr(f"wedding-planner-main/{path}", body)
            z.writestr("wedding-planner-main/LICENSE",
                       D.SUBCHI_FILES["LICENSE"] + b"\nNon-commercial use only.\n")
            z.comment = D.SHA.encode()
    reader = github._ZipReader.from_bytes(buf.getvalue(), "SubchiBeats", "wedding-planner", None)
    assert "LICENSE" not in reader.members and "LICENSE" in reader.unreadable
    assert reader.get("LICENSE") is None and "index.html" in reader.members
    T._fresh_cache()
    net = D._zip_net(buf.getvalue())
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    it = copy.deepcopy(D.item())
    it["size"] = 300
    got = w.details(D.found_for(it), budget=D.budget())
    assert got["repo"]["license_read"] == w.CUSTOM_LICENSE
    assert got["trust"]["pretick_ok"] is False


# ---------------------------------------------------------------------------
# 9. the 90 s margin, and the per-host pace
# ---------------------------------------------------------------------------

def test_the_plan_aims_well_inside_the_limit():
    from puffpai.wizard import find_parts
    assert find_parts.PLAN_AIM_S <= find_parts.SEARCH_DEADLINE_S - 10
    assert find_parts.SCORING_MULTI_S == 42.0
    # the brain's last question ends by the aim, with room to build the lines
    assert find_parts.PLAN_AIM_S - find_parts.JUDGE_TICKS_RESERVE_S \
        + find_parts.JUDGE_TIMEOUT_S <= find_parts.PLAN_AIM_S


def test_every_brain_check_stops_by_the_aim_not_the_deadline():
    import inspect
    from puffpai.wizard import find_parts
    src = inspect.getsource(find_parts.plan_items)
    assert "deadline - JUDGE" not in src
    assert src.count("aim - JUDGE_RESERVE_S") == 2 and src.count("aim - JUDGE_TICKS_RESERVE_S") == 2
    assert "aim = t0 + min(PLAN_AIM_S, SEARCH_DEADLINE_S)" in src


def test_the_warmer_measures_its_backlog(monkeypatch):
    from puffpai.wizard import find_parts
    monkeypatch.setattr(find_parts, "_warm", lambda texts: time.sleep(0.002 * len(texts)))
    warm = find_parts._Warmer()
    try:
        assert warm.backlog_s() == 0.0                     # nothing measured: never held
        warm.add_texts(["a"] * find_parts.WARM_RATE_MIN_TEXTS)
        deadline = time.monotonic() + 5
        while warm._pending and time.monotonic() < deadline:
            time.sleep(0.01)
        assert warm.backlog_s() == 0.0
        monkeypatch.setattr(find_parts, "_warm", lambda texts: time.sleep(0.5))
        warm.add_texts(["b"] * 4000)                       # at the measured speed: seconds
        assert warm.backlog_s() > find_parts.WARM_BACKLOG_S
    finally:
        warm.finish(wait=False)


def test_no_look_starts_while_the_helper_is_behind_and_the_looks_wait(monkeypatch):
    from puffpai.wizard import find_parts
    started: list[float] = []

    class Wtl:
        def details(self, row, *, budget):
            started.append(time.monotonic())
            return dict(row)

    looks = find_parts._CloserLooks.__new__(find_parts._CloserLooks)
    held = {"until": time.monotonic() + 0.6}

    class Warm:
        def backlog_s(self):
            return 99.0 if time.monotonic() < held["until"] else 0.0

        def add_texts(self, texts):
            pass

        def finish(self, wait=True):
            pass

    import threading
    looks.wtl, looks.budget, looks.until = Wtl(), None, time.monotonic() + 3
    looks.jobs, looks.kind = [{"id": "j1"}], ""
    looks.read, looks._cv, looks._pending = {}, threading.Condition(), []
    row = {"id": "github:o/p", "source": "github"}
    looks._cands, looks._rows = {"j1": [(0.9, "github:o/p")]}, {"github:o/p": row}
    looks._started, looks._adding, looks._closed, looks._scored = set(), False, False, True
    looks._looks, looks._busy, looks._warm, looks._threads = {}, {}, Warm(), []
    t0 = time.monotonic()
    looks._look_loop()
    assert len(started) == 1 and started[0] - t0 >= 0.5     # waited, then read; never gave up


def test_raw_is_four_a_second_and_every_other_host_one():
    """The per-host pace; the full set is tests/test_per_host_request_pace.py."""
    assert source_fetch.HOST_MIN_INTERVAL_BY_HOST == {"raw.githubusercontent.com": 0.25}
    assert source_fetch.host_min_interval("raw.githubusercontent.com") == 0.25
    for host in ("codeload.github.com", "github.com", "api.github.com", "codeberg.org",
                 "x.raw.githubusercontent.com"):
        assert source_fetch.host_min_interval(host) == 1.0
