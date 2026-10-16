"""The only way words about the owner's goal leave this computer.

BuildBot searches GitHub, Codeberg, picture libraries
and the web for parts that fit the owner's bot. Every word it sends starts
life in something she typed, so every phrase goes through :func:`screen`
first, and every searcher refuses anything that is not a :class:`Sendable`
(``is_sendable``) and puts it in a web address only through
:func:`url_quote`.

A phrase is sendable when every word passes the rules below or is a word of
the bot's **subject** ("wedding planner", "minecraft server"). Subject words
are exempt from rules 3 and 5 only. One failing word drops the **whole
phrase**, never half of it, so nothing goes out that she did not see:

1. hard words: relatives, pronouns ("near me"), months, weekdays, numbers
   (a digit-letter word such as "3d" only when it is a subject word), ages,
   years (news words such as "married" or "moved" are ordinary words; the
   sensitive ones are rule 5); and the time of her own plans: seasons,
   holidays, weekends and days such as "memorial day", held from her goal's
   personal list with the reason "date", never tickable, which leave only
   as words of the subject;
2. the goal's own personal list (names after "named"/"called", possessive
   names, "I'm <Name>", places and brands she typed), unless she ticked the
   word (``allowed_words``); also every piece of such a word as typed
   ("shawn" of "DeShawn", "brien" and "obrien" of "O'Brien"), and a name
   glued to more or cut short ("graceis", "zo" of "Zoë"), which a tick of
   the whole word does not let out. A tick lets out the whole word only:
   its pieces joined ("oconnor") or in order as a run ("mary kate"), never
   one piece alone, and a tick of a piece is no tick;
3. a proper noun by the SCOWL dictionary (languages and all-capital
   acronyms are fine), unless she ticked it;
4. her home town, its state, or a compass region when the goal holds a
   place. Never exempt, never tickable;
5. a sensitive word: health or diet, mental health, addiction, sex life,
   sexual orientation and gender identity, religion or belief, legal
   trouble, money hardship, violence and abuse, grief; and
   when the goal tells such a story about her (a listed sensitive word that
   is not the bot's subject), every word of that goal except the subject's
   (no list can name every word of a private story); and what
   the sentence's shape says is her story, whatever the word ("my wife
   deals with X", "since my X started", "my kid with X", "my X pills"; see
   ``personal_words._story_holds``); also "my brother has X", "my son who
   just started X", "because of X" and what follows "Context:"/"Note:"/"FYI:",
   and, in a goal about her or her people, a word that is private only
   there ("shingles", "lent", "pan"; ``personal_words.PERSONAL_SENSITIVE``),
   all held through the goal's personal list;
6. anything :class:`~puffpai.security.data_classifier.DataClassifier` calls
   a personal detail (this one applies to the subject too).

Then the phrase must already be plain: letters, digits and single spaces,
one to four words. Quotes, colons, search operators (``user:``, ``repo:``,
``in:readme``, ``stars:>5``), hyphens and anything else make it unsendable.

Fail closed: when a check cannot run, the phrase is held.

Nothing here logs a phrase or a goal. The held list is for the owner's own
screen only.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import contextvars
import re
import urllib.parse
import weakref
from dataclasses import dataclass, field
from typing import Iterable

#: What a sendable phrase looks like: 1 to 4 plain lower-case words.
PHRASE_RE = re.compile(r"[a-z0-9]+(?: [a-z0-9]+){0,3}")
#: What an incoming phrase may look like before it is lower-cased. ASCII
#: only, checked BEFORE lower-casing (the Kelvin sign lower-cases to "k").
_INPUT_RE = re.compile(r"[A-Za-z0-9]+(?: [A-Za-z0-9]+){0,3}")
#: A kind of thing written with a digit ("2d", "3d", "4d"), never a number.
_DIMENSION_RE = re.compile(r"[234]d")
#: No real search word is longer. Longer runs could carry a pasted secret.
MAX_WORD = 24
MAX_PHRASE = 80

_MINTING: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "puffpai_outbound_minting", default=None)
_TOKEN = object()
#: id(Sendable) -> (weak reference, phrase, source_hint, subject) as minted.
#: is_sendable compares the object with what was minted, so a Sendable
#: changed afterwards (object.__setattr__) is refused.
_MINTED: dict[int, tuple] = {}


def _forget(key: int, ref: "weakref.ref") -> None:
    got = _MINTED.get(key)
    if got is not None and got[0] is ref:
        _MINTED.pop(key, None)


@dataclass(frozen=True)
class Sendable:
    """A phrase that passed :func:`screen`. Only this module makes them."""

    phrase: str                   # ^[a-z0-9]+( [a-z0-9]+){0,3}$ and passed every rule
    source_hint: str = ""         # job id ("j3") or "subject"
    subject: bool = False

    def __post_init__(self) -> None:
        if _MINTING.get() is not _TOKEN:
            raise TypeError("A Sendable is made only by outbound_query.screen().")
        if not isinstance(self.phrase, str) or not PHRASE_RE.fullmatch(self.phrase) \
                or len(self.phrase) > MAX_PHRASE:
            raise ValueError("not a plain search phrase")

    # Immutable: a copy is the same object, so a copied Sendable stays sendable.
    def __copy__(self) -> "Sendable":
        return self

    def __deepcopy__(self, memo) -> "Sendable":
        return self

    def __reduce__(self):
        raise TypeError("A Sendable cannot be saved and loaded; screen the phrase again.")


def _mint(phrase: str, source_hint: str, subject: bool) -> Sendable:
    reset = _MINTING.set(_TOKEN)
    try:
        obj = Sendable(phrase=phrase, source_hint=str(source_hint or "")[:40],
                       subject=bool(subject))
    finally:
        _MINTING.reset(reset)
    key = id(obj)
    ref = weakref.ref(obj, lambda r, k=key: _forget(k, r))
    _MINTED[key] = (ref, obj.phrase, obj.source_hint, obj.subject)
    return obj


def is_sendable(obj: object) -> bool:
    """Was *obj* made by :func:`screen` in this process (and is it still plain)?"""
    if type(obj) is not Sendable:
        return False
    minted = _MINTED.get(id(obj))
    if minted is None or minted[0]() is not obj:
        return False
    # Exact types first: a str subclass could answer == with anything.
    if type(obj.phrase) is not str or type(obj.source_hint) is not str \
            or type(obj.subject) is not bool:
        return False
    return (str.__eq__(obj.phrase, minted[1]) is True
            and str.__eq__(obj.source_hint, minted[2]) is True
            and obj.subject is minted[3]
            and bool(PHRASE_RE.fullmatch(obj.phrase))
            and len(obj.phrase) <= MAX_PHRASE)


def url_quote(s: Sendable) -> str:
    """The phrase, ready for a web address. Refuses anything not screened."""
    if not is_sendable(s):
        raise TypeError("url_quote takes a Sendable from outbound_query.screen().")
    return urllib.parse.quote_plus(s.phrase)


@dataclass
class ScreenResult:
    sendable: list[Sendable] = field(default_factory=list)
    held: list[dict] = field(default_factory=list)   # {"phrase", "words": [{"word", "why"}]}
    subject: str = ""                                 # "" when the goal has none
    subject_notes: list[str] = field(default_factory=list)


# --- the rules -------------------------------------------------------------------

def saved_home() -> dict | None:
    """The household place saved on this computer (``puffpai home set``) in
    the shape the screen reads (``{"label": "Tucson, Arizona"}``), or None
    when none is saved.

    When a caller gives no ``home`` (without this, her home town in a goal
    is searched), the screen reads
    the saved one itself. Only the town-level label is read, never the
    position. Any error reading it is raised, so the screen fails closed.
    """
    from puffpai import home_location
    got = home_location.get_home()
    if not isinstance(got, dict):
        return None
    label = str(got.get("label") or "").strip()
    return {"label": label} if label else None


#: Words that are never search words of their own.
_NEVER_ALONE = frozenset({"next"})


class _Rules:
    """Everything the screen needs about one goal, worked out once."""

    def __init__(self, goal: str, home: dict | None, allowed_words: Iterable[str]):
        from puffpai.catalogs import goal_words, personal_words as pw

        if home is None:
            home = saved_home()
        self.gw = goal_words
        self.pw = pw
        found = pw.find(goal, home=home)
        self.subject = found.subject
        self.subject_notes = list(found.subject_notes)
        self.subject_forms: set[str] = set()
        for w in self.subject.split():
            self.subject_forms |= {w, goal_words.singular(w)}
        # A money word said as her own job in a money bot ("debt" in a
        # family budget bot) is a subject word too
        # (personal_words.subject_domain_words).
        self.subject_forms |= set(getattr(found, "subject_also", ()) or ())
        self.hard = set(found.hard)
        #: The goal names a time of her own plans: every time word and run
        #: of time words is held in every phrase.
        self.plan_time = bool(getattr(found, "plan_time", False))
        #: Days of the year of more than one word her goal names.
        self.date_seqs = [tuple(s) for s in (getattr(found, "date_seqs", ()) or ())]
        pieces = getattr(found, "pieces", {}) or {}
        #: word form -> (why, can_allow). Every form of a held word counts:
        #: its plural or possessive typed without the apostrophe ("emmas"),
        #: each part of a joined name ("rose-marie": "rose", "marie"), and
        #: each piece of it as typed ("DeShawn": "shawn"; "O'Brien": "brien",
        #: "obrien").
        self.held: dict[str, tuple[str, bool]] = {}
        #: Held names, brands and places (whole forms, three letters or
        #: more), for the glued and cut-off forms rule.
        self.name_keys: set[str] = set()
        for row in found.held:
            word = str(row.get("word") or "")
            status = (str(row.get("why") or "name"), bool(row.get("can_allow")))
            keys = pw.word_keys(word)
            if status[0] in ("name", "brand", "place"):
                # A tick lets out only the word she ticked, never a piece of
                # it on its own (fail closed).
                for p in pieces.get(pw._base(word), ()):
                    keys |= pw.loose_forms(p)
                self.name_keys |= {k for k in keys if len(k) >= 3 and k.isalpha()}
            if status[1]:
                # A tickable name or brand is never held where it IS the
                # subject (personal_words.find does the same for the word
                # itself), so the subject stays sendable.
                keys -= self.subject_forms
            for f in keys:
                old = self.held.get(f)
                # A form that can never be allowed stays that way.
                if old is None or (old[1] and not status[1]):
                    self.held[f] = status
        self.name_keys -= self.subject_forms
        # Words held though she did not type them ("estate" after "my late
        # aunt"): never tickable.
        for f in getattr(found, "quiet", ()) or ():
            self.held[str(f)] = ("private", False)
        # A tick lets out the word she ticked, whole: its pieces joined
        # ("O'Connor": "oconnor", "DeMarcus": "demarcus") and, as a run of
        # words in a phrase, its pieces in order ("mary kate"). Never one
        # piece on its own ("connor", "marcus", "mary"): otherwise a tick of
        # "o'connor" sends "connor".
        self.allowed: set[str] = set()
        self.allowed_seqs: list[tuple[str, ...]] = []
        #: Pieces of held words that are not a held word themselves: a tick
        #: of one ("marcus" of "DeMarcus") is not a tick of anything she saw.
        only_pieces: set[str] = set()
        rows = {pw._base(str(r.get("word") or "")) for r in found.held}
        rows |= {"".join(goal_words.raw_words(r)) for r in rows}
        for word, got in pieces.items():
            only_pieces |= {p for p in got if p not in rows}
        for a in allowed_words or ():
            tick = goal_words.raw_words(str(a)[:120])
            if not tick:
                continue
            joined = "".join(tick)
            if joined in only_pieces:
                continue
            self.allowed |= {joined, goal_words.singular(joined)}
            if len(tick) > 1 and tuple(tick) not in self.allowed_seqs:
                self.allowed_seqs.append(tuple(tick))
        self.home_singles, self.home_seqs = pw.home_words(home)
        self.holds_place = pw.goal_holds_place(goal, home=home)
        #: A goal that tells a private story (a sensitive word that is not
        #: the bot's subject): every goal word but the subject's, never
        #: tickable (rule 5 for the whole story).
        self.private_story: dict[str, str] = pw.private_goal_words(goal, home=home)
        from puffpai.security.data_classifier import DataClassifier
        self.classifier = DataClassifier()

    def forms(self, w: str) -> set[str]:
        return {w, self.gw.singular(w)}

    def word_why(self, w: str, prev: str = "", *, ticked: bool = False) -> str:
        """Why one lower-case word may not leave (``""`` when it may).

        *prev* is the word before it in the phrase (for "study aids").
        *ticked*: the word sits in a run of words that is a whole word she
        ticked ("mary kate" for "Mary-Kate"); only rules 2 and 3 give way."""
        forms = self.forms(w)
        # Wider forms for the goal's own lists only: "emmas" is "emma".
        loose = self.pw.loose_forms(w) | forms
        is_subject = bool(forms & self.subject_forms)
        if len(w) > MAX_WORD:
            return "too long"
        # Rule 1: hard by grammar, or hard in this goal.
        digits = any(ch.isdigit() for ch in w)
        if digits:
            # "2d", "3d" and "4d" are kinds of things ("3d printer"), never a
            # number about her, unless her goal typed that very word
            # (otherwise "Ender 3 printer" holds every "3d ..." phrase the
            # model writes).
            dimension = bool(_DIMENSION_RE.fullmatch(w)) and not (loose & self.hard)
            if (w.isdigit() or not is_subject) and not dimension:
                return "number"
        else:
            why = self.pw.is_hard_word(w)
            if why:
                return why
            if loose & self.hard:
                return "private detail"
        # Rule 1 for the time of her plans: a
        # time glued to plan words ("springbreak", "julytrip") anywhere, and,
        # when her goal names a time of her plans, every word for a time of
        # year, a month, a day or a holiday, whoever wrote it ("autumn" from
        # a model for her "fall"), unless it names the bot.
        if not is_subject and not digits:
            if self.pw.glued_time(w):
                return "date"
            if self.plan_time and loose & self.pw.TIME_WORDS:
                return "date"
        # "next" says when, never what (otherwise the Add page can send
        # "book tenth anniversary next"). Never a search word
        # unless it names the bot.
        if w in _NEVER_ALONE and not is_subject:
            return "date"
        # Rule 4: her home town and state, compass regions. Never exempt.
        if loose & self.home_singles:
            return "place"
        if self.holds_place and forms & self.pw.REGION_WORDS:
            return "region"
        # Rule 5 for the whole story: in a goal that tells a private story,
        # a word of that goal leaves only as a subject word.
        if not is_subject:
            story = next((self.private_story[f] for f in sorted(loose)
                          if f in self.private_story), "")
            if story:
                return story
        held = next((self.held[f] for f in sorted(loose) if f in self.held
                     and not self.held[f][1]), None) \
            or next((self.held[f] for f in sorted(loose) if f in self.held), None)
        if held is not None and not held[1]:
            why = held[0]
            # A sensitive word that names the bot is searched (rule 5
            # exemption), and so is a season or holiday the bot is about
            # ("a summer camp finder bot").
            if not (why in ("health", "private", "date") and is_subject):
                return why
        # Rule 2: the goal's own names, places and brands, unless ticked.
        # (personal_words.find never lists a subject word here.)
        if held is not None and held[1] and not (ticked or loose & self.allowed):
            return held[0]
        # A held name glued to another word or cut short ("graceis" from
        # "Grace" + line break + "is", "zo" of "Zoë").
        if held is None and not is_subject and not (ticked or loose & self.allowed) \
                and self._glued_name(w):
            return "name"
        # Rule 3: a proper noun, unless a language, a subject word, or ticked.
        # A name's plural or possessive typed without the apostrophe
        # ("emmas", "olivias") is one too.
        if not is_subject and not (ticked or loose & self.allowed) \
                and w not in self.pw.LANGUAGES and self._proper(w):
            return "name"
        # Rule 5: sensitive words (health and diet, mental health, addiction,
        # sex life and identity, belief, legal trouble, money hardship,
        # violence, grief), unless the word names the bot.
        if not is_subject:
            touchy = self.pw.sensitive_why(w, prev)
            if touchy:
                return touchy
        return ""

    def _ticked_runs(self, words: list[str]) -> set[tuple[int, int]]:
        """(start, end) of each run of *words* that is a whole word she ticked."""
        out: set[tuple[int, int]] = set()
        for seq in self.allowed_seqs:
            n = len(seq)
            for i in range(len(words) - n + 1):
                if tuple(words[i:i + n]) == seq:
                    out.add((i, i + n))
        return out

    def _glued_name(self, w: str) -> bool:
        """Is *w* a held name with more glued on ("graceis") or the start
        of one cut short ("zo" of "zoe")? (personal_words.glued_to)"""
        return bool(self.name_keys) and self.pw.glued_to(w, self.name_keys)

    def _proper(self, w: str) -> bool:
        """Rule 3's test: a name or place by the spelling list, also in a
        plural or possessive form the list does not know ("emmas")."""
        kind = self.pw.dict_kind(w)
        if kind == "proper":
            return True
        if kind == "common":
            return False
        return any(self.pw.dict_kind(f) == "proper" for f in self.pw.loose_forms(w) - {w})

    def phrase_why(self, words: list[str], phrase: str) -> list[dict]:
        out: list[dict] = []
        ticked: set[int] = set()
        for seq in self.allowed_seqs:
            n = len(seq)
            for i in range(len(words) - n + 1):
                if tuple(words[i:i + n]) == seq:
                    ticked |= set(range(i, i + n))
        for k, w in enumerate(words):
            why = self.word_why(w, words[k - 1] if k else "", ticked=k in ticked)
            if why:
                out.append({"word": w, "why": why})
        # Rule 5 for sensitive pairs ("passed away", "mental health"), unless
        # every word of the pair names the bot.
        for seq, cat in self.pw.sensitive_phrase_hits(words):
            if not all(self.forms(x) & self.subject_forms for x in seq):
                out.append({"word": " ".join(seq),
                            "why": "health" if cat == "health" else "private"})
        # Rule 1 for days of the year of more than one word that her goal
        # names ("memorial day", "new year"), unless the subject is made of
        # them.
        for seq in self.date_seqs:
            if self.pw.contains_seq(words, seq) \
                    and not all(self.forms(x) & self.subject_forms for x in seq):
                out.append({"word": " ".join(seq), "why": "date"})
        # ... and, when her goal names a time of her plans, any run of words
        # that names a time ("new years eve", "school year"), whoever wrote
        # it.
        if self.plan_time:
            for seq in self.pw.TIME_SEQS:
                if self.pw.contains_seq(words, seq) \
                        and not all(self.forms(x) & self.subject_forms for x in seq):
                    out.append({"word": " ".join(seq), "why": "date"})
                    break
        # Rule 2 for a held name split over words ("li am", "kay leigh"):
        # words next to each other that join into one.
        if self.name_keys:
            for i in range(len(words)):
                for j in range(i + 2, min(len(words), i + 4) + 1):
                    joined = "".join(words[i:j])
                    if joined in self.name_keys and not (i, j) in self._ticked_runs(words):
                        out.append({"word": " ".join(words[i:j]), "why": "name"})
        # Rule 4 for places and regions of more than one word.
        for seq in self.home_seqs:
            if self.pw.contains_seq(words, seq):
                out.append({"word": " ".join(seq), "why": "place"})
                break
        if self.holds_place:
            for seq in self.pw.REGION_PHRASES:
                if self.pw.contains_seq(words, seq):
                    out.append({"word": " ".join(seq), "why": "region"})
        # Rule 6: the personal-detail scan (the subject too).
        try:
            if self.classifier.scan_for_pii(phrase).has_pii:
                out.append({"word": "", "why": "private detail"})
        except Exception:  # noqa: BLE001 - fail closed
            out.append({"word": "", "why": "could not check"})
        return out


def _plain(phrase: object) -> list[str] | None:
    """The phrase's words when it is already plain, else ``None``."""
    if not isinstance(phrase, str):
        return None
    text = phrase.strip(" ")
    text = re.sub(r" {2,}", " ", text)
    if not text or len(text) > MAX_PHRASE or not _INPUT_RE.fullmatch(text):
        return None
    return text.lower().split(" ")


