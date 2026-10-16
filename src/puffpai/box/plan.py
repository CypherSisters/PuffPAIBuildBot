"""The locked box on the plan: its line, and setting it up after "Looks good".

* Section provider ``box`` (``proposal.register_provider``): when a line the
  finder put on the plan ``requires`` the box and the box is not ready, one
  line ``box:setup:locked-box`` (apply kind ``box_setup``), ticked. The
  proposal module's ``apply_requires`` keeps it and the lines that need it
  together (unticking it unticks them).
* Applier ``box_setup`` (plan and Add page): sets the box up under the plan's
  yes, bound to the approving device with a ``box_setup`` ticket. With no
  Windows' Linux feature it ends ``waiting`` with its plain words; the Turn
  it on tap is its own yes (``/box/install-wsl``).
* :func:`ensure_box_for`: a line that needs the box may come before the box
  line on the plan; it sets the box up first (once per build) when the plan
  approved the box line.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import importlib
from typing import Any

from puffpai.box import driver
from puffpai.box import wording as W

ITEM_ID = "box:setup:locked-box"
KIND = "box_setup"
SIZE_BYTES = 140 * 1024 * 1024
_MEMO_KEY = "box_setup_result"


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


def _needs_box(sctx: Any) -> int:
    parts = getattr(sctx, "parts", None)
    items = (parts or {}).get("items") if isinstance(parts, dict) else None
    return sum(1 for it in items or [] if isinstance(it, dict)
               and KIND in (it.get("requires") or ()))


def box_line(status: driver.BoxStatus) -> dict:
    detail = W.PLAN_BOX_DETAIL
    if status.state in ("no_wsl", "wsl_off"):
        detail = f"{W.PLAN_BOX_DETAIL_SHORT} {W.PLAN_BOX_WSL}"
    return {"id": ITEM_ID, "section": "box", "label": W.PLAN_BOX_LABEL,
            "detail": detail, "size_bytes": SIZE_BYTES,
            "outside_host": driver.ROOTFS_HOST, "default_on": True, "needs_yes": True,
            "apply": {"kind": KIND, "ref": "box:setup"}}


def items(sctx: Any) -> list[dict]:
    """The ``box`` section provider (``proposal.register_provider``)."""
    if not _needs_box(sctx):
        return []
    st = driver.status()
    if st.ready:
        return []
    if st.state == "unsupported":
        return [{"id": ITEM_ID, "section": "box", "label": "The locked box",
                 "detail": W.UNSUPPORTED, "size_bytes": None, "outside_host": None,
                 "default_on": False, "needs_yes": False,
                 "apply": {"kind": "none", "ref": "box:unsupported"}}]
    return [box_line(st)]


def _approved(ctx: Any) -> list[dict]:
    shared = getattr(ctx, "shared", None)
    state = shared.get("summary") if isinstance(shared, dict) else None
    proposal = _optional("puffpai.wizard.proposal")
    fn = getattr(proposal, "approved_items", None) if proposal is not None else None
    if not callable(fn):
        return []
    try:
        return list(fn(state) or [])
    except Exception:
        return []


def _box_line_approved(ctx: Any) -> bool:
    return any((it.get("apply") or {}).get("kind") == KIND for it in _approved(ctx))


def _waiting_count(ctx: Any) -> int:
    n = sum(1 for it in _approved(ctx) if KIND in (it.get("requires") or ()))
    return max(n, len(driver.waiting()), 1)


def _device(ctx: Any) -> str:
    device = getattr(ctx, "device_id", None)
    if isinstance(device, str) and device:
        return device
    from puffpai.security import consent_tickets
    return consent_tickets.terminal_device_id()


def _set_up(ctx: Any) -> driver.BoxStatus:
    """Set the box up under the plan's yes (once per build)."""
    shared = getattr(ctx, "shared", None)
    memo = shared.get(_MEMO_KEY) if isinstance(shared, dict) else None
    if isinstance(memo, dict) and memo.get("state") == "ready":
        return driver.BoxStatus("ready", W.READY, "", str(memo.get("distro") or ""))
    ct = _optional("puffpai.security.consent_tickets")
    if ct is None or not callable(getattr(ct, "target_box", None)):
        return driver.BoxStatus("no_box", W.SETUP_NOT_IN_BUILD, "")
    distro = driver.distro_name()
    device = _device(ctx)
    try:
        ticket = ct.mint("box_setup", ct.target_box(distro),
                         "Set up the locked box for outside code, as the plan said.", device)
    except (ValueError, getattr(ct, "TicketRefused", ValueError)) as exc:
        message = getattr(exc, "message", "") or W.SETUP_NOT_IN_BUILD
        return driver.BoxStatus("no_box", message, "")
    progress = getattr(ctx, "progress", None)
    st = driver.setup(ticket=ticket, device_id=device,
                      progress=progress if callable(progress) else (lambda line: None))
    if isinstance(shared, dict):
        shared[_MEMO_KEY] = {"state": st.state, "distro": st.distro}
    return st


def apply_box_setup(item: dict, ctx: Any) -> dict:
    """Plan applier for ``box_setup``."""
    st = driver.status()
    if st.ready:
        return {"status": "added", "line": W.SETUP_ALREADY, "lines": [W.SETUP_ALREADY]}
    if st.state in ("no_wsl", "wsl_off"):
        n = _waiting_count(ctx)
        line = W.WSL_NEEDED_ONE if n == 1 else W.WSL_NEEDED.format(n=n)
        return {"status": "waiting", "line": line, "lines": [line]}
    if getattr(ctx, "online", True) is False:
        return {"status": "failed", "line": W.SETUP_OFFLINE, "lines": [W.SETUP_OFFLINE]}
    st = _set_up(ctx)
    if st.ready:
        return {"status": "added", "line": W.SETUP_DONE, "lines": [W.SETUP_DONE]}
    line = W.SETUP_FAILED.format(message=st.message)
    return {"status": "failed", "line": line, "lines": [line]}


def ensure_box_for(item: dict, ctx: Any) -> driver.BoxStatus:
    """The box's status for a line that needs it, setting it up first when
    the plan approved the box line (whatever order the lines come in)."""
    st = driver.status()
    if st.ready or st.state in ("no_wsl", "wsl_off"):
        return st
    if st.state == "no_box" and _box_line_approved(ctx) and getattr(ctx, "online", True):
        return _set_up(ctx)
    return st


__all__ = ["ITEM_ID", "KIND", "apply_box_setup", "box_line", "ensure_box_for", "items"]
