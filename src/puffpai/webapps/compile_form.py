"""Toolsmith's form compiler: the app's own add forms become checked actions.

Goal-agnostic: it knows no app. Nothing here is written by hand for an app,
and the local model writes no steps. On throwaway check copies (their own
never-reused address, empty storage, every outside host off):

1. find the forms: the page as it opens, then behind each visible button or
   tab whose words say add, new, create, save, ``+``, plus or insert (one or
   two clicks deep);
2. the form's fields are the controls that appeared with the last click (or
   the visible fields before a save button); each field becomes a typed
   argument from the control itself: a select's options become the choices,
   a number field gets bounds, a date a pattern, text a length limit;
3. fill every field with a marked test value (``PPTEST-<hex>``), press the save
   button, and DIFF the app's stored data: the new record shows which stored
   field each argument landed in. That diff is the action's check, and the
   first text argument that landed is the key the owner names it by;
4. reads come from the same diff: the stored list the record landed in gets a
   ``read_storage`` action with the learned fields;
5. the words (``says``) come from templates filled with the form's own heading
   or button words, cleaned; Drawbridge checks them (R8).

The effect is never written by hand: an add form's effect is the one the
effect proof (``recipe_check.prove_effect``, R11) finds in the stored-data diff
of the compile run itself, and Drawbridge proves it again on its own test run
on a fresh copy; the stricter one wins. A read only reads stored data.

The driver (``runner_host.RunnerDriver``, or a stand-in in tests) offers::

    open(label, fresh=False); reload(label); snapshot(label) -> {"controls", "headings", "title"}
    click(label, ref) -> dict; storage(label) -> {"localStorage": {..}, "indexedDB": [..]}
    count(label, sel) -> int | None; run(label, recipe, args) -> {"ok", "error", "results",
    "dialogs", "clicked"}; close(label)

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import re
import secrets
from typing import Any, Callable, Optional

from puffpai.webapps import recipe_check as rc
from puffpai.webapps import wording

#: The same words in the languages apps are most often written in (an app
#: in Russian has "Добавить", which no English word list finds).
#: Spanish, Portuguese, French, Italian, German, Russian, Korean, Chinese and
#: Japanese.
_ADD_OTHER = (r"a[ñn]adir|agregar|adicionar|ajouter|aggiungi(?:ere)?|hinzuf[üu]gen|anlegen|"
              r"добавить|추가|添加|新增|追加")
_NEW_OTHER = (r"nuev[oa]|nov[oa]|nouve(?:au|lle)|nuov[oa]|neue?[rs]?|нов(?:ый|ое|ая)|새|新建|新規|"
              r"crear|criar|cr[ée]er|crea(?:re)?|erstellen|создать|만들기|创建|作成")
_SAVE_OTHER = (r"guardar|salvar|enregistrer|sauvegarder|salva(?:re)?|speichern|сохранить|저장|"
               r"保存|enviar|envoyer|invia|absenden|отправить")
OPENER_WORDS = re.compile(r"^\W*(add|new|create|save|plus|insert|" + _ADD_OTHER + "|" + _NEW_OTHER
                          + r")(?!\w)|^\s*\+", re.I)
SAVE_WORDS = re.compile(r"^\W*(save|add|submit|create|done|ok|confirm|insert|" + _ADD_OTHER + "|"
                        + _SAVE_OTHER + r")(?!\w)|^\s*[+＋](?:\s|$)", re.I)
FIELD_ROLES = {"text", "choose", "number", "date", "email", "tel", "url", "time", "range"}
_PLACEHOLDER_OPTION = re.compile(r"^\W*(all|none|any|unassigned|select|choose|pick|--|—)", re.I)
_SEARCHY = re.compile(r"\b(search|filter|find)\b", re.I)
_STOP = {"a", "an", "the", "new", "add", "create", "save", "insert", "plus", "your", "my", "to",
         "of", "this", "button", "submit", "ok", "done", "confirm", "another", "one"}
MAX_FORMS = 8
MAX_WALKS = 24
#: How many page elements before a form's first field its heading may sit.
HEADING_REACH = 12


def _label(text: str) -> str:
    """A control's label without hints in brackets, stars or a trailing colon."""
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", str(text or ""))
    return re.sub(r"[\s*:]+$", "", re.sub(r"\s+", " ", t)).strip()


