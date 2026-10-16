"""HTTP routes for the owner's data folder and linked folders (Knowledge page).

Registered on the backend app by ``api_server`` (one line), so every route
sits behind the same API-key check, rate limit and CORS rules as the rest.
The logic is :mod:`puffpai.knowledge.my_data`.

Routes::

    GET    /knowledge/my-data           the data folder, linked folders, file
                                        counts, last scan, waiting for the vault
    POST   /knowledge/data-folder       {folder_path}: use this folder from now on
    POST   /knowledge/folders           {folder_path, name?}: link a folder
    DELETE /knowledge/folders/<id>      unlink it (its files leave the index;
                                        the folder itself is never touched)
    POST   /knowledge/my-files/upload   multipart ``files`` (many), with a
                                        ``relative_path`` field per file for a
                                        whole folder: saved into My files and
                                        added at once
    POST   /knowledge/my-data/scan      look at the folders again now

Changing the folder or linking one needs a paired device, the same gate as
choosing where the vault lives. The folder picker is the vault step's own
(``GET /vault/location/browse``: folder names only). Path fields end in
``_path`` so the pasted-key check reads them as paths, not as keys.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("knowledge_folders", __name__)

_NOT_PAIRED = ("Pair this device first. Your data folder and linked folders "
               "can only be changed from a paired device.")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_FILES = 500
_ID = re.compile(r"^lf[0-9a-f]{6,16}$")


def _paired():
    from puffpai import outside_api
    if not outside_api.paired_device_id():
        return jsonify({"ok": False, "code": "not_paired", "message": _NOT_PAIRED}), 403
    return None


def _body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


@bp.route("/knowledge/my-data", methods=["GET"])
def my_data_status():
    from puffpai.knowledge import my_data
    return jsonify({"ok": True, **my_data.status()})


@bp.route("/knowledge/data-folder", methods=["POST"])
def data_folder_set():
    bad = _paired()
    if bad:
        return bad
    from puffpai.knowledge import my_data
    body = _body()
    out = my_data.set_data_dir(str(body.get("folder_path") or body.get("folder") or ""))
    return jsonify(out), (200 if out.get("ok") else 400)


@bp.route("/knowledge/folders", methods=["GET"])
def folders_list():
    from puffpai.knowledge import my_data
    return jsonify({"ok": True, "folders": my_data.linked_folders()})


@bp.route("/knowledge/folders", methods=["POST"])
def folders_link():
    bad = _paired()
    if bad:
        return bad
    from puffpai.knowledge import my_data
    body = _body()
    out = my_data.link_folder(str(body.get("folder_path") or ""),
                              str(body.get("name") or ""))
    return jsonify(out), (200 if out.get("ok") else 400)


@bp.route("/knowledge/folders/<folder_id>", methods=["DELETE"])
def folders_unlink(folder_id):
    bad = _paired()
    if bad:
        return bad
    if not _ID.match(str(folder_id or "")):
        return jsonify({"ok": False, "message": "No linked folder has that id."}), 404
    from puffpai.knowledge import my_data
    out = my_data.unlink_folder(folder_id)
    return jsonify(out), (200 if out.get("ok") else 404)


@bp.route("/knowledge/my-data/scan", methods=["POST"])
def my_data_scan():
    from puffpai.knowledge import my_data
    my_data.poke()
    return jsonify({"ok": True})


@bp.route("/knowledge/my-files/upload", methods=["POST"])
def my_files_upload():
    """Many files at once, or a whole folder. Each lands in My files and
    goes through the same checks as any upload."""
    from puffpai.knowledge import my_data
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify({"ok": False, "message": "No file came with that."}), 400
    if len(files) > MAX_FILES:
        return jsonify({"ok": False, "message": f"At most {MAX_FILES} files at a "
                        "time. Link the folder instead and it is read where it is."}), 413
    relative = request.form.getlist("relative_path")
    results = []
    for n, f in enumerate(files):
        name = str(f.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not name:
            continue
        data = f.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            results.append({"filename": name, "status": "too_big",
                            "message": "Too big to add here (over 25 MB). Put it in "
                                       "your data folder and it is looked at there."})
            continue
        if not data:
            results.append({"filename": name, "status": "empty",
                            "message": "That file is empty."})
            continue
        rel = relative[n] if n < len(relative) else ""
        try:
            out = my_data.add_upload(data, name, relative=rel, auto_apply=False,
                                     hint="notes")
        except Exception as exc:  # noqa: BLE001 - one bad file never stops the rest
            logger.info("upload of %s failed: %s", name, type(exc).__name__)
            results.append({"filename": name, "status": "unreadable",
                            "message": "That file could not be added."})
            continue
        results.append({"filename": name, "status": out.get("status"),
                        "saved_to": out.get("saved_to"),
                        "message": out.get("error") or " ".join(out.get("actions_taken") or [])})
    counts: dict[str, int] = {}
    for r in results:
        counts[str(r["status"])] = counts.get(str(r["status"]), 0) + 1
    return jsonify({"ok": True, "results": results, "counts": counts})


__all__ = ["bp"]
