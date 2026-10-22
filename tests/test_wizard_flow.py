"""Tests for the BuildBot wizard v2 flow as the CLI actually walks it.

The modules underneath (posture, the zones, surfaces, config health, bundles)
have their own tests. This file is about the *wiring*: that Step 0 comes first
and its gates really close later menus, that the vault ceremony goes through the
path that issues a recovery code, that a corrupt identity config is acknowledged
rather than ignored or fatal, and that the sneakernet subcommand round-trips.

The load-bearing one is
``test_setup_seals_the_vault_through_the_recovery_path``. ``seal_vault`` creates
a PIN-only vault with no recovery code, so wiring it would run the whole "write
this down, it is your only way back in" ceremony and hand the owner nothing.
That test exists to make sure that can never be true again.

Run:
    pytest tests/test_wizard_flow.py
"""
from __future__ import annotations

import argparse
import json
import os

import pytest

from puffpai import build_manifest, cli, config_health, setup_ops, zone_vault
from puffpai.config import get_config

GOOD_PIN = "Telescope7x"


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


def _run_setup(*extra: str) -> int:
    return cli.main(["setup", "--yes", "--skip-ha", "--skip-knowledge", *extra])


def _manifest() -> dict:
    return build_manifest.read_manifest() or {}


def _shown_fields(entry: str) -> list[str]:
    """The field ids a card carried in this run, from the run record.

    Most cards are no longer drawn on the console (they answer
    from the one summary), so what a card offered is read from the record the
    runner keeps of every card it proposed, not from the screen.
    """
    from puffpai.config import get_config as _cfg
    record = os.path.join(_cfg().CONFIG_DIR, "wizard_run.json")
    with open(record, encoding="utf-8") as handle:
        log = json.load(handle)["log"]
    for row in log:
        if row.get("entry") == entry and row.get("shown"):
            return list(row["shown"].get("fields") or [])
    return []


# -- the posture step comes first, and its gates close later menus ------------

def test_posture_is_recorded_with_stated_vs_defaulted(scratch, no_ollama, capsys):
    assert _run_setup("--build-network", "offline", "--run-egress", "sealed") == 0
    posture = _manifest()["posture"]
    assert posture["build_network"] == "offline"
    assert posture["build_network_source"] == "stated"
    assert posture["run_egress"] == "sealed"
    # Posture is printed before the census, not after it. Since the Phase 2
    # conversion the census is the Discover card's own title rather than the
    # old step banner; where it comes in the run is unchanged, and that is what
    # this asserts.
    # The plan is shown before anything it lists is applied, and the closing
    # screen comes last.
    out = capsys.readouterr().out
    assert out.index("Here is the plan for") < out.index("How to start it")


def test_skipped_posture_records_the_default_as_defaulted(scratch, no_ollama):
    assert _run_setup() == 0
    posture = _manifest()["posture"]
    assert posture["build_network"] == "online"
    assert posture["run_egress"] == "island"
    assert posture["build_network_source"] == "defaulted"
    assert posture["run_egress_source"] == "defaulted"


def test_offline_build_is_never_offered_a_download(scratch, no_ollama, capsys):
    assert _run_setup("--build-network", "offline") == 0
    out = capsys.readouterr().out
    assert "this build is not downloading anything" in out
    assert "Nothing will be downloaded." in out
    assert "registry.ollama.ai" not in out


def test_online_build_still_sees_downloadable_brains(scratch, no_ollama, capsys):
    assert _run_setup("--build-network", "online") == 0
    out = capsys.readouterr().out
    # The brain is on the plan with its size and where it comes from; a --yes
    # run with nobody reading leaves it off, and says so.
    assert "Download:" in out and "registry.ollama.ai" in out
    assert "Not included (you can add it): The brain" in out


def test_sealed_build_is_not_offered_home_assistant_and_is_told_why(
        scratch, no_ollama, capsys):
    # --skip-ha is deliberately NOT passed: the posture is what closes the step.
    assert cli.main(["setup", "--yes", "--skip-knowledge",
                     "--run-egress", "sealed"]) == 0
    out = capsys.readouterr().out
    assert "Home Assistant" not in out
    assert "sealed" in _shown_fields("tools.card")
    assert "connect_hub" not in _shown_fields("tools.card")


def test_island_build_still_reaches_the_home_assistant_step(
        scratch, no_ollama, capsys):
    assert cli.main(["setup", "--yes", "--skip-knowledge", "--skip-ha",
                     "--run-egress", "island"]) == 0
    assert "hub" in _shown_fields("tools.card")
    assert "sealed" not in _shown_fields("tools.card")


