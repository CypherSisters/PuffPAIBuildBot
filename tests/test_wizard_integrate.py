"""Integrate, the bytes skill: the split has to write what today's step wrote.

The load-bearing test is
:func:`test_the_two_skills_write_the_block_the_old_step_wrote`: for the same
scripted choices, the ``knowledge`` block Sources+Integrate produce is the one
``cli._setup_knowledge_step`` produces. The design allows the wording to change in
Phase 2; it does not allow the record to.

Nothing here downloads. ``setup_ops.fetch_source`` is replaced by the same kind
of spy ``tests/test_wizard_sourcing_flow.py`` uses, and a test asserts the
fetch goes through that one existing call site rather than a new one.

Run:
    pytest tests/test_wizard_integrate.py
"""
from __future__ import annotations

import argparse
import copy
import json
import os

import pytest

from puffpai import cli, setup_ops
from puffpai.config import get_config
from puffpai.knowledge import auto_index, build_sources
from puffpai.wizard import cards, loader, runner as wizard_runner
from puffpai.wizard import sources_plan

ASTRONOMY = "kiwix_astronomy_stackexchange"
SPACE = "kiwix_space_stackexchange"
WIKIPEDIA = "kiwix_wikipedia_en_nopic"

SPACE_GOAL = ("knows everything about space, space facts, current space "
              "events, astronomy, Earth weather events")
SPACE_PACKS = ["pack:wikipedia", "pack:stackexchange_science"]

ONLINE = {"build_network": "online", "run_egress": "island"}


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    """One data dir, one state dir, no downloads and no real indexing."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    cfg = get_config(_force_reload=True)
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(auto_index, "start_background_index",
                        lambda *a, **k: (0, None))
    # Learning the owner's folders goes through the upload pipeline; here it
    # is a stub that says "learned" without embedding anything.
    from puffpai import ingestion
    monkeypatch.setattr(ingestion, "ingest_file", _fake_ingest)
    yield cfg
    get_config(_force_reload=True)


class _Ingested:
    def __init__(self, error=None, pii_detected=False, vaulted=False):
        self.error, self.pii_detected, self.vaulted = error, pii_detected, vaulted


def _fake_ingest(path, original_filename=None, user_hint=None, auto_apply=True,
                 *, vault=None):
    return _Ingested()


def _args(**kw):
    base = {"yes": True, "skip_knowledge": False, "skip_sourcing": False,
            "skip_own_data": False, "fetch_source": None,
            "accept_source_size": False, "own_data": None, "zim_dir": None,
            "no_auto_index": False}
    base.update(kw)
    return argparse.Namespace(**base)


def _fetch_spy(monkeypatch, ok=True):
    """The only download path there is, replaced. Records every call."""
    calls: list[dict] = []

    def _spy(source_id, posture, *, size_acknowledged=False, dest_dir=None):
        calls.append({"source_id": source_id,
                      "size_acknowledged": size_acknowledged})
        return {"schema": "source_fetch/v1", "ok": ok,
                "status": "fetched" if ok else "failed",
                "source_id": source_id, "reason": "ok", "path": "x.zim",
                "bytes": 123, "sha256": "abc", "resumed": False,
                "provenance": {"label": "Astronomy Stack Exchange",
                               "license": "CC-BY-SA-4.0"}}

    monkeypatch.setattr(setup_ops, "fetch_source", _spy)
    return calls


def _modules():
    packages, refusals = loader.load_builtin_packages()
    assert refusals == []
    return (loader.load_module(packages["sources"]), packages["sources"],
            loader.load_module(packages["integrate"]), packages["integrate"])


def _run_split(cfg, *, goal=SPACE_GOAL, packs=(), non_interactive=True,
               source_answers=None, integrate_answers=None, **args):
    """Sources then Integrate, exactly as the schedule would run them."""
    sources, sources_pkg, integrate, integrate_pkg = _modules()
    interview = {"goal": goal, "selected": list(packs)}
    shared = {"posture": ONLINE, "gates": setup_ops.posture_gates(ONLINE),
              "interview": interview}
    sections = {"persona": {"interview": interview}}
    namespace = _args(**args)

    ctx = wizard_runner.RunContext(
        args=namespace, cfg=cfg, non_interactive=non_interactive,
        shared=shared, sections=sections, package=sources_pkg)
    ctx.proposal = sources.propose(ctx)
    cards.validate_card(ctx.proposal["card"])
    plan = sources.apply(ctx, source_answers or {})
    sections["sources"] = plan["data"]
    shared.update(plan.get("shared") or {})

    ctx2 = wizard_runner.RunContext(
        args=namespace, cfg=cfg, non_interactive=non_interactive,
        shared=shared, sections=sections, package=integrate_pkg)
    ctx2.proposal = integrate.propose(ctx2)
    cards.validate_card(ctx2.proposal["card"])
    result = integrate.apply(ctx2, integrate_answers or {})
    sections["knowledge"] = result["data"]
    return {"sources": plan, "integrate": result, "sections": sections,
            "ctx": ctx2, "integrate_module": integrate}


def _old(cfg, **args):
    """The control: today's whole knowledge step, scripted, printing nothing."""
    return cli._setup_knowledge_step(
        _args(**args), cfg, posture=ONLINE,
        gates=setup_ops.posture_gates(ONLINE),
        interview={"goal": SPACE_GOAL, "selected": list(SPACE_PACKS)},
        non_interactive=True)


