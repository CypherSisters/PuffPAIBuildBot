"""Two findings about setup.

1. Under `island`, `--ha-url http://8.8.8.8:8123 --ha-token <fake>`
               was accepted and written to home_assistant_config.json, exit 0,
               in the same run that refused a public Ollama URL. Home Assistant
               is written to its own file rather than through
               write_main_config, so it missed the LAN posture check -- and it
               is the one integration carrying a credential.
2. The crash headline named the last PRINTED step, so a failure on
               the way into step N read "Step N-1" and pointed the owner at the
               one thing that had just succeeded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from puffpai import build_posture, cli, config as config_mod, setup_ops
from puffpai.security import egress_doors, lan_posture


@pytest.fixture(autouse=True)
def _clean():
    lan_posture.clear_active_posture()
    lan_posture.record_load_violations([])
    egress_doors.reset_cache()
    yield
    lan_posture.clear_active_posture()
    lan_posture.record_load_violations([])
    egress_doors.reset_cache()


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A temp install, optionally with a build manifest."""
    def _make(run_egress: str | None = None):
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(exist_ok=True)
        (cfg_dir / "puffpai_config.json").write_text("{}", encoding="utf-8")
        if run_egress is not None:
            block = build_posture.posture_manifest_block(
                build_posture.assess_posture("online", run_egress, None))
            (cfg_dir / "build_manifest.json").write_text(
                json.dumps({"schema": "build_manifest/v2", "posture": block}),
                encoding="utf-8")
        monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
        config_mod.get_config(_force_reload=True)
        return cfg_dir

    yield _make
    monkeypatch.undo()
    config_mod.get_config(_force_reload=True)


def _ha_file(cfg_dir: Path) -> Path:
    return cfg_dir / "home_assistant_config.json"


# ══ the credential-carrying integration is posture-checked ════════

def test_a_public_ha_url_is_refused_under_island(install) -> None:
    """The exact refuted case."""
    cfg_dir = install(run_egress="island")
    lan_posture.set_active_posture({"run_egress": "island"})
    with pytest.raises(lan_posture.PostureViolation) as exc:
        setup_ops.set_ha_config("http://8.8.8.8:8123", "fake-llat-token")
    assert "'island'" in exc.value.user_message
    assert "ha.url" in exc.value.user_message


def test_no_token_is_written_when_the_address_is_refused(install) -> None:
    """The part that matters most: a refusal must leave no credential on disk.

    The check runs before _write_json, not after, so there is no window in
    which the token exists in a file the wizard then has to clean up.
    """
    cfg_dir = install(run_egress="island")
    lan_posture.set_active_posture({"run_egress": "island"})
    with pytest.raises(lan_posture.PostureViolation):
        setup_ops.set_ha_config("http://8.8.8.8:8123", "fake-llat-token")

    assert not _ha_file(cfg_dir).exists(), "a refused write created the file"
    # And nothing anywhere in the config directory learned the token.
    for path in cfg_dir.rglob("*"):
        if path.is_file():
            assert "fake-llat-token" not in path.read_text(
                encoding="utf-8", errors="replace"), path


def test_an_existing_ha_file_is_not_overwritten_by_a_refused_write(install) -> None:
    cfg_dir = install(run_egress="island")
    lan_posture.set_active_posture({"run_egress": "island"})
    setup_ops.set_ha_config("http://192.168.1.50:8123", "good-token")
    before = _ha_file(cfg_dir).read_text(encoding="utf-8")

    with pytest.raises(lan_posture.PostureViolation):
        setup_ops.set_ha_config("http://8.8.8.8:8123", "fake-llat-token")
    assert _ha_file(cfg_dir).read_text(encoding="utf-8") == before


def test_a_lan_ha_url_is_allowed_under_island(install) -> None:
    cfg_dir = install(run_egress="island")
    lan_posture.set_active_posture({"run_egress": "island"})
    setup_ops.set_ha_config("http://192.168.1.50:8123", "good-token")
    saved = json.loads(_ha_file(cfg_dir).read_text(encoding="utf-8"))
    assert saved["url"] == "http://192.168.1.50:8123"
    assert saved["enabled"] is True


def test_a_lan_ha_url_is_refused_under_sealed(install) -> None:
    """`sealed` is this machine, and Home Assistant is not on it."""
    install(run_egress="sealed")
    lan_posture.set_active_posture({"run_egress": "sealed"})
    with pytest.raises(lan_posture.PostureViolation) as exc:
        setup_ops.set_ha_config("http://192.168.1.50:8123", "good-token")
    assert "island" in exc.value.user_message


