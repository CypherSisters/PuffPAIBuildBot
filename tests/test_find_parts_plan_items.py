"""find_parts.plan_items with fakes for the outbound screen and the trust floor (pinned signatures, no network)."""
from __future__ import annotations

import copy
import re
import sys
import types
from dataclasses import dataclass, field

import pytest

from puffpai.catalogs import needs_rules
from puffpai.wizard import find_parts, needs

GOAL = ("I want to build a wedding planner bot. My brother just got engaged. I'll need to be "
        "able to plan the budget, keep a guest list and find vendors.")


# -- fake security.outbound_query ------------------------------------------
def make_outbound_query() -> types.ModuleType:
    mod = types.ModuleType("puffpai.security.outbound_query")
    token = object()

    @dataclass(frozen=True)
    class Sendable:
        phrase: str
        source_hint: str = ""
        subject: bool = False
        _t: object = field(default=None, repr=False, compare=False)

        def __post_init__(self):
            if self._t is not token:
                raise TypeError("only outbound_query makes a Sendable")

    @dataclass
    class ScreenResult:
        sendable: list
        held: list
        subject: str
        subject_notes: list

    def _personal(goal):
        return {w for w, why in needs_rules.personal_words(goal).items()}

    def screen(phrases, *, goal, allowed_words=(), home=None, source_hint=""):
        personal = _personal(goal) - {str(w).lower() for w in allowed_words}
        subject = needs_rules.subject_of(goal)
        out, held = [], []
        for p in phrases:
            text = str(p)
            words = text.split()
            bad = [w for w in words if w in personal]
            if bad:
                held.append({"phrase": text, "words": [{"word": w, "why": "name"} for w in bad]})
            elif re.fullmatch(r"[a-z0-9]+( [a-z0-9]+){0,3}", text):
                out.append(Sendable(text, source_hint, False, token))
        return ScreenResult(out, held, subject, [])

    def subject_sendable(goal, *, home=None):
        s = needs_rules.subject_of(goal)
        return Sendable(s, "subject", True, token) if s else None

    def is_sendable(obj):
        return isinstance(obj, Sendable)

    def url_quote(s):
        if not isinstance(s, Sendable):
            raise TypeError("not sendable")
        return s.phrase.replace(" ", "+")

    mod.Sendable, mod.ScreenResult, mod.screen = Sendable, ScreenResult, screen
    mod.subject_sendable, mod.is_sendable, mod.url_quote = subject_sendable, is_sendable, url_quote
    return mod


# -- fake where_to_look + trust_floor ----------------------------------------
def repo_find(owner, name, what, *, stars=50, mode="static_web", licence="MIT", readme="",
              lines=None, part="code-app", keeps=True, topics=()):
    return {"schema": "found/v1", "id": f"github:{owner}/{name}", "source": "github",
            "part": part, "name": name, "friendly": name, "what_it_is": what,
            "url": f"https://github.com/{owner}/{name}",
            "repo": {"host": "github.com", "owner": owner, "name": name, "default_branch": "main",
                     "pinned_commit": None, "pinned_at": None, "stars": stars,
                     "pushed_at": "2026-06-18", "archived": False, "fork": False,
                     "license_reported": licence, "license_read": None, "size_kb": 320,
                     "language": "HTML", "topics": list(topics), "latest_release": None,
                     "in_awesome_selfhosted": False, "renamed_from": None},
            "details": {"readme_text": readme, "feature_lines": list(lines or []),
                        "keeps_data": keeps, "runtime_hosts_guess": []},
            "mode_guess": mode,
            "integrable": mode in ("static_web", "web_build", "cli_tool", "data_template"),
            "meaning": None}


class FakeWhere:
    def __init__(self, *, local=None, online=None, clock=None, step=0.0, later=False):
        self.local = local or {}
        self.online = online or {}
        self.clock, self.step, self._later = clock, step, later
        self.calls: list[tuple] = []
        self.mod = types.ModuleType("puffpai.catalogs.where_to_look")
        m = self.mod
        m.find_local = self.find_local
        m.find_online = self.find_online
        m.details = self.details
        m.new_budget = self.new_budget
        m.route = lambda kind: [{"id": "github_repo_search", "label": "GitHub", "role": "live",
                                 "enabled": True}]
        m.license_words = lambda x: f"Free to use and change ({x})." \
            if str(x).upper() in ("MIT", "APACHE-2.0", "GPL-3.0") else None
        m.snapshot_vectors = lambda stem: None
        m.registry = lambda: []

    def find_local(self, job, *, kind, limit_per_source=25):
        self.calls.append(("local", job["id"], kind))
        return copy.deepcopy(self.local.get(job["id"], []))

    def find_online(self, job, phrases, *, kind, budget, progress=None):
        oq = sys.modules["puffpai.security.outbound_query"]
        for p in phrases:
            assert oq.is_sendable(p), p
        self.calls.append(("online", job["id"], kind, [p.phrase for p in phrases]))
        if self.clock is not None:
            self.clock[0] += self.step
        if progress:
            progress(f"GitHub: {phrases[0].phrase} ... 1 fit" if phrases else "GitHub")
        return copy.deepcopy(self.online.get(job["id"], []))

    def details(self, found, *, budget):
        self.calls.append(("details", found["id"]))
        return found

    def new_budget(self, *, wizard):
        outer = self

        class Budget:
            def remaining(self, source):
                return None

            def spent(self):
                return {}

            def later(self, source):
                return outer._later
        return Budget()


