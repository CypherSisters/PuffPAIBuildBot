"""The reach tiers wired in: resolver order and caps, what starts ticked,
adding a text book end to end, the named outside services, plain words.

No network: every outside answer is a fake.
"""
from __future__ import annotations

import json
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="puffpai_reach_test_")
os.environ.setdefault("PUFFPAI_DATA_DIR", _TMP)

import pytest  # noqa: E402

from puffpai import catalogs, plain_words  # noqa: E402
from puffpai.catalogs import knowledge_resolver as r  # noqa: E402
from puffpai.config import get_config  # noqa: E402
from puffpai.knowledge import find_more, text_books  # noqa: E402
from puffpai.security import egress_doors  # noqa: E402
from puffpai.wizard import sources_plan  # noqa: E402

from test_reach_wikipedia_topics import _dino_wiki  # noqa: E402

G1 = "I want a space bot that knows everything about space, shows me cool space stuff, and works with my smart telescope"
G2 = "I love dinosaurs, I want a bot that knows every dinosaur"
G3 = "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos"
G4 = "A garden helper for my vegetable beds in Ohio"
G5 = "A knitting and crochet buddy with patterns and tips"
G6 = "A music bot that knows bands and albums"
ONLINE = {"build_network": "online", "run_egress": "island"}
OFFLINE = {"build_network": "offline", "run_egress": "sealed"}


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    get_config(_force_reload=True)
    learned = []
    monkeypatch.setattr(text_books, "learn",
                        lambda path, **kw: learned.append(path) or
                        {"status": "complete", "docs": 3, "chunks": 9, "learned_now": 3})
    # The meaning stage is the background learner's; no thread in a test.
    monkeypatch.setattr(find_more, "_learn_rest_in_background", lambda folder: None)
    yield learned
    get_config(_force_reload=True)


# -- what each place gives, for six strangers --------------------------------------

@pytest.mark.parametrize("goal,age", [(G1, 10), (G2, 8), (G3, 40), (G4, 40), (G5, 40), (G6, 15)])
def test_every_goal_gets_a_topic_book_and_every_place_is_reported(goal, age):
    found = r.find(goal, age=age)
    assert set(found["tiers"]) == {"registry", "kiwix", "wikipedia_topic", "gutenberg",
                                   "openstax"}
    topic = [c for c in found["candidates"] if r.tier_of(c) == "wikipedia_topic"]
    assert topic, "a Wikipedia book made from the goal's own words is always offered"
    items = r.proposal_items(goal, age=age)
    matched = [i for i in items if "Wikipedia (short" not in i["label"]
               and "Vikidia" not in i["label"]]
    assert len(items) <= r.SUMMARY_MATCHES + r.SUMMARY_GENERAL
    assert any(i["apply"]["ref"].startswith("wikipedia:") for i in matched)


def test_the_saved_lists_find_real_books_for_knitting_and_gardening_offline():
    knit = [c["id"] for c in r.find(G5)["candidates"]]
    assert any(i.startswith("gutenberg:") for i in knit)
    garden = [c for c in r.find(G4)["candidates"] if r.tier_of(c) == "gutenberg"]
    assert garden and all("garden" in (c["name"] + c["what_it_is"]).lower() for c in garden)
    space = [c["id"] for c in r.find(G1)["candidates"] if r.tier_of(c) == "openstax"]
    assert space == [] or space[0].startswith("openstax:astronomy")


def test_simple_english_first_for_a_young_builder_order_only():
    child = [c["id"] for c in r.find(G2, age=8)["candidates"]]
    adult = [c["id"] for c in r.find(G2, age=40)["candidates"]]
    assert child[0] == "wikipedia:simple:dinosaur"
    assert adult.index("wikipedia:en:dinosaur") < adult.index("wikipedia:simple:dinosaur")
    assert set(child) == set(adult)


def test_a_kiwix_only_fake_is_never_sent_another_librarys_requests():
    seen = []

    def kiwix_only(url, **kw):
        seen.append(url)
        return None, "no"

    r.find("knitting", live=True, getter=kiwix_only)
    assert seen and all("kiwix" in u for u in seen)


def test_the_local_model_may_reorder_but_not_add():
    def ask(prompt):
        return "gutenberg:36669\nhttps://evil.example/x\nmade-up:1"
    plain = {c["id"] for c in r.find(G5)["candidates"]}
    ranked = r.find(G5, ask=ask)["candidates"]
    assert {c["id"] for c in ranked} == plain
    specific = [c for c in ranked if not c.get("general")]
    assert specific[0]["id"] == "gutenberg:36669"


# -- what starts ticked ------------------------------------------------------------

def _cand(ident, tier, **kw):
    base = {"id": ident, "tier": tier, "fetchable": True, "name": ident}
    base.update(kw)
    return base


