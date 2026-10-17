"""Quality fixes for the Assemble step.

1. A live card keeps its host's picture address template, so a gallery has
   pictures.
2. Website rows go to capabilities by meaning; a check must fit its
   capability (the view its name asks for, an ask about it).
3. The proof depends on the view: pictures that load, a forecast with times
   and readings, a map with positions, a player with a stream; a name alone
   proves nothing.
4. Labels come from the source's own field names and the brain's short words
   from its docs; value meanings are kept.
5. Proven app tools on both chat paths, a "Your apps" note and a nudge.
6. Each assembled app is a thing with its own page.
7. The console path keeps capabilities too.

Nothing goes online: samples are fixture files, the Bridge and the app server
are fakes, the brain is scripted.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest

from puffpai import brain_tools
from puffpai.assemble import accept, api, apps_note, plan, report, shape, spec

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "assemble")
EPIC_TMPL = ("https://epic.gsfc.nasa.gov/archive/natural/{date:year}/{date:month}/"
             "{date:day}/jpg/{image}.jpg")
PICS = {"id": "cap-show-pictures-of-earth", "name": "Show pictures of Earth",
        "does": "Display images of Earth taken from space."}
SATS = {"id": "cap-show-satellite-positions", "name": "Show satellite positions",
        "does": "Display the current positions of satellites visible from the user's location."}
SKY = {"id": "cap-display-night-sky-visibility", "name": "Display night sky visibility",
       "does": "Show what is visible in the night sky tonight from the user's location."}
EPIC_ROW = {"id": "nasa_epic", "name": "NASA EPIC Earth photos", "host": "epic.gsfc.nasa.gov",
            "what_it_is": "Daily full-disc photos of Earth taken from a million miles away.",
            "sample_endpoint": "https://epic.gsfc.nasa.gov/api/natural",
            "image_template": EPIC_TMPL, "topics": ["space", "earth"],
            "match_terms": ["earth", "space", "nasa", "planet", "photo", "satellite",
                            "picture"], "needs": []}
ISS_ROW = {"id": "iss_position", "name": "Where the ISS at", "host": "api.wheretheiss.at",
           "what_it_is": "Where the International Space Station is right now.",
           "sample_endpoint": "https://api.wheretheiss.at/v1/satellites/25544",
           "topics": ["space"], "match_terms": ["iss", "space", "station", "satellite",
                                                 "orbit"], "needs": []}
NO_FIT = (lambda a, b: None)  # noqa: E731 - meaning not measured


def sample(name: str) -> str:
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


def meaning_here() -> bool:
    try:
        from puffpai.catalogs import meaning
        meaning.embed(["a sky forecast"])
        return True
    except Exception:  # noqa: BLE001
        return False


needs_meaning = pytest.mark.skipif(not meaning_here(),
                                   reason="the reading helper is not on this computer")


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delitem(sys.modules, api.SERVER_MODULE, raising=False)
    yield tmp_path
    brain_tools.unregister_provider(api.PROVIDER)


def part_of(fixture: str, sid: str = "s1", **over) -> dict:
    found = shape.shape(sample(fixture))
    p = {"source_id": sid, "kind": "http_json", "url": "https://x.example/api",
         "door": "app:x:feed", "service": "x",
         "found": {"arrays": found["arrays"], "fields": found["fields"]}}
    p.update(over)
    return p


def n_of(part: dict, path: str) -> int:
    return next(f["n"] for f in part["found"]["fields"] if f["path"] == path)


# ── 1. a live card keeps its picture address template ───────────────────────

def test_a_live_card_takes_the_picture_template_from_its_hosts_website_row(monkeypatch):
    from puffpai import feeds
    monkeypatch.setattr(feeds, "_find", lambda ref: (
        {"id": ref, "blurb": "Photos of the whole Earth."},
        {"url": "https://epic.gsfc.nasa.gov/api/natural",
         "service": {"host": "epic.gsfc.nasa.gov"}, "card": {"kind": "list"}}))
    card = {"id": "feed-epic", "kind": "feed", "name": "NASA EPIC Earth photos",
            "ref": "nasa_epic_earth_photos"}
    row = plan.decide(PICS, [card], [EPIC_ROW])
    assert len(row["parts"]) == 1                  # one part per host, not two
    part = row["parts"][0]
    assert part["kind"] == "feed" and part["door"] == "app:nasa_epic_earth_photos:feed"
    assert part["image_template"] == EPIC_TMPL      # from the website row
    assert "million miles" in part["about"]


def test_a_live_cards_own_gallery_template_is_kept(monkeypatch):
    from puffpai import feeds
    monkeypatch.setattr(feeds, "_find", lambda ref: (
        {"id": ref}, {"url": "https://epic.gsfc.nasa.gov/api/natural",
                      "service": {"host": "epic.gsfc.nasa.gov"},
                      "card": {"kind": "gallery", "item_image_template": EPIC_TMPL}}))
    part = plan._feed_part({"id": "feed-epic", "kind": "feed", "name": "EPIC", "ref": "e"})
    assert part["image_template"] == EPIC_TMPL


def test_with_the_template_the_planned_app_is_a_gallery_with_pictures(state):
    part = part_of("nasa_epic.json", image_template=EPIC_TMPL,
                   url="https://epic.gsfc.nasa.gov/api/natural")
    s = spec.fallback("show_pictures_of_earth", [PICS], [part])
    assert s["views"][0]["type"] == "gallery"
    assert s["sources"][0]["fields"]["image"] == "tmpl:" + EPIC_TMPL
    rows = shape.rows_from(json.loads(sample("nasa_epic.json")), "$[*]",
                           s["sources"][0]["fields"])
    assert rows[0]["image"].startswith("https://epic.gsfc.nasa.gov/archive/natural/")


# ── 2. by meaning, and the check fits its capability ────────────────────────

@needs_meaning
def test_website_rows_go_to_the_capability_they_mean():
    got = plan.seeds_for_caps([PICS, SATS], [EPIC_ROW, ISS_ROW])
    assert [r["id"] for r in got[PICS["id"]]] == ["nasa_epic"]
    assert [r["id"] for r in got[SATS["id"]]] == ["iss_position"]


def test_without_the_reading_helper_rows_match_by_words(monkeypatch):
    monkeypatch.setattr(plan, "_meaning", lambda caps, rows: None)
    got = plan.seeds_for_caps([SATS], [EPIC_ROW, ISS_ROW])
    assert {r["id"] for r in got[SATS["id"]]} == {"nasa_epic", "iss_position"}


def test_a_check_must_show_what_its_capability_asks_for():
    assert accept.fit_reason(SATS, "Where are the satellites now?", ["gallery"], NO_FIT) == \
        "this job needs positions on a map, and its data has none"
    assert accept.fit_reason(PICS, "Show me Earth", ["table"], NO_FIT) == \
        "this job needs pictures, and its data has none"
    assert accept.fit_reason(PICS, "Show me Earth", ["gallery"], NO_FIT) == ""
    low = lambda a, b: 0.2  # noqa: E731
    assert accept.fit_reason(SKY, "Show me pictures of Earth", ["forecast"], low) == \
        "its test question is not about this job"


@needs_meaning
def test_the_ask_is_measured_against_the_capability():
    assert accept.fit_reason(SKY, "What can I see in the sky tonight?", ["table"]) == ""
    assert accept.fit_reason(SKY, "Show me an Earth image", ["table"]) == \
        "its test question is not about this job"


def test_photos_put_under_satellite_positions_are_not_proven(state):
    """The live run's case: the EPIC gallery landed under "Show satellite
    positions" and was called working with Earth photos."""
    part = part_of("nasa_epic.json", image_template=EPIC_TMPL,
                   url="https://epic.gsfc.nasa.gov/api/natural")
    s = spec.fallback("show_satellite_positions", [PICS], [part])
    s["caps"], s["cap_words"] = [SATS["id"]], {SATS["id"]: accept.cap_text(SATS)}
    s["checks"][0]["cap"] = SATS["id"]
    s["views"] = [{"type": "gallery", "source": "s1", "lightbox": True}]
    called = []
    got = accept.prove("show_satellite_positions", s, fit=NO_FIT,
                       call=lambda *a: called.append(a) or {"ok": True, "rows": [
                           {"title": "Earth", "image": "https://epic.gsfc.nasa.gov/a.jpg"}]},
                       reach=OK)[SATS["id"]]
    assert got["ok"] is False and called == []
    assert got["reason"] == "this job needs positions on a map, and its data has none"


