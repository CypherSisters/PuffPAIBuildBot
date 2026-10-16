"""The card: what one skill decided, in words the owner can read and change.

A card is the whole of what a skill shows: the CLI renderer
and the dashboard render *this object* rather than each writing their own
version of the same interview, which is how fourteen steps and eight steps came
to be two different wizards.

Three rules hold this module up, and every refusal below is one of them:

* **A card is data.** Plain JSON: strings, numbers, booleans, lists, objects.
  Never code, never a command, never a line of prose for something else to
  execute. A gap's fix is the one thing that looks like a command and it goes
  through :mod:`puffpai.wizard.fixes`, which renders it from a registered id.
* **A card carries no secret.** ``secret_entry`` is the field kind for a PIN or
  a key: it has no value on the card, its answer is never written into the run
  log or returned by the API, and a card that arrives carrying a value in one
  is refused rather than cleaned up quietly.
* **Strict in, strict out.** An unknown field kind, an unknown key, an answer
  for a field that does not exist, an answer of the wrong type -- all refused
  with a reason. A renderer that has drifted from the card should fail loudly
  while it is still a bug and not yet an owner's build.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
from typing import Any

from puffpai.wizard import fixes

SCHEMA = "wizard_card/v1"

#: Every status a row on the review screen may be in (spec E).
STATUSES = ("ready", "needs_you", "gap", "blocked", "not_built")

#: How honestly a card knows **how long** its work takes.
#:
#: **``basis`` is about the seconds, not the bytes**, and the difference is the
#: whole rule:
#:
#: * ``bytes`` is a *declared size* -- the shipped source registry's figure for
#:   a pack, the model catalog's figure for a model. It is always a real number
#:   somebody published, so it is summed into a total whatever the basis says.
#:   Dropping a 96 GB row from the total because nobody timed it would tell an
#:   owner a 96 GB build downloads nothing.
#: * ``seconds`` is a *prediction about this machine and this connection*.
#:   ``measured`` means this build measured it, ``calibrated`` means it comes
#:   from real runs elsewhere, and ``unknown`` means nobody knows -- in which
#:   case :func:`_estimate` drops the number entirely rather than offering one,
#:   and a total containing such a row says "time unknown".
#:
#: The renderers say it in those words: the review screen's total reads
#: "about 96 GB declared - time unknown" (``reviewModel.totalLine``).
BASES = ("measured", "calibrated", "unknown")

#: One unit convention for every size this wizard puts in front of an owner.
#: **A GB here is 1024 MB**, which is what the model catalog's ``disk_gb`` and
#: the source registry's ``size_mb`` already mean and what the console printed.
#: The review screen formatted the same figure in powers of a thousand, so one
#: Sources row said "about 57.2 GB" in its own words and "61.4 GB" in the
#: estimate beside it -- two numbers for one download. Everything that turns
#: bytes into words goes through :func:`format_bytes`
#: (``sources_plan.size_label``, ``render_cli._gb``) or its mirror in
#: ``dashboard/app/onboarding/review/reviewModel.ts``.
_KB = 1024
_MB = 1024 ** 2
_GB = 1024 ** 3


def format_bytes(size: Any) -> str:
    """Bytes in the units a person reads. Empty for nothing to download."""
    try:
        value = float(size or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value >= _GB:
        gigabytes = value / _GB
        return (f"{round(gigabytes)} GB" if gigabytes >= 100
                else f"{round(gigabytes, 1):g} GB")
    if value >= _MB:
        return f"{round(value / _MB)} MB"
    return f"{max(1, round(value / _KB))} kB"

#: The four things that make a row consequential. A reason code,
#: not free text, so the review screen can weight rows without reading prose.
CONSEQUENTIAL_REASONS = ("heavy_bytes", "destructive_rerun", "runtime_egress",
                         "vault_pin_unset")

#: The field kinds a renderer knows how to draw. Adding one means teaching both
#: renderers, so the list is short on purpose.
FIELD_KINDS = ("choice", "multi_choice", "text", "toggle", "number",
               "secret_entry", "info")

#: Kinds the owner cannot answer: ``info`` is a line of the card, not a question.
READ_ONLY_KINDS = ("info",)

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

MAX_FIELDS = 40
MAX_OPTIONS = 60
MAX_LABEL = 120
MAX_HELP = 300
MAX_TITLE = 80
MAX_DECIDED = 200
MAX_TEXT_CAP = 2000
MAX_PATTERN = 200

_CARD_KEYS = {"schema", "skill", "section", "part", "title", "decided",
              "status", "consequential", "consequential_reasons", "estimate",
              "fields", "gaps"}
_FIELD_KEYS = {"id", "kind", "label", "help", "current", "proposed", "options",
               "pattern", "max_length", "min", "max", "free_text", "path_like",
               "examples"}
#: Sample answers a text field may show (the browser's buttons fill the box).
MAX_EXAMPLES = 3

#: Words an owner types when they mean "stop asking", which a folder field used
#: to take as a folder name. The Integrate card's knowledge folder accepted the
#: bare word ``no`` and the build ended with ``"zim_dir": "no"`` in its config -
#: a folder that cannot exist, recorded as though the owner had chosen it. An
#: answer that is one of these words is refused and the proposal is kept.
NOT_A_PATH_WORDS = frozenset({
    "y", "n", "yes", "no", "yeah", "nope", "ok", "okay", "sure", "none",
    "null", "nil", "nothing", "skip", "later", "true", "false", "default",
})

#: Characters no folder on either platform may carry. Refused, never stripped:
#: a path this build silently edited is not the path the owner typed.
_PATH_BAD = set('<>|?*"\0\n\r\t')


class CardError(ValueError):
    """A card or an answer this build will not show or store, and why."""


def path_refusal(value: Any) -> str:
    """Why *value* is not a folder path, in one plain sentence, or ``""``.

    Deliberately narrow on purpose (least privilege applied to an answer): a
    bare word is refused rather than turned into a relative folder somewhere
    nobody can predict. A separator, a drive letter, a ``~`` or a ``.`` is what
    makes an answer a place; anything else is a word.
    """
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    if text.lower() in NOT_A_PATH_WORDS:
        return (f"'{text}' is a word, not a folder. Leave it blank to keep "
                f"what is proposed.")
    if set(text) & _PATH_BAD:
        return "A folder path cannot contain < > | ? * or a quote."
    if ("/" in text or "\\" in text or text.startswith("~")
            or text.startswith(".")
            or re.match(r"^[A-Za-z]:$", text)):
        return ""
    return (f"'{text}' does not look like a folder. Give the whole path, or "
            f"leave it blank to keep what is proposed.")


def plain(text: Any, limit: int) -> str:
    """Printable text, capped, with control characters dropped not escaped.

    A card is printed to a terminal and rendered in a browser, so an escape
    sequence in a label is a way to repaint someone's console. The characters
    that could do it never survive this function.
    """
    raw = text if isinstance(text, str) else ""
    clean = "".join(ch for ch in raw
                    if ch == " " or (ch.isprintable() and ch != "\x7f"))
    return " ".join(clean.split())[:limit]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _option(raw: Any, field_id: str) -> dict:
    """One row of a choice: ``{"value", "label"}``. Values are plain tokens."""
    if isinstance(raw, str):
        raw = {"value": raw, "label": raw}
    if not isinstance(raw, dict):
        raise CardError(f"Field '{field_id}': an option is an object or a "
                        f"string, not {type(raw).__name__}.")
    value = raw.get("value")
    if not isinstance(value, str) or not value or len(value) > MAX_LABEL:
        raise CardError(f"Field '{field_id}': an option's value is a short "
                        f"string; {value!r} is not one.")
    return {"value": plain(value, MAX_LABEL),
            "label": plain(raw.get("label") or value, MAX_LABEL)}


def _validate_field(raw: Any, seen: set[str]) -> dict:
    if not isinstance(raw, dict):
        raise CardError("A field is an object, never a line of text.")
    unknown = set(raw) - _FIELD_KEYS
    if unknown:
        raise CardError(
            f"A field carries {sorted(unknown)}, which a card has no place for. "
            f"A renderer only draws what it was taught to draw.")
    field_id = raw.get("id")
    if not isinstance(field_id, str) or not ID_RE.match(field_id):
        raise CardError(
            f"{field_id!r} is not usable as a field id: lowercase letters, "
            f"digits and underscore, starting with a letter.")
    if field_id in seen:
        raise CardError(f"Two fields claim the id '{field_id}'; the answer to "
                        f"one of them would be the answer to neither.")
    seen.add(field_id)

    kind = raw.get("kind")
    if kind not in FIELD_KINDS:
        raise CardError(
            f"Field '{field_id}': {kind!r} is not a field kind this build "
            f"draws ({', '.join(FIELD_KINDS)}).")

    out: dict[str, Any] = {
        "id": field_id,
        "kind": kind,
        "label": plain(raw.get("label"), MAX_LABEL),
        "help": plain(raw.get("help"), MAX_HELP),
    }
    if not out["label"]:
        raise CardError(f"Field '{field_id}' has no label. A question nobody "
                        f"can read is not a question.")

    if kind == "secret_entry":
        # The card is stored and returned over HTTP. A secret has no business
        # in either, so it has no value here at all -- not blanked later, not
        # present now.
        for key in ("current", "proposed"):
            if raw.get(key) not in (None, ""):
                raise CardError(
                    f"Field '{field_id}' is a secret_entry and carries a "
                    f"{key} value. A card never holds a secret.")
        out["current"] = ""
        out["proposed"] = ""
        out["max_length"] = _max_length(raw, field_id, default=128)
        return out

    if kind == "info":
        out["current"] = plain(raw.get("current"), MAX_TEXT_CAP)
        out["proposed"] = out["current"]
        return out

    if kind in ("choice", "multi_choice"):
        options_raw = raw.get("options")
        if not isinstance(options_raw, list) or not options_raw:
            raise CardError(
                f"Field '{field_id}' is a {kind} with nothing to choose from.")
        if len(options_raw) > MAX_OPTIONS:
            raise CardError(f"Field '{field_id}' offers more options than a "
                            f"person is going to read.")
        options = [_option(item, field_id) for item in options_raw]
        values = {item["value"] for item in options}
        if len(values) != len(options):
            raise CardError(f"Field '{field_id}' offers the same value twice.")
        out["options"] = options
        out["free_text"] = bool(raw.get("free_text"))
        if out["free_text"] and kind != "choice":
            raise CardError(f"Field '{field_id}': only a choice may also be "
                            f"typed into.")
        out["pattern"] = _pattern(raw, field_id)
        if out["free_text"] and not out["pattern"]:
            raise CardError(
                f"Field '{field_id}' may be typed into but says nothing about "
                f"what a typed answer may look like.")
        if kind == "choice":
            out["current"] = plain(raw.get("current"), MAX_LABEL)
            out["proposed"] = plain(raw.get("proposed", raw.get("current")),
                                    MAX_LABEL)
            for key in ("current", "proposed"):
                value = out[key]
                if value and value not in values and not out["free_text"]:
                    raise CardError(
                        f"Field '{field_id}': its {key} value {value!r} is not "
                        f"one of the options it offers.")
        else:
            out["current"] = _value_list(raw.get("current"), field_id, values)
            proposed = raw.get("proposed", raw.get("current"))
            out["proposed"] = _value_list(proposed, field_id, values)
        return out

    if kind == "text":
        out["max_length"] = _max_length(raw, field_id, default=200)
        out["pattern"] = _pattern(raw, field_id)
        out["path_like"] = bool(raw.get("path_like"))
        examples = raw.get("examples")
        if examples is not None:
            if not isinstance(examples, list) or len(examples) > MAX_EXAMPLES or not all(
                    isinstance(e, str) and e.strip() for e in examples):
                raise CardError(f"Field '{field_id}': examples are at most "
                                f"{MAX_EXAMPLES} short sentences.")
            out["examples"] = [plain(e, min(MAX_HELP, out["max_length"])) for e in examples]
        out["current"] = plain(raw.get("current"), out["max_length"])
        out["proposed"] = plain(raw.get("proposed", raw.get("current")),
                                out["max_length"])
        return out

    if kind == "toggle":
        for key in ("current", "proposed"):
            value = raw.get(key, raw.get("current"))
            if not isinstance(value, bool):
                raise CardError(
                    f"Field '{field_id}' is a toggle, so its {key} is true or "
                    f"false; {value!r} is neither.")
            out[key] = value
        return out

    # number
    low, high = raw.get("min"), raw.get("max")
    if not _is_number(low) or not _is_number(high) or low > high:
        raise CardError(
            f"Field '{field_id}' is a number and must say what range it "
            f"accepts; {low!r}..{high!r} is not a range.")
    out["min"], out["max"] = low, high
    for key in ("current", "proposed"):
        value = raw.get(key, raw.get("current"))
        if value is None:
            out[key] = None
            continue
        if not _is_number(value) or not low <= value <= high:
            raise CardError(
                f"Field '{field_id}': {value!r} is not a number between {low} "
                f"and {high}.")
        out[key] = value
    return out


def _value_list(raw: Any, field_id: str, values: set[str]) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CardError(f"Field '{field_id}' holds a list of chosen values; "
                        f"{type(raw).__name__} is not one.")
    out = []
    for item in raw:
        if not isinstance(item, str) or item not in values:
            raise CardError(
                f"Field '{field_id}': {item!r} is not one of the options it "
                f"offers.")
        out.append(item)
    return out


def _max_length(raw: dict, field_id: str, *, default: int) -> int:
    value = raw.get("max_length", default)
    if not isinstance(value, int) or isinstance(value, bool) or not (
            1 <= value <= MAX_TEXT_CAP):
        raise CardError(
            f"Field '{field_id}': a length cap is a whole number between 1 and "
            f"{MAX_TEXT_CAP}; {value!r} is not.")
    return value


def _pattern(raw: dict, field_id: str) -> str:
    pattern = raw.get("pattern", "")
    if pattern in (None, ""):
        return ""
    if not isinstance(pattern, str) or len(pattern) > MAX_PATTERN:
        raise CardError(f"Field '{field_id}': its pattern is not a short "
                        f"expression.")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise CardError(
            f"Field '{field_id}': its pattern will not compile ({exc}).") from exc
    return pattern


def _estimate(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise CardError("A card's estimate is an object with bytes, seconds "
                        "and a basis.")
    unknown = set(raw) - {"bytes", "seconds", "basis"}
    if unknown:
        raise CardError(f"An estimate carries {sorted(unknown)}, which is not "
                        f"part of one.")
    basis = raw.get("basis")
    if basis not in BASES:
        raise CardError(
            f"{basis!r} is not an honest basis ({', '.join(BASES)}). A number "
            f"with no basis is a number somebody made up.")
    out = {"basis": basis}
    for key in ("bytes", "seconds"):
        value = raw.get(key, 0)
        if value is None:
            out[key] = None
            continue
        if not _is_number(value) or value < 0:
            raise CardError(f"An estimate's {key} is a number of {key}, not "
                            f"{value!r}.")
        out[key] = value
    if basis == "unknown":
        # Said once, structurally: an unknown basis carries no number to sum.
        out["seconds"] = None
    return out


def validate_card(raw: Any) -> dict:
    """Check one card. Returns it canonical, or raises :class:`CardError`."""
    if not isinstance(raw, dict):
        raise CardError("A card is an object, never a line of text.")
    unknown = set(raw) - _CARD_KEYS
    if unknown:
        raise CardError(
            f"A card carries {sorted(unknown)}, which is not part of a card. "
            f"A renderer only draws what it was taught to draw.")

    skill = raw.get("skill")
    if not isinstance(skill, str) or not ID_RE.match(skill):
        raise CardError(f"{skill!r} is not a skill id.")
    section = raw.get("section")
    if not isinstance(section, str) or not ID_RE.match(section):
        raise CardError(f"Card '{skill}': {section!r} is not a section name.")

    # A skill that asks more than one question names which one this card is
    # (Secure: posture, vault, bridge). One card, one question, and the id the
    # renderers key rows by is ``skill`` or ``skill:part`` -- never a position
    # in a list, which is how two renderers drift.
    part = raw.get("part") or ""
    if not isinstance(part, str) or (part and not ID_RE.match(part)):
        raise CardError(f"Card '{skill}': {part!r} is not a part name.")

    status = raw.get("status")
    if status not in STATUSES:
        raise CardError(
            f"Card '{skill}': {status!r} is not a status ({', '.join(STATUSES)}).")

    reasons_raw = raw.get("consequential_reasons") or []
    if not isinstance(reasons_raw, list):
        raise CardError(f"Card '{skill}': its reasons are a list.")
    reasons = []
    for reason in reasons_raw:
        if reason not in CONSEQUENTIAL_REASONS:
            raise CardError(
                f"Card '{skill}': {reason!r} is not one of the things that make "
                f"a row consequential ({', '.join(CONSEQUENTIAL_REASONS)}).")
        reasons.append(reason)
    consequential = bool(raw.get("consequential"))
    if consequential and not reasons:
        raise CardError(
            f"Card '{skill}' calls itself consequential and does not say why. "
            f"A heavier row has to name what makes it heavy.")
    if reasons and not consequential:
        raise CardError(
            f"Card '{skill}' names {reasons} and does not call itself "
            f"consequential.")

    title = plain(raw.get("title"), MAX_TITLE)
    if not title:
        raise CardError(f"Card '{skill}' has no title. Review-log 12: the "
                        f"title is the owner's words, the id is small type.")

    fields_raw = raw.get("fields") or []
    if not isinstance(fields_raw, list):
        raise CardError(f"Card '{skill}': its fields are a list.")
    if len(fields_raw) > MAX_FIELDS:
        raise CardError(f"Card '{skill}' asks more than one screen of "
                        f"questions.")
    seen: set[str] = set()
    fields = [_validate_field(item, seen) for item in fields_raw]

    return {
        "schema": SCHEMA,
        "skill": skill,
        "section": section,
        "part": part,
        "title": title,
        "decided": plain(raw.get("decided"), MAX_DECIDED),
        "status": status,
        "consequential": consequential,
        "consequential_reasons": reasons,
        "estimate": _estimate(raw.get("estimate")),
        "fields": fields,
        "gaps": fixes.safe_gaps(raw.get("gaps") or []),
    }


def field_map(card: dict) -> dict[str, dict]:
    return {field["id"]: field for field in card.get("fields") or ()}


def card_id(card: dict) -> str:
    """How a renderer names one card: ``skill`` or ``skill:part``."""
    skill = str((card or {}).get("skill") or "")
    part = str((card or {}).get("part") or "")
    return f"{skill}:{part}" if part else skill


def split_card_id(value: Any) -> tuple[str, str]:
    """``"secure:vault"`` -> ``("secure", "vault")``. Never raises.

    Anything that is not two id-shaped halves comes back as ``("", "")``, which
    every caller treats as "no such card" -- the id arrives over HTTP, so it is
    parsed, never trusted.
    """
    if not isinstance(value, str) or len(value) > 2 * 40 + 1:
        return ("", "")
    skill, _, part = value.partition(":")
    if not ID_RE.match(skill or ""):
        return ("", "")
    if part and not ID_RE.match(part):
        return ("", "")
    return (skill, part)


def has_secret_field(card: dict) -> bool:
    """True when this card asks for something only a person can type."""
    return any(field.get("kind") == "secret_entry"
               for field in (card or {}).get("fields") or ())


def field_for_error(message: str, field_ids: Any) -> str | None:
    """Which field a refusal is about, when the message names a known one.

    Every refusal from :func:`validate_answers` is written as ``'field_id'
    takes ...``, so a caller can put it beside the field instead of at the
    bottom of the card. A message naming nothing known returns ``None`` and is
    never reworded: the text the owner sees is the text that was raised.
    """
    match = re.search(r"'([a-z][a-z0-9_]{1,39})'", message or "")
    if not match:
        return None
    return match.group(1) if match.group(1) in set(field_ids or ()) else None


def validate_answers(card: dict, answers: Any) -> dict:
    """Type-check what the owner sent back against the card that asked.

    Returns only the fields that were answered, canonical. An unknown field id,
    an answer to an ``info`` line, or an answer of the wrong type is refused --
    the alternative is a skill applying something nobody was shown.
    """
    if answers is None:
        return {}
    if not isinstance(answers, dict):
        raise CardError("Answers are an object of field id to value.")
    known = field_map(card)
    unknown = sorted(set(answers) - set(known))
    if unknown:
        raise CardError(
            f"This card has no field called {unknown[0]!r}, so there is nothing "
            f"for that answer to change.")

    out: dict[str, Any] = {}
    for field_id, value in answers.items():
        field = known[field_id]
        kind = field["kind"]
        if kind in READ_ONLY_KINDS:
            raise CardError(
                f"'{field_id}' is something the card says, not something it "
                f"asks. There is nothing to answer.")
        out[field_id] = _check_answer(field, kind, value)
    return out


def _check_answer(field: dict, kind: str, value: Any) -> Any:
    field_id = field["id"]
    if kind == "toggle":
        if not isinstance(value, bool):
            raise CardError(f"'{field_id}' is yes or no; {value!r} is neither.")
        return value
    if kind == "number":
        if not _is_number(value) or not field["min"] <= value <= field["max"]:
            raise CardError(
                f"'{field_id}' takes a number between {field['min']} and "
                f"{field['max']}; {value!r} is not one.")
        return value
    if kind == "multi_choice":
        values = {option["value"] for option in field["options"]}
        if not isinstance(value, list):
            raise CardError(f"'{field_id}' takes a list of choices.")
        if len(value) > MAX_OPTIONS:
            raise CardError(f"'{field_id}' was answered with more choices than "
                            f"it offers.")
        for item in value:
            if not isinstance(item, str) or item not in values:
                raise CardError(f"'{field_id}' does not offer {item!r}.")
        return list(dict.fromkeys(value))
    if not isinstance(value, str):
        raise CardError(f"'{field_id}' takes text; {type(value).__name__} is "
                        f"not text.")
    if kind == "secret_entry":
        # Length-checked and control-stripped like anything else, and then
        # handed straight back: it is never stored here, so it is never capped
        # into something that would silently not be the owner's PIN.
        if len(value) > field["max_length"]:
            raise CardError(f"'{field_id}' takes at most "
                            f"{field['max_length']} characters.")
        if any(not ch.isprintable() for ch in value):
            raise CardError(f"'{field_id}' takes printable characters only.")
        return value
    if kind == "choice":
        values = {option["value"] for option in field["options"]}
        if value in values:
            return value
        if field.get("free_text") and field["pattern"] and re.match(
                field["pattern"], plain(value, MAX_LABEL)):
            return plain(value, MAX_LABEL)
        raise CardError(f"'{field_id}' does not offer {value!r}.")
    # text
    cap = field["max_length"]
    if len(value) > cap:
        raise CardError(f"'{field_id}' takes at most {cap} characters.")
    clean = plain(value, cap)
    if field["pattern"] and not re.match(field["pattern"], clean):
        raise CardError(f"'{field_id}' was given something it does not accept.")
    if field.get("path_like"):
        refusal = path_refusal(clean)
        if refusal:
            raise CardError(f"'{field_id}' takes a folder path. {refusal}")
    return clean


def redact_answers(card: dict, answers: dict) -> dict:
    """The copy that may be logged or returned: every secret replaced.

    Used everywhere an answer leaves the process -- the run log, the API reply.
    The replacement says a value was given, because "the owner set a PIN" is a
    fact the log should carry and the PIN is not.
    """
    known = field_map(card)
    out: dict[str, Any] = {}
    for field_id, value in (answers or {}).items():
        field = known.get(field_id)
        if field is not None and field["kind"] == "secret_entry":
            out[field_id] = "(entered, not recorded)"
            continue
        out[field_id] = value
    return out


def total_estimate(cards: list[dict]) -> dict:
    """Bytes and seconds over several cards, refusing to sum an unknown.

    If any card's basis is ``unknown`` the total says so rather
    than dropping that row and presenting a confident number.
    """
    total_bytes = 0
    total_seconds = 0
    seconds_known = True
    bases = set()
    for card in cards:
        estimate = card.get("estimate") or {}
        bases.add(estimate.get("basis"))
        total_bytes += estimate.get("bytes") or 0
        seconds = estimate.get("seconds")
        if seconds is None or estimate.get("basis") == "unknown":
            seconds_known = False
        else:
            total_seconds += seconds
    basis = "unknown" if not seconds_known else (
        "measured" if bases <= {"measured"} else "calibrated")
    return {"bytes": total_bytes,
            "seconds": total_seconds if seconds_known else None,
            "basis": basis}


__all__ = ["BASES", "CONSEQUENTIAL_REASONS", "CardError", "FIELD_KINDS",
           "NOT_A_PATH_WORDS", "SCHEMA", "STATUSES", "card_id",
           "field_for_error", "field_map", "format_bytes", "has_secret_field", "path_refusal",
           "plain", "redact_answers", "split_card_id", "total_estimate",
           "validate_answers", "validate_card"]