def _folder(tmp_path, name="papers", count=3):
    folder = tmp_path / name
    folder.mkdir()
    for i in range(count):
        (folder / f"note{i}.txt").write_text("notes", encoding="utf-8")
    return str(folder)


# -- equivalence with today's step -------------------------------------------

def _assert_same_as_old(new_block, old_block):
    """Today's record, plus books the old step never searched for.

    The old step searched only BuildBot's own list. The split skills also
    search the Kiwix library, so ``offered`` may carry extra ``kiwix:`` ids
    AFTER the old ones; everything else must be identical.
    """
    new_offered = list(new_block["sources"]["offered"])
    old_offered = list(old_block["sources"]["offered"])
    assert new_offered[:len(old_offered)] == old_offered
    assert all(i.startswith("kiwix:") for i in new_offered[len(old_offered):])
    trimmed = {**new_block, "sources": {**new_block["sources"], "offered": old_offered}}
    assert trimmed == old_block


def test_the_two_skills_write_the_block_the_old_step_wrote(scratch, tmp_path,
                                                           monkeypatch,
                                                           capsys):
    """A --yes run, a goal, a named folder: same record, both ways."""
    _fetch_spy(monkeypatch)
    folder = _folder(tmp_path)
    zim_dir = str(tmp_path / "packs")
    os.makedirs(zim_dir, exist_ok=True)
    scripted = {"own_data": [folder], "zim_dir": zim_dir}

    new = _run_split(scratch, packs=SPACE_PACKS, **scripted)["integrate"]
    old = _old(scratch, **scripted)
    capsys.readouterr()

    _assert_same_as_old(new["data"]["block"], old)
    assert old["sources"]["skipped"] == "non_interactive"
    assert old["own_data"]["location_count"] == 1


def test_the_same_block_when_a_source_is_named_and_its_size_accepted(
        scratch, tmp_path, monkeypatch, capsys):
    calls = _fetch_spy(monkeypatch)
    zim_dir = str(tmp_path / "packs")
    os.makedirs(zim_dir, exist_ok=True)
    scripted = {"fetch_source": [ASTRONOMY], "accept_source_size": True,
                "zim_dir": zim_dir}

    new = _run_split(scratch, packs=SPACE_PACKS, **scripted)["integrate"]
    old = _old(scratch, **scripted)
    capsys.readouterr()

    _assert_same_as_old(new["data"]["block"], old)
    assert old["sources"]["fetched"][0]["id"] == ASTRONOMY
    assert [call["source_id"] for call in calls] == [ASTRONOMY, ASTRONOMY]
    assert all(call["size_acknowledged"] for call in calls)


def test_a_named_source_without_the_size_accepted_is_fetched_by_neither(
        scratch, tmp_path, monkeypatch, capsys):
    calls = _fetch_spy(monkeypatch)
    scripted = {"fetch_source": [ASTRONOMY], "zim_dir": str(tmp_path)}

    new = _run_split(scratch, **scripted)["integrate"]
    old = _old(scratch, **scripted)
    capsys.readouterr()

    assert calls == []
    _assert_same_as_old(new["data"]["block"], old)
    assert ASTRONOMY in old["sources"]["not_fetched"]


