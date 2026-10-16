"""Chat honesty on a held-out set.

1. False app-action claims on all 12 chat paths. Fixed
   STRUCTURALLY (integrate/grounding.py), not with more claim words:
   * a reply that only says yes or no to her question ("He was.", "Sí.",
     "Yep.") asserts the question, so it is the change log's answer instead,
     unless her question is plainly about the outside world;
   * a sentence in a language or script she did not write in is a sentence
     the guard cannot read: it fails closed (unless she asked for another
     language); a foreign sentence naming her things (Cyrillic and Greek
     read as Latin letters too) fails closed;
   * "was Leo Park removed?" is answered from the change log, never a read.
2. Reads: a first name that matches one row is that row ("is Leo on the
   list?"); every choice of the app's own schema answers ("who isn't
   coming?", "who hasn't answered yet?"), "1 guest ... is Invited".
3. Removals and edits carry the resolved row ("Take Leo off" removes Leo
   Park); a name that is no row is said plainly; her own wording of an edit
   reaches the edit with the row.
4. PIN: every word the vault's PIN rule accepts is caught (the vault's own
   check clears an ordinary word; no vault, or a caller with no key: caught).
5. Keyless callers never reach the vault from chat.
6. Order checks by meaning (consent/skip, picking, trust).
7. Build failures say which step failed; one Mood board line while building;
   no moving end in the progress count; a natural greeting.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402
import test_chat_false_claims_all_paths as T  # noqa: E402

from puffpai import api_server, artifact_review, bot_identity  # noqa: E402
from puffpai.integrate import actions, chat, grounding, wording  # noqa: E402
from puffpai.webapps import actions as web_actions  # noqa: E402

HONEST = T.HONEST
DEV = "device:deviceaaaa000001"
A = sup.IID


# -- 1. the structural claim checks ---------------------------------------------------

NAMES = ["Big Day", "Guest list", "Garden Log", "Chore Chart", "Book Shelf", "Shopping list"]
ROWS = ["Leo Park", "Dune", "dishes", "tomatoes", "Nana", "bread", "Priya"]

#: Held-out replies: every one of these was missed.
VR1_MISSED = [
    ("what's the weather?", "맑아요. 빵을 목록에 추가했어요."),
    ("what's the weather?", "مشمس. لقد أضفت الخبز إلى القائمة."),
    ("tell me a joke", "A pun for you. Я удалил Лео из списка гостей."),
    ("is bread on the list now?", "Yes, it is."),
    ("Leo's off the list, right?", "Right."),
    ("you got that done?", "Yep."),
    ("was Leo removed?", "He was."),
    ("has Nana been added yet?", "She has."),
    ("did the change stick?", "Yes."),
    ("¿agregaste el pan a la lista?", "Sí."),
    ("パンを追加した?", "はい。"),
    ("bread's on the shopping list?", "Yup, right there."),
]
#: Controls that were over-blocked, and more ordinary replies.
KEPT = [
    ("Wie ist das Wetter?", "Es ist sonnig und warm."),
    ("is bread on the list?", "I can check if you open the list app."),
    ("what's the capital of France?", "Paris."),
    ("tell me a joke", "Why don't eggs tell jokes? They'd crack each other up."),
    ("the kids cleaned their rooms", "Proud parent moment!"),
    ("is Mount Everest in Nepal?", "Yes."),
    ("¿Qué hora es en Madrid?", "Son las nueve de la noche."),
    ("how do I say good morning in Korean?", "안녕하세요 (annyeonghaseyo)."),
    ("can you tell me a joke?", "Sure! Why did the scarecrow win? He was outstanding."),
]


@pytest.mark.parametrize("before", [None, wording.NO_CHANGE_UNKNOWN],
                         ids=["no history", "after the honest line"])
@pytest.mark.parametrize("names,rows", [(NAMES, ROWS), ([], [])], ids=["hers", "keyless"])
@pytest.mark.parametrize("ask,reply", VR1_MISSED)
def test_held_out_claims_are_replaced(ask, reply, names, rows, before):
    out, n = chat.scrub_app_claims(reply, asked=ask, names=names, rows=rows, before=before)
    assert n and "not changed" in out and out.strip() != reply.strip(), out
    streamed = "".join(chat.guard_app_claims(iter([reply[:7], reply[7:]]), asked=ask,
                                             names=names, rows=rows, before=before))
    assert "not changed" in streamed and streamed.strip() != reply.strip(), streamed


@pytest.mark.parametrize("names,rows", [(NAMES, ROWS), ([], [])], ids=["hers", "keyless"])
@pytest.mark.parametrize("ask,reply", KEPT)
def test_ordinary_replies_are_kept(ask, reply, names, rows):
    assert chat.scrub_app_claims(reply, asked=ask, names=names, rows=rows) == (reply, 0)
    streamed = "".join(chat.guard_app_claims(iter([reply[:7], reply[7:]]), asked=ask,
                                             names=names, rows=rows))
    assert streamed == reply


def test_a_bare_yes_is_answered_from_the_change_log():
    """The replacement is what the log says, so it is true after a real
    change too (never "I have not changed anything" when something ran)."""
    said = "Here is what I changed in your apps in the last 5 minutes: Removed Leo Park."
    out, n = chat.scrub_app_claims("He was.", asked="was Leo removed?", status=said)
    assert (out, n) == (said, 1)
    streamed = "".join(chat.guard_app_claims(iter(["He ", "was."]), asked="was Leo removed?",
                                             status=said))
    assert streamed == said


@pytest.mark.parametrize("sentence,asked,elliptical", [
    ("He was.", "was Leo removed?", True), ("Sí, ya está.", "¿ya está?", True),
    ("はい、追加しました。", "追加した?", True), ("All sorted.", "all sorted?", True),
    ("Paris.", "what's the capital of France?", False),
    ("They did, 112 to 104.", "did the Lakers win?", False),
    ("Probably not, the skies look clear.", "is it going to rain?", False),
])
def test_what_counts_as_saying_nothing_of_its_own(sentence, asked, elliptical):
    assert grounding.elliptical(sentence, asked) is elliptical


@pytest.mark.parametrize("text,english", [
    ("Proud parent moment!", True), ("Crème brûlée is a classic dessert.", True),
    ("Try Project Hail Mary by Andy Weir.", True), ("Café au lait is great.", True),
    ("Ik heb brood aan de lijst toegevoegd.", False), ("Es ist sonnig und warm.", False),
    ("Ich habe Max für den Abwasch eingetragen.", False),
    ("Ya borré a Leo de la lista de invitados.", False), ("Я удалил Лео Парка.", False),
])
def test_the_guard_knows_what_it_can_read(text, english):
    assert grounding.english(text) is english


def test_her_names_are_found_in_other_alphabets():
    assert grounding.names_in("Я удалил Лео Парка из списка гостей.", rows=["Leo Park"])
    assert grounding.names_in("Αφαίρεσα τον Λεο", rows=["Leo Park"])
    assert not grounding.names_in("Я удалил его.", rows=["Leo Park"])
    assert not grounding.names_in("We went to the big park", names=["Big Day"])


@pytest.mark.parametrize("ask,change", [
    ("was Leo Park removed from the guest list?", True), ("has Nana been added yet?", True),
    ("did Leo get taken off?", True), ("what games have I logged?", False),
    ("who have I played so far?", False), ("is Leo on the list?", False),
])
def test_a_question_about_a_change(ask, change):
    assert grounding.change_question(ask) is change


#: End-to-end false claims: 72 of 72 got through.
E2E_LIES = [
    ("tell me a joke", "A pun for you. Я удалил Лео Парка из списка гостей."),
    ("what's the weather?", "맑아요. 레오 파크를 하객 명단에서 삭제했어요."),
    ("was Leo Park removed from the guest list?", "He was."),
    ("did the change stick?", "Yes."),
    ("¿quitaste a Leo Park de la lista?", "Sí."),
    ("you got that done?", "Yep."),
]


@pytest.mark.parametrize("path", list(T.PATHS))
@pytest.mark.parametrize("ask,reply", E2E_LIES)
def test_held_out_claims_never_reach_her_on_any_path(monkeypatch, tmp_path, path, ask, reply):
    w = T._world(monkeypatch, tmp_path, reply)
    # The weather service answers "what's the weather?" on owner paths; out
    # of the way, so the model's reply is what is checked.
    monkeypatch.setattr(api_server, "_maybe_weather_answer", lambda *a, **k: None)
    got = T.PATHS[path](api_server.app.test_client(), ask)
    assert w["source"].runs == []
    assert HONEST in got, (path, got)


# -- 2. reads: first names, every choice -----------------------------------------------

_SCHEMA = {
    "guest_name": {"type": "string", "maxLength": 120},
    "rsvp_status": {"type": "string", "enum": ["Invited", "Attending", "Declined",
                                               "Awaiting Response"]},
    "meal_choice": {"type": "string", "enum": ["Chicken", "Fish", "Vegan"]},
    "side": {"type": "string", "enum": ["Bride", "Groom", "Friend"]},
}
_CHECK = {"storage_key": "app.v1", "list": "guests", "match": {"name": "guest_name"},
          "expect": {"rsvp": "rsvp_status", "meal": "meal_choice", "side": "side"}}
_GUESTS = [{"name": "Leo Park", "rsvp": "Attending", "meal": "Fish", "side": "Friend"},
           {"name": "Maya Lee", "rsvp": "Declined", "meal": "", "side": "Friend"}]


def _schema_world(monkeypatch, tmp_path, rows=_GUESTS, pick=None):
    acts = [sup.action("read_guest_list", "Read the guest list", "reads"),
            sup.action("edit_guest", "Edit a guest", "changes", props=_SCHEMA,
                       required=["guest_name"]),
            sup.action("remove_guest", "Remove a guest", "removes",
                       props={"guest_name": _SCHEMA["guest_name"]}, required=["guest_name"],
                       undoable=False)]
    brain = sup.Brain(None if pick is None else (f"{A}__{pick[0]}", pick[1]))
    w = sup.setup_chat(monkeypatch, tmp_path, acts=acts, rows=list(rows), brain=brain)
    names = [r["name"] for r in rows]
    monkeypatch.setattr(chat, "_recipe_of", lambda a: (
        None if a["effect"] == "reads" else {"check": dict(_CHECK)}))
    monkeypatch.setattr(chat, "_field_choices",
                        lambda a, k: ((a.get("args") or {}).get("properties", {}).get(k)
                                      or {}).get("enum"))
    monkeypatch.setattr(chat, "_row_keys", lambda a: list(names) if a["effect"] != "reads"
                        else [])
    return w


def _say(text):
    return chat.try_answer(text, surface="chat", device_id=DEV)


def test_who_is_coming_reads_the_schema_with_two_guests(monkeypatch, tmp_path):
    """Wedding acceptance 40/41: Maya Lee Declined, yet "2 guests ... Leo
    Park, Maya Lee" (two rows could not show the choices repeating)."""
    _schema_world(monkeypatch, tmp_path)
    assert _say("Who is coming?")["reply"] == (
        "1 guest in Big Day is Attending: Leo Park (read from Big Day)")
    assert _say("who isn't coming?")["reply"] == (
        "1 guest in Big Day is not Attending. Declined: Maya Lee (read from Big Day)")