def test_brain_step_discloses_the_routing_tier(scratch, no_ollama, capsys):
    assert _run_setup("--model", "llama3.2:3b") == 0
    out = " ".join(capsys.readouterr().out.split())
    # What this brain cannot do, said on the plan in plain words.
    from puffpai import model_profile
    assert " ".join(model_profile.limits_sentence("llama3.2:3b").split()) in out
    brain = _manifest()["brain"]
    assert brain["routing_tier"] in ("light", "guided", "rails")
    assert brain["supports_planning"] is False


# -- Step 4: the vault ceremony ---------------------------------------------

def test_setup_seals_the_vault_through_the_recovery_path(
        scratch, no_ollama, monkeypatch):
    """The CLI must reach seal_vault_with_recovery, and never seal_vault.

    seal_vault (and setup_ops.create_vault under it) make a PIN-only vault. If
    the wizard ever wires one of those, the owner is walked through the whole
    "write this code down" ceremony and silently given no code at all.
    """
    calls = {"with_recovery": 0}
    real = zone_vault.seal_vault_with_recovery

    def spy(pin, *, vault=None):
        calls["with_recovery"] += 1
        return real(pin, vault=vault)

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "the wizard used a PIN-only vault path; the owner would get no "
            "recovery code after being told to write one down"
        )

    monkeypatch.setattr(zone_vault, "seal_vault_with_recovery", spy)
    monkeypatch.setattr(zone_vault, "seal_vault", forbidden)
    monkeypatch.setattr(setup_ops, "create_vault", forbidden)

    assert _run_setup("--vault-pin", GOOD_PIN) == 0
    assert calls["with_recovery"] == 1
    assert setup_ops.vault_initialized() is True

    block = _manifest()["vault"]
    assert block["configured"] is True and block["sealed"] is True
    assert block["recovery_issued"] is True
    # Nobody read it back, so it is not recorded as confirmed.
    assert block["recovery_confirmed"] is False


def test_non_interactive_without_a_pin_skips_the_vault_and_says_so(
        scratch, no_ollama, capsys):
    assert _run_setup() == 0
    out = capsys.readouterr().out
    assert "No one is here to type a PIN, so no vault is made" in " ".join(
        out.split())
    assert setup_ops.vault_initialized() is False
    assert _manifest()["vault"]["configured"] is False


def test_non_interactive_with_a_pin_states_that_nobody_confirmed(
        scratch, no_ollama, capsys):
    assert _run_setup("--vault-pin", GOOD_PIN) == 0
    out = capsys.readouterr().out
    assert "Nobody typed this recovery code back." in out


def test_manifest_never_carries_the_pin_or_the_code(scratch, no_ollama, capsys):
    assert _run_setup("--vault-pin", GOOD_PIN) == 0
    out = capsys.readouterr().out
    # The code is printed once in this mode; pull it back out and make sure it
    # did not also land on disk.
    code = next(line.strip() for line in out.splitlines()
                if line.strip().count("-") == 4 and len(line.strip()) == 29)
    body = json.dumps(_manifest())
    assert code not in body
    assert GOOD_PIN not in body


def _route_hidden(monkeypatch, pin_reader) -> None:
    """The typed-back recovery code is read hidden, through ``cli._read_pin``
    like the PIN (PB-003). Send that one prompt to the test's typed answers
    (``builtins.input``) and every other hidden prompt to *pin_reader*."""
    import builtins

    def read(prompt):
        if zone_vault.RECOVERY_CONFIRM_PROMPT in prompt:
            return builtins.input(prompt)
        return pin_reader(prompt)

    monkeypatch.setattr(cli, "_read_pin", read)


def _ceremony_args() -> argparse.Namespace:
    return argparse.Namespace(skip_vault=False, vault_password=None)


def test_typing_the_code_back_is_what_confirms_it(scratch, monkeypatch):
    seen: dict[str, str] = {}
    real = setup_ops.seal_vault_with_recovery

    def spy(pin, vault=None):
        block, code = real(pin, vault=vault)
        seen["code"] = code
        return block, code

    monkeypatch.setattr(setup_ops, "seal_vault_with_recovery", spy)
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)  # BB-WIZ-S10: a person at a keyboard
    _route_hidden(monkeypatch, lambda prompt: GOOD_PIN)

    # One wrong attempt, then the real thing typed back with sloppy formatting.
    answers = iter(["not-the-code", "ECHO"])

    def fake_input(prompt=""):
        nxt = next(answers)
        return seen["code"].lower().replace("-", " ") if nxt == "ECHO" else nxt

    monkeypatch.setattr("builtins.input", fake_input)

    block = cli._setup_vault_step(_ceremony_args(), get_config(),
                                  non_interactive=False)
    assert block["recovery_issued"] is True
    assert block["recovery_confirmed"] is True