def test_the_fallback_ask_is_made_from_the_capabilitys_words():
    assert spec.ask_for(SATS) == "Show satellite positions."
    assert spec.ask_for({"name": "Moon phases"}) == "Show me moon phases."


def test_the_brain_is_asked_again_when_its_view_does_not_fit_the_capability():
    part = part_of("nasa_epic.json", image_template=EPIC_TMPL)
    table = {"title": "Earth", "views": [{"view": spec.VIEWS.index("table") + 1,
                                          "source": "s1", "list": 1}],
             "fields": {"s1": {"title": n_of(part, "caption"), "image": n_of(part, "image")}},
             "checks": [{"cap": 1, "source": "s1", "ask": "Show me Earth"}]}
    gallery = dict(table, views=[{"view": 1, "source": "s1", "list": 1}])
    answers = [json.dumps(table), json.dumps(gallery)]
    seen = []

    def ask(messages):
        seen.append(messages)
        return answers.pop(0)
    made = spec.build("show_pictures_of_earth", [PICS], [part], ask=ask, meaning_fit=NO_FIT)
    assert made["how"] == "brain" and made["rounds"] == 2
    assert "capability 1 needs pictures" in seen[1][-1]["content"]


# ── 3. the proof depends on the view ────────────────────────────────────────

OK = (lambda d, u: (True, ""))  # noqa: E731


def test_a_gallery_needs_pictures_and_one_that_does_not_load_never_fails_it():
    # Since 4515fc54 (owner, 2026-10-12): a picture that does not load shows a
    # "No picture" tile; it never fails a gallery whose rows load.
    rows = [{"title": "a", "image": "https://p.example/1.jpg"},
            {"title": "b", "image": "https://p.example/2.jpg"}]
    assert accept.view_problem("gallery", rows, door="d", reach=OK) == ""
    assert accept.view_problem("gallery", [{"title": "a"}], reach=OK) == \
        "it has no pictures to show"
    assert accept.view_problem("gallery", [rows[0], {"title": "b"}], reach=OK) == \
        "it has no pictures to show"
    tried = []

    def second_fails(door, url):
        tried.append(url)
        return (False, "p.example answered 404") if url.endswith("2.jpg") else (True, "")
    assert accept.view_problem("gallery", rows, door="d", reach=second_fails) == ""
    assert tried == []


def test_a_name_alone_proves_nothing():
    assert accept.view_problem("list", [{"title": "Earth"}]) == \
        "it shows only a name, nothing more"
    assert accept.view_problem("table", [{"title": "Earth", "when": "2026-10-08"}]) == ""


def test_a_forecast_needs_times_and_a_named_weather_reading():
    assert accept.view_problem("forecast", [{"when": 3, "cloudcover": 2}]) == ""
    assert accept.view_problem("forecast", [{"cloudcover": 2}]) == "its forecast has no times"
    assert accept.view_problem("forecast", [{"when": 3, "title": "x", "id": 4}]) == \
        "its forecast has no weather readings"
    # a reading named only by its label counts
    assert accept.view_problem("forecast", [{"when": 3, "kp": 4}],
                               labels={"kp": "Sky activity"}) == ""


def test_a_map_needs_positions_and_a_player_a_stream():
    assert accept.view_problem("map", [{"lat": 10.5, "lon": -20}]) == ""
    assert accept.view_problem("map", [{"lat": "x", "lon": 3}]) == \
        "it has no positions to put on a map"
    assert accept.view_problem("player", [{"stream": "https://youtu.be/abc"}]) == ""
    hls = "/media?url=https%3A%2F%2Ftv.example%2Flive.m3u8"
    seen = []
    assert accept.view_problem("player", [{"stream": hls}], door="d",
                               reach=lambda d, u: seen.append(u) or (True, "")) == ""
    assert seen == ["https://tv.example/live.m3u8"]
    assert accept.view_problem("player", [{"title": "x"}]) == "it has nothing to play"


