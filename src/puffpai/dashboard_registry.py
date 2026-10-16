"""
PuffPAI Dashboard Tile Registry — dynamic camera tiles.

Agent flow:
  - The agent decides to add a camera tile.
  - The HA bridge resolves the user's words to a real Home Assistant camera
    entity — closed-world: if HA doesn't expose it, nothing gets added.
  - The consent ledger (security.consent_gate) must have approved the entity;
    otherwise a consent request is filed and the assistant asks the user. Every
    verdict is audited to security_logs/dashboard_tile_reviews.jsonl.

The web dashboard reads tiles via GET /dashboard/cameras (api_server).
Registry persists at data/dashboard_cameras.json.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REGISTRY_PATH = os.path.join(_REPO_ROOT, "data", "dashboard_cameras.json")
_AUDIT_PATH = os.path.join(_REPO_ROOT, "security_logs", "dashboard_tile_reviews.jsonl")

_lock = threading.Lock()

# No cameras are seeded by default — the dashboard starts empty and the user
# adds their own Home Assistant camera entities through the consent flow.
_SEED: list[dict] = []


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> list[dict]:
    if os.path.exists(_REGISTRY_PATH):
        try:
            with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, IOError):
            pass
    return [dict(x) for x in _SEED]


def _save(items: list[dict]) -> None:
    os.makedirs(os.path.dirname(_REGISTRY_PATH), exist_ok=True)
    tmp = _REGISTRY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _REGISTRY_PATH)


def _audit(verdict: str, entity_id: str, reasons: list[str], requested_by: str,
           task_text: str = "") -> None:
    """One line in the tile review log. Nothing in it keeps a key or a
    password, or a piece of one: each text a caller gave is checked WHOLE
    before it is cut (web_search.key_safe_text), and the id goes by the id
    rule (web_search.checked_id): a Home Assistant entity id is written as
    it is; any other id that looks like a key is refused, and then no line
    is written at all (the verdict itself is unchanged)."""
    try:
        from puffpai.web_search import checked_id, key_safe_text
        eid = checked_id(entity_id)
        if eid is None:
            return
        line = json.dumps({
            "ts": _utcnow(),
            "action": "dashboard_add_camera",
            "verdict": verdict,
            "entity_id": eid,
            "reasons": [key_safe_text(r) for r in (reasons or [])],
            "requested_by": key_safe_text(requested_by),
            "task_text": key_safe_text(task_text, 300),
        })
    except Exception:  # never the raw text instead; the verdict stands
        return
    try:
        os.makedirs(os.path.dirname(_AUDIT_PATH), exist_ok=True)
        with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except IOError:
        pass  # auditing must never break the user path


def list_cameras() -> list[dict]:
    with _lock:
        return _load()


def remove_camera(entity_id: str) -> bool:
    with _lock:
        items = _load()
        remaining = [x for x in items if x.get("entity_id") != entity_id]
        if len(remaining) == len(items):
            return False
        _save(remaining)
        return True


def _default_label(friendly_name: str | None, entity_id: str) -> str:
    if friendly_name:
        return friendly_name
    name = entity_id.split(".", 1)[-1]
    return name.replace("_", " ").title()


def find_camera_entity(bridge, query: str):
    """Bridge step: resolve a user phrase to HA camera DeviceInfo objects.

    Returns a list of matching DeviceInfo (camera domain only). Closed-world:
    only entities Home Assistant actually exposes can ever match.
    """
    # controllable_only=False: cameras aren't in CONTROLLABLE_DOMAINS but we
    # only *view* them, never actuate.
    try:
        devices = bridge.list_devices(domain="camera", controllable_only=False)
    except TypeError:  # mock/legacy bridges without the kwargs
        devices = bridge.list_devices()
    devices = [d for d in devices if d.entity_id.startswith("camera.")]
    q = (query or "").strip().lower().strip("'\"")
    if not q:
        return devices
    # Exact entity_id
    exact = [d for d in devices if d.entity_id.lower() == q]
    if exact:
        return exact
    # Token overlap on friendly name / entity id
    tokens = [t for t in q.replace(".", " ").replace("_", " ").split()
              if t not in ("camera", "cam", "the", "my", "new", "a", "feed")]
    if not tokens:
        return devices

    def score(d) -> int:
        hay = f"{d.friendly_name} {d.entity_id}".lower().replace("_", " ")
        return sum(1 for t in tokens if t in hay)

    scored = sorted(((score(d), d) for d in devices), key=lambda x: -x[0])
    best = [d for s, d in scored if s > 0 and s == scored[0][0]]
    return best


def sentinel_check(entity_id: str, requested_by: str, task_text: str = "",
                   label: str | None = None) -> tuple[bool, str]:
    """Consent step: verdict for exposing a camera tile.

    Approved per-device consent -> allow; anything else -> deny, file a consent
    request, and have the assistant ask the user. Every verdict is audited.
    """
    try:
        from puffpai.security.consent_gate import get_ledger
        status = get_ledger().status(entity_id)
    except Exception as exc:  # consent ledger unavailable — fail closed
        _audit("deny", entity_id, [f"consent ledger unavailable: {exc}"],
               requested_by, task_text)
        return False, f"Could not verify consent for {entity_id}: {exc}"

    if status == "approved":
        _audit("allow", entity_id, ["per-device consent on file"],
               requested_by, task_text)
        return True, "approved (per-device consent)"
    # No consent yet — don't dead-end: file a consent request and have the
    # assistant ask the user for permission.
    _audit("deny", entity_id, [f"consent status: {status} — consent request filed"],
           requested_by, task_text)
    nice = label or entity_id
    try:
        from puffpai.security import consent_requests as _creq
        from puffpai.web_search import key_safe_text
        _creq.create_request(
            entity_id, nice, "dashboard_add_camera",
            f"no per-device consent (status: {status})",
            requested_by="sentinel",
            # Checked whole for a key before the cut, never cut first.
            context={"requested_by": requested_by,
                     "task_text": key_safe_text(task_text, 200)})
    except Exception:
        pass
    return False, (
        f"NEEDS USER PERMISSION: {nice} ({entity_id}) has no per-device consent "
        f"yet, so a consent request was filed. Ask the user directly: 'I need "
        f"your permission to show {nice} on the dashboard. Should I approve it?'")


def add_camera_validated(bridge, query: str, label: str | None = None,
                         added_by: str = "agent", task_text: str = "") -> tuple[bool, str]:
    """Full Bridge -> consent -> registry pipeline. Returns (ok, message)."""
    from puffpai.ha_bridge import HABridgeError
    try:
        matches = find_camera_entity(bridge, query)
    except HABridgeError as exc:
        return False, f"Home Assistant is unreachable: {exc}"

    if not matches:
        _audit("deny", query or "(none)",
               ["no matching camera entity in Home Assistant (closed world)"],
               added_by, task_text)
        return False, (
            f"I couldn't find a camera matching '{query}' in Home Assistant. "
            "If your camera integration sees it but HA doesn't yet, reload the "
            "integration or check whether the new entity is disabled, then ask "
            "me again."
        )

    on_dash = {x["entity_id"] for x in list_cameras()}
    new = [d for d in matches if d.entity_id not in on_dash]
    if not new:
        only = matches[0]
        return False, (f"{only.friendly_name} ({only.entity_id}) is already "
                       "on the dashboard.")
    if len(new) > 1:
        opts = "\n".join(f"  - {d.friendly_name} ({d.entity_id})" for d in new[:6])
        return False, ("More than one camera matches. Which one?\n" + opts)

    dev = new[0]
    ok, verdict = sentinel_check(dev.entity_id, added_by, task_text,
                                 label=_default_label(dev.friendly_name, dev.entity_id))
    if not ok:
        return False, verdict

    entry = {
        "entity_id": dev.entity_id,
        "label": label or _default_label(dev.friendly_name, dev.entity_id),
        "added_by": added_by,
        "added_at": _utcnow(),
    }
    with _lock:
        items = _load()
        items.append(entry)
        _save(items)
    return True, (f"Done, {entry['label']} ({dev.entity_id}) is now live on "
                  f"the dashboard. Consent verdict: {verdict}.")
