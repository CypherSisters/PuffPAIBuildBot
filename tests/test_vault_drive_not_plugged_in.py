"""A missing vault drive reaches the dashboard pages and the chat.

The vault code remembers the folder the vault was made in and, when that folder is
gone, says "Your vault drive is not plugged in" and never makes a new vault.
The Developer vault page, ``puffpai where`` and the YOUR PRIVATE STUFF
card were built before that existed: they only checked the drive letter, so
a vault whose folder was gone (drive letter still there) read "No vault has
been made in this folder yet" / "No vault yet", which points the owner at
making a second, empty vault. The chat's privacy lines said "You have not
set up a vault yet" the same way.

No network. Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import shutil

import pytest

from puffpai import cli, developer_api as dev
from puffpai.security import chat_privacy
from puffpai.security import privacy_explainer as pe
from puffpai.security import vault as vault_mod
from puffpai.security.vault import Vault

GOOD_PIN = "correct horse battery staple"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOT_PLUGGED = "Your vault drive is not plugged in."


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(vault_mod, "_PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(vault_mod, "_RECOVERY_PBKDF2_ITERATIONS", 1000)


def _unplugged() -> str:
    """Make the install's own vault, then take its folder away."""
    v = Vault()
    v.create(GOOD_PIN)
    v.lock()
    shutil.rmtree(v.vault_dir)
    return v.vault_dir


def test_the_vault_state_is_not_plugged_in():
    _unplugged()
    v = Vault()
    assert pe.vault_state(v) == "not_plugged_in"
    line = pe._vault_line("not_plugged_in")  # noqa: SLF001
    assert line.startswith(NOT_PLUGGED)
    answer = pe.privacy_answer(rich=False, vault=v, model_url="http://127.0.0.1:11435",
                               services=[])
    assert NOT_PLUGGED in answer
    assert "not set up a vault" not in answer and "no vault set up" not in answer
    from puffpai import plain_words as pw
    from test_plain_words_in_ui_strings import banned_in
    for text in (line, answer, pe._chat_line("not_plugged_in")):  # noqa: SLF001
        assert not pw.check(text) and not banned_in(text)


def test_a_fresh_install_still_has_no_vault_yet(tmp_path):
    assert pe.vault_state(Vault()) == "missing"
    assert pe.vault_state(Vault(vault_dir=str(tmp_path / "none"))) == "missing"


def test_the_wizard_never_offers_a_new_vault_in_its_place():
    _unplugged()
    items = {i["id"]: i for i in pe.proposal_items("x", [], vault=Vault(),
                                                   model_url="http://127.0.0.1:11435")}
    vault_item = items["private:vault"]
    assert vault_item["apply"]["ref"] != "create"
    assert NOT_PLUGGED.rstrip(".").lower() in (vault_item["label"] + " "
                                               + vault_item["detail"]).lower()


def test_the_chat_notice_says_the_drive_is_not_plugged_in():
    _unplugged()
    kept, state = chat_privacy._file(Vault(), {"kind": "address"})  # noqa: SLF001
    assert kept == chat_privacy.NOT_KEPT and state == "not_plugged_in"
    notice = chat_privacy.plain_notice(["address"], kept, state)
    assert NOT_PLUGGED.rstrip(".") in notice and "not set up a vault" not in notice
    from puffpai import plain_words as pw
    assert not pw.check(notice)


def test_the_developer_vault_page_and_where_say_not_plugged_in(capsys):
    folder = _unplugged()
    state = dev.vault_folder_state(folder)
    assert state["drive_there"] is False and state["made"] is False
    assert state["line"] == NOT_PLUGGED
    assert cli.main(["where"]) == 0
    line = [ln for ln in capsys.readouterr().out.splitlines()
            if ln.strip().startswith("vault:")]
    assert len(line) == 1 and line[0].endswith("(its drive is not plugged in)")


def test_the_card_and_the_page_have_words_for_it():
    def read(*parts):
        with open(os.path.join(REPO, "dashboard", "app", "dashboard", *parts),
                  encoding="utf-8") as fh:
            return fh.read()
    card = read("_components", "AsideMenu", "index.tsx")
    assert 'not_plugged_in: "Vault drive not plugged in"' in card
    words = read("developer", "developerService.ts")
    assert 'case "not_plugged_in":' in words
