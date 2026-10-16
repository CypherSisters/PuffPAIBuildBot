"""What a bot needs, read from the owner's own words, with no model at all.

"Finding out what a <kind> bot needs". Everything here is grammar,
never a topic list, so it works the same for any goal:

* :func:`explicit_items` reads the list the owner typed ("I'll need to be able
  to X, Y and Z", "a bot that tracks A, B and C", orders such as "Track oil
  changes ...", bullets) into one item per thing asked, in her words. The verb
  carries over ("track A, B and C" gives "track B").
* :func:`kind_words` is the bot's subject ("a wedding planner bot" ->
  ``wedding planner``). It is the same algorithm as
  ``puffpai.catalogs.personal_words.subject_of``, which is used
  instead whenever it is in the build.
* :func:`personal_words` marks what in the goal is about the person
  (relatives, pronouns, dates, ages, names, places, life events). It is the
  fallback for ``personal_words.find`` and is only ever used to keep words
  OUT of search phrases; nothing here decides what may leave the computer
  (``security.outbound_query`` does, and without it nothing leaves).
* :func:`rule_needs` is the whole rule-only answer; :func:`rule_phrases` makes
  2 to 3 word search phrases for a job name (the slow-brain path).
* :func:`is_learn` is the written "learn about" rule.

Ported from a design prototype (measured: 52 of
53 listed items recovered over 12 unrelated goals, 1.3 ms).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re
from typing import Iterable

from puffpai.catalogs import goal_words

# -- grammar lists ------------------------------------------------------------
RELATIVES = frozenset("""sister sisters brother brothers sibling siblings mom moms mum mother mothers
dad dads father fathers parent parents son sons daughter daughters wife wives husband husbands
partner partners spouse fiance fiancee fiancé fiancée boyfriend girlfriend nephew nephews niece
nieces aunt aunts uncle uncles cousin cousins grandma grandpa grandmother grandfather grandson
granddaughter grandkids grandchildren stepson stepdaughter stepmom stepdad inlaws in-laws bride
groom friend friends neighbor neighbors neighbour neighbours roommate roommates coworker
coworkers boss""".split())
#: Audience words say who the bot is for and identify nobody.
AUDIENCE = frozenset("kid kids child children family families beginner beginners teen teens".split())
PRONOUNS = frozenset("""i i'm i'll i've i'd me my mine myself we we're we'll we've us our ours
ourselves you your yours he him his she her hers they them their theirs it's""".split())
#: Verbs and fillers that say how a person talks, never what the bot needs.
FILLER = frozenset("""able getting get gets got going go goes gonna want wants wanted wanting need
needs needed would could should will can might must like likes love loves really just also so
very much lots lot whole entire every some any thing things stuff way ways kind sort bit little
new next last this that these those here there now soon today tonight tomorrow yesterday week
weeks weekend month months year years day days time times always usually often sometimes
build building make making made do doing done have has had having use using used run runs
running keep keeps want to something anything everything someone anyone help helps named called
one two three four five six seven eight nine ten eleven twelve dozen couple""".split())
#: Life-event words: the person's situation, not the bot's job.
LIFE_EVENTS = frozenset("""married marry marrying engaged moved moving move adopted adopting
retired retiring divorced pregnant expecting graduated graduating born died passed""".split())
MONTHS = frozenset("""january february march april june july august september october november
december jan feb mar apr jun jul aug sep sept oct nov dec monday tuesday wednesday thursday
friday saturday sunday mondays tuesdays wednesdays thursdays fridays saturdays sundays""".split())
SEASON_CUES = frozenset("next this last in every each".split())
SEASONS = frozenset("spring summer autumn fall winter".split())
#: Joining words that start a place: "in Tucson", "over Dayton".
PLACE_CUES = frozenset("in at near from to over around outside visiting".split())
NAME_CUES = frozenset("named called".split())
#: Languages are never a personal detail by themselves.
LANGUAGES = frozenset("""english spanish french german italian portuguese dutch russian chinese
mandarin cantonese japanese korean arabic hindi urdu bengali punjabi turkish greek hebrew polish
swedish norwegian danish finnish czech hungarian romanian ukrainian vietnamese thai indonesian
malay tagalog swahili persian farsi latin irish welsh icelandic catalan basque""".split())

#: The nouns that name the bot itself (the subject rule).
BOT_NOUNS = frozenset("bot app helper buddy assistant tutor coach".split())
#: A generic word dropped when it directly precedes the final bot noun.
GENERIC_BOT_WORDS = frozenset("helper assistant buddy app".split())
#: The subject starts after the nearest of these.
SUBJECT_CUES = frozenset("a an my the build make want need".split())
#: Joining verbs: "a bot to be her coach" says what the bot is, not what it is about.
COPULAS = frozenset("be is are am was were been being become becomes act acts".split())
#: Words that only say "a program" inside the subject ("a bot to be ..."): never its topic.
PROGRAM_WORDS = frozenset("bot bots chatbot chatbots app apps".split())
#: Words that name the bot's role, not what it is about ("tutor" in "a tutor
#: bot"). A subject made only of these (and joining words) is not a subject.
ROLE_WORDS = frozenset("""bot bots chatbot chatbots app apps coach coaches tutor tutors helper
helpers assistant assistants buddy buddies companion companions guide guides mentor mentors
teacher teachers trainer trainers pal pals friend friends keeper keepers expert experts advisor
advisors adviser advisers""".split())

