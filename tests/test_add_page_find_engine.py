"""The Add page and chat "add ..." use the same needs + find_parts engine."""
from __future__ import annotations

import pytest

from puffpai import add_more
from puffpai.wizard import find_parts, needs


def _item(section: str, ident: str, need: str = "j1") -> dict:
    return {"id": f"{section}:{ident}", "section": section, "label": f"Thing {ident}",
            "detail": "A tool that does the job. Free to use and change (MIT).",
            "size_bytes": 1024, "outside_host": "codeload.github.com", "default_on": False,
            "needs_yes": True, "apply": {"kind": "code_project", "ref": f"github:a/{ident}"},
            "need": need, "why": "helps with: the job", "might_help": True}


@pytest.fixture
def engine(monkeypatch):
    add_more.reset()
    calls: list[dict] = []

    def plan_items(doc, *, goal, online, allow_send, cfg, on_line=None):
        calls.append({"needs": doc, "goal": goal, "online": online, "allow_send": allow_send})
        return {"items": [_item("apps_code", "timer"), _item("pictures", "pics")],
                "notes": {"j1": ["Old timer (github.com/a/old, needs its own server)"]},
                "not_reached": [], "similar": [],
                "sends": [{"phrase": "pomodoro timer", "source": "GitHub"}] if online else [],
                "left_out": 0}
    monkeypatch.setattr(find_parts, "plan_items", plan_items)
    monkeypatch.setattr(find_parts, "found_for", lambda item_id: {"id": item_id, "server": 1})
    monkeypatch.setattr(add_more, "online_allowed", lambda: True)
    yield calls
    add_more.reset()


def test_her_words_become_jobs_and_apps_come_back_grouped(engine):
    session = add_more.find("I need to track study hours and drill flashcards", via="browser",
                            client="c1")
    assert [j["name"] for j in session["jobs"]] == ["Track study hours", "Drill flashcards"]
    sections = [i["section"] for i in session["items"]]
    assert "apps_code" in sections and "pictures" in sections
    view = add_more.public(session, "browser")
    groups = {g["section"]: g for g in view["groups"]}
    assert groups["apps_code"]["heading"] == "Apps and code"
    assert groups["apps_code"]["items"][0]["need"] == "j1"
    assert view["jobs"] == session["jobs"]
    # the server-side record is kept for the applier, never read from the page
    assert session["kept"]["apps_code:timer"] == {"id": "apps_code:timer", "server": 1}


def test_this_computer_first_and_online_only_when_asked(engine):
    add_more.find("a pomodoro timer", via="browser", client="c1")
    assert engine[-1]["online"] is False and engine[-1]["allow_send"] is False
    session = add_more.find("a pomodoro timer", via="browser", client="c1", online=True)
    assert engine[-1]["online"] is True and engine[-1]["allow_send"] is True
    assert "pomodoro timer" in session["sends"]
    assert session["searched_online"] is True


def test_the_offer_to_search_online_lists_only_screened_phrases(engine):
    session = add_more.find("a pomodoro timer for my daughter Emma", via="browser",
                            client="c1")
    sends = session["sends"]
    assert "emma" not in " ".join(sends).lower()
    assert "daughter" not in " ".join(sends).lower()
    if session["online_possible"]:
        line = add_more.online_line(session, "browser")
        assert "It sends only these words" in line


def test_chat_never_searches_online_by_itself(engine):
    add_more.find("a pomodoro timer", via="chat", client="c2")
    assert engine[-1]["online"] is False


def test_a_bot_that_stays_offline_never_searches_code_online(engine, monkeypatch):
    monkeypatch.setattr(add_more, "online_allowed", lambda: False)
    add_more.find("a pomodoro timer", via="browser", client="c1", online=True)
    assert engine[-1]["online"] is False


def test_notes_are_said_as_look_at_it_yourself(engine):
    session = add_more.find("a pomodoro timer", via="browser", client="c1")
    assert any(n.startswith("Also found, look at it yourself: Old timer") for n in session["notes"])


def test_home_page_requests_search_apps_and_pictures_too():
    assert {"apps_code", "pictures"} <= set(add_more.SHOWN_SECTIONS)


