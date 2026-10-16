"""Turning what a person typed into words worth searching a library for.

Deterministic and offline, so it behaves the same with the network unplugged:

* ``AstronomyBot`` and ``astronomybot`` both give ``astronomy``: camel case is split, and a
  closing ``bot`` / ``buddy`` / ``helper`` glued onto a word is taken off.
* Filler never searches. ``a bot that knows everything and shows cool stuff``
  has named no subject, and a search for "everything" would return the whole
  library and call it a match.
* Simple plurals fold (``dinosaurs`` -> ``dinosaur``, ``recalls`` -> ``recall``).
  A one-rule fold, not a stemmer, so what matched can always be explained.

There are no topic tables here. Which words mean "space" or "cars" is the
library's business; this module only decides which of the owner's words are
words at all.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import html
import re
import unicodedata
import urllib.parse
from typing import Iterable

from puffpai.build_interview import _STOPWORDS

MAX_TERMS = 12
MAX_TEXT = 400

#: Words that describe how a bot talks or what it is, never what it knows.
#: Added to the build interview's filler list, which stays the shared base.
EXTRA_STOPWORDS = frozenset("""
buddy buddies helper helpers pal pals friend friends companion guide guides
work works working worked show showing shown cool awesome fun neat amazing
love loves loving tip tips trick tricks info information fact facts
smart everything anything whole entire lots
tos howto howtos way ways
day days daily today tonight time times
able let lets go goes going come
answer answers question questions ask asks talk talks chat chats
""".split())

#: Endings that people glue onto a subject to name a bot.
_GLUED_SUFFIXES = ("bots", "bot", "buddy", "helper", "pal")

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD_RE = re.compile(r"[a-z0-9]+")


def stopwords() -> frozenset[str]:
    return _STOPWORDS | EXTRA_STOPWORDS


#: English words that really end in "oe" (so "canoes" -> "canoe", while
#: "volcanoes" -> "volcano"). A spelling rule, not a topic list.
_OE_WORDS = frozenset("canoe tiptoe mistletoe felloe".split())
_OE_ENDINGS = ("shoe", "oboe", "floe", "sloe")


def singular(word: str) -> str:
    """One plural fold. ``dinosaurs`` -> ``dinosaur``, ``batteries`` -> ``battery``."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if len(word) > 5 and word.endswith("oes") and word[:-1] not in _OE_WORDS \
            and not word[:-1].endswith(_OE_ENDINGS):
        return word[:-2]
    # "christmas" and "atlas" are not plurals (else the Wikipedia book is
    # asked for "christma"); neither are "ss", "us" and "is" words. An "as"
    # word folds only when the spelling list knows it without the "s"
    # ("ideas" -> "idea", "pizzas" -> "pizza"); with no list, it never folds.
    if len(word) > 3 and word.endswith("as"):
        return word[:-1] if _is_common(word[:-1]) else word
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _is_common(word: str) -> bool:
    """Is *word* an ordinary word in the shipped spelling list?"""
    from puffpai.catalogs import personal_words
    try:
        return personal_words.is_common_word(word)
    except Exception:  # noqa: BLE001 - no list: never fold
        return False


def _unglue(word: str) -> str:
    for suffix in _GLUED_SUFFIXES:
        if word.endswith(suffix) and len(word) >= len(suffix) + 3:
            return word[: -len(suffix)]
    return word


#: Letters with no accent to take off that still have a plain spelling.
_FOLD_LETTERS = {"ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ð": "d",
                 "Ð": "D", "þ": "th", "Þ": "Th", "ß": "ss", "ẞ": "SS", "æ": "ae",
                 "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ı": "i", "ħ": "h", "Ħ": "H",
                 "ŋ": "ng", "Ŋ": "Ng", "ŧ": "t", "Ŧ": "T", "ĸ": "k", "ſ": "s"}


