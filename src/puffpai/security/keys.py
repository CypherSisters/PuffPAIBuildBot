"""Sign-up keys: the broker. The key goes into the vault; the bot holds a handle.

Scope
-----
Free sign-up keys: the wizard walks the owner through getting one, the key
goes straight into the vault, and the bot only holds a handle. This covers
every sign-up key, not only the Home Assistant admin credential. The
unattended half is the HomeBotBuild format ported, not a new one: see the
``SERVICE_SECRET_FILES`` note in :mod:`puffpai.security.vault`.

What a key is here
------------------
* A curated row in ``config/catalog_seeds/keys.json`` -- DATA written by a
  person: sign-up page, header name, hosts, test address, monthly cap,
  whether sign-up asks for a payment card. The model never adds a row, never
  names a host, never chooses an address. Only curated rows get a key flow.
* The value lives ONLY in the vault: a reserved record
  ``__puffpai.key.<id>`` that every generic vault route refuses or hides, and
  that only this module may read (:func:`vault._require_key_broker`). For a
  free no-card key whose owner ticked "keep working when the vault is locked",
  a second copy lives in the ported unattended tier (``key_<id>.json``).
* Everything else -- packages, cards, the model, chat, logs, the outside
  traffic log, error text -- sees only the handle ``key:<id>``.
* A key is used ONLY through :func:`keyed_get`, which hands it to the one
  egress seam (:func:`egress_doors._bind_credential`) as a HEADER, bound to the
  hosts frozen into the record when it was stored. Query placement is refused
  in this version: a key in an address is copied into every log that sees it.

The rules
---------
1. **Nothing is sent before the shape check.** A value that does not look like
   a key never leaves the computer.
2. **A key that never passed its test call is never stored.**
3. **Card-backed keys never work while the vault is locked**, whatever any
   file says: the row's ``card_required`` and the record's copy of it are both
   checked. Free no-card keys may, per key, when the owner ticked it.
4. **401/403 stops the key.** It is marked "stopped working" after exactly one
   call and is not used again until it is replaced or checked.
5. **Local caps.** A monthly cap (Brave: 900, under its free amount) and a
   per-minute cap. An unreadable usage file stops a capped key.
6. **Switching on is the caller's consent.** Routes and the terminal redeem a
   ``key_on`` ticket (:mod:`puffpai.security.consent_tickets`) before calling
   :func:`add_key`; there is no PIN check here and never ``check_pin``. Storing
   still needs the vault OPEN, because storing writes the vault.
7. **Off is one step.** :func:`remove_key` works with the vault locked: the
   note that says the key exists is removed at once, so nothing can use it, and
   the locked record is cleared the next time the vault is open.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlsplit

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

CATALOG_SCHEMA = "puffpai_key_catalog/v1"
RECORD_SCHEMA = "puffpai_key/v1"
INDEX_SCHEMA = "puffpai_keys_index/v1"
USAGE_SCHEMA = "puffpai_keys_usage/v1"

HANDLE_PREFIX = "key:"
_RESERVED = "__puffpai.key."
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_HEADER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
_BG_FILE_RE = re.compile(r"^key_([a-z][a-z0-9_]{1,31})\.json$")
#: A tool server's headers: ``__puffpai.key.tool.<name>`` in the
#: vault, and ``key_tool_<name>.json`` when kept working while it is locked.
_TOOL = "tool."
#: A skill's own keys (``__puffpai.key.skill.<folder>``): see "Skill keys".
_SKILL = "skill."
_SKILL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SKILL_SCHEMA = "puffpai_skill_keys/v1"
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_TOOL_FILE_RE = re.compile(r"^key_tool_([a-z][a-z0-9_]{0,31})\.json$")
TOOL_SCHEMA = "puffpai_tool_secret/v1"
#: Longest header value kept (a Home Assistant token is about 180 characters).
MAX_TOOL_VALUE = 4096

#: What a pasted value may be at most/least, before any row's own shape.
MIN_VALUE = 8
MAX_VALUE = 256

#: Store/check attempts per minute, per process (a key-testing oracle guard).
STORE_PER_MINUTE = 5

#: Largest reply a keyed call hands back.
MAX_REPLY_BYTES = 1024 * 1024

_lock = threading.RLock()
_vault_provider: Optional[Callable[[], Any]] = None
_store_times: collections.deque = collections.deque(maxlen=64)
_use_times: dict[str, collections.deque] = {}
_catalog_cache: dict[str, Any] = {"path": None, "mtime": None, "rows": {}}


class KeyRefused(ValueError):
    """A key could not be stored or checked. ``message`` is plain words."""

    def __init__(self, message: str, code: str = "refused"):
        super().__init__(message)
        self.message = message
        self.code = code


class KeyUnavailable(RuntimeError):
    """A keyed call did not happen. ``message`` is plain words; ``reason`` is
    one of: missing, locked, stopped_working, paused_limit, switched_off,
    not_curated, busy, unreachable."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class KeyedReply:
    """What a keyed call hands back: never the request, never the key."""
    status: int
    body: bytes
    content_type: str
    #: The service's Retry-After answer header (seconds or a date), or "".
    retry_after: str = ""

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8", "replace"))


# ── the curated rows (data) ──────────────────────────────────────────────────

def _catalog_path() -> str:
    from puffpai.config import get_config
    return os.path.join(get_config().REPO_ROOT, "config", "catalog_seeds",
                        "keys.json")


def _registrable(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _shown(name: Any) -> str:
    """A name from the key list as a log line may show it (plain and
    key-safe, else "(not shown)"): the list may be damaged or edited."""
    try:
        from puffpai import web_search
        return web_search.log_name(name, placeholder="(not shown)", limit=64)
    except Exception:
        return "(not shown)"


#: Field names as a person reads them in a refusal line.
_FIELD_WORDS = {"door": "the outside service", "signup_url": "sign-up address",
                "how_long": "how long it takes", "cost_note": "cost note"}


def row_problem(row: Any) -> str:
    """Why a catalog row cannot be used, or '' when it can. Fails closed."""
    from puffpai.security import egress_doors as ed
    if not isinstance(row, dict):
        return "not an object"
    sid = row.get("id")
    if not isinstance(sid, str) or not _ID_RE.match(sid):
        return "bad id"
    for field in ("name", "for", "signup_url", "door", "how_long", "cost_note"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            # Plain words (this reaches the terminal): the "door" field is
            # the outside service the key is for.
            return f"{_FIELD_WORDS.get(field, field)} missing"
    for field in ("card_required", "personal_use_only"):
        if not isinstance(row.get(field), bool):
            return f"{field} is not true/false"
    if "vault_only" in row and not isinstance(row.get("vault_only"), bool):
        return "vault_only is not true/false"
    if "own_page" in row and not (isinstance(row.get("own_page"), str)
                                  and _ID_RE.match(row["own_page"])):
        return "own_page is not a plain name"
    if "store_before_setup" in row and (row.get("store_before_setup") is not True
                                        or row.get("vault_only") is not True
                                        or "check_before_setup" in row):
        return "store_before_setup needs vault_only and no check_before_setup"
    if "check_before_setup" in row:
        from puffpai.security import egress_ledger
        if row.get("check_before_setup") is not True \
                or row.get("setup_service") not in egress_ledger.R3_SERVICES:
            return "check_before_setup needs a known setup_service"
    placement = row.get("placement")
    if not isinstance(placement, dict):
        return "placement missing"
    if placement.get("in") != "header":
        # v1: header only. A key in a query string is copied into every log,
        # proxy and exception that sees the address.
        return "only header placement is allowed"
    header = placement.get("name")
    if not isinstance(header, str) or not _HEADER_RE.match(header):
        return "bad header name"
    hosts = row.get("hosts")
    if not isinstance(hosts, list) or not 1 <= len(hosts) <= 2:
        return "hosts must be 1 or 2 names"
    for host in hosts:
        if not ed.is_registrable_hostname(host):
            return f"host {_shown(host)} is not a public name"
    door = ed.door(row["door"])
    if door is None or ed.is_app_door(door) or door.dynamic_hosts:
        return "it does not name an outside service BuildBot has"
    if not set(hosts) <= set(door.hosts):
        return "hosts are not the service's own"
    try:
        signup = urlsplit(row["signup_url"])
    except ValueError:
        return "bad sign-up address"
    if signup.scheme != "https" or not signup.hostname or signup.username:
        return "sign-up address must be a plain https address"
    if _registrable(signup.hostname.lower()) not in {_registrable(h) for h in hosts}:
        return "sign-up page is not on the service's own site"
    check = row.get("check")
    if not isinstance(check, dict):
        return "check missing"
    try:
        cparts = urlsplit(str(check.get("url") or ""))
    except ValueError:
        return "bad test address"
    if cparts.scheme != "https" or (cparts.hostname or "").lower() not in hosts \
            or cparts.username or cparts.port not in (None, 443):
        return "test address must be https on one of the key's hosts"
    for name, _value in parse_qsl(cparts.query, keep_blank_values=True):
        if ed._is_credential_header(name):  # noqa: SLF001 - same package
            return "test address carries a key-shaped value"
    for field in ("ok", "rejected"):
        codes = check.get(field)
        if not isinstance(codes, list) or not codes or not all(
                isinstance(c, int) and 100 <= c <= 599 for c in codes):
            return f"check.{field} must be status numbers"
    extra = row.get("check_headers")
    if extra is not None:
        # Fixed plain headers a service needs on every call (Anthropic's API
        # version). Never a credential-shaped name, never a long value.
        if not isinstance(extra, dict) or len(extra) > 4 or not all(
                isinstance(k, str) and _HEADER_RE.match(k)
                and not ed._is_credential_header(k)  # noqa: SLF001 - same package
                and isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9._\-]{1,64}", v)
                for k, v in extra.items()):
            return "check_headers must be plain names and values"
    shape = row.get("shape")
    if shape is not None:
        try:
            re.compile(shape)
        except (re.error, TypeError):
            return "bad shape"
    cap = row.get("monthly_cap")
    if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int)
                            or cap <= 0):
        return "bad monthly cap"
    if row["card_required"] and cap is None:
        # A card on file with no local cap could cost the owner money.
        return "a key with a payment card needs a monthly cap"
    per_min = row.get("per_minute_cap")
    if per_min is not None and (isinstance(per_min, bool)
                                or not isinstance(per_min, int) or per_min <= 0):
        return "bad per-minute cap"
    steps = row.get("signup_steps")
    if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        return "signup_steps must be sentences"
    from puffpai import artifact_review
    for text in [row["name"], row["for"], row["cost_note"], *steps]:
        if artifact_review.reads_like_orders(text):
            return "text reads like an order"
    return ""


