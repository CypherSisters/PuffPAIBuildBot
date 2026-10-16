"""Finding knowledge for ANY goal: goal words, the resolver, proposal items.

BuildBot is a factory: a person can ask for any bot. These tests run six very
different goals through the same code with the network off (the shipped
dated Kiwix list), and check that each gets books that cite the catalog words
that matched - and that nothing here knows any subject by name.
"""
from __future__ import annotations

import pytest

from puffpai.catalogs import goal_words, knowledge_resolver as r
from puffpai import plain_words  # noqa: E402  (the one banned list)

G1 = ("I want a space bot that knows everything about space, shows me cool "
      "space stuff, and works with my smart telescope")
G2 = "I love dinosaurs, I want a bot that knows every dinosaur"
G3 = "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos"
G4 = "A garden helper for my vegetable beds in Ohio"
G5 = "A knitting and crochet buddy with patterns and tips"
G6 = "A music bot that knows bands and albums"


@pytest.fixture(autouse=True)
def scratch(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    from puffpai.config import get_config
    get_config(_force_reload=True)
    yield
    get_config(_force_reload=True)


# -- goal words ---------------------------------------------------------------

@pytest.mark.parametrize("goal,expected", [
    ("I want to build an AstronomyBot", ["astronomy"]),
    ("astronomybot", ["astronomy"]),
    ("DinoBuddy", ["dino"]),
    (G1, ["space", "telescope"]),
    (G2, ["dinosaur"]),
    (G3, ["honda", "civic", "maintenance", "recall"]),
    (G4, ["garden", "vegetable", "bed", "ohio"]),
    (G5, ["knitting", "crochet", "pattern"]),
    (G6, ["music", "band", "album"]),
    ("a bot that knows everything and shows cool stuff", []),
])
def test_goal_words_are_the_subject_and_nothing_else(goal, expected):
    assert goal_words.goal_terms(goal) == expected


def test_extra_words_from_a_model_go_through_the_same_filter():
    assert goal_words.goal_terms("", extra=["cool stuff", "Cars", "auto repair",
                                            "2014"]) == ["car", "auto", "repair"]


@pytest.mark.parametrize("term,word,hit", [
    ("garden", "gardening", True), ("telescope", "telescopes", True),
    ("dinosaur", "dinosaurs", True), ("civic", "civicrm", False),
    ("car", "career", False), ("car", "cars", True), ("art", "artist", False),
    ("music", "musicfans", False), ("astronomy", "astronomy", True),
])
def test_a_term_matches_the_same_word_not_a_different_one(term, word, hit):
    assert goal_words.term_hits_word(term, word) is hit


# -- the resolver, six different goals ----------------------------------------

def _ids(result):
    return [c["id"] for c in result["candidates"]]


@pytest.mark.parametrize("goal,age,must_include", [
    (G1, 10, {"kiwix_space_stackexchange", "kiwix_astronomy_stackexchange"}),
    (G2, 8, {"kiwix:ted_mul_dinosaurs"}),
    (G4, None, {"kiwix:gardening.stackexchange.com_en_all"}),
    (G6, 15, {"kiwix:music.stackexchange.com_en_all"}),
])
def test_each_goal_finds_books_that_cite_what_matched(goal, age, must_include):
    result = r.find(goal, age=age)
    ids = set(_ids(result))
    assert must_include <= ids, ids
    for cand in result["candidates"]:
        if cand.get("general"):
            assert "encyclopedia" in cand["reason"].lower()
        else:
            assert cand["match_terms"], cand["id"]


def test_a_goal_that_needs_the_model_s_words_finds_them_through_it():
    """A goal naming something no shelf lists: the model's words close the gap."""
    goal = "keep my old Vespa running"
    alone = set(_ids(r.find(goal)))
    helped = set(_ids(r.find(goal, terms=["car", "auto repair"])))
    assert "kiwix:mechanics.stackexchange.com_en_all" not in alone
    assert "kiwix:mechanics.stackexchange.com_en_all" in helped


def test_the_shelf_list_finds_the_book_a_brand_or_hobby_belongs_with():
    """A user names a Honda Civic without saying 'car', and another says
    knitting, never 'crafts'. config/subject_shelves.json (data) says which
    shelf those subjects sit on, so both find their book without the model."""
    assert "kiwix:mechanics.stackexchange.com_en_all" in _ids(r.find(G3))
    crafts = r.find(G5)
    # Gutenberg's knitting books and the topic book
    # now rank alongside; the shelf still finds the crafts book first among
    # the Kiwix library's books.
    kiwix = [c for c in crafts["candidates"] if c["id"].startswith("kiwix:")]
    assert kiwix[0]["id"] == "kiwix:crafts.stackexchange.com_en_all"
    assert "crafts" in kiwix[0]["reason"]
    assert "kiwix:craftcms.stackexchange.com_en_all" not in _ids(crafts)
    assert "kiwix:crafts.stackexchange.com_en_all" not in _ids(
        r.find("a space bot about spacecraft"))


def test_a_single_cited_word_in_a_blurb_is_enough_and_ranks_below_a_title_hit():
    ids = _ids(r.find("car"))
    assert "kiwix:mechanics.stackexchange.com_en_all" in ids
    music = r.find("music")["candidates"]
    title_hits = [c for c in music if "music" in c["name"].lower()]
    assert music[0] in title_hits


def test_a_goal_nothing_matches_is_told_honestly_that_only_an_encyclopedia_covers_it():
    result = r.find("a bot about competitive zorbing")
    assert "Nothing in the library is only about" in result["note"]
    # Only general encyclopedias, and the Wikipedia book that could be made
    # from the words (a plan, not a claim that a library has the subject).
    assert all(c.get("general") or r.tier_of(c) == "wikipedia_topic"
               for c in result["candidates"])
    assert "a Wikipedia book can be made from your words" in result["note"]


def test_a_young_builder_sees_books_for_children_first_and_nothing_is_hidden():
    child = r.find(G2, age=8)
    grown = r.find(G2, age=40)
    # Simple English Wikipedia first for an 8-year-old, then the children's
    # encyclopedia; order only.
    assert _ids(child)[:2] == ["wikipedia:simple:dinosaur", "kiwix:vikidia_en_all:nopic"]
    assert _ids(grown)[0] != "kiwix:vikidia_en_all:nopic"
    assert _ids(grown).index("wikipedia:en:dinosaur") <         _ids(grown).index("wikipedia:simple:dinosaur")
    assert set(_ids(child)) == set(_ids(grown)), "age may change order, never contents"


@pytest.mark.parametrize("age,band", [
    (8, "child"), ("10", "child"), (15, "teen"), (40, "adult"), ("kid", "child"),
    (None, ""), ("", ""), (-3, ""), (True, ""), ("unknown", ""),
])
def test_age_bands(age, band):
    assert r.age_band(age) == band


def test_a_filler_goal_searches_nothing():
    result = r.find("a bot that knows everything")
    assert result["candidates"] == [] and result["terms"] == []
    assert "Name a subject" in result["note"]


def test_one_book_per_name_the_smallest_downloadable_issue():
    for goal in (G1, G2, G4, G6, "chemistry", "wikipedia"):
        cands = r.find(goal, limit=40)["candidates"]
        books = [c["book"] for c in cands if c["book"]]
        assert len(books) == len(set(books)), goal
    chem = [c for c in r.find("chemistry", limit=40)["candidates"]
            if c["book"] == "wikipedia_en_chemistry"]
    assert chem and chem[0]["flavour"] in ("mini", "nopic"), chem


def test_a_live_search_that_fails_falls_back_to_the_saved_list():
    def getter(*a, **kw):
        return None, "no network here"

    result = r.find(G6, live=True, getter=getter)
    assert result["searched"] == "snapshot"
    # one plain line, never the socket's own words
    assert "The Kiwix library didn't answer this time" in result["note"]
    assert "no network here" not in result["note"]
    assert "kiwix:music.stackexchange.com_en_all" in _ids(result)


def test_no_subject_is_named_in_the_resolver_code():
    """Factory rule: no per-topic tables in core. Subjects live in catalogs.

    Docstrings may use examples; code (names and string values) may not.
    """
    import ast
    import inspect

    words: list[str] = []
    for module in (r, goal_words):
        tree = ast.parse(inspect.getsource(module))
        docstrings = set()
        for node in ast.walk(tree):
            if (isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
                    and node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)):
                docstrings.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings):
                words.append(node.value.lower())
            elif isinstance(node, ast.Name):
                words.append(node.id.lower())
    code = " ".join(words)
    for subject in ("dinosaur", "telescope", "honda", "knitting", "astronomy",
                    "garden", "nasa", "space"):
        assert subject not in code, subject