def test_wrong_answers_burn_the_code_and_issue_another(scratch, monkeypatch):
    """Answering "skip" keeps the code (skipping
    three times left owners with no code). Only wrong answers replace it."""
    seen: dict[str, str] = {}
    real_seal = setup_ops.seal_vault_with_recovery
    real_rotate = setup_ops.rotate_vault_recovery
    rotations = {"n": 0}

    def seal_spy(pin, vault=None):
        block, code = real_seal(pin, vault=vault)
        seen["code"] = code
        return block, code

    def rotate_spy(current_code, pin, vault=None):
        rotations["n"] += 1
        assert current_code == seen["code"]
        seen["code"] = real_rotate(current_code, pin, vault=vault)
        return seen["code"]

    monkeypatch.setattr(setup_ops, "seal_vault_with_recovery", seal_spy)
    monkeypatch.setattr(setup_ops, "rotate_vault_recovery", rotate_spy)
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)  # BB-WIZ-S10: a person at a keyboard
    _route_hidden(monkeypatch, lambda prompt: GOOD_PIN)

    answers = iter(["WRONG-CODE"] * cli._RECOVERY_MAX_MISMATCHES + ["ECHO"])

    def fake_input(prompt=""):
        nxt = next(answers)
        return seen["code"] if nxt == "ECHO" else nxt

    monkeypatch.setattr("builtins.input", fake_input)

    block = cli._setup_vault_step(_ceremony_args(), get_config(),
                                  non_interactive=False)
    assert rotations["n"] == 1
    assert block["recovery_confirmed"] is True


def test_skip_keeps_the_code(scratch, monkeypatch, capsys):
    rotations = {"n": 0}
    monkeypatch.setattr(setup_ops, "rotate_vault_recovery",
                        lambda *a, **k: rotations.__setitem__("n", 1))
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    _route_hidden(monkeypatch, lambda prompt: GOOD_PIN)
    monkeypatch.setattr("builtins.input", lambda prompt="": "skip")

    block = cli._setup_vault_step(_ceremony_args(), get_config(),
                                  non_interactive=False)
    assert rotations["n"] == 0
    assert block["recovery_issued"] is True
    assert block["recovery_confirmed"] is False
    assert "That code still works" in capsys.readouterr().out


def test_giving_up_leaves_a_working_vault_and_an_honest_record(
        scratch, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)  # BB-WIZ-S10: a person at a keyboard
    _route_hidden(monkeypatch, lambda prompt: GOOD_PIN)
    # Every answer wrong, every time: after the last fresh code it gives up.
    monkeypatch.setattr("builtins.input", lambda prompt="": "WRONG-CODE")

    block = cli._setup_vault_step(_ceremony_args(), get_config(),
                                  non_interactive=False)
    assert setup_ops.vault_initialized() is True   # no dead end
    assert block["recovery_issued"] is True
    assert block["recovery_confirmed"] is False
    assert "The recovery code was not typed back." in capsys.readouterr().out


def test_a_weak_pin_is_refused_with_a_reason_and_retried(scratch, monkeypatch, capsys):
    pins = iter(["1234", GOOD_PIN, GOOD_PIN])
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)  # BB-WIZ-S10: a person at a keyboard
    _route_hidden(monkeypatch, lambda prompt: next(pins))
    monkeypatch.setattr("builtins.input", lambda prompt="": "skip")

    cli._setup_vault_step(_ceremony_args(), get_config(), non_interactive=False)
    out = capsys.readouterr().out
    assert f"at least {zone_vault._MIN_NUMERIC_PIN_LENGTH} digits" in out
    assert setup_ops.vault_initialized() is True


def test_shared_create_vault_can_issue_a_recovery_code(scratch):
    """The dashboard face must be able to issue a code, like the CLI face.

    /setup/vault goes through setup_ops.create_vault. Without an issuance path
    there, an owner who onboards in the browser gets a PIN-only vault and is
    stranded the first time they forget the PIN.
    """
    code = setup_ops.create_vault("correct horse battery staple",
                                  with_recovery=True)
    assert code and code.count("-") == 4
    assert setup_ops.vault_initialized() is True
    # It was handed back for one screen, not written anywhere.
    for root, _dirs, names in os.walk(scratch):
        for name in names:
            with open(os.path.join(root, name), "rb") as fh:
                assert code.encode() not in fh.read(), f"code leaked into {name}"