_DICT: dict[str, str] | None = None


def _dict_path() -> str:
    from puffpai.catalogs import live
    return os.path.join(live.repo_root(), "config", "wordlists", "en_US.dic")


def _dict() -> dict[str, str]:
    """SCOWL word list (the personal_words file) as word -> common|proper; {} when absent."""
    global _DICT
    if _DICT is None:
        out: dict[str, str] = {}
        try:
            with open(_dict_path(), encoding="utf-8", errors="replace") as fh:
                next(fh, None)
                for line in fh:
                    w = line.split("/", 1)[0].strip()
                    if not w or not w.isalpha():
                        continue
                    low = w.lower()
                    kind = "proper" if w[0].isupper() and not (len(w) > 1 and w.isupper()) \
                        else "common"
                    if out.get(low) != "common":
                        out[low] = kind
        except OSError:
            out = {}
        _DICT = out
    return _DICT


def dict_kind(word: str) -> str:
    """``common``, ``proper`` or ``unknown`` (always unknown without the list)."""
    w = word.lower().strip("'")
    if w.endswith("'s"):
        w = w[:-2]
    d = _dict()
    if w in d:
        return d[w]
    return d.get(goal_words.singular(w), "unknown")


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’&-]*|&")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
#: A full stop after these is not the end of a sentence ("Dr. Okafor"):
#: without this, a job is cut to "Keeping my appointments with Dr".
ABBREVIATIONS = frozenset("""dr mr mrs ms mx prof st mt jr sr vs etc eg ie no approx dept
ave rd fr rev sgt capt lt col gen gov sen rep""".split())


def _tokens(text: str) -> list[str]:
    return [t.replace("’", "'") for t in _TOKEN.findall(str(text or ""))]


def sentences(text: str) -> list[str]:
    out: list[str] = []
    glue = False
    for piece in _SENT.split(str(text or "")):
        if not piece.strip():
            continue
        if glue and out:
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
        last = (piece.split() or [""])[-1]
        # "Dr." or a single initial ("J.") does not end the sentence.
        glue = bool(re.fullmatch(r"[A-Za-z]+\.", last)) and (
            len(last) == 2 or last[:-1].lower() in ABBREVIATIONS)
    return out


def personal_words(goal: str, *, home: dict | None = None) -> dict[str, str]:
    """Every word of *goal* about the person -> ``hard:<why>`` or ``held:<why>``."""
    out: dict[str, str] = {}

    def mark(word: str, why: str) -> None:
        w = word.lower().strip("'")
        if w.endswith("'s"):
            w = w[:-2]
        for key in {w, goal_words.singular(w)}:
            if key and (key not in out or out[key].startswith("held") and why.startswith("hard")):
                out[key] = why

    try:
        from puffpai.security.data_classifier import health_terms
        for term in health_terms(str(goal or "")):
            for w in _tokens(term):
                mark(w, "held:health")
    except Exception:  # noqa: BLE001 - the classifier is a backstop, never required
        pass
    for place in ((home or {}).get("town"), (home or {}).get("state")):
        for w in _tokens(str(place or "")):
            mark(w, "held:place")
    for sent in sentences(goal):
        toks = _tokens(sent)
        for i, tok in enumerate(toks):
            low = tok.lower()
            base = low[:-2] if low.endswith("'s") else low
            prev = toks[i - 1].lower() if i else ""
            if base in RELATIVES:
                mark(base, "hard:relative")
            elif base in PRONOUNS:
                mark(base, "hard:pronoun")
            elif base in MONTHS or (tok == "May" and i > 0):
                mark(base, "hard:date")
            elif base in SEASONS and prev in SEASON_CUES:
                mark(base, "hard:date")
            elif re.fullmatch(r"\d+", base):
                mark(base, "hard:number or age")
            elif re.fullmatch(r"\d+-?(year|yr)s?-?old", base):
                mark(base, "hard:age")
            if not tok[0].isalpha():
                continue
            if prev in NAME_CUES and tok[0].isupper():
                j = i
                while j < len(toks) and toks[j][0].isupper():
                    # "called The Corner Lot": the name's words, not its "The".
                    if toks[j].lower() not in ("a", "an", "the"):
                        mark(toks[j], "hard:name")
                    j += 1
            if tok[0].isupper() and low.endswith("'s") and base not in RELATIVES:
                mark(base, "hard:name")
            if prev in ("i'm", "im") or (i > 1 and toks[i - 2].lower() == "i" and prev == "am"):
                if tok[0].isupper():
                    mark(base, "hard:name")
            if tok[0].isupper() and base not in ("i", "i'm", "i'll", "i've", "i'd"):
                if base in LANGUAGES or (len(tok) > 1 and re.sub(r"s$", "", tok).isupper()):
                    continue  # a language or an acronym is not a person
                kind = dict_kind(base)
                mid = i > 0
                if mid and prev in PLACE_CUES:
                    mark(base, "held:place")
                    j = i + 1
                    while j < len(toks) and toks[j][0].isupper():
                        mark(toks[j], "held:place")
                        j += 1
                elif mid and kind != "common":
                    mark(base, "held:name")
                elif mid and i + 1 < len(toks) and toks[i + 1][0].isupper():
                    mark(base, "held:name")
                elif mid and toks[i - 1][0].isupper() and toks[i - 1].lower() != "i":
                    mark(base, "held:name")
                elif not mid and kind == "proper":
                    mark(base, "held:name")
    return out


