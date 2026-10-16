"""Data and template projects become lists the bot can read.

The plant CSV fixture (a synthetic plant-variety list shaped like the real
bripatch/plant-variety-database: CC BY licence file, data under ``data/``)
goes through the whole Toolsmith path with fakes for the other parts: fetched
at the pinned commit, Drawbridge's code check and data check, a list app the
lists fastpath can read, a ``data_query`` read the chat step answers from.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import app_fastpath, drawbridge  # noqa: E402
from puffpai.integrate import actions, apply, chat, data_import, records, wording  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "fixtures", "integrate", "plant_varieties.csv")
CC_BY = os.path.join(HERE, "fixtures", "recorded_web", "license_CC-BY-4.0.txt")
DEV = "device:deviceaaaa000001"


def _read(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
        return fh.read()


def plant_tree(root, *, csv_text=None, extra=None):
    sup.write(root, "LICENSE", _read(CC_BY))
    sup.write(root, "README.md", "# Plant variety database\n\nVarieties people grow.\n")
    sup.write(root, "data/varieties.csv", csv_text if csv_text is not None else _read(CSV))
    sup.write(root, "data/growing_notes.md", "Sow after the last frost.\n\nKeep soil moist.\n")
    for rel, text in (extra or {}).items():
        sup.write(root, rel, text)
    return root


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    sup.fresh_drawbridge(monkeypatch)
    drawbridge.register_checker(data_import.KIND, data_import.data_rows,
                                block_codes=data_import.BLOCK_CODES,
                                hold_codes=data_import.HOLD_CODES,
                                version=data_import.CHECKER_VERSION)
    from puffpai.security import consent_tickets
    consent_tickets.reset()
    monkeypatch.setattr(actions, "_SOURCES", {})
    actions.register_source("data_query", data_import.list_actions, data_import.run_action)
    monkeypatch.setattr(chat, "_backend", lambda: None)
    uploads = []
    from puffpai.knowledge import upload_search
    monkeypatch.setattr(upload_search, "add",
                        lambda fid, name, chunks, **k: uploads.append((fid, name, chunks))
                        or len(chunks))
    monkeypatch.setattr(upload_search, "remove", lambda fid, **k: 0)

    def make(**tree_kw):
        found = sup.found(name="plant-variety-database", owner="someone",
                          friendly="Plant varieties", license_reported="NOASSERTION",
                          license_read="CC-BY-4.0", mode="data_template", size_kb=4,
                          what="A list of plant varieties people grow.")
        tree = plant_tree(str(tmp_path / "tree"), **tree_kw)
        archive = sup.tar_gz_of(tree)
        found["repo"]["size_kb"] = max(1, len(archive) // 1024 + 1)
        h = sup.install_fakes(monkeypatch, str(tmp_path / "apps"),
                              founds={"apps_code:github:someone/plant-variety-database": found},
                              archives={sup.archive_url(found): archive})
        h["uploads"] = uploads
        return h
    return make


ITEM = {"id": "apps_code:github:someone/plant-variety-database", "label": "Plant varieties"}


def test_the_plant_csv_becomes_a_list_the_bot_can_read(world):
    h = world()
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    assert out["status"] == "added", ctx.lines
    rec = records.get(out["integration_id"])
    assert rec["status"] == "ready" and rec["mode"] == "data_template"
    assert rec["list_app_id"] and records.valid_id(rec["id"])
    assert any("10 rows your bot can read" in line for line in ctx.lines), ctx.lines
    # the lists fastpath reads it
    from puffpai.app_data import AppData
    answer = app_fastpath.handle("what is on the varieties list", AppData())
    assert "Brandywine" in answer and "Sungold" in answer
    # the order-shaped cell was neutralised, never stored as orders
    doc = AppData().get_data(rec["list_app_id"])
    text = json.dumps(doc)
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" not in text
    assert doc["credit"] and "CC" in doc["credit"][0]
    # the text file became passages, neutralised
    assert h["uploads"] and h["uploads"][0][0] == f"integration-{rec['id']}-0"
    # the chat step answers from the data_query read (rule path, no model)
    got = chat.try_answer("what is on the varieties list?", surface="chat", device_id=DEV)
    assert got and "Brandywine" in got["reply"] and "(read from Plant varieties)" in got["reply"]


def test_every_row_says_what_it_is(world):
    world()
    out = apply.apply_code_project(ITEM, sup.Ctx())
    from puffpai.app_data import AppData
    doc = AppData().get_data(records.get(out["integration_id"])["list_app_id"])
    rows = doc["varieties"]
    assert all(r.get("item") for r in rows)
    assert rows[0]["item"] == "Brandywine" and rows[0]["Crop"] == "Tomato"


def test_too_many_rows_waits_for_a_yes(world):
    body = "Variety,Crop\n" + "".join(f"V{i},Tomato\n" for i in range(5200))
    world(csv_text=body)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "held"
    rec = records.get(out["integration_id"])
    assert "too_many_rows" in rec["held"]["reasons"]
    assert rec["list_app_id"] is None


def test_personal_details_wait_for_a_yes(world):
    body = "Name,Phone,Email\nPat,555-867-5309,pat@example.com\nLee,555-123-4567,lee@example.com\n"
    world(csv_text=body)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "held"
    assert "pii_rows" in records.get(out["integration_id"])["held"]["reasons"]
    assert wording.reason_words("pii_rows") in out["line"]


@pytest.mark.parametrize("field", ["__proto__", "constructor", "a" * 41, "bad<field>"])
def test_odd_column_names_are_refused(world, field):
    world(csv_text=f"Variety,{field}\nBrandywine,x\n")
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "refused"
    assert records.get(out["integration_id"])["list_app_id"] is None


def test_other_data_kinds_are_refused(world):
    world(extra={"data/more.yaml": "a: 1\n"})
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "refused"


def test_reading_uses_no_code():
    """Only the standard library reads data: json and csv, never eval or pickle."""
    import ast
    tree = ast.parse(open(data_import.__file__, encoding="utf-8").read())
    imported = {a.name.split(".")[0] for n in ast.walk(tree)
                if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in (n.names if isinstance(n, ast.Import) else [ast.alias(n.module or "")])}
    assert not imported & {"pickle", "yaml", "marshal", "shelve", "dill"}
    calls = {n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & {"eval", "exec", "compile", "__import__"}


def test_json_lists_are_read(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"items": [{"name": "Kale", "days": 60}, {"name": "Leek"}]}),
                    encoding="utf-8")
    got = data_import.read_file(str(path), "x.json")
    assert got["kind"] == "list" and [r["name"] for r in got["rows"]] == ["Kale", "Leek"]


def test_a_list_app_id_is_an_owner_app_id():
    from puffpai import app_registry
    for iid in ("plant-variety-database-abcdefgh", "9lives-bcdefghi", "a" * 30 + "-cdefghij"):
        assert app_registry.valid_id(data_import.list_app_id(iid)), iid


# -- files that cannot be read (validator F, fix 1) ----------------------------------

DEEP_JSON = "[" * 100_000 + "]" * 100_000        # 200 KB, deeper than any parser allows


def test_json_nested_too_deep_is_unreadable_not_a_crash(tmp_path):
    path = tmp_path / "deep.json"
    path.write_text(DEEP_JSON, encoding="utf-8")
    with pytest.raises(data_import.DataError) as err:
        data_import.read_file(str(path), "deep.json")
    assert err.value.code == "unreadable"


def test_a_project_with_a_too_deep_file_is_blocked_and_left_tidy(world):
    world(extra={"data/deep.json": DEEP_JSON})
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rec = records.get(out["integration_id"])
    assert out["status"] == "refused", out
    assert rec["status"] == "refused" and rec["list_app_id"] is None
    assert rec["drawbridge"]["verdict"] == "block"
    assert any("a file in it could not be read" in line for line in out["lines"]), out["lines"]
    assert not os.path.isdir(apply._src_root(rec))     # nothing of it is kept


def test_anything_unexpected_while_adding_ends_failed_never_stuck(world, monkeypatch):
    world()

    def broken(_src):
        raise RuntimeError("boom")
    monkeypatch.setattr(data_import, "collect", broken)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rec = records.get(out["integration_id"])
    assert out["status"] == "failed" and rec["status"] == "failed", out
    assert wording.SET_UP_FAILED in out["line"]
    assert [r["status"] for r in records.all()] == ["failed"]


def test_a_data_file_named_credit_keeps_its_rows_and_its_read(world):
    """``doc["credit"]`` holds the
    licence credit line, so a project list called "credit" was overwritten and
    got no read action. It is now the list "credit_2"."""
    world(extra={"data/credit.csv": "name,amount\nFlorist,40\nBaker,55\n"})
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "added", out
    rec = records.get(out["integration_id"])
    from puffpai.app_data import AppData
    doc = AppData().get_data(rec["list_app_id"])
    assert doc["credit"] and "CC" in doc["credit"][0]            # the licence credit
    assert [r.get("name") for r in doc["credit_2"]] == ["Florist", "Baker"]
    says = {a["says"] for a in data_import.list_actions() if a["integration"] == rec["id"]}
    assert "Read the credit 2 list" in says and not any(s == "Read the credit list"
                                                         for s in says)
