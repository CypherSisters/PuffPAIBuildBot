"""The closing screen tells the truth, and the CLI uses the name it was given.

String fixes and the closing screen.

* After the owner names the bot, the CLI went on saying "PuffPAI" - in the
  knowledge step and as the prefix on every line of `puffpai chat`. A product that
  forgets the name you just gave it is not customisable, whatever the manifest says.
* "the customization page" is not a page. It is /dashboard/profile.
* The scaffold's Try line said `add something to the ... list`, which would have
  saved a row literally reading "something".
* `puffpai serve` printed a traceback when the API key was unusable, instead of the
  written refusal that function raises.
* PUFFPAI_PORT was ignored, though every other setting here is PUFFPAI_-prefixed.
* And the closing screen never mentioned dashboard/.env.local, so a stranger who
  did everything it said got a dashboard that rendered and then failed every call.

Run:
    pytest tests/test_closing_and_strings.py
"""
from __future__ import annotations

import argparse
import os

import pytest

from puffpai import cli, persona_scaffold


# ---------------------------------------------------------------------------
# the name
# ---------------------------------------------------------------------------

def test_bot_name_reads_the_config_the_dashboard_reads(monkeypatch):
    from puffpai import config as config_mod

    class _Identity:
        name = "GardenBot"

    class _Cfg:
        identity = _Identity()

    monkeypatch.setattr(config_mod, "get_config", lambda: _Cfg())
    assert cli.bot_name() == "GardenBot"


def test_an_unnamed_build_falls_back_to_the_shipped_default(monkeypatch):
    from puffpai import config as config_mod

    monkeypatch.setattr(config_mod, "get_config",
                        lambda: (_ for _ in ()).throw(RuntimeError("no config")))
    assert cli.bot_name() == cli.DEFAULT_BOT_NAME


def test_the_knowledge_step_uses_the_name_the_owner_typed():
    """The exact line the review named. Checked in the source, because running
    the whole Zone 2 step drags the indexer and the source registry in with it.
    """
    import inspect

    source = inspect.getsource(cli._setup_knowledge_step)
    assert "PuffPAI starts with" not in source
    assert "bot_name()" in source
    assert "{named} starts with only its base-model knowledge" in source


def test_no_sentence_about_the_owners_bot_is_hard_coded_to_puffpai():
    """The product may be called PuffPAI. The owner's bot is called what they said.

    So a version banner, a bug-report line and a component name keep the string;
    a sentence whose subject is the bot ("X starts with", "next time X starts")
    must read the name. The list below is the allowed set, checked by name rather
    than by a loose substring so a new hard-coded sentence fails this test.
    """
    import inspect

    allowed = (
        'print(f"PuffPAI v{__version__}")',
        'print(f"  Welcome to PuffPAI v{__version__}")',
        'print("PuffPAI voice components")',
        'print("This is a bug in PuffPAI, not something you did wrong. Please open")',
        'print(f"PuffPAI API server on http://{host}:{port}")',
        'print("  PuffPAI product-agent team")',
        # The wizard's version banner names the product and its version.
        'print(render_cli.ascii_only(f"BuildBot, from PuffPAI version {__version__}"))',
        'print("an issue at https://github.com/CypherSisters/PuffPAIBuildBot/issues with the")',
    )
    offenders = []
    for line in inspect.getsource(cli).splitlines():
        text = line.strip()
        if not text.startswith("print(") or "PuffPAI" not in text:
            continue
        if text in allowed or "DEFAULT_BOT_NAME" in text:
            continue
        offenders.append(text)
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# "the customization page"
# ---------------------------------------------------------------------------

def test_no_screen_still_says_the_customization_page():
    import inspect

    source = inspect.getsource(cli)
    assert "customization page" not in source
    assert "/dashboard/profile" in source


# ---------------------------------------------------------------------------
# the Try line
# ---------------------------------------------------------------------------

def test_the_scaffold_try_line_is_a_sentence_that_works():
    result = {"name": "GardenBot", "id": "gardenbot",
              "app_path": "a", "pack_path": "b",
              "lists": ["plantings"], "keywords": ["gardenbot"],
              "route": "/x", "surfaces": ["daily.notes"], "installed": True}
    lines = persona_scaffold.scaffold_summary_lines(result)
    tries = [ln for ln in lines if "Try:" in ln]
    assert tries
    assert persona_scaffold.TRY_ITEM in tries[0]
    assert "add something to" not in tries[0]
    # It is exactly the phrase the fastpath handles.
    assert persona_scaffold.try_phrase("plantings") in tries[0]


# ---------------------------------------------------------------------------
# `puffpai serve`
# ---------------------------------------------------------------------------

def test_serve_prints_the_api_key_refusal_instead_of_a_traceback(monkeypatch,
                                                                capsys):
    """The refusal is a written sentence. It was arriving as a stack trace."""
    message = ("FATAL: refusing to start - the resolved API key is unusable "
               "because it is a placeholder, not a secret. Fix it where it "
               "lives: the OS keyring entry PUFFPAI_API_KEY.")

    def _raise():
        raise RuntimeError(message)

    monkeypatch.setattr(cli, "_import_api_server", _raise)
    code = cli._cmd_serve(argparse.Namespace(host=None, port=None))
    assert code == 1
    out = capsys.readouterr().out
    assert message in out
    assert "Traceback" not in out


def test_the_refusal_really_is_a_runtime_error_from_the_key_validator():
    """The seam is only honest if the thing it catches is the thing that raises."""
    from puffpai import api_server

    with pytest.raises(RuntimeError) as exc:
        api_server.validate_api_key("changeme")
    assert "refusing to start" in str(exc.value)


def test_the_port_comes_from_the_flag_then_puffpai_port_then_port(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("PUFFPAI_PORT", raising=False)
    args = argparse.Namespace(port=None)

    assert cli._serve_port(args, 8000) == 8000

    monkeypatch.setenv("PORT", "8200")
    assert cli._serve_port(args, 8000) == 8200

    # PUFFPAI_PORT wins: every other setting this product reads is prefixed, and
    # a bare PORT is a name half the tools on a machine already use.
    monkeypatch.setenv("PUFFPAI_PORT", "8300")
    assert cli._serve_port(args, 8000) == 8300

    # The flag beats both.
    assert cli._serve_port(argparse.Namespace(port=8400), 8000) == 8400


def test_an_unparsable_port_variable_is_ignored_not_fatal(monkeypatch, capsys):
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("PUFFPAI_PORT", "eight thousand")
    assert cli._serve_port(argparse.Namespace(port=None), 8000) == 8000
    assert "not a port number" in capsys.readouterr().out

