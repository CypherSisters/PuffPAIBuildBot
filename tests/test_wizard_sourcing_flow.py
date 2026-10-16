"""Tests for Zone 2 of the wizard as the CLI actually walks it.

The modules underneath - the source registry, the fetcher, the owner's-data
survey, the indexing estimate - all have their own tests. This file is about the
*wiring*, and specifically about the four things that would be silent, invisible
failures if the wiring were wrong:

1. Posture gates the sourcing menu. An offline build is never shown a catalog it
   is not allowed to use, and is told where the door actually is.
2. The consent screen comes before the bytes. License, size and address are on
   screen before ``fetch`` is called, and ``fetch`` is never called at all
   without the acknowledgement only a person can give.
3. A declined folder leaves no trace. Not a path, not a count, not a mention.
4. The estimate is spoken, including - especially including - the honest
   "I cannot tell you yet". A wizard that suppresses the no-basis case is a
   wizard that has quietly started inventing durations.

Run:
    pytest tests/test_wizard_sourcing_flow.py
"""
from __future__ import annotations

import builtins
import json
import os

import pytest

from puffpai import build_manifest, cli, data_sources, index_estimate, setup_ops
from puffpai.config import get_config
from puffpai.knowledge import auto_index

# A real id from the shipped registry: a ZIM pack with a license, a size and an
# address, which is exactly what the consent screen has to be able to show.
ASTRONOMY = "kiwix_astronomy_stackexchange"

ONLINE = {"build_network": "online", "run_egress": "island"}
OFFLINE = {"build_network": "offline", "run_egress": "sealed"}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def scratch(tmp_path):
    """Point the config singleton at an isolated data dir for one test."""
    prev = os.environ.get("PUFFPAI_DATA_DIR")
    os.environ["PUFFPAI_DATA_DIR"] = str(tmp_path)
    get_config(_force_reload=True)
    try:
        yield tmp_path
    finally:
        if prev is not None:
            os.environ["PUFFPAI_DATA_DIR"] = prev
        else:
            os.environ.pop("PUFFPAI_DATA_DIR", None)
        get_config(_force_reload=True)


@pytest.fixture
def no_ollama(monkeypatch):
    """Keep the wizard off the network: Ollama is up with nothing installed."""
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])


@pytest.fixture
def printed(monkeypatch):
    """Every line the wizard printed, in order, readable mid-test.

    ``capsys`` can only be drained from the test body, and the load-bearing
    assertion here is about what was on screen *at the moment* fetch was called.
    So the recorder has to be live.
    """
    lines: list[str] = []

    def _print(*args, **kwargs):
        lines.append(" ".join(str(a) for a in args))

    monkeypatch.setattr(builtins, "print", _print)
    return lines


