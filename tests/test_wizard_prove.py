"""Prove, replayed against the build that made it necessary.

An owner built a space bot called Infinity and the wizard said
"Setup complete. Your bot is ready." while the persona id was the reserved word
``apps``, the persona was invisible in the dashboard, two knowledge packs were
"chosen" and never downloaded with nothing saying so, and the closing screen
printed commands that did not exist on her machine.

The rule: *"a Prove that cannot fail on a known-broken build is not a
proof."* So the first test here rebuilds that machine, on disk, and requires
Prove to fail on it - and then requires the healthy equivalent to pass, with
only the gaps that are genuinely gaps.

The live half is exercised against a stub HTTP server on an ephemeral port:
never a real backend, never a real model, and the API key never printed.

Run:
    pytest tests/test_wizard_prove.py
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from puffpai import persona_scaffold, prove, prove_live
from puffpai.config import get_state_dir
from puffpai.knowledge import build_sources

#: An owner's trimmed allowlist, verbatim, WITHOUT the persona's own surface -
#: which is the second failure: a bot that nothing in the nav can hang on.
BROKEN_ALLOWLIST = ["comms.calendar", "daily.notes", "daily.weather",
                    "instruments.telescope", "knowledge.library"]

GOAL = "a space bot for my telescopes"


# ---------------------------------------------------------------------------
# fixtures: the two builds
# ---------------------------------------------------------------------------

def _write_persona_files(persona_id: str, name: str, *, surfaces: list[str]) -> dict:
    """Write the two files, and return them in the shape ``scaffold`` returns.

    ``persona_scaffold.scaffold`` refuses a reserved id, which is the fix that
    landed afterwards. To replay the broken build the two files are written
    directly from the same pure builders: the fixture bypasses the refusal, the
    code under test does not. Everything else -- including the manifest block --
    is built by the code that really builds it.
    """
    state = get_state_dir()
    app = persona_scaffold.build_app_manifest(persona_id, name, GOAL,
                                              ["telescopes"])
    app["surfaces"] = list(surfaces)
    pack = persona_scaffold.build_pack_manifest(persona_id, name)
    paths = {}
    for key, folder, filename, payload in (
            ("app_path", os.path.join(state, "apps", persona_id),
             "app.json", app),
            ("pack_path", os.path.join(state, "packs", persona_id),
             "pack.json", pack)):
        os.makedirs(folder, exist_ok=True)
        paths[key] = os.path.join(folder, filename)
        with open(paths[key], "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    return {"id": persona_id, "name": name, "goal": GOAL,
            "lists": app.get("lists") or {}, "surfaces": list(surfaces),
            **paths}


def _manifest(made: dict, allowlist: list[str]) -> dict:
    """The manifest a real build writes for *made*.

    **Built by the wizard's own builder**, not by hand. The hand-written
    fixture this replaced carried ``persona.id``, which no build has ever
    written: the wizard and ``puffpai persona new`` both write ``app``. Prove
    keyed on ``id``, so every check here passed against a shape no owner has,
    while every real build was told it had made no persona.
    """
    from puffpai.wizard.packages.purpose import skill as purpose_skill

    return {
        "schema": "build_manifest/v2",
        "persona": purpose_skill.scaffolded_block(made),
        "surfaces": {"allowlist": list(allowlist)},
    }


def _goal_only_manifest(name: str) -> dict:
    """What a build that recorded a goal and scaffolded no place writes."""
    from puffpai.wizard.packages.purpose import skill as purpose_skill

    return {"schema": "build_manifest/v2",
            "persona": purpose_skill.recorded_persona({"goal": GOAL}, name)}


def _sources_nothing_downloaded() -> dict:
    """Two packs chosen, nothing on disk - a Knowledge page reading zero."""
    items = [
        {"id": "wikipedia", "label": "Wikipedia",
         "status": build_sources.STATUS_SELECTED_NOT_DOWNLOADED},
        {"id": "stackexchange_science", "label": "Science Stack Exchange",
         "status": build_sources.STATUS_SELECTED_NOT_DOWNLOADED},
    ]
    return {"schema": build_sources.SCHEMA, "items": items,
            "counts": {build_sources.STATUS_SELECTED_NOT_DOWNLOADED: 2,
                       build_sources.STATUS_FETCHED_NOT_INDEXED: 0,
                       build_sources.STATUS_INDEXED: 0,
                       build_sources.STATUS_OFFERED: 0},
            "packs_chosen": ["pack:wikipedia", "pack:stackexchange_science"]}


def _sources_all_done() -> dict:
    return {"schema": build_sources.SCHEMA,
            "items": [{"id": "wikipedia", "label": "Wikipedia",
                       "status": build_sources.STATUS_INDEXED}],
            "counts": {build_sources.STATUS_INDEXED: 1,
                       build_sources.STATUS_SELECTED_NOT_DOWNLOADED: 0,
                       build_sources.STATUS_FETCHED_NOT_INDEXED: 0,
                       build_sources.STATUS_OFFERED: 0},
            "packs_chosen": ["pack:wikipedia"]}


def _launchers(tmp_path, *, present: bool) -> dict:
    """An installer pointer whose launcher scripts are, or are not, there."""
    folder = tmp_path / ("good" if present else "bad")
    folder.mkdir(parents=True, exist_ok=True)
    env_file = folder / ".env.install"
    env_file.write_text("PUFFPAI_DASHBOARD_PORT=3100\n", encoding="utf-8")
    scripts = {"backend": ".\\run-backend.ps1", "dashboard": ".\\run-dashboard.ps1"}
    if present:
        for script in scripts.values():
            (folder / script.lstrip(".\\")).write_text("echo hi\n",
                                                       encoding="utf-8")
    return {"env_file": str(env_file), **scripts,
            "dashboard_url": "http://127.0.0.1:3100"}


def _result(record: dict, check_id: str) -> str:
    for check in record["checks"]:
        if check["id"] == check_id:
            return check["result"]
    raise AssertionError(f"no check called {check_id}: "
                         f"{[c['id'] for c in record['checks']]}")


# ---------------------------------------------------------------------------
# THE KEY TEST: the broken build, and the same build made right
# ---------------------------------------------------------------------------

def test_prove_fails_on_the_09_19_build(tmp_path):
    made = _write_persona_files("apps", "Infinity", surfaces=["daily.notes"])
    record = prove.static_prove(
        manifest=_manifest(made, BROKEN_ALLOWLIST),
        sources=_sources_nothing_downloaded(),
        launchers=_launchers(tmp_path, present=False),
        goal=GOAL, bot_name="Infinity")

    assert record["verdict"] == prove.FAIL
    # 1. the id was a word the build already uses
    assert _result(record, "id_sanity") == prove.FAIL
    # 2. nothing in the built nav could show it
    assert _result(record, "persona_visible") == prove.FAIL
    # 3. the packs were chosen and never fetched - a gap, not a failure
    assert _result(record, "sources_honest") == prove.GAP
    # 4. the closing screen would have printed a launcher that is not there
    assert _result(record, "handoff_correct") == prove.FAIL
    # 5. the goal did reach the model, and Prove says so rather than piling on
    assert _result(record, "goal_reaches_model") == prove.PASS

    fetches = [g for g in record["gaps"] if g["fix"]["id"] == "source.fetch"]
    assert {g["fix"]["args"]["source_id"] for g in fetches} == {
        "wikipedia", "stackexchange_science"}
    for gap in fetches:
        assert gap["fix_command"].startswith("puffpai sources add ")
    rename = [g for g in record["gaps"]
              if g["fix"]["id"] == "persona.rename_id"]
    assert rename, "a reserved id gets the one command that changes it"

    lines = "\n".join(prove.closing_lines(record))
    assert "Before you start" in lines
    assert prove.BEGIN_MARK in lines and prove.END_MARK in lines


def test_prove_passes_on_the_same_build_made_right(tmp_path):
    surface = persona_scaffold.persona_surface("infinity")
    made = _write_persona_files("infinity", "Infinity", surfaces=[surface])
    record = prove.static_prove(
        manifest=_manifest(made, BROKEN_ALLOWLIST + [surface]),
        sources=_sources_nothing_downloaded(),
        launchers=_launchers(tmp_path, present=True),
        goal=GOAL, bot_name="Infinity")

    assert _result(record, "id_sanity") == prove.PASS
    assert _result(record, "persona_visible") == prove.PASS
    assert _result(record, "handoff_correct") == prove.PASS
    assert _result(record, "goal_reaches_model") == prove.PASS
    # The sources are still not downloaded: that is true of the healthy build
    # too, and the whole point is that it is SAID.
    assert _result(record, "sources_honest") == prove.GAP
    assert record["verdict"] == prove.GAP
    assert all(c["result"] != prove.FAIL for c in record["checks"])


def test_everything_finished_prints_one_line_only(tmp_path):
    surface = persona_scaffold.persona_surface("infinity")
    made = _write_persona_files("infinity", "Infinity", surfaces=[surface])
    record = prove.static_prove(
        manifest=_manifest(made, [surface]),
        sources=_sources_all_done(),
        launchers=_launchers(tmp_path, present=True),
        goal=GOAL, bot_name="Infinity")
    assert record["verdict"] == prove.PASS
    assert record["gaps"] == []
    assert prove.closing_lines(record) == [prove.BEGIN_MARK, prove.RUN_LINE,
                                           prove.END_MARK]
    assert prove.strip_block(prove.closing_lines(record)) == []


def test_a_build_with_no_persona_is_not_a_failure(tmp_path):
    record = prove.static_prove(
        manifest={"schema": "build_manifest/v2"},
        sources=_sources_all_done(),
        launchers=_launchers(tmp_path, present=True),
        goal="", bot_name="")
    assert _result(record, "id_sanity") == prove.NOT_APPLICABLE
    assert _result(record, "persona_visible") == prove.NOT_APPLICABLE
    assert _result(record, "goal_reaches_model") == prove.NOT_APPLICABLE
    assert record["verdict"] == prove.PASS


def test_the_shape_the_wizard_really_writes_is_the_shape_prove_reads(tmp_path):
    """The HIGH defect: prove keyed persona checks on a key nobody writes.

    The block here is built by the wizard's own builder, so it carries ``app``
    and ``pack`` and no ``id`` at all. Before the fix every check below was
    ``not_applicable`` with the reason "This build made no persona", on every
    real build.
    """
    surface = persona_scaffold.persona_surface("infinity")
    made = _write_persona_files("infinity", "Infinity", surfaces=[surface])
    manifest = _manifest(made, [surface])
    assert "id" not in manifest["persona"]
    assert manifest["persona"]["app"] == "infinity"

    record = prove.static_prove(manifest=manifest, sources=_sources_all_done(),
                                launchers=_launchers(tmp_path, present=True),
                                goal=GOAL, bot_name="Infinity")
    assert record["persona_id"] == "infinity"
    for check in ("id_sanity", "persona_visible", "goal_reaches_model"):
        assert _result(record, check) == prove.PASS, check
    assert not any("made no persona" in c["reason"] for c in record["checks"])


def test_a_goal_with_no_place_still_has_to_reach_the_model(tmp_path):
    """The second HIGH defect: --yes records a goal and scaffolds nothing.

    The persona checks are honestly not applicable -- there is no folder and no
    page -- and the goal check is not: the goal is recorded where
    ``build_context.collect_goal`` reads it, so it applies and it passes.
    """
    record = prove.static_prove(
        manifest=_goal_only_manifest("Infinity"),
        sources=_sources_all_done(),
        launchers=_launchers(tmp_path, present=True))
    assert _result(record, "id_sanity") == prove.NOT_APPLICABLE
    assert _result(record, "persona_visible") == prove.NOT_APPLICABLE
    assert _result(record, "goal_reaches_model") == prove.PASS
    assert record["verdict"] == prove.PASS


def test_an_old_record_that_says_id_is_still_read(tmp_path):
    """One record shape is real; the other is tolerated, not required."""
    surface = persona_scaffold.persona_surface("infinity")
    _write_persona_files("infinity", "Infinity", surfaces=[surface])
    record = prove.static_prove(
        manifest={"schema": "build_manifest/v2",
                  "persona": {"id": "infinity", "name": "Infinity",
                              "goal": GOAL},
                  "surfaces": {"allowlist": [surface]}},
        sources=_sources_all_done(),
        launchers=_launchers(tmp_path, present=True),
        goal=GOAL, bot_name="Infinity")
    assert _result(record, "id_sanity") == prove.PASS


def test_an_untrimmed_build_cannot_hide_the_persona(tmp_path):
    made = _write_persona_files("infinity", "Infinity", surfaces=[])
    record = prove.static_prove(
        manifest=_manifest(made, []),
        sources=_sources_all_done(),
        launchers=_launchers(tmp_path, present=True),
        goal=GOAL, bot_name="Infinity")
    assert _result(record, "persona_visible") == prove.PASS


def test_no_pointer_and_no_puffpai_on_path_is_a_gap(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    record = prove.static_prove(manifest={}, sources=_sources_all_done(),
                                launchers=None)
    assert _result(record, "handoff_correct") == prove.GAP


# ---------------------------------------------------------------------------
# The live half, against a stub backend
# ---------------------------------------------------------------------------

class _Stub(BaseHTTPRequestHandler):
    """Answers the three routes Prove calls, and counts what it was asked."""

    reply = "I am Infinity, your space bot."
    personas = [{"id": "infinity", "display_name": "Infinity", "apps": ["infinity"]}]
    counts: dict = {}
    keys_seen: list = []

    def log_message(self, *args):  # noqa: D102 - quiet test server
        return

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        _Stub.counts[self.path] = _Stub.counts.get(self.path, 0) + 1
        _Stub.keys_seen.append(self.headers.get("X-API-Key"))
        if self.path == "/persona/list":
            return self._json({"personas": _Stub.personas, "apps": []})
        if self.path == "/knowledge/sources":
            return self._json({"success": True, "counts": {
                build_sources.STATUS_SELECTED_NOT_DOWNLOADED: 0,
                build_sources.STATUS_FETCHED_NOT_INDEXED: 0,
                build_sources.STATUS_INDEXED: 1,
                build_sources.STATUS_OFFERED: 0}})
        return self._json({"error": "no"}, 404)

    #: What a healthy backend answers when it is asked to prove itself: the
    #: static half ran THERE, so the bot name in it is the running bot's.
    static_payload: dict = {}
    bodies_seen: list = []

    def do_POST(self):  # noqa: N802
        _Stub.counts[self.path] = _Stub.counts.get(self.path, 0) + 1
        _Stub.keys_seen.append(self.headers.get("X-API-Key"))
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if self.path.startswith("/build/prove"):
            _Stub.bodies_seen.append((self.path, raw.decode("utf-8", "replace")))
            if self.path == "/build/prove/owner":
                return self._json({"ok": True, "owner": "owner_unproven"})
            payload = dict(_Stub.static_payload or _healthy_static_payload())
            if self.path == "/build/prove/live":
                payload["live"] = _healthy_live_payload(_Stub.reply)
            return self._json(payload)
        return self._json({"response": _Stub.reply, "conversation_id": "x"})


def _healthy_static_payload() -> dict:
    """The record a healthy running "Infinity" answers with, server-side."""
    return {
        "ran": True, "verdict": prove.PASS, "static_at": "now",
        "bot_name": "Infinity", "persona_id": "infinity",
        "checks": [
            {"id": "id_sanity", "title": "The bot's id is its own",
             "result": prove.PASS, "reason": "'infinity' is the bot's own word."},
            {"id": "persona_visible", "title": "The bot can be shown",
             "result": prove.PASS, "reason": "its surface is in the list."},
            {"id": "sources_honest", "title": "The knowledge it was promised",
             "result": prove.PASS, "reason": "everything is indexed."},
        ],
        "gaps": [], "live": None,
    }


def _healthy_live_payload(reply: str) -> dict:
    named = "infinity" in reply.lower()
    return {
        "ran": True, "at": "now", "note": "", "owner": prove_live.OWNER_UNPROVEN,
        "dashboard_url": "http://127.0.0.1:3003",
        "checks": [
            {"id": "served_persona", "title": "The running bot is served",
             "result": prove.PASS, "reason": "the backend serves 'infinity'."},
            {"id": "answers_as_itself", "title": "It answers as itself",
             "result": prove.PASS if named else prove.FAIL,
             "reason": f"Asked who it is, it {'said' if named else 'did not say'} "
                       f"Infinity."},
        ],
        "transcript": [{"question": prove.IDENTITY_QUESTION, "reply": reply,
                        "verdict": prove.PASS if named else prove.FAIL}],
    }


@pytest.fixture
def stub():
    _Stub.counts = {}
    _Stub.keys_seen = []
    _Stub.bodies_seen = []
    _Stub.static_payload = {}
    _Stub.reply = "I am Infinity, your space bot."
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _Stub
    finally:
        server.shutdown()
        server.server_close()


def _static() -> dict:
    return {"schema": prove.SCHEMA, "persona_id": "infinity",
            "bot_name": "Infinity",
            "checks": [{"id": "sources_honest", "title": "t",
                        "result": prove.PASS, "reason": "r"}],
            "gaps": [], "verdict": prove.PASS, "live": None}


def test_live_pass_when_the_bot_answers_to_its_name(stub, capsys):
    url, handler = stub
    live = prove_live.live_prove(_static(), url=url, api_key="k" * 64,
                                 goal=GOAL, timeout=10)
    assert live["ran"] is True
    results = {c["id"]: c["result"] for c in live["checks"]}
    assert results["served_persona"] == prove.PASS
    assert results["served_sources"] == prove.PASS
    assert results["answers_as_itself"] == prove.PASS
    assert live["transcript"][0]["question"] == prove.IDENTITY_QUESTION
    # Three generations at most, ever.
    assert live["chat_calls"] <= prove.MAX_CHAT_CALLS
    assert handler.counts["/chat/send"] == live["chat_calls"]
    # The knowledge answers are transcript, never graded.
    assert [t["verdict"] for t in live["transcript"][1:]] == \
        ["transcript_only"] * (len(live["transcript"]) - 1)
    # Non-interactive: nothing about the browser is claimed.
    assert live["owner"] == prove_live.OWNER_UNPROVEN
    # The key travelled as a header and was never printed.
    assert handler.keys_seen and all(k == "k" * 64 for k in handler.keys_seen)
    assert "k" * 64 not in json.dumps(live)
    assert "k" * 64 not in capsys.readouterr().out


def test_live_fails_when_the_reply_is_a_generic_assistant(stub):
    url, handler = stub
    handler.reply = "I am a helpful AI assistant. How can I help you today?"
    live = prove_live.live_prove(_static(), url=url, api_key="", goal=GOAL,
                                 timeout=10)
    results = {c["id"]: c["result"] for c in live["checks"]}
    assert results["answers_as_itself"] == prove.FAIL
    assert live["transcript"][0]["verdict"] == prove.FAIL
    assert "helpful AI assistant" in live["transcript"][0]["reply"]


def test_live_fails_when_the_backend_does_not_serve_the_persona(stub):
    url, handler = stub
    handler.personas = [{"id": "apps", "display_name": "apps pack"}]
    try:
        live = prove_live.live_prove(_static(), url=url, api_key="", goal=GOAL,
                                     timeout=10)
    finally:
        handler.personas = [{"id": "infinity", "display_name": "Infinity"}]
    results = {c["id"]: c["result"] for c in live["checks"]}
    assert results["served_persona"] == prove.FAIL


def test_live_says_so_when_nothing_is_listening():
    # Port 1 on loopback: nothing binds it, and nothing is started here.
    live = prove_live.live_prove(_static(), url="http://127.0.0.1:1",
                                 api_key="", goal=GOAL, timeout=2)
    assert live["ran"] is False
    assert live["note"].startswith("not run: backend not answering at "
                                   "http://127.0.0.1:1")
    assert live["checks"] == [] and live["transcript"] == []
    assert live["owner"] == prove_live.OWNER_UNPROVEN


def test_the_owner_answer_is_recorded_not_assumed(stub):
    url, _ = stub
    for answer, expected in (("y", prove_live.OWNER_CONFIRMED),
                             ("n", prove_live.OWNER_REFUTED),
                             ("skip", prove_live.OWNER_UNPROVEN),
                             ("", prove_live.OWNER_UNPROVEN)):
        live = prove_live.live_prove(_static(), url=url, api_key="", goal=GOAL,
                                     timeout=10, ask_owner=lambda q, a=answer: a)
        assert live["owner"] == expected
        assert "left menu" in live["owner_question"]


def test_the_questions_come_from_the_goal_not_from_a_model():
    asked = prove_live.questions(GOAL)
    assert len(asked) <= prove.MAX_CHAT_CALLS - 1
    assert all(q.startswith("What do you know about ") for q in asked)
    assert prove_live.questions(GOAL) == asked   # deterministic
    assert prove_live.questions("") == []


# ---------------------------------------------------------------------------
# The record, and the route that reads it
# ---------------------------------------------------------------------------

def test_the_record_lands_in_the_runs_own_store(tmp_path):
    class Cfg:
        CONFIG_DIR = str(tmp_path / "config")

    assert prove.read_record(Cfg) == {}
    record = prove.static_prove(manifest={}, sources=_sources_all_done(),
                                launchers=None)
    path = prove.write_record(record, Cfg)
    assert os.path.basename(path) == "wizard_run.json"
    with open(path, encoding="utf-8") as handle:
        assert json.load(handle)["sections"]["prove"]["schema"] == prove.SCHEMA
    assert prove.read_record(Cfg)["verdict"] == record["verdict"]


def test_the_build_prove_route_answers_before_and_after(tmp_path, monkeypatch):
    from puffpai import api_server

    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}

    monkeypatch.setattr(prove, "read_record", lambda cfg=None: {})
    never = client.get("/build/prove", headers=auth)
    assert never.status_code == 200
    assert never.get_json()["ran"] is False
    assert never.get_json()["message"] == "Not proven yet: run puffpai prove"

    made = _write_persona_files(
        "infinity", "Infinity",
        surfaces=[persona_scaffold.persona_surface("infinity")])
    record = prove.static_prove(
        manifest=_manifest(made, BROKEN_ALLOWLIST),
        sources=_sources_nothing_downloaded(),
        launchers=_launchers(tmp_path, present=False),
        goal=GOAL, bot_name="Infinity")
    record["live"] = {"ran": True, "owner": prove_live.OWNER_UNPROVEN,
                      "checks": [], "transcript": [
                          {"question": "q", "reply": "a", "verdict": "pass"}],
                      "at": "now", "note": "", "dashboard_url": ""}
    monkeypatch.setattr(prove, "read_record", lambda cfg=None: record)

    after = client.get("/build/prove", headers=auth).get_json()
    assert after["ran"] is True
    assert after["verdict"] == prove.FAIL
    assert any(c["result"] == prove.FAIL for c in after["checks"])
    assert all(g["fix_command"] for g in after["gaps"])
    assert after["live"]["owner"] == prove_live.OWNER_UNPROVEN
    assert after["live"]["transcript"][0]["reply"] == "a"


def test_the_command_runs_end_to_end_and_grades_the_running_build(stub, capsys,
                                                                  monkeypatch,
                                                                  tmp_path):
    """``puffpai prove`` against a stub backend: no model, no real server.

    The HIGH defect: the static half ran in the CALLER's shell,
    so a healthy bot was graded against whatever was on the machine the command
    was typed on - "This build scaffolded no place of its own" and "it did not
    say its name" printed directly above a transcript of the bot saying its
    name - and a ``wizard_run.json`` was dropped into the caller's config dir.

    Here the caller's state is an EMPTY temp dir: nothing local could make
    these checks pass, so a pass is the backend's own record and nothing else.
    """
    from puffpai import cli

    url, handler = stub
    key = "cd" * 32
    empty = tmp_path / "caller_state"
    empty.mkdir()
    monkeypatch.setenv("PUFFPAI_API_KEY", key)
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(empty))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(empty))
    monkeypatch.chdir(tmp_path)
    from puffpai.config import get_config
    get_config(_force_reload=True)
    before = sorted(os.listdir(tmp_path))

    code = cli.main(["prove", "--url", url, "--yes", "--timeout", "10"])
    out = capsys.readouterr().out

    assert code == 0, out
    assert "Proof it works" in out
    assert "It answers as itself: Asked who it is, it said Infinity" in out
    assert "scaffolded no place of its own" not in out
    assert key not in out, "the API key must never reach a console"
    assert "not run: backend not answering" not in out
    assert handler.counts.get("/build/prove/static") == 1
    assert handler.counts.get("/build/prove/live") == 1

    # Nothing of the caller's was written: not in the cwd, not in the config
    # dir, which is the build being proved's business and not this shell's.
    assert sorted(os.listdir(tmp_path)) == before
    assert sorted(os.listdir(empty)) == []
    assert prove.read_record(get_config()) == {}
    assert key not in json.dumps(handler.bodies_seen)


def test_an_older_backend_is_a_sentence_not_a_traceback(stub, capsys,
                                                        monkeypatch, tmp_path):
    """A build from before these routes answers 404. That is a plain message."""
    from puffpai import cli

    url, handler = stub
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    from puffpai.config import get_config
    get_config(_force_reload=True)

    class _Old(type(handler)):
        pass

    def refuse(self):
        _Stub.counts[self.path] = _Stub.counts.get(self.path, 0) + 1
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self._json({"error": "no such route"}, 404)

    monkeypatch.setattr(handler, "do_POST", refuse)
    code = cli.main(["prove", "--url", url, "--yes", "--timeout", "10"])
    out = capsys.readouterr().out
    assert code == 2
    assert cli.PROVE_OLD_BACKEND in out
    assert "Traceback" not in out


def test_the_command_exits_non_zero_when_nothing_is_listening(capsys):
    from puffpai import cli

    assert cli.main(["prove", "--url", "http://127.0.0.1:1", "--yes",
                     "--timeout", "2"]) == 2
    assert "not run: backend not answering" in capsys.readouterr().out


def test_the_route_is_behind_the_api_key():
    from puffpai import api_server

    api_server.app.config.update(TESTING=True)
    assert api_server.app.test_client().get("/build/prove").status_code == 401


# ---------------------------------------------------------------------------
# a stored gap is data, and a command line is not
# ---------------------------------------------------------------------------

#: What an edited run record could carry: a fix naming a command this build does
#: not own, and a pre-rendered string hoping somebody prints it.
PLANTED_GAP = {
    "skill": "integrate",
    "what": "Two packs were chosen\x1b[2K and never downloaded.",
    "why": "planted",
    "impact": "planted",
    "fix": {"kind": "command", "id": "os.system",
            "args": {"cmd": "rm -rf / # planted"}},
    "fix_command": "rm -rf / # planted",
    "blocking": False,
    "recorded_at": "now",
}


def _plant_record(tmp_path, monkeypatch) -> dict:
    """Write a run record whose prove section carries the planted gap."""
    from puffpai.config import get_config

    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    cfg = get_config(_force_reload=True)
    record = {"schema": prove.SCHEMA, "verdict": prove.GAP,
              "checks": [{"id": "sources_honest", "title": "t",
                          "result": prove.GAP, "reason": "r"}],
              "gaps": [dict(PLANTED_GAP)], "live": None}
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    with open(os.path.join(cfg.CONFIG_DIR, "wizard_run.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"schema": "wizard_run/v1",
                   "sections": {"prove": record}}, handle)
    return record


def test_a_planted_fix_never_comes_back_out_of_the_record(tmp_path, monkeypatch):
    _plant_record(tmp_path, monkeypatch)
    record = prove.read_record()
    assert record["gaps"] == [], "a gap whose fix does not validate is dropped"
    assert "rm -rf" not in json.dumps(record)


def test_the_closing_screen_renders_the_fix_and_never_echoes_it():
    lines = prove.closing_lines({"checks": [], "gaps": [dict(PLANTED_GAP)]})
    text = "\n".join(lines)
    assert "rm -rf" not in text
    assert "\x1b" not in text, "control characters are dropped, not printed"
    # Degraded to the one command that is always true.
    assert "puffpai setup" in text


def test_the_build_prove_route_never_serves_a_planted_command(tmp_path,
                                                              monkeypatch):
    from puffpai import api_server

    _plant_record(tmp_path, monkeypatch)
    client = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}
    payload = client.get("/build/prove", headers=auth).get_json()
    assert payload["ran"] is True
    assert payload["gaps"] == []
    assert "rm -rf" not in json.dumps(payload)


# ---------------------------------------------------------------------------
# prove talks to this machine, or to nothing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://example.com",
    "http://user:secret@127.0.0.1:8000",
    "file:///etc/passwd",
    "ftp://127.0.0.1",
    "127.0.0.1:8000",
    "http://127.0.0.1:8000/?redirect=http://example.com",
    "http://",
    "",
])
def test_prove_refuses_an_address_that_is_not_this_machine_or_this_network(url):
    with pytest.raises(prove_live.NotLocal):
        prove_live.check_local_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/", "http://[::1]:8000",
                                 "http://localhost:8000"])
def test_prove_accepts_loopback(url):
    assert prove_live.check_local_url(url).startswith("http://")


def test_a_public_url_is_refused_before_anything_is_sent(monkeypatch):
    def never(*a, **k):
        raise AssertionError("prove reached the network for a public address")

    monkeypatch.setattr(prove_live, "_request", never)
    live = prove_live.live_prove({"persona_id": "infinity"},
                                 url="http://example.com", api_key="k")
    assert live["ran"] is False
    assert "not run:" in live["note"]


def test_a_redirect_is_never_followed():
    handler = prove_live._NoRedirects()
    assert handler.redirect_request(None, None, 302, "Found", {},
                                    "http://example.com") is None