def test_a_first_run_with_no_manifest_refuses_a_public_ha_url(install) -> None:
    """No manifest, no stated posture: sealed applies, and public is refused
    under every posture anyway."""
    install(run_egress=None)
    with pytest.raises(lan_posture.PostureViolation):
        setup_ops.set_ha_config("http://8.8.8.8:8123", "fake-llat-token")


def test_the_write_path_check_cannot_be_routed_around(install) -> None:
    """Every caller goes through set_ha_config, so the gate is at the write.

    A test that only checked the wizard would pass while the API route, a
    script, or a future caller wrote whatever it liked.
    """
    import inspect

    source = inspect.getsource(setup_ops.set_ha_config)
    # Skip the docstring: it names both symbols, in the other order.
    body = source.split('"""', 2)[2]
    check_at = body.index("_check_lan_posture")
    write_at = body.index("_write_json")
    assert check_at < write_at, (
        "the posture check must run before the token is written")


def test_a_stated_posture_is_still_normalised(install) -> None:
    """The pass-through cannot be used to invent a permissive tier."""
    install(run_egress=None)
    with pytest.raises(lan_posture.PostureViolation):
        setup_ops.set_ha_config("http://8.8.8.8:8123", "fake-llat-token",
                                posture="wide-open")


# ── the wizard step ─────────────────────────────────────────────────────────

def test_the_wizard_step_refuses_a_public_ha_url(install) -> None:
    """A token on the command line for a forbidden address stops the run.

    Not "quietly drop the integration and report success": the owner asked
    for something this build may not do and has to be told.
    """
    import argparse

    install(run_egress="island")
    args = argparse.Namespace(skip_ha=False, ha_url="http://8.8.8.8:8123",
                              ha_token="fake-llat-token")
    with pytest.raises(cli.SetupRefused) as exc:
        cli._setup_ha_step(args, {"may_offer_lan_integrations": True},
                           non_interactive=True,
                           posture={"run_egress": "island"})
    assert "posture" in exc.value.headline
    assert exc.value.remedy


def test_the_wizard_step_accepts_a_lan_ha_url(install) -> None:
    import argparse

    cfg_dir = install(run_egress="island")
    args = argparse.Namespace(skip_ha=False, ha_url="http://192.168.1.50:8123",
                              ha_token="good-token")
    path = cli._setup_ha_step(args, {"may_offer_lan_integrations": True},
                              non_interactive=True,
                              posture={"run_egress": "island"})
    assert path and Path(path).exists()


def test_the_refusal_names_the_address_and_the_posture(install) -> None:
    import argparse

    install(run_egress="island")
    args = argparse.Namespace(skip_ha=False, ha_url="http://8.8.8.8:8123",
                              ha_token="fake-llat-token")
    with pytest.raises(cli.SetupRefused) as exc:
        cli._setup_ha_step(args, {"may_offer_lan_integrations": True},
                           non_interactive=True,
                           posture={"run_egress": "island"})
    assert "8.8.8.8" in exc.value.message
    assert "'island'" in exc.value.message
    assert "fake-llat-token" not in exc.value.message, "the token leaked"


def test_the_refusal_screen_never_prints_the_token(install, capsys) -> None:
    import argparse

    install(run_egress="island")
    args = argparse.Namespace(skip_ha=False, ha_url="http://8.8.8.8:8123",
                              ha_token="fake-llat-token")
    with pytest.raises(cli.SetupRefused) as exc:
        cli._setup_ha_step(args, {"may_offer_lan_integrations": True},
                           non_interactive=True,
                           posture={"run_egress": "island"})
    cli._print_refusal(exc.value)
    assert "fake-llat-token" not in capsys.readouterr().out


# ── the onboarding route ────────────────────────────────────────────────────

@pytest.fixture
def client(install, monkeypatch):
    from puffpai import api_server

    api_server.app.config["TESTING"] = True
    flask_client = api_server.app.test_client()
    flask_client.environ_base["HTTP_X_API_KEY"] = api_server._API_KEY
    return flask_client


