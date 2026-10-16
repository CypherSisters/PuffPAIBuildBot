"""``puffpai setup`` with a pipe on stdin must finish, not wait for a keyboard.

On Windows ``getpass.getpass`` reads the console handle directly
(``msvcrt.getwch``) whenever stdin looks like the process's own -- which a pipe
or a redirected file does. So the wizard's secret fields asked a keyboard nobody
was at and the run hung forever, with no output to say why. ``cli._read_pin``
had already been fixed for the vault ceremony; the card renderer had not, and
the card renderer is what every converted skill asks through now.

This test runs the wizard as a subprocess with a pipe on stdin and a timeout: a
hang is a failure here rather than a stuck install on somebody's machine. It
writes into a temp data dir and reaches no network it would not reach anyway
(the Ollama probe on loopback).

Run:
    pytest tests/test_wizard_piped_stdin.py
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

#: Long enough that the run ends because it is finished, not because the input
#: ran out on the first card.
ANSWERS = "\n" * 200

#: Generous, and still a fraction of forever. The same run takes ~15s.
TIMEOUT_S = 300


def test_setup_with_a_pipe_on_stdin_terminates(tmp_path):
    """The timeout is on the subprocess: a hang fails, it does not wedge."""
    env = dict(os.environ)
    env["PUFFPAI_DATA_DIR"] = str(tmp_path / "data")
    env["PUFFPAI_STATE_DIR"] = str(tmp_path / "data")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = os.path.join(root, "src")

    try:
        done = subprocess.run(
            [sys.executable, "-m", "puffpai", "setup"],
            input=ANSWERS, capture_output=True, text=True, env=env,
            cwd=root, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:  # pragma: no cover - the bug itself
        pytest.fail("`puffpai setup` hung on a pipe: a secret field is asking "
                    "a console nobody is at")

    # Today's contract: the build finishes, and an unanswered vault PIN is a
    # vault that was not created rather than a wizard that stops.
    assert done.returncode in (0, 1), (done.returncode, done.stdout[-2000:])
    assert "Traceback" not in done.stderr, done.stderr[-2000:]
    assert "Your private stuff" in done.stdout
    assert os.path.isfile(os.path.join(str(tmp_path / "data"), "config",
                                       "build_manifest.json"))
