"""Every sentence finding shows her, in one place. Plain words only.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
from typing import Any

#: The types every find is labelled with (in her words). A book
#: find also says which kind of book it is (book, guide, Q&A collection,
#: textbook) in its own ``type_words``.
TYPES: dict[str, str] = {
    "skill": "skill",
    "book": "books and guides",
    "browser_app": "browser app",
    "server_app": "app that needs its own server",
    "cli": "command-line tool",
    "library": "code library",
    "mcp": "MCP tool server",
    "data": "data/template",
}
TYPE_ORDER = ("skill", "book", "mcp", "browser_app", "data", "server_app", "cli",
              "library")

#: Type words she (or the model) may use for ``kinds``, mapped to the keys.
KIND_ALIASES: dict[str, str] = {
    **{v: k for k, v in TYPES.items()}, **{k: k for k in TYPES},
    "skills": "skill", "app": "browser_app", "apps": "browser_app",
    "web app": "browser_app", "server": "server_app", "self-hosted": "server_app",
    "cli tool": "cli", "command line tool": "cli", "tool": "cli",
    "library": "library", "code": "library", "mcp": "mcp", "mcp server": "mcp",
    "tool server": "mcp", "data": "data", "template": "data", "templates": "data",
    "books": "book", "guide": "book", "guides": "book", "textbook": "book",
    "textbooks": "book", "q&a collection": "book", "q&a": "book",
    "books and guides": "book", "reading": "book",
}

SITE_NAMES: dict[str, str] = {
    "clawhub": "ClawHub",
    "skills.sh": "skills.sh",
    "hermes": "the Hermes skill list",
    "github": "GitHub",
    "codeberg": "Codeberg",
    "npm": "npm",
    "pypi": "PyPI",
    "awesome-selfhosted": "the awesome-selfhosted list",
    "mcp-registry": "the MCP Registry",
    "libraries": "the free libraries",
}

CAN = {
    "install": "BuildBot can install it as a skill your bot reads when it fits.",
    "add_mcp": "BuildBot can add it to your bot's tools.",
    "integrate": "BuildBot can try to add it as an app on this computer.",
    "add_book": "BuildBot can download it, and your bot reads it to answer questions.",
    "look": "Look at it yourself",
}

#: Why a find is "look at it yourself", each with what to do instead. Code
#: projects (apps, programs, libraries, data) are never "look": Toolsmith
#: runs them or makes an app from them after her Yes.
LOOK_WHY = {
    "skill": "it holds skills: look for the one you want on the skill shelves.",
    "mcp": "it is a tool server: add it with Add a tool server on the tools page.",
    "shelf": "BuildBot cannot download skills from this shelf yet.",
    "mcp_secret": "it needs a key or settings only you can type. Add it on the tools page.",
    "mcp_start": "it does not say how to start it. Add it with Add a tool server on the "
                 "tools page.",
    "unknown": "BuildBot does not know how to add this kind yet. Give its GitHub address "
               "in Find more on a job.",
}
#: A code project with no licence: never a refusal, her call.
NO_LICENCE_LINE = ("Its maker has not said others may copy it. Using it on your own computer "
                   "is your call.")

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")

TRADE_NOTE = "This one can place trades with real money."
#: What places trades: order words, trading bots and agents, and the brokers
#: and exchanges with trading programming interfaces. Reading prices, tracking
#: other people's trades or analysing a portfolio is not trading.
TRADE_RE = re.compile(
    r"(?i)(\b(execut|plac|submit|send)\w*\s+(\w+\s+){0,2}(trades?|orders?)\b"
    r"|\b(trade|order) execution\b|\bauto[- ]?trad\w*"
    r"|\b(algo(rithmic)?|day|options|crypto|forex|swing)[- ]trad\w*"
    r"|\btrading[- ](bots?|agents?|engines?|systems?|signals?|strateg\w*)\b"
    r"|\b(robinhood|alpaca|binance|coinbase|kraken|bybit|tradier|metatrader|ccxt|"
    r"interactive brokers|ibkr)\b"
    r"|\bbuy and sell (orders|stocks|shares|crypto)\b)")
#: Words that say it does not trade.
READ_ONLY_RE = re.compile(
    r"(?i)\b(read[- ]only|no brokerage|without a brokerage|never (places|executes) "
    r"(trades|orders)|does not (place|execute) (trades|orders))\b")
_TRADER_NAME_RE = re.compile(r"(?i)\btrad(ing|er|ers)\b")
#: Her words that ask for trading things, so they are shown ("orders" alone
#: is not enough: "track our filament orders" is not about trading).
ASKS_TRADING_RE = re.compile(r"(?i)\b(trad(e|es|ing|er|ers)|broker\w*)\b")
TRADE_NOT_ASKED = ("{name} can place trades with real money, so BuildBot does not install it "
                   "unless you ask for trading yourself. Nothing was changed.")
TRADE_LEFT_OUT = ("{n} that can place trades with real money {verb} left out. Ask for "
                  "trading by name to see them.")


def can_trade(name: str, what: str) -> bool:
    """Can this place trades with real money (from its own name and words)?"""
    text = f"{name} {what}"
    if READ_ONLY_RE.search(text):
        return False
    return bool(TRADE_RE.search(text)) or bool(
        _TRADER_NAME_RE.search(re.sub(r"[-_./@]+", " ", str(name or ""))))

FINDING_TITLE = "Finding things for your bot"
FINDING_INTRO = ("BuildBot is looking on the skill shelves, the tool shelf, the code sites and "
                 "the free libraries for things other people already made. At most two "
                 "small free books that clearly fit start ticked; nothing else is. Tap what "
                 "you want, then press Add what I picked.")
NOTHING_TICKED = "Nothing was picked, so nothing was added."
NOTHING_FOUND = "Nothing turned up for this."
SITE_QUIET = "{site} did not answer, so nothing from there is shown."
SITE_OFF = ("{site} is switched off under What leaves your computer, so it was not asked. "
            "The owner can switch it on there.")
LOOKING_LINE = "Looking for {words} on {sites}."
JOBS_LINE = "Your bot's jobs: {jobs}."
NO_BRAIN_LINE = "Your bot's brain did not answer, so BuildBot looked for your own words."
DONE_LOOKING = "Done looking."
LINK_UNKNOWN = ("That link is not a project on GitHub, Codeberg, npm, PyPI, ClawHub or "
                "skills.sh, so there is nothing to look up. A tool server's address or a "
                "docs page goes in Add my own tool on the Developer Brain page.")
LINK_CLOSE = "Nothing is at that exact address, so here are the closest ones."

ASK_LINE = "Shall BuildBot install it?"
INSTALLED = "{name} is installed. Your bot can use it on your next message."
ADDED_TOOLS = "{name} is added to your bot's tools."
ALREADY = "{name} is already in your bot, so it was not added again."
REFUSED_MALICIOUS = ("{name} was not installed: {shelf} marks it as harmful (malicious). "
                     "Nothing was changed.")
LOOK_YOURSELF = "{name}: look at it yourself at {url}, because {why}"
NOT_FOUND = "BuildBot no longer has that find. Look for it again first. Nothing was changed."
DOWNLOAD_FAILED = "{name} could not be downloaded, so nothing was changed."
SWITCHED_OFF = ("{name} was not installed: {site} is switched off under What leaves your "
                "computer. Nothing was changed.")
UNDONE = "{name} is removed."
UNDO_FAILED = "{name} could not be removed: {why}"


def type_words(kind: str) -> str:
    return TYPES.get(kind, "thing")


def number_words(n: Any) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + " million"
    return f"{n:,}"


def popularity_words(pop: dict) -> str:
    for key, word in (("stars", "stars"), ("downloads", "downloads"),
                      ("installs", "installs"), ("monthly_downloads", "downloads a month")):
        if isinstance(pop.get(key), int):
            n = pop[key]
            if key == "stars" and n == 1:
                return "1 star"
            return f"{number_words(n)} {word}"
    return ""


def month_words(iso_or_ms: Any) -> str:
    """"changed May 2026" from an ISO date or epoch milliseconds, else ""."""
    text = ""
    if isinstance(iso_or_ms, (int, float)) and iso_or_ms > 10_000_000_000:
        import datetime
        try:
            d = datetime.datetime.fromtimestamp(iso_or_ms / 1000, datetime.timezone.utc)
            text = d.strftime("%Y-%m")
        except (OverflowError, OSError, ValueError):
            text = ""
    else:
        m = re.match(r"^(\d{4})-(\d{2})", str(iso_or_ms or ""))
        text = f"{m.group(1)}-{m.group(2)}" if m else ""
    m = re.match(r"^(\d{4})-(\d{2})$", text)
    if not m or not 1 <= int(m.group(2)) <= 12:
        return ""
    return f"changed {MONTHS[int(m.group(2)) - 1]} {m.group(1)}"


_FREE = {"mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "isc", "zlib", "0bsd",
         "bsl-1.0", "python-2.0", "x11"}
_FOR_ANYTHING = {"mit-0", "unlicense", "cc0-1.0", "wtfpl"}
_SHARE = {"gpl-2.0", "gpl-3.0", "agpl-3.0", "lgpl-2.1", "lgpl-3.0", "mpl-2.0", "epl-2.0",
          "gpl-2.0-only", "gpl-3.0-only", "gpl-2.0-or-later", "gpl-3.0-or-later",
          "agpl-3.0-only", "agpl-3.0-or-later", "lgpl-3.0-only", "eupl-1.2"}


def licence_words(spdx: Any) -> str:
    """Plain words for a licence id ("MIT: free to use and change")."""
    raw = str(spdx or "").strip()
    low = raw.lower()
    if not raw or low in ("noassertion", "none", "null"):
        return "no licence shown"
    if low == "other":
        return "a licence of its own, read it first"
    if low in _FOR_ANYTHING:
        return f"{raw}: free for anything"
    if low in _FREE:
        return f"{raw}: free to use and change"
    if low in _SHARE:
        return f"{raw}: free to use, changes must stay open"
    if low.startswith("cc-by"):
        return f"{raw}: free to use with credit"
    return f"{raw[:40]}: read the licence first"


def verdict_words(shelf: str, verdict: str) -> str:
    v = str(verdict or "").lower()
    if not v:
        return ""
    if v in ("clean", "pass", "passed", "benign", "safe"):
        return f"{shelf} check: passed"
    if v in ("suspicious", "warn", "review", "fail", "caution"):
        return f"{shelf} check: suspicious, read what it does first"
    if v == "malicious":
        return f"{shelf} check: harmful (malicious), BuildBot will not install it"
    if v == "builtin":
        return f"made by the {shelf} team"
    if v == "trusted":
        return f"{shelf}: from a trusted collection"
    return f"{shelf} check: {v[:30]}"


def can_words(can: str, kind: str, why: str = "") -> str:
    if can == "integrate" and kind == "data":
        return "BuildBot can try to add its lists and files to your bot." + (
            f" {why}" if why else "")
    if can == "integrate":
        return ("BuildBot can try to add it as an app that opens in your browser."
                + (f" {why}" if why else ""))
    if can == "look":
        return f"{CAN['look']}: {why or LOOK_WHY.get(kind, LOOK_WHY['unknown'])}"
    return CAN.get(can, CAN["look"])


def line_for(f: dict) -> str:
    """One plain line for a find: name, type, who, numbers, date, licence, check, can."""
    parts = [f"{f['name']} ({f.get('type_words') or type_words(f['type'])})"]
    if f.get("by"):
        parts.append(f"by {f['by']}")
    parts.append(f"on {SITE_NAMES.get(f['site'], f['site'])}")
    head = " ".join(parts)
    facts = [x for x in (f.get("popularity_words"), f.get("changed_words"),
                         f.get("licence_words"), f.get("size_words"),
                         f.get("verdict_words")) if x]
    text = head + (". " + ", ".join(facts) if facts else "")
    if f.get("what"):
        text += ". " + f["what"].rstrip(".")
    text += ". " + f.get("can_words", "")
    if f.get("can_trade"):
        text += " " + TRADE_NOTE
    return " ".join(text.split())


__all__ = ["CAN", "FINDING_INTRO", "FINDING_TITLE", "KIND_ALIASES", "NO_LICENCE_LINE", "SITE_NAMES",
           "TRADE_RE", "TYPES", "TYPE_ORDER", "can_words", "licence_words", "line_for",
           "month_words", "number_words", "popularity_words", "type_words", "verdict_words"]
