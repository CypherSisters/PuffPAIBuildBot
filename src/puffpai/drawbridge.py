"""Drawbridge: one policy for every addition, before the owner's yes counts.

Everything BuildBot adds after a look outside -- a live card made from a
service, a book downloaded whole -- is checked here, the same way, from every
path (the wizard's build, chat, the dashboard, the terminal).
:func:`check_addition` runs :mod:`puffpai.artifact_review`'s checklist and
turns its rows into ONE verdict:

``block``
    Never installed, whatever the owner says. Any of:

    * something downloaded whole whose license is not known;
    * an address that is not an ordinary encrypted connection on port 443, or
      that does not belong to the service the addition says it comes from;
    * a code-shaped field anywhere in it (``handler``, ``script``, ...), or a
      record that does not pass its own format check;
    * words that read like orders to a model or a browser in any field the
      owner reads;
    * a sample reply that was too big or was not data.

``warn``
    Any other check that failed or could not be checked. The owner may still
    say yes; the rows say what to know first.

``allow``
    Every check passed.

``hold`` (kinds registered with :func:`register_checker`)
    A check found something that needs its own yes (a licence file that
    differs from the plan, a size far over the plan ...). Nothing of it runs
    until the owner says yes to exactly what was found.

A checker registry: other parts register one checklist per kind
(:func:`register_checker`), before :func:`close_registration` is called at
the end of :func:`puffpai.factory_wiring.connect`. After that nobody can add
or replace a checker. A kind is registered once; a checker that raises is a
``block``; the always-block codes apply to every kind; a failed row whose code
the checker did not name is a ``hold`` (never a pass); a checklist with no
check rows (empty, or notes only) is a ``block``; unknown kinds still
block. Every verdict of a registered kind records the checker's version and a
fingerprint of its rows (``rows_sha``).

Every verdict is appended to ``<state>/addition_checks.jsonl`` with a
fingerprint of exactly what was checked (``draft_sha``), so an install can
tell whether the thing changed after it was checked. Installing re-checks it
anyway: a stored verdict is never trusted.

What this protects against, and what it does not: it stops the listed shapes
of a bad addition from being installed by any caller that goes through
:func:`puffpai.feed_generator.install_draft` or calls this first. It does not
judge whether a service is honest or its data is true, and it cannot see
instructions written in a way its patterns do not know (another language,
reworded). It reads only the record; it never opens a connection.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from puffpai import fsutil
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

ALLOW, WARN, HOLD, BLOCK = "allow", "warn", "hold", "block"
VERDICTS = (ALLOW, WARN, HOLD, BLOCK)

LOG_FILENAME = "addition_checks.jsonl"
#: The log is a record of recent checks, not an archive. Past this size the
#: oldest lines go.
_MAX_LOG_BYTES = 1024 * 1024
_KEEP_LINES = 2000
_LOCK = threading.Lock()

#: Kinds that are downloaded whole and kept (so their license matters before
#: anything arrives).
DOWNLOAD_KINDS = frozenset({"book", "knowledge", "download", "pack"})
LIVE_KINDS = frozenset({"live_card", "feed", "live_feed"})

#: Row codes that stop an addition whatever else is true.
_ALWAYS_BLOCK = frozenset({
    "not_https_443", "host_mismatch", "forbidden_key", "invalid",
    "instruction_text", "too_big", "wrong_type",
})
_DOWNLOAD_BLOCK = frozenset({"license_unknown"})

#: Keys that change without the addition changing: its review, its place in
#: the owner's queue, and the record of an install.
_VOLATILE = frozenset({"review", "status", "installed_at", "drawbridge",
                       "checks", "created_at"})

#: Words shaped like an order to a model or a browser. Narrower than
#: ``artifact_review.reads_like_orders`` on purpose: a web address in a
#: description is data, while "ignore previous instructions" never is.
_ORDER_RE = re.compile(
    r"ignore (?:all |the |any )?(?:previous|prior|above)|disregard (?:all|the|"
    r"any|previous|prior|your)|system prompt|you are (?:now )?(?:a|an|the)\b|"
    r"from now on,? you|new instructions?|as an ai|^\s*(?:system|assistant|"
    r"user)\s*:|<\s*script|javascript:|data:text|on(?:load|error)\s*=|"
    r"\bsudo\b|\brm -rf\b", re.IGNORECASE)


#: Checker registry (see the module doc).
_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")
#: Codes may carry capitals: the browser-app recipe rules are named R1-R12.
_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}\Z")
_CHECKERS: dict[str, dict[str, Any]] = {}
_REGISTRY = {"closed": False}
_REG_LOCK = threading.Lock()


class RegistrationClosed(RuntimeError):
    """A checker was registered after the wiring closed the registry."""


class Blocked(Exception):
    """An addition Drawbridge stopped. ``message`` is plain words."""

    def __init__(self, message: str, verdict: dict[str, Any]):
        super().__init__(message)
        self.message = message
        self.verdict = verdict


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _state_dir() -> str:
    from puffpai.config import get_state_dir
    return get_state_dir()


def log_path() -> str:
    return os.path.join(_state_dir(), LOG_FILENAME)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def draft_sha(draft: Any) -> str:
    """A fingerprint of exactly what is checked, minus bookkeeping keys."""
    body = ({k: v for k, v in draft.items() if k not in _VOLATILE}
            if isinstance(draft, dict) else draft)
    try:
        blob = json.dumps(body, sort_keys=True, ensure_ascii=True, default=str)
    except (TypeError, ValueError):
        blob = repr(body)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def instruction_shaped(text: Any) -> bool:
    """True when *text* reads like an order to a model or a browser.

    Read after folding (zero-width, full-width and look-alike letters), and
    with ``artifact_review``'s words aimed at a model as well."""
    if not isinstance(text, str):
        return False
    from puffpai import artifact_review
    folded = artifact_review.fold_for_orders(text)
    return bool(_ORDER_RE.search(folded)) or artifact_review.model_orders(folded)