def hard_words(goal: str, *, home: dict | None = None) -> list[str]:
    """The goal's hard personal words, pronouns left out, in goal order."""
    marks = personal_words(goal, home=home)
    out: list[str] = []
    for tok in _tokens(goal):
        w = tok.lower()
        w = w[:-2] if w.endswith("'s") else w
        why = marks.get(w, "")
        if why.startswith("hard:") and why != "hard:pronoun" and w not in out:
            out.append(w)
    return out


def place_words(goal: str, *, home: dict | None = None) -> set[str]:
    """Words the goal marks as a place (after a place cue) plus her town and state."""
    out: set[str] = set()
    for place in ((home or {}).get("town"), (home or {}).get("state")):
        out |= {w.lower() for w in _tokens(str(place or ""))}
    for sent in sentences(goal):
        toks = _tokens(sent)
        for i, tok in enumerate(toks):
            if i and toks[i - 1].lower() in PLACE_CUES and tok[0].isupper():
                j = i
                while j < len(toks) and toks[j][0].isupper():
                    out.add(toks[j].lower())
                    j += 1
    return out


# -- the bot's subject ----------------------------------------------------------
def kind_words(goal: str, *, home: dict | None = None) -> list[str]:
    """The words that name the kind of bot ("a wedding planner bot" -> wedding planner).

    1. In the first sentence with a bot noun, take the LAST bot noun and up to
       4 words before it, stopping after the nearest a|an|my|the|build|make|
       want|need.
    2. Drop a generic word (helper, assistant, buddy, app) right before the
       final bot noun; drop filler, hard-personal words and place words.
    3. Lower-case, ``[a-z0-9 &]`` only, ``&`` -> ``and``, at most 4 words.
    """
    marks = personal_words(goal, home=home)
    places = place_words(goal, home=home)
    for sent in sentences(goal):
        toks = _tokens(sent)
        lows = [t.lower() for t in toks]
        # A capitalised bot noun inside a sentence is a product's name ("Dev
        # put Home Assistant on a box" would give the kind "put home"), not
        # the kind of bot she wants.
        nouns = [i for i, w in enumerate(lows) if w in BOT_NOUNS
                 and not (i > 0 and toks[i][:1].isupper() and toks[i - 1][:1].isupper())]
        if not nouns:
            continue
        end = nouns[-1]
        start = end
        while start > 0 and end - start < 4 and lows[start - 1] not in SUBJECT_CUES:
            start -= 1
        words = toks[start:end]
        if words and words[-1].lower() in GENERIC_BOT_WORDS:
            words = words[:-1]
        kept: list[str] = []
        for i, tok in enumerate(words):
            low = tok.lower()
            base = low[:-2] if low.endswith("'s") else low
            why = marks.get(base, "")
            if base in ("a", "an", "the") or base in FILLER and base not in LANGUAGES:
                continue
            if base in COPULAS or base in PROGRAM_WORDS:
                continue  # "a bot to be her coach": joining words, not the subject
            if why.startswith("hard:") or base in RELATIVES or base in PRONOUNS:
                continue
            if tok[0].isupper() and low.endswith("'s"):
                continue  # a possessive name
            if re.fullmatch(r"\d+", base) or base in MONTHS or base in LIFE_EVENTS:
                continue
            if base in places:
                continue
            clean = re.sub(r"[^a-z0-9 &]", "", base).replace("&", "and").strip()
            if clean:
                kept.extend(clean.split())
        return kept[-4:] if len(kept) > 4 else kept
    return []


def subject_of(goal: str, *, home: dict | None = None) -> str:
    return " ".join(kind_words(goal, home=home))


def usable_subject(subject: str) -> str:
    """*subject* when it says what the bot is about, else "".

    Joining words and "bot"/"app" are dropped ("bot be" came from "a bot to
    be her coach"); a subject left with only role words ("tutor", "coach")
    says what the bot is but not what it is about, so it is not a subject to
    search for (the brain's kind of bot is used instead)."""
    words = [w for w in str(subject or "").lower().split()
             if w not in COPULAS and w not in PROGRAM_WORDS]
    if not any(w not in ROLE_WORDS for w in words):
        return ""
    if len(words) >= 2 and words[0] in VERBS and words[1] in PARTICLES:
        # "set up home" from "Dave set up Home Assistant": a thing someone
        # did, read in front of a product's name, is no kind of bot (it sent
        # "set thermostat display").
        return ""
    return " ".join(words)


