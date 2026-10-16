"""The search budget: polite per build, honest about waits, never pre-checks.

What must hold (recorded headers, fake clock, no network):

* a 403 whose reset is at most 60 s away: the owner sees "waiting N seconds",
  the call is tried once more;
* a reset further away: the source is marked later for this build and nothing
  more is sent to it;
* the headers on every answer are read (a 200 saying 0 left makes the next call wait);
* repeats use the cache, and a stale entry is asked with ``If-None-Match``;
* ``/rate_limit`` is never called; per-build caps hold; the state is persisted.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
import types
import urllib.parse

import pytest

from puffpai import source_fetch
from puffpai.catalogs import search_budget, where_to_look as w
from puffpai.catalogs import searchers as S

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "recorded_web")
T0 = 1_790_484_000.0


def read(name: str) -> bytes:
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


def fake_outbound(monkeypatch):
    mod = types.ModuleType("puffpai.security.outbound_query")

    @dataclasses.dataclass(frozen=True)
    class Sendable:
        phrase: str
        source_hint: str = ""
        subject: bool = False

    mod.Sendable = Sendable
    mod.is_sendable = lambda o: isinstance(o, Sendable)

    def url_quote(s):
        if not isinstance(s, Sendable):
            raise TypeError
        return urllib.parse.quote_plus(s.phrase)

    mod.url_quote = url_quote
    monkeypatch.setitem(sys.modules, "puffpai.security.outbound_query", mod)
    return mod


class Clock:
    def __init__(self, t=T0):
        self.t = t
        self.slept: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class Net:
    """Answers queued per call; records every call."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def __call__(self, url, *, allowed_hosts, max_bytes, service, honour_robots=True,
                 allow_truncated=False, headers=None, key_id=None, transport=None, resolver=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "key_id": key_id})
        ans = self.answers.pop(0) if self.answers else {"status": 200, "body": b'{"items": []}'}
        return {"body": ans.get("body"), "reason": "ok", "status": ans["status"],
                "truncated": False, "headers": ans.get("headers", {})}


def ok_answer(**headers):
    base = json.loads(read("github_rate_headers_200.json"))["headers"]
    base.update(headers)
    return {"status": 200, "body": read("github_search_wedding.json"), "headers": base}


def forbidden(reset_in: float, clock: Clock):
    rec = json.loads(read("github_search_403.json"))
    hdr = dict(rec["headers"], **{"x-ratelimit-reset": str(int(clock.t + reset_in))})
    return {"status": 403, "body": json.dumps(rec["body"]).encode(), "headers": hdr}


def github_only(monkeypatch):
    """These tests measure the budget on one source and one call per search:
    only the GitHub row is routed, and only each phrase's first query shape."""
    from puffpai.catalogs.searchers import github
    real = w._route_all
    monkeypatch.setattr(w, "_route_all", lambda kind: [r for r in real(kind)
                                                       if r["id"] == "github_repo_search"])
    monkeypatch.setattr(github, "SECOND_SHAPE_RESERVE", 10_000)


def search(monkeypatch, net, clock, b=None, phrase="party planner"):
    oq = fake_outbound(monkeypatch)
    github_only(monkeypatch)
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    b = b or search_budget.SearchBudget(wizard=True, clock=clock, sleeper=clock.sleep)
    lines: list[str] = []
    found = w.find_online({"id": "j1"}, [oq.Sendable(phrase, "j1")], kind="code-app",
                          budget=b, progress=lines.append)
    return found, lines, b


def test_recorded_403_with_a_near_reset_waits_and_retries_once(monkeypatch):
    clock = Clock()
    net = Net([forbidden(40, clock), ok_answer()])
    found, lines, b = search(monkeypatch, net, clock)
    assert found and len(net.calls) == 2
    assert clock.slept == [40.0]
    assert "GitHub asked BuildBot to slow down, so it is waiting 40 seconds." in lines
    assert b.later("github_search") is False and b.spent()["github_search"] == 2


def test_a_far_reset_marks_the_source_later(monkeypatch):
    clock = Clock()
    net = Net([forbidden(1800, clock)])
    found, lines, b = search(monkeypatch, net, clock)
    assert found == [] and len(net.calls) == 1 and clock.slept == []
    assert b.later("github_search") is True and b.remaining("github_search") == 0
    assert search_budget.later_line("github_search") in lines
    found2, _, _ = search(monkeypatch, net, clock, b=b, phrase="guest seating")
    assert found2 == [] and len(net.calls) == 1        # nothing more sent this build


def test_a_second_refusal_after_the_wait_marks_later(monkeypatch):
    clock = Clock()
    net = Net([forbidden(10, clock), forbidden(10, clock)])
    found, _lines, b = search(monkeypatch, net, clock)
    assert found == [] and len(net.calls) == 2 and b.later("github_search")


def test_headers_on_every_answer_are_read(monkeypatch):
    clock = Clock()
    net = Net([ok_answer(**{"x-ratelimit-remaining": "0",
                            "x-ratelimit-reset": str(int(T0 + 25))}), ok_answer()])
    _f, _l, b = search(monkeypatch, net, clock, phrase="seat chart")
    _f2, lines, _ = search(monkeypatch, net, clock, b=b, phrase="guest list")
    assert clock.slept == [25.0] and len(net.calls) == 2
    assert any("waiting 25 seconds" in line for line in lines)