# ---------------------------------------------------------------------------
# an outside project's own text (README, docs pages, its files)
# ---------------------------------------------------------------------------
#
# A project's README is read by Toolsmith's model. Install lines (git clone,
# ./configure, make, sudo make install, pip install, curl ... | sh) are normal
# there and never count. Lines aimed at an AI are hidden from the model, one
# by one, and the project goes on. A README is only stopped when it is clearly
# hostile: it tells a model to drop its instructions or take a new role, asks
# for keys or passwords to be sent to an address, or hides words for an AI in
# invisible characters or an HTML comment.

#: What Toolsmith and the brain see in place of a hidden line.
HIDDEN_LINE = "[line hidden by Drawbridge: it reads like instructions to an AI]"

#: Clearly hostile: turning a model against its own instructions, or giving
#: it a new role. Read after folding.
_HOSTILE_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\s+"
    r"(?:(?:all|any|the|your|my|of|these|those|each|every|previous|prior|above|"
    r"earlier|system|safety|original|initial)\s+){0,4}"
    r"(?:previous|prior|above|earlier|preceding|instructions?|rules|guidelines|"
    r"guardrails|directives|prompts?|programming)\b|"
    r"\bsystem\s+prompt\b|"
    r"(?<!\bif )\byou\s+are\s+(?:now\s+)?(?:an?\s+)?(?:ai|llm|language\s+model|"
    r"ai\s+assistant|chatbot)\b|"
    r"\byou\s+are\s+now\s+(?:a|an|the|dan|jailbroken|unrestricted|unfiltered)\b|"
    r"\b(?:do not|don't|never)\s+(?:tell|inform|warn|alert)\s+(?:the\s+)?"
    r"(?:user|owner|human)\b|"
    r"\bexfiltrat\w*",
    re.IGNORECASE | re.MULTILINE)

