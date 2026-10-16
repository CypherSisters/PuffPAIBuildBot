"""A stand-in for the runner in unit tests (no browser, no network).

``FakeApp`` models the synthetic list apps in this folder the way the real
pages behave: an add form (maybe behind an opener button or a tab), a list of
records kept in localStorage under one key, and optional row controls (a
delete button with an are-you-sure box, a status choice), a "Clear all"
button, an import form that replaces the whole list, or IndexedDB-only
storage. ``FakeDriver`` offers the driver methods ``compile_form`` and
``recipe_check`` use, per address label, with empty storage for a new label.

The live tests run the same compiler on the real pages in Edge.
"""
from __future__ import annotations

import copy
import itertools
import json
import re

FIELDS = [
    {"sel": "#f_name", "role": "text", "tag": "input", "type": "", "name": "Item name",
     "maxLength": 80, "store": "name"},
    {"sel": "#f_kind", "role": "choose", "tag": "select", "type": "", "name": "Kind",
     "options": [{"v": "", "t": "Choose one"}, {"v": "Food", "t": "Food"},
                 {"v": "Drink", "t": "Drink"}, {"v": "Decor", "t": "Decor"}], "store": "kind"},
    {"sel": "#f_qty", "role": "number", "tag": "input", "type": "number", "name": "Quantity",
     "min": "0", "max": "500", "store": "qty"},
    {"sel": "#f_when", "role": "date", "tag": "input", "type": "date", "name": "Needed by",
     "store": "when"},
]


class FakeApp:
    def __init__(self, *, key="items.v1", hidden_form=False, tabs=False, idb=False,
                 row_delete=False, row_select=False, clear_all=False, clear_confirm=True,
                 import_form=False, counter=False, heading="New item",
                 save_text="Save item", opener_text="+ Add item", fields=None,
                 row_edit=False, row_check=False, row_wipe=False, row_toggle=False,
                 row_choices=("To get", "Got it", "Skip")):
        self.key = key
        self.hidden_form = hidden_form
        self.tabs = tabs
        self.idb = idb
        self.row_delete = row_delete
        self.row_select = row_select
        self.clear_all = clear_all
        self.clear_confirm = clear_confirm
        self.import_form = import_form
        self.counter = counter
        self.heading = heading
        self.save_text = save_text
        self.opener_text = opener_text
        self.fields = copy.deepcopy(fields or FIELDS)
        self.row_edit = row_edit          # an Edit button that fills the form with the record
        self.row_check = row_check        # a "done" checkbox on each row
        self.row_wipe = row_wipe          # a row button that clears EVERY record (must be skipped)
        self.row_toggle = row_toggle      # a row button that flips one field ("Mark done")
        self.row_choices = list(row_choices)
        self._ids = itertools.count(1)

    # -- stored data -------------------------------------------------------
    def load(self, ls):
        try:
            return json.loads(ls.get(self.key) or "null") or {"items": []}
        except ValueError:
            return {"items": []}

    def save(self, ls, doc):
        ls[self.key] = json.dumps(doc)

    def update(self, ls, rid, values):
        doc = self.load(ls)
        for rec in doc["items"]:
            if rec["id"] == rid:
                for f in self.fields:
                    if f["sel"] in values:
                        v = values[f["sel"]]
                        if f["role"] == "number":
                            v = None if v in (None, "") else float(v) if "." in str(v) else int(v)
                        rec[f["store"]] = v
        self.save(ls, doc)

    def add(self, ls, values):
        if self.idb:
            return
        doc = self.load(ls)
        rec = {"id": f"r{next(self._ids)}"}
        for f in self.fields:
            v = values.get(f["sel"])
            if f["role"] == "number":
                v = None if v in (None, "") else float(v) if "." in str(v) else int(v)
            rec[f["store"]] = "" if v is None and f["role"] != "number" else v
        if self.row_select:
            rec["status"] = self.row_choices[0]
        if self.row_check or self.row_toggle:
            rec["done"] = False
        if self.import_form:
            doc["items"] = [rec]
        else:
            doc["items"].append(rec)
        if self.counter:
            doc["next"] = doc.get("next", 0) + 1
        self.save(ls, doc)