def test_needs_for_uses_the_rules_only(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the Add page never asks the brain")
    from puffpai.wizard import needs_llm
    monkeypatch.setattr(needs_llm, "ask", boom)
    doc = add_more.needs_for("a metronome and a tuner")
    assert doc["schema"] == "needs/v1" and doc["prompt"] == "rules"
    assert [j["name"] for j in doc["jobs"]] == ["Metronome and a tuner"]


def test_without_the_engine_nothing_breaks(monkeypatch):
    add_more.reset()
    monkeypatch.setattr(add_more, "needs_for", lambda words: None)
    session = add_more.find("a pomodoro timer", via="browser", client="c1")
    assert not [i for i in session["items"] if i["section"] == "apps_code"]
    add_more.reset()


def test_what_she_takes_is_logged_without_her_words(engine, monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    session = add_more.find("a pomodoro timer for my study group", via="browser", client="c1")
    session["kept"]["apps_code:timer"] = {"meaning": {"blurb": 0.6, "does": 0.5, "fit": 0.55,
                                                      "state": "might_help"},
                                          "trust": {"path": "stars"}}
    add_more._log_choices(session, {"apps_code:timer"})
    import json
    rows = [json.loads(x) for x in (tmp_path / "logs" / "plan_choices.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    by_id = {r["item_id"]: r for r in rows}
    assert by_id["apps_code:timer"]["owner_on"] is True
    assert by_id["apps_code:timer"]["fit"] == 0.55
    assert by_id["pictures:pics"]["owner_on"] is False
    assert "pomodoro" not in json.dumps(rows) and "study group" not in json.dumps(rows)


# -- Home Assistant lines and meaning ------------------

def _ha_file(on: bool) -> None:
    import json
    import os
    from puffpai.config import get_config
    cfg = get_config(_force_reload=True)
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    path = os.path.join(cfg.CONFIG_DIR, "home_assistant_config.json")
    if on:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"enabled": True, "url": "http://192.168.1.20:8123",
                       "token": "t" * 40}, fh)
    elif os.path.exists(path):
        os.remove(path)


@pytest.mark.parametrize("words", ["space pictures and my smart telescope",
                                   "a robot vacuum schedule", "my thermostat and lights"])
def test_no_home_assistant_line_when_home_assistant_is_not_in_use(engine, words):
    _ha_file(False)
    assert add_more.home_assistant_in_use() is False
    session = add_more.find(words, via="browser", client="c1")
    text = " ".join(f"{i.get('label')} {i.get('detail')}" for i in session["items"])
    assert "if you have Home Assistant" not in text
    assert not [i for i in session["items"] if i["section"] == "devices"]


def test_home_assistant_lines_only_once_it_is_set_up(engine):
    _ha_file(True)
    try:
        assert add_more.home_assistant_in_use() is True
        session = add_more.find("space pictures and my smart telescope", via="browser",
                                client="c1")
        assert [i for i in session["items"] if i["section"] == "devices"]
    finally:
        _ha_file(False)


def test_meaning_leaves_out_a_line_that_shares_only_a_word(engine, monkeypatch):
    """A live card or gadget line must fit one of her jobs by meaning, not by a
    shared word. Fake vectors: the line about games is far from her job."""
    import numpy as np
    from puffpai.catalogs import meaning

    def embed(texts):
        out = np.zeros((len(texts), 2), dtype=np.float32)
        for n, t in enumerate(texts):
            out[n] = (0.0, 1.0) if "Epic Games" in t else (1.0, 0.0)
        return out
    monkeypatch.setattr(meaning, "helper_state", lambda: "ready")
    monkeypatch.setattr(meaning, "embed", embed)
    ctx = add_more.AddContext(words="practice games", terms=["practice", "games"],
                              via="browser", online=False, bot_name="Rexy",
                              needs={"kind": "chess coach",
                                     "jobs": [{"id": "j1", "name": "Play practice games",
                                               "does": "Plays a game of chess with you."}]})
    assert add_more._meaning_fits(ctx, ["Your games, if you have Home Assistant (Epic Games "
                                        "Store)", "A chess board to play practice games"]
                                  ) == [False, True]
    # With no reading helper, or no jobs, the word match decides as before.
    monkeypatch.setattr(meaning, "helper_state", lambda: "missing")
    assert add_more._meaning_fits(ctx, ["Epic Games Store"]) == [True]
