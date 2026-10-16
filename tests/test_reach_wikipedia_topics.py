"""Wikipedia topic books: ids, relevance by code, polite asking, bounded fetch.

Every network answer here is a fake; nothing leaves the machine.
"""
from __future__ import annotations

import json
import threading
import urllib.parse

import pytest

from puffpai.catalogs import wikipedia_topics as wt


# -- a tiny fake Wikipedia ------------------------------------------------------

class FakeWiki:
    """Answers the action API from a small in-memory set of pages."""

    def __init__(self, pages, categories=None, members=None, busy=0):
        self.pages = pages                  # title -> {"lead","text","length","dab"}
        self.categories = categories or {}  # title -> [category]
        self.members = members or {}        # category -> [(title, ns)]
        self.calls = []
        self.busy = busy

    def __call__(self, url, *, allowed_hosts, max_bytes, honour_robots):
        parts = urllib.parse.urlsplit(url)
        assert parts.scheme == "https" and parts.hostname in allowed_hosts
        assert honour_robots is False  # the API, not a crawl
        q = dict(urllib.parse.parse_qsl(parts.query))
        assert q["maxlag"] == "5" and q["format"] == "json"
        self.calls.append(q)
        if self.busy:
            self.busy -= 1
            return json.dumps({"error": {"code": "maxlag"}}).encode(), "ok"
        if q.get("list") == "search":
            words = q["srsearch"].lower().split()
            hits = [{"title": t, "size": p.get("length", 1000)}
                    for t, p in self.pages.items()
                    if all(w in (t + " " + p.get("lead", "")).lower() for w in words)]
            return self._json({"query": {"search": hits}})
        if q.get("list") == "categorymembers":
            rows = [{"title": t, "ns": ns} for t, ns in self.members.get(q["cmtitle"], [])]
            return self._json({"query": {"categorymembers": rows}})
        titles = q.get("titles", "").split("|")
        out = []
        for title in titles:
            page = self.pages.get(title)
            if page is None:
                out.append({"title": title, "missing": True})
                continue
            row = {"title": title, "pageid": 1, "length": page.get("length", 1000)}
            if "categories" in q.get("prop", ""):
                row["categories"] = [{"title": c} for c in self.categories.get(title, [])]
            if q.get("exintro"):
                row["extract"] = page.get("lead", "")
                if page.get("dab"):
                    row["pageprops"] = {"disambiguation": ""}
            elif "extracts" in q.get("prop", ""):
                row["extract"] = page.get("text", page.get("lead", ""))
                row["revisions"] = [{"revid": 42}]
                row["canonicalurl"] = ("https://" + parts.hostname + "/wiki/" +
                                       title.replace(" ", "_"))
            out.append(row)
        return self._json({"query": {"pages": out}})

    def _json(self, data):
        return json.dumps(data).encode(), "ok"


def _dino_wiki():
    pages = {
        "Dinosaur": {"lead": "Dinosaurs are reptiles that lived long ago.", "length": 20000},
        "Tyrannosaurus": {"lead": "Tyrannosaurus was a large meat-eating theropod.",
                          "text": "Tyrannosaurus was a large meat-eating theropod. It had tiny arms.",
                          "length": 10000},
        "Dinosaur Jr": {"lead": "Dinosaur Jr is an American rock band.", "length": 3000},
        "Dinosaur (disambiguation)": {"lead": "Dinosaur may refer to", "dab": True},
        "Triceratops": {"lead": "Triceratops had three horns on its face.", "length": 8000},
    }
    cats = {"Dinosaur": ["Category:Dinosaurs", "Category:Reptiles"]}
    members = {"Category:Dinosaurs": [("Tyrannosaurus", 0), ("Triceratops", 0),
                                      ("Category:Ornithischia", 14)],
               "Category:Ornithischia": [("Triceratops", 0)]}
    return FakeWiki(pages, cats, members)


# -- ids ------------------------------------------------------------------------

def test_ids_are_built_from_goal_words_and_nothing_else():
    assert wt.topic_words("I love dinosaurs, I want a bot that knows every dinosaur") \
        == ["dinosaur"]
    assert wt.book_id("simple", ["dinosaur"]) == "wikipedia:simple:dinosaur"
    assert wt.split_id("wikipedia:en:knitting-crochet") == ("en", ["knitting", "crochet"])
    for bad in ("wikipedia:de:hund", "wikipedia:en:", "wikipedia:en:a-b",
                "wikipedia:en:knitting--x", "wikipedia:en:everything",
                "wikipedia:en:one-two-three-four-five", "wikipedia:en:Knitting",
                "wikipedia:en:knitting;rm", "https://evil.example/"):
        assert wt.split_id(bad) is None, bad


def test_a_candidate_names_its_site_license_and_ceiling_without_network():
    cand = wt.candidate("simple", ["dinosaur"])
    assert cand["host"] == "simple.wikipedia.org"
    assert cand["license"] == "CC-BY-SA-4.0" and cand["commercial_ok"] is True
    assert cand["size_bytes"] is None and cand["size_ceiling"] == 25 * 1024 * 1024
    assert cand["audience"] == "kids" and cand["fetchable"] is True
    assert cand["sends"] == ["dinosaur"]


def test_simple_english_comes_first_only_for_a_young_builder():
    young = [c["id"] for c in wt.candidates_for("dinosaurs", child_first=True)]
    grown = [c["id"] for c in wt.candidates_for("dinosaurs")]
    assert young == ["wikipedia:simple:dinosaur", "wikipedia:en:dinosaur"]
    assert grown == list(reversed(young))


