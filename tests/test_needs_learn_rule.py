"""The written "learn about" rule (K2): knowledge only for learn jobs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from puffpai.catalogs import needs_rules
from puffpai.wizard import needs, needs_llm

FIX = Path(__file__).parent / "fixtures"
CFG = {"ollama_url": "http://127.0.0.1:11435", "chosen_model": "qwen2.5:7b-instruct"}


def _goal(gid: str) -> str:
    path = FIX / "tuning_goals.json"
    return next(g["text"] for g in json.loads(path.read_text(encoding="utf-8"))["goals"]
                if g["id"] == gid)


def _v5(gid: str) -> dict:
    return json.loads((FIX / "needs_model_raw" / f"{gid}.json").read_text(encoding="utf-8"))


class _Brain:
    def __init__(self, text):
        self.text = text

    def chat_stream(self, messages, temperature=0.7, max_tokens=2048, format=None, on_line=None):
        yield self.text


@pytest.fixture
def run_recorded(monkeypatch):
    def run(gid: str) -> dict:
        raw = _v5(gid)["raw"]
        monkeypatch.setattr(needs_llm, "make_backend", lambda *a, **k: _Brain(raw))
        needs._CACHE.clear()
        return needs.run(_goal(gid), cfg=CFG)
    yield run
    needs._CACHE.clear()


def test_no_recorded_wedding_job_is_a_learn_job():
    rec = _v5("wedding")["parsed"]
    assert len(rec["jobs"]) == 7
    learn = [j["name"] for j in rec["jobs"]
             if needs_rules.is_learn(j["name"], j["does"], "wedding planner")]
    assert learn == []


def test_the_spanish_tutor_has_learn_jobs():
    rec = _v5("spanish")["parsed"]
    learn = [j for j in rec["jobs"] if needs_rules.is_learn(j["name"], j["does"], "spanish tutor")]
    assert len(learn) >= 1


@pytest.mark.parametrize("name,does,subject,want", [
    ("Quiz me about stars", "", "space", True),
    ("Rules of chess", "", "chess", True),
    ("History of jazz", "", "music", True),
    ("How to prune roses", "", "garden", True),
    ("Practise scales", "", "piano", True),
    ("Budget planner", "create and manage a budget", "wedding planner", False),
    ("Recipe box", "keeps recipes", "vegan recipe", False),
    ("Flashcards", "shows cards", "spanish tutor", True),
    ("Relearning", "", "x", False),  # whole words only
])
def test_the_rule(name, does, subject, want):
    assert needs_rules.is_learn(name, does, subject) is want


def test_knowledge_parts_of_non_learn_jobs_are_dropped(run_recorded):
    out = run_recorded("wedding")
    assert all(not j["learn"] for j in out["jobs"])
    kinds = {p["kind"] for j in out["jobs"] for p in j["parts"]}
    assert "knowledge" not in kinds
    # the recorded answer did ask for knowledge on most wedding jobs
    raw_kinds = [p["kind"] for j in _v5("wedding")["parsed"]["jobs"] for p in j["parts"]]
    assert raw_kinds.count("knowledge") >= 4


def test_knowledge_parts_of_learn_jobs_are_kept(run_recorded):
    out = run_recorded("spanish")
    learn = [j for j in out["jobs"] if j["learn"]]
    assert learn
    assert any(p["kind"] == "knowledge" for j in learn for p in j["parts"])


def test_knowledge_parts_never_survive_on_non_learn_jobs_in_any_goal(run_recorded):
    for gid in ("garden", "fitness", "dnd", "bakery", "car", "pet", "budget", "band", "space",
                "meals"):
        out = run_recorded(gid)
        for job in out["jobs"]:
            if not job["learn"]:
                assert all(p["kind"] != "knowledge" for p in job["parts"]), (gid, job["name"])
