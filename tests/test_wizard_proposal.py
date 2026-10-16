"""The one summary: its item shape, its providers, and what "Looks good" means.

ONE summary lists every download size and
every outside service; ONE "Looks good" approves it; "Change something" opens
any item. These tests hold the model underneath both faces:

* the shared ProposalItem shape is validated strictly (a host is a host name,
  a size is bytes or unknown, never "0 for unknown");
* providers are pluggable and a broken one never sinks the summary;
* a decision is checked against what was shown, and a ``--yes`` run says yes
  to nothing that downloads or reaches out unless a flag named it;
* the cards the owner no longer sees answer exactly what was approved.

Run:
    pytest tests/test_wizard_proposal.py
"""
from __future__ import annotations

import sys
import types

import pytest

from puffpai.wizard import cards, proposal, wording

GB = 1024 ** 3


@pytest.fixture(autouse=True)
def _no_catalog_lines(monkeypatch):
    """These tests hold the summary's own rules, so the real catalogs' lines
    (wired in by :mod:`puffpai.wizard.factory`) are left out of them. The
    wired lines have their own tests (``test_factory_wiring.py``)."""
    from puffpai.wizard import factory
    monkeypatch.setattr(factory, "PROVIDERS", tuple(
        (name, lambda sctx: []) for name, _fn in factory.PROVIDERS))


def _item(item_id, section, kind, **extra):
    base = {"id": item_id, "section": section, "label": item_id.split(":")[-1],
            "detail": "", "size_bytes": None, "outside_host": None,
            "default_on": True, "needs_yes": kind not in ("none", "model"),
            "apply": {"kind": kind, "ref": item_id.split(":")[-1]}}
    base.update(extra)
    return base


# -- the shape ----------------------------------------------------------------

def test_the_shared_shape_validates_and_unknown_keys_are_dropped():
    item = proposal.validate_item(_item(
        "knowledge:kiwix:vikidia_en_all", "knowledge", "source_fetch",
        size_bytes=3 * GB, outside_host="Download.Kiwix.org", extra="kept?"))
    assert set(item) == {"id", "section", "label", "detail", "size_bytes",
                         "outside_host", "default_on", "needs_yes", "apply"}
    assert item["outside_host"] == "download.kiwix.org"


@pytest.mark.parametrize("change", [
    {"id": "no-section-here"},
    {"id": "Knowledge:x"},
    {"section": "somewhere"},
    {"label": ""},
    {"size_bytes": -1},
    {"size_bytes": True},
    {"size_bytes": 1.5},
    {"outside_host": "https://download.kiwix.org/zim"},
    {"outside_host": "two words"},
    {"outside_host": "localhost"},
    {"apply": "source_fetch"},
    {"apply": {"kind": "Fetch It", "ref": "x"}},
    {"apply": {"kind": "source_fetch", "ref": 7}},
])
def test_a_bad_item_is_refused_with_a_reason(change):
    raw = _item("knowledge:kiwix:x", "knowledge", "source_fetch")
    raw.update(change)
    with pytest.raises(proposal.ProposalError):
        proposal.validate_item(raw)


def test_a_label_is_plain_and_capped_and_carries_no_control_characters():
    item = proposal.validate_item(_item(
        "knowledge:kiwix:x", "knowledge", "source_fetch",
        label="\x1b[31mred\x1b[0m " + "long " * 40))
    assert "\x1b" not in item["label"] and len(item["label"]) <= 80


# -- providers ----------------------------------------------------------------

def _sctx(**kw):
    base = dict(goal="a space bot", terms=["space"], name="StargazerBot",
                mode=wording.STANDARD, shared={}, previews={},
                args=types.SimpleNamespace(), cfg=None, internet=True)
    base.update(kw)
    return proposal.SummaryContext(**base)


def test_a_registered_provider_lands_in_its_section_and_a_broken_one_is_a_note():
    proposal.register_provider("test:good", lambda sctx: [_item(
        "live_info:test:sky", "live_info", "live_feed",
        outside_host="api.example.org", default_on=True, needs_yes=True)])
    proposal.register_provider("test:broken", lambda sctx: 1 / 0)
    proposal.register_provider("test:junk", lambda sctx: [{"id": "nope"}])
    try:
        items, notes = proposal.collect_items(_sctx())
    finally:
        for name in ("test:good", "test:broken", "test:junk"):
            proposal.unregister_provider(name)
    assert [i["id"] for i in items if i["section"] == "live_info"] == [
        "live_info:test:sky"]
    assert any("test:broken" in note for note in notes)
    assert any("nope" in note for note in notes)


