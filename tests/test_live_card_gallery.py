"""Live cards show varied content, and pictures when a service has them.

The NASA EPIC card showed one sentence ("This image
was taken by NASA's EPIC camera...") ten times and no pictures, because the
reply carries picture NAMES and dates, not addresses, and the one wordy field
was the same in every row. The NOAA card showed the word "observed" ten times.

The fix is generic, never a topic table:

* a field that says the same thing in every row never becomes a list;
* rows that each carry a different picture become a ``gallery`` card;
* a catalog row may carry an ``image_template`` (DATA) that turns a row's
  picture name and date into a picture address on the service's own website;
* pictures still go through the backend's checked picture store.

Replies are literals shaped like the real ones checked live.
No network.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json

import pytest

from livedata_support import (PNG_1PX, FakeResponse, fake_network,
                              feed_manifest, install, set_posture)
from puffpai import feed_generator, feeds
from puffpai.catalogs import api_seeds
from puffpai.security import egress_doors

SAME = "This image was taken by NASA's EPIC camera onboard the NOAA DSCOVR spacecraft"
EPIC_TEMPLATE = ("https://epic.gsfc.nasa.gov/archive/natural/{date:year}/"
                 "{date:month}/{date:day}/jpg/{image}.jpg")


def _epic_rows(n: int = 4) -> list[dict]:
    return [{"identifier": f"2026092400{i:02d}30", "caption": SAME,
             "image": f"epic_1b_2026092400{i:02d}30", "version": "04",
             "centroid_coordinates": {"lat": 5.7, "lon": 175.0 - i * 25},
             "date": f"2026-09-24 0{i}:03:42"} for i in range(n)]


def _cand(**extra):
    base = {"id": "seed:pics", "kind": "live_data", "name": "Picture service",
            "what_it_is": "Pictures of things, updated every day.",
            "source_catalog": "api_seeds", "host": "epic.gsfc.nasa.gov",
            "sample_endpoint": "https://epic.gsfc.nasa.gov/api/natural",
            "auth": "none", "license": "public domain", "match_terms": ["earth"]}
    base.update(extra)
    return base


def _probe(document):
    body = json.dumps(document).encode()

    def probe(url, host, build_online, max_bytes):
        return {"status": 200, "content_type": "application/json",
                "bytes": len(body), "truncated": False, "body": body}
    return probe


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()
    yield
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()


# ---------------------------------------------------------------------------
# one sentence ten times is never a card
# ---------------------------------------------------------------------------

def test_a_field_that_is_the_same_in_every_row_is_never_a_list():
    for option in feed_generator.inspect(_epic_rows()):
        assert option["card"].get("item_title") != "caption"
        assert option["card"].get("item_detail") != "caption"
    noaa = [{"time_tag": f"2026-09-19T0{i}:00:00", "kp": 1.5 + i,
             "observed": "observed", "noaa_scale": None} for i in range(6)]
    assert all(o["card"].get("item_title") != "observed"
               for o in feed_generator.inspect(noaa))


def test_without_a_picture_address_the_epic_reply_makes_no_card():
    # Before the fix this became ten rows of SAME. Now: nothing worth showing.
    with pytest.raises(feed_generator.DraftError) as caught:
        feed_generator.draft_feed_app(_cand(), build_online=True,
                                      probe=_probe(_epic_rows()))
    assert "nothing" in caught.value.message or "worth" in caught.value.message


def test_varies_rejects_repeated_rows_and_keeps_differing_pictures():
    same = {"kind": "list", "items": [{"title": SAME}] * 5}
    assert feeds.varies(same) is False
    assert feeds.varies({"kind": "list", "items": [{"title": "a"}, {"title": "b"}]})
    assert feeds.varies({"kind": "list", "items": [{"title": SAME}]})
    pics = {"kind": "gallery", "items": [
        {"title": "Picture", "image_url": f"https://x.org/{i}.jpg"} for i in range(3)]}
    assert feeds.varies(pics) is True
    one_pic = {"kind": "gallery", "items": [
        {"title": "Picture", "image_url": "https://x.org/1.jpg"}] * 3}
    assert feeds.varies(one_pic) is False
    assert feeds.varies({"kind": "value", "value": "3"}) is True


# ---------------------------------------------------------------------------
# a picture template turns names and dates into pictures
# ---------------------------------------------------------------------------

def test_the_epic_template_makes_a_gallery_of_real_archive_addresses():
    draft = feed_generator.draft_feed_app(
        _cand(image_template=EPIC_TEMPLATE), build_online=True,
        probe=_probe(_epic_rows()))
    card = draft["manifest"]["feed"]["card"]
    assert card["kind"] == "gallery"
    assert card["item_image_template"] == EPIC_TEMPLATE
    assert card["item_detail"] == "date"          # each picture with its time
    assert "item_title" not in card               # the same caption is not a title
    assert card["caption"].endswith(".caption")   # said once, for the whole card
    feed = draft["manifest"]["feed"]
    shown = feeds.extract(_epic_rows(), feed)
    assert [i["image_url"] for i in shown["items"]][:2] == [
        "https://epic.gsfc.nasa.gov/archive/natural/2026/09/24/jpg/"
        "epic_1b_2026092400" + f"{i:02d}30.jpg" for i in range(2)]
    assert [i["title"] for i in shown["items"]][:2] == [
        "2026-09-24 00:03:42", "2026-09-24 01:03:42"]
    assert shown["caption"] == SAME
    assert draft["preview_has_picture"] is True
    assert feeds.varies(shown)


@pytest.mark.parametrize("value", ["../../etc", "a/b", "x?y=1", "..", "a b",
                                   "", None, True, {"a": 1}])
def test_a_row_value_that_is_not_a_plain_name_gives_no_picture(value):
    row = {"image": value, "date": "2026-09-24 00:03:42"}
    assert feeds.fill_image_template(EPIC_TEMPLATE, row) is None


def test_a_date_part_needs_a_date():
    row = {"image": "epic_1", "date": "yesterday"}
    assert feeds.fill_image_template(EPIC_TEMPLATE, row) is None
    row["date"] = "2026-01-05T10:00:00Z"
    assert feeds.fill_image_template(EPIC_TEMPLATE, row).endswith(
        "/2026/01/05/jpg/epic_1.jpg")


@pytest.mark.parametrize("template, hosts", [
    ("https://{host}/x/{image}.jpg", ["a.org"]),            # a row picks the site
    ("https://evil.example.com/{image}.jpg", ["a.org"]),    # not its own host
    ("http://a.org/{image}.jpg", ["a.org"]),                # not https
    ("https://a.org/fixed.jpg", ["a.org"]),                 # nothing from the row
    ("https://a.org/{image()}.jpg", ["a.org"]),              # not a simple path
    ("https://a.org/{image:hour}.jpg", ["a.org"]),          # unknown part
    ("https://a.org/{image}}.jpg", ["a.org"]),              # stray brace
    ("https://a.org:8443/{image}.jpg", ["a.org"]),          # a port
])
def test_unsafe_picture_templates_are_refused(template, hosts):
    assert feeds.image_template_problem(template, hosts) is not None


def test_a_gallery_feed_block_is_checked_like_any_other():
    good = {"kind": "gallery", "title": "Pictures", "items": "rows",
            "item_image_template": "https://img.example.org/p/{name}.jpg",
            "item_detail": "when", "max_items": 12}
    assert feeds.validate_feed("pics", feed_manifest("pics", card=good)["feed"]) is None
    for bad, words in (
            ({**good, "item_image_template": "https://other.example.net/{name}.jpg"},
             "gallery picture address"),
            ({k: v for k, v in good.items() if k != "item_image_template"},
             "where its pictures are"),
            ({**good, "item_image": "pic", "bogus": 1}, "does not read"),
            ({**good, "max_items": 50}, "1 to 20")):
        problem = feeds.validate_feed("pics", feed_manifest("pics", card=bad)["feed"])
        assert problem and words in problem, (bad, problem)


def test_seed_rows_carry_only_checked_templates():
    rows = {r["id"]: r for r in api_seeds.load_rows()}
    assert rows["nasa_epic"]["image_template"] == EPIC_TEMPLATE
    bad = dict(rows["nasa_epic"], image_template="https://evil.example.com/{image}.jpg")
    assert "image_template" in api_seeds.row_problem(bad)
    cands = {c["id"]: c for c in api_seeds.candidates()}
    assert cands["seed:nasa_epic"]["image_template"] == EPIC_TEMPLATE
    checked = feed_generator.check_candidate(cands["seed:nasa_epic"])
    assert checked["image_template"] == EPIC_TEMPLATE
    checked = feed_generator.check_candidate(
        dict(cands["seed:nasa_epic"], image_template="https://a.org/{x}"))
    assert checked["image_template"] is None


# ---------------------------------------------------------------------------
# the same rule for any service whose rows carry pictures
# ---------------------------------------------------------------------------

TV = [{"score": 0.9 - i / 10, "show": {"id": i, "name": f"Show {i}",
                                       "summary": f"<p>About show {i}.</p>",
                                       "image": {"medium": f"https://static.tv.example/{i}.jpg"}}}
      for i in range(5)]
ART = {"data": [{"id": i, "title": f"Work {i}", "artist_display": f"Artist {i}",
                 "image_id": (None if i == 2 else f"abc-{i}")} for i in range(6)]}


def test_rows_with_different_pictures_become_a_gallery():
    draft = feed_generator.draft_feed_app(
        _cand(host="api.tv.example",
              sample_endpoint="https://api.tv.example/search?q=x"),
        build_online=True, probe=_probe(TV))
    card = draft["manifest"]["feed"]["card"]
    assert card["kind"] == "gallery" and card["item_image"] == "show.image.medium"
    assert card["item_title"] == "show.name"
    assert draft["manifest"]["feed"]["service"]["image_host"] == "static.tv.example"
    assert [i["title"] for i in draft["preview"]["items"]][:2] == ["Show 0", "Show 1"]


def test_a_template_on_a_sister_site_skips_rows_without_a_picture():
    template = "https://www.art.example.org/iiif/{image_id}/full/400,/0/default.jpg"
    draft = feed_generator.draft_feed_app(
        _cand(host="api.art.example.org",
              sample_endpoint="https://api.art.example.org/search?q=cats",
              image_template=template),
        build_online=True, probe=_probe(ART))
    card = draft["manifest"]["feed"]["card"]
    assert card["kind"] == "gallery" and card["item_image_template"] == template
    assert draft["manifest"]["feed"]["service"]["image_host"] == "www.art.example.org"
    titles = [i["title"] for i in draft["preview"]["items"]]
    assert "Work 2" not in titles and len(titles) == 5


def test_a_declared_list_with_pictures_is_offered_as_a_gallery_first():
    chart = {"data": [{"name": f"Band {i}", "picture": f"https://cdn.example.net/{i}.jpg"}
                      for i in range(4)]}
    cand = _cand(host="api.music.example", sample_endpoint="https://api.music.example/chart",
                 card={"kind": "list", "items": "data", "item_title": "name",
                       "item_image": "picture", "max_items": 4})
    options = feed_generator.card_options(feed_generator.check_candidate(cand), chart)
    assert [o["kind"] for o in options[:2]] == ["gallery", "list"]
    draft = feed_generator.draft_feed_app(cand, build_online=True, probe=_probe(chart))
    assert draft["manifest"]["feed"]["card"]["kind"] == "gallery"
    assert feed_generator.shows_words(draft["preview"]).startswith("4 pictures: Band 0")


# ---------------------------------------------------------------------------
# pictures go through the backend's store, and are not fetched twice
# ---------------------------------------------------------------------------

def _gallery_manifest():
    return feed_manifest("earthpics", card={
        "kind": "gallery", "title": "Earth", "items": "",
        "item_image_template": "https://img.example.org/{date:year}/{image}.png",
        "item_detail": "date", "caption": "[0].caption", "max_items": 12},
        url="https://api.example.org/v1/natural")


def test_a_gallery_refresh_stores_checked_pictures_and_reuses_them(monkeypatch):
    set_posture("enumerated")
    install(_gallery_manifest())
    feeds.set_allowed("earthpics", True)
    net = fake_network(monkeypatch)
    rows = [{"caption": SAME, "image": f"pic{i}", "date": f"2026-09-2{i} 10:00:00"}
            for i in range(3)]
    net.on("https://api.example.org/", FakeResponse(200, json.dumps(rows).encode()))
    for i in range(3):
        net.on(f"https://img.example.org/2026/pic{i}.png",
               FakeResponse(200, PNG_1PX + bytes([i]), "image/png"))
    net.on("https://img.example.org/2026/pic1.png",
           FakeResponse(200, b"<svg onload=x>", "image/png"))   # lies about its type

    state = feeds.refresh("earthpics", force=True)
    card = state["card"]
    assert state["status"] == "ok" and card["kind"] == "gallery"
    assert card["caption"] == SAME
    # The picture that was not really a PNG is refused and its row dropped.
    assert [i["detail"] if "detail" in i else i["title"] for i in card["items"]] == \
        ["2026-09-20 10:00:00", "2026-09-22 10:00:00"]
    for item in card["items"]:
        assert "image_url" not in item                     # never an outside address
        assert feeds.image_file("earthpics", item["image"])
    picture_calls = [c for c in net.calls if c["url"].startswith("https://img.")]
    assert len(picture_calls) == 3

    # Next refresh: the same addresses are not fetched again.
    later = feeds.refresh("earthpics", force=True, now=__import__("time").time() + 3600)
    assert later["card"]["items"] == card["items"]
    picture_calls = [c for c in net.calls if c["url"].startswith("https://img.")]
    assert len(picture_calls) == 4                         # only the refused one again


def test_the_chat_line_and_readings_read_a_gallery(monkeypatch):
    state = {"name": "Earth", "fetched_at": "2026-09-26T10:00:00+00:00",
             "card": {"kind": "gallery", "caption": SAME,
                      "items": [{"title": "2026-09-24 00:03:42", "image": "a"},
                                {"title": "2026-09-24 01:51:44", "image": "b"}]}}
    line = feeds.card_words_for_chat(state)
    assert "2 pictures" in line and "2026-09-24 01:51:44" in line


# ---------------------------------------------------------------------------
# the home page reads galleries, sizes tiles, and keeps the added order
# ---------------------------------------------------------------------------

def test_the_front_page_lists_cards_in_added_order_with_their_kind(monkeypatch):
    import os
    import time

    from puffpai import app_registry, front_page, plain_words

    set_posture("enumerated")
    install(_gallery_manifest())
    later = feed_manifest("zzreading")
    install(later)
    first = feed_manifest("aareading")
    install(first)
    # Written in this order: gallery, zz, aa. Name order would be aa, earth, zz.
    base = time.time() - 100
    for n, app_id in enumerate(("earthpics", "zzreading", "aareading")):
        path = os.path.join(app_registry.user_apps_dir(), app_id,
                            app_registry.MANIFEST_NAME)
        os.utime(path, (base + n, base + n))
    rows = front_page.cards()
    assert [r["app_id"] for r in rows] == ["earthpics", "zzreading", "aareading"]
    assert [r["kind"] for r in rows] == ["gallery", "value", "value"]
    line = front_page._card_line({"card": {"kind": "gallery", "items": [
        {"title": "2026-09-24 00:03:42"}, {"title": "2026-09-24 01:51:44"}]}})
    assert line == "2 pictures: 2026-09-24 00:03:42; 2026-09-24 01:51:44"
    assert "kind" not in front_page.owner_text({"cards": rows}).split("\n")
    assert "gallery" not in front_page.owner_text({"cards": rows})


def test_new_owner_words_are_plain():
    from puffpai import plain_words
    for text in ("2 pictures: a; b", "Picture 3",
                 "And 6 more pictures on its page.", "Live on Infinity",
                 feed_generator.shows_words({"kind": "gallery", "items": [
                     {"title": "Saturn"}, {"title": "Mars"}]})):
        assert plain_words.check(text) == [], text
