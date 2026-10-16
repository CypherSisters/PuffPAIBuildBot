"""Tests for the BuildBot wizard: census, brain menu, crew, interview, manifest.

Covers the census-first steps the wizard grows into and the /setup/* endpoints
that back them. Two properties get special attention because they are the ones a
regression would quietly break:

* **RAM-first fitting is honest** — a model above the machine's floor is never
  offered, and measured numbers surface only for benched hardware classes.
* **The contracts are not manifest-configurable** — a smuggled field that tries
  to disable the vault, consent, gates, or telemetry never survives a write.

Run:
    pytest tests/test_buildbot_wizard.py
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import (
    build_interview,
    build_manifest,
    crew_sizing,
    hardware_census,
    model_catalog,
    setup_ops,
)
from puffpai.config import get_config


@pytest.fixture
def scratch(tmp_path):
    """Isolate PUFFPAI_DATA_DIR for one test, restoring the singleton after."""
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


def _census(ram_gb, hardware_class="mid", accel="none"):
    """A minimal synthetic census for fitting tests."""
    return {
        "schema": hardware_census.SCHEMA,
        "memory": {"total_gb": ram_gb, "source": "stated"},
        "hardware_class": hardware_class,
        "model_budget_gb": hardware_census.usable_model_budget_gb(ram_gb),
        "accelerator": {"kind": accel},
        "cpu": {"model": "test", "logical_cores": 4},
        "disk": {},
        "soc": {},
        "notes": [],
    }


# ── hardware census ──────────────────────────────────────────────────────────

def test_run_census_never_raises_and_has_shape():
    census = hardware_census.run_census()
    assert census["schema"] == hardware_census.SCHEMA
    for key in ("os", "cpu", "memory", "accelerator", "disk", "hardware_class"):
        assert key in census


@pytest.mark.parametrize("ram,expected", [
    (None, "unknown"),
    (4, "minimal"),
    (8, "entry"),
    (16, "mid"),
    (24, "mid"),
    (32, "high"),
    (48, "workstation"),
    (64, "workstation"),
])
def test_classify_hardware_ram_first(ram, expected):
    assert hardware_census.classify_hardware(ram, "none") == expected


def test_model_budget_reserves_headroom():
    assert hardware_census.usable_model_budget_gb(None) is None
    # 20% reserve, clamped [2, 8].
    assert hardware_census.usable_model_budget_gb(64) == 56.0   # cap 8
    assert hardware_census.usable_model_budget_gb(16) == 12.8   # 20%
    assert hardware_census.usable_model_budget_gb(8) == 6.0     # floor 2


def test_apply_stated_is_first_class():
    census = hardware_census.run_census()
    census["memory"]["total_gb"] = None
    stated = hardware_census.apply_stated(census, total_ram_gb=16, accelerator_kind="none")
    assert stated["memory"]["total_gb"] == 16.0
    assert stated["memory"]["source"] == "stated"
    assert stated["hardware_class"] == "mid"
    assert stated["model_budget_gb"] == 12.8
    # Does not mutate the original.
    assert census["memory"]["total_gb"] is None


def test_census_summary_lines_are_ascii():
    census = hardware_census.run_census()
    for line in hardware_census.census_summary_lines(census):
        assert line.isascii(), f"non-ascii wizard string would garble a console: {line!r}"


# ── model catalog / brain menu ───────────────────────────────────────────────

def test_catalog_loads_with_models():
    cat = model_catalog.load_catalog()
    assert cat.get("models")
    assert any(m["id"] == "qwen3:30b-a3b" for m in cat["models"])


def test_fit_menu_ram_first_excludes_too_big():
    menu = model_catalog.fit_menu(_census(8, "entry"))
    fitting_ids = {o["id"] for o in menu["options"]}
    too_big_ids = {t["id"] for t in menu["too_big"]}
    assert "qwen3:30b-a3b" in too_big_ids       # 24GB floor, 8GB machine
    assert "qwen3:30b-a3b" not in fitting_ids
    assert menu["recommended"] is not None


def test_fit_menu_recommends_the_9b_tool_model_on_a_big_box():
    # The rule: 9B or larger where it fits, and
    # the 9B rather than the 19 GB row when nothing is installed.
    menu = model_catalog.fit_menu(_census(64, "workstation"))
    assert menu["recommended"]["id"] == "qwen3.5:9b"


def _jetson_census(ram_gb=64):
    """A census that really IS the board the numbers were measured on."""
    census = _census(ram_gb, "workstation", accel="jetson_integrated")
    census["soc"] = {"model": "NVIDIA Jetson AGX Orin"}
    return census


def test_fit_menu_honest_tiers_benched_vs_unbenched():
    # The measured numbers belong to the Jetson AGX Orin, so they appear on a
    # machine that is one.
    ws = model_catalog.fit_menu(_jetson_census())
    q3 = next(o for o in ws["options"] if o["id"] == "qwen3:30b-a3b")
    assert q3["benchmark"] and "classify_p50_s" in q3["benchmark"]
    # Mid class is not benched -> the same model says reports-welcome, no numbers.
    mid = model_catalog.fit_menu(_census(24, "mid"))
    q3m = next(o for o in mid["options"] if o["id"] == "qwen3:30b-a3b")
    assert q3m["benchmark"] is None
    assert "reports welcome" in q3m["benchmark_note"].lower()


def test_a_plain_workstation_is_not_handed_the_jetsons_numbers():
    """The review's finding: class alone put Orin timings on a Windows laptop.

    Every Jetson AND every desktop with a discrete GPU is class ``workstation``,
    so matching on class attached the authors' measurements to a stranger's
    machine - which reads as "hard-coded to the authors' hardware", the opposite
    of what a census is for. ``fit_menu`` now applies the same board-identity
    check the CLI's ``basis_label`` already did.
    """
    laptop = _census(64, "workstation", accel="nvidia_dgpu")
    laptop["accelerator"] = {"kind": "nvidia_dgpu",
                             "name": "NVIDIA RTX 2000 Ada", "vram_gb": 8}
    q3 = next(o for o in model_catalog.fit_menu(laptop)["options"]
              if o["id"] == "qwen3:30b-a3b")
    assert q3["benchmark"] is None
    assert "reports welcome" in q3["benchmark_note"].lower()


def test_a_gpu_smaller_than_the_model_is_said_out_loud():
    """RAM-only fitting told an 8 GB card that a 19 GB model "fits". It does not."""
    laptop = _census(64, "workstation")
    # 6 GB: smaller than the 7 GB 9B tool model the wizard recommends;
    # an 8 GB card holds that one.
    laptop["accelerator"] = {"kind": "nvidia_dgpu",
                             "name": "NVIDIA RTX 2000 Ada", "vram_gb": 6}
    menu = model_catalog.fit_menu(laptop, installed_models=["qwen2.5:7b-instruct"])
    reason = menu["recommended"]["reason"]
    assert "fits your RAM" in reason
    assert "6 GB of VRAM" in reason
    assert "runs on the CPU" in reason
    assert "expect it to be slow" in reason
    # And it names the model already on the box rather than only warning.
    assert "you already have will feel faster" in reason

    q3 = next(o for o in menu["options"] if o["id"] == "qwen3:30b-a3b")
    assert q3["vram_note"] and "6 GB of VRAM" in q3["vram_note"]
    lines = "\n".join(model_catalog.menu_summary_lines(menu))
    assert "6 GB of VRAM" in lines


def test_unified_memory_and_no_gpu_get_no_vram_clause():
    """A Jetson shares its RAM with the GPU, so there is no VRAM to fall short."""
    for census in (_jetson_census(), _census(64, "workstation", accel="none")):
        menu = model_catalog.fit_menu(census)
        assert "VRAM" not in menu["recommended"]["reason"]
        assert all(o["vram_note"] is None for o in menu["options"])


def test_a_gpu_big_enough_says_nothing_extra():
    big = _census(64, "workstation")
    big["accelerator"] = {"kind": "nvidia_dgpu", "name": "RTX 6000", "vram_gb": 48}
    menu = model_catalog.fit_menu(big)
    assert "VRAM" not in menu["recommended"]["reason"]


def test_fit_menu_unknown_ram_asks():
    menu = model_catalog.fit_menu(_census(None, "unknown"))
    assert menu["recommended"] is None
    assert menu["message"]


def test_fit_menu_nothing_fits_is_honest():
    menu = model_catalog.fit_menu(_census(1, "minimal"))
    assert menu["options"] == []
    assert menu["recommended"] is None
    assert "below" in menu["message"].lower()


def test_fit_menu_marks_installed():
    menu = model_catalog.fit_menu(_census(64, "workstation"),
                                  installed_models=["qwen3:30b-a3b"])
    q3 = next(o for o in menu["options"] if o["id"] == "qwen3:30b-a3b")
    assert q3["installed"] is True


# ── crew sizing ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("klass,specialists,basis", [
    ("workstation", 5, "reference"),
    ("high", 4, "conservative-default"),
    ("mid", 2, "conservative-default"),
    ("entry", 1, "conservative-default"),
    ("minimal", 0, "conservative-default"),
    ("unknown", 0, "conservative-default"),
])
def test_crew_budget_by_class(klass, specialists, basis):
    budget = crew_sizing.crew_budget({"hardware_class": klass})
    assert budget["max_specialists"] == specialists
    assert budget["basis"] == basis


def test_crew_budget_reads_master_core():
    budget = crew_sizing.crew_budget({"hardware_class": "mid"})
    # Best-effort read of the live roster; 6 today, and never a wild number.
    assert budget["master_core"] is None or budget["master_core"] >= 1


# ── build manifest + the contract guard ──────────────────────────────────────

def test_assemble_manifest_has_contracts_and_sections():
    man = build_manifest.assemble_manifest(_census(64, "workstation"))
    assert man["schema"] == build_manifest.SCHEMA
    for key in ("build", "hardware", "brain", "crew", "egress", "contracts"):
        assert key in man
    assert "vault" in man["contracts"]


def test_validate_flags_smuggled_contract_disable():
    man = build_manifest.assemble_manifest(_census(64, "workstation"))
    man["disable_vault"] = True
    man["integrations"]["telemetry_enabled"] = True
    warnings = build_manifest.validate_manifest(man)
    assert any("disable_vault" in w for w in warnings)
    assert any("telemetry_enabled" in w for w in warnings)


def test_sanitize_strips_forbidden_keys():
    man = build_manifest.assemble_manifest(_census(64, "workstation"))
    man["disable_consent"] = True
    man["integrations"]["phone_home"] = "evil.example.com"
    clean = build_manifest.sanitize_manifest(man)
    assert "disable_consent" not in clean
    assert "phone_home" not in clean["integrations"]
    # Contracts are always the canonical text.
    assert clean["contracts"]["vault"].startswith("Encrypted")


def test_write_strips_contract_override_on_disk(scratch):
    man = build_manifest.assemble_manifest(_census(64, "workstation"))
    man["disable_vault"] = True
    path = build_manifest.write_manifest(man)
    on_disk = json.loads(open(path, encoding="utf-8").read())
    assert "disable_vault" not in on_disk
    round_tripped = build_manifest.read_manifest()
    assert round_tripped is not None
    assert "disable_vault" not in round_tripped


def test_diff_manifest_reports_meaningful_changes():
    old = build_manifest.assemble_manifest(
        _census(16, "mid"), brain={"model": "qwen2.5:7b-instruct"})
    new = build_manifest.assemble_manifest(
        _census(64, "workstation"), brain={"model": "qwen3:30b-a3b"})
    lines = build_manifest.diff_manifest(old, new)
    joined = " ".join(lines)
    assert "Brain" in joined and "Memory" in joined


def test_diff_manifest_new_when_no_previous():
    new = build_manifest.assemble_manifest(_census(64, "workstation"))
    assert build_manifest.diff_manifest(None, new) == [
        "New build manifest (no previous one to compare)."]


# ── build interview ──────────────────────────────────────────────────────────

def test_interview_matches_astronomy_goal():
    proposal = build_interview.propose("a space bot that knows about astronomy")
    ids = {e["id"] for e in proposal["flat"]}
    assert "pack:stackexchange_science" in ids


def test_interview_matches_medical_goal():
    proposal = build_interview.propose("help with my clinic and first aid")
    ids = {e["id"] for e in proposal["flat"]}
    assert "pack:wikem" in ids


def test_interview_empty_goal_prompts():
    proposal = build_interview.propose("")
    assert proposal["flat"] == []
    assert proposal["message"]


def test_interview_nonsense_goal_is_honest():
    """Nothing matched must SAY nothing matched, and offer the way forward.

    The old sentence was "no strong matches ... add things by hand", which reads
    as "we might have something, look harder". A goal we ship nothing for gets
    the plain fact plus the scaffold offer.
    """
    proposal = build_interview.propose("zzxq qwph")
    assert proposal["flat"] == []
    message = proposal["message"].lower()
    assert "nothing we ship matches that yet" in message
    assert "scaffold a blank persona" in message
    assert build_interview.SCAFFOLD_STEP in message
    assert proposal["scaffold_offer"] is True


def test_interview_goal_is_sanitized_data_only():
    dirty = "build\x00\x07 me a \x1b[31m space bot"
    proposal = build_interview.propose(dirty)
    # Control characters stripped; stored as data, never as an instruction.
    assert "\x00" not in proposal["goal"]
    assert "\x1b" not in proposal["goal"]


def test_interview_goal_length_capped():
    """Capped, but not below what the prompt invited.

    The cap was 240 while the CLI invited a 1000-character paragraph and told
    the owner it would trim at 1000 - so a long goal was cut twice, the second
    time silently. The bound is now the CLI's, and the two are pinned together
    in tests/test_wizard_ux_fixes.py.
    """
    proposal = build_interview.propose("space " * 400)
    assert len(proposal["goal"]) <= build_interview._MAX_GOAL_LEN
    # Still bounded - an unbounded goal is a storage and rendering problem.
    assert len(proposal["goal"]) < len("space " * 400)


# ── setup_ops wrappers ───────────────────────────────────────────────────────

def test_setup_ops_preview_does_not_write(scratch):
    census = _census(64, "workstation")
    preview = setup_ops.preview_manifest(census, brain={"model": "qwen3:30b-a3b"})
    assert preview["manifest"]["brain"]["model"] == "qwen3:30b-a3b"
    # Preview must not create a manifest on disk.
    assert setup_ops.manifest_status()["exists"] is False


def test_setup_ops_commit_then_status(scratch):
    census = _census(64, "workstation")
    preview = setup_ops.preview_manifest(census, brain={"model": "qwen3:30b-a3b"})
    setup_ops.commit_manifest(preview["manifest"])
    status = setup_ops.manifest_status()
    assert status["exists"] is True
    assert any("qwen3:30b-a3b" in line for line in status["summary"])


def test_setup_status_includes_manifest(scratch):
    status = setup_ops.setup_status(probe_ollama=False)
    assert "manifest" in status
    assert status["manifest"]["exists"] is False


# ── API endpoints ────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from puffpai import api_server
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def _auth():
    from puffpai import api_server
    return {"Authorization": f"Bearer {api_server._API_KEY}"}


def _no_ollama(monkeypatch):
    """Keep brain-menu tests offline: no models installed."""
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])


def test_hardware_endpoint_requires_key(client):
    assert client.get("/setup/hardware").status_code == 401


def test_hardware_endpoint_returns_census(client, scratch):
    resp = client.get("/setup/hardware", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["census"]["schema"] == hardware_census.SCHEMA
    assert isinstance(body["summary"], list)


def test_hardware_endpoint_applies_stated_ram(client, scratch):
    resp = client.post("/setup/hardware", json={"stated_ram": 16}, headers=_auth())
    body = resp.get_json()
    assert body["census"]["memory"]["total_gb"] == 16.0
    assert body["census"]["memory"]["source"] == "stated"
    assert body["census"]["hardware_class"] == "mid"


def test_brain_menu_endpoint_fits_stated_ram(client, scratch, monkeypatch):
    _no_ollama(monkeypatch)
    resp = client.post("/setup/brain-menu", json={"stated_ram": 8}, headers=_auth())
    assert resp.status_code == 200
    menu = resp.get_json()["menu"]
    too_big = {t["id"] for t in menu["too_big"]}
    assert "qwen3:30b-a3b" in too_big
    assert menu["recommended"] is not None


def test_crew_endpoint_returns_budget(client, scratch):
    resp = client.post("/setup/crew", json={"stated_ram": 64}, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["crew"]["max_specialists"] == 5


def test_interview_endpoint_matches_goal(client, scratch):
    resp = client.post("/setup/interview",
                       json={"goal": "astronomy and telescopes"}, headers=_auth())
    assert resp.status_code == 200
    ids = {e["id"] for e in resp.get_json()["proposal"]["flat"]}
    assert "pack:stackexchange_science" in ids


def test_manifest_endpoint_preview_then_commit(client, scratch, monkeypatch):
    _no_ollama(monkeypatch)
    # GET: nothing yet.
    assert client.get("/setup/manifest", headers=_auth()).get_json()["exists"] is False
    # POST without commit: preview only, still nothing on disk.
    preview = client.post("/setup/manifest",
                          json={"stated_ram": 64, "brain": {"model": "qwen3:30b-a3b"}},
                          headers=_auth()).get_json()
    assert preview["written"] is False
    assert client.get("/setup/manifest", headers=_auth()).get_json()["exists"] is False
    # POST with commit: written.
    committed = client.post("/setup/manifest",
                            json={"stated_ram": 64, "brain": {"model": "qwen3:30b-a3b"},
                                  "commit": True},
                            headers=_auth()).get_json()
    assert committed["written"] is True
    assert client.get("/setup/manifest", headers=_auth()).get_json()["exists"] is True


def test_manifest_endpoint_strips_contract_override(client, scratch, monkeypatch):
    _no_ollama(monkeypatch)
    resp = client.post("/setup/manifest",
                       json={"stated_ram": 64,
                             "integrations": {"home_assistant": False,
                                              "telemetry_enabled": True},
                             "commit": True},
                       headers=_auth())
    body = resp.get_json()
    assert "telemetry_enabled" not in body["manifest"]["integrations"]
    assert any("telemetry_enabled" in w for w in body["warnings"])
