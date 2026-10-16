"""The Developer area's backend: read-only routes and the Use CLI list.

* Every command the panel shows is one this build really has: each is parsed
  with the real ``puffpai`` parser.
* No command ever carries a secret option.
* Every word an owner reads here is plain (the one plain-words list and the
  banned list).
* The routes need the app key, change nothing, and never send a password.
* ``puffpai where`` names the vault folder; ``puffpai vault status`` gives
  advice that works (and never says to make a second vault).

No network. Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import shlex
import string

import pytest

from puffpai import cli, developer_api as dev
from puffpai import plain_words as pw
from test_plain_words_in_ui_strings import banned_in

FILL = {"<name>": "shopping", "<model>": "qwen3:8b", "<device>": "light.desk",
        "<number>": "1"}


def _argv(command: str) -> list[str]:
    parts = shlex.split(command, posix=True) if not command.startswith(".\\") \
        else command.split()
    assert parts[0] in ("puffpai", "./puffpai", ".\\puffpai.cmd"), command
    return [FILL.get(p, p) for p in parts[1:]]


def _all_pages(folder):
    return [dev.cli_page(page, folder=str(folder)) for page in dev.PAGES]


# -- only real commands --------------------------------------------------------

@pytest.mark.parametrize("page", dev.PAGES)
def test_every_command_is_one_the_cli_really_has(page, tmp_path):
    parser = cli.build_parser()
    panel = dev.cli_page(page, folder=str(tmp_path))
    assert panel["commands"], page
    for row in panel["commands"]:
        for shell in ("windows", "posix"):
            argv = _argv(row[shell])
            try:
                args = parser.parse_args(argv)
            except SystemExit:  # argparse refuses what it does not know
                pytest.fail(f"not a real command: {row[shell]}")
            assert callable(getattr(args, "func", None)), row[shell]


def test_a_placeholder_always_says_what_to_put_there(tmp_path):
    for panel in _all_pages(tmp_path):
        for row in panel["commands"]:
            if "<" in row["posix"]:
                assert row.get("fill"), row


def test_the_three_pages_have_the_commands_the_design_names():
    flat = {page: [" ".join(r["argv"]) for r in rows]
            for page, rows in dev.COMMANDS.items()}
    assert {"vault status", "vault recover", "vault store <name>",
            "vault get <name>", "keys list"} <= set(flat["vault"])
    assert {"where", "prove", "agents status", "devices list",
            "setup --model <model>", "stop", "start"} <= set(flat["brain"])
    assert {"outside list", "outside on <number>", "outside off <number>",
            "outside log --hours 24", "keys list", "pair"} <= set(flat["bridge"])


# -- no secrets, ever ------------------------------------------------------------

def test_no_command_carries_a_secret_option(tmp_path):
    for panel in _all_pages(tmp_path):
        for row in panel["commands"]:
            for shell in ("windows", "posix"):
                for token in row[shell].split():
                    assert not token.startswith(dev.SECRET_OPTIONS), row[shell]
                    assert token not in dev.SECRET_OPTIONS, row[shell]
    for rows in dev.COMMANDS.values():
        for row in rows:
            assert not set(row["argv"]) & set(dev.SECRET_OPTIONS), row


def test_the_home_assistant_command_asks_for_the_password():
    rows = [r for r in dev.COMMANDS["brain"] if "Home Assistant" in r["what"]]
    assert rows and all("--ha-token" not in r["argv"] for r in rows)
    assert any("password" in r.get("asks", "") for r in rows)


# -- plain words ------------------------------------------------------------------

def _owner_text(panel) -> list[str]:
    out = [panel["go_to_folder_line"], panel["secrets_line"],
           *panel["open_terminal"].values(), *panel["no_command_yet"]]
    for row in panel["commands"]:
        out += [row["what"], row.get("fill", ""), row.get("asks", ""),
                row["posix"], row["windows"]]
    return [t for t in out if t]


def test_every_panel_word_is_plain(tmp_path):
    for panel in _all_pages(tmp_path / "bot"):
        text = "\n".join(_owner_text(panel))
        assert not pw.check(text), pw.check(text)
        assert not banned_in(text), banned_in(text)
        assert "zone" not in text.lower()


def test_the_panel_says_open_powershell_then_paste(tmp_path):
    panel = dev.cli_page("vault", folder=str(tmp_path))
    assert "Open PowerShell" in panel["open_terminal"]["windows"]
    assert panel["open_terminal"]["windows"].endswith("then paste.")


# -- both shells --------------------------------------------------------------

def test_the_installed_launcher_is_used_with_the_folder_step(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "box_shell", lambda: "windows")
    (tmp_path / "puffpai.cmd").write_text("@echo off\n")
    panel = dev.cli_page("vault", folder=str(tmp_path))
    first = panel["commands"][0]
    assert first["windows"] == ".\\puffpai.cmd vault status"
    assert first["posix"] == "./puffpai vault status"
    assert panel["go_to_folder"]["windows"] == f"cd '{tmp_path}'"


def test_no_launcher_on_this_computer_means_the_plain_command(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "box_shell", lambda: "posix")
    panel = dev.cli_page("bridge", folder=str(tmp_path))
    assert panel["commands"][0]["posix"] == "puffpai outside list"
    assert "posix" not in panel["go_to_folder"]


def test_a_folder_with_a_quote_is_quoted_for_each_shell(tmp_path, monkeypatch):
    folder = tmp_path / "Sam's bot"
    folder.mkdir()
    (folder / "puffpai").write_text("#!/bin/sh\n")
    (folder / "puffpai.cmd").write_text("@echo off\n")
    for shell in ("windows", "posix"):
        monkeypatch.setattr(dev, "box_shell", lambda s=shell: s)
        go = dev.cli_page("brain", folder=str(folder))["go_to_folder"]
        if shell == "windows":
            assert go["windows"] == "cd '" + str(folder).replace("'", "''") + "'"
        else:
            assert shlex.split(go["posix"]) == ["cd", str(folder)]


# -- the routes -----------------------------------------------------------------

@pytest.fixture
def client():
    from puffpai import api_server
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def _auth():
    from puffpai import api_server
    return {"Authorization": f"Bearer {api_server._API_KEY}"}


def test_every_developer_route_needs_the_key(client):
    for path in ("/developer/cli/vault", "/developer/vault", "/developer/brain"):
        assert client.get(path).status_code == 401, path


def test_the_cli_route_answers_each_page_and_nothing_else(client):
    for page in dev.PAGES:
        body = client.get(f"/developer/cli/{page}", headers=_auth()).get_json()
        assert body["page"] == page and body["commands"]
    assert client.get("/developer/cli/settings", headers=_auth()).status_code == 404


def test_the_developer_routes_are_read_only(client):
    for path in ("/developer/cli/vault", "/developer/vault", "/developer/brain"):
        assert client.post(path, headers=_auth()).status_code == 405, path


def test_the_brain_route_never_sends_the_home_assistant_password(client, monkeypatch):
    from puffpai.config import get_config
    cfg = get_config()
    monkeypatch.setattr(cfg.ha, "enabled", True)
    monkeypatch.setattr(cfg.ha, "token", "tok-SECRET-9f8e7d")
    monkeypatch.setattr(cfg.ha, "url",
                        "http://owner:pw-SECRET@homeassistant.local:8123/?t=x")
    raw = client.get("/developer/brain", headers=_auth()).get_data(as_text=True)
    assert "SECRET" not in raw and "owner:" not in raw
    body = client.get("/developer/brain", headers=_auth()).get_json()
    assert body["home_assistant"] == {
        "set_up": True, "address": "http://homeassistant.local:8123/"}
    assert body["model"]
    assert not pw.check("\n".join([body["only_ollama_line"],
                                   body["restart_line"]]))


def test_the_vault_route_names_the_folder(client, tmp_path, monkeypatch):
    from puffpai import api_server
    from puffpai.security.vault import Vault
    folder = tmp_path / "vault_data"
    monkeypatch.setattr(api_server, "_get_vault",
                        lambda: Vault(vault_dir=str(folder)))
    body = client.get("/developer/vault", headers=_auth()).get_json()
    assert body["folder"] == str(folder)
    assert body["state"] == "missing" and body["made"] is False
    assert body["drive_there"] is True
    assert body["line"] == "No vault has been made in this folder yet."


def _missing_drive_folder(tmp_path) -> str:
    if os.name == "nt":
        for letter in reversed(string.ascii_uppercase):
            if not os.path.exists(f"{letter}:\\"):
                return f"{letter}:\\BuildBot\\vault_data"
        pytest.skip("every drive letter is in use")
    return str(tmp_path / "unplugged" / "vault_data")


def test_a_missing_drive_is_said_plainly(tmp_path):
    state = dev.vault_folder_state(_missing_drive_folder(tmp_path))
    assert state["drive_there"] is False and state["made"] is False
    assert state["line"] == "Your vault drive is not plugged in."


def test_every_vault_line_is_plain(tmp_path):
    made = tmp_path / "made"
    made.mkdir()
    (made / "vault.meta.json").write_text("{}")
    lines = [dev.vault_folder_state(str(made))["line"],
             dev.vault_folder_state(str(tmp_path / "none"))["line"],
             dev.vault_folder_state(_missing_drive_folder(tmp_path))["line"]]
    assert dev.vault_folder_state(str(made))["made"] is True
    assert not pw.check("\n".join(lines)) and not banned_in("\n".join(lines))


# -- the CLI bits ----------------------------------------------------------------

def test_where_shows_the_vault_folder(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_vault_dir", lambda cfg: str(tmp_path / "vd"))
    assert cli.main(["where"]) == 0
    line = [ln for ln in capsys.readouterr().out.splitlines()
            if ln.strip().startswith("vault:")]
    assert line == [f"  vault:       {tmp_path / 'vd'} (not made yet)"]


def test_vault_status_never_offers_store_to_make_a_vault(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_vault_dir", lambda cfg: str(tmp_path / "vd"))
    assert cli.main(["vault", "status"]) == 0
    out = capsys.readouterr().out
    assert "not made yet" in out and "`puffpai setup`" in out
    assert "vault store" not in out
    assert not pw.check(out) and not banned_in(out)


def test_vault_status_for_a_made_vault_gives_commands_that_work(capsys, tmp_path, monkeypatch):
    folder = tmp_path / "vd"
    folder.mkdir()
    (folder / "vault.meta.json").write_text("{}")
    monkeypatch.setattr(cli, "_vault_dir", lambda cfg: str(folder))
    assert cli.main(["vault", "status"]) == 0
    out = capsys.readouterr().out
    assert "password" not in out.lower()
    for cmd in ("puffpai vault store <name>", "puffpai vault get <name>",
                "puffpai vault recover"):
        assert cmd in out
    assert "puffpai setup" not in out
    assert not pw.check(out) and not banned_in(out)


def test_vault_status_with_the_drive_missing_refuses_to_suggest_a_new_vault(
        capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_vault_dir",
                        lambda cfg: _missing_drive_folder(tmp_path))
    assert cli.main(["vault", "status"]) == 1
    out = capsys.readouterr().out
    assert "Your vault drive is not plugged in." in out
    assert "puffpai setup" not in out