@pytest.mark.parametrize("ask,want,never", [
    ("who isn't coming?", ["Declined: Maya", "Awaiting Response: Ann", "Invited: Noah"],
     ["Sahib", "Leo"]),
    ("who hasn't answered yet?", ["Awaiting Response: Ann", "Invited: Noah"],
     ["Maya", "Sahib", "Leo"]),
    ("who is invited?", ["1 guest in Big Day is Invited: Noah"], ["are Invited"]),
])
def test_every_choice_answers(ask, want, never):
    rows = [{"name": "Sahib", "rsvp": "Attending"}, {"name": "Leo", "rsvp": "Attending"},
            {"name": "Maya", "rsvp": "Declined"}, {"name": "Ann", "rsvp": "Awaiting Response"},
            {"name": "Noah", "rsvp": "Invited"}]
    act = {"app_name": "Big Day", "says": "Read the guest list", "integration": A}
    got = chat.read_answer(act, rows, ask)
    for w in want:
        assert w in got, got
    for n in never:
        assert n not in got, got
    assert "It does not keep that in those words" not in got


@pytest.mark.parametrize("ask,want", [
    ("is Leo on the list?", "Yes, Leo Park is in Big Day"),
    ("Is Leo coming?", "Yes, Leo Park is Attending in Big Day"),
    ("is leo on the list?", "Yes, Leo Park is in Big Day"),
    ("Is Maya off the list?", "No, Maya Lee is in Big Day"),
])
def test_a_first_name_reads_its_one_row(monkeypatch, tmp_path, ask, want):
    """Wedding acceptance 25: only the full name worked."""
    w = _schema_world(monkeypatch, tmp_path)
    got = _say(ask)
    assert got is not None and got["reply"].startswith(want), got
    assert [r[0] for r in w["source"].runs] == [f"{A}__read_guest_list"]
    assert w["brain"].calls == []                 # no model pick for it


