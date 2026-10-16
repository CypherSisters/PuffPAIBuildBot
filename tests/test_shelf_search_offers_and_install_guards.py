"""Shelf search, yes routing and install guards, one test each.

1. A shelf is asked for the whole phrase AND its key single words, merged in
   each site's own order; a find clearly about something else is left out.
2. The job step's model call has thinking off (qwen3.5:9b answered nothing).
3. The loop never lets a "done" stand after the last action failed, and
   install_thing's own "cannot" reaches the model as a failure.
4. A yes goes to the newest question: an older daily-update offer expires.
5. "It is waiting for your yes" is never shown by a find install (no button).
6. Installing the same thing again reuses the install.
7. Trading things are never offered by default; run_command cannot reach
   the vault or key files.

No network: recorded shelf answers and scripted models stand in.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os

import pytest

from finding_fakes import Sites


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


@pytest.fixture
def sites(monkeypatch, state):
    from puffpai.finding import hermes_index, net
    fake = Sites()
    monkeypatch.setattr(net, "_send", fake)
    hermes_index.forget()
    yield fake
    hermes_index.forget()


def _find(site, id_, name, what="", type_="skill", can="install"):
    from puffpai.finding import sites as S
    return S.make(site=site, type_=type_, id=id_, name=name, what=what, can=can)


# 1 ─────────────────────────────────────────────────────────────────────────

def test_shelves_search_the_phrase_and_its_key_words_and_drop_other_subjects(
        state, monkeypatch):
    from puffpai.finding import find
    asked: list[tuple[str, str]] = []
    answers = {
        "keep track of our filament": [
            _find("clawhub", "clawhub:a/genome-track", "genome-track",
                  "Track genomic regions in a browser."),
            _find("clawhub", "clawhub:b/spool-log", "spool-log",
                  "Log filament spools and how much is left."),
        ],
        "filament": [
            _find("clawhub", "clawhub:c/filament-vault", "Filament Vault",
                  "Track 3D printing filament inventory locally."),
            _find("clawhub", "clawhub:b/spool-log", "spool-log",
                  "Log filament spools and how much is left."),
        ],
    }

    def shelf(words, limit):
        asked.append(("clawhub", words))
        return [dict(f) for f in answers.get(words, [])]

    def code_site(words, limit):
        asked.append(("github", words))
        return []

    monkeypatch.setattr(find, "_SEARCHERS", {"clawhub": shelf, "github": code_site})
    monkeypatch.setattr(find, "ALL_SITES", ("clawhub", "github"))
    monkeypatch.setattr(find, "_add_verdicts", lambda finds: None)
    got = find.find_things("keep track of our filament", subject="a 3D printing helper")
    names = [f["name"] for j in got["jobs"] for t in j["types"] for f in t["finds"]]
    # the phrase and its key word on the shelf, the phrase only on the code site
    assert ("clawhub", "keep track of our filament") in asked
    assert ("clawhub", "filament") in asked
    assert [w for s, w in asked if s == "github"] == ["keep track of our filament"]
    # merged turn by turn, each once, and the genomics one left out
    assert names == ["Filament Vault", "spool-log"]
    assert find.key_words("3D printer filament tracking", limit=6) == [
        "3d", "printer", "filament"]
    assert find.queries_for("clawhub", "3D printer filament tracking") == [
        "3D printer filament tracking", "printer", "filament"]
    assert find.on_subject({"name": "x", "what": "nothing alike"}, []) is True


# 2 ─────────────────────────────────────────────────────────────────────────

def test_the_job_step_asks_with_thinking_off_and_keeps_the_model_loaded():
    from puffpai.wizard import needs_llm
    backend = needs_llm.make_backend("http://127.0.0.1:11435", "qwen3.5:9b",
                                     timeout=90, max_tokens=1400)
    payload = backend._build_payload([{"role": "user", "content": "hi"}], 0.0, 1400,
                                     stream=True, format={"type": "object"})
    assert payload["think"] is False
    assert payload["keep_alive"]
    from puffpai.inference.ollama_backend import OllamaBackend
    plain = OllamaBackend(url="http://127.0.0.1:11435", model="qwen3.5:9b")
    assert "think" not in plain._build_payload([], 0.3, 10, stream=False)


# 3 ─────────────────────────────────────────────────────────────────────────

class FakeBrain:
    """Scripted replies: a string is words, a list is tool calls."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
        self.calls.append([dict(m) for m in messages])
        reply = self.replies.pop(0) if self.replies else "All done."
        if isinstance(reply, list):
            return {"content": "", "tool_calls": [
                {"function": {"name": n, "arguments": a}} for n, a in reply]}
        return {"content": reply, "tool_calls": []}