def catalog() -> dict[str, dict]:
    """``{id: row}`` of the curated rows that pass :func:`row_problem`."""
    try:
        path = _catalog_path()
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    with _lock:
        if _catalog_cache["path"] == path and _catalog_cache["mtime"] == stamp:
            return dict(_catalog_cache["rows"])
    rows: dict[str, dict] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or data.get("schema") != CATALOG_SCHEMA:
            raise ValueError("not a key list")
        for row in data.get("keys") or []:
            problem = row_problem(row)
            if problem:
                logger.warning("A sign-up key entry was skipped: %s",
                               problem if not isinstance(row, dict)
                               else f"{_shown(row.get('id'))}: {problem}")
                continue
            rows[row["id"]] = row
    except (OSError, ValueError) as exc:
        logger.warning("key list unreadable (%s); no sign-up keys offered",
                       type(exc).__name__)
        rows = {}
    with _lock:
        _catalog_cache.update({"path": path, "mtime": stamp, "rows": rows})
    return dict(rows)


def spec(service_id: Any) -> Optional[dict]:
    return catalog().get(str(service_id or ""))


def vault_only(service_id: Any) -> bool:
    """True for a key that is NEVER kept outside the vault (no copy for while
    it is locked, whatever any file says). Fails closed: a row that cannot be
    read counts as vault only."""
    row = spec(service_id)
    return True if row is None else bool(row.get("vault_only"))


def listed() -> dict[str, dict]:
    """The rows the Sign-up keys page and the build summary offer: every row
    but the ones added on a page of their own (``own_page``: GitHub is
    connected on the Developer page and in the wizard, puffpai.github_account)."""
    return {sid: row for sid, row in catalog().items() if not row.get("own_page")}


def handle(service_id: str) -> str:
    """The only thing a package, card or model ever holds: ``key:<id>``."""
    return HANDLE_PREFIX + str(service_id)


def service_of(key_handle: Any) -> str:
    text = str(key_handle or "")
    if not text.startswith(HANDLE_PREFIX):
        return ""
    sid = text[len(HANDLE_PREFIX):]
    return sid if _ID_RE.match(sid) else ""


def background_file(service_id: str) -> str:
    return f"key_{service_id}.json"


def background_file_allowed(filename: Any) -> bool:
    """The vault's unattended-tier policy: a curated FREE no-card key, or a
    tool server's headers the owner kept working while the vault is locked."""
    if _TOOL_FILE_RE.match(str(filename or "")):
        return True
    match = _BG_FILE_RE.match(str(filename or ""))
    if not match:
        return False
    row = spec(match.group(1))
    return bool(row) and row.get("card_required") is False \
        and not row.get("vault_only")


# ── where state lives ────────────────────────────────────────────────────────

def _state_path(name: str) -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(create=True), name)


def _index_path() -> str:
    return _state_path("keys_index.json")


def _wanted_path() -> str:
    return _state_path("keys_wanted.json")


def _wanted() -> set[str]:
    """Keys the owner switched on in the build summary but has not added."""
    from puffpai import fsutil
    data = fsutil.read_json(_wanted_path(), default=None)
    ids = data.get("ids") if isinstance(data, dict) else None
    return {i for i in ids or () if isinstance(i, str) and _ID_RE.match(i)}


def _remember_wanted(service_id: str) -> None:
    from puffpai import fsutil
    fsutil.atomic_write_json(_wanted_path(),
                             {"ids": sorted(_wanted() | {service_id})})


def _usage_path() -> str:
    return _state_path("keys_usage.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_index() -> dict[str, dict]:
    """``{id: entry}``. Missing = no keys; broken = no keys (nothing used)."""
    from puffpai import fsutil
    data = fsutil.read_json(_index_path(), default=None)
    if not isinstance(data, dict) or data.get("schema") != INDEX_SCHEMA:
        return {}
    keys = data.get("keys")
    if not isinstance(keys, dict):
        return {}
    return {k: dict(v) for k, v in keys.items()
            if isinstance(k, str) and _ID_RE.match(k) and isinstance(v, dict)}


def _write_index(entries: dict[str, dict]) -> None:
    from puffpai import fsutil
    fsutil.atomic_write_json(_index_path(), {"schema": INDEX_SCHEMA,
                                             "keys": entries})


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _read_usage() -> Optional[dict]:
    """The usage counts, ``{}`` when there is no file, None when broken."""
    path = _usage_path()
    if not os.path.exists(path):
        return {}
    from puffpai import fsutil
    data = fsutil.read_json(path, default=None)
    if not isinstance(data, dict) or data.get("schema") != USAGE_SCHEMA \
            or not isinstance(data.get("months"), dict):
        return None
    return data["months"]


def uses_this_month(service_id: str) -> Optional[int]:
    months = _read_usage()
    if months is None:
        return None
    count = (months.get(_month()) or {}).get(service_id, 0)
    return count if isinstance(count, int) and count >= 0 else None


def _count_use(service_id: str) -> None:
    from puffpai import fsutil
    with _lock:
        months = _read_usage()
        if months is None:
            months = {}
        month = _month()
        now = datetime.now(timezone.utc)
        previous = (f"{now.year - 1}-12" if now.month == 1
                    else f"{now.year}-{now.month - 1:02d}")
        # Only the current and previous month are kept.
        months = {m: v for m, v in months.items()
                  if m in (month, previous) and isinstance(v, dict)}
        bucket = dict(months.get(month) or {})
        bucket[service_id] = int(bucket.get(service_id, 0) or 0) + 1
        months[month] = bucket
        fsutil.atomic_write_json(_usage_path(), {"schema": USAGE_SCHEMA,
                                                 "months": months})


def _audit(action: str, caller: str, result: str, details: str) -> None:
    """One line in the keys' own audit file: never a value, a query or a body.

    The caller and the details are checked WHOLE for a key before they are
    cut (key_safe_text): cutting first could leave a piece of a key that no
    later check would catch."""
    try:
        from puffpai.security.audit_log import AuditLog
        from puffpai.web_search import key_safe_text
        AuditLog(log_file=_state_path(os.path.join("security_logs",
                                                   "keys_audit.jsonl"))
                 ).log(action=action, user=key_safe_text(caller, 60),
                       result=result, details=key_safe_text(details, 200))
    except Exception as exc:  # the audit line must never break a call
        logger.debug("keys audit skipped: %s", type(exc).__name__)


# ── the vault ────────────────────────────────────────────────────────────────

def set_vault_provider(provider: Optional[Callable[[], Any]]) -> None:
    """How this process reaches its vault (the backend's open singleton)."""
    global _vault_provider
    _vault_provider = provider


def _vault(explicit: Any = None) -> Any:
    if explicit is not None:
        return explicit
    if _vault_provider is not None:
        try:
            got = _vault_provider()
            if got is not None:
                return got
        except Exception:
            pass
    try:
        from puffpai.security.vault import Vault
        return Vault()
    except Exception:
        return None


def _is_open(vault: Any) -> bool:
    return bool(vault is not None and getattr(vault, "is_unlocked", False))


def background_available(vault: Any = None) -> bool:
    """Can this computer keep a free key working while the vault is locked?"""
    from puffpai.security import vault as vault_mod
    v = _vault(vault)
    vault_dir = getattr(v, "vault_dir", None)
    secret, _src = vault_mod.read_service_key(vault_dir)
    return secret is not None or vault_mod._usable_keyring() is not None  # noqa: SLF001


def _record_for(vault: Any, service_id: str) -> Optional[dict]:
    """The key record: from the open vault, else the unattended copy."""
    raw: Any = None
    if _is_open(vault):
        raw = vault._reserved_get(_RESERVED + service_id)  # noqa: SLF001
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = None
    elif vault is not None and not vault_only(service_id):
        # A vault-only key has no copy outside the vault: locked is locked.
        raw = vault.read_service_secret_unattended(background_file(service_id))
    if not isinstance(raw, dict) or raw.get("schema") != RECORD_SCHEMA \
            or raw.get("service_id") != service_id \
            or not isinstance(raw.get("value"), str):
        return None
    return raw


def read_for_lease(service_id: str, vault: Any = None) -> Optional[tuple]:
    """``(value, hosts, stored_at)`` of a key, read from the OPEN vault, for
    Toolsmith's build lease (:mod:`puffpai.security.toolsmith_cloud`) ONLY."""
    caller = sys._getframe(1).f_globals.get("__name__", "")
    if caller != "puffpai.security.toolsmith_cloud":
        raise PermissionError("only Toolsmith's key lease may read a key")
    v = _vault(vault)
    if not _is_open(v):
        return None
    entry = _read_index().get(service_id)
    if entry is None or entry.get("rejected_at"):
        return None
    record = _record_for(v, service_id)
    if record is None:
        return None
    try:
        return (record["value"], list(record.get("hosts") or []),
                str(entry.get("stored_at") or ""))
    finally:
        record = None


def _lease_ends(service_id: str) -> None:
    """A key was added again or removed: Toolsmith's lease on it ends now."""
    try:
        from puffpai.security import toolsmith_cloud as _tc
        if service_id == _tc.PROVIDERS["anthropic"]["key_id"]:
            _tc.drop_lease("key")
    except Exception as exc:  # noqa: BLE001 - the lease is also checked per call
        logger.debug("toolsmith lease not dropped: %s", type(exc).__name__)


def _lease_starts(service_id: str, vault: Any) -> None:
    """A new key was locked in the open vault and is on file: with cloud
    Toolsmith on, its lease starts again with the NEW key at once (any lease
    taken in between, maybe on the old key, is let go first)."""
    try:
        from puffpai.security import toolsmith_cloud as _tc
        if service_id == _tc.PROVIDERS["anthropic"]["key_id"]:
            _tc.key_saved(vault)
    except Exception as exc:  # noqa: BLE001 - a run or an unlock takes it later
        logger.debug("toolsmith lease not started: %s", type(exc).__name__)


def _pending_path() -> str:
    return _state_path("keys_remove_pending.json")


def _pending_removals() -> set[str]:
    """Keys removed while the vault was locked, whose record is still in it."""
    from puffpai import fsutil
    data = fsutil.read_json(_pending_path(), default=None)
    ids = data.get("ids") if isinstance(data, dict) else None
    return {i for i in ids or () if isinstance(i, str) and _ID_RE.match(i)}


def _write_pending(ids: set[str]) -> None:
    from puffpai import fsutil
    if ids:
        fsutil.atomic_write_json(_pending_path(), {"ids": sorted(ids)})
    else:
        try:
            os.unlink(_pending_path())
        except OSError:
            pass


def purge_pending(vault: Any = None) -> int:
    """Delete exactly the records of keys removed while the vault was locked
    (and not added again since). Needs an open vault. Never a broad sweep:
    only the ids :func:`remove_key` wrote down."""
    v = _vault(vault)
    if not _is_open(v):
        return 0
    with _lock:
        pending = _pending_removals()
        if not pending:
            return 0
        index = _read_index()
        gone = 0
        left: set[str] = set()
        for sid in sorted(pending):
            if sid in index:
                continue                 # added again since: its new record stays
            try:
                if v._reserved_delete(_RESERVED + sid):  # noqa: SLF001
                    gone += 1
            except Exception as exc:  # noqa: BLE001 - tried again next time
                logger.debug("removed key record not cleared yet: %s", type(exc).__name__)
                left.add(sid)
        _write_pending(left)
    if gone:
        _audit("key_remove", "vault_open", "cleared", f"{gone} removed while locked")
    return gone


def on_vault_opened(vault: Any) -> None:
    """The vault was just opened (unlock, create, recovery): clear the records
    of keys removed while it was locked. Never raises."""
    try:
        purge_pending(vault)
    except Exception as exc:  # noqa: BLE001 - never block opening the vault
        logger.debug("pending key removals not cleared: %s", type(exc).__name__)


def purge_removed(vault: Any = None) -> int:
    """Clear locked records whose key the owner removed. Needs an open vault."""
    v = _vault(vault)
    if not _is_open(v):
        return 0
    index = _read_index()
    gone = 0
    tools = _tool_names()
    for name in v._reserved_key_names():  # noqa: SLF001
        sid = name[len(_RESERVED):]
        if sid.startswith(_TOOL):
            if sid[len(_TOOL):] not in tools:
                v._reserved_delete(name)  # noqa: SLF001
                gone += 1
            continue
        if sid.startswith(_SKILL):
            if sid[len(_SKILL):] not in _skill_index():
                v._reserved_delete(name)  # noqa: SLF001
                gone += 1
            continue
        if sid.startswith(_APP):
            if sid[len(_APP):] not in _app_index():
                v._reserved_delete(name)  # noqa: SLF001
                gone += 1
            continue
        if sid.startswith(_PROGRAM):
            continue                # program records hold no key; kept
        if sid not in index:
            v._reserved_delete(name)  # noqa: SLF001
            gone += 1
    return gone


# ── plain words ──────────────────────────────────────────────────────────────

def _month_end_words() -> str:
    now = datetime.now(timezone.utc)
    nxt = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1)
    return f"1 {nxt.strftime('%B')}"


