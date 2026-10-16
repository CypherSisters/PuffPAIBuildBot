"""Reader level and old books in discovery.

* Learn items go through meaning relevance and the reader's level: a single
  old book is offered only when it clearly fits the job, and a builder under
  13 is not offered old books or textbooks written for grown-ups ("Space-Time-
  Matter" for a 10-year-old's space bot).
* With no jobs, "Checking what fits your jobs ... 8 kept" is never said: the
  fallback finds are not job fits.
* The top unticked find of a job with nothing ticked says "Best match for
  this job." and stays unticked.
* "if you have Home Assistant" lines only where Home Assistant is in use.

Nothing here reaches the network.
"""
from __future__ import annotations

import copy
import types

import pytest

from puffpai.catalogs import meaning
from puffpai.wizard import factory, find_parts, proposal
from tests.test_find_parts_plan_items import (BUDGET_JOB, GOAL, FakeWhere, _job, _needs,  # noqa: F401
                                      lanes)

HELPER = pytest.mark.skipif(meaning.helper_state() != "ready",
                            reason="BuildBot's reading helper is not on this computer")

TRUSTED = {"floor_ok": True, "path": "", "pretick_ok": True, "reasons": []}


def _book(fid: str, *, audience: str = "general", tier: str = "") -> dict:
    row = {"schema": "found/v1", "id": fid, "part": "knowledge", "name": "A book",
           "what_it_is": "A book.", "integrable": False, "audience": audience,
           "trust": dict(TRUSTED)}
    if tier:
        row["tier"] = tier
    return row


# -- the reader's level ---------------------------------------------------------------
@pytest.mark.parametrize("fid,audience,young_ok", [
    ("gutenberg:43006", "general", False),       # an old book for grown-ups
    ("gutenberg:1", "kids", True),               # an old book marked for children
    ("openstax:physics", "general", False),      # a college textbook
    ("kiwix_space_stackexchange", "general", True),   # a library pack: order only
    ("kiwix:ted_mul_space", "technical", True),
])
def test_a_young_reader_is_not_offered_books_written_for_grown_ups(fid, audience, young_ok):
    row = _book(fid, audience=audience)
    assert meaning.reader_fits(row, "under_13") is young_ok
    for reader in (None, "", "13_17", "adult"):
        assert meaning.reader_fits(row, reader) is True


def test_the_level_rule_is_only_about_books():
    app = {"id": "github:a/b", "part": "code-app", "audience": "general"}
    assert meaning.reader_fits(app, "under_13") is True


def test_a_find_from_this_computer_gets_its_audience_from_the_library_list():
    # A snapshot find carries only its id: the saved list says who it is for.
    assert find_parts._at_reader_level({"id": "gutenberg:43006", "part": "knowledge"},
                                       "under_13") is False
    assert find_parts._at_reader_level({"id": "gutenberg:43006", "part": "knowledge"},
                                       None) is True


# -- a single old book needs a clear fit ------------------------------------------------
@pytest.mark.parametrize("fid,fit,state", [
    ("gutenberg:43006", 0.445, "dropped"),     # Space-Time-Matter for a space bot
    ("gutenberg:5614", 0.586, "ticked"),       # Chess Strategy for a chess tutor
    ("openstax:x", 0.47, "dropped"),
    ("kiwix:chess.stackexchange.com_en_all", 0.447, "might_help"),   # a pack: usual band
    ("kiwix_space_stackexchange", 0.39, "dropped"),
])
def test_an_old_book_is_offered_only_when_it_clearly_fits(fid, fit, state):
    row = _book(fid)
    assert meaning._classify_why(row, fit)[0] == state


@HELPER
def test_the_space_learn_job_gets_no_off_purpose_old_books(lanes):
    """The integrator's finding: "Knows everything about space" was filled
    with Space-Time-Matter, a Whitehead book and "The new science of space
    speech"."""
    job = _job("j1", "Knows everything about space", "", "asked", ["space"],
               ("code-app", "knowledge"))
    job["learn"] = True
    lanes(FakeWhere())
    for reader in (None, "under_13"):
        doc = _needs([copy.deepcopy(job)])
        doc["kind"] = "space"
        if reader:
            doc["reader"] = reader
        out = find_parts.plan_items(doc, goal=GOAL, online=True, allow_send=False, cfg={})
        books = [i["id"] for i in out["items"] if i["section"] == "knowledge"]
        assert not [b for b in books if b.startswith("knowledge:gutenberg:")], (reader, books)


@HELPER
def test_a_young_reader_gets_no_grown_up_old_book_even_when_it_fits(lanes):
    job = _job("j1", "Learn chess strategy", "Teaches chess strategy and openings.", "asked",
               ["chess strategy"], ("code-app", "knowledge"))
    job["learn"] = True
    lanes(FakeWhere())

    def old_books(reader):
        doc = _needs([copy.deepcopy(job)])
        doc["kind"] = "chess"
        if reader:
            doc["reader"] = reader
        out = find_parts.plan_items(doc, goal=GOAL, online=True, allow_send=False, cfg={})
        ev = {f["id"]: f for f in find_parts.last_evidence()["finds"]}
        return ([i for i in out["items"] if i["id"].startswith("knowledge:gutenberg:")], ev)
    grown, _ev = old_books(None)
    assert grown, "a chess book that fits was expected for a grown-up"
    young, ev = old_books("under_13")
    from puffpai.catalogs import knowledge_resolver as kr
    assert all((kr.candidate_by_id(i["apply"]["ref"]) or {}).get("audience") == "kids"
               for i in young), young
    held = [f for f in ev.values() if (f.get("meaning") or {}).get("why_dropped")
            == meaning.NOT_AT_LEVEL]
    assert held, "the evidence says why the grown-up books were left out"