def make_trust_floor() -> types.ModuleType:
    mod = types.ModuleType("puffpai.catalogs.trust_floor")

    def check(found):
        repo = found.get("repo") or {}
        if not repo.get("license_reported"):
            return False, "", ["license"]
        if (repo.get("stars") or 0) >= 5:
            return True, "stars", []
        return False, "", ["few stars"]
    mod.check = check
    mod.pretick_allowed = lambda found: check(found)[1] in ("stars", "listed")
    return mod


def _needs(jobs: list[dict]) -> dict:
    return {"schema": "needs/v1", "prompt": "rules", "model": "", "goal_digest": needs.goal_digest(GOAL),
            "kind": "wedding planner", "kind_source": "rules",
            "subject": {"phrase": "wedding planner", "notes": [], "sendable": True},
            "jobs": jobs, "held": [], "private_left_out": ["brother", "engaged"], "helper": "ready",
            "timings": {}}


def _job(jid, name, does, source, phrases, kinds=("code-app",), ticked=True):
    return {"id": jid, "name": name, "does": does, "source": source, "seen_in": [],
            "ticked": ticked, "learn": False,
            "parts": [{"kind": k, "search": [{"phrase": p, "state": "out", "why": []}
                                             for p in phrases]} for k in kinds]}


BUDGET_JOB = _job("j1", "Plan the budget", "Plans what each part costs and tracks what is spent.",
                  "asked", ["wedding budget", "brother wedding budget"], ("code-app", "data-template"))
GUESTS_JOB = _job("j2", "Guest list", "Keeps the guest list, plus-ones and RSVPs.", "usual",
                  ["wedding guest list"])
VENDOR_JOB = _job("j3", "Find vendors", "Finds and compares wedding vendors.", "owner",
                  ["wedding vendors"])

BUDGET_APP = repo_find("acme", "wedding-budget",
                       "A wedding budget planner: plan what each part costs and track what is spent.",
                       lines=["Plan costs per category", "Track what is spent against the budget"])
SERVER_APP = repo_find("acme", "wedding-share",
                       "A self-hosted wedding budget and cost tracking server for planners.",
                       mode="server_app", stars=900, licence="GPL-3.0",
                       lines=["Track the wedding budget and payments", "Plan costs per vendor"])
PAGE = repo_find("acme", "our-wedding", "Our wedding website with the budget and RSVP",
                 stars=40, topics=["wedding-website"])
BUILD_APP = repo_find("acme", "rsvp-board", "Guest list and RSVP tracker for weddings.",
                      mode="web_build", lines=["Guest list with RSVP status", "Plus-ones and meals"])


@pytest.fixture
def lanes(monkeypatch):
    """Installs the fake outbound screen and trust floor; returns a function that installs a FakeWhere."""
    monkeypatch.setitem(sys.modules, "puffpai.security.outbound_query", make_outbound_query())
    monkeypatch.setitem(sys.modules, "puffpai.catalogs.trust_floor", make_trust_floor())
    find_parts._MEMO.clear()
    find_parts._FOUND.clear()
    needs._GOALS[needs.goal_digest(GOAL)] = (GOAL, None)

    def install(where: FakeWhere) -> FakeWhere:
        monkeypatch.setitem(sys.modules, "puffpai.catalogs.where_to_look", where.mod)
        return where
    yield install
    find_parts._MEMO.clear()
    find_parts._FOUND.clear()


def _online_order(where: FakeWhere) -> list[str]:
    return [c[1] for c in where.calls if c[0] == "online"]


def test_asked_jobs_are_searched_first_and_the_subject_once(lanes, monkeypatch):
    # The searches start in this order; with one at a time they also run in it.
    monkeypatch.setattr(find_parts, "SEARCH_WORKERS", 1)
    where = lanes(FakeWhere())
    needs_doc = _needs([copy.deepcopy(GUESTS_JOB), copy.deepcopy(VENDOR_JOB),
                        copy.deepcopy(BUDGET_JOB)])
    out = find_parts.plan_items(needs_doc, goal=GOAL, online=True, allow_send=True, cfg={})
    order = _online_order(where)
    assert order[0] == "subject" and order.count("subject") == 1
    jobs_only = [j for j in order if j != "subject"]
    assert jobs_only[0] == "j1"                        # asked
    # Her own jobs (asked and added by her) are searched, every kind, before
    # the job the brain added.
    assert max(jobs_only.index("j1"), jobs_only.index("j3")) < jobs_only.index("j2")
    assert {"phrase": "wedding planner", "source": "GitHub"} in out["sends"]


