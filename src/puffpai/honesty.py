"""Evidence or silence: two layers that stop the bot claiming what it cannot do.

The adversarial GardenBot run asked "plan my fall garden in three steps and
remind me each Saturday to water" and was told "I will remind you each
Saturday." There is no reminder store on that build, no scheduler anywhere in
this tree that fires one, and the chat path's own standing note already forbade
the sentence. The model said it anyway, because a prohibition in a system
prompt is a suggestion to a 7B model.

So two layers, neither of which is a prompt:

1. **A deterministic answer for the ask.** :func:`reminder_note` recognises a
   reminder / alarm / timer / recurring-schedule REQUEST and returns the exact
   sentence this build can honestly say about it. It does not swallow the rest
   of the message: "plan my fall garden AND remind me" is two asks, and only the
   second one is refused - the old behaviour answered the whole sentence with "I
   do not have comms.timers", which threw away the part the bot could do.

2. **A post-generation guard.** :func:`scrub_claims` finds first-person claims
   that an action was taken or will be taken - "I will remind you", "I've set an
   alarm", "I turned on the lights" - and replaces that sentence with the honest
   one. Conservative by construction: the subject has to be *I/I'll/I've*, the
   verb has to be one of a short list of things this build genuinely cannot do,
   and anything describing what the OWNER could do ("you can add it to a list",
   "you could set a reminder on your phone") is left alone.

**What this build can really do about reminders.** ``apps/alarmflow`` declares
the ``comms.timers`` surface and :class:`~puffpai.app_data.AppData` has
``alarm_create_timer`` / ``alarm_create_alarm``, which append an item to
``alarmflow``'s store. Nothing in this tree counts that item down or rings it:
the countdown lives in the dashboard's AlarmFlow page, so an alarm only ticks
while that page is open, and there is no recurring ("every Saturday") mode at
all. A build whose surfaces were trimmed - which is what the wizard does to a
GardenBot - does not even have that. Both facts are said out loud rather than
rounded up.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. recognising the ask
# ---------------------------------------------------------------------------

#: A reminder / alarm / timer / recurring-schedule REQUEST, in the owner's own
#: vocabulary. Requests only: "what timers are running" is a question about the
#: store and belongs to the app fastpath, not here.
_REMINDER_REQUEST = re.compile(
    r"(?ix)"
    r"\b(?:"
    r"remind\s+me|remind\s+us|set\s+(?:a|an|the)?\s*(?:reminder|alarm|timer)"
    r"|put\s+(?:a|an)\s+(?:reminder|alarm|timer)"
    r"|wake\s+me(?:\s+up)?|nudge\s+me|ping\s+me|alert\s+me|notify\s+me"
    r"|schedule\s+(?:a|an|me|this|that|it)"
    r"|every\s+(?:day|morning|evening|night|week|month|hour|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|each\s+(?:day|morning|evening|night|week|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|daily\s+reminder|weekly\s+reminder"
    r")\b")

#: The reminder ask on its own, with nothing else in the sentence. Used only to
#: decide whether the model is worth consulting at all - a bare "remind me to
#: water the garden on Saturday" has no other question in it to answer.
_OTHER_ASK = re.compile(
    r"(?ix)\b(?:plan|explain|what|which|when|why|how|who|where|tell\s+me|"
    r"suggest|recommend|list|compare|summari[sz]e|help\s+me\s+(?:plan|think|"
    r"choose|decide)|should\s+i|ideas?\s+for|write|draft)\b")


#: Words that point back at this chat: "remind me, what club did I mention?"
#: asks about the chat so far, not for a reminder. ("remind me when ..." is
#: left out on purpose: "remind me when it is time to water" IS a reminder.)
_RECALLS_CHAT = re.compile(
    r"(?i)\b(?:did i|i) (?:mention|mentioned|say|said|tell you|told you)\b"
    r"|\bremind (?:me|us),? (?:again,? )?(?:what|which|who|where|how)\b"
    r"|\byou (?:said|told me|mentioned)\b|\bearlier\b")


def recalls_chat(text: Any) -> bool:
    """True when the message asks about something said earlier in this chat."""
    return bool(_RECALLS_CHAT.search(str(text or "")))


def reminder_request(text: Any) -> bool:
    """True when the message asks for a reminder, alarm, timer or schedule."""
    body = str(text or "")
    return bool(_REMINDER_REQUEST.search(body)) and not recalls_chat(body)


def reminder_is_the_whole_message(text: Any) -> bool:
    """True when the reminder ask is all there is - nothing else to answer."""
    body = str(text or "")
    return reminder_request(body) and not _OTHER_ASK.search(body)


def reminder_capability() -> str:
    """What this build can really do about reminders: ``"none"`` or ``"store"``.

    ``"none"``  - the timers surface is not loaded on this build at all.
    ``"store"`` - AlarmFlow is here, so a one-off timer or alarm can be written
                  down, but nothing server-side rings it and there is no
                  recurring mode.

    Never raises: an unreadable manifest is reported as ``"none"``, which is the
    claim that promises the owner least.
    """
    try:
        from puffpai import self_knowledge
        return "store" if self_knowledge.can("comms.timers")[0] else "none"
    except Exception:
        return "none"


_REMIND_REST = re.compile(r"(?i)\bremind\s+(?:me|us)\b(?P<rest>[^.?!\n]{1,80})")
_TIMING = re.compile(
    r"(?i)^(?P<when>(?:each|every|on|at|in|tomorrow|tonight|next)\b[^,]*?)"
    r"\s+(?:to|about|of|that)\s+(?P<what>.+)$")


def reminder_thing(text: Any) -> str:
    """What the owner asked to be reminded of, in their own words, or "".

    "remind me about oil changes" -> "oil changes"; "remind me each Saturday to
    water" -> "water each Saturday". Used so the honest note offers the owner's
    own thing back, whatever the bot is for.
    """
    match = _REMIND_REST.search(str(text or ""))
    if not match:
        return ""
    rest = match.group("rest").strip(" ,")
    timed = _TIMING.match(rest)
    if timed:
        rest = f"{timed.group('what').strip()} {timed.group('when').strip()}"
    else:
        rest = re.sub(r"(?i)^(?:to|about|of|that)\s+", "", rest)
    rest = re.sub(r"\s+", " ", rest).strip(" ,\"'")
    return rest[:60]


def reminder_note(text: Any, list_label: str = "", *,
                  capability: Optional[str] = None) -> str:
    """The honest sentence about the reminder part of *text*, or "".

    ``list_label`` is a real list of this build, so the suggestion the owner is
    given is one they can say back verbatim and have land (the wordings in
    :mod:`puffpai.app_fastpath` are deterministic and run before any model).
    """
    if not reminder_request(text):
        return ""
    label = str(list_label or "").strip()
    thing = reminder_thing(text) or "<what to remember>"
    fallback = (f"Once you have a list, say 'add {thing} to the <list> list' "
                f"and I will keep it there.")
    offer = (f"I can write it down instead: say \"add {thing} to the "
             f"{label} list\" and it stays on that list."
             if label else fallback)
    if (capability or reminder_capability()) == "store":
        return ("About the reminder: this build can write down a one-off timer "
                "or alarm, but nothing here rings it unless the AlarmFlow page "
                "is open, and there is no repeating reminder at all - so I have "
                "not set one. " + offer)
    return ("About the reminder: I can't set reminders on this build yet, so I "
            "have not set one. " + offer)


# ---------------------------------------------------------------------------
# 2. the post-generation guard
# ---------------------------------------------------------------------------

#: Actions this build cannot perform from the chat path, as the model words
#: them. Kept short on purpose: every entry here is a sentence the bot will have
#: rewritten, so a loose one costs the owner a real answer.
_CLAIMED_ACTIONS = (
    r"remind(?:ed|ing)?"
    r"|set(?:ting)?\s+(?:up\s+)?(?:a|an|the)?\s*"
    r"(?:reminder|reminders|alarm|alarms|timer|timers|schedule)"
    r"|schedul(?:e|ed|ing)"
    r"|add(?:ed|ing)?\s+(?:a|an|the)?\s*"
    r"(?:reminder|alarm|timer|calendar\s+entry|event)"
    r"|turn(?:ed|ing)?\s+(?:on|off)"
    r"|switch(?:ed|ing)?\s+(?:on|off)"
    r"|start(?:ed|ing)?\s+(?:the\s+)?(?:music|playback|timer)"
    r"|sen[dt](?:ing)?\s+(?:you\s+)?(?:a|an|the)?\s*"
    r"(?:email|text|message|notification)"
    # A model answer never went online (a real web search is answered before
    # the model is asked), so "I looked online" is never true from it.
    r"|look(?:ed|ing)?\s+(?:it\s+|that\s+|this\s+)?(?:up\s+)?online"
    r"|search(?:ed|ing)?\s+(?:the\s+)?(?:web|internet|online)"
    r"|found\s+(?:it\s+|this\s+|that\s+)?online"
)

#: First person, and a tense that asserts the deed. "I would remind you" and
#: "I can remind you if..." are not claims that it happened, so they are not
#: matched; "I will remind", "I've set", "I have scheduled", "I just turned on",
#: "I'm setting" are.
_CLAIM = re.compile(
    r"(?ix)\bI\s*(?:"
    r"'ll|\s+will|\s+shall"
    r"|'ve|\s+have(?!\s+not\b)(?!n't)"
    r"|'m|\s+am(?!\s+not\b)"
    r"|\s+did|\s+just|\s+already"
    r")?\s*(?:just\s+|now\s+|already\s+)?(?:" + _CLAIMED_ACTIONS + r")\b")

#: A claim is only a claim if it is not immediately disowned. These are the
#: shapes an honest sentence takes, and a sentence containing one is kept.
_DISOWNED = re.compile(
    r"(?ix)\b(?:can(?:'|no)?t|cannot|can\s+not|could\s+not|couldn't|won'?t|"
    r"will\s+not|do\s+not|don'?t|does\s+not|doesn'?t|did\s+not|didn'?t|"
    r"have\s+not|haven'?t|"
    r"has\s+not|hasn'?t|am\s+not|unable|no\s+way\s+to|not\s+able)\b")

#: Sentence split that keeps the terminator, so a rewrite puts the text back
#: together the way the model wrote it.
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*\n?")


#: "I can look online for more about pterosaurs now." - said as the lead-in to
#: an answer, it is a claim that a lookup is happening. The model never looks
#: anything up, so the sentence is replaced like any other claimed deed.
_LOOKUP_NOW = re.compile(
    r"(?ix)\bI\s*(?:can|'ll|\s+will|'m|\s+am)\s+(?:now\s+)?"
    r"(?:look|looking|search|searching|check|checking)\b[^.!?\n]{0,50}"
    r"\b(?:online|the\s+web|the\s+internet)\b[^.!?\n]{0,60}\bnow\b"
    r"|\bhere(?:'s|\u2019s|\s+is)\s+what\s+I\s+found\s+(?:online|on\s+the\s+"
    r"(?:web|internet))")


def claims_an_action(sentence: Any) -> bool:
    """True when one sentence claims this build did or will do something."""
    body = str(sentence or "")
    if _LOOKUP_NOW.search(body) and not _DISOWNED.search(body):
        return True
    if not _CLAIM.search(body):
        return False
    return not _DISOWNED.search(body)


def scrub_claims(response: Any, honest_sentence: str = "") -> tuple[str, int]:
    """Replace claimed-action sentences with the honest one. ``(text, count)``.

    ``honest_sentence`` is what goes in their place; the default names no
    specific capability, because this function is used on every model answer and
    the caller does not always know which capability was claimed. A response
    with nothing to fix is returned unchanged, character for character.
    """
    body = str(response or "")
    if not body.strip():
        return body, 0
    replacement = (honest_sentence or
                   "I have not done that - nothing in this chat can take an "
                   "action on its own.")
    out: list[str] = []
    fixed = 0
    for sentence in _SENTENCE.findall(body):
        if claims_an_action(sentence):
            fixed += 1
            trailing = "\n" if sentence.endswith("\n") else " "
            out.append(replacement + trailing)
            continue
        out.append(sentence)
    if not fixed:
        return body, 0
    logger.info("Honesty guard rewrote %d claimed-action sentence(s)", fixed)
    return "".join(out).strip(), fixed


# ---------------------------------------------------------------------------
# 3. the one-line list suggestion
# ---------------------------------------------------------------------------

#: A list whose name says it holds things the owner grows, buys, reads... The
#: suggestion only fires when a list is named for the thing being discussed, so
#: the bot never offers to save a fact into a list it has no business in.
_LIST_TOPICS = {
    "plantings": ("plant", "planting", "sow", "seed", "grow", "transplant"),
    "harvest": ("harvest", "pick", "crop", "yield"),
    "shopping": ("buy", "shopping", "purchase", "store"),
    "groceries": ("buy", "grocery", "groceries", "shopping"),
    "reading": ("read", "book", "reading"),
    "watchlist": ("watch", "film", "movie", "show"),
    "chores": ("chore", "clean", "tidy", "task"),
    "projects": ("project", "build", "repair", "fix"),
}


def list_suggestion(question: Any, answer: Any,
                    lists: Optional[dict[str, list]] = None) -> str:
    """One line telling the owner the wording that would save this, or "".

    Fires only when a list of this build is named for what was actually being
    discussed, and never claims anything was saved - the whole value of the line
    is that it is an offer the owner can take, in words that work.

    The test is on the ANSWER, not the question: the point is "you just named
    some plants, here is how to keep one", so a question that mentions growing
    and an answer that does not is nothing to offer against. A very short answer
    (a refusal, an acknowledgement) is left alone for the same reason.
    """
    body = str(answer or "")
    if len(body.strip()) < 60:
        return ""
    text = body.lower()
    for label, rows in list((lists or {}).items()):
        key = str(label).strip().lower()
        if not key:
            continue
        words = None
        for topic, hints in _LIST_TOPICS.items():
            if topic in key:
                words = hints
                break
        if words is None:
            continue
        if not any(word in text for word in words):
            continue
        # Two suppressions, both learned from the real 7B run. An answer that
        # already names the list ("I keep track of your plantings") or reads
        # rows back to the owner ("you have planted tomatoes and basil") is not
        # a recommendation, and offering to save something there reads as a bot
        # that was not listening.
        if any(token in text for token in re.findall(r"\w{4,}", key)):
            continue
        if any(str(row).strip().lower() in text
               for row in (rows or []) if str(row).strip()):
            continue
        return (f"Say \"add <the thing> to the {label} list\" and I will save "
                f"it to your {label} list - nothing has been saved yet.")
    return ""


# ---------------------------------------------------------------------------
# 4. made-up links and pictures
# ---------------------------------------------------------------------------
#
# A space bot asked for "the NASA picture of the day" can answer with
# ``![Image Title: Comet NEOWISE ...](https://example.com/image.jpg)`` when
# nothing fetched a picture: the model wrote an address. The rule is about
# addresses, not topics: any bot, any subject. In the dashboard
# that markdown is also a request from the owner's browser to a host nobody
# chose. So a link or picture whose address no tool produced in this turn is
# taken out. Relative links (this build's own pages) are left alone.

#: A markdown picture: ``![alt](url "title")``.
_MD_IMAGE = re.compile(
    r"!\[(?P<text>[^\]\n]{0,300})\]\(\s*<?(?P<url>(?:[^()\s<>]|\([^()\s<>]*\))+)>?"
    r"(?:\s+\"[^\"\n]*\")?\s*\)")
#: A markdown link: ``[text](url)``, not preceded by ``!``.
_MD_LINK = re.compile(
    r"(?<!!)\[(?P<text>[^\]\n]{1,300})\]\(\s*<?(?P<url>(?:[^()\s<>]|\([^()\s<>]*\))+)>?"
    r"(?:\s+\"[^\"\n]*\")?\s*\)")
_ABSOLUTE = re.compile(r"(?i)^(?:[a-z][a-z0-9+.-]*:|//)")
_URL_IN_TEXT = re.compile(
    r"(?i)\bhttps?://(?:[^\s<>()\"']|\([^\s<>()\"']*\))+")

PICTURE_REMOVED = ("[I took out a picture here: nothing I can reach gave it "
                   "to me, so it would have been made up.]")


def _norm_url(url: Any) -> str:
    text = str(url or "").strip().strip("<>").rstrip("/.,;")
    return text.lower()


def urls_in(value: Any) -> set[str]:
    """Every http(s) address in a string, a source dict, or a list of either."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(_norm_url(u) for u in _URL_IN_TEXT.findall(value))
    elif isinstance(value, dict):
        for key in ("url", "link", "href", "source_url", "image_url"):
            if value.get(key):
                found.add(_norm_url(value.get(key)))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            found.update(urls_in(item))
    found.discard("")
    return found


