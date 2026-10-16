"""The one summary: everything the build will do, on one screen, one "Looks good".

The wizard ends on ONE plain summary listing every download size and every
outside service. One "Looks good" approves it; "Change something" opens any
item. No big row has to be opened before Build.

What this module holds:

* **The shapes.** A :data:`ProposalItem` is one line of the summary, and it is
  the shared shape every provider codes against (field names exact)::

      {"id", "section", "label", "detail", "size_bytes", "outside_host",
       "default_on", "needs_yes", "apply": {"kind", "ref"}}

  :func:`validate_item` is strict: a label is plain text capped at 80
  characters, a host is a hostname and nothing else, a size is a whole number
  or ``None`` (unknown stays unknown, it is never shown as zero).
* **The providers.** Each section is filled by callables returning items. The
  built-in ones wrap what this build already proposes today -- the brain card,
  the sources plan, the one real runtime door, the Home Assistant question, the
  vault. Other modules' providers expose ``proposal_items(goal, terms)`` and are
  picked up by import when their module is present
  (:data:`EXTERNAL_PROVIDER_MODULES`); anything else is registered with
  :func:`register_provider`. Fetched text a provider returns is data: it is
  validated, capped and printed, never followed.
* **The decisions.** What the owner said on the summary is stored in the run's
  shared notebook under ``summary`` (:func:`decide`), and
  :func:`answers_for_card` turns it into the answers the existing cards take,
  so the cards the owner no longer sees still apply exactly what the summary
  said and nothing else.

Nothing here downloads, opens a door or writes a file.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import threading
from typing import Any, Callable
from urllib.parse import urlparse

from puffpai.wizard import cards, wording

SCHEMA = "wizard_summary/v1"

#: The sections, in the order they are shown. The headings live in
#: :mod:`puffpai.wizard.wording` so both wordings have them.
#: Updates come straight after the brain: "want one cool thing every
#: morning?" is the bot's first offer, and at the end of a long list nobody
#: saw it (it was item 23 of 23 on one space bot's list).
#: "Apps and code", "Pictures" and "The locked box" come after the brain,
#: behind the updates, which stay straight after the brain. They are shown
#: only when something is in them, and the locked box is drawn last, under
#: everything that needs it.
SECTIONS: tuple[str, ...] = ("thinking", "updates", "apps_code", "pictures",
                             "box", "knowledge", "live_info",
                             "outside_services", "private", "devices", "looks")
#: Sections shown only when something was found for them ("How it looks" is
#: a nice extra, never a "Nothing here yet" line). Live info is no longer
#: offered here (it is an ingredient of a job in the build plan),
#: so its heading shows only on an older summary that still has lines in it.
OPTIONAL_SECTIONS: tuple[str, ...] = ("looks", "apps_code", "pictures", "box", "live_info")
#: Drawn after every other section, whatever SECTIONS says.
LAST_SECTIONS: tuple[str, ...] = ("box",)

#: Extra apply kinds (appliers live in other modules, through
#: ``factory.register_plan_applier``). Listed so a reader can find them; the
#: shape rule (:data:`_KIND_RE`) is what accepts them.
R3_APPLY_KINDS: tuple[str, ...] = ("code_project", "data_import", "cli_tool",
                                   "picture_set", "folder_palette",
                                   "box_setup")

#: How the finished bot may use the internet. Asked only under "Change
#: something", in plain words; the default is the middle one.
REACH_OFFLINE = "offline"
REACH_ONLY_LISTED = "only_listed"
REACH_ASK = "ask_each_time"
#: The old per-visit asking choice is not offered: nothing in the running bot
#: ever asked before each visit, so it was dropped. Each
#: outside service asks once, when it is added. The value is still read from
#: an older record, as "only the services listed".
REACH_CHOICES: tuple[str, ...] = (REACH_OFFLINE, REACH_ONLY_LISTED)

#: The special id "Change something" uses for the internet question.
REACH_ITEM_ID = "reach"

#: The brain's line keeps one id whichever brain is picked, so a change made
#: to it still names it after the plan is redrawn with another brain.
BRAIN_ITEM_ID = "thinking:brain"

#: Where Ollama pulls a model from. Named on the summary because a model
#: download is something leaving this computer.
MODEL_REGISTRY_HOST = "registry.ollama.ai"

#: An item kind that is a statement, not a switch: it is on the summary so the
#: owner knows, and "Change something" does not offer it.
INFO_KIND = "none"

MAX_LABEL = 80
MAX_DETAIL = 300
MAX_REASON = 120
MAX_ITEMS = 60

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9_.:/-]{1,160}$")
_HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}"
                      r"[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}"
                      r"[A-Za-z0-9])?)+$")
_KIND_RE = re.compile(r"^[a-z][a-z0-9_.]{0,39}$")

_ITEM_KEYS = ("id", "section", "label", "detail", "size_bytes",
              "outside_host", "default_on", "needs_yes", "apply")

#: Extras a line may carry. **Display only**: they are
#: drawn on the plan and kept in the decision, and no applier reads them back
#: (appliers re-derive everything from ``find_parts.found_for``).
EXTRA_KEYS = ("need", "why", "might_help", "requires", "facts")
_NEED_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
MAX_WHY = 120
MAX_REQUIRES = 6
#: The facts a line may show, and what each may hold.
_FACT_INTS = ("stars", "size_bytes")
_FACT_WORDS = ("changed", "license", "mode", "trust_path")
_FACT_HOSTS = ("hosts_all", "runtime_hosts")
MAX_FACT_HOSTS = 20


class ProposalError(ValueError):
    """An item this build will not put on the summary, and why."""


# -- the item shape -----------------------------------------------------------

def fit_detail(text: str, limit: int = MAX_DETAIL) -> str:
    """*text* in *limit* characters, cut at the end of a sentence (or, failing
    that, a word, marked with "...") -- never in the middle of a word."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = cut.rfind(". ")
    if end >= limit // 2:
        return cut[:end + 1]
    space = cut[:limit - 3].rfind(" ")
    return (cut[:space] if space > 0 else cut[:limit - 3]).rstrip(" ,;:") + "..."


def validate_item(raw: Any) -> dict:
    """One :data:`ProposalItem`, canonical, or :class:`ProposalError`.

    Unknown keys are dropped rather than refused: another provider may carry a
    field of its own, and the summary only ever reads the ones named here.
    """
    if not isinstance(raw, dict):
        raise ProposalError("An item is an object.")
    item_id = raw.get("id")
    if not isinstance(item_id, str) or not _ID_RE.match(item_id):
        raise ProposalError(f"{item_id!r} is not an item id "
                            f"(section:source:name).")
    section = raw.get("section")
    if section not in SECTIONS:
        raise ProposalError(f"{item_id}: {section!r} is not a section.")
    label = cards.plain(raw.get("label"), MAX_LABEL)
    if not label:
        raise ProposalError(f"{item_id} has no label.")
    detail = fit_detail(cards.plain(raw.get("detail"), 4 * MAX_DETAIL))
    size = raw.get("size_bytes")
    if size is not None and (isinstance(size, bool)
                             or not isinstance(size, int) or size < 0):
        raise ProposalError(f"{item_id}: a size is a whole number of bytes "
                            f"or unknown.")
    host = raw.get("outside_host")
    if host is not None:
        if not isinstance(host, str) or not _HOST_RE.match(host):
            raise ProposalError(f"{item_id}: {host!r} is not a host name.")
        host = host.lower()
    apply_raw = raw.get("apply")
    if not isinstance(apply_raw, dict):
        raise ProposalError(f"{item_id}: apply is an object.")
    kind = apply_raw.get("kind")
    ref = apply_raw.get("ref")
    if not isinstance(kind, str) or not _KIND_RE.match(kind):
        raise ProposalError(f"{item_id}: {kind!r} is not an apply kind.")
    if not isinstance(ref, str) or len(ref) > 200:
        raise ProposalError(f"{item_id}: an apply ref is a short string.")
    extra = {}
    reason = cards.plain(raw.get("off_reason"), MAX_REASON) \
        if isinstance(raw.get("off_reason"), str) else ""
    if reason:
        # Optional: why it starts off, when the provider knows better than
        # :func:`off_reason` can tell from the line itself.
        extra["off_reason"] = reason
    if raw.get("needs_town") is True:
        # Optional: this line only works once the bot knows its home town
        # (the weather, a live card about the weather where you live). The
        # plan asks for the town once, on the first such line.
        extra["needs_town"] = True
    extra.update(_extras(raw))
    return {**extra,
        "id": item_id,
        "section": section,
        "label": label,
        "detail": detail,
        "size_bytes": size,
        "outside_host": host,
        "default_on": bool(raw.get("default_on")),
        "needs_yes": bool(raw.get("needs_yes")),
        "apply": {"kind": kind, "ref": cards.plain(ref, 200)},
    }


def _extras(raw: dict) -> dict:
    """The extras, cleaned: a wrong shape is dropped, never guessed."""
    out: dict = {}
    need = raw.get("need")
    if isinstance(need, str) and _NEED_RE.match(need):
        out["need"] = need
    why = cards.plain(raw.get("why"), MAX_WHY) if isinstance(
        raw.get("why"), str) else ""
    if why:
        out["why"] = why
    if isinstance(raw.get("might_help"), bool):
        out["might_help"] = raw["might_help"]
    requires = raw.get("requires")
    if isinstance(requires, (list, tuple)):
        kinds = [k for k in requires if isinstance(k, str) and _KIND_RE.match(k)]
        if kinds:
            out["requires"] = list(dict.fromkeys(kinds))[:MAX_REQUIRES]
    facts = raw.get("facts")
    if isinstance(facts, dict):
        clean = _clean_facts(facts)
        if clean:
            out["facts"] = clean
    return out


def _clean_facts(facts: dict) -> dict:
    out: dict = {}
    for key in _FACT_INTS:
        value = facts.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[key] = value
    for key in _FACT_WORDS:
        value = cards.plain(facts.get(key), 40) if isinstance(
            facts.get(key), str) else ""
        if value:
            out[key] = value
    for key in _FACT_HOSTS:
        value = facts.get(key)
        if isinstance(value, (list, tuple)):
            out[key] = [h.lower() for h in value if isinstance(h, str)
                        and _HOST_RE.match(h)][:MAX_FACT_HOSTS]
    if isinstance(facts.get("box"), bool):
        out["box"] = facts["box"]
    return out


def toggleable(item: dict) -> bool:
    """Can "Change something" turn this on or off?"""
    return item.get("apply", {}).get("kind") != INFO_KIND


# -- providers ----------------------------------------------------------------