def test_another_lanes_module_is_picked_up_by_import(monkeypatch):
    module = types.ModuleType("puffpai.routine_catalog")
    module.proposal_items = lambda goal, terms: [_item(
        "updates:routines:daily_fact", "updates", "routine",
        label=f"A daily {terms[0]} fact", needs_yes=False)]
    monkeypatch.setitem(sys.modules, "puffpai.routine_catalog", module)
    # Providers are wired by name; the import hook stays for later
    # packages, so it is switched on here the way such a package would be.
    monkeypatch.setattr(proposal, "EXTERNAL_PROVIDER_MODULES",
                        ("puffpai.routine_catalog",))
    items, _notes = proposal.collect_items(_sctx())
    assert "updates:routines:daily_fact" in {i["id"] for i in items}
    assert next(i for i in items if i["section"] == "updates")["label"] == \
        "A daily space fact"


def _brain_preview(installed=False, fit="comfortable", may_download=True):
    return {"model": "qwen2.5:7b-instruct",
            "gates": {"may_download_models": may_download},
            "menu": {"installed_models": ["qwen2.5:7b-instruct"] if installed
                     else [],
                     "options": [
                         {"id": "qwen2.5:7b-instruct", "label": "Qwen2.5 7B",
                          "disk_gb": 5, "fit": fit, "installed": installed},
                         {"id": "llama3.2:1b", "label": "Llama 3.2 1B",
                          "disk_gb": 1, "fit": "comfortable",
                          "installed": False}]}}


@pytest.mark.parametrize("internet,fit,on", [
    (True, "comfortable", True),    # online and it fits: on by default
    (None, "comfortable", False),   # not checked: nothing downloads by default
    (False, "comfortable", False),  # no internet
    (True, "tight", False),         # a tight fit is offered, not assumed
])
def test_the_brain_download_is_on_by_default_only_when_online_and_it_fits(
        internet, fit, on):
    items = proposal._thinking(_sctx(internet=internet,
                                     previews={"brain": _brain_preview(fit=fit)}))
    brain = next(i for i in items if i["id"] == proposal.BRAIN_ITEM_ID)
    assert brain["default_on"] is on
    assert brain["apply"]["kind"] == "model_pull"
    assert brain["outside_host"] == proposal.MODEL_REGISTRY_HOST
    assert brain["size_bytes"] == 5 * GB


def test_an_installed_brain_downloads_nothing_and_says_so():
    items = proposal._thinking(_sctx(previews={"brain": _brain_preview(
        installed=True)}))
    brain = next(i for i in items if i["id"] == proposal.BRAIN_ITEM_ID)
    assert brain["apply"]["kind"] == "model" and brain["size_bytes"] is None
    assert brain["outside_host"] is None and "Already on this computer" in \
        brain["detail"]


def test_a_brain_on_another_known_address_is_not_a_download(monkeypatch):
    """The first address found holds other brains, the chosen one is on the
    second: the plan says it is already here, never "Download ... from
    registry.ollama.ai", and nothing is pulled."""
    from puffpai import setup_ops
    from puffpai.wizard.packages.brain import skill as brain

    model = "qwen3.5:9b"
    monkeypatch.setattr(brain, "_menu", lambda ctx, machine, gates: {
        "options": [{"id": model, "label": "Qwen 3.5 9B", "installed": False,
                     "disk_gb": 6.6, "fit": "comfortable"}],
        "installed_models": ["llama3.2:1b"], "recommended": {"id": model}})
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda start=None: [
        {"url": "http://127.0.0.1:11434", "models": ["llama3.2:1b"]},
        {"url": "http://127.0.0.1:11435", "models": [model, "gemma3:4b"]}])
    ctx = types.SimpleNamespace(
        shared={"census": {}, "ollama_url": "http://127.0.0.1:11434",
                "installed_models": ["llama3.2:1b"], "gates": {}},
        args=types.SimpleNamespace(model=model, ollama_url=None),
        applied=lambda *a, **k: {}, non_interactive=True, cfg=None,
        read=lambda section: {})
    preview = brain.propose(ctx)
    assert not any(f["id"] == "download_now" for f in preview["card"]["fields"])
    items = proposal._thinking(_sctx(previews={"brain": preview}))
    item = next(i for i in items if i["id"] == proposal.BRAIN_ITEM_ID)
    assert "Already on this computer" in item["detail"]
    assert item["outside_host"] is None and item["apply"]["kind"] == "model"