def strip_unbacked_links(text: Any, allowed_urls: Any = ()) -> tuple[str, int]:
    """Take out pictures and links whose address no tool produced. ``(text, n)``.

    A picture becomes :data:`PICTURE_REMOVED`; a link keeps its words and loses
    its address. ``allowed_urls`` is the set of addresses a tool (the library
    search, a fetch) or the owner's own message put in this turn. Text with
    nothing to remove is returned unchanged, character for character.
    """
    body = str(text or "")
    if "](" not in body:
        return body, 0
    allowed = {_norm_url(u) for u in (allowed_urls or ()) if u}
    removed = 0

    def _backed(url: str) -> bool:
        return not _ABSOLUTE.match(url) or _norm_url(url) in allowed

    def _image(match: re.Match) -> str:
        nonlocal removed
        if _backed(match.group("url")):
            return match.group(0)
        removed += 1
        return PICTURE_REMOVED

    def _link(match: re.Match) -> str:
        nonlocal removed
        if _backed(match.group("url")):
            return match.group(0)
        removed += 1
        return match.group("text")

    out = _MD_IMAGE.sub(_image, body)
    out = _MD_LINK.sub(_link, out)
    if removed:
        logger.info("Honesty guard removed %d unbacked link(s)", removed)
    return out, removed