#: Letters of other alphabets that look like a plain letter (Cyrillic,
#: Greek, Cherokee, Armenian and a few Latin look-alikes; the Unicode
#: confusables idea, cut to letters). Without this, "DeShawn" typed with a
#: Cyrillic "e" is read as "DShawn", so "deshawn soccer" goes out: such a
#: letter is read as the plain letter it looks like.
_LOOK_ALIKE = {
    # Cyrillic, lower case
    "а": "a", "в": "b", "с": "c", "е": "e", "ё": "e", "һ": "h", "і": "i", "ї": "i",
    "ј": "j", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "ѕ": "s", "т": "t",
    "у": "y", "х": "x", "ԁ": "d", "ԛ": "q", "ԝ": "w", "г": "r", "п": "n", "ь": "b",
    "ӏ": "l", "ɡ": "g", "з": "3", "ү": "y", "ʏ": "y", "ԍ": "g", "ѵ": "v", "ѡ": "w",
    # Cyrillic, upper case
    "А": "A", "В": "B", "С": "C", "Е": "E", "Ё": "E", "Н": "H", "І": "I", "Ї": "I",
    "Ј": "J", "К": "K", "М": "M", "О": "O", "Р": "P", "Ѕ": "S", "Т": "T", "Х": "X",
    "У": "Y", "Ү": "Y", "Ԁ": "D", "Ԛ": "Q", "Ԝ": "W", "З": "3", "Ӏ": "I", "Ь": "b",
    # Greek
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p",
    "τ": "t", "υ": "u", "χ": "x", "γ": "y", "ω": "w", "η": "n", "μ": "u",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M",
    "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    # Cherokee capitals that look like Latin capitals
    "Ꭺ": "A", "Ᏼ": "B", "Ꮯ": "C", "Ꭰ": "D", "Ꭼ": "E", "Ꮐ": "G", "Ꮋ": "H", "Ꭵ": "i",
    "Ꮶ": "K", "Ꮮ": "L", "Ꮇ": "M", "Ꮲ": "P", "Ꭱ": "R", "Ꮢ": "R", "Ꮪ": "S", "Ꭲ": "T",
    "Ꮩ": "V", "Ꮃ": "W", "Ꮤ": "W", "Ꮍ": "Y", "Ꮓ": "Z",
    # Armenian
    "օ": "o", "ս": "u", "հ": "h", "ո": "n", "ց": "g", "զ": "q",
    # Latin look-alikes NFKD leaves alone
    "ɑ": "a", "ɩ": "i", "ʟ": "L", "ɴ": "N", "ʀ": "R", "ᴀ": "A", "ᴄ": "C", "ᴅ": "D",
    "ᴇ": "E", "ᴋ": "K", "ᴍ": "M", "ᴏ": "O", "ᴘ": "P", "ᴛ": "T", "ᴜ": "U", "ᴠ": "V",
    "ᴡ": "W", "ᴢ": "Z",
}
#: The same letters read by their sound (a transliteration): "Maгia" may be
#: meant, and sent by a model, as "Magia" ("г" is "g"). Only letters whose
#: sound differs from their look are listed; the rest read as they look.
_BY_SOUND = {
    "в": "v", "В": "V", "г": "g", "Г": "G", "н": "n", "Н": "N", "р": "r", "Р": "R",
    "у": "u", "У": "U", "х": "kh", "Х": "Kh", "з": "z", "З": "Z", "п": "p", "П": "P",
    "с": "s", "С": "S", "и": "i", "И": "I", "л": "l", "Л": "L", "д": "d", "Д": "D",
    "б": "b", "Б": "B", "ж": "zh", "Ж": "Zh", "ф": "f", "Ф": "F", "ц": "ts", "Ц": "Ts",
    "ч": "ch", "Ч": "Ch", "ш": "sh", "Ш": "Sh", "щ": "shch", "Щ": "Shch", "ы": "y",
    "э": "e", "Э": "E", "ю": "yu", "Ю": "Yu", "я": "ya", "Я": "Ya", "й": "y", "Й": "Y",
    "ь": "", "ъ": "", "η": "i", "Η": "I", "β": "v", "Β": "V", "γ": "g", "ν": "n",
    "ρ": "r", "Ρ": "R", "χ": "ch", "Χ": "Ch", "μ": "m", "δ": "d", "λ": "l", "π": "p",
    "σ": "s", "ς": "s", "φ": "f", "θ": "th", "ξ": "x", "ψ": "ps", "ζ": "z",
}


