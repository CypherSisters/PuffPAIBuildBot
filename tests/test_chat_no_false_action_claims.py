"""No reply claims an app action that did not run.

The slice showed keyless ``/v1/chat/completions`` and ``/api/chat`` replying
"I've added Leo Park to the guest list" when nothing ran. Later (the
validator found 21 of 27 claim shapes and 9 of 24 live replies slipping past
the sentence rules, among them "Got it! (emoji) Basil added to your Garden
list."): the decision is made on the OWNER'S message, which is bounded, not on
the model's reply, which is not.

* When her message asks for a change to her things (:func:`chat.change_turn`)
  and the turn reaches the model, nothing ran: every real app action is
  answered before the model from its own evidence and the model has no tools.
  So the model is not asked at all; the reply is one fixed honest line, on
  every chat path: the plain and streamed OpenAI routes, the Ollama route,
  /conversation, /chat/send, /chat/stream and the terminal.
* Any reply that still reaches the claim guard after such an ask is replaced
  whole (a second net for callers of ``scrub_app_claims``/``guard_app_claims``).
* Every other turn keeps the sentence rules: a claim in an answer to a
  question or a statement is replaced, the rest of the answer stays.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import api_server  # noqa: E402
from puffpai.integrate import chat, wording  # noqa: E402

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
XKEY = {"X-API-Key": api_server._API_KEY}
DEVICE = "deviceaaaa000001"
PAIRED = {**AUTH, "X-Puff-Device-Id": DEVICE}
CLAIM = ("Sure! I've added Leo Park to the guest list as attending, with chicken. "
         "Anything else?")
EMOJI_CLAIM = "Got it! \U0001f33f Leo Park added to your Big Day list. Anything else?"
ASK = "add Leo Park to the guest list, attending, chicken"


def _world(monkeypatch, tmp_path, reply):
    w = sup.setup_chat(monkeypatch, tmp_path, brain=sup.Brain(None, None, None, None))
    w["model_calls"] = []

    def direct(*a, **k):
        w["model_calls"].append("direct")
        return {"response": reply, "sources": [], "suggestions": []} if k.get("enriched") \
            else reply

    def stream(*a, **k):
        w["model_calls"].append("stream")
        return iter([reply[:9], reply[9:30], reply[30:]])

    monkeypatch.setattr(api_server, "_query_ollama_direct", direct)
    monkeypatch.setattr(api_server, "_stream_ollama_tokens", stream)
    monkeypatch.setattr(api_server, "_detect_route", lambda *a, **k: "rag")
    api_server.app.config.update(TESTING=True)
    return w


@pytest.fixture(params=[CLAIM, EMOJI_CLAIM], ids=["plain", "emoji"])
def world(request, monkeypatch, tmp_path):
    return _world(monkeypatch, tmp_path, request.param)


@pytest.fixture
def client():
    return api_server.app.test_client()


def _stream_text(body: str) -> str:
    out = ""
    for line in body.splitlines():
        if not line.startswith("data: {"):
            continue
        data = json.loads(line[6:])
        if "choices" in data:
            out += data["choices"][0]["delta"].get("content") or ""
        else:
            out += data.get("delta") or ""
    return out


#: A caller the app step runs for (a paired device, the
#: terminal) hears which app it tried and what that app can do, when no
#: action fits a change ask. Still never the model, still nothing run.
FITS = wording.NOTHING_FITS.format(
    app="Big Day", can="add a guest to the guest list, remove a guest from the guest list, "
                       "add a task to the checklist or clear the done tasks")


def _honest(world, text: str, line: str = wording.NO_CHANGE_UNKNOWN) -> None:
    assert "Leo Park" not in text, text
    assert "added" not in text.lower(), text
    assert text.strip() == line, text
    assert world["model_calls"] == []                # the model was never asked
    assert world["source"].runs == []


# -- every chat path: a change ask that nothing ran never reaches the model --------

@pytest.mark.parametrize("headers,line", [
    ({}, wording.NO_CHANGE_KEYLESS),
    ({"X-Client": "home-assistant"}, wording.NO_CHANGE_KEYLESS),
    (AUTH, wording.NO_CHANGE_UNKNOWN)])
def test_the_openai_route_never_claims(world, client, headers, line):
    resp = client.post("/v1/chat/completions", headers=headers,
                       json={"model": "x", "messages": [{"role": "user", "content": ASK}]})
    _honest(world, resp.get_json()["choices"][0]["message"]["content"], line)


@pytest.mark.parametrize("headers,line", [({}, wording.NO_CHANGE_KEYLESS),
                                          (PAIRED, FITS)])
def test_the_streamed_openai_route_never_claims(world, client, headers, line):
    resp = client.post("/v1/chat/completions", headers=headers,
                       json={"model": "puffpai", "stream": True,
                             "messages": [{"role": "user", "content": ASK}]})
    body = resp.get_data(as_text=True)
    _honest(world, _stream_text(body), line)
    assert body.rstrip().endswith("data: [DONE]")


@pytest.mark.parametrize("stream", [False, True])
def test_the_ollama_route_never_claims(world, client, stream):
    resp = client.post("/api/chat", json={"stream": stream,
                                          "messages": [{"role": "user", "content": ASK}]})
    raw = resp.get_data(as_text=True)
    text = "".join((json.loads(l).get("message") or {}).get("content", "")
                   for l in raw.splitlines() if l.strip().startswith("{"))
    _honest(world, text, wording.NO_CHANGE_KEYLESS)


def test_the_conversation_route_never_claims(world, client):
    got = client.post("/conversation", headers=XKEY, json={"text": ASK}).get_json()
    _honest(world, got["response"])


def test_chat_send_and_stream_never_claim(world, client):
    _honest(world, client.post("/chat/send", headers=PAIRED,
                               json={"message": ASK}).get_json()["response"], FITS)
    _honest(world, _stream_text(client.post("/chat/stream", headers=PAIRED,
                                            json={"message": ASK}).get_data(as_text=True)), FITS)


def test_the_terminal_never_claims(world):
    _honest(world, api_server._process_chat_query(ASK, [])["response"], FITS)


@pytest.mark.parametrize("route", ["agent", "rag"])
def test_both_model_routes_are_covered(world, monkeypatch, route):
    monkeypatch.setattr(api_server, "_detect_route", lambda *a, **k: route)
    _honest(world, api_server._process_chat_query(ASK, [])["response"], FITS)
    _honest(world, api_server._process_voice_query(ASK, []))


def test_a_reminder_ask_keeps_its_own_honest_line(world, monkeypatch):
    monkeypatch.setattr(api_server, "_reminder_note",
                        lambda msg: "I cannot set reminders in this build.")
    got = api_server._process_chat_query("add milk to the list and remind me", [])
    assert got["response"].startswith(wording.NO_APP_CHANGE.rstrip("."))
    assert got["response"].endswith("I cannot set reminders in this build.")
    assert world["model_calls"] == []


def test_a_real_action_keeps_its_own_line(monkeypatch, tmp_path):
    brain = sup.Brain((f"{sup.IID}__add_guest", {"guest_name": "Leo Park"}))
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    got = chat.try_answer("add Leo Park to the guest list", surface="chat",
                          device_id=f"device:{DEVICE}")
    assert got["reply"].startswith("Done in Big Day") and len(w["source"].runs) == 1


def test_a_real_action_on_the_streamed_dashboard_route(monkeypatch, tmp_path, client):
    """The honest line comes AFTER the real steps: a paired device's change
    still runs and is answered from its own evidence."""
    brain = sup.Brain((f"{sup.IID}__add_guest", {"guest_name": "Leo Park"}))
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    monkeypatch.setattr(api_server, "_detect_route", lambda *a, **k: "rag")
    resp = client.post("/v1/chat/completions", headers=PAIRED,
                       json={"model": "puffpai", "stream": True, "messages": [
                           {"role": "user", "content": "add Leo Park to the guest list in Big Day"}]})
    assert _stream_text(resp.get_data(as_text=True)).startswith("Done in Big Day")
    assert len(w["source"].runs) == 1


# -- the owner's message: which asks are changes ------------------------------------

@pytest.mark.parametrize("ask", [
    "add Leo Park to the guest list", "add basil to plantings", "add Leo",
    "Sam did the dishes, mark it", "add Wonderwall to the setlist",
    "put $40 groceries in the budget", "remove Aunt May from the guests",
    "take tomatoes off the plantings", "mark the oil change as done", "log a 5k run",
    "Leo can come after all, put him down", "we bought eggs, cross them off",
    "can you add Leo to the guest list?", "please delete the cake from my list",
    "hey puff, add eggs", "add these to the shopping list: eggs, milk, bread",
    "clear the plantings list", "write the dentist down for Friday",
    "update my budget sheet with the florist", "change basil to kale on the plantings list",
    "Save me a seat at the table", "add feeding at 6pm for the cat",
])
def test_change_asks(ask):
    assert chat.change_turn(ask), ask


@pytest.mark.parametrize("ask", [
    "Leo Park is coming too", "who is on my guest list so far?", "add a summary",
    "add more detail", "add 5 and 7", "remove the jargon from this email",
    "write me a poem about my dog", "check the weather for Saturday", "put on some jazz",
    "how do I add a filter in Excel?", "fix this list: apples, pears, apples, plums",
    "take out the trash", "check out this song", "what should I add to my garden?",
    "I need to add milk", "update me on the news", "change the subject",
    "cancel the Tuesday lesson", "Put simply, what is a mortgage?",
    "remove the duplicates from these names: Ann, Bob, Ann, Cy, Dee",
])
def test_not_change_asks(ask):
    assert not chat.change_turn(ask), ask


def test_her_list_names_make_a_softer_verb_a_change():
    assert not chat.change_turn("cancel the Tuesday lesson")
    assert chat.change_turn("cancel the Tuesday lesson", names=["Lessons"]) is False
    assert chat.change_turn("cancel the Tuesday lesson in Lessons", names=["Lessons"])
    assert chat.change_turn("move basil to Harvest", names=["Harvest"])


# -- the second net: after a change ask any reply is replaced whole -----------------
#
# Probe phrases and the
# real qwen2.5:7b replies that slipped past the first sentence rules.

_VALIDATOR_PHRASES = [
    ("add Leo Park to the guest list", "Here's your updated guest list: Leo Park, Aunt May."),
    ("add Leo Park to the guest list", "Updated guest list:\n- Leo Park\n- Aunt May"),
    ("add Leo Park to the guest list", "Leo Park is now a guest at your wedding!"),
    ("add Leo Park to the guest list", "Your guest list now includes Leo Park."),
    ("add Leo Park to the guest list", "Leo Park has joined the guest list."),
    ("add Leo Park to the guest list", "Noted!"),
    ("add Leo Park to the guest list", "Okay, Leo Park is in!"),
    ("add Leo Park to the guest list", "Leo Park: added."),
    ("add Leo Park to the guest list", "Welcome aboard, Leo Park! You're on the list."),
    ("remove Aunt May from the guests", "Aunt May is no longer on the guest list."),
    ("remove Aunt May from the guests", "Consider Aunt May removed."),
    ("remove Aunt May from the guests", "Aunt May won't be on the list anymore."),
    ("mark the oil change as done", "The oil change is complete."),
    ("mark the oil change as done", "Oil change: done."),
    ("mark the oil change as done", "Your oil change is now marked as completed in the tracker."),
    ("mark the oil change as done", "Great, the oil change is all taken care of!"),
    ("log a 5k run", "Your 5k run is in the books!"),
    ("log a 5k run", "5k run logged for today."),
    ("Leo can come after all, put him down", "Got it, Leo is down for the wedding."),
    ("we bought eggs, cross them off", "Eggs are crossed off."),
    ("add basil to plantings", "Basil has been planted in your list."),
    ("add basil to plantings", "Your plantings list: basil, tomatoes."),
    ("add basil to plantings", "Done and done!"),
    ("add basil to plantings", "Voila, basil is there."),
    ("add basil to plantings", "Added!"),
    ("add basil to plantings", "Added."),
]
_LIVE_MISSES = [
    ("add basil to plantings",
     "Got it! \U0001f33f Basil added to your Garden list. Happy herb growing! \U0001f35d"),
    ("Sam did the dishes, mark it",
     "Got it, Sam did the dishes! \U0001f31f Marked it on the Chores board!"),
    ("Sam did the dishes, mark it",
     "Great job, Sam! \U0001f6b4 Marking it on the Chores board now!"),
    ("add Wonderwall to the setlist", "Sure thing, buddy! Wonderwall is a classic! \U0001f3b8"),
    ("add Wonderwall to the setlist", "Got it, Wonderwall is on for the show! \U0001f3b8\U0001f31f"),
    ("put $40 groceries in the budget",
     "\U0001f44c Got it! Added $40 for groceries. Shopping list updated too! \U0001f6d2"),
    ("put $40 groceries in the budget",
     "Budget updated! \U0001f34e+$40. Shopping day vibes! \U0001f389"),
    ("remove Aunt May from the guests",
     "Got it, Aunt May will be crossed off the guest list! \U0001f64c"),
    ("take tomatoes off the plantings", "Tomato plants out of the garden plan."),
]


@pytest.mark.parametrize("ask,reply", _VALIDATOR_PHRASES + _LIVE_MISSES)
def test_any_reply_to_a_change_ask_is_replaced_whole(ask, reply):
    out, n = chat.scrub_app_claims(reply, asked=ask)
    assert (out, n) == (wording.NO_APP_CHANGE, 1), out
    streamed = "".join(chat.guard_app_claims(iter([reply[:9], reply[9:]]), asked=ask))
    assert streamed == wording.NO_APP_CHANGE, streamed
    got = api_server._honest_answer({"response": reply, "sources": [],
                                     "suggestions": []}, ask)["response"]
    assert reply.split("!")[0] not in got or reply.split("!")[0] in wording.NO_APP_CHANGE
    assert got.startswith("I have not changed anything in your apps"), got


def test_the_stream_guard_closes_the_model_stream_after_a_change_ask():
    closed = []

    def tokens():
        try:
            yield "Got it! "
            yield "Leo added."
        finally:
            closed.append(True)

    gen = tokens()
    next(gen)                                      # the model has started
    assert list(chat.guard_app_claims(gen, asked="add Leo")) == [wording.NO_APP_CHANGE]
    assert closed == [True]


# -- every other turn: the sentence rules, the rest of the answer stays -------------

@pytest.mark.parametrize("text", [
    "I've added Leo Park to the guest list.",
    "I have added \"Leo Park, attending, chicken\" to the guest list.",
    "Leo Park has been added to your guest list.",
    "Added Leo Park to the guest list.",
    "I removed the cake from your shopping list.",
    "I'll put that on your checklist.",
    "Leo is now on the guest list.",
    "I have updated your budget sheet with the florist cost.",
    "I went ahead and took Leo off the guest list.",
    "Got it! \U0001f33f I've added basil to your garden list.",
])
def test_claims_are_caught(text):
    assert chat.claims_app_action(text)


@pytest.mark.parametrize("text", [
    "I can't add things to your guest list from here.",
    "You can add Leo to the guest list in the app.",
    "I added some ideas below.",
    "I have not added anything to your list.",
    "Adding flowers to the table is a nice touch.",
    "Paris is the capital of France.",
    "I recommend adding a buffer to your budget.",
])
def test_ordinary_sentences_are_left_alone(text):
    assert not chat.claims_app_action(text)
    assert chat.scrub_app_claims(text) == (text, 0)


def test_round3_final_offers_go_and_list_contents_are_claims():
    """An offer of a change is taken
    out, so a yes to it cannot arise; a model sentence that says what is on
    her list is a claim (no model ever sees her app data)."""
    out, n = chat.scrub_app_claims("Would you like me to add Leo to the guest list?")
    assert n == 1 and "add Leo" not in out
    out, n = chat.scrub_app_claims("The guest list so far includes Leo Park.")
    assert n == 1 and out == wording.NO_APP_CHANGE
    # the same after "who is on my guest list so far?" (a read that did not
    # run: the model cannot know, so it may not say)
    out, n = chat.scrub_app_claims("The guest list so far includes Leo Park.",
                                   asked="who is on my guest list so far?")
    assert n == 1 and out == wording.NO_APP_CHANGE


def test_the_stream_guard_keeps_everything_else_in_order():
    parts = list(chat.guard_app_claims(["Hello. I", "'ve added Leo to ", "the guest list.",
                                        " Bye", "!"]))
    assert "".join(parts) == "Hello. " + wording.NO_APP_CHANGE + " Bye!"


@pytest.mark.parametrize("ask,reply", [
    ("Leo Park is coming too", "I've added Leo Park as a guest."),
    ("Leo Park is coming too", "Great news! I'll add him to the guest list right away!"),
    ("Leo Park is coming too", "Leo Park: added."),
    ("the oil change is done", "Great, I've marked it as done."),
    ("change basil to kale", "I changed basil to kale."),
    ("cancel the Tuesday lesson", "I've cancelled Tuesday's lesson."),
    ("who is coming so far?", "I've added Leo Park to the guest list."),
])
def test_claims_after_other_turns_are_caught(ask, reply):
    out, n = chat.scrub_app_claims(reply + " Anything else?", asked=ask)
    assert n >= 1 and wording.NO_APP_CHANGE in out and "Anything else?" in out, out


@pytest.mark.parametrize("ask,reply", [
    ("add a summary", "I've added a short summary below."),
    ("write me a poem about my dog", "I've written a poem about your dog."),
    ("what is a good bread recipe?", "I've included a simple recipe."),
    ("what is a good bread recipe?", "Salt added to the dough helps the flavour."),
    ("check the weather for Saturday", "I checked, and it looks sunny."),
    ("Leo Park is coming too", "Yay! Welcome Leo Park to the big day!"),
    ("tell me a story about a knight", "The knight added a log to the fire."),
])
def test_ordinary_replies_are_left_alone(ask, reply):
    assert chat.scrub_app_claims(reply, asked=ask) == (reply, 0)


def test_a_question_turn_keeps_the_rest_of_the_answer(monkeypatch, tmp_path, client):
    w = _world(monkeypatch, tmp_path, "I've added Leo Park to the guest list. Anything else?")
    got = client.post("/chat/send", headers=PAIRED,
                      json={"message": "what is a good first dance song?"}).get_json()["response"]
    assert wording.NO_APP_CHANGE in got and "Anything else?" in got
    assert "added Leo Park" not in got and w["model_calls"] == ["direct"]


# -- shapes found with the real model ----------------
#
# "I need Leo Park on the guest list" and "stick Leo Park on
# the guest list" reached the model, which said "Leo Park is all set for the
# big day!"; after statements ("Leo Park is coming too", "Leo cancelled") the
# replies "Leo Park has been invited." and "Leo's been added." came through.

@pytest.mark.parametrize("ask,names", [
    ("I need Leo Park on the guest list", ()), ("stick Leo Park on the guest list", ()),
    ("stick Leo on the guest list", ()), ("let's add Leo Park to the guest list", ()),
    ("count Leo Park in for Big Day", ("Big Day",)), ("include Leo Park", ()),
    ("include Leo Park in Big Day", ("Big Day",)), ("scratch Leo", ()),
    ("Leo Park should be invited", ()), ("can Leo Park be on the list?", ()),
    ("we'd like basil in Plantings", ("Plantings",)), ("pop the florist on my budget", ()),
    ("Leo is coming, can you note that", ()), ("I want Aunt May off the guest list", ()),
])
def test_more_change_asks(ask, names):
    assert chat.change_turn(ask, names), ask


@pytest.mark.parametrize("ask", [
    "I want to stick to the budget", "I need help on the budget", "keep an eye on the budget",
    "I'm working on the guest list", "get started on the guest list", "scratch that, tell me a joke",
    "include more details", "include a joke in the toast", "let's talk about the budget",
    "let's plan the wedding", "I need ideas for the guest list", "count to ten", "keep it short",
    "the cake should be baked at 350", "should I invite my boss?", "Leo Park is coming too",
])
def test_ordinary_asks_still_reach_the_model(ask):
    assert not chat.change_turn(ask, ["Big Day", "Guest list", "Plantings"]), ask


@pytest.mark.parametrize("ask,reply", [
    ("Leo Park is coming too", "Leo Park has been invited."),
    ("Leo Park is coming too", "Got it, Leo Park is in."),
    ("Leo Park is coming too", "Your guest list now includes Leo Park."),
    ("Leo Park is coming too", "Leo's been added."),
    ("Leo Park is coming too", "Leo Park is confirmed for the wedding."),
    ("Leo Park is coming too", "Great, I've noted that Leo is coming."),
    ("Leo Park is coming too", "I have RSVP'd Leo Park."),
    ("Leo Park is coming too", "Your guest count is now 43."),
    ("Leo Park is coming too",
     "Yay! Welcome to the family, Leo! Big Day app just updated his name on the guest list!"),
    ("count Leo in", "Got it! Leo's in the guest list now."),
    ("count Leo in", "He's in!"),
    ("Leo cancelled", "He's off the list."),
    ("Leo cancelled", "Leo won't be on the guest list anymore."),
    ("Leo cancelled", "Oh no! Let's update the guest list right away."),
    ("Leo can't come anymore", "They have been taken off."),
    ("we invited Leo Park", "Leo Park is all set for the big day!"),
])
def test_claims_after_a_statement_are_caught(ask, reply):
    out, n = chat.scrub_app_claims(reply, asked=ask)
    assert n >= 1 and wording.NO_APP_CHANGE in out, out
    streamed = "".join(chat.guard_app_claims(iter([reply[:7], reply[7:]]), asked=ask))
    assert wording.NO_APP_CHANGE in streamed, streamed


@pytest.mark.parametrize("ask,reply", [
    ("I baked cookies", "Yum! They're done just in time for the party."),
    ("I'm tired today", "Rest up, you've earned it."),
    ("My brother just got engaged", "Congratulations! He must be so excited."),
    ("Leo is in Paris this week", "Oh nice, Leo is in Paris! Enjoy the quiet."),
    ("we invited Leo Park", "Lovely! Leo Park will be a great guest."),
    ("Leo RSVP'd yes", "Yay! Leo's coming to the big day!"),
    ("Leo Park is coming too", "Yay! Leo's coming, add him to the guest list!"),
    ("the oil change is done", "Great job staying on top of your car care."),
])
def test_ordinary_replies_to_a_statement_stay(ask, reply):
    assert chat.scrub_app_claims(reply, asked=ask) == (reply, 0)


@pytest.mark.parametrize("ask,reply,kept", [
    ("Leo Park is coming too", "That's wonderful news. Would you like me to add him?",
     "That's wonderful news."),
    ("Leo cancelled", "Oh no, sorry to hear that. Do you want me to take him off the list?",
     "Oh no, sorry to hear that."),
])
def test_round3_final_offers_after_a_statement_are_taken_out(ask, reply, kept):
    assert chat.scrub_app_claims(reply, asked=ask) == (kept, 1)


@pytest.mark.parametrize("ask,reply", [
    ("I need Leo Park on the guest list in Big Day", "Leo's been added."),
    ("count Leo Park in for Big Day", "Got it, Leo Park is in."),
    ("Leo Park is coming too", "Leo Park has been invited."),
    ("stick Leo on the guest list", "Leo's been added to the guest list."),
])
def test_the_paired_chat_and_the_terminal_stay_honest(monkeypatch, tmp_path, client, ask, reply):
    w = _world(monkeypatch, tmp_path, reply)
    got = client.post("/chat/send", headers=PAIRED, json={"message": ask}).get_json()["response"]
    assert reply not in got, got
    assert got.startswith("I have not changed anything in your apps"), got
    term = api_server._process_chat_query(ask)
    term = term.get("response") if isinstance(term, dict) else term
    assert reply not in term, term
    assert term.startswith("I have not changed anything in your apps"), term
    assert w["source"].runs == []


def test_her_app_names_anchor_a_claim_after_a_statement():
    """Live (seed 11): after "Leo can't come anymore" the model said "Let's
    update Big Day right away." Her app name anchors it."""
    reply = "Oh no! That's a bummer. Let's update Big Day right away."
    out, n = chat.scrub_app_claims(reply, asked="Leo can't come anymore", names=["Big Day"])
    assert n == 1 and "Big Day" not in out and wording.NO_APP_CHANGE in out, out
    streamed = "".join(chat.guard_app_claims(iter([reply]), asked="Leo can't come anymore",
                                             names=["Big Day"]))
    assert "Big Day" not in streamed, streamed
    ok = "Oh no! Maybe Big Day can still be fun."
    assert chat.scrub_app_claims(ok, asked="Leo can't come anymore",
                                 names=["Big Day"]) == (ok, 0)