def test_retry_after_is_honoured(monkeypatch):
    clock = Clock()
    net = Net([{"status": 429, "headers": {"retry-after": "12"}}, ok_answer()])
    found, _lines, _b = search(monkeypatch, net, clock)
    assert found and clock.slept == [12.0]


def test_our_own_pace_is_ten_searches_a_minute(monkeypatch):
    clock = Clock()
    b = search_budget.SearchBudget(wizard=True, clock=clock, sleeper=clock.sleep,
                                   caps=dict(search_budget.CAPS_WIZARD, github_search=20))
    for _ in range(10):
        assert b.take("github_search")
    assert clock.slept == []
    assert b.take("github_search") and clock.slept == [60.0]


def test_the_cache_answers_repeats_and_stale_entries_send_the_etag(monkeypatch):
    clock = Clock()
    etagged = ok_answer(etag='W/"abc123"')
    net = Net([etagged, {"status": 304, "headers": {}}])
    found1, _l, _b = search(monkeypatch, net, clock)
    found2, _l, b2 = search(monkeypatch, net, clock)         # fresh: no call at all
    assert len(net.calls) == 1 and b2.spent() == {}
    assert [f["id"] for f in found1] == [f["id"] for f in found2]
    clock.t += S.CACHE_TTL_S + 1                              # stale: asked with the etag
    found3, _l, _b = search(monkeypatch, net, clock)
    assert len(net.calls) == 2
    assert net.calls[1]["headers"]["if-none-match"] == 'W/"abc123"'
    assert [f["id"] for f in found3] == [f["id"] for f in found1]


def test_never_calls_rate_limit(monkeypatch):
    clock = Clock()
    net = Net([forbidden(30, clock), ok_answer(), forbidden(900, clock)])
    search(monkeypatch, net, clock)
    search(monkeypatch, net, clock, phrase="table plan")
    assert net.calls and not any("rate_limit" in c["url"] for c in net.calls)
    src = os.path.join(HERE, "..", "src", "puffpai", "catalogs")
    for root, _dirs, files in os.walk(src):
        for n in files:
            if n.endswith(".py"):
                with open(os.path.join(root, n), encoding="utf-8") as fh:
                    assert "api.github.com/rate_limit" not in fh.read(), n


def test_per_build_caps_and_readers():
    b = search_budget.new_budget(wizard=True)
    assert b.remaining("github_search") == 10 and b.remaining("no_such_source") is None
    for _ in range(3):
        assert b.take("github_core", "github_core_pin")
    assert b.take("github_core", "github_core_pin") is False      # 3 pin fallbacks a build
    assert b.spent() == {"github_core": 3, "github_core_pin": 3}
    add = search_budget.new_budget(wizard=False)
    assert add.remaining("github_search") < b.remaining("github_search")
    caps = search_budget.CAPS_WIZARD
    # The search caps are the spec's; the reading caps are raised to look
    # closely at 20 or more finds per job: reading spends no search.
    assert (caps["github_search"], caps["github_core"], caps["github_core_pin"],
            caps["github_core_tree"], caps["codeberg"], caps["npm"], caps["commons"],
            caps["openverse"]) == (10, 8, 3, 5, 2, 4, 2, 2)
    assert (caps["github_raw"], caps["github_git"], caps["details"]) == (800, 100, 100)
    assert (caps["codeberg_git"], caps["codeberg_raw"]) == (20, 120)
    for name, cap in search_budget.CAPS_ADD.items():
        assert cap <= caps[name], name                     # the Add page is never bigger


def test_state_is_persisted_per_source(monkeypatch):
    clock = Clock()
    net = Net([forbidden(1800, clock)])
    search(monkeypatch, net, clock)
    with open(search_budget.state_path(), encoding="utf-8") as fh:
        saved = json.load(fh)
    rec = saved["sources"]["github_search"]
    assert saved["schema"] == "search_budget/v1"
    assert rec["remaining"] == 0 and rec["reset"] == int(T0 + 1800) and rec["resource"] == "search"
    search_budget.forget()
    b = search_budget.SearchBudget(wizard=True, clock=clock, sleeper=clock.sleep)
    assert b.take("github_search") is False and b.later("github_search")   # restart remembers


def test_the_older_fetch_path_waits_sixty_seconds_once(monkeypatch):
    oq = fake_outbound(monkeypatch)
    github_only(monkeypatch)
    monkeypatch.delattr(source_fetch, "fetch_small", raising=False)
    answers = [(None, "The library answered 403, so there is nothing to read."),
               (read("github_search_wedding.json"), "ok")]
    monkeypatch.setattr(source_fetch, "get_small", lambda url, **kw: answers.pop(0))
    clock = Clock()
    b = search_budget.SearchBudget(wizard=True, clock=clock, sleeper=clock.sleep)
    lines: list[str] = []
    found = w.find_online({"id": "j1"}, [oq.Sendable("party planner", "j1")], kind="code-app",
                          budget=b, progress=lines.append)
    assert found and clock.slept == [60.0] and answers == []
    # No headers, so no reason given: the owner is not told GitHub asked to slow down.
    assert lines[0] == ("GitHub turned BuildBot away, so it is waiting 60 seconds before it "
                        "tries once more.")
    assert not any("slow down" in line for line in lines)


