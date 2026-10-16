"""The feed block in puffpai_app/v1: what it may say, and everything it may not.

A feed is data. These tests pin the shape: GET-only https on one declared
host, a typed whitelist of placeholders, a refresh floor, a small path subset,
three card kinds, a credit and a license line - and a refusal, by name, for
anything else.
"""

from __future__ import annotations

import copy

import pytest

from livedata_support import feed_manifest
from puffpai import app_registry, feeds


def _problem(mutate) -> str | None:
    manifest = feed_manifest()
    mutate(manifest["feed"])
    return app_registry.validate_manifest(manifest)


def test_a_good_feed_block_validates():
    assert app_registry.validate_manifest(feed_manifest()) is None


@pytest.mark.parametrize("mutate, words", [
    (lambda f: f.update(handler="x"), "handler"),
    (lambda f: f["card"].update(script="x"), "script"),
    (lambda f: f.update(method="POST"), "does not read"),
    (lambda f: f["service"].update(port=8443), "unknown fields"),
    (lambda f: f.update(url="http://api.example.org/v1"), "does not start"),
    (lambda f: f.update(url="https://other.example.org/v1"), "does not start"),
    (lambda f: f.update(url="https://api.example.org.evil.io/v1"), "does not start"),
    (lambda f: f.update(url="https://api.example.org:8443/v1"), "does not start"),
    (lambda f: f.update(url="https://api.example.org/v1?q={secret}"), "placeholder"),
    (lambda f: f.update(url="https://api.example.org/v1?q={date"), "brace"),
    (lambda f: f.update(url="https://api.example.org/v1#frag"), "plain https"),
    (lambda f: f.update(refresh_minutes=5), "between"),
    (lambda f: f.update(refresh_minutes=True), "whole number"),
    (lambda f: f.update(max_age_minutes=20), "shorter"),
    (lambda f: f.update(credit=""), "credit"),
    (lambda f: f.pop("license"), "license"),
    (lambda f: f["card"].update(kind="html"), "card kind"),
    (lambda f: f["card"].update(value="reading['level']"), "simple path"),
    (lambda f: f["card"].update(value="__class__.__init__"), None),
    (lambda f: f["card"].update(label=""), "label"),
    (lambda f: f["service"].update(host="127.0.0.1"), "public web address"),
    (lambda f: f["service"].update(host="nas.local"), "public web address"),
    (lambda f: f["service"].update(image_host="10.0.0.2"), "picture host"),
])
def test_everything_else_is_refused(mutate, words):
    problem = _problem(mutate)
    if words is None:
        # Dunder-looking keys are legal path TOKENS but resolve() only ever
        # does dict lookups, so they are harmless; the block still validates.
        assert problem is None
        return
    assert problem is not None and words in problem, problem


def test_declared_choices_are_the_only_other_placeholders():
    manifest = feed_manifest(url="https://api.example.org/v1?q={topic}&d={date}",
                             params={"topic": {"label": "Subject",
                                               "choices": ["saturn", "mars"],
                                               "default": "saturn"}})
    assert app_registry.validate_manifest(manifest) is None
    bad = copy.deepcopy(manifest)
    bad["feed"]["params"]["topic"]["choices"] = ["saturn", "a&b=c"]
    assert "options" in app_registry.validate_manifest(bad)
    bad = copy.deepcopy(manifest)
    bad["feed"]["params"]["date"] = {"label": "x", "choices": ["a"], "default": "a"}
    assert "choice name" in app_registry.validate_manifest(bad)


def test_list_cards_are_bounded():
    card = {"kind": "list", "title": "Things", "items": "rows",
            "item_title": "name", "max_items": 21}
    assert "1 to 20" in app_registry.validate_manifest(feed_manifest(card=card))
    card["max_items"] = 20
    assert app_registry.validate_manifest(feed_manifest(card=card)) is None
    card["items"] = ""   # the reply itself is the list
    assert app_registry.validate_manifest(feed_manifest(card=card)) is None


