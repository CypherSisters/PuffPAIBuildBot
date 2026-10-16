"""build_abilities: the one list of what this build can really do.

The nephew run: a space bot whose goal said "works with my smart
telescope" offered to "set up the telescope now", and its own "what can you
do?" said a web-page door was open when the running config had it shut. The
list here is read from the running build, and what the goal asks for that
nothing covers is COMPUTED from the goal sentence - never from a topic table.
So every test that proves the telescope case also proves goals that have
nothing to do with space.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from puffpai import build_abilities as ba
from puffpai import build_context as bc
from puffpai import self_knowledge as sk
from puffpai import plain_words  # the one banned list

G1 = ("I want a space bot that knows everything about space, shows me cool "
      "space stuff, and works with my smart telescope")
G2 = "I love dinosaurs, I want a bot that knows every dinosaur"
G3 = "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos"
G4 = "A garden helper for my vegetable beds in Ohio"
G5 = "A knitting and crochet buddy with patterns and tips"
G6 = "A music bot that knows bands and albums"

#: Words that must never reach an owner (the plain-words rule).
#: This area's own internal ids, on top of plain_words' one list.
JARGON_EXTRA = ("web_fetch", "news_rss", "instruments.", "comms.")


@pytest.fixture
def runtime(monkeypatch):
    """A build with no manifest, no apps, every outside website shut, no
    library, no smart home - the runtime pieces replaced one by one."""
    state = {"manifest": {}, "apps": [], "doors": [
        {"id": "weather", "open": False, "hosts": ["api.open-meteo.com"]},
        {"id": "news_rss", "open": False, "hosts": ["(the address)"]},
        {"id": "web_fetch", "open": True, "hosts": []},
    ], "library": 0}

    from puffpai.security import egress_doors

    monkeypatch.setattr(ba, "_manifest", lambda: state["manifest"])
    monkeypatch.setattr(ba, "_config", lambda: None)
    monkeypatch.setattr(ba, "_manifests", lambda: state["apps"])
    monkeypatch.setattr(egress_doors, "door_states",
                        lambda: {"doors": state["doors"]})

    def _library():
        label = "Look things up in an offline library on this computer"
        if state["library"]:
            return ba.row("knowledge_library", label, ba.AVAILABLE)
        return ba.row("knowledge_library", label, ba.NOT_SET_UP, "run it")
    monkeypatch.setattr(ba, "_knowledge_row", _library)
    monkeypatch.setattr(ba, "_PROVIDERS", [])
    # A town is known unless a test says otherwise (the weather needs one).
    state["town"] = True
    monkeypatch.setattr(ba, "_town_set", lambda: state["town"])
    return state


# ── the row shape ────────────────────────────────────────────────────────────

def test_every_row_has_the_shared_shape(runtime):
    for r in ba.abilities(goal=G1):
        # "action" is optional; when present, how_to_add is its
        # terminal wording.
        assert set(r) - {"action"} == {"id", "label", "status", "how_to_add"}
        if "action" in r:
            assert r["how_to_add"] == plain_words.render_action(r["action"],
                                                                "terminal")
        assert r["status"] in ba.STATUSES
        assert r["label"] and r["label"][0].isupper()


def test_an_unknown_status_becomes_unknown_and_unknown_is_never_available():
    r = ba.row("x", "Do a thing", "definitely")
    assert r["status"] == ba.UNKNOWN
    assert not ba.is_available("x", [r])
    assert ba.absent([r]) == [r]


# ── read from the runtime, not the record ────────────────────────────────────

def test_an_outside_website_follows_the_running_config(runtime):
    assert ba.get("outside:weather")["status"] == ba.NOT_SET_UP
    runtime["doors"][0]["open"] = True
    assert ba.is_available("outside:weather")


def test_the_weather_switched_on_without_a_town_says_so(runtime):
    """Switched on is not working until it knows
    where home is; the way to fix it is the town, not Home Assistant."""
    runtime["doors"][0]["open"] = True
    runtime["town"] = False
    weather = ba.get("outside:weather")
    assert weather["status"] == ba.NOT_SET_UP
    assert "home town" in ba.status_words(weather)
    assert weather["action"]["id"] in ("home.set", "home.set_card")
    assert "Home Assistant" not in ba.absent_line(weather)


def test_a_switched_off_service_points_at_its_own_switch(runtime):
    weather = ba.get("outside:weather")
    assert weather["action"] == {"id": "outside.on",
                                 "args": {"name": "Weather forecast"}}
    assert weather["how_to_add"] == 'puffpai outside on "Weather forecast"'
    assert "What leaves your computer" in plain_words.render_action(
        weather["action"], "browser")


def test_only_home_gadgets_mention_home_assistant_and_as_an_if(runtime):
    for r in ba.abilities(goal=G1):
        words = r["label"] + " " + str(r.get("how_to_add") or "")
        if "Home Assistant" in words:
            assert r["id"] == "device_control", r
            assert "if you have" in words.lower()


def test_a_smart_telescope_is_not_a_smart_home(runtime):
    assert "smart" not in ba.terms(ba.get("device_control"))


def test_an_open_web_page_door_is_not_an_ability_chat_has(runtime):
    """web_fetch open in config does not mean chat can read a page: nothing on
    the chat path uses it."""
    assert ba.get("outside:web_fetch") is None
    assert ba.get("web_page")["status"] == ba.NOT_IN_THIS_BUILD


def test_unreadable_doors_are_unknown_not_shut_and_not_open(runtime,
                                                           monkeypatch):
    from puffpai.security import egress_doors

    def _boom():
        raise RuntimeError("config on fire")
    monkeypatch.setattr(egress_doors, "door_states", _boom)
    weather = ba.get("outside:weather")
    assert weather["status"] == ba.UNKNOWN


def test_a_service_another_lane_adds_gets_a_plain_label(runtime):
    runtime["doors"].append({"id": "nasa", "open": True,
                             "hosts": ["api.nasa.gov"]})
    assert ba.get("outside:nasa")["label"] == \
        "Use the outside website api.nasa.gov"


def test_installed_apps_are_available_and_trimmed_to_the_page_list(runtime):
    runtime["apps"] = [
        {"id": "calendar", "name": "Calendar", "surfaces": ["comms.calendar"],
         "blurb": "Family events."},
        {"id": "trivia", "name": "Trivia", "surfaces": ["daily.games"]},
    ]
    runtime["manifest"] = {"surfaces": {"allowlist": ["daily.games"],
                                        "derived_from_goal": True}}
    ids = {r["id"] for r in ba.available()}
    assert "app:trivia" in ids and "app:calendar" not in ids


def test_the_library_is_available_only_when_something_is_indexed(runtime):
    assert not ba.is_available("knowledge_library")
    runtime["library"] = 3
    assert ba.is_available("knowledge_library")


# ── pages are labels until something stands behind them ─────────────────────

def test_a_page_with_nothing_behind_it_is_not_in_this_build(runtime):
    runtime["manifest"] = {"surfaces": {
        "allowlist": ["instruments.telescope", "knowledge.library"],
        "derived_from_goal": True}}
    page = ba.page_row("instruments.telescope")
    assert page["status"] == ba.NOT_IN_THIS_BUILD
    assert "instruments." not in page["label"]
    # A page core code serves answers with that code's row.
    assert ba.page_row("knowledge.library")["id"] == "knowledge_library"
    # A page outside the taxonomy (a persona's own) is not this module's.
    assert ba.page_row("persona.nova") is None


def test_an_app_that_declares_a_page_backs_it(runtime):
    runtime["apps"] = [{"id": "alarmflow", "name": "AlarmFlow",
                        "surfaces": ["comms.timers"]}]
    assert ba.page_row("comms.timers")["status"] == ba.AVAILABLE


# ── what the goal asks for, computed from the goal ───────────────────────────

@pytest.mark.parametrize("goal,things", [
    (G1, ["smart telescope"]),
    (G2, []), (G3, []), (G4, []), (G5, []), (G6, []),
    ("a bot that controls my lights and plays through my record player",
     ["lights", "record player"]),
    ("it connects to my Nintendo Switch and talks to my 3D printer",
     ["nintendo switch", "3d printer"]),
    # one verb, two things (one case)
    ("I want a bot to control my nintendo switch and my record player",
     ["nintendo switch", "record player"]),
    ("works with my sewing machine, my serger and the embroidery hoop",
     ["sewing machine", "serger", "embroidery hoop"]),
    # a second VERB after and is not a thing
    ("works with my smart telescope and shows me cool space stuff",
     ["smart telescope"]),
])
def test_goal_things_come_from_the_sentence(goal, things):
    assert ba.goal_things(goal) == things


def test_a_goal_thing_nothing_covers_becomes_not_connected_yet(runtime):
    rows = ba.abilities(goal=G1)
    assert rows[0]["id"] == "goal:smart_telescope"
    assert rows[0]["label"] == "Work with your smart telescope"
    assert rows[0]["status"] == ba.NOT_IN_THIS_BUILD
    assert ba.absent_line(rows[0]) == ("work with your smart telescope: not "
                                       "connected yet, it can be added later.")


def test_the_same_rule_works_for_a_thing_that_is_not_about_space(runtime):
    rows = ba.abilities(goal="a bot that works with my sewing machine")
    assert ba.get("goal:sewing_machine", rows)["label"] == \
        "Work with your sewing machine"


def test_the_bots_own_app_blurb_does_not_cover_the_goal_thing(runtime):
    """The scaffolded app's blurb IS the goal sentence; describing a telescope
    drives none."""
    runtime["apps"] = [{"id": "nova", "name": "Nova", "surfaces": [],
                        "blurb": G1}]
    ids = [r["id"] for r in ba.abilities(goal=G1)]
    assert "goal:smart_telescope" in ids and "app:nova" in ids


def test_a_goal_thing_an_ability_already_covers_gets_no_second_row(runtime):
    rows = ba.abilities(goal="a bot that controls my lights")
    assert not [r for r in rows if r["id"].startswith("goal:")]
    assert ba.get("device_control", rows)["status"] == ba.NOT_SET_UP


def test_a_page_row_about_the_goal_thing_is_not_said_twice(runtime):
    runtime["manifest"] = {"surfaces": {
        "allowlist": ["instruments.telescope"], "derived_from_goal": True}}
    ids = [r["id"] for r in ba.abilities(goal=G1)]
    assert "goal:smart_telescope" in ids
    assert "page:instruments.telescope" not in ids


def test_goals_without_devices_add_no_goal_rows(runtime):
    for goal in (G2, G3, G4, G5, G6):
        assert not [r for r in ba.abilities(goal=goal)
                    if r["id"].startswith("goal:")], goal


# ── the provider hook ────────────────────────────────────────────────────────

def test_a_provider_row_replaces_the_core_row_with_the_same_id(runtime):
    ba.register_provider(lambda: [{"id": "scheduled_messages",
                                   "label": "Send you a daily message",
                                   "status": "available"}])
    assert ba.is_available("scheduled_messages")


def test_a_live_source_removes_the_generic_no_live_information_row(runtime):
    assert ba.get("live_data") is not None
    ba.register_provider(lambda: [{"id": "live:apod", "label": "Show a daily "
                                   "space picture", "status": "available"}])
    assert ba.get("live_data") is None
    assert ba.is_available("live:apod")


def test_a_provider_that_raises_or_returns_junk_changes_nothing(runtime):
    before = ba.abilities(goal="")

    def _boom():
        raise RuntimeError("provider broke")
    ba.register_provider(_boom)
    ba.register_provider(lambda: [None, {"id": ""}, "junk", {"label": "x"}])
    assert ba.abilities(goal="") == before


def test_registering_twice_is_a_no_op(runtime):
    def p():
        return []
    ba.register_provider(p)
    ba.register_provider(p)
    assert ba.providers() == (p,)
    ba.unregister_provider(p)
    assert ba.providers() == ()


# ── ordering ─────────────────────────────────────────────────────────────────

def test_the_goal_decides_what_is_named_first(runtime):
    order = [r["id"] for r in ba.goal_order(ba.absent(ba.abilities(goal=G1)),
                                            G1)]
    assert order[0] == "goal:smart_telescope"
    remind = "a bot that sends me daily reminders"
    order = [r["id"] for r in ba.goal_order(ba.absent(ba.abilities(goal=remind)),
                                            remind)]
    assert order[0] == "scheduled_messages"


# ── plain words everywhere an owner reads ────────────────────────────────────

@pytest.mark.parametrize("goal", [G1, G2, G3, G4, G5, G6])
def test_every_label_and_line_is_plain(runtime, goal):
    runtime["manifest"] = {"surfaces": {
        "allowlist": ["instruments.telescope", "house.cameras"],
        "derived_from_goal": True}}
    for r in ba.abilities(goal=goal):
        text = (ba.absent_line(r) + " " + r["label"]).lower()
        assert text.isascii()
        assert plain_words.is_plain(text, also=JARGON_EXTRA), \
            plain_words.check(text, also=JARGON_EXTRA)


# ── the model is told what it cannot do ──────────────────────────────────────

def test_the_prompt_lists_what_the_build_cannot_do_goal_first(runtime):
    block = bc.build_block("Nova", G1, [], home="")
    assert "Things you cannot do in this build:" in block
    lines = block.splitlines()
    first = lines[lines.index("Things you cannot do in this build:") + 1]
    assert "smart telescope" in first
    assert "say you can't do it yet" in block
    assert block.index("Things you cannot do") < block.index("[END BUILD")


def test_the_cannot_list_is_short(runtime):
    block = bc.build_block("Axle", G3, [], home="")
    listed = [ln for ln in block.splitlines() if ln.startswith("  - ")]
    assert 0 < len(listed) <= bc.MAX_CANNOT_LINES
    assert "live information" in block    # the thing a car bot would fake


def test_an_available_ability_is_not_listed_as_missing(runtime):
    runtime["doors"][0]["open"] = True
    assert not any("weather" in ln for ln in bc.cannot_lines(None, G4))


def test_the_clock_line_names_date_time_and_zone():
    moment = datetime(2026, 9, 23, 21, 5,
                      tzinfo=timezone(timedelta(hours=-4), "EDT"))
    line = bc.clock_line(moment)
    assert "Wednesday 23 September 2026" in line
    assert "21:05" in line and "EDT" in line


def test_the_clock_is_only_written_when_the_caller_gives_the_moment(runtime):
    """The wizard fingerprints this prompt; a clock would change that
    fingerprint every minute. The chat path passes the moment each call."""
    assert "Today is" not in bc.build_block("Nova", G1, [], home="")
    moment = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
    assert "Today is Wednesday" in bc.build_block("Nova", G1, [], home="",
                                                  now=moment)


def test_the_home_line_comes_from_the_provider_when_there_is_one(
        runtime, monkeypatch):
    import sys
    import types
    fake = types.ModuleType("puffpai.home_location")
    fake.context_line = lambda: "Home is near Columbus, Ohio (America/New_York)."
    monkeypatch.setitem(sys.modules, "puffpai.home_location", fake)
    assert "near Columbus" in bc.build_block("Sprout", G4, [])


def test_no_home_provider_means_no_home_line(runtime, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "puffpai.home_location", None)
    assert bc.home_line() == ""


# ── self_knowledge reads the same list ───────────────────────────────────────

def test_can_is_never_yes_without_a_record():
    ok, reason = sk.can("instruments.telescope", {})
    assert ok is False and reason
    assert sk.unknown("instruments.telescope", {})


def test_can_is_never_yes_when_the_check_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("broken")
    monkeypatch.setattr(sk, "_can", _boom)
    ok, reason = sk.can("house.lights", {"build": {"name": "x"}})
    assert ok is False and "can't tell" in reason
    assert sk.unknown("house.lights", {"build": {"name": "x"}})
    assert sk.refusal("house.lights", {"build": {"name": "x"}}) is None


def test_what_can_you_do_reads_the_runtime_not_the_record(runtime):
    """The record says web_fetch was opened; the runtime has weather open and
    nothing that reads web pages in chat."""
    runtime["doors"][0]["open"] = True
    man = {"build": {"name": "Nova"},
           "posture": {"run_egress": "enumerated",
                       "enabled_switches": ["weather", "web_fetch"]},
           "brain": {"model": "qwen2.5:7b-instruct"},
           "persona": {"goal": G1}}
    joined = "\n".join(sk.limits_summary(man, rows=ba.abilities(goal=G1)))
    assert "a weather website" in joined
    assert "web_fetch" not in joined
    assert "work with your smart telescope: not connected yet" in joined
    assert plain_words.is_plain(joined, also=JARGON_EXTRA), \
        plain_words.check(joined, also=JARGON_EXTRA)


def test_how_to_add_names_real_commands_not_talking(runtime):
    reply = sk.how_to_add_answer("how do I add more space stuff to you?",
                                 rows=ba.abilities(goal=G1))
    assert reply is not None
    assert "Talking to me does not add anything" in reply
    assert "run it" in reply                       # the library's command
    assert "smart telescope" in reply              # and what cannot be added
    assert sk.how_to_add_answer("what is a comet?") is None


def test_the_workflows_answer_says_none_when_there_are_none(monkeypatch,
                                                            runtime):
    from types import SimpleNamespace
    import puffpai.config as config
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(
        n8n=SimpleNamespace(enabled=False, url="")))
    text = sk.workflows_answer()
    assert "don't have any workflows" in text
    assert "Daily Briefing" not in text


def test_the_workflows_answer_names_a_schedule_a_provider_supplies(
        monkeypatch, runtime):
    from types import SimpleNamespace
    import puffpai.config as config
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(
        n8n=SimpleNamespace(enabled=False, url="")))
    ba.register_provider(lambda: [{"id": "scheduled_messages",
                                   "label": "Send you a morning message",
                                   "status": "available"}])
    assert sk.workflows_answer().startswith(
        "This build can send you a morning message.")


def test_other_lanes_rows_reach_every_reader(runtime):
    """The hook end to end: a routines row, a live source and a device adapter
    registered the way other parts will, then read by every reader."""
    from puffpai import build_context as bc
    from puffpai import honesty

    runtime["manifest"] = {"goal": G1}
    before = ba.abilities(goal=G1)
    assert ba.get("goal:smart_telescope", before) is not None
    offer = ("Would you like me to send you daily space updates every morning? "
             "I can also connect to your smart telescope.")
    assert len(honesty.offer_corrections(offer, before)) == 2

    ba.register_provider(lambda: [
        {"id": "scheduled_messages", "label": "Send you a message on a "
         "schedule", "status": "available", "how_to_add": None},
        {"id": "live:space_pictures", "label": "Show a new space picture "
         "each day", "status": "available", "how_to_add": None},
        {"id": "device:telescope", "label": "Work with your telescope",
         "status": "available", "how_to_add": None},
    ])
    after = ba.abilities(goal=G1)
    ids = {r["id"] for r in after}
    assert "live_data" not in ids and "goal:smart_telescope" not in ids
    assert ba.is_available("scheduled_messages", after)

    summary = "\n".join(sk.limits_summary(runtime["manifest"],
                                               rows=after))
    assert "send you a message on a schedule" in summary.lower()
    assert "telescope: not connected" not in summary.lower()
    cannot = "\n".join(bc.cannot_lines(after, G1)).lower()
    assert "schedule" not in cannot and "telescope" not in cannot
    assert honesty.offer_corrections(offer, after) == []


def test_home_assistant_over_mcp_counts_as_device_control(monkeypatch):
    from puffpai import mcp_client
    monkeypatch.setattr(mcp_client, "server", lambda name: (
        {"switched_on": True, "tool_count": 3} if name == mcp_client.HOME_ASSISTANT else None))
    got = ba._device_row(None)
    assert got["id"] == "device_control" and got["status"] == ba.AVAILABLE
    monkeypatch.setattr(mcp_client, "server", lambda name: None)
    assert ba._device_row(None)["status"] == ba.NOT_SET_UP
