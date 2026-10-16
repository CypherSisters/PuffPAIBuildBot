"""The reply guard: made-up links out, offers of missing abilities corrected.

Both failures are from a real run, answered verbatim below:
Q6 got a picture at https://example.com/image.jpg that nothing fetched, and
Q2/Q5 were offers ("set up the telescope now", "set up a news feed") of things
the build cannot do. The matching is generic - the build's own ability rows plus
a short list of doing verbs - so the same guard is tested on a car bot, a
knitting bot and a sewing machine, and every negative matters as much as every
positive: a false correction under a good answer is its own kind of lie.
"""

from __future__ import annotations

import pytest

from puffpai import build_abilities as ba
from puffpai import honesty

Q2 = ("I can help you with that! To determine what's in the sky tonight, we "
      "would need to check the current conditions and possibly use the smart "
      "telescope to look at the sky. Would you like to set up the telescope "
      "now?")
Q4 = ("Jupiter has many moons, but the four largest ones are known as the "
      "Galilean moons: Io, Europa, Ganymede, and Callisto. Ganymede is the "
      "largest moon in the Solar System.")
Q5 = ("I can help with that! To send you daily space updates, I'll need a way "
      "to fetch and deliver them. Would you like me to set up a news feed from "
      "a reliable space news site?")
Q6 = ("I can help with that! Here is the NASA Picture of the Day:\n\n"
      "![Image Title: Comet NEOWISE Over the Northern Lights]"
      "(https://example.com/image.jpg)\n\nWould you like more information?")


def _rows(goal_thing: str = "smart telescope") -> list[dict]:
    return [
        ba.row("goal:" + goal_thing.replace(" ", "_"),
               f"Work with your {goal_thing}", ba.NOT_IN_THIS_BUILD),
        ba.row("scheduled_messages", "Send you messages on a schedule without "
               "being asked (like daily updates or reminders that ring)",
               ba.NOT_IN_THIS_BUILD),
        ba.row("outside:news_rss", "Show news headlines from a news feed you "
               "name", ba.NOT_SET_UP),
        ba.row("outside:weather", "Get the weather forecast from a weather "
               "website", ba.AVAILABLE),
        ba.row("live_data", "Look up live information on the internet, like "
               "today's news or new pictures", ba.NOT_IN_THIS_BUILD),
        ba.row("web_search", "Search the web", ba.NOT_IN_THIS_BUILD),
        ba.row("knowledge_library", "Look things up in an offline library on "
               "this computer", ba.NOT_SET_UP, "puffpai knowledge sync"),
    ]


# ── made-up pictures and links ───────────────────────────────────────────────

def test_the_made_up_picture_of_the_day_is_taken_out():
    out, removed = honesty.strip_unbacked_links(Q6)
    assert removed == 1
    assert "example.com" not in out
    assert honesty.PICTURE_REMOVED in out


def test_a_picture_a_tool_fetched_stays():
    url = "https://apod.nasa.gov/apod/image/2609/x.jpg"
    text = f"Here it is: ![today]({url})"
    out, removed = honesty.strip_unbacked_links(text, {url})
    assert removed == 0 and out == text


def test_a_made_up_link_keeps_its_words_and_loses_its_address():
    out, removed = honesty.strip_unbacked_links(
        "See [the recall list](https://www.made-up-recalls.example/civic).")
    assert removed == 1
    assert out == "See the recall list."


@pytest.mark.parametrize("text", [
    "Jupiter has four large moons.",
    "Open [your garden page](/dashboard/garden) to see it.",   # this build's
    "Arrays look like [1, 2, 3] in Python.",                   # not a link
    "Knit 2 [k2], purl 2 [p2] and repeat.",
])
def test_text_without_an_outside_address_is_untouched(text):
    assert honesty.strip_unbacked_links(text) == (text, 0)


def test_an_address_the_owner_typed_is_backed():
    url = "https://www.nhtsa.gov/recalls"
    assert honesty.urls_in(f"is {url} right?") == {url}
    out, removed = honesty.strip_unbacked_links(f"[NHTSA]({url})",
                                                honesty.urls_in(url))
    assert removed == 0


def test_an_address_with_parentheses_is_read_whole():
    """Wikipedia addresses carry "(moon)"; cutting at the first ")" used to
    leave a broken link and a stray bracket in the owner's chat."""
    url = "https://en.wikipedia.org/wiki/Io_(moon)"
    assert honesty.urls_in(f"is {url} right?") == {url.lower()}
    text = f"See [Io]({url})."
    assert honesty.strip_unbacked_links(text, {url}) == (text, 0)
    assert honesty.strip_unbacked_links(text) == ("See Io.", 1)
    streamed = "".join(honesty.guard_stream(
        ["See [Io](https://en.wikipedia.org/wiki/Io_", "(moon))."], {url},
        abilities=[]))
    assert streamed == text


