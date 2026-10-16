"""Licence holder lines and search order, each with its test.

Recorded answers and fakes only, no network. What must hold:

* a copyright line can no longer carry a condition next to a licence: the
  holders a file names are read together, and two or more holders must all
  be the project itself (its owner and repository names, from the find's
  address, never from the file); one holder alone may be any name. Web and
  e-mail addresses beside the licence must be made of known words. So every
  "name, then more capitalised words" line reads as custom, in any language;
* a licence file that was read and is not one BuildBot knows is recorded as
  custom, so the licence the host reports never stands in for it (the stars
  path used GitHub's "MIT" for an MIT file with a condition in its copyright
  line);
* a real capital letter next to a closing mark is never read again as
  UTF-8, while text read the wrong way still is;
* data jobs get the generic shapes too: a ``topic:`` search and a README
  search, on spare budget, 100 finds a search;
* one job's places are asked at once, and ``details_many`` returns by its
  deadline, not after it.
"""
from __future__ import annotations

import copy
import datetime
import os
import sys
import time

import pytest

from puffpai import source_fetch
from puffpai.catalogs import searchers as S, trust_floor, where_to_look as w
from puffpai.catalogs.searchers import github

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_github_find_details as D  # noqa: E402
from test_descriptions_and_codeberg_search import budget, fake_outbound  # noqa: E402
from test_searchers_query_shapes import gh_row  # noqa: E402

MIT_LICENSE = D.read("subchibeats_LICENSE").decode("utf-8")
MIT_BODY = MIT_LICENSE.split("\n\n", 2)[2]
PROJECT = ("SubchiBeats", "wedding-planner")       # the recorded find's owner and name


def mit(line: str) -> str:
    return "MIT License\n\n" + line + "\n\n" + MIT_BODY


# ---------------------------------------------------------------------------
# the copyright line
# ---------------------------------------------------------------------------

ATTACKS = [
    "Copyright (c) 2024 Jane Doe, Corporations Barred",
    "Copyright (c) 2024 Jane Doe, Governments Excepted",
    "Copyright (c) 2024 Jane Doe, Big Tech Barred",
    "Copyright (c) 2024 Jane Doe, Firms Remunerate Author",
    "Copyright (c) 2024 Jane Doe, Hobby Projects Solely",
    "Copyright (c) 2024 Jane Doe, NOT FOR PROFIT",
    "Copyright (c) 2024 Jane Doe & Corporations Barred",
    "Copyright (c) 2024 Jane Doe, Nonprofits Gratis, Corporations Invoiced",
    "Copyright (c) 2024 Jane Doe, Gewinnorientierte Firmen Ausgeschlossen",
    "Copyright (c) 2024 Jane Doe, Firmen ausgeschlossen",
    "Copyright (c) 2024 Jane Doe, Imprese Escluse",
    "Copyright (c) 2024 Jane Doe, Empresas Excluidas",
    "Copyright (c) 2024 Jane Doe, Ingen Firmor",
    "Copyright (c) 2024 Jane Doe, ICE Barred",
    "Copyright (c) 2024 Jane Doe, Zakazane Firmy",
    "Copyright (c) 2024 Jane Doe (Corporations Barred)",
    "Copyright (c) 2024 Jane Doe [Corporations Barred]",
    "Copyright (c) 2024 Jane Doe <jane@corporations-barred.example>",
    "Copyright (c) 2024 Jane Doe, NoCorps",
    "Copyright (c) 2024 Jane Doe, Individuals",
    "Copyright (c) 2024 Jane Doe, www.pay-per-seat.example",
    "Copyright (c) 2024 Jane Doe, www.corporations-barred.example",
    "Copyright (c) 2024 Jane Doe and Corporations Barred",
    "Copyright (c) 2024 Jane Doe + Big Tech Barred",
]


@pytest.mark.parametrize("line", ATTACKS)
@pytest.mark.parametrize("identity", [(), PROJECT, ("janedoe", "planner")],
                         ids=["no_project", "other_project", "jane_is_the_project"])
def test_a_name_then_more_words_reads_as_custom(line, identity):
    """Whatever the extra words are, and whoever the project is (even when
    "Jane Doe" is the project, the words after her name are not)."""
    for text in (mit(line), "MIT License\n\nCopyright (c) 2024 Jane Doe\n\n" + MIT_BODY
                 + "\n" + line + "\n"):
        assert w.license_from_text(text, identity=identity) is None, line


