"""Long licence texts, each with its test.

Recorded answers and fakes only, no network. What must hold:

* a long licence (Apache, the GNU licences, MPL, CC0, OFL, Creative Commons
  4.0) reads as itself only when every word is its own: a restriction put
  anywhere inside it, one word changed or one word left out reads None; the
  known wording differences between real copies still read (the FSF's old
  addresses, the sample signer, "&" or "and", the Apache owner line filled
  in with real copyright lines); a missing or broken words file recognises
  no long licence at all;
* ``details`` keeps no code fact on a picture, and nothing is pre-ticked that
  cannot be added or was not read at a pinned commit;
* files in dot folders (``.github``) never make a project a data set;
* descriptions never end on a doubled full stop or an open bracket, and
  names never end mid-word or on a word that needs the next one;
* the "Start looking" words name only the places a search really sends one
  of her phrases to (PyPI only for an exact package name, Wikipedia only for
  a learn job), and each searcher's own check agrees with what it sends;
* Codeberg answers are kept only when the searched word stands as a word;
* one build reads every job's best finds before any job's twentieth.
"""
from __future__ import annotations

import copy
import datetime
import json
import os
import re
import sys

import pytest

from puffpai import source_fetch
from puffpai.catalogs import code_mode, trust_floor, where_to_look as w
from puffpai.catalogs import searchers as S
from puffpai.catalogs.searchers import codeberg, github, knowledge, pypi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_github_find_details as D  # noqa: E402
from test_descriptions_and_codeberg_search import Net, budget, fake_outbound, read  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def text(name: str) -> str:
    return read(name).decode("utf-8")


# ---------------------------------------------------------------------------
# long licences, word by word
# ---------------------------------------------------------------------------

LONG = {
    "license_Apache-2.0.txt": "Apache-2.0", "license_Apache-2.0_github.txt": "Apache-2.0",
    "license_MPL-2.0.txt": "MPL-2.0", "license_MPL-2.0_github.txt": "MPL-2.0",
    "license_GPL-3.0.txt": "GPL-3.0", "license_GPL-3.0_github.txt": "GPL-3.0",
    "license_AGPL-3.0.txt": "AGPL-3.0", "license_AGPL-3.0_github.txt": "AGPL-3.0",
    "license_LGPL-3.0.txt": "LGPL-3.0", "license_LGPL-3.0_github.txt": "LGPL-3.0",
    "license_GPL-2.0.txt": "GPL-2.0", "license_GPL-2.0_github.txt": "GPL-2.0",
    "license_LGPL-2.1.txt": "LGPL-2.1", "license_LGPL-2.1_github.txt": "LGPL-2.1",
    "license_CC0-1.0.txt": "CC0-1.0", "license_CC0-1.0_github.txt": "CC0-1.0",
    "license_OFL-1.1.txt": "OFL-1.1", "license_OFL-1.1_github.txt": "OFL-1.1",
    "license_CC-BY-4.0.txt": "CC-BY-4.0", "license_CC-BY-SA-4.0.txt": "CC-BY-SA-4.0",
    "license_CC-BY-NC-4.0.txt": "CC-BY-NC-4.0", "license_CC-BY-NC-SA-4.0.txt": "CC-BY-NC-SA-4.0",
    "license_CC-BY-NC-ND-4.0.txt": "CC-BY-NC-ND-4.0", "license_CC-BY-ND-4.0.txt": "CC-BY-ND-4.0",
}


def licence(name: str) -> str:
    # GitHub's OFL text starts with a copyright line for the owner to fill in.
    return text(name).replace("Copyright (c) [year] [fullname] ([email])",
                              "Copyright (c) 2021 Jane Doe")


@pytest.mark.parametrize("name", sorted(LONG))
def test_every_long_licence_text_reads_as_itself(name):
    assert w.license_from_text(licence(name)) == LONG[name]


def _paragraph_breaks(body: str) -> list[int]:
    return [m.start() for m in re.finditer(r"\n\s*\n", body)]