def _invisible(ch: str) -> bool:
    """A format character that shows nothing: zero-width spaces and joiners,
    the soft hyphen, direction marks, the word joiner, the byte-order mark.
    Without this, "Li" + U+200B + "am" is read as two words, so "liam
    soccer" goes out: such a character is dropped, it never splits a word."""
    return unicodedata.category(ch) == "Cf" or ch in "­͏ᅟᅠㅤ"


#: A run of percent-encoded bytes ("%4C", "%C3%A9").
_PCT_RUN_RE = re.compile(r"(?:%[0-9A-Fa-f]{2})+")


def _pct_decoded(m: "re.Match[str]") -> str:
    try:
        got = urllib.parse.unquote(m.group(0), errors="strict")
    except UnicodeDecodeError:
        return m.group(0)
    # A decoded control character is a space, never glue.
    return "".join(ch if ch.isprintable() else " " for ch in got)


def decode_encoded(text: str) -> str:
    """*text* with web encodings read as the letters they stand for: HTML
    character references ("&#76;iam", "&#x4C;iam", "&amp;") and
    percent-encoding ("%4Ciam", "Zo%C3%AB"), up to three layers deep
    ("&#37;4C" is "%4C" is "L").

    Without this, "my son &#76;iam" and "my son %4Ciam" are read as "iam",
    so a model's "liam soccer" goes out. Only ever used to read words; never
    shown back."""
    text = str(text or "")
    for _ in range(3):
        before = text
        if "&" in text:
            text = html.unescape(text)
        if "%" in text:
            text = _PCT_RUN_RE.sub(_pct_decoded, text)
        if text == before:
            break
    return text


def fold_text(text: str, *, other_letters: str = "look") -> str:
    """*text* with accented letters spelled plainly ("Noémie" -> "Noemie",
    "Søren" -> "Soren", "Łukasz" -> "Lukasz", "Müller" -> "Muller").

    The word splitter reads only a-z and 0-9, so without folding "Noémie"
    splits into "no" and "mie", and "mie" goes out while the name is held
    as "no". Folding first keeps every
    word whole. Curly apostrophes stay (the callers read them). Only ever
    used to read words; never shown back.

    So that names typed with look-alike letters are caught too, the text is
    NFKC-normalised first, invisible format
    characters are dropped (never a split), and a letter of another alphabet
    that looks like a plain one is read as that letter ("DеShawn" with a
    Cyrillic "е" is "DeShawn"). *other_letters* says how such letters read:
    ``"look"`` (default) as the letter they look like, ``"sound"`` by their
    sound ("Maгia" is "Magia"), ``"drop"`` not at all ("Maia"). A letter with
    no plain reading is dropped from inside its word, never used to split
    it, so no piece of a name becomes a word of its own.

    Web encodings are read first (:func:`decode_encoded`), and direction
    controls (a right-to-left override, marks, isolates) are dropped like
    every other invisible format character.
    """
    text = decode_encoded(str(text or ""))
    if text.isascii():
        return text
    text = unicodedata.normalize("NFKC", text)
    out: list[str] = []
    upper_next = False
    for ch in unicodedata.normalize("NFKD", text):
        if unicodedata.combining(ch) or _invisible(ch):
            continue
        if ch.isascii() or ch in "’‘":
            if upper_next and ch.isalpha():
                ch = ch.upper()
            upper_next = False
            out.append(ch)
            continue
        plain = _FOLD_LETTERS.get(ch)
        if plain is None and other_letters != "drop":
            if other_letters == "sound":
                plain = _BY_SOUND.get(ch)
            if plain is None:
                plain = _LOOK_ALIKE.get(ch)
        if plain is not None:
            out.append(plain)
            upper_next = False
        elif ch.isalnum():
            # No plain spelling: dropped, the word stays one word ("Ωmar"
            # reads "Mar", never "mar" split off from a name).
            upper_next = upper_next or ch.isupper()
        else:
            out.append(" " if ch.isspace() or not ch.isprintable() else ch)
            upper_next = False
    return "".join(out)