def test_only_real_runtime_switches_are_offered_as_outside_services():
    items = proposal._outside(_sctx(args=types.SimpleNamespace(
        egress_door=["weather"])))
    refs = {i["apply"]["ref"] for i in items}
    # web search is now a real runtime switch (keyless Wikipedia).
    assert refs == {"weather", "web_search"}, \
        "a switch the running bot does not have was offered"
    by_ref = {i["apply"]["ref"]: i for i in items}
    assert by_ref["weather"]["default_on"] is True
    assert by_ref["weather"]["outside_host"]
    assert by_ref["web_search"]["default_on"] is False  # not named: stays off
    assert by_ref["web_search"]["outside_host"] == "en.wikipedia.org"


# -- the summary --------------------------------------------------------------

def _summary(items, **kw):
    state = kw.pop("state", None)
    base = dict(previews={"brain": _brain_preview()})
    base.update(kw)
    sctx = _sctx(**base)
    for number, raw in enumerate(items):
        proposal.register_provider(f"test:item{number}",
                                   lambda sctx, raw=raw: [raw])
    try:
        return proposal.build(sctx, state=state)
    finally:
        for number in range(len(items)):
            proposal.unregister_provider(f"test:item{number}")


def test_the_summary_has_its_sections_in_order_with_plain_headings():
    summary = _summary([])
    assert [s["id"] for s in summary["sections"]] == [
        s for s in proposal.SECTIONS if s not in proposal.OPTIONAL_SECTIONS]
    assert [s["heading"] for s in summary["sections"]] == [
        "How it thinks", "Updates it can send you", "What it will know",
        "Websites and services it may use",
        "Your private stuff", "Your gadgets"]
    assert [a["label"] for a in summary["actions"]] == ["Looks good",
                                                        "Change something"]


def test_every_size_and_every_outside_host_is_spelled_out():
    summary = _summary([_item("knowledge:kiwix:space", "knowledge",
                              "source_fetch", size_bytes=700 * 1024 ** 2,
                              outside_host="download.kiwix.org")])
    assert summary["total_bytes"] == 5 * GB + 700 * 1024 ** 2
    assert summary["hosts"] == ["download.kiwix.org", "registry.ollama.ai"]
    assert "Total to download: 5.7 GB." == summary["lines"]["total"]
    assert "download.kiwix.org; registry.ollama.ai" in summary["lines"]["leaves"]


def test_keeping_it_off_the_internet_switches_every_outside_service_off():
    feed = _item("live_info:apis:sky", "live_info", "live_feed",
                 outside_host="api.example.org")
    on = _summary([feed])
    assert next(i for i in proposal.items_of(on)
                if i["id"] == feed["id"])["on"] is True
    off = _summary([feed], state={"reach": proposal.REACH_OFFLINE, "on": {}})
    assert next(i for i in proposal.items_of(off)
                if i["id"] == feed["id"])["on"] is False
    assert "api.example.org" not in off["hosts"]


# -- deciding -----------------------------------------------------------------

def test_looks_good_takes_the_plan_as_shown():
    summary = _summary([])
    state = proposal.decide(summary)
    assert state["approved"] is True and state["reach"] == "only_listed"
    assert proposal.brain_on(state) is True


@pytest.mark.parametrize("changes,why", [
    ({"on": {"knowledge:kiwix:never_offered": True}}, "no item"),
    ({"on": {"thinking:team": False}}, "no item"),        # a statement
    ({"on": {"thinking:brain": "yes"}}, "true or false"),
    ({"model": "made-up:1b"}, "not one of the brains"),
    ({"reach": "anywhere"}, "not one of the internet"),
])
def test_a_change_the_plan_did_not_offer_is_refused(changes, why):
    with pytest.raises(proposal.ProposalError, match=why):
        proposal.decide(_summary([]), changes=changes)


def test_a_yes_run_says_yes_to_nothing_that_downloads_unless_a_flag_did():
    feed = _item("knowledge:registry:space", "knowledge", "source_fetch",
                 outside_host="download.kiwix.org", size_bytes=GB)
    summary = _summary([feed])
    nobody = proposal.decide(summary, non_interactive=True,
                             args=types.SimpleNamespace())
    assert proposal.approved_refs(nobody, "model_pull") == []
    assert proposal.approved_refs(nobody, "source_fetch") == []
    named = proposal.decide(summary, non_interactive=True,
                            args=types.SimpleNamespace(
                                download_model=True, fetch_source=["space"],
                                accept_source_size=True))
    assert proposal.approved_refs(named, "model_pull") == ["qwen2.5:7b-instruct"]
    assert proposal.approved_refs(named, "source_fetch") == ["space"]
    half = proposal.decide(summary, non_interactive=True,
                           args=types.SimpleNamespace(fetch_source=["space"]))
    assert proposal.approved_refs(half, "source_fetch") == [], (
        "naming a source without accepting its size is not a yes")