class SummaryContext:
    """What a built-in provider is handed. Read-only.

    ``previews`` holds the propose() result of the cards the summary speaks
    for (``brain``, ``sources``, ``secure:posture``), proposed against a
    provisional notebook, so the summary and the cards that apply it later are
    the same proposal.
    """

    def __init__(self, *, goal: str, terms: list[str], name: str, mode: str,
                 shared: dict, previews: dict, args: Any, cfg: Any,
                 internet: bool | None) -> None:
        self.goal = goal
        self.terms = terms
        self.name = name
        self.mode = mode
        self.shared = shared
        self.previews = previews
        self.args = args
        self.cfg = cfg
        self.internet = internet
        #: What ``find_parts`` found for the owner's jobs, set by
        #: the parts provider when the needs step ran; ``None`` otherwise.
        self.parts: dict | None = None


Provider = Callable[[SummaryContext], list]

_PROVIDERS: list[tuple[str, Provider]] = []

#: Modules that may expose ``proposal_items(goal, terms)``, imported only if
#: present. Known providers are wired by name instead
#: (:data:`puffpai.wizard.factory.PROVIDERS`, run after the built-ins), so each
#: section has exactly one provider; this stays for a later package.
EXTERNAL_PROVIDER_MODULES: tuple[str, ...] = ()


def register_provider(name: str, provider: Provider) -> None:
    """Add a provider. Called at import time by a module that has one."""
    if not callable(provider):
        raise ProposalError("A provider is a callable.")
    _PROVIDERS[:] = [(n, p) for n, p in _PROVIDERS if n != name]
    _PROVIDERS.append((str(name), provider))


def unregister_provider(name: str) -> None:
    _PROVIDERS[:] = [(n, p) for n, p in _PROVIDERS if n != name]


def _external_items(goal: str, terms: list[str]) -> tuple[list, list[str]]:
    items: list = []
    notes: list[str] = []
    for module_name in EXTERNAL_PROVIDER_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 - absent is the normal case
            continue
        func = getattr(module, "proposal_items", None)
        if not callable(func):
            continue
        try:
            found = func(goal, list(terms))
        except Exception as exc:  # noqa: BLE001 - one provider never sinks the rest
            notes.append(f"{module_name} could not say what it offers: "
                         f"{type(exc).__name__}")
            continue
        if isinstance(found, list):
            items.extend(found)
    return items, notes


def _gb(size: Any) -> str:
    return cards.format_bytes(size) or "a small amount"


def _thinking(sctx: SummaryContext) -> list[dict]:
    """The brain, from the Brain card's own proposal, and the team line."""
    out: list[dict] = []
    brain = sctx.previews.get("brain") or {}
    model = str(brain.get("model") or "")
    menu = brain.get("menu") or {}
    gates = brain.get("gates") or {}
    options = {str(opt.get("id")): opt for opt in menu.get("options") or ()}
    if model:
        opt = options.get(model) or {}
        installed = bool(opt.get("installed")) or model in [
            str(m) for m in (menu.get("installed_models") or ())]
        may_download = bool(gates.get("may_download_models", True))
        disk_gb = opt.get("disk_gb")
        fits = opt.get("fit") == "comfortable" if opt else None
        if installed:
            out.append({
                "id": BRAIN_ITEM_ID, "section": "thinking",
                "label": f"The brain: {model}",
                "detail": "Already on this computer, so nothing is "
                          "downloaded. " + _limits(model),
                "size_bytes": None, "outside_host": None,
                "default_on": True, "needs_yes": False,
                "apply": {"kind": "model", "ref": model}})
        else:
            size = int(float(disk_gb) * 1024 ** 3) if disk_gb else None
            fit_words = ("It fits this computer." if fits
                         else "It is a tight fit for this computer."
                         if fits is False else
                         "This build cannot tell if it fits this computer.")
            detail = (f"Download: {_gb(size) if size else 'size not known'}, "
                      f"from {MODEL_REGISTRY_HOST}. {fit_words} "
                      f"{_limits(model)}")
            if not may_download:
                detail = ("Not on this computer, and this build is not "
                          "downloading anything. " + _limits(model))
            out.append({
                "id": BRAIN_ITEM_ID, "section": "thinking",
                "label": f"The brain: {model}",
                "detail": detail,
                "size_bytes": size if may_download else None,
                "outside_host": MODEL_REGISTRY_HOST if may_download else None,
                "default_on": bool(may_download and fits
                                   and sctx.internet is True),
                "needs_yes": bool(may_download),
                "apply": {"kind": "model_pull" if may_download else "model",
                          "ref": model}})
    elif menu.get("message"):
        # Nothing fits (or the memory is unknown): the plan says so in the
        # menu's own words instead of leaving the brain off the page.
        out.append({
            "id": "thinking:no-brain", "section": "thinking",
            "label": "No brain picked",
            "detail": str(menu["message"]),
            "size_bytes": None, "outside_host": None,
            # A statement, shown with its reason like the team line below,
            # not a switch listed as "Left out".
            "default_on": True, "needs_yes": False,
            "apply": {"kind": INFO_KIND, "ref": ""}})
    count = _team_size()
    if count:
        out.append({
            "id": "thinking:team", "section": "thinking",
            "label": wording.say("team_label", sctx.mode, count=count,
                                 name=sctx.name),
            "detail": wording.say("team_detail", sctx.mode),
            "size_bytes": None, "outside_host": None,
            "default_on": True, "needs_yes": False,
            "apply": {"kind": INFO_KIND, "ref": ""}})
    return out


def _limits(model: str) -> str:
    try:
        from puffpai import model_profile
        return model_profile.limits_sentence(model)
    except Exception:  # noqa: BLE001
        return ""


def _team_size() -> int:
    try:
        from puffpai.agents import registry
        return len(registry.ROSTER or ())
    except Exception:  # noqa: BLE001 - an unreadable roster claims nothing
        return 0


def _host_of(url: Any) -> str | None:
    try:
        host = urlparse(str(url or "")).hostname or ""
    except ValueError:
        return None
    return host.lower() if host and _HOST_RE.match(host) else None


#: At most this many matched books are listed besides the ticked ones; the
#: rest matched more loosely and are one line saying so. A space plan used to
#: list 25 lines including Medical Sciences Q&A.
KNOWLEDGE_SHOWN = 3
#: General encyclopedias listed after the matches, at most.
GENERAL_SHOWN = 2


def _knowledge_order(items: list[dict], wanted: set, goal: str
                     ) -> tuple[list[dict], int]:
    """(rows to list, how many more were left off), best first.

    Ticked rows first, then rows that matched the owner's own goal words (at
    most :data:`KNOWLEDGE_SHOWN`), then general encyclopedias (at most
    :data:`GENERAL_SHOWN`). Rows that matched only a word the model suggested
    are left off the plan and counted.
    """
    from puffpai.wizard import sources_plan
    ticked = [i for i in items if i["id"] in wanted]
    rest = [i for i in items if i["id"] not in wanted]
    matched = [i for i in rest if i.get("matched_terms")
               and sources_plan.goal_match(i, goal)]
    general = [i for i in rest if not i.get("matched_terms")
               and not i.get("selected_by")]
    shown = ticked + matched[:KNOWLEDGE_SHOWN] + general[:GENERAL_SHOWN]
    return shown, len(items) - len(shown)


def _nothing_specific(sctx: SummaryContext, shown: list[dict]) -> list[dict]:
    """One plain line when no book is about the goal itself."""
    if any(i.get("matched_terms") or i.get("selected_by") for i in shown):
        return []
    from puffpai import bot_identity
    topic = bot_identity.topic_phrase(sctx.goal, form="subject") or "this"
    return [{
        "id": "knowledge:none_found", "section": "knowledge",
        "label": f"No library about {topic} was found yet"[:MAX_LABEL],
        "detail": (f"{sctx.name} will answer from what its brain already knows. "
                   f"You can add your own files (for example PDFs) on the "
                   f"dashboard's Documents page, or look again later on its "
                   f"Find more knowledge panel.")[:MAX_DETAIL],
        "size_bytes": None, "outside_host": None, "default_on": True,
        "needs_yes": False, "apply": {"kind": INFO_KIND, "ref": ""}}]


def _topic_pack(item: dict) -> bool:
    """A library pack about the goal's topic (matched or picked), not a whole
    encyclopedia, an old book or a textbook."""
    if not (item.get("matched_terms") or item.get("selected_by")):
        return False
    try:
        from puffpai.catalogs import knowledge_resolver as kr
        cand = kr.candidate_by_id(str(item.get("id") or "")) or {}
        tier = kr.tier_of(cand) if cand else ""
    except Exception:  # noqa: BLE001 - an unreadable list: judged by its id
        tier = ""
    head = str(item.get("id") or "").split(":", 1)[0]
    return tier not in ("gutenberg", "openstax", "wikipedia_topic") \
        and head not in ("gutenberg", "openstax", "wikipedia")