class _Page:
    def __init__(self, app, ls):
        self.app = app
        self.ls = ls
        self.form_open = not app.hidden_form and not app.tabs
        self.editing = None
        self.tab = None
        self.values = {}
        self.refs = {}

    def visible_controls(self):
        app = self.app
        out = []
        if app.tabs:
            out.append({"role": "tab", "tag": "button", "text": "Overview", "sel": None, "act": ("tab", "p1")})
            out.append({"role": "tab", "tag": "button", "text": "Shopping list", "sel": None, "act": ("tab", "p2")})
        in_panel = (not app.tabs) or self.tab == "p2"
        if in_panel and app.hidden_form and not self.form_open:
            out.append({"role": "button", "tag": "button", "text": app.opener_text, "sel": "#open",
                        "act": ("open",)})
        if in_panel and self.form_open:
            for f in app.fields:
                c = {k: v for k, v in f.items() if k != "store"}
                c["value"] = str(self.values.get(f["sel"], ""))
                out.append(c)
            out.append({"role": "button", "tag": "button", "text": app.save_text, "sel": "#save",
                        "act": ("save",)})
        if in_panel:
            for rec in app.load(self.ls)["items"]:
                if app.row_select:
                    out.append({"role": "choose", "tag": "select", "text": "", "sel": None,
                                "row_sel": "select.status", "value": rec.get("status", ""),
                                "options": [{"v": o, "t": o} for o in app.row_choices],
                                "act": ("row_select", rec["id"])})
                if app.row_check:
                    out.append({"role": "checkbox", "tag": "input", "type": "checkbox", "text": "",
                                "sel": None, "row_sel": "input.done", "checked": bool(rec.get("done")),
                                "act": ("row_check", rec["id"])})
                if app.row_toggle:
                    out.append({"role": "button", "tag": "button", "text": "Mark done", "sel": None,
                                "row_sel": "button.mark", "act": ("row_toggle", rec["id"])})
                if app.row_edit:
                    out.append({"role": "button", "tag": "button", "text": "Edit", "sel": None,
                                "row_sel": "button.edit", "act": ("row_edit", rec["id"])})
                if app.row_wipe:
                    out.append({"role": "button", "tag": "button", "text": "Tidy", "sel": None,
                                "row_sel": "button.tidy", "act": ("row_wipe", rec["id"])})
                if app.row_delete:
                    out.append({"role": "button", "tag": "button", "text": "Delete", "sel": None,
                                "row_sel": "button.del", "act": ("row_delete", rec["id"])})
            if app.clear_all:
                out.append({"role": "button", "tag": "button", "text": "Clear all", "sel": "#clear",
                            "act": ("clear",)})
        return out


