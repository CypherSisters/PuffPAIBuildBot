"""The product agrees on the name of its own security agent.

`README.md:50` names Drawbridge and captions the screenshot "Drawbridge holding
the veto"; the shipped `docs/images/agents.png` directly above that caption
reads "Reviewer Sentinel", a card titled "Sentinel", and a badge `sentinel`.
A stranger catches it without cloning anything.

The product decision: display name **Drawbridge**,
internal id stays **sentinel**. Renaming the id would break every stored
envelope, every route, every saved config and the whole audit trail; the id was
never the problem. Showing the id was.

So this file pins both halves: no user-facing surface says Sentinel, and every
internal id still does.

Run:
    pytest tests/test_reviewer_named_drawbridge.py
"""
from __future__ import annotations

import io
import os
import re

import pytest

from puffpai.agents import (REVIEWER_NAME, ROSTER, display_name,
                            roster_summary)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── the display name ────────────────────────────────────────────────────────

def test_the_reviewer_is_shown_as_drawbridge():
    assert display_name("sentinel") == "Drawbridge"


def test_the_reviewers_internal_id_is_untouched():
    """The rename is cosmetic on purpose. Break this and stored state breaks."""
    assert REVIEWER_NAME == "sentinel"
    assert any(e.name == "sentinel" for e in ROSTER)


def test_every_roster_row_carries_a_display_name():
    rows = roster_summary()
    # Seven since the Toolsmith landed. The
    # summary must cover the whole roster, so this counts ROSTER rather than
    # pinning a literal that has to be edited every time the team changes.
    assert len(rows) == len(ROSTER) == 7
    for row in rows:
        assert row["display_name"], f"{row['name']} has no display name"
        assert row["display_name"][0].isupper()


def test_no_roster_row_shows_the_retired_name():
    for row in roster_summary():
        assert "Sentinel" not in row["display_name"]
        assert "Sentinel" not in row["evolves_from"]


def test_an_unknown_agent_id_still_renders_readably():
    assert display_name("toolsmith") == "Toolsmith"
    assert display_name("") == ""


# ── the API surface the dashboard renders ───────────────────────────────────

def test_the_bus_reports_a_display_name_for_its_reviewer(monkeypatch):
    """The "Reviewer Sentinel" line in the shipped screenshot came from here."""
    from puffpai import api_server

    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    resp = client.get("/agents/status",
                      headers={"Authorization": f"Bearer {api_server._API_KEY}"})
    body = resp.get_json()
    assert resp.status_code == 200

    bus = body.get("bus") or {}
    if bus.get("reviewer"):
        assert bus["reviewer"] == "sentinel"          # id, unchanged
        assert bus["reviewer_display"] == "Drawbridge"  # what a person sees
    for row in body.get("team") or []:
        assert row.get("display_name")
        assert "Sentinel" not in row["display_name"]


# ── no user-facing string anywhere says Sentinel ────────────────────────────

_UI_FILES = [
    os.path.join("dashboard", "app", "dashboard", "agents", "page.tsx"),
    os.path.join("src", "puffpai", "dashboard_widgets.py"),
]


@pytest.mark.parametrize("relpath", _UI_FILES)
def test_no_rendered_string_says_sentinel(relpath):
    """Comments and internal ids may; anything a person reads may not."""
    path = os.path.join(_REPO_ROOT, relpath)
    with io.open(path, encoding="utf-8") as fh:
        lines = fh.readlines()

    offenders = []
    for number, line in enumerate(lines, 1):
        if "Sentinel" not in line:
            continue
        stripped = line.strip()
        # Code comments are not a surface anybody reads in the product.
        if stripped.startswith(("#", "//", "*", "/*")):
            continue
        offenders.append(f"{relpath}:{number}: {stripped}")

    assert not offenders, "user-facing 'Sentinel':\n" + "\n".join(offenders)


def test_the_dashboard_renders_the_display_name_not_the_id():
    """`capitalize(agent.name)` is what put "Sentinel" on the agents card."""
    path = os.path.join(_REPO_ROOT, "dashboard", "app", "dashboard",
                        "agents", "page.tsx")
    with io.open(path, encoding="utf-8") as fh:
        source = fh.read()

    assert "agent.display_name" in source
    assert "bus.reviewer_display" in source
    # The bare-id renderings are gone from the headings they used to title.
    assert not re.search(r"<h3[^>]*>\{capitalize\(agent\.name\)\}", source)
    assert not re.search(r">\{capitalize\(bus\.reviewer\)\}<", source)


def test_the_cli_roster_prints_drawbridge(capsys, monkeypatch):
    from puffpai import cli

    monkeypatch.setattr(cli, "_maybe_autoindex", lambda: None)
    cli._cmd_agents(type("A", (), {"agents_action": "status"})())
    out = capsys.readouterr().out

    assert "Drawbridge" in out
    assert "Sentinel" not in out
    assert "reviewer=Drawbridge" in out