def test_a_second_holder_on_its_own_line_is_read_with_the_first():
    text = "Copyright (c) 2024 Jane Doe\nCopyright (c) 2024 Corporations Barred\n\n" + MIT_BODY
    assert w.license_from_text(text) is None
    assert w.license_from_text(text, identity=("janedoe", "planner")) is None


def test_the_holder_spot_takes_only_the_files_own_holders():
    """validator: the holder gap "AUTHORS OR COPYRIGHT HOLDERS OR CORPORATIONS BARRED"."""
    text = mit("Copyright (c) 2024 Jane Doe, Corporations Barred").replace(
        "AUTHORS OR COPYRIGHT HOLDERS", "AUTHORS OR COPYRIGHT HOLDERS OR CORPORATIONS BARRED")
    assert w.license_from_text(text) is None
    plain = mit("Copyright (c) 2024 Jane Doe").replace(
        "AUTHORS OR COPYRIGHT HOLDERS", "AUTHORS OR COPYRIGHT HOLDERS OR CORPORATIONS BARRED")
    assert w.license_from_text(plain) is None


def test_holders_that_are_all_the_project_still_read():
    """Several holders read when every one is the project's own name."""
    assert w.license_from_text(mit("Copyright (c) 2024 Jane Doe, JaneDoe Labs"),
                               identity=("janedoe", "labs")) == "MIT"
    assert w.license_from_text(mit("Copyright (c) 2024 Evil Martians"),
                               identity=("evilmartians", "lefthook")) == "MIT"
    assert w.license_from_text(mit("Copyright (c) 2023 lichess.org"),
                               identity=("lichess-org", "lila")) == "MIT"
    # the same holder named twice is one holder
    assert w.license_from_text("Copyright 2023 Jane Doe\nCopyright 2024 Jane Doe\n\n" + MIT_BODY) \
        == "MIT"


def test_a_project_named_after_a_condition_does_not_make_it_a_name():
    ident = ("janedoe", "no-commercial-use")
    assert w.license_from_text(mit("Copyright (c) 2024 Jane Doe, No Commercial Use"),
                               identity=ident) is None
    assert w.license_from_text(mit("Copyright (c) 2024 Non-Commercial"),
                               identity=("noncommercial", "x")) is None


def test_one_holder_alone_must_be_a_plain_name():
    """One holder alone is no longer "any name". A phrase
    in the holder's place ("Copyright 2024 Corporations Barred") reads as
    custom; a person's or a studio's name still reads."""
    assert w.license_from_text(mit("Copyright (c) 2024 Corporations Barred")) is None
    assert w.license_from_text(mit("Copyright (c) 2026 Sahib Singh"), identity=PROJECT) == "MIT"


@pytest.mark.parametrize("line, identity", [
    ("Copyright (c) 2010-2024 Google LLC. https://angular.dev/license", ("angular", "angular")),
    ("Copyright (c) 2015 Evil Martians", ("evilmartians", "lefthook")),
    ("Copyright (c) 2019 Contact Software GmbH", ("contact-software", "elements")),
    ("Copyright (c) 2020 Personal Capital", ("personalcapital", "tools")),
    ("Copyright (c) 2020 Client IO Ltd", ("clientio", "sdk")),
    ("Copyright (c) 2019 Only Tech", ("onlytech", "tool")),
    ("Copyright (c) 2018 Free Software Foundation", ("fsf", "tool")),
    ("Copyright (c) 2016 Sindre Sorhus <sindresorhus@" + "gmail.com> (sindresorhus.com)",
     ("sindresorhus", "got")),
    ("Copyright (c) 2026 JollyJOJO (idonafraid-create)", ("idonafraid-create", "oh-my-petcare")),
    ("Copyright (c) 2020, Comma.ai, Inc.", ("commaai", "opendbc")),
])
def test_real_holder_lines_read_with_the_project_they_come_from(line, identity):
    """The validator's false refusals (genuine2.py): read with who the project is."""
    assert w.license_from_text(mit(line), identity=identity) == "MIT"