def _knowledge(sctx: SummaryContext) -> list[dict]:
    """The vetted downloads the sources plan found for this goal."""
    sources = sctx.previews.get("sources") or {}
    items = [item for item in sources.get("items") or ()
             if isinstance(item, dict) and item.get("selectable")]
    wanted = set(sources.get("selected") or ())
    # Only when the search ran and found something general: a skipped or
    # offline search found nothing, and "nothing about it" would be untrue.
    out: list[dict] = _nothing_specific(sctx, items) if items else []
    items, left_off = _knowledge_order(items, wanted, sctx.goal)
    if needs_active(getattr(sctx, "shared", None)):
        # With her jobs, only topic packs from this word-matched list: no
        # whole encyclopedia, no old book or textbook (those come from
        # find_parts, checked by meaning and the reader's level).
        kept = [i for i in items if _topic_pack(i)]
        left_off += len(items) - len(kept)
        items = kept
    for item in items[:20]:
        size = item.get("bytes")
        size = int(size) if isinstance(size, (int, float)) and size else None
        licence = str(item.get("license") or "license not stated")
        use = ("free to use, even for business" if item.get("commercial_use")
               else "free to use, but not for business")
        what = _plain_sentence(item.get("what_it_is"), 160)
        host = _host_of(item.get("source_url"))
        if not host and (item.get("kiwix_book")
                         or str(item.get("id") or "").startswith("kiwix:")):
            # A library book: its first hop is the library's download site.
            from puffpai.catalogs import kiwix_opds
            host = kiwix_opds.DOWNLOAD_HOST
        size_words = f"Download: {_gb(size)}" if size else "Size not known"
        # The size is in the label, so a left-out book (whose detail is not
        # shown) still says how big it is.
        from puffpai.catalogs import knowledge_resolver
        label = (f"{item.get('label') or item['id']} "
                 f"({knowledge_resolver.size_words(size)})")
        if len(label) > MAX_LABEL:
            label = label[:MAX_LABEL - 3].rstrip() + "..."
        out.append({
            "id": f"knowledge:registry:{item['id']}", "section": "knowledge",
            "label": label,
            "detail": f"{what} {size_words}, from {host or 'its home site'}. "
                      f"License {licence}, {use}.".strip(),
            "size_bytes": size, "outside_host": host,
            # A heavy download (Wikipedia is 49 GB) is listed with its size
            # and left for the owner to tick; "Looks good" alone never
            # starts one.
            "default_on": bool(item["id"] in wanted and sctx.internet is True
                               and size is not None
                               and size <= _heavy_bytes()),
            "needs_yes": True,
            # A general encyclopedia is listed for every goal; it is not a
            # loose match for this one.
            "off_reason": ("" if item.get("matched_terms")
                           or item.get("selected_by") else
                           wording.say("off_general", sctx.mode)),
            "apply": {"kind": "source_fetch", "ref": str(item["id"])}})
    if left_off > 0:
        out.append({
            "id": "knowledge:more", "section": "knowledge",
            "label": (f"{left_off} more book{'s' if left_off != 1 else ''} "
                      f"matched more loosely")[:MAX_LABEL],
            "detail": ("They are not on this plan. After the build you can look "
                       "through them on the dashboard's Find more knowledge "
                       "panel."),
            "size_bytes": None, "outside_host": None, "default_on": True,
            "needs_yes": False, "apply": {"kind": INFO_KIND, "ref": ""}})
    out.extend(_look_yourself(sctx, sources))
    return out


def _heavy_bytes() -> int:
    from puffpai.wizard import sources_plan
    return int(sources_plan.HEAVY_BYTES)


def _look_yourself(sctx: SummaryContext, sources: dict) -> list[dict]:
    """What the brain suggested outside the lists BuildBot checks.

    A model find outside the checked catalogs is shown
    as "look it up yourself" and never fetched. Only the name and the model's
    one-line reason are shown -- not its web address, because a model may
    make one up -- and nothing about it is switched on or opened.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for row in (sources.get("suggested") or ())[:5]:
        if not isinstance(row, dict):
            continue
        name = _plain_sentence(row.get("label"), 60)
        host = str(row.get("host") or "")
        if not name or not host or host in seen:
            continue
        seen.add(host)
        why = _plain_sentence(row.get("why"), 160)
        out.append({
            "id": f"knowledge:look_yourself:{host}", "section": "knowledge",
            "label": wording.say("look_yourself_label", sctx.mode, name=name),
            # The "nothing is downloaded" sentence once, on the first note:
            # three copies of it is a wall of text.
            "detail": " ".join(part for part in (
                why, "" if out else wording.say(
                    "look_yourself_detail", sctx.mode, name=sctx.name))
                if part),
            # "On" for a note means it is shown in full on the plan, like the
            # helpers line; there is nothing behind it to switch or fetch.
            "size_bytes": None, "outside_host": None,
            "default_on": True, "needs_yes": False,
            "apply": {"kind": INFO_KIND, "ref": ""}})
    return out


#: Words that belong to the build machinery, not to a builder.
#: A catalog's own sentence that uses one is left off the summary rather than
#: shown -- the label, size, host and license still say what the item is.
_LINGO = re.compile(r"(?<![A-Za-z_])(doors?|posture|tiers?|surfaces?|manifest|"
                    r"zones?|egress|sealed|island|enumerated|moat|routing|"
                    r"sourcing|catalog(?:s|ue)?)(?![A-Za-z_])", re.I)


def _plain_sentence(text: Any, limit: int) -> str:
    clean = cards.plain(text, limit)
    return "" if _LINGO.search(clean) else clean


def _outside(sctx: SummaryContext) -> list[dict]:
    """The runtime doors this build really has a switch for.

    Built from ``egress_doors`` rather than from the wizard's old menu: a door
    listed on a screen that the running bot has no switch for is a promise
    nobody keeps. Every one starts off
    unless the command line named it.
    """
    try:
        from puffpai import build_posture
        from puffpai.security import egress_doors
        switches = {str(row["id"]): row for row in build_posture.egress_switches()}
        doors = egress_doors.DOORS
        doors = doors.values() if isinstance(doors, dict) else doors
    except Exception:  # noqa: BLE001
        return []
    named = {str(d) for d in (getattr(sctx.args, "egress_door", None) or ())}
    out: list[dict] = []
    for door in doors:
        switch = getattr(door, "posture_switch", None)
        if not switch or switch not in switches:
            continue
        hosts = [h for h in (getattr(door, "hosts", None) or ()) if _HOST_RE.match(str(h))]
        row = switches[switch]
        item = {
            "id": f"outside_services:door:{switch}",
            "section": "outside_services",
            "label": str(row.get("label") or switch),
            "detail": str(row.get("description") or ""),
            "size_bytes": None,
            "outside_host": str(hosts[0]) if hosts else None,
            "default_on": switch in named,
            "needs_yes": True,
            "apply": {"kind": "door", "ref": switch}}
        out.append(_service_words(item, switch, hosts, sctx))
    return out


def _service_words(item: dict, switch: str, hosts: list, sctx: SummaryContext
                   ) -> dict:
    """A built-in service in the page's own plain words, on when it fits.

    Otherwise a weather card can sit on a bot's page list with its service
    left off, so the card can only say why it is empty.
    A service whose card the goal asked for (the capability list decides, see
    :func:`puffpai.weather_service.goal_fits`) is proposed ON, with what leaves
    the computer said on the line; "Looks good" is the yes.
    """
    try:
        from puffpai import outside_services
        said = outside_services._BUILT_IN.get(switch) or {}  # noqa: SLF001
    except Exception:  # noqa: BLE001
        said = {}
    if said:
        where = f", sent to {hosts[0]}" if hosts else ""
        item["label"] = str(said.get("name") or item["label"])
        item["detail"] = (f"{said.get('for', '')} What leaves your computer: "
                          f"{said.get('what_leaves', '')}".strip()
                          .rstrip(".") + f"{where}. No sign-up needed.")
    if switch in TOWN_SERVICES:
        item["needs_town"] = True
    if switch == "weather" and not item["default_on"]:
        item["default_on"] = weather_fits(sctx.goal)
    return item


#: Words that name the weather in one sense and something else in another
#: ("my laptop freezes", "I freeze up in interviews", "a cold call"): a goal
#: that matches the weather card on one of these alone is not about the
#: weather (else Weather forecast is pre-ticked on a Minecraft server and on
#: a job hunt).
_WEATHER_OTHER_SENSE = frozenset({"freeze", "freezes", "freezing", "frozen", "cold", "colds",
                                  "hot", "heat", "storm", "storms", "cool", "chill"})


def weather_fits(goal: str) -> bool:
    """Is the goal about the weather (the forecast is offered switched on)?
    The capability list decides (:func:`puffpai.weather_service.goal_fits`),
    and a match on a word that also means something else does not count."""
    text = str(goal or "").strip()
    if not text:
        return False
    try:
        from puffpai import build_interview, weather_service
        flat = build_interview.propose(text).get("flat") or []
        page = getattr(weather_service, "WEATHER_PAGE", "daily.weather")
    except Exception:  # noqa: BLE001 - "does not fit" is the safe answer
        return False
    for item in flat:
        if not isinstance(item, dict) or page not in (item.get("surfaces") or []):
            continue
        terms = [str(t).lower() for t in item.get("matched_terms") or []]
        if any(t not in _WEATHER_OTHER_SENSE for t in terms):
            return True
    return False


#: Built-in services that only work once the bot knows its home town
#: (build_abilities._NEEDS_TOWN is the same pair).
TOWN_SERVICES = frozenset({"weather", "air_quality"})


def _private(sctx: SummaryContext) -> list[dict]:
    if getattr(sctx.args, "skip_vault", False):
        detail = "You asked to skip the vault, so nothing is locked away yet."
    else:
        detail = ("Only your PIN opens it, and the PIN never leaves this "
                  "computer. You choose it right after you say yes.")
    return [{"id": "private:vault", "section": "private",
             "label": "Kept in a locked vault on this computer",
             "detail": detail, "size_bytes": None, "outside_host": None,
             "default_on": True, "needs_yes": False,
             "apply": {"kind": INFO_KIND, "ref": "vault"}}]


def _devices(sctx: SummaryContext) -> list[dict]:
    if getattr(sctx.args, "skip_ha", False):
        return []
    gates = (sctx.previews.get("secure:posture") or {}).get(
        "shared_preview", {}).get("gates") or {}
    if gates and not gates.get("may_offer_lan_integrations", True):
        return []
    stated = bool(getattr(sctx.args, "ha_url", None)
                  and getattr(sctx.args, "ha_token", None))
    # Most people have no hub. It is offered only
    # when the goal asks for what only a hub does (lights, plugs, thermostats,
    # locks, home sensors), or when its address was given, and as an "if".
    try:
        from puffpai import weather_service
        wanted = weather_service.needs_hub(sctx.goal)
    except Exception:  # noqa: BLE001
        wanted = False
    if not (wanted or stated):
        return []
    # "If you have Home Assistant" never shows where it is not in use: with
    # the hub set up it is "your" Home Assistant; without
    # one, the hub is offered (for a smart-home goal only) as a hub to add.
    try:
        from puffpai import setup_ops
        configured = bool(setup_ops.ha_configured())
    except Exception:  # noqa: BLE001 - unreadable settings: not in use
        configured = False
    if configured or stated:
        label = "Use your Home Assistant"
        detail = (f"Lets {sctx.name} see and control the lights, plugs and "
                  f"other gadgets your Home Assistant runs. It talks to the "
                  f"hub on your home network only.")
    else:
        label = "Home Assistant, a smart home hub"
        detail = (f"Lets {sctx.name} see and control the lights, plugs and "
                  f"other gadgets in your home through a Home Assistant hub. "
                  f"Leave it off when there is no hub in your home: nothing "
                  f"else needs it. It talks to the hub on your home network "
                  f"only, and you type the hub's address and key next.")
    return [{"id": "devices:home_assistant", "section": "devices",
             "label": label,
             "detail": detail,
             "size_bytes": None, "outside_host": None,
             "default_on": stated, "needs_yes": True,
             "apply": {"kind": "home_assistant", "ref": "connect"}}]


def _factory(name: str) -> Provider:
    """A provider from :mod:`puffpai.wizard.factory`, imported when it runs."""
    def provider(sctx: SummaryContext) -> list:
        from puffpai.wizard import factory
        return dict(factory.PROVIDERS)[name](sctx)
    return provider


_BUILTIN: tuple[tuple[str, Provider], ...] = (
    ("builtin:thinking", _thinking),
    ("builtin:knowledge", _knowledge),
    ("builtin:outside", _outside),
    ("builtin:private", _private),
    ("builtin:devices", _devices),
    ("factory:knowledge", _factory("factory:knowledge")),
    ("factory:updates", _factory("factory:updates")),
    ("factory:private", _factory("factory:private")),
    ("factory:devices", _factory("factory:devices")),
    ("factory:keys", _factory("factory:keys")),
    ("factory:looks", _factory("factory:looks")),
    ("r3:find_parts", lambda sctx: _found_parts(sctx)),
)

#: Providers the needs engine replaces. ``builtin:knowledge`` is
#: the interview's word-matched library list: with the owner's jobs known, a
#: book comes only from ``find_parts`` for a "learn about" job, never because
#: the goal happened to contain a word like "ideas".
#: ``factory:devices`` matches Home Assistant gadget lines on a bare word
#: ("Your practice games against it, if you have Home Assistant (Epic Games
#: Store)" on a chess plan): with jobs, only what fits a job by meaning is
#: offered.
SKIPPED_WITH_NEEDS: frozenset = frozenset({"builtin:knowledge",
                                           "factory:knowledge",
                                           "factory:devices"})
#: The plan section each older provider fills.
_OLDER_SECTION: dict[str, str] = {"builtin:knowledge": "knowledge",
                                  "factory:knowledge": "knowledge",
                                  "factory:devices": "devices"}


def skipped_with_needs(sctx: "SummaryContext") -> frozenset:
    """Which older providers the owner's jobs replace, for this plan.

    The older word-matching providers step aside only where the jobs really
    replace them (Milestone S: a needs step with no jobs dropped every live
    card and book):

    * ``find_parts`` found lines for that section: it speaks for it.
    * No ticked job (the brain did not answer and the goal lists nothing):
      the plan is drawn as before the needs step.
    * Books: only a "learn about" job asks for them, so
      with jobs and none of them a learn job, the word-matched books stay off
      (and with one, ``factory.knowledge_items`` keeps only topic packs).
    * Live info with jobs: from ``find_parts`` only, so a website is never
      offered (or looked at) because it shares a word with the goal.
    """
    if not needs_active(sctx.shared):
        return frozenset()
    parts = parts_for(sctx.shared, goal=sctx.goal, cfg=sctx.cfg)
    sctx.parts = parts
    covered = {str(item.get("section") or "") for item in parts.get("items") or ()}
    jobs = [job for job in (needs_of(sctx.shared) or {}).get("jobs") or ()
            if isinstance(job, dict) and job.get("ticked")]
    learn = any(job.get("learn") is True for job in jobs)
    skip: set[str] = set()
    for name, section in _OLDER_SECTION.items():
        if section in covered:
            skip.add(name)
        elif not jobs:
            continue
        elif section == "knowledge" and learn:
            continue
        else:
            skip.add(name)
    return frozenset(skip)


# -- the summary --------------------------------------------------------------

def goal_terms(goal: str) -> list[str]:
    """Search words for a goal: the meaningful words, in order. No model.

    The one search-word extractor
    (``catalogs.goal_words.search_terms``) when it is in this build, so
    personal words never become search words; today's keywords otherwise."""
    words = _optional("puffpai.catalogs.goal_words")
    if words is not None and callable(getattr(words, "search_terms", None)):
        try:
            return list(words.search_terms(goal))
        except Exception:  # noqa: BLE001 - fall back to today's words
            pass
    try:
        from puffpai import persona_scaffold
        return list(persona_scaffold.goal_keywords(goal))
    except Exception:  # noqa: BLE001
        return []


