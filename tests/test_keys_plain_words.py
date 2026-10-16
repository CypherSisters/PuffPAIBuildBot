"""Every line an owner reads about keys and search passes the ONE banned list,
and the add-more lines are the same for every kind of bot (six strangers)."""

from __future__ import annotations

import pytest

from keys_support import reset_keys, set_posture
from puffpai import keys_api, outside_services, plain_words, web_search
from puffpai.security import egress_doors, keys
from puffpai.wizard import proposal

#: Internal words of this area that must never reach an owner either.
ALSO = ("handle", "broker", "credential", "placement", "reserved", "sdk",
        "__puffpai", "key_on", "keyed_get", "brave_search", "marginalia_search")

GOALS = {
    "G1": "I want a space bot that knows everything about space, shows me cool "
          "space stuff, and works with my smart telescope",
    "G2": "I love dinosaurs, I want a bot that knows every dinosaur",
    "G3": "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos",
    "G4": "A garden helper for my vegetable beds in Ohio",
    "G5": "A knitting and crochet buddy with patterns and tips",
    "G6": "A music bot that knows bands and albums",
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    reset_keys()
    set_posture("enumerated")
    yield
    reset_keys()


def _owner_lines() -> list[str]:
    lines = [keys_api.PAGE_LINE, keys_api.TITLE, web_search.KEY_PASTE_REPLY,
             web_search.KEY_PASTE_PLACEHOLDER]
    for sid, row in keys.catalog().items():
        for surface in ("browser", "chat"):
            how = keys.walk_through(sid, surface, age=10)
            lines += [how["line"], *how["steps"], how["cost_note"],
                      how.get("grown_up_line", ""), how.get("label", "")]
        for state in ("works", "missing", "locked", "stopped_working",
                      "paused_limit", "switched_off", "unknown_count"):
            lines.append(keys._state_line(state, row, "browser"))  # noqa: SLF001
        lines += [row["name"], row["for"], row["cost_note"],
                  row.get("results_note", "")]
        lines.append(keys.confirm_sentence(sid, background=True))
        lines.append(keys.confirm_sentence(sid, background=False))
        lines.append(keys.apply_item({"kind": "key", "ref": sid})["why"])
    for sid in ("web_search", "brave_search", "marginalia_search"):
        d = egress_doors.door(sid)
        lines += [d.purpose, d.data_sent, d.settings_hint]
        r = outside_services.row(sid)
        lines += [r["name"], r["for"], r["what_leaves"], r["state_line"]]
    for r in web_search.ability_rows():
        lines.append(r["label"])
        if r.get("action"):
            lines += list(plain_words.renderings(r["action"]).values())
    return [line for line in lines if line]


def test_every_owner_line_is_plain():
    problems = []
    for line in _owner_lines():
        problems += plain_words.check(line, also=ALSO)
    # The terminal wording names the key by its id on purpose (it is what
    # the owner types), so ids are only checked on the other surfaces.
    assert [p for p in problems
            if "puffpai keys add" not in p and "puffpai keys" not in p] == []


def test_the_terminal_wording_is_its_own_and_equal():
    browser = keys.walk_through("brave_search", "browser")["line"]
    terminal = keys.walk_through("brave_search", "terminal")["line"]
    chat = keys.walk_through("brave_search", "chat")["line"]
    assert browser.endswith("3) paste the key here.")
    assert terminal.endswith("3) run: puffpai keys add brave_search and paste "
                             "the key when it asks.")
    assert "Never paste a key into the chat" in chat
    head = "Brave Search needs a free key: 1) open "
    assert browser.startswith(head) and terminal.startswith(head)


@pytest.mark.parametrize("who", sorted(GOALS))
def test_the_add_more_lines_are_the_same_for_every_stranger(who):
    items = keys.proposal_items(GOALS[who], [], age=10 if who in ("G1", "G2")
                                else None)
    clean = [proposal.validate_item(i) for i in items]
    assert [i["apply"] for i in clean] == [
        {"kind": "key", "ref": "brave_search"},
        {"kind": "key", "ref": "marginalia_search"}]
    assert all(i["default_on"] is False and i["needs_yes"] for i in clean)
    for item in clean:
        assert not plain_words.check(item["label"]), item["label"]
    if who in ("G1", "G2"):
        assert clean[0]["detail"].startswith("Ask a grown-up to help")
    # Nothing about the goal's topic leaks into a key line.
    topic = GOALS[who].lower()
    for item in clean:
        for word in ("dinosaur", "telescope", "honda", "garden", "crochet",
                     "albums"):
            if word in topic:
                assert word not in item["label"].lower()


def test_personal_use_only_is_labelled():
    items = {i["apply"]["ref"]: i for i in keys.proposal_items("", [])}
    assert "(personal use only)" in items["marginalia_search"]["label"]
    assert "personal use only" not in items["brave_search"]["label"]


def test_apply_never_takes_a_key_and_gives_the_walk_through():
    got = keys.apply_item({"kind": "key", "ref": "brave_search"})
    assert got["ok"] is False and got["needs_key"] == "brave_search"
    assert got["why"].startswith("Brave Search needs a free key: 1) open ")
    assert keys.apply_item({"kind": "key", "ref": "nope"})["ok"] is False
    assert keys.apply_item({"kind": "routine", "ref": "x"})["ok"] is False


def test_the_wizard_summary_offers_keyless_search_as_its_own_line():
    """The outside-services line the summary builds from the list: the
    keyless web search now has a real service behind it (Wikipedia)."""
    door = egress_doors.door("web_search")
    assert door.posture_switch == "web_search"
    assert door.hosts == ("en.wikipedia.org", "simple.wikipedia.org")
    assert door.default is False
