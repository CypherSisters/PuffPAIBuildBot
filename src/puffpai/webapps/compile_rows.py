"""Toolsmith's row compiler: the controls on an app's list rows become actions.

Goal-agnostic: it knows no app, and nothing here is written by hand for one.
It starts from an add form :mod:`puffpai.webapps.compile_form` already
compiled (and Drawbridge allowed), whose stored-data diff taught which stored
field holds the record's key (the text the owner names a record by).

On throwaway check copies (their own never-reused address, empty storage,
every outside host off):

1. add two marked test records through the compiled add form;
2. find the repeated row that shows the first record's key text (the runner's
   ``row_controls`` verb: the nearest ``tr``, ``li``, ``[role=row]`` or an
   element whose siblings share its tag and class signature) and the controls
   inside that row, each with a selector that works inside any such row;
3. try each control on a FRESH copy with the same two records, and diff the
   stored data before and after:

   * a choice (select) that stores the chosen words in one field of that one
     record -> ``choose_in_row`` (args: the key and the new value, the choices
     from the app, each checked by R8);
   * a checkbox that flips one field of that one record -> ``check_in_row``
     (args: the key and yes or no);
   * a button or link: the record is gone and nothing else changed -> a
     remove action (``click_in_row``, answering the app's are-you-sure box
     only when it asked); one field of that one record changed -> a change
     action; nothing stored changed but a form opened holding the record's
     values -> an edit action, compiled like an add form starting from the
     row's button (the key field is left as it is);
   * anything that touches another record, another stored key, or data
     outside the list is skipped.

4. the words come from templates (``Remove a <noun>``, ``Change the <field>
   of a <noun>``, ``Edit a <noun>``) with the add form's noun and the learned
   field name, cleaned and checked by R8.

Every action made here is then checked by Drawbridge on its own fresh copy
(:func:`puffpai.webapps.recipe_check.check_recipe` with the two records put
there first), and its effect is the stricter of the declared and the proven.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import re
import secrets
from typing import Any, Callable, Optional

from puffpai.webapps import compile_form
from puffpai.webapps import recipe_check as rc
from puffpai.webapps import wording

MAX_ROW_CONTROLS = 12
BUTTON_ROLES = {"button", "link"}


def _sel_ok(sel: Any) -> bool:
    return isinstance(sel, str) and bool(rc.SAFE_SEL.fullmatch(sel)) and not rc.BAD_SEL.search(sel)


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip().lower()


def field_words(field: str) -> str:
    """A stored field name as plain words: ``rsvpStatus`` -> ``rsvp status``."""
    t = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(field or ""))
    t = re.sub(r"[^A-Za-z0-9]+", " ", t).strip().lower()
    words = t.split()[:4]
    text = " ".join(words)
    return text if text and rc.label_ok(text, "words") else "details"


def _records(ls: dict, storage_key: str, path: str) -> Optional[tuple[list, Any]]:
    """(records, the stored document with the list left out), or None."""
    ok, doc = rc._loads(ls.get(storage_key))
    if not ok or doc is None:
        return None
    items = rc.pick(doc, path)
    if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
        return None
    rest = json.loads(json.dumps(doc))
    if path:
        parts = path.split(".")
        cur = rest
        for p in parts[:-1]:
            cur = cur.get(p) if isinstance(cur, dict) else None
        if isinstance(cur, dict):
            cur.pop(parts[-1], None)
    else:
        rest = None
    return items, rest


def row_diff(before: dict, after: dict, *, storage_key: str, path: str,
             key_field: str) -> Optional[dict]:
    """What happened to which record, by the key field. None when unreadable."""
    b = _records(before, storage_key, path)
    a = _records(after, storage_key, path)
    if b is None or a is None:
        return None
    (bl, brest), (al, arest) = b, a
    other = sorted(k for k in set(before) | set(after)
                   if k != storage_key and before.get(k) != after.get(k))
    bm: dict[str, dict] = {}
    am: dict[str, dict] = {}
    for m, lst in ((bm, bl), (am, al)):
        for r in lst:
            k = _norm(r.get(key_field))
            if k in m:                         # two records share a key: cannot tell
                return None
            m[k] = r
    changed = {}
    for k in bm:
        if k in am and bm[k] != am[k]:
            changed[k] = sorted(f for f in set(bm[k]) | set(am[k]) if bm[k].get(f) != am[k].get(f))
    return {"other_keys": other, "outside_list": brest != arest,
            "gone": sorted(k for k in bm if k not in am),
            "new": sorted(k for k in am if k not in bm),
            "changed": changed, "after": am}


def _only_this_record(d: Optional[dict], key: str) -> bool:
    return bool(d) and not d["other_keys"] and not d["outside_list"] and not d["new"] \
        and set(d["gone"]) | set(d["changed"]) <= {key}


class _Ctx:
    """What every row action of one add form shares."""

    def __init__(self, add: dict) -> None:
        self.add = add
        recipe = add["recipe"]
        self.key_arg = next(a for a, s in recipe["args"].items() if s.get("required"))
        self.key_field = add["mapping"][self.key_arg]
        self.key_spec = dict(recipe["args"][self.key_arg])
        self.storage_key = add["storage_key"]
        self.path = add.get("list") or ""
        self.noun = add.get("noun") or "entry"
        marker = "PPTEST-" + secrets.token_hex(4)
        self.k1, self.k2 = f"{marker}-a", f"{marker}-b"
        # the way to the list: the add form's walk without its last click (the
        # "add" opener); a tab the list sits on is clicked again after a reload
        walk = list(add.get("walk") or [])
        self.prefix = walk[:-2] if len(walk) >= 2 else []

    def seed(self, driver: Any, label: str) -> bool:
        for k in (self.k1, self.k2):
            args = dict(self.add["tests"])
            args[self.key_arg] = k
            if not driver.run(label, self.add["recipe"], args).get("ok"):
                return False
        return True

    def check(self, **extra: Any) -> dict:
        c = {"storage_key": self.storage_key, "list": self.path,
             "match": {self.key_field: self.key_arg}}
        c.update(extra)
        return c

    def steps(self, *row_steps: dict) -> list:
        return [dict(s) for s in self.prefix] + list(row_steps)

    def row_step(self, do: str, info: dict, sel: str, **extra: Any) -> dict:
        step = {"do": do, "row": info["row"], "key": info["key"], "match_arg": self.key_arg,
                "sel": sel}
        step.update(extra)
        return step


def _unique(name: str, taken: set) -> str:
    name = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:36] or "row_action"
    if not re.match(r"[a-z]", name):
        name = "row_" + name
    out, n = name, 2
    while out in taken:
        out = f"{name}_{n}"
        n += 1
    taken.add(out)
    return out


def _run_on_fresh(ctx: _Ctx, driver: Any, new_copy: Callable[[], str], recipe: dict,
                  args: dict, *, snapshot_after: bool = False) -> Optional[dict]:
    label = new_copy()
    try:
        driver.open(label, fresh=True)
        if not ctx.seed(driver, label):
            return None
        before = driver.storage(label)["localStorage"]
        res = driver.run(label, recipe, args)
        after = driver.storage(label)["localStorage"]
        snap = driver.snapshot(label) if snapshot_after else None
        return {"res": res, "before": before, "after": after, "snap": snap}
    finally:
        driver.close(label)


def _finish(ctx: _Ctx, recipe: dict, tests: dict, run: dict) -> dict:
    proven, _why = rc.prove_effect(recipe, tests, run["before"], run["after"],
                                   clicked=run["res"].get("clicked") or [],
                                   dialogs=run["res"].get("dialogs") or [])
    recipe["effect"] = rc.stricter(recipe.get("effect") or "changes", proven)
    return {"ok": True, "why": "", "recipe": recipe, "tests": tests}


def _choice(ctx: _Ctx, driver: Any, new_copy: Callable[[], str], info: dict, c: dict,
            taken: set) -> dict:
    opts = [o for o in c.get("options") or [] if not o.get("disabled")]
    texts = [str(o.get("t") or "") for o in opts]
    choices = [t for t in dict.fromkeys(texts) if t and rc.label_ok(t, "choice")][:rc.MAX_CHOICES]
    current = _norm(c.get("value"))
    test = next((t for t in choices if _norm(t) != current), None)
    if len(choices) < 2 or test is None:
        return {"ok": False, "why": "a choice with nothing else to choose"}
    arg = "new_value"
    recipe = {"name": "row_choice", "says": "x", "effect": "changes", "start": "reload",
              "args": {ctx.key_arg: dict(ctx.key_spec, required=True),
                       arg: {"type": "string", "enum": choices, "says": "the new choice"}},
              "steps": ctx.steps(ctx.row_step("choose_in_row", info, c["sel"], arg=arg))}
    tests = {ctx.key_arg: ctx.k1, arg: test}
    run = _run_on_fresh(ctx, driver, new_copy, recipe, tests)
    if run is None or not run["res"].get("ok"):
        return {"ok": False, "why": "the choice did not work on the test copy"}
    d = row_diff(run["before"], run["after"], storage_key=ctx.storage_key, path=ctx.path,
                 key_field=ctx.key_field)
    k1 = _norm(ctx.k1)
    if not _only_this_record(d, k1) or d["gone"] or len(d["changed"].get(k1, [])) != 1:
        return {"ok": False, "why": "the choice changed more than one field of one record"}
    field = d["changed"][k1][0]
    if not rc._same(d["after"][k1].get(field), test):
        # the app keeps a code, not the words: the bot could not show what it chose
        return {"ok": False, "why": "the choice keeps a code, not its words"}
    words = field_words(field)
    recipe["name"] = _unique(f"change_{words.replace(' ', '_')}", taken)
    recipe["says"] = wording.SAYS_CHANGE.format(field=words, a=wording.article(ctx.noun),
                                                noun=ctx.noun)
    recipe["args"][arg]["says"] = f"the new {words}"
    recipe["check"] = ctx.check(expect={field: arg})
    return _finish(ctx, recipe, tests, run)


def _checkbox(ctx: _Ctx, driver: Any, new_copy: Callable[[], str], info: dict, c: dict,
              taken: set) -> dict:
    arg = "ticked"
    want = "no" if c.get("checked") else "yes"
    recipe = {"name": "row_tick", "says": "x", "effect": "changes", "start": "reload",
              "args": {ctx.key_arg: dict(ctx.key_spec, required=True),
                       arg: {"type": "string", "enum": ["yes", "no"],
                             "says": "yes to tick it, no to clear it"}},
              "steps": ctx.steps(ctx.row_step("check_in_row", info, c["sel"], arg=arg))}
    tests = {ctx.key_arg: ctx.k1, arg: want}
    run = _run_on_fresh(ctx, driver, new_copy, recipe, tests)
    if run is None or not run["res"].get("ok"):
        return {"ok": False, "why": "the tick box did not work on the test copy"}
    d = row_diff(run["before"], run["after"], storage_key=ctx.storage_key, path=ctx.path,
                 key_field=ctx.key_field)
    k1 = _norm(ctx.k1)
    if not _only_this_record(d, k1) or d["gone"] or len(d["changed"].get(k1, [])) != 1:
        return {"ok": False, "why": "the tick box changed more than one field of one record"}
    field = d["changed"][k1][0]
    if bool(d["after"][k1].get(field)) != (want == "yes"):
        return {"ok": False, "why": "the tick box does not keep yes or no"}
    words = field_words(field)
    recipe["name"] = _unique(f"tick_{words.replace(' ', '_')}", taken)
    recipe["says"] = wording.SAYS_CHANGE.format(field=words, a=wording.article(ctx.noun),
                                                noun=ctx.noun)
    recipe["check"] = ctx.check(expect_truth={field: arg})
    return _finish(ctx, recipe, tests, run)


def _different(spec: dict, value: Any, current: Any) -> Any:
    """A test value that differs from what the field holds now (else the edit
    would prove nothing), or None when there is no other value to try."""
    if not rc._same(value, current if current is not None else ""):
        return value
    if spec.get("enum"):
        return next((o for o in spec["enum"] if not rc._same(o, current)), None)
    if spec.get("type") == "number":
        for cand in (value + 1, value - 1):
            if spec.get("min", -1e12) <= cand <= spec.get("max", 1e12):
                return cand
        return None
    pat = str(spec.get("pattern") or "")
    if r"\d{4}-\d{2}-\d{2}" in pat:
        return "2026-11-20" if value != "2026-11-20" else "2026-11-21"
    if r"\d{2}:\d{2}" in pat:
        return "11:11" if value != "11:11" else "12:12"
    return f"{value}-2"


def _edit(ctx: _Ctx, driver: Any, new_copy: Callable[[], str], info: dict, c: dict,
          snap: dict, taken: set) -> dict:
    """The row's button opened a form holding the record: compile it like an add form."""
    controls = snap.get("controls") or []
    k1 = _norm(ctx.k1)
    forms = compile_form._forms_in(controls, controls)
    form = next((f for f in forms if any(_norm(x.get("value")) == k1 for x in f[0])), None)
    if form is None:
        return {"ok": False, "why": "the row's button opened nothing the bot can use"}
    fields, save = form
    marker = "PPTEST-" + secrets.token_hex(4)
    args: dict[str, dict] = {ctx.key_arg: dict(ctx.key_spec, required=True)}
    tests: dict[str, Any] = {ctx.key_arg: ctx.k1}
    fills: list[dict] = []
    for i, f in enumerate(fields):
        if _norm(f.get("value")) == k1:
            continue                       # the key stays as it is
        got = compile_form._argument(f, i, marker)
        if got is None:
            continue
        spec, value, step = got
        value = _different(spec, value, f.get("value"))
        if value is None:
            continue
        a = compile_form._slug(f.get("name") or "", f"{f.get('role')}_{i}")
        if not rc.label_ok(a, "name"):
            a = f"value_{i}"
        while a in args:
            a = (a + "_2")[:40]
        args[a] = spec
        tests[a] = value
        fills.append(dict(step, arg=a, if_arg=a))
    save_step = compile_form._click_step(save)
    if not fills or save_step is None:
        return {"ok": False, "why": "the edit form has nothing the bot can fill"}
    recipe = {"name": "row_edit", "says": "x", "effect": "changes", "start": "reload",
              "args": args,
              "steps": ctx.steps(ctx.row_step("click_in_row", info, c["sel"]), {"do": "wait", "ms": 400})
              + fills + [{"do": "wait", "ms": 200}, save_step, {"do": "wait", "ms": 500}]}
    run = _run_on_fresh(ctx, driver, new_copy, recipe, tests)
    if run is None or not run["res"].get("ok"):
        return {"ok": False, "why": "the edit did not work on the test copy"}
    d = row_diff(run["before"], run["after"], storage_key=ctx.storage_key, path=ctx.path,
                 key_field=ctx.key_field)
    if d is not None and not d["gone"] and not d["changed"] and not d["new"]:
        return {"ok": False, "why": "the edit changed nothing"}
    if not _only_this_record(d, k1) or d["gone"] or not d["changed"].get(k1):
        return {"ok": False, "why": "the edit touched more than its one record"}
    rec = d["after"][k1]
    expect = {}
    for a, v in tests.items():
        if a == ctx.key_arg:
            continue
        field = next((f for f in d["changed"][k1] if rc._same(rec.get(f), v)), None)
        if field is None:
            recipe["args"].pop(a, None)
            recipe["steps"] = [s for s in recipe["steps"] if s.get("arg") != a]
        else:
            expect[field] = a
    if not expect:
        return {"ok": False, "why": "the edit stored none of the details"}
    recipe["name"] = _unique(f"edit_{compile_form._slug(ctx.noun, 'entry')}", taken)
    recipe["says"] = wording.SAYS_EDIT.format(a=wording.article(ctx.noun), noun=ctx.noun)
    recipe["check"] = ctx.check(expect=expect)
    tests = {a: v for a, v in tests.items() if a in recipe["args"]}
    return _finish(ctx, recipe, tests, run)