def test_skipping_the_zone_writes_what_skipping_wrote(scratch, capsys):
    """Both paths record the skip, in the same words.

    The block used to be empty on both sides, and an empty block is a build
    record that cannot tell "skipped" from "nothing matched": the manifest step
    then wrote the goal-matched packs as the owner's choices and the closing
    screen said they "were chosen during the build and never downloaded". What
    is recorded now is the skip itself, and nothing downstream calls anything
    chosen.
    """
    from puffpai.knowledge import build_sources

    new = _run_split(scratch, skip_knowledge=True)["integrate"]
    old = _old(scratch, skip_knowledge=True)
    capsys.readouterr()
    _assert_same_as_old(new["data"]["block"], old)
    assert old == {"sources": {"offered": [], "fetched": [],
                               "not_fetched": [], "skipped": "skipped"}}
    assert build_sources.sourcing_skipped({"knowledge": old}) == "skipped"


def test_an_offline_or_skipped_sourcing_run_records_the_same_reason(scratch,
                                                                    capsys):
    new = _run_split(scratch, skip_sourcing=True)["integrate"]
    old = _old(scratch, skip_sourcing=True)
    capsys.readouterr()
    assert new["data"]["block"]["sources"]["skipped"] == "owner"
    _assert_same_as_old(new["data"]["block"], old)


# -- the card ----------------------------------------------------------------

def test_the_card_says_what_is_fetched_now_and_what_is_left(scratch, tmp_path):
    run = _run_split(scratch, packs=SPACE_PACKS, non_interactive=False,
                     zim_dir=str(tmp_path))
    card = cards.validate_card(run["ctx"].proposal["card"])
    fields = {f["id"]: f for f in card["fields"]}

    # Wikipedia is 55-odd GB: chosen, and deferred by default.
    assert WIKIPEDIA in run["integrate"]["data"]["deferred"]
    assert WIKIPEDIA not in fields["fetch_now"]["current"]
    assert set(fields["fetch_now"]["current"]) == {ASTRONOMY, SPACE}
    assert any("left for later" in f["label"] for f in card["fields"])
    # 566 MB fetched now is under the heavy line, and a download made while
    # building is not "network access after the build" (the card used to say
    # it was). The size is still on the card for the owner to see.
    assert card["consequential"] is False
    assert card["consequential_reasons"] == []
    assert card["estimate"]["bytes"] > 500 * 1024 * 1024


def test_a_deferred_source_becomes_a_gap_carrying_the_fetch_command(
        scratch, tmp_path, monkeypatch):
    _fetch_spy(monkeypatch)
    run = _run_split(scratch, packs=SPACE_PACKS, non_interactive=False,
                     zim_dir=str(tmp_path))
    gaps = {gap["skill"] + gap["what"]: gap
            for gap in run["integrate"].get("gaps") or []}
    fixes = [gap["fix"] for gap in gaps.values()]
    assert {"kind": "command", "id": "source.fetch",
            "args": {"source_id": WIKIPEDIA}} in fixes


def test_the_owner_can_defer_anything_by_unticking_it(scratch, tmp_path,
                                                      monkeypatch):
    calls = _fetch_spy(monkeypatch)
    run = _run_split(scratch, packs=SPACE_PACKS, non_interactive=False,
                     zim_dir=str(tmp_path),
                     integrate_answers={"fetch_now": [ASTRONOMY]})
    assert [call["source_id"] for call in calls] == [ASTRONOMY]
    assert set(run["integrate"]["data"]["deferred"]) == {WIKIPEDIA, SPACE}


def test_the_estimate_never_claims_a_duration_it_did_not_measure(scratch,
                                                                 tmp_path):
    run = _run_split(scratch, packs=SPACE_PACKS, non_interactive=False,
                     zim_dir=str(tmp_path))
    estimate = cards.validate_card(run["ctx"].proposal["card"])["estimate"]
    assert estimate["basis"] in ("measured", "calibrated", "unknown")
    if estimate["basis"] == "unknown":
        assert estimate["seconds"] is None