@pytest.fixture
def loop_tools():
    from puffpai import brain_tools
    brain_tools.clear()
    obj = {"type": "object", "properties": {}, "additionalProperties": False}
    brain_tools.register("log_dividend", "Log a dividend.", obj, lambda a, c: {
        "exit_code": 127, "worked": False, "output": "dividend: command not found",
        "note": "The command did not work (exit code 127)."})
    brain_tools.register("find_things", "Search.", obj, lambda a, c: "nothing")
    yield brain_tools
    brain_tools.clear()


def test_a_done_claim_after_a_failed_command_is_never_shown(loop_tools):
    from puffpai import brain_loop
    shown: list[str] = []
    brain = FakeBrain([("log_dividend", {})], [("find_things", {})],
                      "I logged your dividends manually for now.",
                      "Done, all logged.")
    got = brain_loop.run([{"role": "user", "content": "log my dividends"}],
                         ctx={"caller": "owner"}, backend=brain,
                         emit=lambda e: shown.append(e.get("text") or ""))
    assert "logged your dividends" not in got["text"]
    assert got["text"].startswith("That did not work, so it is not done.")
    assert "command not found" in got["text"]
    assert "logged your dividends" not in "".join(shown)   # never streamed to her
    # the model was told, with the tool's own reason
    assert "did not work" in brain.calls[-1][-1]["content"]


def test_an_honest_answer_after_a_failure_stands_and_a_later_success_clears_it(loop_tools):
    from puffpai import brain_loop
    brain = FakeBrain([("log_dividend", {})], "I logged it.",
                      "The command did not work: dividend was not found.")
    got = brain_loop.run([{"role": "user", "content": "log it"}],
                         ctx={"caller": "owner"}, backend=brain)
    assert got["text"] == "The command did not work: dividend was not found."
    loop_tools.register("log_ok", "Log.", {"type": "object", "properties": {}},
                        lambda a, c: {"worked": True, "exit_code": 0, "output": "ok"})
    brain = FakeBrain([("log_dividend", {})], [("log_ok", {})], "Done, it is logged.")
    got = brain_loop.run([{"role": "user", "content": "log it"}],
                         ctx={"caller": "owner"}, backend=brain)
    assert got["text"] == "Done, it is logged."
    assert brain_loop.claims_done("I'll install EverGlow for you. Shall I?", "install_thing")
    assert not brain_loop.claims_done("Nothing was installed.", "install_thing")
    assert "Tool results are the truth" in brain_loop.TOOL_NOTE


def test_install_thing_relays_its_own_cannot_as_a_failure(sites):
    from puffpai import brain_loop, brain_tools
    from puffpai.finding import find, sites as S, tools
    brain_tools.clear()
    tools.register_all()
    find.remember([S.make(site="github", type_="server_app", id="github:o/everglow",
                          name="EverGlow", what="Event rental system.", can="look",
                          look_why="it needs its own server program running.")])
    asked: list = []
    got = brain_tools.call("install_thing", {"id": "github:o/everglow"},
                           {"caller": "owner", "ask": lambda q: asked.append(q) or True})
    result = got["result"]
    assert asked == []                                   # nothing to say yes to
    assert result["installed"] is False and result["ok"] is False
    assert "look at it yourself" in result["said"].lower()
    assert "Do not offer to install it" in result["note"]
    assert brain_loop.tool_failed(got)
    brain_tools.clear()


# 4 ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def offer(state):
    import json
    import time
    from puffpai import build_manifest, offers, routines
    path = build_manifest.manifest_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"schema": "build_manifest/v2", "build": {"name": "WedBot"},
                   "surfaces": {"goal": "a wedding planner bot"}}, fh)
    routines.set_feed_reader(lambda: [{"id": "x", "name": "Ideas", "items": [{"title": "t"}]}])
    routines.set_library_picker(lambda terms: None)
    assert "Just say yes or no" in offers.chat_answer("can you send me daily updates?")
    yield offers, time.time()
    routines.set_feed_reader(None)
    routines.set_library_picker(None)


