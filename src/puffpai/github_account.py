"""Her GitHub account: connect it once, and BuildBot reads with it.

Ask whether she has a GitHub as soon as it makes
sense in the wizard, let her connect it there (and on the Developer page), so
searches are better and BuildBot can even pull from her own projects.

What connecting does
--------------------
* Her token goes ONLY into the vault, as the curated sign-up key ``github``
  (:mod:`puffpai.security.keys`, row marked ``vault_only``: there is never a
  copy for while the vault is locked). Nothing here keeps it: not a file, not
  a log line, not an error, not a cache key, not an address.
* Connecting is her yes to the "GitHub" switch on What leaves your computer
  (egress door ``github``): a ``key_on`` consent ticket is minted and spent at
  once for her paired device, the same path a fix card's tap takes, and both
  are written to the audit trail.
* The token is checked with ``GET https://api.github.com/user`` through the
  Bridge (``keys.add_key``: the egress door with a bound credential). A token
  that can also change her projects is accepted, and the status says so
  (``extra_scopes`` and :data:`WRITE_WARNING`); BuildBot only ever reads.
* The device sign-in (no token to paste) works only when a GitHub OAuth App
  client id is set (``github_oauth_client_id`` in the settings file; empty by
  default, so it is off). It needs no client secret, asks for no scope (public
  reads only), and the one-time device code stays in this process's memory.

What it is used for
-------------------
1. GitHub searches (:func:`puffpai.finding.sites.github`) carry the token only
   when she is connected, the vault is open and the GitHub switch is on
   (:func:`search_key_id`); otherwise they go unauthenticated as before. GitHub's
   rate limits are read from every answer and respected (:func:`rate_blocked`).
2. Her own projects and the ones she starred become finds in the one build
   (:func:`candidates_for`, origin "From your GitHub"), through the same
   fits / classify / Drawbridge path as any find. A private project is a find
   only when her token can read it, and pulling it needs its own setup row
   ("Use your private project <name>") that the build's one Yes covers
   (:func:`pull_private`). It is downloaded through GitHub's signed-in archive
   address, the token riding only that first request inside the security
   package; the short-lived download address GitHub answers with is never
   written anywhere, and no git remote is ever made.
3. Vault locked: searches fall back to unauthenticated, the status says
   ``vault_locked``, and private projects wait.

Disconnect deletes the token from the vault (``keys.remove_key``), switches
GitHub off, and forgets her profile and project list. Nothing is revoked on
GitHub's side (that needs a client secret BuildBot does not have); the answer
says where to revoke it on github.com.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
from typing import Any, Iterator, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

KEY_ID = "github"
DOOR = "github"
#: The ledger and switch id of her account's own requests (fetch_small).
SERVICE = "github"
#: Plain public reads when she is not connected (the code search switch).
PUBLIC_SERVICE = "code_search"
API = "api.github.com"
WEB = "github.com"
ARCHIVE_HOST = "codeload.github.com"
ORIGIN = "From your GitHub"

WRITE_WARNING = "This token can also change your projects; BuildBot only reads."
NOT_PAIRED = "This has to be done from a browser that is paired with your bot."
NO_DEVICE = ("Signing in without a token is not set up on this computer. Make a "
             "token on github.com instead; the steps are on this page.")
LOCKED = "Your vault is locked. Open it on the Vault page, then try again."
NO_VAULT = "Connecting GitHub needs your vault. Set your vault PIN first."
SCOPES_UNKNOWN = ("GitHub does not say what this kind of token can do. Give it "
                  "read-only permissions on github.com; BuildBot only reads.")

#: Classic and OAuth scopes that only read. Anything else can change something.
READ_ONLY_SCOPES = frozenset({
    "read:user", "user:email", "user:follow", "read:org", "read:public_key",
    "read:gpg_key", "read:ssh_signing_key", "read:packages", "read:project",
    "read:discussion", "read:audit_log", "read:enterprise", "read:repo_hook",
})
#: Where she revokes it on github.com, by how it was connected.
REVOKE_URL = {"token": "https://github.com/settings/personal-access-tokens",
              "device": "https://github.com/settings/applications"}
#: The token creation page, filled in where GitHub supports it.
TOKEN_PAGE = "https://github.com/settings/personal-access-tokens/new"
TOKEN_PREFILL = (("name", "BuildBot"),
                 ("description", "Lets BuildBot search GitHub and read your projects"),
                 ("expires_in", "90"),
                 ("contents", "read"),
                 ("metadata", "read"))

#: At most this many of her projects join one build's finds (best fit first).
MINE_PER_BUILD = 12
OWN_PROJECT = "Your own project"
REPO_PAGES = 3
STARRED_PAGES = 2
PER_PAGE = 100
LIST_MAX_BYTES = 2 * 1024 * 1024
SMALL_MAX_BYTES = 256 * 1024
#: Her project list is read again on a build when older than this.
REPOS_FRESH_S = 600

_FULL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
_LOGIN = re.compile(r"^[A-Za-z0-9-]{1,39}\Z")
_COMMIT = re.compile(r"^[0-9a-f]{40}\Z")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}\Z")
_USER_CODE = re.compile(r"^[A-Z0-9-]{4,16}\Z")
_SCOPE = re.compile(r"^[a-z_:]{1,40}\Z")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset({"the", "and", "for", "with", "from", "that", "this", "your", "you",
                   "app", "bot", "tool", "make", "show", "get", "use", "about", "into",
                   "what", "when", "every", "day", "all", "new", "can", "will", "her",
                   "his", "its", "our", "out", "how", "who", "are", "not"})

_lock = threading.RLock()
#: The one device sign-in waiting, in memory only (never written to disk).
_pending: dict[str, Any] = {}
#: The private projects the build's Yes lets this thread pull right now.
_PULLING: contextvars.ContextVar[frozenset] = contextvars.ContextVar(
    "puffpai_github_private_pull", default=frozenset())


class GitHubError(Exception):
    """Something could not be done. ``message`` is plain words for her;
    ``status`` is the HTTP status a route answers with."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ── small state (never the token) ──────────────────────────────────────────