# -- relevance by code ------------------------------------------------------------

def test_a_title_that_only_borrows_the_word_ranks_below_real_articles():
    band, keep_band, _ = wt.score_article("Dinosaur Jr", "Dinosaur Jr is a rock band.",
                                          ["dinosaur"], in_topic_category=False)
    real, keep_real, _ = wt.score_article("Dinosaur", "Dinosaurs are reptiles; a dinosaur...",
                                          ["dinosaur"], in_topic_category=True)
    assert keep_real and real > band


def test_a_later_word_alone_is_not_enough():
    """'pattern' alone (a Turing pattern) is not about knitting."""
    terms = ["knitting", "crochet", "pattern"]
    _s, keep, _m = wt.score_article("Turing pattern", "A Turing pattern is a pattern in nature.",
                                    terms, in_topic_category=False)
    assert not keep
    _s, keep, _m = wt.score_article("Aran jumper", "A knitting pattern with cables.",
                                    terms, in_topic_category=False)
    assert keep


def test_an_empty_first_paragraph_is_never_kept():
    assert wt.score_article("Dinosaur", "", ["dinosaur"], in_topic_category=True)[1] is False


# -- discovery ----------------------------------------------------------------------

def test_discover_finds_by_search_and_category_and_drops_disambiguation():
    fake = _dino_wiki()
    plan = wt.discover("wikipedia:simple:dinosaur", getter=fake, sleeper=lambda s: None)
    titles = [a["title"] for a in plan["articles"]]
    assert "Dinosaur" in titles and "Tyrannosaurus" in titles
    assert "Triceratops" in titles          # found through the category
    assert "Dinosaur (disambiguation)" not in titles
    assert titles.index("Dinosaur") < titles.index("Dinosaur Jr")
    assert plan["categories"] == ["Category:Dinosaurs"]
    assert plan["estimate_bytes"] == int(sum(a["length"] for a in plan["articles"]) * wt.TEXT_SHARE)
    assert plan["requests"] == len(fake.calls)


def test_discover_skips_titles_the_book_already_has():
    plan = wt.discover("wikipedia:simple:dinosaur", getter=_dino_wiki(),
                       have=["Tyrannosaurus"], sleeper=lambda s: None)
    assert "Tyrannosaurus" not in [a["title"] for a in plan["articles"]]


def test_the_model_may_only_remove_found_titles_and_never_most_of_them():
    plan = wt.discover("wikipedia:simple:dinosaur", getter=_dino_wiki(),
                       sleeper=lambda s: None,
                       prune=lambda titles: ["Dinosaur Jr", "Invented Title"])
    titles = [a["title"] for a in plan["articles"]]
    assert "Dinosaur Jr" not in titles and "Invented Title" not in titles
    assert plan["pruned"] == ["Dinosaur Jr"]
    greedy = wt.discover("wikipedia:simple:dinosaur", getter=_dino_wiki(),
                         sleeper=lambda s: None, prune=lambda titles: list(titles))
    assert greedy["pruned"] == [] and greedy["count"] >= 3


def test_model_pruner_reads_numbers_only():
    prune = wt.model_pruner("dinosaurs", lambda prompt: "2, 9, http://x 1")
    assert prune(["A", "B"]) == ["B", "A"]


def test_a_busy_wikipedia_is_waited_for_then_answered():
    fake = _dino_wiki()
    fake.busy = 2
    waits = []
    data = wt.ask("simple", {"list": "search", "srsearch": "dinosaur"}, getter=fake,
                  sleeper=waits.append)
    assert waits == [wt.MAXLAG_SECONDS, wt.MAXLAG_SECONDS]
    assert data["query"]["search"]


def test_a_refused_request_is_a_plain_error():
    def down(url, **kw):
        return None, "Could not reach that source: timed out"
    with pytest.raises(wt.WikipediaError, match="did not answer"):
        wt.ask("en", {"list": "search", "srsearch": "x"}, getter=down,
               sleeper=lambda s: None)


def test_the_allowed_hosts_come_from_the_named_outside_service():
    from puffpai.security import egress_doors
    door = egress_doors.door("wikipedia_books")
    assert set(door.hosts) == set(wt.SITES.values())
    assert wt._hosts() == set(door.hosts)


# -- fetching ------------------------------------------------------------------------

def test_fetch_pages_records_address_and_revision_and_stops_at_the_limit():
    fake = _dino_wiki()
    plan = {"lang": "simple", "articles": [{"title": "Tyrannosaurus"},
                                           {"title": "Triceratops"}]}
    pages = list(wt.fetch_pages(plan, getter=fake, sleeper=lambda s: None))
    assert pages[0]["url"] == "https://simple.wikipedia.org/wiki/Tyrannosaurus"
    assert pages[0]["revision"] == "42"
    capped = list(wt.fetch_pages(plan, getter=fake, sleeper=lambda s: None, max_bytes=10))
    assert len(capped) == 1


def test_fetch_pages_stops_when_cancelled():
    stop = threading.Event()
    stop.set()
    plan = {"lang": "simple", "articles": [{"title": "Tyrannosaurus"}]}
    assert list(wt.fetch_pages(plan, getter=_dino_wiki(), cancel=stop)) == []


def test_book_meta_carries_the_credit_and_license():
    meta = wt.book_meta("wikipedia:en:knitting")
    assert meta["license"] == "CC-BY-SA-4.0" and "CC BY-SA 4.0" in meta["credit"]
    assert meta["commercial_use"] is True and meta["personal_use_only"] is False
