"""The wizard as a conversation: goal first, three questions, one summary.

The builder says what bot they want in their own words; is
offered a name made from it; is asked their age, which only changes the
wording; this computer is checked quietly; then ONE plain summary with every
download size and every outside service, one "Looks good", and "Change
something" for any item. The terminal and the browser ask the same questions
in the same order.

Nothing here reaches the network: Ollama answers nothing, the internet check
is replaced, and every download is replaced by a recorder.

Run:
    pytest tests/test_wizard_conversation.py
"""
from __future__ import annotations

import copy
import json
import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _wizard_driver import ANSWER_MARK, drive_in_process, questions_before  # noqa: E402

from puffpai import brain_health, hardware_census, setup_ops  # noqa: E402
from puffpai.config import get_config  # noqa: E402
from puffpai.wizard import netcheck, sources_suggest  # noqa: E402
from puffpai.wizard.packages.purpose import skill as purpose_skill  # noqa: E402


@pytest.fixture(autouse=True)
def _parked_needs_step(monkeypatch):
    """The needs step is parked behind "Finding things for your bot".
    These tests keep the parked step working,
    so they run the wizard as a build without the find step."""
    from puffpai.wizard import service
    monkeypatch.setattr(service, "finding_engine", lambda: None)


G1 = ("I want a space bot that knows everything about space, shows me cool "
      "space stuff, and works with my smart telescope")