def _dir() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(create=True), "github")


def _path(name: str) -> str:
    return os.path.join(_dir(), name)


def _read(name: str) -> dict:
    from puffpai import fsutil
    data = fsutil.read_json(_path(name), default=None)
    return data if isinstance(data, dict) else {}


def _write(name: str, data: dict) -> None:
    from puffpai import fsutil
    os.makedirs(_dir(), exist_ok=True)
    fsutil.atomic_write_json(_path(name), data)


def _drop(name: str) -> None:
    try:
        os.unlink(_path(name))
    except OSError:
        pass


def _audit(action: str, result: str, details: str = "") -> None:
    try:
        from puffpai.security.audit_log import AuditLog
        AuditLog().log(action=action, user="owner", result=result, details=details[:120])
    except Exception as exc:  # noqa: BLE001 - the ticket's own lines are kept
        logger.debug("github audit line skipped: %s", type(exc).__name__)


# ── where things stand (no network) ────────────────────────────────────────

def _vault() -> Any:
    from puffpai.security import keys
    return keys._vault()  # noqa: SLF001 - the backend's own vault


def _vault_state(v: Any = None) -> tuple[bool, bool]:
    """``(set up, open)``. Fails closed: anything unreadable is locked."""
    v = v if v is not None else _vault()
    try:
        return bool(getattr(v, "is_initialized", False)), bool(getattr(v, "is_unlocked", False))
    except Exception:  # noqa: BLE001
        return False, False


def _entry() -> dict:
    from puffpai.security import keys
    try:
        return dict(keys._read_index().get(KEY_ID) or {})  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return {}


def connected() -> bool:
    """Her token is stored and GitHub has not said it stopped working."""
    e = _entry()
    return bool(e) and not e.get("rejected_at")


def before_setup() -> bool:
    """True only while the wizard run is under way (goal saved, plan not yet
    approved: a positive signal from the run's own record) AND no run setting
    has ever been recorded here (the manifest has none and the sticky note
    ``github/setup_seen.json`` was never written). Anything unknown is "set
    up" (fail closed)."""
    try:
        from puffpai import outside_services
        if outside_services._has_posture_record():  # noqa: SLF001
            _note_setup_seen()
            return False
        if os.path.exists(_path("setup_seen.json")):
            return False
        from puffpai.wizard import service
        return bool(service.setup_in_progress())
    except Exception:  # noqa: BLE001
        return False


def _note_setup_seen() -> None:
    if not os.path.exists(_path("setup_seen.json")):
        try:
            _write("setup_seen.json", {"at": int(time.time())})
        except OSError:
            pass


def switched_on() -> bool:
    """The GitHub switch on What leaves your computer is on, under the bot's
    own run setting (the egress door). Nothing else: before the bot is set up
    her token goes out exactly once, for the check when she connects
    (keys/egress_doors.setup_check_hop), and waits after that. Fails closed."""
    try:
        from puffpai.security import egress_doors
        ok = bool(egress_doors.egress_allowed(DOOR)[0])
        if ok:
            _note_setup_seen()
        return ok
    except Exception:  # noqa: BLE001
        return False


def can_use_key() -> bool:
    """Connected, vault open, GitHub switched on: only then does her token
    leave this computer."""
    return connected() and _vault_state()[1] and switched_on()


def search_key_id() -> Optional[str]:
    """The key id a GitHub search may carry, or None (unauthenticated)."""
    return KEY_ID if can_use_key() else None