def test_propose_downloads_nothing_and_starts_no_indexing(scratch, tmp_path,
                                                          monkeypatch):
    def _never(*a, **k):
        raise AssertionError("propose moved bytes")

    monkeypatch.setattr(setup_ops, "fetch_source", _never)
    monkeypatch.setattr(setup_ops, "start_indexing", _never)
    sources, sources_pkg, integrate, integrate_pkg = _modules()
    interview = {"goal": SPACE_GOAL, "selected": SPACE_PACKS}
    ctx = wizard_runner.RunContext(
        args=_args(zim_dir=str(tmp_path)), cfg=scratch, non_interactive=False,
        shared={"posture": ONLINE, "gates": setup_ops.posture_gates(ONLINE),
                "interview": interview,
                "sources_plan": {"selected": [ASTRONOMY], "offered": [],
                                 "plan": {"items": []}}},
        sections={}, package=integrate_pkg)
    cards.validate_card(integrate.propose(ctx)["card"])
    assert ctx._sections == {}


# -- verify -------------------------------------------------------------------

def _verify(cfg, section, tmp_path):
    _s, _sp, integrate, package = _modules()
    ctx = wizard_runner.RunContext(
        args=_args(), cfg=cfg, non_interactive=True, shared={},
        sections={"knowledge": section}, package=package)
    return integrate.verify(ctx)


def _fetched_section(zim_dir, source_id=ASTRONOMY):
    return {"zim_dir": zim_dir, "packs": [],
            "block": {"sources": {"offered": [], "not_fetched": [],
                                  "skipped": None,
                                  "fetched": [{"id": source_id,
                                               "label": "Astronomy",
                                               "license": "CC-BY-SA-4.0",
                                               "bytes": 3, "sha256": "abc"}]}}}