def walk_through(service_id: str, surface: str = "browser", *,
                 age: Any = None) -> dict:
    """The owner's steps to get a free key. Words differ by where they are read."""
    row = spec(service_id)
    if row is None:
        return {}
    name = row["name"]
    url = row["signup_url"]
    if surface == "terminal":
        last = f"run: puffpai keys add {service_id} and paste the key when it asks"
    elif surface == "chat":
        last = ("paste the key on the Sign-up keys page. Never paste a key "
                "into the chat")
    elif surface == "page":
        last = "paste the key on the Sign-up keys page"
    else:
        last = "paste the key here"
    line = (f"{name} needs a free key: 1) open {url} 2) sign up 3) {last}.")
    out = {"line": line, "signup_url": url, "steps": list(row["signup_steps"]),
           "how_long": row["how_long"], "cost_note": row["cost_note"],
           "personal_use_only": row["personal_use_only"],
           "card_required": row["card_required"]}
    try:
        young = age is not None and int(age) < 18
    except (TypeError, ValueError):
        young = False
    if young and row["card_required"]:
        out["grown_up_line"] = (f"Ask a grown-up to help with this step: "
                                f"{name} asks for a payment card.")
    if row["personal_use_only"]:
        out["label"] = "Free for personal use only."
    return out


def _state_line(state: str, row: dict, surface: str) -> str:
    name = row["name"]
    if state == "works":
        return "Works."
    if state == "missing":
        if row.get("id") in _wanted():
            return ("You chose this when you built your bot. Not added yet: "
                    "follow the steps to get the key.")
        return "Not added yet."
    if state == "locked":
        if row["card_required"]:
            return (f"Paused: your vault is locked. {name} asks for a payment "
                    f"card, so its key never works while the vault is locked.")
        return "Paused: your vault is locked."
    if state == "stopped_working":
        return (f"Stopped working: {name} said the key is not right. Get a new "
                f"one and paste it in again.")
    if state == "paused_limit":
        return (f"Paused until {_month_end_words()}: this month's searches "
                f"are used up.")
    if state == "switched_off":
        return "Switched off under What leaves your computer."
    if state == "unknown_count":
        return ("Paused: the count of this month's searches could not be "
                "read, so it is not used, to keep you under the free amount.")
    return "Not added yet."


def _door_open(row: dict) -> bool:
    from puffpai.security import egress_doors
    allowed, _why = egress_doors.egress_allowed(row["door"])
    return allowed


def _state(service_id: str, row: dict, vault: Any) -> str:
    entry = _read_index().get(service_id)
    if entry is None:
        return "missing"
    if entry.get("rejected_at"):
        return "stopped_working"
    cap = row.get("monthly_cap")
    used = uses_this_month(service_id)
    if cap is not None:
        if used is None:
            return "unknown_count"
        if used >= cap:
            return "paused_limit"
    if not _door_open(row):
        return "switched_off"
    if not _is_open(vault):
        if row["card_required"] or entry.get("card_required") is not False \
                or not entry.get("background") or row.get("vault_only"):
            return "locked"
    return "works"


def key_status(service_id: str, *, vault: Any = None,
               surface: str = "browser") -> dict:
    """One key's state. Never the value, never a vault read."""
    row = spec(service_id)
    if row is None:
        return {}
    v = _vault(vault)
    state = _state(service_id, row, v)
    entry = _read_index().get(service_id) or {}
    used = uses_this_month(service_id)
    cap = row.get("monthly_cap")
    uses_line = ""
    if used is not None and entry:
        uses_line = (f"{used} of {cap} searches used this month." if cap
                     else f"{used} used this month.")
    return {
        "id": service_id, "name": row["name"], "for": row["for"],
        "state": state, "state_line": _state_line(state, row, surface),
        "card_required": row["card_required"],
        "personal_use_only": row["personal_use_only"],
        "cost_note": row["cost_note"],
        "background": bool(entry.get("background")),
        "background_possible": (not row["card_required"]
                                and not row.get("vault_only")
                                and background_available(v)),
        "uses_this_month": used, "cap": cap, "uses_line": uses_line,
        "checked_at": entry.get("checked_at") or "",
        "walk_through": walk_through(service_id, surface),
        "handle": handle(service_id),
    }


def list_keys(*, vault: Any = None, surface: str = "browser") -> list[dict]:
    v = _vault(vault)
    try:
        purge_removed(v)
    except Exception as exc:
        logger.debug("key purge skipped: %s", type(exc).__name__)
    return [key_status(sid, vault=v, surface=surface) for sid in listed()]


# ── checks before anything is sent ───────────────────────────────────────────