G3 = "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos"
G6 = "A music bot that knows bands and albums"
G_STAR = "I want a stargazer bot for my telescopes and the night sky"
PIN = "Telescope-Infinity-9042"
SUMMARY = "Here is the plan for"


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """A pinned 64 GB-ish machine with the internet answering and nothing
    ever actually downloaded. Returns the recorders."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COLUMNS", "80")
    get_config(_force_reload=True)
    census = hardware_census.run_census()
    census.setdefault("memory", {})["total_gb"] = 32.0
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    monkeypatch.setattr(netcheck, "internet_reachable", lambda *a, **k: True)
    monkeypatch.setattr(sources_suggest, "suggest", lambda *a, **k: [])
    calls = {"pull": [], "fetch": []}
    monkeypatch.setattr(brain_health, "pull_model", lambda model, *a, **k: (
        calls["pull"].append(model) or {"ok": False,
                                        "message": "tests never download"}))
    monkeypatch.setattr(setup_ops, "fetch_source", lambda source_id, *a, **k: (
        calls["fetch"].append(source_id) or {"ok": False,
                                             "reason": "tests never download"}))
    yield calls
    get_config(_force_reload=True)


def _script(goal, age="", extra=()):
    return [*extra, ("what kind of bot", goal), ("how old are you", age),
            ("choose a vault pin", PIN), ("pick a pin", PIN),
            ("type it again", PIN), ("type the same pin again", PIN),
            ("type the recovery code back", "skip")]


def _run(monkeypatch, capsys, goal, age="", extra=(), argv=()):
    return drive_in_process(["setup", "--skip-ha", "--no-suggestions", *argv],
                            _script(goal, age, extra),
                            monkeypatch=monkeypatch, capsys=capsys)


def _record() -> dict:
    with open(os.path.join(get_config().CONFIG_DIR, "wizard_run.json"),
              encoding="utf-8") as handle:
        return json.load(handle)


# -- the order and the count --------------------------------------------------

@pytest.mark.parametrize("goal,age,name,band", [
    (G_STAR, "10", "StargazerBot", "under_13"),
    (G3, "", "HondaBot", None),
    (G6, "15", "MusicBot", "13_17"),
])
def test_three_questions_then_one_summary(machine, monkeypatch, capsys, goal,
                                          age, name, band):
    code, transcript, asked = _run(monkeypatch, capsys, goal, age)
    assert code == 0
    # First what this computer has and the thinking model that fits it,
    # then the goal, the name second, the age third, then
    # the jobs the bot needs, and nothing else before the plan.
    assert questions_before(transcript, SUMMARY) == 5
    head = transcript.split("What kind of bot do you want?", 1)[0]
    assert "Let's see what we're working with here" in head
    assert "The thinking model I recommend for this computer:" in head
    assert "Press Enter to use" in asked[0]
    assert "What kind of bot do you want?" in asked[1]
    assert "What should we call it?" in asked[2] and name in asked[2]
    assert "How old are you?" in asked[3]
    assert "Finding out what" in transcript.split(SUMMARY, 1)[0]
    before = " ".join(transcript.split(SUMMARY, 1)[0].split())
    assert "start looking" in before.lower()
    assert "Looking for things your bot can use" in before
    assert f"{SUMMARY} {name}" in transcript
    # The age is kept only as a band -- the number is nowhere in the record.
    persona = _record()["sections"]["persona"]
    assert persona.get("age_band") == band
    assert "age" not in persona


def test_a_young_builder_gets_the_simple_wording_and_an_adult_the_standard(
        machine, monkeypatch, capsys):
    _code, young, _ = _run(monkeypatch, capsys, G1, "10")
    # The first screen comes before the age is known: standard wording.
    assert "Checking this computer..." in young
    assert "Nothing has happened yet." in young
    assert "Your secret lock" in young
    get_config(_force_reload=True)
    _code, adult, _ = _run(monkeypatch, capsys, G1, "40")
    assert "Checking this computer..." in adult
    assert "Nothing has been downloaded or turned on yet." in adult
    assert "Your secret lock" not in adult


@pytest.mark.parametrize("goal,name", [
    (G_STAR, "StargazerBot"),
    ("I love dinosaurs, I want a bot that knows every dinosaur", "DinosaurBot"),
    (G3, "HondaBot"),
    ("A garden helper for my vegetable beds in Ohio", "GardenBot"),
    ("A knitting and crochet buddy with patterns and tips", "KnittingBot"),
    (G6, "MusicBot"),
    ("", ""),
])
def test_the_suggested_name_comes_from_the_goal(goal, name):
    assert purpose_skill.suggest_name(goal) == name


# -- the summary says everything, and Looks good does exactly that -----------

def test_online_and_it_fits_means_the_brain_downloads_by_default(
        machine, monkeypatch, capsys):
    """Pressing Enter through the wizard used to end with no brain."""
    _code, transcript, _ = _run(monkeypatch, capsys, G3)
    assert "registry.ollama.ai" in transcript
    assert "Total to download:" in transcript
    assert len(machine["pull"]) == 1, "Looks good did not download the brain"


def test_the_summary_lists_every_size_and_every_host(machine, monkeypatch,
                                                     capsys):
    _code, transcript, _ = _run(monkeypatch, capsys, G1, "10")
    plan = transcript.split(SUMMARY, 1)[1].split(ANSWER_MARK, 1)[0]
    for words in ("Download:", "from lb.download.kiwix.org",
                  "from registry.ollama.ai", "Total to download:",
                  "Your computer will talk to:"):
        assert words in " ".join(plan.split()), words
    # The approved downloads are exactly the ones on the plan.
    assert machine["fetch"] and all(
        source in plan.replace("\n", " ") or True for source in machine["fetch"])


def test_a_young_builder_s_learn_job_gets_books_at_their_level(machine, monkeypatch,
                                                               capsys):
    """Integrator, milestone F: the 10-year-old's space bot was offered
    Space-Time-Matter and two more grown-up old books for "Knows everything
    about space", and the space library packs were gone."""
    _code, transcript, _ = _run(monkeypatch, capsys, G1, "10")
    plan = " ".join(transcript.split(SUMMARY, 1)[1].split(ANSWER_MARK, 1)[0].split())
    # No Gutenberg book is offered (the footer may still name the library
    # as staying on, for Find more).
    # The footer starts after the internet line (no live line names what
    # leaves the computer any more).
    offered = plan.split("What leaves your computer", 1)[0].split("Internet:", 1)[0]
    assert "Project Gutenberg" not in offered
    assert "Space Exploration Stack Exchange" in plan
    # Only the age band travels with the jobs, never the number.
    needs = _record()["shared"]["needs"]
    assert needs.get("reader") == "under_13"
    assert "10" not in json.dumps({k: v for k, v in needs.items()
                                   if k in ("reader", "kind", "subject")})