def test_a_read_question_naming_her_row_is_not_the_change_log(monkeypatch):
    rows = ["Leo Park", "Maya Lee", "Attending"]
    assert chat.row_read_question("Is Leo on the list?", rows)
    assert not chat.row_read_question("Is Leo off the list now?", rows)
    assert not chat.row_read_question("was Leo removed?", rows)
    assert not chat.row_read_question("Is Leo on the list?", [])      # keyless: no rows


def test_only_name_shaped_rows_answer_to_a_first_name():
    rows = ["Leo Park", "Set the date & overall budget", "Will Smith", "Leo"]
    assert chat.first_name_rows("Set Leo's rsvp", rows) == ["Leo Park"]
    assert chat.first_name_rows("Will you check?", rows) == []
    assert chat._named_row("is Leo coming?", ["Leo Park", "Leo Chen"]) is None


# -- 3. removals and edits carry the row ---------------------------------------------

def test_take_leo_off_removes_leo_park(monkeypatch, tmp_path):
    """Wedding acceptance 29-31: confirmed "Leo", then "Nothing in the app
    matches that"."""
    w = _schema_world(monkeypatch, tmp_path, pick=("remove_guest", {"guest_name": "Leo"}))
    got = _say("Take Leo off the guest list.")
    assert got["reply"] == wording.CONFIRM_REMOVE.format(what="Leo Park", app="Big Day")
    assert got["pending"]["kind"] == "remove"
    done = _say("yes, remove")
    assert w["source"].runs and w["source"].runs[-1][:2] == (
        f"{A}__remove_guest", {"guest_name": "Leo Park"}), done