# -- the owner's own list ---------------------------------------------------------
VERBS = frozenset("""track find plan log remind show tell quiz help keep make schedule split save
post look roll drill correct teach practice set pay see use grow take come figure follow get
manage organize organise create add list record suggest compare book send share play learn write
read answer check watch water feed walk cook order sort count measure calculate generate store
collect search notify alert explain identify recommend budget estimate convert translate
design draw build print scan import export sync monitor measure study review rate rank
invite seat assign cost price photograph do work join sell bake train lift clean fix repair
map chart tune split sing jot note memorise memorize drill revise plant harvest prune
journal give hold understand remember know handle update edit pick choose decide
prepare pack text email call message brainstorm draft warn divide""".split())
#: Small joining words that follow a verb ("work through", "look up", "keep track").
PARTICLES = frozenset("""through up down out on off over about into with in at for from by
around along back away against""".split())
#: "has a metronome", "is a place for": what the bot has, not something it does.
HAS_LEAD = frozenset("has have had is are gives give includes include offers offer".split())
ARTICLES = frozenset("a an the some".split())
_CUE = re.compile(
    r"(?:\bi(?:'ll| will)? need (?:it |the bot |you )?to (?:be able to )?"
    r"|\b(?:be able to|able to)\b"
    r"|\bi (?:want|would like|need) (?:it|the bot|a bot|an app) (?:that|to|which) "
    r"|\b(?:bot|app|assistant|helper|tutor|coach|planner)s? (?:that|which|to|who) (?:helps? (?:me |us )?(?:with |to )?)?"
    r"|\bi (?:want|need|would like) to (?!build|make|create|have|get)"
    # "I want reminders on when to water": what she wants, named directly
    r"|\bi (?:want|need|would like) (?!(?:a|an|the|my|our|this|that|it|to|some|you|one|"
    r"something|someone|help|him|her|them|us|me)\b)"
    r"|\bthat (?:helps? (?:me |us )?(?:with |to )?|can |will |should )"
    r"|\bhelp (?:me |us )?(?:with |to )?(?!build|make|create)"
    r"|\bit should\b|\bshould be able to\b"
    r"|\b(?:bot|app|assistant|helper|tutor|coach|planner|buddy)s? (?:with|that has|which has) "
    # "a study buddy bot: flashcards, a quiz mode and ...": the list after the colon
    r"|\b(?:bot|app|assistant|helper|tutor|coach|planner|buddy)s?\s*:\s*"
    # "I'd also like to see ...", "we'd really love to ..."
    r"|\b(?:i|we)(?:'d| would) (?:also |really |just )?(?:like|love|want) to "
    r"(?!build|make|create|have|get)"
    # "We'd import our statements, set limits ...": what she would do with it
    r"|^(?:we|i|it)(?:'d| would|'ll| will| could| can) (?:also |then |just )?(?=(?:%s)\b)"
    # "a dashboard that shows the thermostat, the porch light and ...": what it does
    r"|\b(?:that|which) (?=(?:%s)(?:s|es)?\b))"
    % ("|".join(sorted(VERBS)), "|".join(sorted(VERBS))),
    re.I)
#: "a band bot for our setlists, who has learned which songs, a metronome ...":
#: a list after "<bot> for" (two items or more; "a bot for my grandson" is who
#: it is for, never a job).
_FOR_CUE = re.compile(r"\b(?:bot|app|assistant|helper|tutor|coach|planner|buddy)s? for ", re.I)
#: "..., so reminders would really help": the thing she says would help.
_WOULD_HELP = re.compile(
    r"(?:^|[,;]\s*(?:so\s+|and\s+|but\s+)?|\b(?:so|and|but)\s+)"
    r"((?:[A-Za-z][\w'-]*\s+){0,3}[A-Za-z][\w'-]*)"
    r"\s+would\s+(?:really\s+|also\s+|be\s+(?:a\s+)?(?:big\s+|great\s+|real\s+)?)?"
    r"(?:help|helpful|great|nice|useful|good)\b", re.I)
#: "like the broken knob on ...", "such as ...": an example of the item before
#: it, never a job of its own.
_EXAMPLE_LEAD = re.compile(r"^(?:like|such as|say|e\.?g\.?|for example|for instance|including)\b",
                           re.I)
#: "..., who came over from ...": a relative clause about the name before it.
_RELATIVE_LEAD = re.compile(r"^(?:who|whom|whose|which|where)\b", re.I)
WH_WORDS = frozenset("whether what which who how when where".split())
#: "keep track of A, B and C": the words carried to B and C with the verb.
_CARRY_TAIL = re.compile(r"^(?:track|tabs|note|notes|an eye|care)\s+(?:of|on)\b", re.I)
#: "helps me", "helps with": the lead-in, not the job ("a bot that helps me track X").
_HELP_LEAD = re.compile(r"^(?:helps?|helping)\s+(?:me\s+|us\s+|you\s+)?(?:with\s+|to\s+)?", re.I)
#: "a way to track X", "something to log Y": the job is the verb after it.
_WAY_LEAD = re.compile(r"^(?:(?:a|an|some)\s+)?(?:way|place|tool|spot|something)\s+to\s+", re.I)
_BOT_WORDS = re.compile(r"\b(?:bot|bots|chatbot|assistant)\b", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•+]|\d+[.)])\s+(.*\S)\s*$")


_FLAGS: dict[str, str] = {}


def _word_flags(word: str) -> str:
    """The word list's flags for *word* ("name" -> "AGDS"); "" when absent."""
    if not _FLAGS:
        try:
            with open(_dict_path(), encoding="utf-8", errors="replace") as fh:
                next(fh, None)
                for line in fh:
                    head, _, flags = line.strip().partition("/")
                    if head.isalpha() and head.islower():
                        _FLAGS[head] = flags
        except OSError:
            pass
        _FLAGS.setdefault("", "")
    return _FLAGS.get(str(word or "").lower(), "")


