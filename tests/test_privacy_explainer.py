"""Privacy sentences are generated from the real state, in plain words."""

from __future__ import annotations

import pytest

from puffpai.security import chat_privacy, privacy_explainer as pe, vault_inbox
from puffpai.security.vault import Vault
from puffpai import plain_words  # noqa: E402  (the one banned list)

PW = "correct horse battery"
GOALS = [
    "I want a space bot that knows everything about space, shows me cool "
    "space stuff, and works with my smart telescope",
    "I love dinosaurs, I want a bot that knows every dinosaur",
    "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos",
    "A garden helper for my vegetable beds in Ohio",
    "A knitting and crochet buddy with patterns and tips",
    "A music bot that knows bands and albums",
]
#: On top of plain_words' one list: privacy wording that misled.
JARGON_EXTRA = ("drawbridge", "encrypted at rest", "[redacted", "pii")
PROPOSAL_KEYS = {"id", "section", "label", "detail", "size_bytes",
                 "outside_host", "default_on", "needs_yes", "apply"}
LOCAL = "http://127.0.0.1:11435"


def _plain(text: str) -> bool:
    return plain_words.is_plain(text, also=JARGON_EXTRA)


@pytest.fixture()
def locked(tmp_path):
    v = Vault(vault_dir=str(tmp_path / "v"))
    v.create(PW)
    v.lock()
    return v


def test_summary_locked_vault_counts_waiting_items(locked):
    vault_inbox.deposit(locked.vault_dir, {"items": {"phone": ["x"]}})
    lines = pe.plain_summary(None, vault=locked, services=[], model_url=LOCAL)
    text = " ".join(lines)
    assert "locked in a vault" in text
    assert "1 personal detail is waiting" in text
    assert "even while the vault is locked" in text
    assert "not encrypted" in text
    assert "Nothing leaves your computer without your consent. Every outside service is off." in text
    assert all(_plain(line) for line in lines), lines


def test_summary_without_vault_says_no_copy_is_kept(tmp_path):
    v = Vault(vault_dir=str(tmp_path / "none"))
    text = " ".join(pe.plain_summary(None, vault=v, services=[],
                                     model_url=LOCAL))
    assert "not set up a vault" in text
    assert "nowhere safe to keep a copy" in text
    assert "put it in your vault" not in text


def test_old_locked_vault_without_inbox_key_does_not_overclaim(locked, tmp_path):
    import shutil
    shutil.rmtree(vault_inbox.inbox_dir(locked.vault_dir))
    text = " ".join(pe.plain_summary(None, vault=locked, services=[],
                                     model_url=LOCAL))
    assert "even while the vault is locked" not in text
    assert "opened the vault once" in text


def test_unknown_states_say_so_and_never_claim_off():
    lines = pe.plain_summary(None, vault=None, services=None, model_url="")
    text = " ".join(lines)
    assert "could not check your vault" in text
    assert "could not check which outside services" in text
    assert "Every outside service is off" not in text
    assert "could not tell where the AI model runs" in text


def test_build_record_fills_in_only_when_the_vault_cannot_be_read():
    text = " ".join(pe.plain_summary({"vault": {"configured": False}},
                                     vault=None, services=[], model_url=LOCAL))
    assert "not set up a vault" in text


def test_open_services_are_listed_with_what_they_send():
    lines = pe.plain_summary(None, vault=None, model_url=LOCAL, services=[
        {"id": "weather", "name": "Weather", "hosts": ["api.open-meteo.com"]},
        {"id": "some_new_feed", "name": "Some new feed",
         "hosts": ["api.example.org"]},
    ])
    text = " ".join(lines)
    assert "approximate location" in text and "api.open-meteo.com" in text
    assert "Some new feed: may reach api.example.org" in text
    assert all(_plain(line) for line in lines)


def test_model_on_another_computer_is_said():
    text = " ".join(pe.plain_summary(None, vault=None, services=[],
                                     model_url="http://192.168.1.20:11434"))
    assert "another computer (192.168.1.20)" in text


def test_live_services_come_from_the_real_switches():
    # Whatever this machine has on, the list must match the one list of
    # outside services ("What leaves your computer") exactly: every count on
    # every page comes from it (it used to be the
    # built-in services only, so pages disagreed).
    from puffpai import outside_services
    on = [r["id"] for r in outside_services.rows() if r["state"] == "on"]
    assert [s["id"] for s in pe.open_services()] == on


@pytest.mark.parametrize("goal", GOALS)
def test_proposal_items_same_for_every_goal_and_plain(goal, locked):
    items = pe.proposal_items(goal, goal.split(), vault=locked, model_url=LOCAL)
    baseline = pe.proposal_items("", [], vault=locked, model_url=LOCAL)
    assert items == baseline
    for it in items:
        assert set(it) == PROPOSAL_KEYS
        assert it["section"] == "private"
        assert len(it["label"]) <= 80
        assert _plain(it["label"]) and _plain(it["detail"])
        assert it["needs_yes"] is False  # nothing here downloads or reaches out


def test_proposal_items_new_vault_and_remote_model(tmp_path):
    v = Vault(vault_dir=str(tmp_path / "none"))
    items = {i["id"]: i for i in pe.proposal_items(
        "x", [], vault=v, model_url="http://10.0.0.5:11434")}
    assert items["private:vault"]["apply"] == {"kind": "vault", "ref": "create"}
    model = items["private:model_location"]
    assert model["needs_yes"] is True and model["outside_host"] == "10.0.0.5"


@pytest.mark.parametrize("kept,state,expect", [
    ("vault", "open", "put it in your vault."),
    ("inbox", "locked", "put it in your locked vault"),
    ("not_kept", "missing", "not set up a vault"),
    ("not_kept", "never_opened", "opened once with your PIN"),
])
def test_chat_notice_is_one_plain_sentence(kept, state, expect):
    notice = chat_privacy.plain_notice(["address"], kept, state)
    assert notice.startswith("I kept the home address out of my saved chats")
    assert expect in notice and _plain(notice)


def test_notice_names_several_kinds():
    n = chat_privacy.plain_notice(["phone", "school"], "inbox", "locked")
    assert "phone number and school name" in n and "them" in n


def test_plural_details_read_as_them():
    n = chat_privacy.plain_notice(["health.condition"], "inbox", "locked")
    assert "put them in your locked vault" in n