def _raw_settings() -> dict:
    try:
        from puffpai.config import get_config
        path = os.path.join(get_config().CONFIG_DIR, "puffpai_config.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def client_id() -> str:
    """The GitHub OAuth App client id for the device sign-in, or "" (off)."""
    value = _raw_settings().get("github_oauth_client_id")
    text = str(value or "").strip()
    return text if _CLIENT_ID.match(text) else ""


def device_available() -> bool:
    return bool(client_id())


def _rate_view(resource: str = "search") -> dict:
    data = _read("rate.json")
    got = data.get(resource) if isinstance(data.get(resource), dict) else {}
    return {"limit": got.get("limit"), "remaining": got.get("remaining"),
            "reset": got.get("reset")}


def refresh_rate() -> None:
    """Ask GitHub how many searches are left (``GET /rate_limit``, which
    GitHub does not count). Keyed when her account can be used."""
    keyed = can_use_key()
    got = _json(_get("/rate_limit", keyed=keyed))
    resources = got.get("resources") if isinstance(got, dict) else None
    if not isinstance(resources, dict):
        return
    with _lock:
        data = _read("rate.json")
        for name in ("search", "core"):
            r = resources.get(name)
            if isinstance(r, dict) and all(isinstance(r.get(k), int)
                                           for k in ("limit", "remaining", "reset")):
                data[name] = {"limit": r["limit"], "remaining": r["remaining"],
                              "reset": r["reset"], "at": int(time.time())}
        try:
            _write("rate.json", data)
        except OSError:
            pass


def _write_words(scopes: list[str]) -> list[str]:
    return [s for s in scopes if s not in READ_ONLY_SCOPES]


def status() -> dict:
    """Where her GitHub connection stands. Reads files only; sends nothing."""
    acct = _read("account.json")
    entry = _entry()
    set_up, is_open = _vault_state()
    is_connected = bool(entry) and not entry.get("rejected_at")
    scopes = [s for s in acct.get("scopes") or [] if isinstance(s, str) and _SCOPE.match(s)]
    extra = _write_words(scopes)
    known = bool(acct.get("scopes_known"))
    out = {
        "connected": is_connected,
        "login": str(acct.get("login") or "") if is_connected else "",
        "name": str(acct.get("name") or "") if is_connected else "",
        "avatar_url": str(acct.get("avatar_url") or "") if is_connected else "",
        "scopes": scopes if is_connected else [],
        "scopes_known": known if is_connected else False,
        "extra_scopes": extra if is_connected else [],
        "method": str(acct.get("method") or "token") if acct.get("method") in (
            "token", "device") else "token",
        "device_available": device_available(),
        "can_read_private": bool(acct.get("can_read_private")) and is_connected,
        "vault_locked": set_up and not is_open,
        "vault_set_up": set_up,
        "switched_on": switched_on(),
        "stopped_working": bool(entry.get("rejected_at")),
        # GitHub's SEARCH limit (per minute), which is what searches spend.
        "rate": _rate_view("search"),
        "rate_window": "minute",
        "warning": "",
        "line": "",
    }
    if is_connected and extra:
        out["warning"] = WRITE_WARNING
    elif is_connected and not known:
        out["warning"] = SCOPES_UNKNOWN
    if out["stopped_working"]:
        out["line"] = ("GitHub said your token is not right any more. Connect again "
                       "with a new one.")
    elif not is_connected:
        out["line"] = "Not connected." if set_up else "Not connected. No vault yet."
    elif not set_up:
        out["line"] = "No vault yet."
    elif not is_open:
        out["line"] = ("Connected, but your vault is locked: searches go without your "
                       "account until you open it.")
    elif not out["switched_on"]:
        out["line"] = "Connected, but GitHub is switched off under What leaves your computer."
    else:
        out["line"] = "Connected. BuildBot only reads."
    return out


# ── rate limits ────────────────────────────────────────────────────────────

#: How long BuildBot waits after GitHub says "slow down" without saying how long.
BACKOFF_S = 60


def note_rate(headers: Any, status: Any = None, resource_hint: str = "search") -> None:
    """Keep GitHub's rate numbers from one answer (numbers only). A 403 or
    429 that says to wait (``Retry-After``, or GitHub's secondary limit with
    no numbers) blocks that resource until then: nothing is sent meanwhile."""
    if not isinstance(headers, dict):
        return
    h = {str(k).lower(): str(v) for k, v in headers.items()}
    if status in (403, 429) and (h.get("retry-after") or "x-ratelimit-remaining" not in h):
        try:
            wait = max(1, min(int(h.get("retry-after") or BACKOFF_S), 3600))
        except ValueError:
            wait = BACKOFF_S
        resource = h.get("x-ratelimit-resource") or resource_hint
        if re.match(r"^[a-z_]{1,40}\Z", resource):
            with _lock:
                data = _read("rate.json")
                data[resource] = {"limit": 0, "remaining": 0, "reset": int(time.time()) + wait,
                                  "at": int(time.time())}
                try:
                    _write("rate.json", data)
                except OSError:
                    pass
        return
    try:
        limit = int(h["x-ratelimit-limit"])
        remaining = int(h["x-ratelimit-remaining"])
        reset = int(h.get("x-ratelimit-reset") or 0)
    except (KeyError, ValueError):
        return
    resource = h.get("x-ratelimit-resource") or "core"
    if not re.match(r"^[a-z_]{1,40}\Z", resource):
        return
    with _lock:
        data = _read("rate.json")
        data[resource] = {"limit": limit, "remaining": remaining, "reset": reset,
                          "at": int(time.time())}
        try:
            _write("rate.json", data)
        except OSError:
            pass


def rate_blocked(resource: str = "core", *, now: Optional[float] = None) -> bool:
    """True when GitHub said no requests are left until a time still ahead."""
    got = _read("rate.json").get(resource)
    if not isinstance(got, dict):
        return False
    clock = time.time() if now is None else now
    return got.get("remaining") == 0 and float(got.get("reset") or 0) > clock


# ── the one way this module asks GitHub ────────────────────────────────────

def _get(path_and_query: str, *, keyed: bool, accept: str = "application/vnd.github+json",
         max_bytes: int = SMALL_MAX_BYTES) -> dict:
    """One GET to api.github.com. Keyed: her token on this request only,
    through the security package (``fetch_small(key_id=...)``), under the
    GitHub switch. Not keyed: a plain public read under the code search
    switch. ``{"status", "body", "headers", "reason"}``; never raises."""
    from puffpai import source_fetch
    url = f"https://{API}{path_and_query}"
    if keyed:
        if not can_use_key():
            return {"status": None, "body": None, "headers": {}, "reason": "key_unavailable"}
        if rate_blocked("core"):
            return {"status": None, "body": None, "headers": {}, "reason": "rate_limited"}
        got = source_fetch.fetch_small(url, allowed_hosts={API}, max_bytes=max_bytes,
                                       service=SERVICE, honour_robots=False,
                                       headers={"accept": accept}, key_id=KEY_ID)
    else:
        got = source_fetch.fetch_small(url, allowed_hosts={API}, max_bytes=max_bytes,
                                       service=PUBLIC_SERVICE, honour_robots=False,
                                       headers={"accept": accept})
    got = dict(got or {})
    note_rate(got.get("headers"))
    return got


def _json(got: dict) -> Any:
    if got.get("status") != 200 or not got.get("body"):
        return None
    try:
        return json.loads(bytes(got["body"]).decode("utf-8", errors="replace"))
    except ValueError:
        return None


def _scopes_of(headers: Any) -> tuple[list[str], bool]:
    """``(scopes, known)`` from ``X-OAuth-Scopes``. A fine-grained token sends
    no such header: then nothing is known."""
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()} \
        if isinstance(headers, dict) else {}
    if "x-oauth-scopes" not in h:
        return [], False
    scopes = [s.strip() for s in h["x-oauth-scopes"].split(",") if s.strip()]
    return [s for s in scopes if _SCOPE.match(s)][:40], True