@pytest.mark.parametrize("line", [
    "Copyright (c) 2020 Lea Verou, Chris Lilley",
    "Copyright (c) 2014 Nicolas Gallagher and Jonathan Neal",
])
def test_two_real_people_that_are_not_the_project_fail_closed(line):
    """The price of the rule: two named people read as custom unless both are
    the project (a note on the plan, never an added part). ("Jane Doe, Bez Firm"
    is the same shape.)"""
    assert w.license_from_text(mit(line), identity=("someone", "tool")) is None


@pytest.mark.parametrize("address, ok", [
    ("jane.doe@" + "gmail.com", True),              # split: no address in the tree
    ("jane@" + "janedoe.dev", True),
    ("https://janedoe.github.io/planner/", True),
    ("jane@acme.example", False),                                 # a word that is not known
    ("https://www.corporations-barred.example", False),
    ("jane@no-companies.example", False),
])
def test_an_address_beside_a_holder_is_made_of_known_words(address, ok):
    text = mit(f"Copyright (c) 2024 Jane Doe <{address}>")
    assert (w.license_from_text(text, identity=("janedoe", "planner")) == "MIT") is ok


# -- from start to finish -------------------------------------------------------

def _details_with(monkeypatch, extra: str, **item_over) -> dict:
    lines = MIT_LICENSE.splitlines()
    at = next(i for i, x in enumerate(lines) if x.lower().startswith("copyright"))
    lines[at] += extra
    files = dict(D.SUBCHI_FILES, LICENSE=("\n".join(lines) + "\n").encode())
    monkeypatch.setattr(source_fetch, "fetch_small",
                        D.FakeNet(files, refs=D.read("info_refs_subchibeats.pktline")))
    monkeypatch.setattr(trust_floor, "_today", lambda: datetime.date(2026, 9, 27))
    it = copy.deepcopy(D.item())
    it.update(item_over)
    return w.details(D.found_for(it), budget=D.budget())


@pytest.mark.parametrize("extra", ["", ", Firmen ausgeschlossen", ", Corporations Barred",
                                   ", Businesses Buy Seats"])
def test_the_recorded_find_with_a_condition_in_its_copyright_line(monkeypatch, tmp_path, extra):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    rec = _details_with(monkeypatch, extra)
    if not extra:
        assert rec["repo"]["license_read"] == "MIT"
        assert rec["trust"]["floor_ok"] is True and rec["trust"]["path"] == "static_small"
        return
    assert rec["repo"]["license_read"] == w.CUSTOM_LICENSE
    assert rec["trust"]["floor_ok"] is False and rec["trust"]["pretick_ok"] is False
    assert "license" in rec["trust"]["reasons"]


def test_the_licence_the_host_reports_never_stands_in_for_a_custom_file(monkeypatch, tmp_path):
    """50 stars and GitHub's own "MIT": still custom, so the stars path does not pass."""
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    rec = _details_with(monkeypatch, ", Corporations Barred", stargazers_count=50,
                        license={"key": "mit", "spdx_id": "MIT", "name": "MIT License"})
    assert rec["repo"]["license_reported"] == "MIT" and rec["repo"]["stars"] == 50
    assert rec["repo"]["license_read"] == w.CUSTOM_LICENSE
    assert rec["trust"]["floor_ok"] is False and rec["trust"]["path"] == ""
    # the same find, its licence file unread: the report still counts (nothing was read)
    unread = copy.deepcopy(rec)
    unread["repo"]["license_read"] = None
    assert trust_floor.check(unread)[1] == "stars"


def test_custom_is_never_a_usable_licence():
    assert w.license_words(w.CUSTOM_LICENSE) is None
    assert trust_floor._open(w.CUSTOM_LICENSE) is False


# ---------------------------------------------------------------------------
# descriptions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, want", [
    ("«CAFÉ» and CafÃ©", "«CAFÉ» and Café."),
    ("CAFÉ—BAR and CafÃ© app", "CAFÉ, BAR and Café app."),
    ("ÄÖÜ» test with Ã©", "ÄÖÜ» test with é."),
    ("«MÄ» and CafÃ©", "«MÄ» and Café."),
    ("CafÃ© été", "Café été."),
    ("Big day â€” CafÃ© edition", "Big day, Café edition."),
    ("MichaÅ‚ Ã³ plan", "Michał ó plan."),
])
def test_a_real_capital_beside_a_mark_is_kept(raw, want):
    assert S.plain_text(raw) == want


