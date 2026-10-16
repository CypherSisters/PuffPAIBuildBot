"""Add more after the build: ONE find -> make -> check -> yes loop.

The wizard runs this loop once, for the whole build. After the build, the
owner re-enters it from three places, and all three run the code here:

* chat -- "add dinosaur fossils to you" (:func:`chat_turn`);
* the dashboard's **Add to <bot>** page (``add_more_api``);
* the terminal -- ``puffpai add "<words>"`` (``add_more_cli``).

The loop
--------
1. **Find** (:func:`find`). The owner's words are privacy-scrubbed, turned
   into topic words, and handed to every registered *provider*, one section
   at a time: what it will know (books), live info (live cards), updates
   (a daily note) and gadgets. Everything found is kept in a *session* on the
   server, with the catalog record behind each line. The owner is shown plain
   lines and numbers; nothing the owner (or a model) sends back can name an
   address -- a request carries item ids from that session, and only those.
2. **Search online too?** A search starts on the lists saved on this
   computer. Going online is always a second, separate step that first says
   exactly which words would be sent ("It sends only: dinosaur, fossil"), and
   only when this bot may go online at all. A chat sentence never searches
   online by itself: people tell their bot private things, and those words
   must not leave the house.
3. **Yes** (:func:`confirm` + :func:`approve`). From a browser or chat the
   yes is a consent ticket (:mod:`puffpai.security.consent_tickets`): the
   server builds the sentence, the ticket is bound to that device, lasts ten
   minutes and works once. No PIN: being paired is the gate. The terminal on this computer says yes directly. Voice and
   the keyless chat routes never say yes.
4. **Make and check** -- each approved line is carried out by the *applier*
   for its kind. Anything made from an outside service is checked by
   Drawbridge's one policy (:func:`puffpai.drawbridge.check_addition`) before
   it is added; a ``block`` is never added.
5. **Tell the truth** -- after adding, the real state is read back. A live
   card whose website the bot is not allowed to use is reported as added but
   switched off, with a way to switch it on (another consent ticket), never
   as "Added".

**On the home page.** A line with something to show (a live card, an update,
a look) is offered "Put it on my home page" at the yes (ticked for live
cards); a chat request that names the home page ("add the ISS tracker to my
dashboard") puts what is picked there. Placement is kept by
:mod:`puffpai.home_placement` and read by the home page (``/front`` ``placed``).

**A question waits on the server.** A chat question ("which ones?", "look
online too?") is held here per paired device, never in the model's memory of
the chat. If the owner says something else in between, the question is kept
(for :data:`QUESTION_SECONDS`, through :data:`MAX_INTERRUPTS` other
messages): a later numbered pick ("the first one") or a bare "yes" is said
back with its sentence before anything is added, a bare "no" lets it go
plainly, and the daily-update offer's own later yes/no question keeps its
bare yes.

Everything added is written to ``<state>/additions.jsonl`` with how to take
it out again (:func:`added`, :func:`take_out`). When nothing is found, the
owner is told where BuildBot looked and what works today, and the topic
words (never the raw sentence) are kept in ``<state>/add_wishes.jsonl`` for
a later version that can make new tools.

Plugging in (for other parts)
-----------------------------
**Providers** add lines to a section::

    add_more.register_provider("looks", find_looks, heading="How it looks")

    def find_looks(words: str, ctx: AddContext) -> list[dict]:
        ...  # ProposalItems (puffpai.wizard.proposal shape)
        ctx.keep(item["id"], record)   # server-side record for the applier

An item is ``{"id", "section", "label", "detail", "size_bytes",
"outside_host", "default_on", "needs_yes", "apply": {"kind", "ref"}}``;
``section`` must be the section it was registered for. An item may also
say ``"rides_along": True``: it is not evidence the words were found (a
book made from the words, a daily update) and is offered only next to a line
that is. Items are validated
(labels are plain text, hosts are host names, sizes are whole numbers or
unknown). Provider text is data: it is capped and shown, never followed.

**Appliers** carry out one approved line of a kind::

    add_more.register_applier("look", apply_look)

    def apply_look(item: dict, ctx: ApplyContext) -> dict:
        record = ctx.candidate          # what the provider kept
        ...
        return {"status": "added", "line": "Your bot has a new look.",
                "undo": {"kind": "look", "ref": look_id}}

An applier returns ``status`` (``added``, ``added_off``, ``working``,
``held`` or ``refused``) and one plain ``line``; optionally ``checks``
(Drawbridge rows), ``drawbridge`` (its verdict), ``hosts``, ``undo``
(how :func:`take_out` removes it; register a remover with
:func:`register_remover`), ``learn_id`` (a knowledge progress record) and
``switch_on`` (``{"service": <outside service id>}`` when it was added but
its website is switched off). Anything made from outside must go through
:func:`puffpai.drawbridge.check_addition` in the applier. A kind with no
applier is shown as information and cannot be ticked.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlsplit

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: A session lasts this long; a chat question is answerable for this long.
SESSION_SECONDS = 30 * 60
QUESTION_SECONDS = 15 * 60
#: At most this many sessions are kept in memory (oldest dropped first).
MAX_SESSIONS = 50
#: At most this many lines may be approved at once.
MAX_APPROVE = 10
#: At most this many lines per section are shown in chat (the rest are on
#: the Add page).
CHAT_PER_SECTION = 2
#: At most this many topic words are searched.
MAX_TERMS = 8
#: A held chat question survives at most this many other messages.
MAX_INTERRUPTS = 3
#: Sections whose lines have nothing to show on the home page.
NOT_SHOWN = frozenset({"knowledge", "devices", "outside_services"})
#: What a request for the home page searches first.
SHOWN_SECTIONS = ("live_info", "updates", "apps_code", "pictures")

ADDITIONS_FILE = "additions.jsonl"
WISHES_FILE = "add_wishes.jsonl"

SURFACES = ("chat", "terminal", "browser")
ACTION = "add_on"

#: Kinds that are statements, never switches.
INFO_KINDS = frozenset({"none", "look_yourself", "ha_integration"})

_SESSION_RE = re.compile(r"^add_[0-9a-f]{16}$")
_ITEM_ID_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9_.:/-]{1,160}$")
_MARKER_RE = re.compile(r"\[[^\]]{0,60}\]")

_lock = threading.RLock()
_sessions: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_pending: dict[str, dict[str, Any]] = {}
#: When each client's last list of finds was answered and closed: a late
#: numbered reply to it is turned away here, not handed to the model.
_closed: dict[str, datetime] = {}
#: Per client: the words of a held request let go after too many other
#: messages, so a late numbered reply is told so plainly.
_let_go: dict[str, str] = {}
#: Per client: the libraries an online search is asking, and how each went.
_looking: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


class AddError(ValueError):
    """Something the owner asked for cannot be done. ``message`` is plain."""

    def __init__(self, message: str, code: str = "refused"):
        super().__init__(message)
        self.message = message
        self.code = code


# ---------------------------------------------------------------------------
# registries
# ---------------------------------------------------------------------------

@dataclass
class AddContext:
    """What a provider is handed. ``keep`` stores a record server-side."""

    words: str
    terms: list[str]
    via: str
    online: bool
    bot_name: str
    searched: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    kept: dict[str, Any] = field(default_factory=dict)
    #: The words that may LEAVE this computer: ``terms`` with people's names,
    #: health words and exact places taken out (:func:`private_words`). The
    #: online search, the Wikipedia book made from the words and the "It
    #: sends only these words" line all use these; ``terms`` stays for the
    #: search of the lists saved on this computer, which sends nothing.
    online_terms: list[str] = field(default_factory=list)
    online_words: str = ""
    #: The bot's own goal words and their library shelves (never sent
    #: anywhere): they break ties between finds, and let a find that holds
    #: the request's first word and one of them fit ("tomato" + "garden").
    goal_terms: list[str] = field(default_factory=list)
    goal_shelves: list[str] = field(default_factory=list)
    #: Hears ``(library, state)`` as each library asked online answers.
    on_library: Optional[Callable[[str, str], None]] = None
    #: Her words as jobs (``needs.from_words``, needs/v1), shared by
    #: the apps-and-code and pictures providers; None when not in this build.
    needs: Optional[dict] = None
    #: What ``find_parts`` found for ``needs`` (asked once per search).
    parts: Optional[dict] = None
    #: May the apps-and-code search go online (she asked, the bot may, and
    #: the outbound screen let at least one phrase out)?
    code_online: bool = False
    #: Her words as typed (printable, one line): what the outbound screen
    #: reads. The scrubbed words drop the very detail that makes the rest of
    #: a story private ("radiation" would leave once "chemo" was scrubbed). Read here only, never sent.
    typed: str = ""

    def keep(self, item_id: str, record: Any) -> None:
        self.kept[str(item_id)] = record


@dataclass
class ApplyContext:
    """What an applier is handed."""

    via: str
    by: str
    surface: str
    words: str
    candidate: Any = None
    session_id: str = ""


Provider = Callable[[str, AddContext], list]
Applier = Callable[[dict, ApplyContext], dict]
Remover = Callable[[dict, str], dict]

#: section -> heading, in the order sections are shown.
_SECTIONS: "OrderedDict[str, str]" = OrderedDict()
_PROVIDERS: dict[str, list[tuple[str, Provider]]] = {}
_APPLIERS: dict[str, Applier] = {}
_REMOVERS: dict[str, Remover] = {}
#: section -> test on the owner's words: a section asked for by name only.
_ONLY_WHEN: dict[str, Callable[[str], bool]] = {}


def register_provider(section: str, fn: Provider, *, heading: str = "",
                      name: str = "",
                      only_when: Optional[Callable[[str], bool]] = None) -> None:
    """Add a provider of lines for *section* (``fn(words, ctx) -> items``).

    A section is shown under *heading* (plain words; the first registration
    names it). Registering the same *name* again replaces it. With
    *only_when*, the section is searched only when ``only_when(words)`` is
    true, and then it is the only kind of section searched ("a jungle look"
    asks for a look, not for books about jungles).
    """
    if only_when is not None:
        if not callable(only_when):
            raise ValueError("only_when is a callable.")
        with _lock:
            _ONLY_WHEN[str(section)] = only_when
    if not callable(fn):
        raise ValueError("A provider is a callable.")
    section = str(section)
    if not re.match(r"^[a-z][a-z0-9_]{0,31}$", section):
        raise ValueError(f"{section!r} is not a section name.")
    with _lock:
        if section not in _SECTIONS:
            _SECTIONS[section] = str(heading or section.replace("_", " ").capitalize())
        elif heading:
            _SECTIONS[section] = str(heading)
        key = str(name or getattr(fn, "__name__", "provider"))
        rows = [(n, f) for n, f in _PROVIDERS.get(section, []) if n != key]
        rows.append((key, fn))
        _PROVIDERS[section] = rows


def register_applier(kind: str, fn: Applier) -> None:
    """Carry out approved lines whose ``apply.kind`` is *kind*."""
    if not callable(fn):
        raise ValueError("An applier is a callable.")
    with _lock:
        _APPLIERS[str(kind)] = fn


def register_remover(kind: str, fn: Remover) -> None:
    """Take out an addition whose ``undo.kind`` is *kind* (``fn(undo, by)``)."""
    if not callable(fn):
        raise ValueError("A remover is a callable.")
    with _lock:
        _REMOVERS[str(kind)] = fn


def sections() -> list[tuple[str, str]]:
    with _lock:
        return list(_SECTIONS.items())


def can_apply(kind: str) -> bool:
    return kind not in INFO_KINDS and kind in _APPLIERS


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _state_dir() -> str:
    from puffpai.config import get_state_dir
    return get_state_dir()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bot_name() -> str:
    try:
        from puffpai import bot_identity
        return bot_identity.bot_name() or "your bot"
    except Exception:  # noqa: BLE001
        return "your bot"


def title() -> str:
    return f"Add to {bot_name()}"


def _surface(surface: str) -> str:
    return surface if surface in SURFACES else "chat"


def scrub(words: Any) -> str:
    """The owner's words with private details taken out, one line, capped."""
    from puffpai.security import chat_privacy
    raw = "".join(ch for ch in str(words or "") if ch.isprintable())
    raw = " ".join(raw.split())[:200]
    if not raw:
        return ""
    try:
        cleaned, _ = chat_privacy.redact(raw)
    except Exception:  # noqa: BLE001 - never search unscrubbed words
        return ""
    # The marker a scrub leaves ("[health details kept private]") is not a
    # topic: it must never become a search word.
    cleaned = _MARKER_RE.sub(" ", str(cleaned or ""))
    return " ".join(cleaned.split())


