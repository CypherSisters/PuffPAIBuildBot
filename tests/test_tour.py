"""The welcome tour: theme words from the goal, looks that fit, the pick that
sticks, and showing once per build (plus "Show me around again")."""

from __future__ import annotations

import pytest

from puffpai import api_server, build_manifest, plain_words, tour
from puffpai.looks import package as P
from puffpai.looks import store

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


@pytest.fixture
def built(monkeypatch):
    """A wedding build whose record was written at a known time."""
    man = {"generated_at": 1000.0, "build": {"name": "WeddingBot"}}
    monkeypatch.setattr(build_manifest, "read_manifest", lambda *a, **k: man)
    from puffpai import bot_identity
    monkeypatch.setattr(bot_identity, "bot_name", lambda *a, **k: "WeddingBot")
    monkeypatch.setattr(bot_identity, "bot_goal", lambda: "a wedding planner bot")
    return man


# -- theme words ---------------------------------------------------------------

def test_theme_words_start_with_the_goal_and_add_the_theme():
    got = tour.theme_words("a wedding planner bot")
    assert got[:2] == ["wedding", "planner"]
    assert {"elegant", "flower", "pastel"} <= set(got)
    assert "serious" in tour.theme_words("a finance and investing bot")
    assert "tech" in tour.theme_words("a 3D printing helper")


def test_a_goal_with_no_theme_keeps_only_its_own_words():
    assert tour.theme_of("a bot about quilts") is None
    assert tour.theme_words("a bot about quilts") == tour.goal_terms("a bot about quilts")


def test_kind_is_the_subject_without_the_role():
    assert tour.kind_of("a wedding planner bot") == "wedding planner"
    assert tour.kind_of("a 3D printing helper") == "3D printing"
    assert tour.kind_of("") == ""


@pytest.mark.parametrize("goal", ["a wedding planner bot", "a finance and investing bot",
                                  "a 3D printing helper", "a bot about quilts", ""])
def test_four_to_six_looks_each_wearing_a_background(goal):
    got = tour.tour_looks(goal)
    assert tour.MIN_LOOKS <= len(got["looks"]) <= tour.MAX_LOOKS
    ids = [row["id"] for row in got["looks"]]
    assert len(ids) == len(set(ids))
    for row in got["looks"]:
        assert row["background"] in P.BACKGROUNDS
        assert row["background_label"]
        assert set(row["vars"]) == {"day", "night"}


def test_wedding_looks_fit_the_theme():
    got = tour.tour_looks("a wedding planner bot")
    assert got["themed"] is True
    ids = {row["id"] for row in got["looks"]}
    assert ids & {"tweakcn:elegant-luxury", "tweakcn:pastel-dreams", "tweakcn:violet-bloom",
                  "tweakcn:quantum-rose"}
    assert "tweakcn:retro-arcade" not in ids


def test_a_wedding_gets_petals_and_no_night_sky():
    """Cosmic Night with stars was offered for a
    wedding, and only one look fit. The theme's look words come first."""
    goal = ("I want to build a wedding planner bot. My brother just got engaged and "
            "there is a lot to do and plan. I'll need to be able to track themes from "
            "pictures, come up with ideas, find wedding vendors and materials to rent, "
            "plan the budget, and do a whole wedding plan.")
    rows = tour.tour_looks(goal)["looks"]
    assert len(rows) >= tour.MIN_LOOKS
    assert "petals" in {r["background"] for r in rows}
    assert "stars" not in {r["background"] for r in rows}
    assert "tweakcn:cosmic-night" not in {r["id"] for r in rows}
    party = tour.tour_looks("a birthday party bot")["looks"]
    assert "confetti" in {r["background"] for r in party}


def test_the_looks_line_names_the_kind():
    assert tour.looks_line("wedding planner", True) == \
        "I'm your wedding planner bot, so I picked looks that fit my theme."
    assert tour.looks_line("investing", False).startswith("I'm your investing bot. ")
    assert tour.looks_line("", False) == "Here are some looks to start with."


def _sentences(got: dict) -> list[str]:
    out = [v for v in got.values() if isinstance(v, str)]
    for step in got.get("walk", []):
        out += [step["title"], step["line"]]
    return out


def test_every_sentence_is_plain():
    for text in _sentences(tour.words("WeddingBot", "a wedding planner bot",
                                      thing="Guest list", thing_page="/dashboard/things/x")):
        assert not plain_words.check(text), text
        assert "—" not in text and "door" not in text.lower() and "beta" not in text.lower()


def test_the_walk_covers_every_part_for_any_bot():
    steps = tour.walk("GardenBot")
    assert [s["id"] for s in steps] == ["home", "thing", "chat", "find", "own", "parts",
                                        "vault", "brain", "expert", "looks", "settings"]
    for step in steps:
        assert step["target"] in tour.WALK_TARGETS
        # A spot step names one marked control; no other step carries a spot.
        assert (step["target"] == "spot") == ("spot" in step), step["id"]
        if step["target"] == "spot":
            assert step["spot"] in tour.WALK_SPOTS and step["href"], step["id"]
        assert step["href"] == "" or step["href"].startswith("/dashboard")
        assert len(step["line"]) <= 260, step["id"]       # short per step
    assert "GardenBot" in steps[0]["line"]
    thing = tour.walk("GardenBot", "Seed box", "/dashboard/things/seeds")[1]
    assert "like Seed box" in thing["line"] and thing["href"] == "/dashboard/things/seeds"
    for button in ("Try it", "Ask", "On my home page", "Remove"):
        assert button in thing["line"]
    parts = next(s for s in steps if s["id"] == "parts")
    assert "Nothing leaves your computer without your consent." in parts["line"]
    # Nothing about one kind of bot.
    for word in ("music", "wedding", "garden", "song"):
        assert all(word not in s["line"].lower() for s in tour.walk("Bot"))