def _slug(text: str, fallback: str = "value") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", _label(text).lower()).strip("_")
    s = re.sub(r"^[0-9_]+", "", s)
    return (s or fallback)[:30]


def _clean_words(text: str, limit: int = 40) -> str:
    t = re.sub(r"[^A-Za-z0-9 '\-]+", " ", str(text or ""))
    return re.sub(r"\s+", " ", t).strip()[:limit]


def _field_words(text: str, limit: int = 60) -> str:
    """A field's words for the AI, like :func:`_clean_words` but a clock time
    stays whole: "08:00 or every 6h" is not "08 00 or every 6h" (the AI
    copied that and the app refused it). Only a colon between two digits is
    kept; names and slugs never use this."""
    t = re.sub(r"[^A-Za-z0-9 :'\-]+|(?<!\d):|:(?!\d)", " ", str(text or ""))
    return re.sub(r"\s+", " ", t).strip()[:limit].lower()


def noun_from(*candidates: str) -> str:
    """The thing a form adds, from its heading or button words; "entry" if none."""
    for text in candidates:
        words = [w for w in _clean_words(text, 60).lower().split() if w not in _STOP]
        words = [w for w in words if re.fullmatch(r"[a-z][a-z'\-]{1,20}", w)]
        if words:
            noun = " ".join(words[:3])
            if not rc.BANNED_WORDS.search(noun) and not rc._orders(noun):
                return noun
    return "entry"


def _form_noun(form: dict) -> str:
    """The thing a form adds. A form that saves on Enter has no button
    words: its field's own words name it ("Add to your list..." adds an
    item to the list, so "list" there is "item")."""
    enter = bool((form.get("save") or {}).get("enter"))
    names = [str(f.get("name") or "") for f in form.get("fields") or []] if enter else []
    save = _words(form.get("save") or {})
    # A save button that says what it adds ("Add Medicine", "+ CATEGORY")
    # names the thing better than a heading further up ("Add a grocery run"
    # for a form that adds a category); "Save changes" does not.
    says_what = save if _ADDS_WHAT.match(save) else ""
    noun = noun_from(says_what, form.get("heading") or "", *(form.get("walk_words") or []),
                     save, *names)
    if enter and noun in ("list", "lists", "entry", "entries"):
        return "item"
    return _one_of(noun)


#: A button that adds a named thing: "Add Medicine", "+ Item", "Create event".
_ADDS_WHAT = re.compile(r"^\W*(?:add|create|insert|new)\s+\w|^\s*[+＋]\s*\w", re.I)


def _one_of(noun: str) -> str:
    """The noun for one thing ("suggested medications" is "suggested
    medication"), so "Add a ..." reads right. Only a plain plural ending."""
    words = noun.split()
    if not words:
        return noun
    last = words[-1]
    if len(last) > 4 and last.endswith("ies"):
        last = last[:-3] + "y"
    elif len(last) > 3 and last.endswith("s") and not last.endswith(("ss", "us", "is", "ics", "news")):
        last = last[:-1]
    return " ".join(words[:-1] + [last])


def _key(c: dict) -> str:
    return c.get("sel") or f"{c.get('role')}|{c.get('name')}|{c.get('text')}"


def _is_field(c: dict) -> bool:
    if c.get("disabled") or c.get("role") not in FIELD_ROLES:
        return False
    if c.get("type") in ("search", "password", "hidden") or _SEARCHY.search(str(c.get("name") or "")):
        return False
    return True


def _words(c: dict) -> str:
    return str(c.get("text") or c.get("name") or "")


def _is_button(c: dict) -> bool:
    return c.get("role") in ("button", "link", "tab") and not c.get("disabled")


def _click_step(c: dict) -> Optional[dict]:
    sel = c.get("sel")
    if sel and rc.SAFE_SEL.fullmatch(sel) and not rc.BAD_SEL.search(sel):
        return {"do": "click", "sel": sel}
    text = re.sub(r"\s+", " ", str(c.get("text") or "")).strip()
    if not text or len(text) > 60 or re.search(r"[\x00-\x1f{}$\\]", text):
        return None
    tag = {"tab": "[role=tab]", "link": "a"}.get(c.get("role"), c.get("tag") or "button")
    if not re.fullmatch(r"[a-z]+|\[role=tab\]", tag):
        return None
    return {"do": "click_text", "sel": tag, "text": text}


