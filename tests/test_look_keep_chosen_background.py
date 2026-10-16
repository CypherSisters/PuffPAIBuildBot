"""(Design 3c) "Keep it" keeps the background she picked.

Before this, the owner chose Stars, tried a look on, pressed "Keep it", and the
clouds came back: ``store.use()`` wrote the look with its own background and
her choice lived only in a field that is used with the bot's own look.
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import api_server, plain_words
from puffpai.looks import find as F
from puffpai.looks import package as P
from puffpai.looks import store

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
CATALOG_LOOK = "tweakcn:kodama-grove"      # its own background is "soft"


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def _current_file() -> dict:
    from puffpai.config import get_state_dir
    with open(os.path.join(get_state_dir(), "looks", "current.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _assert_stars(state: dict) -> None:
    assert state["background"] == "stars"
    assert state["chosen_background"] == "stars"
    assert state["vars"]["night"]["--puff-stars-more"] == "1"
    assert state["vars"]["night"]["--imgPage"] == "0"
    assert state["vars"]["day"]["--imgPage"] == "0"


def test_a_catalog_look_kept_after_stars_wears_stars():
    assert store.set_background("stars")["ok"]
    assert _current_file()["chosen_background"] == "stars"
    got = store.use(CATALOG_LOOK)
    assert got["ok"] and got["changed"]
    _assert_stars(store.state())
    assert _current_file()["look"]["background"] == "stars"


def test_a_made_look_kept_after_stars_wears_stars():
    made = F.made_from_colour_words("forest green")
    assert made is not None
    store.remember_found("forest green", [made])
    store.set_background("stars")
    assert store.use(made["id"])["ok"]
    _assert_stars(store.state())


def test_undo_restores_the_choice_and_reset_clears_it():
    store.set_background("stars")                   # own look, stars
    store.use(CATALOG_LOOK)                         # the look, stars
    back = store.undo()
    assert back["ok"] and store.current() is None
    assert store.state()["background"] == "stars"
    assert store.state()["chosen_background"] == "stars"
    store.undo()                                    # before stars: the clouds
    assert store.state()["background"] == "clouds"
    assert store.state()["chosen_background"] is None
    store.set_background("plain")
    store.use(CATALOG_LOOK)
    assert store.state()["background"] == "plain"
    assert store.reset()["changed"]
    assert store.state()["chosen_background"] is None
    assert store.state()["background"] == "clouds"
    # A look used after the reset wears its own background again.
    store.use(CATALOG_LOOK)
    assert store.state()["background"] == "soft"


def test_history_rows_record_the_choice():
    store.set_background("stars")
    store.use(CATALOG_LOOK)
    from puffpai.config import get_state_dir
    with open(os.path.join(get_state_dir(), "looks", "history.json"), encoding="utf-8") as fh:
        rows = json.load(fh)
    assert rows[-1]["chosen_background"] == "stars"


def test_a_look_that_cannot_wear_it_keeps_its_own_and_says_so(monkeypatch):
    store.set_background("stars")

    def refuse(pkg, background):
        raise P.LookError("too hard to read")

    monkeypatch.setattr(P, "with_background", refuse)
    got = store.use(CATALOG_LOOK)
    assert got["ok"]
    assert ("That look does not read well with stars, so it keeps its own background."
            in got["message"])
    assert store.state()["background"] == "soft"
    assert store.state()["chosen_background"] == "stars"    # her choice stays
    assert not plain_words.check(got["message"])


def test_try_on_equals_keep(client):
    assert client.post("/looks/background", headers=AUTH,
                       json={"background": "stars"}).status_code == 200
    found = client.post("/looks/find", headers=AUTH,
                        json={"words": "dinosaur jungle colours"}).get_json()
    assert found["chosen_background"] == "stars"
    first = found["looks"][0]
    assert first["background"] == "stars"
    kept = client.post("/looks/use", headers=AUTH, json={"id": first["id"]}).get_json()
    assert kept["ok"]
    assert kept["vars"] == first["vars"]                   # what she tried is what she kept
    assert kept["chosen_background"] == "stars"


def test_without_a_choice_try_on_still_equals_keep(client):
    found = client.post("/looks/find", headers=AUTH,
                        json={"words": "dinosaur jungle colours"}).get_json()
    first = found["looks"][0]
    kept = client.post("/looks/use", headers=AUTH, json={"id": first["id"]}).get_json()
    assert kept["vars"] == first["vars"]
    assert kept["chosen_background"] is None


GOOD = ('{"name": "Night Sky", "day": {"bg": "#EEF2FF", "card": "#ffffff", '
        '"text": "#1e1b4b", "accent": "#4f46e5", "accent2": "#f59e0b"}, '
        '"night": {"bg": "#0b1026", "card": "#141a3a", "text": "#e0e7ff", '
        '"accent": "#818cf8", "accent2": "#fbbf24"}, "background": "clouds"}')


def test_the_model_background_is_taken_only_when_her_words_name_one():
    own = F.read_model_palette(GOOD, words="deep blue and gold")
    assert own is not None and own["background"] == P.default_background(own)
    named = F.read_model_palette(GOOD, words="deep blue with clouds")
    assert named is not None and named["background"] == "clouds"
    assert F.names_background("a plain page") == "plain"
    assert F.names_background("forest green") is None


def test_the_brain_path_passes_her_words(monkeypatch):
    got = F.made_by_brain("deep blue and gold", lambda prompt: GOOD)
    assert got is not None and got["background"] != "clouds"