def topic_words(words: str) -> list[str]:
    from puffpai.catalogs import goal_words
    return list(goal_words.goal_terms(words))[:MAX_TERMS]


# ---------------------------------------------------------------------------
# words that never leave: people's names, health words, exact places
# ---------------------------------------------------------------------------
# "books about my son Jake Miller's leukemia at St Jude" would offer to send
# jake, miller, leukemia and jude. The owner sees the words before saying
# yes, but the rule is that personal details never cause an outside call.
# These words are taken out of everything that can leave
# (the online search, the Wikipedia book made from the words, the "It sends
# only these words" line). The search of the lists saved on this computer
# still uses them: it sends nothing.

_RELATIONS = (
    r"sons?|daughters?|kids?|child|children|boy|girl|baby|babies|wife|husband|"
    r"partner|spouse|mom|mum|mommy|mummy|mother|dad|daddy|father|parents?|"
    r"brothers?|sisters?|siblings?|grandma|grandpa|grandmother|grandfather|"
    r"granny|nana|grandsons?|granddaughters?|grandkids|grandchildren|aunt|"
    r"uncle|cousins?|niece|nephew|friends?|boyfriend|girlfriend|fianc[eé]e?|"
    r"neighbou?rs?|teacher|doctor|boss|nanny|family|twins?")
_RELATION_RE = re.compile(
    r"(?i)\b(?:my|our|his|her|their)\s+(?:(?:little|baby|big|older|younger|"
    r"oldest|youngest|best|step|late|ex|new|twin)[\s-]+)?(" + _RELATIONS + r")\b")
_NAMED_RE = re.compile(r"(?i)\b(?:named|called|name\s+is|names\s+are)\s+")
_RESIDENCE_RE = re.compile(
    r"(?i)\b(?:live|lives|living|lived|stay|stays|staying|moved|move|born|"
    r"grew\s+up|reside|resides|address\s+is)\b(?:\s+(?:in|at|on|near|by|to|off))?\s+")
_AT_RE = re.compile(r"\b(?:at|near)\s+")
_IN_RE = re.compile(r"\b(?:in|from)\s+")
_PERSONAL_RE = re.compile(r"(?i)\b(?:my|our|me|us|we|i|i'm|we're)\b")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’.\-]*")
_CAP_RE = re.compile(r"^(?:[A-Z][a-z'’\-]+|St\.?|Mt\.?|Ft\.?|[A-Z]{2,4})$")
_POSSESSIVE_RE = re.compile(r"['’]s$", re.I)
_STOP_AFTER = frozenset({
    "about", "and", "or", "for", "to", "with", "who", "that", "which", "is",
    "was", "has", "had", "loves", "likes", "wants", "needs", "the", "a", "an",
    "of", "on", "in", "at", "from", "by", "books", "book", "some", "more",
})


def _following(text: str, start: int, most: int) -> list[str]:
    """Up to *most* word tokens after *start*, stopping at punctuation."""
    tail = re.split(r"[,.;:!?\n()]", text[start:], maxsplit=1)[0]
    return _TOKEN_RE.findall(tail)[:most]


def _bare(token: str) -> str:
    return _POSSESSIVE_RE.sub("", token.strip("'’.-")).lower()


def private_words(text: Any) -> set[str]:
    """Lowercase words in *text* that are about a person, their health or
    exactly where they are. Never raises; an empty set when unsure how."""
    raw = "".join(ch for ch in str(text or "") if ch.isprintable())[:600]
    out: set[str] = set()
    if not raw.strip():
        return out

    def add(words):
        for word in words:
            bare = _bare(word)
            if bare and bare not in _STOP_AFTER:
                out.add(bare)
    try:
        # Relatives, and a name right after one ("my son Jake Miller's ...").
        for match in _RELATION_RE.finditer(raw):
            add([match.group(1)])
            after = _following(raw, match.end(), 3)
            caps = []
            for tok in after:
                if not _CAP_RE.match(_POSSESSIVE_RE.sub("", tok)):
                    break
                caps.append(tok)
            if caps:
                add(caps)
                continue
            for i, tok in enumerate(after):
                if tok.lower() in _STOP_AFTER:
                    break
                if _POSSESSIVE_RE.search(tok):
                    add(after[:i + 1])
                    break
        # "a dog named Rex", "her name is Maria".
        for match in _NAMED_RE.finditer(raw):
            after = _following(raw, match.end(), 2)
            if after:
                add(after[:1])
                if len(after) > 1 and _CAP_RE.match(after[1]):
                    add(after[1:2])
        # Where someone lives, and a named place they are at.
        for match in _RESIDENCE_RE.finditer(raw):
            add(_following(raw, match.end(), 4))
        for match in _AT_RE.finditer(raw):
            after = _following(raw, match.end(), 3)
            caps = []
            for tok in after:
                if not _CAP_RE.match(tok):
                    break
                caps.append(tok)
            add(caps)
        if _PERSONAL_RE.search(raw) or _RELATION_RE.search(raw):
            for match in _IN_RE.finditer(raw):
                after = _following(raw, match.end(), 3)
                caps = []
                for tok in after:
                    if not _CAP_RE.match(tok):
                        break
                    caps.append(tok)
                add(caps)
        # What the privacy classifier finds, and every health word.
        from puffpai.security import data_classifier as dc
        found = dc.DataClassifier().scan_for_pii(raw, include_health=True)
        for kind in ("names", "addresses", "schools", "locations",
                     "health_conditions", "emails", "phones"):
            for value in getattr(found, kind, []) or []:
                add(_TOKEN_RE.findall(str(value)))
        for term in dc.health_terms(raw):
            add(_TOKEN_RE.findall(term))
        # The home town this bot knows, if any.
        try:
            from puffpai import home_location
            home = home_location.get_home()
            if home and home.get("source") and home.get("label"):
                add(_TOKEN_RE.findall(str(home["label"]).split(",")[0]))
        except Exception:  # noqa: BLE001 - no town saved is the normal case
            pass
    except Exception as exc:  # noqa: BLE001 - fail closed: send nothing
        logger.warning("add-more: private-word check failed (%s); nothing "
                       "will be sent", type(exc).__name__)
        raise
    from puffpai.catalogs import goal_words
    return out | {goal_words.singular(w) for w in out}


def without_words(text: str, drop: set[str]) -> str:
    """*text* with every token whose bare form is in *drop* taken out."""
    if not drop:
        return text
    from puffpai.catalogs import goal_words
    kept = []
    for token in str(text or "").split():
        bare = _bare(re.sub(r"[^\w'’.\-]", "", token))
        if bare and (bare in drop or goal_words.singular(bare) in drop):
            continue
        kept.append(token)
    return " ".join(kept)


def online_allowed() -> bool:
    """May this bot go online to search or fetch at all (its recorded setting)?"""
    try:
        from puffpai.knowledge import find_more
        return find_more.online_allowed(find_more.recorded_posture())
    except Exception:  # noqa: BLE001 - not known is not allowed
        return False


def _set_up() -> bool:
    try:
        from puffpai.knowledge import find_more
        return find_more.recorded_posture() is not None
    except Exception:  # noqa: BLE001
        return False


def _validate(item: Any, section: str) -> Optional[dict[str, Any]]:
    """One item in the shared shape, or None (logged) when it is not usable."""
    from puffpai import plain_words
    from puffpai.wizard import proposal
    if not isinstance(item, dict) or item.get("section") != section:
        return None
    try:
        # The wizard's one validator checks every field; its section list is
        # the wizard's, so the check runs under a known section and the real
        # one is put back.
        clean = proposal.validate_item(dict(item, section="knowledge"))
    except proposal.ProposalError as exc:
        logger.info("add-more: a %s line was left out: %s", section, exc)
        return None
    clean["section"] = section
    if not _ITEM_ID_RE.match(clean["id"]):
        return None
    if plain_words.check(clean["label"]):
        host = clean.get("outside_host") or ""
        clean["label"] = (f"Live: {host}" if section == "live_info" and host
                          else clean["label"])
    return clean


# ---------------------------------------------------------------------------
# built-in providers
# ---------------------------------------------------------------------------

def _book_known(ref: str) -> bool:
    """Is this book already in the bot (learned, learning or ordered)?"""
    try:
        from puffpai.knowledge import find_more, progress, text_books
        if find_more.is_text_book(ref):
            if ref in find_more.pending_orders():
                return True
            rec = progress.read(text_books.learn_id(ref)) or {}
            return rec.get("stage") not in (None, "error", "fetch_stopped")
        from puffpai.knowledge.auto_index import STATUS_REVOKED, PackIndex
        for rec in PackIndex().records():
            if rec.get("source_id") == ref and rec.get("status") != STATUS_REVOKED:
                return True
    except Exception:  # noqa: BLE001 - offer it; adding again is refused later
        return False
    return False


