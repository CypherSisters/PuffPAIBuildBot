"""The simple app path: "this was the first item returned
on the search 'world news'. this isn't that hard".

* GitHub by the job's words and the goal's words, most stars first; the
  results whose README says how to run them are the job's apps, in star
  order (no fit score, no brain judge);
* the README Quick Start is read as commands (git clone / cd dropped), with
  the port it says to open; those commands go on the Yes card;
* the app runs the way its README says (npm natively even when it also
  ships a compose file);
* an app that also offers a tool server is still an app;
* only real tunnel tools count as a hidden connection;
* a short goal is 1 to 3 jobs tied to its words.

Nothing here goes online.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os

from puffpai.build import apps, decide, interpret, setup as setup_mod
from puffpai.build import schema as S
from puffpai.finding import sites
from puffpai.integrate import classify as icls
from puffpai.integrate import readme_steps, run_here

README = """# World Monitor

Badges and words.

## Quick Start

```bash
git clone https://github.com/someone/worldmonitor.git
cd worldmonitor
npm install
npm run dev
```

Open [localhost:3000](http://localhost:3000). It runs with no settings.

For variants:

```bash
npm run dev:tech       # another site
```

## Contributing

```bash
npm run typecheck
```
"""


def test_readme_quick_start_is_read_as_commands():
    got = readme_steps.read(README)
    assert got == {"steps": ["npm install", "npm run dev"], "port": 3000, "runtime": "node"}


def test_readme_without_a_run_heading_uses_its_shell_blocks():
    text = "# Thing\n\n```sh\n$ pip install -r requirements.txt\n$ python app.py\n```\n"
    got = readme_steps.read(text)
    assert got["steps"] == ["pip install -r requirements.txt", "python app.py"]
    assert got["runtime"] == "python"


def test_readme_drops_global_installs_and_prose_blocks():
    text = ("## Installation\n\n```bash\nnpm install -g thing\nsudo apt install x\n"
            "docker compose up -d\n```\n\n```json\n{\"npm\": 1}\n```\n")
    got = readme_steps.read(text)
    assert got["steps"] == ["docker compose up -d"] and got["runtime"] == "docker"
    assert readme_steps.read("# Nothing to run here\nJust words.")["steps"] == []


def test_an_app_that_also_offers_a_tool_server_is_an_app():
    assert sites.repo_type("worldmonitor", "Real-time global intelligence dashboard.",
                           ["ai", "dashboard", "mcp", "mcp-server"], "TypeScript") \
        != "mcp"
    assert sites.repo_type("weather-mcp", "An MCP server for weather.", ["mcp"],
                           "Python") == "mcp"


def _item(name, stars, lang="TypeScript", desc="A real-time news reader app.", **kw):
    return {"full_name": f"o/{name}", "name": name, "stargazers_count": stars,
            "language": lang, "description": desc, "topics": [], "archived": False,
            "fork": False, "html_url": f"https://github.com/o/{name}",
            "default_branch": "main", "license": {"spdx_id": "MIT"},
            "owner": {"login": "o"}, **kw}


def test_minimal_filters():
    assert apps.not_an_app(_item("awesome-news", 9000)) == "a list"
    assert apps.not_an_app(_item("notes", 10, lang=None))
    assert apps.not_an_app(_item("droid", 10, lang="Kotlin")) == "a phone app"
    assert apps.not_an_app(_item("reader", 10)) == ""


def test_jobs_search_once_rank_once_and_read_readmes_in_that_order(monkeypatch):
    listed = [dict(_item("ledger-app", 0, desc="A news ledger."), listed=True,
                   row_id="awesome_selfhosted:ledger-app", platforms=["Docker"],
                   topics=["Money"])]
    items = {("world", "news"): [_item("big", 900), _item("words-only", 500),
                                 _item("awesome-news", 9000), _item("lib", 800),
                                 _item("mid", 300)]}
    asked = []

    def search(words, stars=apps.MIN_STARS):
        asked.append(tuple(words))
        return items.get(tuple(words), [])

    readmes = {"o/big": README, "o/mid": "## Usage\n```\ndocker compose up\n```\n",
               "o/lib": README, "o/words-only": "# Words only",
               "o/ledger-app": "## Run\n```\ndocker compose up -d\n```\n"}
    calls = []

    def brain(messages, fmt):
        ask = json.loads(messages[-1]["content"])
        calls.append(messages[0]["content"].split("\n")[0])
        if "projects" in ask:       # the one order: the list row first, "lib" left out
            names = [p["name"] for p in ask["projects"]]
            return json.dumps({"order": [names.index(n) for n in
                                         ("ledger-app", "mid", "big") if n in names]})
        return json.dumps({"goal": ["world news"],
                           "jobs": [{"id": "j1", "phrases": ["world news"]}]})

    monkeypatch.setattr(apps, "catalog_items", lambda job: listed)
    monkeypatch.setattr(apps, "_search", search)
    monkeypatch.setattr(apps, "readme_text", lambda full, branch="": readmes.get(full, ""))
    monkeypatch.setattr(apps, "has_compose", lambda full, branch="": "")
    monkeypatch.setattr(apps, "_default_brain", lambda: brain)
    monkeypatch.setattr(apps, "_beyond", lambda text, brain=None: [])
    got = apps.for_jobs([{"id": "j1", "name": "World news dashboard", "action": "show"}],
                        "a WorldNews bot")
    assert asked == [("world", "news")]                       # one phrase, searched once
    assert calls == ["TASK=search_phrases", "TASK=rank_apps"]  # no judge per README
    # the brain's order first; "lib" (left out by it) still follows: never a veto
    assert [c["name"] for c in got] == ["ledger-app", "mid", "big", "lib"]
    big = got[2]
    assert big["type"] == "app" and big["direct"] and big["for_jobs"] == ["j1"]
    assert big["readme_steps"] == ["npm install", "npm run dev"] and big["readme_port"] == 3000
    assert got[0]["source"] == "awesome-selfhosted"
    assert [c["job_rank"]["j1"] for c in got] == [0, 1, 2, 3]


SPAM = dict(_item("china-dictatorship", 2500, lang="HTML",
                  desc="Archive. world news map report " * 2000), topics=["china"])


def _tonight(monkeypatch, brain):
    """The world news test of 2026-10-12, with GitHub's answers made up."""
    results = {
        ("world", "news"): [dict(_item("worldmonitor", 88000, desc="Real-time global "
                                       "intelligence dashboard with a map."),
                                 topics=["news", "map"]),
                            dict(SPAM), dict(SPAM, full_name="x/china-dictatorship")],
        ("news", "map"): [dict(SPAM, full_name="y/china-dictatorship"),
                          _item("news-map", 300, desc="World news on a map.")],
        ("news", "reader"): [_item("NewsBlur", 7000, lang="Python",
                                   desc="Personal news reader.")]}
    monkeypatch.setattr(apps, "_search", lambda words, stars=apps.MIN_STARS:
                        list(results.get(tuple(words), [])))
    monkeypatch.setattr(apps, "catalog_items", lambda job: [])
    monkeypatch.setattr(apps, "readme_text", lambda full, branch="": README)
    monkeypatch.setattr(apps, "has_compose", lambda full, branch="": "")
    monkeypatch.setattr(apps, "_beyond", lambda text, brain=None: [])
    monkeypatch.setattr(apps, "_default_brain", lambda: brain)
    jobs = [{"id": "map", "name": "World News Map", "action": "show"},
            {"id": "ctx", "name": "News context", "action": "ask"}]
    return apps.for_jobs(jobs, "A world news bot that tracks news on a map")


def test_spam_that_only_matches_in_its_readme_is_left_out(monkeypatch):
    got = _tonight(monkeypatch, None)
    names = {c["name"] for c in got}
    assert "china-dictatorship" not in names and "worldmonitor" in names
    assert apps.not_an_app(SPAM) == "a description stuffed with words"


def test_the_top_app_by_stars_leads_when_the_brain_fails(monkeypatch):
    def broken(messages, fmt):
        raise RuntimeError("no model")
    got = _tonight(monkeypatch, broken)
    first = {j: min((c for c in got if j in c["for_jobs"]),
                    key=lambda c: c["job_rank"][j])["name"] for j in ("map", "ctx")}
    assert first == {"map": "worldmonitor", "ctx": "worldmonitor"}


def test_the_brain_phrases_are_searched_and_its_order_wins(monkeypatch):
    def brain(messages, fmt):
        ask = json.loads(messages[-1]["content"])
        if "projects" in ask:
            names = [p["name"] for p in ask["projects"]]
            return json.dumps({"order": [names.index("worldmonitor")]})
        return json.dumps({"goal": ["world news"],
                           "jobs": [{"id": "map", "phrases": ["news map"]},
                                    {"id": "ctx", "phrases": ["news reader"]}]})
    got = _tonight(monkeypatch, brain)
    by_job = {j: [c["name"] for c in sorted((c for c in got if j in c["for_jobs"]),
                                             key=lambda c: c["job_rank"][j])]
              for j in ("map", "ctx")}
    assert by_job["map"][0] == "worldmonitor" and "news-map" in by_job["map"]
    assert by_job["ctx"][0] == "worldmonitor" and "NewsBlur" in by_job["ctx"]


def test_readme_never_takes_tests_or_lint_as_the_start():
    text = "## Dev\n```\npnpm install\npnpm test\npnpm typecheck\npnpm lint\n```\n"
    assert readme_steps.read(text)["steps"] == []
    text = "## Install\n```\npython -m pip install prophet\n```\n"
    assert readme_steps.read(text)["steps"] == []        # installs a library, starts nothing


def _direct(name, rank, jid="j1"):
    return {"ref": f"github:o/{name}", "name": name, "type": "app", "source": "github",
            "what": "A news app.", "url": f"https://github.com/o/{name}", "direct": True,
            "for_jobs": [jid], "job_rank": {jid: rank}, "hosts": [], "needs": [],
            "readme_steps": ["npm install", "npm run dev"], "readme_port": 3000}


def test_decide_uses_the_star_order_and_never_asks_the_brain():
    calls = []

    def brain(messages, fmt):
        calls.append(messages)
        return json.dumps({"answers": []})

    jobs = [S.new_job("World news dashboard", "The world's news on one screen", action="show",
                      subject=["world", "news"], output="text", needs=["none"], ask="News?")]
    jobs[0]["id"] = "j1"
    cands = [_direct("second", 1), _direct("first", 0), _direct("other-job", 0, jid="j9")]
    got = decide.decide(jobs, cands, brain=brain, embed=None)
    j = got["jobs"][0]
    assert j["route"] == S.A and j["pick"]["ref"] == "github:o/first"
    assert j["alternates"] == ["github:o/second"]
    assert not calls
    # found for a job this build does not have: not a "not used" row (2026-10-13:
    # that list holds only what was tried or left out for a real reason)
    assert "github:o/other-job" not in {d["ref"] for d in got["dropped"]}
    assert "github:o/other-job" not in j["ingredients"] + j["alternates"]


def test_the_yes_card_shows_the_readme_commands():
    c = dict(_direct("first", 0), check={"ok": True, "later": True}, **{"class": S.APP})
    job = {"id": "j1", "route": S.A, "ingredients": [c["ref"]], "alternates": []}
    rows = setup_mod.rows_for([job], {c["ref"]: c})
    app = next(r for r in rows if r["id"].startswith("program-app-"))
    assert "Its README says to run: npm install; npm run dev, then open localhost:3000" \
        in app["detail"]
    assert app["commands"] == ["npm install", "npm run dev"]


def test_it_runs_the_way_its_readme_says(tmp_path, monkeypatch):
    src = tmp_path / "app"
    src.mkdir()
    (src / "README.md").write_text(README, encoding="utf-8")
    (src / "package.json").write_text(json.dumps(
        {"name": "x", "scripts": {"dev": "vite", "build": "vite build"},
         "dependencies": {"vite": "5"}}), encoding="utf-8")
    (src / "package-lock.json").write_text("{}", encoding="utf-8")
    (src / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: nginx\n    ports:\n      - '8080:80'\n",
        encoding="utf-8")
    monkeypatch.setattr(run_here, "docker_route_ok", lambda: True)
    plan = run_here.detect(str(src), port=4321)
    assert plan["how"] == "node_server", plan
    assert "dev" in plan["start"]["args"]
    # A README that says Docker keeps Docker first.
    (src / "README.md").write_text("## Quick start\n```\ndocker compose up -d\n```\n",
                                   encoding="utf-8")
    assert run_here.detect(str(src), port=4321)["how"] == "compose"


def test_only_real_tunnel_tools_are_a_hidden_connection():
    assert not icls._tunnel_in("install.sh", "curl -fsSL https://x.example/i.sh | sh")  # noqa: SLF001
    assert not icls._tunnel_in("docs/README.md", "Use ngrok to try webhooks.")  # noqa: SLF001
    assert icls._tunnel_in("src/expose.js", "const t = require('ngrok');")  # noqa: SLF001
    assert icls._tunnel_in("run.sh", "cloudflared tunnel --url http://x")  # noqa: SLF001


def test_a_short_goal_keeps_the_jobs_tied_to_its_words():
    jobs = [{"name": "Global news tracker", "does": "", "subject": ["news"]},
            {"name": "Contextual update generator", "does": "", "subject": ["updates"]},
            {"name": "World event map", "does": "", "subject": ["world"]},
            {"name": "World news briefing", "does": "", "subject": ["world"]}]
    got = interpret.focus(jobs, "a WorldNews bot")
    assert [j["name"] for j in got] == ["Global news tracker", "World event map",
                                        "World news briefing"]
    assert interpret.split_camel("a WorldNews bot") == "a World News bot"


def test_big_apps_are_not_stopped_for_their_size():
    from puffpai.integrate import fetch
    assert fetch.MAX_ARCHIVE_BYTES >= 2 * 1024 ** 3
    assert fetch.MAX_EXTRACTED_BYTES >= 4 * 1024 ** 3
    assert os.path.basename(apps.__file__) == "apps.py"


def test_read_app_gives_the_brain_the_pages_visible_text(monkeypatch):
    from puffpai.integrate import host_tools
    html = ("<html><head><title>World Monitor</title><script>var x</script></head><body>"
            "<h1>Live news</h1><ul><li>Earthquake near the coast</li><li>Election results"
            "</li></ul><a href='/map'>Map</a></body></html>")
    from puffpai.integrate import page_reader
    monkeypatch.setattr(page_reader, "read", lambda url, wait_s=0: None)   # the plain fallback
    monkeypatch.setattr(host_tools, "plain_read", lambda url: host_tools.page_text(html))
    monkeypatch.setattr(host_tools, "_answers", lambda url: True)
    got = host_tools.read_page("http://127.0.0.1:5555/")
    assert got["ok"] and got["title"] == "World Monitor"
    assert got["text"].splitlines()[:3] == ["# Live news", "Earthquake near the coast",
                                            "Election results"]
    assert got["links"] == ["Map -> /map"] and "var x" not in got["text"]
    assert not host_tools.read_page("http://example.com/")["ok"]    # this computer only
    apps = [{"id": "wm", "name": "World Monitor", "ops": [], "cli": {},
             "status": {"state": "running", "url": "http://127.0.0.1:5555/", "can_run": True}}]
    monkeypatch.setattr(host_tools, "_apps", lambda: apps)
    names = [t.name for t in host_tools.provide()]
    assert names[0] == "read_app"
    assert host_tools._read({"app": "World Monitor", "path": "/map"})["url"] \
        == "http://127.0.0.1:5555/map"  # noqa: SLF001
    assert host_tools._read({"app": "worldmonitor"})["url"] == "http://127.0.0.1:5555/"  # noqa: SLF001


def test_route_a_is_working_only_when_the_brain_can_read_its_page():
    from puffpai.build import prove
    job = {"id": "j1", "route": "A", "ask": "What is on the dashboard?"}
    made = {"ok": True, "screen": {"integration_id": "wm", "url": "http://127.0.0.1:5/"},
            "made": {"integration_id": "wm", "app_name": "World Monitor"}}
    base = {"status": lambda iid: {"can_run": True, "state": "running",
                                   "url": "http://127.0.0.1:5/"},
            "page": lambda url: ""}
    page = '{"ok": true, "text": "' + "Earthquake near the coast and more news " * 3 + '"}'
    ok = prove.prove(job, {}, made=made,
                     hooks={**base, "read_ask": lambda j: (True, "", [("read_app", page)])})
    assert ok["state"] == "working"
    empty = prove.prove(job, {}, made=made, hooks={
        **base, "read_ask": lambda j: (True, "", [("read_app", '{"ok": false}')])})
    assert empty["state"] == "not_yet" and "no content" in empty["reason"]


def test_read_app_uses_what_a_person_sees_once_the_page_filled_in(monkeypatch):
    from puffpai.integrate import host_tools, page_reader
    text = "\n".join(["LIVE NEWS", "84", "⇅", "Bloomberg", "i",
                      "Digest coverage: 84 publishers, 283 items"])
    seen = {"title": "World Monitor", "text": text, "links": [["Countries", "/countries"]]}
    assert page_reader.read("http://example.com/") is None       # this computer only
    monkeypatch.setattr(page_reader, "read", lambda url, wait_s=0: seen)
    monkeypatch.setattr(host_tools, "_answers", lambda url: True)
    got = host_tools.read_page("http://127.0.0.1:5555/")
    assert got["ok"] and got["text"].splitlines() == [
        "LIVE NEWS", "Bloomberg", "Digest coverage: 84 publishers, 283 items"]
    assert got["links"] == ["Countries -> /countries"]