RESTRICTIONS = (
    "You may not use the Work for commercial purposes.",
    "Companies with more than 10 employees need to buy a seat.",
    "Nicht fuer kommerzielle Nutzung.",
)


@pytest.mark.parametrize("name", sorted(LONG))
@pytest.mark.parametrize("where", (0.25, 0.5, 0.75))
def test_a_restriction_anywhere_inside_a_long_licence_reads_none(name, where):
    body = licence(name)
    breaks = _paragraph_breaks(body)
    at = min(breaks, key=lambda b: abs(b - len(body) * where))
    for sentence in RESTRICTIONS:
        changed = body[:at] + "\n\n" + sentence + "\n\n" + body[at:]
        assert w.license_from_text(changed) is None, (name, where, sentence)


@pytest.mark.parametrize("name", sorted(LONG))
def test_one_word_changed_or_left_out_reads_none(name):
    body = licence(name)
    mid = len(body) // 2
    m = re.compile(r"\b[a-z]{4,}\b").search(body, mid)
    assert m
    assert w.license_from_text(body[:m.start()] + "zzzz" + body[m.end():]) is None
    assert w.license_from_text(body[:m.start()] + body[m.end():]) is None


def test_the_validators_three_bypasses_read_none():
    for name, sentence in (("license_Apache-2.0.txt",
                            "You may not use the Work for commercial purposes."),
                           ("license_GPL-3.0.txt",
                            "This program may not be used for commercial purposes."),
                           ("license_CC0-1.0.txt", "Non-commercial use only.")):
        body = text(name)
        i = body.find("\n\n", len(body) // 2)
        assert w.license_from_text(body[:i] + "\n\n" + sentence + "\n\n" + body[i:]) is None


@pytest.mark.parametrize("address", (
    "51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA",
    "59 Temple Place, Suite 330, Boston, MA  02111-1307  USA",
    "59 Temple Place - Suite 330, Boston, MA 02111-1307, USA",
    "675 Mass Ave, Cambridge, MA 02139, USA",
))
@pytest.mark.parametrize("name", ("license_GPL-2.0_github.txt", "license_LGPL-2.1_github.txt"))
def test_the_fsf_addresses_real_copies_carry_still_read(name, address):
    franklin = re.compile(r"51\s+Franklin\s+Street,\s+Fifth\s+Floor,\s+Boston,\s+MA\s+"
                          r"02110-1301\s+USA")
    original = text(name)
    spans = [m.span() for m in franklin.finditer(original)]
    assert len(spans) == 2                  # at the head and in the how-to-apply part
    assert w.license_from_text(franklin.sub(address, original)) == LONG[name]
    # ... but not with anything else where an address stands, in either place.
    for _start, end in spans:
        extra = original[:end] + ". Businesses need to buy a seat." + original[end:]
        assert w.license_from_text(extra) is None, end


@pytest.mark.parametrize("name", ("license_GPL-3.0.txt", "license_GPL-2.0_github.txt",
                                  "license_AGPL-3.0.txt", "license_LGPL-2.1.txt"))
def test_a_gnu_licence_without_its_how_to_apply_part_still_reads(name):
    body = text(name)
    cut = body.index("How to Apply These Terms")
    assert w.license_from_text(body[:cut]) == LONG[name]
    # Half of that part is not the licence.
    assert w.license_from_text(body[:cut + (len(body) - cut) // 2]) is None


APACHE = "license_Apache-2.0.txt"
OWNER = "Copyright [yyyy] [name of copyright owner]"


@pytest.mark.parametrize("filled, want", [
    (OWNER, "Apache-2.0"),
    ("Copyright 2019-2024 Acme Widgets, Inc.", "Apache-2.0"),
    # two owners, when not both the project: custom (the holders rule)
    ("Copyright 2019 Acme Widgets\n   Copyright (c) 2020 Jane Doe", None),
    ("Copyright 2019 Acme. Companies need to buy a seat.", None),
    ("Copyright 2019 Acme\n   Companies need to buy a seat.", None),
    ("Copyright 2019 Acme, non-commercial use only", None),
    ("Copyright 2019 Acme\n   Copyright 2020 B\n   Copyright 2021 C\n   Copyright 2022 D", None),
    ("", None),
])
def test_the_apache_owner_line_takes_only_real_copyright_lines(filled, want):
    body = text(APACHE)
    assert OWNER in body
    assert w.license_from_text(body.replace(OWNER, filled)) == want


def test_the_apache_licence_without_its_appendix_reads():
    body = text(APACHE)
    assert w.license_from_text(body.split("APPENDIX:")[0]) == "Apache-2.0"
    assert w.license_from_text("Copyright 2024 Jane Doe\n\n" + body) == "Apache-2.0"
    assert w.license_from_text(body.replace("\n", "\r\n")) == "Apache-2.0"
    assert w.license_from_text("﻿" + body) == "Apache-2.0"


def _mit_gap(filler: str) -> str:
    mit = text("subchibeats_LICENSE")            # "Copyright (c) 2026 Sahib Singh"
    return re.sub(r"(SHALL THE\s+)(.*?)(\s+BE LIABLE)",
                  lambda m: m.group(1) + filler + m.group(3), mit, count=1, flags=re.S)


@pytest.mark.parametrize("filler, want", [
    ("AUTHORS OR COPYRIGHT HOLDERS", "MIT"),
    ("SAHIB SINGH", "MIT"),                       # the holder of the file's own copyright line
    ("COMPANIES WITH MORE THAN TEN EMPLOYEES NEED TO BUY A SEAT", None),
    ("JANE DOE", None),                           # a name the file does not hold
    ("AUTHORS UNLESS YOU ARE A COMPANY", None),
])
def test_a_short_licence_names_only_its_own_holders(filler, want):
    assert w.license_from_text(_mit_gap(filler)) == want


def test_a_bsd_licence_may_name_the_holder_of_its_copyright_line():
    bsd = text("license_BSD-3-Clause.txt")                       # Copyright 2008 Fair Oaks Labs, Inc.
    named = re.sub(r"Neither the name of .*? nor the names",
                   "Neither the name of Fair Oaks Labs, Inc. nor the names", bsd, count=1, flags=re.S)
    assert w.license_from_text(named) == "BSD-3-Clause"
    other = re.sub(r"Neither the name of .*? nor the names",
                   "Neither the name of any company that has not paid nor the names", bsd,
                   count=1, flags=re.S)
    assert w.license_from_text(other) is None


def test_the_words_file_is_what_the_fixtures_give():
    # The words live in the registry file.
    from puffpai.catalogs import snapshot_build_code as B
    with open(w.registry_path(), encoding="utf-8") as fh:
        now = fh.read()
    assert now == B.registry_text(B.with_licence_words(json.loads(now))), \
        "run: py -3.14 -m puffpai.catalogs.snapshot_build_code licence"
    assert not os.path.exists(os.path.join(ROOT, "config", "licence_words.json"))
    editions = w.load_long_editions()
    assert {e[0] for e in editions} == set(LONG.values())
    # Only the registry's own rows are read as places to look.
    assert all("id" in row for row in w.load_registry())


def test_a_registry_without_licence_words_recognises_no_long_licence(tmp_path, monkeypatch):
    with open(w.registry_path(), encoding="utf-8") as fh:
        data = json.load(fh)
    data.pop(w.LICENCE_WORDS_KEY)
    path = tmp_path / "where_to_look.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert w.load_long_editions(str(path)) == []
    assert len(w.load_registry(str(path))) == len(data["rows"])
    monkeypatch.setitem(w._LONG_CACHE, "editions", w.load_long_editions(str(path)))
    assert w.license_from_text(text(APACHE)) is None


def test_licence_words_of_the_wrong_type_is_a_broken_registry(tmp_path):
    with open(w.registry_path(), encoding="utf-8") as fh:
        data = json.load(fh)
    data[w.LICENCE_WORDS_KEY] = ["not", "an", "object"]
    path = tmp_path / "where_to_look.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(w.RegistryError):
        w.load_registry(str(path))
    assert w.load_long_editions(str(path)) == []


@pytest.mark.parametrize("content", [
    None, "", "{", json.dumps({"schema": "other", "editions": []}),
    json.dumps({"schema": w.LICENCE_WORDS_SCHEMA,
                "editions": [{"id": "MIT", "elements": [{"w": "x"}]}]}),
    json.dumps({"schema": w.LICENCE_WORDS_SCHEMA,
                "editions": [{"id": "Apache-2.0", "elements": [{"w": "Not Words!"}]}]}),
    json.dumps({"schema": w.LICENCE_WORDS_SCHEMA,
                "editions": [{"id": "Apache-2.0", "elements": [{"cline": 99}]}]}),
    json.dumps({"schema": w.LICENCE_WORDS_SCHEMA,
                "editions": [{"id": "Apache-2.0", "elements": [{"gap": 5}]}]}),
])
def test_a_missing_or_broken_words_file_recognises_no_long_licence(tmp_path, monkeypatch, content):
    path = tmp_path / "licence_words.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert w.load_long_editions(str(path)) == []
    monkeypatch.setitem(w._LONG_CACHE, "editions", w.load_long_editions(str(path)))
    assert w.license_from_text(text(APACHE)) is None
    assert w.license_from_text(text("license_GPL-3.0.txt")) is None
    assert w.license_from_text(text("subchibeats_LICENSE")) == "MIT"     # short ones still read


def test_reading_long_licences_stays_quick():
    import time
    heavy = ("MIT License\n" * 55) + text("license_GPL-3.0.txt")
    t = time.perf_counter()
    for name in LONG:
        w.license_from_text(licence(name))
    w.license_from_text(heavy)
    w.license_from_text(text("license_AGPL-3.0.txt") * 5)
    assert time.perf_counter() - t < 2.0


@pytest.mark.parametrize("restricted", (False, True))
def test_a_restricted_apache_file_is_not_free_to_use_from_start_to_finish(
        monkeypatch, tmp_path, restricted):
    """The validator's end-to-end case: the SubchiBeats files with this LICENSE."""
    monkeypatch.setattr(S, "cache_dir", lambda: str(tmp_path))     # a file cache of its own
    monkeypatch.setattr(trust_floor, "_today", lambda: datetime.date(2026, 9, 27))
    lic = read(APACHE)
    if restricted:
        i = lic.find(b"\n\n", len(lic) // 2)
        lic = lic[:i] + b"\n\nYou may not use the Work for any commercial purpose.\n\n" + lic[i:]
    net = D.FakeNet(dict(D.SUBCHI_FILES, LICENSE=lic), refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net)
    rec = w.details(D.found_for(D.item()), budget=D.budget())
    assert "LICENSE" in net.raw_paths()
    if restricted:
        assert rec["repo"]["license_read"] == w.CUSTOM_LICENSE
        assert rec["trust"]["path"] != "static_small" and rec["trust"]["pretick_ok"] is False
        assert w.license_words(rec["repo"]["license_read"] or "") is None
    else:
        assert rec["repo"]["license_read"] == "Apache-2.0"


# ---------------------------------------------------------------------------
# forged facts
# ---------------------------------------------------------------------------

FORGED_DETAILS = {"entry_html": "index.html", "keeps_data": True, "inputs": 10,
                  "has_build_script": False, "code_bytes": 1000,
                  "runtime_hosts_guess": ["evil.example"], "root_markers": ["index.html"],
                  "package_json": {"scripts": [], "bin": True, "deps": []}}


def _forged(source, part, repo=None):
    rec = {"id": f"{source}:1", "source": source, "part": part, "name": "Tool",
           "what_it_is": "", "url": "", "mode_guess": "static_web", "integrable": True,
           "trust": {"floor_ok": True, "path": "stars", "pretick_ok": True, "reasons": []},
           "details": copy.deepcopy(FORGED_DETAILS), "repo": repo, "package": None,
           "picture": None}
    return rec


@pytest.mark.parametrize("source", ("commons", "openverse"))
def test_a_picture_keeps_no_code_fact(monkeypatch, source):
    monkeypatch.setattr(source_fetch, "fetch_small", Net())
    rec = _forged(source, "pictures", repo={"host": "github.com", "owner": "o", "name": "r",
                                            "stars": 999, "pinned_commit": "a" * 40,
                                            "license_read": "MIT"})
    rec["package"] = {"registry": "npm", "name": "x", "bin": ["x"]}
    rec["picture"] = {"license": "CC0", "credit": "x", "thumb_local": None,
                      "full_host": "upload.wikimedia.org"}
    got = w.details(rec, budget=budget())
    assert got["mode_guess"] == "other" and got["details"] is None
    assert got["repo"] is None and got["package"] is None
    assert got["integrable"] is True and got["trust"]["pretick_ok"] is False


@pytest.mark.parametrize("source, host", (("github", "github.com"), ("codeberg", "codeberg.org"),
                                          ("awesome_selfhosted", "github.com"),
                                          ("github", "evil.example")))
def test_a_find_not_read_at_a_pin_gets_no_trust_from_its_record(monkeypatch, source, host):
    monkeypatch.setattr(source_fetch, "fetch_small", Net())          # every answer: 404
    repo = {"host": host, "owner": "o", "name": "r", "default_branch": "main",
            "pinned_commit": "a" * 40, "license_read": "MIT", "license_reported": "MIT",
            "stars": 999, "pushed_at": "2026-09-01", "archived": False, "fork": False,
            "size_kb": 10}
    got = w.details(_forged(source, "code-app", repo=repo), budget=budget())
    assert got["integrable"] is False and got["mode_guess"] == "unknown"
    assert got["trust"]["floor_ok"] is False and got["trust"]["path"] == ""
    assert got["trust"]["pretick_ok"] is False and w.NOT_READ in got["trust"]["reasons"]


def test_nothing_that_cannot_be_added_is_pre_ticked(monkeypatch):
    monkeypatch.setattr(source_fetch, "fetch_small", Net())
    repo = {"host": "gitlab.com", "owner": "o", "name": "r", "stars": 999,
            "pushed_at": "2026-09-01", "license_reported": "MIT"}
    got = w.details(_forged("awesome_selfhosted", "code-app", repo=repo), budget=budget())
    assert got["integrable"] is False and got["trust"]["pretick_ok"] is False


# ---------------------------------------------------------------------------
# data sets
# ---------------------------------------------------------------------------

def _tree_find(paths):
    d = S.empty_details()
    d["root_markers"] = ["README.md", "LICENSE"]
    d["tree"] = github.count_paths(paths)
    return S.new_found(source="github", part="data-template", id="github:x/y", name="y",
                       url="https://github.com/x/y",
                       repo={"host": "github.com", "owner": "x", "name": "y",
                             "default_branch": "main", "language": None, "topics": []},
                       details=d)


@pytest.mark.parametrize("folder", (".github", ".vscode", ".devcontainer", "a/.config"))
def test_files_in_dot_folders_never_make_a_data_set(folder):
    paths = ["README.md", "LICENSE"] + [f"{folder}/w{i}.json" for i in range(30)]
    assert github.count_paths(paths)["data"] == 0
    rec = _tree_find(paths)
    mode = code_mode.guess(rec)
    assert mode != "data_template" and not code_mode.integrable(rec, mode)


def test_real_data_files_still_make_a_data_set():
    rec = _tree_find(["README.md", "LICENSE", ".github/ci.yml"]
                     + [f"data/d{i}.csv" for i in range(30)])
    assert code_mode.guess(rec) == "data_template"


# ---------------------------------------------------------------------------
# descriptions and names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, want", [
    ("A free book by W. M..", "A free book by W. M."),
    ("A free book by Higgin, L..", "A free book by Higgin, L."),
    ("Tools...", "Tools..."),
    ("Plain (draft 2, June 2026", "Plain (draft 2, June 2026)."),     # too little left to cut
    ("A long enough text (draft 2, June 2026", "A long enough text."),
    ("Closed (draft) text", "Closed (draft) text."),
    ("Is [open", "Is [open]."),
    ("A tool for lists (x) and (y", "A tool for lists (x)."),
])
def test_no_doubled_full_stop_or_open_bracket(raw, want):
    assert S.plain_text(raw) == want


def test_a_cut_description_never_leaves_a_bracket_open():
    raw = ("MCP wrapping C2PA + watermarking as an EU Code of Practice on AI content marking "
           "(draft 2, June 2026) with more words after it to make the cut land inside")
    got = S.plain_text(raw, 100)
    assert got.count("(") == got.count(")") and got.endswith(".") and ".." not in got


def test_a_long_name_ends_on_a_whole_word():
    name = ("The Compleat Cook Expertly Prescribing the Most Ready Wayes, Whether Italian, "
            "Spanish or French, for Dressing of Flesh and Fish")
    got = S.new_found(source="gutenberg", part="knowledge", id="gutenberg:1", name=name)["name"]
    assert len(got) <= 100 and name.startswith(got) and name[len(got)] in " ,"
    assert got.split()[-1].lower() not in ("for", "or", "the", "of", "a")
    assert not got.endswith(".")


def test_text_a_source_stored_cut_ends_on_a_whole_word():
    cut = "The Compleat Cook Expertly Prescribing the Most Ready Wayes, Whether Italian, Spanish or French, for"
    assert len(cut) == 100
    assert S.whole_words(cut, 100) == ("The Compleat Cook Expertly Prescribing the Most Ready "
                                       "Wayes, Whether Italian, Spanish or French")
    assert S.whole_words("The Wesleyan Methodist Pulpit in Malvern Sermons Preached at the "
                         "Opening Services of the Wesleyan Me", 100).endswith("of the Wesleyan")
    assert S.whole_words("short title", 100) == "short title"
    assert S.whole_words("x" * 99 + ".", 100) == "x" * 99 + "."


def test_every_gutenberg_title_the_snapshot_cut_ends_on_a_whole_word():
    rows = knowledge._gutenberg_rows()
    long_ones = [str(b.get("title") or "") for b in rows
                 if len(str(b.get("title") or "")) >= knowledge.GUTENBERG_TITLE_CUT]
    assert len(long_ones) > 1000
    for title in long_ones:
        got = S.whole_words(title, knowledge.GUTENBERG_TITLE_CUT)
        assert title.startswith(got) and (len(got) == len(title) or title[len(got)] in " ,;:-")
        assert got.split()[-1].lower() not in S._DANGLING if got else True


# ---------------------------------------------------------------------------
# the places named are the places asked
# ---------------------------------------------------------------------------

def _needs(*jobs):
    return {"schema": "needs/v1", "jobs": [dict(j, ticked=j.get("ticked", True)) for j in jobs]}


def _job(jid, kinds, words, *, learn=False, ticked=True):
    return {"id": jid, "name": words.title(), "learn": learn, "ticked": ticked,
            "parts": [{"kind": k, "search": [words]} for k in kinds]}


def test_pypi_is_named_only_for_an_exact_package_name(monkeypatch):
    oq = fake_outbound(monkeypatch)
    row = w.row_by_id("pypi")
    job = _job("j1", ["cli"], "chore chart")
    assert pypi.would_send(job, [oq.Sendable("chore chart", "j1")], kind="cli", row=row) is False
    job = _job("j1", ["cli"], "csvkit")
    assert pypi.would_send(job, [oq.Sendable("csvkit", "j1")], kind="cli", row=row) is True
    assert pypi.would_send(job, [oq.Sendable("csvkit", "j1")], kind="code-app", row=row) is False
    labels = w.asked_labels(_needs(_job("j1", ["cli"], "chore chart")),
                            [oq.Sendable("chore chart", "j1")])
    assert "PyPI" not in labels and "npm" in labels
    labels = w.asked_labels(_needs(_job("j1", ["cli"], "csvkit")), [oq.Sendable("csvkit", "j1")])
    assert "PyPI" in labels


def test_wikipedia_is_named_only_for_a_learn_job(monkeypatch):
    oq = fake_outbound(monkeypatch)
    phrase = [oq.Sendable("chess openings", "j1")]
    assert "Wikipedia" not in w.asked_labels(
        _needs(_job("j1", ["knowledge"], "chess openings")), phrase)
    assert "Wikipedia" in w.asked_labels(
        _needs(_job("j1", ["knowledge"], "chess openings", learn=True)), phrase)


def test_only_ticked_jobs_searched_kinds_and_their_own_phrases_count(monkeypatch):
    oq = fake_outbound(monkeypatch)
    needs = _needs(_job("j1", ["code-app"], "chore chart"),
                   _job("j2", ["pictures"], "wedding flowers"),
                   _job("j3", ["data-template"], "family budget", ticked=False))
    sent = [oq.Sendable("chore chart", "j1"), oq.Sendable("wedding flowers", "j2"),
            oq.Sendable("family budget", "j3")]
    assert w.asked_labels(needs, sent, kinds=("code-app", "data-template")) == \
        ["GitHub", "Codeberg", "npm"]
    all_kinds = w.asked_labels(needs, sent)
    assert "Wikimedia Commons" in all_kinds or any("Commons" in x for x in all_kinds)
    assert not any("Hugging" in x for x in all_kinds)          # its job is not ticked
    # A job with no phrase of its own names nothing.
    assert w.asked_labels(needs, [oq.Sendable("chore chart", "j9")]) == []
    # The subject is searched only beside a searched job.
    subject = oq.Sendable("chess coach", "subject", True)
    assert w.asked_labels(_needs(_job("j2", ["pictures"], "wedding flowers")), [subject],
                          kinds=("code-app",)) == []
    assert w.asked_labels(needs, [subject], kinds=("code-app",)) == ["GitHub", "Codeberg"]


_CASES = [
    (_job("j1", ["code-app"], "chore chart"), "code-app", ["chore chart"]),
    (_job("j1", ["code-app"], "chess"), "code-app", ["chess"]),
    (_job("j1", ["cli"], "chore chart"), "cli", ["chore chart"]),
    (_job("j1", ["cli"], "csvkit"), "cli", ["csvkit"]),
    (_job("j1", ["data-template"], "family budget"), "data-template", ["family budget"]),
    (_job("j1", ["data-template"], "csv"), "data-template", ["csv"]),
    (_job("j1", ["pictures"], "wedding flowers"), "pictures", ["wedding flowers"]),
    (_job("j1", ["knowledge"], "chess openings"), "knowledge", ["chess openings"]),
    (_job("j1", ["knowledge"], "chess openings", learn=True), "knowledge", ["chess openings"]),
    (_job("j1", ["knowledge"], "zzqx wibble", learn=True), "knowledge", ["zzqx wibble"]),
    (_job("j1", ["code-app"], "chore chart"), "code-app", ["wedding flowers"]),
    ({"id": "subject", "name": "chess coach"}, "subject", ["chess coach"]),
]


@pytest.mark.parametrize("case", range(len(_CASES)))
def test_each_searchers_own_check_agrees_with_what_it_sends(monkeypatch, case):
    oq = fake_outbound(monkeypatch)
    job, kind, words = _CASES[case]
    hint = "subject" if job["id"] == "subject" else "j1"
    phrases = [oq.Sendable(p, hint, hint == "subject") for p in words]
    for row in w.registry():
        if row["role"] != "live" or not row["enabled"] or kind not in row["kinds"] \
                or w.adapter_fn(row) is None or not w.key_ready(row):
            continue
        net = Net()
        monkeypatch.setattr(source_fetch, "fetch_small", net)
        said = w.would_send(row, job, phrases, kind) and w._asks(row, job)
        w.adapter_fn(row)(job, phrases, kind=kind, budget=budget(), row=row, progress=None)
        sent = [c for c in net.calls if "robots.txt" not in c["url"]]
        assert bool(sent) == said, (row["id"], kind, words, said, len(sent))


def test_the_places_asked_for_a_plan_name_only_those_really_asked(monkeypatch):
    # The "then asks ..." line: asked_labels over the phrases the
    # plan would send.
    oq = fake_outbound(monkeypatch)
    needs = _needs(_job("j1", ["code-app", "cli"], "chore chart"),
                   _job("j2", ["knowledge"], "chore rules"))
    sent = [oq.Sendable("chore chart", "j1"), oq.Sendable("chore rules", "j2")]
    labels = w.asked_labels(needs, sent)
    assert "GitHub" in labels and "PyPI" not in labels and "Wikipedia" not in labels


# ---------------------------------------------------------------------------
# Codeberg matches inside words
# ---------------------------------------------------------------------------

def test_codeberg_keeps_only_whole_word_hits(monkeypatch):
    body = read("codeberg_search_chore.json")
    monkeypatch.setattr(source_fetch, "fetch_small",
                        Net([(r"codeberg\.org/api/v1/repos/search\?q=chore&", body)]))
    got = codeberg._run_search("chore", part="code-app", budget=budget(),
                               row=w.row_by_id("codeberg"), progress=None)
    ids = {r["id"] for r in got}
    for gone in ("codeberg:kita/nachorez", "codeberg:sunshine-it/zerotrust",
                 "codeberg:sicchio/hackingchoreography", "codeberg:3d/Anchored"):
        assert gone not in ids
    for kept in ("codeberg:vishalscodes/chore-counter", "codeberg:WillyWompel/ChoreStrike",
                 "codeberg:sama-gharib/corvees", "codeberg:Kehvarl/StarChores"):
        assert kept in ids
    assert len(got) < len(json.loads(body)["data"])


@pytest.mark.parametrize("item, word, want", [
    ({"name": "chorizard", "description": ""}, "chore", False),
    ({"name": "choreography"}, "chore", False),
    ({"name": "x", "description": "hardware-anchored"}, "chore", False),
    ({"name": "MoreChores"}, "chore", True),
    ({"name": "chore_list"}, "chore", True),
    ({"name": "x", "topics": ["chores"]}, "chore", True),
    ({"name": "x", "description": "Keep your boxes"}, "box", True),
    (None, "chore", False),
])
def test_whole_word_hit(item, word, want):
    assert codeberg.whole_word_hit(item, word) is want


# ---------------------------------------------------------------------------
# reading many jobs within one deadline
# ---------------------------------------------------------------------------

def test_read_order_takes_every_jobs_best_first():
    a = [{"id": f"a{i}"} for i in range(30)]
    b = [{"id": f"b{i}"} for i in range(3)] + [{"id": "a0"}]
    c = [{"id": f"c{i}"} for i in range(25)]
    got = [f["id"] for f in w.read_order([("j1", a), ("j2", b), ("j3", c)])]
    assert got[:6] == ["a0", "b0", "c0", "a1", "b1", "c1"]
    assert got.count("a0") == 1
    assert sum(1 for x in got if x.startswith("a")) == w.READ_PER_JOB
    assert sum(1 for x in got if x.startswith("c")) == w.READ_PER_JOB
    assert [f["id"] for f in w.read_order({"j": a}, per_job=2)] == ["a0", "a1"]
    assert w.read_order([]) == []


def test_read_order_reads_finds_that_can_pass_the_floor_first():
    def gh(i, lic):
        return {"id": f"github:o/r{i}", "url": f"https://github.com/o/r{i}",
                "repo": {"host": "github.com", "owner": "o", "name": f"r{i}",
                         "license_reported": lic}}
    finds = [gh(0, None), gh(1, "MIT"), gh(2, None), gh(3, "GPL-3.0"),
             {"id": "codeberg:o/x", "repo": {"host": "codeberg.org", "license_reported": None}}]
    got = [f["id"] for f in w.read_order([("j", finds)])]
    assert got == ["github:o/r1", "github:o/r3", "codeberg:o/x", "github:o/r0", "github:o/r2"]
    # A find GitHub has not said anything about is never put last.
    unknown = {"id": "github:o/u", "repo": {"host": "github.com"}}
    assert w.read_order([("j", [gh(0, None), unknown])])[0]["id"] == "github:o/u"
