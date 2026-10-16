"""Three wizard defects a stranger hits in the first two minutes.

Found walking `puffpai setup` by hand for a garden build:

1. **The CLI never asked what to call it.** The dashboard wizard's first question
   was missing from the headless one, so a build made entirely at the keyboard
   came out with ``build.name == ""`` and answered to the shipped default. Both
   wizards now write ``identity.name`` (what ``/profile/ai`` reads for
   ``ai_name``) AND the manifest.
2. **A configured Ollama address was second-guessed.** Auto-discovery probed
   ``_ALTERNATE_PORTS`` - including 11434 - whenever ``--ollama-url`` was absent,
   even when the address had been set by ``PUFFPAI_OLLAMA_URL`` or by the config
   file. A configured address is an answer.
3. **Two ways to stall a scripted run.** A refused ``--vault-pin`` under piped
   stdin fell through to a hidden prompt nobody could answer and then reported
   "skipping the vault" over a vault the owner had asked for; and the line reader
   had no end-of-file latch, so a redirected answers file shorter than the wizard
   left every later step asking again.

Run:
    pytest tests/test_wizard_name_probe_stdin.py
"""
from __future__ import annotations

import argparse
import io
import os
from contextlib import redirect_stdout

import pytest

from puffpai import build_manifest, cli, first_run, setup_ops
from puffpai.config import get_config

GOOD_PIN = "8316047295"
SHORT_PIN = "1234"


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    get_config(_force_reload=True)
    yield tmp_path
    get_config(_force_reload=True)