#: A phrase may not start or end with these: what is left of a phrase once a
#: held word was taken out ("to deals", "minecraft server old", "chunky
#: pattern aren", "worth pointing"). Never a fragment.
_NOT_FIRST = frozenset("""a an the and or nor of for to with in on at by from as into about
than worth later aren isn don doesn didn wasn weren couldn shouldn wouldn""".split())
_NOT_LAST = _NOT_FIRST | frozenset("""old new late next last little big small early own other
my our your his her their""".split())


def _broken(words: list[str]) -> bool:
    """Is this phrase a piece of one (see :data:`_NOT_FIRST`)? A phrase of
    one word is judged by the other rules."""
    return len(words) > 1 and (words[0] in _NOT_FIRST or words[-1] in _NOT_LAST)


def _shown(phrase: object) -> str:
    """The phrase as the owner's held list shows it (never logged)."""
    text = phrase if isinstance(phrase, str) else ""
    return "".join(ch for ch in text if ch.isprintable())[:120]


def screen(phrases: Iterable[str], *, goal: str, allowed_words: Iterable[str] = (),
           home: dict | None = None, source_hint: str = "") -> ScreenResult:
    """Sort *phrases* into what may leave (``sendable``) and what stays (``held``).

    ``goal`` is the owner's goal as typed (read here, never sent); ``home`` is
    the household place (``{"town", "state"}``) or ``None``; ``allowed_words``
    are held words she ticked on her own screen. A phrase equal to the bot's
    subject comes back with ``subject=True`` and ``source_hint="subject"``.
    Duplicates come back once.
    """
    if isinstance(phrases, str):
        phrases = [phrases]
    try:
        rules = _Rules(str(goal or ""), home, allowed_words)
    except Exception:  # noqa: BLE001 - fail closed: nothing leaves
        held = [{"phrase": _shown(p), "words": [{"word": "", "why": "could not check"}]}
                for p in (phrases or ())]
        return ScreenResult(sendable=[], held=held, subject="", subject_notes=[])
    result = ScreenResult(subject=rules.subject, subject_notes=list(rules.subject_notes))
    seen: set[str] = set()
    for phrase in phrases or ():
        words = _plain(phrase)
        if words is None:
            result.held.append({"phrase": _shown(phrase),
                                "words": [{"word": "", "why": "not a plain search phrase"}]})
            continue
        clean = " ".join(words)
        if clean in seen:
            continue
        seen.add(clean)
        if _broken(words):
            result.held.append({"phrase": _shown(phrase),
                                "words": [{"word": "", "why": "not a whole search phrase"}]})
            continue
        whys = rules.phrase_why(words, clean)
        if whys:
            result.held.append({"phrase": _shown(phrase), "words": whys})
            continue
        is_subject = bool(rules.subject) and clean == rules.subject
        result.sendable.append(_mint(clean, "subject" if is_subject else source_hint,
                                     is_subject))
    return result