def _button(ctx: _Ctx, driver: Any, new_copy: Callable[[], str], info: dict, c: dict,
            taken: set) -> dict:
    probe = {"name": "row_probe", "says": "x", "effect": "removes", "start": "reload",
             "args": {ctx.key_arg: dict(ctx.key_spec, required=True)},
             "steps": ctx.steps({"do": "accept_next_dialog"}, ctx.row_step("click_in_row", info, c["sel"]),
                                {"do": "wait", "ms": 400})}
    tests = {ctx.key_arg: ctx.k1}
    run = _run_on_fresh(ctx, driver, new_copy, probe, tests, snapshot_after=True)
    if run is None or not run["res"].get("ok"):
        return {"ok": False, "why": "the button did not work on the test copy"}
    d = row_diff(run["before"], run["after"], storage_key=ctx.storage_key, path=ctx.path,
                 key_field=ctx.key_field)
    k1 = _norm(ctx.k1)
    asked = bool(run["res"].get("dialogs"))
    click = [ctx.row_step("click_in_row", info, c["sel"]), {"do": "wait", "ms": 400}]
    ask = [{"do": "accept_next_dialog"}] if asked else []
    if d is not None and not d["gone"] and not d["changed"] and not d["other_keys"] \
            and not d["outside_list"] and not d["new"]:
        return _edit(ctx, driver, new_copy, info, c, run["snap"] or {}, taken)
    if not _only_this_record(d, k1):
        return {"ok": False, "why": "the button touched more than its one record"}
    if d["gone"] == [k1] and not d["changed"]:
        recipe = {"name": _unique(f"remove_{compile_form._slug(ctx.noun, 'entry')}", taken),
                  "says": wording.SAYS_REMOVE.format(a=wording.article(ctx.noun), noun=ctx.noun),
                  "effect": "removes", "start": "reload",
                  "args": {ctx.key_arg: dict(ctx.key_spec, required=True)},
                  "steps": ctx.steps(*ask, *click),
                  "check": ctx.check(present=False)}
        return _finish(ctx, recipe, tests, run)
    if not d["gone"] and len(d["changed"].get(k1, [])) == 1:
        field = d["changed"][k1][0]
        words = field_words(field)
        recipe = {"name": _unique(f"change_{words.replace(' ', '_')}", taken),
                  "says": wording.SAYS_CHANGE.format(field=words, a=wording.article(ctx.noun),
                                                     noun=ctx.noun),
                  "effect": "removes" if asked else "changes", "start": "reload",
                  "args": {ctx.key_arg: dict(ctx.key_spec, required=True)},
                  "steps": ctx.steps(*ask, *click),
                  # a button that flips a field: it landed only if the record changed
                  "check": ctx.check(changed=True)}
        return _finish(ctx, recipe, tests, run)
    return {"ok": False, "why": "the button changed more than one field"}