def _own_verb(words: list[str], verb: str) -> bool:
    """"rolls dice, builds encounters, names characters": after a carried verb
    that ends in -s, an item that starts with another -s word the word list
    knows as a doing word ("name" has -ing and -ed forms) has its own verb."""
    if not verb.endswith("s") or len(words) < 2 or words[1].lower() in ("and", "or", "of"):
        return False
    first = words[0].lower()
    if not first.endswith("s") or first in PRONOUNS:
        return False
    flags = _word_flags(first[:-1]) or (_word_flags(first[:-2]) if first.endswith("es") else "")
    return "G" in flags and "D" in flags


def _parts(span: str) -> list[str]:
    """Commas, semicolons and colons split; "and" splits only before a doing
    word ("track A and remind me of B", "organizing X and figuring out Y")."""
    # A comma or colon inside a number ("$9,000", "10:30") splits nothing.
    pieces = [p.strip() for p in re.split(r"\s*(?:;|[,:](?!\d))\s*(?:and\s+|or\s+)?", span)
              if p.strip()]
    out: list[str] = []
    for piece in pieces:
        toks = piece.split(" ")
        start = 0
        for i in range(1, len(toks) - 1):
            nxt = toks[i + 1].lower()
            if toks[i].lower() == "and" and (
                    nxt in VERBS
                    or nxt.endswith("ing") and _lead_is_verb(nxt)
                    and toks[start].lower().endswith("ing") and _lead_is_verb(toks[start])):
                out.append(" ".join(toks[start:i]))
                start = i + 1
        out.append(" ".join(toks[start:]))
    return [p for p in out if p.strip()]


def _split_items(span: str) -> list[str]:
    span = re.sub(r"\s+", " ", span).strip(" .!?")
    parts = _parts(span)
    out: list[str] = []
    verb = ""
    for p in parts:
        p = re.sub(r"^(?:and|or|also|then)\s+", "", p, flags=re.I)
        if _EXAMPLE_LEAD.match(p):
            continue   # an example of the item before it, not a job
        if out and _RELATIVE_LEAD.match(p) and re.search(r"[A-Z][\w'’-]*$", out[-1]):
            continue   # "my grandfather Stan, who came over ...": about a name
        caps = p.split()
        if out and caps and len(caps) <= 3 and all(w[:1].isupper() for w in caps) \
                and not _lead_is_verb(caps[0]):
            continue   # "Scranton, Pennsylvania": the end of a place name
        bare = p
        p = _WAY_LEAD.sub("", _HELP_LEAD.sub("", p)).strip()
        # "help me understand X", "a way to track X": the word after the lead is
        # her own verb, so the verb of the item before is never put in front.
        led = p != bare.strip()
        words = p.split()
        if not words:
            continue
        if words[0].lower() in HAS_LEAD and len(words) > 1 \
                and words[1].lower() not in PRONOUNS:
            # "has a timer" is a thing it has; "give her quizzes" is a job.
            words = words[1:]
            p = " ".join(words)
            verb = ""
        first = words[0].lower()
        second = words[1].lower() if len(words) > 1 else ""
        # "adding mods, restarting it when it freezes, and stopping ...": after
        # a carried -ing verb, an item that starts with its own -ing word has
        # its own verb (not "Adding restarting it when it freezes").
        parallel = (verb.split(" ")[0].endswith("ing") and first.endswith("ing")
                    and len(first) > 5 and second not in ("and", "or", "of"))
        if _lead_is_verb(first) or led or parallel or _own_verb(words, verb.split(" ")[0]):
            verb = first
            tail = _CARRY_TAIL.match(" ".join(words[1:]))
            if tail:
                verb = f"{first} {tail.group(0).lower()}"   # "keeping track of"
            out.append(p)
        elif second in PARTICLES and first not in ARTICLES and not first.endswith("s"):
            # "work through chess puzzles": a verb this list does not know yet.
            verb = ""
            out.append(p)
        elif verb and (first not in ARTICLES or verb.endswith("s")) \
                and (len(words) <= 6 or " " in verb or first in WH_WORDS):
            # "shows the thermostat, the porch light and whether the door is
            # open": each one is something it shows.
            out.append(f"{verb} {p}")
        else:
            out.append(p)
    return out


def explicit_items(goal: str) -> list[str]:
    """The things the owner listed, in her words (personal words still inside)."""
    text = str(goal or "")
    bullets = [m.group(1) for m in map(_BULLET.match, text.splitlines()) if m]
    if len(bullets) >= 2:
        return bullets
    items: list[str] = []
    for sent in sentences(text):
        first = (sent.split() or [""])[0].lower().strip(",")
        m = _CUE.search(sent)
        found: list[str] = []
        if first in VERBS and not _BOT_WORDS.search(sent):
            found = _split_items(sent)
        elif m is not None:
            span = re.sub(r"^(?:me|us)\s+", "", sent[m.end():].strip(), flags=re.I)
            found = _split_items(_HELP_LEAD.sub("", span))
        f = _FOR_CUE.search(sent)
        if not found and f is not None:
            listed = _split_items(sent[f.end():])
            if len(listed) >= 2:
                found = listed
        w = _WOULD_HELP.search(sent)
        if w is not None and not _BOT_WORDS.search(w.group(1)):
            thing = re.sub(r"^(?:so|and|but)\s+", "", w.group(1).strip(), flags=re.I)
            lead = thing.split()[0].lower() if thing.split() else ""
            if lead and lead not in PRONOUNS and lead not in ("it", "that", "this", "which", "what"):
                found.append(thing)
        for it in found:
            if not it.split() or _BOT_WORDS.search(it):
                continue
            if it.split()[0].lower() in COPULAS:
                continue  # "be her coach": what the bot is, not a job it does
            if len(it.split()) >= 2 or it.split()[0].lower() not in VERBS:
                items.append(it)
    return items