def screen_words(words: Iterable[str], *, goal: str, allowed_words: Iterable[str] = (),
                 home: dict | None = None) -> list[str]:
    """The words of *words* (in order, lower case) that may leave as one
    search, for a caller that sends loose words rather than phrases (the Add
    page's and the chat's "look online", a live card's subject).

    Callers use this, not a private list of their own (a page that only
    takes away its own list can send "book jose baseball", "book rash arm",
    "book cope disorder"). Each word
    is screened on its own with *goal* (her words as typed), and every run
    of two to four words next to each other is screened too, so a pair rule
    ("passed away", "school year") holds each word of the pair. A word that
    is not a plain word, or any error, holds it (fail closed). Nothing is
    logged."""
    try:
        clean = [str(w or "").strip().lower() for w in words or ()]
        rules = _Rules(str(goal or ""), home, allowed_words)
    except Exception:  # noqa: BLE001 - fail closed: nothing leaves
        return []
    try:
        ok: list[bool] = []
        for w in clean:
            plain = _plain(w)
            ok.append(bool(plain) and len(plain) == 1
                      and not rules.phrase_why(plain, " ".join(plain)))
        for size in (2, 3, 4):
            for i in range(len(clean) - size + 1):
                run = clean[i:i + size]
                if not all(ok[i:i + size]):
                    continue
                plain = _plain(" ".join(run))
                if plain is None or rules.phrase_why(plain, " ".join(plain)):
                    for k in range(i, i + size):
                        ok[k] = False
    except Exception:  # noqa: BLE001 - fail closed
        return []
    out: list[str] = []
    for w, good in zip(clean, ok):
        if good and w not in out:
            out.append(w)
    return out


def subject_sendable(goal: str, *, home: dict | None = None) -> Sendable | None:
    """The bot's subject as a :class:`Sendable`, or ``None`` when it has none.

    The subject never waits behind a tick box. It is still checked against
    her home town, compass regions and the personal-detail scan (rules 4 and
    6), and it is ``None`` when any of those holds it.
    """
    try:
        rules = _Rules(str(goal or ""), home, ())
    except Exception:  # noqa: BLE001 - fail closed
        return None
    if not rules.subject:
        return None
    words = _plain(rules.subject)
    if words is None or rules.phrase_why(words, rules.subject):
        return None
    return _mint(" ".join(words), "subject", True)


__all__ = ["Sendable", "ScreenResult", "screen", "screen_words", "subject_sendable",
           "is_sendable",
           "url_quote", "saved_home", "PHRASE_RE", "MAX_WORD", "MAX_PHRASE"]
