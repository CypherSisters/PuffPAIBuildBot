"""Recognise "add more to the bot" in chat, and read the owner's reply.

Pure functions: no I/O, no model, no network. :mod:`puffpai.add_more` calls
them; nothing here searches or installs anything.

What counts as an add request (English only, and deliberately narrow):

1. An add verb aimed at the bot itself::

       add dinosaur fossil maps to you
       can you put car recalls on my bot
       I'd like to add knitting patterns to Rexy

   The destination must be the bot ("you", "yourself", "my bot", "this bot"
   or the bot's own name). "add milk to the shopping list" names somewhere
   else, so it is never ours -- the lists keep their own sentences.

   Or "add me/us X" with no destination at all::

       add me some cross stitch tips too

   "me"/"us" says the bot is being asked for something, not a list being
   written to (the lists read "add me ..." the same way and leave it). A
   sentence that goes on to say where ("add me to the calendar", "add me
   milk to the shopping list", "add me as a user") is not taken.
   Or aimed at the home page, which also asks for it to be SHOWN there
   (``"place": "home"``; without this, "can you add ... to my dashboard"
   goes to the model, which asks questions and loses the thread)::

       add the ISS tracker to my dashboard
       put a weather card on my home page
       show me NASA pictures on my dashboard

   Words that only say "a thing on a page" (card, tile, widget) are dropped
   from what is searched for: "a weather card" looks for weather.
2. Asking the bot to learn::

       teach yourself about volcanoes
       can you learn about bird songs

3. "can you ALSO show/give/send/get me X" -- only when the caller says the
   bot does not have X yet (``ability_missing``). If it already has it, the
   message is an ordinary question and falls through.

What deliberately does NOT count (for privacy):
"I want you to know/have X" and a bare "tell me X". People tell their bot
private things in exactly those shapes ("I want you to know my son has
epilepsy"); those are not requests to go and find anything.

A how-question ("how do I add more to you?") is not a request either: it is
answered by the bot's own description of how to add things.

The reply grammar (:func:`read_reply`) reads the owner's OWN message, never
model output: "yes", "all", "no", "1 and 3", "just the first", "not 2",
"look online", "switch it on".

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Optional

#: The longest thing-to-add we accept, and the shortest (in letters).
MAX_WHAT = 160
MIN_LETTERS = 3

_ADD_VERB = r"(?:add|put|load|install|stick)"
_SELF = (r"(?:you|yourself|your\s+(?:brain|memory|library|knowledge)|"
         r"my\s+bot|this\s+bot|the\s+bot|my\s+helper|my\s+buddy|my\s+assistant)")
_LEAD = (r"(?:(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?"
         r"|i\s+(?:want|would\s+like|'d\s+like|wanna)\s+to\s+|i'd\s+like\s+to\s+"
         r"|let'?s\s+|please\s+))?(?:also\s+)?")
_TAIL = r"(?:\s+(?:too|as\s+well|please|now))*\s*[.!?]*\s*$"
#: The owner's home page, by the names people give it.
_HOME = (r"(?:my|the|your|our)\s+(?:dashboard|home\s*page|home\s+screen|"
         r"front\s+page|main\s+page|start\s+page)")
_HOME_RE = re.compile(r"^" + _HOME + r"$", re.I)
#: "show me X on my dashboard", "can you display X on the home page".
_SHOW_ON = re.compile(
    r"^\s*" + _LEAD + r"(?:show|display|pin)\s+(?:me\s+|us\s+)?(?P<what>.+?)\s+"
    r"(?:on|in|onto)\s+" + _HOME + _TAIL, re.I)
#: Words that say "a thing on a page", not what it is about.
_PAGE_THING = re.compile(r"\b(?:live\s+)?(?:cards?|tiles?|widgets?|panels?)\b", re.I)

_QUESTION_START = re.compile(r"^\s*(?:how|what|why|where|when|who|which|"
                             r"is|are|do|does|did)\b", re.I)
_LEARN = re.compile(
    r"^\s*(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"(?:also\s+)?)?(?:teach|train)\s+(?:yourself|you)\s+(?:all\s+|more\s+)?"
    r"(?:about\s+)?(?P<what>.+?)" + _TAIL, re.I)
_LEARN_ABOUT = re.compile(
    r"^\s*(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"(?:also\s+)?|go\s+)?learn\s+(?:all\s+|more\s+)?about\s+(?P<what>.+?)"
    + _TAIL, re.I)
_SHOW_ALSO = re.compile(
    r"^\s*(?:can|could)\s+you\s+also\s+(?:show|give|send|get)\s+me\s+"
    r"(?P<what>.+?)" + _TAIL, re.I)
_ARTICLE = re.compile(r"^(?:a|an|the|some|more|any|all|all\s+the|about)\s+", re.I)
_PRONOUN = re.compile(r"^(?:it|that|this|them|those|these|stuff|things?|"
                      r"something|anything|everything|more)$", re.I)


#: Words that ask for a look (colours, a theme), not for something to know.
_LOOK_WORDS = re.compile(r"\b(?:looks?|colou?rs?|themes?)\b", re.I)
#: Words that ask for web search (a sign-up key line), not for a topic.
_SEARCH_WORDS = re.compile(r"\b(?:web\s+search|search(?:ing)?\s+the\s+(?:web|"
                           r"internet)|internet\s+search|search\s+engine)\b", re.I)


def is_look_words(words: Any) -> bool:
    """True when *words* ask for a look ("a jungle look", "dinosaur colours")."""
    return bool(_LOOK_WORDS.search(str(words or "")))


def is_search_words(words: Any) -> bool:
    """True when *words* ask for web search itself ("web search", "brave")."""
    text = str(words or "")
    return bool(_SEARCH_WORDS.search(text) or re.search(
        r"\b(?:brave|marginalia)\b", text, re.I))


def _look_pattern(bot_name: str) -> re.Pattern:
    """ "make it look like a jungle", "change your colours to dinosaur green"."""
    targets = r"(?:it|you|yourself|this|the\s+dashboard|" + _SELF[3:-1] + r")"
    name = " ".join(str(bot_name or "").split())
    if name and len(name) <= 40:
        targets = rf"(?:{targets}|{re.escape(name)})"
    return re.compile(
        rf"^\s*{_LEAD}(?:"
        rf"(?:make|turn)\s+{targets}\s+look\s+(?:more\s+)?(?:like\s+)?(?P<what>.+?)"
        rf"|(?:change|set|switch)\s+(?:your|its|the|my\s+bot'?s|the\s+dashboard'?s)\s+"
        rf"(?:look|colou?rs|theme)\s+to\s+(?P<what2>.+?)"
        rf"|give\s+(?:yourself|it|you|{targets})\s+(?P<what3>.+?\s+(?:look|colou?rs|theme))"
        rf")" + _TAIL, re.I)


def _add_pattern(bot_name: str) -> re.Pattern:
    targets = rf"(?:{_SELF}|{_HOME})"
    name = " ".join(str(bot_name or "").split())
    if name and len(name) <= 40:
        targets = rf"(?:{_SELF}|{_HOME}|{re.escape(name)})"
    return re.compile(
        rf"^\s*{_LEAD}(?P<verb>{_ADD_VERB})\s+(?P<what>.+?)\s+"
        rf"(?:to|onto|into|on|in)\s+(?P<target>{targets})" + _TAIL, re.I)


_ADD_ME = re.compile(
    rf"^\s*{_LEAD}{_ADD_VERB}\s+(?:me|us)\s+(?P<what>.+?)" + _TAIL, re.I)
#: "add me to ...", "add me as ...": about the person, not a thing to add.
_ADD_ME_NOT = re.compile(r"^(?:to|onto|into|on|in|as|for|with|at)\b"
                         r"|^(?:some|more|any)\s*$", re.I)
#: A place named later in the sentence ("... to the shopping list").
_ADD_ME_DEST = re.compile(r"\b(?:to|onto|into|on|in)\s+(?:the|my|our|your)\s+"
                          r"(?P<place>.+)$", re.I)
_PLACE_WORDS = re.compile(r"\b(?:list|lists|log|calendar|notes?)\b", re.I)


def _add_me(text: str, is_list_name: Optional[Callable[[str], bool]]
            ) -> Optional[str]:
    """The words of "add me some X too", or None when it names a place."""
    match = _ADD_ME.match(text)
    if not match or _ADD_ME_NOT.match(match.group("what")):
        return None
    dest = _ADD_ME_DEST.search(match.group("what"))
    if dest and (_PLACE_WORDS.search(dest.group("place"))
                 or (is_list_name is not None and is_list_name(dest.group("place")))):
        return None
    return _clean_what(match.group("what"))


def _clean_what(raw: str) -> Optional[str]:
    what = " ".join(str(raw or "").split()).strip(" ,.;:!?\"'")
    for _ in range(2):
        what = _ARTICLE.sub("", what).strip()
    if not what or len(what) > MAX_WHAT or _PRONOUN.match(what):
        return None
    if sum(ch.isalpha() for ch in what) < MIN_LETTERS:
        return None
    return what


def _home_what(raw: str) -> Optional[str]:
    """What to look for, when it is asked for on the home page."""
    what = _clean_what(raw)
    if not what or re.match(r"(?i)^(?:what|which|how|where|who|why)\b", what):
        return None  # "show me what's on my dashboard" is a question
    bare = _clean_what(_PAGE_THING.sub(" ", what))
    return bare or what


def detect(message: Any, *, bot_name: str = "",
           is_list_name: Optional[Callable[[str], bool]] = None,
           ability_missing: Optional[Callable[[str], bool]] = None
           ) -> Optional[dict[str, str]]:
    """``{"words", "shape"}`` when *message* asks to add something, else None.
    A request aimed at the home page also says ``"place": "home"``.

    ``is_list_name(text)`` says whether a destination is one of the owner's
    own lists (a list sentence is never taken). ``ability_missing(words)``
    says the bot does not have that yet; without it the "can you also show
    me" shape is never taken.
    """
    text = " ".join(str(message or "").split())
    if not text or len(text) > 400 or _QUESTION_START.match(text):
        return None
    match = _look_pattern(bot_name).match(text)
    if match:
        what = _clean_what(match.group("what") or match.group("what2")
                           or match.group("what3"))
        return {"words": what, "shape": "look"} if what else None
    match = _add_pattern(bot_name).match(text)
    if match:
        target = match.group("target")
        if is_list_name is not None and is_list_name(target):
            return None
        if _HOME_RE.match(" ".join(target.split())):
            what = _home_what(match.group("what"))
            if not what:
                return None
            if is_look_words(what):
                return {"words": what, "shape": "look"}
            return {"words": what, "shape": "add", "place": "home"}
        what = _clean_what(match.group("what"))
        if not what:
            return None
        return {"words": what, "shape": "look" if is_look_words(what) else "add"}
    match = _SHOW_ON.match(text)
    if match:
        what = _home_what(match.group("what"))
        if not what or is_look_words(what):
            return None
        return {"words": what, "shape": "add", "place": "home"}
    what = _add_me(text, is_list_name)
    if what:
        return {"words": what, "shape": "look" if is_look_words(what) else "add"}
    for pattern in (_LEARN, _LEARN_ABOUT):
        match = pattern.match(text)
        if match:
            what = _clean_what(match.group("what"))
            return {"words": what, "shape": "learn"} if what else None
    match = _SHOW_ALSO.match(text)
    if match and ability_missing is not None:
        what = _clean_what(match.group("what"))
        if what and ability_missing(what):
            return {"words": what, "shape": "show"}
    return None


# -- the owner's reply ---------------------------------------------------------

_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
             "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
             "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_YES_WORDS = frozenset({"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "y",
                        "please", "all", "both", "everything", "go", "ahead",
                        "do", "sounds", "good"})
_FILLER = frozenset({"just", "only", "add", "and", "the", "ones", "number",
                     "numbers", "of", "them", "it", "those", "these", "item",
                     "items", "line", "lines", "please", "thanks", "thank",
                     "you", "one", "yes", "yeah", "ok", "okay", "sure", "all",
                     "both", "everything", "go", "ahead", "do", "sounds",
                     "good", "yep", "yup", "y", "&", "plus", "too", "also"})
_NO = re.compile(r"^\s*(?:no|nope|nah|n|no\s+thanks|no\s+thank\s+you|not\s+now|"
                 r"none|nothing|neither|maybe\s+later|cancel|never\s*mind|"
                 r"don'?t|do\s+not|skip)\b[\s!.,]*(?:thanks|thank\s+you|it)?"
                 r"[\s!.]*$", re.I)
_ONLINE = re.compile(r"^\s*(?:yes[\s,]+|ok[\s,]+|okay[\s,]+|sure[\s,]+|please[\s,]+)?"
                     r"(?:look|search|check|try|ask)\s+(?:it\s+)?online"
                     r"(?:\s+too|\s+as\s+well|\s+please)*[\s!.]*$", re.I)
_SWITCH_ON = re.compile(r"^\s*(?:yes[\s,]+|ok[\s,]+|okay[\s,]+|sure[\s,]+|please[\s,]+)?"
                        r"(?:switch|turn)\s+(?:it|that)\s+on(?:\s+please)?[\s!.]*$",
                        re.I)
_EXCEPT = re.compile(r"\b(?:not|except|but|without|apart\s+from)\b", re.I)
_TOKEN = re.compile(r"[a-z]+|\d+|&")


def _numbers(tokens: Iterable[str]) -> Optional[list[int]]:
    """The numbers in *tokens*; None if any token is not part of a reply."""
    out: list[int] = []
    for tok in tokens:
        if tok.isdigit():
            out.append(int(tok))
        elif tok in _ORDINALS and tok != "one":
            out.append(_ORDINALS[tok])
        elif tok in _FILLER or tok in ("not", "except", "but", "without",
                                       "apart", "from", "no"):
            continue
        else:
            return None
    return out


def is_numbered_pick(message: Any) -> bool:
    """True when *message* reads as picking lines by number ("just 1",
    "1 and 3", "not 2") - a reply shape that only makes sense after a
    numbered list. A bare yes or no is not one (other questions use those)."""
    text = " ".join(str(message or "").lower().split())
    if not text or len(text) > 120:
        return False
    picked = _numbers(_TOKEN.findall(text.replace("'", "")))
    return bool(picked) and read_reply(text, max(20, max(picked))) is not None


def read_reply(message: Any, numbers: Any) -> Optional[dict[str, Any]]:
    """The owner's answer to a question that showed these line *numbers*.

    *numbers* is the list of numbers shown (or a count: 1..count). Returns
    ``{"yes": [numbers]}``, ``{"no": True}``, ``{"online": True}``,
    ``{"switch_on": True}``, or None when the message is not a reply (it is
    then treated as a new message). A number that was not shown is not a
    reply.
    """
    if isinstance(numbers, int):
        allowed = list(range(1, numbers + 1))
    else:
        allowed = sorted({int(n) for n in numbers or [] if isinstance(n, int)})
    text = " ".join(str(message or "").lower().split())
    if not text or len(text) > 120:
        return None
    if _ONLINE.match(text):
        return {"online": True}
    if _SWITCH_ON.match(text):
        return {"switch_on": True}
    if _NO.match(text):
        return {"no": True}
    tokens = _TOKEN.findall(text.replace("'", ""))
    if not tokens:
        return None
    picked = _numbers(tokens)
    if picked is None:
        return None
    if "one" in tokens and not picked and not (set(tokens) & _YES_WORDS):
        # "the one" alone is not an answer; "just one" is ambiguous
        return None
    if any(n not in allowed for n in picked):
        return None
    if _EXCEPT.search(text):
        if not picked:
            return None
        rest = [n for n in allowed if n not in picked]
        return {"yes": rest} if rest else {"no": True}
    if picked:
        return {"yes": sorted(dict.fromkeys(picked))}
    if set(tokens) & _YES_WORDS and allowed:
        return {"yes": allowed}
    return None


__all__ = ["MAX_WHAT", "detect", "is_look_words", "is_search_words", "read_reply"]
