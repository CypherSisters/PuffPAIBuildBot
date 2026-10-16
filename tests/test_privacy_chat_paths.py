"""Every chat path keeps personal details out of saved chats and tells the person.

Covers /chat/save (the dashboard's main save, which wrote the message verbatim),
/chat/send, /v1/chat/completions (stream and voice callers), /api/chat,
``puffpai chat``, the privacy answers, /system/privacy, /vault/scan-pii and
/knowledge/rescan_pii. Flask test client only; no port, no model, no network.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile

_TMP_DATA_DIR = tempfile.mkdtemp(prefix="puffpai_privacy_chat_")
os.environ.setdefault("PUFFPAI_DATA_DIR", _TMP_DATA_DIR)

import pytest  # noqa: E402

from puffpai import api_server  # noqa: E402
from puffpai.security import chat_privacy, vault_inbox  # noqa: E402
from puffpai.security.vault import Vault  # noqa: E402
from puffpai import plain_words  # noqa: E402  (the one banned list)

PW = "correct horse battery"
PHONE = "555-867-5309"
SCHOOL = "Lincoln Elementary"
ADDRESS = "42 Maple Street"
TEXT = f"my mom is {PHONE} and I go to {SCHOOL}, we live at {ADDRESS}"
RAW = (PHONE, SCHOOL, ADDRESS)
HEADERS = {"Authorization": f"Bearer {api_server._API_KEY}"}
#: On top of plain_words' one list: privacy wording that misled.
JARGON_EXTRA = ("encrypted at rest", "[redacted")


def _disk_text(folder: str) -> str:
    out = ""
    for root, _d, files in os.walk(folder):
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                out += fh.read().decode("utf-8", "replace")
    return out


def _records(v: Vault) -> list:
    return json.loads(v.retrieve(vault_inbox.PII_RECORD_NAME) or "[]")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    api_server.app.config.update(TESTING=True)
    chats = tmp_path / "chats"
    chats.mkdir()
    monkeypatch.setattr(api_server, "_get_chat_history_dir", lambda: str(chats))
    with api_server._chat_history_lock:
        saved = list(api_server._chat_history)
    chat_privacy._recent.clear()
    chat_privacy.forget_details()

    state = {"vault": None}

    def make(kind: str) -> Vault | None:
        if kind == "none":
            v = Vault(vault_dir=str(tmp_path / "novault"))
        else:
            v = Vault(vault_dir=str(tmp_path / "vault"))
            v.create(PW)
            if kind == "locked":
                v.lock()
        monkeypatch.setattr(api_server, "_vault_instance", v)
        state["vault"] = v
        return v

    ns = type("Env", (), {})()
    ns.make = make
    ns.chats = str(chats)
    ns.tmp = str(tmp_path)
    ns.client = api_server.app.test_client()
    yield ns
    with api_server._chat_history_lock:
        api_server._chat_history[:] = saved
    chat_privacy._recent.clear()
    chat_privacy.forget_details()


def _opened(tmp: str) -> Vault:
    v = Vault(vault_dir=os.path.join(tmp, "vault"))
    v.unlock(PW)
    return v


# ── /chat/save ────────────────────────────────────────────────────────────

def test_chat_save_locked_keeps_raw_off_disk_and_files_in_inbox(env, caplog):
    env.make("locked")
    caplog.set_level("DEBUG")
    r = env.client.post("/chat/save", headers=HEADERS, json={
        "conversation_id": "c1", "user_message": TEXT,
        "assistant_message": f"Got it, I will call {PHONE}."})
    body = r.get_json()
    assert body["success"] and body["pii"]["kept"] == "inbox"
    assert "locked vault" in body["pii"]["notice"]
    disk = _disk_text(env.chats)
    for raw in RAW:
        assert raw not in disk
        assert raw not in caplog.text
    assert "[phone number kept private]" in disk
    assert vault_inbox.pending_count(os.path.join(env.tmp, "vault")) == 1
    items = _records(_opened(env.tmp))[-1]["items"]
    assert items["phone"] == [PHONE] and items["school"] == [SCHOOL]


def test_chat_save_open_vault_files_now(env):
    v = env.make("open")
    body = env.client.post("/chat/save", headers=HEADERS, json={
        "conversation_id": "c2", "user_message": TEXT,
        "assistant_message": "ok"}).get_json()
    assert body["pii"]["kept"] == "vault"
    assert _records(v)[-1]["items"]["address"] == [ADDRESS]


def test_chat_save_without_a_vault_says_so_and_still_keeps_raw_off_disk(env):
    env.make("none")
    body = env.client.post("/chat/save", headers=HEADERS, json={
        "conversation_id": "c3", "user_message": TEXT,
        "assistant_message": "ok"}).get_json()
    assert body["pii"]["kept"] == "not_kept"
    assert "not set up a vault" in body["pii"]["notice"]
    disk = _disk_text(env.chats)
    assert not any(raw in disk for raw in RAW)


def test_clean_message_is_saved_unchanged(env):
    env.make("locked")
    body = env.client.post("/chat/save", headers=HEADERS, json={
        "conversation_id": "c4", "user_message": "tell me about T. rex",
        "assistant_message": "It lived 66 million years ago."}).get_json()
    assert "pii" not in body
    assert "tell me about T. rex" in _disk_text(env.chats)


# ── /chat/send and the OpenAI-compatible route ──────────────────────────────

def _echo(user_message, conversation_history=None, pii_result=None):
    return {"response": f"You said {user_message}", "sources": [],
            "suggestions": []}


def test_chat_send_locked(env, monkeypatch):
    env.make("locked")
    monkeypatch.setattr(api_server, "_process_chat_query", _echo)
    body = env.client.post("/chat/send", headers=HEADERS, json={
        "message": TEXT, "conversation_id": "s1"}).get_json()
    assert body["pii"]["kept"] == "inbox" and body["pii"]["notice"]
    assert not any(raw in _disk_text(env.chats) for raw in RAW)


def test_v1_voice_caller_hears_the_notice(env, monkeypatch):
    env.make("locked")
    monkeypatch.setattr(api_server, "_process_chat_query", _echo)
    monkeypatch.setattr(api_server, "_detect_route", lambda *a, **k: "agent")
    body = env.client.post("/v1/chat/completions", headers=HEADERS, json={
        "model": "other", "messages": [{"role": "user", "content": TEXT}]}
    ).get_json()
    content = body["choices"][0]["message"]["content"]
    assert "locked vault" in content


def _stream_meta(resp) -> dict:
    meta = {}
    for line in resp.get_data(as_text=True).splitlines():
        if line.startswith("data: ") and "puffpai_meta" in line:
            meta = json.loads(line[6:])["puffpai_meta"]
    return meta


def test_dashboard_stream_then_save_files_once(env, monkeypatch):
    env.make("locked")
    monkeypatch.setattr(api_server, "_detect_route", lambda *a, **k: "rag")
    monkeypatch.setattr(api_server, "_stream_ollama_tokens",
                        lambda *a, **k: iter(["Nice ", "school!"]))
    r = env.client.post("/v1/chat/completions", headers=HEADERS, json={
        "model": "puffpai", "stream": True,
        "messages": [{"role": "user", "content": TEXT}]})
    pii = _stream_meta(r)["pii"]
    assert pii["notice"] and pii["kept"] == "inbox"
    assert not any(raw in pii["redacted_message"] for raw in RAW)
    # the dashboard now saves the redacted copy; an old one sends the original
    env.client.post("/chat/save", headers=HEADERS, json={
        "conversation_id": "d1", "user_message": TEXT,
        "assistant_message": "Nice school!"})
    assert vault_inbox.pending_count(os.path.join(env.tmp, "vault")) == 1
    assert not any(raw in _disk_text(env.chats) for raw in RAW)


def test_ollama_style_api_chat_scans(env, monkeypatch):
    env.make("locked")
    monkeypatch.setattr(api_server, "_process_voice_query",
                        lambda *a, **k: "Okay.")
    body = env.client.post("/api/chat", headers=HEADERS, json={
        "messages": [{"role": "user", "content": TEXT}]}).get_json()
    assert "locked vault" in body["message"]["content"]
    assert vault_inbox.pending_count(os.path.join(env.tmp, "vault")) == 1


# ── puffpai chat ────────────────────────────────────────────────────────────

def test_cli_chat_scans_even_on_the_fastpath(env, monkeypatch, capsys):
    from puffpai import app_fastpath, cli, setup_ops
    env.make("locked")
    monkeypatch.setattr(cli, "_maybe_autoindex", lambda: None)
    monkeypatch.setattr(app_fastpath, "handle", lambda text: "Noted.")
    monkeypatch.setattr(setup_ops, "brain_status",
                        lambda: {"status": "model_missing", "message": "none"})
    rc = cli._cmd_chat(argparse.Namespace(once=TEXT))
    out = capsys.readouterr().out
    assert rc == 0 and "Noted." in out and "locked vault" in out
    assert not any(raw in out for raw in RAW)
    assert vault_inbox.pending_count(os.path.join(env.tmp, "vault")) == 1


# ── what the bot says about privacy ─────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "what are the three security zones",
    "how do you keep my things private?",
    "which zone am I in right now",
])
def test_privacy_answers_are_true_and_plain(env, question):
    env.make("locked")
    answer = api_server._maybe_puffpai_facts_answer(question, rich=True)
    assert answer
    low = answer.lower()
    assert "not encrypted" in low
    assert plain_words.is_plain(answer, also=JARGON_EXTRA), answer


def test_privacy_question_on_chat_send_path(env, monkeypatch):
    env.make("open")
    res = api_server._process_chat_query("how do you keep my things private?")
    assert "not encrypted" in res["response"]


def test_system_privacy_route(env):
    env.make("locked")
    body = env.client.get("/system/privacy", headers=HEADERS).get_json()
    assert body["vault"] == "locked" and body["saved_chats_encrypted"] is False
    assert isinstance(body["outside_services_on"], int)
    text = " ".join(body["lines"]).lower()
    assert "locked in a vault" in text
    assert plain_words.is_plain(text, also=JARGON_EXTRA), text


def test_scan_demo_files_nothing(env):
    v = env.make("open")
    body = env.client.post("/vault/scan-pii", headers=HEADERS,
                           json={"text": TEXT}).get_json()
    assert body["pii_detected"] and PHONE not in body["redacted_text"]
    assert _records(v) == []


# ── /knowledge/rescan_pii ───────────────────────────────────────────────────

class _Coll:
    def __init__(self, docs):
        self.docs = dict(docs)

    def get(self, include=None, limit=300, offset=0):
        ids = list(self.docs)[offset:offset + limit]
        return {"ids": ids, "documents": [self.docs[i] for i in ids]}

    def update(self, ids, documents):
        for i, d in zip(ids, documents):
            self.docs[i] = d


def test_rescan_pii_runs_and_redacts(env, monkeypatch, tmp_path):
    v = env.make("open")
    coll = _Coll({"a": "My SSN is 123-45-6789.", "b": "Plain text."})
    rag = type("R", (), {})()
    rag.client = type("C", (), {"get_collection": lambda self, name: coll})()
    monkeypatch.setattr(api_server, "_get_dual_zone_rag", lambda: rag)
    src = tmp_path / "src"
    src.mkdir()
    (src / "indexed_libraries.json").write_text(json.dumps([
        {"collection": "notes", "library": "Notes",
         "uploaded_at": "2026-09-23"}]))
    monkeypatch.setattr(api_server, "_SRC_DIR", str(src))
    body = env.client.post("/knowledge/rescan_pii", headers=HEADERS,
                           json={}).get_json()
    assert body["success"] and body["total_remediated"] == 1
    assert "123-45-6789" not in coll.docs["a"]
    assert coll.docs["b"] == "Plain text."
    assert v.list_documents()


# ── a detail repeated with no naming word (reproduction) ─────────

NAME = "Casey Verifier"
SSN = "219-09-9999"


def test_reply_that_repeats_a_name_with_no_naming_word_is_hidden(env):
    """The exact leak found in review: the SSN was hidden, the name was not."""
    env.make("locked")
    env.client.post("/chat/save", headers=HEADERS, json={
        "conversation_id": "e1",
        "user_message": f"My name is {NAME} and my SSN is {SSN}",
        "assistant_message": f"Got it, thanks {NAME}! I noted your SSN {SSN}."})
    disk = _disk_text(env.chats)
    for raw in ("Casey", "Verifier", SSN):
        assert raw not in disk, raw
    assert "thanks [full name kept private]!" in disk


def test_stream_then_save_of_the_redacted_copy_still_hides_the_name(env,
                                                                    monkeypatch):
    """The dashboard saves the redacted question, so the name is only known
    from the stream call; the reply's copy must still be hidden."""
    env.make("locked")
    monkeypatch.setattr(api_server, "_detect_route", lambda *a, **k: "rag")
    monkeypatch.setattr(api_server, "_stream_ollama_tokens",
                        lambda *a, **k: iter(["Hi ", "Casey!"]))
    r = env.client.post("/v1/chat/completions", headers=HEADERS, json={
        "model": "puffpai", "stream": True,
        "messages": [{"role": "user", "content": f"call me {NAME}"}]})
    redacted = _stream_meta(r)["pii"]["redacted_message"]
    assert "Casey" not in redacted
    env.client.post("/chat/save", headers=HEADERS, json={
        "conversation_id": "e2", "user_message": redacted,
        "assistant_message": f"Hi Casey! Nice to meet you, {NAME}."})
    env.client.post("/chat/save", headers=HEADERS, json={
        "conversation_id": "e2", "user_message": "Casey Verifier likes rockets",
        "assistant_message": "Rockets are great, Casey's pick is a good one."})
    disk = _disk_text(env.chats)
    assert "Casey" not in disk and "Verifier" not in disk
    assert "Rockets are great" in disk