def _plain(value: Any, limit: int) -> str:
    from puffpai.finding import sites
    return sites.plain(value, limit)


def _avatar(value: Any) -> str:
    text = str(value or "")
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme == "https" and (parts.hostname or "").endswith(".githubusercontent.com") \
            and not parts.username and len(text) <= 300:
        return text
    return ""


def refresh_profile(method: str = "") -> dict:
    """Read her login, name, picture and what the token may do; keep them
    (never the token). Sends at most two keyed requests."""
    got = _get("/user", keyed=True)
    data = _json(got)
    if not isinstance(data, dict) or not _LOGIN.match(str(data.get("login") or "")):
        return {}
    scopes, known = _scopes_of(got.get("headers"))
    private = "repo" in scopes
    if not private:
        # A fine-grained token (or the device sign-in) says nothing about
        # itself: ask for one private project it can see.
        probe = _json(_get("/user/repos?visibility=private&per_page=1", keyed=True))
        private = isinstance(probe, list) and any(
            isinstance(r, dict) and r.get("private") is True for r in probe)
    acct = _read("account.json")
    acct.update({"login": str(data["login"]), "name": _plain(data.get("name"), 80),
                 "avatar_url": _avatar(data.get("avatar_url")), "scopes": scopes,
                 "scopes_known": known, "can_read_private": bool(private),
                 "checked_at": int(time.time())})
    if method in ("token", "device"):
        acct["method"] = method
        acct["connected_at"] = int(time.time())
    acct.pop("partial", None)
    _write("account.json", acct)
    return acct


# ── connecting ─────────────────────────────────────────────────────────────

def _need_device(device: str) -> None:
    if not str(device or "").startswith(("device:", "terminal:")):
        raise GitHubError("not_paired", NOT_PAIRED, 403)


def _need_open_vault() -> Any:
    v = _vault()
    set_up, is_open = _vault_state(v)
    if not set_up:
        try:
            from puffpai.security.vault import missing_words
            missing = missing_words(v)
        except Exception:  # noqa: BLE001
            missing = ""
        raise GitHubError("no_vault", missing or NO_VAULT, 409)
    if not is_open:
        raise GitHubError("locked", LOCKED, 409)
    return v


def _consent(device: str, target: str) -> None:
    """Her tap on Connect is her yes to the GitHub switch: the server's own
    sentence, a ``key_on`` ticket for this device and this target, minted and
    spent at once (both audited), as a fix card does."""
    from puffpai.security import consent_tickets, keys
    try:
        sentence = keys.confirm_sentence(KEY_ID)
    except keys.KeyRefused as exc:
        raise GitHubError(exc.code, exc.message, 409) from None
    try:
        ticket = consent_tickets.mint("key_on", target, sentence, device)
    except consent_tickets.TicketRefused as exc:
        raise GitHubError(exc.code, exc.message,
                          429 if exc.code == "throttled" else 403) from None
    ok, verdict = consent_tickets.redeem(ticket, device, "key_on", target)
    if not ok:
        raise GitHubError("ticket", consent_tickets.refusal_words(verdict), 403)


def _store(token: str, *, v: Any, method: str) -> dict:
    """Check the token with GitHub and lock it in the vault (keys.add_key),
    then read her profile. Never echoes or logs the token."""
    from puffpai.security import keys
    try:
        added = keys.add_key(KEY_ID, token, vault=v, background=False, by="owner")
    except keys.KeyRefused as exc:
        http = {"rejected": 400, "shape": 400, "busy": 429, "unreachable": 502,
                "locked": 409, "no_vault": 409, "cannot": 409,
                "switched_off": 409}.get(exc.code, 400)
        raise GitHubError(exc.code, exc.message, http) from None
    checked = added.get("checked") if isinstance(added, dict) else None
    if isinstance(checked, dict):
        # Before the bot is set up: the one check request's own answer is her
        # profile; nothing else is asked until the run setting allows it.
        _profile_from(checked, method)
    else:
        refresh_profile(method)
        refresh_rate()
    _audit("github_connect", "connected", method)
    return status()


def _profile_from(checked: dict, method: str) -> None:
    data = checked.get("user") if isinstance(checked.get("user"), dict) else {}
    if not _LOGIN.match(str(data.get("login") or "")):
        return
    scopes, known = _scopes_of(checked.get("headers"))
    acct = {"login": str(data["login"]), "name": _plain(data.get("name"), 80),
            "avatar_url": _avatar(data.get("avatar_url")), "scopes": scopes,
            "scopes_known": known, "can_read_private": "repo" in scopes,
            "method": method if method in ("token", "device") else "token",
            "connected_at": int(time.time()), "partial": True}
    _write("account.json", acct)


def connect_token(token: Any, device: str) -> dict:
    """Connect with a pasted token. Returns :func:`status`."""
    from puffpai.security import keys
    _need_device(device)
    try:
        keys.check_shape(KEY_ID, token)  # nothing is sent if this fails
    except keys.KeyRefused as exc:
        raise GitHubError(exc.code, exc.message, 400 if exc.code != "unknown" else 404) from None
    v = _need_open_vault()
    _consent(device, KEY_ID)
    try:
        return _store(str(token).strip(), v=v, method="token")
    finally:
        token = None