# -- proposal items for the wizard's one summary -----------------------------

@pytest.mark.parametrize("goal,age", [(G1, 10), (G2, 8), (G4, None), (G6, 15)])
def test_proposal_items_have_the_shared_shape(goal, age):
    items = r.proposal_items(goal, [], age=age)
    assert items
    ticked = 0
    for item in items:
        assert set(item) == {"id", "section", "label", "detail", "size_bytes",
                             "outside_host", "default_on", "needs_yes", "apply"}
        assert item["id"].startswith("knowledge:")
        assert item["section"] == "knowledge"
        assert len(item["label"]) <= 80
        assert item["needs_yes"] is True
        assert item["apply"] == {"kind": "knowledge.add",
                                 "ref": item["id"][len("knowledge:"):]}
        ref = item["apply"]["ref"]
        host = {"wikipedia": None, "gutenberg": "www.gutenberg.org",
                "openstax": "openstax.org"}.get(ref.split(":", 1)[0],
                                                "lb.download.kiwix.org")
        if host is None:
            assert item["outside_host"] in ("en.wikipedia.org", "simple.wikipedia.org")
        else:
            assert item["outside_host"] == host
        assert "License:" in item["detail"]
        if host == "lb.download.kiwix.org":
            assert "Kiwix library" in item["detail"]
        if item["default_on"]:
            ticked += 1
            assert item["size_bytes"] <= r.DEFAULT_ON_MAX_BYTES
    # at most two library books plus one Wikipedia topic book start ticked
    assert ticked <= r.DEFAULT_ON_MAX_ITEMS + 1