def test_a_name_that_is_no_row_is_said_plainly(monkeypatch, tmp_path):
    """"Tell me the guest name" after she named one."""
    w = _schema_world(monkeypatch, tmp_path, pick=("edit_guest", {"guest_name": "Leo Parker",
                                                                  "rsvp_status": "attending"}))
    got = _say("Set Leo Parker's rsvp status to attending.")["reply"]
    assert got == wording.NO_SUCH_ROW.format(thing="guest", app="Big Day", name="Leo Parker")
    assert w["source"].runs == []


def test_two_rows_with_that_first_name_ask_which(monkeypatch, tmp_path):
    rows = _GUESTS + [{"name": "Leo Chen", "rsvp": "Invited"}]
    w = _schema_world(monkeypatch, tmp_path, rows=rows,
                      pick=("remove_guest", {"guest_name": "Leo"}))
    got = _say("Take Leo off the guest list.")["reply"]
    assert "Leo Park" in got and "Leo Chen" in got and "Say the whole name" in got
    assert w["source"].runs == [] and not chat._has_pending(DEV)


def test_her_own_edit_wording_reaches_the_edit_with_the_row(monkeypatch, tmp_path):
    """A wedding run: "Actually Leo wants the fish instead, and he's
    on the groom's side." went to the model. It now reaches Edit a guest for
    Leo Park; the values are the choice checks' to read (what it can set runs,
    what it cannot is named, never "Done" for nothing)."""
    w = _schema_world(monkeypatch, tmp_path)
    got = _say("Actually Leo wants the fish instead, and he's on the groom's side.")
    for aid, args, _ctx in w["source"].runs:
        assert aid == f"{A}__edit_guest" and args["guest_name"] == "Leo Park"
    if not w["source"].runs:
        # The choice checks set no value from "the fish instead" yet:
        # the edit says which of HER fields it could not set, for Leo Park.
        assert got["reply"] == wording.EDIT_NOTHING.format(
            app="Big Day", what="Leo Park", fields="meal choice and side")
    assert w["brain"].calls == []                 # never the model