def test_a_title_only_check_is_not_proven_any_more(state):
    s = {"schema": "assembled_app/v1", "id": "pics", "title": "Pics", "caps": [PICS["id"]],
         "cap_words": {PICS["id"]: accept.cap_text(PICS)}, "settings": [], "sources": [{"id": "s1", "kind": "http_json", "door": "d",
                                      "url": "https://x.example/", "items": "$[*]",
                                      "fields": {"title": "caption", "text": "identifier"}}],
         "views": [{"type": "list", "source": "s1"}],
         "actions": [{"id": "get_s1", "source": "s1", "input": {}}],
         "checks": [{"cap": PICS["id"], "ask": "Show me an Earth image", "action": "get_s1",
                     "input": {}, "expect": {"min_items": 1, "fields": ["title"]}}]}
    got = accept.prove("pics", s, fit=NO_FIT, call=lambda *a: {"ok": True, "rows": [
        {"title": "Earth"}]}, reach=OK)[PICS["id"]]
    assert got["ok"] is False
    assert got["reason"] == "this job needs pictures, and its data has none"


# ── 4. labels from the source, meanings from its docs ───────────────────────

def test_field_names_become_plain_labels():
    assert shape.humanize("cloudcover") == "Cloud cover"
    assert shape.humanize("wind10m.direction") == "Wind direction"
    assert shape.humanize("prec_type") == "Precipitation type"
    assert shape.humanize("temp2m") == "Temperature"
    assert shape.humanize("lifted_index") == "Lifted index"


def seventimer_part() -> dict:
    return part_of("seventimer.json", sid="s2", name="7Timer! stargazing forecast",
                   about="Stargazing forecast for your town. Its card shows Cloud cover "
                         "(1 clear, 9 cloudy).",
                   url="https://www.7timer.info/bin/astro.php?lon={lon_round}"
                       "&lat={lat_round}&output=json")


def test_the_brain_writes_labels_and_meanings_from_the_sources_words():
    part = seventimer_part()
    picks = {"title": "Night sky", "views": [{"view": spec.VIEWS.index("forecast") + 1,
                                              "source": "s2", "list": 1}],
             "fields": {"s2": {"when": n_of(part, "timepoint"),
                               "more": [n_of(part, "cloudcover"), n_of(part, "seeing"),
                                        n_of(part, "wind10m.direction"), 999]}},
             "labels": [{"source": "s2", "field": n_of(part, "timepoint"),
                         "label": "Hours ahead"},
                        {"source": "s2", "field": n_of(part, "cloudcover"),
                         "label": "Cloud cover", "means": "1 clear to 9 overcast"},
                        {"source": "s2", "field": n_of(part, "seeing"),
                         "label": "see https://evil.example", "means": "<b>x</b>"}],
             "checks": [{"cap": 1, "source": "s2", "ask": "What can I see in the sky tonight?"}]}
    asked = []

    def ask(messages):
        asked.append(messages)
        return json.dumps(picks)
    made = spec.build("night_sky", [SKY], [part], ask=ask, meaning_fit=NO_FIT)
    assert made["how"] == "brain", made["errors"]
    src = made["spec"]["sources"][0]
    assert src["fields"] == {"when": "timepoint", "cloudcover": "cloudcover",
                             "seeing": "seeing", "wind10m_direction": "wind10m.direction"}
    assert src["labels"] == {"when": "Hours ahead", "cloudcover": "Cloud cover",
                             "seeing": "Seeing", "wind10m_direction": "Wind direction"}
    assert src["meanings"] == {"cloudcover": "1 clear to 9 overcast"}
    view = made["spec"]["views"][0]
    assert view["type"] == "forecast" and view["labels"] == src["labels"]
    assert view["meanings"] == src["meanings"]
    assert made["spec"]["checks"][0]["expect"]["fields"] == ["when"]
    text = asked[0][0]["content"]
    assert "7Timer! stargazing forecast) says: Stargazing forecast" in text
    assert "(1 clear, 9 cloudy)" in text
    assert spec.validate(made["spec"]) == []


def test_numbers_no_longer_fill_the_time_or_map_slots():
    part = seventimer_part()
    picks = {"views": [{"view": 2, "source": "s2", "list": 1}],
             "fields": {"s2": {"title": n_of(part, "timepoint"), "when": n_of(part, "cloudcover"),
                               "lat": n_of(part, "seeing")}},
             "checks": [{"cap": 1, "source": "s2", "ask": "x"}]}
    _spec, errors = spec.from_picks("night_sky", [SKY], [part], picks)
    assert any("is number, not when" in e for e in errors)
    assert any("is number, not lat" in e for e in errors)


def test_the_fallback_forecast_shows_the_sources_own_columns_with_labels():
    s = spec.fallback("night_sky", [SKY], [seventimer_part()])
    assert spec.validate(s) == []
    assert s["views"][0]["type"] == "forecast"
    labels = s["sources"][0]["labels"]
    assert labels["cloudcover"] == "Cloud cover" and labels["wind10m_direction"] == \
        "Wind direction"
    assert "lat" not in s["sources"][0]["fields"]


def test_the_validator_wants_short_plain_labels():
    s = spec.fallback("night_sky", [SKY], [seventimer_part()])
    s["sources"][0]["labels"]["cloudcover"] = "x" * 80
    assert any("labels.cloudcover must be short plain words" in e for e in spec.validate(s))
    s = spec.fallback("night_sky", [SKY], [seventimer_part()])
    s["views"][0]["meanings"] = {"cloudcover": "see http://x.example"}
    assert any("meanings.cloudcover" in e for e in spec.validate(s))


# ── 5. the brain uses the assembled tools ───────────────────────────────────

def sky_spec() -> dict:
    s = spec.fallback("night_sky", [SKY], [seventimer_part()])
    s["sources"][0]["meanings"] = {"cloudcover": "1 clear to 9 overcast"}
    return s


def fake_server(monkeypatch, rows):
    mod = types.ModuleType(api.SERVER_MODULE)
    mod.start_assembled = lambda app_id: {"ok": True, "port": 45678}
    mod.call_action = lambda app_id, action, inp: {"ok": True, "rows": rows}
    monkeypatch.setitem(sys.modules, api.SERVER_MODULE, mod)


def prove_sky(state_path) -> None:
    api.save_spec(sky_spec())
    report.write([report.entry({"cap": SKY["id"], "name": SKY["name"]}, report.WORKING,
                               app_id="night_sky")])


def test_the_tool_says_its_job_and_what_its_values_mean(state, monkeypatch):
    prove_sky(state)
    rows = [{"when": 3, "cloudcover": 1, "seeing": 6}]
    fake_server(monkeypatch, rows)
    tool = next(t for t in api.app_tools("night_sky", sky_spec()))
    text = tool.spec()["function"]["description"]
    assert "Does the job: Display night sky visibility" in text
    assert "Cloud cover (1 clear to 9 overcast)" in text
    got = tool.fn({}, {})
    assert got["items"] == rows and got["fields"]["cloudcover"] == \
        "Cloud cover (1 clear to 9 overcast)"