def _args(**overrides):
    """A setup Namespace with every Zone 2 flag at its off position."""
    import argparse
    base = {
        "skip_knowledge": False, "skip_sourcing": False, "skip_own_data": False,
        "fetch_source": None, "accept_source_size": False, "own_data": None,
        "zim_dir": None, "no_auto_index": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _gates(posture):
    return setup_ops.posture_gates(posture)


def _run_setup(*extra: str) -> int:
    return cli.main(["setup", "--yes", "--skip-ha", *extra])


def _manifest() -> dict:
    return build_manifest.read_manifest() or {}


# ---------------------------------------------------------------------------
# 1. posture gates the whole sourcing step
# ---------------------------------------------------------------------------

def test_an_offline_build_is_never_shown_the_sourcing_menu(scratch, printed,
                                                           monkeypatch):
    def _never(*a, **k):
        raise AssertionError("the registry was searched on an offline build")

    monkeypatch.setattr(setup_ops, "source_search", _never)

    record = cli._setup_sourcing_step(
        _args(), OFFLINE, _gates(OFFLINE),
        {"goal": "a space bot for my telescopes"}, non_interactive=False)

    assert record["skipped"] == "offline_build"
    assert record["offered"] == [] and record["fetched"] == []
    out = "\n".join(printed)
    assert "offline build" in out
    # Skipping never dead-ends: the step names the door that is still open.
    assert "puffpai build import" in out


def test_an_online_build_is_shown_the_matches_and_why_each_matched(
        scratch, printed):
    record = cli._setup_sourcing_step(
        _args(), ONLINE, _gates(ONLINE),
        {"goal": "a space bot for my telescopes"}, non_interactive=True)

    assert ASTRONOMY in record["offered"]
    out = "\n".join(printed)
    # The citation is the reason the source is being offered at all.
    assert "Matched what you asked for on: telescope." in out


def test_the_sourcing_step_is_skippable_without_dead_ending(scratch, printed):
    record = cli._setup_sourcing_step(
        _args(skip_sourcing=True), ONLINE, _gates(ONLINE),
        {"goal": "telescopes"}, non_interactive=False)
    assert record["skipped"] == "owner"
    assert record["offered"] == []


# ---------------------------------------------------------------------------
# 2. the consent screen precedes the bytes
# ---------------------------------------------------------------------------

def _fetch_spy(monkeypatch, printed, result=None):
    """Replace the fetch wrapper with a spy that snapshots the screen."""
    calls: list[dict] = []

    def _spy(source_id, posture, *, size_acknowledged=False, dest_dir=None):
        calls.append({
            "source_id": source_id,
            "size_acknowledged": size_acknowledged,
            "screen": list(printed),
        })
        return result or {
            "schema": "source_fetch/v1", "ok": True, "status": "fetched",
            "source_id": source_id, "reason": "ok", "path": "x.zim",
            "bytes": 123, "sha256": "abc", "resumed": False,
            "provenance": {"label": "Astronomy Stack Exchange",
                           "license": "CC-BY-SA-4.0"},
        }

    monkeypatch.setattr(setup_ops, "fetch_source", _spy)
    return calls


def test_license_size_and_url_are_on_screen_before_any_fetch(
        scratch, printed, monkeypatch):
    calls = _fetch_spy(monkeypatch, printed)
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *a, **k: True)

    cli._setup_sourcing_step(
        _args(fetch_source=[ASTRONOMY]), ONLINE, _gates(ONLINE),
        {"goal": "telescopes"}, non_interactive=False)

    assert len(calls) == 1
    screen = "\n".join(calls[0]["screen"])
    # All three facts, before the byte moved - not afterwards in a receipt.
    assert "License:" in screen and "CC-BY-SA-4.0" in screen
    assert "Size:" in screen and "about 187 MB" in screen
    assert "From:" in screen
    assert "download.kiwix.org" in screen


def test_fetch_is_never_called_without_the_acknowledgement(
        scratch, printed, monkeypatch):
    calls = _fetch_spy(monkeypatch, printed)
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *a, **k: True)

    cli._setup_sourcing_step(
        _args(fetch_source=[ASTRONOMY]), ONLINE, _gates(ONLINE),
        {"goal": "telescopes"}, non_interactive=False)

    assert calls and all(c["size_acknowledged"] is True for c in calls)


def test_saying_no_on_the_consent_screen_fetches_nothing(
        scratch, printed, monkeypatch):
    calls = _fetch_spy(monkeypatch, printed)
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *a, **k: False)

    record = cli._setup_sourcing_step(
        _args(fetch_source=[ASTRONOMY]), ONLINE, _gates(ONLINE),
        {"goal": "telescopes"}, non_interactive=False)

    assert calls == []
    assert record["fetched"] == []
    assert ASTRONOMY in record["not_fetched"]


def test_the_module_itself_refuses_an_unacknowledged_fetch(scratch):
    """Belt and braces: the guard lives in the module, not only in the CLI."""
    result = setup_ops.fetch_source(ASTRONOMY, ONLINE)
    assert result["ok"] is False
    assert result["status"] == "needs_confirmation"
    assert "187 MB" in result["reason"]


def test_the_fetch_wrapper_exposes_no_registry_parameter():
    """The one argument that could widen what the fetcher may reach.

    Production passes nothing and gets the shipped registry. If this parameter
    ever appears on the wrapper, a front-end can be talked into supplying it.
    """
    import inspect
    assert "registry" not in inspect.signature(setup_ops.fetch_source).parameters
    assert "registry" not in inspect.signature(setup_ops.source_preview).parameters


# ---------------------------------------------------------------------------
# 3. the owner's own data - asked for, and a no leaves nothing behind
# ---------------------------------------------------------------------------

