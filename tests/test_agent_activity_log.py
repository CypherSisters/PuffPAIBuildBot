"""The team page shows the work the agents really did.

Installs and checks (Toolsmith, Drawbridge), the page made after an install,
feed refreshes and routine runs (Majordomo) are written to the activity log
the status route reads, so the team stops reading "idle" while it works.
"""
from __future__ import annotations

import json

from puffpai import agent_activity, api_server


def _status(client):
    return client.get("/agents/status",
                      headers={"Authorization": f"Bearer {api_server._API_KEY}"}).get_json()


def _row(body, name):
    return next(r for r in body["team"] if r["name"] == name)


def test_recorded_work_shows_on_the_owning_agents_card():
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    agent_activity.record("toolsmith", "Installed Weather Lookup")
    agent_activity.record("majordomo", "Refreshed Example Feed", status="error")
    body = _status(client)
    tool = _row(body, "toolsmith")
    assert tool["status"] == "completed" and tool["task"] == "Installed Weather Lookup"
    assert tool["recent"][0]["task"] == "Installed Weather Lookup"
    assert _row(body, "majordomo")["status"] == "error"
    assert _row(body, "steward")["status"] == "idle"


def test_the_log_is_in_the_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    agent_activity.record("chat", "Used search web", tool="search_web")
    rows = [json.loads(x) for x in (tmp_path / "agent-activity.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert rows[-1]["agent"] == "chat" and rows[-1]["tool"] == "search_web"


def test_an_install_is_logged_under_toolsmith_and_drawbridge(monkeypatch):
    from puffpai.finding import install
    monkeypatch.setattr(install.find_mod, "recall", lambda _id: {"name": "Pianist"})
    install._log_install("f1", {"ok": True, "status": "installed"})
    install._log_install("f2", {"ok": False, "status": "refused"})
    rows = [json.loads(x) for x in open(agent_activity.path(), encoding="utf-8")]
    said = [(r["agent"], r["task"]) for r in rows]
    assert ("toolsmith", "Installed Pianist") in said
    assert ("sentinel", "Checked Pianist before it was installed") in said
    assert ("sentinel", "Checked Pianist and did not install it") in said


def test_a_routine_run_is_logged_under_majordomo(monkeypatch):
    from puffpai import announcer, routines
    row = {"id": "r1", "kind": "reminder", "label": "Water the plants",
           "params": {"text": "Water the plants"}, "enabled": True}
    monkeypatch.setattr(routines, "get_routine", lambda _id: row)
    monkeypatch.setattr(routines, "is_runnable", lambda _row: True)
    monkeypatch.setattr(routines, "_update", lambda *a, **k: row)
    monkeypatch.setattr(routines, "compose", lambda r, now=None: ("Hi", {}))
    monkeypatch.setattr(announcer, "announce", lambda *a, **k: {"ok": True})
    routines.execute("r1")
    rows = [json.loads(x) for x in open(agent_activity.path(), encoding="utf-8")]
    assert rows[-1]["agent"] == "majordomo"
    assert "Water the plants" in rows[-1]["task"]
