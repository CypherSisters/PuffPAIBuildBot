"""
Consent requests — the "ask the user on the agent's behalf" loop.

When an agent action is blocked because a device has no per-device consent, the
Sentinel doesn't just deny: it files a consent request here, the request is
logged to the agent activity feed, and the assistant asks the user directly in
chat or on the dashboard. A yes/no reply resolves the request; approval is
recorded in the consent ledger as granted by the user (``by="user_via_chat"``),
never by the model on its own initiative. Only entities with an OPEN request can
be resolved, so the model cannot self-approve arbitrary devices.

Persists at ``$PUFFPAI_DATA_DIR/consent_requests.json`` and appends to the agent
activity feed at ``$PUFFPAI_DATA_DIR/agent-activity.jsonl``.

Neither file ever keeps a key or a password a caller handed over: every text
field is checked whole (``web_search.key_safe_text``) and ``context`` and the
feed's ``result`` string by string, key names too (``web_search.key_safe_data``);
such text is replaced by one plain placeholder. The entity id and the action
are lookup keys, never replaced by a placeholder: an entity id with Home
Assistant's entity-id shape is kept exactly (``web_search.checked_id``); any
other entity id, and any action, that looks like a key or a password is
refused (ValueError), and nothing about it is written. The feed lines built
here are made of parts that were each checked, and are not checked again as
a whole (a Home Assistant id inside one must not blank it).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from puffpai.web_search import (checked_id, id_looks_like_key, key_safe_data,
                                key_safe_text)

_DATA_DIR = os.environ.get(
    "PUFFPAI_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "data"),
)
REQUESTS_PATH = os.path.join(_DATA_DIR, "consent_requests.json")
ACTIVITY_LOG = os.path.join(_DATA_DIR, "agent-activity.jsonl")

_LOCK = threading.Lock()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict]:
    try:
        with open(REQUESTS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict]) -> None:
    os.makedirs(os.path.dirname(REQUESTS_PATH), exist_ok=True)
    tmp = REQUESTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    os.replace(tmp, REQUESTS_PATH)


def log_agent_activity(agent: str, event: str, task: str, status: str,
                       result: dict | None = None) -> None:
    """Append to the agent activity feed (same schema as the API server).
    Nothing that looks like a key or a password is written."""
    _append_activity(key_safe_text(agent), key_safe_text(event),
                     key_safe_text(task),
                     key_safe_text(status),
                     None if result is None else key_safe_data(result))


def _append_activity(agent: str, event: str, task: str, status: str,
                     result: dict | None) -> None:
    """Write one feed line from parts the caller has already checked."""
    entry: dict[str, Any] = {"agent": agent, "event": event, "task": task,
                             "status": status, "timestamp": _utcnow()}
    if result is not None:
        entry["result"] = result
    try:
        os.makedirs(os.path.dirname(ACTIVITY_LOG), exist_ok=True)
        with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # activity feed must never break the user path


def create_request(entity_id: str, label: str, action: str, reason: str,
                   requested_by: str = "sentinel", context: dict | None = None) -> dict:
    """File (or refresh) an open consent request for entity+action.

    Raises ValueError, writing nothing, when the entity id is refused by
    ``web_search.checked_id`` or the action looks like a key or a password
    (both are lookup keys: a placeholder there would match the wrong
    request)."""
    if id_looks_like_key(action):
        raise ValueError("consent request refused: its action looked like a "
                         "key or a password, so it was not kept")
    eid = checked_id(entity_id)
    if eid is None:
        raise ValueError("consent request refused: its entity id looked like "
                         "a key or a password, so it was not kept")
    entity_id = eid
    label = key_safe_text(label)
    reason = key_safe_text(reason)
    with _LOCK:
        items = _load()
        for req in items:
            if (req.get("entity_id") == entity_id
                    and req.get("action") == action
                    and req.get("status") == "open"):
                return req  # already pending — idempotent
        req = {
            "entity_id": entity_id,
            "label": label,
            "action": action,
            "reason": reason,
            "requested_by": key_safe_text(requested_by),
            "context": key_safe_data(context or {}),
            "status": "open",
            "created_at": _utcnow(),
        }
        items.append(req)
        _save(items)
    # Every part of this line was checked above; the whole is not checked
    # again (a Home Assistant id in it must not blank it).
    _append_activity(
        "sentinel", "consent_requested",
        f"Consent request: {action} for {label} ({entity_id})",
        "waiting_user",
        {"entity_id": entity_id, "reason": reason})
    return req


def list_open() -> list[dict]:
    return [r for r in _load() if r.get("status") == "open"]


def find_open(query: str = "") -> dict | None:
    """Match an open request by entity_id/label; empty query -> sole open one."""
    open_reqs = list_open()
    if not open_reqs:
        return None
    q = (query or "").strip().lower().strip("'\"")
    if not q:
        return open_reqs[0] if len(open_reqs) == 1 else None
    exact = [r for r in open_reqs if r["entity_id"].lower() == q]
    if exact:
        return exact[0]
    tokens = [t for t in q.replace(".", " ").replace("_", " ").split()
              if t not in ("camera", "cam", "the", "my", "device", "it",
                           "yes", "please", "approve", "that")]
    if not tokens:
        return open_reqs[0] if len(open_reqs) == 1 else None

    def score(r: dict) -> int:
        hay = f"{r.get('label','')} {r['entity_id']}".lower().replace("_", " ")
        return sum(1 for t in tokens if t in hay)

    scored = sorted(open_reqs, key=score, reverse=True)
    return scored[0] if score(scored[0]) > 0 else None


def resolve(entity_id: str, *, approved: bool, by: str = "user_via_chat",
            note: str = "") -> dict | None:
    """Close the open request and record the verdict in the consent ledger.

    An entity id refused by ``web_search.checked_id`` matches nothing: None,
    and nothing is written."""
    eid = checked_id(entity_id)
    if eid is None:
        return None
    entity_id = eid
    by = key_safe_text(by)
    note = key_safe_text(note) if note else note
    with _LOCK:
        items = _load()
        target = None
        for req in items:
            if req.get("entity_id") == entity_id and req.get("status") == "open":
                target = req
                break
        if target is None:
            return None
        target["status"] = "approved" if approved else "denied"
        target["resolved_by"] = by
        target["resolved_at"] = _utcnow()
        if note:
            target["note"] = note
        _save(items)

    from puffpai.security import consent_gate
    if approved:
        consent_gate.approve(entity_id, by=by)
    else:
        consent_gate.deny(entity_id, by=by)

    # Built from checked parts (a label filed before labels were checked is
    # checked here); the whole line is not checked again.
    label = (key_safe_text(target["label"]) if "label" in target
             else entity_id)
    _append_activity(
        "sentinel", "consent_resolved",
        f"Consent {'granted' if approved else 'denied'}: {label} ({entity_id})",
        "completed",
        {"entity_id": entity_id, "approved": approved, "by": by})
    return target
