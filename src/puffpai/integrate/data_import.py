"""Data and template projects become lists the bot can read (mode ``data_template``).

A project whose files are mostly data (csv, tsv, json) or plain text (md,
txt) is read with the STANDARD LIBRARY only (``csv``, ``json``; no eval, no
pickle, no yaml) and becomes:

* one list app through the existing owner-app path
  (:func:`puffpai.persona_scaffold.scaffold`), one list per data file, at most
  5,000 rows a list, each row given an ``item`` (its first text value) so the
  lists fastpath and the read action can say what a row is;
* for text files, passages in the owner's word look-up
  (:mod:`puffpai.knowledge.upload_search`), each passage neutralised first;
* a ``data_query`` action per list, "Read the <list> list" (``by: reader``).

Drawbridge (kind ``data_template``) checks the draft first: only csv, tsv,
json, md and txt; at most 20 MB a file; at most 5,000 rows a list (more waits
for her yes, then the first 5,000 are kept); field names
``^[A-Za-z0-9 _-]{1,40}$`` and never ``__proto__``, ``constructor`` or
``prototype``; every text value through ``neutralise_passage``; what looks
like personal details in the rows (e-mail addresses, phone numbers, street
addresses, ID numbers) waits for her yes (``pii_rows``). The licence and its
credit line come from the code check that ran before (CC BY credit kept).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from typing import Any, Optional

from puffpai.integrate import records, wording
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

KIND = "data_template"
CHECKER_VERSION = "r3.1"
TYPES = (".csv", ".tsv", ".json", ".md", ".txt")
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 5000
MAX_LISTS = 8
#: Keys of a list app's data that are not lists from the project.
RESERVED_KEYS = frozenset({"credit"})
MAX_FIELDS = 40
MAX_VALUE = 500
MAX_TEXT_FILES = 40
PASSAGE_CHARS = 700
FIELD_RE = re.compile(r"^[A-Za-z0-9 _-]{1,40}\Z")
BAD_FIELDS = frozenset({"__proto__", "constructor", "prototype"})
BLOCK_CODES = frozenset({"wrong_type", "too_big", "bad_fields", "unreadable"})
HOLD_CODES = frozenset({"too_many_rows", "pii_rows"})
_SKIP_TEXT = re.compile(r"^(?:readme|licen[cs]e|copying|changelog|changes|history|"
                        r"contributing|code_of_conduct|security|authors|notice)", re.I)
_SKIP_DATA = re.compile(r"(?:^|/)(?:package(?:-lock)?|composer|tsconfig|manifest|"
                        r"\.[^/]*)\.json\Z|lock|config", re.I)
READ_ACTION = "read_"


class DataError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# -- reading (standard library only) -------------------------------------------------------

def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _clean_value(value: Any) -> Any:
    from puffpai import artifact_review
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    text = " ".join(str(value).split())[:MAX_VALUE]
    return " ".join(artifact_review.neutralise_passage(text, MAX_VALUE).split())


def _rows_from_table(text: str, delimiter: str) -> tuple[list[str], list[dict]]:
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header: list[str] = []
    rows: list[dict] = []
    for i, cells in enumerate(reader):
        if i == 0:
            header = [c.strip() for c in cells]
            continue
        if not any(c.strip() for c in cells):
            continue
        rows.append({header[j] if j < len(header) else f"field {j + 1}": c
                     for j, c in enumerate(cells) if j < MAX_FIELDS})
        if len(rows) > MAX_ROWS:
            break
    return header, rows


def _rows_from_json(text: str) -> tuple[list[str], list[dict]]:
    data = json.loads(text)
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = max(lists, key=len) if lists else []
    if not isinstance(data, list):
        return [], []
    rows: list[dict] = []
    fields: list[str] = []
    for item in data[:MAX_ROWS + 1]:
        if isinstance(item, dict):
            row = {}
            for k, v in list(item.items())[:MAX_FIELDS]:
                if isinstance(v, (str, int, float, bool)) or v is None:
                    row[str(k)] = v
                if str(k) not in fields:
                    fields.append(str(k))
            rows.append(row)
        elif isinstance(item, (str, int, float)):
            rows.append({"item": item})
            if "item" not in fields:
                fields.append("item")
    return fields, rows


def read_file(path: str, rel: str) -> dict[str, Any]:
    """``{"rel", "kind": "list"|"text", "fields", "rows", "text", "bytes"}``
    for one data file. Raises DataError (``too_big``, ``unreadable``)."""
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        raise DataError("too_big")
    with open(path, "rb") as fh:
        text = _decode(fh.read(MAX_FILE_BYTES + 1))
    ext = os.path.splitext(rel)[1].lower()
    try:
        if ext in (".csv", ".tsv"):
            fields, rows = _rows_from_table(text, "\t" if ext == ".tsv" else ",")
            return {"rel": rel, "kind": "list", "fields": fields, "rows": rows,
                    "bytes": size}
        if ext == ".json":
            fields, rows = _rows_from_json(text)
            return {"rel": rel, "kind": "list", "fields": fields, "rows": rows,
                    "bytes": size}
    except (ValueError, csv.Error, RecursionError, MemoryError):
        # RecursionError: JSON nested deeper than the parser allows (a file
        # of 100,000 open brackets). Never a crash, never half-read.
        raise DataError("unreadable") from None
    return {"rel": rel, "kind": "text", "text": text, "bytes": size}


def collect(src_root: str) -> dict[str, Any]:
    """Every data and text file of the unpacked project, read, plus what is
    there that is not allowed. ``{"lists", "texts", "refused": [(rel, code)],
    "other_types": [ext]}``."""
    from puffpai.integrate import classify
    lists: list[dict] = []
    texts: list[dict] = []
    refused: list[tuple[str, str]] = []
    other: list[str] = []
    for rel, full, _size in classify._walk(src_root):  # noqa: SLF001 - no links followed
        name = rel.rsplit("/", 1)[-1]
        ext = os.path.splitext(name)[1].lower()
        if ext not in TYPES:
            if ext in (".yml", ".yaml", ".xml", ".xlsx", ".pkl", ".pickle", ".db", ".sqlite"):
                other.append(ext)
            continue
        if ext in (".csv", ".tsv", ".json"):
            if _SKIP_DATA.search(rel):
                continue
            if len(lists) >= MAX_LISTS:
                continue
        elif _SKIP_TEXT.match(name) or len(texts) >= MAX_TEXT_FILES:
            continue
        try:
            got = read_file(full, rel)
        except DataError as exc:
            refused.append((rel, exc.code))
            continue
        except OSError:
            refused.append((rel, "unreadable"))
            continue
        (lists if got["kind"] == "list" else texts).append(got)
    return {"lists": [x for x in lists if x.get("rows")], "texts": texts,
            "refused": refused, "other_types": sorted(set(other))}


# -- Drawbridge: the data_template checklist -------------------------------------------------

def _pii(rows: list[dict]) -> bool:
    from puffpai.security.data_classifier import DataClassifier
    sample = "\n".join(" ; ".join(str(v) for v in r.values() if isinstance(v, str))
                       for r in rows[:500])
    if not sample.strip():
        return False
    found = DataClassifier().scan_for_pii(sample)
    return any(getattr(found, f, None) for f in (
        "emails", "phones", "ssns", "addresses", "credit_cards", "dates_of_birth",
        "government_ids", "passwords"))


def _row(check: str, ok: Optional[bool], why: str, code: str) -> dict[str, str]:
    return {"check": check, "result": "pass" if ok else ("unknown" if ok is None else "fail"),
            "why": why, "code": code}


def data_rows(draft: dict) -> list[dict[str, str]]:
    """The ``data_template`` checklist over the draft made by :func:`draft_for`."""
    rows: list[dict[str, str]] = []
    codes = {code for _rel, code in draft.get("refused") or []}
    has_any = bool(draft.get("lists") or draft.get("texts"))
    rows.append(_row("Only plain data and text files", has_any and not draft.get("other_types"),
                     "Only csv, tsv, json, md and txt files." if has_any
                     and not draft.get("other_types")
                     else "It has files BuildBot does not read as data.", "wrong_type"))
    rows.append(_row("Not too big", "too_big" not in codes,
                     "Each file is a normal size." if "too_big" not in codes
                     else "A file is too big to read.", "too_big"))
    rows.append(_row("Every file reads", "unreadable" not in codes,
                     "Every file read cleanly." if "unreadable" not in codes
                     else "A file could not be read.", "unreadable"))
    bad = [f for lst in draft.get("lists") or [] for f in lst.get("fields") or []
           if not FIELD_RE.match(str(f)) or str(f).strip().lower() in BAD_FIELDS]
    rows.append(_row("Plain column names", not bad,
                     "The column names are plain words." if not bad
                     else "A column name is not a plain word.", "bad_fields"))
    long_lists = [lst for lst in draft.get("lists") or [] if len(lst.get("rows") or []) > MAX_ROWS]
    rows.append(_row("Fits in a list", not long_lists,
                     "Every list fits." if not long_lists
                     else wording.reason_words("too_many_rows"), "too_many_rows"))
    pii = bool(draft.get("pii"))
    rows.append(_row("No personal details", not pii,
                     "Nothing in it looks like personal details." if not pii
                     else wording.reason_words("pii_rows"), "pii_rows"))
    return rows


def draft_for(rec: dict, got: dict) -> dict[str, Any]:
    lists = got.get("lists") or []
    return {"kind": KIND, "name": rec.get("name") or "", "id": rec["id"],
            "lists": [{"rel": x["rel"], "fields": list(x.get("fields") or [])[:MAX_FIELDS + 1],
                       "rows": x.get("rows") or []} for x in lists],
            "texts": [{"rel": x["rel"]} for x in got.get("texts") or []],
            "refused": list(got.get("refused") or []),
            "other_types": list(got.get("other_types") or []),
            "pii": any(_pii(x.get("rows") or []) for x in lists)}


def fingerprint(draft: dict) -> dict[str, Any]:
    """The draft without its rows (what a verdict is logged with)."""
    out = dict(draft)
    out["lists"] = [{"rel": x["rel"], "fields": x["fields"], "n": len(x["rows"])}
                    for x in draft.get("lists") or []]
    return out


# -- making the list app ----------------------------------------------------------------------

def list_app_id(integration_id: str) -> str:
    """An owner-app id (``^[a-z][a-z0-9_]*$``, at most 32) made from the
    integration id, so it is unique and never reused either."""
    slug, _dash, suffix = integration_id.rpartition("-")
    base = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")[:22].strip("_") or "list"
    if not base[0].isalpha():
        base = "l" + base[:21]
    return f"{base}_{suffix}"[:32]


def list_label(rel: str) -> str:
    stem = os.path.splitext(rel.rsplit("/", 1)[-1])[0]
    words = re.findall(r"[a-z0-9]+", stem.lower())
    return " ".join(words)[:40] or "items"


def _item_of(row: dict, fields: list[str]) -> str:
    for f in ("item", "name", "title", "text", "description", "label"):
        v = row.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for f in fields:
        v = row.get(f)
        if isinstance(v, str) and v.strip() and not re.fullmatch(r"[\d.,\s-]+", v):
            return v.strip()
    return ""


def make_list_app(rec: dict, got: dict, *, credit: str) -> dict[str, Any]:
    """The list app and its rows. Returns ``{"app_id", "lists": {key: n}}``."""
    from puffpai import persona_scaffold
    from puffpai.app_data import AppData
    app_id = list_app_id(rec["id"])
    keys: list[str] = []
    doc: dict[str, list] = {}
    for lst in (got.get("lists") or [])[:MAX_LISTS]:
        key = persona_scaffold.list_key(list_label(lst["rel"])) or "items"
        base, n = key, 2
        # "credit" holds the licence credit line (set below): a data file
        # named credit.csv becomes the list "credit_2", never overwritten.
        while key in doc or key in RESERVED_KEYS:
            key = f"{base}_{n}"[:40]
            n += 1
        fields = [str(f) for f in lst.get("fields") or []][:MAX_FIELDS]
        rows = []
        for row in (lst.get("rows") or [])[:MAX_ROWS]:
            clean = {str(k): _clean_value(v) for k, v in row.items()
                     if FIELD_RE.match(str(k)) and str(k).lower() not in BAD_FIELDS}
            item = _item_of(clean, fields)
            if item and "item" not in clean:
                clean["item"] = item
            rows.append(clean)
        doc[key] = rows
        keys.append(key)
    if not keys:
        raise DataError("unreadable")
    persona_scaffold.scaffold(app_id, name=rec["name"], goal=rec.get("purpose") or rec["name"],
                              lists=[k.replace("_", " ") for k in keys])
    doc["credit"] = [credit] if credit else []
    store = AppData()
    base_doc = store.get_data(app_id) or {}
    base_doc = base_doc if isinstance(base_doc, dict) else {}
    base_doc.update(doc)
    if not store.set_data(app_id, base_doc):
        raise DataError("unreadable")
    return {"app_id": app_id, "lists": {k: len(doc[k]) for k in keys}}


def add_texts(rec: dict, got: dict) -> list[str]:
    """Text files as passages in the owner's word look-up. Returns their ids."""
    from puffpai import artifact_review
    from puffpai.knowledge import upload_search
    ids = []
    for n, txt in enumerate((got.get("texts") or [])[:MAX_TEXT_FILES]):
        text = str(txt.get("text") or "")
        chunks = []
        for i in range(0, min(len(text), 400_000), PASSAGE_CHARS):
            piece = artifact_review.neutralise_passage(text[i:i + PASSAGE_CHARS], PASSAGE_CHARS)
            if piece.strip():
                chunks.append(piece)
        if not chunks:
            continue
        file_id = f"integration-{rec['id']}-{n}"
        upload_search.add(file_id, f"{rec['name']}: {txt['rel'].rsplit('/', 1)[-1]}"[:120],
                          chunks[:600])
        ids.append(file_id)
    return ids