def collect_items(sctx: SummaryContext) -> tuple[list[dict], list[str]]:
    """Every provider's items, validated, de-duplicated by id, capped.

    When the needs step ran, what was found for the owner's jobs
    comes from ``find_parts`` and the older word-matching knowledge and
    live-info providers are not asked: one engine, not two lists."""
    raw: list = []
    notes: list[str] = []
    skipped = skipped_with_needs(sctx)
    for name, provider in (*_BUILTIN, *_PROVIDERS):
        if name in skipped:
            continue
        try:
            found = provider(sctx)
        except Exception as exc:  # noqa: BLE001 - a provider never sinks the summary
            notes.append(f"{name} could not say what it offers: "
                         f"{type(exc).__name__}")
            continue
        raw.extend(found if isinstance(found, list) else [])
    external, more = _external_items(sctx.goal, sctx.terms)
    raw.extend(external)
    notes.extend(more)

    raw = _one_line_per_website(raw)
    seen: set[str] = set()
    items: list[dict] = []
    for entry in raw:
        try:
            item = validate_item(entry)
        except ProposalError as exc:
            notes.append(str(exc))
            continue
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        items.append(item)
        if len(items) >= MAX_ITEMS:
            break
    return items, notes


def _one_line_per_website(raw: list) -> list:
    """A built-in service proposed ON speaks for its website: a live card
    reading the same website is the same thing said twice (her space bot had
    "Live: Open-Meteo weather" and "Weather" both, and only one worked)."""
    on_hosts = {str(e.get("outside_host")) for e in raw
                if isinstance(e, dict) and e.get("default_on")
                and (e.get("apply") or {}).get("kind") == "door"
                and e.get("outside_host")}
    if not on_hosts:
        return raw
    return [e for e in raw if not (
        isinstance(e, dict) and e.get("section") == "live_info"
        and str(e.get("outside_host") or "") in on_hosts)]


def model_choices(brain_preview: dict) -> list[dict]:
    """The other brains this computer can run, for "Change something"."""
    menu = (brain_preview or {}).get("menu") or {}
    out: list[dict] = []
    for opt in menu.get("options") or ():
        model = str(opt.get("id") or "")
        if not model:
            continue
        disk = opt.get("disk_gb")
        if opt.get("installed"):
            words = "already on this computer"
        elif disk:
            words = f"{_gb(int(float(disk) * 1024 ** 3))} to download"
        else:
            words = "size not known"
        fit = ("fits well" if opt.get("fit") == "comfortable"
               else "a tight fit")
        out.append({"value": model,
                    "label": cards.plain(f"{opt.get('label') or model} "
                                         f"({model}): {words}, {fit}",
                                         cards.MAX_LABEL),
                    "size_bytes": (0 if opt.get("installed") else
                                   int(float(disk) * 1024 ** 3) if disk
                                   else None)})
    return out[:12]


def reach_choices(name: str, mode: str) -> list[dict]:
    return [{"value": value,
             "label": wording.say(f"reach_{value}", mode, name=name)}
            for value in REACH_CHOICES]


def default_reach(args: Any) -> str:
    """What the command line already said about runtime reach, or the default."""
    run_egress = str(getattr(args, "run_egress", "") or "")
    bridge = str(getattr(args, "bridge_posture", "") or "")
    if run_egress in ("sealed", "island") or bridge == "drawbridge_up":
        return REACH_OFFLINE
    # A --bridge-posture flag is kept as given (bridge_for leaves it alone).
    return REACH_ONLY_LISTED


# -- found, and not included --------------------------------------------------
#
# A bare "Left out: TED telescopes (about 301 MB)" leaves people asking what it
# means and why it was left out. A line that starts off says it is
# not included, that it can be added, and one plain reason. The same line is
# sent to the browser (``off_line``), so both faces print the same words.

def _relevance_client(sctx: SummaryContext):
    """The local model that judges found books, or ``None`` (the rule judges)."""
    if getattr(sctx.args, "no_suggestions", False):
        return None
    from puffpai.wizard import relevance

    shared = sctx.shared or {}
    installed = [str(m) for m in (shared.get("installed_models") or ())]
    url = str(shared.get("ollama_url") or "")
    if not url or not installed:
        return None
    wanted = [str(shared.get("chosen_model") or ""),
              str((shared.get("first_look") or {}).get("model") or ""),
              str(getattr(getattr(sctx.cfg, "ollama", None), "model", "")
                  or "")]
    model = next((m for m in wanted if m and m in installed), installed[0])
    return relevance.memo_client(url, model)


def _propose_topic(sctx: SummaryContext) -> None:
    """Let the local model propose the bot's topic phrase, once per goal.

    Code keeps the phrase only when every word of it is in the goal
    (:func:`puffpai.bot_identity.accept_proposal`); every line drawn after
    this, and the closing screen, then use it. No model, no change: the
    deterministic rule names the topic.
    """
    if not sctx.goal:
        return
    try:
        from puffpai import bot_identity
        bot_identity.topic_phrase(sctx.goal, ask=_relevance_client(sctx))
    except Exception:  # noqa: BLE001 - a proposal never sinks the plan
        pass


def _relevant(sctx: SummaryContext, items: list[dict],
              notes: list[str]) -> list[dict]:
    """*items* without found books that only share a word with the goal."""
    from puffpai.wizard import relevance

    try:
        kept, dropped, how = relevance.cut(sctx.goal, items,
                                           _relevance_client(sctx))
    except Exception as exc:  # noqa: BLE001 - a judge never sinks the plan
        notes.append(f"the relevance check did not run: {type(exc).__name__}")
        return items
    if dropped:
        notes.append(f"left off as not about the goal ({how}): "
                     + ", ".join(dropped)[:200])
    return kept


_SIZE_IN_LABEL = re.compile(r"^(?P<name>.*\S)\s*\((?P<size>(?:about|up to) "
                            r"[^)]*)\)\s*$")