def raw_words(text: str) -> list[str]:
    """Lowercase words, camel case split, in order. No filtering."""
    # A line break or tab is a space, never glue: "Grace" + line break +
    # "is" is two words, not "graceis".
    cleaned = "".join(ch if ch.isprintable() else " "
                      for ch in fold_text(str(text or "")[:MAX_TEXT * 2]))[:MAX_TEXT]
    return _WORD_RE.findall(_CAMEL_RE.sub(" ", cleaned).lower())


def goal_terms(text: str, extra: Iterable[str] = (), *, limit: int = MAX_TERMS) -> list[str]:
    """The words of a goal worth searching for, in the order they were typed.

    ``extra`` is a list of search words from elsewhere (the local model's
    suggestions); they go through exactly the same filter, after the owner's
    own words, so a model cannot slip filler or anything odd past it.
    """
    stop = stopwords()
    out: list[str] = []
    for source in (raw_words(text), [w for t in extra for w in raw_words(str(t))]):
        for word in source:
            word = _unglue(word)
            if word.isdigit() or len(word) < 3 or word in stop:
                continue
            word = singular(word)
            if word in stop or len(word) < 3 or word in out:
                continue
            out.append(word)
            if len(out) >= limit:
                return out
    return out


#: Words that say how a person talks about their plans, never what the bot
#: is about ("we have so much to do", "I'll need to be able to").
CONTEXT_STOPWORDS = frozenset("""getting able much come whole need needs want wants
lot lots stuff things named called next figure moved moving adopted adopting expecting born
engaged married marry marrying graduated graduating""".split())


#: Words in front of a news verb: "we are opening", "she's getting", "I'm
#: retiring", "we just moved", "he recently joined".
_NEWS_CUES = frozenset("""am is are was were be been being re m s ve just recently soon
finally""".split())


def news_words(text: str) -> set[str]:
    """Words the goal uses to tell her news, not to say what the bot is about.

    Grammar only (without it, a Wikipedia book is named "opening bakery
    recipe costing" from "my mom and I are opening a small bakery"): a
    life-event word (``personal_words.LIFE_EVENTS``) right after a form of
    "be" or "just"/"recently" ("are opening", "is getting", "just moved"),
    and any "-ed" word right after "just"/"recently" ("just joined"). The
    same word used as a thing ("learn the openings") is not news.
    """
    from puffpai.catalogs import personal_words
    words = raw_words(text)
    out: set[str] = set()
    for k in range(1, len(words)):
        prev, w = words[k - 1], words[k]
        if prev not in _NEWS_CUES:
            continue
        if w in personal_words.LIFE_EVENTS or (
                prev in ("just", "recently") and len(w) > 4 and w.endswith("ed")):
            out.add(w)
    return out


