"""The Assemble step, core: plan per capability, one sample's shape, the
``assembled_app/v1`` spec the brain fills by picking from lists, the proof
with a real ask, the report, the wizard step and the routes.

Nothing goes online: samples are hand-written files in tests/fixtures/assemble,
the Bridge is a fake getter, the brain is a fake.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest

from puffpai import brain_tools
from puffpai.assemble import accept, api, plan, report, shape, spec
from puffpai.security import egress_doors

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "assemble")
EPIC_TMPL = ("https://epic.gsfc.nasa.gov/archive/natural/{date:year}/{date:month}/"
             "{date:day}/jpg/{image}.jpg")
CAP = {"id": "cap-see-earth-from-space", "name": "See Earth from space",
       "does": "Shows the newest photos of the whole Earth."}


def sample(name: str) -> str:
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


class Resp:
    def __init__(self, text: str = "", code: int = 200):
        self.status_code = code
        self._body = text.encode("utf-8")

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        pass


def getter_for(text: str, seen: list | None = None):
    def get(door, url, **kw):
        if seen is not None:
            seen.append((door, url))
        return Resp(text)
    return get


def blocked(door, url, **kw):
    raise egress_doors.EgressBlocked(door, "switch off", "Turn it on under What leaves "
                                                         "your computer.")


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delitem(sys.modules, api.SERVER_MODULE, raising=False)
    monkeypatch.delitem(sys.modules, api.TOOLSMITH_MODULE, raising=False)
    yield tmp_path
    brain_tools.unregister_provider(api.PROVIDER)


def epic_part(door: str = "app:nasa_epic:feed") -> dict:
    found = shape.shape(sample("nasa_epic.json"))
    return {"source_id": "s1", "kind": "http_json", "url": "https://epic.gsfc.nasa.gov/api/natural",
            "door": door, "service": "nasa_epic", "image_template": EPIC_TMPL,
            "found": {"arrays": found["arrays"], "fields": found["fields"]}}


def field_n(part: dict, path: str) -> int:
    return next(f["n"] for f in part["found"]["fields"] if f["path"] == path)


# ── shape: one sample, numbered fields ──────────────────────────────────────

def test_nasa_epic_sample_lists_the_photo_name_date_and_map_position():
    got = shape.shape(sample("nasa_epic.json"))
    assert got["kind"] == "json"
    assert [a["path"] for a in got["arrays"]] == ["$[*]"]
    roles = {f["path"]: f["role"] for f in got["fields"]}
    assert roles["caption"] == "title"
    assert roles["image"] == "image_ref"        # a picture's name, needs the address
    assert roles["date"] == "date"
    assert roles["centroid_coordinates.lat"] == "lat"
    assert roles["centroid_coordinates.lon"] == "lon"
    assert [f["n"] for f in got["fields"]] == list(range(1, len(got["fields"]) + 1))


def test_seventimer_sample_finds_the_forecast_rows():
    got = shape.shape(sample("seventimer.json"))
    assert [(a["path"], a["count"]) for a in got["arrays"]] == [("$.dataseries[*]", 3)]
    roles = {f["path"]: f["role"] for f in got["fields"]}
    assert roles["timepoint"] == "date"
    assert roles["cloudcover"] == "number" and roles["wind10m.speed"] == "number"


def test_deezer_sample_finds_song_previews_and_covers():
    got = shape.shape(sample("deezer.json"))
    roles = {f["path"]: f["role"] for f in got["fields"]}
    assert got["arrays"][0]["path"] == "$.data[*]"
    assert roles["preview"] == "audio"
    assert roles["album.cover_medium"] == "image"
    assert roles["title"] == "title"


def test_rss_feed_is_read_into_items():
    got = shape.shape(sample("feed.rss"))
    assert got["kind"] == "rss" and got["arrays"][0]["path"] == "$.items[*]"
    roles = {f["path"]: f["role"] for f in got["fields"]}
    assert roles == {"title": "title", "image": "image", "pubdate": "date",
                     "description": "text", "link": "link"}


def test_stream_list_finds_hls_links_and_logos():
    got = shape.shape(sample("channels.m3u"))
    assert got["kind"] == "m3u"
    roles = {f["path"]: f["role"] for f in got["fields"]}
    assert roles["url"] == "hls" and roles["tvg_logo"] == "image"
    assert got["data"]["items"][1]["title"] == "Space Station View"


def test_youtube_links_and_unsafe_xml():
    assert shape.role_of("video", "https://www.youtube.com/watch?v=abc") == "youtube"
    assert shape.parse('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>')[0] == "text"


def test_rows_from_fills_the_picture_address_template():
    _kind, data = shape.parse(sample("nasa_epic.json"))
    rows = shape.rows_from(data, "$[*]", {"title": "caption",
                                          "image": "tmpl:" + EPIC_TMPL,
                                          "lat": "centroid_coordinates.lat"})
    assert rows[0]["image"] == ("https://epic.gsfc.nasa.gov/archive/natural/2026/10/06/"
                                "jpg/epic_1b_20261006003633.jpg")
    assert rows[0]["lat"] == 5.18
    assert shape.fill_template("https://x/{missing}.jpg", {}) == ""


def test_fetch_sample_goes_through_the_bridge_under_the_parts_switch():
    seen: list = []
    got = shape.fetch_sample("app:nasa_epic:feed", "https://epic.gsfc.nasa.gov/api/natural",
                             getter=getter_for(sample("nasa_epic.json"), seen))
    assert got["ok"] and seen == [("app:nasa_epic:feed", "https://epic.gsfc.nasa.gov/api/natural")]


def test_a_switch_that_is_off_is_recorded_and_nothing_is_fetched():
    got = shape.fetch_sample("app:nasa_epic:feed", "https://epic.gsfc.nasa.gov/x",
                             getter=blocked)
    assert got == {"ok": False, "door": "app:nasa_epic:feed",
                   "url": "https://epic.gsfc.nasa.gov/x", "switch_off": True,
                   "reason": shape.SWITCH_OFF,
                   "said": "Turn it on under What leaves your computer."}
    none = shape.fetch_sample(None, "https://x.example/a", getter=getter_for("{}"))
    assert none["switch_off"] and none["reason"] == shape.NOT_ADDED


def test_a_sample_too_big_is_refused():
    got = shape.fetch_sample("d", "https://x.example/a", getter=getter_for("x" * 100),
                             max_bytes=10)
    assert not got["ok"] and "too big" in got["reason"]


# ── spec: the validator ─────────────────────────────────────────────────────

def good_spec() -> dict:
    return {"schema": "assembled_app/v1", "id": "see_earth", "title": "Earth from space",
            "caps": [CAP["id"]],
            "settings": [{"key": "home_town", "kind": "place", "use": "home_location"}],
            "sources": [{"id": "pics", "kind": "http_json", "service": "nasa_epic",
                         "door": "app:nasa_epic:feed",
                         "url": "https://epic.gsfc.nasa.gov/api/natural", "items": "$[*]",
                         "fields": {"title": "caption", "image": "tmpl:" + EPIC_TMPL},
                         "args": {"lat": "setting:home_town.lat"}}],
            "views": [{"type": "gallery", "source": "pics", "lightbox": True},
                      {"type": "settings", "source": ""}],
            "actions": [{"id": "get_pics", "source": "pics", "input": {}}],
            "checks": [{"cap": CAP["id"], "ask": "Show me Earth today", "action": "get_pics",
                        "input": {}, "expect": {"min_items": 1, "fields": ["image"]}}]}


def test_a_good_spec_validates():
    assert spec.validate(good_spec()) == []


@pytest.mark.parametrize("change, words", [
    (lambda s: s["views"].append({"type": "carousel", "source": "pics"}), "views[2].type"),
    (lambda s: s["checks"].clear(), "has no check"),
    (lambda s: s["sources"][0]["args"].update(lon="setting:town.lon"), "not listed"),
    (lambda s: s["views"].append({"type": "player", "source": "pics"}), "needs mode"),
    (lambda s: s["sources"][0].update(url="http://plain.example/"), "https"),
    (lambda s: s["sources"][0].update(items="dataseries"), "path like"),
    (lambda s: s.update(id="Bad Id"), "id must be"),
])
def test_the_validator_names_what_is_wrong(change, words):
    s = good_spec()
    change(s)
    errors = spec.validate(s)
    assert any(words in e for e in errors), errors


# ── spec: the brain picks, the module writes ────────────────────────────────

def picks_for(part: dict, **over) -> dict:
    p = {"title": "Earth today",
         "views": [{"view": spec.VIEWS.index("gallery") + 1, "source": "s1", "list": 1}],
         "fields": {"s1": {"title": field_n(part, "caption"), "image": field_n(part, "image"),
                           "when": field_n(part, "date")}},
         "checks": [{"cap": 1, "source": "s1", "ask": "Show me Earth today", "q": ""}]}
    p.update(over)
    return p


def test_the_brain_only_picks_numbers_and_the_spec_is_written_for_it():
    part = epic_part()
    asked: list = []

    def ask(messages):
        asked.append(messages)
        return json.dumps(picks_for(part))
    made = spec.build("see_earth", [CAP], [part], ask=ask)
    assert made["how"] == "brain" and made["rounds"] == 1
    s = made["spec"]
    assert spec.validate(s) == []
    assert s["sources"][0]["fields"]["image"] == "tmpl:" + EPIC_TMPL
    # Each view carries its source's labels, from the source's own field names.
    assert s["views"][0] == {"type": "gallery", "source": "s1", "lightbox": True,
                             "labels": {"title": "Caption", "image": "Image", "when": "Date"}}
    assert s["checks"][0]["expect"] == {"min_items": 1, "fields": ["image"],
                                        "views": ["gallery"]}
    prompt_text = asked[0][0]["content"]
    assert "1. gallery" in prompt_text and "[image_ref]" in prompt_text
    assert "—" not in prompt_text


def test_a_wrong_answer_gets_the_validator_words_back_and_is_repaired():
    part = epic_part()
    answers = [json.dumps(picks_for(part, views=[{"view": 42, "source": "s1", "list": 1}])),
               json.dumps(picks_for(part))]
    seen: list = []

    def ask(messages):
        seen.append(messages)
        return answers.pop(0)
    made = spec.build("see_earth", [CAP], [part], ask=ask)
    assert made["how"] == "brain" and made["rounds"] == 2
    assert "view 42 is not on the list" in seen[1][-1]["content"]


def test_after_two_repairs_the_fixed_fallback_is_used():
    part = epic_part()
    calls = []

    def ask(messages):
        calls.append(1)
        return "I think a gallery would be lovely."
    made = spec.build("see_earth", [CAP], [part], ask=ask)
    assert len(calls) == 1 + spec.REPAIR_ROUNDS
    assert made["how"] == "fallback" and made["fallback_problems"] == []
    assert made["spec"]["views"][0]["type"] == "gallery"


def test_fallback_is_a_forecast_for_times_with_weather_readings():
    found = shape.shape(sample("seventimer.json"))
    part = {"source_id": "s1", "kind": "http_json", "door": "app:seventimer:feed",
            "url": "https://www.7timer.info/bin/astro.php?lon={lon_round}&lat={lat_round}"
                   "&ac=0&unit=metric&output=json",
            "found": {"arrays": found["arrays"], "fields": found["fields"]}}
    cap = {"id": "cap-stargazing-forecast", "name": "Stargazing forecast", "does": ""}
    s = spec.fallback("stargazing", [cap], [part])
    assert spec.validate(s) == []
    assert s["views"][0]["type"] == "forecast"
    assert s["sources"][0]["labels"]["cloudcover"] == "Cloud cover"
    # The address needs her town: a place setting from the bot's own home town.
    assert s["settings"] == [{"key": "home_town", "kind": "place", "use": "home_location",
                              "label": "Your town"}]
    assert s["sources"][0]["args"] == {"lon_round": "setting:home_town.lon",
                                       "lat_round": "setting:home_town.lat"}


def test_no_brain_at_all_still_ends_in_a_spec():
    def ask(messages):
        raise ConnectionError("no ollama")
    made = spec.build("see_earth", [CAP], [epic_part()], ask=ask)
    assert made["how"] == "fallback" and spec.validate(made["spec"]) == []


def test_a_place_setting_uses_the_bots_home_town(state, monkeypatch):
    from puffpai import home_location
    monkeypatch.setattr(home_location, "get_home", lambda: {
        "latitude": 40.0, "longitude": -105.3, "label": "Boulder"})
    s = good_spec()
    args, missing = spec.resolve_args("see_earth", s, {"lat": "setting:home_town.lat",
                                                       "x": 1})
    assert args == {"lat": 40.0, "x": 1} and missing == []
    monkeypatch.setattr(home_location, "get_home", lambda: None)
    assert spec.resolve_args("see_earth", s, {"lat": "setting:home_town.lat"}) == ({}, ["home_town"])
    s["settings"][0].pop("use")
    spec.save_setting("see_earth", "home_town", {"lat": 51.4779, "lon": -0.0015, "name": "Here"})
    assert spec.resolve_place("see_earth", s["settings"][0]) == {
        "lat": 51.5, "lon": -0.0, "name": "Here"}


# ── plan: use as is, put together, or nothing ───────────────────────────────

SEEDS = [{"id": "nasa_epic", "name": "NASA EPIC Earth photos", "host": "epic.gsfc.nasa.gov",
          "sample_endpoint": "https://epic.gsfc.nasa.gov/api/natural",
          "image_template": EPIC_TMPL, "match_terms": ["earth", "space", "photo"],
          "topics": ["space"], "needs": []},
         {"id": "deezer", "name": "Deezer", "host": "api.deezer.com",
          "sample_endpoint": "https://api.deezer.com/chart", "match_terms": ["music", "song"],
          "topics": ["music"], "needs": []}]


def test_a_skill_that_covers_it_is_used_as_is():
    row = plan.decide(CAP, [{"id": "skill-x", "kind": "skill", "name": "Earth Coach",
                             "ref": "x"}], [])
    assert row["decision"] == plan.USE_AS_IS and row["app_id"] == ""
    assert row["uses"][0]["how"] == "a skill your bot follows"


def test_only_parts_means_put_it_together():
    seeds = plan.matching_seeds(CAP, SEEDS)
    assert [s["id"] for s in seeds] == ["nasa_epic"]
    row = plan.decide(CAP, [], seeds)
    assert row["decision"] == plan.ASSEMBLE
    assert row["app_id"] == "see_earth_from_space"
    part = row["parts"][0]
    assert part["kind"] == "seed" and part["door"] == ""      # not switched on yet
    assert part["image_template"] == EPIC_TMPL
    # A live card already reading that host gives its switch.
    row = plan.decide(CAP, [], seeds, {"epic.gsfc.nasa.gov": "app:epic:feed"})
    assert row["parts"][0]["door"] == "app:epic:feed"


def test_nothing_means_not_found():
    row = plan.decide({"id": "cap-fold-laundry", "name": "Fold laundry", "does": ""}, [], [])
    assert row["decision"] == plan.NOT_FOUND and row["why"] == "Nothing found for this yet."


def test_the_plan_is_kept(state, monkeypatch):
    from puffpai.finding import capabilities
    monkeypatch.setattr(capabilities, "load", lambda: [CAP])
    monkeypatch.setattr(capabilities, "covering", lambda caps=None: {})
    monkeypatch.setattr(plan, "_seed_rows", lambda: SEEDS)
    monkeypatch.setattr(plan, "_live_doors", lambda: {})
    made = plan.build_plan()
    assert plan.load_plan() == made
    assert made["capabilities"][0]["decision"] == plan.ASSEMBLE
    assert os.path.isfile(os.path.join(str(state), "state", "assemble", "plan.json"))


def test_app_ids_are_ones_the_app_host_accepts():
    from puffpai.assemble import APP_ID_RE
    for cap in ("cap-see-earth-from-space", "cap-3d-models", "cap-x",
                "cap-" + "a-very-long-name" * 5):
        assert APP_ID_RE.match(plan.app_id_for(cap)), cap


# ── accept: the action works, and the brain uses it ─────────────────────────

def test_check_result_needs_rows_filled_fields_and_a_picture_never_fails_it():
    ok_reach = lambda door, url: (True, "")  # noqa: E731
    rows = [{"title": "Earth", "image": "https://epic.gsfc.nasa.gov/a.jpg"}]
    exp = {"min_items": 1, "fields": ["image"]}
    assert accept.check_result({"ok": True, "rows": rows}, exp, reach=ok_reach) == (True, "", 1)
    assert accept.check_result({"ok": True, "rows": []}, exp, reach=ok_reach)[1] == \
        "it answered 0 items, not 1"
    assert accept.check_result({"ok": True, "rows": [{"image": ""}]}, exp,
                               reach=ok_reach)[1] == "its image was empty"
    # A picture that does not load is a "No picture" tile: the rows decide.
    blocked = accept.check_result({"ok": True, "rows": rows}, exp,
                                  reach=lambda d, u: (False, "the Bridge does not let it reach x"))
    assert blocked == (True, "", 1)
    assert accept.check_result({"ok": False, "reason": "still being built"}, exp)[1] == \
        "still being built"


def test_reachable_uses_the_bridge_and_checks_stream_lists():
    calls = []

    def req(door, method, url, **kw):
        calls.append((door, method))
        return Resp("#EXTM3U\n#EXT-X-VERSION:3\n" if url.endswith(".m3u8") else "", 200)
    assert accept.reachable("app:x:feed", "https://a.example/p.jpg", requester=req) == (True, "")
    assert calls == [("app:x:feed", "HEAD")]
    assert accept.reachable("app:x:feed", "https://a.example/live.m3u8", requester=req)[0]
    assert accept.reachable("", "https://a.example/p.jpg", requester=req)[0] is False

    def refuse(door, method, url, **kw):
        raise egress_doors.EgressBlocked(door, "host not allowed", "no")
    ok, why = accept.reachable("app:x:feed", "https://cdn.example/p.jpg", requester=refuse)
    assert not ok and "cdn.example" in why


class FakeBrain:
    """Calls one tool, then answers."""

    def __init__(self, tool: str | None, args: dict | None = None):
        self.tool, self.args, self.n = tool, args or {}, 0

    def chat(self, messages, temperature=0.3, max_tokens=1024, tools=None):
        self.n += 1
        if self.n == 1 and self.tool:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"function": {"name": self.tool, "arguments": self.args}}]}
        return {"role": "assistant", "content": "Here are the newest pictures of Earth.",
                "tool_calls": []}


def fake_server(monkeypatch, rows_for):
    mod = types.ModuleType(api.SERVER_MODULE)
    mod.start_assembled = lambda app_id: {"ok": True, "port": 45678}
    mod.call_action = lambda app_id, action, inp: rows_for(app_id, action, inp)
    monkeypatch.setitem(sys.modules, api.SERVER_MODULE, mod)
    return mod


def test_the_ask_passes_only_when_the_right_tool_ran_and_gave_rows(state, monkeypatch):
    s = good_spec()
    api.save_spec(s)
    fake_server(monkeypatch, lambda a, b, c: {"ok": True, "rows": [{"title": "Earth"}]})
    check = s["checks"][0]
    ok, why, used = accept.ask_through_brain("see_earth", s, check,
                                             backend=FakeBrain("see_earth__get_pics"))
    assert (ok, why, used) == (True, "", ["see_earth__get_pics"])
    ok, why, _ = accept.ask_through_brain("see_earth", s, check, backend=FakeBrain(None))
    assert not ok and why == "the brain answered without using the app"
    fake_server(monkeypatch, lambda a, b, c: {"ok": True, "rows": []})
    ok, why, _ = accept.ask_through_brain("see_earth", s, check,
                                          backend=FakeBrain("see_earth__get_pics"))
    assert not ok and "gave nothing back" in why


def test_the_proof_offers_only_that_apps_tools(state, monkeypatch):
    s = good_spec()
    api.save_spec(s)
    other = dict(good_spec(), id="other_app")
    api.save_spec(other)
    api.register_tools()
    names = {t.name for t in brain_tools.available({"assemble_app": "see_earth"})
             if t.source == api.PROVIDER}
    assert names == {"see_earth__get_pics"}
    # In chat, only apps whose capability was proven.
    assert not [t for t in brain_tools.available({}) if t.source == api.PROVIDER]
    report.write([report.entry({"cap": CAP["id"], "name": CAP["name"]}, report.WORKING)])
    names = {t.name for t in brain_tools.available({}) if t.source == api.PROVIDER}
    assert names == {"see_earth__get_pics", "other_app__get_pics"}


# ── the seams for the other parts ───────────────────────────────────────────

def _without_parts(monkeypatch):
    """As if the app server and Toolsmith parts were not in this build."""
    real = api.importlib.import_module

    def gone(name, *a, **kw):
        if name in (api.SERVER_MODULE, api.TOOLSMITH_MODULE):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real(name, *a, **kw)
    monkeypatch.setattr(api.importlib, "import_module", gone)


def test_parts_not_in_this_build_say_still_being_built(state, monkeypatch):
    _without_parts(monkeypatch)
    assert api.call_action("see_earth", "get_pics", {}) == {
        "ok": False, "rows": [], "reason": "still being built"}
    assert api.start_assembled("see_earth")["reason"] == "still being built"
    assert api.run_toolsmith("see_earth", CAP, [], api.BUDGET) == {
        "ok": False, "glue": [], "reason": "still being built"}


def test_fetch_source_reads_rows_through_the_sources_own_switch(state, monkeypatch):
    s = good_spec()
    s["settings"], s["views"] = [], s["views"][:1]
    s["sources"][0].pop("args")
    api.save_spec(s)
    seen: list = []
    got = api.fetch_source("see_earth", "pics", {"q": "dscovr"},
                           getter=getter_for(sample("nasa_epic.json"), seen))
    assert got["ok"] and len(got["rows"]) == 2
    assert seen == [("app:nasa_epic:feed", "https://epic.gsfc.nasa.gov/api/natural")]
    assert api.fetch_source("see_earth", "pics", {"q": "volcano"},
                            getter=getter_for(sample("nasa_epic.json")))["rows"] == got["rows"]
    # (nothing matched "volcano": the newest items, never an empty answer)
    off = api.fetch_source("see_earth", "pics", {}, getter=blocked)
    assert off["fix"] == {"kind": "switch_on", "door": "app:nasa_epic:feed",
                          "service": "nasa_epic"}
    assert off["reason"] == "the Bridge switch for nasa_epic is off"


def test_fill_url_rounds_the_town_and_fills_the_date():
    url, missing = api.fill_url("https://x.example/?lat={lat_round}&lon={lon_round}&d={date}",
                                {"lat": 40.0149, "lon": -105.27})
    assert url.startswith("https://x.example/?lat=40.0&lon=-105.3&d=2") and missing == []
    assert api.fill_url("https://x.example/{topic}", {})[1] == ["topic"]


# ── the whole run ───────────────────────────────────────────────────────────

def prepare_run(monkeypatch):
    from puffpai.finding import capabilities
    monkeypatch.setattr(capabilities, "load", lambda: [CAP])
    monkeypatch.setattr(capabilities, "covering", lambda caps=None: {})
    monkeypatch.setattr(plan, "_seed_rows", lambda: SEEDS)
    monkeypatch.setattr(plan, "_live_doors", lambda: {"epic.gsfc.nasa.gov": "app:epic:feed"})
    # The owner's one yes for this app (the gate itself: test_assemble_consent.py).
    from puffpai.assemble import consent
    monkeypatch.setattr(consent, "has_yes", lambda app_id: True)


def picks_ask(messages):
    text = messages[0]["content"]
    # Find the field numbers the way the brain would: from the numbered list.
    nums = {}
    for line in text.splitlines():
        line = line.strip()
        for path in ("caption", "image", "date"):
            if line.split(". ", 1)[-1].startswith(f"{path} ["):
                nums[path] = int(line.split(".", 1)[0])
    return json.dumps({"title": "Earth today",
                       "views": [{"view": 1, "source": "s1", "list": 1}],
                       "fields": {"s1": {"title": nums["caption"], "image": nums["image"]}},
                       "checks": [{"cap": 1, "source": "s1", "ask": "Show me Earth today"}]})


def test_the_whole_run_proves_a_capability_and_coverage_says_working(state, monkeypatch):
    prepare_run(monkeypatch)
    getter = getter_for(sample("nasa_epic.json"))
    fake_server(monkeypatch, lambda app_id, action, inp: api.fetch_source(
        app_id, action.split("_", 1)[1], inp, getter=getter))
    lines: list = []
    from puffpai import agent_activity
    monkeypatch.setattr(agent_activity, "record", lambda agent, task, **kw: lines.append(
        (agent, task)))
    data = api.run_all(ask=picks_ask, getter=getter, reach=lambda d, u: (True, ""),
                       backend=FakeBrain("see_earth_from_space__get_s1"))
    row = data["capabilities"][0]
    assert row["state"] == report.WORKING, row
    assert row["line"] == "Working: proven with a real question."
    assert row["checks"][0]["tool_ran"] is True and row["checks"][0]["rows"] == 2
    assert ("majordomo", "Putting together: 1 of 1 (See Earth from space)") in lines
    view = api.report_view()["apps"]
    assert view == [{"id": "see_earth_from_space", "title": "Earth today",
                     "thing": "assembled-see_earth_from_space", "caps": [{
                         "cap": CAP["id"], "name": CAP["name"], "state": "working",
                         "reason": "", "line": row["line"], "fix": None,
                         "card": None}]}]
    assert api.read_status()["state"] == "done"
    assert api.load_spec("see_earth_from_space")["views"][0]["type"] == "gallery"
    from puffpai.finding import capabilities
    monkeypatch.setattr(capabilities, "_covering", lambda have=None, caps=None: {})
    cov = capabilities.coverage()
    assert cov[0]["working"] is True and cov[0]["proof"].startswith("Working")


def test_without_the_app_server_it_is_not_yet_still_being_built(state, monkeypatch):
    _without_parts(monkeypatch)
    prepare_run(monkeypatch)
    getter = getter_for(sample("nasa_epic.json"))
    data = api.run_all(ask=picks_ask, getter=getter)
    row = data["capabilities"][0]
    assert row["state"] == report.NOT_YET
    assert row["line"] == "Not yet: still being built."
    assert report.is_working(CAP["id"]) is False


def test_the_real_app_server_starts_and_the_proof_goes_through_the_bridge(state, monkeypatch):
    """The real path (the assembled app server): the app's own server
    starts on 127.0.0.1, the proof runs its action, and the action's outside
    fetch goes through the Bridge, whose switch for it is off (as it ships)."""
    from puffpai.assemble.server import host
    from puffpai.integrate import run_here
    prepare_run(monkeypatch)
    hid = host.host_id("see_earth_from_space")
    try:
        data = api.run_all(ask=picks_ask, getter=getter_for(sample("nasa_epic.json")))
        assert run_here.status(hid)["state"] in ("running", "starting")
        assert run_here.read_state(hid)["plan"]["how"] == "assembled"
    finally:
        run_here.stop(hid, by="owner")
    assert run_here.status(hid)["state"] == "stopped"
    row = data["capabilities"][0]
    assert row["state"] == report.NOT_YET
    # The Bridge's own refusal (this test registers no door for it), said
    # as it is: never "the switch is off" when that is not what happened.
    assert row["line"] == "Not yet: NASA EPIC Earth photos has no place on the Bridge yet."
    assert row["checks"][0]["action"] == "get_s1" and row["checks"][0]["rows"] == 0
    assert report.is_working(CAP["id"]) is False


def test_a_switch_that_is_off_is_not_yet_with_a_fix(state, monkeypatch):
    prepare_run(monkeypatch)
    data = api.run_all(ask=picks_ask, getter=blocked)
    row = data["capabilities"][0]
    assert row["state"] == report.NOT_YET
    assert row["reason"] == "the Bridge switch for NASA EPIC Earth photos is off"
    assert row["fix"] == {"kind": "switch_on", "door": "app:epic:feed", "service": "nasa_epic"}


def test_plain_words_in_what_she_reads(state, monkeypatch):
    prepare_run(monkeypatch)
    data = api.run_all(ask=picks_ask, getter=blocked)
    shown = [r[k] for r in data["capabilities"] for k in ("line", "reason")]
    shown.append(api.read_status()["line"])
    for line in shown:
        for bad in ("—", "door", "beta"):
            assert bad not in line.lower(), (bad, line)


# ── the wizard step and the routes ──────────────────────────────────────────

def test_the_assemble_package_loads_and_sits_between_integrate_and_prove():
    from puffpai.wizard import loader, runner
    packages, refusals = loader.load_builtin_packages()
    assert "assemble" in packages and not [r for r in refusals if "assemble" in r]
    assert packages["assemble"].writes == "assemble"
    entries = [s.entry for s in runner.SCHEDULE]
    assert entries.index("integrate.card") < entries.index("assemble.plan") \
        < entries.index("prove.check")


def test_a_waiting_plan_shows_on_the_status_view(state, monkeypatch):
    # The wizard step plans through the one build pipeline now
    # (test_build_wire.py); the older per-capability plan is still made by
    # plan.build_plan and still shows on the team page.
    prepare_run(monkeypatch)
    plan.build_plan()
    api.mark_waiting()
    assert api.read_status()["state"] == "waiting"
    assert plan.load_plan()["capabilities"][0]["app_id"] == "see_earth_from_space"
    from puffpai import agent_activity
    agent_activity.record("majordomo", "Planned: 1 to put together")
    monkeypatch.setattr(api, "resume_waiting", lambda delay=0.0: False)
    view = api.status_view()
    assert view["plan"] == [{"cap": CAP["id"], "name": CAP["name"],
                             "how": "Putting it together from parts", "decision": "assemble",
                             "why": "Putting it together from 1 part.",
                             "app_id": "see_earth_from_space",
                             "finds": ["NASA EPIC Earth photos"]}]
    assert view["lines"][-1]["agent"] == "majordomo"
    assert view["lines"][-1]["text"] == "Planned: 1 to put together"


@pytest.fixture
def client(state, monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    monkeypatch.setattr(api, "_device", lambda: "device:abc")
    return app.test_client()


def test_routes_need_a_paired_device(state, monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    monkeypatch.setattr(api, "_device", lambda: "")
    c = app.test_client()
    for method, path in (("get", "/assemble/status"), ("post", "/assemble/run"),
                         ("get", "/assemble/report")):
        assert getattr(c, method)(path).status_code == 403


def test_status_report_and_put_it_together(client, monkeypatch):
    started = []
    monkeypatch.setattr(api, "start_background", lambda **kw: started.append(kw) or True)
    got = client.get("/assemble/status").get_json()
    assert got["ok"] and got["running"] is False and got["plan"] == [] and got["lines"] == []
    assert got["status"]["state"] == "idle"
    assert client.get("/assemble/report").get_json()["apps"] == []
    r = client.post("/assemble/run", json={"cap": CAP["id"]})
    assert r.status_code == 202 and started == [{"cap": CAP["id"]}]
    assert client.post("/assemble/run", json={"cap": "../x"}).status_code == 400


def test_the_backend_registers_the_routes():
    import importlib
    src = importlib.util.find_spec("puffpai.api_server").origin
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert "'puffpai.assemble.api'" in text and "_start_assemble()" in text


def test_rows_timed_from_a_start_and_an_offset():
    """A forecast answer's rows are "init" plus "timepoint" hours: the real
    moment is what the page gets (it shows it in her own time)."""
    data = {"init": "2026101006", "dataseries": [{"timepoint": 3}, {"timepoint": 27}]}
    rows = [{"when": 3}, {"when": 27}]
    got = api.timed_rows(rows, data, {"start": "init", "format": "%Y%m%d%H", "unit": "hours",
                                      "field": "when"})
    assert [r["when"] for r in got] == ["2026-10-10T09:00:00Z", "2026-10-11T09:00:00Z"]
    assert api.timed_rows(rows, data, None) == rows


def test_the_jobs_view_opens_first():
    spec = {"views": [{"type": "gallery", "source": "s1"}, {"type": "list", "source": "s2"}],
            "sources": [], "checks": []}
    got = api.with_view(spec, "list")
    assert [v["type"] for v in got["views"]] == ["list", "gallery"]
    assert api.with_view(spec, "gallery") is spec


def test_chat_gets_times_in_this_computers_own_time_with_the_zone():
    items, zone = api.local_times([{"when": "2026-10-12T18:00:00Z", "title": "Launch"},
                                   {"when": "2026-10-12"}])
    assert zone and items[0]["title"] == "Launch" and "Oct 12" in items[0]["when"] \
        and "T18" not in items[0]["when"]
    assert items[1]["when"] == "2026-10-12", "a date alone is left as it is"


def test_a_view_with_nothing_to_draw_goes():
    spec = {"views": [{"type": "gallery", "source": "s1"}, {"type": "gallery", "source": "s2"}],
            "sources": [{"id": "s1", "fields": {"title": "t", "image": "i"}},
                        {"id": "s2", "fields": {"link": "href"}}],
            "actions": [{"id": "get_s1", "source": "s1"}, {"id": "search_s2", "source": "s2"}],
            "checks": [{"action": "get_s1", "expect": {"views": ["gallery"]}},
                       {"action": "search_s2", "expect": {"views": ["gallery"]}}]}
    import puffpai.assemble.spec as spec_mod
    orig = spec_mod.validate
    try:
        spec_mod.validate = lambda s: []
        got = api.drawable_views(spec)
    finally:
        spec_mod.validate = orig
    assert [v["source"] for v in got["views"]] == ["s1"]
    assert [c["action"] for c in got["checks"]] == ["get_s1"]


def test_a_failing_extra_source_leaves_the_page_when_the_jobs_own_works(monkeypatch):
    import puffpai.assemble.spec as spec_mod
    monkeypatch.setattr(spec_mod, "validate", lambda s: [])
    spec = {"sources": [{"id": "s1"}, {"id": "s2"}],
            "actions": [{"id": "get_s1", "source": "s1"}, {"id": "search_s2", "source": "s2"}],
            "views": [{"type": "gallery", "source": "s1"}, {"type": "list", "source": "s2"}],
            "checks": [{"action": "get_s1"}, {"action": "search_s2"}]}
    proof = {"ok": False, "checks": [{"action": "get_s1", "ok": True},
                                     {"action": "search_s2", "ok": False}]}
    got = api.without_failed_extras(spec, proof)
    assert [v["source"] for v in got["views"]] == ["s1"] and len(got["checks"]) == 1
    proof["checks"][0]["ok"] = False
    assert api.without_failed_extras(spec, proof) is None, "the job's own source must work"


def test_a_pictures_only_site_is_checked_with_a_plain_get():
    from puffpai.security import egress_doors
    calls = []

    class Resp:
        status_code = 200

        def close(self):
            pass

    def req(door, method, url, **kw):
        calls.append(method)
        if method == "HEAD":
            raise egress_doors.EgressBlocked(door, "a pictures-only site gets a plain GET only",
                                             "no")
        return Resp()
    assert accept.reachable("app:x:feed", "https://pics.example/p.jpg", requester=req) == (True, "")
    assert calls == ["HEAD", "GET"]


def test_two_views_of_one_kind_are_named_by_what_they_hold():
    spec = {"views": [{"type": "list", "source": "s1"}, {"type": "list", "source": "s2"},
                      {"type": "settings", "source": ""}],
            "sources": [{"id": "s1", "name": "Launch Library 2 (The Space Devs)",
                         "about": "Upcoming rocket launches from around the world, with who is flying"},
                        {"id": "s2", "name": "Spaceflight News API",
                         "about": "Latest space news stories collected from many news websites."}]}
    got = api.named_tabs(spec)
    assert [v.get("title") for v in got["views"]] == ["Rocket launches", "Space news stories", None]


def test_a_slow_service_is_asked_again(monkeypatch):
    monkeypatch.setattr(shape, "FETCH_BACKOFF_S", (0.0, 0.0))
    tries = []

    class Resp:
        status_code = 200

        def iter_content(self, n):
            yield b'{"a": [1]}'

        def close(self):
            pass

    def flaky(door, url, **kw):
        tries.append(1)
        if len(tries) < 3:
            raise TimeoutError("slow")
        return Resp()
    got = shape.fetch_sample("app:x:feed", "https://slow.example/a", getter=flaky)
    assert got["ok"] and len(tries) == 3

    def down(door, url, **kw):
        raise TimeoutError("slow")
    got = shape.fetch_sample("app:x:feed", "https://slow.example/a", getter=down)
    assert not got["ok"] and got.get("transient") and "did not answer" in got["reason"]


def test_toolsmith_takes_a_job_whose_sources_answer_the_wrong_way():
    assert api._toolsmith_can_help({"ok": False, "reason": "it shows only a name, nothing more"})  # noqa: SLF001
    for why in ("7Timer: it did not answer (ConnectTimeout)", "your town is not set yet",
                "the Bridge switch for X is off"):
        assert not api._toolsmith_can_help({"ok": False, "reason": why}), why  # noqa: SLF001