def off_reason(item: dict, sctx: SummaryContext, picked_bytes: int = 0) -> str:
    """One plain reason a line starts off. ``""`` when its label says it."""
    mode = sctx.mode
    kind = item.get("apply", {}).get("kind")
    section = item.get("section")
    reaches = (kind in ("model_pull", "source_fetch", "knowledge.add")
               or section in ("live_info", "outside_services"))
    if reaches and sctx.internet is False:
        return wording.say("off_offline", mode)
    if kind in ("source_fetch", "knowledge.add"):
        detail = str(item.get("detail") or "").lower()
        if "personal use only" in detail or "not for business" in detail:
            return wording.say("off_personal_use", mode)
        size = item.get("size_bytes")
        if size is None:
            return wording.say("off_size_unknown", mode)
        from puffpai.wizard import sources_plan
        if size > sources_plan.DEFAULT_ON_BYTES:
            return wording.say("off_big", mode)
        if item.get("off_reason"):
            return str(item["off_reason"])
        ident = str(item.get("id") or "")
        if ident.startswith(("knowledge:gutenberg:", "knowledge:openstax:")):
            # Never ticked for anybody (sources_plan.knowledge_default_on).
            return wording.say("off_owner_pick", mode)
        if picked_bytes and size > picked_bytes:
            return wording.say("off_bigger", mode)
        return wording.say("off_loose", mode)
    if item.get("off_reason"):
        return str(item["off_reason"])
    if kind in ("model", "model_pull"):
        return wording.say("off_offline" if sctx.internet is False
                           else "off_brain", mode)
    if str(item.get("id") or "").startswith("outside_services:key:"):
        return ""  # "...: needs a free key" is the reason, in the label
    if section in ("live_info", "outside_services"):
        return wording.say("off_outside", mode)
    if section == "updates":
        return wording.say("off_update", mode)
    if kind == "home_assistant":
        return wording.say("off_hub", mode)
    if section == "devices":
        return ""  # "Your telescope: no ready way to connect found yet"
    return wording.say("off_default", mode)


#: The names a person knows for the places a plan downloads from, with what
#: comes from there ("requests to codeload.github.com" reads as nothing she
#: knows).
_APPS = "GitHub, where the apps come from"
_PICTURES = "where the pictures come from"
HOST_NAMES = {"codeload.github.com": _APPS, "github.com": _APPS, "api.github.com": _APPS,
              "raw.githubusercontent.com": _APPS, "objects.githubusercontent.com": _APPS,
              "codeberg.org": "Codeberg, where the apps come from",
              "registry.npmjs.org": "npm, where the code comes from",
              "upload.wikimedia.org": f"Wikimedia Commons, {_PICTURES}",
              "commons.wikimedia.org": f"Wikimedia Commons, {_PICTURES}",
              "live.staticflickr.com": f"Flickr, {_PICTURES}",
              "api.openverse.org": f"Openverse, {_PICTURES}",
              "api.openverse.engineering": f"Openverse, {_PICTURES}"}


def host_name(host: str) -> str:
    """"GitHub, where the apps come from" for codeload.github.com; any other
    address as it is."""
    return HOST_NAMES.get(str(host or "").lower(), str(host or ""))


def host_names(hosts) -> str:
    """The footer's list of places, each said once ("; " between them, as
    each may carry its own comma)."""
    return "; ".join(dict.fromkeys(host_name(h) for h in hosts))


def off_line(item: dict, mode: str, reason: str = "") -> str:
    """"Not included (you can add it): TED telescopes - about 301 MB, free for
    personal use only". The size moves out of the brackets to sit beside the
    reason."""
    label = str(item.get("label") or "")
    match = _SIZE_IN_LABEL.match(label)
    name, size = (match.group("name"), match.group("size")) if match \
        else (label, "")
    tail = ", ".join(part for part in (size, reason) if part)
    line = wording.say("item_off", mode, label=name)
    return f"{line} - {tail}" if tail else line


def detail_when_off(item: dict) -> bool:
    """Does an unticked line still show its detail? Yes for a find made for
    the owner's jobs (it has a job or facts to show): "Not included" alone
    would hide what it is (Milestone S)."""
    return bool(item.get("detail")) and bool(item.get("need") or item.get("facts"))


def _set_off_lines(sctx: SummaryContext, items: list[dict]) -> None:
    """Give every switchable line the words it shows when it is off."""
    picked = [int(i["size_bytes"]) for i in items
              if i.get("on") and i["section"] == "knowledge"
              and i["apply"]["kind"] in ("source_fetch", "knowledge.add")
              and isinstance(i.get("size_bytes"), int)]
    biggest = max(picked) if picked else 0
    for item in items:
        if toggleable(item):
            item["off_line"] = off_line(item, sctx.mode,
                                        off_reason(item, sctx, biggest))


def build(sctx: SummaryContext, state: dict | None = None) -> dict:
    """The summary object both faces render. Pure: reads, never writes.

    *state* is a stored decision (:func:`decide`); when given, the ``on``
    flags, the brain and the internet choice reflect it, so a re-drawn summary
    shows what the owner changed rather than the first proposal.
    """
    _propose_topic(sctx)
    items, notes = collect_items(sctx)
    items = _relevant(sctx, items, notes)
    state = state if isinstance(state, dict) else {}
    chosen = state.get("on") if isinstance(state.get("on"), dict) else {}
    reach = state.get("reach") if state.get("reach") in REACH_CHOICES \
        else default_reach(sctx.args)
    active = needs_active(sctx.shared)
    statements: list[tuple[dict, str]] = []
    if active:
        # Every line on the plan can be ticked or unticked. A line
        # that is only a statement becomes a note: under its job when it has
        # one, else under its section.
        items, statements = _info_to_notes(items)
    for item in items:
        if item["id"] in chosen and toggleable(item):
            item["on"] = bool(chosen[item["id"]])
        else:
            item["on"] = bool(item["default_on"])
    if reach == REACH_OFFLINE:
        for item in items:
            if item["section"] in ("outside_services", "live_info"):
                item["on"] = False
    forced_off = apply_requires(items, chosen)
    _set_off_lines(sctx, items)
    for item in items:
        if item["id"] in forced_off:
            item["off_line"] = off_line(item, sctx.mode, wording.say(
                "plan_box_off", sctx.mode))

    jobs = _job_groups(sctx, items, statements) if active else []
    grouped = {item["id"] for group in jobs for item in group["items"]}
    placed = {line for group in jobs for line in group["notes"]}
    said: dict[str, list[str]] = {}
    for item, text in statements:
        if not (item.get("need") and text in placed):
            said.setdefault(item["section"], []).append(text)
    sections = []
    for section in section_order():
        rows = [item for item in items if item["section"] == section
                and item["id"] not in grouped]
        notes_here = said.get(section, [])
        if not rows and not notes_here and section in OPTIONAL_SECTIONS:
            continue
        entry = {"id": section,
                 "heading": wording.say(f"section_{section}", sctx.mode),
                 "items": rows,
                 "empty": "" if notes_here else wording.say("section_empty",
                                                            sctx.mode)}
        if notes_here:
            entry["notes"] = notes_here
        sections.append(entry)

    on_items = [item for item in items if item["on"]]
    total = sum(item["size_bytes"] or 0 for item in on_items)
    unknown = any(item["size_bytes"] is None and item["needs_yes"]
                  and item["outside_host"] for item in on_items
                  if item["apply"]["kind"] in ("model_pull", "source_fetch"))
    hosts = {item["outside_host"] for item in on_items if item["outside_host"]}
    town = town_question(items, sctx, reach, typed=state.get("town"))
    if town["ask"]:
        # The town typed on the plan is looked up there, once: said here too,
        # so the footer names every place the build asks.
        hosts.add(town["host"])
    hosts = sorted(hosts)
    from puffpai import plan_outside
    outside = plan_outside.footer(shared=sctx.shared, items=on_items,
                                  reach=reach, name=sctx.name, mode=sctx.mode,
                                  hosts_text=host_names(hosts) if hosts else "")
    lines = {
        "total": (wording.say("total_download", sctx.mode,
                              size=cards.format_bytes(total)
                              + (" plus some of unknown size" if unknown
                                 else ""))
                  if total else wording.say("total_nothing", sctx.mode)),
        # The list that stays on
        # is the same one "What leaves your computer" shows after the build.
        "leaves": outside["line"],
        "reach": wording.say("reach_line", sctx.mode,
                             choice=wording.say(f"reach_{reach}", sctx.mode,
                                                name=sctx.name)),
    }
    return {
        "schema": SCHEMA,
        "name": sctx.name,
        "goal": sctx.goal,
        "mode": sctx.mode,
        "internet": sctx.internet,
        "title": wording.say("summary_title", sctx.mode, name=sctx.name),
        "intro": wording.say("summary_intro", sctx.mode).replace("\n", " "),
        # "What it will do", one group per ticked job, drawn before
        # the sections. Empty when the needs step did not run.
        "jobs_heading": (wording.say("plan_what_it_does", sctx.mode)
                         if jobs else ""),
        "jobs": jobs,
        # Feature lines that 2 or more fitting projects have and no
        # ticked job covers; ticking one adds it as a job (add_similar).
        "similar": similar_group(sctx) if active else
        {"heading": "", "rows": []},
        "sections": sections,
        "reach": {"current": reach,
                  "label": wording.say("reach_item", sctx.mode,
                                       name=sctx.name,
                                       choice=wording.say(f"reach_{reach}",
                                                          sctx.mode,
                                                          name=sctx.name)),
                  "choices": reach_choices(sctx.name, sctx.mode)},
        "model_choices": model_choices(sctx.previews.get("brain") or {}),
        "brain_off_label": wording.say("leave_out_brain", sctx.mode),
        "total_bytes": total,
        "hosts": hosts,
        "outside": {"stay_on": outside["stay_on"], "already": outside["already"],
                    "count": outside["count"]},
        "lines": lines,
        "actions": [{"value": "looks_good",
                     "label": wording.say("looks_good", sctx.mode)},
                    {"value": "change",
                     "label": wording.say("change_something", sctx.mode)}],
        "approved": bool(state.get("approved")),
        "town": town,
        "notes": notes[:20],
    }


# -- the plan grouped by the owner's jobs -------------------------------------
#
# When the needs step ran, what was found for each job comes from
# ``find_parts.plan_items``, run once when the owner pressed "Start
# looking" and remembered here, so drawing the plan again after a tick never
# searches again.

def _optional(modname: str):
    """Another part's module, or ``None`` when it is not in this build."""
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


def needs_of(shared: Any) -> dict | None:
    """The jobs the owner kept (``shared["needs"]``, needs/v1), or ``None``."""
    needs = shared.get("needs") if isinstance(shared, dict) else None
    if isinstance(needs, dict) and needs.get("schema") == "needs/v1" \
            and isinstance(needs.get("jobs"), list):
        return needs
    return None


def needs_active(shared: Any) -> bool:
    """Is the plan drawn from the owner's jobs? Only with ``find_parts`` here."""
    return needs_of(shared) is not None and \
        _optional("puffpai.wizard.find_parts") is not None


_PARTS_LOCK = threading.Lock()
_PARTS_MEMO: dict[str, dict] = {}
MAX_PARTS_MEMO = 32