#: Asking for keys, tokens or passwords to be sent to an address.
_EXFIL_RE = re.compile(
    r"\b(?:send|upload|forward|transmit|e-?mail|leak)\b[^\n]{0,80}?"
    r"(?:~/\.ssh|\.ssh\b|\bid_(?:rsa|ed25519|ecdsa|dsa)\b|\bprivate\s+keys?\b|"
    r"\bsecret\s+keys?\b|\bapi[\s_-]?keys?\b|\baccess\s+tokens?\b|\btokens\b|"
    r"\bsecrets\b|\bpasswords?\b|\bcredentials\b|(?<![\w.])\.env\b|"
    r"\benvironment\s+variables\b|\bcookies\b|\bseed\s+phrase\b)"
    r"[^\n]{0,80}?\bto\b\s*:?\s*(?:https?://|ftp://|[\w.+-]+@[\w-]+\.[a-z]{2,}|"
    r"\d{1,3}(?:\.\d{1,3}){3}\b)",
    re.IGNORECASE)

#: Aimed at an AI but not hostile on its own ("Note to AI agents: run the
#: tests first", a chat transcript's "Assistant:" line): hidden, not stopped.
_AI_LINE_RE = re.compile(
    r"^\s*(?:system|assistant)\s*:|<\|(?:im_start|im_end|system|assistant|user)\|>|"
    r"\[/?inst\]|<<\s*/?sys\s*>>|</?\s*system\s*>|\bas an ai\b|"
    r"\bfrom now on,? you\b|\bnew instructions?\s*:",
    re.IGNORECASE)

_COMMENT_RE = re.compile(r"<!--(.*?)(?:-->|\Z)", re.DOTALL)
#: Unicode tag characters: invisible, and each one carries an ASCII letter.
_TAG_RUN_RE = re.compile("[\U000E0000-\U000E007F]+")


def _hostile(folded: str) -> bool:
    return bool(_HOSTILE_RE.search(folded) or _EXFIL_RE.search(folded))


def ai_line(line: Any) -> bool:
    """True when one line of an outside project's text reads like instructions
    to an AI (hidden from the model). Shell install lines never count."""
    if not isinstance(line, str) or not line.strip():
        return False
    from puffpai import artifact_review
    folded = artifact_review.fold_for_orders(line)
    return (_hostile(folded) or bool(_AI_LINE_RE.search(folded))
            or artifact_review.model_orders(line))


def _tag_text(text: str) -> str:
    """The ASCII that runs of invisible tag characters spell."""
    return " ".join("".join(chr(ord(ch) - 0xE0000) for ch in m.group(0)
                            if 0x20 <= ord(ch) - 0xE0000 < 0x7F)
                    for m in _TAG_RUN_RE.finditer(text))


def readme_block(text: Any) -> str:
    """Why an outside project's README (or doc) is stopped, or "".

    Only clearly hostile text stops it; anything else that reads like
    instructions to an AI is hidden line by line (:func:`hide_ai_lines`)."""
    if not isinstance(text, str) or not text:
        return ""
    from puffpai import artifact_review
    tags = _tag_text(text)
    if tags and (ai_line(tags) or len(tags.replace(" ", "")) > 16):
        return "it hides words for an AI in invisible characters"
    for m in _COMMENT_RE.finditer(text):
        if any(ai_line(ln) for ln in m.group(1).splitlines()):
            return "it hides words for an AI in a comment people do not see"
    for line in text.splitlines():
        if not ai_line(line):
            continue
        if not ai_line_plain(line):
            # Only read as words for an AI once invisible or look-alike
            # characters are taken out: hidden on purpose.
            return "it hides words for an AI behind invisible or look-alike characters"
        folded = artifact_review.fold_for_orders(line)
        if _EXFIL_RE.search(folded):
            return "its words ask an AI to send keys or passwords away"
        if _HOSTILE_RE.search(folded):
            return "its words read like orders to a computer"
    return ""


def ai_line_plain(line: str) -> bool:
    """The line reads like instructions to an AI WITHOUT folding (so nothing
    invisible or look-alike was needed to say it)."""
    from puffpai import artifact_review
    return bool(_HOSTILE_RE.search(line) or _EXFIL_RE.search(line)
                or _AI_LINE_RE.search(line)
                or artifact_review._MODEL_ORDER_RE.search(line)
                or artifact_review._MODEL_ORDER_MORE_RE.search(line))


