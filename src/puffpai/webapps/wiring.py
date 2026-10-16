"""Connect browser apps to the rest of the bot (called by ``factory_wiring.connect``).

Registers, each only when the other part's hook is in this build:

* the action source ``web_recipe`` (``integrate.actions.register_source``),
* the ability rows (``build_abilities.register_provider``),
* the outside-service rows, one per installed app and outside site its scan
  found it needs while running (``outside_services.register_rows``),
* Drawbridge's checkers ``static_web`` and ``web_recipe``
  (``drawbridge.register_checker``).

The outside rows fail closed: a site is shown "on" only when
``egress_doors.app_web_hosts`` says so. It is switched from the app's own
page (``/webapps/<id>/hosts``, two taps with a device-bound ticket), not from
this list; the row says so.
Safe to call more than once.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
from typing import Any

from puffpai import webapps

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_done: list[str] = []

OUTSIDE_ROWS_NAME = "webapps"


def _switch_id(app_id: str, host: str) -> str:
    sw = webapps._optional("puffpai.security.outside_switches")
    fn = getattr(sw, "switch_id_app", None) if sw is not None else None
    if callable(fn):
        return str(fn(app_id, host))
    return f"app_web:{app_id}:{host}"


def _hosts_on(app_id: str) -> dict:
    ed = webapps._optional("puffpai.security.egress_doors")
    fn = getattr(ed, "app_web_hosts", None) if ed is not None else None
    if not callable(fn):
        return {}
    try:
        got = fn(app_id)
    except Exception:  # noqa: BLE001 - unknown means off
        return {}
    return got if isinstance(got, dict) else {}


#: Second-level names under a two-letter country ending that are sold as
#: endings of their own (``example.co.uk``): the owner sees the name a person
#: registered, never just ``co.uk``.
_SHARED_SECOND = frozenset({"ac", "co", "com", "edu", "gov", "govt", "ltd", "me", "mil", "net",
                            "nhs", "or", "org", "plc", "sch", "ne", "gr", "lg", "go"})


def registrable_domain(host: str) -> str:
    """The name a person registered, for the owner to read (``fonts.example.com``
    -> ``example.com``; ``shop.example.co.uk`` -> ``example.co.uk``).

    Plain rule, no downloaded list: the last two labels, or the last three when
    the ending is a two-letter country code under a shared second-level name.
    Addresses and one-label names are shown whole.
    """
    h = str(host or "").strip().lower().rstrip(".")
    try:
        ipaddress.ip_address(h.strip("[]"))
        return h
    except ValueError:
        pass
    parts = [p for p in h.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if len(parts[-1]) == 2 and parts[-2] in _SHARED_SECOND:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def outside_rows() -> list[dict[str, Any]]:
    """One row per installed app and outside site it needs while running,
    grouped by the registrable domain the owner reads (never raises).

    Words as pinned by the design (2.8): ``<App name> uses <registrable
    domain>`` / ``<Toolsmith's words>. What the app sends there, and your
    internet address.`` A row shows "on" when ANY of its sites is on, so the
    list never says less can leave than really can.
    """
    from puffpai.webapps import host, install, wording
    out: list[dict[str, Any]] = []
    try:
        ids = host.installed_ids()
    except Exception as exc:  # noqa: BLE001
        logger.warning("outside list: browser apps failed: %s", exc)
        return out
    for app_id in ids:
        try:
            rec = host.read_record(app_id) or {}
            name = str(rec.get("name") or app_id)
            scan = rec.get("scan") if isinstance(rec.get("scan"), dict) else {}
            on = _hosts_on(app_id)
            groups: dict[str, dict[str, list[str]]] = {}
            for h in install._host_rows(scan):
                g = groups.setdefault(registrable_domain(h["host"]), {"hosts": [], "for": []})
                if h["host"] not in g["hosts"]:
                    g["hosts"].append(h["host"])
                if h["for"] not in g["for"]:
                    g["for"].append(h["for"])
            for domain, g in groups.items():
                hosts = sorted(g["hosts"])
                words = " and ".join(g["for"])
                is_on = any(on.get(x) is True for x in hosts)
                out.append({
                    "id": _switch_id(app_id, domain), "name": wording.uses_line(name, domain),
                    "for": words, "what_leaves": wording.what_leaves_line(words),
                    "websites": hosts, "state": "on" if is_on else "off",
                    "state_line": "Switched on." if is_on else "Switched off.",
                    "can_switch": False, "switch_to": None,
                    "why_cannot_switch": wording.SWITCH_ON_APP_PAGE,
                    "turned_on_by": "", "turned_on_at": "", "kind": "app_web",
                    "last_used": "", "today": {"sent": 0, "stopped": 0},
                })
        except Exception as exc:  # noqa: BLE001 - one app never breaks the list
            logger.warning("outside list: app %s failed: %s", app_id, exc)
    return out


def connect() -> list[str]:
    with _LOCK:
        if _done:
            return list(_done)
        from puffpai import build_abilities, drawbridge
        from puffpai.webapps import abilities, actions, install
        from puffpai.webapps import recipe_check as rc
        done: list[str] = []
        ia = webapps._optional("puffpai.integrate.actions")
        if ia is not None and hasattr(ia, "register_source"):
            ia.register_source(actions.KIND, actions.list_actions, actions.run)
            done.append("actions:web_recipe")
        build_abilities.register_provider(abilities.rows)
        done.append("abilities:webapps")
        os_mod = webapps._optional("puffpai.outside_services")
        reg_rows = getattr(os_mod, "register_rows", None) if os_mod is not None else None
        if callable(reg_rows):
            reg_rows(OUTSIDE_ROWS_NAME, outside_rows)
            done.append("outside:webapps")
        register = getattr(drawbridge, "register_checker", None)
        if callable(register):
            register("static_web", install.drawbridge_rows, block_codes=install.BLOCK_CODES,
                     hold_codes=install.HOLD_CODES, version=install.CHECKER_VERSION)
            register("web_recipe", rc.drawbridge_rows, block_codes=rc.BLOCK_CODES,
                     hold_codes=frozenset(), version=rc.CHECKER_VERSION)
            done.append("drawbridge:static_web")
            done.append("drawbridge:web_recipe")
        _done.extend(done)
        return list(done)


def _reset_for_tests() -> None:
    from puffpai import build_abilities
    from puffpai.webapps import abilities
    with _LOCK:
        _done.clear()
    build_abilities.unregister_provider(abilities.rows)


__all__ = ["connect", "outside_rows", "registrable_domain", "OUTSIDE_ROWS_NAME"]