def test_the_deadline_puts_the_rest_in_not_reached(lanes, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(find_parts.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(find_parts, "SEARCH_WORKERS", 1)   # a clock stepped per search
    where = lanes(FakeWhere(clock=clock, step=30.0))
    lines: list[str] = []
    needs_doc = _needs([copy.deepcopy(BUDGET_JOB), copy.deepcopy(GUESTS_JOB),
                        copy.deepcopy(VENDOR_JOB)])
    out = find_parts.plan_items(needs_doc, goal=GOAL, online=True, allow_send=True, cfg={},
                                on_line=lines.append)
    # Her jobs (asked j1, added by her j3) are searched first: the job the
    # brain added is the one the time runs out for (final directive).
    assert out["not_reached"] == ["j2"]
    assert find_parts.LINE_STOPPED in lines
    assert all(c[1] != "j2" for c in where.calls if c[0] == "online")
    assert find_parts.NOTHING_FOUND not in out["notes"].get("j2", [])


def test_a_source_marked_later_means_not_reached(lanes):
    lanes(FakeWhere(later=True))
    out = find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL, online=True,
                                allow_send=True, cfg={})
    assert out["not_reached"] == ["j1"]


def test_memo_second_call_does_no_searches(lanes):
    where = lanes(FakeWhere(online={"j1": [BUDGET_APP]}))
    needs_doc = _needs([copy.deepcopy(BUDGET_JOB)])
    first = find_parts.plan_items(needs_doc, goal=GOAL, online=True, allow_send=True, cfg={})
    n = len(where.calls)
    second = find_parts.plan_items(needs_doc, goal=GOAL, online=True, allow_send=True, cfg={})
    assert len(where.calls) == n and second == first
    find_parts.plan_items(needs_doc, goal=GOAL, online=False, allow_send=True, cfg={})
    assert len(where.calls) > n  # another key is another look


def test_a_phrase_that_is_not_sendable_never_reaches_the_searcher(lanes):
    where = lanes(FakeWhere())
    find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL, online=True,
                          allow_send=True, cfg={})
    sent = [p for c in where.calls if c[0] == "online" for p in c[3]]
    assert "wedding budget" in sent
    assert all("brother" not in p for p in sent)


def test_offline_or_no_yes_searches_only_this_computer(lanes):
    where = lanes(FakeWhere(local={"j1": [BUDGET_APP]}, online={"j1": [SERVER_APP]}))
    for online, allow in ((False, True), (True, False)):
        find_parts._MEMO.clear()
        out = find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL,
                                    online=online, allow_send=allow, cfg={})
        assert _online_order(where) == [] and out["sends"] == []
        assert [i["apply"]["ref"] for i in out["items"]] == ["github:acme/wedding-budget"]


def test_found_for_returns_the_server_record_and_ignores_client_extras(lanes):
    lanes(FakeWhere(online={"j1": [BUDGET_APP]}))
    out = find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL, online=True,
                                allow_send=True, cfg={})
    item = out["items"][0]
    rec = find_parts.found_for(item["id"])
    assert rec["id"] == "github:acme/wedding-budget" and rec["meaning"]["job"] == "j1"
    # what a browser could send back changes nothing
    item["apply"]["ref"] = "github:evil/other"
    item["facts"]["mode"] = "cli_tool"
    item["facts"]["license"] = "MIT"
    rec["mode_guess"] = "cli_tool"
    again = find_parts.found_for(item["id"])
    assert again["mode_guess"] == "static_web" and again["id"] == "github:acme/wedding-budget"
    assert find_parts.found_for("apps_code:github:evil/other") is None