def hide_ai_lines(text: Any) -> tuple[Any, int]:
    """*text* with each line that reads like instructions to an AI replaced by
    :data:`HIDDEN_LINE`, and how many were. Not a string: as given, 0."""
    if not isinstance(text, str) or not text:
        return text, 0
    if _TAG_RUN_RE.search(text):
        text = _TAG_RUN_RE.sub("", text)          # never passed on to a model
    out, n = [], 0
    for line in text.split("\n"):
        if ai_line(line):
            out.append(HIDDEN_LINE)
            n += 1
        else:
            out.append(line)
    return "\n".join(out), n


def hide_ai_lines_in(value: Any) -> tuple[Any, int]:
    """:func:`hide_ai_lines` on every string inside *value* (a tool's answer:
    dicts, lists, strings). Returns the new value and the lines hidden."""
    if isinstance(value, str):
        return hide_ai_lines(value)
    if isinstance(value, dict):
        total, out = 0, {}
        for k, v in value.items():
            out[k], n = hide_ai_lines_in(v)
            total += n
        return out, total
    if isinstance(value, (list, tuple)):
        total, items = 0, []
        for v in value:
            item, n = hide_ai_lines_in(v)
            items.append(item)
            total += n
        return (type(value)(items) if isinstance(value, tuple) else items), total
    return value, 0


def kind_of(draft: Any) -> str:
    if not isinstance(draft, dict):
        return ""
    kind = str(draft.get("kind") or "").strip().lower()
    if kind:
        return kind
    if isinstance(draft.get("manifest"), dict):
        return "live_card"
    return ""


def _owner_fields(draft: dict[str, Any], kind: str) -> list[str]:
    """Every string of the addition a person reads."""
    from puffpai import artifact_review
    out: list[Any] = [draft.get("name"), draft.get("label"),
                      draft.get("what_leaves")]
    cand = draft.get("candidate") if isinstance(draft.get("candidate"), dict) else {}
    out += [cand.get("name"), cand.get("what_it_is"), cand.get("license_plain")]
    manifest = draft.get("manifest")
    if isinstance(manifest, dict):
        out += artifact_review._labels(manifest)
    if kind in DOWNLOAD_KINDS and not cand:
        out += [draft.get("what_it_is"), draft.get("license_plain")]
    return [s for s in out if isinstance(s, str) and s]


def _host_of(draft: dict[str, Any]) -> str:
    manifest = draft.get("manifest") if isinstance(draft.get("manifest"), dict) else {}
    service = (manifest.get("feed") or {}).get("service") or {}
    cand = draft.get("candidate") if isinstance(draft.get("candidate"), dict) else {}
    host = str(service.get("host") or cand.get("host") or draft.get("host") or "")
    return host if re.fullmatch(r"[A-Za-z0-9.-]{1,120}", host) else ""


def _safe_name(draft: dict[str, Any], kind: str) -> str:
    """The addition's name for the record; never words that read like orders."""
    cand = draft.get("candidate") if isinstance(draft.get("candidate"), dict) else {}
    name = " ".join(str(draft.get("name") or cand.get("name") or "").split())[:80]
    if name and not instruction_shaped(name) and "://" not in name:
        return name
    host = _host_of(draft)
    if kind in DOWNLOAD_KINDS:
        return f"A book from {host}" if host else "A book"
    return f"Live info from {host}" if host else "An addition"