def _lead_is_verb(word: str) -> bool:
    lead = word.lower()
    return (lead in VERBS
            or lead.endswith("ing") and (lead[:-3] in VERBS or lead[:-3] + "e" in VERBS)
            or lead.endswith("s") and lead[:-1] in VERBS)


#: Words that join or qualify, never what a job is about ("already", "before",
#: "whether"): without this, a search reads "already figuring look".
GRAMMAR = frozenset("""already before after whether what which who whom whose how many much
where when why each every more most less least than then still yet even ever never again
also only just about while until since because though although worth plus later too very
safely easily quickly really""".split())


#: Words that stand for a thing already named ("pointing it at", "restarting
#: it"): the -ing word before them is a doing word.
_OBJECTS = frozenset("it them him her this that these those".split())


def _verb_form(word: str, nxt: str) -> bool:
    """"learned", "settled", or "eating" with nothing after it it could
    describe ("eating each week"): a doing word, not a thing to search for.
    "baking schedule" and "feeding times" keep their -ing word."""
    w = word.lower()

    if w.endswith("ed") and len(w) > 4:
        stems, flag = [w[:-2], w[:-1], w[:-3] if w[-3:-2] == w[-4:-3] else ""], "D"
    elif w.endswith("ing") and len(w) > 5 and (not nxt or nxt in GRAMMAR or nxt in PARTICLES
                                                or nxt in ARTICLES or nxt in PRONOUNS
                                                or nxt in _OBJECTS):
        stems, flag = [w[:-3], w[:-3] + "e", w[:-4] if w[-4:-3] == w[-5:-4] else ""], "G"
    else:
        return False
    return any(st and (st in VERBS or flag in _word_flags(st)) for st in stems)


#: A word right after these is a doing word: "to look for", "can see",
#: "we each put in".
_BEFORE_VERB = frozenset("""to can could will would should may might must i we you they he she
each""".split())


_OWNERS = frozenset("my our his her their your".split())


def _owned_after_names(toks: list[str], personal: dict[str, str]) -> set[str]:
    """The thing a possessive and a held name own: "den" in "his Cub Scout
    den", "dryer" in "my mom's dryer" (without this, "stargazing night den"
    is sent). "my campaign notes" has no name between, so nothing here."""
    lows = [re.sub(r"[^a-z0-9'-]", "", t.lower()) for t in toks]
    out: set[str] = set()
    for i, w in enumerate(lows):
        if w not in _OWNERS:
            continue
        k, names = i + 1, 0
        while k < len(lows):
            base = lows[k][:-2] if lows[k].endswith("'s") else lows[k]
            if not str(personal.get(base, "")).startswith(("hard", "held")):
                break
            names, k = names + 1, k + 1
        if names and k < len(lows) and lows[k]:
            out |= {lows[k], goal_words.singular(lows[k])}
    return out


def item_words(item: str, personal: dict[str, str]) -> list[str]:
    """Content words of one listed item; its leading verb is the job, not a search word."""
    toks = str(item or "").split()
    owned = _owned_after_names(toks, personal)
    lead = toks[0].lower() if toks else ""
    nxt = toks[1].lower() if len(toks) > 1 else ""
    if toks and (_lead_is_verb(lead) or lead.endswith("ing") and len(lead) > 5
                 and (nxt in ARTICLES or nxt in PRONOUNS or nxt in _OWNERS
                      or nxt in _OBJECTS)):
        # "stopping the kids ...", "restarting it ...": the doing word leads
        toks = toks[1:]
    if len(toks) >= 2 and _CARRY_TAIL.match(" ".join(toks)):
        toks = toks[2:]   # "keeping track of X": X is the thing
    lows = [re.sub(r"[^a-z0-9'-]", "", t.lower()) for t in toks]
    drop = {lows[i] for i in range(len(lows))
            if lows[i] in GRAMMAR or _verb_form(lows[i], lows[i + 1] if i + 1 < len(lows) else "")
            or i and lows[i - 1] in _BEFORE_VERB and lows[i] not in ARTICLES
            and (lows[i] in VERBS or not _word_flags(lows[i]) or "D" in _word_flags(lows[i])
                 or "G" in _word_flags(lows[i]))}
    words = []
    for w in goal_words.goal_terms(" ".join(toks), limit=12):
        if w in personal or w in FILLER or w in LIFE_EVENTS or w in PARTICLES \
                or w in ("figure", "come") or w in GRAMMAR or w in drop or w in owned \
                or w in PLACE_CUES:
            continue
        words.append(w)
    return words