def parts_key(needs: dict, *, online: bool, allow_send: bool) -> str:
    """What a search result is kept under: the jobs exactly, and whether it
    was allowed to go online."""
    blob = json.dumps(needs, sort_keys=True, default=str)
    return hashlib.sha256(f"{blob}|{int(bool(online))}|{int(bool(allow_send))}"
                          .encode("utf-8")).hexdigest()


def clean_parts(result: Any) -> dict:
    """``plan_items``'s answer with every field the plan reads in its shape."""
    result = result if isinstance(result, dict) else {}
    notes = result.get("notes") if isinstance(result.get("notes"), dict) else {}
    return {
        "items": [dict(i) for i in result.get("items") or () if isinstance(i, dict)],
        "notes": {str(job): [str(n) for n in lines if isinstance(n, str)]
                  for job, lines in notes.items() if isinstance(lines, list)},
        "not_reached": [str(j) for j in result.get("not_reached") or ()
                        if isinstance(j, str)],
        "similar": [dict(row) for row in result.get("similar") or ()
                    if isinstance(row, dict)],
        "sends": [dict(row) for row in result.get("sends") or ()
                  if isinstance(row, dict)],
        "left_out": int(result.get("left_out") or 0)
        if isinstance(result.get("left_out"), int) else 0,
    }


def remember_parts(needs: dict, result: Any, *, online: bool,
                   allow_send: bool) -> dict:
    """Keep what was found for these jobs, so the plan draws it without
    searching again. Returns the kept record (with its ``key``)."""
    record = clean_parts(result)
    record["key"] = parts_key(needs, online=online, allow_send=allow_send)
    with _PARTS_LOCK:
        _PARTS_MEMO[record["key"]] = record
        while len(_PARTS_MEMO) > MAX_PARTS_MEMO:
            _PARTS_MEMO.pop(next(iter(_PARTS_MEMO)))
    return record


def parts_for(shared: dict, *, goal: str, cfg: Any) -> dict:
    """What was found for the owner's jobs: from the run record, from memory,
    or (only if neither has it) by asking ``find_parts`` once. Looking online
    only happens when the owner chose "Start looking" (``needs_look``)."""
    needs = needs_of(shared)
    if needs is None:
        return clean_parts({})
    online = shared.get("internet") is True
    allow_send = shared.get("needs_look") == "online" and online
    key = parts_key(needs, online=online, allow_send=allow_send)
    stored = shared.get("needs_parts")
    if isinstance(stored, dict) and stored.get("key") == key:
        return dict(clean_parts(stored), key=key)
    with _PARTS_LOCK:
        hit = _PARTS_MEMO.get(key)
    if hit is not None:
        return hit
    engine = _optional("puffpai.wizard.find_parts")
    if engine is None:
        return clean_parts({})
    result = engine.plan_items(needs, goal=goal, online=online,
                               allow_send=allow_send, cfg=cfg)
    return remember_parts(needs, result, online=online, allow_send=allow_send)


def _found_parts(sctx: SummaryContext) -> list[dict]:
    """The parts provider: the lines ``find_parts`` found for the jobs."""
    if not needs_active(sctx.shared):
        return []
    parts = parts_for(sctx.shared, goal=sctx.goal, cfg=sctx.cfg)
    sctx.parts = parts
    return [dict(item) for item in parts.get("items") or ()]


def _info_to_notes(items: list[dict]) -> tuple[list[dict],
                                                list[tuple[dict, str]]]:
    """(the lines that can be ticked, [(statement line, its note)])."""
    kept: list[dict] = []
    said: list[tuple[dict, str]] = []
    for item in items:
        if toggleable(item):
            kept.append(item)
            continue
        label = str(item.get("label") or "")
        detail = str(item.get("detail") or "")
        text = label if not detail else (
            f"{label} {detail}" if label.endswith((".", "!", "?", ":"))
            else f"{label}. {detail}")
        if text:
            said.append((item, text))
    return kept, said


def section_order() -> list[str]:
    """SECTIONS in the order they are drawn: the locked box last."""
    return ([s for s in SECTIONS if s not in LAST_SECTIONS]
            + [s for s in SECTIONS if s in LAST_SECTIONS])


def apply_requires(rows: list[dict], chosen: Any) -> set[str]:
    """Hold "a line is off while what it needs is off".

    For each apply kind some line ``requires`` (the locked box,
    ``box_setup``): if the owner switched that line on or off, that answer
    stands; otherwise it is on exactly when a line needing it is on. When it
    is off, every line needing it is off too. Returns the ids switched off
    because of it. Changes *rows* in place."""
    chosen = chosen if isinstance(chosen, dict) else {}
    needed: list[str] = []
    for row in rows:
        for kind in row.get("requires") or ():
            if kind not in needed:
                needed.append(kind)
    forced: set[str] = set()
    for kind in needed:
        providers = [row for row in rows
                     if (row.get("apply") or {}).get("kind") == kind]
        if not providers:
            continue
        users = [row for row in rows if kind in (row.get("requires") or ())]
        said = [row for row in providers if row["id"] in chosen]
        if said:
            wanted = any(bool(chosen[row["id"]]) for row in said)
        else:
            wanted = any(row.get("on") for row in users)
        for row in providers:
            row["on"] = wanted
        if not wanted:
            for row in users:
                if row.get("on"):
                    row["on"] = False
                    forced.add(row["id"])
    return forced


def _job_groups(sctx: SummaryContext, items: list[dict],
                statements: list[tuple[dict, str]] = ()) -> list[dict]:
    """One group per ticked job: its lines, its notes (what was found and
    cannot be added, and the plan's own statements about the job), and
    whether it was reached before the search stopped."""
    needs = needs_of(sctx.shared) or {}
    parts = sctx.parts or {}
    notes = parts.get("notes") or {}
    not_reached = set(parts.get("not_reached") or ())
    groups: list[dict] = []
    for job in needs.get("jobs") or ():
        if not isinstance(job, dict) or not job.get("ticked") \
                or not isinstance(job.get("id"), str):
            continue
        name = cards.plain(job.get("name"), MAX_LABEL)
        if not name:
            continue
        rows = [item for item in items if item.get("need") == job["id"]]
        found = [cards.plain(n, MAX_DETAIL) for n in notes.get(job["id"]) or ()]
        found = [n for n in found if n]
        nothing = [n for n in found if n == wording.FOUND_NOTHING]
        others = [n for n in found if n != wording.FOUND_NOTHING]
        lines = ([wording.say("plan_also_found", sctx.mode)] + others
                 if others else []) + nothing
        lines += [text for item, text in statements
                  if item.get("need") == job["id"] and text not in lines]
        groups.append({
            "id": job["id"], "heading": name, "items": rows, "notes": lines,
            "not_reached": (" ".join(wording.say(
                "plan_not_reached", sctx.mode, job=name).split())
                if job["id"] in not_reached else "")})
    return groups


# -- "Similar projects also have" ---------------------------------------------
#
# After looking, a feature that 2 or more fitting projects
# have and that no ticked job covers is one unticked line. Ticking it adds it
# as a job (through the needs engine's ``choose``) and moves the finds that
# fit it under it, without searching again.

MAX_SIMILAR = 8
MIN_SEEN_IN = 2


def similar_rows(parts: Any) -> list[dict]:
    """The similar-project lines ``find_parts`` found, cleaned."""
    rows: list[dict] = []
    seen: set[str] = set()
    for row in (parts or {}).get("similar") or ():
        if not isinstance(row, dict):
            continue
        name = cards.plain(row.get("name"), 40)
        count = row.get("seen_in")
        if not name or name.lower() in seen or isinstance(count, bool) \
                or not isinstance(count, int) or count < MIN_SEEN_IN:
            continue
        seen.add(name.lower())
        rows.append({"name": name, "seen_in": count,
                     "items": [str(i) for i in row.get("items") or ()
                               if isinstance(i, str)][:40]})
    return rows[:MAX_SIMILAR]


def similar_group(sctx: SummaryContext) -> dict:
    """``{"heading", "rows": [{"name", "seen_in", "line"}]}`` for the plan."""
    rows = similar_rows(sctx.parts)
    return {"heading": (wording.say("plan_similar_heading", sctx.mode)
                        if rows else ""),
            "rows": [{"name": row["name"], "seen_in": row["seen_in"],
                      "line": wording.say("plan_similar_line", sctx.mode,
                                          name=row["name"], n=row["seen_in"])}
                     for row in rows]}


class SimilarRefused(ValueError):
    """A similar-project line that cannot be added, in plain words."""


def add_similar(shared: dict, name: str, *, engine: Any = None) -> str:
    """Add the similar-project line *name* as a ticked job; move the finds
    that fit it under it. Changes *shared* (``needs``, ``needs_draft``,
    ``needs_parts``) and remembers the moved finds under the new jobs, so
    drawing the plan never searches again. Returns the new job's id."""
    needs = needs_of(shared)
    parts = shared.get("needs_parts") if isinstance(shared, dict) else None
    wanted = " ".join(str(name or "").split()).lower()
    row = next((r for r in similar_rows(parts) if r["name"].lower() == wanted),
               None) if needs is not None and isinstance(parts, dict) else None
    if row is None:
        raise SimilarRefused(wording.say("plan_similar_unknown"))
    engine = engine if engine is not None else _optional("puffpai.wizard.needs")
    choose = getattr(engine, "choose", None) if engine is not None else None
    if choose is None:
        raise SimilarRefused(wording.say("plan_similar_later"))
    before = {job.get("id") for job in needs["jobs"] if isinstance(job, dict)}
    ticked = [job["id"] for job in needs["jobs"]
              if isinstance(job, dict) and job.get("ticked")]
    chosen = choose(needs, ticked=ticked, added=[row["name"]], allowed_words=[])
    if not (isinstance(chosen, dict) and chosen.get("schema") == "needs/v1"
            and isinstance(chosen.get("jobs"), list)):
        raise SimilarRefused(wording.say("plan_similar_later"))
    new = [job for job in chosen["jobs"] if isinstance(job, dict)
           and job.get("id") not in before and isinstance(job.get("id"), str)]
    if not new:
        raise SimilarRefused(wording.say("plan_similar_later"))
    job = new[0]
    job["ticked"] = True
    record = clean_parts(parts)
    moved = set(row["items"])
    emptied: set[str] = set()
    for item in record["items"]:
        if item.get("id") in moved:
            if item.get("need"):
                emptied.add(str(item["need"]))
            item["need"] = job["id"]
            item["why"] = f"helps with: {cards.plain(job.get('name'), 80)}"
    for old in emptied - {item.get("need") for item in record["items"]}:
        # A job whose finds all moved says where they went, never nothing.
        if not record["notes"].get(old):
            record["notes"][old] = [wording.say(
                "plan_moved_to", job=cards.plain(job.get("name"), 80))]
    record["similar"] = [r for r in record["similar"]
                         if " ".join(str(r.get("name") or "").split()).lower()
                         != wanted]
    online = shared.get("internet") is True
    allow_send = shared.get("needs_look") == "online" and online
    kept = remember_parts(chosen, record, online=online, allow_send=allow_send)
    shared["needs"] = chosen
    shared["needs_draft"] = chosen
    shared["needs_parts"] = dict(kept, lines=list(parts.get("lines") or ()))
    return job["id"]


