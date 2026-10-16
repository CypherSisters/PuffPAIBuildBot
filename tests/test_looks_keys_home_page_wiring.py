"""Wiring: looks and sign-up keys in the one add loop and the wizard
summary, the home page's links and goal-fit questions, one switch place for
the book sites, and a home-network bot that says so before it fetches.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import types

import pytest

from add_more_support import built, guard_network, item_numbered
from puffpai import add_intent, add_more, front_page
from puffpai import plain_words as pw
from puffpai.catalogs import goal_words
from puffpai.security import consent_tickets


@pytest.fixture(autouse=True)
def _fresh():
    add_more.reset()
    yield
    add_more.reset()


# -- one loop: looks and keys are registered into add-more ----------------------

def test_looks_and_keys_are_sections_appliers_and_a_remover_of_the_one_loop():
    sections = dict(add_more.sections())
    assert sections["looks"] == "How it looks"
    assert sections["outside_services"] == "Sign-up keys"
    assert add_more.can_apply("theme") and add_more.can_apply("key")
    assert "look" in add_more._REMOVERS  # noqa: SLF001


def test_a_topic_search_never_offers_a_look_or_a_key(monkeypatch):
    built()
    guard_network(monkeypatch)
    session = add_more.find("dinosaur fossil maps", via="terminal", client="t")
    got = {i["section"] for i in session["items"]}
    assert "looks" not in got and "outside_services" not in got
    assert "knowledge" in got


@pytest.mark.parametrize("message", ["add a jungle look to Rexy",
                                     "make it look like a jungle",
                                     "change your colours to jungle green"])
def test_chat_look_requests_go_to_looks_through_add_more(message, monkeypatch):
    built()
    guard_network(monkeypatch)
    found = add_intent.detect(message, bot_name="Rexy")
    assert found and found["shape"] == "look", found
    here = consent_tickets.terminal_device_id()
    reply = add_more.chat_turn(message, surface="terminal", client=here,
                               device=here, can_approve=True)
    assert reply and "How it looks" in reply, reply
    assert "What it will know" not in reply  # no books about jungles
    assert not pw.check(reply), pw.check(reply)


def test_a_look_is_added_through_the_yes_and_taken_out_again(monkeypatch):
    from puffpai.looks import store
    built()
    guard_network(monkeypatch)
    device = consent_tickets.terminal_device_id()
    session = add_more.find("dinosaur jungle colours", via="terminal",
                            client=device)
    look = item_numbered(add_more.public(session, "browser"), "looks")
    got = add_more.approve(session["id"], [look["id"]], client=device,
                           device=device, surface="terminal")
    assert got[0]["status"] == "added", got
    assert store.current()["id"] == look["id"].split("looks:theme:", 1)[1]
    rows = add_more.added()
    assert rows[-1]["kind"] == "theme"
    line = add_more.take_out(rows[-1]["n"])
    assert "own look" in line and store.current() is None


def test_web_search_words_offer_the_key_lines_and_a_yes_gives_the_steps(monkeypatch):
    built()
    guard_network(monkeypatch)
    device = consent_tickets.terminal_device_id()
    session = add_more.find("web search", via="terminal", client=device)
    public = add_more.public(session, "browser")
    assert [g["section"] for g in public["groups"]] == ["outside_services"]
    key = item_numbered(public, "outside_services")
    assert "needs a free key" in key["label"]
    got = add_more.approve(session["id"], [key["id"]], client=device,
                           device=device, surface="terminal")
    assert got[0]["status"] == "held"
    assert "puffpai keys add" in got[0]["line"]  # the terminal's own steps
    assert add_more.added() == []  # nothing was added: a key is never taken here
    assert not pw.check(add_more.render_text(session, "terminal"))


def test_search_the_web_never_reaches_add_more(monkeypatch):
    built()
    guard_network(monkeypatch)
    assert add_intent.detect("search the web for dinosaur fossils",
                             bot_name="Rexy") is None
    from puffpai import api_server
    assert api_server._maybe_add_more_answer(  # noqa: SLF001
        "search the web for dinosaur fossils") is None


# -- the wizard summary: the same providers ----------------------------------------

def _sctx(goal, internet=True):
    from puffpai.wizard import proposal, wording
    return proposal.SummaryContext(
        goal=goal, terms=proposal.goal_terms(goal), name="Rexy",
        mode=wording.STANDARD, shared={}, previews={},
        args=types.SimpleNamespace(), cfg=None, internet=internet)


def test_the_summary_gets_a_look_line_only_for_a_word_match():
    from puffpai.wizard import factory, proposal
    assert "looks" in proposal.SECTIONS and "looks" in proposal.OPTIONAL_SECTIONS
    space = factory.looks_items(_sctx("a space bot for my telescope"))
    assert len(space) == 1 and space[0]["default_on"] is True
    assert factory.looks_items(_sctx("help me keep my car running")) == []


def test_the_summary_gets_needs_a_free_key_lines_off_by_default():
    from puffpai.wizard import factory
    lines = factory.key_items(_sctx("a space bot"))
    assert lines and all(i["default_on"] is False for i in lines)
    assert all("needs a free key" in i["label"] for i in lines)
    assert factory.key_items(_sctx("a space bot", internet=False)) == []
    for item in lines:
        assert not pw.check(item["label"] + "\n" + item["detail"])


def test_looks_good_applies_a_look_and_turns_a_key_line_into_its_steps():
    from puffpai.looks import store
    from puffpai.security import keys
    from puffpai.wizard import factory, proposal
    items = (factory.looks_items(_sctx("a space bot"))
             + factory.key_items(_sctx("a space bot"))[:1])
    state = {"items": [dict(i, on=True) for i in items]}
    ctx = types.SimpleNamespace(shared={"summary": state, "interview": {}},
                                remote=False, _sections={})
    real = proposal.approved_items
    proposal.approved_items = lambda s: [i for i in s["items"] if i["on"]]
    try:
        got = factory.apply_approved(ctx)
    finally:
        proposal.approved_items = real
    rows = {r["kind"]: r for r in got["shared"]["factory"]["items"]}
    assert rows["theme"]["ok"] and store.current() is not None
    assert rows["key"]["ok"] is False
    gap = next(g for g in got["gaps"] if "needs a free key" in g["why"])
    assert "puffpai keys add" in gap["why"]
    sid = items[-1]["apply"]["ref"]
    assert "You chose this when you built your bot" in \
        keys.key_status(sid)["state_line"]
    assert "Sign-up keys page" in keys.walk_through(sid, "page")["line"]


# -- the home page ------------------------------------------------------------------

def test_the_home_page_links_to_add_looks_keys_and_what_leaves():
    page = front_page.front()
    hrefs = {page[k]["href"] for k in ("add", "looks", "keys", "outside")}
    assert hrefs == {"/dashboard/add", "/dashboard/looks", "/dashboard/keys",
                     "/dashboard/outside"}
    assert not pw.check(front_page.owner_text(page))


def test_a_question_must_be_about_the_goal():
    goal = "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos"
    rows = {"honda": [{"title": "Toyota Corolla", "relevance": 0.9,
                       "lead": "The Toyota Corolla is a series of compact cars."},
                      {"title": "Honda Civic", "relevance": 0.9,
                       "lead": "The Honda Civic is a series of cars."}]}

    def hits(q):
        q = q.lower()
        for key, found in rows.items():
            if key in q:
                return found
        return [r for r in rows["honda"] if r["title"].lower() in q]

    got = [q["text"] for q in front_page.book_questions(goal, hits=hits)]
    # Design 3b.3: offers.goal_terms keeps only
    # goal_words.search_terms, so a brand she typed ("Honda Civic", held until
    # she ticks it) no longer makes a question; nothing off-topic does either.
    assert got == []
    assert not any("Corolla" in q for q in got)


@pytest.mark.parametrize("request_words,text,subjects,fits", [
    ("car parts and recalls",
     "Langley Memoir on Mechanical Flight, Parts I and II", [], False),
    ("car parts and recalls", "NHTSA recalls. Safety recalls for a car", [], True),
    ("dinosaurs", "Tyrannosaurus is a genus of dinosaur", [], True),
    ("dinosaur fossil maps",
     "Live: OpenStreetMap place search", ["map", "place", "geography"], False),
    ("space pictures", "Live: NASA Image and Video Library. Photos of planets",
     ["space", "picture"], True),
    ("my 2014 Honda Civic",
     "Toyota Corolla. The Toyota Corolla is a series of compact cars", [], False),
])
def test_fits_request(request_words, text, subjects, fits):
    assert goal_words.fits_request(request_words, text, subjects=subjects) is fits


def test_add_more_drops_a_book_that_is_not_about_the_words(monkeypatch):
    built()
    guard_network(monkeypatch)
    session = add_more.find("car parts and recalls", via="terminal", client="t")
    labels = " ".join(i["label"] for i in session["items"])
    assert "Langley" not in labels


# -- one switch place for the book sites ------------------------------------------

def test_book_sites_are_not_dead_switches(capsys):
    from puffpai import cli
    from puffpai.security import egress_doors
    rows = {d["id"]: d for d in egress_doors.door_states()["doors"]}
    for sid in ("wikipedia_books", "gutenberg_books", "openstax_books"):
        assert rows[sid]["config_flag"] is None
        assert "puffpai outside" in rows[sid]["switched_in"]
    assert cli.main(["services", "on", "wikipedia_books"]) == 1
    out = capsys.readouterr().out
    assert "puffpai outside on wikipedia_books" in out
    cli.main(["services"])
    assert "puffpai outside" in capsys.readouterr().out


# -- a home-network bot says so before it fetches a book it was told to ----------

def test_a_switched_off_book_site_keeps_the_order_waiting(monkeypatch):
    from puffpai.knowledge import find_more, progress, text_books
    from puffpai.security import outside_switches
    built("island")
    guard_network(monkeypatch)
    find_more.order_book("wikipedia:en:dinosaur", "Wikipedia about dinosaur")
    outside_switches.set_switch("wikipedia_books", False, by="owner")
    got = find_more.run_orders()
    assert got == [{"id": "wikipedia:en:dinosaur", "ok": False,
                    "reason": "switched_off"}]
    assert "wikipedia:en:dinosaur" in find_more.pending_orders()
    rec = progress.read(text_books.learn_id("wikipedia:en:dinosaur"))
    line = progress.plain_lines(rec, "terminal")[0]
    assert "switched off" in line and not pw.check(line)
    assert find_more.orders_note("Rexy") == ""


def test_a_home_network_bot_says_it_will_fetch_the_book(monkeypatch):
    from puffpai.knowledge import find_more
    built("island")
    find_more.order_book("wikipedia:en:dinosaur", "Wikipedia about dinosaur")
    note = find_more.orders_note("Rexy")
    assert "Rexy stays on your home network" in note
    assert "en.wikipedia.org" in note and not pw.check(note)


def test_a_bot_that_reaches_nothing_outside_never_fetches(monkeypatch):
    from puffpai.knowledge import find_more
    built("sealed")
    guard_network(monkeypatch)
    find_more.order_book("wikipedia:en:dinosaur", "Wikipedia about dinosaur")
    assert find_more.run_orders()[0]["reason"] == "kept_offline"


def test_add_more_drops_a_website_that_only_shares_a_side_word(monkeypatch):
    built()
    guard_network(monkeypatch)
    session = add_more.find("dinosaur fossil maps", via="terminal", client="t")
    labels = " ".join(i["label"] for i in session["items"]
                      if i["section"] == "live_info")
    assert "OpenStreetMap" not in labels and "hardiness" not in labels
    assert "Paleobiology" in labels or "iDigBio" in labels


def test_a_look_only_search_does_not_offer_to_look_online(monkeypatch):
    built()
    guard_network(monkeypatch)
    session = add_more.find("jungle look", via="terminal", client="t")
    assert session["online_possible"] is False


def test_a_look_already_in_use_is_not_reported_as_added(monkeypatch):
    built()
    guard_network(monkeypatch)
    device = consent_tickets.terminal_device_id()
    for _ in range(2):
        session = add_more.find("jungle look", via="terminal", client=device)
        look = item_numbered(add_more.public(session, "browser"), "looks")
        got = add_more.approve(session["id"], [look["id"]], client=device,
                               device=device, surface="terminal")
    assert got[0]["status"] == "held" and "already uses" in got[0]["line"]
    assert len(add_more.added()) == 1