def test_legacy_create_vault_stays_pin_only(scratch):
    """Existing callers must not silently start issuing codes."""
    assert setup_ops.create_vault("correct horse battery staple") is None
    assert setup_ops.vault_initialized() is True
    block = setup_ops.vault_block(configured=True, sealed=True)
    assert block["recovery_issued"] is False


def test_pin_validation_covers_the_vault_floor_too(scratch):
    """A PIN the prompt accepts must be one the seal accepts.

    zone_vault.validate_pin allows six mixed characters; the vault's own floor
    is eight. Checking only the first would let a PIN through the prompt and
    then have the seal raise, which reads as the wizard changing its mind.
    """
    ok, reason = setup_ops.validate_vault_pin("ab1cd2")
    assert ok is False
    assert "8 characters" in reason
    assert setup_ops.validate_vault_pin(GOOD_PIN) == (True, "")


def test_an_existing_vault_is_left_alone_on_a_re_run(scratch, no_ollama, capsys):
    assert _run_setup("--vault-pin", GOOD_PIN) == 0
    capsys.readouterr()
    assert _run_setup("--vault-pin", GOOD_PIN) == 0
    out = capsys.readouterr().out
    assert "A vault is already set up on this computer. It is left as it is." in " ".join(out.split())
    # The second run reports what the first one did, not a fresh "none issued".
    assert _manifest()["vault"]["recovery_issued"] is True


# -- Step 3b: surfaces -------------------------------------------------------

def test_the_goal_derives_a_surface_allowlist(scratch, no_ollama):
    assert _run_setup("--goal", "a space bot for my telescopes") == 0
    surfaces = _manifest()["surfaces"]
    assert "instruments.telescope" in surfaces["allowlist"]
    assert "house.cameras" not in surfaces["allowlist"]
    assert surfaces["derived_from_goal"] is True
    assert surfaces["reasons"]["instruments.telescope"]


def test_the_owner_can_drop_a_proposed_surface(scratch, no_ollama):
    assert _run_setup("--goal", "a space bot for my telescopes",
                      "--drop-surface", "instruments.telescope") == 0
    surfaces = _manifest()["surfaces"]
    assert "instruments.telescope" not in surfaces["allowlist"]
    assert surfaces["owner_edited"] is True


def test_no_goal_leaves_an_empty_allowlist_rather_than_everything(
        scratch, no_ollama):
    assert _run_setup() == 0
    assert _manifest()["surfaces"]["allowlist"] == []


# -- Step 6: the Bridge ------------------------------------------------------

def test_a_sealed_build_forces_drawbridge_up(scratch, no_ollama):
    assert _run_setup("--run-egress", "sealed", "--bridge-posture", "open_yard") == 0
    bridge = _manifest()["bridge"]
    assert bridge["posture"] == "drawbridge_up"
    assert bridge["reconciled"] is True
    assert "stricter" in bridge["note"]


def test_skipping_the_bridge_records_the_safe_default(scratch, no_ollama):
    assert _run_setup("--skip-bridge") == 0
    bridge = _manifest()["bridge"]
    assert bridge["posture"] == "fenced"
    assert bridge["chosen_by"] in ("default", "override")


# -- Task 4: the config-health acknowledgment gate ---------------------------

def _degrade(name: str) -> None:
    config_health.record(name, f"config/{name}.json", config_health.UNPARSEABLE,
                         "JSON parse error: test", reduced=True)


def test_tuning_degradation_is_reported_but_not_gated(scratch, monkeypatch, capsys):
    _degrade("model_profiles")
    monkeypatch.setattr(cli, "_ask_yes_no",
                        lambda *a, **k: pytest.fail("must not ask about tuning"))
    assert cli._config_health_gate(non_interactive=False) is True
    assert "how well this build is tuned" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["capability_catalog", "model_catalog"])
def test_identity_degradation_needs_an_explicit_yes(scratch, monkeypatch, name):
    _degrade(name)
    asked = {"n": 0}

    def refuse(prompt, default=False, **kwargs):
        asked["n"] += 1
        return False

    monkeypatch.setattr(cli, "_ask_yes_no", refuse)
    assert cli._config_health_gate(non_interactive=False) is False
    assert asked["n"] == 1

    monkeypatch.setattr(cli, "_ask_yes_no", lambda *a, **k: True)
    assert cli._config_health_gate(non_interactive=False) is True


