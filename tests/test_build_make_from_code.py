"""Toolsmith makes an app from code (owner, 2026-10-13: "That's what the
Toolsmith is for: taking code and creating an app").

* apps.py keeps the pool items that are code, not a whole app, as the
  job's ``code`` (best first, up to 3);
* a job with no app but code gets a MAKE try; every app giving up falls
  through to it (at most 2 code projects);
* the made app lands in toolsmith_apps.json with ``made_from`` and its job,
  and is found again for reuse like any app;
* the job's one setup row says it in plain words;
* the MAKE task has its own prompt and budget (30 steps, 15 minutes).

The brain and the tool loop are mocked; nothing runs and nothing goes online.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from puffpai.build import apps, make, setup as setup_mod
from puffpai.build import schema as S
import pytest

from puffpai.build import toolsmith_run as T


@pytest.fixture(autouse=True)
def _no_real_listeners(monkeypatch):
    """No test here reads this computer's real listening ports."""
    monkeypatch.setattr(T, "listening_ports", lambda: set())
    monkeypatch.setattr(T, "tree_listen_ports", lambda pid: [])

LIB = {"ref": "code:o/newslib", "full_name": "o/newslib", "name": "newslib",
       "url": "https://github.com/o/newslib", "what": "A news feed parser library.",
       "stars": 900, "language": "Python", "kind": "a library"}
LIB2 = dict(LIB, ref="code:o/feedkit", full_name="o/feedkit", name="feedkit",
            url="https://github.com/o/feedkit")


def _job(**kw) -> dict:
    return {"id": "news", "name": "World news", "does": "Show the world news.",
            "route": "A", "pick": {"ref": LIB["ref"], "class": "APP"}, "ingredients": [],
            "alternates": [], "code": [dict(LIB), dict(LIB2)], **kw}


def _build(job: dict, parts: dict | None = None) -> dict:
    return {"jobs": [dict(job)], "parts": parts or {},
            "setup": setup_mod.rows_for([job], parts or {})}


# ── apps.py keeps the code ─────────────────────────────────────────────────

def _item(name, stars, desc="A news thing."):
    return {"full_name": f"o/{name}", "name": name, "stargazers_count": stars,
            "language": "Python", "description": desc, "topics": [], "archived": False,
            "fork": False, "html_url": f"https://github.com/o/{name}",
            "default_branch": "main", "owner": {"login": "o"}}


def test_apps_py_keeps_pool_items_that_are_code_as_the_jobs_code(monkeypatch):
    items = [_item("reader", 900), _item("newslib", 800, "A news parser library."),
             _item("feedkit", 700), _item("cli-news", 600), _item("more", 500)]
    readmes = {"o/reader": "## Run\n```\npython app.py\n```\n",
               "o/newslib": "# newslib\n```python\nimport newslib\n```\n",
               "o/feedkit": "# feedkit\nJust words.", "o/cli-news": "# cli\nWords.",
               "o/more": "# more\nWords."}
    monkeypatch.setattr(apps, "_search", lambda words, stars=apps.MIN_STARS: list(items))
    monkeypatch.setattr(apps, "catalog_items", lambda job: [])
    monkeypatch.setattr(apps, "readme_text", lambda full, branch="": readmes.get(full, ""))
    monkeypatch.setattr(apps, "has_compose", lambda full, branch="": "")
    monkeypatch.setattr(apps, "_beyond", lambda text, brain=None: [])
    monkeypatch.setattr(apps, "_default_brain", lambda: None)
    job = {"id": "news", "name": "World news", "action": "show"}
    got = apps.for_jobs([job], "world news")
    assert [c["name"] for c in got] == ["reader"]
    assert [c["full_name"] for c in job["code"]] == ["o/newslib", "o/feedkit", "o/cli-news"]
    assert job["code"][0]["ref"] == "code:o/newslib" and job["code"][0]["kind"] == "a library"


# ── the one setup row ──────────────────────────────────────────────────────