def device_start(device: str) -> dict:
    """Start the device sign-in: ``{user_code, verification_uri, interval,
    expires_in}``. Only with a configured client id."""
    from puffpai import outside_services
    from puffpai.security import egress_doors
    _need_device(device)
    cid = client_id()
    if not cid:
        raise GitHubError("not_available", NO_DEVICE, 404)
    _need_open_vault()
    _consent(device, f"{KEY_ID}:device")
    was_on = switched_on()
    if not was_on:
        try:
            outside_services.switch_on(DOOR, by="owner")
        except outside_services.OutsideError as exc:
            raise GitHubError(exc.code, exc.message, 409) from None
    try:
        resp = egress_doors.egress_request(
            DOOR, "POST", f"https://{WEB}/login/device/code",
            data={"client_id": cid, "scope": ""}, headers={"Accept": "application/json"})
        try:
            data = resp.json() if int(resp.status_code) == 200 else None
        finally:
            resp.close()
    except egress_doors.EgressBlocked as blocked:
        _quiet_off(was_on)
        raise GitHubError("switched_off", blocked.user_message, 409) from None
    except Exception as exc:  # noqa: BLE001 - transport or a bad answer
        _quiet_off(was_on)
        logger.info("github device sign-in did not start (%s)", type(exc).__name__)
        raise GitHubError("unreachable", "GitHub could not be reached. Try again later.",
                          502) from None
    if not isinstance(data, dict):
        _quiet_off(was_on)
        raise GitHubError("unreachable", "GitHub gave an answer BuildBot did not expect.", 502)
    code = str(data.get("device_code") or "")
    user_code = str(data.get("user_code") or "")
    uri = str(data.get("verification_uri") or "")
    try:
        interval = max(5, min(int(data.get("interval") or 5), 60))
        expires = max(60, min(int(data.get("expires_in") or 900), 1800))
    except (TypeError, ValueError):
        interval, expires = 5, 900
    try:
        uri_ok = (urllib.parse.urlsplit(uri).scheme == "https"
                  and urllib.parse.urlsplit(uri).hostname == WEB)
    except ValueError:
        uri_ok = False
    if not (0 < len(code) <= 200) or not _USER_CODE.match(user_code) or not uri_ok:
        _quiet_off(was_on)
        raise GitHubError("unreachable", "GitHub gave an answer BuildBot did not expect.", 502)
    now = time.monotonic()
    with _lock:
        _pending.clear()
        _pending.update({"code": code, "device": device, "interval": interval,
                         "expires_at": now + expires, "next_at": now + interval,
                         "was_on": was_on})
    _audit("github_device_start", "started")
    return {"user_code": user_code, "verification_uri": uri, "interval": interval,
            "expires_in": expires}


def _quiet_off(was_on: bool) -> None:
    if was_on:
        return
    try:
        from puffpai import outside_services
        outside_services.switch_off(DOOR, by="owner")
    except Exception as exc:  # noqa: BLE001
        logger.debug("github switch not put back off: %s", type(exc).__name__)