def _folder_with_docs(tmp_path, name="papers", count=3):
    folder = tmp_path / name
    folder.mkdir()
    for i in range(count):
        (folder / f"note{i}.txt").write_text("telescope notes", encoding="utf-8")
    return str(folder)


def test_the_notes_are_printed_before_the_yes_not_after(scratch, printed,
                                                        monkeypatch, tmp_path):
    folder = _folder_with_docs(tmp_path)
    asked_at: list[int] = []

    def _yes(prompt, default=False, **k):
        asked_at.append(len(printed))
        return True

    monkeypatch.setattr(cli, "_ask", lambda *a, **k: folder)
    monkeypatch.setattr(cli, "_ask_yes_no", _yes)

    cli._setup_own_data_step(_args(), non_interactive=False)

    assert asked_at, "the owner was never asked about the folder"
    screen = "\n".join(printed[:asked_at[0]])
    # RECORD_NOTE is useless after the fact - it says what the build record
    # keeps - so it has to be on the confirmation screen.
    assert data_sources.RECORD_NOTE in screen
    assert data_sources.VAULT_NOTE in screen
    assert data_sources.CONSENT_NOTE in screen


def test_a_declined_folder_leaves_no_trace_in_the_manifest_block(
        scratch, printed, monkeypatch, tmp_path):
    folder = _folder_with_docs(tmp_path, name="private")
    monkeypatch.setattr(cli, "_ask", lambda *a, **k: folder)
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *a, **k: False)

    block = cli._setup_own_data_step(_args(), non_interactive=False)

    assert block["location_count"] == 0
    assert block["locations"] == []
    assert block["files"] == 0
    serialized = json.dumps(block)
    assert "private" not in serialized
    assert os.path.basename(folder) not in serialized


def test_an_approved_folder_is_recorded_as_counts_and_nothing_else(
        scratch, printed, monkeypatch, tmp_path):
    folder = _folder_with_docs(tmp_path, name="papers")
    monkeypatch.setattr(cli, "_ask", lambda *a, **k: folder)
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *a, **k: True)

    block = cli._setup_own_data_step(_args(), non_interactive=False)

    assert block["location_count"] == 1
    assert block["files"] == 3
    serialized = json.dumps(block)
    # Counts, yes. Filenames, never.
    assert "note0.txt" not in serialized
    assert block["pii_routing"] == "vault"


def test_nothing_is_surveyed_that_the_owner_did_not_name(
        scratch, printed, monkeypatch):
    # Decouple from whatever happens to exist in the real account: point
    # "home" at the scratch tree and create the folders the suggestion list
    # would offer, so the test is deterministic on any machine/CI runner.
    for name in ("Documents", "Desktop", "Downloads", "Pictures"):
        (scratch / name).mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(scratch))
    monkeypatch.setenv("USERPROFILE", str(scratch))

    def _never(*a, **k):
        raise AssertionError("a folder was surveyed without being named")

    monkeypatch.setattr(setup_ops, "survey_data_locations", _never)
    monkeypatch.setattr(cli, "_ask", lambda *a, **k: "")

    block = cli._setup_own_data_step(_args(), non_interactive=False)
    assert block["location_count"] == 0
    # The suggestion list was shown, and showing it is all it did.
    assert "These are suggestions" in "\n".join(printed)


def test_suggested_locations_are_never_scanned_by_offering_them(scratch):
    for loc in setup_ops.data_location_suggestions():
        assert loc["scanned"] is False
        assert loc["suggestion"] is True


# ---------------------------------------------------------------------------
# 4. the spoken indexing handoff
# ---------------------------------------------------------------------------

def _fake_packs(monkeypatch, packs):
    monkeypatch.setattr(auto_index, "find_new_packs", lambda *a, **k: packs)


def _no_real_indexing(monkeypatch):
    started: list[str] = []

    def _start(zim_dir, **kwargs):
        started.append(zim_dir)
        return (1, None)

    monkeypatch.setattr(auto_index, "start_background_index", _start)
    return started


PACK = {"uuid_hex": "aa" * 16, "entry_count": 100000, "filename": "space.zim"}