def test_the_setup_row_says_it_in_plain_words():
    rows = setup_mod.rows_for([_job()], {})
    row = next(r for r in rows if r.get("make") == setup_mod.MAKE_CODE)
    assert row["label"] == ("If no ready-made app runs, Toolsmith makes one from o/newslib "
                            "(a library)")
    assert "o/feedkit" in row["detail"] and row["refs"] == [LIB["ref"], LIB2["ref"]]
    assert any(r["id"] == "switch-code-download" for r in rows)
    for text in (row["label"], row["detail"]):
        assert "—" not in text and "door" not in text.lower() and "beta" not in text.lower()
    assert make.not_listed(_build(_job()), LIB, setup_mod.MAKE_CODE) == ""
    assert make.not_listed({"jobs": [_job()], "setup": []}, LIB, setup_mod.MAKE_CODE)


# ── make: the MAKE try ─────────────────────────────────────────────────────

def _fake_run_job(calls, outcome):
    def run_job(j, cands, progress=None, make=False, **kw):
        calls.append({"make": make, "refs": [c["ref"] for c in cands]})
        return outcome(make, cands)
    return run_job


def _made(cands):
    c = cands[0]
    return {"ok": True, "app": {"app_id": "newslib-abc123", "name": c["name"],
                                "url": "http://127.0.0.1:5999/", "ref": c["ref"],
                                "made_from": "o/newslib"}, "beyond_done": [], "tried": []}


def test_a_job_with_no_app_but_a_library_gets_a_make_try(monkeypatch):
    job = _job()
    build = _build(job)
    calls: list = []
    monkeypatch.setattr(T, "running_for", lambda cands: None)
    monkeypatch.setattr(T, "run_job", _fake_run_job(calls, lambda mk, cands: _made(cands)))
    monkeypatch.setattr(S, "update_build", lambda change: change(build) or build)
    got = make._make_app(job, build, [], {}, lambda _l: None)  # noqa: SLF001
    assert calls == [{"make": True, "refs": [LIB["ref"], LIB2["ref"]]}]
    assert got["ok"] and got["screen"]["toolsmith_app"] == "newslib-abc123"
    assert got["made"]["made_from"] == "o/newslib"