def device_poll(device: str) -> dict:
    """Ask GitHub once whether she finished. ``{"pending": True, "interval"}``
    while waiting (asked no faster than GitHub allows), else :func:`status`."""
    from puffpai.security import egress_doors
    _need_device(device)
    with _lock:
        flow = dict(_pending)
    if not flow:
        raise GitHubError("none", "There is no sign-in waiting. Start again.", 404)
    if flow["device"] != device:
        raise GitHubError("not_paired", "That sign-in was started on another device.", 403)
    now = time.monotonic()
    if now >= flow["expires_at"]:
        _end_flow(flow)
        raise GitHubError("expired", "The sign-in code ran out. Start again.", 410)
    if now < flow["next_at"]:
        return {"pending": True, "interval": flow["interval"]}
    with _lock:
        _pending["next_at"] = now + flow["interval"]
    try:
        resp = egress_doors.egress_request(
            DOOR, "POST", f"https://{WEB}/login/oauth/access_token",
            data={"client_id": client_id(), "device_code": flow["code"],
                  "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
            headers={"Accept": "application/json"})
        try:
            data = resp.json() if int(resp.status_code) == 200 else None
        finally:
            resp.close()
    except egress_doors.EgressBlocked as blocked:
        raise GitHubError("switched_off", blocked.user_message, 409) from None
    except Exception as exc:  # noqa: BLE001
        logger.info("github device sign-in poll failed (%s)", type(exc).__name__)
        return {"pending": True, "interval": flow["interval"]}
    if not isinstance(data, dict):
        return {"pending": True, "interval": flow["interval"]}
    error = str(data.get("error") or "")
    if error == "authorization_pending":
        return {"pending": True, "interval": flow["interval"]}
    if error == "slow_down":
        with _lock:
            _pending["interval"] = min(flow["interval"] + 5, 60)
            _pending["next_at"] = time.monotonic() + _pending["interval"]
        return {"pending": True, "interval": _pending["interval"]}
    if error in ("expired_token", "token_expired"):
        _end_flow(flow)
        raise GitHubError("expired", "The sign-in code ran out. Start again.", 410)
    if error == "access_denied":
        _end_flow(flow)
        raise GitHubError("denied", "GitHub says the sign-in was cancelled.", 400)
    token = data.get("access_token")
    data = None
    if error or not isinstance(token, str) or not token:
        _end_flow(flow)
        raise GitHubError("unreachable", "GitHub gave an answer BuildBot did not expect.", 502)
    with _lock:
        _pending.clear()
    try:
        v = _need_open_vault()
        return _store(token, v=v, method="device")
    except GitHubError:
        _quiet_off(flow.get("was_on", True))
        raise
    finally:
        token = None


def _end_flow(flow: dict) -> None:
    with _lock:
        _pending.clear()
    _quiet_off(flow.get("was_on", True))


def disconnect(device: str) -> dict:
    """Delete the token from the vault, switch GitHub off, forget her profile
    and project list. Says where to revoke it on github.com."""
    from puffpai.security import keys
    _need_device(device)
    method = str(_read("account.json").get("method") or "token")
    method = method if method in REVOKE_URL else "token"
    v = _vault()
    try:
        keys.remove_key(KEY_ID, vault=v, by="owner")
    except keys.KeyRefused as exc:
        raise GitHubError(exc.code, exc.message, 409) from None
    try:
        keys.purge_removed(v)
    except Exception as exc:  # noqa: BLE001 - cleared the next time it is open
        logger.debug("github record purge later: %s", type(exc).__name__)
    with _lock:
        _pending.clear()
    for name in ("account.json", "repos.json", "rate.json"):
        _drop(name)
    _audit("github_disconnect", "removed", method)
    if method == "device":
        how = ("To stop it on GitHub's side too, open the link and revoke BuildBot "
               "under Authorized OAuth Apps.")
    else:
        how = "To stop the token working anywhere, open the link and delete the token there."
    _, is_open = _vault_state(v)
    line = "Disconnected. Your token is deleted from your vault." if is_open else (
        "Disconnected. Nothing can use your token now; it is deleted from your vault "
        "the moment you next open it.")
    return {"ok": True, "line": f"{line} {how}", "revoke_url": REVOKE_URL[method],
            "status": status()}


def token_help() -> dict:
    """How to make a read-only token: a pre-filled creation link and steps."""
    url = TOKEN_PAGE + "?" + urllib.parse.urlencode(TOKEN_PREFILL)
    return {
        "url": url,
        "steps": [
            "Open the link and sign in to GitHub. A free account is enough.",
            "The token is already named BuildBot and set to read only. Keep it that way.",
            "Under Repository access, Public repositories is enough for better searches. "
            "To let BuildBot use a private project, pick it there.",
            "Press Generate token, copy it, and paste it here. It goes straight into "
            "your vault.",
        ],
        "revoke_url": REVOKE_URL["token"],
    }


# ── her projects ───────────────────────────────────────────────────────────

def _slim(item: Any, from_: str) -> Optional[dict]:
    if not isinstance(item, dict) or not _FULL_NAME.match(str(item.get("full_name") or "")):
        return None
    from puffpai import artifact_review
    desc = str(item.get("description") or "")
    if artifact_review.reads_like_orders(desc[:2000]) or \
            artifact_review.reads_like_orders(str(item.get("name") or "")):
        return None          # reads like orders to a model: never a find
    lic = item.get("license") if isinstance(item.get("license"), dict) else {}
    topics = [t for t in item.get("topics") or [] if isinstance(t, str)
              and re.match(r"^[a-z0-9-]{1,50}\Z", t)][:20]
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    return {"full_name": str(item["full_name"]), "name": _plain(item.get("name"), 100),
            "owner": str(owner.get("login") or "")[:39],
            "description": _plain(desc, 300), "topics": topics,
            "language": _plain(item.get("language"), 30),
            "private": item.get("private") is True, "fork": item.get("fork") is True,
            "archived": item.get("archived") is True,
            "default_branch": str(item.get("default_branch") or "")[:100],
            "license": str(lic.get("spdx_id") or "")[:60],
            "stars": item.get("stargazers_count") if isinstance(
                item.get("stargazers_count"), int) else 0,
            "pushed_at": str(item.get("pushed_at") or "")[:25], "from": from_}


def refresh_repos() -> list[dict]:
    """Read her own projects and the ones she starred (keyed), keep the list
    (names and descriptions only). Returns the kept list; on any failure the
    previous list is kept as it was."""
    if not can_use_key():
        return list(_read("repos.json").get("repos") or [])
    if _read("account.json").get("partial"):
        refresh_profile()            # made before setup: read it fully now
    acct = _read("account.json")
    login = str(acct.get("login") or "")
    seen: dict[str, dict] = {}
    ok = False
    for page in range(1, REPO_PAGES + 1):
        got = _json(_get(f"/user/repos?affiliation=owner&sort=updated&per_page={PER_PAGE}"
                         f"&page={page}", keyed=True, max_bytes=LIST_MAX_BYTES))
        if not isinstance(got, list):
            break
        ok = True
        for item in got:
            slim = _slim(item, "mine")
            if slim:
                seen.setdefault(slim["full_name"].lower(), slim)
        if len(got) < PER_PAGE:
            break
    if _LOGIN.match(login):
        for page in range(1, STARRED_PAGES + 1):
            got = _json(_get(f"/users/{login}/starred?per_page={PER_PAGE}&page={page}",
                             keyed=True, max_bytes=LIST_MAX_BYTES))
            if not isinstance(got, list):
                break
            ok = True
            for item in got:
                slim = _slim(item, "starred")
                if slim:
                    seen.setdefault(slim["full_name"].lower(), slim)
            if len(got) < PER_PAGE:
                break
    if not ok:
        return list(_read("repos.json").get("repos") or [])
    repos = list(seen.values())
    _write("repos.json", {"login": login, "at": int(time.time()), "repos": repos})
    return repos


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(str(text or "").lower()) if len(w) >= 3 and w not in _STOP}