# -- no jobs ----------------------------------------------------------------------------
def test_with_no_jobs_nothing_is_called_a_fit_for_your_jobs(lanes, monkeypatch):
    lanes(FakeWhere())
    monkeypatch.setattr(factory, "knowledge_items", lambda ctx: [
        {"id": "knowledge:kiwix:x", "section": "knowledge", "label": "A book"}])
    lines: list[str] = []
    find_parts.plan_items(_needs([]), goal=GOAL, online=True, allow_send=False, cfg={},
                          on_line=lines.append)
    assert lines == [find_parts.LINE_NO_JOBS.format(n=1)]
    assert not [line for line in lines if line.startswith("Checking what fits")]


def test_the_closing_lines_never_count_fallback_finds_as_job_fits():
    from puffpai.wizard import service
    record = proposal.clean_parts({"items": [
        {"id": f"live_info:x{n}", "section": "live_info", "label": "x",
         "apply": {"kind": "live_feed", "ref": "x"}} for n in range(8)]})
    assert service._parts_tail(record, "standard", ticked=set(), said=[]) == []
    # With a job kept, the line is still added when the search did not say it.
    tail = service._parts_tail(record, "standard", ticked={"j1"}, said=[])
    assert tail == ["Checking what fits your jobs ... 8 kept, 0 left out"]


# -- the best match marker ----------------------------------------------------------------
def _line(iid, need, on=False, section="apps_code"):
    return {"id": iid, "section": section, "need": need, "default_on": on,
            "label": iid, "detail": "Does the thing."}


def _fit(fit):
    return {"meaning": {"state": "might_help", "fit": fit, "judge": "yes", "purpose": "yes"}}


def test_the_top_unticked_find_of_a_job_with_nothing_ticked_is_marked():
    items = [_line("a", "j1"), _line("b", "j1"), _line("c", "j2", on=True), _line("d", "j2"),
             _line("e", "j3", section="knowledge"), _line("f", "j3", section="knowledge")]
    rows = {k: _fit(v) for k, v in {"a": 0.54, "b": 0.62, "c": 0.6, "d": 0.7, "e": 0.41,
                                     "f": 0.51}.items()}
    jobs = [{"id": j, "name": "A job", "source": "asked"} for j in ("j1", "j2", "j3")]
    find_parts._mark_best(items, rows, jobs)
    marked = [i["id"] for i in items if i.get("best_match")]
    # Final directive: a weak fit (0.41) is never the best match.
    assert marked == ["b", "f"]
    by_id = {i["id"]: i for i in items}
    assert by_id["b"]["detail"] == f"{find_parts.BEST_MATCH} Does the thing."
    assert by_id["a"]["detail"] == "Does the thing."
    # The marker never ticks anything.
    assert [i["id"] for i in items if i["default_on"]] == ["c"]


def test_the_marker_is_plain_words():
    assert find_parts.BEST_MATCH == "Best match for this job."
    for text in (find_parts.BEST_MATCH, find_parts.LINE_NO_JOBS):
        assert "—" not in text and "door" not in text.lower()


# -- Home Assistant lines only where it is in use ------------------------------------------
def _device_ctx(**args):
    return types.SimpleNamespace(goal="A chess coach for practice games",
                                 args=types.SimpleNamespace(**args))


@pytest.fixture
def device_lines(monkeypatch):
    lines = [{"id": "devices:epic", "section": "devices",
              "label": "Your practice games against it, if you have Home Assistant "
                       "(Epic Games Store)"}]
    fake = types.SimpleNamespace(proposal_items=lambda goal: list(lines))
    monkeypatch.setattr(factory, "_live_catalog", lambda: fake)
    return lines


def test_no_home_assistant_lines_when_it_is_not_in_use(device_lines, monkeypatch):
    from puffpai import setup_ops
    monkeypatch.setattr(setup_ops, "ha_configured", lambda: False)
    assert factory.device_items(_device_ctx(skip_ha=False, ha_url=None, ha_token=None)) == []


def test_home_assistant_lines_when_it_is_set_up_or_given_now(device_lines, monkeypatch):
    from puffpai import setup_ops
    monkeypatch.setattr(setup_ops, "ha_configured", lambda: True)
    assert factory.device_items(_device_ctx(skip_ha=False)) == device_lines
    monkeypatch.setattr(setup_ops, "ha_configured", lambda: False)
    assert factory.device_items(_device_ctx(skip_ha=False, ha_url="http://hub.local:8123",
                                            ha_token="t")) == device_lines
    assert factory.device_items(_device_ctx(skip_ha=True, ha_url="http://hub.local:8123",
                                            ha_token="t")) == []


def test_unreadable_home_assistant_settings_mean_not_in_use(device_lines, monkeypatch):
    from puffpai import setup_ops

    def broken():
        raise OSError("unreadable")
    monkeypatch.setattr(setup_ops, "ha_configured", broken)
    assert factory.device_items(_device_ctx(skip_ha=False)) == []


def test_with_jobs_the_word_matched_gadget_lines_step_aside(monkeypatch):
    needs = _needs([copy.deepcopy(BUDGET_JOB)])
    monkeypatch.setattr(proposal, "needs_active", lambda shared: True)
    monkeypatch.setattr(proposal, "parts_for", lambda shared, **kw: proposal.clean_parts({}))
    sctx = proposal.SummaryContext(goal="g", terms=[], name="Bot", mode="standard",
                                   shared={"needs": needs}, previews={}, args=None,
                                   cfg=None, internet=True)
    assert "factory:devices" in proposal.skipped_with_needs(sctx)
