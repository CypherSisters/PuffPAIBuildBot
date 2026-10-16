"""What the bot says about privacy, built from what is true right now.

The old answers were hand-written and said conversations and documents were
"encrypted at rest". They are not: saved chats are ordinary files on this
computer. This module builds every privacy sentence a person reads from the
live vault state, the model's address and the outside services that are on,
so the words cannot drift away from the code.

Every sentence is checked against the code it describes:

* Vault: :mod:`puffpai.security.vault` - Fernet (AES-128 + HMAC-SHA256), keys
  from the PIN or the recovery code with PBKDF2-HMAC-SHA256 at 600,000 rounds,
  locked at start.
* Personal details in chat: :mod:`puffpai.security.chat_privacy` - kept out of
  saved chats, filed in the vault (sealed in while it is locked, see
  :mod:`puffpai.security.vault_inbox`).
* Uploads: :mod:`puffpai.ingestion` - a document with personal details keeps
  its original in the vault; only a copy without them is searchable.
* Outside services: :mod:`puffpai.outside_services` - the one list, with
  each service's switch.

Plain words only: nothing a person reads says zone, door, egress, posture,
tier or an internal id. The three part names (the Vault, the Brain, the
Bridge) are product names and each is explained where it is used.

Public:

* :func:`plain_summary` - short sentences for the wizard's summary and the
  Settings page.
* :func:`proposal_items` - the "private" lines of the wizard's one summary.
* :func:`privacy_answer`, :func:`where_am_i_answer` - chat answers.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Optional
from urllib.parse import urlparse

_LIVE = "live"

#: Plain names for the outside services a build can turn on.
SERVICE_NAMES: dict[str, str] = {
    "weather": "Weather",
    "air_quality": "Air quality",
    "news_rss": "News headlines",
    "web_fetch": "Reading a web page you ask about",
    "web_search": "Web search",
}

#: What leaves the computer when that service is on, in plain words. These
#: restate each service's own ``data_sent`` note (egress_doors.DOORS) without
#: its technical words. A service not listed here is described from the hosts
#: it may reach, which the code knows for certain.
_SENDS: dict[str, str] = {
    "weather": "your approximate location, and nothing else",
    "air_quality": "your approximate location, and nothing else",
    "news_rss": ("only the request itself; the news site sees that your "
                 "computer asked for its headlines"),
    "web_fetch": ("only the request itself; the website sees that your "
                  "computer asked for that page"),
    "web_search": "the words you search for",
}


def _service_name(service_id: str) -> str:
    return (SERVICE_NAMES.get(service_id)
            or service_id.replace("_", " ").strip().capitalize()
            or "An outside service")


# ── live facts ─────────────────────────────────────────────────────────────

def _live_vault():
    try:
        from puffpai.api_server import _get_vault
        return _get_vault()
    except Exception:
        try:
            from puffpai.security.vault import Vault
            return Vault()
        except Exception:
            return None


def vault_state(vault) -> str:
    """``"open"``, ``"locked"``, ``"missing"``, ``"not_plugged_in"`` or
    ``"unknown"``.

    ``"not_plugged_in"``: a vault was made, but its folder or drive is not
    there now (the vault's own ``missing_reason``). Never read as "no vault
    yet", which would point the owner at making a second, empty one.
    """
    if vault is None:
        return "unknown"
    try:
        if not vault.is_initialized:
            from puffpai.security.vault import missing_words
            return "not_plugged_in" if missing_words(vault) else "missing"
        return "open" if vault.is_unlocked else "locked"
    except Exception:
        return "unknown"


def waiting_count(vault) -> Optional[int]:
    """How many details wait in the locked vault's inbox, or None if unknown."""
    try:
        from puffpai.security import vault_inbox
        return vault_inbox.pending_count(vault.vault_dir)
    except Exception:
        return None


def open_services() -> Optional[list[dict[str, Any]]]:
    """Outside services that are on now, or None when that cannot be read.

    None is not "none": a build that cannot read its own switches says so
    instead of claiming everything is off.

    One source for every count: the rows of "What leaves your computer"
    (:func:`puffpai.outside_services.rows`, state ``on``). This used to read
    only the built-in services (weather, news, ...), so the sidebar, chat and
    Developer pages said "none" while the home page and the list said 12.
    """
    try:
        from puffpai import outside_services
        rows = outside_services.rows()
    except Exception:
        return None
    out = []
    for row in rows:
        if row.get("state") != "on":
            continue
        sid = str(row.get("id") or "")
        hosts = [str(h) for h in (row.get("websites") or [])
                 if h and " " not in str(h) and not str(h).startswith("(")]
        out.append({"id": sid,
                    "name": SERVICE_NAMES.get(sid) or str(row.get("name") or "")
                    or _service_name(sid),
                    # The name "What leaves your computer" shows for the row,
                    # so every on-list reads the same words as that page.
                    "label": str(row.get("name") or "") or _service_name(sid),
                    "hosts": hosts})
    return out


def _live_model_url() -> str:
    try:
        from puffpai.config import get_config
        return str(get_config().ollama.url or "")
    except Exception:
        return ""


def model_is_local(model_url: str) -> Optional[bool]:
    """True for a model on this computer, False for another machine, None if unknown."""
    try:
        host = (urlparse(model_url).hostname or "").strip().lower()
    except Exception:
        return None
    if not host:
        return None
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# ── sentences ──────────────────────────────────────────────────────────────

def _vault_line(state: str, waiting: Optional[int] = None) -> str:
    if state == "open":
        return ("Your private stuff is in a vault on this computer. It is open "
                "right now; it locks again when you lock it or restart.")
    if state == "locked":
        line = ("Your private stuff is locked in a vault on this computer. "
                "Only your PIN or your recovery code opens it.")
        if waiting:
            line += (f" {waiting} personal detail{'s' if waiting != 1 else ''} "
                     f"{'are' if waiting != 1 else 'is'} waiting inside for "
                     f"you to open it.")
        return line
    if state == "missing":
        return "You have not set up a vault yet."
    if state == "not_plugged_in":
        return ("Your vault drive is not plugged in. Plug it in to use your "
                "vault; no new vault is made in its place.")
    return "I could not check your vault just now."


def inbox_ready(vault) -> bool:
    """True when a locked vault can take a detail (its inbox key exists)."""
    try:
        from puffpai.security import vault_inbox
        return vault_inbox.can_accept(vault.vault_dir)
    except Exception:
        return False


def _chat_line(state: str, ready: bool = True) -> str:
    base = ("When I notice a personal detail in chat, like a phone number, a "
            "home address or a birthday, I keep it out of my saved chats")
    if state == "open" or (state == "locked" and ready):
        return base + " and put it in your vault, even while the vault is locked."
    if state == "locked":
        return (base + ". I can only put it in your vault while it is locked "
                "after you have opened the vault once with your PIN.")
    if state == "missing":
        return (base + ", but with no vault set up there is nowhere safe to "
                "keep a copy.")
    if state == "not_plugged_in":
        return (base + ", but your vault drive is not plugged in, so there is "
                "nowhere safe to keep a copy just now.")
    return base + "."


_SAVED_CHATS = ("Saved chats are ordinary files on this computer. They are not "
                "encrypted, which is why personal details are taken out first.")
_DOCUMENTS = ("Documents you add are searchable files on this computer. If one "
              "has personal details, the original goes into the vault and only "
              "a copy without them is searchable.")


def _model_line(model_url: str) -> str:
    local = model_is_local(model_url)
    if local is True:
        return "The AI model runs on this computer."
    if local is False:
        host = urlparse(model_url).hostname or "another computer"
        return (f"The AI model runs on another computer ({host}), so what you "
                f"type is sent there over your network.")
    return "I could not tell where the AI model runs."


def _service_line(s: dict[str, Any]) -> str:
    sends = _SENDS.get(s["id"])
    hosts = ", ".join(s.get("hosts") or [])
    if sends and hosts:
        return f"{s['name']}: sends {sends}, to {hosts}."
    if sends:
        return f"{s['name']}: sends {sends}."
    if hosts:
        return f"{s['name']}: may reach {hosts}."
    return f"{s['name']}: may reach a website you name."


#: The one privacy promise. Never
#: "nothing leaves your computer" on its own.
CONSENT_LINE = "Nothing leaves your computer without your consent."


def consent_summary(services: Any = _LIVE) -> dict[str, Any]:
    """The consent line and the outside services that are on, for every page.

    One source for the sidebar card, the home page and the Developer pages:
    the "on" rows of "What leaves your computer" (:func:`open_services`), with
    that page's names, in its order. ``count`` is None when the switches
    cannot be read, never 0. Never raises.
    """
    try:
        svc = open_services() if services == _LIVE else services
    except Exception:
        svc = None
    if svc is None:
        return {"consent_line": CONSENT_LINE, "count": None, "on": [],
                "line": "I could not check which outside services are on."}
    names = [str(s.get("label") or s.get("name") or "") for s in svc]
    names = [n for n in names if n]
    if not svc:
        line = ("What leaves your computer: only what you say yes to. Every "
                "outside service is off.")
    else:
        noun = "service is" if len(svc) == 1 else "services are"
        line = (f"What leaves your computer: only what you say yes to. "
                f"{len(svc)} outside {noun} on: {', '.join(names)}.")
    return {"consent_line": CONSENT_LINE, "count": len(svc), "on": names,
            "line": line}


def _services_lines(services: Optional[list[dict[str, Any]]]) -> list[str]:
    if services is None:
        return ["I could not check which outside services are on."]
    if not services:
        return [f"{CONSENT_LINE} Every outside service is off."]
    return (["What leaves your computer, and only while that service is on:"]
            + [_service_line(s) for s in services])


def _home_lines(home: Any) -> list[str]:
    """The town-level home, when one is set."""
    if home == _LIVE:
        try:
            from puffpai import home_location
            home = home_location.get_home()
        except Exception:  # noqa: BLE001 - unreadable is "not said"
            home = None
    if not isinstance(home, dict) or not home.get("label"):
        return []
    return [f"Your home is kept as a town only ({home['label']}), a rough spot "
            f"about 11 km wide. An exact address is never stored."]


def plain_summary(build: Optional[dict] = None, *, vault: Any = _LIVE,
                  services: Any = _LIVE, model_url: Optional[str] = None,
                  home: Any = _LIVE) -> list[str]:
    """Short plain sentences: what stays private and what leaves the computer.

    Built from the live vault, the live outside-service switches and the model
    address. ``build`` is an optional build record (the wizard's manifest):
    when no vault can be read at all, its ``vault.configured`` flag says
    whether one was set up; nothing else in it is trusted over the live state.
    Never raises.
    """
    try:
        live = _live_vault() if vault == _LIVE else vault
        state = vault_state(live)
        if state == "unknown" and isinstance(build, dict):
            block = build.get("vault") if isinstance(build.get("vault"), dict) else {}
            if block.get("configured") is True:
                state = "locked"
            elif block.get("configured") is False:
                state = "missing"
        waiting = waiting_count(live) if state == "locked" and live else None
        svc = open_services() if services == _LIVE else services
        url = _live_model_url() if model_url is None else model_url
        return [
            _vault_line(state, waiting),
            _chat_line(state, inbox_ready(live)),
            _SAVED_CHATS,
            _DOCUMENTS,
            _model_line(url),
            *_home_lines(home),
            *_services_lines(svc),
        ]
    except Exception:
        return ["I could not put together what stays private just now."]


def proposal_items(goal: str = "", terms: Optional[list[str]] = None,
                   *, vault: Any = _LIVE, model_url: Optional[str] = None
                   ) -> list[dict[str, Any]]:
    """The "private" lines of the wizard's one summary.

    What stays private does not depend on what the bot is about: a dinosaur
    bot and a car bot get the same protections, so ``goal`` and ``terms`` are
    accepted for the shared shape and do not change the result.
    """
    live = _live_vault() if vault == _LIVE else vault
    state = vault_state(live)
    items: list[dict[str, Any]] = []
    if state in ("open", "locked"):
        items.append({
            "id": "private:vault",
            "section": "private",
            "label": "Your locked vault for private stuff (already set up)",
            "detail": ("Only your PIN or your recovery code opens it. Nothing "
                       "in it leaves your computer."),
            "size_bytes": None, "outside_host": None,
            "default_on": True, "needs_yes": False,
            "apply": {"kind": "vault", "ref": "keep"},
        })
    elif state == "not_plugged_in":
        items.append({
            "id": "private:vault",
            "section": "private",
            "label": "Your vault (its drive is not plugged in)",
            "detail": ("Your vault drive is not plugged in. Plug it in to use "
                       "your vault. No new vault is made in its place."),
            "size_bytes": None, "outside_host": None,
            "default_on": True, "needs_yes": False,
            "apply": {"kind": "vault", "ref": "keep"},
        })
    else:
        items.append({
            "id": "private:vault",
            "section": "private",
            "label": "A locked vault for your private stuff",
            "detail": ("You choose a PIN and get a recovery code to write "
                       "down. Only those open it. Nothing in it leaves your "
                       "computer."),
            "size_bytes": None, "outside_host": None,
            "default_on": True, "needs_yes": False,
            "apply": {"kind": "vault", "ref": "create"},
        })
    items.append({
        "id": "private:chat_details",
        "section": "private",
        "label": "Keep personal details out of saved chats",
        "detail": ("Phone numbers, home addresses, birthdays, ID and card "
                   "numbers are taken out before a chat is saved and put in "
                   "the vault, even while it is locked. Saved chats are not "
                   "encrypted."),
        "size_bytes": None, "outside_host": None,
        "default_on": True, "needs_yes": False,
        "apply": {"kind": "privacy", "ref": "chat_details"},
    })
    url = _live_model_url() if model_url is None else model_url
    local = model_is_local(url)
    if local is not None:
        host = None if local else (urlparse(url).hostname or None)
        items.append({
            "id": "private:model_location",
            "section": "private",
            "label": ("The AI model runs on this computer" if local
                      else "The AI model runs on another computer"),
            "detail": ("What you type stays on this computer." if local
                       else f"What you type is sent to {host} over your "
                            f"network."),
            "size_bytes": None, "outside_host": host,
            "default_on": True, "needs_yes": not local,
            "apply": {"kind": "model", "ref": "current"},
        })
    return items


# ── chat answers ───────────────────────────────────────────────────────────

def privacy_answer(*, rich: bool, vault: Any = _LIVE, model_url: str = "",
                   services: Any = _LIVE) -> str:
    """The answer to "how do you keep my things private" / "what are the three parts"."""
    live = _live_vault() if vault == _LIVE else vault
    svc = open_services() if services == _LIVE else services
    state = vault_state(live)
    vault_text = _vault_line(state) + " "
    if state in ("open", "locked"):
        vault_text += "It uses strong encryption. "
    vault_text += _chat_line(state, inbox_ready(live))
    brain_text = (_model_line(model_url) + " " + _SAVED_CHATS + " "
                  + _DOCUMENTS)
    bridge_head = ("The part that talks to outside services on the "
                   "internet. Each one has its own switch on \"What leaves "
                   "your computer\", and you can switch any of them off.")
    bridge_lines = _services_lines(svc)
    if rich:
        bridge = "\n".join(f"  {line}" for line in bridge_lines)
        return (
            "Your bot has three parts:\n\n"
            f"- **The Vault**: {vault_text}\n\n"
            f"- **The Brain**: {brain_text}\n\n"
            f"- **The Bridge**: {bridge_head}\n{bridge}"
        )
    return ("Your bot has three parts. The Vault: " + vault_text
            + " The Brain: " + brain_text + " The Bridge: " + bridge_head
            + " " + " ".join(bridge_lines))


def where_am_i_answer(*, rich: bool, vault: Any = _LIVE,
                      model_url: str = "") -> str:
    """The answer to "which part am I in right now"."""
    live = _live_vault() if vault == _LIVE else vault
    state = vault_state(live)
    body = (_model_line(model_url) + " " + _SAVED_CHATS + " The Vault holds "
            "your private stuff. " + _vault_line(state))
    if rich:
        return "Right now you are talking to **the Brain**, the part that thinks.\n\n" + body
    return "Right now you are talking to the Brain, the part that thinks. " + body


__all__ = ["SERVICE_NAMES", "vault_state", "waiting_count", "open_services",
           "model_is_local", "plain_summary", "proposal_items",
           "privacy_answer", "where_am_i_answer"]