def _land_a_pack(zim_dir, source_id=ASTRONOMY, name="astronomy.zim"):
    from puffpai import source_fetch

    os.makedirs(zim_dir, exist_ok=True)
    path = os.path.join(zim_dir, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("pack")
    with open(path + source_fetch.PROVENANCE_SUFFIX, "w",
              encoding="utf-8") as handle:
        json.dump({"schema": source_fetch.PROVENANCE_SCHEMA,
                   "source_id": source_id, "filename": name, "bytes": 4,
                   "fetched_at": "2026-09-19T00:00:00+00:00",
                   "revoked_at": None, "path": path}, handle)
    return path


def test_a_source_recorded_as_fetched_with_nothing_on_disk_is_a_gap(scratch,
                                                                    tmp_path):
    zim_dir = str(tmp_path / "packs")
    os.makedirs(zim_dir, exist_ok=True)
    verdict = _verify(scratch, _fetched_section(zim_dir), tmp_path)
    assert verdict["status"] == "gap"
    assert verdict["gap"]["fix"] == {"kind": "command", "id": "source.fetch",
                                    "args": {"source_id": ASTRONOMY}}


def test_a_fetched_pack_that_is_not_indexed_yet_points_at_the_sync_command(
        scratch, tmp_path):
    zim_dir = str(tmp_path / "packs")
    _land_a_pack(zim_dir)
    verdict = _verify(scratch, _fetched_section(zim_dir), tmp_path)
    assert verdict["status"] == "gap"
    assert verdict["gap"]["fix"]["id"] == "knowledge.sync"


def test_a_chosen_pack_that_was_never_downloaded_is_a_gap(scratch, tmp_path):
    zim_dir = str(tmp_path / "packs")
    os.makedirs(zim_dir, exist_ok=True)
    section = {"zim_dir": zim_dir, "packs": ["pack:wikipedia"],
               "block": {"sources": {"offered": [], "fetched": [],
                                     "not_fetched": [WIKIPEDIA],
                                     "skipped": None}}}
    verdict = _verify(scratch, section, tmp_path)
    assert verdict["status"] == "gap"
    assert verdict["gap"]["fix"]["id"] == "source.fetch"


def test_a_build_that_chose_nothing_verifies_clean(scratch, tmp_path):
    zim_dir = str(tmp_path / "packs")
    os.makedirs(zim_dir, exist_ok=True)
    section = {"zim_dir": zim_dir, "packs": [],
               "block": {"sources": {"offered": [], "fetched": [],
                                     "not_fetched": [], "skipped": None}}}
    assert _verify(scratch, section, tmp_path)["status"] == "pass"


def test_verify_reads_the_disk_and_not_the_proposal(scratch, tmp_path,
                                                    monkeypatch):
    seen: list[str] = []
    real = build_sources.on_disk_sources

    def _watch(*directories):
        seen.extend(directories)
        return real(*directories)

    monkeypatch.setattr(build_sources, "on_disk_sources", _watch)
    zim_dir = str(tmp_path / "packs")
    _land_a_pack(zim_dir)
    _verify(scratch, _fetched_section(zim_dir), tmp_path)
    assert any(zim_dir == str(d) for d in seen)


# -- the knowledge section keeps the shape the rest of the build reads --------

def test_the_section_carries_todays_shape_for_build_sources_and_prove(
        scratch, tmp_path, monkeypatch, capsys):
    _fetch_spy(monkeypatch)
    zim_dir = str(tmp_path / "packs")
    os.makedirs(zim_dir, exist_ok=True)
    run = _run_split(scratch, packs=SPACE_PACKS, zim_dir=zim_dir,
                     fetch_source=[ASTRONOMY], accept_source_size=True)
    capsys.readouterr()
    section = run["integrate"]["data"]

    assert section["packs"] == SPACE_PACKS
    assert set(section["sources"]) == {"offered", "fetched", "not_fetched",
                                       "skipped"}
    assert section["own_data"]["location_count"] == 0
    assert section["status"] == "fetched"
    # The shape build_sources reads a manifest in, straight off the section.
    status = build_sources.source_status(
        {"knowledge": {"sources": section["sources"],
                       "packs": section["packs"]}},
        data_dir=str(tmp_path), zim_dir=zim_dir)
    assert status["packs_chosen"] == SPACE_PACKS
    assert ASTRONOMY in {item["id"] for item in status["items"]}


def test_the_manifest_step_still_finds_its_block_where_it_looks(scratch,
                                                                tmp_path,
                                                                capsys):
    run = _run_split(scratch, zim_dir=str(tmp_path))
    capsys.readouterr()
    sections = run["sections"]
    # cli._run_setup's manifest part reads exactly this.
    assert isinstance((sections.get("knowledge") or {}).get("block"), dict)


def test_the_plan_the_owner_agreed_to_is_what_integrate_carries_out(scratch,
                                                                    tmp_path):
    run = _run_split(scratch, packs=SPACE_PACKS, non_interactive=False,
                     zim_dir=str(tmp_path))
    planned = set(run["sections"]["sources"]["selected"])
    carried = set(run["integrate"]["data"]["deferred"]) | set(
        run["ctx"].proposal["fetch_now"])
    assert planned == carried


def test_heavy_is_one_number_both_halves_agree_on():
    assert sources_plan.HEAVY_BYTES == 2 * 1024 ** 3
    items = [{"id": "big", "bytes": sources_plan.HEAVY_BYTES + 1},
             {"id": "small", "bytes": 10}]
    from puffpai.wizard import integrate_fetch
    now, later = integrate_fetch.fetch_plan(items, ["big", "small"])
    assert now == ["small"] and later == ["big"]


# -- through the runner -------------------------------------------------------

def test_the_runner_walks_integrate_and_records_what_it_showed(scratch,
                                                               tmp_path,
                                                               monkeypatch):
    _fetch_spy(monkeypatch)
    zim_dir = str(tmp_path / "packs")
    os.makedirs(zim_dir, exist_ok=True)
    run = wizard_runner.Runner(
        args=_args(zim_dir=zim_dir), cfg=scratch, non_interactive=False,
        schedule=(wizard_runner.Step("integrate.card", "integrate",
                                     wizard_runner.V2_PART),),
        state_path=str(tmp_path / "wizard_run.json"),
        renderer=lambda card, **kw: {})
    run.shared.update({
        "posture": ONLINE, "gates": setup_ops.posture_gates(ONLINE),
        "interview": {"goal": SPACE_GOAL, "selected": SPACE_PACKS},
        "sources_plan": {
            "selected": [ASTRONOMY], "offered": [ASTRONOMY],
            "plan": {"items": [copy.deepcopy(_astronomy_item())]}}})
    run.run()

    row = run.log[-1]
    assert row["outcome"] == "done"
    assert row["shown"]["title"] == "Getting it in"
    assert run.sections["knowledge"]["fetched"] == [ASTRONOMY]


def _astronomy_item() -> dict:
    from puffpai import source_registry
    return sources_plan.row(source_registry.get_source(ASTRONOMY))


# -- every build has a knowledge folder; library books need the owner's yes ---

def test_a_yes_run_with_no_folder_named_gets_one_under_its_data_dir(scratch):
    """A real run ended with 'No knowledge folder configured'."""
    result = _run_split(scratch)["integrate"]
    expected = os.path.join(str(scratch.DATA_DIR), "knowledge_packs")
    assert result["data"]["zim_dir"] == expected
    assert os.path.isdir(expected)
    assert get_config(_force_reload=True).knowledge.zim_dir == expected


def test_a_library_book_is_recorded_only_after_a_yes(scratch, monkeypatch):
    from puffpai import source_registry
    from puffpai.wizard import integrate_fetch

    calls = _fetch_spy(monkeypatch)
    book = "kiwix:vikidia_en_all:nopic"

    out = integrate_fetch.run_fetches([book], ONLINE, acknowledged=False)
    assert out["not_fetched"] == [book] and calls == []
    assert source_registry.load_overlay() == []

    out = integrate_fetch.run_fetches([book], ONLINE, acknowledged=True)
    assert [c["source_id"] for c in calls] == [book]
    rows = source_registry.load_overlay()
    assert [r["id"] for r in rows] == [book]
    assert rows[0]["added_via"] == "wizard" and rows[0]["provenance"] == "owner_added"


def test_a_library_book_with_no_known_license_is_never_recorded(scratch, monkeypatch):
    from puffpai import source_registry
    from puffpai.wizard import integrate_fetch

    calls = _fetch_spy(monkeypatch)
    out = integrate_fetch.run_fetches(["kiwix:gardenology_en_all"], ONLINE,
                                      acknowledged=True)
    assert out["not_fetched"] == ["kiwix:gardenology_en_all"]
    assert calls == [] and source_registry.load_overlay() == []


def test_the_owner_can_tick_a_library_book_on_the_card(scratch, monkeypatch):
    calls = _fetch_spy(monkeypatch)
    book = "kiwix:ted_mul_dinosaurs"
    run = _run_split(scratch, goal="I love dinosaurs", non_interactive=False,
                     source_answers={"selected": [book]},
                     integrate_answers={"fetch_now": [book]})
    assert book in [i["id"] for i in run["sources"]["data"]["plan"]["items"]]
    assert [c["source_id"] for c in calls] == [book]


def test_the_folders_the_owner_ticks_are_learned_and_counted_without_names(
        scratch, tmp_path, monkeypatch):
    folder = _folder(tmp_path, name="Oncology notes", count=3)
    seen = []
    from puffpai import ingestion

    def _record(path, original_filename=None, user_hint=None, auto_apply=True, *, vault=None):
        seen.append({"path": path, "hint": user_hint, "auto_apply": auto_apply,
                     "vault": vault})
        return _Ingested()

    monkeypatch.setattr(ingestion, "ingest_file", _record)
    data = _run_split(scratch, own_data=[folder])["integrate"]["data"]
    learned = data["own_data_learned"]
    assert learned["learned"] == 3
    assert "Learned 3 file(s)" in " ".join(learned["lines"])
    # The owner's originals are never handed to the pipeline (it deletes its
    # input after vaulting), and nothing of the folder name is recorded.
    assert all(not s["path"].startswith(folder) for s in seen)
    assert all(s["auto_apply"] is False and "study" in s["hint"] for s in seen)
    assert all(s["vault"] is not None for s in seen), "a locked vault, never a server import"
    assert "Oncology" not in json.dumps(learned)
    assert sorted(os.listdir(folder)) == ["note0.txt", "note1.txt", "note2.txt"]