# -- shapes found through every layer ----------------
#
# honesty.scrub_claims then
# chat.scrub_app_claims with her app and list names. No list of reply shapes is
# complete; these pin what was found and the two ways it is closed: more asks
# are read as change asks (the model is not asked at all), and after a
# statement a status line about her thing is a claim.

_R5_NAMES = ["Big Day", "Guest list", "Garden Log", "plantings", "Chore Chart", "chores",
             "Book Club", "reading list"]


@pytest.mark.parametrize("ask", [
    "assign laundry to Sam", "swap Dune for Emma", "bump Dune to next month",
    "change Dune's rating to 5", "rename laundry to wash clothes", "set the dishes to Sam",
    "move the dishes to Tuesday", "Dune goes on the reading list",
])
def test_record_verbs_are_change_asks_when_she_has_apps(ask):
    assert chat.change_turn(ask, _R5_NAMES), ask


@pytest.mark.parametrize("ask", [
    "set a timer for 10 minutes", "set the lights to 50%", "change the tone to formal",
    "move on to the next topic", "replace butter with oil", "switch to Spanish",
    "give it to me straight", "assign a value to x in python", "rename a file in linux",
    "move the couch to the living room", "change my name to Robin", "set the oven to 350",
    "swap butter for oil", "bump the volume up to 11", "set my alarm for 7",
    "the kids go to school at 8", "give Sam a hug for me", "how many tomatoes did I plant?",
])
def test_everyday_asks_with_those_verbs_still_reach_the_model(ask):
    assert not chat.change_turn(ask, _R5_NAMES), ask


