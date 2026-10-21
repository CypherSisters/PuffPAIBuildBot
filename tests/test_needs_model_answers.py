"""The needs step with a fake brain fed the recorded model answers.

Recorded runs: ``fixtures/needs_model_raw`` (qwen2.5:7b-instruct, needs-v5, the raw
streamed text) and ``fixtures/needs_jobs_parsed`` (the parsed stage-A answers).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from puffpai.wizard import needs, needs_llm

FIX = Path(__file__).parent / "fixtures"
CFG = {"ollama_url": "http://127.0.0.1:11435", "chosen_model": "qwen2.5:7b-instruct"}
GOAL_IDS = ["wedding", "garden", "fitness", "dnd", "bakery", "car", "spanish", "pet", "budget",
            "band", "space", "meals"]


def _goal(gid: str) -> str:
    path = FIX / "tuning_goals.json"
    return next(g["text"] for g in json.loads(path.read_text(encoding="utf-8"))["goals"]
                if g["id"] == gid)


def _v5(gid: str) -> dict:
    return json.loads((FIX / "needs_model_raw" / f"{gid}.json").read_text(encoding="utf-8"))


class FakeBrain:
    """Streams a fixed text in small pieces; records every prompt it was sent."""

    def __init__(self, text: str, *, piece: int = 7, tok_s: float = 46.0, error=None):
        self.text, self.piece, self.tok_s, self.error = text, piece, tok_s, error
        self.sent: list[dict] = []
        self.consumed = 0

    def chat_stream(self, messages, temperature=0.7, max_tokens=2048, format=None, on_line=None):
        self.sent.append({"messages": messages, "format": format, "max_tokens": max_tokens,
                          "temperature": temperature})
        if self.error:
            raise self.error
        for i in range(0, len(self.text), self.piece):
            self.consumed = i + self.piece
            yield self.text[i:i + self.piece]
        if on_line:
            on_line({"done": True, "eval_count": 600,
                     "eval_duration": int(600 / self.tok_s * 1e9)})

    def prewarm(self, keep_alive="10m"):
        self.sent.append({"prewarm": keep_alive})
        return True


@pytest.fixture
def brain(monkeypatch):
    holder: dict = {}

    def install(fake: FakeBrain):
        holder["fake"] = fake
        monkeypatch.setattr(needs_llm, "make_backend",
                            lambda url, model, timeout, max_tokens: fake)
        needs._CACHE.clear()
        return fake
    yield install
    needs._CACHE.clear()


@pytest.mark.parametrize("gid", GOAL_IDS)
def test_every_recorded_v5_answer_parses(gid):
    rec = _v5(gid)
    got = needs_llm.parse(rec["raw"])
    assert 4 <= len(got["jobs"]) <= 10
    assert [j["name"] for j in got["jobs"]] == [j["name"] for j in rec["parsed"]["jobs"]]
    for job in got["jobs"]:
        for part in job["parts"]:
            assert part["kind"] in needs_llm.KINDS
            for phrase in part["search"]:
                assert 2 <= len(phrase.split()) <= 4


@pytest.mark.parametrize("gid", GOAL_IDS)
def test_every_recorded_stage_a_answer_parses(gid):
    rec = json.loads((FIX / "needs_jobs_parsed" / f"{gid}.json").read_text(encoding="utf-8"))
    got = needs_llm.parse(json.dumps(rec["parsed"]))
    assert [j["name"] for j in got["jobs"]] == [j["name"] for j in rec["parsed"]["jobs"]]
    assert all(j["parts"] == [] for j in got["jobs"])


def test_the_first_job_is_parsed_at_its_closing_brace():
    raw = _v5("wedding")["raw"]
    seen: list[tuple[int, dict]] = []
    stream = needs_llm.JobStream(lambda job: seen.append((len(stream.text), job)))
    for ch in raw:
        stream.feed(ch)
    first_close = raw.index("}", raw.index('"jobs"'))
    # the first job object closes at the first "}" after its last part closes
    first_job_end = raw.index("\n    }", raw.index('"jobs"')) + len("\n    }")
    assert seen[0][0] == first_job_end
    assert first_close < first_job_end  # the parts' own braces did not trigger it
    assert seen[0][1]["name"] == "theme tracker"
    assert len(seen) == 7


def test_the_jobs_are_asked_for_with_greedy_decoding(brain):
    # At temperature 0.2 the same goal gave different
    # job names from run to run, so a find was filed and judged differently and
    # a goal passed the bar in one run and failed it in the next.
    fake = brain(FakeBrain(_v5("wedding")["raw"]))
    needs.run(_goal("wedding"), cfg=CFG)
    calls = [c for c in fake.sent if "messages" in c]
    assert calls and all(c["temperature"] == 0.0 for c in calls)
    assert needs_llm.TEMPERATURE == 0.0


def test_run_streams_jobs_before_the_answer_ends(brain):
    fake = brain(FakeBrain(_v5("wedding")["raw"], piece=5))
    order: list[str] = []
    needs.run(_goal("wedding"), cfg=CFG,
              on_job=lambda job: order.append(f"{job['source']}:{job['name']}"))
    assert order[:5] == ["asked:Track themes from pictures", "asked:Come up with ideas",
                         "asked:Find wedding vendors and materials to rent",
                         "asked:Plan the budget", "asked:Do a whole wedding plan"]
    assert fake.sent[0]["format"] == needs_llm.SCHEMA_V5


def test_new_model_jobs_are_shown_while_the_answer_streams(brain):
    from puffpai.catalogs import meaning
    if meaning.helper_state() != "ready":
        pytest.skip("her items are told apart by meaning, and the reading helper is not here")
    fake = brain(FakeBrain(_v5("wedding")["raw"], piece=5))
    events: list[tuple[int, dict]] = []
    out = needs.run(_goal("wedding"), cfg=CFG,
                    on_job=lambda job: events.append((fake.consumed, dict(job))))
    early = [(pos, e) for pos, e in events if e.get("provisional")]
    assert early and all(pos < len(fake.text) for pos, _e in early)
    assert all(e["id"] is None and e["source"] == "usual" for _p, e in early)
    final = [e for _p, e in events if e.get("id") and e["source"] == "usual"]
    assert [e["name"] for e in final] == [j["name"] for j in out["jobs"] if j["source"] == "usual"]
    # her own items were not shown twice
    assert not any(e["name"].lower().startswith(("theme tracker", "budget planner"))
                   for _p, e in early)


def test_run_with_the_recorded_wedding_answer(brain):
    from puffpai.catalogs import meaning
    if meaning.helper_state() != "ready":
        pytest.skip("the merge is measured with the reading helper, which is not here")
    brain(FakeBrain(_v5("wedding")["raw"]))
    lines: list[str] = []
    out = needs.run(_goal("wedding"), cfg=CFG, on_line=lines.append)
    assert out["schema"] == "needs/v1" and out["prompt"] == "needs-v5"
    assert out["kind"] == "wedding planner" and out["kind_source"] == "rules"
    assert out["goal_digest"].startswith("sha256:")
    names = [(j["id"], j["name"], j["source"]) for j in out["jobs"]]
    assert names[:5] == [("j1", "Track themes from pictures", "asked"),
                         ("j2", "Come up with ideas", "asked"),
                         ("j3", "Find wedding vendors and materials to rent", "asked"),
                         ("j4", "Plan the budget", "asked"),
                         ("j5", "Do a whole wedding plan", "asked")]
    assert [s for _i, _n, s in names[5:]] == ["usual", "usual"]
    assert out["jobs"][3]["does"] == "create and manage a wedding budget"
    # The outbound screen may hold more personal words ("got").
    assert out["private_left_out"][:1] == ["brother"]
    # ordinary words are never listed as private; a relative's news is held,
    # so it is listed too (every word held is listed)
    assert "got" not in out["private_left_out"]
    assert "engaged" in out["private_left_out"]
    kept = [ln for ln in lines if ln.startswith("Kept private, never searched: ")]
    assert len(kept) == 1 and kept[0].startswith("Kept private, never searched: brother")
    assert "engaged" in kept[0]
    assert "Asking your bot's brain what wedding planner bots usually need ... 2 more" in lines
    assert "From your words: 5 things" in lines
    assert out["timings"]["first_job_s"] is not None
    assert "goal" not in json.dumps(out).replace("goal_digest", "")


def test_prose_falls_back_to_rules_with_the_plain_line(brain):
    brain(FakeBrain("Sure! A wedding planner bot needs lots of things, like a budget."))
    lines: list[str] = []
    out = needs.run(_goal("wedding"), cfg=CFG, on_line=lines.append)
    assert out["prompt"] == "rules"
    assert [j["source"] for j in out["jobs"]] == ["asked"] * 5
    assert needs.LINE_FAILED in lines


def test_an_error_falls_back_to_rules(brain):
    brain(FakeBrain("", error=ConnectionError("down")))
    lines: list[str] = []
    out = needs.run(_goal("car"), cfg=CFG, on_line=lines.append)
    assert out["prompt"] == "rules" and needs.LINE_FAILED in lines
    assert len(out["jobs"]) == 4


def test_timeout_falls_back_to_rules(brain, monkeypatch):
    fake = brain(FakeBrain(_v5("wedding")["raw"], piece=3))
    ticks = iter(range(0, 100000))
    monkeypatch.setattr(needs_llm.time, "monotonic", lambda: float(next(ticks)))
    lines: list[str] = []
    out = needs.run(_goal("wedding"), cfg=CFG, on_line=lines.append, deadline_s=30)
    assert out["prompt"] == "rules" and needs.LINE_FAILED in lines
    assert needs.LINE_NOT_DOWNLOADED not in lines and "model_missing" not in out
    assert fake.consumed < len(fake.text)  # it stopped reading at the deadline


NOT_FOUND = "[Error] Ollama error: 404 Client Error: Not Found for url: http://127.0.0.1:11435/api/chat"


def _ollama_has(monkeypatch, names):
    from puffpai import brain_health
    asked: list[str] = []

    def list_models(url, timeout=3):
        asked.append(url)
        return names
    monkeypatch.setattr(brain_health, "list_models", list_models)
    return asked


def test_a_model_not_downloaded_yet_says_so(brain, monkeypatch):
    # Ollama answers a chat for a model it does not have with a 404.
    brain(FakeBrain(NOT_FOUND, piece=len(NOT_FOUND)))
    asked = _ollama_has(monkeypatch, ["qwen3.5:9b"])
    lines: list[str] = []
    out = needs.run(_goal("car"), cfg=CFG, on_line=lines.append)
    assert asked == [CFG["ollama_url"]]
    assert out["prompt"] == "rules" and out["model_missing"] is True
    assert needs.LINE_NOT_DOWNLOADED in lines and needs.LINE_FAILED not in lines
    assert len(out["jobs"]) == 4  # the list from her own words, as before


@pytest.mark.parametrize("names", [["qwen2.5:7b-instruct"], None])
def test_a_404_with_the_model_there_or_ollama_gone_is_a_plain_failure(brain, monkeypatch,
                                                                       names):
    brain(FakeBrain(NOT_FOUND, piece=len(NOT_FOUND)))
    _ollama_has(monkeypatch, names)
    lines: list[str] = []
    out = needs.run(_goal("car"), cfg=CFG, on_line=lines.append)
    assert out["prompt"] == "rules" and "model_missing" not in out
    assert needs.LINE_FAILED in lines and needs.LINE_NOT_DOWNLOADED not in lines


def test_timeout_and_other_errors_keep_the_old_line_and_never_list_models(brain,
                                                                          monkeypatch):
    asked = _ollama_has(monkeypatch, [])
    brain(FakeBrain("[Error] Cannot connect to Ollama. Is it running?"))
    lines: list[str] = []
    out = needs.run(_goal("car"), cfg=CFG, on_line=lines.append)
    assert needs.LINE_FAILED in lines and "model_missing" not in out
    with pytest.raises(needs_llm.NeedsUnavailable) as err:
        needs_llm.ask("x", url=CFG["ollama_url"], model="m:7b", prompt=needs_llm.PROMPT_V5,
                      deadline_s=-1.0)
    assert err.value.reason == needs_llm.TIMED_OUT
    assert asked == []


def test_error_text_from_the_backend_counts_as_no_answer(brain):
    brain(FakeBrain("[Error] Cannot connect to Ollama. Is it running?"))
    out = needs.run(_goal("pet"), cfg=CFG)
    assert out["prompt"] == "rules"


@pytest.mark.parametrize("model,tok_s,prompt", [
    ("qwen2.5:7b-instruct", None, "needs-v5"),
    ("qwen2.5:7b-instruct", 46.0, "needs-v5"),
    ("qwen2.5:7b-instruct", 10.5, "stage-A"),
    ("llama3.2:1b", 90.0, "rules"),
    ("qwen2.5:0.5b", None, "rules"),
    ("gemma3:4b", 70.0, "needs-v5"),
])
def test_the_path_is_chosen_by_size_and_speed(model, tok_s, prompt):
    assert needs_llm.choose_prompt(model, tok_s) == prompt


def test_small_brains_are_never_asked(brain):
    fake = brain(FakeBrain(_v5("wedding")["raw"]))
    lines: list[str] = []
    out = needs.run(_goal("wedding"), cfg={**CFG, "chosen_model": "llama3.2:1b"},
                    on_line=lines.append)
    assert fake.sent == [] and out["prompt"] == "rules" and needs.LINE_SMALL in lines


def test_no_brain_means_rules(brain):
    fake = brain(FakeBrain("{}"))
    lines: list[str] = []
    out = needs.run(_goal("garden"), cfg={}, on_line=lines.append)
    assert fake.sent == [] and out["prompt"] == "rules" and needs.LINE_NO_BRAIN in lines


def test_slow_brain_runs_stage_a_and_makes_phrases_by_rule(brain):
    rec = json.loads((FIX / "needs_jobs_parsed" / "wedding.json").read_text(encoding="utf-8"))
    fake = brain(FakeBrain(json.dumps(rec["parsed"])))
    lines: list[str] = []
    out = needs.run(_goal("wedding"), cfg={**CFG, "brain_tok_s": 10.5}, on_line=lines.append)
    assert out["prompt"] == "stage-A" and fake.sent[0]["format"] == needs_llm.SCHEMA_STAGE_A
    assert needs.LINE_SLOW in lines
    added = [j for j in out["jobs"] if j["source"] == "usual"]
    assert added, out["jobs"]
    for job in added:
        phrases = [s["phrase"] for p in job["parts"] for s in p["search"]]
        assert phrases and all(1 <= len(p.split()) <= 3 for p in phrases)


def test_measured_speed_is_remembered(brain):
    brain(FakeBrain(_v5("space")["raw"], tok_s=12.0))
    needs.run(_goal("space"), cfg=CFG)
    assert needs._known_speed({}, "qwen2.5:7b-instruct") == 12.0


def test_the_prompt_holds_only_the_goal_and_fixed_words():
    msgs = needs_llm.messages("a knitting patterns bot", needs_llm.PROMPT_V5)
    assert msgs[0]["content"] == needs_llm.SYSTEM_PROMPT_V5
    assert msgs[1]["content"] == 'The person wrote: "a knitting patterns bot"'


def test_prewarm_asks_the_backend_to_keep_the_model_ten_minutes(brain):
    fake = brain(FakeBrain(""))
    needs.prewarm(CFG)
    assert fake.sent == [{"prewarm": "10m"}]


def test_prewarm_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no")
    monkeypatch.setattr(needs_llm, "make_backend", boom)
    assert needs.prewarm(CFG) is None


def test_second_run_of_the_same_goal_is_cached(brain):
    fake = brain(FakeBrain(_v5("band")["raw"]))
    a = needs.run(_goal("band"), cfg=CFG)
    b = needs.run(_goal("band"), cfg=CFG)
    assert a == b and len(fake.sent) == 1


def test_choose_ticks_adds_and_allows(brain):
    brain(FakeBrain(_v5("wedding")["raw"]))
    out = needs.run(_goal("wedding"), cfg=CFG)
    ids = [j["id"] for j in out["jobs"]]
    chosen = needs.choose(out, ticked=ids[:3], added=["Guest list and RSVPs"], allowed_words=[])
    assert [j["ticked"] for j in chosen["jobs"]][:len(ids)] == [True] * 3 + [False] * (len(ids) - 3)
    owner = chosen["jobs"][-1]
    assert owner["name"] == "Guest list and RSVPs" and owner["source"] == "owner"
    assert owner["ticked"] is True and owner["id"] == f"j{len(ids) + 1}"
    assert out["jobs"][0]["ticked"] is True  # the input is not changed


def test_from_words_makes_one_owner_job():
    out = needs.from_words("a budget tracker", cfg={})
    assert out["schema"] == "needs/v1" and len(out["jobs"]) == 1
    assert out["jobs"][0]["source"] == "owner"
    kinds = {p["kind"] for p in out["jobs"][0]["parts"]}
    assert {"code-app", "data-template"} <= kinds


def test_sends_is_empty_without_the_outbound_screen(brain):
    try:
        import puffpai.security.outbound_query  # noqa: F401
        pytest.skip("the outbound screen is in this build")
    except ModuleNotFoundError:
        pass
    brain(FakeBrain(_v5("wedding")["raw"]))
    out = needs.run(_goal("wedding"), cfg=CFG)
    assert needs.sends(out, goal=_goal("wedding")) == []
    states = {s["state"] for j in out["jobs"] for p in j["parts"] for s in p["search"]}
    assert states == {"held"}
    assert out["subject"] == {"phrase": "wedding planner", "notes": [], "sendable": False}


def test_a_long_phrase_is_shortened_not_glued():
    from puffpai.wizard import needs_llm
    assert needs_llm.clean_phrase("physical chess board with electronics") == \
        "physical chess board electronics"
    assert needs_llm.clean_phrase("wedding budget spreadsheet") == "wedding budget spreadsheet"
    assert needs_llm.clean_phrase("budget") == ""
    assert needs_llm.clean_phrase("a b c d e f g h") == ""
    # the grammar lets the model write up to 6 words, so it never glues words to fit 4
    import re
    assert re.match(needs_llm.PHRASE_PATTERN, "physical chess board with electronics")


def test_a_brain_job_that_only_repeats_her_words_joins_her_job():
    # The brain's "Metronome" next to her "Metronome and tuner" and
    # "Track oil changes" next to "Track oil changes and tire rotations" were
    # second jobs, and finds were filed (and pre-ticked) under the copy.
    from puffpai.wizard import needs
    mine = [{"name": "Metronome and tuner", "does": "", "source": "asked",
             "parts": [{"kind": "code-app", "search": ["metronome tuner"]}]},
            {"name": "Track oil changes and tire rotations", "does": "", "source": "asked",
             "parts": [{"kind": "code-app", "search": ["oil change tire"]}]}]
    model = {"kind": "band", "asked": [], "jobs": [
        {"name": "metronome", "does": "Keeps time.",
         "parts": [{"kind": "code-app", "search": ["online metronome"]}]},
        {"name": "Track oil changes", "does": "Logs each oil change.",
         "parts": [{"kind": "code-app", "search": ["oil change log"]}]},
        {"name": "Schedule gigs", "does": "Plans shows.",
         "parts": [{"kind": "code-app", "search": ["gig calendar"]}]}]}
    got = needs._merge(mine, model, {})
    names = [j["name"] for j in got]
    assert names[:2] == ["Metronome and tuner", "Track oil changes and tire rotations"]
    assert "Metronome" not in names and "Track oil changes" not in names
    assert "online metronome" in got[0]["parts"][0]["search"]
    assert "oil change log" in got[1]["parts"][0]["search"]