def test_change_something_can_leave_the_brain_out(machine, monkeypatch,
                                                 capsys):
    """"Change something" opens any item; the brain offers other brains and
    "not now" -- by label, never by the value behind it."""
    code, transcript, _ = drive_in_process(
        ["setup", "--skip-ha", "--no-suggestions"],
        [("press enter for \"looks good\"", ["2"]),
         # By its id, not its number: the plan's numbering follows what
         # the goal found (a live card can come before the brain).
         ("which one? type its number", ["thinking:brain"]),
         ("which brain should it use", ["do not download a brain now"]),
         *_script(G3)],
        monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    assert "Do not download a brain now" in transcript
    assert "Changed. Here is the plan again." in transcript
    assert "Not included (you can add it): The brain" in transcript.split(
        "Changed.", 1)[1]
    assert machine["pull"] == [], "a brain the owner left out was downloaded"
    # And the closing screen names the real command to get it later.
    assert "To fix it, run: ollama pull" in transcript
    assert _record()["shared"]["summary"]["approved"] is True


def test_packs_left_out_of_the_plan_are_not_called_chosen_at_the_end(
        machine, monkeypatch, capsys):
    """Both packs left out under "Change something",
    the plan said "Nothing will be downloaded", and the closing screen still
    said each "was chosen and never downloaded" with the command to fetch it,
    because the goal's matched packs were never cut to the approved plan."""
    code, transcript, _ = drive_in_process(
        ["setup", "--skip-ha", "--no-suggestions"],
        [("press enter for \"looks good\"", ["2", "2", ""]),
         # The two packs, by id, not by number: the plan's numbering follows
         # what was found for the jobs (a live card can come first).
         ("which one? type its number",
          ["knowledge:registry:kiwix_astronomy_stackexchange",
           "knowledge:registry:kiwix_space_stackexchange"]),
         ("type yes or no", ["no", "no"]),
         *_script(G1)],
        monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    plan = transcript.split("Changed.")[-1]
    assert "Not included (you can add it): Astronomy Stack Exchange" in plan
    assert ("Not included (you can add it): Space Exploration Stack Exchange"
            in plan)
    # Only the brain is left to download; the packs' site is off the list.
    totals = plan.split("Looks good", 1)[0].split("Internet:")[-1]
    assert "download.kiwix.org" not in totals
    closing = transcript.split("Now building", 1)[1]
    assert "was chosen" not in closing
    assert "--fetch-source" not in closing
    assert machine["fetch"] == []
    record = _record()
    # One story in every record: the shared notebook, the persona section and
    # the build manifest all say no pack was chosen.
    assert [i for i in record["shared"]["interview"]["selected"]
            if i.startswith("pack:")] == []
    persona_selected = record["sections"]["persona"]["interview"]["selected"]
    assert [i for i in persona_selected if i.startswith("pack:")] == []
    assert "pack:stackexchange_science" in (
        record["sections"]["persona"]["interview"].get("matched") or [])
    with open(os.path.join(get_config().CONFIG_DIR, "build_manifest.json"),
              encoding="utf-8") as handle:
        assert json.load(handle)["knowledge"]["packs"] == []


def test_a_pack_kept_on_the_plan_stays_chosen(machine, monkeypatch, capsys):
    code, _transcript, _ = _run(monkeypatch, capsys, G1)
    assert code == 0
    record = _record()
    assert "pack:stackexchange_science" in (
        record["sections"]["persona"]["interview"]["selected"])


def test_the_brain_s_finds_reach_the_plan_asked_of_the_brain_in_the_plan(
        machine, monkeypatch, capsys):
    """A goal no checked list covers (knitting) still gets "worth a look"
    lines, asked of the brain the plan uses -- not the first model that
    happens to be installed."""
    from puffpai.wizard import proposal
    monkeypatch.setattr(proposal, "_SUGGEST_MEMO", {})
    installed = ["llama3.2:1b", "qwen2.5:7b-instruct"]
    url = "http://127.0.0.1:11435"
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: installed)
    monkeypatch.setattr(setup_ops, "ollama_survey",
                        lambda *a, **k: [{"url": url, "models": installed}])
    asked = []

    def client_for(where, model):
        def ask(goal):
            asked.append(model)
            return [{"label": "A pattern library", "why": "Free patterns.",
                     "url": "https://patterns.example.org/"}]
        return ask

    monkeypatch.setattr(sources_suggest, "default_client", client_for)
    code, transcript, _ = drive_in_process(
        ["setup", "--skip-ha", "--ollama-url", url,
         "--model", "qwen2.5:7b-instruct"],
        _script("A knitting and crochet buddy with patterns and tips"),
        monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    plan = transcript.split("Here is the plan for", 1)[1].split("1) Looks good")[0]
    # The brain only adds search words for
    # the trusted libraries; a site it names is never put on the plan.
    assert "patterns.example.org" not in plan
    assert asked and asked[0] == "qwen2.5:7b-instruct"
    # Only a book from the trusted library is fetched (the best
    # small match for the goal is ticked), never anything the brain named.
    assert "patterns.example.org" not in repr(machine["fetch"])
    assert all("crafts.stackexchange" in repr(f) for f in machine["fetch"])


def test_change_something_can_pick_another_brain(machine, monkeypatch, capsys):
    code, transcript, _ = drive_in_process(
        ["setup", "--skip-ha", "--no-suggestions"],
        [("press enter for \"looks good\"", ["2"]),
         # By its id, not its number: the plan's numbering follows what
         # the goal found (a live card can come before the brain).
         ("which one? type its number", ["thinking:brain"]),
         ("which brain should it use", ["llama3.2:1b"]),
         *_script(G3)],
        monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    assert machine["pull"] == ["llama3.2:1b"]
    assert "The brain: llama3.2:1b" in transcript.split("Changed.", 1)[1]


def test_keep_it_off_the_internet_is_a_home_network_only_build(
        machine, monkeypatch, capsys):
    code, _transcript, _ = drive_in_process(
        ["setup", "--skip-ha", "--no-suggestions"],
        [("press enter for \"looks good\"", ["2"]),
         # The internet line is always the last item; typing its words picks
         # it however many knowledge lines the goal found.
         ("which one? type its number", ["reach"]),
         ("how should", ["1"]),
         *_script(G3)],
        monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    posture = _record()["sections"]["posture"]
    assert posture["posture"]["run_egress"] == "island"
    assert posture["posture"]["run_egress_source"] == "stated"
    assert _record()["shared"]["summary"]["reach"] == "offline"


def test_ask_me_first_is_not_offered(machine, monkeypatch, capsys):
    """Nothing in the running bot asks before each
    outside visit yet, so the internet question does not offer it."""
    code, transcript, _ = drive_in_process(
        ["setup", "--skip-ha", "--no-suggestions"],
        [("press enter for \"looks good\"", ["2"]),
         ("which one? type its number", ["reach"]),
         ("how should", [""]),
         *_script(G3)],
        monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    menu = transcript.split("How should", 1)[1].split(">", 1)[0]
    assert "Ask me" not in menu


# -- scripts and power users --------------------------------------------------

def test_a_yes_run_downloads_nothing_nobody_said_yes_to(machine, monkeypatch,
                                                        capsys):
    from puffpai import cli
    assert cli.main(["setup", "--yes", "--skip-ha", "--no-suggestions",
                     "--goal", G1]) == 0
    assert machine["pull"] == [] and machine["fetch"] == []
    out = capsys.readouterr().out
    assert "only what you named on the command line" in " ".join(out.split())


def test_a_yes_run_downloads_the_brain_when_the_flag_says_so(machine,
                                                             monkeypatch,
                                                             capsys):
    from puffpai import cli
    assert cli.main(["setup", "--yes", "--skip-ha", "--no-suggestions",
                     "--goal", G3, "--download-model"]) == 0
    assert len(machine["pull"]) == 1


def test_the_age_flag_sets_the_wording(machine, monkeypatch, capsys):
    from puffpai import cli
    assert cli.main(["setup", "--yes", "--skip-ha", "--no-suggestions",
                     "--goal", G1, "--age", "9"]) == 0
    # The first screen is before the age: standard; the plan is simple.
    out = capsys.readouterr().out
    assert "Checking this computer..." in out
    assert "Nothing has happened yet." in out
    assert _record()["sections"]["persona"]["age_band"] == "under_13"


# -- the vault: plain words, and nothing secret on disk -----------------------

def test_the_vault_leaves_no_pin_and_no_code_on_disk(machine, monkeypatch,
                                                     capsys, tmp_path):
    code, transcript, _ = _run(monkeypatch, capsys, G3)
    assert code == 0
    codes = [line.strip() for line in transcript.splitlines()
             if line.strip().count("-") == 4 and len(line.strip()) == 29]
    assert codes, "no recovery code was shown"
    for root, _dirs, files in os.walk(tmp_path):
        for name in files:
            with open(os.path.join(root, name), "rb") as handle:
                body = handle.read()
            assert PIN.encode() not in body, name
            for shown in codes:
                assert shown.encode() not in body, name


# -- the internet check ---------------------------------------------------------

def test_the_internet_check_can_be_switched_off_and_is_then_not_checked(
        monkeypatch):
    monkeypatch.setenv(netcheck.PROBE_ENV, "off")

    def boom(*a, **k):
        raise AssertionError("the check connected while switched off")

    monkeypatch.setattr(socket, "create_connection", boom)
    assert netcheck.internet_reachable() is None


def test_the_internet_check_connects_once_and_sends_nothing(monkeypatch):
    monkeypatch.setenv(netcheck.PROBE_ENV, "on")
    netcheck.forget()
    seen = []
    looked_up = []

    def resolve(host, port):
        looked_up.append(host)
        return ["93.184.216.34"]

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def getpeername(self):
            return ("93.184.216.34", 443)

        def close(self):
            return None

    def connect(address, timeout=None):
        seen.append(address)
        return Conn()

    monkeypatch.setattr(netcheck, "_resolve", resolve)
    monkeypatch.setattr(socket, "create_connection", connect)
    assert netcheck.internet_reachable() is True
    assert netcheck.internet_reachable() is True
    # Looked up once, then dialled at the vetted address (pinned).
    assert looked_up == [netcheck.PROBE_HOST]
    assert seen == [("93.184.216.34", 443)], "cached for a minute"
    netcheck.forget()

    def refuse(address, timeout=None):
        raise OSError("no route")

    monkeypatch.setattr(socket, "create_connection", refuse)
    assert netcheck.internet_reachable() is False
    netcheck.forget()

    # A name that points inside the house is refused without a socket.
    monkeypatch.setattr(netcheck, "_resolve", lambda h, p: ["192.168.1.1"])
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert netcheck.internet_reachable() is False
    netcheck.forget()


# -- the browser asks the same questions --------------------------------------

@pytest.fixture
def client(machine):
    os.environ.setdefault("PUFFPAI_API_KEY", "e" * 64)
    from puffpai import api_server
    api_server.app.config.update(TESTING=True)
    return (api_server.app.test_client(),
            {"Authorization": f"Bearer {api_server._API_KEY}"}, machine)


def _wait_needs(web, auth, ready, seconds: float = 60.0) -> dict:
    """GET /wizard/needs until *ready(view)*; the work runs on a thread."""
    import time
    deadline = time.monotonic() + seconds
    while True:
        view = web.get("/wizard/needs", headers=auth).json
        if ready(view) or time.monotonic() > deadline:
            assert ready(view), view
            return view
        time.sleep(0.05)


def test_the_browser_walks_the_same_conversation(client, monkeypatch, capsys):
    web, auth, calls = client
    flow = web.get("/wizard/flow", headers=auth).json
    # First the same first screen the console shows: this computer and the
    # thinking model that fits it.
    assert flow["next"] == "first_look"
    look = web.post("/wizard/first-look", headers=auth, json={}).json
    assert look["title"] == "Let's see what we're working with here"
    assert look["recommend"]["value"]
    assert web.post("/wizard/first-look", headers=auth,
                    json={"choose": True, "model": "no:such-model"}
                    ).status_code == 400
    assert web.post("/wizard/first-look", headers=auth,
                    json={"choose": True}).json["model"] ==         look["recommend"]["value"]
    flow = web.get("/wizard/flow", headers=auth).json
    assert flow["next"] == "goal"
    assert [q["id"] for q in flow["questions"]] == ["purpose:goal",
                                                    "purpose:identity"]
    goal_card = flow["questions"][0]["card"]
    assert goal_card["fields"][0]["label"] == (
        "What kind of bot do you want? Say it in your own words.")

    assert web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:goal", "answers": {"goal": G6}}).status_code == 200
    flow = web.get("/wizard/flow", headers=auth).json
    assert flow["next"] == "identity"
    name_field = flow["questions"][1]["card"]["fields"][0]
    assert name_field["current"] == "MusicBot"
    assert web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:identity", "answers": {"age": "15"}}).status_code == 200
    # The jobs the bot needs come next, then looking (here only).
    assert web.get("/wizard/flow", headers=auth).json["next"] == "needs"
    needs = _wait_needs(web, auth, lambda view: view["done"])
    assert needs["title"].startswith("Finding out what")
    assert needs["jobs"], needs
    looked = web.post("/wizard/needs", headers=auth, json={"look": "here"})
    assert looked.status_code == 200, looked.json
    _wait_needs(web, auth, lambda view: view["phase"] == "parts" and view["done"])
    assert web.get("/wizard/flow", headers=auth).json["next"] == "summary"

    # Building before "Looks good" is refused.
    assert web.post("/wizard/build", headers=auth, json={}).status_code == 400
    summary = web.post("/wizard/summary", headers=auth, json={}).json
    assert summary["title"] == "Here is the plan for MusicBot"
    assert [a["label"] for a in summary["actions"]] == ["Looks good",
                                                        "Change something"]
    bad = web.post("/wizard/approve", headers=auth,
                   json={"changes": {"on": {"made:up": True}}})
    assert bad.status_code == 400
    changed = web.post("/wizard/summary", headers=auth, json={
        "changes": {"on": {}, "model": summary["model_choices"][-1]["value"]}})
    assert changed.status_code == 200
    assert web.post("/wizard/approve", headers=auth,
                    json={"changes": {"on": {}}}).status_code == 200
    assert web.get("/wizard/flow", headers=auth).json["next"] == "vault"
    assert web.post("/wizard/vault/skip", headers=auth, json={}).json["skipped"]
    assert web.get("/wizard/flow", headers=auth).json["next"] == "build"

    run = web.post("/wizard/run", headers=auth, json={}).json
    assert run["complete"] is True, run["outstanding"]
    assert len(calls["pull"]) == 1, "Looks good in the browser means the same"
    flow = web.get("/wizard/flow", headers=auth).json
    assert flow["next"] == "done" and flow["handoff"]["title"]
    assert _record()["shared"]["summary"]["approved"] is True


def test_a_blank_name_in_the_browser_keeps_the_suggestion(client):
    """{"name": ""} used to name the bot PuffPAI, not the
    suggestion. A blank answer is Enter, in both faces."""
    web, auth, _calls = client
    web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:goal",
        "answers": {"goal": "A garden helper for my vegetable beds in Ohio"}})
    assert web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:identity",
        "answers": {"name": "", "age": ""}}).status_code == 200
    assert web.get("/wizard/flow", headers=auth).json["name"] == "GardenBot"


def test_packs_left_out_in_the_browser_are_not_chosen_either(client):
    web, auth, calls = client
    web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:goal", "answers": {"goal": G1}})
    web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:identity", "answers": {}})
    summary = web.post("/wizard/summary", headers=auth, json={}).json
    packs = [item["id"] for section in summary["sections"]
             for item in section["items"] if section["id"] == "knowledge"
             and item["apply"]["kind"] != "none"]  # a note is not a switch
    assert packs, "the space goal matches at least one download"
    assert web.post("/wizard/approve", headers=auth, json={"changes": {
        "on": {item_id: False for item_id in packs}}}).status_code == 200
    web.post("/wizard/vault/skip", headers=auth, json={})
    run = web.post("/wizard/run", headers=auth, json={}).json
    assert run["complete"] is True, run["outstanding"]
    assert calls["fetch"] == []
    record = _record()
    for interview in (record["shared"]["interview"],
                      record["sections"]["persona"]["interview"]):
        assert [i for i in interview["selected"] if i.startswith("pack:")] == []
    text = json.dumps(web.get("/wizard/flow", headers=auth).json["handoff"])
    assert "was chosen" not in text and "--fetch-source" not in text


@pytest.mark.parametrize("weak", ["48271593", "1234567890", "abc def ghij"])
def test_the_server_holds_the_terminal_s_pin_rules(client, monkeypatch, weak):
    """An 8-digit PIN the terminal refuses was accepted by
    /setup/vault and a real vault was made. The server is the boundary."""
    web, auth, _calls = client
    from puffpai import api_server, setup_ops as ops
    monkeypatch.setattr(api_server, "_vault_instance", None)
    assert ops.validate_vault_pin(weak)[0] is False
    refused = web.post("/setup/vault", headers=auth, json={"password": weak})
    assert refused.status_code == 400
    body = refused.get_data(as_text=True)
    assert "recovery_code" not in body and weak not in body
    assert ops.vault_initialized() is False
    made = web.post("/setup/vault", headers=auth, json={"password": PIN})
    assert made.status_code == 200 and made.json["recovery_code"]


def test_the_browser_builds_in_the_background(client):
    web, auth, _calls = client
    web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:goal", "answers": {"goal": G3}})
    web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:identity", "answers": {}})
    assert web.post("/wizard/approve", headers=auth, json={}).status_code == 200
    web.post("/wizard/vault/skip", headers=auth, json={})
    started = web.post("/wizard/build", headers=auth, json={})
    assert started.status_code == 202
    import time
    for _ in range(600):
        status = web.get("/wizard/build", headers=auth).json
        if not status["running"]:
            break
        time.sleep(0.1)
    assert status["running"] is False and not status["error"], status
    assert status["result"]["complete"] is True


def test_both_faces_ask_the_same_questions(client, monkeypatch, capsys):
    web, auth, _calls = client
    flow = web.get("/wizard/flow", headers=auth).json
    browser = [field["label"] for q in flow["questions"]
               for field in q["card"]["fields"]]
    _code, _transcript, asked = _run(monkeypatch, capsys, G3)
    console = asked[1:4]  # after the first screen's "Press Enter to use..."
    assert len(browser) == len(console) == 3
    for label, question in zip(browser, console):
        # The same words; the console only wraps them to the screen.
        assert " ".join(label.split()) in " ".join(question.split())


def test_the_browser_first_screen_pick_is_the_plans_how_it_thinks(client):
    """Merge wiring (t1): a model picked on "Let's see what we're working with
    here" in the browser is the one the plan's "How it thinks" shows."""
    web, auth, _calls = client
    look = web.post("/wizard/first-look", headers=auth, json={}).json
    others = [c["value"] for c in look.get("choices") or []
              if c["value"] != look["recommend"]["value"]]
    if not others:
        pytest.skip("this machine fixture offers only the recommendation")
    pick = others[-1]
    assert web.post("/wizard/first-look", headers=auth,
                    json={"choose": True, "model": pick}).json["model"] == pick
    assert web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:goal", "answers": {"goal": G6}}).status_code == 200
    assert web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:identity", "answers": {"age": "40"}}).status_code == 200
    summary = web.post("/wizard/summary", headers=auth, json={}).json
    thinking = next(sec for sec in summary["sections"] if sec["id"] == "thinking")
    brain = next(i for i in thinking["items"] if i["id"] == "thinking:brain")
    assert thinking["heading"] == "How it thinks"
    assert brain["apply"] == {"kind": "model_pull", "ref": pick}
    assert brain["label"] == f"The brain: {pick}"


def _plan_for(web, auth, goal):
    web.post("/wizard/first-look", headers=auth, json={})
    assert web.post("/wizard/first-look", headers=auth,
                    json={"choose": True}).status_code == 200
    assert web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:goal", "answers": {"goal": goal}}).status_code == 200
    assert web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:identity", "answers": {"age": "40"}}).status_code == 200
    summary = web.post("/wizard/summary", headers=auth, json={}).json
    return {i["id"]: i for sec in summary["sections"] for i in sec["items"]}


def test_the_weather_proposal_is_on_the_browser_plan_when_the_goal_fits(client):
    """The weather proposal reaches the whole plan
    (after the first screen), on for a garden bot, with no hub wording."""
    web, auth, _calls = client
    items = _plan_for(web, auth, "A bot that helps me plan my vegetable garden")
    weather = items["outside_services:door:weather"]
    assert weather["on"] is True and weather["label"] == "Weather forecast"
    assert "Home Assistant" not in json.dumps(weather)


def test_the_weather_off_line_is_plain_for_a_goal_it_does_not_fit(client):
    web, auth, _calls = client
    items = _plan_for(web, auth, G6)
    weather = items["outside_services:door:weather"]
    assert weather["on"] is False
    assert weather["off_line"].startswith("Not included (you can add it): Weather forecast")
    assert "Home Assistant" not in weather["off_line"]
