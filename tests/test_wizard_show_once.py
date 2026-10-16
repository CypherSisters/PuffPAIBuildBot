"""The show-once face: a value shown to the owner and stored nowhere.

A card may not carry a secret (``cards._validate_field``), and the vault's
recovery code has to reach the owner exactly once. That is what the face is
for, and these tests hold its three properties:

* **nobody is watching unless this run says so** -- the face is carried on the
  run's context, never registered in the module, so nothing is process-global
  and a server cannot inherit a console face from an earlier CLI render;
* **a run drawing its cards somewhere else has no face** -- which is what an
  HTTP request is, and why the vault card refuses to seal a vault there;
* **nothing that crosses the face is kept.**

Run:
    pytest tests/test_wizard_show_once.py
"""
from __future__ import annotations

import types

from puffpai.wizard import cards, render_cli, runner as wizard_runner, secure_vault


def _run(**kwargs):
    return wizard_runner.Runner(args=types.SimpleNamespace(),
                                cfg=types.SimpleNamespace(CONFIG_DIR=""),
                                non_interactive=True, packages={}, **kwargs)


def _ctx(face):
    return wizard_runner.RunContext(
        args=types.SimpleNamespace(), cfg=types.SimpleNamespace(),
        non_interactive=True, shared={}, sections={}, package=None,
        show_once=face)


# -- nothing is global --------------------------------------------------------

def test_the_module_registers_nothing_at_all():
    """The old process-global sink is gone, not merely unset."""
    for name in ("set_show_once_sink", "show_once_sink", "show_once_available"):
        assert not hasattr(cards, name), (
            f"cards.{name} is a process-global face; a server would inherit it")
    assert not hasattr(render_cli, "install_show_once")


def test_rendering_a_card_registers_nothing():
    card = {"skill": "secure", "section": "posture", "title": "Zone 1",
            "decided": "a line", "status": "ready", "consequential": False,
            "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
            "fields": [{"id": "note", "kind": "info", "label": "Note",
                        "current": "nothing to answer"}]}
    render_cli.render(card, non_interactive=True, out=lambda line: None)
    # Nothing anywhere to ask. A context with no face is nobody.
    assert secure_vault.can_show(_ctx(None)) is False


# -- who has a face -----------------------------------------------------------

def test_a_console_run_has_a_face_and_an_http_run_does_not():
    assert isinstance(_run().show_once(), render_cli.ConsoleShowOnce)
    # A run that hands in its own renderer draws its cards somewhere this
    # process cannot see, so it has nobody to show a value to.
    assert _run(renderer=lambda card, **kw: {}).show_once() is None


def test_a_face_without_the_two_methods_is_nobody():
    assert secure_vault.can_show(_ctx(object())) is False
    assert _ctx(object()).face() is None


# -- what the console face does -----------------------------------------------

def test_the_console_face_prints_the_lines_after_a_blank_line():
    """No dashed fences any more: one blank line sets the block
    apart, and the code keeps its indent so it stands out."""
    lines = []
    face = render_cli.ConsoleShowOnce(out=lines.append, ask=lambda prompt: None)
    face.show(["  write this down", "      ABCD-2345"])
    assert lines[0] == ""
    assert render_cli.BEGIN_MARK not in lines
    assert "      ABCD-2345" in lines


def test_the_console_face_drops_control_characters():
    lines = []
    render_cli.ConsoleShowOnce(out=lines.append).show(["\x1b[2Jrepaint", "ok"])
    assert "\x1b" not in "".join(lines)


def test_the_face_reads_one_line_back():
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return "ABCD-2345"

    face = render_cli.ConsoleShowOnce(out=lambda line: None, ask=ask)
    assert face.ask("type it back") == "ABCD-2345"
    assert asked == ["type it back"]


def test_the_channel_keeps_no_copy_of_what_went_through_it():
    """The only record of a shown value is the owner's eyes."""
    shown = []
    face = render_cli.ConsoleShowOnce(out=shown.append)
    face.show(["ABCD-2345"])
    assert not [value for value in vars(face).values()
                if isinstance(value, (str, list, dict))]
    assert "ABCD-2345" not in repr(vars(cards))