# -- the home town ------------------------------------------------------------
#
# The owner's weather needs a town, and the wizard does not otherwise ask.
# When a line that needs the town is on (the weather forecast, a live card
# about the weather where you live), the plan asks for it once, on that line:
# the terminal right after "Looks good", the browser as a field on the line.
# It is stored the town-level way (puffpai.home_location: a rough position,
# never the street) through the town lookup on What leaves your computer, when
# the build runs. An empty answer is a skip; the weather card asks again.

#: The longest town anybody types ("Llanfairpwllgwyngyll, Wales" fits).
MAX_TOWN = 100


def _home_known() -> bool:
    try:
        from puffpai import home_location
        return home_location.is_set()
    except Exception:  # noqa: BLE001 - unreadable is "not known yet"
        return False


def clean_town(text: Any) -> str:
    """A typed town as plain text, or ``""``. Never raises."""
    if not isinstance(text, str):
        return ""
    return cards.plain(" ".join(text.split()), MAX_TOWN)


def town_question(items: list[dict], sctx: SummaryContext, reach: str, *,
                  typed: Any = None) -> dict:
    """``{"ask", "item", "question", "help", "placeholder", "value"}``.

    ``ask`` is true only when a switched-on line needs the town, the bot does
    not know its town yet, and it is allowed on the internet at all. ``item``
    is the first such line: the browser draws the field on it.
    """
    needing = [item for item in items if item.get("on")
               and item.get("needs_town")]
    ask = bool(needing) and reach != REACH_OFFLINE and not _home_known()
    try:
        from puffpai import home_location
        host = home_location.GEOCODE_URL.split("/")[2]
    except Exception:  # noqa: BLE001
        host = "geocoding-api.open-meteo.com"
    return {"ask": ask,
            "item": needing[0]["id"] if ask else "",
            "host": host,
            "question": wording.say("town_question", sctx.mode),
            "help": " ".join(wording.say("town_help", sctx.mode,
                                         host=host, name=sctx.name).split()),
            "skip": " ".join(wording.say("town_skip", sctx.mode).split()),
            "placeholder": "Springfield, Missouri",
            "value": clean_town(typed)}


def items_of(summary: dict) -> list[dict]:
    """Every line, in the order it is drawn: the job groups, then the
    sections."""
    return ([item for group in summary.get("jobs") or ()
             for item in group.get("items") or ()]
            + [item for section in summary.get("sections") or ()
               for item in section.get("items") or ()])


def numbered(summary: dict) -> list[dict]:
    """Every item "Change something" may open, in summary order."""
    return [item for item in items_of(summary) if toggleable(item)]


# -- assembling it from a run -------------------------------------------------

#: The cards the summary speaks for, proposed in schedule order so each sees
#: what the one before it would share (the posture's gates reach the brain).
#: In this order: the brain is previewed before the sources card, so the
#: brain the plan picks is the one asked for "worth a look" suggestions (in
#: the run itself sources comes first, and it fell back to whichever model
#: happened to be installed first -- a tiny one that suggested nothing).
PREVIEW_CARDS: tuple[str, ...] = ("secure:posture", "brain", "sources")


class _PreviewArgs:
    """The run's flags, read so a missing one is ``None``, never an error."""

    def __init__(self, args: Any) -> None:
        self._args = args

    def __getattr__(self, name: str) -> Any:
        return getattr(self._args, name, None)


#: The brain's "worth a look" answers, kept per (address, brain, goal) for the
#: life of this process, so redrawing the plan after "Change something" (or
#: each browser request for it) does not ask the brain again.
_SUGGEST_MEMO: dict[tuple[str, str, str], Any] = {}


def _memo_suggest_client(notebook: dict, cfg: Any):
    """The client the sources preview asks: this build's own brain -- the one
    the plan picked when it is installed -- asked once per goal. ``None``
    (nothing asked) when Discover found no local brain."""
    from puffpai.wizard import sources_suggest

    installed = [str(m) for m in (notebook.get("installed_models") or ())]
    url = str(notebook.get("ollama_url") or "")
    if not url or not installed:
        return None
    wanted = [str(notebook.get("chosen_model") or ""),
              str(getattr(getattr(cfg, "ollama", None), "model", "") or "")]
    model = next((m for m in wanted if m and m in installed), installed[0])

    def ask(goal: str) -> Any:
        key = (url, model, str(goal))
        if key not in _SUGGEST_MEMO:
            client = sources_suggest.default_client(url, model)
            _SUGGEST_MEMO[key] = client(goal) if client is not None else []
        return _SUGGEST_MEMO[key]

    return ask


def previews(*, args: Any, cfg: Any, shared: dict, sections: dict,
             draft: dict | None = None,
             only: tuple[str, ...] = PREVIEW_CARDS) -> tuple[dict, list[str]]:
    """``{card_id: proposal}`` for :data:`PREVIEW_CARDS`. Writes nothing.

    *draft* is a not-yet-approved decision (a brain picked under "Change
    something"), shown to the cards as if approved so the redrawn summary is
    the plan that will be built.
    """
    from puffpai.wizard import loader, runner as wizard_runner

    notebook = dict(shared or {})
    if isinstance(draft, dict):
        notebook["summary"] = {"approved": True, "items": [],
                               "model": draft.get("model"),
                               "reach": draft.get("reach")}
    packages, _refusals = loader.load_builtin_packages()
    wrapped = _PreviewArgs(args)
    out: dict = {}
    notes: list[str] = []
    steps = [step for step in wizard_runner.SCHEDULE
             if wizard_runner.card_id_of(step) in PREVIEW_CARDS
             and wizard_runner.card_id_of(step) in only]
    steps.sort(key=lambda step: PREVIEW_CARDS.index(
        wizard_runner.card_id_of(step)))
    for step in steps:
        card_id = wizard_runner.card_id_of(step)
        package = packages.get(step.skill)
        if package is None:
            continue
        if (card_id == "sources" and not getattr(args, "no_suggestions", False)
                and notebook.get("source_suggest_client") is None):
            notebook["source_suggest_client"] = _memo_suggest_client(
                notebook, cfg)
        ctx = wizard_runner.RunContext(
            args=wrapped, cfg=cfg, non_interactive=True, shared=notebook,
            sections=sections, package=package,
            part=wizard_runner.lifecycle_part(step.part) or "",
            show_once=None, remote=True)
        try:
            found = loader.load_module(package).propose(ctx) or {}
        except Exception as exc:  # noqa: BLE001 - a preview never sinks the plan
            notes.append(f"{card_id} could not say what it would do: "
                         f"{type(exc).__name__}")
            continue
        out[card_id] = found
        preview = found.get("shared_preview")
        if isinstance(preview, dict):
            notebook.update({k: v for k, v in preview.items()
                             if isinstance(k, str)})
    return out, notes


def summary_for(*, args: Any, cfg: Any, shared: dict, sections: dict,
                changes: dict | None = None) -> dict:
    """The summary for a run as it stands, with *changes* drawn in."""
    from puffpai import cli

    shared = shared or {}
    interview = shared.get("interview") or (sections.get("persona") or {}).get(
        "interview") or {}
    goal = str((interview or {}).get("goal") or "")
    name = str(shared.get("bot_name") or (sections.get("persona") or {}).get(
        "bot_name") or cli.DEFAULT_BOT_NAME)
    found, notes = previews(args=args, cfg=cfg, shared=shared,
                            sections=sections, draft=changes)
    sctx = SummaryContext(goal=goal, terms=goal_terms(goal), name=name,
                          mode=wording.mode_of(shared), shared=shared,
                          previews=found, args=args, cfg=cfg,
                          internet=shared.get("internet"))
    summary = build(sctx, state=changes)
    summary["notes"] = (notes + summary.get("notes", []))[:20]
    summary["checked"] = checked_lines(shared, sctx.mode)
    return summary


def checked_lines(shared: dict, mode: str) -> list[str]:
    """What the quiet check of this computer found, in plain sentences."""
    census = (shared or {}).get("census") or {}
    memory = (census.get("memory") or {}).get("total_gb")
    disk = (census.get("disk") or {}).get("free_gb")
    out: list[str] = []
    if memory:
        out.append(wording.say(
            "checked_memory", mode, memory=f"{memory:g} GB",
            disk=f"{disk:g} GB" if disk else "an unknown amount"))
    internet = (shared or {}).get("internet")
    key = ("checked_online" if internet is True else
           "checked_offline" if internet is False else "checked_unknown")
    out.append(wording.say(key, mode))
    return [" ".join(line.split()) for line in out]


# -- decisions ----------------------------------------------------------------

def stated_by_flag(item: dict, args: Any) -> bool:
    """Did the command line say yes to this item? The only yes ``--yes`` has."""
    kind = item.get("apply", {}).get("kind")
    ref = item.get("apply", {}).get("ref")
    if kind == "model_pull":
        return bool(getattr(args, "download_model", False))
    if kind in ("source_fetch", "knowledge.add"):
        return bool(ref in (getattr(args, "fetch_source", None) or ())
                    and getattr(args, "accept_source_size", False))
    if kind == "door":
        return ref in (getattr(args, "egress_door", None) or ())
    if kind == "home_assistant":
        return bool(getattr(args, "ha_url", None)
                    and getattr(args, "ha_token", None))
    if kind in ("model", INFO_KIND):
        return True
    return False