def _clean_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Rows fit to keep and show: never a quote of the words that were stopped."""
    out = []
    for r in rows:
        why = str(r.get("why") or "")
        if r.get("code") in ("instruction_text", "copied_text"):
            why = why.split(":", 1)[0].rstrip(".") + "."
        out.append({"check": str(r.get("check") or ""),
                    "result": str(r.get("result") or ""),
                    "why": why[:240], "code": str(r.get("code") or "")})
    return out


# ---------------------------------------------------------------------------
# one checklist per kind, registered once, then closed
# ---------------------------------------------------------------------------

def register_checker(kind: str, rows_fn, *, block_codes: frozenset = frozenset(),
                     hold_codes: frozenset = frozenset(), version: str = "1") -> None:
    """Register the checklist for *kind*: ``rows_fn(draft) -> [row]``.

    Rows are ``{"check", "result", "why", "code"}`` with result ``pass``,
    ``fail``, ``unknown`` or ``note`` (a plain note, never a verdict). A failed
    or unknown row whose code is in *block_codes* (or the always-block codes)
    blocks; any other failed or unknown row holds.

    Raises :class:`RegistrationClosed` after :func:`close_registration`, and
    ``ValueError`` for a kind registered before, a built-in kind, or a bad
    argument.
    """
    if not isinstance(kind, str) or not _KIND_RE.match(kind):
        raise ValueError("A checker kind is a short lowercase name.")
    if not callable(rows_fn):
        raise ValueError("A checker is a callable.")
    groups = []
    for group in (block_codes, hold_codes):
        if not isinstance(group, frozenset) or not all(
                isinstance(c, str) and _CODE_RE.match(c) for c in group):
            raise ValueError("Checker codes are a frozenset of short names.")
        groups.append(group)
    version = str(version or "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,20}", version):
        raise ValueError("A checker version is a short plain string.")
    with _REG_LOCK:
        if _REGISTRY["closed"]:
            raise RegistrationClosed(
                f"Drawbridge checkers are closed; {kind!r} was not registered.")
        if kind in _CHECKERS or kind in DOWNLOAD_KINDS or kind in LIVE_KINDS:
            raise ValueError(f"A checker for {kind!r} is already registered.")
        _CHECKERS[kind] = {"rows_fn": rows_fn, "block": groups[0],
                           # a code named both ways blocks (the stricter reading)
                           "hold": groups[1] - groups[0], "version": version}


def close_registration() -> None:
    """No checker can be registered or replaced after this. Idempotent."""
    with _REG_LOCK:
        _REGISTRY["closed"] = True


def registration_closed() -> bool:
    with _REG_LOCK:
        return bool(_REGISTRY["closed"])


def checker_kinds() -> list[str]:
    with _REG_LOCK:
        return sorted(_CHECKERS)


def _checker_for(kind: str) -> Optional[dict[str, Any]]:
    with _REG_LOCK:
        return _CHECKERS.get(kind)


def decide_registered(rows: list[dict[str, str]], checker: dict[str, Any],
                      extra_blocks: Optional[list[str]] = None) -> tuple[str, list[str]]:
    """``(verdict, reason codes)`` for a registered kind's rows. Pure."""
    blocking = set(_ALWAYS_BLOCK) | set(checker.get("block") or ())
    blocks = list(extra_blocks or [])
    holds: list[str] = []
    if not any(r.get("result") != "note" for r in rows) and "check_failed" not in blocks:
        # A checklist that checked nothing (no rows, or only plain notes) is
        # a broken checker, never a pass.
        blocks.append("check_failed")
    for r in rows:
        result, code = r.get("result"), str(r.get("code") or "")
        if result in ("pass", "note"):
            continue
        if code in blocking:
            if code not in blocks:
                blocks.append(code)
        elif (code or str(result)) not in holds:
            # Named hold codes and anything the checker did not name: a row
            # that did not pass is never a pass.
            holds.append(code or str(result))
    if blocks:
        return BLOCK, blocks
    if holds:
        return HOLD, holds
    return ALLOW, []