def search_terms(text: str, *, limit: int = MAX_TERMS) -> list[str]:
    """The goal's words worth searching for, with everything personal taken out.

    :func:`goal_terms`, minus the goal's hard personal words (relatives,
    pronouns, dates, numbers), minus its held words (names,
    brands, places, health words; see
    :mod:`puffpai.catalogs.personal_words`), minus words that only say how
    people talk about plans (:data:`CONTEXT_STOPWORDS`, and "plan" unless it
    names the bot). A health word that names the bot itself ("a vegan recipe
    bot") stays: the bot's subject is always searchable.

    These words may be searched on this computer. Anything that leaves the
    computer still goes through
    :func:`puffpai.security.outbound_query.screen` first.
    """
    from puffpai.catalogs import personal_words

    found = personal_words.find(text)
    subject = set()
    for word in found.subject.split():
        subject |= {word, singular(word)}
    blocked = set(found.hard)
    pieces = getattr(found, "pieces", {}) or {}
    for row in found.held:
        word = str(row.get("word") or "")
        # A sensitive word, or a season or holiday, that names the bot is
        # searched.
        if row.get("why") in ("health", "private", "date") and (
                {word, singular(word)} & subject):
            continue
        # Every form: "emmas" for "emma", and "rose" and "marie" for
        # "rose-marie", and each piece of a name as typed: "shawn" of
        # "DeShawn", "brien" of "O'Brien".
        keys = personal_words.word_keys(word)
        if row.get("why") in ("name", "brand", "place"):
            for piece in pieces.get(word, ()):
                keys |= personal_words.loose_forms(piece)
        if row.get("can_allow"):
            keys -= subject
        blocked |= keys
    blocked |= news_words(text)
    # Held names, brands and places, for the glued and cut-off forms
    # ("emmasbirthday").
    name_keys = {k for row in found.held if row.get("why") in ("name", "brand", "place")
                 for k in personal_words.word_keys(str(row.get("word") or ""))
                 if k.isalpha()} - subject
    # A goal that tells a private story keeps all its words but the
    # subject's (see personal_words.private_goal_words).
    blocked |= set(personal_words.private_goal_words(text)) - subject
    # Days of the year of more than one word ("memorial day", "new year"):
    # their words are not searched outside the subject.
    for seq in getattr(found, "date_seqs", ()) or ():
        if not all({x, singular(x)} & subject for x in seq):
            blocked |= {w for x in seq for w in (x, singular(x))} - subject
    # A listed word that is ordinary after the word in front of it wherever
    # she typed it ("chain lube", "study aids"): searched. The screen still
    # reads each phrase with its own words.
    raw = raw_words(text)
    touchy_here = {w for k, w in enumerate(raw)
                   if personal_words.sensitive_why(w, raw[k - 1] if k else "")}
    ordinary_here = {w for k, w in enumerate(raw) if k and w not in touchy_here
                     and personal_words.sensitive_why(w)
                     and not personal_words.sensitive_why(w, raw[k - 1])}
    ordinary_here |= {singular(w) for w in ordinary_here}
    out: list[str] = []
    # Hard words go out whole first, so the pieces of "10-year-old" or
    # "sister-in-law" ("year", "old", "law") never become search words.
    for word in goal_terms(personal_words.without_hard_tokens(text),
                           limit=max(limit, 1) * 4 + 40):
        forms = {word, singular(word)}
        if (personal_words.loose_forms(word) | forms) & blocked \
                or forms & CONTEXT_STOPWORDS:
            continue
        if (personal_words.sensitive_why(word) and not (forms & subject)
                and not (forms & ordinary_here and not forms & touchy_here)):
            continue
        if not (forms & subject) and personal_words.glued_to(word, name_keys):
            continue
        if word == "plan" and "plan" not in subject:
            continue
        if word not in out:
            out.append(word)
        if len(out) >= limit:
            break
    return out


def word_forms(text: str) -> set[str]:
    """Every content word of *text* plus its singular, for set matching."""
    stop = stopwords()
    forms: set[str] = set()
    for word in raw_words(text):
        word = _unglue(word)
        if word.isdigit() or len(word) < 3 or word in stop:
            continue
        forms.add(word)
        forms.add(singular(word))
    return forms


#: Endings that keep a word about the same thing: garden -> gardening,
#: telescope -> telescopes. A prefix with any other tail is a different word
#: (civic is not civicrm), so it does not match.
_SAME_THING_TAILS = frozenset({"s", "es", "ing", "ings", "er", "ers", "ed", "al",
                               "ic", "ical", "y", "ist", "ists", "ian", "ians",
                               "ery", "ly"})


def term_hits_word(term: str, word: str) -> bool:
    """Does a search term match one catalog word? Equal, plural, or the same word with a common ending."""
    if not term or not word:
        return False
    if word == term or singular(word) == term:
        return True
    return (len(term) >= 4 and word.startswith(term)
            and word[len(term):] in _SAME_THING_TAILS)


# ---------------------------------------------------------------------------
# Does a book, a website or a page fit what the owner asked for?
# ---------------------------------------------------------------------------
# A one-word match goes wrong: "tomato diseases" would offer "Special Report
# on Diseases of the Horse", "cross stitch tips" would offer "Cross
# Validated" (a statistics site), "car parts and recalls" a memoir on
# mechanical flight. Each shares ONE word with the request, and one
# shared word is not a match. The rule below knows no topic: it reads the
# owner's words as they typed them.

#: Punctuation and joining words that end a phrase the owner typed. A hyphen
#: inside a word ("cross-stitch") keeps the phrase together; a dash between
#: spaces ("Civic running - maintenance") ends it.
_PHRASE_BREAK_RE = re.compile(r"[,.;:!?()\[\]/\"“”&+]|\s[-–—]+\s")