def test_your_apps_note_and_the_nudge(state, monkeypatch):
    from puffpai import brain_loop
    prove_sky(state)
    api.register_tools()
    tools = [t for t in brain_tools.available({}) if t.source == api.PROVIDER]
    assert [t.name for t in tools] == ["night_sky__get_s2"]
    notes = "\n".join(brain_loop.system_notes(tools))
    assert "## Your apps" in notes
    assert "- Display night sky visibility: call night_sky__get_s2" in notes
    assert "Cloud cover (1 clear to 9 overcast)" in notes
    near = lambda a, b: 0.7 if "sky" in a else 0.1  # noqa: E731
    assert apps_note.nudge("what can I see in the sky tonight?", tools, fit=near) == \
        apps_note.NUDGE.format(job=SKY["name"], tool="night_sky__get_s2")
    assert apps_note.nudge("tell me a joke", tools, fit=near) == ""
    assert apps_note.block([]) == ""            # no tool on offer, no note


def test_the_capability_map_names_the_proven_app(state, monkeypatch):
    from puffpai.finding import capabilities
    prove_sky(state)
    monkeypatch.setattr(capabilities, "load", lambda: [SKY])
    monkeypatch.setattr(capabilities, "_covering", lambda have=None, caps=None: {})
    monkeypatch.setattr(capabilities, "_things_by_id", lambda: {})
    block = capabilities.prompt_block()
    assert "- Display night sky visibility: its own app (call night_sky__get_s2)" in block


@needs_meaning
def test_the_nudge_matches_her_question_by_meaning(state, monkeypatch):
    prove_sky(state)
    api.register_tools()
    tools = [t for t in brain_tools.available({}) if t.source == api.PROVIDER]
    assert "night_sky__get_s2" in apps_note.nudge("what can I see in the sky tonight?", tools)
    assert apps_note.nudge("write me a poem about my cat", tools) == ""


# ── 6. the thing page ───────────────────────────────────────────────────────

def test_an_assembled_app_is_a_thing_with_its_own_page(state, monkeypatch):
    from puffpai import things
    prove_sky(state)
    tid = "assembled-night_sky"
    assert api.report_view()["apps"][0]["thing"] == tid
    assert any(t["id"] == tid and t["kind"] == "app" for t in things.listing())
    page = things.page(tid)
    assert page["assembled"] == "night_sky"
    assert "state" in page["host"] and page["host"]["state"] != "running"
    assert page["recipe"]["try"] == ["Display night sky visibility."]
    assert "BuildBot put this together for: Display night sky visibility" in \
        things.get(tid)["blurb"]
    started = []
    monkeypatch.setattr(api, "start_assembled",
                        lambda app_id: started.append(app_id) or {"ok": True, "port": 1})
    got = things.host_action(tid, "start")
    assert started == ["night_sky"] and got["ok"] is True
    assert things.remove(tid)["code"] == "assembled"
    assert things.page("assembled-nope") is None


# ── 7. the console path keeps capabilities ──────────────────────────────────

def test_the_console_path_keeps_the_jobs_she_ticked_as_capabilities(state, monkeypatch):
    from puffpai.finding import capabilities
    from puffpai.wizard import loader
    monkeypatch.setattr(plan, "_seed_rows", lambda: [])
    monkeypatch.setattr(plan, "_live_doors", lambda: {})
    monkeypatch.setattr(capabilities, "_covering", lambda have=None, caps=None: {})
    packages, _ = loader.load_builtin_packages()
    module = loader.load_module(packages["assemble"])
    needs = {"schema": "needs/v1", "kind": "space", "jobs": [
        {"id": "j1", "name": "Show pictures of Earth", "does": "Photos of Earth.",
         "ticked": True},
        {"id": "j2", "name": "Fold laundry", "does": "", "ticked": False}]}
    ctx = types.SimpleNamespace(shared={"needs": needs, "interview": {"goal": "a space bot"}},
                                sections={})
    monkeypatch.setattr("puffpai.wizard.proposal.needs_of",
                        lambda shared: shared.get("needs"))
    from puffpai.build import schema as build_schema
    monkeypatch.setattr("puffpai.build.interpret.default_brain", lambda cfg=None: None)
    module.part_plan(ctx)
    assert [c["name"] for c in capabilities.load()] == ["Show pictures of Earth"]
    # The one build record plans exactly the job she ticked.
    assert [j["name"] for j in build_schema.load_build()["jobs"]] == ["Show pictures of Earth"]
    # The web wizard's own kept capabilities are never replaced.
    needs["jobs"][1]["ticked"] = True
    module.part_plan(ctx)
    assert [c["name"] for c in capabilities.load()] == ["Show pictures of Earth"]
    assert [j["name"] for j in build_schema.load_build()["jobs"]] == ["Show pictures of Earth"]


# ── 5b. both chat paths, with a scripted model ──────────────────────────────