def test_the_onboarding_route_refuses_a_public_ha_url(install, client) -> None:
    """400 with the sentence, not a 500 with a traceback."""
    cfg_dir = install(run_egress="island")
    resp = client.post("/setup/ha", json={"url": "http://8.8.8.8:8123",
                                          "token": "fake-llat-token"})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False
    assert "8.8.8.8" in body["message"]
    assert "fake-llat-token" not in resp.get_data(as_text=True)
    assert not _ha_file(cfg_dir).exists()


def test_the_onboarding_route_still_accepts_a_lan_address(install, client) -> None:
    """The fix must not brick browser onboarding on a box with no manifest."""
    cfg_dir = install(run_egress=None)
    resp = client.post("/setup/ha", json={"url": "http://192.168.1.50:8123",
                                          "token": "good-token"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _ha_file(cfg_dir).exists()


def test_the_onboarding_route_honours_a_sealed_manifest(install, client) -> None:
    cfg_dir = install(run_egress="sealed")
    resp = client.post("/setup/ha", json={"url": "http://192.168.1.50:8123",
                                          "token": "good-token"})
    assert resp.status_code == 400
    assert resp.get_json()["posture"] == "sealed"
    assert not _ha_file(cfg_dir).exists()


# ══ the step is recorded at ENTRY ════════════════════════════════

def test_enter_step_records_before_the_banner_prints(capsys) -> None:
    """The whole finding in one assertion."""
    cli.enter_step("Integrations")
    assert cli.current_step_label() == "Step 11 of 14 - Integrations"
    assert capsys.readouterr().out == "", "recording the step must print nothing"


def test_every_step_records_itself_on_entry() -> None:
    """Each step function calls enter_step with its own name as its first act.

    Reading the source is the point: the bug was that the only thing setting
    the label was a print several statements in, so anything that failed on the
    way there reported the previous step.
    """
    import inspect

    expected = {
        "_setup_posture_step": "Posture",
        "_setup_hardware_step": "Hardware census",
        "_setup_ollama_url_step": "The Ollama address",
        "_setup_brain_step": "The brain",
        "_setup_brain_ready_step": "Brain ready?",
        "_setup_crew_step": "Crew sizing",
        "_setup_interview_step": "Build interview",
        "_setup_surfaces_step": "Surfaces",
        "_setup_bridge_step": "Zone 3, the Bridge",
        "_setup_ha_step": "Integrations",
        "_setup_manifest_step": "The build manifest",
        "_setup_vault_step": "Zone 1, the Vault",
        "_setup_knowledge_step": "Zone 2, the Brain",
    }
    for func_name, step_name in expected.items():
        func = getattr(cli, func_name)
        body = inspect.getsource(func)
        marker = f'enter_step("{step_name}")'
        assert marker in body, f"{func_name} does not call {marker}"
        # ...and it is the FIRST statement, before any work.
        # Split on the FIRST pair only: several of these functions contain a
        # nested function with its own docstring, and splitting on the last
        # pair would start reading from inside it.
        after_doc = body.split('"""', 2)[2] if body.count('"""') >= 2 else body
        first = next(line.strip() for line in after_doc.splitlines()
                     if line.strip())
        assert first == marker, (
            f"{func_name} does something before recording its step: {first!r}")


def test_a_failure_entering_a_step_names_that_step_not_the_previous_one(
        install, monkeypatch) -> None:
    """The reported symptom, reproduced and fixed.

    Enter step 3, then fail on the way into step 11 before its banner prints.
    The label must read step 11.
    """
    import argparse

    install(run_egress="island")
    cli.enter_step("The Ollama address")
    assert cli.current_step_label().startswith("Step 3 of 14")

    args = argparse.Namespace(skip_ha=False, ha_url="http://8.8.8.8:8123",
                              ha_token="fake-llat-token")
    with pytest.raises(cli.SetupRefused):
        cli._setup_ha_step(args, {"may_offer_lan_integrations": True},
                           non_interactive=True,
                           posture={"run_egress": "island"})
    assert cli.current_step_label() == "Step 11 of 14 - Integrations"


def test_enter_step_rejects_a_name_that_is_not_a_step() -> None:
    """A renamed step is a test failure here, not a wrong number on a screen."""
    with pytest.raises(ValueError):
        cli.enter_step("Not A Real Step")


def test_step_banner_still_sets_the_label_as_a_backstop() -> None:
    """A step that forgets enter_step is one line late, not wrong."""
    cli.enter_step("Posture")
    cli.step_banner("Surfaces")
    assert cli.current_step_label() == "Step 7 of 14 - Surfaces"


def test_the_label_matches_the_banner_number() -> None:
    """enter_step and step_banner must never disagree about a step's number."""
    for name in cli.SETUP_STEPS:
        banner = cli.step_banner(name)
        label = cli.current_step_label()
        number = banner.split(" of ")[0]
        assert label.startswith(number), (name, banner, label)


# ── B2-5: the interactive token question never echoes ───────────────────────

_LAN_HUB = "http://192.168.1.50:8123"
_TYPED_TOKEN = "b25-typed-token-not-real-0123456789"


def _interactive_args():
    import argparse
    return argparse.Namespace(skip_ha=False, ha_url=None, ha_token=None)


def _no_plain_reads(monkeypatch):
    """Fail the test if the token is read through a plain, echoing reader."""
    def _refuse(*_a, **_k):
        raise AssertionError("the token was read through plain input")
    monkeypatch.setattr(cli, "_read_line", _refuse)
    monkeypatch.setattr("builtins.input", _refuse)


def test_setup_reads_the_ha_token_without_echo(install, monkeypatch,
                                              capsys) -> None:
    """B2-5: the token goes through _read_pin, never _read_line or input()."""
    install(run_egress="island")
    asked = []
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_ask", lambda prompt, *a, **k: _LAN_HUB)
    _no_plain_reads(monkeypatch)

    def _hidden(prompt):
        asked.append(prompt)
        return _TYPED_TOKEN
    monkeypatch.setattr(cli, "_read_pin", _hidden)

    path = cli._setup_ha_step(_interactive_args(),
                              {"may_offer_lan_integrations": True},
                              non_interactive=False,
                              posture={"run_egress": "island"})
    assert path and Path(path).exists()
    assert len(asked) == 1 and asked[0].endswith("(hidden): ")
    assert _TYPED_TOKEN not in capsys.readouterr().out


def test_an_empty_hidden_answer_skips_home_assistant(install, monkeypatch,
                                                     capsys) -> None:
    cfg_dir = install(run_egress="island")
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_ask", lambda prompt, *a, **k: _LAN_HUB)
    _no_plain_reads(monkeypatch)
    monkeypatch.setattr(cli, "_read_pin", lambda prompt: "")

    path = cli._setup_ha_step(_interactive_args(),
                              {"may_offer_lan_integrations": True},
                              non_interactive=False,
                              posture={"run_egress": "island"})
    assert path == ""
    assert not _ha_file(cfg_dir).exists()
    assert ("Incomplete, so Home Assistant is skipped for now."
            in capsys.readouterr().out)


def test_piped_setup_still_reads_the_token_and_never_hangs(install,
                                                           monkeypatch,
                                                           capsys) -> None:
    """No tty: one plain line from the pipe, getpass is never touched."""
    import getpass
    import io

    install(run_egress="island")

    def _no_console(*_a, **_k):
        raise AssertionError("getpass was used with no terminal")
    monkeypatch.setattr(getpass, "getpass", _no_console)
    # Restored after the test, so an end-of-input here never leaks.
    monkeypatch.setattr(cli, "_stdin_exhausted", False)
    monkeypatch.setattr(cli.sys, "stdin",
                        io.StringIO(f"y\n{_LAN_HUB}\n{_TYPED_TOKEN}\n"))
    assert cli._stdin_is_tty() is False

    path = cli._setup_ha_step(_interactive_args(),
                              {"may_offer_lan_integrations": True},
                              non_interactive=False,
                              posture={"run_egress": "island"})
    assert path and Path(path).exists()
    out = capsys.readouterr().out
    assert "(hidden): " in out
    assert _TYPED_TOKEN not in out


def test_piped_setup_at_end_of_input_skips_rather_than_hangs(
        install, monkeypatch, capsys) -> None:
    import io

    cfg_dir = install(run_egress="island")
    monkeypatch.setattr(cli, "_stdin_exhausted", False)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(f"y\n{_LAN_HUB}\n"))
    path = cli._setup_ha_step(_interactive_args(),
                              {"may_offer_lan_integrations": True},
                              non_interactive=False,
                              posture={"run_egress": "island"})
    assert path == ""
    assert not _ha_file(cfg_dir).exists()
    assert ("Incomplete, so Home Assistant is skipped for now."
            in capsys.readouterr().out)