def test_record_verbs_are_change_asks_for_keyless_callers_too():
    """"bump the vet visit to
    Thursday" is a record change whoever asks; a keyless caller gets the
    honest keyless line, not the model."""
    assert chat.change_turn("assign laundry to Sam")
    assert chat.change_turn("bump Dune to next month", ())
    assert not chat.change_turn("assign a value to x in python", ())


_R5_CLAIMS = [
    ("Leo Park is coming too", "Guest list updated."),
    ("Leo Park is coming too", "Big Day updated: Leo Park attending."),
    ("Leo Park is coming too", "Leo Park: attending."),
    ("Leo Park is coming too", "Leo Park, attending. That brings you to 43."),
    ("Leo Park is coming too", "Your guest list reflects that now."),
    ("Leo Park is coming too", "Noted! I'll remember Leo is coming."),
    ("Leo cancelled", "Leo removed."),
    ("the dishes are done", "Chore Chart updated: dishes done."),
    ("the dishes are done", "Nice work! That's one less chore on the chart."),
    ("the kids did laundry", "Laundry: complete."),
    ("the kids did laundry", "Your chore chart reflects that now."),
    ("I planted tomatoes today", "Tomatoes planted today, got it in there."),
    ("assign laundry to Sam", "Laundry assigned to Sam."),
    ("assign laundry to Sam", "Laundry is now assigned to Sam."),
    ("assign laundry to Sam", "Sam has laundry now."),
    ("swap Dune for Emma", "Swapped! Emma replaces Dune."),
    ("bump Dune to next month", "Dune is bumped to next month."),
    ("change Dune's rating to 5", "Dune's rating is 5 now."),
    ("rename laundry to wash clothes", "Laundry is now called wash clothes."),
    ("set the dishes to Sam", "The dishes are Sam's now."),
    ("move the dishes to Tuesday", "The dishes are on Tuesday now."),
    # shapes found while fixing, not in the validator's list
    ("Leo Park is coming too", "Leo Park - confirmed!"),
    ("Leo Park is coming too", "Checked: Leo Park."),
    ("Leo Park is coming too", "Guest list: Aunt May, Leo Park."),
    ("Leo Park is coming too", "Leo Park now appears on your guest list."),
    ("Leo Park is coming too", "He'll show up on the list."),
    ("Leo Park is coming too", "There's Leo on the list now."),
    ("Leo Park is coming too", "One more guest for Big Day!"),
    ("Leo Park is coming too", "I'll keep Leo in mind for the seating."),
    ("Leo cancelled", "Leo gone from the list."),
    ("Leo cancelled", "No more Leo on the guest list."),
    ("the dishes are done", "Dishes ticked."),
    ("we finished Dune", "Book Club: Dune finished."),
    ("assign laundry to Sam", "Sam's on laundry duty now."),
]


