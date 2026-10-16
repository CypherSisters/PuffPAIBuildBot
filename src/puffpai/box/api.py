"""HTTP routes for the locked box (registered on the backend by ``api_server``).

Every route sits behind the backend's API-key hook (none is public)::

    GET  /box/status               where the box stands, plus what waits for it
    POST /box/setup                set it up (the owner's yes, from a paired device)
    POST /box/install-wsl          ask Windows to turn its Linux feature on
    POST /box/finish               set it up and add what was waiting
    GET  /box/progress             the lines of the running setup or finish
    POST /box/tools/<id>/folder    {"path", "mode"} give a command-line tool a folder

A yes is accepted only from a paired device (``device:<id>`` as the dashboard
proxy asserts it): the route mints and redeems the ticket for that device
(``box_setup`` for ``target_box(distro)``, ``folder_give`` for
``target_folder(id, realpath, mode)``), so a yes is bound to the device that
gave it, and owner-only when the household has an owner.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from puffpai.box import driver
from puffpai.box import wording as W
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("box", __name__)

NOT_PAIRED = ("This device is not paired with your bot, so it cannot say yes to "
              "this.")
BUSY = "The locked box is already being set up. Wait for it to finish."

_job_lock = threading.Lock()
_job: dict[str, Any] = {"running": False, "lines": [], "done": False, "status": None,
                        "kind": "", "started": 0.0}


def _device() -> str:
    try:
        from puffpai import api_server
        client = str(api_server._get_chat_client_id() or "")  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return ""
    return client if client.startswith("device:") else ""


def _box_ticket(device: str, sentence: str) -> tuple[str, str]:
    """(ticket, "") or ("", plain refusal)."""
    from puffpai.security import consent_tickets as ct
    distro = driver.distro_name()
    try:
        return ct.mint("box_setup", ct.target_box(distro), sentence, device), ""
    except ct.TicketRefused as exc:
        return "", exc.message
    except ValueError:
        return "", W.TAKEN.format(distro=distro)


def _start(kind: str, work: Callable[[Callable[[str], None]], Any]) -> bool:
    with _job_lock:
        if _job["running"]:
            return False
        _job.update({"running": True, "lines": [], "done": False, "status": None,
                     "kind": kind, "started": time.time()})

    def say(line: str) -> None:
        with _job_lock:
            _job["lines"] = (_job["lines"] + [str(line)[:300]])[-100:]

    def body() -> None:
        try:
            got = work(say)
        except Exception as exc:  # noqa: BLE001 - a job never dies silently
            logger.warning("box %s failed: %s", kind, type(exc).__name__)
            got = None
            say("Something went wrong. Nothing half-made was kept.")
        with _job_lock:
            _job.update({"running": False, "done": True,
                         "status": got.as_dict() if isinstance(got, driver.BoxStatus) else got})
    threading.Thread(target=body, name=f"box-{kind}", daemon=True).start()
    return True


@bp.route("/box/status", methods=["GET"])
def box_status():
    st = driver.status()
    body = st.as_dict()
    body["waiting"] = len(driver.waiting())
    body["waiting_lines"] = driver.waiting_lines()
    declined = driver.wsl_install_outcome()
    if declined.get("declined"):
        body["wsl_declined"] = declined["message"]
    return jsonify(body)


@bp.route("/box/setup", methods=["POST"])
def box_setup():
    device = _device()
    if not device:
        return jsonify({"ok": False, "message": NOT_PAIRED}), 403
    ticket, why = _box_ticket(device, "Set up the locked box for outside code.")
    if not ticket:
        return jsonify({"ok": False, "message": why}), 403
    started = _start("setup", lambda say: driver.setup(ticket=ticket, device_id=device,
                                                       progress=say))
    if not started:
        return jsonify({"ok": False, "message": BUSY}), 409
    return jsonify({"ok": True, "started": True})


@bp.route("/box/install-wsl", methods=["POST"])
def box_install_wsl():
    device = _device()
    if not device:
        return jsonify({"ok": False, "message": NOT_PAIRED}), 403
    ticket, why = _box_ticket(device, "Turn on Windows' Linux feature for the locked box.")
    if not ticket:
        return jsonify({"ok": False, "message": why}), 403
    got = driver.install_wsl(ticket=ticket, device_id=device)
    return jsonify({"ok": bool(got.get("started")), "message": got.get("message")})


@bp.route("/box/finish", methods=["POST"])
def box_finish():
    device = _device()
    if not device:
        return jsonify({"ok": False, "message": NOT_PAIRED}), 403
    ticket, why = _box_ticket(device, "Set up the locked box and add what was waiting.")
    if not ticket:
        return jsonify({"ok": False, "message": why}), 403
    from puffpai.security import consent_tickets as ct
    ok, verdict = ct.redeem(ticket, device, "box_setup", ct.target_box(driver.distro_name()))
    if not ok:
        return jsonify({"ok": False, "message": ct.refusal_words(verdict)}), 403
    started = _start("finish", lambda say: driver.finish_waiting(progress=say))
    if not started:
        return jsonify({"ok": False, "message": BUSY}), 409
    return jsonify({"ok": True, "started": True})


@bp.route("/box/progress", methods=["GET"])
def box_progress():
    with _job_lock:
        return jsonify({"running": _job["running"], "done": _job["done"],
                        "kind": _job["kind"], "lines": list(_job["lines"]),
                        "status": _job["status"]})


@bp.route("/box/tools/<integration_id>/folder", methods=["POST"])
def box_tool_folder(integration_id: str):
    from puffpai.box import cli_tools
    from puffpai.security import consent_tickets as ct
    from puffpai.security import owner_folders
    device = _device()
    if not device:
        return jsonify({"ok": False, "message": NOT_PAIRED}), 403
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    mode = body.get("mode")
    path = body.get("path") if isinstance(body.get("path"), str) else ""
    if mode not in ("ro", "rw"):
        return jsonify({"ok": False, "message": W.FOLDER_BAD_MODE}), 400
    rec_mod = cli_tools._records()  # noqa: SLF001
    rec = rec_mod.get(integration_id) if rec_mod is not None and rec_mod.valid_id(
        integration_id) else None
    if not rec or rec.get("mode") != cli_tools.KIND:
        return jsonify({"ok": False, "message": W.FOLDER_NOT_A_TOOL}), 404
    if mode == "rw":
        try:
            real = owner_folders.write_folder("BuildBot", str(rec.get("name") or ""))
        except PermissionError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 400
    else:
        real, why = owner_folders.check(path, "ro")
        if not real:
            return jsonify({"ok": False, "message": why}), 400
    try:
        ticket = ct.mint("folder_give", ct.target_folder(integration_id, real, mode),
                         f"Give {rec.get('name')} this folder.", device)
    except ct.TicketRefused as exc:
        return jsonify({"ok": False, "message": exc.message}), 403
    got = cli_tools.grant_folder(integration_id, real, mode, ticket=ticket, device_id=device)
    return jsonify(got), (200 if got.get("ok") else 400)


__all__ = ["bp"]