def test_plan_lines_have_the_pinned_shape(lanes):
    lanes(FakeWhere(online={"j1": [BUDGET_APP, SERVER_APP, PAGE], "j2": [BUILD_APP]}))
    lines: list[str] = []
    out = find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB), copy.deepcopy(GUESTS_JOB)]),
                                goal=GOAL, online=True, allow_send=True, cfg={},
                                on_line=lines.append)
    by_ref = {i["apply"]["ref"]: i for i in out["items"]}
    budget = by_ref["github:acme/wedding-budget"]
    assert re.match(r"^[a-z][a-z0-9_]*:[A-Za-z0-9_.:/-]{1,160}$", budget["id"])
    assert len(budget["id"]) <= 160 and budget["section"] == "apps_code"
    assert "github.com/acme/wedding-budget" in budget["label"] and len(budget["label"]) <= 80
    assert budget["need"] == "j1" and budget["why"] == "helps with: Plan the budget"
    assert budget["apply"]["kind"] == "code_project" and budget["needs_yes"] is True
    assert budget["outside_host"] == "codeload.github.com"
    assert set(budget["facts"]) == {"stars", "changed", "license", "mode", "trust_path",
                                    "hosts_all", "runtime_hosts", "size_bytes", "box"}
    assert budget["facts"]["changed"] == "2026-06" and budget["facts"]["trust_path"] == "stars"
    assert "last changed June 2026" in budget["detail"]
    assert budget["default_on"] == (not budget["might_help"])
    board = by_ref.get("github:acme/rsvp-board")
    if board is not None:
        assert board["requires"] == ["box_setup"] and board["facts"]["box"] is True
    # the server app is a note with its reason, the page is left out
    notes = " ".join(n for ns in out["notes"].values() for n in ns)
    assert "github.com/acme/wedding-share, needs its own server" in notes
    assert "github:acme/our-wedding" not in by_ref and out["left_out"] >= 1
    assert any(line.startswith("Checking what fits your jobs ...") for line in lines)


def test_a_searched_job_with_nothing_usable_gets_the_plain_note(lanes):
    lanes(FakeWhere(online={"j1": [PAGE]}))
    out = find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL, online=True,
                                allow_send=True, cfg={})
    assert out["notes"]["j1"] == [find_parts.NOTHING_FOUND]
    assert out["items"] == [] and out["left_out"] == 1


def test_unticked_jobs_are_not_searched(lanes):
    where = lanes(FakeWhere())
    job = copy.deepcopy(GUESTS_JOB)
    job["ticked"] = False
    out = find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB), job]), goal=GOAL, online=True,
                                allow_send=True, cfg={})
    assert "j2" not in [c[1] for c in where.calls]
    assert "j2" not in out["notes"]


def test_without_where_to_look_nothing_is_searched(monkeypatch):
    monkeypatch.setitem(sys.modules, "puffpai.catalogs.where_to_look", None)
    find_parts._MEMO.clear()
    out = find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL, online=True,
                                allow_send=True, cfg={})
    find_parts._MEMO.clear()
    assert out["items"] == [] and out["sends"] == []
    assert out["notes"] == {"j1": [find_parts.NOT_IN_BUILD]}


def test_result_keys_are_the_pinned_ones(lanes):
    lanes(FakeWhere())
    out = find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL, online=False,
                                allow_send=False, cfg={})
    assert set(out) == {"items", "notes", "not_reached", "similar", "sends", "left_out"}


class SlowWhere(FakeWhere):
    """One job's search waits on a slow site; the others answer at once."""

    def __init__(self, *, slow_job: str, wait_s: float, **kw):
        super().__init__(**kw)
        self.slow_job, self.wait_s = slow_job, wait_s

    def find_online(self, job, phrases, *, kind, budget, progress=None):
        import time as _t
        got = super().find_online(job, phrases, kind=kind, budget=budget, progress=progress)
        if job["id"] == self.slow_job:
            _t.sleep(self.wait_s)
        return got


def test_a_slow_site_does_not_hold_up_the_other_jobs(lanes, monkeypatch):
    # Each Codeberg search took 30 s, and every job
    # after it was never searched. Now the jobs are asked a few at a time: a
    # slow answer holds only its own job, and one that comes after the search
    # time is not used.
    monkeypatch.setattr(find_parts, "SEARCH_DEADLINE_S", find_parts.CLOSER_LOOK_S + 1.5)
    monkeypatch.setattr(find_parts, "LOOK_GRACE_S", 0.3)
    late = repo_find("acme", "late-budget", "A wedding budget planner that answered late.")
    where = lanes(SlowWhere(slow_job="j1", wait_s=4.0, online={
        "j1": [late], "j2": [BUILD_APP], "j3": [BUDGET_APP]}))
    needs_doc = _needs([copy.deepcopy(BUDGET_JOB), copy.deepcopy(GUESTS_JOB),
                        copy.deepcopy(VENDOR_JOB)])
    import time as _t
    t0 = _t.monotonic()
    out = find_parts.plan_items(needs_doc, goal=GOAL, online=True, allow_send=True, cfg={})
    assert _t.monotonic() - t0 < 3.9                     # the plan did not wait for j1
    searched = {c[1] for c in where.calls if c[0] == "online"}
    assert {"j2", "j3"} <= searched                       # later jobs were still searched
    assert out["not_reached"] == []
    ev = find_parts.last_evidence()
    assert "github:acme/late-budget" not in {f["id"] for f in ev["finds"]}
