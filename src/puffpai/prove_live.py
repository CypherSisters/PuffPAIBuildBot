"""The live half of Prove: the built bot, asked over HTTP, answering for itself.

Reading the manifest and agreeing with it is not a proof (spec C.11). So this
half opens no files and trusts nothing it wrote: it calls the backend the way
the dashboard does -- ``GET /persona/list``, ``GET /knowledge/sources``, and at
most three ``POST /chat/send`` generations -- and records what came back,
verbatim.

Three rules it does not bend:

* **The questions are deterministic.** The identity question is one fixed
  sentence; the other two are built from the nouns already in the owner's goal.
  No model writes the test it is about to sit.
* **Only identity is graded.** Pass means the reply contains the bot's name.
  The knowledge answers are transcript -- there is no honest automatic grade for
  "did it answer well", and a made-up one is how a build comes to agree with
  itself again.
* **The browser is the owner's.** This module has no browser. It prints the
  dashboard address, asks whether the bot is in the left menu, and records
  ``owner_confirmed`` / ``owner_refuted`` / ``owner_unproven``. Unproven is a
  perfectly good outcome; a guess is not.

The API key is sent as a header and never printed, never returned in the
record, and never written to the run state.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from puffpai import prove

#: Cap on the reply text kept in the record. Enough for an owner to judge it,
#: short enough that the run log does not become a chat history.
REPLY_CHARS = 300

OWNER_CONFIRMED = "owner_confirmed"
OWNER_REFUTED = "owner_refuted"
OWNER_UNPROVEN = "owner_unproven"


class Unreachable(Exception):
    """The backend did not answer. Never a crash, always a stated outcome.

    ``status`` carries the HTTP code when there was one, and ``None`` when
    nothing answered at all. The caller needs the difference: a backend that
    answers 404 to a route is an OLDER backend, which is a plain sentence to
    print, and a backend that answers nothing is a backend to start.
    """

    def __init__(self, message: str = "", status: int | None = None):
        super().__init__(message)
        self.status = status


class NotLocal(ValueError):
    """An address Prove will not talk to, with the reason in plain words."""


def _addresses(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError, ValueError) as exc:
        raise NotLocal(f"{host} does not resolve on this machine ({exc}).") from exc
    return [info[4][0] for info in infos if info[4]]


def check_local_url(url: Any) -> str:
    """The URL Prove may use, or :class:`NotLocal`. Loopback and LAN only.

    Prove exists to ask **the owner's own backend** whether it answers as
    itself, and it carries this install's API key in a header while it does.
    So the destination is checked before anything is sent: the scheme is http
    or https, the URL carries no credentials and no query, and **every address
    the host resolves to** is loopback, private or link-local. One public
    address among several is a refusal -- that is what makes the
    ``lan-egress`` marker on the call below a true statement rather than a
    comment.
    """
    text = str(url or "").strip()
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in ("http", "https"):
        raise NotLocal(f"{text!r} is not an http address.")
    if parsed.username or parsed.password:
        raise NotLocal("A URL carrying a name and password is refused; Prove "
                       "sends this install's key in a header, nowhere else.")
    if parsed.query or parsed.fragment:
        raise NotLocal("Give the address of the backend, without a query.")
    host = (parsed.hostname or "").strip().rstrip(".")
    if not host:
        raise NotLocal(f"{text!r} names no host.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise NotLocal(f"{text!r} has no usable port.") from exc

    addresses = _addresses(host, port)
    if not addresses:
        raise NotLocal(f"{host} resolves to nothing on this machine.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise NotLocal(f"{host} resolves to something that is not an "
                           f"address ({address!r}).") from exc
        if not (ip.is_loopback or ip.is_private or ip.is_link_local):
            raise NotLocal(
                f"{host} is out on the internet ({ip}). Prove talks to the "
                f"backend on this machine or on this network, and to nothing "
                f"else.")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect.

    A checked LAN address that answers 302 to somewhere public would carry the
    API key off the machine, so the hop is not followed at all: urllib turns a
    refused redirect into an HTTPError, which is reported as the backend not
    answering.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(url: str, path: str, *, api_key: str, method: str = "GET",
             body: dict | None = None, timeout: float = 30.0) -> Any:
    try:
        base = check_local_url(url)
    except NotLocal as exc:
        raise Unreachable(str(exc)) from exc
    payload = None
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=payload,
                                     headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects)  # lan-egress: no proxy; the owner's own backend, loopback or LAN only, checked by check_local_url above
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(1_000_000)
    except urllib.error.HTTPError as exc:
        raise Unreachable(f"the backend answered {exc.code} for {path}",
                          status=exc.code) from exc
    except Exception as exc:
        raise Unreachable(f"{type(exc).__name__}") from exc
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise Unreachable(f"the answer to {path} was not JSON") from exc


def request_json(url: str, path: str, *, api_key: str, method: str = "GET",
                 body: dict | None = None, timeout: float = 30.0) -> Any:
    """One checked call to the owner's own backend. The public name of
    :func:`_request`, so ``puffpai prove`` asks the backend through the same
    loopback/LAN check, the same refused redirects and the same header-only key
    as the live half does. Nothing else in this build opens a socket for Prove.
    """
    return _request(url, path, api_key=api_key, method=method, body=body,
                    timeout=timeout)


def questions(goal: str, *, limit: int = prove.MAX_CHAT_CALLS - 1) -> list[str]:
    """Up to *limit* knowledge questions, built from the goal's own nouns."""
    from puffpai import persona_scaffold

    out: list[str] = []
    for word in persona_scaffold.goal_keywords(str(goal or "")):
        if len(word) < 4:
            continue
        out.append(f"What do you know about {word}?")
        if len(out) >= limit:
            break
    return out