def test_the_stream_guard_catches_a_link_split_across_tokens():
    tokens = ["Here ", "is ", "![", "Comet", "](https://exa", "mple.com/",
              "i.jpg", ") done"]
    out = "".join(honesty.guard_stream(tokens, set(), abilities=[]))
    assert "example.com" not in out
    assert honesty.PICTURE_REMOVED in out and out.endswith(" done")


def test_the_stream_guard_passes_plain_text_through_unchanged():
    tokens = ["Io, ", "Europa, ", "Ganymede ", "and [Callisto]."]
    assert "".join(honesty.guard_stream(tokens, set(), abilities=[])) == \
        "Io, Europa, Ganymede and [Callisto]."


# ── offers of what the build cannot do ───────────────────────────────────────

def test_q2_the_telescope_offer_is_corrected():
    fixes = honesty.offer_corrections(Q2, _rows())
    assert fixes == ["To be clear: I can't work with your smart telescope "
                     "yet. That isn't part of this build; it can be added "
                     "later."]


def test_q5_daily_updates_and_the_news_feed_are_corrected():
    fixes = honesty.offer_corrections(Q5, _rows())
    assert len(fixes) == 2
    assert "send you messages on a schedule" in fixes[0]
    assert "(like" not in fixes[0]
    assert "news headlines" in fixes[1] and "isn't switched on" in fixes[1]


@pytest.mark.parametrize("thing,reply", [
    ("sewing machine", "Would you like me to connect to your sewing machine?"),
    ("3d printer", "I can start the 3D printer for you."),
    ("record player", "Let me turn on the record player."),
])
def test_the_same_rule_catches_things_that_are_not_about_space(thing, reply):
    fixes = honesty.offer_corrections(reply, _rows(thing))
    assert fixes and thing in fixes[0]


@pytest.mark.parametrize("reply", [
    Q4,                                                        # a real answer
    "You can use a telescope to see Jupiter's moons.",         # advice
    "I can tell you about telescopes and how they work.",      # knowledge
    "I cannot connect to your telescope yet.",                 # already honest
    "I can check the current weather for you.",                # weather is on
    "Would you like me to tell you the name of a good news site?",
    "Change your oil every 5,000 miles; I can explain why.",   # a car bot
    "I can suggest a daily knitting routine for beginners.",   # a knit bot
    "Should I explain how recalls work?",
])
def test_no_correction_under_answers_that_offer_nothing_missing(reply):
    assert honesty.offer_corrections(reply, _rows()) == []


def test_an_ability_a_provider_makes_available_is_never_corrected():
    rows = _rows()
    rows[1] = ba.row("scheduled_messages", "Send you a morning message",
                     ba.AVAILABLE)
    assert honesty.offer_corrections("I can send you daily updates.", rows) \
        == []


def test_an_unknown_ability_gets_a_cannot_be_sure_line():
    rows = [ba.row("device_control", "Control smart-home devices like lights",
                   ba.UNKNOWN)]
    fixes = honesty.offer_corrections("I'll turn on the lights now.", rows)
    assert fixes == ["To be clear: I can't be sure I can control smart-home "
                     "devices like lights right now."]


def test_check_reply_does_both_and_adds_each_fix_once():
    body = honesty.check_reply(Q6 + " " + Q2, allowed_urls=(),
                               abilities=_rows())
    assert "example.com" not in body
    assert body.count("To be clear:") == 1


def test_the_stream_guard_appends_the_correction_at_the_end():
    out = "".join(honesty.guard_stream([Q2[:40], Q2[40:]], set(),
                                       abilities=_rows()))
    assert out.startswith(Q2)
    assert out.endswith("it can be added later.")


# ── the chat path uses it ────────────────────────────────────────────────────

def test_the_chat_path_strips_the_fake_picture_and_corrects_the_offer(
        monkeypatch):
    from puffpai import api_server
    monkeypatch.setattr(ba, "abilities", lambda *a, **k: _rows())
    monkeypatch.setattr(api_server, "_owner_lists", lambda: {})
    result = api_server._honest_answer(
        {"response": Q6 + "\n" + Q2, "sources": []},
        "show me the NASA picture of the day")
    text = result["response"]
    assert "example.com" not in text
    assert "can't work with your smart telescope" in text


def test_the_chat_path_keeps_a_link_its_sources_produced(monkeypatch):
    from puffpai import api_server
    monkeypatch.setattr(ba, "abilities", lambda *a, **k: [])
    monkeypatch.setattr(api_server, "_owner_lists", lambda: {})
    url = "https://en.wikipedia.org/wiki/Io_(moon)"
    result = api_server._honest_answer(
        {"response": f"See [Io]({url}).", "sources": [{"url": url}]}, "io?")
    assert url in result["response"]