def _args(**kw):
    base = dict(yes=False, name=None, ollama_url=None, vault_password=None,
                skip_vault=False, goal=None, scaffold=False, lists=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _run(fn, *a, **kw):
    """Call a wizard step, returning ``(result, printed)``."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(*a, **kw)
    return result, buf.getvalue()


# ---------------------------------------------------------------------------
# 1. the name
# ---------------------------------------------------------------------------

def test_the_cli_asks_for_a_name_and_writes_it_where_the_dashboard_reads_it(
        scratch, monkeypatch):
    monkeypatch.setattr(cli, "_read_line", lambda prompt: "GardenBot")
    name, printed = _run(cli._setup_name_step, _args(), non_interactive=False)

    assert name == "GardenBot"
    assert "It is GardenBot." in printed
    # The one place the running app reads its name from.
    assert get_config(_force_reload=True).identity.name == "GardenBot"
    assert first_run.current_name() == "GardenBot"


def test_the_name_flag_is_the_scripted_way_to_answer(scratch):
    name, _ = _run(cli._setup_name_step, _args(name="GardenBot", yes=True),
                   non_interactive=True)
    assert name == "GardenBot"
    assert get_config(_force_reload=True).identity.name == "GardenBot"


def test_a_typed_name_that_cannot_be_used_is_said_out_loud(scratch):
    name, printed = _run(cli._setup_name_step, _args(name="​​"),
                         non_interactive=True)
    assert name == ""
    assert "cannot be used" in printed
    assert "at least one letter or number" in printed


def test_pressing_enter_leaves_it_unnamed_rather_than_claiming_a_choice(
        scratch, monkeypatch):
    monkeypatch.setattr(cli, "_read_line", lambda prompt: "")
    name, printed = _run(cli._setup_name_step, _args(), non_interactive=False)
    assert name == "PuffPAI"  # the offered default, typed by pressing Enter
    assert "It is PuffPAI." in printed


def test_a_non_interactive_run_with_no_name_records_unnamed(scratch):
    name, printed = _run(cli._setup_name_step, _args(yes=True),
                         non_interactive=True)
    assert name == ""
    assert "records it as unnamed" in printed


def test_the_name_reaches_the_manifest(scratch):
    _run(cli._setup_manifest_step, {"ram_gb": 8}, {"model": "m"},
         {"master_core": 3, "max_specialists": 2},
         {"goal": "", "selected": [], "proposal": None, "persona": None},
         bot_name="GardenBot", non_interactive=True)
    assert build_manifest.read_manifest()["build"]["name"] == "GardenBot"


def test_a_scaffolded_persona_is_recorded_in_the_manifest(scratch):
    persona = {"scaffolded": True, "app": "gardenbot", "pack": "gardenbot",
               "name": "GardenBot", "goal": "a garden bot", "lists": ["plantings"],
               "surfaces": ["daily.notes"], "app_manifest": "x", "pack_manifest": "y",
               "note": "written by the wizard"}
    _run(cli._setup_manifest_step, {"ram_gb": 8}, {"model": "m"},
         {"master_core": 3, "max_specialists": 2},
         {"goal": "a garden bot", "selected": [], "proposal": None,
          "persona": persona},
         bot_name="GardenBot", non_interactive=True)
    written = build_manifest.read_manifest()["persona"]
    assert written["app"] == "gardenbot" and written["lists"] == ["plantings"]


# ---------------------------------------------------------------------------
# 2. a configured Ollama address is never guessed past
# ---------------------------------------------------------------------------

@pytest.fixture
def no_ollama(monkeypatch):
    """Nothing answers anywhere, and any probe beyond the first is a failure."""
    probed: list[str] = []

    def _list_models(url, timeout=3):
        probed.append(url)
        return None

    def _find(default_url=None, timeout=2):
        raise AssertionError(f"the wizard went guessing past a configured "
                             f"address (probed {probed})")

    monkeypatch.setattr(setup_ops, "list_models", _list_models)
    monkeypatch.setattr(setup_ops, "find_ollama", _find)
    return probed


@pytest.mark.parametrize("where", ["flag", "env", "config"])
def test_a_configured_address_suppresses_the_guessing(scratch, monkeypatch,
                                                      no_ollama, where):
    url = "http://127.0.0.1:11499"
    args = _args(yes=True)
    if where == "flag":
        args.ollama_url = url
    elif where == "env":
        monkeypatch.setenv("PUFFPAI_OLLAMA_URL", url)
        get_config(_force_reload=True)
    else:
        setup_ops.write_main_config({"ollama": {"url": url}})
        get_config(_force_reload=True)

    (settled, models), printed = _run(
        cli._setup_ollama_url_step, args, get_config(), non_interactive=True)

    assert settled == url and models is None
    assert no_ollama == [url], "only the configured address may be probed"
    assert "not guessing past it" in printed
    assert "Nothing else was probed." in printed


def test_an_unset_address_may_still_probe_and_says_which_it_found(
        scratch, monkeypatch):
    """The other half: with nothing configured, guessing is the useful thing."""
    found = "http://127.0.0.1:11435"
    monkeypatch.setattr(setup_ops, "list_models",
                        lambda url, timeout=3: ["m"] if url == found else None)
    monkeypatch.setattr(setup_ops, "find_ollama",
                        lambda default_url=None, timeout=2: found)

    (settled, models), printed = _run(
        cli._setup_ollama_url_step, _args(yes=True), get_config(),
        non_interactive=True)

    assert settled == found and models == ["m"]
    assert "No address is configured" in printed
    assert f"Found Ollama at {found}, by probing." in printed


def test_the_source_of_the_address_is_named(scratch, monkeypatch):
    cfg = get_config()
    assert cli._ollama_address_source(_args(ollama_url="http://a:1"), cfg) == (
        "http://a:1", "the --ollama-url flag")
    monkeypatch.setenv("PUFFPAI_OLLAMA_URL", "http://b:2")
    assert cli._ollama_address_source(_args(), cfg) == (
        "http://b:2", "the PUFFPAI_OLLAMA_URL environment variable")
    monkeypatch.delenv("PUFFPAI_OLLAMA_URL")
    assert cli._ollama_address_source(_args(), get_config(_force_reload=True))[1] == ""


# ---------------------------------------------------------------------------
# 3. a scripted run cannot stall
# ---------------------------------------------------------------------------

def test_a_refused_pin_with_no_terminal_refuses_loudly(scratch, monkeypatch):
    """The defect: this used to print "skipping the vault" and exit 0."""
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(cli, "_prompt_for_pin",
                        lambda: pytest.fail("prompted with no terminal"))

    with pytest.raises(cli.SetupRefused) as exc:
        with redirect_stdout(io.StringIO()):
            cli._setup_vault_step(_args(vault_password=SHORT_PIN),
                                  get_config(), non_interactive=False)

    refusal = exc.value
    assert "no terminal" in refusal.headline
    assert any("--skip-vault" in line for line in refusal.remedy)
    assert not os.path.isdir(os.path.join(str(scratch), "vault"))


def test_that_refusal_is_exit_code_one_not_a_finished_build(scratch, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(cli, "_run_setup", lambda args: (_ for _ in ()).throw(
        cli.SetupRefused("No terminal.", "Nothing was written.", ["--skip-vault"])))
    with redirect_stdout(io.StringIO()) as buf:
        code = cli._cmd_setup(_args())
    assert code == 1
    assert "No terminal." in buf.getvalue()


def test_prompt_for_pin_itself_will_not_read_a_pipe(monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(cli, "_read_pin",
                        lambda prompt: pytest.fail("read a hidden PIN with no tty"))
    answer, printed = _run(cli._prompt_for_pin)
    assert answer == ""
    assert "no terminal to type a PIN into" in printed


def test_a_hidden_pin_read_uses_the_plain_reader_with_no_tty(monkeypatch):
    """getpass on Windows reads the CONSOLE, which a redirected file is not."""
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(cli, "_read_line", lambda prompt: " 8316047295 ")
    import getpass
    monkeypatch.setattr(getpass, "getpass",
                        lambda prompt="": pytest.fail("getpass with no tty"))
    assert cli._read_pin("  PIN: ") == GOOD_PIN


def test_running_out_of_input_stops_the_prompting(monkeypatch):
    """A redirected answers file shorter than the wizard finishes the wizard."""
    answers = iter(["offline"])

    def fake_input(prompt=""):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)
    cli._reset_stdin_state()

    assert cli._ask("First", "default-1") == "offline"
    # Second read hits end of file; every read after it takes the default
    # WITHOUT touching stdin again - that is what the latch is for.
    assert cli._ask("Second", "default-2") == "default-2"
    monkeypatch.setattr("builtins.input",
                        lambda prompt="": pytest.fail("read past end of input"))
    assert cli._ask("Third", "default-3") == "default-3"
    assert cli._ask_yes_no("Fourth", default=True) is True
    assert cli._ask_paragraph("Fifth") == ""
    cli._reset_stdin_state()


def test_an_empty_line_is_still_an_answer(monkeypatch):
    """Empty means "the default"; end-of-file means "stop asking". Not the same."""
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    cli._reset_stdin_state()
    assert cli._ask("Name", "PuffPAI") == "PuffPAI"
    assert cli._ask("Name", "PuffPAI") == "PuffPAI"  # not latched
    assert cli._stdin_exhausted is False
