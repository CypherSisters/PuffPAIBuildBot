"""Structural checks of a model reply on a turn where no app action ran.

The older rules in :mod:`puffpai.integrate.chat` read a reply sentence by
sentence and look for the SHAPES of a claim. A held-out set showed what a
shape list cannot see: a bare "Yes." to "did the change stick?", "He was."
to "was Leo removed?", "Sí." to a Spanish question, a claim in Korean after
an English question. These checks do not look for claim words at all. They
look at what the reply can mean, given her message:

* an ELLIPTICAL reply (a yes or no word, pronouns and helpers, or her own
  words back) has no statement of its own: it asserts her question. Nothing
  ran this turn, so the model cannot know the answer to a question about
  her things; the answer comes from the change log instead. Only a
  question plainly about the outside world ("is Paris the capital of
  France?") keeps the model's yes;
* a sentence in a LANGUAGE OR SCRIPT she did not write in is a sentence the
  guard cannot read, so it fails closed (unless she asked for another
  language);
* a sentence in another language that NAMES one of her things (her rows
  and app names, also written in Cyrillic or Greek letters) is a claim,
  since the guard cannot read what it says about them.

Every check here only ever turns a sentence into a claim; none of them
lets through a sentence the older rules stop. The word sets below are
grammar (pronouns, helper verbs, yes and no words, language names), not
claim phrases.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

# -- words ------------------------------------------------------------------------

_TOKEN = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*|\d+(?:[.,:]\d+)*")

#: Yes and no words, in the languages a household may write in.
YES_NO = frozenset("""
yes yeah yep yup yea ya yas yess yah sure right correct exactly indeed absolutely definitely
certainly totally affirmative ok okay alright mhm uh-huh no nope nah negative
si sí oui ouais ja jo jep jawohl nein nee non não nao sim claro vale certo certamente esatto
tak nie da ne ano evet hayır hayir ja nej jo
да нет ага угу так ні ναι όχι
כן לא نعم لا اي ايوه
हाँ हां जी नहीं
はい いいえ ええ うん ううん そう
네 예 응 아니 아니요 아뇨
是 是的 对 對 没有 沒有 不 嗯 好
có không vâng dạ ừ
ใช่ ไม่ ครับ ค่ะ
""".split())

#: English closed-class words (pronouns, helpers, determiners, little
#: adverbs and prepositions): a sentence made only of these and her own
#: words says nothing of its own.
CLOSED = frozenset("""
i me my mine myself you your yours yourself he him his himself she her hers herself it its
itself they them their theirs themselves we us our ours ourselves one ones
this that these those there here what which who whom whose
a an the and or but so if then than too also just only still already yet now again even
all both each every some any none nothing everything something anything everyone everybody
someone somebody anyone anybody no not
is am are was were be been being do does did has have had having
will would shall should can could may might must
to of in on at by for with from up off out over into onto about as
very really quite pretty well of course sure thing absolutely
it's that's there's he's she's they're we're you're i'm i've you've we've they've
isn't wasn't aren't weren't didn't doesn't don't hasn't haven't hadn't won't can't
yes yeah yep yup ok okay right
""".split())

#: Second-person words: a question about what "you" did is about the bot.
_YOU = frozenset("""
you u ya your yours yourself du dir dich tu toi vous usted ustedes tú voi lei você vocês
ты вы ти ви εσύ εσείς sen siz ty wy jij jullie anata あなた 君 당신 너 你 您 bạn
""".split())

#: Language names: she asked for another language, so a reply in it is
#: expected.
_LANGUAGES = frozenset("""
spanish español espanol french français francais german deutsch italian italiano portuguese
português portugues japanese 日本語 chinese mandarin cantonese 中文 汉语 korean 한국어
russian русский arabic العربية hindi हिन्दी hebrew עברית greek ελληνικά turkish türkçe
polish polski dutch nederlands swedish svenska norwegian norsk danish dansk finnish suomi
vietnamese tiếng thai ไทย indonesian bahasa malay tagalog filipino swahili latin ukrainian
czech hungarian romanian persian farsi urdu bengali punjabi tamil telugu irish welsh yiddish
esperanto catalan basque icelandic serbian croatian bulgarian slovak slovenian lithuanian
latvian estonian translate translated translation translating
""".split())

#: Common English words (a language test, not claim words).
_EN = frozenset("""
the a an and or but nor so yet to of in on at by for with from up down out off over under into
onto about as than then if when while because though although after before since until till
is am are was were be been being do does did done has have had having will would shall should
can could may might must need needs i me my mine you your yours he him his she her hers it its
we us our they them their this that these those there here who whom whose what which where why
how not no yes all any both each every few more most other some such only own same too very
just also even ever never always often still already again once now today tonight tomorrow
yesterday soon later new old good great best better bad worse little big small long short high
low right left first last next one two three four five ten hundred thousand many much lot lots
get gets got go goes going went gone make makes made take takes took taken give gives gave
come comes came see sees saw seen know knows knew think thinks look looks want wants like likes
use uses used find found tell tells told say says said ask asks try tries put keep let help
start stop turn turns open close play run read write call feel feels leave work works
way thing things time times day days week weeks year years people man woman child kids family
home house world life part place case point group number fact water food light night morning
evening around between through during without within along across again ago away back off
it's that's there's here's what's who's let's i'm i've i'd i'll you're you've you'd you'll he's
she's we're we've they're they've isn't aren't wasn't weren't don't doesn't didn't haven't
hasn't won't can't couldn't shouldn't wouldn't
about above below near far well really quite pretty almost enough maybe perhaps probably sure
thanks thank please sorry hello hi hey okay ok fine nice love happy fun easy hard free full
""".split())

#: Function words of other Latin-script languages that are not English words.
_OTHER_FW = frozenset("""
el los las del al un una unos unas y o pero que por para con sin muy más ya está están es son
fue hay su sus mi mis tu tus lo le les se nos este esta esto ese esa como cuando donde porque
le la les des du de au aux et ou mais est sont était avec sans pour dans sur pas ne très plus
je tu il elle nous vous ils elles ce cette ces qui quoi j'ai c'est n'est fait
der die das den dem des ein eine einen einem einer und oder aber ist sind war waren mit ohne
für auf aus bei nach von zu zum zur nicht sehr ich du er sie wir ihr es habe hat haben wird
il lo gli della delle dei degli nel nella con per tra fra non che sono era anche ho hai ha
abbiamo alla alle allo è
os as um uma uns umas e ou mas não são foi com sem para pelo pela isso esse essa eu ele ela
nós vocês dos das na nas no nos já
het een en of maar niet zijn was met zonder voor naar ik jij hij zij wij jullie heb heeft
aan bij uit ook dat deze dit
och att det som är var med utan för på av till jag du han hon vi ni har inte
og at det som er var med uden for på af til jeg du han hun vi har ikke
się jest są był była nie tak że jak ale dla przez na po od ze
ve bir bu şu için ile ama değil çok daha ben sen biz siz onlar
dan yang ini itu dengan untuk tidak ada saya kamu kami
și sau este sunt cu fără pentru pe din la nu
""".split()) - _EN


def english(text: Any) -> bool:
    """*text* reads as English: its words are mostly Latin letters and, of
    the words that are not names, English ones outweigh other languages'
    little words and accented words ("J'ai retiré Leo Park de la liste." is
    not; "Crème brûlée is a classic dessert." is)."""
    body = str(text or "")
    s = scripts(body)
    latin = s.get("latin", 0)
    if not latin or latin * 4 < sum(s.values()) * 3:
        return not s
    raw = _TOKEN.findall(body)
    words = []
    for i, w in enumerate(raw):
        if w[:1].isdigit():
            continue
        if i > 0 and w[:1].isupper() and not w.isupper():
            continue                                    # a name
        words.append(w.lower().replace("’", "'"))
    if len(words) < 2:
        return True
    eng = sum(1 for w in words if w in _EN)
    other = sum(1 for w in words if w not in _EN and (
        w in _OTHER_FW or _fold(w) != w or any(p in _OTHER_FW for p in w.split("'"))))
    # Not English only on evidence of another language: its little words or
    # accented words ("Proud parent moment!" has neither, and is English).
    if other >= 2 and other > eng:
        return False
    return not (other >= 1 and eng == 0 and len(words) >= 3)


_LETTER_RANGES = (
    ("latin", (0x0041, 0x024F), (0x1E00, 0x1EFF)),
    ("greek", (0x0370, 0x03FF), (0x1F00, 0x1FFF)),
    ("cyrillic", (0x0400, 0x052F)),
    ("hebrew", (0x0590, 0x05FF)),
    ("arabic", (0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
    ("indic", (0x0900, 0x0DFF)),
    ("thai", (0x0E00, 0x0EFF)),
    ("hangul", (0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF)),
    ("cjk", (0x3040, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)),
)


def _script_of(ch: str) -> str:
    if not ch.isalpha():
        return ""
    code = ord(ch)
    for name, *ranges in _LETTER_RANGES:
        if any(lo <= code <= hi for lo, hi in ranges):
            return name
    return "other"


def scripts(text: Any) -> dict[str, int]:
    """``{script: letters}`` in *text*."""
    out: dict[str, int] = {}
    for ch in str(text or ""):
        s = _script_of(ch)
        if s:
            out[s] = out.get(s, 0) + 1
    return out


def main_script(text: Any) -> str:
    counts = scripts(text)
    return max(counts, key=counts.get) if counts else ""


def tokens(text: Any) -> list[str]:
    return [t.lower().replace("’", "'") for t in _TOKEN.findall(str(text or ""))]


def _fold(word: str) -> str:
    """Lower case, accents off: "Pérou" -> "perou"."""
    return "".join(c for c in unicodedata.normalize("NFKD", word.lower())
                   if not unicodedata.combining(c))


# -- letters of other alphabets, read as Latin letters --------------------------------

_CYRILLIC = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґ",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r",
     "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya", "i", "i",
     "e", "g"]))
_GREEK = dict(zip(
    "αβγδεζηθικλμνξοπρσςτυφχψωάέήίόύώϊϋΐΰ",
    ["a", "v", "g", "d", "e", "z", "i", "th", "i", "k", "l", "m", "n", "x", "o", "p", "r", "s",
     "s", "t", "y", "f", "ch", "ps", "o", "a", "e", "i", "i", "o", "y", "o", "i", "y", "i", "y"]))


def latin_letters(text: Any) -> str:
    """*text* with Cyrillic and Greek letters written as Latin ones and
    accents off, for finding her names in it ("Лео Парка" -> "leo parka")."""
    out = []
    for ch in _fold(str(text or "")):
        out.append(_CYRILLIC.get(ch) or _GREEK.get(ch) or ch)
    return "".join(out)


def _name_tokens(things: Iterable[Any], first_word: bool = True) -> set[str]:
    """Words of her names and rows a sentence could name one by: whole
    single words of 3+ letters, and (*first_word*, for rows) the first word
    of a longer name ("Leo" of "Leo Park") when it is a name (capitalised in
    her app). An app name ("Big Day") counts only whole."""
    out: set[str] = set()
    for thing in things or ():
        raw = str(thing or "").strip()
        words = re.findall(r"[^\W\d_]+", raw)
        if not words:
            continue
        folded = [latin_letters(w) for w in words]
        if len(words) == 1:
            if len(folded[0]) >= 3:
                out.add(folded[0])
            continue
        out.add(" ".join(folded))
        if first_word and words[0][:1].isupper() and len(folded[0]) >= 3:
            out.add(folded[0])
    return out


def names_in(text: Any, names: Iterable[Any] = (), rows: Iterable[Any] = ()) -> bool:
    """*text* names one of her app names (whole) or rows, in Latin, Cyrillic
    or Greek letters, with a word ending another language puts on a name
    ("Leo Parka", "Leo'yu")."""
    want = _name_tokens(names, first_word=False) | _name_tokens(rows)
    if not want:
        return False
    words = [w for w in re.findall(r"[a-z]+", latin_letters(text))]
    if not words:
        return False
    for name in want:
        parts = name.split()
        n = len(parts)
        for i in range(len(words) - n + 1):
            if all(_same_word(words[i + k], parts[k]) for k in range(n)):
                return True
    return False


def _same_word(seen: str, want: str) -> bool:
    if seen == want:
        return True
    # A case ending on a name ("parka", "leoya"): at most three letters more.
    return len(want) >= 3 and seen.startswith(want) and len(seen) - len(want) <= 3


# -- her message ---------------------------------------------------------------------

def is_question(asked: Any) -> bool:
    text = str(asked or "").strip()
    return bool(text) and (text.endswith(("?", "？", "؟")) or text.startswith("¿"))


#: "can you ...?", "could you ...?", "would you mind ...?": she asks for
#: something to be done, and "Sure." agrees to do it; it asserts nothing.
_REQUEST = re.compile(r"(?i)^\W*(?:please\s+)?(?:can|could|would|will|may)\s+(?:you|u|i|we)\b")

#: A question about a change: a past participle after a helper ("was Leo
#: removed?", "has it been added?", "did he get taken off?"), or a word
#: that compares with before ("now", "yet", "anymore", "still", "already").
_CHANGE_QUESTION = re.compile(
    r"(?i)\b(?:was|were|been|be|being|get|gets|got|gotten|is|are|'s|has|have|had)\s+"
    r"(?:\w+\s+){0,2}?(?:\w{2,}(?:ed|en)|done|gone|put|set|cut|kept|made|sorted|off|out)"
    r"\b|\b(?:now|yet|anymore|any\s+more|still|already|so\s+far)\b")
#: Words that name a list or an app ("on the guest list", "in the chore
#: chart"): a question with one is about her things, not the outside world.
_HER_CONTAINER = re.compile(
    r"(?i)\b(?:lists?|checklists?|to-?do|charts?|logs?|trackers?|planners?|apps?|rosters?|"
    r"schedules?|calendars?|boards?|sheets?|inventor(?:y|ies)|registr(?:y|ies)|"
    r"headcount|rsvps?)\b")


#: A finished change asked about: a passive or perfect helper, then a past
#: participle ("was Leo removed", "has Nana been added", "did he get taken
#: off", "have you put eggs on").
_PARTICIPLE = re.compile(
    r"(?i)\b(?:was|were|been|get|gets|got|gotten|has|have|had)\s+(?:[\w'-]+\s+){0,3}?"
    r"(?:\w{2,}(?:ed|en)|done|gone|put|set|cut|kept|made|sorted)\b")


def change_question(asked: Any) -> bool:
    """Her message asks whether a change happened (an English question with
    a helper and a past participle). It is answered from the change log."""
    text = str(asked or "").strip()
    if not text or not english(text):
        return False
    if not (is_question(text) or re.match(r"(?i)^\W*(?:was|were|has|have|had|did)\b", text)):
        return False
    # A yes-or-no question only: "what games have I logged?" asks for what
    # the app holds (a read), not whether a change happened.
    if _WH_START.match(text):
        return False
    return bool(_PARTICIPLE.search(text))


#: A question that asks for a thing, not for a yes or a no.
_WH_START = re.compile(r"(?i)^\W*(?:(?:so|and|ok|okay|but)[\s,]+)?(?:what|what's|which|who|"
                       r"who's|whom|whose|where|where's|when|why|how)\b")


def proper_name_not_hers(asked: Any, names: Iterable[Any] = (),
                         rows: Iterable[Any] = ()) -> bool:
    """Her message names someone or something with a capital letter that is
    not one of her names or rows ("was Obama elected?")."""
    text = str(asked or "")
    rest = re.sub(r"^\W*\S+", "", text, count=1)
    for m in re.finditer(r"\b[A-Z][^\W\d_]+(?:\s+[A-Z][^\W\d_]+)*", rest):
        if m.group(0) != "I" and not names_in(m.group(0), names, rows):
            return True
    return False


def asks_language(asked: Any) -> bool:
    """She asked for or about another language ("how do you say thank you
    in Japanese?"), or wrote in one."""
    words = set(tokens(asked)) | {_fold(w) for w in tokens(asked)}
    return bool(words & _LANGUAGES)


def reads_english(text: Any, english_readable=None) -> bool:
    """*text* is English as far as the guard can tell (see :func:`english`)."""
    return english(text)


def outside_anchor(asked: Any, names: Iterable[Any] = (), rows: Iterable[Any] = ()) -> bool:
    """Her question is about something outside her apps: it names a proper
    name that is none of hers ("is Paris the capital of France?", "did the
    Lakers win?"), it is not about the bot ("did you ..."), and it does not
    ask about a change ("was X removed?", "is X on dishes now?")."""
    text = str(asked or "").strip()
    if not text or names_in(text, names, rows):
        return False
    # Only a question the guard can read: in another language it cannot
    # tell "did YOU remove Leo?" ("¿quitaste a Leo?") from "is Paris ...?".
    if not english(text):
        return False
    if set(tokens(text)) & _YOU:
        return False
    if _CHANGE_QUESTION.search(text) or _HER_CONTAINER.search(text):
        return False
    rest = re.sub(r"^\W*\S+", "", text, count=1)   # the first word is capitalised anyway
    proper = [m.group(0) for m in re.finditer(r"\b[A-Z][^\W\d_]+", rest) if m.group(0) != "I"]
    return bool(proper)


# -- the reply -----------------------------------------------------------------------

def _echo(word: str, asked_words: set[str]) -> bool:
    if word in asked_words:
        return True
    if len(word) >= 4:
        stem = word[:4]
        return any(len(a) >= 4 and a[:4] == stem for a in asked_words)
    return False


def elliptical(sentence: Any, asked: Any) -> bool:
    """*sentence* says nothing of its own: a yes or no word with at most a
    few words after it ("Yes, it is.", "Sí, ya está.", "はい。"), or only
    helpers, pronouns and her own words back ("He was.", "It did.",
    "All sorted.", "Gone.")."""
    words = tokens(sentence)
    if not words or any(w[:1].isdigit() for w in words):
        return False
    first = _fold(words[0])
    if (words[0] in YES_NO or first in YES_NO) and len(words) <= 5:
        return True
    if len(words) > 8:
        return False
    asked_words = {_fold(w) for w in tokens(asked)}
    return all(w in CLOSED or w in YES_NO or _fold(w) in YES_NO or _echo(_fold(w), asked_words)
               for w in words)


def asserts_her_question(reply: Any, asked: Any, names: Iterable[Any], rows: Iterable[Any],
                         split, question: bool = False) -> bool:
    """The whole *reply* is an elliptical answer to her question, so it
    asserts what she asked, and what she asked is not plainly about the
    outside world. *split* cuts a reply into sentences (the caller's)."""
    if not may_assert(asked, names, rows, question):
        return False
    parts = [p for p in split(str(reply or "")) if p.strip()]
    if not parts or not all(elliptical(p, asked) for p in parts):
        return False
    return not outside_anchor(asked, names, rows)


def may_assert(asked: Any, names: Iterable[Any], rows: Iterable[Any],
               question: bool = False) -> bool:
    """An elliptical reply to *asked* would assert her question (see
    :func:`asserts_her_question`); for a streamed reply, decided before any
    token is passed on."""
    if asked is None or not (question or is_question(asked)) or _REQUEST.match(str(asked)):
        return False
    # "which one is the most useful?": a bare yes cannot answer a question
    # that asks for a thing (only a yes-or-no question can be asserted by
    # one). Read in English only; another language fails closed.
    if english(asked) and _WH_START.match(str(asked)):
        return False
    return not outside_anchor(asked, names, rows)


def unreadable_switch(sentence: Any, asked: Any, english_readable) -> bool:
    """*sentence* is in a language or script she did not write in, and she
    did not ask for another language: the guard cannot read it, so it
    fails closed ("Il fait beau. J'ai retiré Leo Park de la liste." after
    "what's the weather?")."""
    if asked is None or asks_language(asked):
        return False
    body = str(sentence or "").strip()
    if not scripts(body):
        return False
    hers = main_script(asked)
    mine = main_script(body)
    if hers and mine and hers != mine and scripts(body).get(mine, 0) >= 2:
        return True
    if reads_english(asked, english_readable) and len(tokens(asked)) >= 1:
        return not reads_english(body, english_readable) and len(
            [w for w in tokens(body) if not w[:1].isdigit()]) >= 3
    return False


def foreign_names_hers(sentence: Any, names: Iterable[Any], rows: Iterable[Any],
                       english_readable) -> bool:
    """A sentence the guard cannot read as English that names one of her
    things: what it says about them cannot be checked, so it fails closed
    (a question back to her is fine)."""
    body = str(sentence or "").strip()
    if not body or is_question(body) or reads_english(body, english_readable):
        return False
    return names_in(body, names, rows)


__all__ = ["CLOSED", "YES_NO", "asks_language", "asserts_her_question", "change_question",
           "elliptical", "english", "foreign_names_hers", "is_question", "latin_letters",
           "main_script", "may_assert", "names_in", "outside_anchor", "proper_name_not_hers",
           "reads_english", "scripts", "tokens", "unreadable_switch"]