def _book_item(cand: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A book found by the online search, in the shared item shape."""
    from puffpai import drawbridge
    from puffpai.catalogs import kiwix_opds, knowledge_resolver
    if not cand.get("fetchable"):
        return None
    # A listing is data: words in it shaped like orders are never shown.
    # (Drawbridge still checks the book itself before anything is fetched.)
    detail = knowledge_resolver._item_detail(cand)[:400]  # noqa: SLF001
    if drawbridge.instruction_shaped(detail):
        detail = "Its description had odd words in it, so they are not shown."
    size = cand.get("size_bytes")
    if not isinstance(size, int) or size <= 0:
        ceiling = cand.get("size_ceiling")
        size = ceiling if isinstance(ceiling, int) and ceiling > 0 else None
    size_part = f" ({knowledge_resolver.download_words(cand)})"
    name = str(cand.get("name") or cand.get("id"))
    if drawbridge.instruction_shaped(name):
        name = "A book whose name had odd words in it"
    if len(name) + len(size_part) > 80:
        name = name[:80 - len(size_part) - 3].rstrip() + "..."
    return {"id": "knowledge:" + str(cand["id"]), "section": "knowledge",
            "label": name + size_part,
            "detail": detail,
            "size_bytes": size,
            "outside_host": str(cand.get("host") or "") or kiwix_opds.DOWNLOAD_HOST,
            "default_on": False, "needs_yes": True,
            "apply": {"kind": "knowledge.add", "ref": str(cand["id"])}}


SAVED_BOOKS = ("the book lists saved on this computer (Wikipedia, Project "
               "Gutenberg, OpenStax and the Kiwix library)")
SAVED_LIVE = "the list of free live-info websites saved on this computer"


#: The most books offered for one request (the topic book comes on top).
MAX_BOOKS = 3


def _is_general(cand: dict[str, Any]) -> bool:
    """A general encyclopedia, however it was found: flagged by the resolver,
    a book the Kiwix catalog row names as a general reference, or a row on
    BuildBot's own list whose domain is ``general``. Broad tags on such a row
    (it lists many subjects) must not turn it into a match for the words."""
    if cand.get("general"):
        return True
    from puffpai import catalogs, source_registry
    from puffpai.catalogs import kiwix_opds
    book = str(cand.get("book") or "")
    if book:
        row = catalogs.catalog_row(kiwix_opds.CATALOG_ID) or {}
        refs = {str(r.get("book") or "") for r in row.get("general_reference") or []
                if isinstance(r, dict)}
        if book in refs:
            return True
    entry = source_registry.get_source(str(cand.get("id") or ""))
    return bool(entry) and str(entry.get("domain") or "") == "general"


def _knowledge_provider(words: str, ctx: AddContext) -> list[dict[str, Any]]:
    """Books about the words. General encyclopedias are not offered here
    (they are not about the words; the Knowledge page has them), and a
    Wikipedia book made from the words rides along only with a real match."""
    from puffpai.catalogs import knowledge_resolver
    query = " ".join(ctx.terms)
    ctx.searched.append(SAVED_BOOKS)
    found = knowledge_resolver.find(query, live=False, limit=16,
                                    include_general=False)
    cands = [c for c in found.get("candidates") or [] if c.get("fetchable")]
    private_left_out = list(ctx.online_terms) != list(ctx.terms)
    if private_left_out:
        # A Wikipedia book made from these words would send them when it is
        # fetched: make it from the words that may leave, or not at all.
        cands = [c for c in cands
                 if knowledge_resolver.tier_of(c) != "wikipedia_topic"]
        if ctx.online_terms:
            safe = knowledge_resolver.find(" ".join(ctx.online_terms),
                                           live=False, limit=16,
                                           include_general=False)
            cands += [c for c in safe.get("candidates") or []
                      if c.get("fetchable")
                      and knowledge_resolver.tier_of(c) == "wikipedia_topic"]
    if ctx.goal_terms and ctx.terms:
        # The request's subject words with the bot's main goal word ("tomato"
        # for a garden bot finds "Tomato Culture", filed under gardening,
        # for "tomato diseases" and for "problems that affect tomato plants").
        # The saved lists only: the goal words are never sent anywhere.
        from puffpai.catalogs import goal_words
        for word in (goal_words.subject_words(ctx.words) or ctx.terms[:1])[:3]:
            have = {str(c.get("id")) for c in cands}
            near = knowledge_resolver.find(
                f"{word} {ctx.goal_terms[0]}", live=False, limit=16,
                include_general=False)
            cands += [c for c in near.get("candidates") or []
                      if c.get("fetchable") and str(c.get("id")) not in have
                      and knowledge_resolver.tier_of(c) != "wikipedia_topic"]
    if ctx.online:
        from puffpai.knowledge import find_more
        # Only the words that may leave this computer (never a name, a health
        # word or a place): see private_words.
        result = find_more.search(" ".join(ctx.online_terms), live=True,
                                  on_library=ctx.on_library)
        if result.get("searched") == "live":
            ctx.searched.append("the book libraries online")
        if result.get("note"):
            ctx.notes.append(str(result["note"]))
        have = {str(c.get("id")) for c in cands}
        cands = [c for c in result.get("candidates") or []
                 if c.get("fetchable") and str(c.get("id")) not in have] + cands
    out: list[dict[str, Any]] = []
    topic = None
    fitting: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for cand in cands:
        if _is_general(cand) or _book_known(str(cand.get("id"))):
            continue
        tier = knowledge_resolver.tier_of(cand)
        if tier == "wikipedia_topic":
            topic = topic or cand
            continue
        # A book must fit what the owner asked for, not share one word with
        # it: 'Parts' in a flight memoir is not 'car parts', 'Cross' in a
        # statistics site is not 'cross stitch'.
        strength = _fit(ctx, cand.get("name"), cand.get("what_it_is"),
                        cand.get("subjects") or cand.get("match_terms"))
        if strength[0]:
            fitting.append((strength, cand))
    # The strongest fit first; the bot's own goal words break a tie.
    fitting.sort(key=lambda row: (-row[0][0], -row[0][1]))
    for _strength, cand in fitting[:MAX_BOOKS]:
        item = _book_item(cand)
        if item:
            ctx.keep(item["id"], cand)
            out.append(item)
    if topic is not None:
        item = _book_item(topic)
        if item:
            ctx.keep(item["id"], topic)
            out.append(dict(item, rides_along=True))
    return out


def _fit(ctx: AddContext, name: Any, opening: Any, subjects: Any) -> tuple[int, int]:
    """How well one find fits the owner's words (:func:`goal_words.match_strength`).

    *subjects* is what the catalog files it under: a book's own subjects, or
    the words a website's list gives for it.
    """
    from puffpai.catalogs import goal_words
    return goal_words.match_strength(
        ctx.words, f"{name or ''} {opening or ''}",
        subjects=[str(s) for s in subjects or ()],
        goal=ctx.goal_terms, shelves=ctx.goal_shelves)


def bot_goal_words() -> tuple[list[str], list[str]]:
    """The bot's own goal words and their library shelves, from its build record."""
    try:
        from puffpai import offers
        from puffpai.catalogs import goal_words, knowledge_resolver
        goal = offers.bot_goal()
        terms = goal_words.goal_terms(goal)
        return terms, knowledge_resolver.shelf_words(goal, terms)
    except Exception:  # noqa: BLE001 - no goal known: the words alone decide
        return [], []


def _fixed_example(cand: dict[str, Any]) -> str:
    """For a service whose card always asks the same example question, say so."""
    if cand.get("card_url"):
        return ""
    try:
        pairs = [(k, v) for k, v in parse_qsl(urlsplit(
            str(cand.get("sample_endpoint") or "")).query)
            if k.lower() not in ("format", "page_size", "limit", "per_page",
                                 "media_type", "lang", "language")]
    except ValueError:
        return ""
    shown = [f"{k} {v}" for k, v in pairs if re.match(r"^[A-Za-z0-9 .-]{1,30}$", v)
             and re.match(r"^[A-Za-z][A-Za-z_]{0,20}$", k)][:4]
    if not shown:
        return ""
    return (f"It always shows the website's own example ({', '.join(shown)}); "
            f"picking your own is not built yet.")


def _installed_hosts() -> set[str]:
    try:
        from puffpai import feeds
        return {h for _m, feed in feeds.feed_apps() for h in feeds.hosts_of(feed)}
    except Exception:  # noqa: BLE001
        return set()


def _live_provider(words: str, ctx: AddContext) -> list[dict[str, Any]]:
    from puffpai import feeds, plain_words
    from puffpai.catalogs import live
    ctx.searched.append(SAVED_LIVE)
    cands = live.discover_live(live.terms_from_goal(words) or ctx.terms, limit=12)
    by_id = {str(c.get("id")): c for c in cands}
    have = _installed_hosts()
    out: list[dict[str, Any]] = []
    hosts: set[str] = set()
    for item in feeds.proposal_items(words, [], candidates=cands):
        host = str(item.get("outside_host") or "")
        if host in hosts or host in have:
            continue
        ref = str(item["apply"]["ref"])
        detail = str(item.get("detail") or "")
        # A website must fit what the owner asked for, by its name, its
        # description or the words its list files it under: 'map' alone is
        # not 'dinosaur fossil maps'.
        cand = by_id.get(ref) or {}
        if not _fit(ctx, item.get("label"), detail, cand.get("match_terms"))[0]:
            continue
        # Not also by meaning here: measured, a short request
        # ("pterosaurs", "bands and albums") read as one job scored fitting
        # cards under the floor (Paleobiology Database 0.20, a stargazing
        # forecast for a telescope 0.24, Deezer 0.28); the website's own subject
        # list above is the better guide for these.
        hosts.add(host)
        if plain_words.check(detail):
            detail = (f"Comes from {host}. What leaves your computer: a request "
                      f"for the data and your internet address.")
        if item["apply"]["kind"] == "look_yourself":
            item = dict(item, detail=detail, apply={"kind": "look_yourself",
                                                    "ref": ref},
                        default_on=False)
        else:
            cand = by_id.get(ref)
            if cand is None:
                continue
            ctx.keep(item["id"], cand)
            # The example note goes first: the summary cuts long details.
            item = dict(item, detail=(_fixed_example(cand) + " " + detail).strip())
        out.append(item)
        if len(out) >= 3:
            break
    return out


def _updates_provider(words: str, ctx: AddContext) -> list[dict[str, Any]]:
    from puffpai import offers, routines
    if routines.get_routine(offers.ROUTINE_ID) is not None:
        return []
    # The daily update is the bot's, not the request's: it is named from the
    # bot's own topic ("A daily garden update"), never from the words just
    # asked for ("A daily cross update" for "cross stitch tips").
    return [dict(item, rides_along=True)
            for item in offers.proposal_items("", [], topic=offers.bot_topic())]


def home_assistant_in_use() -> bool:
    """Is Home Assistant set up for this bot (switched on, with its address and key)?"""
    try:
        from puffpai import setup_ops
        return bool(setup_ops.ha_configured())
    except Exception:  # noqa: BLE001 - unreadable settings: not in use
        return False


def _meaning_fits(ctx: AddContext, texts: list[str]) -> list[bool]:
    """Does each line fit one of her jobs by meaning (no line matched on a
    bare word)? The same floor as the first look at a find
    (``meaning.BLURB_FLOOR``). With no jobs or no reading helper every line
    stays (the word match already decided, as before)."""
    jobs = [j for j in (ctx.needs or {}).get("jobs") or [] if isinstance(j, dict)]
    if not texts or not jobs:
        return [True] * len(texts)
    meaning = _optional("puffpai.catalogs.meaning")
    if meaning is None or meaning.helper_state() != "ready":
        return [True] * len(texts)
    try:
        kind = str((ctx.needs or {}).get("kind") or "")
        jv = meaning.embed([meaning.job_text(kind, j) for j in jobs])
        tv = meaning.embed([str(t or "") for t in texts])
        best = (tv @ jv.T).max(axis=1)
    except Exception:  # noqa: BLE001 - no reading helper after all: words decide
        return [True] * len(texts)
    return [float(b) >= meaning.BLURB_FLOOR for b in best]


def _gadgets_provider(words: str, ctx: AddContext) -> list[dict[str, Any]]:
    # A Home Assistant line ("Your ..., if you have Home Assistant") is shown
    # only when Home Assistant is set up.
    if not home_assistant_in_use():
        return []
    from puffpai.catalogs import live
    found = [item for item in live.proposal_items(words)
             if item.get("section") == "devices"]
    fits = _meaning_fits(ctx, [f"{i.get('label') or ''}. {i.get('detail') or ''}"
                               for i in found])
    out = []
    for item, fit in zip(found, fits):
        if not fit:
            continue
        detail = (str(item.get("detail") or "") + " Connecting gadgets comes in "
                  "a later version, so nothing is connected yet.").strip()
        out.append(dict(item, detail=detail,
                        apply={"kind": "none", "ref": ""}, default_on=False,
                        needs_yes=False))
    return out


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

def _prune(now: float) -> None:
    for sid in [s for s, v in _sessions.items() if v["expires"] <= now]:
        _sessions.pop(sid, None)
    while len(_sessions) > MAX_SESSIONS:
        _sessions.popitem(last=False)


def _sections_for(words: str, only: Any = None) -> list[str]:
    """The sections searched for *words*: those asked for by name, else the
    rest. ``only`` names them outright (chat's "make it look like ...")."""
    names = [s for s, _heading in sections()]
    if only:
        return [s for s in names if s in set(only)]
    asked = []
    for section in names:
        test = _ONLY_WHEN.get(section)
        try:
            if test is not None and test(words):
                asked.append(section)
        except Exception:  # noqa: BLE001 - a broken test never asks
            continue
    return asked or [s for s in names if s not in _ONLY_WHEN]


def find(words: Any, *, via: str, client: str, online: bool = False,
         only: Any = None, keep_out: Any = (),
         place_home: bool = False) -> dict[str, Any]:
    """Search every section for *words*. Returns the session (server-side).

    ``online`` asks the book libraries online too; it is honoured only when
    this bot may go online. Nothing is sent anywhere when it is False.
    ``only`` limits the search to these sections. ``keep_out`` is more words
    that must never be sent (the first search's, when the owner says "look
    online" to it). ``place_home``: the owner asked for it on the home page,
    so what they pick that has something to show goes there.
    """
    clean = scrub(words)
    terms = topic_words(clean)
    try:
        private = private_words(words) | {str(w).lower() for w in keep_out or ()}
        online_words = without_words(clean, private)
        online_terms = topic_words(online_words)
    except Exception:  # noqa: BLE001 - not sure what is private: send nothing
        private, online_words, online_terms = set(terms), "", []
    # The same needs engine as the wizard reads her words as jobs.
    # Its personal and held words never leave either (both checks apply).
    # The needs step and its screen read her words as typed, never the scrubbed ones.
    typed = " ".join("".join(ch for ch in str(words or "") if ch.isprintable()).split())[:600]
    needs_doc = needs_for(typed or clean)
    if needs_doc is not None:
        extra = {str(w).lower() for w in needs_doc.get("private_left_out") or []}
        extra |= {str(h.get("word") or "").lower() for h in needs_doc.get("held") or []
                  if not h.get("allowed")}
        extra.discard("")
        if extra - private:
            private |= extra
            online_words = without_words(online_words, extra)
            online_terms = [t for t in topic_words(online_words)]
    # What may leave also passes the outbound screen, read against her words
    # as typed (otherwise "book jose baseball" or "book rash arm" goes out). Any error sends nothing.
    try:
        from puffpai.security import outbound_query
        screened = outbound_query.screen_words(online_terms, goal=str(words or "")[:600])
    except Exception:  # noqa: BLE001 - fail closed
        screened = []
    if screened != list(online_terms):
        private |= set(online_terms) - set(screened)
        online_terms, online_words = screened, " ".join(screened)
    may_online = online_allowed()
    goal_terms, goal_shelves = bot_goal_words()
    ctx = AddContext(words=clean, terms=terms, via=str(via), bot_name=bot_name(),
                     online=bool(online and may_online and online_terms),
                     online_terms=online_terms, online_words=online_words,
                     goal_terms=goal_terms, goal_shelves=goal_shelves,
                     needs=needs_doc, typed=typed)
    code_phrases = code_sends(needs_doc)
    ctx.code_online = bool(online and may_online and code_phrases)
    if ctx.online and client:
        ctx.on_library = _start_looking(str(client))
    left_out = [t for t in terms if t not in online_terms]
    if left_out and may_online:
        ctx.notes.append("Words that could be about you (a name, a health word "
                         "or a place) are never sent anywhere.")
    if online and not may_online:
        ctx.notes.append("This bot is set to stay off the internet, so only the "
                         "lists saved on this computer were searched.")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    if terms:
        for section in _sections_for(clean, only):
            for name, provider in list(_PROVIDERS.get(section, [])):
                try:
                    found = provider(clean, ctx) or []
                except Exception as exc:  # noqa: BLE001 - one never sinks the rest
                    logger.warning("add-more: %s could not search: %s", name, exc)
                    continue
                for raw in found:
                    item = _validate(raw, section)
                    if item is None or item["id"] in seen:
                        continue
                    seen.add(item["id"])
                    item["rides_along"] = bool(isinstance(raw, dict)
                                               and raw.get("rides_along"))
                    items.append(item)
    # Lines that are not evidence the words were found (a Wikipedia book made
    # from the words, a daily update) are offered only next to one that is...
    if not any(can_apply(i["apply"]["kind"]) and not i["rides_along"]
               for i in items):
        # ...except in the online step (the owner said "look online", so
        # sending the words is what they asked for): then the Wikipedia book
        # made from the words is offered on its own.
        fallback = (_topic_fallback(items, ctx.online_words)
                    if ctx.online else None)
        items = ([fallback] if fallback else []) + [
            i for i in items if not can_apply(i["apply"]["kind"])]
    if ctx.online and client:
        _stop_looking(str(client))
    sends: list[str] = []
    if online_terms:  # only words that may leave; never a name, health or place
        from puffpai.catalogs import goal_words, knowledge_resolver
        query = " ".join(online_terms)
        first = list(goal_words.goal_terms(query))
        sends = list(dict.fromkeys(first + list(
            knowledge_resolver.shelf_words(query, first))))
    # The apps-and-code search sends only the phrases the outbound screen let
    # out; when it went online, exactly the ones it sent.
    sent_code = [s["phrase"] for s in (ctx.parts or {}).get("sends") or []
                 if isinstance(s, dict) and s.get("phrase")]
    sends = list(dict.fromkeys(sends + (sent_code if ctx.code_online else code_phrases)))
    number = 0
    for item in items:
        if can_apply(item["apply"]["kind"]):
            number += 1
            item["n"] = number
        else:
            item["n"] = None
    now = time.time()
    session = {
        "id": "add_" + secrets.token_hex(8),
        "created": now, "expires": now + SESSION_SECONDS,
        "client": str(client or ""), "via": str(via),
        "words": clean, "terms": terms, "sends": sends,
        "searched": list(dict.fromkeys(ctx.searched)),
        "notes": list(dict.fromkeys(ctx.notes)),
        # Looking online is a book search, so it is offered only when books
        # were looked for (not for a look or a sign-up key).
        "searched_online": ctx.online or ctx.code_online,
        "online_possible": (may_online and not ctx.online and not ctx.code_online and (
            bool(online_terms) and "knowledge" in _sections_for(clean, only)
            or bool(code_phrases) and "apps_code" in _sections_for(clean, only))),
        # Her words as jobs, so the page can group what was found.
        "jobs": [{"id": str(j.get("id")), "name": str(j.get("name") or "")}
                 for j in (needs_doc or {}).get("jobs") or []],
        # Server-side only: words the online step must leave out too.
        "keep_out": sorted(private),
        "items": items, "kept": dict(ctx.kept), "results": {},
        "place_home": bool(place_home),
    }
    if not number:
        session["wish_kept"] = _keep_wish(session)
    with _lock:
        _prune(now)
        _sessions[session["id"]] = session
    return session


def _start_looking(client: str) -> Callable[[str, str], None]:
    """Start a record of the libraries asked for *client*; the hook updates it."""
    record: dict[str, Any] = {"active": True, "libraries": OrderedDict(),
                              "started": time.time()}
    with _lock:
        _looking[client] = record
        _looking.move_to_end(client)
        while len(_looking) > MAX_SESSIONS:
            _looking.popitem(last=False)

    def hear(library: str, state: str) -> None:
        with _lock:
            record["libraries"][str(library)] = str(state)
    return hear


def _stop_looking(client: str) -> None:
    with _lock:
        record = _looking.get(client)
        if record is not None:
            record["active"] = False


def looking(client: str) -> dict[str, Any]:
    """How the online search for *client* is going, in plain lines."""
    from puffpai.catalogs import knowledge_resolver
    with _lock:
        record = _looking.get(str(client or ""))
        libraries = list((record or {}).get("libraries", {}).items())
        active = bool(record and record.get("active"))
    names = knowledge_resolver.LIBRARY_NAMES
    rows = []
    for library, state in libraries:
        name = names.get(library, "A library")
        rows.append({"library": library, "state": state, "line": {
            "asking": f"{name}: still looking",
            "answered": f"{name}: answered",
        }.get(state, f"{name}: didn't answer this time")})
    count = len(rows)
    noun = "library" if count == 1 else "libraries"
    if active:
        line = (f"Looking in {count} {noun} online..." if count
                else "Looking online...")
    else:
        line = f"Looked in {count} {noun} online." if count else ""
    return {"active": active, "line": line, "libraries": rows}


def _topic_fallback(items: list[dict[str, Any]], words: str
                    ) -> Optional[dict[str, Any]]:
    """The Wikipedia book made from the words, when an online search found
    nothing else. It is the same book the wizard offers at build time ("up to
    300 Wikipedia articles about ..."), named with the owner's words as they
    typed them, and it still waits for their yes before anything is fetched."""
    from puffpai import plain_words
    topic = next((i for i in items if i.get("rides_along")
                  and str(i["apply"].get("ref") or "").startswith("wikipedia:")
                  and can_apply(i["apply"]["kind"])), None)
    if topic is None:
        return None
    topic = dict(topic, rides_along=False, fallback=True)
    label = f"Wikipedia articles about {words}"
    size = f" ({topic['label'].rsplit(' (', 1)[1]}" if " (" in topic["label"] else ""
    if words and len(label) + len(size) <= 80 and not plain_words.check(label):
        topic["label"] = label + size
    return topic


def get_session(session_id: Any, client: str) -> dict[str, Any]:
    """The session, if it exists, is live and belongs to *client*."""
    sid = str(session_id or "")
    with _lock:
        _prune(time.time())
        session = _sessions.get(sid) if _SESSION_RE.match(sid) else None
    if session is None or not secrets.compare_digest(
            str(session.get("client") or ""), str(client or "")) or not client:
        raise AddError("That list of finds has run out. Search again.", "gone")
    return session


def actionable(session: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in session["items"] if i.get("n")]


def _pick(session: dict[str, Any], ids: Any) -> list[dict[str, Any]]:
    if not isinstance(ids, list) or not ids:
        raise AddError("Pick at least one thing to add.", "none_picked")
    if len(ids) > MAX_APPROVE:
        raise AddError(f"Add at most {MAX_APPROVE} things at once.", "too_many")
    by_id = {i["id"]: i for i in actionable(session)}
    picked: list[dict[str, Any]] = []
    for raw in ids:
        item = by_id.get(str(raw)) if isinstance(raw, str) else None
        if item is None:
            raise AddError("That is not one of the things found for you. "
                           "Search again.", "unknown_item")
        if item not in picked:
            picked.append(item)
    return picked


def ids_for_numbers(session: dict[str, Any], numbers: list[int]) -> list[str]:
    by_n = {i["n"]: i["id"] for i in actionable(session)}
    return [by_n[n] for n in numbers if n in by_n]


def target(session_id: str, ids: Optional[list[str]] = None) -> str:
    """The consent ticket target: the session, and exactly these lines."""
    hexpart = str(session_id).split("_", 1)[-1]
    if ids is None:
        return f"add-{hexpart}-all"
    digest = hashlib.sha256(",".join(sorted(ids)).encode()).hexdigest()[:12]
    return f"add-{hexpart}-{digest}"


# ---------------------------------------------------------------------------
# words the owner reads
# ---------------------------------------------------------------------------

def _where(item: dict[str, Any]) -> str:
    host = item.get("outside_host")
    if item["section"] == "knowledge" and host:
        return f"fetched once from {host}, then read on this computer"
    if host:
        return f"asks {host} for new data"
    return "stays on this computer"


def sentence(session: dict[str, Any], ids: Optional[list[str]] = None) -> str:
    """The server's own sentence for saying yes to these lines."""
    picked = _pick(session, ids) if ids is not None else actionable(session)
    parts = [f"{n}. {item['label']} ({_where(item)})"
             for n, item in enumerate(picked, 1)]
    hosts = sorted({i["outside_host"] for i in picked if i.get("outside_host")})
    leaves = (f" What leaves your computer: a request to {', '.join(hosts)} and "
              f"your internet address." if hosts else
              " These need nothing from the internet. Nothing leaves your "
              "computer without your consent.")
    return (f"Add to {bot_name()}: " + " ".join(parts) + "." + leaves +
            " You can take any of them out again.")


def searched_line(session: dict[str, Any]) -> str:
    places = session.get("searched") or []
    if not places:
        return ""
    return "I looked in " + "; ".join(places) + "."


def online_line(session: dict[str, Any], surface: str) -> str:
    if not session.get("online_possible"):
        return ""
    sends = ", ".join(session.get("sends") or [])
    if not sends:
        return ""
    return {
        "chat": f"Look online too? It sends only these words: {sends}. "
                f"Say \"look online\".",
        "terminal": f"Look online too? It sends only these words: {sends}.",
        "browser": f"Look online too? It sends only these words: {sends}.",
    }[_surface(surface)]


def choose_line(surface: str) -> str:
    return {
        "chat": "Say \"yes\" to add them all, a number like \"just 1\", or \"no\".",
        "terminal": "Add which? Type numbers like 1,3, or all. Press Enter for none.",
        "browser": "Tick what you want, then press Add these.",
    }[_surface(surface)]


def nothing_found_lines(session: dict[str, Any], surface: str) -> list[str]:
    surface = _surface(surface)
    terms = session.get("terms") or []
    if not terms:
        return ["Tell me a subject to look for - a hobby, a thing, a place."]
    lines = [f"I couldn't find anything I can add for "
             f"\"{session.get('words') or ' '.join(terms)}\"."]
    if session.get("searched"):
        lines.append(searched_line(session))
    # A place that did not answer is not a place that had nothing.
    lines.extend(session.get("notes") or [])
    lines.append({
        "chat": "What works today: upload a file on the Documents page and I "
                "will learn it, or try other words.",
        "browser": "What works today: upload a file on the Documents page and "
                   "your bot will learn it, or try other words.",
        "terminal": "What works today: put your own files in a folder and run "
                    "puffpai knowledge sync --folder <that folder>, or try "
                    "other words.",
    }[surface])
    lines.append("I can't make new tools yet.")
    if session.get("wish_kept"):
        lines.append({
            "chat": "I kept a note of these words on this computer, so a later "
                    "version can build it. You can clear it on the Add page.",
            "browser": "A note of these words was kept on this computer, so a "
                       "later version can build it. Clear it below.",
            "terminal": "A note of these words was kept on this computer, so a "
                        "later version can build it. Clear it with: puffpai "
                        "add --forget-notes",
        }[surface])
    return lines


def public(session: dict[str, Any], surface: str = "browser") -> dict[str, Any]:
    """What a page or a chat card may see: no records, no addresses."""
    from puffpai.catalogs import knowledge_resolver
    groups: list[dict[str, Any]] = []
    for section, heading in sections():
        rows = []
        for item in session["items"]:
            if item["section"] != section:
                continue
            size = item.get("size_bytes")
            rows.append({
                "n": item.get("n"), "id": item["id"], "label": item["label"],
                "detail": item["detail"],
                "size_words": (knowledge_resolver.size_words(size)
                               if isinstance(size, int) else ""),
                "from": item.get("outside_host") or "",
                "can_add": bool(item.get("n")),
                "result": session["results"].get(item["id"]),
                "home": home_offer(session, item),
                "need": item.get("need") or None,
            })
        if rows:
            groups.append({"section": section, "heading": heading, "items": rows})
    has_any = bool(actionable(session))
    return {
        "session": session["id"], "title": title(),
        "words": session["words"], "terms": session["terms"],
        "searched_line": searched_line(session),
        "notes": session.get("notes") or [],
        "searched_online": bool(session.get("searched_online")),
        "online": {"possible": bool(session.get("online_possible")),
                   "sends": session.get("sends") or [],
                   "line": online_line(session, surface)},
        "groups": groups,
        "jobs": list(session.get("jobs") or []),
        "choose_line": choose_line(surface) if has_any else "",
        "nothing_found": [] if has_any else nothing_found_lines(session, surface),
    }


def home_offer(session: dict[str, Any], item: dict[str, Any]) -> dict[str, bool]:
    """Is "Put it on my home page" offered for this line, and ticked at first?

    Offered for anything with something to show (not a book, not a gadget
    note, not a sign-up key); ticked for live cards, or for everything when
    the owner asked for it on the home page."""
    offer = bool(item.get("n")) and item["section"] not in NOT_SHOWN \
        and can_apply(item["apply"]["kind"])
    ticked = offer and (item["apply"]["kind"] == "live_feed"
                        or bool(session.get("place_home")))
    return {"offer": offer, "default": ticked}


def shown_items(session: dict[str, Any],
                per_section: Optional[int] = None) -> list[dict[str, Any]]:
    """The numbered lines a text answer shows, in section order."""
    out: list[dict[str, Any]] = []
    rows = actionable(session)
    for section, _heading in sections():
        in_section = [i for i in rows if i["section"] == section]
        out.extend(in_section if per_section is None else in_section[:per_section])
    return out


def render_text(session: dict[str, Any], surface: str, *,
                per_section: Optional[int] = None) -> str:
    """The numbered lines for chat or the terminal."""
    surface = _surface(surface)
    rows = actionable(session)
    if not rows:
        return "\n".join(nothing_found_lines(session, surface))
    shown = shown_items(session, per_section)
    lines = [f"Here is what I found for \"{session['words']}\":"]
    headings = dict(sections())
    last = None
    for item in shown:
        if item["section"] != last:
            last = item["section"]
            lines.append(f"{headings.get(last, last)}:")
        lines.append(f"  {item['n']}. {item['label']} - {_where(item)}")
        if surface == "terminal":
            lines.append(f"     {item['detail']}")
    more = len(rows) - len(shown)
    if more > 0:
        lines.append(f"...and {more} more on the Add page.")
    info = [i for i in session["items"] if not i.get("n")]
    for item in info[:3]:
        lines.append(f"Also: {item['label']}. Not something I can add yet.")
    lines.append(searched_line(session))
    lines.extend(session.get("notes") or [])
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# yes: confirm, approve, apply
# ---------------------------------------------------------------------------

def confirm(session_id: Any, ids: Any, *, client: str, device: str
            ) -> dict[str, Any]:
    """The server's sentence for exactly these lines, and a one-time yes."""
    from puffpai.security import consent_tickets
    session = get_session(session_id, client)
    picked = _pick(session, ids)
    chosen = [i["id"] for i in picked]
    text = sentence(session, chosen)
    try:
        ticket = consent_tickets.mint(ACTION, target(session["id"], chosen),
                                      text, device)
    except consent_tickets.TicketRefused as exc:
        raise AddError(exc.message, exc.code)
    return {"ok": True, "ticket": ticket, "sentence": text,
            "expires_in": consent_tickets.TTL_SECONDS}


def approve(session_id: Any, ids: Any, *, client: str, device: str,
            surface: str, ticket: Any = None, whole_session: bool = False,
            by: str = "owner", home: Any = None) -> list[dict[str, Any]]:
    """Carry out the owner's yes. Returns one result per line.

    The terminal on this computer approves directly. Anything else must
    present a consent ticket minted for this session (``whole_session``: the
    chat question's ticket covers every line it showed) or for exactly these
    lines (the page), on this device.

    ``home`` is the ids to put on the home page (the page's "Put it on my
    home page" ticks); None means each line's own default
    (:func:`home_offer`). Only lines that offer it are ever placed.
    """
    from puffpai.security import consent_tickets
    session = get_session(session_id, client)
    picked = _pick(session, ids)
    chosen = [i["id"] for i in picked]
    terminal = (surface == "terminal"
                and device == consent_tickets.terminal_device_id())
    if not terminal:
        if surface not in ("chat", "browser"):
            raise AddError("Things can only be added from the Add page, the "
                           "chat on a paired device, or the terminal.", "surface")
        ok, verdict = consent_tickets.redeem(
            ticket, device, ACTION,
            target(session["id"], None if whole_session else chosen))
        if not ok:
            raise AddError(consent_tickets.refusal_words(verdict).replace(
                "Nothing was switched on", "Nothing was added"),
                f"ticket_{verdict}")
    if isinstance(home, list):
        to_place = {str(i) for i in home if isinstance(i, str)}
    else:
        to_place = {i["id"] for i in picked if home_offer(session, i)["default"]}
    _log_choices(session, set(chosen))
    results = []
    for item in picked:
        result = _apply_one(session, item, surface=surface, by=by,
                            device_word="the terminal on this computer"
                            if terminal else "a paired device")
        _place_result(item, result,
                      item["id"] in to_place and home_offer(session, item)["offer"])
        session["results"][item["id"]] = result
        results.append(dict(result, id=item["id"], n=item.get("n"),
                            label=item["label"]))
    return results


def _log_choices(session: dict[str, Any], chosen: set[str]) -> None:
    """Every apps-and-code or pictures line she was offered, with whether she
    took it, into ``plan_choices.jsonl`` (scores only, never her words)."""
    try:
        from puffpai.catalogs import meaning
    except Exception:  # noqa: BLE001
        return
    for item in session.get("items") or []:
        if item.get("section") not in ("apps_code", "pictures"):
            continue
        record = (session.get("kept") or {}).get(item["id"])
        meaning.log_choice(item, item["id"] in chosen,
                           record if isinstance(record, dict) else None)


def _place_result(item: dict[str, Any], result: dict[str, Any], wanted: bool) -> None:
    """Put an added line on the home page when the owner said so, and make
    its line say where it really is."""
    from puffpai import home_placement
    if result.get("status") not in ("added", "added_off", "working"):
        return
    where = home_placement.for_undo(result.get("undo"))
    if where is None:
        return
    placed = False
    if wanted:
        try:
            placed = home_placement.place(where[0], where[1], item["label"])
        except OSError as exc:
            logger.info("add-more: not put on the home page: %s", exc)
    result["placed"] = placed
    line = str(result.get("line") or "")
    if placed:
        if "home page" not in line:
            result["line"] = (line + " It is on your home page.").strip()
    else:
        result["line"] = (line.replace("a live card on your home page",
                                       "a live card, on its own page under Apps")
                          .replace(" is on your home page, but", " is added, but"))


def _apply_one(session: dict[str, Any], item: dict[str, Any], *, surface: str,
               by: str, device_word: str) -> dict[str, Any]:
    kind = item["apply"]["kind"]
    fn = _APPLIERS.get(kind)
    if fn is None or kind in INFO_KINDS:
        return {"status": "refused", "line": f"{item['label']}: this cannot be "
                                             f"added yet. Nothing was changed."}
    ctx = ApplyContext(via=session["via"], by=by, surface=_surface(surface),
                       words=session["words"],
                       candidate=session["kept"].get(item["id"]),
                       session_id=session["id"])
    try:
        result = dict(fn(item, ctx) or {})
    except Exception as exc:  # noqa: BLE001 - one line never sinks the rest
        logger.warning("add-more: %s failed: %s", item["id"], exc)
        result = {"status": "held", "line": f"{item['label']}: something went "
                                            f"wrong while adding it. Nothing "
                                            f"half-made was kept."}
    result.setdefault("status", "held")
    result.setdefault("line", item["label"])
    if result["status"] in ("added", "added_off", "working"):
        _record_addition(item, result, via=session["via"], device=device_word)
    return result


# -- the built-in appliers -----------------------------------------------------

def _held(line: str, **extra: Any) -> dict[str, Any]:
    return {"status": "held", "line": line, **extra}


def _book_verdict(cand: dict[str, Any], via: str) -> dict[str, Any]:
    from puffpai import drawbridge
    record = {k: v for k, v in cand.items() if k != "url"}
    return drawbridge.check_addition(
        {"kind": "book", "name": cand.get("name"), "candidate": record},
        by=f"add:{via}"[:40])


def _apply_book(item: dict[str, Any], ctx: ApplyContext) -> dict[str, Any]:
    from puffpai import drawbridge
    from puffpai.knowledge import find_more, progress
    ref = str(item["apply"]["ref"])
    name = re.sub(r"\s+\((?:about|up to|under|size)[^)]*\)$", "", item["label"])
    if not _set_up():
        return _held(f"{name}: this bot has not been set up yet, so it cannot "
                     f"fetch books.")
    if not online_allowed():
        return _held(f"{name}: this bot is set to stay off the internet, so it "
                     f"does not fetch books. You can bring a book over on a USB "
                     f"stick instead.")
    cand = ctx.candidate if isinstance(ctx.candidate, dict) else None
    cand = cand or find_more.look_for_add(ref)
    if cand is None or not cand.get("fetchable"):
        return _held(f"{name}: BuildBot could not find that book on its lists "
                     f"any more.")
    verdict = _book_verdict(cand, ctx.via)
    checks = verdict.get("results") or []
    if verdict.get("verdict") == drawbridge.BLOCK:
        return _held(str(verdict.get("line")), checks=checks, drawbridge="block")
    hosts = [h for h in [item.get("outside_host")] if h]
    undo = {"kind": "book", "ref": ref}
    if find_more.is_text_book(ref):
        try:
            lid = find_more.start_in_background(ref, name, via=f"add:{ctx.via}"[:40],
                                                cand=cand)
        except find_more.OrderBlocked as exc:
            return _held(exc.line, checks=checks, drawbridge="block")
        undo["book_id"] = lid
        rec = progress.read(lid)
        lines = progress.plain_lines(rec, ctx.surface) if rec else []
        tail = ({"terminal": " It is fetched while your bot is running, or "
                             "right now with: puffpai knowledge sync"}
                .get(ctx.surface, " I will tell you on the home page when I can "
                                  "answer from it."))
        return {"status": "working", "line": f"{name}: being fetched and learned "
                                             f"in the background.{tail}",
                "progress": lines, "learn_id": lid, "checks": checks,
                "drawbridge": verdict.get("verdict"), "hosts": hosts,
                "undo": undo}
    got = find_more.api_add(ref, size_acknowledged=True)
    if not got.get("success"):
        return _held(f"{name}: {got.get('message') or 'the download did not start.'}",
                     checks=checks, drawbridge=verdict.get("verdict"))
    return {"status": "working",
            "line": f"{name}: downloading now. It is learned as soon as it has "
                    f"arrived.",
            "checks": checks, "drawbridge": verdict.get("verdict"),
            "hosts": hosts, "undo": undo, "download": ref}


def _switch_on_words(surface: str) -> str:
    return {
        "chat": "Say \"switch it on\" to let it use the internet for this one "
                "website.",
        "browser": "Press Switch it on to let it use the internet for this one "
                   "website.",
        "terminal": "It can be switched on for this one website now.",
    }[_surface(surface)]


def _apply_live(item: dict[str, Any], ctx: ApplyContext) -> dict[str, Any]:
    from puffpai import drawbridge, feed_generator, feeds
    cand = ctx.candidate if isinstance(ctx.candidate, dict) else None
    label = item["label"]
    if cand is None:
        return _held(f"{label}: BuildBot could not find that website on its "
                     f"lists any more.")
    try:
        draft = feed_generator.draft_feed_app(cand, build_online=online_allowed(),
                                              goal=ctx.words, chooser=None)
    except feed_generator.DraftError as exc:
        return _held(f"{label}: {exc} Nothing was added.")
    verdict = drawbridge.check_addition(draft, by=f"add:{ctx.via}"[:40])
    checks = verdict.get("results") or []
    if verdict.get("verdict") == drawbridge.BLOCK:
        feed_generator.discard_draft(draft["id"])
        return _held(str(verdict.get("line")), checks=checks, drawbridge="block")
    try:
        done = feed_generator.install_draft(draft["id"], allow=True)
    except feed_generator.DraftError as exc:
        feed_generator.discard_draft(draft["id"])
        return _held(f"{label}: {exc}", checks=checks,
                     drawbridge=verdict.get("verdict"))
    app_id = str(done["id"])
    name = str(done.get("name") or label)
    undo = {"kind": "card", "ref": app_id}
    state = feeds.service_state(app_id)
    hosts = [h for h in state.get("hosts") or [] if isinstance(h, str)]
    base = {"checks": checks, "drawbridge": verdict.get("verdict"),
            "hosts": hosts, "undo": undo, "app_id": app_id}
    if state.get("state") != "open":
        # The truth, read back: added, but it will not fetch anything yet.
        why = {"bot_offline": "your bot is set to stay off the internet",
               "switched_off": "whoever runs this computer switched live cards "
                               "off in its settings",
               }.get(str(state.get("state")), "its website is switched off")
        line = (f"{name} is on your home page, but it is switched off: {why}, "
                f"so it cannot fetch anything yet.")
        out = dict(base, status="added_off", line=line)
        if state.get("state") == "bot_offline":
            out["line"] += " " + _switch_on_words(ctx.surface)
            out["switch_on"] = {"service": "card-" + app_id}
        return out
    card = feeds.refresh(app_id, force=True)
    shows = feed_generator.shows_words(card.get("card") or {}) \
        if card.get("status") == "ok" else ""
    line = f"Added {name}: a live card on your home page."
    if shows:
        line += f" Right now it shows: {shows}."
    elif card.get("message"):
        line += f" {card['message']}"
    return dict(base, status="added", line=line, card_status=card.get("status"))


def _apply_routine(item: dict[str, Any], ctx: ApplyContext) -> dict[str, Any]:
    from puffpai import offers, routines
    got = offers.apply_item(item["apply"], by=ctx.by)
    if not got.get("ok"):
        return _held(f"{item['label']}: {got.get('message') or 'it could not be switched on.'}")
    row = got.get("routine") or {}
    when = routines.spoken_when(row) if row else ""
    stop = {"chat": "Say \"stop daily updates\" to turn it off.",
            "browser": "Take it out below to turn it off.",
            "terminal": "Turn it off with: puffpai add --take-out <its number>"
            }[_surface(ctx.surface)]
    return {"status": "added",
            "line": f"Added: a short update {when}. It shows on your home page. "
                    f"{stop}".replace("  ", " "),
            "undo": {"kind": "routine", "ref": offers.ROUTINE_ID},
            "hosts": []}


# ---------------------------------------------------------------------------
# after a yes: switching a website on, progress
# ---------------------------------------------------------------------------

def switch_on_sentence(service: str) -> str:
    from puffpai import outside_services
    try:
        return outside_services.proposal(service)
    except outside_services.OutsideError as exc:
        raise AddError(exc.message, exc.code)


def switch_on(service: str, *, ticket: Any, device: str, surface: str) -> str:
    """Switch one website on after its own consent ticket. Plain line back."""
    from puffpai import outside_services
    from puffpai.security import consent_tickets
    if not (surface == "terminal" and device == consent_tickets.terminal_device_id()
            and ticket is None):
        ok, verdict = consent_tickets.redeem(ticket, device, "outside_on", service)
        if not ok:
            raise AddError(consent_tickets.refusal_words(verdict),
                           f"ticket_{verdict}")
    try:
        row = outside_services.switch_on(service, by="owner")
    except outside_services.OutsideError as exc:
        raise AddError(exc.message, exc.code)
    if row.get("state") == "on":
        return f"{row.get('name') or 'It'} is switched on. It fetches its data now."
    return str(row.get("state_line") or "It could not be switched on.")


def progress_lines(session: dict[str, Any], surface: str) -> dict[str, list[str]]:
    """How far each book added in this session has got, per line id."""
    from puffpai.knowledge import find_more, progress
    out: dict[str, list[str]] = {}
    status = None
    for item_id, result in session["results"].items():
        if result.get("learn_id"):
            rec = progress.read(str(result["learn_id"]))
            if rec:
                out[item_id] = progress.plain_lines(rec, _surface(surface))
        elif result.get("download"):
            status = status or find_more.api_add_status()
            if status.get("id") == result["download"]:
                lines = []
                if status.get("state") == "running":
                    got = int(status.get("downloaded") or 0) // (1024 * 1024)
                    lines.append(f"Downloading: {got} MB so far.")
                elif status.get("message"):
                    lines.append(str(status["message"]))
                lines.extend(status.get("learning") or [])
                out[item_id] = lines
    return out


# ---------------------------------------------------------------------------
# what was added, and taking it out
# ---------------------------------------------------------------------------

def _additions_path() -> str:
    return os.path.join(_state_dir(), ADDITIONS_FILE)


def _record_addition(item: dict[str, Any], result: dict[str, Any], *, via: str,
                     device: str) -> None:
    from puffpai import fsutil
    entry = {"at": _now_iso(), "entry": "a_" + secrets.token_hex(4),
             "label": item["label"], "section": item["section"],
             "kind": item["apply"]["kind"], "ref": item["apply"]["ref"],
             "via": via, "device": device,
             "hosts": [h for h in result.get("hosts") or [] if isinstance(h, str)],
             "status": result["status"], "drawbridge": result.get("drawbridge"),
             "undo": result.get("undo")}
    path = _additions_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fsutil.atomic_append(path, json.dumps(entry, ensure_ascii=False))


def _read_lines(path: str) -> list[dict[str, Any]]:
    from puffpai import fsutil
    try:
        text = fsutil.read_text(path)
    except (FileNotFoundError, OSError):
        return []
    rows = []
    for line in str(text or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def added() -> list[dict[str, Any]]:
    """Everything added after the build and still in, numbered, newest last."""
    rows = _read_lines(_additions_path())
    gone = {r.get("removed") for r in rows if r.get("removed")}
    out = []
    for row in rows:
        if row.get("entry") and row["entry"] not in gone:
            out.append(row)
    for n, row in enumerate(out, 1):
        row["n"] = n
    return out


def added_lines(surface: str = "terminal") -> list[str]:
    rows = added()
    if not rows:
        return ["Nothing has been added since your bot was built."]
    out = []
    for row in rows:
        day = str(row.get("at") or "")[:10]
        out.append(f"{row['n']}. {row['label']} (added {day} from "
                   f"{row.get('device') or 'this computer'})")
    return out


def take_out(which: Any, *, by: str = "owner") -> str:
    """Take one addition out (by its number or entry id). Plain line back."""
    from puffpai import fsutil
    rows = added()
    row = next((r for r in rows if str(which) in (str(r["n"]), r["entry"])), None)
    if row is None:
        raise AddError("There is nothing added under that number.", "unknown")
    undo = row.get("undo") or {}
    remover = _REMOVERS.get(str(undo.get("kind")))
    if remover is None:
        raise AddError(f"{row['label']} cannot be taken out from here.", "cannot")
    got = remover(undo, by)
    if got.get("ok"):
        fsutil.atomic_append(_additions_path(), json.dumps(
            {"at": _now_iso(), "removed": row["entry"]}))
        from puffpai import home_placement
        where = home_placement.for_undo(undo)
        if where is not None:
            home_placement.unplace(*where)
    return str(got.get("line") or row["label"])


def home_state(row: dict[str, Any]) -> dict[str, bool]:
    """Can this addition go on the home page, and is it there?"""
    from puffpai import home_placement
    where = home_placement.for_undo(row.get("undo"))
    if where is None:
        return {"can": False, "on": False}
    return {"can": True, "on": home_placement.is_placed(*where)}


def set_home(which: Any, on: bool) -> str:
    """Put one addition on the home page, or take it off. Plain line back."""
    from puffpai import home_placement
    row = next((r for r in added() if str(which) in (str(r["n"]), r["entry"])), None)
    if row is None:
        raise AddError("There is nothing added under that number.", "unknown")
    where = home_placement.for_undo(row.get("undo"))
    if where is None:
        raise AddError(f"{row['label']} has nothing to show on the home page.",
                       "cannot")
    if on:
        if not home_placement.place(where[0], where[1], row["label"]):
            raise AddError("Your home page is full. Take something off it first.",
                           "cannot")
        return f"{row['label']} is on your home page."
    home_placement.unplace(*where)
    return f"{row['label']} is off your home page. It is still added."


def _remove_card(undo: dict[str, Any], by: str) -> dict[str, Any]:
    from puffpai import app_registry, feeds, outside_services
    app_id = str(undo.get("ref") or "")
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", app_id):
        return {"ok": False, "line": "That card could not be found."}
    try:
        outside_services.switch_off("card-" + app_id, by=by)
    except Exception as exc:  # noqa: BLE001 - it may already be gone
        logger.debug("card %s switch off: %s", app_id, exc)
    root = app_registry.user_apps_dir()
    try:
        folder = app_registry.resolve_within(root, app_id)
    except ValueError:
        return {"ok": False, "line": "That card could not be found."}
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    app_registry.rescan()
    try:
        feeds.sync_doors()
    except Exception:  # noqa: BLE001
        pass
    if os.path.isdir(folder):
        return {"ok": False, "line": "The card could not be removed from this "
                                     "computer. Nothing else was changed."}
    return {"ok": True, "line": f"Took out the live card. It no longer fetches "
                                f"anything."}


def _remove_book(undo: dict[str, Any], by: str) -> dict[str, Any]:
    from puffpai.knowledge import find_more, learn, text_books
    from puffpai.knowledge.auto_index import STATUS_REVOKED, PackIndex
    ref = str(undo.get("ref") or "")
    index = PackIndex()
    book_id = ""
    if find_more.is_text_book(ref):
        book_id = str(undo.get("book_id") or text_books.learn_id(ref))
        if index.find_by_book(book_id) is None:
            with find_more._orders_lock:  # noqa: SLF001 - the one orders file
                orders = find_more.pending_orders()
                waiting = orders.pop(ref, None)
                if waiting is not None:
                    find_more._save_orders(orders)  # noqa: SLF001
            if waiting is not None:
                from puffpai.knowledge import progress
                progress.remove(book_id)
                return {"ok": True, "line": "Took out the book before it was "
                                            "fetched. Nothing was downloaded."}
            return {"ok": False, "line": "That book is still being fetched. "
                                         "Take it out again once it has arrived."}
    else:
        for rec in index.records():
            if rec.get("source_id") == ref and rec.get("book_id") \
                    and rec.get("status") != STATUS_REVOKED:
                book_id = str(rec["book_id"])
                break
        if not book_id:
            return {"ok": False, "line": "That book has not arrived yet. Take it "
                                         "out again once it has."}
    done = learn.remove_book(book_id, pack_index=index)
    if done.get("ok"):
        return {"ok": True, "line": "Took out the book and everything learned "
                                    "from it."}
    return {"ok": False, "line": "The book is busy being learned. Try again in a "
                                 "minute." if done.get("reason") == "busy" else
            "The book could not be taken out. Nothing was changed."}


def _remove_routine(undo: dict[str, Any], by: str) -> dict[str, Any]:
    from puffpai import routines
    rid = str(undo.get("ref") or "")
    if routines.remove_routine(rid, by=by):
        return {"ok": True, "line": "Took out the daily update. It will not run "
                                    "again."}
    return {"ok": True, "line": "That update was already gone."}


# ---------------------------------------------------------------------------
# wishes: what could not be found, for a later version
# ---------------------------------------------------------------------------

def _wishes_path() -> str:
    return os.path.join(_state_dir(), WISHES_FILE)


def _keep_wish(session: dict[str, Any]) -> bool:
    """Keep the topic words (never the sentence) of a request nothing met."""
    from puffpai import fsutil
    if not session.get("terms"):
        return False
    try:
        path = _wishes_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fsutil.atomic_append(path, json.dumps({
            "at": _now_iso(), "terms": list(session["terms"]),
            "searched": list(session.get("searched") or [])}))
        return True
    except OSError as exc:
        logger.info("add-more: the note was not kept: %s", exc)
        return False


def wishes() -> list[dict[str, Any]]:
    return [{"at": r.get("at"), "terms": r.get("terms") or []}
            for r in _read_lines(_wishes_path()) if r.get("terms")]


def forget_wishes() -> int:
    rows = wishes()
    try:
        os.remove(_wishes_path())
    except OSError:
        pass
    return len(rows)


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------

def _offer_asked_after(when: datetime) -> bool:
    """Did the daily-update offer ask its own yes/no question after ours?"""
    try:
        from puffpai import offers
        asked = (offers._entry() or {}).get("chat_asked_at")  # noqa: SLF001
        return bool(asked) and datetime.fromisoformat(str(asked)) > when
    except Exception:  # noqa: BLE001
        return False


def _is_list_name(text: str) -> bool:
    try:
        from puffpai import app_fastpath, app_registry
        pairs = app_fastpath._candidates(app_registry.current_manifests())  # noqa: SLF001
        return bool(app_fastpath._named(" ".join(text.lower().split()), pairs))  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return False


def _ability_missing(words: str) -> bool:
    try:
        from puffpai import build_abilities
        return bool(build_abilities.goal_rows(words, build_abilities.abilities()))
    except Exception:  # noqa: BLE001 - not known: not taken
        return False


def _voice_line(words: str) -> str:
    return (f"I can look for {words}, but adding things is chosen on the "
            f"dashboard, not by voice. Open {title()} and type what you want.")


def _ask(client: str, **question: Any) -> None:
    with _lock:
        _pending[client] = dict(question, asked_at=datetime.now(timezone.utc))


def _pending_for(client: str) -> Optional[dict[str, Any]]:
    with _lock:
        question = _pending.get(client)
    if not question:
        return None
    age = (datetime.now(timezone.utc) - question["asked_at"]).total_seconds()
    # The daily-update offer's own yes/no question, asked after ours, owns a
    # bare yes. A held question never takes a bare yes (:func:`_resume`), so
    # it keeps waiting through that offer.
    offer_took_over = (not question.get("interrupted")
                       and _offer_asked_after(question["asked_at"]))
    if age > QUESTION_SECONDS or offer_took_over:
        with _lock:
            _pending.pop(client, None)
        return None
    return question


def _clear(client: str) -> None:
    with _lock:
        _pending.pop(client, None)


def _hold(client: str, question: dict[str, Any]) -> bool:
    """Keep a list-of-finds question through a message about something else.
    False (and it is dropped) once it has waited through too many."""
    count = int(question.get("interrupted") or 0) + 1
    if count > MAX_INTERRUPTS:
        _clear(client)
        with _lock:
            _closed[client] = datetime.now(timezone.utc)
            held = _sessions.get(str(question.get("session") or "")) or {}
            _let_go[client] = str(held.get("words") or "")
        return False
    with _lock:
        if _pending.get(client) is question:
            question["interrupted"] = count
            question["held_at"] = datetime.now(timezone.utc)
    return True


def _propose_in_chat(session: dict[str, Any], *, client: str, device: str,
                     surface: str, can_approve: bool) -> str:
    from puffpai.security import consent_tickets
    per_section = CHAT_PER_SECTION if surface == "chat" else None
    text = render_text(session, surface, per_section=per_section)
    if not actionable(session):
        online = online_line(session, "chat")
        if online and _one_shot(client):
            # A chat with no device behind it gets a new name every message,
            # so a question asked here could never be answered. Say where.
            _clear(client)
            return "\n".join((text, only_paired_line()))
        if online and client:
            # "Look online too?" is a real question with a real answer: the
            # owner's "look online" (or "yes") runs the online search.
            _ask(client, kind="choose", session=session["id"], ticket=None,
                 device=device, numbers=[])
            return "\n".join((text, online))
        _clear(client)
        return text
    online = online_line(session, "chat")
    if not can_approve:
        _clear(client)
        tail = (f"To choose, open {title()} on the dashboard of a paired device "
                f"and type the same words.")
        return "\n".join(x for x in (text, tail) if x)
    shown = shown_items(session, per_section)
    ticket = None
    if not (surface == "terminal" and device == consent_tickets.terminal_device_id()):
        try:
            # The chat's yes covers the lines it showed, and only those.
            ticket = consent_tickets.mint(ACTION, target(session["id"]),
                                          sentence(session, [i["id"] for i in shown]),
                                          device)
        except consent_tickets.TicketRefused as exc:
            _clear(client)
            return "\n".join((text, exc.message))
    _ask(client, kind="choose", session=session["id"], ticket=ticket,
         device=device, numbers=[i["n"] for i in shown])
    home = ""
    if session.get("place_home") and any(home_offer(session, i)["offer"]
                                         for i in shown):
        home = "What you pick that can be shown goes on your home page."
    return "\n".join(x for x in (text, choose_line("chat"), home, online) if x)


def _result_text(results: list[dict[str, Any]]) -> str:
    lines = []
    for r in results:
        lines.append(str(r.get("line") or ""))
        lines.extend(r.get("progress") or [])
    return "\n".join(line for line in lines if line)


#: The answer to "look online" when nothing is waiting to be looked up.
NOTHING_TO_LOOK_UP = ("I can't look that up online yet: nothing is waiting to "
                      "be looked up. To add something, say: add ___ to you. "
                      "I will say which words would be sent before anything "
                      "goes online.")


def _one_shot(client: str) -> bool:
    """A chat client that lasts one message (no paired device behind it)."""
    return str(client or "").startswith("anon:")


def only_paired_line() -> str:
    return (f"Looking online is chosen from a paired device: open {title()} on "
            f"the dashboard of a paired device and type the same words.")


def chat_turn(message: Any, *, surface: str, client: str, device: str = "",
              can_approve: bool = False, new_requests: bool = True) -> Optional[str]:
    """The answer when *message* is an add request or a reply to one; else None.

    ``surface`` is ``chat`` (the dashboard), ``terminal`` (``puffpai chat``
    on this computer) or ``voice`` (spoken, or a keyless chat route), which
    never searches and never says yes. ``can_approve`` is True only for a
    paired device's chat or the terminal. ``new_requests=False`` (the chat's
    tool loop is on) answers only a reply to a question already waiting: a
    new add request goes to the loop, which checks the built-in apps first.
    """
    from puffpai import add_intent
    text = str(message or "")
    question = _pending_for(client) if client else None
    if question is not None:
        if question.get("interrupted"):
            reply = _resume(question, text, client=client, surface=surface)
        else:
            reply = _answer(question, text, client=client, surface=surface)
        if reply is not None:
            if question.get("kind") == "choose" and _pending_for(client) is None:
                with _lock:
                    _closed[client] = datetime.now(timezone.utc)
            return reply
        # Something else was said. A list of finds keeps waiting on the
        # server (a later "the first one" is checked back first); anything
        # else is dropped, so an old yes can no longer land.
        if not (question.get("kind") == "choose" and question.get("session")
                and add_intent_detect(text) is None and _hold(client, question)):
            _clear(client)
    elif client:
        late = _late_pick(client, text)
        if late is not None:
            return late
    if not new_requests:
        return None
    if question is None and (add_intent.read_reply(text, 0) or {}).get("online"):
        # "look online" with nothing waiting to be looked up. Never handed to
        # the model: it would say it looked, and nothing would have happened.
        return only_paired_line() if _one_shot(client) else NOTHING_TO_LOOK_UP
    found = add_intent_detect(text)
    if found is None:
        return None
    if surface == "voice":
        return _voice_line(scrub(found["words"]) or "that")
    via = "chat" if surface != "terminal" else "terminal"
    home = found.get("place") == "home"
    only = ("looks",) if found.get("shape") == "look" else (
        SHOWN_SECTIONS if home else None)
    session = find(found["words"], via=via, client=client, online=False,
                   only=only, place_home=home)
    if home and only == SHOWN_SECTIONS and not actionable(session):
        # Nothing to show matched: say so, and offer what it could learn.
        session = find(found["words"], via=via, client=client, online=False,
                       place_home=True)
        session["notes"] = [f"Nothing I can show on your home page matched "
                            f"\"{session['words']}\"."] + list(session["notes"])
    return _propose_in_chat(session, client=client, device=device,
                            surface=surface, can_approve=can_approve)


def _late_pick(client: str, text: str) -> Optional[str]:
    """A numbered reply that arrives after its list of finds was answered.

    It is turned away plainly (nothing is added, no model is asked). Any other
    message forgets the closed list, so a later numbered question from
    something else is never caught here."""
    from puffpai import add_intent
    with _lock:
        closed_at = _closed.pop(client, None)
        let_go = _let_go.pop(client, None)
    if closed_at is None:
        return None
    age = (datetime.now(timezone.utc) - closed_at).total_seconds()
    if age > QUESTION_SECONDS or not add_intent.is_numbered_pick(text):
        return None
    if let_go is not None:
        about = f" (\"{let_go}\")" if let_go else ""
        return (f"I let go of what you asked me to add{about} after we talked "
                f"about other things, so nothing was added. To add it, ask again, "
                f"like: add ___ to my home page. Or open {title()}.")
    return ("That list of finds is already answered, so nothing more was added. "
            f"To add something else, ask again, like: add ___ to you. Or open {title()}.")


def add_intent_detect(text: str) -> Optional[dict[str, str]]:
    from puffpai import add_intent
    return add_intent.detect(text, bot_name=bot_name(), is_list_name=_is_list_name,
                             ability_missing=_ability_missing)


def _answer(question: dict[str, Any], text: str, *, client: str,
            surface: str) -> Optional[str]:
    from puffpai import add_intent
    from puffpai.security import consent_tickets
    if question["kind"] == "switch_on":
        reply = add_intent.read_reply(text, 1)
        if reply is None:
            return None
        _clear(client)
        if reply.get("no"):
            return ("Okay, it stays switched off. You can switch it on any time "
                    "on the What leaves your computer page.")
        if not (reply.get("switch_on") or reply.get("yes")):
            return None
        try:
            return switch_on(question["service"], ticket=question.get("ticket"),
                             device=question["device"], surface=surface)
        except AddError as exc:
            return exc.message
    reply = add_intent.read_reply(text, list(question.get("numbers") or []))
    if reply is None and not question.get("numbers"):
        # Only "look online too?" was asked, so a plain yes means that.
        yes = add_intent.read_reply(text, 1)
        reply = {"online": True} if yes and yes.get("yes") else yes
    if reply is None:
        return None
    try:
        session = get_session(question["session"], client)
    except AddError as exc:
        _clear(client)
        return exc.message
    if reply.get("no"):
        _clear(client)
        return "Okay, nothing was added."
    if reply.get("online"):
        if not session.get("online_possible"):
            return "I can't look online for this bot: it is set to stay off the internet."
        fresh = find(session["words"], via=session["via"], client=client,
                     online=True, keep_out=session.get("keep_out") or (),
                     place_home=bool(session.get("place_home")))
        return _propose_in_chat(fresh, client=client, device=question["device"],
                                surface=surface,
                                can_approve=bool(question.get("device")))
    if reply.get("switch_on"):
        return None
    _clear(client)
    ids = ids_for_numbers(session, reply.get("yes") or [])
    try:
        results = approve(session["id"], ids, client=client,
                          device=question["device"], surface=surface,
                          ticket=question.get("ticket"), whole_session=True)
    except AddError as exc:
        return exc.message
    text_out = _result_text(results)
    offer = next((r for r in results if r.get("switch_on")), None)
    if offer:
        service = offer["switch_on"]["service"]
        try:
            said = switch_on_sentence(service)
            ticket = None
            if not (surface == "terminal"
                    and question["device"] == consent_tickets.terminal_device_id()):
                ticket = consent_tickets.mint("outside_on", service, said,
                                              question["device"])
            _ask(client, kind="switch_on", service=service, ticket=ticket,
                 device=question["device"])
            text_out += "\n" + said
        except (AddError, consent_tickets.TicketRefused) as exc:
            text_out += "\n" + str(getattr(exc, "message", exc))
    return text_out


def _resume(question: dict[str, Any], text: str, *, client: str,
            surface: str) -> Optional[str]:
    """The owner came back to a list of finds after talking about something
    else. "look online" still means the online step. A numbered pick ("the
    first one") or a bare "yes" is never added straight away (the yes may
    have answered something else): what it would add is said back with its
    sentence for a fresh yes. A bare "no" lets the request go, and says so."""
    from puffpai import add_intent
    from puffpai.security import consent_tickets
    if add_intent.read_reply(text, 0) == {"online": True}:
        return _answer(question, text, client=client, surface=surface)
    if (not add_intent.is_numbered_pick(text)
            and _offer_asked_after(question.get("held_at") or question["asked_at"])):
        # The daily-update offer asked its own yes/no since: a bare yes or
        # no answers that, not this.
        return None
    numbers = list(question.get("numbers") or [])
    # With no numbers shown, only "look online too?" was waiting.
    reply = add_intent.read_reply(text, numbers or 1)
    if not reply or not (reply.get("yes") or reply.get("no")):
        return None
    try:
        session = get_session(question["session"], client)
    except AddError as exc:
        _clear(client)
        return exc.message
    back = f"Back to what you asked me to add (\"{session['words']}\"):"
    if reply.get("no"):
        _clear(client)
        return (f"Okay, I let go of what you asked me to add (\"{session['words']}\"). "
                f"Nothing was added. If your no was for something else, say it again.")
    if not numbers:
        # Ask "look online too?" again, fresh.
        _ask(client, kind="choose", session=session["id"], ticket=None,
             device=question.get("device") or "", numbers=[])
        return "\n".join(x for x in (back, online_line(session, "chat")) if x)
    ids = ids_for_numbers(session, reply["yes"])
    if not ids:
        return None
    said = sentence(session, ids)
    ticket = None
    device = question.get("device") or ""
    if not (surface == "terminal" and device == consent_tickets.terminal_device_id()):
        if not device:
            _clear(client)
            return only_paired_line()
        try:
            ticket = consent_tickets.mint(ACTION, target(session["id"]), said, device)
        except consent_tickets.TicketRefused as exc:
            _clear(client)
            return exc.message
    _ask(client, kind="choose", session=session["id"], ticket=ticket,
         device=device, numbers=list(reply["yes"]))
    many = len(ids) > 1
    return "\n".join((
        back,
        said,
        f"Say \"yes\" to add {'them' if many else 'it'}, or \"no\"."))


def pending_note(client: str) -> str:
    """What the model is told while a list of finds waits on this chat.

    When the owner asks something else in the middle of an add, the model
    answers that; this note (passed as ``chat_memory.assemble(pending=...)``,
    so it is never trimmed) keeps the add in view under the bot's own name
    (:mod:`puffpai.bot_identity`), so the reply does not act as if the request
    was forgotten. "" when nothing is waiting. Never raises.
    """
    try:
        question = _pending_for(client) if client else None
        if not question or question.get("kind") != "choose":
            return ""
        with _lock:
            held = _sessions.get(str(question.get("session") or "")) or {}
        words = scrub(held.get("words") or "")
    except Exception:  # noqa: BLE001 - the chat never breaks on this
        return ""
    if not words:
        return ""
    if question.get("numbers"):
        waiting = ("A numbered list of things to add is still waiting for "
                   "their pick. Nothing has been added yet.")
        tail = ("Answer the question they asked now. Then, in one short "
                "sentence, say the list is still waiting and they can answer "
                "with its number.")
    else:
        waiting = ("A question is still waiting: whether to look online too. "
                   "Nothing has been added yet.")
        tail = ("Answer the question they asked now. Then, in one short "
                "sentence, say they can still say \"look online\".")
    return (f"The owner asked {bot_name()} to add \"{words}\". {waiting} "
            f"Never say anything was added. {tail}")


def reset() -> None:
    """Forget every session and question (tests)."""
    with _lock:
        _sessions.clear()
        _pending.clear()
        _closed.clear()
        _let_go.clear()
        _looking.clear()


# ---------------------------------------------------------------------------
# apps and code, pictures (the wizard's own engine)
# ---------------------------------------------------------------------------

def _optional(modname: str):
    import importlib
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


def needs_for(words: str) -> Optional[dict]:
    """Her words as jobs (``needs.from_words``, no model call), or None."""
    needs = _optional("puffpai.wizard.needs")
    if needs is None or not str(words or "").strip():
        return None
    try:
        doc = needs.from_words(str(words), cfg=None)
    except Exception as exc:  # noqa: BLE001 - no jobs, no code search
        logger.info("add-more: the needs step could not read the words: %s",
                    type(exc).__name__)
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == "needs/v1" else None


def code_sends(needs_doc: Optional[dict]) -> list[str]:
    """The phrases the outbound screen lets out for these jobs (none without it)."""
    out: list[str] = []
    for job in (needs_doc or {}).get("jobs") or []:
        for part in job.get("parts") or []:
            for s in part.get("search") or []:
                if isinstance(s, dict) and s.get("state") == "out" and s.get("phrase"):
                    out.append(str(s["phrase"]))
    return list(dict.fromkeys(out))


#: Said when the apps-and-code search could not go out.
CODE_SEARCH_OFF_NOTE = ("\"Looking for apps and code\" is switched off under "
                        "What leaves your computer, so GitHub was not "
                        "searched. Switch it on there, then search again.")


def _switched_off(sid: str) -> bool:
    try:
        from puffpai.security import outside_switches
        return bool(outside_switches.is_off(sid))
    except Exception:  # noqa: BLE001 - not known: say it was not searched
        return True


def _found_parts(words: str, ctx: AddContext) -> dict:
    """``find_parts.plan_items`` for her words, asked once per search."""
    if ctx.parts is not None:
        return ctx.parts
    ctx.parts = {"items": [], "notes": {}, "sends": []}
    find_parts = _optional("puffpai.wizard.find_parts")
    if find_parts is None or ctx.needs is None:
        return ctx.parts
    got = find_parts.plan_items(ctx.needs, goal=ctx.typed or words, online=ctx.code_online,
                                allow_send=ctx.code_online, cfg=None)
    ctx.parts = got if isinstance(got, dict) else ctx.parts
    # GitHub is named only when the search could go out.
    held = ctx.code_online and _switched_off("code_search")
    ctx.searched.append("lists of apps and code on this computer"
                        + (" and GitHub" if ctx.code_online and not held else ""))
    if held:
        ctx.notes.append(CODE_SEARCH_OFF_NOTE)
    for lines in (ctx.parts.get("notes") or {}).values():
        for line in lines[:2]:
            if line and "nothing ready-made" not in line:
                ctx.notes.append(f"Also found, look at it yourself: {line}")
    return ctx.parts


def _parts_provider(section: str) -> Provider:
    def provider(words: str, ctx: AddContext) -> list[dict[str, Any]]:
        find_parts = _optional("puffpai.wizard.find_parts")
        out = []
        for item in _found_parts(words, ctx).get("items") or []:
            if not isinstance(item, dict) or item.get("section") != section:
                continue
            out.append(dict(item))
            if find_parts is not None:
                rec = find_parts.found_for(str(item.get("id")))
                if rec is not None:
                    ctx.keep(str(item["id"]), rec)
        return out
    provider.__name__ = f"find_parts_{section}"
    return provider


# ---------------------------------------------------------------------------
# the built-ins register themselves like any other provider would
# ---------------------------------------------------------------------------

register_provider("knowledge", _knowledge_provider, heading="What it will know",
                  name="books")
register_provider("live_info", _live_provider, heading="Live info",
                  name="live_cards")
register_provider("updates", _updates_provider, heading="Updates",
                  name="daily_update")
register_provider("devices", _gadgets_provider, heading="Gadgets",
                  name="gadgets")
register_provider("apps_code", _parts_provider("apps_code"), heading="Apps and code",
                  name="find_parts_apps")
register_provider("pictures", _parts_provider("pictures"), heading="Pictures",
                  name="find_parts_pictures")
register_applier("knowledge.add", _apply_book)
register_applier("knowledge_add", _apply_book)
register_applier("live_feed", _apply_live)
register_applier("routine", _apply_routine)
register_remover("card", _remove_card)
register_remover("book", _remove_book)
register_remover("routine", _remove_routine)


# ---------------------------------------------------------------------------
# looks and sign-up keys: the same loop, two more sections
# ---------------------------------------------------------------------------
# Both are asked for by name ("a jungle look", "web search"), so each section
# answers only when the words ask for it, and a topic search never offers a
# look or a key. The same proposal_items feed the wizard's summary.

def _looks_provider(words: str, ctx: AddContext) -> list[dict[str, Any]]:
    from puffpai.looks import proposals
    ctx.searched.append("the free look lists saved on this computer")
    return proposals.proposal_items(words, ctx.terms)


def _apply_look(item: dict[str, Any], ctx: ApplyContext) -> dict[str, Any]:
    from puffpai.looks import proposals
    got = proposals.apply_item(item["apply"])
    line = str(got.get("message") or item["label"])
    if not got.get("ok") or not got.get("changed"):
        return _held(line)  # nothing changed, so nothing to take out later
    return {"status": "added", "line": line + " Reload the page to see it.",
            "undo": {"kind": "look", "ref": str(item["apply"]["ref"])}}


def _remove_look(undo: dict[str, Any], by: str) -> dict[str, Any]:
    from puffpai.looks import store
    now = store.current() or {}
    if str(now.get("id") or "") != str(undo.get("ref") or ""):
        return {"ok": True, "line": "That look is not in use any more, so "
                                    "nothing was changed."}
    got = store.reset()
    return {"ok": bool(got.get("ok")), "line": str(got.get("message") or "")}


def _keys_provider(words: str, ctx: AddContext) -> list[dict[str, Any]]:
    from puffpai.security import keys
    ctx.searched.append("the list of sign-up keys saved on this computer")
    return keys.proposal_items(words, ctx.terms)


def _apply_key(item: dict[str, Any], ctx: ApplyContext) -> dict[str, Any]:
    """A key is never taken here: the yes gives the steps to get one."""
    from puffpai.security import keys
    got = keys.apply_item(item["apply"], by=ctx.by, surface=ctx.surface)
    return _held(str(got.get("line") or got.get("why") or item["label"]))


def _asks_for_look(words: str) -> bool:
    from puffpai import add_intent
    return add_intent.is_look_words(words)


def _asks_for_search(words: str) -> bool:
    from puffpai import add_intent
    return add_intent.is_search_words(words)


register_provider("looks", _looks_provider, heading="How it looks",
                  name="looks", only_when=_asks_for_look)
register_provider("outside_services", _keys_provider, heading="Sign-up keys",
                  name="sign_up_keys", only_when=_asks_for_search)
register_applier("theme", _apply_look)
register_applier("key", _apply_key)
register_remover("look", _remove_look)


__all__ = ["ACTION", "AddContext", "AddError", "ApplyContext", "actionable",
           "added", "added_lines", "approve", "can_apply", "chat_turn",
           "choose_line", "confirm", "find", "forget_wishes", "get_session",
           "home_offer", "home_state", "ids_for_numbers", "looking",
           "nothing_found_lines", "online_line", "pending_note", "set_home",
           "progress_lines", "public", "register_applier", "register_provider",
           "register_remover", "render_text", "reset", "scrub", "searched_line",
           "sections", "sentence", "switch_on", "switch_on_sentence", "take_out",
           "target", "title", "wishes"]