def test_without_its_row_on_the_list_nothing_is_made(monkeypatch):
    job = _job()
    monkeypatch.setattr(T, "run_job", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    got = make._make_app(job, {"jobs": [job], "setup": []}, [], {}, lambda _l: None)  # noqa: SLF001
    assert not got["ok"] and "not on your setup list" in got["reason"]


def test_every_app_giving_up_falls_through_to_make(monkeypatch):
    app = {"ref": "github:o/reader", "name": "reader", "class": "APP", "direct": True,
           "url": "https://github.com/o/reader", "check": {"ok": True, "later": True}}
    job = _job(pick={"ref": app["ref"], "class": "APP"}, ingredients=[app["ref"]])
    parts = {app["ref"]: app}
    build = _build(job, parts)
    calls: list = []

    def outcome(mk, cands):
        if not mk:
            return {"ok": False, "reason": "none of its apps started here: reader: npm "
                    "failed", "tried": [{"ref": app["ref"], "name": "reader", "why": "npm"}]}
        if cands[0]["ref"] == LIB["ref"]:
            return {"ok": False, "reason": "no app could be made",
                    "tried": [{"ref": LIB["ref"], "name": "newslib", "why": "import error"}]}
        return _made(cands)
    monkeypatch.setattr(T, "running_for", lambda cands: None)
    monkeypatch.setattr(T, "run_job", _fake_run_job(calls, outcome))
    monkeypatch.setattr(S, "update_build", lambda change: change(build) or build)
    monkeypatch.setattr(make, "not_listed", lambda b, cand, action, target=None:
                        "" if action in ("app", setup_mod.MAKE_CODE) else "no")
    got = make._make_with_toolsmith(job, build, dict(app), lambda _l: None)  # noqa: SLF001
    assert calls[0] == {"make": False, "refs": [app["ref"]]}
    assert calls[1]["make"] is True and calls[1]["refs"][0] == LIB["ref"]
    assert not got["ok"] and "reader" in got["reason"]          # the app's reason stays
    # Next run: the app and the first library gave up; the second is made.
    got = make._make_with_toolsmith(job, build, dict(app), lambda _l: None)  # noqa: SLF001
    assert calls[-1] == {"make": True, "refs": [LIB2["ref"]]}
    assert got["ok"] and got["made"]["made_from"] == "o/newslib"


# ── the made app: kept, found again, its own prompt and budget ─────────────

def test_a_made_app_lands_in_the_registry_with_made_from(monkeypatch, tmp_path):
    from puffpai.integrate import host_tools
    monkeypatch.setattr(T, "_state_dir", lambda: str(tmp_path))
    monkeypatch.setattr(host_tools, "read_page", lambda url: {
        "ok": True, "title": "News", "text": "World news Search Results headlines today"})
    monkeypatch.setattr(T, "port_open", lambda port: True)
    run = T.Run({"id": "news", "name": "World news"}, [dict(LIB)], lambda _l: None, make=True)
    run.app, run.app_id, run.folder = dict(LIB), "newslib-abc123", str(tmp_path / "a")
    assert run.done({"url": "http://127.0.0.1:5999/"})["ok"]
    entry = T.registry()["newslib-abc123"]
    assert entry["made_from"] == "o/newslib" and entry["job"] == "news"
    assert T.running_apps()[0]["made_from"] == "o/newslib"
    # reused by another job like any app (by its GitHub owner/repo)
    assert T.running_for([{"ref": "github:o/newslib",
                           "url": "https://github.com/o/newslib"}])["app_id"] == "newslib-abc123"


def test_the_make_task_has_its_own_prompt_and_budget(monkeypatch):
    from puffpai import brain_loop
    monkeypatch.setattr(T, "taken_ports", lambda extra=(): [])
    run = T.Run({"id": "news", "name": "World news", "does": "Show the news."},
                [dict(LIB)], lambda _l: None, make=True)
    seen = {}

    def fake_loop(messages, ctx=None, backend=None, tools=None, max_steps=0):
        seen.setdefault("system", messages[0]["content"])
        seen.setdefault("task", messages[1]["content"])
        seen.setdefault("max", max_steps)
        run.app = run.cands[0]
        return {"steps": max_steps, "text": ""}
    monkeypatch.setattr(brain_loop, "run", fake_loop)
    assert run.go(object()) is None
    assert "make a small web app" in seen["system"]
    assert "documented API from its README" in seen["system"]
    assert "127.0.0.1" in seen["system"] and str(run.port) in seen["system"]
    assert "o/newslib" in seen["task"] and T.STEPS_PER_MAKE == 30
    assert seen["max"] == T.STEPS_PER_MAKE + T.READS_PER_TRY      # reading spends no steps
    assert T.MAKE_MINUTES == 15 and run.minutes_max == 15
    assert run.failed[0]["why"].endswith("within 30 steps")
    names = {t.name for t in run.tools()}
    assert names >= {"download", "terminal", "read_docs", "write_file", "start_server",
                     "wait_for_page", "read_page", "done", "give_up", "install_program"}


# ── a pasted project address ───────────────────────────────────────────────

class _Ans:
    def __init__(self, data, ok=True):
        self.ok, self._data = ok, data

    def json(self):
        return self._data


def _pasted(monkeypatch, readme: str, compose: str = "", ok: bool = True):
    from puffpai.finding import net
    monkeypatch.setattr(net, "get", lambda url, **kw: _Ans(_item("plantlib", 3), ok))
    monkeypatch.setattr(apps, "readme_text", lambda full, branch="": readme)
    monkeypatch.setattr(apps, "has_compose", lambda full, branch="": compose)
    monkeypatch.setattr(apps, "_beyond", lambda text, brain=None: [])


def test_a_pasted_library_is_code_to_make_an_app_from(monkeypatch):
    _pasted(monkeypatch, "# plantlib\n```python\nimport plantlib\n```\n")
    got = apps.from_url("https://github.com/o/plantlib")
    assert got["ref"] == "code:o/plantlib" and got["url"] == "https://github.com/o/plantlib"
    assert got["kind"] == "the project you gave"


def test_a_pasted_app_is_still_an_app(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    _pasted(monkeypatch, "## Run\n```\npython app.py\n```\n")
    got = apps.from_url("https://github.com/o/plantlib")
    assert got["ref"] == "github:o/plantlib" and got["readme_steps"]


def test_a_pasted_address_that_is_not_github_or_cannot_be_read_is_none(monkeypatch):
    _pasted(monkeypatch, "", ok=False)
    assert apps.from_url("https://example.org/o/plantlib") is None
    assert apps.from_url("https://github.com/o/plantlib") is None
