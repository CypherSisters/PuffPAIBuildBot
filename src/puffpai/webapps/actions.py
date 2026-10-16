"""The actions the bot's AI may run in integrated browser apps.

An action exists only while its compiled recipe passes R12 and carries a
current Drawbridge ``allow`` for these exact app files
(:func:`puffpai.webapps.recipes.load`). :func:`run` never trusts the action
dict it is handed beyond its id: it looks the action up again, checks the
arguments against the compiled types, and runs the recipe in the bot's own
hidden browser on the app's real address.

Safety rules at run time:

* a ``removes`` action runs only when ``ctx["yes"] is True`` (the caller has
  redeemed the owner's device-bound yes for exactly this action and these
  arguments); otherwise nothing runs;
* a change is claimed only with evidence from the stored data (evidence or
  silence); when the asked-for change did not land but the data moved
  anyway, the held copy is put back, so "nothing was saved" is only ever
  said over unchanged data;
* an action that picks one existing row by the owner's words (a remove, a
  button flip, an edit) runs only when exactly one stored record matches
  them; with two (two guests called "Guest") or none nothing is clicked, and
  the runner refuses the same on the page (two rows whose key text matches).
  After the run the stored-data diff must show that one record removed or
  changed and nothing else (:func:`puffpai.webapps.recipe_check.one_row_only`);
  an app that also took away or changed another record is put back;
* the effect is proven again on the real run of EVERY action, reads
  included. The bot's copy at the start is held in memory for the whole run;
  after the run the bot waits until the app has gone quiet (no write for
  ``QUIET_S``), closes its own page of the app so nothing the run started can
  write later, and diffs the bot's copy itself against the held one (and the
  page's storage as well; the stricter wins). If a read changed anything, an
  action did more than its checked effect, or the run broke partway after
  writing, the held copy is put back (:func:`puffpai.webapps.mirror.put_back`
  with ``data=``, so it never depends on a kept version file), the put-back
  is checked, and the owner is told what really happened;
* a ``changes`` action returns an undo token (the last version it made);
  undoing it puts back the data from before the whole action;
* while an action runs, only the bot's own hidden browser may write the app's
  copy (:func:`puffpai.webapps.mirror.begin_bot_run`): an edit the owner makes
  on her screen meanwhile waits and is sent again afterwards, so it is never
  counted as the action's, put back with it, or undone with it.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Optional

from puffpai import webapps
from puffpai.webapps import mirror, recipes, wording
from puffpai.webapps import recipe_check as rc

KIND = "web_recipe"
MAX_ROWS = 100
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(app_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(app_id, threading.Lock())


#: The only list of choices the AI is shown: BuildBot's own words (a tick box's
#: yes or no). A select's choices are the app's words, so they never go into
#: the tool list at all (see :func:`schema_of`).
OWN_CHOICES = frozenset({"yes", "no"})
#: The owner's words for one of the app's choices, at most this long.
CHOICE_WORDS_MAX = 48
_CHOICE_HINT = "in the owner's own words"


def _own_choices(choices: Any) -> bool:
    return isinstance(choices, list) and bool(choices) and all(
        isinstance(c, str) and c in OWN_CHOICES for c in choices)


def tick_words(recipe: Any) -> dict[str, Optional[str]]:
    """``{argument: the tick box's field in plain words}`` for each tick box
    of a compiled recipe (``check.expect_truth``), from the app's stored field
    name, never the model. Little words (:data:`_FILLER_WORDS`) are left out.
    A field whose own name could turn a meaning around ("notPaid",
    "ticked", "maybeComing") maps to None: no words of hers can set it, as
    "Sam has paid" would tick "not paid" (fail closed)."""
    check = recipe.get("check") if isinstance(recipe, dict) else None
    truth = check.get("expect_truth") if isinstance(check, dict) else None
    out: dict[str, Optional[str]] = {}
    if isinstance(truth, dict):
        for field, arg in truth.items():
            if not isinstance(arg, str) or not isinstance(field, str):
                continue
            t = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", field)
            words = _choice_form(re.sub(r"[^A-Za-z0-9]+", " ", t)).split()
            if not words or _NEGATION.search(" ".join(words)) or any(
                    _turns(w) or w in _TICK_POLARITY for w in words):
                out[arg] = None
                continue
            out[arg] = " ".join(w for w in words if w not in _FILLER_WORDS)[:40]
    return out


def schema_of(recipe_args: dict, ticks: Optional[dict] = None) -> dict:
    """The recipe's typed arguments as a JSON schema (additionalProperties false).

    This is what the AI reads. A select's choices come from the app and no
    word check can tell every order from a real option, so they are never in
    it: the argument is the owner's own words (short free text, which chat
    grounds in her message), and :func:`check_args` matches those words to
    exactly one of the app's choices at run time. A tick box (BuildBot's own
    yes or no) is the owner's own words too ("paid", "not paid"), never a
    bare yes or no the model picks: chat grounds them in her message and
    :func:`check_args` reads yes or no out of them (:func:`tick_value`).
    No list of choices is ever shown.
    """
    props: dict[str, dict] = {}
    required: list[str] = []
    ticks = ticks if isinstance(ticks, dict) else {}
    for name, spec in (recipe_args or {}).items():
        if not isinstance(spec, dict):
            continue
        p: dict[str, Any] = {}
        if spec.get("enum") and _own_choices(spec.get("enum")):
            field = str(ticks.get(name) or "").strip()
            props[name] = {"type": "string", "maxLength": CHOICE_WORDS_MAX,
                           "description": f"the {field + ' ' if field else ''}tick box, "
                                          f"{_CHOICE_HINT}"}
            required.append(name)
            continue
        if spec.get("type") == "number":
            p = {"type": "number", "minimum": spec.get("min"), "maximum": spec.get("max")}
        elif spec.get("enum") and not _own_choices(spec.get("enum")):
            p = {"type": "string", "maxLength": CHOICE_WORDS_MAX}
        else:
            p = {"type": "string"}
            if spec.get("enum"):
                p["enum"] = list(spec["enum"])
            if spec.get("pattern"):
                p["pattern"] = spec["pattern"]
            if spec.get("maxLength"):
                p["maxLength"] = spec["maxLength"]
        if spec.get("enum") and not _own_choices(spec.get("enum")):
            p["description"] = f"{spec.get('says') or 'the choice'}, {_CHOICE_HINT}"
        elif spec.get("says"):
            p["description"] = str(spec["says"])
        props[name] = p
        if spec.get("required"):
            required.append(name)
    return {"type": "object", "additionalProperties": False, "properties": props,
            "required": required}


def _app_name(app_id: str) -> str:
    from puffpai.webapps import host
    rec = host.read_record(app_id) or {}
    return str(rec.get("name") or app_id)


def _action(app_id: str, app_name: str, r: dict) -> dict:
    checked = r.get("checked") or {}
    prov = r.get("provenance") or {}
    return {
        "id": f"{app_id}__{r['name']}", "integration": app_id, "app_name": app_name,
        "says": r.get("says") or "", "effect": checked.get("effect") or "removes",
        "kind": KIND, "args": schema_of(r.get("args") or {}, tick_words(r)),
        "undoable": bool(checked.get("undoable")),
        "provenance": {"by": prov.get("by"), "compiler_version": prov.get("compiler_version"),
                       "app_sha": prov.get("app_sha"), "recipe_sha": prov.get("recipe_sha")},
        "checked": {"verdict": checked.get("verdict"), "effect_proven": checked.get("effect_proven"),
                    "rows_sha": checked.get("rows_sha"), "at": checked.get("at"),
                    "one_value": _one_value(r)},
    }


def _one_value(r: dict) -> bool:
    """A button whose whole proven effect is one stored number or yes/no of
    its own (a row counter's "Increase row count"): no details, a check of
    that one value, proven a change. Every run proves it again from the
    whole stored-data diff (:func:`recipe_check.prove_effect`) and puts back
    anything more, so the action list may call it a change, not a remove."""
    check = r.get("check") if isinstance(r.get("check"), dict) else {}
    checked = r.get("checked") if isinstance(r.get("checked"), dict) else {}
    return (check.get("value") is True and not r.get("args")
            and checked.get("effect") == "changes" and checked.get("effect_proven") == "changes")


def list_actions() -> list[dict]:
    from puffpai.webapps import host
    out: list[dict] = []
    for app_id in host.installed_ids():
        name = _app_name(app_id)
        for r in recipes.load(app_id):
            # a tick box whose own field name could turn a meaning around
            # ("notPaid") can never be set by her words (:func:`tick_words`),
            # so it is never offered: chat would only ask for words that
            # cannot work
            if any(v is None for v in tick_words(r).values()):
                continue
            out.append(_action(app_id, name, r))
    return out


def _find(action_id: Any) -> Optional[tuple[str, dict, str]]:
    if not isinstance(action_id, str) or "__" not in action_id:
        return None
    app_id, _, name = action_id.partition("__")
    if not webapps.valid_app_id(app_id):
        return None
    from puffpai.webapps import host
    if app_id not in host.installed_ids():
        return None
    for r in recipes.load(app_id):
        if r.get("name") == name:
            return app_id, r, _app_name(app_id)
    return None


#: Words that turn a meaning around. A second guard only: :func:`resolve_choice`
#: already lets through no word it does not know is harmless. Read on the
#: folded form, where every apostrophe is a space ("hasn't" is "hasn t").
_NEGATION = re.compile(
    r"\b(?:not|no|nope|nah|never|neither|nor|none|nothing|nobody|noone|nowhere|without|"
    r"cannot|non|unable|lacks?|lacking)\b"
    # every n't contraction: isn't, hasn't, mustn't, needn't, ain't, won't, can't
    r"|\b\w*n t\b"
    # the same written without the apostrophe: isnt, hasnt, wont, cant, aint
    r"|\b(?:is|was|are|were|has|have|had|do|does|did|ca|could|should|would|must|need|"
    r"might|may|sha|wo|ai|dare|ought)nt\b")

#: The only words the owner's words may hold besides the choice (or a choice
#: besides her words) and still name it: little words that never change what
#: a choice means ("Sam is attending", "set it to paid", "turn it off"). Any
#: other word ("hasn't", "almost", "needs to be", "unable to") makes the
#: words name nothing, because no list of opposites is ever complete.
_FILLER_WORDS = frozenset({
    "a", "an", "the", "is", "are", "am", "s", "has", "have", "been", "as", "to", "set",
    "mark", "make", "change", "put", "turn", "switch", "pick", "choose", "select", "now",
    "please", "it", "its", "he", "she", "they", "i", "we", "you", "him", "her", "them",
    "me", "us", "his", "their", "my", "our", "your", "status", "just",
})
_WORD = re.compile(r"[^\W\d_]{2,}")


#: Letters that read as an apostrophe ("hasn\u02bct"): a space, like "'".
_APOSTROPHE_LOOKS = str.maketrans({c: " " for c in "\u02b9\u02bb\u02bc\u02bd\u02be\u02bf"
                                   "\u02c8\u055a\u05f3\ua78c\u1fbd\u1fbf"})


def _choice_form(text: str) -> str:
    """A choice or the owner's words, folded for matching: NFKC, case folded,
    every run of marks or spaces (and letters that read as an apostrophe) one
    space."""
    import unicodedata
    t = unicodedata.normalize("NFKC", str(text)).casefold().translate(_APOSTROPHE_LOOKS)
    return " ".join(re.sub(r"[^\w]+", " ", t).split())


def _without_run(words: list[str], run: list[str]) -> Optional[list[str]]:
    """``words`` less the first place ``run`` sits in it whole, or None."""
    n = len(run)
    for i in range(len(words) - n + 1):
        if words[i:i + n] == run:
            return words[:i] + words[i + n:]
    return None


#: Words that often turn a meaning around or leave it open ("almost paid",
#: "formerly attending", "paid but refunded"). A second wall only, for the
#: words of her other values (a guest passed as "Sam almost"): every word
#: outside those already has to be a :data:`_FILLER_WORDS` word. "will" and
#: "may" are left out on purpose, as they are also names.
_TURNERS = frozenset({
    "almost", "nearly", "barely", "hardly", "scarcely", "partly", "partially", "half", "kinda",
    "sorta", "kind", "sort", "formerly", "previously", "once", "ex", "former", "yet", "still",
    "until", "unless", "if", "whether", "but", "except", "instead", "or", "maybe", "perhaps",
    "possibly", "probably", "supposedly", "allegedly", "apparently", "pretend", "fake",
    "refund", "refunded", "cancel", "cancelled", "canceled", "undo", "undone", "revoke",
    "revoked", "reversed", "stop", "stopped", "quit", "fail", "failed", "refuse", "refused",
    "would", "could", "should", "might", "must", "shall", "gonna", "going", "need", "needs",
    "needed", "used", "soon", "later", "pending", "waiting", "awaiting", "expected", "due",
    "was", "were", "be",
    # a second wall only; the first is the shape rule in _names_only
    "untrue", "false", "wrong", "wrongly", "incorrect", "doubt", "doubtful", "doubtfully",
    "unlikely", "likely", "wish", "wishes", "hope", "hopes", "hopefully", "claim", "claims",
    "pretends", "pretended", "lie", "lied", "lies", "liar", "lying", "hate", "hates",
    "kid", "kidding", "joke", "joking", "jk", "lol", "lmao", "sarcasm", "sarcastic",
    "reportedly", "seemingly", "rumored", "rumoured", "fake", "fakes", "faked", "bogus",
    "technically", "theory", "disputed", "dispute", "wishful", "hypothetically", "unsure",
    "uncertain", "questionable", "dubious", "denied", "deny", "denies", "rejected", "reject",
})


def _turns(word: str) -> bool:
    """A :data:`_TURNERS` word, or one with a plain ending ("pretends",
    "claimed", "faking"). A second wall only."""
    if word in _TURNERS:
        return True
    for end in ("es", "s", "ed", "d", "ing"):
        if word.endswith(end) and len(word) - len(end) >= 3 and word[:-len(end)] in _TURNERS:
            return True
    return False
#: A first word that makes her words a question when a subject follows it
#: ("Is Leo attending", "Has Sam paid", "Are you coming").
_ASKING_FIRST = frozenset({"is", "are", "am", "was", "were", "has", "have", "had", "do", "does",
                           "did", "will", "would", "can", "could", "should", "shall", "must",
                           "may", "might"})
_LEADING = frozenset({"please", "so", "ok", "okay", "hey", "now", "just", "and"})
#: Written marks that never part two words: the apostrophe and its look-alikes.
_APOSTROPHES = frozenset("'\u2018\u2019\u201b`\u00b4\uff07\u2032")


def _asks(text: Any) -> bool:
    """True when her words hold a question mark of any script: "?" and every
    mark whose Unicode name says QUESTION or INTERROBANG (the Arabic, Greek,
    inverted, reversed and full-width ones, and the emoji)."""
    import unicodedata
    raw = str(text)
    for t in (raw, unicodedata.normalize("NFKC", raw)):
        for ch in t:
            if ch == "?":
                return True
            if not ch.isalnum() and not ch.isspace():
                name = unicodedata.name(ch, "")
                if "QUESTION" in name or "INTERROBANG" in name:
                    return True
    return False


#: The only written marks her words may hold besides letters, digits, spaces,
#: an apostrophe on a word and a dash. Every other mark (an
#: emoji, a cross, "~", "/", "<", "*", "_", a quote mark, brackets, a hidden
#: format character) makes the words name nothing, because no list of marks
#: that turn a meaning around is ever complete ("Sam has paid /s", "Leo is
#: ~~attending~~", "Sam has <s>paid</s>", "Sam has paid \U0001F645").
_PLAIN_MARKS = frozenset(".,;:!&¡、。，．；：！＆")
_DASHES = frozenset("-‐‑‒–—―−﹣－")
#: Every mark that reads as an apostrophe, the letter-like ones too.
_APOSTROPHE_ALL = _APOSTROPHES | frozenset("ʹʻʼʽʾʿˈ՚"
                                           "׳ꞌ᾽᾿")
#: Combining marks that, left over after NFKC, decorate a letter instead of
#: being part of it: strikethrough, overlay and the like.
_DECORATING_MARKS = ((0x0300, 0x036F), (0x1AB0, 0x1AFF), (0x1DC0, 0x1DFF), (0x20D0, 0x20FF),
                     (0xFE20, 0xFE2F))
_ZWNJ = "‌"                 # part of ordinary Persian spelling


def _odd_marks(text: Any) -> bool:
    """True when her words hold any mark that could turn a meaning around or
    hide one, read on her text as written and folded (fail closed):

    * any character that is not a letter, a digit, a space, an apostrophe, a
      dash or one of :data:`_PLAIN_MARKS`;
    * an apostrophe that opens a word (a quote: "Sam has 'paid'");
    * a dash stuck to the front of a word ("Sam has -paid");
    * two full stops or an ellipsis ("Sam has paid...");
    * a combining mark that decorates a letter (a strikethrough);
    * a lone "s" that no apostrophe puts on a word ("paid /s", "paid s");
      "Sam's" stays plain."""
    import unicodedata
    raw = str(text)
    folded = unicodedata.normalize("NFKC", raw)
    for is_folded, t in ((False, raw), (True, folded)):
        for i, ch in enumerate(t):
            if ch in " \t\n\r" or ch == _ZWNJ:
                continue
            prev = t[i - 1] if i else ""
            nxt = t[i + 1] if i + 1 < len(t) else ""
            if ch in _APOSTROPHE_ALL:
                if not prev.isalnum():
                    return True       # an opening quote, never an apostrophe
                continue
            cat = unicodedata.category(ch)
            if cat[0] in "LN" or cat == "Zs":
                continue
            if cat in ("Mn", "Mc"):
                # an accent NFKC joins to its letter is part of the letter; one
                # left over after NFKC decorates it
                if is_folded and any(lo <= ord(ch) <= hi for lo, hi in _DECORATING_MARKS):
                    return True
                continue
            if ch in _DASHES:
                if nxt.isalnum() and not prev.isalnum():
                    return True
                continue
            if ch in _PLAIN_MARKS:
                stops = ".．。"
                if ch in stops and ((prev and prev in stops) or (nxt and nxt in stops)):
                    return True
                continue
            return True
    for m in re.finditer(r"(?<!\w)s(?!\w)", folded, re.I):
        if m.start() == 0 or folded[m.start() - 1] not in _APOSTROPHE_ALL:
            return True
    return False


def _tokens(text: str) -> Optional[list[tuple[str, bool, bool]]]:
    """Her words one by one, folded as :func:`_choice_form` folds them, each
    with whether she wrote it with a capital letter and whether a written mark
    (a comma, a colon, a dash, a new line; never an apostrophe) stands between
    it and the word before. None when the words do not come out the same as
    :func:`_choice_form` (never guess)."""
    import unicodedata
    t = unicodedata.normalize("NFKC", str(text)).translate(_APOSTROPHE_LOOKS)
    out: list[tuple[str, bool, bool]] = []
    prev = 0
    for m in re.finditer(r"\w+", t):
        gap = t[prev:m.start()]
        mark = any(ch in "\n\r" or (unicodedata.category(ch).startswith("P")
                                    and ch not in _APOSTROPHES) for ch in gap)
        for i, w in enumerate(_choice_form(m.group()).split()):
            out.append((w, i == 0 and m.group()[:1].isupper(), mark and i == 0))
        prev = m.end()
    if [w for w, _c, _m in out] != _choice_form(text).split():
        return None
    return out


def _starts(words: list[str], run: list[str]) -> list[int]:
    n = len(run)
    return [i for i in range(len(words) - n + 1) if n and words[i:i + n] == run]


def _asking_shape(words: list[str], starts: list[int]) -> bool:
    """"Is Leo attending", "has Sam paid", "please, are you coming": a first
    word that asks, then a subject before the choice words."""
    i = 0
    while i < len(words) and words[i] in _LEADING:
        i += 1
    if i + 1 >= len(words) or words[i] not in _ASKING_FIRST:
        return False
    nxt = i + 1
    # "Will is attending", "May has paid": a name, then the asking word
    return nxt not in starts and words[nxt] not in ("been", "being", "not") \
        and words[nxt] not in _ASKING_FIRST


#: The only words that may join her one value to the choice ("Sam is paid",
#: "Mark Sam as paid", "Set Sam's status to paid", "Sam has been paid").
#: Never a word that starts a new clause ("Doubtful he is attending").
_LINK_WORDS = frozenset({"is", "are", "am", "s", "has", "have", "been", "as", "to", "now",
                         "just", "status"})


def _name_evidence(toks: list[tuple[str, bool, bool]], i: int) -> bool:
    """She wrote word ``i`` with a capital letter where a sentence does not
    start: not her first word, and no written mark right before it. A capital
    at the start of a sentence ("Unlikely Leo is attending") says nothing."""
    return i > 0 and toks[i][1] and not toks[i][2]


def _names_only(toks: list[tuple[str, bool, bool]], run: list[str], context: Any, *,
                harmless: frozenset = frozenset(), others: frozenset = frozenset(),
                her_marks: bool = False) -> bool:
    """True when her words hold the choice words (``run``) and otherwise only
    little words that never change a meaning (:data:`_FILLER_WORDS` or
    ``harmless``), plus at most ONE of her other values in the same call
    (``context``, the guest's name) in one fixed shape.

    No list of turning words is ever complete, so a value is never allowed
    to cover words by what they are, only by where they stand. The one value:

    * stands once, whole, before the choice words, never after them (where a
      "jk", "KIDDING" or "in theory" stands);
    * is joined to the choice only by :data:`_LINK_WORDS` or the label's own
      words ("Sam is paid", "Mark Sam as paid"), with no written mark from
      the value to the choice ("Untrue: paid" and "Sam: paid" are asked back);
      right beside the choice only when she wrote its last word with a
      capital letter where no sentence starts ("Mark Sam paid");
    * is one word, or (only in her own message) every word of it is written
      with a capital letter where no sentence starts ("Please mark Leo Park
      as attending"; "Leo Doubtful is attending" is asked back);
    * never holds a "no" or a :data:`_TURNERS` word (a second wall).

    So the model cannot move a turning word out of the choice into a note or
    the guest's name: a second value, a value after the choice, a lower-case
    word inside a name, or a word that starts a sentence all make the words
    name nothing, and chat asks her. Words that ask ("Is Leo attending")
    never name a choice."""
    words = [w for w, _c, _m in toks]
    starts = _starts(words, run)
    if not starts or _asking_shape(words, starts):
        return False
    n = len(run)
    chosen = [False] * len(words)
    for s in starts:
        chosen[s:s + n] = [True] * n

    def plain(i: int) -> bool:
        w = words[i]
        return (w in _FILLER_WORDS or w in harmless) and w not in others

    rest = [i for i in range(len(words)) if not chosen[i] and not plain(i)]
    if not rest:
        return True
    first = starts[0]
    lo, hi = min(rest), max(rest)
    if hi >= first:
        return False                  # a word she needs covered after the choice
    for value in context if isinstance(context, (list, tuple, set, frozenset)) else ():
        if not isinstance(value, str):
            continue
        folded = _choice_form(value)
        cw = folded.split()
        if not cw or _NEGATION.search(folded) or any(_turns(w) or w in others for w in cw):
            continue
        if len(cw) > 1 and not her_marks:
            continue                  # the model's words carry no capital letters of hers
        for a in _starts(words, cw):
            b = a + len(cw)
            if a > lo or b <= hi or b > first:
                continue              # it must hold every uncovered word, before the choice
            # the model's words for the choice may start with link words of
            # hers ("has paid" out of "Sam has paid"): they join, not choose
            j = first
            while j < first + n - 1 and words[j] in _LINK_WORDS:
                j += 1
            if any(toks[i][2] for i in range(a + 1, j + 1)):
                continue              # a written mark between the value and the choice
            if len(cw) > 1 and not all(_name_evidence(toks, i) for i in range(a, b)):
                continue
            link = words[b:j]
            if any(w not in _LINK_WORDS and w not in harmless or w in others for w in link):
                continue
            if not link and not (her_marks and _name_evidence(toks, b - 1)):
                continue              # right beside the choice: "Unlikely paid"
            return True
    return False


def resolve_choice(choices: Any, said: Any, context: Any = ()) -> Optional[str]:
    """The one app choice the owner's words name, or None (none, or more
    than one could be meant).

    In order: the exact text; the same words up to case and marks; else the
    one choice that her words hold whole, or that holds her words whole,
    where every word on either side beyond the shared ones is a harmless
    little word (:data:`_FILLER_WORDS`), or on her side a whole value of hers
    in the same call (``context``) that does not stand right beside the
    choice (:func:`_names_only`). A word that is not known to be harmless
    makes the words name nothing: "hasn't paid", "almost paid" and "needs to
    be paid" never pick "Paid", and "attend" never picks "Unable to attend".
    As a second guard a "no" on one side only (:data:`_NEGATION`) always
    names nothing, and a question (a question mark of any script, or "is Leo
    attending") names nothing. Words that are not the exact choice text and
    hold any mark that could turn a meaning around (:func:`_odd_marks`: an
    emoji, "~", "/s", a strike-through tag, a quote) name nothing."""
    if not isinstance(choices, list) or not isinstance(said, str):
        return None
    if said in choices:
        return said
    if _asks(said) or _odd_marks(said):
        return None
    want = _choice_form(said)
    if not want:
        return None
    forms = [(c, _choice_form(c)) for c in choices if isinstance(c, str) and _choice_form(c)]
    same = [c for c, f in forms if f == want]
    if same:
        return same[0] if len(same) == 1 else None
    want_words = want.split()
    toks = _tokens(said)
    if toks is None:
        return None
    clean: list[tuple[str, str]] = []
    for c, f in forms:
        f_words = f.split()
        # a harmless word that is part of another choice is not harmless
        # here ("yes no" never picks "No")
        others = frozenset(w for c2, f2 in forms if c2 != c for w in f2.split())
        if _starts(want_words, f_words):
            ok = _names_only(toks, f_words, context, others=others)
        elif len(want) >= 3 and _starts(f_words, want_words):
            extra = _without_run(f_words, want_words) or []
            # the choice's own extra words: little words only, never hers
            ok = all(w in _FILLER_WORDS and w not in others for w in extra)
        else:
            ok = False
        if not ok:
            continue
        if bool(_NEGATION.search(want)) != bool(_NEGATION.search(f)):
            continue
        clean.append((c, f))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0][0]
    longest = max(len(f.split()) for _c, f in clean)
    top = [c for c, f in clean if len(f.split()) == longest]
    return top[0] if len(top) == 1 else None


#: Her words for a ticked box beyond the box's own field words. Kept short on
#: purpose: "done", "on" or "set" could be about something else.
_TICK_YES = frozenset({"yes", "tick", "ticked"})
#: Her words for a cleared box. "no" and "not" are read by :data:`_NEGATION`.
_TICK_NO = frozenset({"untick", "unticked", "uncheck", "unchecked", "off", "unset"})
_TICK_POLARITY = _TICK_YES | _TICK_NO | {"no", "check", "checked", "clear", "cleared"}


def tick_value(words: Any, field: Any = "") -> Optional[str]:
    """``"yes"`` or ``"no"`` for the owner's words about a tick box, or None
    when they do not say plainly which (fail closed).

    ``field`` is the box's own words from the app (:func:`tick_words`), None
    when the field's name could itself turn a meaning around (then nothing
    sets it). Every word must be one of the field's words ("paid"), a
    :data:`_FILLER_WORDS` word, a word that ticks ("tick") or one "no"
    ("not", "hasn't", "untick", "off", or "unpaid" for the field "paid").
    One "no" is ``"no"``; none, with the field named or a tick word said, is
    ``"yes"``. Two "no"s, a tick word beside a "no", any other word
    ("almost", "yet", "for"), a question or any odd mark ("~~paid~~",
    "paid /s") is None. A bare ``"yes"`` or ``"no"`` stays itself.

    Final fix: her words must name EVERY word of the field ("paid" never
    sets "paid zero", "attending" never sets "attending withdrawn"), as the
    word she left out can turn the meaning around. For a field of more than
    one word a "no" of hers ("not", "unpaid") could be about one word or the
    whole field, so it is None too; "untick", "uncheck", "off" and "unset"
    are about the box and still clear it (fail closed)."""
    if field is None:
        return None
    if isinstance(words, str) and words in OWN_CHOICES:
        return words
    if not isinstance(words, str) or len(words) > CHOICE_WORDS_MAX or _CTRL.search(words):
        return None
    if _asks(words) or _odd_marks(words):
        return None
    folded = _choice_form(words)
    if not folded:
        return None
    own = frozenset(_choice_form(str(field or "")).split()) - _TICK_POLARITY - _FILLER_WORDS
    negated: set[str] = set()
    negs = 0
    word_no = False                   # a "no" that may be about one word only
    for m in _NEGATION.finditer(folded):
        negs += 1
        word_no = True
        negated.update(m.group(0).split())
    said_yes = False
    named: set[str] = set()
    for w in folded.split():
        if w in negated:
            continue
        if w in _TICK_NO:
            negs += 1
        elif w.startswith("un") and w[2:] in own:
            negs += 1
            word_no = True
            named.add(w[2:])
        elif w in own:
            named.add(w)
        elif w in _TICK_YES:
            said_yes = True
        elif w not in _FILLER_WORDS or _turns(w):
            return None
    if negs > 1 or (negs == 1 and said_yes):
        return None
    if named and named != own:
        return None                   # part of the field only: "paid" for "paid zero"
    if word_no and len(own) > 1:
        return None                   # "not" or "un-" on a field of several words
    if negs == 1:
        return "no"
    return "yes" if (named or said_yes) else None


def is_choice_words(spec: Any) -> bool:
    """True for an argument that is the owner's words for one of an app's
    choices (:func:`schema_of` gives it no list, a short limit and the hint)."""
    return isinstance(spec, dict) and spec.get("type") == "string" and "enum" not in spec \
        and spec.get("maxLength") == CHOICE_WORDS_MAX \
        and str(spec.get("description") or "").endswith(_CHOICE_HINT)


def tick_box_field(spec: Any) -> Optional[str]:
    """The words for a tick box argument as :func:`schema_of` shows it ("the
    paid box", or "the box" when the app gave no field words), or None when
    ``spec`` is not a tick box."""
    if not is_choice_words(spec):
        return None
    text = str(spec.get("description") or "")[:-len(_CHOICE_HINT)]
    if not text.startswith("the ") or not text.endswith("tick box, "):
        return None
    field = text[len("the "):-len("tick box, ")].strip()
    return f"the {field} box" if field else "the box"


def label_words(spec: Any) -> frozenset:
    """The words of an app choice's own label ("payment" in "Set Sam's
    payment to paid"), from the app's recipe, never the model; never a "no"
    or a :data:`_TURNERS` word."""
    text = str(spec.get("description") or "") if isinstance(spec, dict) else ""
    text = text[:-len(_CHOICE_HINT)] if text.endswith(_CHOICE_HINT) else ""
    return frozenset(w for w in _choice_form(text).split()
                     if not _NEGATION.search(w) and not _turns(w) and w not in _TICK_POLARITY)


def choice_said(message: Any, value: Any, context: Any = (), harmless: Any = frozenset()) -> bool:
    """True when the model's words for an app choice stand whole, in her
    order, in the owner's message, and every other word of her message is a
    little word that never changes a meaning, a word of the choice's own
    label (``harmless``) or a whole value of hers in the same call
    (``context``, the guest's name) as :func:`_names_only` allows.

    So the model cannot keep "paid" out of "Sam has not paid", "Sam has
    almost paid", "Sam still needs to be paid" or "Sam paid but got
    refunded", nor "attending" out of "Is Leo attending" (any question mark,
    or a question's word order, names nothing). A value that fails is dropped
    and chat asks her for it; she can always answer with plain words ("Mark
    Sam as paid")."""
    if not isinstance(message, str) or not isinstance(value, str):
        return False
    if _asks(message):
        return False
    # any mark that could turn a meaning around, anywhere in her message
    # (:func:`_odd_marks`), names nothing. Marks inside the model's words
    # are left to the run: there they only pass as the app's exact choice
    # text ("50/50"), checked by :func:`resolve_choice`.
    bare = message
    if _odd_marks(value):
        hit = re.search(re.escape(value), message, re.I)
        if hit is None:
            return False
        bare = message[:hit.start()] + " " + message[hit.end():]
    if _odd_marks(bare):
        return False
    said, v = _choice_form(message), _choice_form(value)
    if not v or f" {v} " not in f" {said} ":
        return False
    if _NEGATION.search(said) and not _NEGATION.search(v):
        return False
    toks = _tokens(message)
    if toks is None:
        return False
    ok_words = frozenset(harmless) if isinstance(harmless, (set, frozenset, list, tuple)) \
        else frozenset()
    return _names_only(toks, v.split(), context, harmless=ok_words, her_marks=True)


#: Everyday words that mean exactly one app choice word and never its
#: opposite ("He's coming" is "Attending"). A group only ever maps to a
#: choice whose whole text is one word of the group, and only when exactly
#: one choice is. Kept short on purpose; a word that is not here is matched
#: as written or not at all.
_SAME_MEANING = (frozenset({"attending", "coming"}),
                 frozenset({"done", "finished", "complete", "completed"}))
#: Words that join her to a choice in a sentence of its own ("he wants the
#: chicken", "she'd like fish", "they prefer vegan"). Never a word that can
#: turn a meaning around.
_CHOICE_VERBS = frozenset({"want", "wants", "wanted", "like", "likes", "d", "ll", "prefer",
                           "prefers", "preferred", "chose", "chosen", "picked", "picks",
                           "having", "take", "takes", "taking", "gets", "get"})
#: Words of an ask to add or change a row ("Add Leo Park to the guest list").
_ASK_WORDS = frozenset({"add", "put", "include", "list", "lists", "onto", "on", "into", "to",
                        "new", "entry", "row", "also", "too", "can", "update", "edit"})
#: Her message, at most this long, for a choice told in a sentence of its own.
_CLAUSE_TEXT_MAX = 400


def _same_meaning(word: str) -> frozenset:
    for group in _SAME_MEANING:
        if word in group:
            return group
    return frozenset({word})


def _clauses(toks: list[tuple[str, bool, bool]]) -> list[list[int]]:
    """Token positions of her message, cut at every written mark (a full
    stop, a comma, a new line) and at the word "and" (which is left out)."""
    out: list[list[int]] = [[]]
    for i, (w, _cap, mark) in enumerate(toks):
        if mark and out[-1]:
            out.append([])
        if w == "and":
            if out[-1]:
                out.append([])
            continue
        out[-1].append(i)
    return [c for c in out if c]


def _choice_in_own_words(choices: list, candidate: str, context: list) -> Optional[str]:
    got = resolve_choice(choices, candidate, context)
    if got is not None:
        return got
    group = _same_meaning(_choice_form(candidate))
    if len(group) < 2:
        return None
    hits = [c for c in choices if isinstance(c, str) and _choice_form(c) in group]
    return hits[0] if len(hits) == 1 else None


#: An app choice that means "is coming": one of these words, and no "no".
_COMING_WORDS = frozenset({"attending", "attend", "attends", "coming", "going", "accepted",
                           "confirmed"})
#: An app choice that means "is not coming": every word one of these ...
_NOT_COMING_WORDS = frozenset({"declined", "decline", "declines", "regrets", "regret", "absent"})
#: ... or one "no" and one of these ("Not attending", "Can't make it", "Unable to attend").
_COME_WORDS = frozenset({"attend", "attending", "come", "coming", "make", "going", "there",
                         "join", "joining"})
#: Her words, right after one plain "no", that mean coming ("can't make it",
#: "isn't coming", "won't be there"). Nothing may follow them but little words.
_COME_PHRASES = (("make", "it"), ("be", "there"), ("be", "coming"), ("be", "attending"),
                 ("be", "joining", "us"), ("join", "us"), ("joining", "us"), ("come",),
                 ("coming",), ("attend",), ("attending",), ("go",), ("going",), ("join",),
                 ("joining",))
#: The plain ways she says "no" before those words, as folded ("can't" is
#: "can t"). Never a maybe ("would not", "might not", "should not"), never
#: "never" or a bare "no".
_PLAIN_NOS = (("can", "t"), ("cannot",), ("cant",), ("can", "not"), ("won", "t"), ("wont",),
              ("will", "not"), ("ll", "not"), ("isn", "t"), ("isnt",), ("is", "not"),
              ("s", "not"), ("aren", "t"), ("arent",), ("are", "not"), ("re", "not"),
              ("am", "not"), ("m", "not"), ("doesn", "t"), ("doesnt",), ("does", "not"),
              ("don", "t"), ("dont",), ("do", "not"), ("didn", "t"), ("didnt",),
              ("did", "not"), ("couldn", "t"), ("couldnt",))
#: Words that may open a part of her message and change no meaning
#: ("Actually Leo wants the fish instead", "Sadly she can't make it").
_LEAD_WORDS = frozenset({"actually", "oh", "sadly", "unfortunately", "also", "so", "ok",
                         "okay", "well", "and"})
#: Words that may close the part holding a choice: little words, and
#: "instead" as its very last word (a change of mind: "wants that one instead").
_CLOSING_WORDS = frozenset({"please", "too", "also", "now"})


def _coming_choices(forms: list[tuple[str, str]]) -> tuple[Optional[str], Optional[str]]:
    """The app's one choice that means coming and its one choice that means
    not coming, or ``(None, None)`` unless the app has exactly one of each
    (an attendance field: "Declined" alone could be a card payment's)."""
    yes = [c for c, f in forms if set(f.split()) & _COMING_WORDS and not _NEGATION.search(f)]
    no = []
    for c, f in forms:
        w = set(f.split()) - _FILLER_WORDS
        if w and w <= _NOT_COMING_WORDS:
            no.append(c)
        elif len(list(_NEGATION.finditer(f))) == 1 and w & _COME_WORDS:
            no.append(c)
    if len(yes) == 1 and len(no) == 1:
        return yes[0], no[0]
    return None, None


def _run_at(words: list[str], i: int, runs: tuple) -> int:
    """The length of the longest of ``runs`` that stands at ``words[i]``, or 0."""
    best = 0
    for run in runs:
        if tuple(words[i:i + len(run)]) == run and len(run) > best:
            best = len(run)
    return best


def _part_choice(choices: list, pw: list[str], named: list[bool], harmless: frozenset,
                 label: frozenset, need_evidence: bool, stated: bool = False) -> Optional[str]:
    """The one app choice that one part of her message names, or None.

    ``pw`` are the part's folded words, ``named`` says which of them are a
    name she wrote (they are never read as a choice, and may only stand
    before it), ``harmless`` are the app's and the field's own words and
    ``label`` the field's own words. Fail closed:

    * a part with a "no" names a choice only as "can't make it", "isn't
      coming", "won't be there" (one plain "no", then words of coming, then
      little words), and then only the app's one "not coming" choice of a
      field that also has one "coming" choice;
    * a part with no "no" names the one choice whose words stand whole in it
      (or "coming" for the app's "Attending"), with only little words, verbs
      of wanting, ask words, the field's words or her names before it and
      only little words or the field's words after it ("he's on the groom's
      side"), "instead" allowed as the very last word;
    * any word that could turn a meaning around, a question's word order, a
      choice named twice or two choices: nothing;
    * ``need_evidence`` (a field the model did not point at): the part must
      also hold a verb of wanting, the field's own words, or be about coming."""
    forms = [(c, _choice_form(c)) for c in choices if isinstance(c, str) and _choice_form(c)]
    if not forms or not pw:
        return None
    plain_idx = [i for i in range(len(pw)) if not named[i]]
    plain_text = " ".join(pw[i] for i in plain_idx)
    negs = list(_NEGATION.finditer(plain_text))
    if negs:
        yes, no = _coming_choices(forms)
        if no is None or len(negs) != 1:
            return None
        for i in range(len(pw)):
            if named[i]:
                continue
            n = _run_at(pw, i, _PLAIN_NOS)
            if not n:
                continue
            m = _run_at(pw, i + n, _COME_PHRASES)
            if not m:
                return None
            before_ok = all(named[j] or (pw[j] in _FILLER_WORDS or pw[j] in _LEAD_WORDS
                                         or pw[j] in harmless) and not _turns(pw[j])
                            for j in range(i))
            after_ok = all(not named[j] and (pw[j] in _FILLER_WORDS or pw[j] in harmless)
                           and not _turns(pw[j]) for j in range(i + n + m, len(pw)))
            return no if before_ok and after_ok else None
        return None
    last = len(pw) - 1
    for i in plain_idx:
        if _turns(pw[i]) and not (pw[i] == "instead" and i == last):
            return None
    hits: list[tuple[str, int, int]] = []
    for c, f in forms:
        run = f.split()
        alts = [run] + ([[w] for w in _same_meaning(run[0]) if w != run[0]] if len(run) == 1
                        else [])
        for r in alts:
            for s in _starts(pw, r):
                if not any(named[s:s + len(r)]):
                    hits.append((c, s, len(r)))
    # a choice inside a longer one she named ("table" in "head table") is not a second one
    hits = [h for h in hits if not any(o is not h and o[1] <= h[1] and h[1] + h[2] <= o[1] + o[2]
                                       and o[2] > h[2] for o in hits)]
    if len({c for c, _s, _n in hits}) != 1 or len(hits) != 1:
        return None
    choice, s, n = hits[0]
    if not stated and _asking_shape(pw, [s]):
        return None
    before = range(s)
    if any(not (named[j] or pw[j] in _FILLER_WORDS or pw[j] in _CHOICE_VERBS
                or pw[j] in _ASK_WORDS or pw[j] in harmless or pw[j] in _LEAD_WORDS)
           for j in before):
        return None
    for j in range(s + n, len(pw)):
        if named[j]:
            return None                   # nothing of hers after the choice
        w = pw[j]
        if w in _FILLER_WORDS or w in label or w in _CLOSING_WORDS:
            continue
        if w == "instead" and j == last:
            continue
        return None
    if need_evidence:
        yes, _no = _coming_choices(forms)
        said = any(pw[j] in _CHOICE_VERBS for j in before) or any(
            pw[j] in label for j in range(len(pw)) if not named[j]) \
            or (yes is not None and choice in (yes, _no))
        if not said:
            return None
    return choice


def _her_names(toks: list[tuple[str, bool, bool]], values: Any, choice_words: frozenset,
               broad: bool = True) -> list[bool]:
    """Which of her words are a name she wrote: a whole value of hers given
    with the call, written with capital letters where no sentence starts, or
    (``broad``) any word so written that is no app choice's word, no little
    word and no word that could turn a meaning around."""
    words = [w for w, _c, _m in toks]
    named = [False] * len(toks)
    for v in values if isinstance(values, (list, tuple, set, frozenset)) else ():
        if not isinstance(v, str):
            continue
        run = _choice_form(v).split()
        if not run or any(_turns(w) or w in choice_words or _NEGATION.search(w) for w in run):
            continue
        for a in _starts(words, run):
            # her first word may be the name ("Leo wants the fish"): it is
            # written with a capital letter as any first word is
            if all(_name_evidence(toks, i) or (i == a and toks[i][1] and (i == 0 or toks[i][2]))
                   for i in range(a, a + len(run))):
                named[a:a + len(run)] = [True] * len(run)
    for i, w in enumerate(words):
        if broad and not named[i] and _name_evidence(toks, i) and w not in choice_words \
                and w not in _FILLER_WORDS and w not in _ASK_WORDS and not _turns(w) \
                and not _NEGATION.search(w) and not w.isdigit():
            named[i] = True
    return named


def choice_in_clause(choices: Any, clause: Any, harmless: Any = frozenset(), names: Any = (),
                     *, label: Any = frozenset(), need_evidence: bool = False,
                     whole: Any = None) -> Optional[str]:
    """The one app choice (the app's own text) that one clause of the
    owner's message names, or None. See :func:`_part_choice` for the rules:
    "She can't make it" is the app's one "not coming" choice, "he wants the
    fish instead" is "Fish", "he's on the groom's side" is "Groom"; a "no"
    anywhere else, a word that could turn a meaning around, a question or an
    odd mark names nothing. ``names`` are her values in the same call (the
    guest's name); ``harmless`` the app's and the field's words; ``label`` the
    field's own words. For chat's edits and adds.

    ``whole`` is her whole message when the clause had her row's name taken
    out ("Leo Park is on the groom's side" read as "is on the groom's side"):
    a whole message that asks names nothing ("Is Leo Park on the groom's
    side"), and the clause's first word is read as following the name only
    where her message shows one of ``names`` right before it."""
    if not isinstance(choices, list) or not isinstance(clause, str) \
            or not clause.strip() or len(clause) > _CLAUSE_TEXT_MAX:
        return None
    if _asks(clause) or _odd_marks(clause):
        return None
    toks = _tokens(clause)
    if not toks:
        return None
    stated = False
    if whole is not None:
        if not isinstance(whole, str) or len(whole) > _CLAUSE_TEXT_MAX \
                or _asks(whole) or _odd_marks(whole):
            return None
        wt = _tokens(whole)
        if not wt or _asking_shape([w for w, _c, _m in wt], []):
            return None
        first = re.escape(clause.strip().split()[0]) if clause.strip() else ""
        stated = bool(first) and any(
            isinstance(n, str) and n.strip() and re.search(
                r"(?i)(?<!\w)" + re.escape(n.strip()) + r"(?:['’]s)?\s+" + first + r"(?!\w)",
                whole)
            for n in (names if isinstance(names, (list, tuple, set, frozenset)) else ()))
    choice_words = frozenset(w for c in choices if isinstance(c, str)
                             for w in _choice_form(c).split())
    named = _her_names(toks, names, choice_words)
    ok = frozenset(w for w in _choice_form(" ".join(
        str(x) for x in (harmless if isinstance(harmless, (set, frozenset, list, tuple)) else ())
    )).split() if not _turns(w) and not _NEGATION.search(w))
    lab = frozenset(w for w in _choice_form(" ".join(
        str(x) for x in (label if isinstance(label, (set, frozenset, list, tuple)) else ())
    )).split() if not _turns(w) and not _NEGATION.search(w))
    return _part_choice(list(choices), [w for w, _c, _m in toks], named, ok | lab, lab,
                        need_evidence, stated)


def ground_choices(action: Any, text: Any, raw_args: Any, args: dict,
                   dropped: list) -> tuple[dict, list]:
    """Set an app choice her message names in a part of its own ("Add Leo
    Park to the guest list. He's coming and he wants the chicken.", "Add
    Maya Lee. She can't make it.", "Actually Leo wants the fish instead, and
    he's on the groom's side.").

    Fail closed, for the whole message at once:

    * no question and no odd mark anywhere in her message;
    * the message is cut into parts at written marks and at "and"; each
      part names at most one field's choice (:func:`_part_choice`), and each
      field's choice stands in exactly one part;
    * every part that names nothing holds only little words, ask words, the
      app's and the action's own words, and names she gave; so a "no" or a
      word that could turn a meaning around anywhere else ("in his dreams",
      "not really", "kidding") sets nothing at all;
    * a choice the model pointed at that means another of the app's choices
      than her words do sets nothing (she is asked);
    * a field the model did not point at is set only in an add or a change,
      and only when her part holds a verb of wanting, the field's own words
      or is about coming.

    The choice set is the app's own choice text, so :func:`check_args` reads
    it as written. Only for web app actions; any other action comes back
    unchanged."""
    if not isinstance(action, dict) or action.get("kind") != KIND \
            or not isinstance(raw_args, dict) or not isinstance(args, dict):
        return args, dropped
    found = _find(action.get("id"))
    if found is None:
        return args, dropped
    _app_id, recipe, app_name = found
    rargs = recipe.get("args") if isinstance(recipe.get("args"), dict) else {}
    pointed = {k for k in dropped
               if isinstance(raw_args.get(k), str) and 0 < len(raw_args[k]) <= CHOICE_WORDS_MAX}
    adds = action.get("effect") == "changes"
    keys = [k for k, spec in rargs.items()
            if isinstance(spec, dict) and spec.get("enum") and not _own_choices(spec["enum"])
            and k not in args and (k in pointed or adds)]
    if not keys or not isinstance(text, str) or len(text) > _CLAUSE_TEXT_MAX:
        return args, dropped
    if _asks(text) or _odd_marks(text):
        return args, dropped
    toks = _tokens(text)
    if not toks:
        return args, dropped
    words = [w for w, _c, _m in toks]

    def label(k: str) -> frozenset:
        spec = rargs.get(k) if isinstance(rargs.get(k), dict) else {}
        return frozenset(w for w in _choice_form(f"{k.replace('_', ' ')} {spec.get('says') or ''}")
                         .split() if not _turns(w) and not _NEGATION.search(w))

    own_words = frozenset(w for w in _choice_form(
        f"{recipe.get('says') or ''} {action.get('says') or ''} {app_name}").split()
        if not _turns(w) and not _NEGATION.search(w))
    ask = set(_FILLER_WORDS) | _ASK_WORDS | own_words
    for k in rargs:
        ask |= label(k)
    choice_words = frozenset(w for k in keys for c in rargs[k]["enum"] if isinstance(c, str)
                             for w in _choice_form(c).split())
    values = [v for k, v in args.items() if isinstance(v, str)]
    # names she gave with the call (strict: these alone may fill a part that names nothing)
    given = _her_names(toks, values, choice_words, broad=False)
    # any name she wrote (a part's subject: "Actually Leo wants the fish")
    named = _her_names(toks, values, choice_words)
    parts = _clauses(toks)
    owner: dict[int, tuple[str, str]] = {}           # part -> (key, app choice)
    clash: set[int] = set()
    for k in keys:
        enum = [c for c in rargs[k]["enum"] if isinstance(c, str)]
        lab = label(k)
        hits = []
        for n, part in enumerate(parts):
            got = _part_choice(enum, [words[i] for i in part], [named[i] for i in part],
                               own_words | lab, lab, need_evidence=k not in pointed)
            if got is not None:
                hits.append((n, got))
        if len(hits) != 1:
            continue                                  # not said, or said twice
        n, choice = hits[0]
        if k in pointed:
            meant = _choice_in_own_words(enum, raw_args[k], [])
            if meant is not None and meant != choice:
                continue                              # the model read another choice: ask
        if n in owner:
            clash.add(n)
            continue
        owner[n] = (k, choice)
    for n in clash:
        owner.pop(n, None)                            # one part, two fields: ask
    if not owner:
        return args, dropped
    for n, part in enumerate(parts):
        if n in owner:
            continue
        if any(words[i] not in ask and not given[i] and words[i] not in _LEAD_WORDS
               for i in part):
            return args, dropped                      # a part that is not an ask
        if any(_turns(words[i]) or _NEGATION.search(words[i]) for i in part):
            return args, dropped
    kept = dict(args)
    for k, choice in owner.values():
        kept[k] = choice
    return kept, [k for k in dropped if k not in kept]


def _other_values(args: dict, key: Any) -> list[str]:
    return [v for k, v in args.items() if k != key and isinstance(v, str)]


def _no_such_choice(recipe_args: dict, args: Any, ticks: Optional[dict] = None) -> bool:
    """True when the owner's words for an app choice match none (or more
    than one) of the app's choices, or do not say plainly whether a box is
    ticked."""
    if not isinstance(args, dict):
        return False
    ticks = ticks if isinstance(ticks, dict) else {}
    for k, v in args.items():
        spec = (recipe_args or {}).get(k)
        if isinstance(spec, dict) and _own_choices(spec.get("enum")) and isinstance(v, str) \
                and v and tick_value(v, ticks.get(k, "")) is None:
            return True
        if isinstance(spec, dict) and spec.get("enum") and not _own_choices(spec["enum"]) \
                and isinstance(v, str) and v \
                and resolve_choice(list(spec["enum"]), v, _other_values(args, k)) is None:
            return True
    return False


def _no_plain_tick(recipe_args: dict, args: Any, ticks: Optional[dict] = None) -> bool:
    """True when a tick box's words are missing or do not say plainly
    whether to tick it or clear it."""
    if not isinstance(args, dict):
        return False
    ticks = ticks if isinstance(ticks, dict) else {}
    for k, spec in (recipe_args or {}).items():
        if isinstance(spec, dict) and _own_choices(spec.get("enum")):
            v = args.get(k)
            if not isinstance(v, str) or tick_value(v, ticks.get(k, "")) is None:
                return True
    return False


def check_args(recipe_args: dict, args: Any, ticks: Optional[dict] = None) -> Optional[dict]:
    """The arguments, checked against the compiled types, or None when they do not fit.

    An app choice arrives as the owner's own words and leaves as the one app
    choice they name (:func:`resolve_choice`, with her other values in the
    same call as the words it may pass over); words that name none, or more
    than one, or that hold any word that is not known to be harmless, do not
    fit. A tick box arrives as her words too and leaves as yes or no
    (:func:`tick_value`, with the box's field words from ``ticks``); words
    that do not say plainly which, or no words at all, do not fit. This is
    the last check before anything runs, whatever path the words came by."""
    if not isinstance(args, dict):
        return None
    ticks = ticks if isinstance(ticks, dict) else {}
    clean: dict[str, Any] = {}
    for k, v in args.items():
        spec = (recipe_args or {}).get(k)
        if not isinstance(spec, dict):
            return None
        if v is None or v == "":
            continue
        if spec.get("type") == "number":
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return None
            if not (spec.get("min", -1e12) <= v <= spec.get("max", 1e12)):
                return None
        else:
            if not isinstance(v, str) or _CTRL.search(v):
                return None
            if spec.get("enum"):
                if _own_choices(spec["enum"]):
                    v = tick_value(v, ticks.get(k, ""))
                    if v is None or v not in spec["enum"]:
                        return None
                else:
                    if len(v) > CHOICE_WORDS_MAX:
                        return None
                    v = resolve_choice(list(spec["enum"]), v, _other_values(args, k))
                    if v is None:
                        return None
            if len(v) > int(spec.get("maxLength") or 200):
                return None
            if spec.get("pattern") and not re.fullmatch(str(spec["pattern"]), v):
                return None
        clean[k] = v
    for k, spec in (recipe_args or {}).items():
        if isinstance(spec, dict) and (spec.get("required") or _own_choices(spec.get("enum"))) \
                and k not in clean:
            return None
    return clean


def read_from_copy(app_id: str, recipe: dict) -> list:
    """Answer a stored-data read from the bot's copy, with no browser."""
    data = mirror.get(app_id)["data"]
    rows: list = []
    for s in recipe.get("steps") or []:
        ok, value = rc._loads(data.get(str(s.get("key"))))
        v = rc.pick(value, str(s.get("path") or "")) if ok else None
        if isinstance(v, list):
            fields = [str(f) for f in s.get("fields") or []]
            for x in v[:min(int(s.get("max") or MAX_ROWS), MAX_ROWS)]:
                if isinstance(x, dict):
                    rows.append({f: x.get(f) for f in fields} if fields else x)
        elif s.get("value") is True and ok and (isinstance(v, bool) or (
                isinstance(v, (int, float)) and abs(v) < 1e12)):
            # one stored number or yes/no (a counter): one row, named by the
            # recipe's own words for it
            fields = [str(f) for f in s.get("fields") or []][:1] or ["value"]
            rows.append({fields[0]: v})
    return rows[:MAX_ROWS]


#: A write counts as the app's last only after this long with no other.
QUIET_S = 1.0
#: How long to let a page that writes as it loads settle before the start point.
PRE_QUIET_S = 0.4
#: The longest the bot waits for the app to go quiet (each wait).
SETTLE_S = 4.0
_POLL_S = 0.1


def _quiet(app_id: str, quiet_s: float, max_s: float) -> None:
    """Wait until the bot's copy has had no new write for ``quiet_s`` seconds
    (at most ``max_s``). Time based, so a fast poll never shortens the window."""
    deadline = time.monotonic() + max_s
    last = mirror.version(app_id)
    since = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(_POLL_S)
        v = mirror.version(app_id)
        now = time.monotonic()
        if v != last:
            last, since = v, now
        elif now - since >= quiet_s:
            return


def _stop_page(driver: Any, app_id: str) -> bool:
    """Close the bot's own page of the app, so nothing this run started
    (a timer, a late save) can write after the effect is proven. True only
    when the runner says the page is closed."""
    try:
        return driver.close(app_id) is True
    except Exception:  # noqa: BLE001 - not closed: treated as a broken run
        return False


def _put_back(app_id: str, v_before: int, held: dict) -> bool:
    """Put back the copy held since the run started. True only when the bot's
    copy is that data again (checked, never assumed)."""
    for _ in range(3):
        got = mirror.put_back(app_id, v_before, data=held)
        if not got.get("ok"):
            return False
        # a write that was still on its way when the copy was put back
        time.sleep(_POLL_S * 3)
        if mirror.get(app_id)["data"] == held:
            return True
    return False


def _fail(effect: str, line: str) -> dict:
    return {"ok": False, "effect": effect, "evidence": {}, "line": line, "undo_token": None}


def _overstepped(effect: str, proven: str, res: dict) -> bool:
    return rc.stricter(effect, proven) != effect or (
        effect == "reads" and (proven != "reads" or bool(res.get("clicked"))
                               or any(str((d or {}).get("answered")) == "yes"
                                      for d in res.get("dialogs") or [])))


def run(action: dict, args: dict, ctx: dict) -> dict:
    """Run one action. ``{"ok","effect","data"|"evidence","line","undo_token"|None}``."""
    ctx = ctx if isinstance(ctx, dict) else {}
    found = _find((action or {}).get("id") if isinstance(action, dict) else None)
    if found is None:
        return _fail("reads", wording.ACTION_GONE)
    app_id, recipe, app_name = found
    effect = (recipe.get("checked") or {}).get("effect") or "removes"
    ticks = tick_words(recipe)
    clean = check_args(recipe.get("args") or {}, args, ticks)
    if clean is None:
        if _no_plain_tick(recipe.get("args") or {}, args, ticks):
            return _fail(effect, wording.NO_PLAIN_TICK)
        return _fail(effect, wording.NO_SUCH_CHOICE
                     if _no_such_choice(recipe.get("args") or {}, args, ticks) else wording.BAD_ARGS)
    if effect == "removes" and ctx.get("yes") is not True:
        return _fail(effect, wording.REMOVE_NEEDS_YES)
    from puffpai.webapps import host, runner_host
    ok, line = runner_host.availability()
    if not ok:
        return _fail(effect, line)
    if host.runner_secret() is None:
        return _fail(effect, wording.RUNNER_BUSY)
    body = {k: v for k, v in recipe.items() if k not in ("provenance", "checked")}
    if effect == "reads" and body.get("steps") and all(
            isinstance(s, dict) and s.get("do") == "read_storage" for s in body["steps"]):
        rows = read_from_copy(app_id, body)
        return {"ok": True, "effect": "reads", "data": rows,
                "line": wording.read_line(app_name, len(rows)), "undo_token": None}
    with _lock(app_id):
        # Only the bot's hidden browser may write while this runs: an edit the
        # owner makes meanwhile waits (423) instead of being counted as the
        # action's own and put back or undone with it.
        mirror.begin_bot_run(app_id)
        try:
            return _run_held(app_id, recipe, app_name, effect, body, clean)
        finally:
            mirror.end_bot_run(app_id)


def _run_held(app_id: str, recipe: dict, app_name: str, effect: str, body: dict,
              clean: dict) -> dict:
    """One run while the app's copy is held for the bot (see :func:`run`)."""
    from puffpai.webapps import host, runner_host
    driver = None
    start: Optional[dict] = None
    broke = False
    res: dict = {}
    before: dict = {}
    after: dict = {}
    try:
        driver = runner_host.RunnerDriver(runner_host.ensure(
            host.installed_ids() + list(host.check_copies())))
        driver.open(app_id)
        driver.reload(app_id)          # start from the bot's copy, never a stale page
        _quiet(app_id, PRE_QUIET_S, SETTLE_S)
        # The start point, held in memory for the whole run: a put-back
        # never depends on a kept version file.
        start = mirror.get(app_id)
        # Two records the owner's words both match (two guests called
        # "Guest"): the bot cannot know which one she meant, so it touches
        # neither (the runner refuses the same on the page itself).
        # None that match (nothing called that): nothing is clicked either,
        # so an app that removes some other row is never even started.
        if rc.targets_a_row(body):
            n = rc.matching_records(body, clean, start["data"])
            if n != 1:
                _stop_page(driver, app_id)
                return _fail(effect, wording.MORE_THAN_ONE if n > 1 else wording.NO_MATCH)
        before = driver.storage(app_id)["localStorage"]
        res = driver.run(app_id, body, clean)
        after = driver.storage(app_id)["localStorage"]
    except (runner_host.RunnerError, runner_host.DriverError, OSError, ValueError):
        # a runner that died mid-call (a broken pipe included) is a broken
        # run: whatever it wrote is put back below
        broke = True
    if start is None:
        if driver is not None:
            _stop_page(driver, app_id)
        return _fail(effect, wording.RUNNER_BUSY)
    v_before, held = start["v"], start["data"]
    # Let the app's own later writes land, close the bot's page so this
    # run can write nothing more, let a write already on its way land,
    # then prove the effect again on the bot's copy itself (the data the
    # owner keeps).
    _quiet(app_id, QUIET_S, SETTLE_S)
    if not _stop_page(driver, app_id):
        broke = True               # cannot prove a page that may still write
    _quiet(app_id, _POLL_S * 3, SETTLE_S)
    final = mirror.get(app_id)
    clicked = res.get("clicked") or []
    dialogs = res.get("dialogs") or []
    proven_copy, _r1 = rc.prove_effect(body, clean, held, final["data"],
                                       clicked=clicked, dialogs=dialogs)
    if broke:
        # the run stopped partway: whatever it wrote is put back
        if final["data"] == held:
            return _fail(effect, wording.RUNNER_BUSY)
        return _fail(effect, wording.BROKE_PUT_BACK if _put_back(app_id, v_before, held)
                     else wording.PUT_BACK_FAILED)
    proven_page, _r2 = rc.prove_effect(body, clean, before, after,
                                       clicked=clicked, dialogs=dialogs)
    proven = rc.stricter(proven_copy, proven_page)
    overstepped = _overstepped(effect, proven, res) or (
        effect == "reads" and final["data"] != held)
    if overstepped:
        return _fail(effect, wording.PUT_BACK if _put_back(app_id, v_before, held)
                     else wording.PUT_BACK_FAILED)
    if not res.get("ok"):
        if final["data"] != held:
            # the run said it failed but still wrote: nothing half done is kept
            return _fail(effect, wording.NOT_FINISHED_PUT_BACK
                         if _put_back(app_id, v_before, held) else wording.PUT_BACK_FAILED)
        if effect == "reads":
            return _fail(effect, wording.RUNNER_BUSY)
        if "more than one item matches" in str(res.get("error") or ""):
            return _fail(effect, wording.MORE_THAN_ONE)
        return _fail(effect, wording.APP_DID_NOT_TAKE)
    if effect == "reads":
        rows = (res.get("results") or {}).get("rows")
        rows = rows[:MAX_ROWS] if isinstance(rows, list) else []
        return {"ok": True, "effect": "reads", "data": rows,
                "line": wording.read_line(app_name, len(rows)), "undo_token": None}
    # a change is claimed only from the bot's copy (evidence or silence)
    landed, ev = rc.evidence(body, clean, held, final["data"])
    if not landed:
        if final["data"] != held:
            # the data moved, but not into the change asked for: nothing of
            # it is kept, so "nothing was saved" is never said over a change
            put = _put_back(app_id, v_before, held)
            return {"ok": False, "effect": effect, "evidence": ev,
                    "line": wording.NOT_AS_ASKED_PUT_BACK if put else wording.PUT_BACK_FAILED,
                    "undo_token": None}
        return {"ok": False, "effect": effect, "evidence": ev,
                "line": wording.APP_DID_NOT_TAKE, "undo_token": None}
    # One row asked for, one row touched: the stored-data diff must show that
    # one record gone (a remove) or changed (a change) and nothing else, on
    # the bot's copy and on the page. An app that also took away or changed
    # another record is put back, whatever its effect class allows.
    if rc.targets_a_row(body) and final["data"] != held and (
            not rc.one_row_only(body, clean, held, final["data"])
            or (after != before and not rc.one_row_only(body, clean, before, after))):
        return {"ok": False, "effect": effect, "evidence": ev,
                "line": wording.PUT_BACK if _put_back(app_id, v_before, held)
                else wording.PUT_BACK_FAILED, "undo_token": None}
    token = None
    # A change the check proved undoable, or a remove the owner said yes to:
    # both are put back from the bot's own copy
    # of the data from before the run, and only while nothing wrote after it
    # (mirror.restore). A recipe never claims a remove is undoable; the undo
    # comes from this run's own copy.
    undo_ok = (effect == "changes" and (recipe.get("checked") or {}).get("undoable")) \
        or effect == "removes"
    if undo_ok and final["v"] > v_before and mirror.mark_undo(app_id, final["v"], held):
        token = {"kind": KIND, "app_id": app_id, "version": final["v"]}
    return {"ok": True, "effect": effect, "evidence": ev,
            "line": wording.did_line(app_name, recipe.get("says") or ""), "undo_token": token}


__all__ = ["list_actions", "run", "schema_of", "check_args", "resolve_choice", "KIND",
           "tick_box_field", "choice_in_clause", "ground_choices",
           "OWN_CHOICES", "tick_value", "tick_words"]