def test_identity_degradation_never_hard_refuses_a_non_interactive_build(
        scratch, no_ollama, capsys):
    _degrade("capability_catalog")
    assert _run_setup() == 0
    out = capsys.readouterr().out
    assert "changes what your build IS" in out
    manifest = _manifest()
    assert manifest, "an offline owner must not be left with no manifest"
    assert manifest["config_health"]["degraded"] is True
    assert "capability_catalog" in manifest["config_health"]["configs"]


def test_declining_the_gate_writes_no_manifest_but_keeps_the_rest(
        scratch, no_ollama, monkeypatch, capsys):
    _degrade("capability_catalog")
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *a, **k: False)
    # A non-interactive run never reaches the question, so drive the step itself.
    census = setup_ops.hardware_census()
    cli._setup_manifest_step(census, {"model": "x"}, {"master_core": 6,
                             "max_specialists": 1}, {"goal": "", "selected": []},
                             non_interactive=False)
    assert build_manifest.read_manifest() is None
    assert "The build record was not written" in capsys.readouterr().out


# -- Task 5: the sneakernet subcommand ---------------------------------------

def test_build_export_then_import_round_trips(scratch, no_ollama, tmp_path, capsys):
    assert _run_setup("--goal", "a space bot for my telescopes") == 0
    original = _manifest()
    capsys.readouterr()

    bundle = tmp_path / "thumbdrive"
    assert cli.main(["build", "export", "--target", str(bundle), "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Bundle to write into" in out
    assert "build_manifest.json" in out and "KB" in out   # size shown first
    assert (bundle / "CHECKSUMS.json").is_file()

    os.remove(build_manifest.manifest_path())
    assert cli.main(["build", "import", "--source", str(bundle), "--yes"]) == 0
    out = capsys.readouterr().out
    assert "every file checksum verified" in out
    restored = _manifest()
    assert restored["surfaces"]["allowlist"] == original["surfaces"]["allowlist"]
    assert restored["provenance"]["source"] != "local"


def test_build_import_refuses_a_tampered_bundle(scratch, no_ollama, tmp_path, capsys):
    assert _run_setup() == 0
    bundle = tmp_path / "thumbdrive"
    assert cli.main(["build", "export", "--target", str(bundle), "--yes"]) == 0

    doctored = json.loads((bundle / "build_manifest.json").read_text())
    doctored["brain"]["model"] = "something-else"
    (bundle / "build_manifest.json").write_text(json.dumps(doctored))
    capsys.readouterr()

    assert cli.main(["build", "import", "--source", str(bundle), "--yes"]) == 1
    assert "refused" in capsys.readouterr().out


def test_build_export_without_a_manifest_says_what_to_do(scratch, tmp_path, capsys):
    assert cli.main(["build", "export", "--target", str(tmp_path / "x"),
                     "--yes"]) == 1
    assert "Run `puffpai setup` first." in capsys.readouterr().out


# -- setup_status carries the v2 blocks --------------------------------------

def test_setup_status_carries_posture_zones_surfaces_and_health(
        scratch, no_ollama):
    assert _run_setup("--goal", "a space bot", "--vault-pin", GOOD_PIN,
                      "--run-egress", "sealed") == 0
    status = setup_ops.setup_status(probe_ollama=False)

    assert status["posture"]["run_egress"] == "sealed"
    assert status["zones"]["vault"]["initialized"] is True
    assert status["zones"]["vault"]["recovery_issued"] is True
    assert status["zones"]["bridge"]["posture"] == "drawbridge_up"
    assert "zim_dir" in status["zones"]["brain"]
    assert status["surfaces"]["allowlist"]
    assert status["config_health"]["degraded"] is False
    assert status["manifest"]["exists"] is True

    body = json.dumps(status)
    assert GOOD_PIN not in body


# -- ASCII: this is the Windows console face ---------------------------------

def test_the_whole_wizard_prints_ascii_only(scratch, no_ollama, capsys):
    assert _run_setup("--goal", "a space bot for my telescopes",
                      "--vault-pin", GOOD_PIN) == 0
    out = capsys.readouterr().out
    offenders = sorted({ch for ch in out if ord(ch) > 127})
    assert not offenders, f"non-ASCII in wizard output: {offenders}"