def compile_rows(driver: Any, new_copy: Callable[[], str], add: dict, *,
                 taken: Optional[set] = None) -> dict:
    """Row actions for the records one compiled add form makes.

    Returns ``{"compiled": [{"recipe", "tests", "seed"}], "skipped": [{"why", ...}]}``;
    ``seed(driver, label)`` puts the two test records on a fresh copy, for
    Drawbridge's own check run.
    """
    taken = taken if taken is not None else set()
    if not add or not add.get("ok") or not add.get("mapping"):
        return {"compiled": [], "skipped": [{"why": "no add form to start from"}]}
    ctx = _Ctx(add)
    explore = new_copy()
    try:
        driver.open(explore, fresh=True)
        info = driver.row_controls(explore, ctx.k1) if ctx.seed(driver, explore) else None
    finally:
        driver.close(explore)
    if not isinstance(info, dict) or not _sel_ok(info.get("row")) or not _sel_ok(info.get("key")):
        return {"compiled": [], "skipped": [{"why": "no list rows the bot can find its records in"}]}
    compiled, skipped = [], []
    for c in (info.get("controls") or [])[:MAX_ROW_CONTROLS]:
        if not isinstance(c, dict) or not _sel_ok(c.get("sel")) or c.get("disabled"):
            skipped.append({"why": "a control the bot cannot point at"})
            continue
        role = c.get("role")
        try:
            if role == "choose":
                out = _choice(ctx, driver, new_copy, info, c, taken)
            elif role == "checkbox":
                out = _checkbox(ctx, driver, new_copy, info, c, taken)
            elif role in BUTTON_ROLES:
                out = _button(ctx, driver, new_copy, info, c, taken)
            else:
                out = {"ok": False, "why": "a kind of control the bot does not use"}
        except Exception as exc:  # noqa: BLE001 - one control failing is not the app failing
            out = {"ok": False, "why": str(exc)[:160]}
        if out.get("ok"):
            out["seed"] = ctx.seed
            compiled.append(out)
        else:
            skipped.append({"why": out.get("why"), "control": str(c.get("text") or c.get("sel"))[:60]})
    return {"compiled": compiled, "skipped": skipped}


__all__ = ["compile_rows", "row_diff", "field_words", "MAX_ROW_CONTROLS"]