def test_wait_lines_say_one_second_and_why():
    """No "1 seconds", and the reason must be the true one."""
    assert search_budget.waiting_line("github_search", 1) == \
        "GitHub asked BuildBot to slow down, so it is waiting 1 second."
    assert search_budget.waiting_line("github_search", 2) == \
        "GitHub asked BuildBot to slow down, so it is waiting 2 seconds."
    assert search_budget.waiting_line("openverse", 1, search_budget.WHY_PACE) == \
        "BuildBot is waiting 1 second so it does not ask Openverse too often."


@pytest.mark.parametrize("answer", [
    {"status": 403, "headers": {"x-ratelimit-remaining": "0",
                                "x-ratelimit-reset": str(int(T0 - 50))}},    # reset already past
    {"status": 403, "headers": {"retry-after": "-5"}},
])
def test_a_limit_already_over_waits_one_second(monkeypatch, answer):
    clock = Clock()
    net = Net([answer, ok_answer()])
    found, lines, _b = search(monkeypatch, net, clock)
    assert found and clock.slept == [1.0]
    assert lines[0] == "GitHub asked BuildBot to slow down, so it is waiting 1 second."
    assert not any("seconds" in line for line in lines)


def test_our_own_pace_says_it_is_our_pace():
    clock = Clock()
    b = search_budget.SearchBudget(wizard=True, clock=clock, sleeper=clock.sleep,
                                   caps=dict(search_budget.CAPS_WIZARD, github_search=20))
    for _ in range(10):
        assert b.take("github_search")
    lines: list[str] = []
    assert b.take("github_search", progress=lines.append)
    assert lines == ["BuildBot is waiting 60 seconds so it does not ask GitHub too often."]


def test_a_switched_off_service_is_not_waited_on(monkeypatch):
    oq = fake_outbound(monkeypatch)
    monkeypatch.delattr(source_fetch, "fetch_small", raising=False)
    monkeypatch.setattr(source_fetch, "get_small", lambda url, **kw: (
        None, 'The book library is switched off under "What leaves your computer".'))
    clock = Clock()
    b = search_budget.SearchBudget(wizard=True, clock=clock, sleeper=clock.sleep)
    assert w.find_online({"id": "j1"}, [oq.Sendable("party planner", "j1")], kind="code-app",
                         budget=b) == []
    assert clock.slept == []


def test_the_cache_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "CACHE_MAX_FILES", 10)
    monkeypatch.setattr(S, "CACHE_KEEP_FILES", 6)
    for n in range(12):
        p = tmp_path / f"{n:02d}.json"
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (T0 + n, T0 + n))
    assert S.prune_cache(str(tmp_path)) == 6
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{n:02d}.json" for n in range(6, 12)]


@pytest.mark.parametrize("headers, body", [
    ({"content-type": "text/plain"}, b"Repository access blocked"),
    ({"x-ratelimit-remaining": "7", "x-ratelimit-reset": str(int(T0 + 3000))},
     b'{"message": "Resource not accessible"}'),
])
def test_a_403_that_is_not_a_rate_limit_refuses_only_that_answer(monkeypatch, headers, body):
    clock = Clock()
    net = Net([{"status": 403, "headers": headers, "body": body}, ok_answer()])
    found, lines, b = search(monkeypatch, net, clock)
    assert found == [] and len(net.calls) == 1 and clock.slept == []
    assert b.later("github_search") is False
    assert not any("busy" in line or "slow down" in line for line in lines)
    found2, _, _ = search(monkeypatch, net, clock, b=b, phrase="guest seating")
    assert found2 and len(net.calls) == 2                   # the source is still usable


def test_a_secondary_limit_named_only_in_the_body_waits_once(monkeypatch):
    clock = Clock()
    body = b'{"message": "You have exceeded a secondary rate limit. Please wait."}'
    net = Net([{"status": 403, "headers": {"content-type": "application/json"}, "body": body},
               ok_answer()])
    found, lines, b = search(monkeypatch, net, clock)
    assert found and clock.slept == [60.0] and len(net.calls) == 2


def test_observe_verdicts():
    clock = Clock()
    b = search_budget.SearchBudget(wizard=True, clock=clock, sleeper=clock.sleep)
    assert b.observe("github_core", status=403, headers={"server": "x"}) == "refused"
    assert b.observe("github_core", status=403, headers={"server": "x"}, retried=True) == "refused"
    assert b.observe("github_core", status=404, headers={}) == "ok"
    assert b.observe("github_core", status=403, headers={
        "x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(T0 + 5))}) == "retry"
    assert b.observe("github_core", status=429, headers={"retry-after": "3"},
                     retried=True) == "later"
    assert b.later("github_core") is True