def _persona_check(data: Any, persona_id: str, bot_name: str) -> dict:
    personas = (data or {}).get("personas") if isinstance(data, dict) else None
    personas = personas if isinstance(personas, list) else []
    row = next((p for p in personas
                if isinstance(p, dict) and str(p.get("id")) == persona_id), None)
    if row is None:
        seen = ", ".join(str(p.get("id")) for p in personas
                         if isinstance(p, dict)) or "nothing"
        return prove._check(
            "served_persona", "The running bot is served", prove.FAIL,
            f"The backend lists {seen}, and '{persona_id}' is not there.")
    shown = str(row.get("display_name") or "")
    if bot_name and bot_name.lower() not in shown.lower():
        return prove._check(
            "served_persona", "The running bot is served", prove.FAIL,
            f"The backend serves '{persona_id}' as '{shown}', which is not "
            f"{bot_name}.")
    return prove._check("served_persona", "The running bot is served",
                        prove.PASS,
                        f"The backend serves '{persona_id}' as '{shown}'.")


def _sources_check(data: Any, static_record: dict) -> dict:
    title = "The knowledge page agrees with the disk"
    counts = (data or {}).get("counts") if isinstance(data, dict) else None
    if not isinstance(counts, dict):
        return prove._check("served_sources", title, prove.GAP,
                            "The backend did not answer with a source count.")
    static_check = next((c for c in (static_record.get("checks") or [])
                         if c.get("id") == "sources_honest"), {})
    unfinished = (int(counts.get("selected_not_downloaded") or 0)
                  + int(counts.get("fetched_not_indexed") or 0))
    if static_check.get("result") == prove.NOT_APPLICABLE and not unfinished:
        # Knowledge setup was skipped. Nothing was chosen, so "everything
        # chosen is downloaded" is a sentence about an empty set and reads as a
        # claim the build never earned.
        return prove._check("served_sources", title, prove.NOT_APPLICABLE,
                            "Nothing was chosen during the build, and the "
                            "backend reports nothing unfinished.")
    static_said = static_check.get("result") == prove.GAP
    if unfinished and not static_said:
        return prove._check("served_sources", title, prove.FAIL,
                            f"The backend reports {unfinished} unfinished "
                            f"sources and the build said everything was done.")
    if static_said and not unfinished:
        return prove._check("served_sources", title, prove.FAIL,
                            "The build recorded unfinished sources and the "
                            "backend reports none.")
    if unfinished:
        return prove._check("served_sources", title, prove.GAP,
                            f"{unfinished} chosen sources are still "
                            f"unfinished, which is what the build recorded.")
    return prove._check("served_sources", title, prove.PASS,
                        "Everything chosen is downloaded and indexed, on disk "
                        "and on the page.")