def decide(summary: dict, *, changes: dict | None = None,
           non_interactive: bool = False, args: Any = None) -> dict:
    """The stored decision for an approved summary. Validates every change.

    *changes* is ``{"on": {item_id: bool}, "model": str, "reach": str}``, each
    part optional, checked against *summary* -- an id the summary did not
    offer, a model it did not list or a reach it does not know is refused.

    ``non_interactive`` is ``--yes``: nobody read the summary, so an item that
    downloads or reaches out is on only if the command line named it.
    """
    changes = changes if isinstance(changes, dict) else {}
    known = {item["id"]: item for item in items_of(summary)}
    on: dict[str, bool] = {}
    for item_id, item in known.items():
        value = bool(item.get("on", item.get("default_on")))
        if non_interactive and item.get("needs_yes"):
            value = stated_by_flag(item, args)
        on[item_id] = value
    for item_id, value in (changes.get("on") or {}).items():
        item = known.get(item_id)
        if item is None or not toggleable(item):
            raise ProposalError(f"The plan has no item {item_id!r} to change.")
        if not isinstance(value, bool):
            raise ProposalError(f"{item_id}: on or off is true or false.")
        on[item_id] = value

    model = changes.get("model")
    if model is not None:
        offered = {choice["value"] for choice in summary.get("model_choices")
                   or ()}
        if model not in offered:
            raise ProposalError(f"{model!r} is not one of the brains offered.")
    reach = changes.get("reach", (summary.get("reach") or {}).get("current"))
    if reach not in REACH_CHOICES:
        raise ProposalError(f"{reach!r} is not one of the internet choices.")

    items = []
    for item_id, item in known.items():
        row = {"id": item_id, "section": item["section"],
               "label": item.get("label", ""),
               "apply": dict(item["apply"]),
               "outside_host": item["outside_host"],
               "needs_yes": item["needs_yes"],
               "size_bytes": item["size_bytes"],
               "on": on[item_id]}
        if item.get("needs_town"):
            row["needs_town"] = True
        for key in EXTRA_KEYS:
            if key in item:
                row[key] = item[key]
        if (model is not None and item["section"] == "thinking"
                and item["apply"]["kind"] in ("model", "model_pull")):
            # A different brain was picked: this line is now about that one,
            # and it downloads exactly when that one is not here yet.
            choice = next(c for c in summary["model_choices"]
                          if c["value"] == model)
            pulls = choice.get("size_bytes") != 0
            row["apply"] = {"kind": "model_pull" if pulls else "model",
                            "ref": model}
            row["size_bytes"] = choice.get("size_bytes") if pulls else None
            row["outside_host"] = MODEL_REGISTRY_HOST if pulls else None
            row["needs_yes"] = pulls
            if item_id not in (changes.get("on") or {}):
                row["on"] = True
            on[item_id] = row["on"]
        items.append(row)
    # What the locked box needs is decided the same way the plan drew it: a
    # line cannot be on while the thing it needs is off.
    apply_requires(items, changes.get("on") or {})
    on = {row["id"]: row["on"] for row in items}
    town = changes.get("town")
    if town is not None and not isinstance(town, str):
        raise ProposalError("The town is a short piece of text.")
    # The town is kept only when the plan asked for it: nobody is asked twice,
    # and nothing is looked up for a plan that does not need it.
    asked = bool((summary.get("town") or {}).get("ask")) and not non_interactive
    asked = asked and any(row.get("on") and row.get("needs_town")
                          for row in items) and reach != REACH_OFFLINE
    return {"schema": SCHEMA, "approved": True, "on": on,
            "model": model, "reach": reach, "items": items,
            "town_asked": asked, "town": clean_town(town) if asked else "",
            "non_interactive": bool(non_interactive)}


def approved_refs(state: Any, kind: str) -> list[str]:
    """Refs of the approved items of one apply kind. Empty unless approved."""
    if not isinstance(state, dict) or not state.get("approved"):
        return []
    out = []
    for item in state.get("items") or ():
        if (isinstance(item, dict) and item.get("on")
                and (item.get("apply") or {}).get("kind") == kind):
            ref = str((item.get("apply") or {}).get("ref") or "")
            if ref and ref not in out:
                out.append(ref)
    return out


def approved_items(state: Any) -> list[dict]:
    """Every item the owner said yes to. The seam other modules' appliers read."""
    if not isinstance(state, dict) or not state.get("approved"):
        return []
    return [dict(item) for item in state.get("items") or ()
            if isinstance(item, dict) and item.get("on")]


def settle_interview(interview: Any, state: Any) -> dict:
    """The goal's matched packs, cut down to what the owner approved.

    The goal question matches packs (``interview["selected"]``) before anyone
    has seen a plan. Once a person approves the summary, that plan is the
    choice: a pack stays chosen only if at least one of its downloads is
    switched on there. Without this, leaving a pack out under "Change
    something" still had the closing screen say it "was chosen and never
    downloaded" and hand over the command to fetch it.

    A ``--yes`` run (nobody read the plan) and a run with no approved plan
    keep the goal's matches as they were. What the goal matched is kept
    under ``matched`` so the record still says where the choice started.
    """
    out = dict(interview) if isinstance(interview, dict) else {}
    if (not isinstance(state, dict) or not state.get("approved")
            or state.get("non_interactive")):
        return out
    from puffpai.knowledge import build_sources

    selected = [str(i) for i in (out.get("selected") or [])]
    wanted = set(approved_refs(state, "source_fetch"))
    kept = [i for i in selected
            if not i.startswith("pack:")
            or wanted & set(build_sources.pack_source_ids(i))]
    if kept != selected:
        out.setdefault("matched", selected)
    out["selected"] = kept
    return out


def runtime_reach(state: Any) -> bool:
    """Does anything the owner approved reach outside while the bot runs?"""
    for item in approved_items(state):
        if item.get("section") in ("outside_services", "live_info") and (
                item.get("outside_host")
                or (item.get("apply") or {}).get("kind") == "door"):
            return True
    return False


# -- decisions into card answers ----------------------------------------------

def _fit(card: dict, wanted: dict) -> dict:
    """Keep only answers this card can take. The card is the authority."""
    fields = cards.field_map(card)
    out: dict = {}
    for field_id, value in wanted.items():
        field = fields.get(field_id)
        if field is None or field["kind"] in cards.READ_ONLY_KINDS:
            continue
        if field["kind"] in ("choice", "multi_choice"):
            values = {opt["value"] for opt in field.get("options") or ()}
            if field["kind"] == "multi_choice":
                value = [v for v in value if v in values]
            elif value not in values and not field.get("free_text"):
                continue
        out[field_id] = value
    return out


def run_egress_for(state: Any, args: Any) -> str | None:
    """The runtime tier the decision implies, unless a flag already said."""
    if getattr(args, "run_egress", None):
        return None
    reach = (state or {}).get("reach")
    if reach == REACH_OFFLINE:
        return "island"
    return "enumerated" if runtime_reach(state) else "island"


def bridge_for(state: Any, args: Any) -> str | None:
    if getattr(args, "bridge_posture", None):
        return None
    reach = (state or {}).get("reach")
    # An older record may still say "ask each time" (REACH_ASK). That choice
    # was never built and is folded into "fenced" (zone_bridge).
    if reach in (REACH_ASK, REACH_OFFLINE, REACH_ONLY_LISTED):
        return "fenced"
    return None


def answers_for_card(card: dict, state: Any, args: Any = None, *,
                     internet: bool | None = None) -> dict:
    """What a card the owner does not see answers, from the approved summary.

    Only fields the card actually has, only values it offers. Anything the
    summary did not decide is left to the card's own proposal.
    """
    state = state if isinstance(state, dict) and state.get("approved") else {}
    skill = card.get("skill")
    part = card.get("part") or ""
    wanted: dict[str, Any] = {}
    if skill == "brain" and state:
        if state.get("model"):
            wanted["model"] = state["model"]
        wanted["download_now"] = brain_on(state)
    elif skill == "secure" and part == "posture":
        if internet is not None and not getattr(args, "build_network", None):
            wanted["build_network"] = "online" if internet else "offline"
        # A --yes run approved nothing on anybody's behalf: the tier stays
        # what the flags said, or the recorded default.
        if state and not state.get("non_interactive"):
            egress = run_egress_for(state, args)
            if egress:
                wanted["run_egress"] = egress
            if (egress or getattr(args, "run_egress", None)) == "enumerated" \
                    and not getattr(args, "egress_door", None):
                wanted["doors"] = approved_refs(state, "door")
    elif skill == "secure" and part == "bridge" and state             and not state.get("non_interactive"):
        bridge = bridge_for(state, args)
        if bridge:
            wanted["bridge_posture"] = bridge
    # A --yes run leaves the knowledge and hub cards to their own flags
    # (--fetch-source with --accept-source-size, --ha-url with --ha-token), so
    # what a script names is recorded, and refused, exactly as before.
    elif skill == "sources" and state and not state.get("non_interactive"):
        wanted["selected"] = approved_refs(state, "source_fetch")
    elif skill == "integrate" and state and not state.get("non_interactive"):
        wanted["fetch_now"] = approved_refs(state, "source_fetch")
    elif skill == "tools" and state and not state.get("non_interactive"):
        if approved_refs(state, "home_assistant"):
            wanted["connect_hub"] = True
    return _fit(card, wanted)


def brain_on(state: Any) -> bool:
    """Did the owner keep the brain on the plan (download it if it is needed)?"""
    for item in approved_items(state):
        if (item.get("apply") or {}).get("kind") == "model_pull":
            return True
    return False


def must_ask(card: dict, state: Any) -> bool:
    """A card the owner does not normally see, that has to be asked this time.

    Today exactly one: the Home Assistant hub, once it is on the plan -- its
    address and key are things only the owner can type.
    """
    if card.get("skill") != "tools":
        return False
    if not approved_refs(state, "home_assistant"):
        return False
    return any(field["kind"] in ("secret_entry", "text")
               for field in card.get("fields") or ())


__all__ = ["EXTRA_KEYS", "LAST_SECTIONS", "R3_APPLY_KINDS", "SKIPPED_WITH_NEEDS",
           "skipped_with_needs", "detail_when_off", "add_similar",
           "similar_group", "similar_rows", "SimilarRefused",
           "apply_requires", "clean_parts", "needs_active", "needs_of",
           "parts_for", "parts_key", "remember_parts", "section_order",
           "BRAIN_ITEM_ID", "EXTERNAL_PROVIDER_MODULES", "INFO_KIND", "MODEL_REGISTRY_HOST",
           "ProposalError", "REACH_ASK", "REACH_CHOICES", "REACH_ITEM_ID",
           "OPTIONAL_SECTIONS", "REACH_OFFLINE", "REACH_ONLY_LISTED", "SCHEMA",
           "SECTIONS",
           "SummaryContext", "answers_for_card", "approved_items",
           "approved_refs", "build", "collect_items", "decide",
           "default_reach", "goal_terms", "items_of", "model_choices",
           "must_ask", "numbered", "reach_choices", "register_provider",
           "run_egress_for", "runtime_reach", "stated_by_flag",
           "toggleable", "unregister_provider", "validate_item"]