#: What a tool that does a job's verb is called ("rolls dice" -> "dice roller").
#: Only verbs whose tool name is the usual one people search for; "keeps",
#: "shows" or "helps" name no tool. Measured (a busy brain): the
#: rule phrase "game master dice" found 40 unlicensed "Dice-Game-master"
#: homework repos, the brain's "dice roller" found real dice rollers.
AGENT_NOUNS = {
    "track": "tracker", "roll": "roller", "build": "builder", "plan": "planner",
    "log": "logger", "remind": "reminder", "schedule": "scheduler", "organize": "organizer",
    "organise": "organizer", "manage": "manager", "generate": "generator",
    "calculate": "calculator", "estimate": "estimator", "translate": "translator",
    "find": "finder", "record": "recorder", "count": "counter", "convert": "converter",
    "monitor": "monitor", "check": "checker", "scan": "scanner", "edit": "editor",
    "collect": "collector", "compare": "comparison", "name": "name generator",
    "cost": "cost calculator", "price": "price calculator", "split": "splitter",
    "time": "timer", "design": "designer", "read": "reader", "quiz": "quiz",
}


_TOOL_WORDS = frozenset(w for v in AGENT_NOUNS.values() for w in v.split())


def only_tool_words(phrase: str) -> bool:
    """"logger", "name generator": a tool's name with nothing of what it is for."""
    words = str(phrase or "").lower().split()
    return bool(words) and all(w in _TOOL_WORDS for w in words)


def agent_phrase(item: str, personal: dict[str, str]) -> str:
    """"dice roller" for "Rolls dice", "mileage tracker" for "Tracks mileage
    and repairs" (the first thing named), "" when the verb names no tool."""
    toks = str(item or "").split()
    if len(toks) < 2:
        return ""
    lead = re.sub(r"[^a-z]", "", toks[0].lower())
    stem = next((s for s in (lead, lead[:-1] if lead.endswith("s") else "",
                             lead[:-2] if lead.endswith("es") else "") if s in AGENT_NOUNS), "")
    rest = toks[1:]
    if not stem and len(toks) >= 3:
        # "Keeps my campaign notes organized": the tool is in the last word
        last = re.sub(r"[^a-z]", "", toks[-1].lower())
        stem = next((s for s in ((last[:-1], last[:-2]) if last.endswith("ed") else ())
                     if s in AGENT_NOUNS), "")
        rest = toks[1:-1]
    if not stem:
        return ""
    first = re.split(r"\s+(?:and|or|&)\s+|,", " ".join(rest), maxsplit=1)[0]
    # "hours we each put in", "themes from pictures": the thing, without what follows
    first = re.split(r"\s+(?:we|i|you|they|he|she|it|that|which|who|each|from|for|with|"
                     r"around|on|in|at|to|by|of)(?![\w'])", " " + first, maxsplit=1,
                     flags=re.I)[0]
    words = item_words(first, personal)[:2]
    return f"{' '.join(words)} {AGENT_NOUNS[stem]}" if words else ""


def _phrases(kind: list[str], words: list[str]) -> list[str]:
    """Two short phrases: subject word(s) + item words, and the item words alone."""
    # The kind in its own order ("wedding planner vendor material", never
    # "planner wedding vendor material" when a kind word is also hers).
    lead = list(kind[:2]) if any(w not in words for w in kind[:2]) else []
    body = [w for w in words if w not in lead]
    out = [" ".join((lead + body[:3])[:4])]
    if len(words) > 1:
        alone = " ".join(words[:3])
        if alone not in out:
            out.append(alone)
    return [p for p in out if p]


#: A job the brain named after a tool ("Price comparator") said as what it
#: does ("Compare prices"): the tool word and the doing word in front.
_DOING = {"comparator": "Compare", "comparison": "Compare", "sender": "Send",
          "creator": "Make", "maker": "Make", "generator": "Make", "builder": "Build",
          "tracker": "Track", "logger": "Log", "finder": "Find", "planner": "Plan",
          "scheduler": "Schedule", "calculator": "Work out", "estimator": "Estimate",
          "suggestion": "Suggest", "suggestions": "Suggest", "recommender": "Suggest",
          "recommendation": "Suggest", "recommendations": "Suggest", "organizer": "Organize",
          "organiser": "Organise", "manager": "Keep track of", "checker": "Check",
          "analyzer": "Look at", "analyser": "Look at", "monitor": "Watch",
          "reminder": "Send", "reminders": "Send", "converter": "Convert",
          "translator": "Translate", "editor": "Edit", "collector": "Collect",
          "identifier": "Identify", "locator": "Find", "notifier": "Send"}
#: A tool word that names what is sent, not the tool ("Reminder system").
_SENT_THINGS = {"reminder": "reminders", "reminders": "reminders", "notifier": "alerts"}
_SYSTEM_WORDS = frozenset("system tool app feature module service".split())
#: Things counted as a whole, never with an -s ("Look at sales data").
_MASS = frozenset("""data progress money info information equipment weather music research
feedback advice inventory health sleep nutrition fitness homework mail news software
furniture luggage clothing food traffic budget""".split())