@pytest.mark.parametrize("ask,reply", _R5_CLAIMS)
def test_every_layer_replaces_the_status_claims(ask, reply):
    from puffpai import honesty
    text, _ = honesty.scrub_claims(
        reply, "I have not done that - this chat cannot take actions on its own.")
    out, n = chat.scrub_app_claims(text, asked=ask, names=_R5_NAMES)
    assert n >= 1 and wording.NO_APP_CHANGE in out, out
    # The claim itself is gone (a friendly opener such as "Nice work!" stays).
    first = max(chat._SENTENCE_RE.findall(reply), key=len).strip().rstrip(".!")
    assert first not in out, out
    streamed = "".join(chat.guard_app_claims(iter([reply[:6], reply[6:]]), asked=ask,
                                             names=_R5_NAMES))
    assert wording.NO_APP_CHANGE in streamed and first not in streamed, streamed


@pytest.mark.parametrize("ask,reply", [
    ("we finished Dune", "Congratulations on finishing Dune!"),
    ("we finished Dune", "Dune, what a ride!"),
    ("we finished Dune", "Dune changed my life too!"),
    ("we finished Dune", "What did you think of Dune? It is a great book."),
    ("we finished Dune", "Dune is one of the best books on any reading list."),
    ("Leo Park is coming too", "Yay, Leo Park!"),
    ("Leo Park is coming too", "Oh Leo, how lovely!"),
    ("Leo Park is coming too", "Welcome, Leo Park!"),
    ("Leo Park is coming too", "Leo wanted to come for ages, I bet."),
    ("Leo Park is coming too", "Leo Park sounds like a great guest."),
    ("Leo Park is coming too", "Big Day is going to be so much fun."),
    ("Leo Park is coming too", "Enjoy the rest of your prep for Big Day!"),
    ("Leo Park is coming too", "You can add Leo to the Guest list in the app."),
    ("Leo cancelled", "Oh no, that is a shame about Leo."),
    ("the dishes are done", "Clean dishes, happy home!"),
    ("I planted tomatoes today", "Tomatoes love full sun and steady water."),
    ("the kids did laundry", "Laundry, the chore that never ends!"),
    ("Leo is in Paris this week", "Oh nice, Leo is in Paris! Enjoy the quiet."),
])
def test_ordinary_replies_stay_when_she_has_apps(ask, reply):
    assert chat.scrub_app_claims(reply, asked=ask, names=_R5_NAMES) == (reply, 0)


def test_round3_final_an_offer_to_change_her_list_is_taken_out():
    reply = "That's wonderful news. Want me to add him to the Guest list?"
    assert chat.scrub_app_claims(reply, asked="Leo Park is coming too",
                                 names=_R5_NAMES) == ("That's wonderful news.", 1)


@pytest.mark.parametrize("ask,reply", [
    ("Leo Park is coming too", "Leo Park: attending."),
    ("Leo Park is coming too", "Guest list updated."),
    ("Leo cancelled", "Leo removed."),
    ("assign laundry to Sam", "Laundry assigned to Sam."),
])
def test_the_paired_chat_replaces_status_claims(monkeypatch, tmp_path, client, ask, reply):
    w = _world(monkeypatch, tmp_path, reply)
    got = client.post("/chat/send", headers=PAIRED, json={"message": ask}).get_json()["response"]
    assert reply not in got and "I have not changed anything in your apps" in got, got
    assert w["source"].runs == []
