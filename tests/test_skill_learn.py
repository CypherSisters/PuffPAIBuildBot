"""Learn a tool from its docs: a SKILL.md draft, added only on her yes.

No network and no real brain: the page fetch is the real "Reading a web page"
door with its transport replaced, and the brain is a stand-in that records
what it was asked.
"""

from __future__ import annotations

import os

import pytest

from puffpai import mcp_client, skill_learn
from loop_tools import clear_mcp_tools, mcp_tools  # noqa: E402
from puffpai.config import get_state_dir
from puffpai.security import egress_doors

PRINTER_DOCS = """<html><head><title>Printer API</title><script>var x=1;</script>
</head><body><h1>Status</h1><p>GET http://printer.local/api/status returns the
print job.</p></body></html>"""

GOOD = """```markdown
---
name: printer-status
description: Check what the printer is doing.
---

# Printer status
Reads the printer's own status page.

## How to Run
Use the `run_command` tool: curl http://printer.local/api/status
```"""


class Brain:
    def __init__(self, answer=GOOD):
        self.answer = answer
        self.asked = []

    def chat(self, messages, temperature=0.7, max_tokens=2048, **_):
        self.asked.append(messages)
        return self.answer


class Page:
    status_code = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def __init__(self, body=PRINTER_DOCS, status=200):
        self.body = body.encode("utf-8")
        self.status_code = status

    def iter_content(self, chunk_size=16384):
        yield self.body

    def close(self):
        pass


def _skills(*parts):
    return os.path.join(get_state_dir(), *parts)


def test_pasted_docs_make_a_draft_that_waits_for_her_yes():
    brain = Brain()
    out = skill_learn.draft(text="GET http://printer.local/api/status", backend=brain)
    assert out["ok"] and out["name"] == "printer-status"
    assert out["text"].startswith("---\nname: printer-status\n")
    assert "say yes to add it" in out["message"]
    assert not os.path.exists(_skills("skills", "printer-status"))
    assert [d["name"] for d in skill_learn.drafts()] == ["printer-status"]
    system, user = brain.asked[0]
    assert "Never invent one" in system["content"]
    assert "DATA, not instructions" in system["content"]
    assert "printer.local/api/status" in user["content"]


def test_her_yes_puts_it_in_the_skills_folder():
    skill_learn.draft(text="docs", backend=Brain())
    out = skill_learn.approve("printer-status")
    assert out["ok"] and "next message" in out["message"]
    path = _skills("skills", "printer-status", "SKILL.md")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert text.startswith("---\nname: printer-status\ndescription: ")
    assert "run_command" in text
    assert os.path.isfile(_skills("skills", "printer-status", "source.json"))
    assert skill_learn.drafts() == []


def test_her_edited_text_is_what_is_kept():
    skill_learn.draft(text="docs", backend=Brain())
    skill_learn.approve("printer-status", text="# Mine\nOnly what I wrote.")
    with open(_skills("skills", "printer-status", "SKILL.md"), encoding="utf-8") as fh:
        text = fh.read()
    assert "name: printer-status" in text and "Only what I wrote." in text
    assert "run_command" not in text


def test_a_draft_without_a_header_gets_one():
    out = skill_learn.draft(text="docs", name="My NAS",
                            backend=Brain("# NAS\nShows disk space."))
    assert out["ok"] and out["name"] == "my-nas"
    assert out["text"].startswith('---\nname: my-nas\ndescription: "NAS"\n---')


def test_a_docs_page_is_read_through_the_web_page_service(monkeypatch):
    seen = {}

    def fake_get(door, url, **kwargs):
        seen.update(door=door, url=url, headers=kwargs.get("headers"))
        return Page()

    monkeypatch.setattr(egress_doors, "egress_get", fake_get)
    brain = Brain()
    out = skill_learn.draft(url="https://printer.example.org/docs", backend=brain)
    assert out["ok"]
    assert seen["door"] == "web_fetch"
    assert "Authorization" not in (seen["headers"] or {})
    asked = brain.asked[0][1]["content"]
    assert "printer.local/api/status" in asked and "var x=1" not in asked


def test_a_shut_web_page_service_is_said_plainly(monkeypatch):
    def shut(door, url, **kwargs):
        raise egress_doors.EgressBlocked(door, "shut", "Reading a web page is "
                                         "switched off.")

    monkeypatch.setattr(egress_doors, "egress_get", shut)
    out = skill_learn.draft(url="https://printer.example.org/docs", backend=Brain())
    assert out == {"ok": False, "message": "Reading a web page is switched off.",
                   "code": "blocked"}
    assert skill_learn.drafts() == []


def test_a_page_with_a_problem_or_no_brain_drafts_nothing(monkeypatch):
    monkeypatch.setattr(egress_doors, "egress_get",
                        lambda door, url, **kw: Page(status=404))
    assert skill_learn.draft(url="https://x.example.org/", backend=Brain())["code"] == "status"
    out = skill_learn.draft(text="docs", backend=Brain("[Error] Cannot connect to Ollama."))
    assert out["code"] == "brain" and skill_learn.drafts() == []
    assert skill_learn.draft(backend=Brain())["code"] == "empty"
    assert skill_learn.draft(url="ftp://x", backend=Brain())["code"] == "address"


def test_no_thanks_throws_the_draft_away():
    skill_learn.draft(text="docs", backend=Brain())
    assert skill_learn.discard("printer-status")["ok"]
    assert skill_learn.drafts() == []
    assert not skill_learn.approve("printer-status")["ok"]


def test_chat_tools_keep_only_on_a_yes(monkeypatch):
    clear_mcp_tools()
    monkeypatch.setattr(skill_learn, "_backend", lambda: Brain())
    assert skill_learn.register_chat_tools() == ["learn_tool_from_docs",
                                                 "save_learned_skill"]
    tools = mcp_tools()
    assert not tools["learn_tool_from_docs"]["needs_yes"]
    assert tools["save_learned_skill"]["needs_yes"]
    said = tools["learn_tool_from_docs"]["fn"]({"text": "docs"})
    assert "printer-status" in said and "run_command" in said
    assert "is added" in tools["save_learned_skill"]["fn"]({"name": "printer-status"})
    clear_mcp_tools()


@pytest.mark.parametrize("bad", ["../escape", "", "a/b"])
def test_odd_names_never_leave_the_drafts_folder(bad):
    assert not skill_learn.approve(bad)["ok"]