def test_with_no_history_the_bot_says_it_cannot_tell_yet(
        scratch, printed, monkeypatch, tmp_path):
    zim_dir = str(tmp_path / "packs")
    os.makedirs(zim_dir, exist_ok=True)
    _fake_packs(monkeypatch, [PACK])
    _no_real_indexing(monkeypatch)

    cli._setup_index_handoff_step(zim_dir, auto_index=True, non_interactive=True)

    screen = "\n".join(printed)
    # The no-basis case is printed, not suppressed and not replaced by a guess.
    assert ("I cannot tell you yet how long this will take. I will give you a "
            "real number once I have made a dent in it.") in screen
    assert "I have nothing to measure yet" in screen
    # And no invented duration rode along with it.
    assert "approximately" not in screen


def test_with_history_the_bot_speaks_the_owners_sentence(
        scratch, printed, monkeypatch, tmp_path):
    """A real measured rate, read from a real pack index on disk."""
    index = auto_index.PackIndex(auto_index.default_pack_index_path())
    index.mark("bb" * 16, status="complete", chunks=360000, docs=90000,
               entry_count=100000,
               started_at="2026-08-01T00:00:00+00:00",
               indexed_at="2026-08-01T01:00:00+00:00")

    zim_dir = str(tmp_path / "packs")
    os.makedirs(zim_dir, exist_ok=True)
    _fake_packs(monkeypatch, [PACK])
    _no_real_indexing(monkeypatch)

    cli._setup_index_handoff_step(zim_dir, auto_index=True, non_interactive=True)

    screen = "\n".join(printed)
    assert "This will take approximately" in screen
    assert "to learn all this new stuff." in screen
    assert "I will do it in the background" in screen
    assert "automatic indexing takes over" in screen
    assert "not a guess from a spec sheet" in screen


def test_the_handoff_does_not_block_the_wizard_on_indexing(
        scratch, printed, monkeypatch, tmp_path):
    zim_dir = str(tmp_path / "packs")
    os.makedirs(zim_dir, exist_ok=True)
    _fake_packs(monkeypatch, [PACK])
    started = _no_real_indexing(monkeypatch)

    def _never(*a, **k):
        raise AssertionError("the wizard indexed in the foreground")

    monkeypatch.setattr(auto_index, "sync_new_packs", _never)

    cli._setup_index_handoff_step(zim_dir, auto_index=True, non_interactive=True)
    assert started == [zim_dir]


def test_calibration_never_runs_and_never_touches_the_live_pack_index(
        scratch, printed, monkeypatch, tmp_path):
    """The scariest failure in this whole zone.

    ``index_pack`` marks a pack ``complete``. A 50-article calibration sample
    written into the live index would tell ``find_new_packs`` that a
    million-article pack was fully learned, and it would be skipped forever. The
    wizard's answer is not to be careful with calibration - it is not to
    calibrate at all, because calibrating also means indexing in the foreground.
    """
    live_path = auto_index.default_pack_index_path()
    index = auto_index.PackIndex(live_path)
    index.mark("cc" * 16, status="complete", chunks=1000, docs=100,
               entry_count=500,
               started_at="2026-08-01T00:00:00+00:00",
               indexed_at="2026-08-01T00:10:00+00:00")
    before = open(live_path, encoding="utf-8").read()

    def _never_calibrate(*a, **k):
        raise AssertionError("the wizard ran a calibration sample")

    monkeypatch.setattr(index_estimate, "calibrate", _never_calibrate)

    zim_dir = str(tmp_path / "packs")
    os.makedirs(zim_dir, exist_ok=True)
    _fake_packs(monkeypatch, [PACK])
    _no_real_indexing(monkeypatch)

    cli._setup_index_handoff_step(zim_dir, auto_index=True, non_interactive=True)

    assert open(live_path, encoding="utf-8").read() == before
    # The waiting pack is still unlearned, which is the whole point.
    assert auto_index.PackIndex(live_path).is_indexed(PACK["uuid_hex"]) is False


def test_auto_index_off_states_the_estimate_but_starts_nothing(
        scratch, printed, monkeypatch, tmp_path):
    zim_dir = str(tmp_path / "packs")
    os.makedirs(zim_dir, exist_ok=True)
    _fake_packs(monkeypatch, [PACK])
    started = _no_real_indexing(monkeypatch)

    cli._setup_index_handoff_step(zim_dir, auto_index=False, non_interactive=True)

    assert started == []
    assert "turned auto-indexing off" in "\n".join(printed)