def _content_word(word: str) -> str:
    """*word* as a search word (plural folded), or ``""`` when it is filler."""
    stop = stopwords()
    word = _unglue(word)
    if word.isdigit() or len(word) < 3 or word in stop:
        return ""
    word = singular(word)
    return "" if word in stop or len(word) < 3 else word


#: Joining words that tie a phrase to the next one: "diseases THAT affect
#: tomatoes", "parts FOR my car". Grammar, not topics.
_QUALIFIERS = frozenset(
    "that which who whom whose where when for of about on in into with "
    "from to at by like".split())
#: Joining words that list separate things: "car parts AND recalls".
_LISTING = frozenset({"and", "or"})
#: The bot itself. In "a music BOT that knows bands" the joining word is about
#: the bot, so "music" is not tied to "bands".
_BOT_NOUNS = frozenset(
    "bot bots buddy buddies helper helpers pal pals friend friends companion "
    "companions guide guides assistant assistants".split())


class _Run(list):
    """One typed phrase. ``partners`` is every word of the phrases tied to it
    (``None`` when it was named on its own); ``last`` is True for the last
    phrase of a tied group."""
    partners: list[str] | None = None
    last: bool = True


def _typed_runs(text: str) -> list[_Run]:
    """Each phrase the owner typed, in order, with the phrases tied to it.

    Two phrases in one clause are tied unless only a listing word joins them
    or the bot itself sits between them ("a music bot that knows bands"):
    "diseases that affect my car" is one group (disease / affect / car), so
    "diseases" alone is not what was asked for; "car parts and recalls" is
    two things (car parts, recalls), and "recalls" is whole on its own.
    """
    # A line break or tab is a space, never glue: "Grace" + line break +
    # "is" is two words, not "graceis".
    cleaned = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))[:MAX_TEXT]
    out: list[_Run] = []
    for chunk in _PHRASE_BREAK_RE.split(cleaned):
        runs: list[_Run] = []
        ties: list[bool] = []
        run = _Run()
        between: list[str] = []
        for word in _WORD_RE.findall(_CAMEL_RE.sub(" ", chunk).lower()):
            kept = _content_word(word)
            if kept:
                if not run and runs:
                    ties.append(not any(w in _BOT_NOUNS for w in between)
                                and (any(w in _QUALIFIERS for w in between)
                                     or not any(w in _LISTING for w in between)))
                run.append(kept)
                between = []
            else:
                if run:
                    runs.append(run)
                    run = _Run()
                between.append(word)
        if run:
            runs.append(run)
        start = 0
        for i in range(len(runs)):
            if i + 1 < len(runs) and ties[i]:
                continue
            group = runs[start:i + 1]
            if len(group) > 1:
                for r in group:
                    r.partners = [w for o in group if o is not r for w in o]
                    r.last = r is group[-1]
            start = i + 1
        out.extend(runs)
    return out


def request_runs(text: str) -> list[list[str]]:
    """The owner's words in the phrases they typed them in.

    A phrase is content words typed next to each other: "cross stitch tips"
    is one phrase (cross, stitch), "car parts and recalls" is two (car, part)
    and (recall). Nothing inside a phrase is ever split off on its own.
    """
    out: list[list[str]] = []
    for run in _typed_runs(text):
        if list(run) not in out:
            out.append(list(run))
    return out


def subject_words(text: str) -> list[str]:
    """The words of a request that name its subject, in order.

    In a phrase the last word is what kind of thing is wanted and the words
    before it say about what ("TOMATO diseases", "CAR parts"). The last
    phrase of a tied group names the subject in full ("problems that affect
    TOMATO PLANTS", "what problems does my CAR have"); a lone word earlier
    in a group does not ("diseases" in "diseases that affect cats").
    """
    out: list[str] = []
    for run in _typed_runs(text):
        if run.partners is not None and run.last:
            words = list(run)
        elif len(run) > 1:
            words = run[:-1]
        else:
            words = [] if run.partners is not None else list(run)
        for word in words:
            if word not in out:
                out.append(word)
    return out


