"""A deterministic chat path for declarative apps, driven only by the manifest.

A scaffolded persona used to be understood and then dropped. "add tomatoes to
the plantings list" routed to the apps category, reached ``_try_direct_handle``,
found it was media-only, and fell through to the model - so on a box with no
Ollama the answer to a sentence about the owner's own app was a 503. The app was
installed, its store was there, the words were in the router. Nothing was
missing except the six lines that write the item down.

This module is those lines, and it is deliberately narrow:

* **Manifest-driven, never hand-coded.** The lists come from ``data.default``
  and the words from ``vocabulary``. No app id appears in a rule here, so an app
  scaffolded ten minutes ago is handled exactly as well as one shipped a year
  ago. Nothing is imported from the manifest and nothing in it is executed.
* **It never touches the five hand-written apps.** Alarmflow, calendar,
  freshtable, fitfam and trivia have their own parsers and their own Python
  methods, and a second parser guessing at them is how "add milk" ends up in two
  places. They are excluded by name (:data:`HANDWRITTEN`).
* **Evidence or silence.** Nothing is claimed until the document has been read
  back off disk and the item is in it. A write that did not land is reported as
  a write that did not land.
* **Conservative, and it asks.** One verb, one list, one item. A sentence that
  names no list when there are two is answered with the question ("Which list -
  plantings or harvest log?"), never with a guess.

The grammar, in full:

    add <item> to|on|in <list>        put <item> on <list>
    what is on <list> / show <list> / list <list> / read <list>
    remove|delete|take <item> from|off <list>
    how many ... on|in <list>
    I <verb>ed <item>                 (only when <verb> shares a stem with a
                                       list this build declares: "I planted
                                       tomatoes" reaches `plantings` and nothing
                                       else)

    which lists do you keep?           -> names every list, with its count
    what do you keep track of?            (deterministic; no model)

And three ask-backs, added after the review found the module returning None on
sentences that were plainly about the owner's own app:

    "basil for the plantings list"    -> what would you like me to do with it?
    "add basil"   (one persona)       -> which list?
    "delete the last one"             -> names the row and asks for the sentence
                                         that removes it, because there is no
                                         session here for a "yes" to live in.

An ask-back happens only on EVIDENCE that the sentence asks for a LIST
OPERATION: a list name, or the word list/log, or an unambiguous bare imperative
("add basil"). An app keyword is deliberately NOT evidence. A scaffolded
persona's vocabulary comes from the owner's goal, so its keywords are words like
"greenhouse", "garden" and "farm" - they are in every ordinary question a person
asks that bot, and matching on them answered "what is the greenhouse night
temperature and the code word?" with "Which list - plantings, harvest log or seed
inventory?". Ordinary chat is never hijacked, and a hand-written app's own words
always win.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

#: The apps that own their own phrasing in Python. This module is for the apps
#: nobody wrote code for, which is every app an owner adds.
HANDWRITTEN = frozenset({"alarmflow", "calendar", "freshtable", "fitfam",
                         "trivia"})

#: How long an item may be. A list entry is a line, not a document.
MAX_ITEM_CHARS = 120

#: How many entries a reply reads back before it stops and says how many more.
MAX_READ_BACK = 20

_ARTICLES_RE = re.compile(r"^(?:a|an|the|some|my|our)\s+")
_ADD_VERBS = r"add|put|log|jot\s+down|write\s+down|note\s+down|record"
_REMOVE_VERBS = r"remove|delete|take|cross\s+off|drop|scratch"
_SHOW_VERBS = (r"what(?:'s|s|\s+is|\s+are)?|show(?:\s+me)?|list|read(?:\s+me)?"
               r"|tell\s+me|give\s+me")

#: Trailing words that are when-it-happened, not part of the item. "I planted
#: tomatoes today" saves tomatoes, not "tomatoes today".
_TRAILING_TIME_RE = re.compile(
    r"\s+(?:today|yesterday|just\s+now|this\s+(?:morning|afternoon|evening)|"
    r"last\s+(?:night|week)|earlier|already)$")

#: The word that says a sentence is about a LIST, not merely about the subject an
#: app is named after. A scaffolded persona's vocabulary is derived from the
#: owner's goal, so its keywords are words like "greenhouse", "garden", "farm" -
#: which appear in every ordinary question a person asks a garden bot. Matching
#: on those alone is how "what is the greenhouse night temperature and the code
#: word?" was answered "Which list - plantings, harvest log or seed inventory?".
_LIST_WORD_RE = re.compile(r"\b(?:lists?|logs?)\b")

#: The inventory question: not "what is on a list" but "which lists are there".
#: Deterministic, answered from the manifests and the stores - a build that keeps
#: three lists knows their names and does not need a model to say them.
_WHICH_LISTS_RES = (
    re.compile(r"\b(?:what|which|how\s+many)\s+lists\b"),
    re.compile(r"\b(?:what|which)\s+lists?\s+(?:do|does|are|can)\b"),
    re.compile(r"\bwhat\s+do\s+you\s+(?:keep\s+track\s+of|track|keep)\b"),
    re.compile(r"\bdo\s+you\s+keep\s+(?:any\s+)?lists\b"),
)

#: "delete the last one" and its spellings. Deliberately narrow: this is the one
#: phrase that names a row without naming its text, so it may not be loose.
_LAST_ONE_RE = re.compile(
    r"\b(?:the\s+)?(?:last|latest|most\s+recent|newest)\s+"
    r"(?:one|item|thing|entry|row)\b")


def _lists_of(manifest: dict[str, Any]) -> list[str]:
    """The keys in this app's declared default shape whose value is a list."""
    block = manifest.get("data")
    default = block.get("default") if isinstance(block, dict) else None
    if not isinstance(default, dict):
        return []
    return [str(key) for key, value in default.items() if isinstance(value, list)]


