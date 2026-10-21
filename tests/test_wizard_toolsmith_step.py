"""The wizard's "Who should do Toolsmith's installs?" question.

Asked once, after the GitHub question and before the build, beside ``next``
like GitHub (the flow's order is unchanged). Answering records only the
choice word; the setting and the key go through /toolsmith/model into the
vault. The console asks the same question before its build.

Run:
    pytest tests/test_wizard_toolsmith_step.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keys_support import PIN, grep_tree  # noqa: E402
from test_toolsmith_cloud import AUTH, DEV, KEY, cloud  # noqa: E402,F401 - the fixture
from test_wizard_needs_step import install_fakes, pinned_machine  # noqa: E402


@pytest.fixture
def wizard(monkeypatch, tmp_path):
    from puffpai.wizard import service
    monkeypatch.setattr(service, "finding_engine", lambda: None)
    pinned_machine(monkeypatch, tmp_path)
    install_fakes(monkeypatch, "garden helper")
    return service


def _to_after_github(service) -> None:
    service.first_look_choose()
    service.apply_skill("purpose:goal", {"goal": "Help me water the garden."})
    service.github_choose("skipped")


def test_the_step_after_github_and_skip_keeps_local(wizard, cloud):
    from puffpai.security import toolsmith_cloud as tc
    service = wizard
    assert service.flow()["toolsmith"] == {"asked": False}
    _to_after_github(service)
    flow = service.flow()
    assert flow["github"]["asked"] is True and flow["toolsmith"]["asked"] is False
    assert flow["next"] == "identity", "the question never takes a step of its own"
    r = cloud.client.post("/wizard/toolsmith", headers=DEV, json={"choice": "skipped"})
    assert r.status_code == 200 and r.get_json()["success"] is True
    flow = service.flow()
    assert flow["toolsmith"]["asked"] is True and flow["next"] == "identity"
    assert tc.read_setting()["where"] == "local" and not tc.in_use()
    log = [row for row in service.run_state()["log"] if row.get("part") == "toolsmith_model"]
    assert len(log) == 1 and "skipped" in log[0]["note"]


@pytest.mark.parametrize("bad", [None, "", "yes", KEY, 1, {"choice": "local"}])
def test_anything_else_is_refused(wizard, bad):
    from puffpai.wizard import cards
    with pytest.raises(cards.CardError):
        wizard.toolsmith_choose(bad)
    assert wizard.flow()["toolsmith"]["asked"] is False


def test_anthropic_with_a_key_goes_through_the_vault_route(wizard, cloud, caplog):
    from puffpai.security import keys
    from puffpai.security import toolsmith_cloud as tc
    caplog.set_level("DEBUG")
    service = wizard
    _to_after_github(service)
    r = cloud.client.post("/toolsmith/model", headers=DEV,
                          json={"where": "cloud", "model": "claude-opus-5-5", "key": KEY})
    assert r.status_code == 200 and KEY not in r.get_data(as_text=True)
    assert cloud.client.post("/wizard/toolsmith", headers=DEV,
                             json={"choice": "cloud"}).status_code == 200
    st = cloud.client.get("/toolsmith/model", headers=DEV).get_json()
    assert st["where"] == "cloud" and st["line"] == "Toolsmith's model: Anthropic Opus 5.5"
    assert keys._record_for(cloud.vault, "anthropic")["value"] == KEY  # noqa: SLF001
    assert service.flow()["toolsmith"]["asked"] is True
    from puffpai.config import get_state_dir
    assert grep_tree(get_state_dir(), KEY) == []       # wizard state files included
    assert KEY not in json.dumps(service.run_state())
    assert KEY not in caplog.text
    assert tc.in_use()


def _console(monkeypatch, answers: list, hidden: list):
    from puffpai import cli
    monkeypatch.setattr(cli, "_ask", lambda prompt, default="", **k: answers.pop(0))
    monkeypatch.setattr(cli, "_read_pin", lambda prompt: hidden.pop(0))
    return cli


def test_the_console_stores_the_key_through_the_same_function(wizard, cloud, monkeypatch,
                                                              capsys, caplog):
    from puffpai.security import keys, vault as vault_mod
    from puffpai.security import toolsmith_cloud as tc
    caplog.set_level("DEBUG")
    cloud.vault.lock()
    monkeypatch.setattr(vault_mod, "Vault", lambda **k: cloud.vault)
    seen = []
    real = tc.choose_cloud
    monkeypatch.setattr(tc, "choose_cloud", lambda *a, **k: seen.append(1) or real(*a, **k))
    cli = _console(monkeypatch, ["2", "2"], [KEY, PIN])
    ctx = types.SimpleNamespace(shared={}, non_interactive=False)
    assert cli._setup_toolsmith_model_step(ctx) == "cloud"   # noqa: SLF001
    out = capsys.readouterr().out
    assert cli.TOOLSMITH_QUESTION in out and cli.TOOLSMITH_KEY_LINE in out
    assert tc.CONSENT_TEXT.split()[0] in out
    assert KEY not in out and KEY not in caplog.text
    assert seen == [1] and tc.read_setting()["model"] == "claude-sonnet-5-5"
    assert ctx.shared == {"toolsmith_asked": "cloud"}
    cloud.vault.unlock(PIN)
    assert keys._record_for(cloud.vault, "anthropic")["value"] == KEY  # noqa: SLF001
    # Asked once.
    assert cli._setup_toolsmith_model_step(ctx) == "local"   # noqa: SLF001


def _console_after_the_plan(wizard, cloud, monkeypatch):
    """Where the terminal asks (PB-001): goal saved, plan approved, vault
    made, but the run setting not recorded yet (the manifest comes later)."""
    from puffpai import github_account, outside_services
    from puffpai.security import vault as vault_mod
    _no_setup_yet()
    _to_after_github(wizard)
    monkeypatch.setattr(wizard, "_summary_state", lambda run: {"approved": True})
    assert wizard.setup_in_progress() is False                 # the plan is approved
    assert github_account.before_setup() is False
    assert not outside_services._has_posture_record()          # noqa: SLF001
    cloud.vault.lock()
    monkeypatch.setattr(vault_mod, "Vault", lambda **k: cloud.vault)


def test_pb001_the_console_saves_the_key_after_the_plan_before_the_run_setting(
        wizard, cloud, monkeypatch, capsys, caplog):
    """PB-001 (outside test, Jetson over SSH): the terminal asked for the key
    after the plan and then dropped it ("can be saved once you approve your
    plan"). Now it is locked in the vault unchecked, nothing is sent, and
    her yes is recorded as in the browser."""
    from puffpai.config import get_state_dir
    from puffpai.security import keys
    from puffpai.security import toolsmith_cloud as tc
    caplog.set_level("DEBUG")
    _console_after_the_plan(wizard, cloud, monkeypatch)
    cli = _console(monkeypatch, ["2", "1"], [KEY, PIN])
    ctx = types.SimpleNamespace(shared={}, non_interactive=False)
    assert cli._setup_toolsmith_model_step(ctx) == "cloud"   # noqa: SLF001
    out = capsys.readouterr().out
    assert keys.key_status("anthropic")["state"] != "missing"
    assert cloud.calls == [] and cloud.posts == []            # nothing went to Anthropic
    assert tc.in_use() and not tc.held() and not tc.wished_cloud()
    assert tc.read_setting()["consent"]["text"] == tc._sha(tc.CONSENT_TEXT)[:16]  # noqa: SLF001
    cloud.vault.unlock(PIN)
    assert keys._record_for(cloud.vault, "anthropic")["value"] == KEY  # noqa: SLF001
    assert KEY not in out and KEY not in caplog.text
    assert grep_tree(get_state_dir(), KEY) == []
    assert ctx.shared == {"toolsmith_asked": "cloud"}
    assert tc.BEFORE_SETUP_LINE not in out


def test_pb001_a_malformed_key_is_refused_and_nothing_is_kept(wizard, cloud, monkeypatch,
                                                              capsys):
    from puffpai.security import keys
    from puffpai.security import toolsmith_cloud as tc
    _console_after_the_plan(wizard, cloud, monkeypatch)
    bad = "not-a-key-" + "Z" * 30
    cli = _console(monkeypatch, ["2", "1"], [bad])
    ctx = types.SimpleNamespace(shared={}, non_interactive=False)
    assert cli._setup_toolsmith_model_step(ctx) == "local"   # noqa: SLF001
    out = capsys.readouterr().out
    assert bad not in out
    assert keys.key_status("anthropic")["state"] == "missing"
    assert not tc.in_use() and cloud.calls == []


def _held_not_saved(cli, out: str) -> None:
    from puffpai.security import keys
    from puffpai.security import toolsmith_cloud as tc
    assert KEY not in out and cli.TOOLSMITH_HOLD_LINE in out
    assert "uses this computer for now" not in out
    assert tc.wished_cloud() and tc.held() and not tc.in_use()
    assert tc.status()["hold_line"] == tc.HOLD_LINE
    assert keys.key_status("anthropic")["state"] == "missing"


def test_pb001_a_wrong_pin_holds_the_build_instead_of_going_local(wizard, cloud,
                                                                  monkeypatch, capsys):
    """Owner's 10-14 rule: she picked Anthropic, so no quiet local build."""
    _console_after_the_plan(wizard, cloud, monkeypatch)
    cli = _console(monkeypatch, ["2", "1"], [KEY, "not the pin at all"])
    ctx = types.SimpleNamespace(shared={}, non_interactive=False)
    assert cli._setup_toolsmith_model_step(ctx) == "local"   # noqa: SLF001
    out = capsys.readouterr().out
    assert "That PIN did not open the vault. Nothing was sent." in out
    _held_not_saved(cli, out)
    assert cloud.calls == []


def test_pb001_a_refused_save_holds_the_build_instead_of_going_local(wizard, cloud,
                                                                     monkeypatch, capsys):
    from puffpai.security import keys
    _console_after_the_plan(wizard, cloud, monkeypatch)
    monkeypatch.setattr(keys, "_rate_limited", lambda: True)   # add_key refuses: busy
    cli = _console(monkeypatch, ["2", "1"], [KEY, PIN])
    ctx = types.SimpleNamespace(shared={}, non_interactive=False)
    assert cli._setup_toolsmith_model_step(ctx) == "local"   # noqa: SLF001
    out = capsys.readouterr().out
    assert "Wait a minute and try again." in out
    _held_not_saved(cli, out)
    assert not cloud.vault.is_unlocked and cloud.calls == []


def test_pb001_when_the_key_cannot_be_saved_it_is_not_asked_for(wizard, cloud,
                                                                monkeypatch, capsys):
    """A bot set up before (its sticky note) with no run setting now: saving
    is refused, so the key is never asked for; her wish holds the build."""
    from puffpai import github_account
    from puffpai.security import toolsmith_cloud as tc
    _console_after_the_plan(wizard, cloud, monkeypatch)
    github_account._write("setup_seen.json", {"at": 1})       # noqa: SLF001
    asked = []
    cli = _console(monkeypatch, ["2", "1"], [])
    monkeypatch.setattr(cli, "_read_pin", lambda prompt: asked.append(prompt) or KEY)
    ctx = types.SimpleNamespace(shared={}, non_interactive=False)
    assert cli._setup_toolsmith_model_step(ctx) == "local"   # noqa: SLF001
    out = capsys.readouterr().out
    assert asked == [] and cli.TOOLSMITH_KEY_LINE not in out
    assert "Before Toolsmith's first install" in out
    assert tc.wished_cloud() and tc.held() and not tc.in_use()


def test_pb001_without_a_vault_the_key_is_not_asked_for(wizard, cloud, monkeypatch, capsys):
    from puffpai.security import toolsmith_cloud as tc
    _console_after_the_plan(wizard, cloud, monkeypatch)
    from puffpai.security import vault as vault_mod
    monkeypatch.setattr(vault_mod, "Vault",
                        lambda **k: types.SimpleNamespace(is_initialized=False))
    asked = []
    cli = _console(monkeypatch, ["2", "1"], [])
    monkeypatch.setattr(cli, "_read_pin", lambda prompt: asked.append(prompt) or KEY)
    ctx = types.SimpleNamespace(shared={}, non_interactive=False)
    assert cli._setup_toolsmith_model_step(ctx) == "local"   # noqa: SLF001
    out = capsys.readouterr().out
    assert asked == [] and "no vault yet" in out
    assert tc.wished_cloud() and tc.held() and not tc.in_use()


def test_pb001_only_the_terminal_wizard_says_it_is_the_wizard_run():
    """``wizard_run=True`` lets a key be kept before the run setting; no
    route may pass it (a request body never chooses it)."""
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "puffpai"
    users = sorted(str(p.relative_to(src)).replace("\\", "/")
                   for p in src.rglob("*.py")
                   if "wizard_run=True" in p.read_text(encoding="utf-8"))
    assert users == ["cli.py"]


def test_the_console_default_and_yes_keep_local(wizard, cloud, monkeypatch, capsys):
    from puffpai.security import toolsmith_cloud as tc
    cli = _console(monkeypatch, [""], [])
    ctx = types.SimpleNamespace(shared={}, non_interactive=False)
    assert cli._setup_toolsmith_model_step(ctx) == "local"   # noqa: SLF001
    assert ctx.shared == {"toolsmith_asked": "local"}
    quiet = types.SimpleNamespace(shared={}, non_interactive=True)
    assert cli._setup_toolsmith_model_step(quiet) == "local"   # noqa: SLF001
    assert quiet.shared == {} and not tc.in_use()


def test_the_dashboard_step_uses_the_existing_routes():
    root = pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "app"
    step = (root / "onboarding" / "ToolsmithStep.tsx").read_text(encoding="utf-8")
    assert "Who should do Toolsmith's installs?" in step
    assert "ToolsmithModelCard" in step and "chooseToolsmith" in step
    assert "—" not in step
    gate = (root / "_components" / "OnboardingGate.tsx").read_text(encoding="utf-8")
    assert "/dashboard/settings" in gate and "Back to setup" in gate


def test_a_failed_save_never_marks_the_step_done_and_keeps_her_wish(wizard, cloud):
    """Owner's test 2026-10-14: the key did not save, yet the step read done
    and the build ran on this computer. Now: not done, her wish kept, and the
    step is done only once Toolsmith's setting really says Anthropic."""
    from puffpai.security import toolsmith_cloud as tc
    service = wizard
    _to_after_github(service)
    cloud.vault.lock()
    ask = {"where": "cloud", "model": "claude-opus-5-5", "key": KEY}
    assert cloud.client.post("/toolsmith/model", headers=DEV, json=ask).status_code == 423
    r = cloud.client.post("/wizard/toolsmith", headers=DEV, json={"choice": "cloud"})
    body = r.get_json()
    assert r.status_code == 409 and body["code"] == "not_saved" and body["success"] is False
    assert service.flow()["toolsmith"]["asked"] is False
    assert tc.wished_cloud() and tc.held()
    assert tc.status()["hold_line"] == tc.HOLD_LINE
    # Her PIN, the retry, then the step.
    assert cloud.client.post("/vault/unlock", headers=AUTH, json={"pin": PIN}).status_code == 200
    assert cloud.client.post("/toolsmith/model", headers=DEV, json=ask).status_code == 200
    assert not tc.held() and not tc.wished_cloud()
    r = cloud.client.post("/wizard/toolsmith", headers=DEV, json={"choice": "cloud"})
    assert r.status_code == 200 and service.flow()["toolsmith"]["asked"] is True


def test_this_computer_or_skip_clears_the_wish(wizard, cloud):
    from puffpai.security import toolsmith_cloud as tc
    _to_after_github(wizard)
    assert cloud.client.post("/wizard/toolsmith", headers=DEV,
                             json={"choice": "cloud"}).status_code == 409
    assert tc.held()
    assert cloud.client.post("/wizard/toolsmith", headers=DEV,
                             json={"choice": "local"}).status_code == 200
    assert not tc.wished_cloud() and wizard.flow()["toolsmith"]["asked"] is True


def test_this_computer_in_the_wizard_ends_a_yes_to_the_old_sentence(wizard, cloud):
    """Her yes was to an earlier consent sentence: status says local, so the
    page never calls "back to this computer" itself. Her answer still ends
    the old record, and with it the build's hold."""
    from test_toolsmith_cloud import _old_yes, _turn_on
    from puffpai.security import toolsmith_cloud as tc
    _to_after_github(wizard)
    _turn_on(cloud)
    _old_yes()
    assert tc.consent_outdated() and tc.held()
    assert cloud.client.post("/wizard/toolsmith", headers=DEV,
                             json={"choice": "local"}).status_code == 200
    assert not tc.consent_outdated() and not tc.held() and not tc.in_use()
    assert tc.status()["line"] == "Toolsmith's model: on this computer"


def test_skip_and_anthropic_keep_the_hold_on_a_yes_to_the_old_sentence(wizard, cloud):
    """Only her own pick of This computer ends the old yes. Skip leaves the
    build held, and Anthropic says to agree again, not that the key is
    missing (it is in her vault)."""
    from test_toolsmith_cloud import _old_yes, _turn_on
    from puffpai.security import toolsmith_cloud as tc
    _to_after_github(wizard)
    _turn_on(cloud)
    _old_yes()
    assert cloud.client.post("/wizard/toolsmith", headers=DEV,
                             json={"choice": "skipped"}).status_code == 200
    assert tc.consent_outdated() and tc.held() and not tc.in_use()
    r = cloud.client.post("/wizard/toolsmith", headers=DEV, json={"choice": "cloud"})
    assert r.status_code == 409
    assert "reads differently" in r.get_data(as_text=True)
    assert "not saved" not in r.get_data(as_text=True)
    assert tc.consent_outdated() and tc.held() and not tc.in_use()


def test_the_settings_card_opens_on_the_new_sentence_after_a_change():
    root = pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "app"
    card = (root / "dashboard" / "settings" / "ToolsmithModelCard.tsx").read_text(
        encoding="utf-8")
    assert 'r.data.consent_again ? "cloud"' in card
    assert "st?.consent_again ? void toLocal()" in card


def test_the_cards_show_the_pin_box_and_every_refusal():
    root = pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "app"
    card = (root / "dashboard" / "settings" / "ToolsmithModelCard.tsx").read_text(
        encoding="utf-8")
    assert "vault_locked" in card and "/vault/unlock" in card and "heldKey.current = \"\"" in card
    assert "Your vault is locked. Type your vault PIN to save the key." in card
    step = (root / "onboarding" / "ToolsmithStep.tsx").read_text(encoding="utf-8")
    assert "onRefused" in step
    for text in (card, step):
        assert "—" not in text


def _no_setup_yet():
    from puffpai.config import get_config
    from puffpai.security import egress_doors
    path = os.path.join(get_config().CONFIG_DIR, "build_manifest.json")
    if os.path.exists(path):
        os.unlink(path)
    egress_doors.reset_cache()


@pytest.mark.parametrize("github", ["skipped", "connected"])
def test_the_key_saves_in_the_wizard_before_the_plan_and_the_first_run_uses_it(
        wizard, cloud, github):
    """Owner, 2026-10-14: set Toolsmith's model and key IN the wizard. Saving
    sends nothing to Anthropic; the switch is recorded as hers; the plan's
    run setting then opens it and the first run is on the cloud model."""
    from keys_support import set_posture
    from puffpai import outside_services
    from puffpai.build import toolsmith_run as T
    from puffpai.security import egress_doors, keys
    from puffpai.security import toolsmith_cloud as tc
    _no_setup_yet()
    service = wizard
    service.first_look_choose()
    service.apply_skill("purpose:goal", {"goal": "Help me water the garden."})
    service.github_choose(github)
    assert not outside_services._has_posture_record()          # noqa: SLF001 - not set up
    assert service.flow()["toolsmith"]["asked"] is False
    r = cloud.client.post("/toolsmith/model", headers=DEV,
                          json={"where": "cloud", "model": "claude-opus-5-5", "key": KEY})
    assert r.status_code == 200, r.get_json()
    assert cloud.calls == []                                     # nothing went to Anthropic
    assert tc.status()["where"] == "cloud" and tc.in_use()
    assert keys._record_for(cloud.vault, "anthropic")["value"] == KEY  # noqa: SLF001
    assert cloud.client.post("/wizard/toolsmith", headers=DEV,
                             json={"choice": "cloud"}).status_code == 200
    assert service.flow()["toolsmith"]["asked"] is True and not tc.held()
    # The plan approved (its run setting recorded): the door opens, the first run is cloud.
    set_posture("enumerated")
    assert egress_doors.egress_allowed("toolsmith_cloud")[0] is True
    run = T.Run({"id": "j1", "name": "Garden"}, [{"ref": "r1", "name": "A"}], lambda _l: None)
    run.use_cloud = True                                          # what run_job sets
    scope = run._cloud_scope()                                    # noqa: SLF001
    assert isinstance(scope, tc.Scope) and scope.kind == "install"
    tc.send(scope, system="s", tools=[], messages=[{"role": "user", "content": "x"}])
    assert [c["url"] for c in cloud.posts] == ["https://api.anthropic.com/v1/messages"]
    assert cloud.posts[0]["headers"]["x-api-key"] == KEY


def test_without_the_vault_the_wizard_save_says_set_your_pin_first(wizard, cloud,
                                                                    monkeypatch):
    from puffpai.security import keys
    _no_setup_yet()
    _to_after_github(wizard)
    monkeypatch.setattr(keys, "_vault", lambda explicit=None: None)
    r = cloud.client.post("/toolsmith/model", headers=DEV,
                          json={"where": "cloud", "model": "claude-opus-5-5", "key": KEY})
    assert r.status_code == 409 and "Set your vault PIN first" in r.get_json()["message"]


def test_a_key_saved_in_the_wizard_stays_ready_while_she_finishes_it(wizard, cloud,
                                                                      monkeypatch, caplog):
    """Owner, 2026-10-15: key saved at 14:02 with the vault open, idle lock at
    14:08, plan approved at 14:46, and the first install paused. Now the key
    is ready from the save, and each wizard step she takes counts as work; a
    wizard she leaves alone lets go after her keep time."""
    from puffpai.security import toolsmith_cloud as tc
    caplog.set_level("DEBUG")
    service = wizard
    monkeypatch.setattr(tc, "_wizard_busy", service._wizard_going)  # noqa: SLF001
    now = {"t": 5000.0}
    monkeypatch.setattr(tc, "_clock", lambda: now["t"])
    _to_after_github(service)
    assert service.setup_in_progress() is True
    r = cloud.client.post("/toolsmith/model", headers=DEV,
                          json={"where": "cloud", "model": "claude-opus-5-5", "key": KEY})
    assert r.status_code == 200
    assert cloud.client.post("/wizard/toolsmith", headers=DEV,
                             json={"choice": "cloud"}).status_code == 200
    assert tc.lease_active()
    cloud.vault._lock(reason="idle")  # noqa: SLF001 - the idle watchdog, 6 minutes on
    # A slow wizard: a step every 50 minutes for 2.5 hours keeps it ready.
    for _ in range(3):
        now["t"] += 50 * 60
        assert cloud.client.post("/wizard/github", headers=DEV,
                                 json={"choice": "skipped"}).status_code == 200
    assert tc.lease_active() and not cloud.vault.is_unlocked
    st = cloud.client.get("/wizard/build", headers=AUTH).get_json()
    assert st["vault_wait"] == ""                             # nothing to ask her
    # A refused request is not a step.
    now["t"] += 59 * 60
    assert cloud.client.post("/wizard/toolsmith", headers=DEV,
                             json={"choice": "nope"}).status_code == 400
    # She walks away, the plan never approved: 1 hour after her last step.
    assert service.setup_in_progress() is True
    now["t"] += 60 + 1
    assert not tc.lease_active()
    assert KEY not in caplog.text