def test_the_walk_shows_where_to_go_for_the_key_jobs():
    steps = {s["id"]: s for s in tour.walk("GardenBot")}
    where = {i: (s["href"], s.get("spot")) for i, s in steps.items()}
    assert where["find"] == ("/dashboard/add", "add-search")
    assert where["own"] == ("/dashboard/developer", "developer-add-own")
    assert where["vault"] == ("/dashboard/vault", "vault-unlock")
    assert where["brain"] == ("/dashboard/knowledge", "knowledge-upload")
    assert where["expert"] == ("/dashboard/knowledge", "knowledge-books")
    assert where["settings"] == ("/dashboard/settings", "settings-toolsmith")
    # The pages' own button words, so she can find them.
    for label in ("GitHub", "Run it first", "Add to the list", "until you say yes"):
        assert label in steps["find"]["line"], label
    assert "Add your own" in steps["own"]["line"]
    assert "PIN" in steps["vault"]["line"] and "Unlock vault" in steps["vault"]["line"]
    for label in ("Your files", "Add files", "Link a folder", "vault"):
        assert label in steps["brain"]["line"], label
    assert steps["brain"]["title"] == "GardenBot's offline brain"
    for label in ("Find more knowledge", "Look", "books"):
        assert label in steps["expert"]["line"], label
    assert "Toolsmith" in steps["settings"]["line"] and "Show me around" in steps["settings"]["line"]
    assert 9 <= len(steps) <= 11


def test_the_tour_speaks_as_the_bot_from_its_personality():
    me = {"name": "Bloom", "kind": "wedding helper",
          "intro": "Hi, I'm Bloom, your wedding helper. Let me show you around."}
    got = tour.words("WeddingBot", "a wedding planner bot", me)
    assert got["bot_name"] == "Bloom" and got["kind"] == "wedding"
    assert got["intro"] == me["intro"]
    assert got["things_title"] == "Here is what I just learned"
    assert got["looks_title"] == "Let's pick my look."
    assert got["looks_line"] == "I'm your wedding bot, so I picked looks that fit my theme."
    # no personality yet: the build's own name and the goal's kind
    plain = tour.words("WeddingBot", "a wedding planner bot")
    assert plain["bot_name"] == "WeddingBot" and plain["kind"] == "wedding planner"
    assert plain["intro"] == ""


@pytest.mark.parametrize("goal, kind, example", [
    ("Build me a 3D printing helper. Find models to print, keep track of our filament "
     "and print settings, help when a print fails.", "3D printing",
     "For example: find models to print"),
    ("Help me plan our garden and keep the seeds sorted", "garden",
     "For example: plan our garden"),
    ("A bot about quilts", "quilts", "For example: help with quilts"),
    ("", "", tour.FIND_EXAMPLE),
])
def test_the_find_more_example_comes_from_this_bot(goal, kind, example):
    assert tour.find_example(goal, kind) == example
    assert not plain_words.check(example) and "—" not in example


# -- tour state ----------------------------------------------------------------

def test_no_build_no_tour(monkeypatch):
    monkeypatch.setattr(build_manifest, "read_manifest", lambda *a, **k: None)
    assert tour.should_show() is False


def test_shows_once_per_build_and_again_on_request(built):
    assert tour.should_show() is True
    tour.mark_done()
    assert tour.should_show() is False
    tour.show_again()
    assert tour.should_show() is True
    tour.mark_done()
    assert tour.should_show() is False
    built["generated_at"] = 2000.0          # a new build
    assert tour.should_show() is True


# -- routes ----------------------------------------------------------------------

def test_every_tour_route_needs_the_key(client):
    for method, path in (("get", "/tour"), ("get", "/tour/looks"), ("post", "/tour/look"),
                         ("post", "/tour/done"), ("post", "/tour/again")):
        assert getattr(client, method)(path).status_code == 401, path


def test_the_tour_over_the_routes(client, built):
    body = client.get("/tour", headers=AUTH).get_json()
    assert body["show"] is True and body["built"] is True
    assert body["bot_name"] == "WeddingBot"
    assert body["things_title"] == "Here is what I just learned"
    assert "wedding planner bot" in body["looks_line"]
    assert body["walk"][0]["id"] == "home" and body["walk_offer"]

    looks = client.get("/tour/looks", headers=AUTH).get_json()["looks"]
    pick = looks[0]
    kept = client.post("/tour/look", headers=AUTH,
                       json={"id": pick["id"], "background": pick["background"]}).get_json()
    assert kept["ok"] and kept["current"]["id"] == pick["id"]
    # The background she picked with the look stays, now and for later looks.
    assert kept["background"] == pick["background"]
    assert kept["chosen_background"] == pick["background"]
    assert store.current_background() == pick["background"]
    assert kept["can_undo"] is True

    assert client.post("/tour/done", headers=AUTH).get_json()["show"] is False
    assert client.get("/tour", headers=AUTH).get_json()["show"] is False
    assert client.post("/tour/again", headers=AUTH).get_json()["show"] is True


def test_keeping_a_look_takes_ids_only(client, built):
    for bad in ({"id": "tweakcn:nope", "background": "soft"},
                {"id": "tweakcn:nature", "background": "url(x)"},
                {"id": None, "background": None}, {}):
        r = client.post("/tour/look", headers=AUTH, json=bad)
        assert r.status_code == 400
        assert not plain_words.check(r.get_json()["message"])