def _variants(key: str) -> list[str]:
    """The spellings of one list key a person might type.

    ``harvest_log`` is said "harvest log"; a singular list is asked about in the
    plural and the other way round. A one-character equivalence, not a stemmer:
    deterministic, offline, and impossible to be surprised by.
    """
    label = str(key).replace("_", " ").strip()
    out = {label}
    if label.endswith("s"):
        out.add(label[:-1])
    else:
        out.add(label + "s")
    return sorted((v for v in out if len(v) >= 3), key=len, reverse=True)


def _candidates(manifests: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    """Every ``(manifest, list key)`` pair this module is allowed to answer for."""
    out: list[tuple[dict[str, Any], str]] = []
    for manifest in manifests:
        app_id = str(manifest.get("id") or "")
        if not app_id or app_id in HANDWRITTEN:
            continue
        for key in _lists_of(manifest):
            out.append((manifest, key))
    return out


def _mentions_app(lower: str, manifest: dict[str, Any]) -> bool:
    """Does the message use one of this app's own declared words?"""
    vocabulary = manifest.get("vocabulary")
    words = (vocabulary.get("keywords") or []) if isinstance(vocabulary, dict) else []
    for word in words:
        if isinstance(word, str) and len(word) >= 3 and word.lower() in lower:
            return True
    return False


def _handwritten_mentioned(lower: str, manifests: list[dict[str, Any]]) -> bool:
    """Does this sentence use one of the hand-written apps' own words?

    Those apps have their own parsers and their own Python methods. A message
    that names one of them is theirs even if it also looks like list-keeping, so
    this module stands down rather than answering over the top of them.
    """
    for manifest in manifests:
        if str(manifest.get("id") or "") in HANDWRITTEN and _mentions_app(lower, manifest):
            return True
    # A hand-written app that is turned off (puffpai.first_apps) still owns
    # its words: "add milk to the shopping list" is never a persona's list.
    try:
        from puffpai import app_registry
        listed = {str(m.get("id") or "") for m in manifests}
        for manifest in app_registry.all_manifests():
            app_id = str(manifest.get("id") or "")
            if (app_id in HANDWRITTEN and app_id not in listed
                    and _mentions_app(lower, manifest)):
                return True
    except Exception:  # noqa: BLE001 - the floor: answer as before
        pass
    return False


def _alternation(keys: list[str]) -> str:
    spellings: list[str] = []
    for key in keys:
        spellings.extend(_variants(key))
    spellings.sort(key=len, reverse=True)
    return "|".join(re.escape(s) for s in spellings)


def _named(lower: str, pairs: list[tuple[dict[str, Any], str]]
           ) -> list[tuple[dict[str, Any], str]]:
    """The candidate lists this message names, longest spelling winning.

    A message naming "harvest log" must not also count as naming "log": the
    longer spelling is matched first and the span it used is blanked out.
    """
    haystack = f" {lower} "
    found: list[tuple[dict[str, Any], str]] = []
    ordered = sorted(
        pairs, key=lambda pair: len(max(_variants(pair[1]), key=len)), reverse=True)
    for manifest, key in ordered:
        for spelling in _variants(key):
            match = re.search(rf"\b{re.escape(spelling)}\b", haystack)
            if match:
                haystack = (haystack[:match.start()] + " " * (match.end() - match.start())
                            + haystack[match.end():])
                found.append((manifest, key))
                break
    return found


def _label(manifest: dict[str, Any], key: str) -> str:
    return str(key).replace("_", " ").strip()


def _which_question(pairs: list[tuple[dict[str, Any], str]]) -> str:
    """The ask-back for a sentence that did not say which list.

    Lists from two different apps are named with their app, because "plantings"
    twice is not a question anybody can answer.
    """
    one_app = len({str(m.get("id")) for m, _ in pairs}) == 1
    names = [
        _label(manifest, key) if one_app
        else f"{manifest.get('name') or manifest.get('id')}'s {_label(manifest, key)}"
        for manifest, key in pairs
    ]
    if len(names) == 1:
        return f"Which list? The one I keep is {names[0]}."
    return "Which list - " + ", ".join(names[:-1]) + f" or {names[-1]}?"


def _clean_item(raw: str) -> str:
    item = " ".join(str(raw or "").split())
    item = item.strip(" .!?,;:'\"")
    item = _ARTICLES_RE.sub("", item).strip()
    return item[:MAX_ITEM_CHARS].strip()


def _row_text(row: Any) -> str:
    """What one stored row says, for reading a list back in a sentence."""
    if isinstance(row, str):
        return row.strip()
    if isinstance(row, dict):
        for field in ("item", "name", "title", "text", "description", "label"):
            value = row.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _read(app_data: Any, app_id: str, key: str) -> Optional[list[Any]]:
    """The live rows of one list, or None when the document cannot be read."""
    try:
        document = app_data.get_data(app_id)
    except Exception:
        return None
    if not isinstance(document, dict):
        return None
    rows = document.get(key)
    return rows if isinstance(rows, list) else []


def _write(app_data: Any, app_id: str, key: str, rows: list[Any]) -> bool:
    try:
        document = app_data.get_data(app_id)
        if not isinstance(document, dict):
            return False
        document[key] = rows
        return bool(app_data.set_data(app_id, document))
    except Exception:
        return False


def _count_phrase(n: int, label: str) -> str:
    if n == 0:
        return f"nothing on the {label} list"
    if n == 1:
        return f"one thing on the {label} list"
    return f"{n} things on the {label} list"


def handle(message: Any, app_data: Any = None,
           manifests: Optional[list[dict[str, Any]]] = None) -> Optional[str]:
    """Answer a declarative-app sentence deterministically, or return None.

    None means "this was not one of ours" and the caller carries on to the model.
    Never raises: a broken store, an unreadable manifest or a sentence this
    grammar does not cover is a None or a plain sentence, never a traceback in
    the owner's chat.
    """
    if not isinstance(message, str) or not message.strip():
        return None
    try:
        live = _feed_answer(message, manifests)
        if live:
            return live
    except Exception:  # pragma: no cover - the floor; chat must not crash
        pass
    try:
        return _handle(message, app_data, manifests)
    except Exception:  # pragma: no cover - the floor; chat must not crash
        return None


def _handle(message: str, app_data: Any,
            manifests: Optional[list[dict[str, Any]]]) -> Optional[str]:
    from puffpai import app_registry

    if manifests is None:
        manifests = app_registry.current_manifests()
    pairs = _candidates(manifests)
    if not pairs:
        return None

    lower = " ".join(message.lower().split())
    named = _named(lower, pairs)

    # "add milk to the shopping list" is FreshTable's sentence. This module does
    # not get to answer for an app that has its own words - checked before the
    # ask-backs below, so an ask-back cannot hijack it either.
    handwritten = _handwritten_mentioned(lower, manifests)

    # "which lists do you keep for me?" is a question about the build, and the
    # build knows the answer: the names, and how much is on each. It used to get
    # the ambiguous-list ask-back, which answers a question with the question.
    if not handwritten and _asks_which_lists(lower):
        return _lists_inventory(pairs, _app_data(app_data))

    if named:
        # The verb is read from the sentence with the LIST NAME taken out of it.
        # A list called "harvest log" contains the word "log", which is also a
        # verb for writing something down, so "show me the harvest log" read as
        # "add" until the name stopped being part of the search.
        verb = _verb(_without_lists(lower, named))
        if verb is None:
            # The list is named and the verb is not one of the four. Naming one
            # of the owner's own lists IS the evidence - dropping this to the
            # model is how "basil for the plantings list" became a paragraph
            # about basil instead of a saved row.
            if handwritten or len(named) > 1:
                return None
            # ...but a list name can turn up inside a word: `_variants` matches
            # the singular too, so "do you know anything about companion
            # planting" NAMES the plantings list as far as the search is
            # concerned. With no verb and no list word, that is a sentence about
            # planting, not about a list.
            if not _LIST_WORD_RE.search(lower):
                return None
            return _what_about_it(named[0][0], named[0][1])
        if not _LIST_WORD_RE.search(lower) and not _addresses_list(lower, verb, named):
            # A scaffolded list is named after the goal's topic word, so the
            # topic word alone is not evidence: "what is the biggest dinosaur?"
            # is a question for the model, not a read-out of the dinosaurs list.
            return None
    else:
        verb = _verb(lower)
        past = None if verb is not None else _past_tense_add(lower, pairs)
        if past is not None and not handwritten:
            manifest, key, item = past
            return _add_item(item, _app_data(app_data), str(manifest["id"]), key,
                             _label(manifest, key))
        if verb is None:
            return None
        if handwritten:
            return None
        # No list named, so the evidence has to come from the SHAPE of the
        # sentence: it must ask for a list OPERATION. A verb is not enough on its
        # own - "what is ...?" is a show verb and the opening of most questions
        # anybody asks - and an app keyword is not enough either, because a
        # scaffolded persona's keywords are the owner's goal words ("greenhouse",
        # "garden", "farm"), which appear in every ordinary question about the
        # subject. Without a list operation this is chat, and we stand down.
        if not _list_operation(lower):
            return None
        if len(pairs) == 1:
            named = list(pairs)
        else:
            mentioned = [p for p in pairs if _mentions_app(lower, p[0])]
            if len(mentioned) == 1:
                named = mentioned
            elif mentioned:
                return _which_question(mentioned)
            elif _LIST_WORD_RE.search(lower):
                # A list verb and the bare word "list". Which one is a question,
                # not a guess.
                return _which_question(pairs)
            elif _one_owner_app(pairs) and _bare_imperative(lower):
                # "add basil" on a build with exactly one persona of the owner's.
                # There is only one app it could be about and only one thing the
                # verb can mean, so the missing piece is WHICH list - and that is
                # a question, which is a better answer than silence.
                return _which_question(pairs)
            else:
                return None

    if len(named) > 1:
        return _which_question(named)

    manifest, key = named[0]
    app_id = str(manifest["id"])
    label = _label(manifest, key)
    app_data = _app_data(app_data)

    if verb == "add":
        return _do_add(lower, app_data, app_id, key, label)
    if verb == "remove":
        return _do_remove(lower, app_data, app_id, key, label)
    if verb in ("count", "show") and not _LIST_WORD_RE.search(lower):
        # "show me my dinosaurs" without the word list: answer only when there
        # is something on it. An empty list is not an answer to a topic
        # question, so the sentence goes on to the model and the live cards.
        rows = _read(app_data, app_id, key)
        if not rows:
            return None
    if verb == "count":
        rows = _read(app_data, app_id, key)
        if rows is None:
            return _unreadable(label)
        return _count_phrase(len(rows), label).capitalize() + "."
    return _do_show(app_data, app_id, key, label)


def _addresses_list(lower: str, verb: str,
                    named: list[tuple[dict[str, Any], str]]) -> bool:
    """Does a sentence with no word "list" still clearly mean the list?

    Adds and removes must say where: "add a t-rex to my dinosaurs". Shows and
    counts must say whose: "show me my dinosaurs", "what is on the dinosaurs".
    """
    keys = _alternation([key for _manifest, key in named])
    # The list name must END the phrase. "what does my 2014 Honda Civic need"
    # says "my 2014", and a scaffolded list named after the goal's words is
    # called 2014: the name has to be the thing asked about, not the first
    # word of a longer one.
    end = rf"(?:{keys})(?:\s+(?:please|too|now|today|thanks))*\s*(?:[?.!,;]|$)"
    if verb == "add":
        return re.search(rf"\b(?:{_ADD_VERBS})\s+.+?\s+(?:to|on|onto|in|into)\s+"
                         rf"(?:the\s+|my\s+|our\s+)?{end}", lower) is not None
    if verb == "remove":
        if _LAST_ONE_RE.search(lower):
            return True
        return re.search(rf"\b(?:{_REMOVE_VERBS})\s+.+?\s+(?:from|off|out of)\s+"
                         rf"(?:the\s+|my\s+|our\s+)?{end}", lower) is not None
    return re.search(rf"\b(?:my|our|on\s+the|on\s+my|on\s+our)\s+{end}",
                     lower) is not None


def _app_data(app_data: Any) -> Any:
    """The shared AppData instance, or one made here for a direct caller."""
    if app_data is not None:
        return app_data
    from puffpai.app_data import AppData
    return AppData()


def _one_owner_app(pairs: list[tuple[dict[str, Any], str]]) -> bool:
    """True when every candidate list belongs to the same single app.

    That is the "only one thing it could be" evidence: on a build with one
    persona of the owner's, "add basil" cannot be about anybody else's app, so
    asking which of ITS lists is a real question rather than a guess.
    """
    return len({str(m.get("id")) for m, _ in pairs}) == 1


#: Verbs that can ONLY mean put-this-on-a-list or take-it-off. The wider
#: :data:`_ADD_VERBS` and :data:`_REMOVE_VERBS` are fine once a list has been
#: named, and far too loose without one: "take" is in them, and "how do I take a
#: screenshot" is not a request to delete anything.
_UNAMBIGUOUS_VERBS = r"add|remove|delete|jot\s+down|write\s+down|cross\s+off"

#: A preposition phrase naming where something should go or come from. Its
#: PRESENCE is what says "this sentence already named a destination", and if that
#: destination is not one of our lists then the sentence is not ours - which is
#: how "add milk to my groceries" stays FreshTable's problem rather than becoming
#: an ask-back about plantings.
_DESTINATION_RE = re.compile(r"\b(?:to|onto|into|in|on|from|off|out\s+of)\b")


def _bare_imperative(lower: str) -> bool:
    """"add basil": an unambiguous list verb, at the start, naming no destination.

    Both halves matter. The verb must be one that cannot mean anything else, and
    the sentence must not already say where the thing goes - because if it does,
    and we did not recognise the place, then the place is somebody else's.
    """
    match = re.match(rf"(?:{_UNAMBIGUOUS_VERBS})\b(?P<rest>.*)$", lower)
    if match is None:
        return False
    # "add me some cross stitch tips" asks the BOT for something; it is not
    # writing an item down.
    if re.match(r"\s*(?:me|us|more)\b", match.group("rest")):
        return False
    return not _DESTINATION_RE.search(match.group("rest"))


def _list_operation(lower: str) -> bool:
    """Does this sentence ask for a LIST operation, or just mention the subject?

    Two shapes count once no list has been named: the word list/log, which says
    the thing being operated on is a list, and an unambiguous bare imperative
    ("add basil"), where the verb can mean nothing else. A list NAME is evidence
    too and is stronger than either - that is the ``named`` branch, decided before
    this is asked.

    What deliberately does not count is an app or goal keyword. "what is the
    greenhouse night temperature and the code word?" is a question for the model,
    and answering it with "Which list - plantings, harvest log or seed inventory?"
    is this module talking over the bot.
    """
    return bool(_LIST_WORD_RE.search(lower) or _bare_imperative(lower))


def _asks_which_lists(lower: str) -> bool:
    """"which lists do you keep for me?" - the inventory, not one list's rows."""
    return any(pattern.search(lower) for pattern in _WHICH_LISTS_RES)


def _lists_inventory(pairs: list[tuple[dict[str, Any], str]],
                     app_data: Any) -> str:
    """Name every list this build keeps, with how much is on each.

    The counts are read off disk like every other claim in this module, and a
    list whose document cannot be read is reported as unreadable rather than as
    empty - "empty" is a fact about a list, not about a failure.
    """
    one_app = _one_owner_app(pairs)
    parts: list[str] = []
    for manifest, key in pairs:
        label = _label(manifest, key)
        name = (label if one_app
                else f"{manifest.get('name') or manifest.get('id')}'s {label}")
        rows = _read(app_data, str(manifest["id"]), key)
        if rows is None:
            parts.append(f"{name} (I could not read it off disk)")
        elif not rows:
            parts.append(f"{name} (empty)")
        elif len(rows) == 1:
            parts.append(f"{name} (one thing on it)")
        else:
            parts.append(f"{name} ({len(rows)} things on it)")

    first, owner = _label(pairs[0][0], pairs[0][1]), ""
    if one_app:
        app = pairs[0][0]
        owner = f" for {app.get('name') or app.get('id')}"
    if len(parts) == 1:
        head = f"I keep one list{owner}: {parts[0]}."
    else:
        head = (f"I keep {len(parts)} lists{owner}: "
                + ", ".join(parts[:-1]) + f" and {parts[-1]}.")
    return (f"{head} Say \"what is on the {first} list\" to read one, or "
            f"\"add tomatoes to the {first} list\" to put something on it.")


def _what_about_it(manifest: dict[str, Any], key: str) -> str:
    """The ask-back for a sentence that named a list and no verb."""
    label = _label(manifest, key)
    return (f"I keep the {label} list - what would you like me to do with it? "
            f"Say \"add tomatoes to the {label} list\", \"what is on the {label} "
            f"list\", or \"remove tomatoes from the {label} list\".")


def _stems(key: str) -> set[str]:
    """The word stems one list name shares, for reading a past tense.

    ``plantings`` -> ``{plantings, planting, plant}``. A two-suffix peel, not a
    stemmer: deterministic, offline, and impossible to be surprised by - the same
    discipline as :func:`_variants`.
    """
    label = str(key).replace("_", " ").strip().lower()
    word = label.split()[-1] if label else ""
    out: set[str] = set()
    if not word:
        return out
    out.add(word)
    if word.endswith("s") and len(word) > 3:
        word = word[:-1]
        out.add(word)
    if word.endswith("ing") and len(word) > 5:
        out.add(word[:-3])
    return {w for w in out if len(w) >= 4}


def _past_tense_add(lower: str, pairs: list[tuple[dict[str, Any], str]]
                    ) -> Optional[tuple[dict[str, Any], str, str]]:
    """``"I planted tomatoes today"`` -> add tomatoes to ``plantings``, or None.

    Deliberately narrow, on two axes at once:

    * The sentence must be first person and past tense - ``I <verb>ed <item>`` -
      at the START. "Tomatoes were planted" and "should I plant tomatoes" are
      not somebody writing down what they did.
    * The verb must share a STEM with a list this build declares. A build with a
      ``plantings`` list understands "I planted"; a build without one does not,
      and the sentence goes to the model where it belongs. No list, no verb: that
      is what keeps this from being a general-purpose past-tense grabber.

    Returns ``(manifest, key, item)`` for exactly one match, or None - two lists
    whose stems both fit means this is not unambiguous and it stands down.
    """
    match = re.match(r"i\s+(?P<verb>[a-z]+?)(?:ed|d)\s+(?P<rest>.+)$", lower)
    if match is None:
        return None
    verb_stem = match.group("verb")
    item = _clean_item(_TRAILING_TIME_RE.sub("", match.group("rest")))
    if not item:
        return None
    hits = [(manifest, key) for manifest, key in pairs
            if verb_stem in _stems(key)]
    if len(hits) != 1:
        return None
    return hits[0][0], hits[0][1], item


def _without_lists(lower: str, pairs: list[tuple[dict[str, Any], str]]) -> str:
    """The sentence with the named list spellings blanked out."""
    out = lower
    for _manifest, key in pairs:
        for spelling in _variants(key):
            out = re.sub(rf"\b{re.escape(spelling)}\b", " ", out)
    return " ".join(out.split())


def _verb(lower: str) -> Optional[str]:
    """Which of the four verbs this sentence uses, or None.

    Order matters. "how many" is asked first because it is also a show. A show
    verb only counts at the START of the sentence, which is where a question
    puts it; that is what keeps "log a reading" (an add whose verb is also a
    noun) from being read as a request to read something out.
    """
    if re.search(r"\bhow many\b", lower):
        return "count"
    if re.search(rf"^(?:{_SHOW_VERBS})\b", lower):
        return "show"
    if re.search(rf"\b(?:{_REMOVE_VERBS})\b", lower):
        return "remove"
    if re.search(rf"\b(?:{_ADD_VERBS})\b", lower):
        return "add"
    return None


def _unreadable(label: str) -> str:
    return (f"I could not read the {label} list off disk, so I am not going to "
            f"tell you what is on it.")


def _do_add(lower: str, app_data: Any, app_id: str, key: str,
            label: str) -> Optional[str]:
    pattern = rf"\b(?:{_ADD_VERBS})\s+(?P<item>.+?)\s+(?:to|on|onto|in|into)\s+" \
              rf"(?:the\s+|my\s+|our\s+)?(?:{_alternation([key])})\b"
    match = re.search(pattern, lower)
    if match is None:
        # "add to the plantings list" - the verb and the list, no item.
        return (f"What should I add to the {label} list? Say it as \"add "
                f"tomatoes to the {label} list\".")
    item = _clean_item(match.group("item"))
    if not item:
        return (f"What should I add to the {label} list? Say it as \"add "
                f"tomatoes to the {label} list\".")
    return _add_item(item, app_data, app_id, key, label)


def _add_item(item: str, app_data: Any, app_id: str, key: str,
              label: str) -> str:
    """Write one item down and say what the document holds afterwards.

    Split out of :func:`_do_add` so the past-tense form ("I planted tomatoes")
    writes through exactly the same code, including the read-back verify. A
    second write path is a second place for "Saved" to stop meaning saved.
    """
    rows = _read(app_data, app_id, key)
    if rows is None:
        return (f"I could not read the {label} list off disk, so I did not "
                f"write to it either. Nothing was saved.")
    rows = list(rows) + [{"item": item, "added": datetime.now().isoformat()}]
    if not _write(app_data, app_id, key, rows):
        return (f"I could not save that to the {label} list, so nothing was "
                f"written down.")

    # Read-back verify. "Saved" is a claim, and a claim needs the document.
    after = _read(app_data, app_id, key)
    if after is None or not any(_row_text(row) == item for row in after):
        return (f"I tried to add {item} to the {label} list and it is not in "
                f"the saved document, so treat it as not saved.")
    return (f"Added {item} to the {label} list. That is "
            f"{_count_phrase(len(after), label)}.")


def _do_remove(lower: str, app_data: Any, app_id: str, key: str,
               label: str) -> Optional[str]:
    pattern = rf"\b(?:{_REMOVE_VERBS})\s+(?P<item>.+?)\s+(?:from|off|out of)\s+" \
              rf"(?:the\s+|my\s+|our\s+)?(?:{_alternation([key])})\b"
    # Before the item pattern, not after it: "delete the last one from the
    # plantings list" DOES match the pattern, with "last one" as the item, and
    # then reports that there is no entry called "last one".
    if _LAST_ONE_RE.search(lower):
        return _confirm_last(app_data, app_id, key, label)

    match = re.search(pattern, lower)
    if match is None:
        return (f"What should I take off the {label} list? Say it as \"remove "
                f"tomatoes from the {label} list\".")
    item = _clean_item(match.group("item"))
    if not item:
        return (f"What should I take off the {label} list? Say it as \"remove "
                f"tomatoes from the {label} list\".")

    rows = _read(app_data, app_id, key)
    if rows is None:
        return (f"I could not read the {label} list off disk, so I did not "
                f"change it.")
    kept = [row for row in rows if _row_text(row).lower() != item.lower()]
    if len(kept) == len(rows):
        return (f"{item} is not on the {label} list, so nothing was removed.")
    if not _write(app_data, app_id, key, kept):
        return (f"I could not save the {label} list, so {item} is still on it.")
    after = _read(app_data, app_id, key)
    if after is None or any(_row_text(row).lower() == item.lower() for row in after):
        return (f"I tried to take {item} off the {label} list and it is still "
                f"in the saved document, so treat it as not removed.")
    return (f"Took {item} off the {label} list. That leaves "
            f"{_count_phrase(len(after), label)}.")


def _confirm_last(app_data: Any, app_id: str, key: str, label: str) -> str:
    """"delete the last one": name the most recent row and ask for the word.

    The row is found deterministically - the last one written, which is the last
    one in the document because :func:`_add_item` appends - but it is NOT removed
    here. This module has no memory of the previous turn, so there is nowhere for
    a "yes" to be understood; a deletion whose confirmation cannot be heard is a
    deletion nobody confirmed. So the answer is the item's own text, which the
    owner can say straight back as the sentence that does remove it.
    """
    rows = _read(app_data, app_id, key)
    if rows is None:
        return _unreadable(label)
    texts = [_row_text(row) for row in rows]
    texts = [t for t in texts if t]
    if not texts:
        return (f"The {label} list is empty, so there is no last one to take "
                f"off it.")
    last = texts[-1]
    return (f"The most recent thing on the {label} list is {last}. Say "
            f"\"remove {last} from the {label} list\" and I will take it off - I "
            f"am not deleting anything on a guess.")


def _do_show(app_data: Any, app_id: str, key: str, label: str) -> str:
    rows = _read(app_data, app_id, key)
    if rows is None:
        return _unreadable(label)
    texts = [_row_text(row) for row in rows]
    texts = [t for t in texts if t]
    if not texts:
        if rows:
            return (f"The {label} list has {len(rows)} entries, but none of "
                    f"them say what they are in words I can read out. The page "
                    f"at the app shows them as they are stored.")
        return f"The {label} list is empty."
    shown = texts[:MAX_READ_BACK]
    more = len(texts) - len(shown)
    sentence = f"On the {label} list: " + ", ".join(shown) + "."
    if more:
        sentence += f" And {more} more."
    return sentence


# ---------------------------------------------------------------------------
# live-data apps: answered from the app's own stored card, never the model
# ---------------------------------------------------------------------------
#
# Two sentence shapes, matched against the app's NAME, its card title and (for
# a one-number card) the number's label - never against topic words, so no
# app can capture ordinary chat about its subject:
#
#     show me the latest <app name>        what's the <label or app name>
#
# The answer is read from the stored card, fetched first if it is due and the
# owner allowed the service. It always carries the credit line. When the app
# is not set up, the answer says so and says how, and nothing is made up.

_LATEST_RE = re.compile(
    r"^(?:please\s+)?(?:show|give|tell|get|read)(?:\s+me)?\s+(?:the\s+)?"
    r"(?:latest|newest|current|today'?s|most\s+recent)\s+(?:from\s+)?(?P<x>.+)$")
_WHATS_RE = re.compile(
    r"^what(?:'s|s|\s+is|\s+are)\s+(?:the\s+)?(?:latest\s+|current\s+|newest\s+)?"
    r"(?:from\s+)?(?P<x>.+)$")
_TAIL_RE = re.compile(r"\s+(?:right\s+now|now|today|please)$")
#: "show me upcoming rocket launches": only ever an exact card name or the
#: card's own phrase (see :func:`_feed_names`), never a topic word.
_SHOW_ME_RE = re.compile(r"^(?:please\s+)?(?:show|give|tell)\s+me\s+(?:some\s+)?"
                         r"(?P<x>.+)$")


def _norm_phrase(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    text = " ".join(text.split())
    text = _TAIL_RE.sub("", text)
    return re.sub(r"^(?:the|my)\s+", "", text).strip()


def _feed_names(name: str, card: dict[str, Any],
                about: str = "") -> set[str]:
    out = {_norm_phrase(name)}
    if about:
        out.add(_norm_phrase(about))
    title = str(card.get("title") or "")
    if title:
        out.add(_norm_phrase(title))
    if card.get("kind") == "value" and card.get("label"):
        out.add(_norm_phrase(str(card["label"])))
    return {n for n in out if len(n) >= 2}


def _asked_feed(message: str) -> Optional[str]:
    lower = " ".join(message.lower().strip().rstrip("?.!").split())
    for pattern in (_LATEST_RE, _WHATS_RE, _SHOW_ME_RE):
        match = pattern.match(lower)
        if match:
            return _norm_phrase(match.group("x"))
    return None


def _age_phrase(minutes: Optional[int]) -> str:
    if minutes is None:
        return ""
    if minutes < 2:
        return "just now"
    if minutes < 120:
        return f"{minutes} minutes ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} hours ago"
    return f"{minutes // (60 * 24)} days ago"


def _feed_answer(message: str, manifests: Optional[list[dict[str, Any]]]
                 ) -> Optional[str]:
    asked = _asked_feed(message)
    if not asked:
        return None
    from puffpai import feeds
    for manifest, feed in feeds.feed_apps(manifests):
        name = str(manifest.get("name") or manifest.get("id"))
        if asked in _feed_names(name, feed["card"],
                                str(manifest.get("shows") or "")):
            return _live_reply(str(manifest["id"]), name)
    try:
        from puffpai import feed_generator
        drafts = feed_generator.list_drafts()
    except Exception:
        drafts = []
    for draft in drafts:
        if asked == _norm_phrase(str(draft.get("name") or "")):
            return (f"{draft['name']} is ready to add but not set up yet, so I "
                    f"have nothing from it. To add it, run: puffpai apps "
                    f"install {draft['id']}")
    return None


def _live_reply(app_id: str, name: str) -> str:
    from puffpai import feeds
    state = feeds.refresh(app_id)
    card = state.get("card")
    if not state.get("allowed") and card is None:
        how = state.get("how_to_fix")
        # After a yes on a bot set to stay offline, "not set up yet" would
        # contradict the next sentence; the service's own words say it all.
        lead = "" if state.get("service_state") == "bot_offline" \
            else f"{name} is not set up yet. "
        return (f"{lead}{state.get('service_message')}"
                + (f" To fix it, run: {how}" if how else ""))
    if card is None:
        return (f"I could not get {name} just now: {state.get('message')} "
                f"Nothing was made up.")
    kind = card.get("kind")
    if kind == "value":
        unit = f" {card['unit']}" if card.get("unit") else ""
        body = f"{card.get('label')}: {card.get('value')}{unit}"
        if card.get("caption"):
            body += f" ({card['caption']})"
        body += "."
    elif kind == "image":
        parts = [p for p in (card.get("headline"), card.get("caption")) if p]
        seen: list[str] = []
        for part in parts:
            if part not in seen:
                seen.append(part)
        body = (f"The latest picture from {name}"
                + (f": {'. '.join(seen)}." if seen else ".")
                + f" You can see it on the {name} page.")
        if card.get("image_credit"):
            body += f" Picture credit: {card['image_credit']}."
    else:
        rows = card.get("items") or []
        shown = []
        for row in rows[:5]:
            line = row.get("title", "")
            if row.get("detail"):
                line += f" ({row['detail'][:90]})"
            shown.append(line)
        body = f"The latest from {name}: " + "; ".join(shown) + "."
        if len(rows) > 5:
            body += f" And {len(rows) - 5} more on the {name} page."
    age = _age_phrase(state.get("age_minutes"))
    tail = f" Fetched {age}." if age else ""
    after = ""
    if not state.get("allowed"):
        # Stored data is still true data, but the owner should not think it
        # is being kept up to date when it is not.
        tail += (" This is the last copy I have. I am not getting new data "
                 "from it right now.")
        if state.get("how_to_fix"):
            # Last, so the command is not run together with other words.
            after = f" To turn it back on, run: {state['how_to_fix']}"
    return f"{body}{tail} {state.get('credit')}{after}".strip()


__all__ = ["HANDWRITTEN", "handle"]