def _forms_in(controls: list[dict], candidates: list[dict]) -> list[tuple[list[dict], dict]]:
    """Split fields into forms, each ended by the save-like button after them."""
    out = []
    fields: list[dict] = []
    for c in controls:
        if c not in candidates:
            continue
        if _is_field(c):
            fields.append(c)
        elif _is_button(c) and SAVE_WORDS.search(_words(c)) and not rc.DESTRUCTIVE.search(_words(c)):
            if fields:
                out.append((fields, c))
                fields = []
    return out


#: Field kinds a form that saves on Enter may hold (the key goes to the
#: field typed into last, which keeps the focus).
_ENTER_ROLES = frozenset({"text", "email", "tel", "url"})
#: The most fields a form with no save button may have.
ENTER_FIELDS_MAX = 3
#: What a form that saves on Enter uses as its "save button".
ENTER_SAVE = {"enter": True, "role": "enter", "text": ""}


def _enter_forms(controls: list[dict], candidates: list[dict],
                 taken: set) -> list[tuple[list[dict], dict]]:
    """Text fields with no save button after them, saved by pressing Enter
    ("Add to your list..." in a to-do app). One run of
    1 to :data:`ENTER_FIELDS_MAX` text fields at the end of the page or just
    before a control that is not a save button; never a field a form with a
    save button already holds. Whether Enter stores anything is proven by the
    compile run itself, like every form."""
    out = []
    run: list[dict] = []

    def close() -> None:
        if 0 < len(run) <= ENTER_FIELDS_MAX and all(c.get("role") in _ENTER_ROLES for c in run):
            out.append((list(run), dict(ENTER_SAVE)))
        run.clear()
    for c in controls:
        if c not in candidates:
            continue
        if _is_field(c) and _key(c) not in taken:
            run.append(c)
        elif _is_button(c) and SAVE_WORDS.search(_words(c)):
            run.clear()               # that run has a save button: a form already
        elif run:
            close()
    close()
    return out


def _save_step(save: dict) -> Optional[dict]:
    if save.get("enter") is True:
        return {"do": "key", "key": "Enter"}
    return _click_step(save)


# ---------------------------------------------------------------------------
# finding the forms
# ---------------------------------------------------------------------------

def find_forms(driver: Any, label: str) -> list[dict]:
    """Each form: {"walk": [steps], "walk_words": [str], "fields": [..], "save": {..}, "heading": str}."""
    found: list[dict] = []
    seen_fields: set[tuple] = set()
    walks = 0

    def add(walk: list[dict], walk_words: list[str], before: list[dict], snap: dict) -> None:
        controls = snap["controls"]
        prior = {_key(c) for c in before} if walk else set()
        new = [c for c in controls if _key(c) not in prior] if walk else controls
        with_button = _forms_in(controls, new)
        held = {_key(f) for fields, _s in with_button for f in fields}
        for fields, save in with_button + _enter_forms(controls, new, held):
            sig = tuple(sorted(_key(f) for f in fields))
            if sig in seen_fields:
                continue
            seen_fields.add(sig)
            first = min(f.get("idx", 0) for f in fields)
            # only a heading right before the form names it; one further up the
            # page belongs to something else (then the button words name it)
            heads = [h for h in snap.get("headings") or []
                     if first - HEADING_REACH <= h.get("idx", 0) < first]
            heading = heads[-1]["text"] if heads else ""
            found.append({"walk": walk, "walk_words": walk_words, "fields": fields, "save": save,
                          "heading": heading})

    driver.open(label, fresh=True)
    snap0 = driver.snapshot(label)
    add([], [], [], snap0)

    def openers(controls: list[dict], exclude: set[str]) -> list[dict]:
        return [c for c in controls if _is_button(c) and _key(c) not in exclude
                and OPENER_WORDS.search(_words(c)) and not rc.DESTRUCTIVE.search(_words(c))]

    base_keys = {_key(c) for c in snap0["controls"]}
    first_level = openers(snap0["controls"], set())
    tabs = [c for c in snap0["controls"] if c.get("role") == "tab" and c not in first_level]
    plans: list[list[dict]] = [[o] for o in first_level] + [[t] for t in tabs]
    while plans and walks < MAX_WALKS and len(found) < MAX_FORMS:
        plan = plans.pop(0)
        walks += 1
        steps = [_click_step(c) for c in plan]
        if any(s is None for s in steps):
            continue
        driver.reload(label)
        before = driver.snapshot(label)
        ok = True
        for c in plan:
            now = driver.snapshot(label)
            target = next((x for x in now["controls"] if _key(x) == _key(c)), None)
            if target is None:
                ok = False
                break
            before = now
            driver.click(label, target["ref"])
        if not ok:
            continue
        after = driver.snapshot(label)
        walk = []
        for s in steps:
            walk += [s, {"do": "wait", "ms": 400}]
        add(walk, [_words(c) for c in plan], before["controls"], after)
        if len(plan) == 1 and plan[0].get("role") == "tab":
            for o in openers(after["controls"], base_keys):
                plans.append(plan + [o])
    return found[:MAX_FORMS]