class FakeDriver:
    """Driver methods over FakeApp pages, one page and one storage per label."""

    def __init__(self, app):
        self.app = app
        self.storage_by_label = {}
        self.pages = {}
        self.calls = []

    # -- helpers -----------------------------------------------------------
    def _ls(self, label):
        return self.storage_by_label.setdefault(label, {})

    def _page(self, label):
        if label not in self.pages:
            self.pages[label] = _Page(self.app, self._ls(label))
        return self.pages[label]

    def _do(self, page, act, accept):
        dialogs = []
        kind = act[0]
        if kind == "tab":
            page.tab = act[1]
        elif kind == "open":
            page.form_open = True
        elif kind == "save":
            if not page.values.get("#f_name"):
                dialogs.append({"type": "alert", "message": "Type a name first", "answered": "no"})
            elif page.editing:
                self.app.update(page.ls, page.editing, page.values)
                page.values, page.editing = {}, None
            else:
                self.app.add(page.ls, page.values)
                page.values = {}
                if self.app.hidden_form:
                    page.form_open = False
        elif kind == "row_delete":
            dialogs.append({"type": "confirm", "message": "Delete this item?",
                            "answered": "yes" if accept else "no"})
            if accept:
                doc = self.app.load(page.ls)
                doc["items"] = [r for r in doc["items"] if r["id"] != act[1]]
                self.app.save(page.ls, doc)
        elif kind == "row_edit":
            rec = next(r for r in self.app.load(page.ls)["items"] if r["id"] == act[1])
            page.form_open, page.editing = True, act[1]
            page.values = {f["sel"]: ("" if rec.get(f["store"]) is None else str(rec.get(f["store"])))
                           for f in self.app.fields}
        elif kind in ("row_check", "row_toggle"):
            doc = self.app.load(page.ls)
            for r in doc["items"]:
                if r["id"] == act[1]:
                    r["done"] = not r.get("done")
            self.app.save(page.ls, doc)
        elif kind == "row_wipe":
            doc = self.app.load(page.ls)
            for r in doc["items"]:
                r["name"] = str(r["name"]).upper()
            self.app.save(page.ls, doc)
        elif kind == "clear":
            if self.app.clear_confirm:
                dialogs.append({"type": "confirm", "message": "Clear the whole list?",
                                "answered": "yes" if accept else "no"})
            if accept or not self.app.clear_confirm:
                self.app.save(page.ls, {"items": []})
        return dialogs

    # -- the driver interface ---------------------------------------------
    def open(self, label, fresh=False):
        self.calls.append(("open", label))
        if fresh or label not in self.pages:
            self.pages[label] = _Page(self.app, self._ls(label))

    def reload(self, label):
        self.pages[label] = _Page(self.app, self._ls(label))

    def snapshot(self, label):
        page = self._page(label)
        controls, page.refs = [], {}
        for i, c in enumerate(page.visible_controls(), start=1):
            ref = f"e{i}"
            page.refs[ref] = c
            d = {k: v for k, v in c.items() if k != "act"}
            d.update(ref=ref, idx=10 + i, name=c.get("name") or c.get("text") or "",
                     text=c.get("text", ""), disabled=False)
            controls.append(d)
        return {"title": "Fake", "controls": controls,
                "headings": [{"idx": 1, "text": "Fake list"}, {"idx": 5, "text": self.app.heading}]}

    def click(self, label, ref, *, accept_dialog=False):
        page = self._page(label)
        c = page.refs.get(ref)
        if c is None:
            raise RuntimeError("that control is not on screen any more")
        return {"clicked": c.get("text"), "dialogs": self._do(page, c.get("act", ("none",)), accept_dialog)
                if c.get("act") else []}

    def storage(self, label):
        return {"localStorage": dict(self._ls(label)),
                "indexedDB": ["shelf"] if self.app.idb and self._ls(label).get("__idb") else []}

    def count(self, label, sel):
        if not isinstance(sel, str) or ":contains(" in sel or re.search(r"[\\`<]", sel) \
                or sel.count("[") != sel.count("]"):
            return None
        page = self._page(label)
        return sum(1 for c in page.visible_controls() if c.get("sel") == sel) or (5 if sel == "*" else 0)

    def _find(self, page, step, match):
        text = step.get("text")
        sel = step.get("sel")
        controls = page.visible_controls()
        if step.get("row"):
            recs = [r for r in self.app.load(page.ls)["items"]
                    if str(r.get("name", "")).strip().lower() == str(match).strip().lower()]
            if not recs:
                return None
            rid = recs[0]["id"]
            for c in controls:
                act = c.get("act") or ()
                if len(act) > 1 and act[1] == rid and c.get("row_sel") == sel:
                    return c
            return None
        for c in controls:
            if text is not None and c.get("text") == text and (sel in (None, "*", c.get("tag"))
                                                              or sel == "[role=tab]" and c.get("role") == "tab"):
                return c
            if text is None and sel is not None and c.get("sel") == sel:
                return c
        return None

    def run(self, label, recipe, args):
        self.calls.append(("run", label, recipe.get("name")))
        page = self._page(label)
        if recipe.get("start") == "reload":
            self.reload(label)
            page = self._page(label)
        accept = False
        results, dialogs, clicked = {}, [], []
        try:
            for spec_name, spec in (recipe.get("args") or {}).items():
                v = args.get(spec_name)
                if v in (None, ""):
                    if spec.get("required"):
                        raise RuntimeError(f"{spec_name} is needed")
                    continue
                if spec.get("enum") and v not in spec["enum"]:
                    raise RuntimeError("that is not one of the choices")
            for step in recipe.get("steps") or []:
                do = step.get("do")
                if step.get("if_arg") and args.get(step["if_arg"]) in (None, ""):
                    continue
                val = args.get(step["arg"]) if step.get("arg") else step.get("value")
                match = args.get(step.get("match_arg")) if step.get("match_arg") else ""
                if do in ("click", "click_text", "click_in_row"):
                    c = self._find(page, step, match)
                    if c is None:
                        raise RuntimeError("could not find it")
                    clicked.append(c.get("text") or "")
                    dialogs += self._do(page, c.get("act", ("none",)), accept)
                elif do in ("fill", "fill_in_row"):
                    c = self._find(page, step, match)
                    if c is None:
                        raise RuntimeError("could not find it")
                    page.values[c["sel"]] = val
                elif do in ("choose", "choose_in_row"):
                    c = self._find(page, step, match)
                    if c is None:
                        raise RuntimeError("could not find it")
                    opts = [o["t"] for o in c.get("options") or []] + [o["v"] for o in c.get("options") or []]
                    if val not in opts:
                        raise RuntimeError("that is not one of the choices")
                    if c.get("act") and c["act"][0] == "row_select":
                        doc = self.app.load(page.ls)
                        for r in doc["items"]:
                            if r["id"] == c["act"][1]:
                                r["status"] = val
                        self.app.save(page.ls, doc)
                    else:
                        page.values[c["sel"]] = val
                elif do == "check_in_row":
                    c = self._find(page, step, match)
                    if c is None:
                        raise RuntimeError("could not find it")
                    want = str(val).lower() in ("true", "yes", "on", "1") if step.get("arg") else True
                    if bool(c.get("checked")) != want:
                        clicked.append(c.get("text") or "")
                        dialogs += self._do(page, c["act"], accept)
                elif do == "accept_next_dialog":
                    accept = True
                elif do in ("wait", "wait_for", "key"):
                    continue
                elif do == "read_storage":
                    raw = page.ls.get(step.get("key"))
                    v = json.loads(raw) if raw else None
                    for part in (step.get("path") or "").split("."):
                        if part and isinstance(v, dict):
                            v = v.get(part)
                    if isinstance(v, list) and step.get("fields"):
                        v = [{f: x.get(f) for f in step["fields"]} for x in v]
                    results[step.get("as") or step.get("key")] = v
                else:
                    raise RuntimeError("step not allowed")
            if self.app.idb and any(s.get("do") in ("click", "click_text") for s in recipe.get("steps") or []):
                page.ls["__idb"] = "1"
                page.ls.pop("__idb")
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc), "results": {}, "dialogs": dialogs, "clicked": clicked}
        return {"ok": True, "error": None, "results": results, "dialogs": dialogs, "clicked": clicked}

    def close(self, label):
        self.calls.append(("close", label))
        self.pages.pop(label, None)

    def row_controls(self, label, text):
        """The repeated row holding ``text`` in its key cell, and its controls."""
        page = self._page(label)
        recs = [r for r in self.app.load(page.ls)["items"]
                if str(r.get("name", "")).strip().lower() == str(text).strip().lower()]
        if not recs:
            return None
        rid = recs[0]["id"]
        out = []
        for c in page.visible_controls():
            act = c.get("act") or ()
            if len(act) > 1 and act[1] == rid:
                d = {k: v for k, v in c.items() if k not in ("act", "row_sel")}
                d["sel"] = c["row_sel"]
                d.setdefault("name", c.get("text") or "")
                out.append(d)
        return {"row": "tr.item-row", "key": "td.name", "controls": out}


def labels():
    """A copy factory: a new label every call (empty storage in the driver)."""
    n = itertools.count(1)
    return lambda: f"chk-{next(n):016d}".replace("0", "a")[:20]
