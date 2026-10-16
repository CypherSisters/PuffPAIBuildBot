"""The installers' pointer, and the problem it closes.

``install.ps1 -StateDir .\\state`` wrote the key, the launchers and the env file
for a state dir beside the checkout - and then the documented next step,
``puffpai setup``, ran in a shell with none of that environment and wrote the
config, the vault and the manifest into the DEFAULT per-user state dir.
``run-backend.ps1`` then served the state dir that was asked for, which had no
setup in it. Nothing in either half was wrong on its own; the two halves simply
never agreed on where this install lives.

So these tests are about agreement, from three directions: the pointer is read
when nothing else has answered, an explicit environment always beats it, and a
pointer that has gone stale says so once and changes nothing.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os

import pytest

from puffpai import cli, install_pointer
from puffpai.config import get_state_dir

ENV_NAMES = ("PUFFPAI_STATE_DIR", "PUFFPAI_DATA_DIR", "PUFFPAI_PORT",
             "PUFFPAI_API_KEY", "PUFFPAI_BACKEND_URL")


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A checkout with an install's env file and the pointer beside it.

    ``candidate_roots`` is pinned at the fake checkout because the real one is
    this repo, and a test that wrote a pointer into the repo root would change
    what every other command in the developer's shell resolves.
    """
    root = tmp_path / "checkout"
    state = root / "state"
    state.mkdir(parents=True)
    env_file = state / "buildbot.env"
    env_file.write_text(
        "# PuffPAI BuildBot - the environment THIS install runs with.\n"
        f"PUFFPAI_CHECKOUT={root}\n"
        "PUFFPAI_API_KEY=" + "abcdef0123456789" * 4 + "\n"
        f"PUFFPAI_DATA_DIR={state}\n"
        f"PUFFPAI_STATE_DIR={state}\n"
        "PUFFPAI_PORT=18043\n"
        "PATH=/nowhere\n",
        encoding="utf-8")
    monkeypatch.setattr(install_pointer, "candidate_roots",
                        lambda: [str(root)])
    monkeypatch.setattr(install_pointer, "_warned", False)
    monkeypatch.setattr(install_pointer, "_loaded", None)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    pointer = root / install_pointer.POINTER_NAME
    pointer.write_text(json.dumps({"env_file": str(env_file)}),
                       encoding="utf-8")
    return {"root": root, "state": state, "env_file": env_file,
            "pointer": pointer}


# ── with a pointer and no environment, the pointed state dir wins ───────────

def test_apply_loads_the_pointed_env_file(install):
    used = install_pointer.apply()

    assert used == str(install["env_file"])
    assert os.environ["PUFFPAI_STATE_DIR"] == str(install["state"])
    assert os.environ["PUFFPAI_PORT"] == "18043"
    assert get_state_dir() == str(install["state"])


def test_only_puffpai_names_are_read(install):
    """The env file sits next to a secret, but it is still just a file."""
    before = os.environ.get("PATH")
    install_pointer.apply()
    assert os.environ.get("PATH") == before


def test_a_bare_cli_command_resolves_the_pointed_state_dir(install, capsys):
    """The whole point: `puffpai where` in a fresh shell, via cli.main."""
    assert cli.main(["where"]) == 0

    out = capsys.readouterr().out
    state_line = [ln for ln in out.splitlines() if "state dir:" in ln][0]
    assert str(install["state"]) in state_line
    assert str(install["env_file"]) in out


def test_where_names_the_key_source_and_never_the_key(install, capsys):
    cli.main(["where"])
    out = capsys.readouterr().out
    key = install_pointer.parse_env_file(
        str(install["env_file"]))["PUFFPAI_API_KEY"]
    assert "API key:     from the PUFFPAI_API_KEY environment variable" in out
    assert key not in out


# ── an explicit environment always wins ────────────────────────────────────

def test_an_explicit_state_dir_beats_the_pointer(install, monkeypatch, tmp_path):
    mine = tmp_path / "mine"
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(mine))

    install_pointer.apply()

    assert os.environ["PUFFPAI_STATE_DIR"] == str(mine)
    assert get_state_dir() == str(mine)
    # ...and the settings the shell did NOT choose still come from the pointer.
    assert os.environ["PUFFPAI_PORT"] == "18043"


# ── a stale or broken pointer changes nothing, and says so once ─────────────

def test_no_pointer_is_silent_and_changes_nothing(install, capsys):
    install["pointer"].unlink()

    assert install_pointer.apply() is None

    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert "PUFFPAI_STATE_DIR" not in os.environ


@pytest.mark.parametrize("body", ["not json at all", "{}", '{"env_file": ""}'])
def test_a_corrupt_pointer_is_ignored_with_one_warning_line(install, capsys,
                                                            body):
    install["pointer"].write_text(body, encoding="utf-8")

    assert install_pointer.apply() is None

    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1, err
    assert install_pointer.POINTER_NAME in err[0]
    assert "PUFFPAI_STATE_DIR" not in os.environ


def test_a_pointer_to_a_missing_env_file_warns_once(install, capsys):
    install["env_file"].unlink()

    assert install_pointer.apply() is None
    install_pointer.apply()  # a second command must not narrate it again

    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1, err
    assert "is not there" in err[0]


# ── the anchors ────────────────────────────────────────────────────────────

def test_candidate_roots_include_the_checkout_of_an_editable_install():
    """<checkout>/src/puffpai/__init__.py -> <checkout>, which is this repo."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    assert str(repo) in install_pointer.candidate_roots()
