"""HTTP routes for Toolsmith's model (``toolsmith.model``): this computer or Anthropic.

Registered on the backend app by ``api_server`` (one line), behind the same
API-key check, rate limit and CORS rules as every route; each route also
needs a PAIRED device (403 otherwise). GET changes nothing.

Routes::

    GET  /toolsmith/model             where Toolsmith thinks, the model, the cost so far
    POST /toolsmith/model             {where: "local"} or {where: "cloud", model, key?}
    POST /toolsmith/model/keep        {keep: "1h" | "4h" | "lock"} how long its key stays ready
    POST /toolsmith/model/forget-key  back to local and the key out of the vault

The key travels only in the POST body (``pasted_key_guard`` lets it through
for this one field only), goes straight to the vault (``keys.add_key``) and
is never echoed. A key in the address is refused.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

bp = Blueprint("toolsmith_model", __name__)

NOT_PAIRED = "This has to be done from a browser that is paired with your bot."
_KEYISH_ARGS = ("key", "token", "value", "secret", "api_key")


def _device() -> str:
    from puffpai import outside_api
    return outside_api.paired_device_id()


def _guard():
    if any(name in request.args for name in _KEYISH_ARGS):
        words = "A key is never sent in the address. Nothing was kept."
        return jsonify({"ok": False, "error": words, "message": words}), 400
    if not _device():
        return jsonify({"ok": False, "error": NOT_PAIRED, "message": NOT_PAIRED}), 403
    return None


def _refused(exc) -> tuple:
    return jsonify({"ok": False, "code": exc.code, "error": exc.message,
                    "message": exc.message}), exc.status


@bp.route("/toolsmith/model", methods=["GET"])
def toolsmith_model_get():
    from puffpai.security import toolsmith_cloud
    bad = _guard()
    if bad:
        return bad
    return jsonify({"ok": True, **toolsmith_cloud.status()})


@bp.route("/toolsmith/model", methods=["POST"])
def toolsmith_model_set():
    from puffpai.security import toolsmith_cloud
    bad = _guard()
    if bad:
        return bad
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    where = data.get("where")
    key = data.get("key")
    data = {k: v for k, v in data.items() if k != "key"}
    try:
        if where == "local":
            return jsonify({"ok": True, **toolsmith_cloud.use_local()})
        if where != "cloud":
            words = "Choose this computer or Anthropic."
            return jsonify({"ok": False, "error": words, "message": words}), 400
        if key in (None, "") and toolsmith_cloud.in_use():
            return jsonify({"ok": True, **toolsmith_cloud.set_model(data.get("model"))})
        try:
            # The wizard's "still under way" probe is set when the wizard is
            # loaded; make sure it is before the key's lease starts.
            from puffpai.wizard import service as _wizard  # noqa: F401
        except Exception:  # noqa: BLE001 - without it her keep time runs as before
            pass
        return jsonify({"ok": True, **toolsmith_cloud.choose_cloud(
            data.get("model"), _device(), key=key)})
    except toolsmith_cloud.CloudError as exc:
        return _refused(exc)
    finally:
        key = None


@bp.route("/toolsmith/model/keep", methods=["POST"])
def toolsmith_model_keep():
    """{keep: "1h" | "4h" | "lock"}: how long Toolsmith's key stays ready
    while she builds. "lock" still ends after 24 hours."""
    from puffpai.security import toolsmith_cloud
    bad = _guard()
    if bad:
        return bad
    data = request.get_json(silent=True)
    keep = data.get("keep") if isinstance(data, dict) else None
    try:
        return jsonify({"ok": True, **toolsmith_cloud.set_keep(keep)})
    except toolsmith_cloud.CloudError as exc:
        return _refused(exc)


@bp.route("/toolsmith/model/forget-key", methods=["POST"])
def toolsmith_model_forget_key():
    from puffpai.security import toolsmith_cloud
    bad = _guard()
    if bad:
        return bad
    return jsonify({"ok": True, **toolsmith_cloud.forget_key()})


__all__ = ["bp"]