def test_picking_another_brain_moves_the_download_to_it():
    state = proposal.decide(_summary([]), changes={"model": "llama3.2:1b",
                                                   "on": {}})
    row = next(i for i in state["items"] if i["section"] == "thinking"
               and i["apply"]["kind"] in ("model", "model_pull"))
    assert row["apply"] == {"kind": "model_pull", "ref": "llama3.2:1b"}
    assert row["size_bytes"] == GB and row["on"] is True
    assert state["model"] == "llama3.2:1b"


def test_nothing_is_approved_until_it_is():
    assert proposal.approved_items(None) == []
    assert proposal.approved_items({"items": [{"on": True}]}) == []
    assert proposal.approved_refs({"approved": False}, "door") == []


# -- what the cards the owner no longer sees answer ---------------------------

def _card(skill, fields, part=""):
    return cards.validate_card({
        "skill": skill, "section": skill if skill != "secure" else "posture",
        "part": part, "title": skill, "status": "ready",
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": fields})


POSTURE = _card("secure", [
    {"id": "build_network", "kind": "choice", "label": "x",
     "options": ["online", "offline"], "current": "online"},
    {"id": "run_egress", "kind": "choice", "label": "y",
     "options": ["sealed", "island", "enumerated"], "current": "island"},
    {"id": "doors", "kind": "multi_choice", "label": "z",
     "options": ["weather", "web_search"], "current": []}], part="posture")
BRIDGE = _card("secure", [
    {"id": "bridge_posture", "kind": "choice", "label": "b",
     "options": ["open_yard", "fenced", "moat", "drawbridge_up"],
     "current": "moat"}], part="bridge")


def _state(**on):
    weather = _item("outside_services:door:weather", "outside_services", "door",
                    outside_host="api.open-meteo.com")
    summary = _summary([weather])
    return proposal.decide(summary, changes={"on": {
        "outside_services:door:weather": on.get("weather", True)},
        "reach": on.get("reach", "only_listed")})


def test_an_approved_service_opens_exactly_its_switch_and_nothing_else():
    state = _state(weather=True)
    answers = proposal.answers_for_card(POSTURE, state,
                                        types.SimpleNamespace(), internet=True)
    assert answers == {"build_network": "online", "run_egress": "enumerated",
                       "doors": ["weather"]}
    assert proposal.answers_for_card(BRIDGE, state,
                                     types.SimpleNamespace()) == {
        "bridge_posture": "fenced"}


def test_no_outside_service_means_home_network_only():
    state = _state(weather=False)
    answers = proposal.answers_for_card(POSTURE, state,
                                        types.SimpleNamespace(), internet=False)
    assert answers == {"build_network": "offline", "run_egress": "island"}


def test_ask_me_first_is_not_offered_because_nothing_asks_yet():
    """The "Ask me before each outside visit" choice promised a
    question the running bot never asks, so it is not a choice any more."""
    assert proposal.REACH_ASK not in proposal.REACH_CHOICES
    with pytest.raises(proposal.ProposalError):
        _state(weather=True, reach=proposal.REACH_ASK)


def test_keep_it_off_the_internet_wins_over_a_ticked_service():
    state = _state(weather=True, reach=proposal.REACH_OFFLINE)
    answers = proposal.answers_for_card(POSTURE, state, types.SimpleNamespace())
    assert answers["run_egress"] == "island" and "doors" not in answers


def test_a_flag_is_never_overridden_by_the_summary():
    state = _state(weather=True)
    args = types.SimpleNamespace(run_egress="sealed", build_network="offline",
                                 bridge_posture="drawbridge_up")
    assert proposal.answers_for_card(POSTURE, state, args, internet=True) == {}
    assert proposal.answers_for_card(BRIDGE, state, args) == {}


def test_answers_only_ever_name_fields_and_values_the_card_has():
    sources = _card("sources", [{"id": "selected", "kind": "multi_choice",
                                 "label": "s", "options": ["a", "b"],
                                 "current": []}])
    state = proposal.decide(_summary([
        _item("knowledge:registry:a", "knowledge", "source_fetch"),
        _item("knowledge:registry:zzz", "knowledge", "source_fetch")]))
    assert proposal.answers_for_card(sources, state, None) == {"selected": ["a"]}
    brain = _card("brain", [{"id": "model", "kind": "choice", "label": "m",
                             "options": ["qwen2.5:7b-instruct"],
                             "current": "qwen2.5:7b-instruct"}])
    assert proposal.answers_for_card(brain, state, None) == {}, (
        "an installed brain's card has no download switch to answer")


def test_the_hub_card_is_asked_only_when_the_hub_is_on_the_plan():
    tools = _card("tools", [
        {"id": "connect_hub", "kind": "toggle", "label": "c", "current": False},
        {"id": "hub_token", "kind": "secret_entry", "label": "k"}])
    hub = _item("devices:test:hub", "devices", "home_assistant")
    yes = proposal.decide(_summary([hub]))
    no = proposal.decide(_summary([hub]), changes={"on": {hub["id"]: False}})
    assert proposal.must_ask(tools, yes) and not proposal.must_ask(tools, no)
    assert proposal.answers_for_card(tools, yes, None) == {"connect_hub": True}


# -- the approved plan is the choice ------------------------------------------

def _fetch_state(on_refs, *, non_interactive=False, approved=True):
    return {"approved": approved, "non_interactive": non_interactive,
            "items": [{"id": f"knowledge:registry:{ref}", "on": on,
                       "apply": {"kind": "source_fetch", "ref": ref}}
                      for ref, on in on_refs]}


def test_settle_interview_drops_packs_the_owner_left_out():
    interview = {"goal": "space", "selected": ["pack:stackexchange_science",
                                              "pack:wikipedia", "tool:x"]}
    state = _fetch_state([("kiwix_astronomy_stackexchange", False),
                    ("kiwix_space_stackexchange", True),
                    ("kiwix_wikipedia_en_nopic", False)])
    out = proposal.settle_interview(interview, state)
    assert out["selected"] == ["pack:stackexchange_science", "tool:x"]
    assert out["matched"] == interview["selected"]
    assert interview["selected"][1] == "pack:wikipedia", "input not changed"


def test_settle_interview_leaves_a_yes_run_and_an_unapproved_plan_alone():
    interview = {"selected": ["pack:wikipedia"]}
    left_out = [("kiwix_wikipedia_en_nopic", False)]
    assert proposal.settle_interview(
        interview, _fetch_state(left_out, non_interactive=True)) == interview
    assert proposal.settle_interview(
        interview, _fetch_state(left_out, approved=False)) == interview
    assert proposal.settle_interview(interview, None) == interview


# -- the brain's own finds: look it up yourself -------------------------------

def test_the_brain_s_finds_show_as_look_it_up_yourself_and_never_switch_on():
    sources = {"items": [], "selected": [], "suggested": [
        {"label": "Ravelry pattern library", "host": "www.ravelry.com",
         "url": "https://www.ravelry.com/patterns",
         "why": "A big free library of knitting and crochet patterns."},
        {"label": "Same host again", "host": "www.ravelry.com",
         "url": "https://www.ravelry.com/x", "why": "dup"},
        {"label": "Another site", "host": "b.example.org",
         "url": "https://b.example.org", "why": "Opens every door."},
    ]}
    items = proposal._knowledge(_sctx(previews={"sources": sources}))
    assert [i["id"] for i in items] == [
        "knowledge:look_yourself:www.ravelry.com",
        "knowledge:look_yourself:b.example.org"]
    first = items[0]
    assert first["label"] == "Worth a look: Ravelry pattern library"
    assert "knitting and crochet patterns" in first["detail"]
    assert "Look it up yourself" in first["detail"]
    # Never the model's address, never a download, never a switch.
    assert "ravelry.com" not in first["label"] + first["detail"]
    assert first["outside_host"] is None and first["size_bytes"] is None
    assert not first["needs_yes"]
    assert first["apply"] == {"kind": proposal.INFO_KIND, "ref": ""}
    assert not proposal.toggleable(proposal.validate_item(first))
    # A reason in wizard lingo is left off; the item still says what it is.
    assert "door" not in items[1]["detail"].lower()
    for item in items:
        proposal.validate_item(item)


def test_look_it_up_yourself_items_open_nothing_when_approved():
    sources = {"items": [], "selected": [], "suggested": [
        {"label": "Some site", "host": "a.example.org",
         "url": "https://a.example.org", "why": "Good."}]}
    sctx = _sctx(previews={"sources": sources})
    summary = proposal.build(sctx)
    state = proposal.decide(summary)
    assert proposal.approved_refs(state, "source_fetch") == []
    assert "a.example.org" not in summary["hosts"]
    assert proposal.runtime_reach(state) is False
    # Shown in full (label and reason), never as "Left out".
    from puffpai.wizard import render_cli
    text = " ".join(" ".join(render_cli.summary_lines(summary)).split())
    assert "Left out: Worth a look" not in text
    assert "Worth a look: Some site" in text and "Look it up yourself" in text