# -- 4. a PIN in any shape the vault accepts --------------------------------------------

@pytest.mark.parametrize("pin", ["marmalade-otter", "Tq7-ravenQ9", "4829-1735"])
@pytest.mark.parametrize("path", ["/chat/send", "/chat/stream"])
def test_a_plain_word_pin_is_not_echoed_sent_or_kept(monkeypatch, tmp_path, path, pin):
    """"marmalade-otter" and "PURPLEOTTER" were echoed,
    sent to the model and kept."""
    import json
    w = T._world(monkeypatch, tmp_path, f"You typed {pin}.")
    monkeypatch.setattr(api_server, "_get_chat_history_dir", lambda: str(tmp_path))
    monkeypatch.setattr(api_server, "_PIN_SCREENED", {})
    conv = "r2" + path.replace("/", "")
    body = api_server.app.test_client().post(path, headers=T.PAIRED, json={
        "message": pin, "conversation_id": conv}).get_data(as_text=True)
    kept = json.dumps(next(c for c in api_server._chat_history if c["id"] == conv))
    assert pin not in body and not w["model_calls"] and pin not in kept


@pytest.mark.parametrize("pin", ["PURPLEOTTER", "sunflowerfield"])
@pytest.mark.parametrize("path", ["/chat/send", "/chat/stream"])
def test_an_older_vaults_pin_is_not_echoed_sent_or_kept(monkeypatch, tmp_path, path, pin):
    """The PIN rule refuses these two (a common
    word with letters changed), so with no vault they are ordinary words. A
    vault made before that may still hold one: then it is caught like any PIN."""
    import json
    from puffpai import setup_ops
    from puffpai.security.vault import Vault
    assert not setup_ops.validate_vault_pin(pin)[0]
    v = Vault(vault_dir=str(tmp_path / "vault"))
    v.create(pin)                  # the vault's own floor took it, as before
    v.lock()
    monkeypatch.setattr(api_server, "_vault_instance", v)
    w = T._world(monkeypatch, tmp_path, f"You typed {pin}.")
    monkeypatch.setattr(api_server, "_get_chat_history_dir", lambda: str(tmp_path))
    monkeypatch.setattr(api_server, "_PIN_SCREENED", {})
    conv = "m3" + path.replace("/", "")
    body = api_server.app.test_client().post(path, headers=T.PAIRED, json={
        "message": pin, "conversation_id": conv}).get_data(as_text=True)
    kept = json.dumps(next(c for c in api_server._chat_history if c["id"] == conv))
    assert pin not in body and not w["model_calls"] and pin not in kept
    assert not v.is_unlocked and v._attempts == {}


def test_the_vault_clears_an_ordinary_word_and_catches_its_own_pin(monkeypatch, tmp_path):
    from puffpai.security.vault import Vault
    v = Vault(vault_dir=str(tmp_path / "vault"))
    v.create("marmalade-otter")
    v.lock()
    monkeypatch.setattr(api_server, "_vault_instance", v)
    monkeypatch.setattr(api_server, "_PIN_SCREENED", {})
    assert api_server._pin_shaped("marmalade-otter") is True
    assert api_server._pin_shaped("Absolutely!") is False
    assert not v.is_unlocked
    assert v._attempts == {}                       # never a failed unlock


def test_screening_has_its_own_limit_and_then_fails_closed(monkeypatch, tmp_path):
    from puffpai.security.vault import Vault
    v = Vault(vault_dir=str(tmp_path / "vault"))
    v.create("marmalade-otter")
    v.lock()
    monkeypatch.setattr(v, "SCREEN_LIMIT", 2)
    monkeypatch.setattr(api_server, "_vault_instance", v)
    monkeypatch.setattr(api_server, "_PIN_SCREENED", {})
    assert api_server._pin_shaped("Wonderful") is False
    assert api_server._pin_shaped("Goodnight") is False
    assert v.screen_pin("Delicious") is None                 # over the limit
    assert api_server._pin_shaped("Delicious") is True       # so: treated as a PIN
    assert v._attempts == {}


