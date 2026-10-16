"""The chat step that drives integrated apps (invariant 5).

Grounding, one action per message, removes ask first, arg-less multi-record
actions ask, two apps for one job ask, non-app messages cost no model call,
voice never removes. The brain is a scripted fake with the pinned
``chat(..., tools=...)`` shape.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import plain_words  # noqa: E402
from puffpai.integrate import chat  # noqa: E402
from puffpai.security import consent_tickets  # noqa: E402

DEV = "device:deviceaaaa000001"
DEV_B = "device:devicebbbb000002"
A = sup.IID


def _say(text, *, device=DEV, surface="chat"):
    return chat.try_answer(text, surface=surface, device_id=device)


TODAY = date(2026, 9, 27)          # a Sunday
SPECS = {"title": {"type": "string"}, "due": {"type": "string"},
         "cost": {"type": "number"}}


def test_grounding_drops_an_invented_date_and_number():
    specs = SPECS
    kept, dropped = chat.ground("put buy flowers on the checklist, it cost 42.80",
                                {"title": "buy flowers", "due": "2026-10-01",
                                 "cost": 42.8}, specs, today=TODAY)
    assert kept == {"title": "buy flowers", "cost": 42.8} and dropped == ["due"]
    kept, dropped = chat.ground("buy flowers by october 1", {"due": "2026-10-01",
                                                              "cost": 50}, specs, today=TODAY)
    assert kept == {"due": "2026-10-01"} and dropped == ["cost"]
    kept, dropped = chat.ground("add Leo", {"title": "Somebody Else Entirely"}, specs,
                                today=TODAY)
    assert kept == {} and dropped == ["title"]


@pytest.mark.parametrize("message,due", [
    # words that only look like months: "mark", "decide", "may" (the verb)
    ("Add a task to mark the cake in Big Day", "2031-01-01"),
    ("decide on the cake", "2026-12-01"),
    ("you may add it", "2026-05-01"),
    # a date was said, but not this one
    ("Add the cake task for tomorrow in Big Day", "2031-01-01"),
    ("buy flowers by october 1", "2031-10-01"),          # unsaid year far away
    ("buy flowers by october 1 2027", "2026-10-01"),     # said year differs
    ("buy flowers on 10/1", "2026-10-02"),
    ("call the florist on friday", "2026-10-03"),        # a Saturday
    ("call the florist tomorrow", "2026-09-28T15:00"),   # a time she did not say
    ("call the florist tomorrow at 3pm", "2026-09-28T16:00"),
    ("it costs 12 dollars", "2026-12-12"),               # numbers are not dates
])
def test_a_date_she_did_not_name_is_dropped(message, due):
    kept, dropped = chat.ground(message, {"due": due}, SPECS, today=TODAY)
    assert kept == {} and dropped == ["due"]


@pytest.mark.parametrize("message,due", [
    ("Add the cake task for tomorrow in Big Day", "2026-09-28"),
    ("do it today", "2026-09-27"),
    ("the day after tomorrow", "2026-09-29"),
    ("buy flowers by october 1", "2026-10-01"),
    ("buy flowers by Oct. 1st", "2026-10-01"),
    ("buy flowers by the 1st of october 2027", "2027-10-01"),
    ("buy flowers on 10/1", "2026-10-01"),
    ("buy flowers on 1/10", "2026-10-01"),               # day/month said the other way
    ("buy flowers on 2026-10-01", "2026-10-01"),
    ("call the florist on friday", "2026-10-02"),
    ("call the florist tomorrow at 3pm", "2026-09-28T15:00"),
    ("call the florist tomorrow at 15:30", "2026-09-28T15:30"),
    ("the 3rd of may please", "2027-05-03"),
])
def test_a_date_she_named_is_kept(message, due):
    kept, dropped = chat.ground(message, {"due": due}, SPECS, today=TODAY)
    assert kept == {"due": due} and dropped == []


@pytest.mark.parametrize("message,title,ok", [
    ("add it", "ok", False),                  # short words she did not say
    ("add ok to the list", "ok", True),
    ("add buy flowers", "buy cake", False),   # half her words is not most
    ("add cakes", "cake", True),              # a plain plural is her word
    ("call at 6", "call at 5", False),        # a number in text she did not say
    ("call at 6", "call at 6", True),
    ("add the party", "art", False),          # part of a word is not her word
    ("add something", "!!!", False),
])
def test_free_text_must_be_her_words(message, title, ok):
    kept, _dropped = chat.ground(message, {"title": title}, SPECS, today=TODAY)
    assert ("title" in kept) is ok


def test_enum_and_boolean_values_must_have_the_right_shape():
    specs = {"list": {"type": "string", "enum": ["guests", "tasks"]},
             "done": {"type": "boolean"}, "when": {"type": "string", "format": "date"}}
    kept, dropped = chat.ground("mark it done", {"list": "vendors", "done": "yes",
                                                 "when": "soon"}, specs, today=TODAY)
    assert kept == {} and sorted(dropped) == ["done", "list", "when"]
    kept, _ = chat.ground("mark it done", {"list": "tasks", "done": True}, specs,
                          today=TODAY)
    assert kept == {"list": "tasks", "done": True}


def test_an_invented_argument_never_reaches_the_app(monkeypatch, tmp_path):
    brain = sup.Brain((f"{A}__add_task", {"title": "buy flowers", "due": "2026-10-01"}))
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    got = _say("add buy flowers to the checklist")
    assert got["reply"].endswith('Say "undo" to take it back.')
    assert w["source"].runs == [(f"{A}__add_task", {"title": "buy flowers"},
                                 {"device_id": DEV, "surface": "chat"})]


@pytest.mark.parametrize("message", ["Add a task to mark the cake in Big Day",
                                     "Add the cake task for tomorrow in Big Day"])
def test_a_date_word_that_is_not_the_date_never_reaches_the_app(monkeypatch, tmp_path,
                                                               message):
    # "mark" starts like "mar", "tomorrow" is a date, but not the one the
    # model made up: only the value she said may pass.
    brain = sup.Brain((f"{A}__add_task", {"title": "cake", "due": "2091-01-01",
                                          "cost": 999}))
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    _say(message)
    assert w["source"].runs and w["source"].runs[-1][1] == {"title": "cake"}


def test_a_missing_required_argument_is_asked_for(monkeypatch, tmp_path):
    brain = sup.Brain((f"{A}__add_guest", {"guest_name": "Somebody Invented"}))
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    got = _say("add a guest to the guest list")
    assert got["reply"] == "Tell me the guest name and I will do it in Big Day."
    assert w["source"].runs == []


def test_a_remove_waits_for_a_yes_on_this_device(monkeypatch, tmp_path):
    brain = sup.Brain((f"{A}__remove_guest", {"guest_name": "Leo Park"}))
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    got = _say("take Leo Park off the guest list")
    assert got["reply"] == ('That removes Leo Park from Big Day. Remove it? '
                            '(say "yes, remove")')
    assert got["pending"]["kind"] == "remove" and "ticket" not in got["pending"]
    assert w["source"].runs == []
    # Another device cannot answer for this one.
    assert _say("yes, remove", device=DEV_B) is None or w["source"].runs == []
    assert w["source"].runs == []
    done = _say("yes, remove")
    # The redeemed yes goes on to the app runner (it refuses
    # every remove without it).
    assert w["source"].runs == [(f"{A}__remove_guest", {"guest_name": "Leo Park"},
                                 {"device_id": DEV, "surface": "chat", "yes": True})]
    assert "Big Day" in done["reply"]
    # The yes was single use.
    _say("yes, remove")
    assert len(w["source"].runs) == 1


def test_keep_it_removes_nothing(monkeypatch, tmp_path):
    brain = sup.Brain((f"{A}__remove_guest", {"guest_name": "Leo Park"}))
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    _say("take Leo Park off the guest list")
    assert _say("keep it")["reply"] == "Kept it. Nothing was removed."
    _say("yes, remove")
    assert w["source"].runs == []


def test_any_other_answer_drops_the_question(monkeypatch, tmp_path):
    brain = sup.Brain((f"{A}__remove_guest", {"guest_name": "Leo Park"}))
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    _say("take Leo Park off the guest list")
    _say("what is the capital of France")
    _say("yes, remove")
    assert w["source"].runs == []


def test_an_argless_action_that_can_touch_many_records_asks(monkeypatch, tmp_path):
    brain = sup.Brain((f"{A}__clear_done", {}))
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    got = _say("clear the done tasks")
    assert "Remove it? (say \"yes, remove\")" in got["reply"]
    assert w["source"].runs == []


def test_two_apps_for_the_same_job_ask_which(monkeypatch, tmp_path):
    acts = (sup.guest_actions() + sup.guest_actions(iid=sup.IID_B, app="Party List"))
    w = sup.setup_chat(monkeypatch, tmp_path, acts=acts)
    got = _say("read the guest list")
    assert got["reply"] == "Should I use Big Day or Party List?"
    assert w["brain"].calls == [] and w["source"].runs == []


def test_naming_the_app_picks_it(monkeypatch, tmp_path):
    acts = (sup.guest_actions() + sup.guest_actions(iid=sup.IID_B, app="Party List"))
    brain = sup.Brain((f"{sup.IID_B}__add_guest", {"guest_name": "Leo Park"}))
    w = sup.setup_chat(monkeypatch, tmp_path, acts=acts, brain=brain)
    got = _say("read the guest list in Party List")
    assert "(read from Party List)" in got["reply"]
    assert brain.calls == []                  # a question: the read, by rule
    assert w["source"].runs[0][0] == f"{sup.IID_B}__read_guests"
    _say("add Leo Park to the guest list in Party List")
    offered = {t["function"]["name"] for t in brain.calls[0]["tools"]}
    assert offered and all(n.startswith(sup.IID_B) for n in offered)
    assert w["source"].runs[1][0] == f"{sup.IID_B}__add_guest"


@pytest.mark.parametrize("text", ["what is the capital of France",
                                  "tell me a joke", "how are you today?"])
def test_non_app_messages_cost_no_model_call(monkeypatch, tmp_path, text):
    w = sup.setup_chat(monkeypatch, tmp_path)
    assert _say(text) is None
    assert w["brain"].calls == [] and w["source"].runs == []


def test_no_pick_is_ordinary_chat(monkeypatch, tmp_path):
    w = sup.setup_chat(monkeypatch, tmp_path, brain=sup.Brain(None))
    # A change ask that no action fits gets a plain reason and
    # a next step, never the model (which could only claim or promise it).
    got = _say("add a guest to the guest list")["reply"]
    assert got.startswith("I have not changed anything in your apps: I could not tell which "
                          "thing Big Day does that is. It can add a guest to the guest list")
    assert len(w["brain"].calls) == 1 and w["source"].runs == []
    # Not a change ask: still ordinary chat.
    assert _say("tell me a joke") is None


def test_a_tool_name_that_was_not_offered_runs_nothing(monkeypatch, tmp_path):
    w = sup.setup_chat(monkeypatch, tmp_path,
                       brain=sup.Brain(("some-other-abcdefgh__wipe", {})))
    assert _say("add Leo Park to the guest list")["reply"].startswith(
        "I have not changed anything in your apps")
    assert w["source"].runs == []


def test_voice_refuses_removes(monkeypatch, tmp_path):
    brain = sup.Brain((f"{A}__remove_guest", {"guest_name": "Leo Park"}))
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    got = _say("take Leo Park off the guest list", surface="voice")
    assert got["reply"] == "Open your dashboard to confirm removing that."
    assert w["source"].runs == [] and chat._pending == {}  # noqa: SLF001


def test_voice_reads_and_undoable_changes(monkeypatch, tmp_path):
    brain = sup.Brain((f"{A}__add_guest", {"guest_name": "Leo Park"}))
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    assert "guests in Big Day" in _say("read the guest list", surface="voice")["reply"]
    assert _say("add Leo Park to the guest list", surface="voice")["reply"]
    assert [r[0] for r in w["source"].runs] == [f"{A}__read_guests", f"{A}__add_guest"]


@pytest.mark.parametrize("device", [None, "", "peer:127.0.0.1", "anon:x", "device:no"])
def test_no_identity_no_step(monkeypatch, tmp_path, device):
    w = sup.setup_chat(monkeypatch, tmp_path, brain=sup.Brain((f"{A}__read_guests", {})))
    assert chat.try_answer("read the guest list", surface="chat", device_id=device) is None
    assert w["brain"].calls == [] and w["source"].runs == []


def test_the_terminal_works(monkeypatch, tmp_path):
    w = sup.setup_chat(monkeypatch, tmp_path, brain=sup.Brain((f"{A}__read_guests", {})))
    got = chat.try_answer("read the guest list", surface="terminal",
                          device_id=consent_tickets.terminal_device_id())
    assert "Zanzibar Quill" in got["reply"] and len(w["source"].runs) == 1


def test_a_read_answers_from_the_rows_with_every_value_neutralised(monkeypatch, tmp_path):
    rows = [{"name": "Zanzibar Quill"}, {"name": sup.INJECTION},
            {"name": "see https://evil.example/x"}]
    sup.setup_chat(monkeypatch, tmp_path, rows=rows,
                   brain=sup.Brain((f"{A}__read_guests", {})))
    reply = _say("read the guest list")["reply"]
    assert reply.startswith("3 guests in Big Day: Zanzibar Quill")
    assert "ignore previous instructions" not in reply.lower()
    assert "(a line that read like an instruction was left out)" in reply
    assert "https://" not in reply and reply.endswith("(read from Big Day)")


def test_a_read_of_nothing(monkeypatch, tmp_path):
    sup.setup_chat(monkeypatch, tmp_path, rows=[],
                   brain=sup.Brain((f"{A}__read_guests", {})))
    assert _say("read the guest list")["reply"] == "Big Day has nothing there yet."


def test_without_a_tool_brain_only_an_exact_read_runs(monkeypatch, tmp_path):
    w = sup.setup_chat(monkeypatch, tmp_path)
    monkeypatch.setattr(chat, "_backend", lambda: None)
    assert "Zanzibar Quill" in _say("read the guest list")["reply"]
    assert _say("add Leo Park to the guest list")["reply"].startswith(
        "I have not changed anything in your apps")
    assert [r[0] for r in w["source"].runs] == [f"{A}__read_guests"]


def test_undo_goes_to_this_devices_last_change(monkeypatch, tmp_path):
    brain = sup.Brain((f"{A}__add_guest", {"guest_name": "Leo Park"}))
    sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    _say("add Leo Park to the guest list")
    # The app host's undo is not in this build: the honest line, not "Undone".
    # (The app host's mirror may be present, so hide it for this check.)
    monkeypatch.setitem(sys.modules, "puffpai.webapps.mirror", None)
    assert _say("undo")["reply"] == "This is not in this build yet. Nothing was changed."
    assert _say("undo", device=DEV_B)["reply"] == ("There is nothing of yours to undo "
                                                   "from the last 10 minutes.")


def test_every_reply_is_plain_words(monkeypatch, tmp_path):
    brain = sup.Brain((f"{A}__remove_guest", {"guest_name": "Leo Park"}),
                      (f"{A}__add_guest", {"guest_name": "Leo Park"}))
    sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    for text in ("read the guest list", "take Leo Park off the guest list",
                 "keep it", "add Leo Park to the guest list"):
        reply = _say(text)["reply"]
        assert plain_words.check(reply) == [], reply
        assert "—" not in reply


# -- an action names its integration by the record id or the
# install id of the app it drives; both are one integration ------------------

APP_ID = "big-day-cdefghij"      # the static app's own install id


def _one_record(monkeypatch, status="ready"):
    from puffpai.integrate import records
    rec = records.blank(A, name="Big Day", need="j5")
    rec["status"], rec["app_id"] = status, APP_ID
    monkeypatch.setattr(records, "all", lambda: [dict(rec)])


@pytest.mark.parametrize("iid", [A, APP_ID], ids=["record_id", "app_install_id"])
def test_an_action_may_name_its_integration_by_either_id(monkeypatch, tmp_path, iid):
    brain = sup.Brain((f"{iid}__add_guest", {"guest_name": "Leo Park"}))
    w = sup.setup_chat(monkeypatch, tmp_path, acts=sup.guest_actions(iid=iid), brain=brain)
    _one_record(monkeypatch)
    got = _say("read the guest list in Big Day")
    assert got["reply"].endswith("(read from Big Day)")
    assert w["source"].runs[0][0] == f"{iid}__read_guests"
    _say("add Leo Park to the guest list in Big Day")
    offered = {t["function"]["name"] for t in brain.calls[0]["tools"]}
    assert offered and all(n.startswith(iid + "__") for n in offered)
    assert w["source"].runs[1][0] == f"{iid}__add_guest"


def test_one_app_named_by_both_ids_is_one_app_not_two(monkeypatch, tmp_path):
    acts = [sup.guest_actions(iid=A)[0], sup.guest_actions(iid=APP_ID)[1]]
    brain = sup.Brain((f"{APP_ID}__add_guest", {"guest_name": "Leo Park"}))
    w = sup.setup_chat(monkeypatch, tmp_path, acts=acts, brain=brain)
    _one_record(monkeypatch)
    got = _say("read the guest list")
    assert "Should I use" not in got["reply"]
    assert w["source"].runs[0][0] == f"{A}__read_guests"
    got = _say("add Leo Park to the guest list")
    assert "Should I use" not in got["reply"]
    offered = {t["function"]["name"] for t in brain.calls[0]["tools"]}
    assert offered == {f"{A}__read_guests", f"{APP_ID}__add_guest"}
    assert w["source"].runs[1][0] == f"{APP_ID}__add_guest"


@pytest.mark.parametrize("status", ["held", "refused", "failed", "removed", "checking"])
@pytest.mark.parametrize("iid", [A, APP_ID], ids=["record_id", "app_install_id"])
def test_actions_of_an_integration_that_is_not_ready_are_never_offered(
        monkeypatch, tmp_path, status, iid):
    brain = sup.Brain((f"{iid}__read_guests", {}))
    w = sup.setup_chat(monkeypatch, tmp_path, acts=sup.guest_actions(iid=iid), brain=brain)
    _one_record(monkeypatch, status=status)
    assert _say("read the guest list in Big Day") is None
    assert brain.calls == [] and w["source"].runs == []


# -- directive F: questions go to reads, rules first, on any app --------------------

def _three_apps():
    """Three unrelated apps, each with a few reads and an add (compiled shape)."""
    wed, chess, garden = "big-day-abcdefgh", "chess-log-bcdefghi", "veg-patch-cdefghij"
    acts = []
    for iid, app, nouns in ((wed, "Big Day", ("guest", "task", "budget")),
                            (chess, "Chess Log", ("game", "opening")),
                            (garden, "Veg Patch", ("planting", "harvest"))):
        for noun in nouns:
            acts.append(sup.action(f"read_{noun}", f"Read the {noun} list", "reads",
                                   iid=iid, app=app))
        acts.append(sup.action(f"add_{nouns[0]}", f"Add a {nouns[0]}", "changes",
                               iid=iid, app=app,
                               props={"name": {"type": "string", "maxLength": 80}},
                               required=["name"]))
    return acts, (wed, chess, garden)


@pytest.mark.parametrize("question,want", [
    ("who is coming so far?", "big-day-abcdefgh__read_guest"),
    ("how many tasks are left?", "big-day-abcdefgh__read_task"),
    ("what games have I logged?", "chess-log-bcdefghi__read_game"),
    ("which openings am I learning?", "chess-log-bcdefghi__read_opening"),
    ("show me the harvest so far", "veg-patch-cdefghij__read_harvest"),
    ("what's in my plantings?", "veg-patch-cdefghij__read_planting"),
])
def test_a_question_picks_its_read_by_rule_on_three_apps(monkeypatch, tmp_path,
                                                         question, want):
    acts, _ids = _three_apps()
    w = sup.setup_chat(monkeypatch, tmp_path, acts=acts)
    got = _say(question)
    assert got is not None and got["reply"].endswith(")")
    assert [r[0] for r in w["source"].runs] == [want]
    assert w["brain"].calls == []                      # no model was asked


def test_a_question_the_rules_cannot_place_asks_the_model_with_reads_only(
        monkeypatch, tmp_path):
    acts, (wed, _c, _g) = _three_apps()
    brain = sup.Brain((f"{wed}__read_budget", {}))
    w = sup.setup_chat(monkeypatch, tmp_path, acts=acts, brain=brain)
    got = _say("how much have we spent so far?")
    assert "(read from Big Day)" in got["reply"]
    tools = {t["function"]["name"] for t in brain.calls[0]["tools"]}
    assert tools == {a["id"] for a in acts if a["effect"] == "reads"}
    assert [r[0] for r in w["source"].runs] == [f"{wed}__read_budget"]


def test_a_model_that_picks_a_change_for_a_question_runs_nothing(monkeypatch, tmp_path):
    acts, (wed, _c, _g) = _three_apps()
    brain = sup.Brain((f"{wed}__add_guest", {"name": "Leo"}))
    w = sup.setup_chat(monkeypatch, tmp_path, acts=acts, brain=brain)
    assert _say("how much have we spent so far?") is None
    assert w["source"].runs == []


@pytest.mark.parametrize("text", ["what is the capital of France?", "tell me a joke",
                                  "who wrote Hamlet?", "how are you today?"])
def test_a_question_about_the_world_is_not_ours(monkeypatch, tmp_path, text):
    acts, _ids = _three_apps()
    w = sup.setup_chat(monkeypatch, tmp_path, acts=acts)
    assert _say(text) is None
    assert w["brain"].calls == [] and w["source"].runs == []


def test_the_same_list_in_two_apps_asks_which(monkeypatch, tmp_path):
    acts = sup.guest_actions() + sup.guest_actions(iid=sup.IID_B, app="Party List")
    w = sup.setup_chat(monkeypatch, tmp_path, acts=acts)
    assert _say("who is on the guest list so far?")["reply"] == \
        "Should I use Big Day or Party List?"
    assert w["source"].runs == [] and w["brain"].calls == []


def test_a_pending_remove_never_runs_once_the_app_is_held(monkeypatch, tmp_path):
    """Validator fix: 'yes, remove' after the integration stopped being ready."""
    from puffpai.integrate import records
    brain = sup.Brain((f"{A}__remove_guest", {"guest_name": "Zanzibar Quill"}))
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    assert "Remove it?" in _say("take Zanzibar Quill off the guest list in Big Day")["reply"]
    rec = records.get(A)
    rec["status"] = "held"
    records.save(rec)
    got = _say("yes, remove")
    assert w["source"].runs == []
    assert "not ready" in got["reply"]


@pytest.mark.parametrize("question", ["who have I played so far?", "when did I sow the basil?",
                                      "what's in my garden?"])
def test_questions_the_rules_leave_to_the_model(monkeypatch, tmp_path, question):
    """Measured on qwen2.5:7b: a "who" question
    with no attendance word, and a question about her own things that names no
    list, go to the model with the reads only; the rule never guesses."""
    acts, (_w, chess, _g) = _three_apps()
    brain = sup.Brain((f"{chess}__read_game", {}))
    w = sup.setup_chat(monkeypatch, tmp_path, acts=acts, brain=brain)
    got = _say(question)
    assert len(brain.calls) == 1
    assert {t["function"]["name"] for t in brain.calls[0]["tools"]} == {
        a["id"] for a in acts if a["effect"] == "reads"}
    assert got and [r[0] for r in w["source"].runs] == [f"{chess}__read_game"]


def test_who_with_an_attendance_word_is_a_people_list(monkeypatch, tmp_path):
    acts, (wed, _c, _g) = _three_apps()
    w = sup.setup_chat(monkeypatch, tmp_path, acts=acts)
    for q in ("who is coming so far?", "who's coming?", "who has said yes?",
              "who is invited?"):
        w["source"].runs.clear()
        assert _say(q) is not None, q
        assert [r[0] for r in w["source"].runs] == [f"{wed}__read_guest"], q
    assert w["brain"].calls == []