class ScriptedBrain:
    """Scripted replies: a list is tool calls, a string is words."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages],
                           "tools": None if tools is None else list(tools)})
        reply = self.replies.pop(0) if self.replies else "All done."
        if isinstance(reply, list):
            return {"content": "", "tool_calls": [
                {"function": {"name": n, "arguments": a}} for n, a in reply]}
        return {"content": reply, "tool_calls": []}


@pytest.fixture
def chat_world(monkeypatch, tmp_path):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
    import integrate_support as sup
    from puffpai import api_server, brain_tools_builtin
    monkeypatch.setenv("PUFFPAI_TOOL_LOOP", "on")
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()
    sup.setup_chat(monkeypatch, tmp_path, brain=sup.Brain())
    monkeypatch.setattr(api_server, "_LOOP_UNSUPPORTED", {})
    monkeypatch.setattr(api_server, "_grounding_context", lambda *a, **k: ("", []))
    monkeypatch.delitem(sys.modules, api.SERVER_MODULE, raising=False)
    prove_sky(tmp_path)
    fake_server(monkeypatch, [{"when": 3, "cloudcover": 1, "seeing": 6}])
    # Her question is about the sky job (the meaning measure is scripted here).
    monkeypatch.setattr(accept, "meaning_fit",
                        lambda a, b: 0.7 if "sky" in str(a) and "sky" in str(b) else 0.1)
    brain = ScriptedBrain()
    monkeypatch.setattr(api_server, "_loop_backend", lambda limits=None: brain)
    api_server.app.config.update(TESTING=True)
    yield {"brain": brain, "api_server": api_server, "auth": {
        "Authorization": f"Bearer {api_server._API_KEY}", "X-Puff-Device-Id": "deviceaaaa000001"}}
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()


SKY_TOOL = "night_sky__get_s2"
ASK = "what can I see in the sky tonight?"


def _offered_and_told(call: dict) -> tuple[set, str]:
    names = {t["function"]["name"] for t in call["tools"] or []}
    system = "\n".join(m["content"] for m in call["messages"] if m["role"] == "system")
    return names, system


def test_chat_send_offers_the_proven_tool_says_so_and_it_runs(chat_world):
    w = chat_world
    w["brain"].replies = [[(SKY_TOOL, {})], "Clear skies tonight, cloud cover 1 of 9."]
    client = w["api_server"].app.test_client()
    got = client.post("/chat/send", headers=w["auth"], json={"message": ASK}).get_json()
    assert got["response"] == "Clear skies tonight, cloud cover 1 of 9."
    names, system = _offered_and_told(w["brain"].calls[0])
    assert SKY_TOOL in names
    assert "## Your apps" in system and "Cloud cover (1 clear to 9 overcast)" in system
    assert f"Call {SKY_TOOL} first" in system
    fed = w["brain"].calls[1]["messages"][-1]
    assert fed["role"] == "tool" and '"cloudcover": 1' in fed["content"]


def test_the_streamed_chat_offers_and_runs_it_too(chat_world):
    w = chat_world
    w["brain"].replies = [[(SKY_TOOL, {})], "Clear skies tonight."]
    client = w["api_server"].app.test_client()
    body = client.post("/v1/chat/completions", headers=w["auth"], json={
        "model": "puffpai", "stream": True,
        "messages": [{"role": "user", "content": ASK}]}).get_data(as_text=True)
    chunks = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]
    assert chunks[-1]["puffpai_meta"]["tools_used"] == [SKY_TOOL]
    names, system = _offered_and_told(w["brain"].calls[0])
    assert SKY_TOOL in names and f"Call {SKY_TOOL} first" in system


def test_no_nudge_for_a_question_about_something_else(chat_world):
    w = chat_world
    w["brain"].replies = ["Here is a joke."]
    client = w["api_server"].app.test_client()
    client.post("/chat/send", headers=w["auth"], json={"message": "tell me a joke"})
    names, system = _offered_and_told(w["brain"].calls[0])
    assert SKY_TOOL in names and "## Your apps" in system
    assert "first and answer from what it gives back. Read" not in system


# ── put one app together again ──────────────────────────────────────────────

def test_put_together_again_runs_only_that_app(state, monkeypatch):
    from flask import Flask
    from puffpai import fsutil
    fsutil.atomic_write_json(plan.plan_path(), {
        "schema": "assemble_plan/v1", "made_at": 1, "capabilities": [
            {"cap": SKY["id"], "name": SKY["name"], "app_id": "night_sky", "decision": "assemble"},
            {"cap": PICS["id"], "name": PICS["name"], "app_id": "pics", "decision": "assemble"}]})
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    monkeypatch.setattr(api, "_device", lambda: "device:abc")
    started = []
    monkeypatch.setattr(api, "start_background", lambda **kw: started.append(kw) or True)
    c = app.test_client()
    for body in ({"thing": "assembled-night_sky"}, {"app_id": "night_sky"},
                 {"capability": SKY["id"]}, {}):
        assert c.post("/assemble/run", json=body).status_code == 202
    assert started == [{"cap": SKY["id"]}] * 3 + [{"cap": ""}]
    assert c.post("/assemble/run", json={"thing": "assembled-nope"}).status_code == 404
    assert len(started) == 4


# ── re-verify 20add8b: five more ────────────────────────────────────────────

SPACE = {"id": "cap-provide-space-weather-updates", "name": "Provide space weather updates",
         "does": "Update the user on space weather conditions."}
SEVEN_ROW = {"id": "seventimer", "name": "7Timer! stargazing forecast",
             "host": "www.7timer.info",
             "what_it_is": "Stargazing forecast for your town: cloud cover, steadiness of the "
                           "air, and how clear the sky is.",
             "sample_endpoint": "https://www.7timer.info/bin/astro.php?lon=-83.0&lat=40.0",
             "topics": ["weather", "space", "sky"], "match_terms": ["sky", "star", "weather"],
             "needs": [], "field_meanings": {
                 "seeing": "1 best (under 0.5 arcsec) to 8 worst (over 2.5 arcsec)",
                 "transparency": "1 best (under 0.3) to 8 worst (over 1)"}}


def scripted_fit(table):
    """Meaning, scripted: (words in a, words in b) -> cosine."""
    def fit(a, b):
        for (wa, wb), v in table.items():
            if wa in a and wb in b:
                return v
        return 0.1
    return fit


SEVEN_FIT = scripted_fit({("Stargazing", "night sky"): 0.46,
                          ("Stargazing", "space weather"): 0.43,
                          ("Provide space weather", "space weather"): 0.8})


def test_a_part_that_fits_another_job_better_does_not_do_this_one():
    words = accept.source_words(plan._seed_part(SEVEN_ROW, ""))
    assert accept.source_reason(SPACE, words, [SKY, SPACE], SEVEN_FIT) == accept.NOTHING_DOES
    assert accept.source_reason(SKY, words, [SKY, SPACE], SEVEN_FIT) == ""
    assert accept.source_reason(SKY, words, [SKY], lambda a, b: 0.2) == accept.NOTHING_DOES


def test_the_plan_leaves_out_a_stand_in_and_says_found_nothing():
    row = plan.decide(SPACE, [], [SEVEN_ROW])
    got = plan.only_fitting(row, SPACE, [SKY, SPACE], SEVEN_FIT)
    assert got["decision"] == plan.NOT_FOUND and got["parts"] == []
    assert got["why"] == "Found nothing that does this yet."
    assert got["left_out"] == ["7Timer! stargazing forecast"]


def shipped_row(rid: str) -> dict:
    from puffpai.catalogs import api_seeds
    return next(r for r in api_seeds.load_rows() if r["id"] == rid)


@needs_meaning
def test_the_stargazing_forecast_is_not_space_weather_by_meaning():
    words = accept.source_words(plan._seed_part(shipped_row("seventimer"), ""))
    assert accept.source_reason(SPACE, words, [SKY, SPACE]) == accept.NOTHING_DOES
    assert accept.source_reason(SKY, words, [SKY, SPACE]) == ""


@needs_meaning
def test_earth_weather_is_left_out_when_a_space_weather_source_fits_better():
    caps = [SKY, SPACE, PICS]
    row = plan.decide(SPACE, [], [shipped_row("swpc_kp"), shipped_row("open_meteo")])
    got = plan.only_fitting(row, SPACE, caps)
    assert [p["service"] for p in got["parts"]] == ["swpc_kp"]


def test_the_proof_checks_the_source_fits_too(state):
    part = part_of("seventimer.json", sid="s1", name=SEVEN_ROW["name"],
                   about=SEVEN_ROW["what_it_is"])
    s = spec.fallback("space_weather", [SPACE], [part])
    assert "Stargazing forecast" in s["sources"][0]["about"]
    called = []
    got = accept.prove("space_weather", s, fit=SEVEN_FIT, others=[SKY, SPACE],
                       call=lambda *a: called.append(a) or {"ok": True, "rows": [{}]},
                       reach=OK)[SPACE["id"]]
    assert got["ok"] is False and got["reason"] == accept.NOTHING_DOES and called == []


def test_a_check_on_a_site_that_cannot_search_asks_for_its_newest_items():
    part = part_of("nasa_epic.json", image_template=EPIC_TMPL,
                   url="https://epic.gsfc.nasa.gov/api/natural")
    picks = {"views": [{"view": 1, "source": "s1", "list": 1}], "search": ["s1"],
             "fields": {"s1": {"title": n_of(part, "caption"), "image": n_of(part, "image")}},
             "checks": [{"cap": 1, "source": "s1", "ask": "Show me Earth", "q": "earth"}]}
    s, errors = spec.from_picks("pics", [PICS], [part], picks)
    assert errors == [] and s["actions"][0]["id"] == "search_s1"
    assert s["checks"][0]["input"] == {"q": ""}


def test_documented_scales_reach_the_brain_notes(state, monkeypatch):
    seed = plan._seed_part(SEVEN_ROW, "app:x:feed")
    assert seed["meanings"]["seeing"].startswith("1 best")
    part = seventimer_part()
    part["meanings"] = seed["meanings"]
    s = spec.fallback("night_sky", [SKY], [part])
    assert s["sources"][0]["meanings"]["seeing"] == SEVEN_ROW["field_meanings"]["seeing"]
    assert "Its documents say what values mean: seeing: 1 best" in spec.prompt([SKY], [part])
    api.save_spec(s)
    report.write([report.entry({"cap": SKY["id"], "name": SKY["name"]}, report.WORKING,
                               app_id="night_sky")])
    fake_server(monkeypatch, [{"when": 3, "seeing": 5}])
    api.register_tools()
    tools = [t for t in brain_tools.available({}) if t.source == api.PROVIDER]
    notes = apps_note.block(tools)
    assert "Seeing (1 best (under 0.5 arcsec) to 8 worst (over 2.5 arcsec))" in notes
    assert "Transparency (1 best" in notes
    assert "Seeing (1 best" in tools[0].spec()["function"]["description"]


def test_the_shipped_seed_rows_document_their_scales():
    from puffpai.catalogs import api_seeds
    rows = {r["id"]: r for r in api_seeds.load_rows()}
    assert api_seeds.row_problem(rows["seventimer"]) is None
    assert {"cloudcover", "seeing", "transparency", "lifted_index"} <= \
        set(rows["seventimer"]["field_meanings"])
    bad = dict(rows["seventimer"], field_meanings={"seeing": "x" * 300})
    assert api_seeds.row_problem(bad) == "field_meanings must map field names to short words"


def test_a_source_not_switched_on_yet_offers_one_tap(state, monkeypatch):
    from puffpai import add_more, drawbridge, feed_generator, fix_api, fsutil
    from puffpai.assemble import consent, seed_switch
    from puffpai.catalogs import api_seeds
    monkeypatch.setattr(consent, "has_yes", lambda app_id: True)
    monkeypatch.setattr(api_seeds, "load_rows", lambda path=None: [ISS_ROW])
    monkeypatch.setattr(api_seeds, "row_problem", lambda row: None)
    monkeypatch.setattr(api_seeds, "to_candidate", lambda r: {"id": r["id"]})
    monkeypatch.setattr(seed_switch, "needed_switches", lambda: [])   # sample looks on
    row = plan.decide(SATS, [], [ISS_ROW])
    got = api.assemble_one(row)
    assert got["state"] == report.NOT_YET and "not switched on yet" in got["reason"]
    assert got["fix"] == {"kind": "switch_on", "door": "", "service": "seed-iss_position"}
    report.write([got])
    card = api.report_view()["apps"][0]["caps"][0]["card"]
    assert card["kind"] == "switch_on" and card["service"] == "seed-iss_position"
    assert card["label"] == "Switch on Where the ISS at now?"
    assert "api.wheretheiss.at" in card["detail"]
    # Her tap: the live card is made and switched on; that job is put together again.
    fsutil.atomic_write_json(plan.plan_path(), {"schema": "assemble_plan/v1", "made_at": 1,
                                                "capabilities": [row]})
    did = []
    monkeypatch.setattr(add_more, "online_allowed", lambda: True)
    monkeypatch.setattr(feed_generator, "draft_feed_app",
                        lambda cand, build_online: did.append(("draft", cand["id"], build_online))
                        or {"id": "where_the_iss_at"})
    monkeypatch.setattr(drawbridge, "check_addition", lambda d, by: {"verdict": "pass"})
    monkeypatch.setattr(feed_generator, "install_draft",
                        lambda i, allow: did.append(("install", i, allow)) or {})
    monkeypatch.setattr(api, "start_background", lambda **kw: did.append(("again", kw)) or True)
    out, status = fix_api.apply({"kind": "switch_on", "service": "seed-iss_position"},
                                "device:abc")
    assert status == 200
    assert out["line"] == "Where the ISS at is switched on. Putting it together again."
    assert did == [("draft", "iss_position", True), ("install", "where_the_iss_at", True),
                   ("again", {"cap": SATS["id"],
                              "pending": "switched on, putting it together again"})]
    assert seed_switch.apply("iss_position", "")[1] == 403
    assert fix_api.apply({"kind": "switch_on", "service": "seed-nope"}, "device:abc")[1] == 404


def test_frame_ancestors_follow_the_dashboard_port_without_a_restart(state, monkeypatch):
    import argparse
    from puffpai.assemble.server import app as app_mod, gateway, host
    gw = os.path.join(str(state), "gateway.json")
    with open(gw, "w", encoding="utf-8") as fh:
        json.dump({"port": 5000, "dashboard_port": 3140}, fh)
    server = app_mod.Server(argparse.Namespace(port=1, app_id="night_sky", folder=str(state),
                                               gateway_file=gw, scaffold="",
                                               dashboard_port=3003))
    assert server.frame_ancestors == "http://127.0.0.1:3140 http://localhost:3140"
    with open(gw, "w", encoding="utf-8") as fh:
        json.dump({"port": 5000, "dashboard_port": 3150}, fh)
    assert server.frame_ancestors == "http://127.0.0.1:3150 http://localhost:3150"
    os.remove(gw)
    assert server.frame_ancestors == "http://127.0.0.1:3003 http://localhost:3003"
    # BuildBot keeps the port in the gateway file current.
    monkeypatch.setattr(host, "_dashboard_port", lambda: 3160)
    try:
        gateway.ensure_running()
        with open(gateway.gateway_file(), encoding="utf-8") as fh:
            assert json.load(fh)["dashboard_port"] == 3160
    finally:
        gateway.stop()


# ── re-verify 71e5fc7: three more ───────────────────────────────────────────

class _Resp:
    def __init__(self, text: str):
        self.status_code = 200
        self._body = text.encode("utf-8")

    def iter_content(self, size):
        yield self._body

    def close(self):
        pass


def test_documented_scales_survive_plan_to_spec_to_tool(state, monkeypatch):
    """End to end: a plan part with the seed row's meanings, the brain's picks
    (which leave seeing out and say their own words for cloud cover), the
    saved spec, the tool description and the Your apps note."""
    from puffpai.assemble import consent
    monkeypatch.setattr(consent, "has_yes", lambda app_id: True)
    seed = plan._seed_part(shipped_row("seventimer"), "app:seventimer:feed")
    seed["sample_url"] = "https://www.7timer.info/bin/astro.php?lon=-83.0&lat=40.0"
    row = {"cap": SKY["id"], "name": SKY["name"], "does": SKY["does"], "decision": "assemble",
           "app_id": "night_sky", "parts": [seed]}
    found = shape.shape(sample("seventimer.json"))
    num = {f["path"]: f["n"] for f in found["fields"]}

    def ask(messages):
        return json.dumps({
            "title": "Night sky", "views": [{"view": 3, "source": "s1", "list": 1}],
            "fields": {"s1": {"when": num["timepoint"], "more": [num["cloudcover"]]}},
            "labels": [{"source": "s1", "field": num["cloudcover"], "label": "Cloud cover",
                        "means": "how cloudy it looks"}],
            "checks": [{"cap": 1, "source": "s1", "ask": "What can I see in the sky tonight?"}]})
    monkeypatch.setattr(accept, "meaning_fit", lambda a, b: None)
    fake_server(monkeypatch, [{"when": 3, "cloudcover": 1, "seeing": 5}])
    api.assemble_one(row, ask=ask, getter=lambda door, url, **kw: _Resp(sample("seventimer.json")),
                     reach=OK, backend=None)
    s = api.load_spec("night_sky")
    src = s["sources"][0]
    assert src["fields"]["seeing"] == "seeing"            # documented: shown though not picked
    assert src["meanings"]["seeing"].startswith("1 best")
    assert src["meanings"]["cloudcover"].startswith("1 clear")   # the seed row wins
    assert s["views"][0]["meanings"]["seeing"].startswith("1 best")
    tool = api.app_tools("night_sky", s)[0]
    text = tool.spec()["function"]["description"]
    assert "Seeing (1 best" in text and "Transparency (1 best" in text
    report.write([report.entry({"cap": SKY["id"], "name": SKY["name"]}, report.WORKING,
                               app_id="night_sky")])
    assert "Seeing (1 best" in apps_note.block([tool])
    assert tool.fn({}, {})["fields"]["seeing"].startswith("Seeing (1 best")


SAMPLE_LOOK_ROW = {"id": "sample_look", "name": "Looking at a service before adding it",
                   "state": "off", "can_switch": True}


@pytest.mark.parametrize("seed_id", ["launch_library", "iss_position", "swpc_kp"])
def test_one_tap_switches_on_every_switch_the_job_needs(state, monkeypatch, seed_id):
    from puffpai import add_more, drawbridge, feed_generator, fix_api, fsutil, outside_services
    from puffpai.assemble import seed_switch
    monkeypatch.setattr(outside_services, "row",
                        lambda sid: dict(SAMPLE_LOOK_ROW) if sid == "sample_look" else None)
    name = seed_switch._name(shipped_row(seed_id))  # noqa: SLF001
    card = fix_api.clean({"kind": "switch_on", "door": "", "service": f"seed-{seed_id}"})
    assert card["label"] == f"Switch on {name} and looking at services before adding them?"
    assert "Looking at a service before adding it" in card["detail"]
    fsutil.atomic_write_json(plan.plan_path(), {"schema": "assemble_plan/v1", "made_at": 1,
                                                "capabilities": [{"cap": "cap-x", "parts": [
                                                    {"kind": "seed", "service": seed_id}]}]})
    did = []
    monkeypatch.setattr(fix_api, "_switch_on",
                        lambda sid, device: did.append(("switch", sid, device))
                        or ({"ok": True, "service": {"name": "x"}}, 200))
    monkeypatch.setattr(fix_api, "_audit", lambda *a: did.append(("audit",) + a))
    monkeypatch.setattr(add_more, "online_allowed", lambda: True)
    monkeypatch.setattr(feed_generator, "draft_feed_app",
                        lambda cand, build_online: did.append(("draft", cand["id"]))
                        or {"id": "card_x"})
    monkeypatch.setattr(drawbridge, "check_addition", lambda d, by: {"verdict": "pass"})
    monkeypatch.setattr(feed_generator, "install_draft",
                        lambda i, allow: did.append(("install", i, allow)) or {})
    monkeypatch.setattr(api, "start_background", lambda **kw: did.append(("again", kw)) or True)
    out, status = fix_api.apply({"kind": "switch_on", "service": f"seed-{seed_id}"},
                                "device:abc")
    assert status == 200, out
    assert did == [("switch", "sample_look", "device:abc"),
                   ("audit", "chat_fix_switch_on", "on", "sample_look"),
                   ("draft", f"seed:{seed_id}"), ("install", "card_x", True),
                   ("again", {"cap": "cap-x",
                              "pending": "switched on, putting it together again"}),
                   ("audit", "chat_fix_switch_on", "on", f"seed-{seed_id}")]


def test_a_switch_a_card_cannot_flip_is_said_plainly(state, monkeypatch):
    from puffpai import feed_generator, fix_api, outside_services
    monkeypatch.setattr(outside_services, "row", lambda sid: dict(
        SAMPLE_LOOK_ROW, can_switch=False) if sid == "sample_look" else None)
    monkeypatch.setattr(feed_generator, "draft_feed_app",
                        lambda *a, **k: pytest.fail("nothing is made"))
    out, status = fix_api.apply({"kind": "switch_on", "service": "seed-iss_position"},
                                "device:abc")
    assert status == 409
    assert out["message"] == ("Looking at a service before adding it is held by whoever "
                              "runs this computer, so a card cannot switch it on. Nothing "
                              "was added for Where the ISS at.")


def test_search_is_forgiving_and_falls_back_to_the_newest(state, monkeypatch):
    s = spec.fallback("pics", [PICS], [part_of("nasa_epic.json", image_template=EPIC_TMPL,
                                               url="https://epic.gsfc.nasa.gov/api/natural")])
    s["actions"] = [{"id": "search_s1", "source": "s1", "input": {"q": "string"}}]
    s["checks"][0]["action"] = "search_s1"
    api.save_spec(s)
    get = (lambda door, url, **kw: _Resp(sample("nasa_epic.json")))
    assert api.search_words("Show me the latest Earth photo") == ["earth"]
    every = api.fetch_source("pics", "s1", {}, getter=get)["rows"]
    assert len(every) == 2
    assert api.fetch_source("pics", "s1", {"q": "latest Earth photo"}, getter=get)["rows"] \
        == every                                   # nothing matched: the newest
    assert len(api.fetch_source("pics", "s1", {"q": "DSCOVR pictures"}, getter=get)["rows"]) == 2
    fake_server(monkeypatch, every)
    tool = api.app_tools("pics", s)[0]
    got = tool.fn({"q": "latest Earth photo"}, {})
    assert got["note"] == "nothing matched 'latest Earth photo', here are the newest"
    assert len(got["items"]) == 2


def test_the_proof_passes_on_real_image_rows_with_the_brains_own_query(state, monkeypatch):
    s = spec.fallback("pics", [PICS], [part_of("nasa_epic.json", image_template=EPIC_TMPL,
                                               url="https://epic.gsfc.nasa.gov/api/natural")])
    s["actions"] = [{"id": "search_s1", "source": "s1", "input": {"q": "string"}}]
    s["checks"][0].update(action="search_s1", input={"q": ""})
    api.save_spec(s)
    get = (lambda door, url, **kw: _Resp(sample("nasa_epic.json")))
    mod = types.ModuleType(api.SERVER_MODULE)
    mod.call_action = lambda app_id, action, inp: api.fetch_source(app_id, "s1", inp, getter=get)
    monkeypatch.setitem(sys.modules, api.SERVER_MODULE, mod)

    class Brain:      # asks the way the live brain did
        n = 0

        def chat(self, messages, temperature=0.3, max_tokens=1024, tools=None):
            Brain.n += 1
            if Brain.n == 1:
                return {"role": "assistant", "content": "", "tool_calls": [{"function": {
                    "name": "pics__search_s1", "arguments": {"q": "latest Earth photo"}}}]}
            return {"role": "assistant", "content": "Here is Earth.", "tool_calls": []}
    got = accept.prove("pics", s, backend=Brain(), reach=OK, fit=NO_FIT, others=[PICS])
    assert got[PICS["id"]]["ok"] is True, got
    assert got[PICS["id"]]["checks"][0]["ask"] == spec.ask_for(PICS)


# ── a scoped rebuild that comes during another run is queued, not dropped ───

def test_two_scoped_runs_back_to_back_both_run_and_the_report_is_never_stale(state,
                                                                           monkeypatch):
    import threading
    from puffpai import fsutil
    fsutil.atomic_write_json(plan.plan_path(), {"schema": "assemble_plan/v1", "made_at": 1,
                                                "capabilities": [
        {"cap": SKY["id"], "name": SKY["name"], "app_id": "night_sky", "decision": "assemble"},
        {"cap": SATS["id"], "name": SATS["name"], "app_id": "sats", "decision": "assemble"}]})
    report.write([report.entry({"cap": c["id"], "name": c["name"]}, report.NOT_YET,
                               "Where the ISS at is not switched on yet", app_id=a,
                               decision="assemble")
                  for c, a in ((SKY, "night_sky"), (SATS, "sats"))])
    gate, ran, done = threading.Event(), [], threading.Event()

    def fake_run_all(*, cap="", **kw):
        api._running.set()
        try:
            ran.append(cap)
            if len(ran) == 1:
                gate.wait(5)
            report.update(report.entry({"cap": cap, "name": cap}, report.WORKING,
                                       app_id="x", decision="assemble"))
        finally:
            api._running.clear()
            if len(ran) == 2:
                done.set()
    monkeypatch.setattr(api, "run_all", fake_run_all)
    monkeypatch.setattr(api, "_QUEUED", [])
    assert api.start_background(cap=SKY["id"]) is True
    for _ in range(3):      # the second, asked again while waiting: queued once
        assert api.start_background(cap=SATS["id"],
                                    pending="switched on, " + api.PENDING) is False
    assert api.queued() == [SATS["id"]]
    assert api.status_view()["queued"] == [{"cap": SATS["id"], "name": SATS["name"]}]
    rows = report.by_cap()
    assert rows[SATS["id"]]["reason"] == "switched on, putting it together again"
    assert "fix" not in rows[SATS["id"]]
    gate.set()
    assert done.wait(5)
    assert ran == [SKY["id"], SATS["id"]] and api.queued() == []
    assert {r["state"] for r in report.by_cap().values()} == {report.WORKING}
