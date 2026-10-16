"""Where a find comes from and what language its words are in (finding.sites).

Someone installed an app made in another country without knowing it. A GitHub find now says who made it, where they say they
are, and, when it is not her language, what language its words are in; finds
in another language are listed lower, never left out. Saved GitHub API
answers only (tests/fixtures/a4_origin): no test goes online.
"""
from __future__ import annotations

import json
import pathlib

import pytest

FIX = pathlib.Path(__file__).parent / "fixtures" / "a4_origin"

ANSWERS = {
    "https://api.github.com/repos/lingfeng-dev/weather-hub": "repo__weatherhub-cn.json",
    "https://api.github.com/repos/lingfeng-dev/weather-hub/readme": "readme__weatherhub-cn.md",
    "https://api.github.com/users/lingfeng-dev": "user__lingfeng-dev.json",
    "https://api.github.com/repos/mara-builds/skycast": "repo__skycast-en.json",
    "https://api.github.com/repos/mara-builds/skycast/readme": "readme__skycast-en.md",
    "https://api.github.com/users/mara-builds": "user__mara-builds.json",
}


class SavedGitHub:
    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, url, *, hosts, service, max_bytes, accept=None):
        self.calls.append((url, service, accept))
        assert set(hosts) == {"api.github.com"}
        if url.startswith("https://api.github.com/search/repositories"):
            name = "search__weather_board.json"
        else:
            name = ANSWERS.get(url)
        if name is None:
            return {"status": 404, "body": b"", "reason": "ok", "headers": {}}
        return {"status": 200, "body": (FIX / name).read_bytes(), "reason": "ok",
                "headers": {}}


@pytest.fixture
def gh(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    from puffpai.finding import net
    fake = SavedGitHub()
    monkeypatch.setattr(net, "_send", fake)
    return fake


# -- the human language of a text --------------------------------------------------

def _readme(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_language_of_chinese_english_and_spanish_readmes():
    from puffpai.finding import sites
    assert sites.words_language(_readme("readme__weatherhub-cn.md")) == "Chinese"
    assert sites.words_language(_readme("readme__skycast-en.md")) == "English"
    assert sites.words_language(_readme("readme__tiempo-es.md")) == "Spanish"


def test_language_needs_enough_words_and_ignores_code():
    from puffpai.finding import sites
    assert sites.words_language("Weather app") == ""
    assert sites.words_language("") == ""
    # code and links are not words
    assert sites.words_language("```\nimport os\nfor x in y: pass\n```\nhttps://x.example") == ""
    assert sites.words_language("これは天気を表示するためのシンプルなツールです。") == "Japanese"
    assert sites.words_language("Простой виджет погоды для вашего города и семьи") == "Russian"


def test_owner_language_defaults_to_english_and_reads_the_setting(tmp_path):
    from puffpai.config import get_config
    from puffpai.finding import sites
    path = pathlib.Path(get_config().CONFIG_DIR, "puffpai_config.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    assert sites.owner_language() == "English"
    path.write_text(json.dumps({"owner_language": "spanish"}), encoding="utf-8")
    assert sites.owner_language() == "Spanish"
    path.write_text("{}", encoding="utf-8")


# -- origin, from saved GitHub answers ------------------------------------------------

def test_origin_of_a_chinese_project(gh):
    from puffpai.finding import sites
    origin = sites.github_origin("lingfeng-dev/weather-hub")
    assert origin == {"owner": "lingfeng-dev", "location": "Hangzhou, China",
                      "licence": "MIT", "code_language": "Vue",
                      "words_language": "Chinese"}
    assert sites.origin_line(origin, "English") == (
        "Made by lingfeng-dev (Hangzhou, China). Its words are in Chinese.")
    # Only the project's and its maker's public names are sent.
    urls = {c[0] for c in gh.calls}
    assert urls == {"https://api.github.com/repos/lingfeng-dev/weather-hub",
                    "https://api.github.com/repos/lingfeng-dev/weather-hub/readme",
                    "https://api.github.com/users/lingfeng-dev"}
    assert all(c[1] == "code_search" for c in gh.calls)


def test_origin_of_an_english_project_with_no_public_place(gh):
    from puffpai.finding import sites
    origin = sites.github_origin("mara-builds/skycast")
    assert origin["location"] == "" and origin["words_language"] == "English"
    assert sites.origin_line(origin, "English") == "Made by mara-builds."
    assert sites.origin_line(origin, "Spanish") == (
        "Made by mara-builds. Its words are in English.")


def test_a_bad_name_sends_nothing(gh):
    from puffpai.finding import sites
    assert sites.github_origin("../../etc/passwd") == {}
    assert sites.github_origin("a b/c") == {}
    assert gh.calls == []


def test_the_preview_card_says_who_made_it(gh):
    from puffpai.finding import find, install, sites
    f = sites.make(site="github", type_="cli", id="github:lingfeng-dev/weather-hub",
                   name="weather-hub", by="lingfeng-dev", what="weather board",
                   url="https://github.com/lingfeng-dev/weather-hub", can="look")
    find.remember([f])
    card = install.preview(f["id"])
    assert "Made by lingfeng-dev (Hangzhou, China). Its words are in Chinese." in card["lines"]
    assert card["origin"]["licence"] == "MIT"
    # The README's language is kept for ranking.
    assert find.recall(f["id"])["words_language"] == "Chinese"


# -- ranking prefers her language, never hides -------------------------------------

def test_ranking_lists_other_languages_lower_and_keeps_them(gh):
    from puffpai.finding import find, sites
    finds = sites.github("weather board")
    assert [f["name"] for f in finds] == ["weather-hub", "tiempo", "skycast"]
    assert [f["words_language"] for f in finds] == ["Chinese", "Spanish", "English"]
    assert finds[0]["origin"] == {"owner": "lingfeng-dev", "licence": "MIT",
                                  "code_language": "Vue"}
    grouped = find._group({"id": "j1", "name": "weather", "words": "weather"}, finds)
    names = [x["name"] for t in grouped["types"] for x in t["finds"]]
    assert names == ["skycast", "weather-hub", "tiempo"]       # demoted, all kept


def test_ranking_follows_the_owners_language(gh, monkeypatch):
    from puffpai.finding import find, sites
    monkeypatch.setattr(sites, "owner_language", lambda: "Spanish")
    finds = sites.github("weather board")
    grouped = find._group({"id": "j1", "name": "weather", "words": "weather"}, finds)
    names = [x["name"] for t in grouped["types"] for x in t["finds"]]
    assert names == ["tiempo", "weather-hub", "skycast"]