# ---------------------------------------------------------------------------
# compiling one form
# ---------------------------------------------------------------------------

_EXAMPLE_AFTER = re.compile(r"(?:\be\.?\s?g\.?|\bfor\s+example|\bexample)\s*[:,]?\s*([^,;()\[\]]{1,30})", re.I)
#: A field's words that start with a clock time: the words are an example.
_CLOCK_LEAD = re.compile(r"^\s*\d{1,2}:\d{2}(?:\s|$)")
_EXAMPLE_LEAD = re.compile(r"^\s*(\d{1,2}:\d{2}|\d{1,6}(?:[.,]\d{1,3})?)(?=\s|$)")


def _example(c: dict) -> Optional[str]:
    """The example a field's own hint gives ("e.g. 1" gives "1", "08:00 or
    every 6h" gives "08:00"), or None. Only short plain words or digits."""
    text = str(c.get("name") or "")
    m = _EXAMPLE_AFTER.search(text) or _EXAMPLE_LEAD.search(text)
    if not m:
        return None
    ex = m.group(1).strip().rstrip(".")
    if not ex or len(ex) > 30 or not re.fullmatch(r"[\w :./\-]+", ex) or rc._orders(ex):
        return None
    return ex


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) < 1e12 else None


def _argument(c: dict, i: int, marker: str) -> Optional[tuple[dict, Any, dict]]:
    """(arg spec, test value, fill step) from one control, or None to skip it."""
    sel = c.get("sel")
    if not sel or not rc.SAFE_SEL.fullmatch(sel) or rc.BAD_SEL.search(sel):
        return None
    says = _field_words(_label(c.get("name") or "")) or "a value"
    if _CLOCK_LEAD.match(str(c.get("name") or "")):
        # a field whose own words start with a clock time ("08:00 or every
        # 6h") is a time; its words are only an example of one
        says = f"time, like {says}"
    if not rc.label_ok(says, "words"):
        # the app's label reads like orders or is too long: the AI gets a
        # neutral word for it instead (the value still lands where it did)
        says = "a value"
    role = c.get("role")
    if role == "choose":
        opts = [o.get("t") for o in c.get("options") or []
                if o.get("v") not in (None, "") and not o.get("disabled")
                and o.get("t") and not _PLACEHOLDER_OPTION.match(o.get("t"))]
        # the AI never reads these (actions.schema_of keeps them out of the
        # tool list); each is still checked, and one that reads like orders
        # (or is long enough to carry any) is left out and cannot be chosen
        opts = [str(o) for o in dict.fromkeys(opts) if rc.label_ok(str(o), "choice")]
        opts = opts[:rc.MAX_CHOICES]
        if not opts:
            return None
        return ({"type": "string", "enum": opts, "says": says},
                opts[min(1, len(opts) - 1)], {"do": "choose", "sel": sel})
    if role in ("number", "range"):
        lo, hi = _num(c.get("min")), _num(c.get("max"))
        lo = 0 if lo is None else lo
        hi = 1_000_000 if hi is None or hi < lo else hi
        test = min(hi, max(lo, lo + 7 + 3 * i))
        test = int(test) if float(test).is_integer() else round(test, 2)
        lo = int(lo) if float(lo).is_integer() else lo
        hi = int(hi) if float(hi).is_integer() else hi
        return ({"type": "number", "min": lo, "max": hi, "says": says}, test,
                {"do": "fill", "sel": sel})
    if role == "date":
        return ({"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "says": f"{says}, YYYY-MM-DD"},
                f"2026-10-{1 + (i % 27):02d}", {"do": "fill", "sel": sel})
    if role == "time":
        return ({"type": "string", "pattern": r"^\d{2}:\d{2}$", "says": f"{says}, HH:MM"},
                f"10:{(i * 7) % 60:02d}", {"do": "fill", "sel": sel})
    limit = c.get("maxLength") if isinstance(c.get("maxLength"), int) else None
    limit = max(24, min(limit or (500 if c.get("tag") == "textarea" else 120), 500))
    test = f"{marker}{i}"
    if role == "email":
        test = f"{marker.lower()}{i}@example.org"
    return ({"type": "string", "maxLength": limit, "says": says}, test, {"do": "fill", "sel": sel})


def find_new(before: Any, after: Any, path: str = "") -> Optional[tuple[str, dict]]:
    """(path, record) of a record in ``after`` that is not in ``before``."""
    if isinstance(after, list):
        old = {rc.canonical(x) for x in (before if isinstance(before, list) else [])}
        for x in after:
            if isinstance(x, dict) and rc.canonical(x) not in old:
                for k, v in x.items():
                    if isinstance(v, list):
                        prev = next((y for y in (before or []) if isinstance(y, dict)
                                     and y.get("id") is not None and y.get("id") == x.get("id")), {})
                        hit = find_new(prev.get(k, []), v, (path + "." if path else "") + k)
                        if hit:
                            return hit
                return path, x
    if isinstance(after, dict):
        for k, v in after.items():
            hit = find_new(before.get(k) if isinstance(before, dict) else None, v,
                           (path + "." if path else "") + k)
            if hit:
                return hit
    return None


#: A field's only words are examples of what goes in it, not its name
#: (otherwise the placeholder "Produce, Meat, Toiletries..." becomes the field
#: "produce meat toiletries", and chat says "What I put in: produce meat
#: toiletries Produce"): three or more items with commas, two or more ending
#: in "..." or "etc", or "e.g." / "such as" / "for example" in front. A
#: label of two ("City, State") or a lone "Notes..." is the field's own name.
_EXAMPLES_ONLY = re.compile(
    r"^\s*(?:e\.?\s?g\.?|eg|for\s+example|such\s+as|ex\.)[\s:,]"
    r"|^[^,]{1,30}(?:,\s*[^,]{1,30}){2,}$"
    r"|^[^,]{1,30}(?:,\s*[^,]{1,30}){1,}(?:\.\.\.|…|\s+etc\.?)\s*$", re.I)


def _examples_only(text: str) -> bool:
    return bool(_EXAMPLES_ONLY.search(str(text or "").strip()))


def compile_form(driver: Any, label: str, form: dict, *, name: str) -> dict:
    """Compile one form on a fresh copy. Returns {"ok", "why", "recipe", "tests", "read", ...}."""
    marker = "PPTEST-" + secrets.token_hex(4)
    args: dict[str, dict] = {}
    test: dict[str, Any] = {}
    fills: list[dict] = []
    control_of: dict[str, dict] = {}
    noun = _form_noun(form)
    texts = sum(1 for c in form["fields"] if c.get("role") in ("text", "textarea", None)
                and c.get("tag") in ("input", "textarea"))
    for i, c in enumerate(form["fields"]):
        got = _argument(c, i, marker)
        if got is None:
            continue
        spec, value, step = got
        a = _slug(c.get("name") or "", f"{c.get('role')}_{i}")
        if _examples_only(c.get("name") or "") and c.get("role") in ("text", None) \
                and "enum" not in spec:
            # named from the form itself ("category name"), never from its examples
            words = f"{noun} name" if texts == 1 and rc.label_ok(f"{noun} name", "words") \
                else "a value"
            spec = dict(spec, says=words)
            a = _slug(words, f"{c.get('role')}_{i}")
        if _CLOCK_LEAD.match(str(c.get("name") or "")):
            a = "time"               # not "or_every_6h", the example's tail
        if not rc.label_ok(a, "name"):
            a = f"{c.get('role') if c.get('role') in FIELD_ROLES else 'value'}_{i}"
        while a in args:
            a = (a + "_2")[:40]
        args[a] = spec
        test[a] = value
        control_of[a] = c
        fills.append(dict(step, arg=a))
    save_step = _save_step(form["save"])
    if not fills or save_step is None:
        return {"ok": False, "why": "no fields the bot can fill"}
    recipe = {"name": name, "says": wording.SAYS_ADD.format(a=wording.article(noun), noun=noun), "effect": "unproven",
              "start": "reload", "args": args,
              "steps": list(form["walk"]) + fills + [{"do": "wait", "ms": 200}, save_step,
                                                      {"do": "wait", "ms": 500}]}
    def attempt(values: dict) -> tuple:
        driver.open(label, fresh=True)
        before = driver.storage(label)["localStorage"]
        res = driver.run(label, recipe, values)
        after = driver.storage(label)["localStorage"]
        mapping: dict[str, str] = {}
        key = list_path = None
        changed = [k for k in after if after.get(k) != before.get(k)]
        for k in changed:
            try:
                hit = find_new(json.loads(before.get(k) or "null"), json.loads(after[k]))
            except (TypeError, ValueError):
                continue
            if hit:
                key, (list_path, item) = k, hit
                for a, v in values.items():
                    for field, stored in item.items():
                        if isinstance(stored, (dict, list)):
                            continue
                        if rc._same(stored, v) and (isinstance(v, (int, float)) or str(v).strip()):
                            mapping.setdefault(a, field)
                            break
                break
        return res, before, after, key, list_path, mapping, changed

    res, before, after, key, list_path, mapping, changed = attempt(test)
    if not res.get("ok"):
        return {"ok": False, "why": f"the test run failed: {str(res.get('error'))[:120]}"}
    if not key or not mapping:
        # The app refused the made-up words in a field whose own hint gives an
        # example ("08:00 or every 6h", "e.g. 1"): once more with the example
        # there. The first plain text field keeps the marker, so the record is
        # still found by it.
        first_text = next((a for a, sp in args.items() if sp.get("type") == "string"
                           and "enum" not in sp and "pattern" not in sp), None)
        examples = {a: ex for a, c in control_of.items() if a != first_text
                    and args[a].get("type") == "string" and "enum" not in args[a]
                    and (ex := _example(c)) is not None}
        if examples and first_text is not None:
            again = dict(test, **examples)
            got = attempt(again)
            if got[0].get("ok") and got[3] and got[5]:
                test = again
                res, before, after, key, list_path, mapping, changed = got
    if not key or not mapping:
        return {"ok": False, "why": "the form did not store anything the bot can read",
                "changed_keys": changed}
    text_args = [a for a in mapping if args[a]["type"] == "string"
                 and "enum" not in args[a] and "pattern" not in args[a]]
    if not text_args:
        return {"ok": False, "why": "the form has no name field to find its records by"}
    key_arg = text_args[0]
    for a in list(args):
        if a not in mapping:
            args.pop(a)
            recipe["steps"] = [s for s in recipe["steps"] if s.get("arg") != a]
    all_fields = list(dict.fromkeys(mapping[a] for a in mapping))
    # R1 allows 12 arguments: keep the key and the first 11 others (in form order)
    others = [a for a in args if a != key_arg][:11]
    for a in list(args):
        if a != key_arg and a not in others:
            args.pop(a)
            mapping.pop(a, None)
            recipe["steps"] = [s for s in recipe["steps"] if s.get("arg") != a]
    args[key_arg]["required"] = True
    recipe["steps"] = [dict(s, if_arg=s["arg"]) if s.get("arg") and s.get("arg") != key_arg else s
                       for s in recipe["steps"]]
    recipe["check"] = {"storage_key": key, "list": list_path or "",
                       "match": {mapping[key_arg]: key_arg},
                       "expect": {mapping[a]: a for a in mapping if a != key_arg}}
    # the effect is what the diff of this run proves (R11), never a fixed word
    proven, _why = rc.prove_effect(recipe, {a: v for a, v in test.items() if a in args},
                                   before, after, clicked=res.get("clicked") or [],
                                   dialogs=res.get("dialogs") or [])
    if proven == "reads":
        return {"ok": False, "why": "the form changed nothing the bot can see"}
    recipe["effect"] = proven
    tests = {a: (f"{marker}-check" if a == key_arg else v) for a, v in test.items() if a in args}
    fields = all_fields[:20]
    read = {"name": f"read_{_slug(noun, 'entry')}_list", "says": wording.SAYS_READ.format(noun=noun),
            "effect": "reads", "args": {},
            "steps": [{"do": "read_storage", "key": key, "path": list_path or "",
                       "fields": fields, "as": "rows", "max": 100}]}
    return {"ok": True, "why": "", "recipe": recipe, "tests": tests, "read": read,
            "mapping": mapping, "storage_key": key, "list": list_path or "", "noun": noun,
            "written_keys": changed, "walk": list(form.get("walk") or [])}


def compile_all(driver: Any, new_copy: Callable[[], str],
                on_form: Optional[Callable[[int, int], None]] = None) -> dict:
    """Find and compile every add form. Returns {"compiled": [...], "skipped": [...]}.

    ``on_form(done, total)`` is told before each form (for the owner's
    progress line)."""
    explore = new_copy()
    try:
        forms = find_forms(driver, explore)
    finally:
        driver.close(explore)
    compiled, skipped, names = [], [], set()
    for i, form in enumerate(forms):
        if on_form is not None:
            on_form(i, len(forms))
        noun = _form_noun(form)
        base = f"add_{_slug(noun, 'entry')}"
        name = base
        n = 2
        while name in names:
            name = f"{base}_{n}"
            n += 1
        names.add(name)
        label = new_copy()
        try:
            out = compile_form(driver, label, form, name=name)
        except Exception as exc:  # noqa: BLE001 - one form failing is not the app failing
            out = {"ok": False, "why": str(exc)[:160]}
        finally:
            driver.close(label)
        (compiled if out.get("ok") else skipped).append(dict(out, form_name=name))
    reads = {}
    for c in compiled:
        r = c["read"]
        sig = (c["storage_key"], c["list"])
        if sig not in reads:
            while r["name"] in names:
                r = dict(r, name=r["name"] + "_2")
            names.add(r["name"])
            reads[sig] = dict(c, read=r)
    return {"compiled": compiled, "skipped": skipped, "reads": list(reads.values())}


# ---------------------------------------------------------------------------
# buttons that change one stored number or yes/no
# ---------------------------------------------------------------------------

#: The most buttons tried on throwaway copies per app.
BUTTON_TRIES = 10
#: A first word that says what a button does to its value ("Increase row
#: count"): left out of the words for the value itself ("row count").
_BUTTON_VERB = re.compile(r"^(?:increase|decrease|increment|decrement|add|subtract|raise|lower|"
                          r"next|previous|prev|count|plus|minus|toggle|switch|mark|up|down|more|"
                          r"less|one|bump)\s+", re.I)


def _button_words(c: dict) -> str:
    """A button's own plain words (its label first: a "＋" labelled "Increase
    row count"), or "" when it has none with letters."""
    for text in (c.get("name"), c.get("text")):
        words = _clean_words(_label(str(text or "")), 60)
        if re.search(r"[A-Za-z]{3,}", words):
            return words
    return ""


def _value_words(key: str, words: str) -> str:
    """Plain words for the stored value a button changes: the button's words
    without the verb ("row count"), else the storage key's words, else
    "value"."""
    rest = _BUTTON_VERB.sub("", words).strip().lower()
    if rest and rest != words.lower() and len(rest.split()) <= 4 and rc.label_ok(rest, "words"):
        return rest
    t = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(key))
    kw = [w for w in re.split(r"[^A-Za-z]+", t.lower()) if len(w) >= 2]
    if 0 < len(kw) <= 4 and rc.label_ok(" ".join(kw), "words"):
        return " ".join(kw)
    return "value"