# ── the reminder note speaks the owner's own words ──────────────────────────

@pytest.mark.parametrize("ask,thing", [
    ("can you remind me about oil changes?", "oil changes"),
    ("remind me each Saturday to water", "water each Saturday"),
    ("remind me to cast on the scarf", "cast on the scarf"),
    ("set an alarm for six", ""),
])
def test_the_reminder_note_offers_back_what_was_asked(ask, thing):
    assert honesty.reminder_thing(ask) == thing
    note = honesty.reminder_note(ask, capability="none")
    assert "have not set one" in note
    assert "water the garden" not in note or "garden" in ask


# ── a way to add one thing, handed out for another ──────────────────────────
#
# Verbatim 7B replies from a proof run, after the prompt already said
# on every line whether a way to add it exists.

_HA = ("Run puffpai setup again and connect Home Assistant when it asks about "
       "a smart home")
_LIB = ("Put library files (.zim) in a folder, then run: puffpai knowledge "
        "sync --zim-dir <that folder>")


def _fix_rows() -> list[dict]:
    return [
        ba.row("goal:smart_telescope", "Work with your smart telescope",
               ba.NOT_IN_THIS_BUILD),
        ba.row("live_data", "Look up live information on the internet, like "
               "today's news or new pictures", ba.NOT_IN_THIS_BUILD),
        ba.row("knowledge_library", "Look things up in an offline library on "
               "this computer", ba.NOT_SET_UP, _LIB),
        ba.row("device_control", "Control smart-home devices like lights and "
               "plugs", ba.NOT_SET_UP, _HA),
    ]


def _all_fix_rows() -> list[dict]:
    return _fix_rows() + [
        ba.row("scheduled_messages", "Send you messages on a schedule without "
               "being asked (like daily updates or reminders that ring)",
               ba.NOT_IN_THIS_BUILD),
        ba.row("web_search", "Search the web", ba.NOT_IN_THIS_BUILD),
    ]


@pytest.mark.parametrize("reply,borrowed", [
    ("I can't control your smart telescope as it's not part of this build. To "
     "add control for your telescope, you would need to run puffpai setup "
     "again and connect Home Assistant when it asks about a smart home.",
     "control smart-home devices"),
    ("I can't show you the NASA picture of the day as this build doesn't "
     "include the ability to look up live information on the internet. To add "
     "this feature, you would need to run: puffpai knowledge sync --zim-dir "
     "<that folder>.", "offline library"),
    ("I can't send you daily space updates yet because I don't have access to "
     "live information on the internet. To add this feature, you need to run "
     "`puffpai setup` and connect Home Assistant when it asks about a smart "
     "home.", "control smart-home devices"),
])
def test_a_borrowed_way_to_add_is_corrected(reply, borrowed):
    fixes = honesty.misplaced_fix_corrections(reply, _fix_rows())
    assert len(fixes) == 1
    assert borrowed in fixes[0] and "There is no way to add that yet" in fixes[0]


# An end-to-end re-run (same build, same 7B
# model) produced three more phrasings the first version of the guard let
# through: a "no" it did not recognise ("is not part of this build"), a
# command that adds nothing at all ("puffpai persona new"), and a way to add
# wrapped in its own topic words ("to include an offline library").
@pytest.mark.parametrize("question,reply,said", [
    ("show me my telescope",
     "your telescope is not part of this build. You can add it by running "
     "puffpai setup again and connecting Home Assistant when it asks about a "
     "smart home.",
     "only helps me control smart-home devices like lights and plugs. It does "
     "not let me work with your smart telescope."),
    ("can you send me daily space updates?",
     "I can't send you daily space updates yet. To add this feature, you "
     "would need to run: puffpai persona new.",
     '"puffpai persona new" does not let me send you messages on a schedule'),
    ("what's in the sky tonight?",
     "I can't check the sky tonight as I don't have the ability to look up "
     "live information. We could add that feature by running puffpai "
     "knowledge sync --zim-dir <folder> to include an offline library, but I "
     "can still tell you some interesting facts about space.",
     "only helps me look things up in an offline library on this computer. "
     "It does not let me look up live information"),
])
def test_the_validators_live_phrasings_are_corrected(question, reply, said):
    for asked in (question, ""):   # with and without the owner's question
        fixes = honesty.misplaced_fix_corrections(reply, _all_fix_rows(),
                                                  asked)
        assert len(fixes) == 1, (asked, fixes)
        assert said in fixes[0]
        assert fixes[0].endswith("There is no way to add that yet.")