def _link_end(buf: str, open_at: int) -> int:
    """Index of the ")" that closes the "(" at ``open_at``, or -1 if not yet.

    Balanced, so an address like ``.../Io_(moon)`` is read whole.
    """
    depth = 0
    for index in range(open_at, len(buf)):
        char = buf[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


class LinkStreamGuard:
    """:func:`strip_unbacked_links` for a reply that arrives token by token.

    Text is passed through as it comes, except from a ``[`` onwards: that part
    is held until the markdown link it may start is complete (or plainly is not
    a link), then released through the same check. ``allowed`` is called when a
    link completes, so addresses a tool reports mid-stream still count.
    """

    #: Longest a held fragment may grow before it is released as plain text.
    MAX_HOLD = 700

    def __init__(self, allowed: Any = None) -> None:
        self._allowed = allowed if callable(allowed) else (lambda: allowed or ())
        self._held = ""
        self.removed = 0

    def _release(self, text: str) -> str:
        out, n = strip_unbacked_links(text, self._allowed())
        self.removed += n
        return out

    def feed(self, token: Any) -> str:
        self._held += str(token or "")
        return self._drain()

    def _drain(self) -> str:
        out: list[str] = []
        while self._held:
            buf = self._held
            start = buf.find("[")
            if start < 0:
                # Hold a trailing "!" back: it may be the start of a picture.
                keep = 1 if buf.endswith("!") else 0
                out.append(buf[:len(buf) - keep])
                self._held = buf[len(buf) - keep:]
                break
            if start > 0 and buf[start - 1] == "!":
                start -= 1
            if start > 0:
                out.append(buf[:start])
                self._held = buf = buf[start:]
            open_at = buf.find("[")
            close = buf.find("]", open_at)
            too_long = len(buf) >= self.MAX_HOLD
            if close < 0:
                if too_long or "\n" in buf:
                    out.append(buf[:open_at + 1])  # not a link: let it go
                    self._held = buf[open_at + 1:]
                    continue
                break  # the link text is still arriving
            if close + 1 == len(buf):
                break  # "]" is the last thing so far: wait for "("
            if buf[close + 1] != "(":
                out.append(buf[:close + 1])  # brackets, not a link
                self._held = buf[close + 1:]
                continue
            end = _link_end(buf, close + 1)
            if end < 0:
                if too_long or "\n" in buf[close:]:
                    out.append(self._release(buf))
                    self._held = ""
                    break
                break  # the address is still arriving
            out.append(self._release(buf[:end + 1]))
            self._held = buf[end + 1:]
        return "".join(out)

    def flush(self) -> str:
        rest, self._held = self._held, ""
        return self._release(rest) if rest else ""


# ---------------------------------------------------------------------------
# 5. offers of abilities this build does not have
# ---------------------------------------------------------------------------
#
# "Would you like to set up the telescope now?" and "Would you like me to set up
# a news feed?" are not claims that something happened, so :func:`scrub_claims`
# leaves them. They are offers of things this build cannot do. The reply is
# left as the model wrote it and ONE plain correction per ability is added
# under it, worded from :mod:`puffpai.build_abilities`.
#
# Generic by construction: the things are the build's own ability rows (their
# label words, or the ability vocabulary for the few generic abilities), and the
# verbs are a short list of doing verbs. No topic appears here. A sentence
# counts only when the bot is offering (I can / I'll / let me / would you like
# (me) to ...), a doing verb is followed closely by one of the row's words, and
# the sentence does not already say it cannot. "You can use a telescope to see
# Jupiter's moons" is advice, not an offer, and is left alone.

_OFFER = re.compile(
    r"(?ix)\b(?:I\s+can|I\s*'ll|I\s+will|I\s+could|I\s+would|I\s+am\s+able"
    r"|I\s*'d|I\s*'m\s+able|let\s+me|let's|let\s+us|shall\s+I|should\s+I"
    r"|would\s+you\s+like(?:\s+me)?\s+to|do\s+you\s+want(?:\s+me)?\s+to"
    r"|want\s+me\s+to|we\s+can|we\s+could|how\s+about\s+I)\b")

#: Verbs that DO something to or with a thing. Deliberately not "tell",
#: "explain", "show", "find": those are what a bot does with what it knows.
_DOING = (r"set\s*up|setting\s+up|connect(?:ing)?|link(?:ing)?|pair(?:ing)?"
          r"|hook(?:ing)?\s+up|control(?:ling)?|turn(?:ing)?\s+(?:on|off)"
          r"|switch(?:ing)?\s+(?:on|off)|subscrib(?:e|ing)|add(?:ing)?"
          r"|creat(?:e|ing)|install(?:ing)?|enabl(?:e|ing)|sync(?:ing)?"
          r"|point(?:ing)?|aim(?:ing)?|mov(?:e|ing)|access(?:ing)?|open(?:ing)?"
          r"|operat(?:e|ing)|us(?:e|ing)|fetch(?:ing)?|pull(?:ing)?|send(?:ing)?"
          r"|deliver(?:ing)?|schedul(?:e|ing)|start(?:ing)?|run(?:ning)?")
_DOING_THEN = re.compile(r"(?ix)\b(?:" + _DOING + r")\b(?P<rest>[^.!?\n]{0,45})")

#: The generic abilities, as offers are worded. Ability vocabulary only.
_OFFERED: dict[str, re.Pattern] = {
    "scheduled_messages": re.compile(
        r"(?ix)(?:\b(?:send|sending|deliver|delivering|email|text|message"
        r"|notify|remind|ping|post)\b[^.!?\n]{0,40}"
        r"\b(?:daily|every\s+(?:day|morning|evening|night|week)|each\s+"
        r"(?:day|morning|evening|week)|weekly|regularly|on\s+a\s+schedule)\b"
        r"|\b(?:daily|weekly|regular|morning)\s+(?:\w+\s+)?"
        r"(?:updates?|reminders?|messages?|briefings?|digests?|alerts?)\b)"),
    "web_search": re.compile(
        r"(?ix)\b(?:search|searching|look\s+up|looking\s+up|look|looking"
        r"|google|browse|check)\b"
        r"[^.!?\n]{0,25}\b(?:the\s+)?(?:web|internet|online)\b"),
    "live_data": re.compile(
        r"(?ix)\b(?:fetch|get|pull|grab|show|find|check)\b[^.!?\n]{0,40}"
        r"\b(?:latest|today's|todays|current|live|tonight's|real-time"
        r"|up-to-date)\b"),
    "web_page": re.compile(
        r"(?ix)\b(?:open|visit|read|fetch|browse|go\s+to|pull\s+up)\b"
        r"[^.!?\n]{0,25}\b(?:website|web\s+page|webpage|web\s+site|the\s+link)\b"),
}

#: Label words too common to name a thing on their own.
_WEAK = frozenset({"name", "names", "way", "ways", "computer", "information",
                   "thing", "things", "app", "apps", "use", "work",
                   "without", "being", "asked"})


def _stems(text: str) -> set[str]:
    from puffpai import build_abilities as ba
    return {ba._stem(w) for w in ba.words(text)}


def _offered_words(sentence: str, r: dict) -> Optional[set[str]]:
    """The row's words the offer names (empty for a pattern match), or None."""
    from puffpai import build_abilities as ba
    pattern = _OFFERED.get(str(r.get("id") or ""))
    if pattern is not None:
        return set() if pattern.search(sentence) else None
    wanted = {w for w in ba.label_terms(r) if w not in _WEAK}
    if not wanted:
        return None
    hit: set[str] = set()
    for match in _DOING_THEN.finditer(sentence):
        hit |= _stems(match.group("rest")) & wanted
    return hit or None


def offered_abilities(text: Any,
                      abilities: Optional[list[dict]] = None) -> list[str]:
    """Ability ids the reply OFFERS, in the order they first appear.

    Only rows the build CANNOT do are looked for (that is all a correction is
    for). A sentence that also names something the build CAN do is about that
    thing, and is left alone: "I can check the current weather" on a build
    with the weather website switched on is true.
    """
    try:
        from puffpai import build_abilities as ba
        rows = ba.abilities() if abilities is None else list(abilities)
    except Exception:
        return []
    cannot = ba.absent(rows)
    can_words: set[str] = set()
    for r in ba.available(rows):
        can_words |= {w for w in ba.label_terms(r) if w not in _WEAK}
    found: list[str] = []
    for sentence in _SENTENCE.findall(str(text or "")):
        if not _OFFER.search(sentence) or _DISOWNED.search(sentence):
            continue
        if can_words and _stems(sentence) & can_words:
            continue
        hits = [(r, _offered_words(sentence, r)) for r in cannot]
        hits = [(r, w) for r, w in hits if w is not None]
        for r, words in hits:
            rid = str(r.get("id") or "")
            # "connect to your smart telescope" names the telescope; that
            # "smart" is also a smart-home word does not make it one.
            if words and any(words < other for _r, other in hits):
                continue
            if rid and rid not in found:
                found.append(rid)
    return found


def correction(r: dict) -> str:
    """The one plain line that follows a false offer of ``r``."""
    from puffpai import build_abilities as ba
    label = ba.lower_first(str(r.get("label") or ""))
    label = re.sub(r"\s*\([^)]*\)", "", label)
    status = r.get("status")
    how = str(r.get("how_to_add") or "").rstrip(".")
    if status == ba.UNKNOWN:
        line = f"To be clear: I can't be sure I can {label} right now."
    elif status == ba.NOT_SET_UP:
        line = f"To be clear: I can't {label} yet, because it isn't switched on."
    else:
        line = f"To be clear: I can't {label} yet."
        if not how:
            line += " That isn't part of this build; it can be added later."
    if how:
        line += f" To add it: {how}."
    return line


def offer_corrections(text: Any,
                      abilities: Optional[list[dict]] = None) -> list[str]:
    """One plain correction per offered ability this build does not have.

    ``abilities`` is :func:`puffpai.build_abilities.abilities` output (read
    live when ``None``). An ability that is available gets no correction; one
    whose state is unknown gets a "can't be sure" line, never silence.
    """
    try:
        from puffpai import build_abilities as ba
        rows = ba.abilities() if abilities is None else list(abilities)
    except Exception:
        return []
    lines: list[str] = []
    for ability_id in offered_abilities(text, rows):
        found = ba.get(ability_id, rows)
        if found is not None and found.get("status") != ba.AVAILABLE:
            lines.append(correction(found))
    return lines


# ---------------------------------------------------------------------------
# 6. a way to add one thing, given for another
# ---------------------------------------------------------------------------
#
# Even when the prompt says on every line whether a way to add it exists, the
# bot can get this wrong: asked "show me my telescope", it said "you can add it
# by running puffpai setup again and connecting Home Assistant"; asked for a
# picture of the day it said "run: puffpai knowledge sync"; asked for daily
# updates it said "run: puffpai persona new". Each is a real command, just not
# a way to add THAT. So: when a reply says it lacks X and hands over a way to
# add something else, or a command that adds nothing, one plain line says so.
#
# Generic by construction: X and the other thing are ability rows; the words
# are the rows' own words plus a few sentence shapes ("is not part of", "add
# it", "this feature"). No topic appears here.
#
# How a reply is read, sentence by sentence:
# 1. What it says it lacks: absent rows named in a sentence that says "no" in
#    any common way, or named in the owner's own question.
# 2. Where it hands over a way to add: a sentence repeating a row's way to add
#    (only the parts no other row shares), or naming a ``puffpai ...`` command
#    that is no row's way to add.
# 3. That hand-over is fine when it belongs to something the reply or the
#    question is about, or when its own sentence is plainly about its own
#    thing ("if you want lights too, connect Home Assistant"). It is wrong when
#    it points back at the missing thing ("add it", "this feature", or names
#    it) or when it stands there bare after a "can't".

_COMMANDISH = re.compile(r"(?i)\bpuffpai(?:\s+[a-z][a-z-]*){1,2}")
_PROPER = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")

#: Every common way a sentence says "I don't have that". Wider than
#: :data:`_DISOWNED` on purpose: this only decides what a reply is ABOUT.
_LACKS = re.compile(
    r"(?ix)" + _DISOWNED.pattern[len("(?ix)"):] +
    r"|\b(?:is|are|was|were)\s+not\b|\b(?:isn't|aren't|wasn't|weren't|"
    r"doesn't|does\s+not)\b|\bnot\s+(?:part|available|connected|included|"
    r"set\s+up|possible|supported|yet|in\s+this)\b|\bno\s+(?:way|ability|"
    r"access|option|support)\b|\bwithout\b|\black(?:s|ing)?\b|\bmissing\b")

#: A hand-over that points back at the thing just said to be missing.
_BACK = re.compile(
    r"(?ix)\b(?:add|get|enable|set\s*up|include|connect|allow)\s+"
    r"(?:it|that|this|them|those)\b"
    r"|\b(?:that|this|the|such\s+a)\s+(?:feature|ability|option|capability"
    r"|function|functionality)\b|\bto\s+do\s+(?:that|this|so)\b")


#: A sentence that hands something over as a way to add.
_ADDING = re.compile(
    r"(?ix)\b(?:add|adding|install|installing|enable|enabling|set\s*up|"
    r"setting\s+up|connect|connecting|get|turn\s+on|switch\s+on)\b")


def _fix_fragments(how: str) -> list[str]:
    """Pieces of a way-to-add that identify it when a model repeats it."""
    how = str(how or "").strip().rstrip(".")
    if not how:
        return []
    pieces = [how]
    # Its clauses, since a model repeats them in its own order.
    pieces += [c.strip() for c in re.split(r",\s*then\s+|\s+and\s+|:\s*", how)
               if len(c.split()) >= 3]
    pieces += [m.group(0) for m in _COMMANDISH.finditer(how)]
    pieces += [m.group(0) for m in _PROPER.finditer(how)
               if not m.group(0).split()[0].lower() in ("run", "put", "open")]
    return pieces


def _without(text: str, pieces: list[str]) -> str:
    out = text
    for piece in sorted(pieces, key=len, reverse=True):
        out = re.sub(re.escape(piece), " ", out, flags=re.IGNORECASE)
    return out


def _command_key(cmd: str) -> str:
    """``puffpai knowledge sync --x`` -> ``puffpai knowledge``."""
    return " ".join(cmd.lower().split()[:2])


def _names(text: str, r: dict) -> bool:
    """True when ``text`` talks about row ``r``'s thing."""
    from puffpai import build_abilities as ba
    pattern = _OFFERED.get(str(r.get("id") or ""))
    if pattern is not None and pattern.search(text):
        return True
    wanted = {w for w in ba.label_terms(r) if w not in _WEAK}
    return bool(wanted & _stems(text))


def _plain_label(r: dict) -> str:
    from puffpai import build_abilities as ba
    return re.sub(r"\s*\([^)]*\)", "",
                  ba.lower_first(str(r.get("label") or "")))


def _misplaced_line(what_it_is: str, subject: dict) -> str:
    how = str(subject.get("how_to_add") or "").strip().rstrip(".")
    tail = (f" To add that: {how}." if how
            else " There is no way to add that yet.")
    return (f"To be clear: {what_it_is} does not let me "
            f"{_plain_label(subject)}.{tail}")


def misplaced_fix_corrections(text: Any,
                              abilities: Optional[list[dict]] = None,
                              question: Any = "") -> list[str]:
    """Lines for a way-to-add handed out for a thing it does not add.

    ``question`` is the owner's message this turn, when there is one: what it
    names counts as what the reply is about.
    """
    body = str(text or "")
    if not body.strip():
        return []
    try:
        from puffpai import build_abilities as ba
        rows = ba.abilities() if abilities is None else list(abilities)
    except Exception:
        return []
    fixes = [r for r in rows if r.get("how_to_add")]
    # A piece two rows share ("puffpai setup again") says nothing about which
    # one is meant; only a row's own pieces identify it.
    count: dict[str, int] = {}
    for r in fixes:
        for p in {p.lower() for p in _fix_fragments(r["how_to_add"])}:
            count[p] = count.get(p, 0) + 1
    own_pieces = [[p for p in _fix_fragments(r["how_to_add"])
                   if count.get(p.lower()) == 1] for r in fixes]
    every_piece = [p for r in fixes for p in _fix_fragments(r["how_to_add"])]
    known_commands = {_command_key(m.group(0)) for r in fixes
                      for m in _COMMANDISH.finditer(r["how_to_add"])}

    sentences = _SENTENCE.findall(body)
    plain = [_without(s, every_piece
                      + [m.group(0) for m in _COMMANDISH.finditer(s)])
             for s in sentences]
    asked = str(question or "")
    lack_text = " ".join(p for p in plain if _LACKS.search(p))
    about_stems = _stems(lack_text + " " + asked)
    subjects = [r for r in ba.absent(rows)
                if _names(lack_text, r) or (asked and _names(asked, r))]
    if not subjects:
        return []

    lines: list[str] = []

    def _judge(i: int, owner: Optional[dict], what_it_is: str) -> None:
        """One hand-over, in sentence ``i``: say so if it is the wrong one."""
        others = [s for s in subjects if s is not owner]
        if not others:
            return
        # Words the model echoes when it repeats a way to add ("... when it
        # asks about a smart home") are not evidence of what it is about.
        echo = _stems(str(owner.get("how_to_add") or "")) if owner else set()
        own: set[str] = set()
        if owner is not None:
            own = {w for w in ba.label_terms(owner) if w not in _WEAK} - echo
            for other in others:
                own -= ba.label_terms(other)
            if own & about_stems:
                return   # the reply or the question is about this thing too

        def _named(text: str) -> list[dict]:
            stems = _stems(text)
            out = []
            for s in others:
                pattern = _OFFERED.get(str(s.get("id") or ""))
                wanted = ({w for w in ba.label_terms(s) if w not in _WEAK}
                          - echo)
                if owner is not None:
                    wanted -= ba.label_terms(owner)
                if (pattern is not None and pattern.search(text)) or (
                        wanted & stems):
                    out.append(s)
            return out

        back = bool(_BACK.search(sentences[i]))
        if not back and not _named(plain[i]):
            if own & _stems(plain[i]):
                return   # its own sentence offers it for its own thing
            if owner is None and not _ADDING.search(sentences[i]):
                return   # a command named, not handed out as a way to add
        # A way to add points at the sentence it is in and the one before it.
        near = (plain[i - 1] + " " if i else "") + plain[i]
        near_stems = _stems(near)
        named = _named(near)
        if not named:
            if own & near_stems:
                return   # it is offered for its own thing
            if not back:
                return   # it points at nothing in particular
            named = others
        line = _misplaced_line(what_it_is, named[0])
        if line not in lines:
            lines.append(line)

    for i, sentence in enumerate(sentences):
        low = sentence.lower()
        for r, pieces in zip(fixes, own_pieces):
            if any(p.lower() in low for p in pieces):
                _judge(i, r, f"that way of adding things only helps me "
                             f"{_plain_label(r)}. It")
        for m in _COMMANDISH.finditer(sentence):
            cmd = m.group(0).strip()
            if _command_key(cmd) in known_commands:
                continue
            _judge(i, None, f'"{cmd}"')
    return lines


def guard_stream(tokens: Any, allowed: Any = None,
                 abilities: Optional[list[dict]] = None, question: Any = ""):
    """:func:`check_reply` for a streamed reply: yields the guarded tokens.

    Made-up links come out as the text streams (:class:`LinkStreamGuard`);
    once the model is done, the corrections for any false offer follow as one
    last piece. ``allowed`` is a set of addresses or a callable returning one,
    read when each link completes, so sources a tool reports mid-stream count.
    ``question`` is the owner's message this turn.
    """
    guard = LinkStreamGuard(allowed)
    said = ""
    for token in tokens:
        out = guard.feed(token)
        if out:
            said += out
            yield out
    tail = guard.flush()
    if tail:
        said += tail
        yield tail
    try:
        fixes = [line for line in (offer_corrections(said, abilities)
                                   + misplaced_fix_corrections(said, abilities,
                                                               question))
                 if line not in said]
    except Exception:
        logger.debug("offer check skipped on a stream", exc_info=True)
        fixes = []
    if fixes:
        yield "\n\n" + "\n".join(fixes)


def check_reply(text: Any, *, allowed_urls: Any = (),
                abilities: Optional[list[dict]] = None,
                question: Any = "") -> str:
    """The whole post-reply guard: made-up links out, false offers corrected.

    ``question`` is the owner's message this turn; it helps tell which thing
    a way to add was handed out for.
    """
    body, _removed = strip_unbacked_links(text, allowed_urls)
    fixes = [line for line in (offer_corrections(body, abilities)
                               + misplaced_fix_corrections(body, abilities,
                                                           question))
             if line not in body]
    if fixes:
        body = (body.rstrip() + "\n\n" + "\n".join(fixes)).strip()
    return body


__all__ = [
    "LinkStreamGuard",
    "PICTURE_REMOVED",
    "check_reply",
    "guard_stream",
    "misplaced_fix_corrections",
    "claims_an_action",
    "correction",
    "list_suggestion",
    "offer_corrections",
    "offered_abilities",
    "strip_unbacked_links",
    "urls_in",
    "reminder_capability",
    "reminder_thing",
    "reminder_is_the_whole_message",
    "reminder_note",
    "reminder_request",
    "scrub_claims",
]