def compile_buttons(driver: Any, new_copy: Callable[[], str], *, taken: Any = (),
                    on_button: Optional[Callable[[int, int], None]] = None) -> dict:
    """Buttons whose whole effect is one stored number or yes/no of the app's
    own changing (a row counter's "Increase row count", a switch), each tried
    alone on its own throwaway copy. Each becomes an action with no details
    (effect "changes", so it can be undone) and the value gets one read.

    Fail closed: a button with no plain words, one that opens or saves a
    form, one whose words say delete, clear or reset (in any of the
    languages :data:`recipe_check.DESTRUCTIVE` knows), one that changed
    nothing, more than one stored value, words, a list or a record is never
    compiled here. Drawbridge checks each one again on a fresh copy
    (``recipe_check.check_recipe``) before it can be used."""
    explore = new_copy()
    try:
        driver.open(explore, fresh=True)
        snap = driver.snapshot(explore) or {}
    finally:
        driver.close(explore)
    cands: list[tuple[dict, str]] = []
    seen: set[str] = set()
    for c in snap.get("controls") or []:
        sel = c.get("sel")
        if c.get("role") != "button" or c.get("disabled") or not sel or sel in seen:
            continue
        if not rc.SAFE_SEL.fullmatch(sel) or rc.BAD_SEL.search(sel):
            continue
        words = _button_words(c)
        every = f"{words} {c.get('text') or ''} {c.get('name') or ''}"
        if not words or OPENER_WORDS.search(words) or SAVE_WORDS.search(words) \
                or rc.DESTRUCTIVE.search(every) or not rc.label_ok(words, "words"):
            continue
        seen.add(sel)
        cands.append((c, words))
    names = set(taken)

    def unique(base: str) -> str:
        name, n = base[:40], 2
        while name in names:
            name = f"{base[:36]}_{n}"
            n += 1
        names.add(name)
        return name
    compiled, skipped, reads = [], [], {}
    tries = cands[:BUTTON_TRIES]
    for i, (c, words) in enumerate(tries):
        if on_button is not None:
            on_button(i, len(tries))
        step = {"do": "click", "sel": c["sel"]}
        trial = {"name": "press_trial", "says": words, "effect": "unproven", "start": "reload",
                 "args": {}, "steps": [step, {"do": "wait", "ms": 400}]}
        label = new_copy()
        try:
            driver.open(label, fresh=True)
            before = driver.storage(label)["localStorage"]
            res = driver.run(label, trial, {})
            after = driver.storage(label)["localStorage"]
        except Exception:  # noqa: BLE001 - one button failing is not the app failing
            skipped.append({"why": "the button did not work on the test copy", "control": words})
            continue
        finally:
            driver.close(label)
        differ = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        if not res.get("ok") or len(differ) != 1:
            skipped.append({"why": "the button changed nothing, or more than one thing",
                            "control": words})
            continue
        key = differ[0]
        if not rc._one_counter_change({"args": {}, "check": {"storage_key": key, "value": True}},
                                      before, after):
            skipped.append({"why": "the button changes more than one number or yes/no",
                            "control": words})
            continue
        says = words[:1].upper() + words[1:]
        recipe = {"name": unique("press_" + _slug(words, "button")), "says": says,
                  "effect": "unproven", "start": "reload", "args": {},
                  "steps": [step, {"do": "wait", "ms": 400}],
                  "check": {"storage_key": key, "value": True}}
        # the effect is what the diff of this run proves (R11), never a
        # fixed word; only a proven change is kept here
        proven, _why = rc.prove_effect(recipe, {}, before, after,
                                       clicked=res.get("clicked") or [],
                                       dialogs=res.get("dialogs") or [])
        if proven != "changes":
            skipped.append({"why": "the button does more than change one number or yes/no",
                            "control": words})
            continue
        recipe["effect"] = proven
        what = _value_words(key, words)
        if key not in reads:
            reads[key] = {"name": unique("read_" + _slug(what, "value")), "says": f"Read the {what}",
                          "effect": "reads", "args": {},
                          "steps": [{"do": "read_storage", "key": key, "path": "", "fields": [what],
                                     "as": "rows", "max": 1, "value": True}]}
        compiled.append({"recipe": recipe, "tests": {}, "storage_key": key, "list": "",
                         "written_keys": [key]})
    return {"compiled": compiled, "skipped": skipped,
            "reads": [{"read": r, "storage_key": k, "list": ""} for k, r in reads.items()]}


__all__ = ["find_forms", "compile_form", "compile_all", "compile_buttons", "noun_from", "find_new"]
