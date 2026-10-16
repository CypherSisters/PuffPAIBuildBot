"""make_document / edit_document: Word, Excel and PowerPoint files in her
own files, changed only inside her data folder or a folder she linked.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os

import pytest

from puffpai import brain_tools, brain_tools_builtin, office_docs
from puffpai.knowledge import my_data

docx = pytest.importorskip("docx")
openpyxl = pytest.importorskip("openpyxl")
pptx = pytest.importorskip("pptx")


POKES: list = []


@pytest.fixture
def data(tmp_path, monkeypatch):
    d = tmp_path / "Data"
    linked = tmp_path / "Linked"
    linked.mkdir()
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(my_data, "data_dir", lambda: str(d))
    monkeypatch.setattr(my_data, "linked_folders",
                        lambda: [{"id": "lf1", "path": str(linked), "name": "Linked"}])
    monkeypatch.setattr(office_docs, "_bot_name", lambda: "GardenBot")
    POKES[:] = []
    monkeypatch.setattr(my_data, "poke", lambda: POKES.append(1))
    return d


def test_word_file_in_made_by_folder(data):
    got = office_docs.make_document("word", "Plant plan", "# Spring\n\nWater daily.\n\n- tomatoes\n- basil")
    assert got["ok"] and got["path"].endswith(".docx")
    assert os.path.dirname(got["path"]) == str(data / "My files" / "Made by GardenBot")
    assert office_docs.OPEN_LINE in got["said"]
    assert got["open"] == {"method": "POST", "url": "/files/open", "body": {"path": got["path"]}}
    text = [p.text for p in docx.Document(got["path"]).paragraphs]
    assert text[:3] == ["Plant plan", "Spring", "Water daily."] and "basil" in text
    # A second file with the same title never overwrites the first.
    again = office_docs.make_document("word", "Plant plan", "x")
    assert again["path"] != got["path"] and again["path"].endswith("Plant plan (2).docx")
    assert POKES


def test_excel_and_powerpoint(data):
    x = office_docs.make_document("excel", "Costs", "item,price\nsoil,12\nseeds,3.5")
    ws = openpyxl.load_workbook(x["path"]).active
    assert [[c.value for c in r] for r in ws.iter_rows()] == [
        ["item", "price"], ["soil", 12], ["seeds", 3.5]]
    p = office_docs.make_document("powerpoint", "Talk", "# One\n- a\n- b\n---\nTwo\nc")
    titles = [s.shapes.title.text for s in pptx.Presentation(p["path"]).slides]
    assert titles == ["Talk", "One", "Two"]
    assert office_docs.make_document("pdf", "x", "y")["ok"] is False


def test_edits_in_allowed_folders_only(data, tmp_path):
    w = office_docs.make_document("word", "Notes", "Hello Bob.")["path"]
    assert office_docs.edit_question({"path": w}, {}) is None      # its own file: no yes
    got = office_docs.edit_document(w, [{"replace": "Bob", "with": "Ann"}, {"append": "Bye."}])
    assert got["ok"], got
    text = [p.text for p in docx.Document(w).paragraphs]
    assert "Hello Ann." in text and text[-1] == "Bye."
    x = office_docs.make_document("excel", "Sheet", "a,b")["path"]
    assert office_docs.edit_document(x, [{"rows": [["c", 2]]}])["ok"]
    assert [c.value for c in list(openpyxl.load_workbook(x).active.iter_rows())[-1]] == ["c", 2]
    p = office_docs.make_document("powerpoint", "Deck", "intro")["path"]
    assert office_docs.edit_document(p, [{"append": "# Extra\n- one"}])["ok"]
    assert len(pptx.Presentation(p).slides) == 3
    # A file in a linked folder may change; one anywhere else may not.
    doc = docx.Document()
    doc.add_paragraph("old")
    linked = tmp_path / "Linked" / "theirs.docx"
    doc.save(str(linked))
    assert office_docs.edit_document(str(linked), [{"replace": "old", "with": "new"}],
                                     said_yes=True)["ok"]
    outside = tmp_path / "elsewhere.docx"
    doc.save(str(outside))
    got = office_docs.edit_document(str(outside), [{"append": "x"}])
    assert got["ok"] is False and "data folder" in got["said"]
    assert office_docs.edit_document(str(tmp_path / "nope.docx"), [{"append": "x"}])["ok"] is False


def test_registered_as_builtin_tools_for_the_owner(data):
    brain_tools_builtin.reset_for_tests()
    brain_tools_builtin.register_all()
    names = {t.name for t in brain_tools.available({"caller": "owner"})}
    assert {"make_document", "edit_document"} <= names


def test_new_files_show_in_my_files_newest_first(data):
    a = office_docs.make_document("word", "First", "x")["path"]
    b = office_docs.make_document("excel", "Second", "1,2")["path"]
    os.utime(a, (1, 1))
    rows = my_data.recent_my_files()
    assert [r["path"] for r in rows] == [b, a]
    assert rows[0]["folder"] == "Made by GardenBot" and rows[0]["name"] == "Second.xlsx"


def test_her_own_file_in_my_files_needs_her_yes(data):
    mine = data / "My files" / "letter.docx"
    mine.parent.mkdir(parents=True, exist_ok=True)
    doc = docx.Document()
    doc.add_paragraph("Dear Bob")
    doc.save(str(mine))
    q = office_docs.edit_question({"path": str(mine)}, {})
    assert q and q["title"] == "May I change letter.docx?"
    assert office_docs.edit_document(str(mine), [{"append": "x"}])["ok"] is False
    assert [p.text for p in docx.Document(str(mine)).paragraphs] == ["Dear Bob"]
    assert office_docs.edit_document(str(mine), [{"append": "Hi"}], said_yes=True)["ok"]
    assert not list((data / "My files").glob("*before BuildBot*"))
    # Through the tool loop: asked once, runs only on yes.
    brain_tools_builtin.reset_for_tests()
    brain_tools_builtin.register_all()
    asked = []
    got = brain_tools.call("edit_document", {"path": str(mine), "changes": [{"append": "No"}]},
                           {"caller": "owner", "ask": lambda q: asked.append(q) or False})
    assert got["ok"] is False and len(asked) == 1
    got = brain_tools.call("edit_document", {"path": str(mine), "changes": [{"append": "Yes"}]},
                           {"caller": "owner", "ask": lambda q: True})
    assert got["ok"] and got["result"]["ok"]
    assert [p.text for p in docx.Document(str(mine)).paragraphs][-1] == "Yes"


def test_a_linked_file_asks_the_plain_line_and_keeps_the_original(data, tmp_path):
    linked = tmp_path / "Linked" / "budget.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["rent", 900])
    wb.save(str(linked))
    q = office_docs.edit_question({"path": str(linked)}, {})
    assert q["title"] == ("This file is in a folder you linked. BuildBot normally never "
                          "changes those. Change it anyway?")
    assert office_docs.edit_document(str(linked), [{"rows": [["food", 300]]}])["ok"] is False
    assert not (tmp_path / "Linked" / "budget (before BuildBot).xlsx").exists()
    got = office_docs.edit_document(str(linked), [{"rows": [["food", 300]]}], said_yes=True)
    assert got["ok"] and "budget (before BuildBot).xlsx" in got["said"]
    before = tmp_path / "Linked" / "budget (before BuildBot).xlsx"
    assert [[c.value for c in r] for r in openpyxl.load_workbook(str(before)).active.iter_rows()]         == [["rent", 900]]
    assert len(list(openpyxl.load_workbook(str(linked)).active.iter_rows())) == 2
    # A second change keeps a second copy, never over the first.
    office_docs.edit_document(str(linked), [{"rows": [["x", 1]]}], said_yes=True)
    assert (tmp_path / "Linked" / "budget (before BuildBot) (2).xlsx").exists()