def _same_word(term: str, word: str) -> bool:
    return term_hits_word(term, word) or term_hits_word(word, term)


def _holds(term: str, words: list[str]) -> bool:
    return any(_same_word(term, w) for w in words)


def _holds_phrase(run: list[str], words: list[str]) -> bool:
    size = len(run)
    return any(all(_same_word(run[j], words[i + j]) for j in range(size))
               for i in range(len(words) - size + 1))


def _heading_names(heading: str, word: str) -> bool:
    """Is a catalog subject heading about *word* and nothing narrower or
    else? "Tomatoes" and "Tomatoes -- Diseases" are about tomato; "Black
    Hills (S.D.)" is not about black."""
    main = re.split(r"--|[(,;:/]", str(heading or ""), maxsplit=1)[0]
    kept = [w for w in (_content_word(x) for x in raw_words(main)) if w]
    return len(kept) == 1 and _same_word(word, kept[0])


def match_strength(request: str, text: str, *,
                   subjects: str | Iterable[str] = "",
                   goal: Iterable[str] = (), shelves: Iterable[str] = (),
                   title: str | None = None) -> tuple[int, int]:
    """How well *text* (a title and its opening) fits *request*: ``(level, goal)``.

    ``level`` is 0 when it does not fit. It fits when it holds

    * 3 -- every content word of the request, or one of its typed phrases
      ("black hole", or the first two words of a longer one: "spark plug");
    * 2 -- a one-word phrase of the request ("recalls" in "car parts and
      recalls", "pterosaurs"), a whole thing the owner named. A word tied
      to other phrases ("diseases that affect cats", "parts for my car",
      "what problems does my car have") is not whole on its own: it fits
      only together with a word of those phrases;
    * 1 -- a subject word of the request (:func:`subject_words`: "tomato"
      in "tomato diseases" or "problems that affect tomato plants") as one
      of the catalog's own subject
      headings for it (*subjects*), together with one of the bot's own
      *goal* words or its *shelves* anywhere: "Tomato Culture" (subjects:
      Tomatoes; Gardening) for a garden bot. A title that merely holds the
      word is not enough ("Statements of Car Space" is not about cars for a
      space bot), and neither is a longer heading ("Black Hills" is not
      about black holes).

    *subjects* is the catalog's own subject headings for the item
    (Gutenberg's subjects, the words a website's list files it under; one
    string is one heading): they vouch for levels 3 and 2 the same way the
    title does. With *title*, a shelf word in the title fits
    too (level 1): the whole book is about that shelf ("Astronomy" for a
    space bot). Callers pass it only for the bot's own goal, never for a
    narrower request. A single shared word of a longer phrase never fits
    alone: "diseases", "cross" and "parts" are not the owner's subject.

    ``goal`` counts the bot's goal words the text holds, to break ties.
    """
    runs = request_runs(request)
    words = list(dict.fromkeys(w for run in runs for w in run))
    toks = raw_words(text)
    heads = [subjects] if isinstance(subjects, str) else [str(h) for h in subjects]
    subs = [w for h in heads for w in raw_words(h)]
    both = toks + subs
    own_goal = [g for g in dict.fromkeys(str(x).lower() for x in goal if str(x).strip())]
    goal_hits = sum(1 for g in own_goal if _holds(g, both))
    if not words:
        return (1, goal_hits)  # nothing to judge by
    level = 0
    if all(_holds(w, both) for w in words):
        level = 3
    else:
        for run in _typed_runs(request):
            if len(run) == 1:
                # A word tied to other phrases ("diseases that affect cats",
                # "parts for my car") fits only with a word of those phrases.
                if _holds(run[0], both) and (
                        run.partners is None
                        or any(_holds(w, both) for w in run.partners)):
                    level = max(level, 2)
                continue
            if any(_holds_phrase(seq, toks) or _holds_phrase(seq, subs)
                   for seq in (run, run[:2])):
                level = 3
                break
    if not level:
        shelf = [str(s).lower() for s in shelves if str(s).strip()]
        extra = [g for g in own_goal + shelf if g not in words]
        named = list(dict.fromkeys([words[0]] + subject_words(request)))
        if any(_heading_names(h, w) for h in heads for w in named) \
                and any(_holds(g, both) for g in extra):
            level = 1
        elif title is not None and shelf and any(
                s in raw_words(title) or any(singular(w) == s for w in raw_words(title))
                for s in shelf):
            level = 1
    return (level, goal_hits)