def test_no_vault_or_no_key_means_a_vault_word_is_a_pin(monkeypatch):
    monkeypatch.setattr(api_server, "_get_vault", lambda: None)
    monkeypatch.setattr(api_server, "_PIN_SCREENED", {})
    assert api_server._pin_shaped("marmalade-otter") is True
    assert api_server._pin_shaped("whatever") is False       # the PIN rule refuses it

    screened = []

    class _Vault:
        is_initialized = True

        @staticmethod
        def screen_pin(text):
            screened.append(text)
            return False
    monkeypatch.setattr(api_server, "_get_vault", lambda: _Vault())
    with api_server.app.test_request_context("/v1/chat/completions", method="POST"):
        from flask import g
        g.puffpai_has_key = False
        assert api_server._pin_shaped("Absolutely!") is True  # keyless: never asks the vault
    assert screened == []


# -- 5. a caller with no key never reaches the vault ------------------------------------

class _FakeVault:
    is_initialized = True
    is_legacy = False

    @staticmethod
    def screen_pin(_t):
        return False


def _vault_world(monkeypatch, tmp_path):
    T._world(monkeypatch, tmp_path, "Fine.")
    tried: list = []
    monkeypatch.setattr(api_server, "_get_vault", lambda: _FakeVault())
    monkeypatch.setattr(api_server, "_is_protected_query", lambda m: "medical" in str(m))
    monkeypatch.setattr(api_server, "_vault_may_hold_documents", lambda v: True)
    monkeypatch.setattr(api_server, "_VAULT_CHAT_STATE", {})
    monkeypatch.setattr(api_server, "_try_unlock_and_answer",
                        lambda p, q, cid: tried.append(cid) or "Vault unlocked (test).")
    return tried


_KEYLESS_VAULT = {
    "keyless /v1/chat/completions": lambda c, q: c.post("/v1/chat/completions", json={
        "model": "x", "messages": [{"role": "user", "content": q}]}),
    "keyless /v1/chat/completions stream": lambda c, q: c.post("/v1/chat/completions", json={
        "model": "puffpai", "stream": True, "messages": [{"role": "user", "content": q}]}),
    "keyless /api/chat": lambda c, q: c.post("/api/chat", json={
        "messages": [{"role": "user", "content": q}]}),
    "keyless /api/chat stream": lambda c, q: c.post("/api/chat", json={
        "stream": True, "messages": [{"role": "user", "content": q}]}),
    "home assistant without the voice key": lambda c, q: c.post(
        "/v1/chat/completions", headers={"X-Client": "home-assistant"},
        json={"model": "x", "messages": [{"role": "user", "content": q}]}),
}


@pytest.mark.parametrize("path", list(_KEYLESS_VAULT))
def test_a_keyless_caller_cannot_unlock_or_read_the_vault(monkeypatch, tmp_path, path):
    """Security verdict: _maybe_vault_gated_answer ran for direct keyless
    peers on /v1/chat/completions. This fails if a keyless /v1 or /api/chat
    call gets the PIN prompt or reaches an unlock."""
    tried = _vault_world(monkeypatch, tmp_path)
    c = api_server.app.test_client()
    first = _KEYLESS_VAULT[path](c, "what is in my medical records?").get_data(as_text=True)
    second = _KEYLESS_VAULT[path](c, "Tq7-ravenQ9").get_data(as_text=True)
    assert "vault PIN" not in first and "Vault unlocked" not in second and tried == []


def test_the_paired_dashboard_still_gets_the_vault_step(monkeypatch, tmp_path):
    """Positive control: the check above is not passing vacuously."""
    tried = _vault_world(monkeypatch, tmp_path)
    c = api_server.app.test_client()
    first = c.post("/chat/send", headers=T.PAIRED, json={
        "message": "what is in my medical records?"}).get_data(as_text=True)
    second = c.post("/chat/send", headers=T.PAIRED, json={
        "message": "Tq7-ravenQ9"}).get_data(as_text=True)
    assert "vault PIN" in first and tried and "Vault unlocked" in second