def test_a_topic_book_is_ticked_when_no_better_match_is():
    cands = [_cand("wikipedia:en:knitting-crochet", "wikipedia_topic",
                   words=["knitting", "crochet"], size_ceiling=25 * 2 ** 20),
             _cand("gutenberg:1", "gutenberg"),
             _cand("openstax:x", "openstax", size_bytes=10, personal_use_only=True,
                   commercial_ok=False)]
    assert sources_plan.knowledge_default_on(cands, "knitting and crochet") == {
        "wikipedia:en:knitting-crochet"}


def test_only_one_topic_book_and_never_a_big_one():
    cands = [_cand("wikipedia:simple:dinosaur", "wikipedia_topic", words=["dinosaur"],
                   size_ceiling=25 * 2 ** 20),
             _cand("wikipedia:en:dinosaur", "wikipedia_topic", words=["dinosaur"],
                   size_ceiling=25 * 2 ** 20)]
    assert sources_plan.knowledge_default_on(cands, "dinosaurs") == {"wikipedia:simple:dinosaur"}
    big = [_cand("wikipedia:en:dinosaur", "wikipedia_topic", words=["dinosaur"],
                 size_bytes=80 * 2 ** 20)]
    assert sources_plan.knowledge_default_on(big, "dinosaurs") == set()


def test_a_better_ticked_library_book_keeps_the_topic_book_unticked():
    cands = [_cand("kiwix:dino_en_all", "kiwix", name="Dinosaur Knitting Encyclopedia",
                   size_bytes=10 * 2 ** 20),
             _cand("wikipedia:en:dinosaur", "wikipedia_topic", words=["dinosaur"],
                   size_ceiling=25 * 2 ** 20)]
    assert sources_plan.knowledge_default_on(cands, "dinosaur knitting") == {"kiwix:dino_en_all"}


def test_a_library_book_off_the_subject_is_not_ticked():
    """Before this, "TED ideas" (257 MB) started ticked for a wedding bot."""
    cands = [_cand("kiwix:ted_en_ideas", "kiwix", name="TED ideas", size_bytes=257 * 2 ** 20),
             _cand("wikipedia:en:wedding-planner", "wikipedia_topic",
                   words=["wedding", "planner"], size_ceiling=25 * 2 ** 20)]
    assert sources_plan.knowledge_default_on(cands, "help me plan a wedding") == {
        "wikipedia:en:wedding-planner"}


def test_the_summary_keeps_its_caps_and_the_topic_book_its_seat():
    items = r.proposal_items(G5)
    refs = [i["apply"]["ref"] for i in items]
    assert "wikipedia:en:knitting-crochet" in refs  # named from the subject only (design 3b.4)
    general = [i for i in items if i["apply"]["ref"] in (
        "kiwix:wikipedia_en_all:mini", "kiwix:vikidia_en_all:nopic")]
    assert len(items) - len(general) <= r.SUMMARY_MATCHES and len(general) <= r.SUMMARY_GENERAL
    for item in items:
        assert len(item["label"]) <= 80 and item["label"].endswith(")")


# -- adding a text book --------------------------------------------------------------

def test_a_topic_book_shows_its_articles_and_size_before_the_yes(scratch):
    fake = _dino_wiki()
    first = find_more.add("wikipedia:simple:dinosaur", size_acknowledged=False, via="cli",
                          posture=ONLINE, getter=fake)
    assert first["status"] == "needs_confirmation"
    text = "\n".join(first["lines"])
    assert "Found:" in text and "articles about dinosaur" in text and "Sends:" in text
    assert first["candidate"]["size_bytes"] == first["plan"]["estimate_bytes"]
    assert text_books.find_books(find_more.knowledge_folder()) == []
    asked = len(fake.calls)
    done = find_more.add("wikipedia:simple:dinosaur", size_acknowledged=True, via="cli",
                         posture=ONLINE, getter=fake)
    assert done["ok"], done.get("reason")
    # the yes fetched exactly the articles that were shown (no second search)
    searches = [c for c in fake.calls[asked:] if c.get("list")]
    assert searches == []
    book = text_books.read_book(done["path"])
    assert [i["title"] for i in book["items"]] == [a["title"] for a in first["plan"]["articles"]]
    assert book["license"] == "CC-BY-SA-4.0" and scratch == [done["path"]]
    assert done["learned"] is True and "can answer from it now" in "\n".join(done["lines"])


def test_find_more_grows_an_existing_topic_book(scratch):
    fake = _dino_wiki()
    find_more.add("wikipedia:simple:dinosaur", size_acknowledged=True, via="cli",
                  posture=ONLINE, getter=fake)
    fake.pages["Stegosaurus"] = {"lead": "Stegosaurus was a dinosaur with plates.",
                                 "length": 5000}
    fake.members["Category:Dinosaurs"].append(("Stegosaurus", 0))
    grown = find_more.add("wikipedia:simple:dinosaur", size_acknowledged=True, via="cli",
                          posture=ONLINE, getter=fake)
    assert grown["ok"] and grown["added"] == 1
    titles = [i["title"] for i in text_books.read_book(grown["path"])["items"]]
    assert titles.count("Tyrannosaurus") == 1 and "Stegosaurus" in titles