def remove_texts(integration_id: str, count: int = MAX_TEXT_FILES) -> int:
    from puffpai.knowledge import upload_search
    return sum(upload_search.remove(f"integration-{integration_id}-{n}") for n in range(count))


# -- the read action per list ---------------------------------------------------------------

def list_actions() -> list[dict]:
    from puffpai.app_data import AppData
    out: list[dict] = []
    store = None
    for rec in records.all():
        if rec.get("mode") != KIND or rec.get("status") != "ready" or not rec.get("list_app_id"):
            continue
        store = store or AppData()
        try:
            doc = store.get_data(rec["list_app_id"]) or {}
        except Exception:  # noqa: BLE001 - an unreadable list offers nothing
            continue
        for key, rows in (doc.items() if isinstance(doc, dict) else []):
            if key in RESERVED_KEYS or not isinstance(rows, list):
                continue
            name = (READ_ACTION + re.sub(r"[^a-z0-9_]", "_", key.lower()))[:48]
            out.append({
                "id": f"{rec['id']}__{name}", "integration": rec["id"], "app_name": rec["name"],
                "says": f"Read the {key.replace('_', ' ')} list"[:120], "effect": "reads",
                "kind": "data_query",
                "args": {"type": "object", "additionalProperties": False, "properties": {},
                         "required": []},
                "undoable": False,
                "provenance": {"by": "reader", "compiler_version": CHECKER_VERSION,
                               "app_sha": (rec.get("from") or {}).get("archive_sha256", "")[:16],
                               "recipe_sha": ""},
                "checked": {"verdict": "allow", "effect_proven": "reads",
                            "rows_sha": (rec.get("drawbridge") or {}).get("rows_sha", ""),
                            "at": rec.get("added_at") or ""}})
    return out


def run_action(action: dict, args: dict, ctx: dict) -> dict:
    from puffpai.app_data import AppData
    rec = records.get(str(action.get("integration") or ""))
    if rec is None or not rec.get("list_app_id"):
        return {"ok": False, "line": ""}
    key = str(action["id"]).split("__", 1)[1][len(READ_ACTION):]
    doc = AppData().get_data(rec["list_app_id"]) or {}
    rows = next((v for k, v in doc.items()
                 if re.sub(r"[^a-z0-9_]", "_", k.lower()) == key and isinstance(v, list)), [])
    return {"ok": True, "data": {"rows": rows}, "line": ""}


__all__ = ["BLOCK_CODES", "CHECKER_VERSION", "DataError", "HOLD_CODES", "KIND", "MAX_ROWS",
           "TYPES", "add_texts", "collect", "data_rows", "draft_for", "fingerprint",
           "list_actions", "list_app_id", "list_label", "make_list_app", "read_file",
           "remove_texts", "run_action"]