def plain_job_name(name: str) -> str:
    """"Compare prices" for the brain's "Price comparator", "Send reminders"
    for "Reminder sender" or "Reminder system", "Make to-do lists" for "To-do
    list creator"; any other name as it is (the usual job names read like
    jargon)."""
    words = str(name or "").split()
    low = [w.lower() for w in words]
    if low and _lead_is_verb(low[0]) and not (len(low) >= 2 and low[-1] in _DOING
                                              and not low[-1].endswith("s")):
        # Already a doing word ("Send reminders", "Track plant growth"); "Price
        # comparator" starts with a word that can be a doing word but ends in
        # the tool's name.
        return str(name or "")
    if len(words) >= 2 and low[-1] in _SYSTEM_WORDS:
        words, low = words[:-1], low[:-1]
    if not words:
        return str(name or "")
    if len(words) == 1 and low[0] in _SENT_THINGS:
        return f"Send {_SENT_THINGS[low[0]]}"
    tool = low[-1]
    if tool not in _DOING or len(words) < 2:
        return str(name or "")
    if tool in ("reminder", "reminders"):
        thing = " ".join(words[:-1]).lower()
        return f"Send {thing} reminders"
    obj = [w if w.isupper() and len(w) > 1 else w.lower() for w in words[:-1]]
    if obj[-1] in _SENT_THINGS and _DOING[tool] == "Send":
        return f"Send {_SENT_THINGS[obj[-1]]}"
    last = obj[-1]
    if not (last.endswith("s") or last in _MASS or not last.isalpha()):
        obj[-1] = last + ("es" if last.endswith(("ch", "sh", "x")) else "s")
    return f"{_DOING[tool]} {' '.join(obj)}"


def job_name(item: str) -> str:
    """Her words as a job name: trimmed, a leading "a"/"an" dropped, first letter capitalised."""
    text = re.sub(r"\s+", " ", str(item or "")).strip(" .,;:!?")
    text = re.sub(r"^(?:a|an)\s+(?=\S)", "", text, flags=re.I)
    return text[:1].upper() + text[1:] if text else ""


def rule_needs(goal: str, *, personal: dict[str, str] | None = None,
               kind: list[str] | None = None, home: dict | None = None) -> dict:
    """The whole rule-only answer: kind words and one job per listed item."""
    personal = personal if personal is not None else personal_words(goal, home=home)
    kind = kind if kind is not None else kind_words(goal, home=home)
    jobs = []
    for it in explicit_items(goal):
        words = item_words(it, personal)
        name = job_name(it)
        if not name:
            continue
        # An item whose every word is personal or held is still one of hers
        # ("From your words" counts what she listed); it just searches nothing.
        agent = agent_phrase(it, personal) if words else ""
        search = list(dict.fromkeys(([agent] if agent else []) + _phrases(kind, words)))             if words else []
        jobs.append({"name": name, "words": words, "search": search})
    return {"kind": " ".join(kind), "jobs": jobs}


def rule_phrases(kind: Iterable[str], name: str, *,
                 personal: dict[str, str] | None = None) -> list[str]:
    """2 to 3 word phrases for a job name: the subject word plus its key nouns."""
    personal = personal or {}
    kind = [w for w in kind if w]
    words = [w for w in item_words(name, personal)][:2]
    if not words:
        return []
    head = [kind[0]] if kind and kind[0] not in words else []
    agent = agent_phrase(name, personal)
    out = ([agent] if agent else []) + [" ".join(head + words)]
    if head and len(words) > 1 and " ".join(words) not in out:
        out.append(" ".join(words))
    return list(dict.fromkeys(out))


# -- the written "learn about" rule ---------------------------------------------
LEARN_RE = re.compile(
    r"\b(?:learn|study|practi[cs]e|understand|know about|facts about|how to|teach|quiz|"
    r"revise|memori[sz]e|lessons?|rules of|history of"
    # "knows everything about space", "knows a lot about birds"
    r"|knows?\s+(?:everything|all|a\s+lot|lots|more)\s+about)\b", re.I)
LEARN_SUBJECT_RE = re.compile(r"\b(?:tutor|teacher|coach|lesson|course|study|quiz|language|trivia)\b",
                              re.I)


#: A job named for something the bot does or keeps (a timer, a log, a
#: reminder, a game) is not a "learn about" job, even for a coach or a tutor:
#: a book cannot play practice games or remind her to stretch (without this,
#: an 1800s book about a chess automaton is the best match for "Play practice
#: games").
DOING_RE = re.compile(
    r"\b(?:track|tracks|tracking|tracker|log|logs|logging|record|records|remind|reminds|"
    r"reminder|reminders|schedule|schedules|scheduling|timer|timers|stopwatch|play|plays|"
    r"playing|count|counts|counter|split|splits|sync|syncs|shared|to-?do|checklist|"
    r"order|orders|booking|post|posting)\b", re.I)


def is_learn(name: str, does: str, subject: str) -> bool:
    """True only for a "learn about" job (K2); knowledge is offered only for these."""
    if DOING_RE.search(str(name or "")):
        return False
    return bool(LEARN_RE.search(f"{name or ''}\n{does or ''}")
                or LEARN_SUBJECT_RE.search(str(subject or "")))


__all__ = ["AGENT_NOUNS", "AUDIENCE", "BOT_NOUNS", "DOING_RE", "FILLER", "LANGUAGES", "LEARN_RE",
           "LEARN_SUBJECT_RE", "PRONOUNS", "RELATIVES", "VERBS", "agent_phrase", "dict_kind", "only_tool_words", "explicit_items", "hard_words",
           "is_learn", "item_words", "job_name", "kind_words", "personal_words",
           "place_words", "rule_needs", "rule_phrases", "sentences", "subject_of",
           "usable_subject"]
