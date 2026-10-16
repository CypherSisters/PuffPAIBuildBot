"""A pasted link on Find more: the one project it names, with the same Add,
check and yes as any other find (puffpai.finding.find.find_from_link).

No network: the site searchers are stand-ins. Copyright (c) Cypher Sisters.
Apache-2.0 License.
"""

from __future__ import annotations

import pytest

from puffpai import plain_words as pw
from puffpai.finding import find, sites
from puffpai.finding import wording as W


def _repo(full: str, i: int = 0) -> dict:
    return sites.make(site="github", type_="cli", id=f"github:{full}", name=full.split("/")[1],
                      by=full.split("/")[0], what="A thing", url=f"https://github.com/{full}",
                      can="install", rank=i)


@pytest.fixture
def github(monkeypatch):
    asked: list[str] = []

    def fake(words, limit):
        asked.append(words)
        return [_repo("someone/close-one"), _repo("acme/rocket-tool", 1)]
    monkeypatch.setitem(find._SEARCHERS, "github", fake)
    monkeypatch.setattr(find, "_switched_off", lambda site: False)
    return asked


@pytest.mark.parametrize("url, want", [
    ("https://github.com/acme/rocket-tool", ("github", "repo:acme/rocket-tool")),
    ("https://github.com/acme/rocket-tool.git", ("github", "repo:acme/rocket-tool")),
    ("https://github.com/acme/rocket-tool/tree/main/src", ("github", "repo:acme/rocket-tool")),
    ("https://codeberg.org/acme/rocket", ("codeberg", "rocket")),
    ("https://www.npmjs.com/package/@acme/rocket", ("npm", "@acme/rocket")),
    ("https://pypi.org/project/rocket-tool/", ("pypi", "rocket-tool")),
    ("https://example.org/docs", None),
    ("https://github.com/acme", None),
    ("not a link", None),
])
def test_which_site_and_what_to_ask(url, want):
    assert find.link_search(url) == want


def test_a_github_link_finds_exactly_that_project(github):
    got = find.find_from_link("https://github.com/acme/rocket-tool")
    assert github == ["repo:acme/rocket-tool"]
    ids = [f["id"] for t in got["jobs"][0]["types"] for f in t["finds"]]
    assert ids == ["github:acme/rocket-tool"]
    assert find.recall("github:acme/rocket-tool") is not None    # Add can install it


def test_no_exact_match_shows_the_closest_and_says_so(github):
    got = find.find_from_link("https://github.com/nobody/rocket-tool")
    assert got["count"] == 2
    assert got["jobs"][0]["notes"] == [W.LINK_CLOSE]


def test_a_link_to_somewhere_else_says_where_it_goes(github):
    got = find.find_from_link("https://example.org/docs")
    assert got["count"] == 0 and github == []
    assert got["jobs"][0]["notes"] == [W.LINK_UNKNOWN]


def test_a_switched_off_site_is_not_asked(github, monkeypatch):
    monkeypatch.setattr(find, "_switched_off", lambda site: True)
    got = find.find_from_link("https://github.com/acme/rocket-tool")
    assert github == [] and got["count"] == 0


def test_the_link_words_are_plain():
    for line in (W.LINK_UNKNOWN, W.LINK_CLOSE):
        assert pw.check(line) == [], line
        assert "door" not in line.lower() and "—" not in line


def test_the_find_route_takes_a_link(github):
    from puffpai import api_server
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}
    job = client.post("/add/things/find", headers=auth,
                      json={"words": "https://github.com/acme/rocket-tool"}).get_json()["job"]
    import time
    for _ in range(100):
        view = client.get(f"/add/things/jobs/{job}", headers=auth).get_json()
        if view["done"]:
            break
        time.sleep(0.05)
    assert view["done"] and view["result"]["count"] == 1
    assert github == ["repo:acme/rocket-tool"]