def test_password_and_health_repeats_are_hidden_but_look_alikes_stay():
    chat_privacy.forget_details()
    try:
        chat_privacy.protect_text(
            "my wifi password is Sunflower!22, and I have asthma",
            source="t", vault=None)
        out = chat_privacy.protect_text(
            "Saved Sunflower!22 for you. Asthma can flare in cold air. "
            "Sunflowers are pretty and Caseyville is a town.",
            source="t", role="assistant")["redacted_text"]
        assert "Sunflower!22" not in out and "Asthma" not in out
        assert "Sunflowers are pretty" in out and "Caseyville" in out
    finally:
        chat_privacy.forget_details()


def test_remembered_details_are_hashes_only_and_expire(monkeypatch):
    chat_privacy.forget_details()
    try:
        chat_privacy.protect_text(f"My name is {NAME}", source="t", vault=None)
        held = repr(chat_privacy._echo)
        assert "Casey" not in held and "Verifier" not in held
        assert chat_privacy.hide_repeats("hi Casey")[0] == \
            "hi [full name kept private]"
        monkeypatch.setattr(chat_privacy, "_ECHO_SECONDS", -1.0)
        assert chat_privacy.hide_repeats("hi Casey")[0] == "hi Casey"
    finally:
        chat_privacy.forget_details()


def test_nothing_remembered_means_nothing_changes():
    chat_privacy.forget_details()
    text = "Casey Verifier is a made-up name in a story."
    assert chat_privacy.hide_repeats(text) == (text, [])


def test_log_preview_hides_a_repeat_too():
    chat_privacy.forget_details()
    try:
        chat_privacy.protect_text(f"My name is {NAME}", source="t", vault=None)
        assert "Casey" not in chat_privacy.safe_for_log(
            "Casey Verifier likes rockets")
    finally:
        chat_privacy.forget_details()


def test_saving_again_files_nothing_new(env):
    """Each save re-checks the whole chat; an already-hidden message must not
    produce a new record (it once filed the word after a marker)."""
    v = env.make("open")
    for turn in range(2):
        env.client.post("/chat/save", headers=HEADERS, json={
            "conversation_id": "again",
            "user_message": "our wifi password is Sunflower!22. Please help",
            "assistant_message": f"Sure {turn}."})
    records = _records(v)
    assert len(records) == 1 and records[0]["items"] == {
        "password": ["Sunflower!22."]}