# ---------------------------------------------------------------------------
# 5. --yes fabricates nothing
# ---------------------------------------------------------------------------

def test_non_interactive_fetches_nothing_and_approves_nothing(
        scratch, no_ollama, monkeypatch, capsys):
    def _no_fetch(*a, **k):
        raise AssertionError("a --yes run downloaded something")

    def _no_survey(*a, **k):
        raise AssertionError("a --yes run read the owner's folders")

    monkeypatch.setattr(setup_ops, "fetch_source", _no_fetch)
    monkeypatch.setattr(setup_ops, "survey_data_locations", _no_survey)

    assert _run_setup("--goal", "a space bot for my telescopes") == 0

    knowledge = _manifest()["knowledge"]
    assert knowledge["sources"]["fetched"] == []
    assert knowledge["sources"]["skipped"] == "non_interactive"
    assert knowledge["own_data"]["location_count"] == 0

    # Said what was skipped and why -- on the card, which is kept in the run
    # record; the console shows the one summary instead of every card.
    import json as _json
    import os as _os
    from puffpai.config import get_config as _cfg
    with open(_os.path.join(_cfg().CONFIG_DIR, "wizard_run.json"),
              encoding="utf-8") as handle:
        log = _json.load(handle)["log"]
    shown = next(row["shown"]["fields"] for row in log
                 if row.get("entry") == "integrate.card" and row.get("shown"))
    assert "no_consent" in shown and "no_folders" in shown
    out = capsys.readouterr().out
    assert "only what you named on the command line" in " ".join(out.split())


def test_naming_a_source_without_accepting_its_size_still_fetches_nothing(
        scratch, no_ollama, monkeypatch, capsys):
    def _no_fetch(*a, **k):
        raise AssertionError("a --yes run downloaded without accepting the size")

    monkeypatch.setattr(setup_ops, "fetch_source", _no_fetch)

    assert _run_setup("--goal", "telescopes",
                      "--fetch-source", ASTRONOMY) == 0

    out = capsys.readouterr().out
    # The way to fetch it later is printed: a command that shows the size and
    # asks for a yes, not a flag that pre-accepts it.
    assert "puffpai sources add " + ASTRONOMY in out
    assert _manifest()["knowledge"]["sources"]["fetched"] == []
    assert ASTRONOMY in _manifest()["knowledge"]["sources"]["not_fetched"]


def test_an_offline_yes_run_records_the_gate_rather_than_a_search(
        scratch, no_ollama):
    assert _run_setup("--goal", "telescopes",
                      "--build-network", "offline") == 0
    sources = _manifest()["knowledge"]["sources"]
    assert sources["skipped"] == "offline_build"
    assert sources["offered"] == []


# ---------------------------------------------------------------------------
# 6. the house rules still hold
# ---------------------------------------------------------------------------

def test_zone_two_prints_ascii_only(scratch, no_ollama, monkeypatch, capsys,
                                    tmp_path):
    folder = _folder_with_docs(tmp_path, name="papers")
    assert _run_setup("--goal", "a space bot for my telescopes",
                      "--fetch-source", ASTRONOMY,
                      "--own-data", folder) == 0
    out = capsys.readouterr().out
    offenders = sorted({ch for ch in out if ord(ch) > 127})
    assert not offenders, f"non-ASCII in wizard output: {offenders}"


def test_a_re_run_re_decides_the_zone_rather_than_keeping_stale_choices(
        scratch, no_ollama, capsys, tmp_path):
    """Zone 2 is re-asked on a re-run, and the manifest is previewed first.

    The wizard never writes a zone block it did not just re-derive: the second
    run declines the folder the first run approved, and the record follows the
    owner rather than the file that is already on disk.
    """
    folder = _folder_with_docs(tmp_path, name="papers")
    assert _run_setup("--goal", "telescopes", "--own-data", folder) == 0
    assert _manifest()["knowledge"]["own_data"]["location_count"] == 1

    capsys.readouterr()
    assert _run_setup("--goal", "telescopes", "--skip-own-data") == 0
    out = capsys.readouterr().out
    # The plan was shown before anything was written, on the re-run as much as
    # the first one; the record itself is no longer printed at the owner.
    assert "Here is the plan for" in out and "Build manifest:" not in out
    assert _manifest()["knowledge"]["own_data"]["location_count"] == 0
