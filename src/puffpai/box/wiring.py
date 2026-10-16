"""Connect the locked box to the rest of the bot (``factory_wiring.connect``).

Registers, each only when the other part is in this build (design I-8):

* the plan section provider ``box`` (``proposal.register_provider``),
* the appliers ``box_setup`` and ``cli_tool`` (``factory.register_plan_applier``
  and ``add_more.register_applier``), and the waiting-item applier for
  ``cli_tool`` (things that waited for Windows' Linux feature),
* Drawbridge's checkers ``box_setup`` and ``cli_tool``,
* the action source ``cli`` (``integrate.actions.register_source``),
* the outside-service rows for box projects (``outside_services.register_rows``).

What was registered is kept in :data:`REGISTERED`, so a part that is missing
is visible (and the tools stay usable from ``puffpai box`` commands). Safe to
call more than once.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import importlib
import threading
from typing import Any

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

OUTSIDE_ROWS_NAME = "box"
_LOCK = threading.Lock()
#: What :func:`connect` registered, and what was not in this build.
REGISTERED: list[str] = []


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


def outside_rows() -> list[dict[str, Any]]:
    """One row per box project that may reach the internet while it runs.

    Box tools run with no internet (their run-time list is empty), so this
    is usually empty; install traffic is logged line by line in "What leaves
    your computer" under the project's own service name."""
    from puffpai.box import cli_tools, driver
    rec_mod = _optional("puffpai.integrate.records")
    if rec_mod is None:
        return []
    out: list[dict[str, Any]] = []
    for rec in rec_mod.all():
        if rec.get("mode") != cli_tools.KIND or rec.get("status") != "ready":
            continue
        data = cli_tools._load_json(cli_tools._cli_path(rec["id"])) or {}  # noqa: SLF001
        hosts = sorted({h for s in data.get("specs") or [] if isinstance(s, dict)
                        for h in s.get("allow_hosts") or [] if isinstance(h, str)})
        if not hosts:
            continue
        out.append({"id": driver.service_for(rec["id"]), "name": str(rec.get("name") or ""),
                    "for": "what the tool sends", "what_leaves": "what the tool sends",
                    "websites": hosts, "state": "off", "state_line": "Switched off.",
                    "can_switch": False, "switch_to": None, "why_cannot_switch": "",
                    "turned_on_by": "", "turned_on_at": "", "kind": "box",
                    "last_used": "", "today": {"sent": 0, "stopped": 0}})
    return out


def connect() -> list[str]:
    with _LOCK:
        if REGISTERED:
            return list(REGISTERED)
        from puffpai import add_more, drawbridge
        from puffpai.box import cli_tools, driver, plan
        done: list[str] = []
        proposal = _optional("puffpai.wizard.proposal")
        if proposal is not None and callable(getattr(proposal, "register_provider", None)):
            proposal.register_provider("box", plan.items)
            done.append("provider:box")
        else:
            done.append("provider:box not in this build yet")
        factory = _optional("puffpai.wizard.factory")
        reg_plan = getattr(factory, "register_plan_applier", None) if factory else None
        for kind, fn in ((plan.KIND, plan.apply_box_setup), (cli_tools.KIND, cli_tools.apply_cli_tool)):
            if callable(reg_plan):
                reg_plan(kind, fn)
                done.append(f"plan_applier:{kind}")
            else:
                done.append(f"plan_applier:{kind} not in this build yet")
            add_more.register_applier(kind, fn)
            done.append(f"add_more:{kind}")
        driver.register_waiting_applier(cli_tools.KIND, cli_tools.apply_waiting)
        done.append(f"waiting:{cli_tools.KIND}")
        register = getattr(drawbridge, "register_checker", None)
        if callable(register):
            register(plan.KIND, driver.box_setup_rows, block_codes=driver.BOX_SETUP_BLOCK,
                     hold_codes=frozenset(), version="r3.1")
            register(cli_tools.KIND, cli_tools.cli_tool_rows, block_codes=cli_tools.BLOCK_CODES,
                     hold_codes=cli_tools.HOLD_CODES, version=cli_tools.CHECKER_VERSION)
            done += [f"drawbridge:{plan.KIND}", f"drawbridge:{cli_tools.KIND}"]
        ia = _optional("puffpai.integrate.actions")
        if ia is not None and callable(getattr(ia, "register_source", None)):
            ia.register_source(cli_tools.SOURCE_KIND, cli_tools.list_actions, cli_tools.run_action)
            done.append(f"actions:{cli_tools.SOURCE_KIND}")
        else:
            done.append(f"actions:{cli_tools.SOURCE_KIND} not in this build yet")
        os_mod = _optional("puffpai.outside_services")
        reg_rows = getattr(os_mod, "register_rows", None) if os_mod is not None else None
        if callable(reg_rows):
            reg_rows(OUTSIDE_ROWS_NAME, outside_rows)
            done.append("outside:box")
        else:
            done.append("outside:box not in this build yet")
        REGISTERED.extend(done)
        return list(done)


def _reset_for_tests() -> None:
    with _LOCK:
        REGISTERED.clear()


__all__ = ["OUTSIDE_ROWS_NAME", "REGISTERED", "connect", "outside_rows"]