def fits_request(request: str, text: str, **kwargs) -> bool:
    """Does *text* fit what the owner asked for? See :func:`match_strength`."""
    return match_strength(request, text, **kwargs)[0] > 0


# ---------------------------------------------------------------------------
# What the bot is about, in the word that tells it apart
# ---------------------------------------------------------------------------
# Without this, a 3D printing bot is offered old type-specimen books (filed
# under "printing"), and "TED ideas" starts ticked for a wedding bot because
# her sentence said "come up with ideas". A book or pack is about
# the bot only when it names the word that tells the bot's subject apart.

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def subject_phrase(goal: str) -> str:
    """The bot's subject in a few words ("wedding planner", "3d printing"),
    or "" when the goal names none. A goal that is already short (a bot's
    kind) is its own subject."""
    text = " ".join(str(goal or "").split())[:MAX_TEXT]
    if not text:
        return ""
    try:
        from puffpai.catalogs import personal_words
        phrase = personal_words.subject_of(text)
    except Exception:  # noqa: BLE001 - no subject known: nothing is narrowed
        phrase = ""
    if not phrase and len(text.split()) <= 4:
        phrase = text.lower()
    return " ".join(_TOKEN_RE.findall(phrase.lower()))


#: Words of a goal that say what the bot does or how things are, not what
#: it is about ("help me TRAIN my NEW puppy", "LEARN to PLAY guitar").
_DOING_WORDS = frozenset("""
help helps train learn play playing keep pick choose find
start stay care make get track plan manage improve practice remember healthy
alive better new own good daily want need build
""".split())


def plain_subject(goal: str, limit: int = 2) -> str:
    """The bot's subject for any wording of a goal: :func:`subject_phrase`
    when it names one, else the goal's search words less what the bot does
    ("a bot to help me train my new puppy" gives "puppy", "learn to play
    guitar" gives "guitar"), at most *limit* words; "" when nothing is left.
    For finding books only: the outbound privacy screen keeps its own rule."""
    stop = stopwords()
    phrase = subject_phrase(goal)
    kept = [w for w in phrase.split() if w not in _BOT_NOUNS and w not in stop
            and w not in ("help", "helps")]
    if kept:
        return phrase if len(kept) == len(phrase.split()) else " ".join(kept)
    words = [w for w in search_terms(goal)
             if w not in _DOING_WORDS and w not in _BOT_NOUNS and not w.isdigit()]
    return " ".join(words[:limit])


def subject_marks(goal: str) -> list[str]:
    """The word(s) that tell the bot's subject apart: "wedding" for a wedding
    planner, "3d" for 3D printing, "chess" for a chess coach. In a subject
    the last word says what kind of thing it is and the words before it say
    about what. ``[]`` when the goal names no subject."""
    stop = stopwords()
    words = [w for w in subject_phrase(goal).split()
             if w not in stop and w not in _BOT_NOUNS and not w.isdigit()]
    return (words[:-1] if len(words) > 1 else words)[:3]


def mentions_subject(text: str, marks: Iterable[str]) -> bool:
    """Does *text* (a title, its catalog subjects) hold every mark? True
    when there are no marks. "3D" counts for "3d", "Weddings" for "wedding"."""
    marks = [m for m in marks if m]
    if not marks:
        return True
    tokens = set(_TOKEN_RE.findall(str(text or "").lower()))
    return all(any(term_hits_word(m, t) for t in tokens) for m in marks)


__all__ = ["MAX_TERMS", "EXTRA_STOPWORDS", "CONTEXT_STOPWORDS", "stopwords",
           "singular", "raw_words", "goal_terms", "search_terms", "word_forms", "term_hits_word", "request_runs",
           "subject_words", "subject_phrase", "plain_subject", "subject_marks", "mentions_subject",
           "match_strength", "fits_request"]
