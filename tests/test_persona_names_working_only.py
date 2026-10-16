"""The wake-up intro names only what WORKS (browser look 2026-10-14: it said
"I just learned ... worldmonitor and superdesk" though superdesk never ran).
A working app job names the app that really runs for it. The wake-up has a
"Not now" button and Escape closes it.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os

from puffpai import persona
from puffpai.build import make, schema as S


def test_only_working_jobs_are_named(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    build = S.empty_build("world news")
    build["parts"] = {"github:k/worldmonitor": {"ref": "github:k/worldmonitor",
                                                "name": "worldmonitor", "class": "APP"},
                      "github:sd/superdesk": {"ref": "github:sd/superdesk",
                                              "name": "superdesk", "class": "APP"},
                      "book:history": {"ref": "book:history", "name": "World History",
                                       "class": "TEXT"}}
    build["jobs"] = [
        {"id": "news", "name": "News", "route": "A", "status": "working",
         "ingredients": ["github:k/worldmonitor"]},
        {"id": "desk", "name": "Desk", "route": "A", "status": "not_yet",
         "ingredients": ["github:sd/superdesk"]},
        {"id": "learn", "name": "Learn", "route": "B", "status": "working",
         "ingredients": ["book:history"]},
        {"id": "reuse", "name": "Briefs", "route": "A", "status": "working",
         "ingredients": ["github:sd/superdesk"]},
    ]
    S.save_build(build)
    make.save_made({"id": "reuse", "route": "A"},
                   {"ok": True, "made": {"app_name": "worldmonitor"}})
    names = persona._build_parts_used()  # noqa: SLF001
    assert names == ["worldmonitor", "World History"]


def test_the_wake_up_can_be_closed_without_the_tour():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "dashboard", "app", "_components", "Persona", "WakeUp.tsx"),
              encoding="utf-8") as fh:
        src = fh.read()
    assert "Not now" in src and '"Escape"' in src