def _ask(url: str, api_key: str, question: str, timeout: float) -> str:
    data = _request(url, "/chat/send", api_key=api_key, method="POST",
                    body={"message": question}, timeout=timeout)
    if isinstance(data, dict):
        return str(data.get("response") or data.get("message") or "")
    return ""


def live_prove(static_record: dict, *, url: str, api_key: str = "",
               goal: str = "", timeout: float = 120.0,
               ask_owner: Callable[[str], str] | None = None,
               dashboard_url: str = "") -> dict:
    """Talk to the running build. Never raises; an absent backend is an answer."""
    persona_id = str(static_record.get("persona_id") or "")
    bot_name = str(static_record.get("bot_name") or "")
    live: dict[str, Any] = {
        "ran": False,
        "url": url,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": [],
        "transcript": [],
        "owner": OWNER_UNPROVEN,
        "owner_question": "",
        "dashboard_url": dashboard_url,
        "note": "",
    }
    try:
        live["url"] = check_local_url(url)
    except NotLocal as exc:
        live["note"] = f"not run: {exc}"
        return live
    try:
        personas = _request(url, "/persona/list", api_key=api_key,
                            timeout=min(timeout, 30.0))
    except Unreachable as exc:
        live["note"] = f"not run: backend not answering at {url} ({exc})"
        return live

    live["ran"] = True
    if persona_id:
        live["checks"].append(_persona_check(personas, persona_id, bot_name))
    else:
        live["checks"].append(prove._check(
            "served_persona", "The running bot is served", prove.NOT_APPLICABLE,
            "This build made no persona."))

    try:
        sources = _request(url, "/knowledge/sources", api_key=api_key,
                           timeout=min(timeout, 30.0))
        live["checks"].append(_sources_check(sources, static_record))
    except Unreachable as exc:
        live["checks"].append(prove._check(
            "served_sources", "The knowledge page agrees with the disk",
            prove.GAP, f"The backend did not answer /knowledge/sources ({exc})."))

    asked = 0
    try:
        reply = _ask(url, api_key, prove.IDENTITY_QUESTION, timeout)
        asked += 1
    except Unreachable as exc:
        reply = ""
        live["checks"].append(prove._check(
            "answers_as_itself", "It answers as itself", prove.FAIL,
            f"The chat endpoint did not answer ({exc})."))
    else:
        named = bool(bot_name) and bot_name.lower() in reply.lower()
        live["transcript"].append({
            "question": prove.IDENTITY_QUESTION,
            "reply": reply[:REPLY_CHARS],
            "verdict": prove.PASS if named else prove.FAIL,
        })
        live["checks"].append(prove._check(
            "answers_as_itself", "It answers as itself",
            prove.PASS if named else prove.FAIL,
            f"Asked who it is, it {'said' if named else 'did not say'} "
            f"{bot_name or 'its name'}."))

    for question in questions(goal):
        if asked >= prove.MAX_CHAT_CALLS:
            break
        try:
            reply = _ask(url, api_key, question, timeout)
        except Unreachable:
            break
        asked += 1
        live["transcript"].append({
            "question": question,
            "reply": reply[:REPLY_CHARS],
            "verdict": "transcript_only",
        })
    live["chat_calls"] = asked

    question = (f"Do you see {bot_name or 'your bot'} in the left menu? "
                f"[y/n/skip]")
    live["owner_question"] = question
    answer = ""
    if ask_owner is not None:
        try:
            answer = (ask_owner(question) or "").strip().lower()
        except Exception:
            answer = ""
    if answer.startswith("y"):
        live["owner"] = OWNER_CONFIRMED
    elif answer.startswith("n"):
        live["owner"] = OWNER_REFUTED
    else:
        live["owner"] = OWNER_UNPROVEN
    return live


#: The three answers the owner line may hold, and nothing else is accepted from
#: a request body anywhere in this build.
OWNER_ANSWERS = (OWNER_CONFIRMED, OWNER_REFUTED, OWNER_UNPROVEN)

__all__ = ["OWNER_ANSWERS", "OWNER_CONFIRMED", "OWNER_REFUTED",
           "OWNER_UNPROVEN", "REPLY_CHARS", "NotLocal", "Unreachable",
           "check_local_url", "live_prove", "questions", "request_json"]