# ---------------------------------------------------------------------------
# query shapes for data jobs
# ---------------------------------------------------------------------------

def test_data_jobs_get_a_topic_and_a_readme_shape(monkeypatch):
    oq = fake_outbound(monkeypatch)
    for phrase in ("plant varieties", "recipes csv"):
        urls = [u for u, _p in github.queries({"id": "j4"}, [oq.Sendable(phrase, "j4")],
                                               kind="data-template", row=gh_row())]
        assert any("topic:" in u for u in urls), phrase
        assert any("+in:name,description,readme" in u for u in urls), phrase
        assert all("per_page=100" in u for u in urls), phrase


# ---------------------------------------------------------------------------
# time: places asked at once, and the deadline kept
# ---------------------------------------------------------------------------

def test_one_jobs_places_are_asked_at_once_and_answer_in_order(monkeypatch):
    oq = fake_outbound(monkeypatch)
    rows = [{"id": f"r{i}", "role": "live", "label": f"Place {i}", "enabled": True}
            for i in range(3)]

    def searcher(i):
        def fn(job, phrases, *, kind, budget, row, progress=None):
            time.sleep(0.4)
            if progress:
                progress(f"asked {row['id']}")
            return [{"id": f"f{i}-{k}"} for k in range(2)] + [{"id": "shared"}]
        return fn

    monkeypatch.setattr(w, "_route_all", lambda kind: rows)
    monkeypatch.setattr(w, "adapter_fn", lambda row: searcher(int(row["id"][1:])))
    lines: list[str] = []
    t = time.monotonic()
    got = w.find_online({"id": "j1"}, [oq.Sendable("dice roller", "j1")], kind="code-app",
                        budget=budget(), progress=lines.append)
    assert time.monotonic() - t < 1.0                          # not 3 x 0.4 s one after another
    assert [f["id"] for f in got] == ["f0-0", "f0-1", "shared", "f1-0", "f1-1", "f2-0", "f2-1"]
    assert sorted(lines) == ["asked r0", "asked r1", "asked r2"]


def test_a_failing_place_still_fails_the_search(monkeypatch):
    oq = fake_outbound(monkeypatch)
    rows = [{"id": "r0", "role": "live", "label": "A"}, {"id": "r1", "role": "live", "label": "B"}]

    def ok(job, phrases, **kw):
        return [{"id": "a"}]

    def bad(job, phrases, **kw):
        raise RuntimeError("broken searcher")

    monkeypatch.setattr(w, "_route_all", lambda kind: rows)
    monkeypatch.setattr(w, "adapter_fn", lambda row: ok if row["id"] == "r0" else bad)
    with pytest.raises(RuntimeError):
        w.find_online({"id": "j1"}, [oq.Sendable("dice roller", "j1")], kind="code-app",
                      budget=budget())


def test_the_callers_deadline_holds_in_every_place(monkeypatch):
    oq = fake_outbound(monkeypatch)
    rows = [{"id": "r0", "role": "live", "label": "A"}, {"id": "r1", "role": "live", "label": "B"}]
    seen = []

    def fn(job, phrases, **kw):
        seen.append(getattr(S._DEADLINE, "at", None))
        return []

    monkeypatch.setattr(w, "_route_all", lambda kind: rows)
    monkeypatch.setattr(w, "adapter_fn", lambda row: fn)
    at = time.monotonic() + 30
    with S.deadline(at):
        w.find_online({"id": "j1"}, [oq.Sendable("dice roller", "j1")], kind="code-app",
                      budget=budget())
    assert seen == [at, at]


def test_details_many_returns_by_its_deadline(monkeypatch):
    def slow(found, *, budget):
        time.sleep(2.5)
        return found

    monkeypatch.setattr(w, "details", slow)
    finds = [{"id": f"x{i}"} for i in range(3)]
    t = time.monotonic()
    got = w.details_many(finds, budget=budget(), deadline=time.monotonic() + 1.5)
    assert time.monotonic() - t < 1.5 + 0.3                     # the deadline, not after it
    assert got == finds                                         # untouched, never half-trusted