def check_shape(service_id: str, value: Any) -> dict:
    """The row, if *value* looks like this service's key. Sends NOTHING."""
    row = spec(service_id)
    if row is None:
        raise KeyRefused("There is no sign-up key by that name.", "unknown")
    if not isinstance(value, str):
        raise KeyRefused("That does not look like a key. A key is one long "
                         "word of letters and numbers.", "shape")
    text = value.strip()
    if not (MIN_VALUE <= len(text) <= MAX_VALUE) or any(
            ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise KeyRefused("That does not look like a key. A key is one long "
                         "word of letters and numbers.", "shape")
    if row.get("public_test_key") and text == row["public_test_key"]:
        raise KeyRefused(f"That is {row['name']}'s shared test key. It is only "
                         f"for trying it out. Ask {row['name']} for your own "
                         f"free key.", "test_key")
    shape = row.get("shape")
    if shape and not re.fullmatch(shape, text):
        raise KeyRefused(f"That does not look like a {row['name']} key. Check "
                         f"you copied all of it.", "shape")
    return row


def _rate_limited() -> bool:
    now = time.monotonic()
    with _lock:
        while _store_times and now - _store_times[0] > 60:
            _store_times.popleft()
        if len(_store_times) >= STORE_PER_MINUTE:
            return True
        _store_times.append(now)
    return False


def _same_as_a_vault_secret(vault: Any, value: str) -> bool:
    try:
        for name in vault.list_secrets():
            if vault.retrieve(name) == value:
                return True
    except Exception:
        return False
    return False


# ── the one way a key is used ────────────────────────────────────────────────

def _send_with_key(row: dict, value: str, hosts: list, url: str, *,
                   headers: Optional[dict] = None, timeout: float = 8.0,
                   method: str = "GET", data: Optional[bytes] = None
                   ) -> KeyedReply:
    """One GET (or a POST with *data*) with the key in its header, through
    the egress seam. The row's own fixed ``check_headers`` ride every call.

    Every transport error becomes :class:`KeyUnavailable` raised OUTSIDE the
    ``except`` block, so neither ``__cause__`` nor ``__context__`` carries the
    original exception (whose request object holds the header).
    """
    from puffpai.security import egress_doors
    failed = ""
    blocked_message = ""
    reply: Optional[KeyedReply] = None
    # The row's header shape (GitHub: "Bearer {key}"), the same one the keyed
    # fetch hop uses; a shape that is not plain is refused before anything.
    fmt = (row.get("placement") or {}).get("value_format", "{key}")
    if not egress_doors._value_format_ok(fmt):  # noqa: SLF001 - same package
        raise KeyUnavailable("not_curated", "That service has no sign-up key "
                                            "this bot can use.")
    cred = egress_doors._bind_credential(  # noqa: SLF001 - the one seam
        row["placement"]["name"], fmt.replace("{key}", value), hosts,
        placement="header")
    try:
        sent = {**dict(row.get("check_headers") or {}), **dict(headers or {})}
        extra: dict = {"data": data} if data is not None else {}
        resp = egress_doors.egress_request(
            row["door"], "POST" if method == "POST" else "GET", url, credential=cred,
            timeout=timeout, headers=sent, **extra)
        try:
            body = resp.content[:MAX_REPLY_BYTES] if resp.content else b""
            ctype = str((resp.headers or {}).get("Content-Type") or "")[:120]
            after = str((resp.headers or {}).get("Retry-After") or "")[:40]
            status = int(resp.status_code)
        finally:
            try:
                resp.close()
            except Exception:
                pass
        needle = value.encode("utf-8")
        if needle and needle in body:
            # A service that echoes the key back never gets it further.
            body = body.replace(needle, b"[removed]")
        reply = KeyedReply(status, body, ctype, after)
    except egress_doors.EgressBlocked as blocked:
        blocked_message = blocked.user_message
    except Exception as exc:  # noqa: BLE001 - transport; see docstring
        failed = type(exc).__name__
    finally:
        del cred
    if blocked_message:
        raise KeyUnavailable("switched_off", blocked_message)
    if failed or reply is None:
        raise KeyUnavailable("unreachable", f"{row['name']} could not be "
                                            f"reached. Try again later.")
    return reply


def _per_minute_ok(service_id: str, row: dict) -> bool:
    cap = row.get("per_minute_cap") or 10
    now = time.monotonic()
    with _lock:
        times = _use_times.setdefault(service_id, collections.deque(maxlen=256))
        while times and now - times[0] > 60:
            times.popleft()
        if len(times) >= cap:
            return False
        times.append(now)
    return True


def keyed_get(key_handle: str, *, url: str, caller: str,
              headers: Optional[dict] = None, timeout: float = 8.0,
              vault: Any = None) -> KeyedReply:
    """``GET`` *url* with the key *key_handle* names. The ONLY way to use a key.

    Raises :class:`KeyUnavailable` (plain ``message``) when the key cannot be
    used, and never returns or raises anything that holds the key.
    """
    return _keyed(key_handle, url=url, caller=caller, headers=headers,
                  timeout=timeout, vault=vault)


def keyed_post(key_handle: str, *, url: str, caller: str, data: bytes,
               headers: Optional[dict] = None, timeout: float = 8.0,
               vault: Any = None, lease: Any = None) -> KeyedReply:
    """``POST`` *data* to *url* with the key *key_handle* names: every rule of
    :func:`keyed_get` (curated row, its hosts, caps, vault-only, 401 stops the
    key). A 429 or 5xx answer is handed back as it is (the caller retries).
    Security package only: the one caller is Toolsmith's cloud model
    (:mod:`puffpai.security.toolsmith_cloud`), which checks its own scope.
    *lease*: Toolsmith's build-scoped key lease (toolsmith_cloud), used only
    while the vault is locked, only for its own key, host and caller."""
    caller_mod = sys._getframe(1).f_globals.get("__name__", "")
    if not (caller_mod == "puffpai.security" or caller_mod.startswith("puffpai.security.")):
        raise PermissionError("keyed_post is for the security package only")
    if not isinstance(data, (bytes, bytearray)):
        raise KeyUnavailable("not_curated", "Nothing was sent.")
    return _keyed(key_handle, url=url, caller=caller, headers=headers,
                  timeout=timeout, vault=vault, method="POST", data=bytes(data),
                  lease=lease)


def _keyed(key_handle: str, *, url: str, caller: str,
           headers: Optional[dict] = None, timeout: float = 8.0,
           vault: Any = None, method: str = "GET",
           data: Optional[bytes] = None, lease: Any = None) -> KeyedReply:
    sid = service_of(key_handle)
    row = spec(sid) if sid else None
    if row is None:
        raise KeyUnavailable("not_curated", "That service has no sign-up key "
                                            "this bot can use.")
    try:
        host = (urlsplit(str(url)).hostname or "").lower().rstrip(".")
    except ValueError:
        host = ""
    entry = _read_index().get(sid)
    if entry is None:
        raise KeyUnavailable("missing", walk_through(sid, "chat")["line"])
    if entry.get("rejected_at"):
        raise KeyUnavailable("stopped_working",
                             _state_line("stopped_working", row, "chat"))
    # The host check comes before any lookup: the key only ever goes where
    # both the curated row and the stored record say.
    if host not in row["hosts"] or host not in (entry.get("hosts") or []):
        _audit("credential_use", caller, "refused", f"{sid} {host} host")
        raise KeyUnavailable("not_curated", "That address is not where this "
                                            "key may be sent, so nothing was "
                                            "sent.")
    cap = row.get("monthly_cap")
    if cap is not None:
        used = uses_this_month(sid)
        if used is None:
            raise KeyUnavailable("paused_limit",
                                 _state_line("unknown_count", row, "chat"))
        if used >= cap:
            raise KeyUnavailable("paused_limit",
                                 _state_line("paused_limit", row, "chat"))
    v = _vault(vault)
    leased: Optional[dict] = None
    if lease is not None and not _is_open(v):
        # Toolsmith's build lease (toolsmith_cloud): the one key it read while
        # the vault was open, for this build only. Checked there: the live
        # lease, Anthropic, caller toolsmith, the key still on file.
        from puffpai.security import toolsmith_cloud as _tc
        leased = _tc.lease_record(lease, sid, caller, entry.get("stored_at"))
    if not _is_open(v) and leased is None:
        # Rule 3: a card-backed key NEVER works while the vault is locked,
        # whatever the index or the unattended file says.
        if row["card_required"] or entry.get("card_required") is not False \
                or not entry.get("background") or row.get("vault_only"):
            _audit("credential_use", caller, "refused", f"{sid} {host} locked")
            raise KeyUnavailable("locked", _state_line("locked", row, "chat"))
    via_lease = leased is not None
    record = leased if via_lease else _record_for(v, sid)
    leased = None
    if record is None:
        _audit("credential_use", caller, "refused", f"{sid} {host} no record")
        raise KeyUnavailable("locked" if not _is_open(v) else "missing",
                             _state_line("locked", row, "chat")
                             if not _is_open(v)
                             else walk_through(sid, "chat")["line"])
    if record.get("card_required") is True and not _is_open(v) and not via_lease:
        raise KeyUnavailable("locked", _state_line("locked", row, "chat"))
    if host not in (record.get("hosts") or []):
        raise KeyUnavailable("not_curated", "That address is not where this "
                                            "key may be sent, so nothing was "
                                            "sent.")
    if not _per_minute_ok(sid, row):
        raise KeyUnavailable("busy", "That was a lot of searches in one "
                                     "minute. Wait a moment and ask again.")
    _count_use(sid)  # counted before sending: a lost reply still spent one
    try:
        reply = _send_with_key(row, record["value"], list(record["hosts"]), url,
                               headers=headers, timeout=timeout, method=method,
                               data=data)
    except KeyUnavailable as exc:
        _audit("credential_use", caller, "failed", f"{sid} {host} {exc.reason}")
        raise
    finally:
        record = None
    status_class = f"{reply.status // 100}xx"
    if reply.status in row["check"]["rejected"]:
        _mark_rejected(sid)
        _audit("credential_use", caller, "rejected", f"{sid} {host} {status_class}")
        raise KeyUnavailable("stopped_working",
                             _state_line("stopped_working", row, "chat"))
    _audit("credential_use", caller, "done", f"{sid} {host} {status_class}")
    return reply


def _mark_rejected(sid: str) -> None:
    with _lock:
        index = _read_index()
        if sid in index:
            index[sid]["rejected_at"] = _now_iso()
            _write_index(index)


# ── adding, checking, removing ───────────────────────────────────────────────

def _check_call(row: dict, value: str) -> str:
    """'ok', 'rejected', or a KeyUnavailable raised with plain words."""
    from puffpai import USER_AGENT
    reply = _send_with_key(row, value, list(row["hosts"]), row["check"]["url"],
                           headers={"Accept": "application/json",
                                    "User-Agent": USER_AGENT})
    if reply.status in row["check"]["ok"]:
        return "ok"
    if reply.status in row["check"]["rejected"]:
        return "rejected"
    if reply.status == 429:
        raise KeyUnavailable("busy", f"{row['name']} says there have been too "
                                     f"many tries just now. Try again in a "
                                     f"minute.")
    raise KeyUnavailable("unreachable", f"{row['name']} gave an answer this bot "
                                        f"did not expect. Try again later.")


#: The one pre-setup check's own answer (login, name, picture, scopes: never
#: the key), handed back once by add_key so nothing asks again.
_SETUP_ANSWER: dict = {}


def _check_call_before_setup(row: dict, value: str) -> str:
    """As :func:`_check_call`, before the bot is set up (no run setting yet):
    the one build-time test request (``egress_doors.setup_check_hop``)."""
    from puffpai.security import egress_doors
    status, code, answer = egress_doors.setup_check_hop(row["id"], value)
    _SETUP_ANSWER.clear()
    if isinstance(answer, dict) and answer:
        _SETUP_ANSWER.update(answer)
    if status is None:
        if code == "switched_off":
            raise KeyUnavailable("switched_off", f"{row['name']} is switched off under "
                                                 f"What leaves your computer.")
        raise KeyUnavailable("unreachable", f"{row['name']} could not be reached. Try "
                                            f"again later.")
    if status in row["check"]["ok"]:
        return "ok"
    if status in row["check"]["rejected"]:
        return "rejected"
    if status == 429:
        raise KeyUnavailable("busy", f"{row['name']} says there have been too many tries "
                                     f"just now. Try again in a minute.")
    raise KeyUnavailable("unreachable", f"{row['name']} gave an answer this bot did not "
                                        f"expect. Try again later.")


def _catalog_digest(row: dict) -> str:
    text = json.dumps({k: row[k] for k in ("hosts", "placement", "door")},
                      sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def add_key(service_id: str, value: Any, *, vault: Any = None,
            background: bool = False, by: str = "owner") -> dict:
    """Check a pasted key, then lock it in the vault. Call ONLY after the
    caller redeemed a ``key_on`` consent ticket for *service_id*.

    Returns ``{"ok", "message", "notes", "key"}``. Never returns the value.
    """
    from puffpai import outside_services
    row = check_shape(service_id, value)  # nothing sent if this fails
    text = str(value).strip()
    v = _vault(vault)
    if v is None or not getattr(v, "is_initialized", False):
        from puffpai.security.vault import missing_words
        missing = missing_words(v)
        if missing:
            raise KeyRefused(missing, "no_vault")
        raise KeyRefused("A key needs the vault. Set your vault PIN first, then "
                         "add the key.", "no_vault")
    if not _is_open(v):
        raise KeyRefused("Your vault is locked. Open it first, then paste the "
                         "key again.", "locked")
    if _same_as_a_vault_secret(v, text):
        raise KeyRefused("That is one of the things already in your vault, not "
                         "a key from the service. Nothing was sent.", "shape")
    if _rate_limited():
        raise KeyRefused("That was a lot of tries in one minute. Wait a minute "
                         "and try again.", "busy")
    notes: list[str] = []
    if background and row["card_required"]:
        background = False
        notes.append(f"{row['name']} asks for a payment card, so its key never "
                     f"works while your vault is locked.")
    if row.get("vault_only"):
        background = False          # never a copy outside the vault
    was_on = _door_open(row)
    before_setup = False
    if not was_on:
        try:
            outside_services.switch_on(row["door"], by=by)
        except outside_services.OutsideError as exc:
            under_way = False
            early = (row.get("check_before_setup") is True
                     or row.get("store_before_setup") is True)
            if exc.code == "not_set_up" and early:
                try:
                    from puffpai import github_account
                    under_way = github_account.before_setup()
                except Exception:  # noqa: BLE001 - not knowing is "set up"
                    under_way = False
            if not under_way:
                raise KeyRefused(exc.message, "cannot")
            # The bot is not set up yet (the wizard asks before its plan):
            # her yes is recorded now; the run setting decides later.
            try:
                outside_services.record_yes(row["door"], by=by)
            except outside_services.OutsideError as exc2:
                raise KeyRefused(exc2.message, "cannot")
            before_setup = True
    # A row marked store_before_setup (Toolsmith's Anthropic key) is only
    # locked in the vault while the wizard runs: NOTHING is sent before the
    # bot is set up. Anthropic sees it first on Toolsmith's first call, and
    # a 401 there says the key was not accepted.
    unchecked = before_setup and row.get("store_before_setup") is True
    try:
        outcome = ("ok" if unchecked else
                   _check_call_before_setup(row, text) if before_setup
                   else _check_call(row, text))
    except KeyUnavailable as exc:
        if not was_on:
            _quiet_off(row, by)
        _audit("key_add", by, "failed", f"{service_id} {exc.reason}")
        raise KeyRefused(exc.message, exc.reason)
    if outcome != "ok":
        if not was_on:
            _quiet_off(row, by)
        _audit("key_add", by, "rejected", service_id)
        raise KeyRefused(f"{row['name']} said that key is not right. Check you "
                         f"copied all of it.", "rejected")
    record = {"schema": RECORD_SCHEMA, "service_id": service_id,
              "label": row["name"], "value": text, "hosts": list(row["hosts"]),
              "header": row["placement"]["name"], "stored_at": _now_iso(),
              "catalog_digest": _catalog_digest(row),
              "card_required": row["card_required"],
              "personal_use_only": row["personal_use_only"]}
    # Toolsmith's lease on the old key ends BEFORE the new key is written, so
    # no lease holds the old key once the new one is on file.
    _lease_ends(service_id)
    v._reserved_set(_RESERVED + service_id, json.dumps(record))  # noqa: SLF001
    with _lock:
        if service_id in _pending_removals():
            _write_pending(_pending_removals() - {service_id})
    kept_locked = False
    if background:
        kept_locked = _write_background(v, service_id, record)
        if not kept_locked:
            notes.append("This computer cannot keep keys working while the "
                         "vault is locked, so this one works only while it is "
                         "open.")
    else:
        _drop_background(v, service_id)
    record = None
    with _lock:
        index = _read_index()
        index[service_id] = {
            "stored_at": _now_iso(), "checked_at": None if unchecked else _now_iso(),
            "background": kept_locked, "rejected_at": None,
            "card_required": row["card_required"],
            "hosts": list(row["hosts"]), "catalog_digest": _catalog_digest(row)}
        _write_index(index)
    _lease_starts(service_id, v)
    _audit("key_add", by, "success", f"{service_id} background={kept_locked}"
           + (" unchecked_before_setup" if unchecked else ""))
    out = {"ok": True,
           "message": ("Your key is locked in your vault. It is checked the first "
                       "time it is used." if unchecked else
                       "That key works. It is locked in your vault. Your bot "
                       "keeps only a note that says which key to use."),
           "notes": notes, "key": key_status(service_id, vault=v)}
    if before_setup and not unchecked:
        out["checked"] = dict(_SETUP_ANSWER)
        _SETUP_ANSWER.clear()
    return out


def _write_background(vault: Any, service_id: str, record: dict) -> bool:
    if spec(service_id) is None or spec(service_id)["card_required"]:
        return False
    try:
        if not vault.has_service_wrap and not vault.enroll_service_wrap(
                make_machine_key=True):
            return False
        vault.write_service_secret(background_file(service_id), record)
        return True
    except Exception as exc:
        logger.info("key %s cannot work while locked: %s", service_id,
                    type(exc).__name__)
        return False


def _drop_background(vault: Any, service_id: str) -> None:
    try:
        vault.delete_service_secret(background_file(service_id))
    except Exception as exc:
        logger.debug("no locked copy to drop: %s", type(exc).__name__)


def _quiet_off(row: dict, by: str) -> None:
    from puffpai import outside_services
    try:
        outside_services.switch_off(row["door"], by=by)
    except Exception as exc:
        logger.debug("could not switch %s back off: %s", row["door"],
                     type(exc).__name__)


def set_background(service_id: str, on: bool, *, vault: Any = None,
                   by: str = "owner") -> dict:
    """Keep a FREE key working while the vault is locked, or stop that.

    On needs the vault open (the copy is made from it) and, from a route, a
    redeemed ``key_on`` ticket. Off needs nothing: it only removes a copy.
    """
    row = spec(service_id)
    if row is None:
        raise KeyRefused("There is no sign-up key by that name.", "unknown")
    v = _vault(vault)
    index = _read_index()
    if service_id not in index:
        raise KeyRefused(f"There is no {row['name']} key to change.", "missing")
    if on:
        if row["card_required"]:
            raise KeyRefused(f"{row['name']} asks for a payment card, so its key "
                             f"never works while your vault is locked.", "cannot")
        if row.get("vault_only"):
            raise KeyRefused(f"Your {row['name']} sign-in is kept only in your vault, "
                             f"so it never works while the vault is locked.", "cannot")
        if not _is_open(v):
            raise KeyRefused("Your vault is locked. Open it first.", "locked")
        record = _record_for(v, service_id)
        if record is None or not _write_background(v, service_id, record):
            raise KeyRefused("This computer cannot keep keys working while the "
                             "vault is locked.", "cannot")
    elif v is not None:
        _drop_background(v, service_id)
    with _lock:
        index = _read_index()
        if service_id in index:
            index[service_id]["background"] = bool(on)
            _write_index(index)
    _audit("key_background", by, "on" if on else "off", service_id)
    return key_status(service_id, vault=v)


def check_key(service_id: str, *, vault: Any = None, by: str = "owner") -> dict:
    """Check the stored key again (the owner pressed Check now). One call."""
    row = spec(service_id)
    if row is None:
        raise KeyRefused("There is no sign-up key by that name.", "unknown")
    v = _vault(vault)
    if service_id not in _read_index():
        raise KeyRefused(walk_through(service_id, "browser")["line"], "missing")
    if not _is_open(v):
        raise KeyRefused("Your vault is locked. Open it first, then check "
                         "again.", "locked")
    record = _record_for(v, service_id)
    if record is None:
        raise KeyRefused(walk_through(service_id, "browser")["line"], "missing")
    if _rate_limited():
        raise KeyRefused("That was a lot of tries in one minute. Wait a minute "
                         "and try again.", "busy")
    if not _door_open(row):
        raise KeyRefused(_state_line("switched_off", row, "browser"),
                         "switched_off")
    _count_use(service_id)
    try:
        outcome = _check_call(row, record["value"])
    except KeyUnavailable as exc:
        raise KeyRefused(exc.message, exc.reason)
    finally:
        record = None
    with _lock:
        index = _read_index()
        if service_id in index:
            index[service_id]["checked_at"] = _now_iso()
            index[service_id]["rejected_at"] = (None if outcome == "ok"
                                                else _now_iso())
            _write_index(index)
    _audit("key_check", by, outcome, service_id)
    if outcome != "ok":
        raise KeyRefused(_state_line("stopped_working", row, "browser"),
                         "rejected")
    return {"ok": True, "message": "That key works.",
            "key": key_status(service_id, vault=v)}


def test_public_key(service_id: str) -> dict:
    """One test call with a service's documented shared test key. Stores
    nothing. For trying a service out; never used for the owner's searches."""
    row = spec(service_id)
    if row is None or not row.get("public_test_key"):
        raise KeyRefused("That service has no shared test key.", "unknown")
    if not _door_open(row):
        raise KeyRefused(_state_line("switched_off", row, "terminal"),
                         "switched_off")
    try:
        outcome = _check_call(row, row["public_test_key"])
    except KeyUnavailable as exc:
        raise KeyRefused(exc.message, exc.reason)
    return {"ok": outcome == "ok",
            "message": (f"{row['name']} answered with its shared test key."
                        if outcome == "ok" else
                        f"{row['name']} refused its shared test key.")}


def public_test_get(service_id: str, url: str, *,
                    headers: Optional[dict] = None) -> KeyedReply:
    """A GET with a service's documented shared TEST key (never the owner's).

    For ``puffpai keys test --words``: a trial search before the owner has
    a key of their own. Same seam, same host binding, same door.
    """
    row = spec(service_id)
    if row is None or not row.get("public_test_key"):
        raise KeyUnavailable("not_curated", "That service has no shared test key.")
    try:
        host = (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        host = ""
    if host not in row["hosts"]:
        raise KeyUnavailable("not_curated", "That address is not where this "
                                            "key may be sent.")
    return _send_with_key(row, row["public_test_key"], list(row["hosts"]), url,
                          headers=dict(headers or {"Accept": "application/json"}))


def remove_key(service_id: str, *, vault: Any = None, by: str = "owner") -> dict:
    """Remove a key. One step, works with the vault locked (see rule 7)."""
    row = spec(service_id)
    if row is None:
        raise KeyRefused("There is no sign-up key by that name.", "unknown")
    v = _vault(vault)
    _lease_ends(service_id)
    with _lock:
        index = _read_index()
        existed = index.pop(service_id, None) is not None
        _write_index(index)
    cleared = False
    if v is not None:
        _drop_background(v, service_id)
        if _is_open(v):
            try:
                v._reserved_delete(_RESERVED + service_id)  # noqa: SLF001
                cleared = True
            except Exception as exc:
                logger.debug("locked record not cleared yet: %s",
                             type(exc).__name__)
    if not cleared:
        # Cleared the moment the vault is next opened (on_vault_opened).
        with _lock:
            _write_pending(_pending_removals() | {service_id})
    _quiet_off(row, by)
    _audit("key_remove", by, "success" if existed else "absent", service_id)
    return {"ok": True,
            "message": (f"Removed. Anything that used it is paused. To stop the "
                        f"key working anywhere, also delete it on "
                        f"{row['name']}'s own page: {row['signup_url']}"),
            "key": key_status(service_id, vault=v)}


def confirm_sentence(service_id: str, *, background: bool = False) -> str:
    """The server's own sentence for a ``key_on`` ticket."""
    from puffpai import outside_services
    row = spec(service_id)
    if row is None:
        raise KeyRefused("There is no sign-up key by that name.", "unknown")
    try:
        head = outside_services.proposal(row["door"])
    except outside_services.OutsideError as exc:
        if exc.code == "not_set_up" and row.get("check_before_setup") is True:
            item = outside_services.row(row["door"]) or {}
            sites = ", ".join(item.get("websites") or row["hosts"])
            head = (f"Switch on {row['name']}. What leaves your computer: "
                    f"{item.get('what_leaves') or ''} It goes to {sites}. Your "
                    f"bot's own setting for the internet, chosen later, still "
                    f"decides; you can switch it off again at any time.")
        elif exc.code != "already":
            raise KeyRefused(exc.message, exc.code)
        else:
            head = f"{row['name']} is already switched on."
    locked = ("It keeps working while your vault is locked: anyone who gets "
              "into this computer account could use it."
              if background and not row["card_required"] else
              "It works only while your vault is open.")
    return (f"Add your {row['name']} key. {head} The key is locked in your "
            f"vault and your bot keeps only a note of which key to use. "
            f"{locked}")


def locked_sentence(service_id: str) -> str:
    """The server's own sentence for keeping a FREE key working while locked."""
    row = spec(service_id)
    if row is None:
        raise KeyRefused("There is no sign-up key by that name.", "unknown")
    if row["card_required"]:
        raise KeyRefused(f"{row['name']} asks for a payment card, so its key "
                         f"never works while your vault is locked.", "cannot")
    if service_id not in _read_index():
        raise KeyRefused(f"There is no {row['name']} key to change.", "missing")
    return (f"Keep your {row['name']} key working while your vault is locked. "
            f"Anyone who gets into this computer account could use it. You "
            f"can switch this off again at any time.")


# ── the add-more loop (add_more and the wizard call these) ───────────────────

def proposal_items(goal: str = "", terms: Any = None, *,
                   age: Any = None) -> list[dict]:
    """Summary lines for keyed services (section ``outside_services``).

    Off until the owner switches one on: a key needs their own sign-up, so
    "Looks good" alone never starts one. The keyless Wikipedia search is not
    here: it is the ``web_search`` line the summary already builds from the
    outside-services list.
    """
    out: list[dict] = []
    for sid, row in listed().items():
        if _read_index().get(sid):
            continue  # already added
        how = walk_through(sid, "browser", age=age)
        label = f"Search the web with {row['name']}: needs a free key"
        if row["personal_use_only"]:
            label = f"{label} (personal use only)"
        detail = f"{how['line']} {row['cost_note']}"
        if how.get("grown_up_line"):
            detail = f"{how['grown_up_line']} {detail}"
        out.append({
            "id": f"outside_services:key:{sid}",
            "section": "outside_services",
            "label": label[:80],
            "detail": detail,
            "size_bytes": None,
            "outside_host": row["hosts"][0],
            "default_on": False,
            "needs_yes": True,
            "apply": {"kind": "key", "ref": sid},
        })
    return out


def apply_item(apply: Any, *, by: str = "owner", surface: str = "browser") -> dict:
    """Carry out a switched-on key line. A key is never pasted here: the
    answer is the walk-through, and the line becomes a plain gap until the
    owner pastes the key on the Sign-up keys page or in the terminal."""
    if not isinstance(apply, dict) or apply.get("kind") != "key":
        return {"ok": False, "why": "That is not a sign-up key."}
    sid = str(apply.get("ref") or "")
    row = spec(sid)
    if row is None:
        return {"ok": False, "why": "BuildBot does not know that sign-up key.",
                "fix": {"kind": "none"}}
    if _read_index().get(sid):
        return {"ok": True, "line": f"{row['name']}: already added."}
    how = walk_through(sid, surface)
    try:
        _remember_wanted(sid)  # the Sign-up keys page says it was chosen
    except OSError as exc:
        logger.debug("key choice not remembered: %s", type(exc).__name__)
    return {"ok": False, "needs_key": sid, "why": how["line"],
            "fix": {"kind": "none"}}


# ── tool secrets: the headers a tool server needs ───────────────────────────
#
# "Connect my Home Assistant" and "Add my own tool" can come with a password
# (a long-lived token in an Authorization header). It goes into the vault here,
# the one module allowed to hold vault records for the bot. The tool server
# list keeps only the server's name; the headers are read back at call time.
# The vault locks itself after a few idle minutes, so by default a copy is kept
# in the vault's unattended tier (sealed with this computer's own key, the same
# tier a free sign-up key uses) so the house still answers while it is locked.

def _tool_index_path() -> str:
    return _state_path("tool_secrets.json")


def _tool_names() -> set[str]:
    from puffpai import fsutil
    data = fsutil.read_json(_tool_index_path(), default=None)
    names = data.get("names") if isinstance(data, dict) else None
    return {n for n in names or () if isinstance(n, str) and _TOOL_NAME_RE.match(n)}


def _write_tool_names(names: set[str]) -> None:
    from puffpai import fsutil
    fsutil.atomic_write_json(_tool_index_path(), {"names": sorted(names)})


def tool_file(name: str) -> str:
    return f"key_tool_{name}.json"


def _clean_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, dict) or not headers:
        raise KeyRefused("There is nothing to keep.", "empty")
    out: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not _HEADER_RE.match(key):
            raise KeyRefused("That header name cannot be used.", "header")
        if not isinstance(value, str) or not value.strip() \
                or len(value) > MAX_TOOL_VALUE or "\n" in value or "\r" in value:
            raise KeyRefused("That password cannot be used.", "shape")
        out[key] = value.strip()
    return out


def put_tool_secret(name: str, headers: Any, *, vault: Any = None,
                    keep_working_locked: bool = True, by: str = "owner") -> dict:
    """Lock a tool server's headers in the vault. Needs the vault open."""
    if not isinstance(name, str) or not _TOOL_NAME_RE.match(name):
        raise KeyRefused("That tool name cannot be used.", "name")
    clean = _clean_headers(headers)
    v = _vault(vault)
    if not _is_open(v):
        raise KeyRefused("Your vault is locked. Open it first, then try again.",
                         "locked")
    record = {"schema": TOOL_SCHEMA, "name": name, "headers": clean,
              "at": _now_iso()}
    with _lock:
        v._reserved_set(_RESERVED + _TOOL + name, json.dumps(record))  # noqa: SLF001
        kept = False
        if keep_working_locked:
            try:
                if v.has_service_wrap or v.enroll_service_wrap(make_machine_key=True):
                    v.write_service_secret(tool_file(name), record)
                    kept = True
            except Exception as exc:
                logger.info("tool %s works only while the vault is open: %s",
                            name, type(exc).__name__)
        else:
            _drop_tool_copy(v, name)
        _write_tool_names(_tool_names() | {name})
    _audit("tool_secret_add", by, "success", f"{name} background={kept}")
    return {"ok": True, "kept_when_locked": kept}


def _drop_tool_copy(vault: Any, name: str) -> None:
    try:
        vault.delete_service_secret(tool_file(name))
    except Exception as exc:
        logger.debug("no locked tool copy to drop: %s", type(exc).__name__)


def has_tool_secret(name: str) -> bool:
    return name in _tool_names()


def tool_secret(name: str, *, vault: Any = None) -> Optional[dict[str, str]]:
    """The headers for *name*, or None (none kept, or the vault is locked and
    no copy was kept for that). Never logged."""
    if not isinstance(name, str) or name not in _tool_names():
        return None
    v = _vault(vault)
    raw: Any = None
    if _is_open(v):
        raw = v._reserved_get(_RESERVED + _TOOL + name)  # noqa: SLF001
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = None
    elif v is not None:
        raw = v.read_service_secret_unattended(tool_file(name))
    if not isinstance(raw, dict) or raw.get("schema") != TOOL_SCHEMA \
            or raw.get("name") != name or not isinstance(raw.get("headers"), dict):
        return None
    return {str(k): str(val) for k, val in raw["headers"].items()}


def remove_tool_secret(name: str, *, vault: Any = None, by: str = "owner") -> bool:
    """Forget *name*'s headers. Works with the vault locked: the name leaves
    the list at once, so nothing can use them, and the locked record is
    cleared the next time the vault is open (:func:`purge_removed`)."""
    if not isinstance(name, str) or not _TOOL_NAME_RE.match(name):
        return False
    with _lock:
        names = _tool_names()
        existed = name in names
        if existed:
            _write_tool_names(names - {name})
        v = _vault(vault)
        if v is not None:
            _drop_tool_copy(v, name)
            if _is_open(v):
                try:
                    v._reserved_delete(_RESERVED + _TOOL + name)  # noqa: SLF001
                except Exception as exc:
                    logger.debug("tool record not cleared: %s", type(exc).__name__)
    if existed:
        _audit("tool_secret_remove", by, "success", name)
    return existed


# ── Skill keys ───────────────────────────────────────────────────────────────
#
# A skill can say it needs a key in an environment variable (OpenClaw's
# ``requires.env`` / ``primaryEnv``, Hermes's ``required_environment_variables``;
# see :func:`puffpai.skills.requirements`). Like OpenClaw's
# ``skills.entries.<name>.apiKey``, the owner gives it once; here it goes into
# the vault, under a record named for the skill, and nowhere else.
#
# * Only a variable the skill names can be kept for it: one it DECLARES, or
#   one its text names as a key (``$X_API_KEY``; :func:`puffpai.deps.read`).
#   Never one that changes how programs start (``PATH``, ``NODE_OPTIONS``,
#   ``LD_PRELOAD`` ...) or one of BuildBot's own (:func:`puffpai.deps.env_name_ok`).
# * The value is handed to exactly one command at a time: a ``run_command``
#   for that skill, after her yes (:mod:`puffpai.brain_commands`). Never to
#   another skill, never to the Developer terminal, never logged.
# * No locked copy: a skill's key works only while the vault is open. A
#   command that needs it while the vault is locked is not run; she is asked
#   to open the vault instead.
# * The list of which skills have which keys (names only, never values) is
#   kept beside the other indexes so a page can say "needs your key" without
#   opening the vault.

def _skill_index_path() -> str:
    return _state_path("skill_keys.json")


def skill_identity(skill: str) -> str:
    """Which copy of the skill *skill* is installed now, as a short hash, or
    "" when there is no such folder.

    A key belongs to one installed copy, not to a folder NAME: a folder
    deleted any way at all (not only through Remove) and a new skill put
    under the same name must not inherit the old one's key. The hash covers
    the folder's own file id and creation time (both new for a folder made
    again) and, when the skill came from Find more, where it came from (its
    lock entry: source, version or commit, archive hash). Editing the
    skill's files in place keeps the key; the skill's own content cannot
    choose this value.

    Only WHERE it came from counts (the shelf, publisher, name, project and
    folder), not which version: a Find more update of the same skill, made
    in place (``finding.install.update_skill``), keeps its key."""
    return _identities(skill)[0]


#: The parts of a lock entry that say which skill it is (not which version).
_ORIGIN_FIELDS = ("source", "owner", "slug", "repo", "folder")


def _identities(skill: str) -> tuple[str, str]:
    """``(identity, older form of the identity)``. The second is only read,
    so a key kept under the older form still works until the skill is updated.
    ``("", "")`` when there is no such folder."""
    try:
        from puffpai import skills as skills_mod
        folder = os.path.join(skills_mod.skills_dir(), skill)
        st = os.stat(folder)
        if not os.path.isdir(folder):
            return "", ""
    except (OSError, ValueError):
        return "", ""
    from puffpai import fsutil
    born = fsutil.birth_time(folder, st)
    origin: Any = {}
    try:
        from puffpai.finding import install
        origin = install._read_lock().get(skill) or {}  # noqa: SLF001
    except Exception:
        origin = {}
    origin = origin if isinstance(origin, dict) else {}
    stable = {k: origin.get(k) for k in _ORIGIN_FIELDS if origin.get(k)}
    out = []
    for tag, part in (("puffpai-skill-identity/2:", stable), ("puffpai-skill-identity:", origin)):
        text = json.dumps([skill, st.st_ino, str(born), part], sort_keys=True, default=str)
        out.append(hashlib.sha256((tag + text).encode("utf-8")).hexdigest()[:32])
    return out[0], out[1]


def _same_copy(kept_id: str, skill: str) -> bool:
    return any(i and hmac.compare_digest(kept_id, i) for i in _identities(skill))


def _skill_index() -> dict[str, dict]:
    """``{folder: {"names": [...], "id": <skill_identity when kept>}}``."""
    from puffpai import fsutil
    data = fsutil.read_json(_skill_index_path(), default=None)
    found = data.get("skills") if isinstance(data, dict) else None
    out: dict[str, dict] = {}
    for skill, rec in (found or {}).items() if isinstance(found, dict) else ():
        if not (isinstance(skill, str) and _SKILL_RE.match(skill) and isinstance(rec, dict)):
            continue
        names = rec.get("names")
        good = sorted({n for n in names if isinstance(n, str) and _ENV_NAME_RE.match(n)}) \
            if isinstance(names, list) else []
        ident = rec.get("id") if isinstance(rec.get("id"), str) else ""
        if good and ident:
            out[skill] = {"names": good, "id": ident}
    return out


def _write_skill_index(index: dict[str, dict]) -> None:
    from puffpai import fsutil
    fsutil.atomic_write_json(_skill_index_path(), {"skills": {
        k: {"names": sorted(set(v["names"])), "id": v["id"]}
        for k, v in index.items() if v.get("names") and v.get("id")}})


def skill_keys_set(skill: str) -> list[str]:
    """The names of the keys kept for the skill installed as *skill* now
    (never the values). Keys kept for an earlier copy under the same folder
    name are dropped here, at once (:func:`skill_identity`)."""
    skill = str(skill or "")
    rec = _skill_index().get(skill)
    if rec is None:
        return []
    if not _same_copy(rec["id"], skill):
        remove_skill_key(skill, by="orphan")
        return []
    return list(rec["names"])


def _skill_record(v: Any, skill: str, ident: Any) -> dict[str, str]:
    """The kept values of *skill* when the record belongs to *ident* (one
    identity, or a few that all mean this installed copy)."""
    idents = {ident} if isinstance(ident, str) else {i for i in ident if i}
    raw = v._reserved_get(_RESERVED + _SKILL + skill)  # noqa: SLF001
    try:
        rec = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        rec = None
    if not isinstance(rec, dict) or rec.get("schema") != SKILL_SCHEMA \
            or rec.get("skill") != skill or rec.get("id") not in idents \
            or not isinstance(rec.get("env"), dict):
        return {}
    return {str(k): str(val) for k, val in rec["env"].items()
            if isinstance(k, str) and _ENV_NAME_RE.match(k) and isinstance(val, str)}


def put_skill_key(skill: str, name: str, value: Any, *, declared: Any,
                  vault: Any = None, by: str = "owner") -> dict:
    """Keep *value* as *skill*'s key *name*, for the copy of the skill that
    is installed now. Needs the vault open, and *name* must be one of the
    keys the skill names (*declared*: in its settings or its text, see
    :func:`puffpai.deps.key_names`), never a name that changes how a program
    starts. Raises :class:`KeyRefused` with plain words. The value is never
    logged."""
    from puffpai import deps
    if not isinstance(skill, str) or not _SKILL_RE.match(skill):
        raise KeyRefused("That skill name cannot be used.", "name")
    if not isinstance(name, str) or not _ENV_NAME_RE.match(name) \
            or name not in (declared or ()):
        raise KeyRefused("That skill does not ask for a key by that name.", "name")
    if not deps.env_name_ok(name):
        raise KeyRefused("That name changes how programs start, so no key is kept "
                         "under it.", "name")
    if not isinstance(value, str):
        raise KeyRefused("That key cannot be used.", "shape")
    value = value.strip()
    if not MIN_VALUE <= len(value) <= MAX_TOOL_VALUE or any(
            c in value for c in ("\n", "\r", "\x00")):
        raise KeyRefused("That does not look like a key: it is too short, too long "
                         "or has more than one line.", "shape")
    idents = _identities(skill)
    ident = idents[0]
    if not ident:
        raise KeyRefused("That skill is not installed.", "name")
    v = _vault(vault)
    if not _is_open(v):
        raise KeyRefused("Your vault is locked. Open it on the Vault page, then "
                         "try again.", "locked")
    with _lock:
        index = _skill_index()
        same = index.get(skill, {}).get("id") in idents
        env = _skill_record(v, skill, idents) if same else {}
        env[name] = value
        record = {"schema": SKILL_SCHEMA, "skill": skill, "id": ident, "env": env,
                  "at": _now_iso()}
        v._reserved_set(_RESERVED + _SKILL + skill, json.dumps(record))  # noqa: SLF001
        names = set(index.get(skill, {}).get("names", [])) if same else set()
        index[skill] = {"names": sorted(names | {name}), "id": ident}
        _write_skill_index(index)
    _audit("skill_key_add", by, "success", f"{skill} {name}")
    return {"ok": True, "skill": skill, "name": name}


def skill_keys(skill: str, names: Any, *, vault: Any = None) -> dict[str, str]:
    """The kept values of *skill*'s keys among *names* ({} when none is kept).

    Raises :class:`KeyUnavailable` (code ``locked``) when one of them is kept
    but the vault is locked: the caller asks her to open it, nothing runs."""
    if not isinstance(skill, str) or not _SKILL_RE.match(skill):
        return {}
    wanted = [n for n in (names or ()) if isinstance(n, str) and _ENV_NAME_RE.match(n)]
    kept = [n for n in skill_keys_set(skill) if n in wanted]
    if not kept:
        return {}
    v = _vault(vault)
    if not _is_open(v):
        raise KeyUnavailable("locked", locked_skill_sentence(skill, kept))
    env = _skill_record(v, skill, _identities(skill))
    return {n: env[n] for n in kept if env.get(n)}


def locked_skill_sentence(skill: str, names: list[str]) -> str:
    shown = ", ".join(names)
    return (f"The skill {skill} needs its key {shown} from your vault, and your "
            f"vault is locked. Open your vault on the Vault page, then ask "
            f"again. Nothing was run.")


def remove_skill_key(skill: str, name: Optional[str] = None, *, vault: Any = None,
                     by: str = "owner") -> bool:
    """Forget one of *skill*'s keys, or all of them (``name=None``: the skill
    is removed or installed again, or its keys belonged to an earlier copy).
    Works with the vault locked: the name leaves the list at once, so nothing
    gets it, and the record is cleared now if the vault is open, else the
    next time it is (:func:`purge_removed`)."""
    if not isinstance(skill, str) or not _SKILL_RE.match(skill):
        return False
    with _lock:
        index = _skill_index()
        rec = index.get(skill)
        had = set(rec["names"]) if rec else set()
        left = set() if name is None else had - {name}
        existed = bool(had - left)
        if left and rec:
            index[skill] = {"names": sorted(left), "id": rec["id"]}
        else:
            index.pop(skill, None)
        _write_skill_index(index)
        v = _vault(vault)
        if _is_open(v):
            try:
                if left and rec:
                    env = {k: val for k, val in _skill_record(v, skill, rec["id"]).items()
                           if k in left}
                    v._reserved_set(_RESERVED + _SKILL + skill, json.dumps(  # noqa: SLF001
                        {"schema": SKILL_SCHEMA, "skill": skill, "id": rec["id"],
                         "env": env, "at": _now_iso()}))
                else:
                    v._reserved_delete(_RESERVED + _SKILL + skill)  # noqa: SLF001
            except Exception as exc:
                logger.debug("skill key record not cleared: %s", type(exc).__name__)
    if existed:
        _audit("skill_key_remove", by, "success", f"{skill} {name or 'all'}")
    return existed


# ── programs BuildBot installed for skills ───────────────────────────────────
#
# When "Install what's missing" installs a skill's own command line program
# (npm or pip), :mod:`puffpai.security.skill_programs` records where its entry
# script is and its sha256 here, inside the vault (encrypted and
# authenticated, so a rewritten record does not decrypt). Only a program with
# a record whose hash still matches may be given a skill's key. The record
# holds no secret: paths, names, versions and hashes.

_PROGRAM = "program."
PROGRAM_SCHEMA = "puffpai_skill_program/v2"
_PROGRAM_BIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


def _program_key(skill: str, bin_name: str) -> Optional[str]:
    """``program.<skill>/<bin>`` when both are plain, else None. The skill and
    the program together are the key, so one skill's program can never take
    over another skill's record (``/`` is in neither pattern)."""
    if not (isinstance(skill, str) and _SKILL_RE.match(skill)
            and isinstance(bin_name, str) and _PROGRAM_BIN_RE.match(bin_name)):
        return None
    return _RESERVED + _PROGRAM + skill + "/" + bin_name


def put_program_record(skill: str, bin_name: str, record: dict, *, vault: Any = None) -> bool:
    """Keep *record* for *skill*'s program *bin_name*. ``False`` (nothing kept)
    when the vault is locked or a name is not plain."""
    key = _program_key(skill, bin_name)
    if key is None or not isinstance(record, dict):
        return False
    v = _vault(vault)
    if not _is_open(v):
        return False
    rec = {**record, "schema": PROGRAM_SCHEMA, "skill": skill, "bin": bin_name,
           "at": _now_iso()}
    with _lock:
        v._reserved_set(key, json.dumps(rec, sort_keys=True))  # noqa: SLF001
    _audit("skill_program_record", "install", "success", f"{skill}/{bin_name}")
    return True


def program_record(skill: str, bin_name: str, *, vault: Any = None) -> Optional[dict]:
    """*skill*'s record for *bin_name*, or ``None`` (no record, vault locked,
    or a record that is not ours or not for this skill and program)."""
    key = _program_key(skill, bin_name)
    if key is None:
        return None
    v = _vault(vault)
    if not _is_open(v):
        return None
    try:
        raw = v._reserved_get(key)  # noqa: SLF001
        rec = json.loads(raw) if isinstance(raw, str) else None
    except Exception:  # noqa: BLE001 - unreadable is no record
        return None
    if not isinstance(rec, dict) or rec.get("schema") != PROGRAM_SCHEMA \
            or rec.get("bin") != bin_name or rec.get("skill") != skill:
        return None
    return rec


# ── an app that runs on this computer: its own keys ──────────────────────────
#
# An app from integrate.run_here (a project run as its authors built it) may
# ask for keys in its README or its example settings file. She can keep such
# a key on its page; it reaches only that app's own process when it starts,
# never its install steps and never another app. The rules are the skill
# keys' rules: only a name the app's yes card listed, never a name that
# changes how programs start (deps.env_name_ok), vault open to keep one, and
# the key belongs to the copy set up now (a removed and re-added app does
# not inherit it).

_APP = "app."
_APP_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}$")
APP_SCHEMA = "puffpai_app_keys/v1"


def _app_index_path() -> str:
    return _state_path("app_keys.json")


def app_identity(app_id: str) -> str:
    """Which set-up copy of the app *app_id* is here now (its own folder's
    file id and creation time), or "" when it is not set up."""
    if not isinstance(app_id, str) or not _APP_RE.match(app_id):
        return ""
    try:
        from puffpai.integrate import run_here
        folder = run_here.home(app_id)
        st = os.stat(folder)
        if not os.path.isdir(folder):
            return ""
    except Exception:  # noqa: BLE001 - not set up here
        return ""
    from puffpai import fsutil
    born = fsutil.birth_time(folder, st)
    text = json.dumps([app_id, st.st_ino, str(born)], sort_keys=True, default=str)
    return hashlib.sha256(("puffpai-app-identity/1:" + text).encode("utf-8")).hexdigest()[:32]


def _app_index() -> dict[str, dict]:
    from puffpai import fsutil
    data = fsutil.read_json(_app_index_path(), default=None)
    found = data.get("apps") if isinstance(data, dict) else None
    out: dict[str, dict] = {}
    for app_id, rec in (found or {}).items() if isinstance(found, dict) else ():
        if not (isinstance(app_id, str) and _APP_RE.match(app_id) and isinstance(rec, dict)):
            continue
        names = rec.get("names")
        good = sorted({n for n in names if isinstance(n, str) and _ENV_NAME_RE.match(n)}) \
            if isinstance(names, list) else []
        ident = rec.get("id") if isinstance(rec.get("id"), str) else ""
        if good and ident:
            out[app_id] = {"names": good, "id": ident}
    return out


def _write_app_index(index: dict[str, dict]) -> None:
    from puffpai import fsutil
    fsutil.atomic_write_json(_app_index_path(), {"apps": {
        k: {"names": sorted(set(v["names"])), "id": v["id"]}
        for k, v in index.items() if v.get("names") and v.get("id")}})


def app_keys_set(app_id: str) -> list[str]:
    """The names of the keys kept for the app set up as *app_id* now (never
    the values). Keys kept for an earlier copy are dropped here, at once."""
    rec = _app_index().get(str(app_id or ""))
    if rec is None:
        return []
    now = app_identity(app_id)
    if not now or not hmac.compare_digest(rec["id"], now):
        remove_app_key(app_id, by="orphan")
        return []
    return list(rec["names"])


def _app_record(v: Any, app_id: str, ident: str) -> dict[str, str]:
    raw = v._reserved_get(_RESERVED + _APP + app_id)  # noqa: SLF001
    try:
        rec = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        rec = None
    if not isinstance(rec, dict) or rec.get("schema") != APP_SCHEMA \
            or rec.get("app") != app_id or rec.get("id") != ident \
            or not isinstance(rec.get("env"), dict):
        return {}
    return {str(k): str(val) for k, val in rec["env"].items()
            if isinstance(k, str) and _ENV_NAME_RE.match(k) and isinstance(val, str)}


def put_app_key(app_id: str, name: str, value: Any, *, declared: Any,
                vault: Any = None, by: str = "owner") -> dict:
    """Keep *value* as the app *app_id*'s key *name*. *declared* is the list
    of names its yes card showed (``run_here`` plan ``keys``). Raises
    :class:`KeyRefused` with plain words. The value is never logged."""
    from puffpai import deps
    if not isinstance(app_id, str) or not _APP_RE.match(app_id):
        raise KeyRefused("That app is not known.", "name")
    if not isinstance(name, str) or not _ENV_NAME_RE.match(name) \
            or name not in (declared or ()):
        raise KeyRefused("That app does not ask for a key by that name.", "name")
    if not deps.env_name_ok(name):
        raise KeyRefused("That name changes how programs start, so no key is kept "
                         "under it.", "name")
    if not isinstance(value, str):
        raise KeyRefused("That key cannot be used.", "shape")
    value = value.strip()
    if not MIN_VALUE <= len(value) <= MAX_TOOL_VALUE or any(
            c in value for c in ("\n", "\r", "\x00")):
        raise KeyRefused("That does not look like a key: it is too short, too long "
                         "or has more than one line.", "shape")
    ident = app_identity(app_id)
    if not ident:
        raise KeyRefused("That app is not set up on this computer.", "name")
    v = _vault(vault)
    if not _is_open(v):
        raise KeyRefused("Your vault is locked. Open it on the Vault page, then "
                         "try again.", "locked")
    with _lock:
        index = _app_index()
        same = index.get(app_id, {}).get("id") == ident
        env = _app_record(v, app_id, ident) if same else {}
        env[name] = value
        v._reserved_set(_RESERVED + _APP + app_id, json.dumps(  # noqa: SLF001
            {"schema": APP_SCHEMA, "app": app_id, "id": ident, "env": env, "at": _now_iso()}))
        names = set(index.get(app_id, {}).get("names", [])) if same else set()
        index[app_id] = {"names": sorted(names | {name}), "id": ident}
        _write_app_index(index)
    _audit("app_key_add", by, "success", f"{app_id} {name}")
    return {"ok": True, "app": app_id, "name": name}


def app_keys(app_id: str, names: Any, *, vault: Any = None) -> dict[str, str]:
    """The kept values of *app_id*'s keys among *names* ({} when none is
    kept). Raises :class:`KeyUnavailable` (code ``locked``) when one is kept
    but the vault is locked: the caller does not start it."""
    if not isinstance(app_id, str) or not _APP_RE.match(app_id):
        return {}
    from puffpai import deps
    wanted = [n for n in (names or ()) if isinstance(n, str) and _ENV_NAME_RE.match(n)
              and deps.env_name_ok(n)]
    kept = [n for n in app_keys_set(app_id) if n in wanted]
    if not kept:
        return {}
    v = _vault(vault)
    if not _is_open(v):
        raise KeyUnavailable("locked", locked_app_sentence(kept))
    env = _app_record(v, app_id, app_identity(app_id))
    return {n: env[n] for n in kept if env.get(n)}


def locked_app_sentence(names: list[str]) -> str:
    return (f"It needs its key {', '.join(names)} from your vault, and your vault is "
            f"locked. Open your vault on the Vault page, then start it again. "
            f"Nothing was started.")


def remove_app_key(app_id: str, name: Optional[str] = None, *, vault: Any = None,
                   by: str = "owner") -> bool:
    """Forget one of the app's keys, or all of them (``name=None``: the app
    is removed). Works with the vault locked: the name leaves the list at
    once, and the record is cleared now if the vault is open, else the next
    time it is (:func:`purge_removed`)."""
    if not isinstance(app_id, str) or not _APP_RE.match(app_id):
        return False
    with _lock:
        index = _app_index()
        rec = index.get(app_id)
        had = set(rec["names"]) if rec else set()
        left = set() if name is None else had - {name}
        existed = bool(had - left)
        if left and rec:
            index[app_id] = {"names": sorted(left), "id": rec["id"]}
        else:
            index.pop(app_id, None)
        _write_app_index(index)
        v = _vault(vault)
        if _is_open(v):
            try:
                if left and rec:
                    env = {k: val for k, val in _app_record(v, app_id, rec["id"]).items()
                           if k in left}
                    v._reserved_set(_RESERVED + _APP + app_id, json.dumps(  # noqa: SLF001
                        {"schema": APP_SCHEMA, "app": app_id, "id": rec["id"],
                         "env": env, "at": _now_iso()}))
                else:
                    v._reserved_delete(_RESERVED + _APP + app_id)  # noqa: SLF001
            except Exception as exc:
                logger.debug("app key record not cleared: %s", type(exc).__name__)
    if existed:
        _audit("app_key_remove", by, "success", f"{app_id} {name or 'all'}")
    return existed


__all__ = [
    "HANDLE_PREFIX", "KeyRefused", "KeyUnavailable", "KeyedReply", "add_key",
    "keyed_post",
    "apply_item", "background_available", "background_file_allowed",
    "catalog", "check_key", "check_shape", "confirm_sentence", "handle",
    "key_status", "keyed_get", "list_keys", "locked_sentence", "proposal_items", "public_test_get",
    "purge_removed", "remove_key", "row_problem", "service_of",
    "set_background", "set_vault_provider", "spec", "test_public_key",
    "uses_this_month", "walk_through",
    "has_tool_secret", "put_tool_secret", "remove_tool_secret", "tool_file",
    "tool_secret",
    "locked_skill_sentence", "put_skill_key", "remove_skill_key", "skill_identity",
    "skill_keys",
    "skill_keys_set",
    "app_identity", "app_keys", "app_keys_set", "locked_app_sentence",
    "put_app_key", "remove_app_key",
]
