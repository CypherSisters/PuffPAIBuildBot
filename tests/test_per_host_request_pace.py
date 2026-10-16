"""Per-host pace.

raw.githubusercontent.com may be read 4 times a second; every other host stays
at once a second. The table is exact host names only, so a look-alike or a
sub-host never gets the faster pace. No network: a fake transport and a
virtual clock.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

from puffpai import source_fetch

RAW = "raw.githubusercontent.com"


@pytest.fixture(autouse=True)
def _fresh():
    source_fetch.clear_caches()
    yield
    source_fetch.clear_caches()


def test_the_table_holds_exactly_her_one_exception():
    assert source_fetch.HOST_MIN_INTERVAL_S == 1.0
    assert source_fetch.HOST_MIN_INTERVAL_BY_HOST == {RAW: 0.25}


@pytest.mark.parametrize("host,expected", [
    (RAW, 0.25),
    ("RAW.GitHubUserContent.com", 0.25),
    ("github.com", 1.0),
    ("api.github.com", 1.0),
    ("codeload.github.com", 1.0),
    ("githubusercontent.com", 1.0),
    ("objects.githubusercontent.com", 1.0),
    ("x." + RAW, 1.0),
    (RAW + ".evil.example", 1.0),
    (RAW + ".", 1.0),
    ("raw-githubusercontent.com", 1.0),
    ("registry.npmjs.org", 1.0),
    ("en.wikipedia.org", 1.0),
    ("", 1.0),
    (None, 1.0),
])
def test_only_the_exact_host_is_faster(host, expected):
    assert source_fetch.host_min_interval(host) == expected


def _waits(host: str, n: int) -> list[float]:
    """Waits for *n* requests to *host* asked all at the same moment."""
    now = [100.0]
    slept: list[float] = []
    for _ in range(n):
        before = len(slept)
        source_fetch._rate_limit(host, slept.append, lambda: now[0])  # noqa: SLF001
        if len(slept) == before:
            slept.append(0.0)
    return slept


def test_raw_reads_are_four_a_second():
    assert _waits(RAW, 5) == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])


@pytest.mark.parametrize("host", ["github.com", "codeload.github.com", "x." + RAW,
                                  "registry.npmjs.org"])
def test_every_other_host_stays_at_once_a_second(host):
    assert _waits(host, 4) == pytest.approx([0.0, 1.0, 2.0, 3.0])


def test_the_fast_host_and_a_slow_host_keep_their_own_pace():
    now = [0.0]
    slept: list[tuple[str, float]] = []
    for host in (RAW, "github.com", RAW, "github.com", RAW):
        source_fetch._rate_limit(host, lambda s, h=host: slept.append((h, s)),  # noqa: SLF001
                                 lambda: now[0])
    assert slept == [(RAW, pytest.approx(0.25)), ("github.com", pytest.approx(1.0)),
                     (RAW, pytest.approx(0.5))]


class _Resp:
    def __init__(self, body=b"ok"):
        self.status, self._body, self.headers = 200, body, {}

    def getheader(self, name, default=None):
        return default

    def read(self, n=-1):
        out, self._body = (self._body, b"") if n is None or n < 0 else (self._body[:n], self._body[n:])
        return out

    def close(self):
        pass


def test_the_real_request_path_paces_raw_reads_at_a_quarter_second():
    """get_small (the guarded request path) asks the table, not a fixed second."""
    sent: list[str] = []

    def transport(url, headers, timeout):
        sent.append(url)
        return _Resp()

    slept: list[float] = []
    for i in range(3):
        body, reason = source_fetch.get_small(
            f"https://{RAW}/o/r/{'a' * 40}/f{i}.txt", allowed_hosts={RAW}, max_bytes=100,
            honour_robots=False, transport=transport, resolver=lambda h: ["140.82.112.3"],
            sleeper=slept.append, clock=lambda: 50.0)
        assert body == b"ok", reason
    assert len(sent) == 3
    assert slept == pytest.approx([0.25, 0.5])


def _effect_module():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "simulate_host_rate.py")
    spec = importlib.util.spec_from_file_location("simulate_host_rate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_more_projects_are_looked_at_closely_per_job_and_the_table_is_left_as_it_was():
    mod = _effect_module()
    rows = mod.table()
    assert source_fetch.HOST_MIN_INTERVAL_BY_HOST == {RAW: 0.25}
    assert source_fetch.HOST_MIN_INTERVAL_S == 1.0
    for r in rows:
        assert r["now (table)"] >= r["before (1 per second)"], r
    from puffpai.catalogs import where_to_look as wtl
    late = [r for r in rows if r["window_s"] > 60 and r["files_per_project"] == 8][0]
    assert late["now (table)"] == wtl.READ_PER_JOB
    assert late["before (1 per second)"] < wtl.READ_PER_JOB
    assert sum(r["now (table)"] for r in rows) >= 3 * max(1, sum(r["before (1 per second)"]
                                                                 for r in rows))


def test_the_simulation_counts_only_whole_projects_inside_the_window():
    mod = _effect_module()
    # 1 reader, 1 s pace, 0 s answers, 10 s window, 1 s grace: requests at 0..9,
    # so 2 projects of 4 files (8 requests) and not a third.
    assert mod.projects_read(window_s=10, files_per_project=4, projects=5, workers=1,
                             grace_s=1.0, answer_s=0.0, interval_s=1.0) == 2