def test_an_offline_build_fetches_no_text_book(scratch):
    def never(*a, **kw):
        raise AssertionError("network")
    out = find_more.add("wikipedia:en:knitting", size_acknowledged=True, via="cli",
                        posture=OFFLINE, getter=never)
    assert out["status"] == "refused" and "offline" in out["reason"]


def test_a_gutenberg_book_is_saved_without_its_boilerplate(scratch):
    from test_reach_gutendex import BOOK

    def fake(url, **kw):
        return BOOK.encode(), "ok"
    out = find_more.add("gutenberg:26113", size_acknowledged=True, via="cli",
                        posture=ONLINE, getter=fake)
    assert out["ok"], out.get("reason")
    book = text_books.read_book(out["path"])
    text = open(os.path.join(out["path"], "text", "0001.txt"), encoding="utf-8").read()
    assert "START OF THE PROJECT" not in text and "Cast on 40" in text
    assert "ebook #26113" in book["credit"]


def test_ids_outside_the_grammar_are_refused_before_anything_happens(scratch):
    for ident in ("wikipedia:de:hund", "gutenberg:abc", "openstax:../../x",
                  "wikipedia:en:https", "wikipedia:en:"):
        out = find_more.add(ident, size_acknowledged=True, via="cli", posture=ONLINE,
                            getter=lambda *a, **kw: pytest.fail("network"))
        assert out["ok"] is False


# -- the named outside services and the catalog rows --------------------------------

def test_three_named_outside_services_sit_together_shut_and_unswitched():
    # Appended together at the end; the search services were
    # appended after them.
    ids = [d.id for d in egress_doors.DOORS]
    at = ids.index("wikipedia_books")
    assert ids[at:at + 3] == ["wikipedia_books", "gutenberg_books", "openstax_books"]
    for door in egress_doors.DOORS[at:at + 3]:
        assert door.default is False and door.posture_switch is None
        assert not door.dynamic_hosts and door.hosts


def test_the_catalog_rows_are_live_and_name_their_hosts():
    for cid, module in (("gutenberg", "gutendex"), ("openstax", "openstax"),
                        ("wikipedia_topics", "wikipedia_topics")):
        row = catalogs.catalog_row(cid)
        assert catalogs.is_live(row) and row["adapter"] == f"puffpai.catalogs.{module}"
        assert row["robots"] and row["license_rule"]
    hosts = set(catalogs.catalog_row("wikipedia_topics")["search_hosts"])
    assert hosts == set(egress_doors.door("wikipedia_books").hosts)


# -- plain words -----------------------------------------------------------------------

#: On top of the one list in puffpai.plain_words: this area's own ids.
REACH_EXTRA = ("drawbridge", "gutendex", "wikipedia_topic", "wikipedia_books",
               "openstax_books", "cc-by-nc")


@pytest.mark.parametrize("goal", [G1, G2, G3, G4, G5, G6])
def test_owner_lines_for_the_new_places_are_plain(goal):
    found = r.find(goal)
    texts = [i["label"] + " " + i["detail"] for i in r.proposal_items(goal)]
    for cand in found["candidates"]:
        if r.tier_of(cand) in r.TEXT_TIERS:
            texts += r.candidate_lines(cand)[1:]  # the first line carries the id to type
    texts.append(found["note"])
    blob = " ".join(texts)
    assert plain_words.is_plain(blob, also=REACH_EXTRA), \
        plain_words.check(blob, also=REACH_EXTRA)


def test_consent_lines_label_a_personal_use_book():
    from puffpai.catalogs import openstax
    cand = openstax.candidate({"slug": "astronomy-2e", "title": "Astronomy 2e",
                               "state": "live", "license": "CC-BY-NC-SA-4.0",
                               "subjects": [], "pages": 10})
    text = "\n".join(find_more.consent_lines(cand))
    assert "free for personal use only" in text.lower()
    assert "never included if you share or sell" in text


def test_search_lines_name_every_place_looked(scratch):
    lines = find_more.search("knitting", live=False)["lines"]
    assert "Project Gutenberg" in lines[0] and "OpenStax" in lines[0]
    assert "Wikipedia book made from your words" in lines[0]
    assert json.dumps(lines)  # plain strings only


def test_what_can_you_do_names_the_book_services_honestly(monkeypatch):
    """The door table's generic 'Use the outside website en.wikipedia.org' is
    replaced (same id) by what the bot really does: fetch a book on a yes."""
    monkeypatch.setattr(find_more, "recorded_posture", lambda: dict(ONLINE))
    rows = {r["id"]: r for r in find_more.ability_rows([])}
    for ident in ("outside:wikipedia_books", "outside:gutenberg_books",
                  "outside:openstax_books"):
        assert rows[ident]["status"] == "available"
        assert "when you say yes" in rows[ident]["label"]
    monkeypatch.setattr(find_more, "recorded_posture", lambda: dict(OFFLINE))
    rows = {r["id"]: r for r in find_more.ability_rows([])}
    assert rows["outside:wikipedia_books"]["status"] == "not_set_up"