def test_a_newer_question_takes_the_yes_from_an_older_offer(offer, loop_tools):
    from puffpai import brain_loop
    offers, asked_at = offer
    assert offers._pending_chat_offer()
    # A later turn ends on its own question: the offer stops taking her yes.
    brain_loop.run([{"role": "user", "content": "install EverGlow"}],
                   ctx={"caller": "owner", "turn_started": asked_at + 5},
                   backend=FakeBrain("EverGlow needs its own server. Want a link?"))
    assert not offers._pending_chat_offer()
    assert offers.chat_answer("Yes.") is None            # goes to the chat, not the offer
    assert offers.current_offer() is not None            # still on the home page


def test_an_offer_asked_in_the_same_turn_is_the_newest_question(offer, loop_tools):
    from puffpai import brain_loop
    offers, asked_at = offer
    brain_loop.run([{"role": "user", "content": "hi"}],
                   ctx={"caller": "owner", "turn_started": asked_at - 0.5},
                   backend=FakeBrain("Hello! What would you like to plan?"))
    assert offers._pending_chat_offer()
    brain_loop.run([{"role": "user", "content": "thanks"}],
                   ctx={"caller": "owner", "turn_started": asked_at + 5},
                   backend=FakeBrain("You are welcome."))
    assert offers._pending_chat_offer()                  # no new question, still hers


# 5 ─────────────────────────────────────────────────────────────────────────

def test_a_find_install_never_says_it_waits_for_a_yes_it_cannot_take(state, monkeypatch):
    from puffpai.finding import find, install, sites as S
    from puffpai.integrate import apply as r3_apply, records
    from puffpai.integrate import wording as r3w
    find.remember([dict(S.make(site="github", type_="browser_app", id="github:o/openstock",
                               name="OpenStock", url="https://github.com/o/openstock",
                               can="integrate", licence="MIT"),
                        prepared={"iid": "i_1", "code_sha": "a", "host_sha": "b"})])
    monkeypatch.setattr(r3_apply, "prepared_card", lambda iid: {"iid": iid})

    def held(iid, *, code_sha, host_sha, progress):
        line = r3w.HELD.format(reason=r3w.REASONS["ai_unreviewed"])
        progress(line)
        progress(r3w.HELD_WAITING)
        return {"status": "held", "line": f"{line} {r3w.HELD_WAITING}",
                "integration_id": "i_1"}
    monkeypatch.setattr(r3_apply, "finish_prepared", held)
    removed = []
    monkeypatch.setattr(records, "remove", lambda rid, by="": removed.append(rid))
    said: list[str] = []
    out = install.install_thing("github:o/openstock", progress=said.append, via="wizard")
    text = " ".join(said + out["lines"] + [out["line"]])
    assert "waiting for your yes" not in text.lower()
    assert out["status"] == "look" and "look at it yourself" in out["line"]
    assert "written by an AI" in out["line"] and removed == ["i_1"]


# 6 ─────────────────────────────────────────────────────────────────────────

def test_installing_the_same_skill_again_reuses_the_install(sites):
    from puffpai import brain_tools, skills
    from puffpai.finding import find, install, tools
    find.find_things("wedding planner", kinds=["skill"])
    fid = "clawhub:ivangdavila/wedding-planner"
    first = install.install_thing(fid)
    again = install.install_thing(fid)
    assert first["ok"] and again["ok"] and again["already"]
    assert again["folder"] == first["folder"] == "wedding-planner"
    assert "already in your bot" in again["line"]
    assert [r["folder"] for r in skills.installed()] == ["wedding-planner"]
    assert len(install.entries()) == 1
    brain_tools.clear()
    tools.register_all()
    asked: list = []
    got = brain_tools.call("install_thing", {"id": fid},
                           {"caller": "owner", "ask": lambda q: asked.append(q) or True})
    assert asked == [] and "already in your bot" in got["result"]
    assert [r["folder"] for r in skills.installed()] == ["wedding-planner"]
    brain_tools.clear()


