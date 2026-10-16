"""Live check: real GitHub, Codeberg and npm, keyless, three unrelated jobs, within the caps.

Runs only with ``PUFFPAI_R3_LIVE=1``. The three jobs (budget tracker, plant
watering, workout log) run as one build: ``find_online`` with its
query shapes (GitHub web and most-stars shapes, Codeberg one word, npm), then
the closer look (``details_many``, parallel, 60 s deadline) at the 20 web
finds with most stars (small projects are read as one archive each). Each job must end with at least 2 finds that pass
the trust floor. Every per-build cap must hold. The call counts and timings
are printed (``-s``) for the proof file.

Keyless: ``GITHUB_TOKEN`` and ``GH_TOKEN`` must be unset; this check never sends
a key anyway.
"""
from __future__ import annotations

import importlib
import json
import os
import time

import pytest

from puffpai import source_fetch

#: The real (pinned) transport, captured before the suite's autouse fixture
#: replaces it with a refusal; put back only inside this live test.
_REAL_TRANSPORT = source_fetch._default_transport

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.environ.get("PUFFPAI_R3_LIVE") != "1",
                                 reason="live check: set PUFFPAI_R3_LIVE=1")]

JOBS = [("j1", "Budget tracker", "budget tracker"), ("j2", "Plant watering", "plant watering"),
        ("j3", "Workout log", "workout log")]
#: The closer look: 20 or more finds a job.
DETAILS_PER_JOB = 20
WEB = {"JavaScript", "HTML", "TypeScript"}


def test_three_unrelated_jobs_get_trusted_code_finds_within_the_caps(monkeypatch):
    assert not os.environ.get("GITHUB_TOKEN") and not os.environ.get("GH_TOKEN")
    oq = importlib.import_module("puffpai.security.outbound_query")
    from puffpai.catalogs import search_budget, where_to_look as w

    monkeypatch.setattr(source_fetch, "_default_transport", _REAL_TRANSPORT)
    budget = w.new_budget(wizard=True)
    lines: list[str] = []
    report = {}
    t_all = time.monotonic()
    for jid, name, phrase in JOBS:
        sendable = oq.screen([phrase], goal=phrase, source_hint=jid).sendable[0]
        job = {"id": jid, "name": name, "parts": [{"kind": "code-app", "search": [phrase]}]}
        t0 = time.monotonic()
        found = w.find_online(job, [sendable], kind="code-app", budget=budget,
                              progress=lines.append)
        t_search = time.monotonic() - t0
        web = [f for f in found if f["source"] == "codeberg"
               or (f.get("repo") or {}).get("language") in WEB]
        top = sorted(web, key=lambda f: -((f.get("repo") or {}).get("stars") or 0))
        top = top[:DETAILS_PER_JOB]
        t1 = time.monotonic()
        detailed = w.details_many(top, budget=budget, workers=8, deadline=time.monotonic() + 60)
        trusted = [d for d in detailed if (d.get("trust") or {}).get("floor_ok")]
        zipped = sum(1 for d in detailed if (d.get("repo") or {}).get("pinned_commit"))
        report[phrase] = {
            "found": len(found), "by_source": {s: sum(1 for f in found if f["source"] == s)
                                               for s in sorted({f["source"] for f in found})},
            "looked_at": len(top), "read_at_a_pin": zipped, "search_s": round(t_search, 1),
            "details_s": round(time.monotonic() - t1, 1),
            "trusted": [(d["id"], d["trust"]["path"], d["mode_guess"], d["integrable"])
                        for d in trusted],
            "integrable_trusted": sum(1 for d in trusted if d["integrable"]),
        }
        assert len(trusted) >= 2, (phrase, report[phrase], lines)
    spent = budget.spent()
    print(json.dumps({"spent": spent, "caps": search_budget.CAPS_WIZARD, "jobs": report,
                      "total_s": round(time.monotonic() - t_all, 1), "lines": lines}, indent=1))
    for pool, n in spent.items():
        assert n <= search_budget.CAPS_WIZARD.get(pool, n), pool
    assert spent.get("github_core", 0) <= 8 and spent.get("github_search", 0) <= 10