def test_the_same_rule_works_for_a_sewing_machine_and_a_car():
    rows = [ba.row("goal:sewing_machine", "Work with your sewing machine",
                   ba.NOT_IN_THIS_BUILD)] + _all_fix_rows()
    reply = ("Your sewing machine isn't connected. To do that, run puffpai "
             "setup again and connect Home Assistant.")
    fixes = honesty.misplaced_fix_corrections(reply, rows)
    assert len(fixes) == 1 and "work with your sewing machine" in fixes[0]
    car = ("I can't look up recalls for your Civic, because I have no live "
           "information. You can get that ability with puffpai apps install.")
    fixes = honesty.misplaced_fix_corrections(car, _all_fix_rows())
    assert len(fixes) == 1 and '"puffpai apps install"' in fixes[0]


def test_a_missing_thing_with_its_own_way_to_add_gets_that_way():
    rows = [dict(r, how_to_add="Run puffpai setup again and say yes to the "
                 "search question") if r["id"] == "web_search" else r
            for r in _all_fix_rows()]
    reply = ("I can't search the web yet. To add it, connect Home Assistant "
             "when it asks.")
    fixes = honesty.misplaced_fix_corrections(reply, rows)
    assert len(fixes) == 1
    assert fixes[0].endswith("To add that: Run puffpai setup again and say "
                             "yes to the search question.")


@pytest.mark.parametrize("reply", [
    "I can't control your lights yet. To add it, run puffpai setup again and "
    "connect Home Assistant when it asks about a smart home.",
    "I can't look that up in a library yet. " + _LIB + ".",
    "I can't send you daily updates yet; that is not part of this build.",
    # an alternative offered for its own sake, not for the missing thing
    "I can't work with your telescope. If you want your lights too, run "
    "puffpai setup again and connect Home Assistant when it asks about a "
    "smart home.",
    # the reply is about both
    "I can't control your lights or your telescope. Run puffpai setup again "
    "and connect Home Assistant when it asks about a smart home.",
    # a command that is not handed out as a way to add anything
    "I can't send you daily updates. You can talk to me any time with "
    "puffpai chat.",
    # the bot's own "what can you do" list: each way to add sits on its line
    "Things I can't do yet:\n"
    "- work with your smart telescope: not connected yet, it can be added "
    "later.\n"
    "- look things up in an offline library on this computer: none added "
    "yet. To add it: " + _LIB + ".\n"
    "- control smart-home devices like lights and plugs: not switched on "
    "yet. To add it: " + _HA + ".\n"
    "- look up live information on the internet: not part of this build.\n",
    Q4,
])
def test_a_way_to_add_given_for_its_own_thing_is_left_alone(reply):
    assert honesty.misplaced_fix_corrections(reply, _all_fix_rows()) == []


def test_the_owners_question_names_what_a_reply_is_about():
    reply = ("That isn't something I do. To add it, run puffpai setup again "
             "and connect Home Assistant when it asks about a smart home.")
    assert honesty.misplaced_fix_corrections(reply, _all_fix_rows()) == []
    fixes = honesty.misplaced_fix_corrections(reply, _all_fix_rows(),
                                              "point my telescope at Mars")
    assert len(fixes) == 1 and "smart telescope" in fixes[0]
    # asking about the lights makes the same reply right
    assert honesty.misplaced_fix_corrections(
        reply, _all_fix_rows(), "can you turn on my lights?") == []


def test_check_reply_adds_the_borrowed_fix_correction():
    reply = ("I can't work with your smart telescope. To add it, connect Home "
             "Assistant when it asks about a smart home.")
    out = honesty.check_reply(reply, abilities=_fix_rows())
    assert out.startswith(reply) and "only helps me" in out
    out = honesty.check_reply("That isn't set up. To add it, connect Home "
                              "Assistant.", abilities=_fix_rows(),
                              question="show me my telescope")
    assert "work with your smart telescope" in out


def test_the_stream_guard_uses_the_owners_question_too():
    tokens = ["That isn't set up. ", "To add it, connect ", "Home Assistant."]
    out = "".join(honesty.guard_stream(iter(tokens), set(), _fix_rows(),
                                       question="show me my telescope"))
    assert "does not let me work with your smart telescope" in out


def test_each_prompt_line_says_whether_a_way_to_add_exists():
    from puffpai import build_context as bc
    lines = bc.cannot_lines(_fix_rows(), "")
    assert "work with your smart telescope - not part of this build. There is "\
           "no way to add it yet." in lines
    assert any(line.startswith("control smart-home devices like lights and "
                               "plugs - switched off. Way to add it: Run "
                               "puffpai setup again") for line in lines)


def test_a_correction_is_never_read_as_an_offer_itself():
    """Guarding a reply twice adds nothing the second time."""
    reply = ("I can't send you daily space updates yet. To add this feature, "
             "you would need to run: puffpai persona new.")
    once = honesty.check_reply(reply, abilities=_all_fix_rows())
    assert once != reply
    assert honesty.check_reply(once, abilities=_all_fix_rows()) == once