def candidates_for(jobs: list[dict], *, refresh: bool = True) -> list[dict]:
    """Her projects (and starred ones) that share a word with a job, as build
    candidates marked "From your GitHub". Only projects that run as an app
    (the same type reading as any GitHub find). A private one only when her
    token can read it now. Never raises; [] when she is not connected."""
    try:
        return _candidates_for(jobs, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - her list failing never stops a build
        logger.info("github: her projects not read (%s)", type(exc).__name__)
        return []


def _item_of(r: dict) -> dict:
    """Her kept project as the search answer item the find readers use."""
    full = str(r["full_name"])
    return {"name": r.get("name") or full.split("/", 1)[1], "full_name": full,
            "owner": {"login": r.get("owner") or full.split("/", 1)[0]},
            "private": bool(r.get("private")), "description": r.get("description") or "",
            "html_url": f"https://github.com/{full}", "homepage": None,
            "stargazers_count": int(r.get("stars") or 0),
            "pushed_at": r.get("pushed_at") or None, "archived": bool(r.get("archived")),
            "fork": bool(r.get("fork")),
            "license": {"spdx_id": r.get("license")} if r.get("license") else None,
            "size": None, "language": r.get("language") or None,
            "topics": list(r.get("topics") or []),
            "default_branch": r.get("default_branch") or None}


def _candidates_for(jobs: list[dict], *, refresh: bool) -> list[dict]:
    if not connected():
        return []
    from puffpai.build import find as build_find
    from puffpai.build.classify import APP_TYPES
    from puffpai.finding import find as finding_find
    from puffpai.finding import sites
    usable = can_use_key()
    kept = _read("repos.json")
    fresh = time.time() - float(kept.get("at") or 0) < REPOS_FRESH_S
    repos = refresh_repos() if (refresh and usable and not fresh) else \
        list(kept.get("repos") or [])
    readable = usable and bool(_read("account.json").get("can_read_private"))
    want: set[str] = set()
    for j in jobs or []:
        if isinstance(j, dict):
            want |= _words(" ".join([str(j.get("name") or ""), str(j.get("does") or ""),
                                     " ".join(str(s) for s in j.get("subject") or [])]))
    scored = []
    for r in repos:
        if not isinstance(r, dict) or not _FULL_NAME.match(str(r.get("full_name") or "")):
            continue
        if r.get("private") and not readable:
            continue
        if r.get("archived"):
            continue
        type_ = sites.repo_type(str(r.get("name") or ""), str(r.get("description") or ""),
                                r.get("topics") or [], r.get("language") or None)
        if r.get("private") and type_ not in APP_TYPES:
            # Only an app has a signed-in download path (pull_private); a
            # private skill or library would be fetched without her account.
            continue
        have = _words(" ".join([re.sub(r"[-_.]", " ", str(r.get("name") or "")),
                                str(r.get("description") or ""),
                                " ".join(r.get("topics") or [])]))
        overlap = len(want & have)
        if overlap:
            scored.append((overlap, int(r.get("stars") or 0), r, type_))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    out, public = [], []
    for rank, (_o, _s, r, type_) in enumerate(scored[:MINE_PER_BUILD]):
        full = r["full_name"]
        own = is_own(r.get("owner") or full.split("/", 1)[0])
        lic = r.get("license") or ""
        if own:
            can, why = ("look", "") if type_ in ("skill", "mcp") else ("integrate", "")
        else:
            can, why = sites._code_can(type_, lic)  # noqa: SLF001 - the licence rule
        f = sites.make(site="github", type_=type_, id=f"github:{full}",
                       name=r.get("name") or full, by=r.get("owner") or "",
                       what=r.get("description") or "", url=f"https://github.com/{full}",
                       popularity={"stars": int(r.get("stars") or 0)},
                       changed=r.get("pushed_at"), licence=lic, can=can, look_why=why,
                       rank=rank, raw={"item": _item_of(r)})
        if not r.get("private"):
            public.append(f)      # kept so install reads it like any find
        c = build_find.from_find(f)
        c.update({"origin": ORIGIN, "from_your_github": True,
                  "private": bool(r.get("private")), "full_name": full,
                  "mine": r.get("from") == "mine", "own_project": own})
        if own:
            c["origin_words"] = OWN_PROJECT
        out.append(c)
    if public:
        try:
            finding_find.remember(public)
        except Exception as exc:  # noqa: BLE001 - still listed; install looks it up again
            logger.info("github: her finds not kept (%s)", type(exc).__name__)
    return out


def is_own(owner: Any) -> bool:
    """Is *owner* (a GitHub login from GitHub's own answer) the account she
    connected? Fails closed: not connected, no login kept, or a login that is
    not plain is "no"."""
    login = str(_read("account.json").get("login") or "")
    who = str(owner or "")
    return connected() and bool(_LOGIN.match(login)) and bool(_LOGIN.match(who)) \
        and login.lower() == who.lower()


def is_hers(c: Any) -> bool:
    return isinstance(c, dict) and c.get("origin") == ORIGIN \
        and _FULL_NAME.match(str(c.get("full_name") or "")) is not None


def _pin(full: str, branch: str, *, keyed: bool) -> str:
    got = _get(f"/repos/{full}/commits/{urllib.parse.quote(branch, safe='')}", keyed=keyed,
               accept="application/vnd.github.sha", max_bytes=200)
    body = bytes(got.get("body") or b"").decode("ascii", errors="replace").strip().lower() \
        if got.get("status") == 200 else ""
    return body if _COMMIT.match(body) else ""


def register_found(c: dict) -> str:
    """Make the ``found/v1`` record the integrate path reads for one of her
    projects, pinned at its current commit; the item id, or "" when it cannot
    be read. A private project needs her token now (keyed reads only)."""
    if not is_hers(c):
        return ""
    full = str(c["full_name"])
    keyed = can_use_key()
    if c.get("private") and not keyed:
        return ""
    data = _json(_get(f"/repos/{full}", keyed=keyed))
    if not isinstance(data, dict) or str(data.get("full_name") or "").lower() != full.lower():
        return ""
    private = data.get("private") is True
    if private and not keyed:
        return ""
    from puffpai.catalogs.searchers import github as G
    found = G.found_from_item(dict(data, private=False), part="code-app")
    if not isinstance(found, dict) or not isinstance(found.get("repo"), dict):
        return ""
    branch = str(found["repo"].get("default_branch") or "")
    sha = _pin(full, branch, keyed=keyed) if branch else ""
    if not sha:
        return ""
    found["repo"].update(pinned_commit=sha, pinned_at=time.strftime("%Y-%m-%d"),
                         private=private)
    found["origin_label"] = ORIGIN
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    # Her own project (GitHub says its owner is the account she connected):
    # the licence rule is waived for it (integrate.apply); Drawbridge still
    # checks the code. A project she only starred keeps the licence rule.
    found["own_project"] = is_own(owner.get("login"))
    item_id = "ghmine:" + hashlib.sha256(full.lower().encode()).hexdigest()[:16]
    from puffpai.wizard import find_parts
    with find_parts._LOCK:  # noqa: SLF001 - the one store the integrate path reads
        find_parts._FOUND[item_id] = found  # noqa: SLF001
    return item_id


@contextlib.contextmanager
def private_pull(full_name: str) -> Iterator[None]:
    """Within this block (this thread only), the private project *full_name*
    may be downloaded: the build's Yes covered its setup row."""
    token = _PULLING.set(_PULLING.get() | {str(full_name).lower()})
    try:
        yield
    finally:
        _PULLING.reset(token)


def private_archive(found: dict) -> tuple[str, set, str]:
    """``(archive address, hosts, key id)`` for a private project of hers, at
    its pinned commit. Raises :class:`GitHubError` unless the build's Yes let
    this thread pull it (:func:`private_pull`) and her token can be used now.
    The address names the commit only; the token never goes in it."""
    repo = found.get("repo") if isinstance(found, dict) and isinstance(
        found.get("repo"), dict) else {}
    owner, name = str(repo.get("owner") or ""), str(repo.get("name") or "")
    full = f"{owner}/{name}"
    commit = str(repo.get("pinned_commit") or "").lower()
    if not _FULL_NAME.match(full) or not _COMMIT.match(commit):
        raise GitHubError("not_pinned", "That project has no pinned version.", 409)
    if full.lower() not in _PULLING.get():
        raise GitHubError("not_yes", "A private project is downloaded only after your yes "
                                     "to its row on the setup list.", 403)
    if not can_use_key():
        raise GitHubError("locked", "Your GitHub account cannot be used now (it is not "
                                    "connected, your vault is locked, or GitHub is "
                                    "switched off).", 409)
    return (f"https://{API}/repos/{owner}/{name}/tarball/{commit}", {API, ARCHIVE_HOST},
            KEY_ID)


def pull_private(c: dict, *, prepare: Any = None) -> dict:
    """After the build's Yes covered "Use your private project <name>":
    download it through her account and check it, adding nothing.
    ``{"ok": True, "prepared": {...}}`` or ``{"ok": False, "reason"}``. A
    project Drawbridge wants her to look at first is not added (fail closed)."""
    if not is_hers(c) or not c.get("private"):
        return {"ok": False, "reason": "it is not a private project of yours"}
    if not can_use_key():
        return {"ok": False, "reason": "your GitHub account cannot be used now: open your "
                                       "vault and check GitHub is connected and switched on"}
    if not _read("account.json").get("can_read_private"):
        return {"ok": False, "reason": "your GitHub token cannot read private projects"}
    item_id = register_found(c)
    if not item_id:
        return {"ok": False, "reason": "BuildBot could not read that project on your GitHub"}
    if prepare is None:
        from puffpai.integrate import apply
        prepare = apply.prepare_code_project
    name = str(c.get("name") or c["full_name"])[:80]
    with private_pull(str(c["full_name"])):
        got = prepare({"id": item_id, "label": name}, {"by": "owner"}) or {}
    if str(got.get("status") or "") != "checked":
        return {"ok": False, "reason": str(got.get("line") or "it could not be checked")}
    card = got.get("card") if isinstance(got.get("card"), dict) else {}
    from puffpai import drawbridge
    if card.get("verdict") != drawbridge.ALLOW:
        words = str(card.get("hold_words") or "Drawbridge wants you to look at it first")
        return {"ok": False, "reason": f"{name}: {words}"}
    _audit("github_private_pull", "checked", hashlib.sha256(
        str(c["full_name"]).lower().encode()).hexdigest()[:12])
    return {"ok": True, "prepared": {
        "integration_id": got.get("integration_id"), "code_sha": card.get("code_sha"),
        "host_sha": card.get("host_sha") or "", "mode": card.get("mode"),
        "lines": list(card.get("lines") or [])}}


def _reset_for_tests() -> None:
    with _lock:
        _pending.clear()


__all__ = ["DOOR", "GitHubError", "KEY_ID", "ORIGIN", "WRITE_WARNING", "can_use_key",
           "candidates_for", "client_id", "connect_token", "connected", "device_available",
           "device_poll", "device_start", "disconnect", "is_hers", "note_rate",
           "private_archive", "private_pull", "pull_private", "rate_blocked",
           "refresh_profile", "refresh_repos", "register_found", "search_key_id",
           "status", "switched_on", "token_help"]