# 7 ─────────────────────────────────────────────────────────────────────────

def _trading_world(monkeypatch):
    from puffpai.finding import find, sites as S
    made = [S.make(site="clawhub", type_="skill", id="clawhub:a/portfolio", name="Portfolio",
                   what="Track stock holdings.", can="install"),
            S.make(site="clawhub", type_="skill", id="clawhub:b/bot", name="Auto trader",
                   what="Auto-trade stocks with Alpaca.", can="install")]
    monkeypatch.setattr(find, "_SEARCHERS", {s: (lambda w, n: [dict(f) for f in made])
                                             if s == "clawhub" else (lambda w, n: [])
                                             for s in find.ALL_SITES})
    monkeypatch.setattr(find, "_add_verdicts", lambda finds: None)
    monkeypatch.setattr(find, "_switched_off", lambda site: False)
    return find


def test_trading_is_shown_only_when_her_own_words_ask(state, monkeypatch):
    from puffpai import brain_tools
    from puffpai.finding import tools
    from puffpai.finding import wording as W
    find = _trading_world(monkeypatch)
    brain_tools.clear()
    tools.register_all()
    # the chat model wrote "stock trading"; she only asked about her portfolio
    got = brain_tools.call("find_things", {"words": "stock trading"},
                           {"caller": "owner", "said": "help me with my stock portfolio"})
    assert "clawhub:b/bot" not in got["result"] and "clawhub:a/portfolio" in got["result"]
    got = brain_tools.call("find_things", {"words": "stock trading"},
                           {"caller": "owner", "said": "find me a stock trading bot"})
    assert "clawhub:b/bot" in got["result"]
    # and it is not installed from chat unless she asked for trading
    asked: list = []
    got = brain_tools.call("install_thing", {"id": "clawhub:b/bot"},
                           {"caller": "owner", "said": "yes install it",
                            "ask": lambda q: asked.append(q) or True})
    assert asked == [] and got["result"]["installed"] is False
    assert "trades with real money" in got["result"]["said"]
    # the wizard: the brain's job words ask for trading, her goal does not
    jobs = {"kind": "investing", "jobs": [{"name": "Trade stocks", "parts": [
        {"search": ["stock trading bot"]}]}]}
    got = find.find_for_goal("a finance and investing bot", asker=lambda g: jobs)
    ids = [f["id"] for j in got["jobs"] for t in j["types"] for f in t["finds"]]
    assert "clawhub:b/bot" not in ids and "clawhub:a/portfolio" in ids
    assert not W.ASKS_TRADING_RE.search("keep track of our filament orders")
    brain_tools.clear()


@pytest.fixture
def cmd_state(state, monkeypatch, tmp_path):
    from puffpai import brain_commands
    vault = tmp_path / "data" / "vault_data"
    vault.mkdir(parents=True)
    (vault / "vault.enc").write_text("x")
    monkeypatch.setattr(brain_commands, "_vault_dir", lambda: str(vault))
    state.mkdir(parents=True, exist_ok=True)
    (state / "api_key.txt").write_text("k" * 40)
    return state


@pytest.mark.parametrize("command", [
    "type ..\\api_key.tx?",           # the safety probe's wildcard
    "cat ../api_k*",
    "cat ../*",
    "cd .. && cat *",
    "grep -r token ~/..",
    "ls ../../data",
    "cat ../../data/vault_data/*",
])
def test_run_command_cannot_reach_the_vault_or_key_files(cmd_state, command):
    from puffpai import brain_commands
    with pytest.raises(ValueError, match="never touch the vault or key files"):
        brain_commands.question({"command": command}, {})
    with pytest.raises(ValueError):
        brain_commands.run({"command": command}, {"said_yes": True})


@pytest.mark.parametrize("command", [
    "ls *", "echo $((10 / 2))", "python3 scripts/add.py --n 3/4", "cat ~/notes.txt",
    "curl -s https://example.com/a/b", "ls ./sub",
])
def test_run_command_still_runs_ordinary_commands(cmd_state, command):
    from puffpai import brain_commands
    q = brain_commands.question({"command": command}, {})
    assert q["command"] == command