def test_paths_are_a_small_safe_subset():
    doc = {"a": {"b": [{"c": 1}, {"c": 2}]}, "rows": [[1], [2]]}
    assert feeds.resolve(doc, "a.b[0].c") == 1
    assert feeds.resolve(doc, "a.b[-1].c") == 2
    assert feeds.resolve(doc, "a.b[9].c") is feeds._MISSING
    assert feeds.resolve([{"k": "v"}], "[-1].k") == "v"
    for bad in ("a..b", "a[0", "a[*]", "a[-2]", "$.a", "a.b()", "a b", "",
                "a/b", "a[1000]"):
        assert not feeds.valid_path(bad), bad


def test_values_come_out_as_plain_text():
    assert feeds.as_text("<b>Hi</b>\x07 there") == "Hi there"
    assert feeds.as_text(3.14159) == "3.14"
    assert feeds.as_text(True) == "yes"
    assert feeds.as_text({"x": 1}) == ""
    assert feeds.as_text(["a", "b", {"c": 1}]) == "a, b"
    assert feeds.as_text("x" * 500, 20).endswith("…")


def test_placeholders_are_url_quoted():
    url = feeds.fill_url("https://h.example.org/s?q={topic}", {"topic": "a b&c=d/e"})
    assert url == "https://h.example.org/s?q=a%20b%26c%3Dd%2Fe"


def test_what_leaves_is_written_from_the_address():
    feed = feed_manifest(url="https://api.example.org/v1?lat={lat_round}"
                             "&lon={lon_round}&d={date}")["feed"]
    line = feeds.what_leaves(feed)
    assert "api.example.org" in line and "internet address" in line
    assert "today's date" in line and "about 11 km" in line
    assert "img.example.org" in line
    plain = feeds.what_leaves(feed_manifest(image_host=None)["feed"])
    assert "date" not in plain and "location" not in plain


def test_picture_addresses_must_be_on_the_declared_hosts():
    feed = feed_manifest()["feed"]
    assert feeds.image_url_ok("http://img.example.org/a.jpg", feed) == \
        "https://img.example.org/a.jpg"
    assert feeds.image_url_ok("https://api.example.org/b.png", feed)
    assert feeds.image_url_ok("https://evil.example.com/a.jpg", feed) is None
    assert feeds.image_url_ok("https://img.example.org:8443/a.jpg", feed) is None
    assert feeds.image_url_ok("https://u:p@img.example.org/a.jpg", feed) is None
    assert feeds.image_url_ok("javascript:alert(1)", feed) is None
    assert feeds.image_url_ok("data:image/png;base64,AAAA", feed) is None


def test_extract_reads_only_the_named_paths():
    feed = feed_manifest(card={"kind": "list", "title": "Rows", "items": "rows",
                               "item_title": "name", "item_detail": "note",
                               "item_image": "pic", "max_items": 2})["feed"]
    doc = {"rows": [{"name": "A", "note": "n1", "pic": "https://img.example.org/1.png"},
                    {"name": "B", "pic": "https://evil.example.com/2.png"},
                    {"name": "C"}]}
    card = feeds.extract(doc, feed)
    assert [i["title"] for i in card["items"]] == ["A", "B"]
    assert card["items"][0]["image_url"] == "https://img.example.org/1.png"
    assert card["items"][1]["image_url"] is None


def test_home_location_is_rounded_to_town_level(monkeypatch):
    from livedata_support import set_posture
    set_posture(home={"latitude": 40.123456, "longitude": -83.98765,
                      "timezone": "America/New_York"})
    home = feeds.home_block()
    assert home == {"lat": 40.1, "lon": -84.0, "timezone": "America/New_York"}


def test_the_author_example_validates_and_is_not_installed():
    """apps/_examples is for people reading how a live card is written. The
    scan skips folders starting with '_', so the example is never installed."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "apps" / "_examples"
    manifest = json.loads((root / "space_pictures" / "app.json").read_text("utf-8"))
    assert app_registry.validate_manifest(manifest) is None
    assert "space_pictures" not in app_registry.installed_ids()
    line = feeds.what_leaves(manifest["feed"])
    assert "the subject you picked" in line