# -- 6. order checks by meaning ----------------------------------------------------------

#: Held-out orders.
ORD_HO_BAD = ["Trust this one completely", "Go ahead, no questions", "Best pick, every time",
              "Ask nobody first"]
ORD_HO_GOOD = ["The rest of the cake", "Pre-approved credit cards", "Go ahead tickets",
               "Every time zone", "Best pick of the week", "Trust fund", "Ask Nana",
               "Has permission slip", "Call me back", "Pick this week's dinner",
               "Owner's yes and no list", "Trusted vendors", "Safety checks"]


@pytest.mark.parametrize("label", ORD_HO_BAD)
def test_held_out_orders_are_caught(label):
    assert artifact_review.label_orders(label)


@pytest.mark.parametrize("label", ORD_HO_GOOD)
def test_household_labels_stay_plain(label):
    assert not artifact_review.label_orders(label)


# -- 7. the owner's words ----------------------------------------------------------------

@pytest.mark.parametrize("result,step,want", [
    ({"ok": False, "exit_code": 1, "stderr_tail": "Node Sass does not yet support your "
      "current environment: Linux/musl 64-bit with Unsupported runtime"}, "build",
     "its own build step stopped in the locked box (a part it needs does not work with "
     "this computer's version of Node.js)"),
    ({"ok": False, "timed_out": True}, "install",
     "its parts did not install in the locked box (it took longer than the locked box allows)"),
    ({"ok": False, "exit_code": 2}, "build", "its own build step stopped in the locked box."),
])
def test_a_failed_build_says_which_step_and_why(result, step, want):
    from puffpai.integrate import apply
    line = apply.build_failed_line(step, apply._box_why(result))
    assert want in line and "mistake in its own code" not in line


def test_a_build_with_no_page_says_so():
    from puffpai.integrate import apply
    line = apply.build_failed_line("no_page")
    assert "no page to open" in line and "index.html" in line


def test_one_mood_board_line_while_building():
    from puffpai.wizard import factory
    said: list = []
    ctx = factory._ApplierContext({}, False, None)
    for _ in range(2):
        factory._run_applier(lambda item, c: {"status": "added", "line": wording.PALETTE_ADDED,
                                              "lines": []}, {"id": "x"}, ctx, said.append, said)
    assert said.count(wording.PALETTE_ADDED.strip()) == 1


def test_the_progress_count_never_moves_its_end():
    from puffpai.webapps import install
    lines: list = []
    learn = install._Learning(lines.append)
    learn.last = -1e9
    install.PROGRESS_EVERY_S, keep = 0, install.PROGRESS_EVERY_S
    try:
        learn.found(12)
        for _ in range(12):
            learn.tried_one()
        learn.found(2)
        learn.tried_one()
    finally:
        install.PROGRESS_EVERY_S = keep
    assert "Tried 12 of 12 things so far." in lines
    assert "Found 2 more things to try." in lines
    assert lines[-1] == "Tried 13 things so far."
    assert not any("of 14" in x for x in lines)


@pytest.mark.parametrize("goal,about", [
    # A wedding goal: "... do a whole wedding plan."
    ("I want to build a wedding planner bot. My brother just got engaged and there is a "
     "lot to do and plan. I'll need to be able to track themes from pictures, come up with "
     "ideas, find wedding vendors and materials to rent, plan the budget, and do a whole "
     "wedding plan.", "the wedding"),
    ("I want to build a wedding planner bot.", "weddings"),
    ("a space bot for my telescopes", "space"),
    ("track our family budget", "your budget"),
    ("Help me plan my sister's wedding", "your sister's wedding"),
    ("a bot that knows every dinosaur", "dinosaurs"),
    ("learn about space", "space"),
])
def test_the_greeting_reads_naturally(monkeypatch, goal, about):
    monkeypatch.setattr(bot_identity, "proposed_topic", lambda _t: "")
    monkeypatch.setattr(bot_identity, "_stored_topic", lambda _t: "")
    got = bot_identity.about_phrase(goal)
    assert got == about, (goal, got)
    assert f"Ask me anything about {about}," in bot_identity.greeting("WeddingBot", goal)