def rows_sha(rows: list[dict[str, str]]) -> str:
    """A fingerprint of exactly what the checklist said."""
    blob = json.dumps(_clean_rows(rows), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# the one policy
# ---------------------------------------------------------------------------

def _rows_for(draft: dict[str, Any], kind: str) -> list[dict[str, str]]:
    from puffpai import artifact_review
    if kind in DOWNLOAD_KINDS:
        cand = draft.get("candidate") if isinstance(draft.get("candidate"), dict) else draft
        return artifact_review.review_download(cand)
    return artifact_review.review(draft)


def decide(rows: list[dict[str, str]], kind: str,
           extra_blocks: Optional[list[str]] = None) -> tuple[str, list[str]]:
    """``(verdict, reason codes)`` for checklist rows. Pure; used by tests too."""
    blocking = set(_ALWAYS_BLOCK)
    if kind in DOWNLOAD_KINDS:
        blocking |= _DOWNLOAD_BLOCK
    blocks = list(extra_blocks or [])
    warns: list[str] = []
    for r in rows:
        result, code = r.get("result"), str(r.get("code") or "")
        if result == "pass":
            continue
        if code in blocking and result in ("fail", "unknown"):
            if code not in blocks:
                blocks.append(code)
        elif code not in warns:
            warns.append(code or str(result))
    if blocks:
        return BLOCK, blocks
    if warns:
        return WARN, warns
    return ALLOW, []


def check_addition(draft: Any, *, by: str = "drawbridge", record: bool = True,
                   now: Optional[str] = None) -> dict[str, Any]:
    """Check one addition with the one policy. Never raises.

    Returns ``{"verdict", "results", "checked_at", "draft_sha", "reasons",
    "addition_id", "kind", "name", "line"}``. ``results`` are the checklist
    rows (check, result, why, code). ``line`` is one plain sentence for the
    owner. With *record* (the default) the verdict is appended to the log.
    """
    checked_at = now or _now()
    if not isinstance(draft, dict):
        verdict = {"verdict": BLOCK, "results": [], "checked_at": checked_at,
                   "draft_sha": draft_sha(draft), "reasons": ["not_a_record"],
                   "addition_id": "", "kind": "", "name": "An addition"}
        verdict["line"] = line_for(verdict)
        if record:
            _append(verdict, by)
        return verdict
    kind = kind_of(draft)
    extra: list[str] = []
    checker = _checker_for(kind)
    try:
        if checker is not None:
            rows = list(checker["rows_fn"](draft) or [])
            if not all(isinstance(r, dict) for r in rows):
                raise TypeError("a checklist row is a mapping")
        elif kind not in DOWNLOAD_KINDS and kind not in LIVE_KINDS:
            rows, extra = [], ["unknown_kind"]
        else:
            rows = _rows_for(draft, kind)
    except Exception:  # a checklist that crashed is a stop, never a pass
        logger.exception("drawbridge: the checklist failed")
        rows, extra = [], ["check_failed"]
    if any(instruction_shaped(t) for t in _owner_fields(draft, kind)) \
            and "instruction_text" not in extra:
        extra.append("instruction_text")
    if checker is not None:
        decision, reasons = decide_registered(rows, checker, extra)
    else:
        decision, reasons = decide(rows, kind, extra)
    cand = draft.get("candidate") if isinstance(draft.get("candidate"), dict) else {}
    verdict = {
        "verdict": decision,
        "results": _clean_rows(rows),
        "checked_at": checked_at,
        "draft_sha": draft_sha(draft),
        "reasons": reasons,
        "addition_id": str(draft.get("id") or cand.get("id") or "")[:120],
        "kind": kind,
        "name": _safe_name(draft, kind),
    }
    if checker is not None:
        verdict["checker_version"] = checker["version"]
        verdict["rows_sha"] = rows_sha(rows)
    verdict["line"] = line_for(verdict)
    if record:
        _append(verdict, by)
    return verdict


# ---------------------------------------------------------------------------
# plain words
# ---------------------------------------------------------------------------

_REASON_WORDS = {
    "license_unknown": "nobody says what you may do with it",
    "not_https_443": "its address is not an ordinary encrypted connection",
    "host_mismatch": "its address does not belong to the service it names",
    "forbidden_key": "it has a field where code could hide",
    "invalid": "it did not pass its own format check",
    "instruction_text": "some of its words read like orders to a computer",
    "too_big": "its sample reply was too big",
    "wrong_type": "its sample reply was not data",
    "unknown_kind": "BuildBot does not know what kind of thing it is",
    "check_failed": "the checks could not finish",
    "not_a_record": "it was not something BuildBot can read",
    "hidden_loader": "its page hides encoded code that it unpacks and runs",
}


def line_for(verdict: dict[str, Any]) -> str:
    """One plain sentence about a verdict, for the owner."""
    name = str(verdict.get("name") or "This addition")
    decision = verdict.get("verdict")
    if decision == ALLOW:
        return f"{name}: passed every check."
    if decision == WARN:
        first = next((r.get("why") for r in verdict.get("results") or []
                      if r.get("result") != "pass" and r.get("why")), "")
        tail = f" {first}" if first else ""
        return f"{name}: safe to add, with something to know first.{tail}"
    if decision == HOLD:
        first = next((r.get("why") for r in verdict.get("results") or []
                      if r.get("result") not in ("pass", "note") and r.get("why")), "")
        tail = f" {first}" if first else ""
        return f"{name}: waiting for your yes.{tail}"
    reasons = [_REASON_WORDS.get(c, "") for c in verdict.get("reasons") or []]
    reasons = [r for r in reasons if r]
    why = f" because {reasons[0]}" if reasons else ""
    return f"{name}: stopped{why}. It was not added."


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------

def _append(verdict: dict[str, Any], by: str) -> None:
    line = dict(verdict)
    line["by"] = str(by or "drawbridge")[:40]
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _LOCK:
            fsutil.atomic_append(path, json.dumps(line, ensure_ascii=True))
            if os.path.getsize(path) > _MAX_LOG_BYTES:
                _trim(path)
    except OSError as exc:
        # A check whose record could not be written still stands; the log
        # line says so without any of the addition's words.
        logger.warning("drawbridge: could not record a check: %s", exc)


def _trim(path: str) -> None:
    lines = fsutil.read_text(path).splitlines()[-_KEEP_LINES:]
    fsutil.atomic_write(path, "\n".join(lines) + "\n")


def _read() -> list[dict[str, Any]]:
    try:
        with open(log_path(), "r", encoding="utf-8") as fh:
            raw = fh.read().splitlines()
    except OSError:
        return []
    out = []
    for text in raw:
        try:
            row = json.loads(text)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("verdict") in VERDICTS:
            out.append(row)
    return out


def recent(limit: int = 20) -> list[dict[str, Any]]:
    """The latest checks, newest first, in the shape a page shows.

    ``[{"name", "kind", "verdict", "line", "checked_at", "results"}]``.
    Never raises.
    """
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 20
    rows = _read()[-limit:]
    rows.reverse()
    return [{"name": r.get("name"), "kind": r.get("kind"),
             "verdict": r.get("verdict"), "line": r.get("line") or line_for(r),
             "checked_at": r.get("checked_at"),
             "results": r.get("results") or []} for r in rows]


def latest_for(addition_id: str) -> Optional[dict[str, Any]]:
    """The last recorded check of one addition, or ``None``."""
    if not addition_id:
        return None
    for row in reversed(_read()):
        if row.get("addition_id") == addition_id:
            return row
    return None


def require_not_blocked(draft: Any, *, by: str = "install") -> dict[str, Any]:
    """Re-check *draft* now and raise :class:`Blocked` on a block.

    Returns the fresh verdict, with ``changed_since_check`` set when an
    earlier check of the same addition saw something different.
    """
    addition_id = str((draft or {}).get("id") or "") if isinstance(draft, dict) else ""
    earlier = latest_for(addition_id)
    verdict = check_addition(draft, by=by)
    verdict["changed_since_check"] = bool(
        earlier and earlier.get("draft_sha") != verdict["draft_sha"])
    if verdict["verdict"] == BLOCK:
        raise Blocked(verdict["line"], verdict)
    return verdict


__all__ = ["ALLOW", "BLOCK", "Blocked", "DOWNLOAD_KINDS", "HOLD", "LOG_FILENAME",
           "RegistrationClosed", "VERDICTS", "WARN", "check_addition",
           "checker_kinds", "close_registration", "decide", "decide_registered",
           "draft_sha", "instruction_shaped", "kind_of", "latest_for", "line_for",
           "log_path", "recent", "register_checker", "registration_closed",
           "require_not_blocked", "rows_sha"]