def test_proposal_items_never_offer_a_book_with_an_unknown_license():
    for item in r.proposal_items(G4, []):
        assert "gardenology" not in item["id"]


#: On top of plain_words' one list: words this area once leaked.
BANNED_EXTRA = ("drawbridge", "vetted", "registry")


@pytest.mark.parametrize("goal", [G1, G2, G3, G4, G5, G6])
def test_owner_visible_words_are_plain(goal):
    texts = [i["label"] + " " + i["detail"] for i in r.proposal_items(goal, ["car"])]
    result = r.find(goal)
    texts += [result["note"]] + [line for c in result["candidates"]
                                 for line in r.candidate_lines(c)]
    blob = " ".join(texts).lower()
    assert plain_words.is_plain(blob, also=BANNED_EXTRA), \
        plain_words.check(blob, also=BANNED_EXTRA)


@pytest.mark.parametrize("word,expected", [
    ("volcanoes", "volcano"), ("tomatoes", "tomato"), ("heroes", "hero"),
    ("canoes", "canoe"), ("shoes", "shoe"), ("horseshoes", "horseshoe"),
    ("tiptoes", "tiptoe"), ("dinosaurs", "dinosaur"), ("batteries", "battery"),
    ("recalls", "recall"), ("glasses", "glass"), ("bus", "bus"),
])
def test_plurals_fold_to_the_word_a_person_would_say(word, expected):
    assert goal_words.singular(word) == expected
