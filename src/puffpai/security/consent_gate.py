"""Consent gate — human-in-the-loop device/entity consent ledger.

The Sentinel agent asks this module to rule on a state-changing request: which
entities it would touch (:func:`gate_targets`) and, for an LLM-authored
automation, whether the automation itself is safe (:func:`sentinel_review`).

What this build does — and does not
-----------------------------------
This is the **public reference** consent layer. It contains **no policy
heuristics**: it does not reason about blast radius, safety-critical domains, or
automation schemas. Those live in the appliance and are not part of this build.

Instead it does the one thing a public build can do safely and transparently:
it keeps a **persistent, default-pending consent ledger**. Every new device or
entity is recorded as ``pending`` the first time it is seen and stays pending —
routed back as ``flag`` (allowed-but-noted) — until a **human** approves or
denies it via :func:`approve` / :func:`deny`. Approved entities pass; denied
entities are refused; anything unknown is flagged. Every decision and every
sighting is appended to a JSONL audit trail.

The *structural* guarantees of the build do not depend on this module: the bus
still routes every state-changing envelope through Sentinel and fails closed,
and Sentinel still hard-denies unbudgeted egress and quarantined targets. This
module only supplies the content-consent verdict.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from puffpai.web_search import KEY_TEXT_PLACEHOLDER, checked_id, key_safe_text

_DATA_DIR = os.environ.get(
    "PUFFPAI_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)
CONSENT_LEDGER = os.path.join(_DATA_DIR, "consent_ledger.jsonl")
CONSENT_STATE = os.path.join(_DATA_DIR, "consent_state.json")

# Consent statuses.
PENDING = "pending"
APPROVED = "approved"
DENIED = "denied"

_PENDING_NOTE = (
    "consent pending: device/entity not yet approved by a human, recorded for "
    "review"
)
_REFUSED_NOTE = ("a target id looked like a key or a password, so it was "
                 "refused and not kept")

# No text a caller gives is ever kept with a key or a password in it, or a
# piece of one: every free-text field is checked WHOLE before it is cut
# (web_search.key_safe_text). An entity id is a lookup key, so it is never
# replaced by a placeholder (web_search.checked_id): an id with Home
# Assistant's entity-id shape is kept exactly as Home Assistant writes it;
# any other id that looks like a key or a password (or that the check
# cannot read) is refused, nothing about it is written, and it is treated
# as denied. Stored rows are never rewritten because of the check.


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConsentLedger:
    """A persistent, human-driven consent ledger for devices and entities.

    State lives in a JSON file (``consent_state.json``); every sighting and
    decision is also appended to a JSONL audit trail (``consent_ledger.jsonl``).
    Both default to the configured data dir. The ledger holds no policy of its
    own — a human's :meth:`approve` / :meth:`deny` calls are the only thing that
    moves an entity out of ``pending``.
    """

    def __init__(self, state_path: Optional[str] = None,
                 audit_path: Optional[str] = None) -> None:
        self._state_path = state_path or CONSENT_STATE
        self._audit_path = audit_path or CONSENT_LEDGER
        self._lock = threading.Lock()
        self._state: dict[str, dict] = self._load()

    # ── persistence ──────────────────────────────────────────────────────
    def _load(self) -> dict[str, dict]:
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        """Persist the state file atomically. Never raises."""
        try:
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
            tmp = f"{self._state_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, default=str)
            os.replace(tmp, self._state_path)
        except OSError:
            pass

    def _audit(self, entry: dict) -> None:
        """Append an entry to the JSONL audit trail. Never raises."""
        try:
            os.makedirs(os.path.dirname(self._audit_path), exist_ok=True)
            with open(self._audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            pass

    # ── core API ─────────────────────────────────────────────────────────
    def record(self, entity_id: str, *, source: str = "agent",
               context: str = "") -> str:
        """Ensure ``entity_id`` is tracked; register it as pending if new.

        Returns the entity's current status (``pending`` / ``approved`` /
        ``denied``). An id refused by :func:`~puffpai.web_search.checked_id`
        (it looks like a key or a password) is not recorded: ``denied``.
        """
        eid = checked_id(entity_id)
        if eid is None:
            return DENIED
        with self._lock:
            row = self._state.get(eid)
            if row is None:
                source = key_safe_text(source)
                row = {
                    "status": PENDING,
                    "first_seen": _utcnow(),
                    "source": source,
                    "context": key_safe_text(context, 200),
                    "decided_at": None,
                }
                self._state[eid] = row
                self._save()
                self._audit({"event": "seen", "entity_id": eid,
                             "status": PENDING, "source": source,
                             "at": row["first_seen"]})
            return str(row["status"])

    def status(self, entity_id: str) -> str:
        """Return the current status of ``entity_id`` (``pending`` if unknown;
        ``denied`` for an id :func:`~puffpai.web_search.checked_id` refuses)."""
        eid = checked_id(entity_id)
        if eid is None:
            return DENIED
        row = self._state.get(eid)
        return str(row["status"]) if row else PENDING

    def approve(self, entity_id: str, *, by: str = "owner") -> dict:
        """Human approval: mark ``entity_id`` approved."""
        return self._decide(entity_id, APPROVED, by)

    def deny(self, entity_id: str, *, by: str = "owner") -> dict:
        """Human denial: mark ``entity_id`` denied."""
        return self._decide(entity_id, DENIED, by)

    def _decide(self, entity_id: str, status: str, by: str) -> dict:
        eid = checked_id(entity_id)
        if eid is None:
            # Refused: nothing is written, and it keeps reading as denied.
            return {"entity_id": KEY_TEXT_PLACEHOLDER, "status": DENIED,
                    "refused": _REFUSED_NOTE}
        by = key_safe_text(by)
        with self._lock:
            row = self._state.get(eid) or {
                "status": PENDING,
                "first_seen": _utcnow(),
                "source": "manual",
                "context": "",
                "decided_at": None,
            }
            row["status"] = status
            row["decided_at"] = _utcnow()
            row["decided_by"] = by
            self._state[eid] = row
            self._save()
            self._audit({"event": "decision", "entity_id": eid,
                         "status": status, "by": by, "at": row["decided_at"]})
            return {"entity_id": eid, "status": status}

    def pending(self) -> list[str]:
        """Return the sorted ids of entities awaiting a human decision."""
        return sorted(e for e, r in self._state.items()
                      if r.get("status") == PENDING)

    def all(self) -> dict[str, dict]:
        """Return a copy of the full ledger state."""
        return {e: dict(r) for e, r in self._state.items()}

    # ── verdict for a set of targets ─────────────────────────────────────
    def gate(self, targets: list[str], *, name: str = "", task_text: str = "",
             authored_by: str = "agent") -> dict:
        """Rule on a resolved target list using recorded human consent.

        For each target, records it (pending if new) and reads its status:

        * any target **denied**, or any target id that
          :func:`~puffpai.web_search.checked_id` refuses (it looks like a key
          or a password; never recorded, never named) → ``deny``;
        * else any target **pending/unknown** → ``flag`` (allowed-but-noted);
        * else (all **approved**) → ``allow``.

        Returns ``{"action", "reasons", "targets", ...}``. No text in it (or in
        the ledger) keeps a key: a refused id is left out of ``targets`` and
        the reason does not name it.
        """
        clean: list[str] = []
        refused = 0
        for t in {t for t in targets if t}:
            eid = checked_id(t)
            if eid is None:
                refused += 1
            else:
                clean.append(eid)
        clean.sort()
        denied: list[str] = []
        pending: list[str] = []
        for t in clean:
            st = self.record(t, source=authored_by, context=task_text)
            if st == DENIED:
                denied.append(t)
            elif st != APPROVED:
                pending.append(t)

        reasons: list[str] = []
        if refused:
            reasons.append(_REFUSED_NOTE)
        if denied or refused:
            action = "deny"
            if denied:
                reasons.append(f"consent denied by owner for: {denied[:5]}")
        elif pending:
            action = "flag"
            reasons.append(_PENDING_NOTE)
            reasons.append(f"awaiting approval for: {pending[:5]}")
        else:
            action = "allow"

        result = {
            "action": action,
            "reasons": reasons,
            "targets": clean,
            "automation_name": key_safe_text(name),
            "authored_by": key_safe_text(authored_by),
            "task_text": key_safe_text(task_text, 200),
            "reviewed_at": _utcnow(),
            "gate": "gate_targets",
        }
        self._audit(result)
        return result


# ── module-level default ledger + backward-compatible functions ──────────
_default_ledger: Optional[ConsentLedger] = None


def get_ledger() -> ConsentLedger:
    """Return the process-wide default :class:`ConsentLedger` singleton."""
    global _default_ledger
    if _default_ledger is None:
        _default_ledger = ConsentLedger()
    return _default_ledger


def gate_targets(targets: list[str], *, name: str = "", task_text: str = "",
                 authored_by: str = "agent") -> dict:
    """Rule on a resolved target list via the default consent ledger.

    Backward-compatible entry point for the Sentinel agent. See
    :meth:`ConsentLedger.gate`.
    """
    return get_ledger().gate(list(targets), name=name, task_text=task_text,
                             authored_by=authored_by)


def approve(entity_id: str, *, by: str = "owner") -> dict:
    """Approve a device/entity (human decision)."""
    return get_ledger().approve(entity_id, by=by)


def deny(entity_id: str, *, by: str = "owner") -> dict:
    """Deny a device/entity (human decision)."""
    return get_ledger().deny(entity_id, by=by)


def pending() -> list[str]:
    """List devices/entities awaiting a human decision."""
    return get_ledger().pending()


def sentinel_review(parsed: dict, states: list | None = None,
                    task_text: str = "") -> dict:
    """Record an LLM-authored automation for review; default to pending.

    Returns ``{"action": "flag", "reasons": [...]}``. Schema-aware automation
    review (unsafe triggers, unbounded loops, dangerous services) is part of the
    appliance policy engine and is deliberately **not** included in this build,
    so every automation is flagged for a human rather than machine-approved.
    """
    result = {
        "action": "flag",
        "reasons": [_PENDING_NOTE],
        "automation_keys": sorted(key_safe_text(str(k))
                                  for k in (parsed or {}).keys()),
        "task_text": key_safe_text(task_text, 200),
        "reviewed_at": _utcnow(),
        "gate": "sentinel_review",
    }
    get_ledger()._audit(result)
    return result


__all__ = [
    "ConsentLedger",
    "get_ledger",
    "gate_targets",
    "sentinel_review",
    "approve",
    "deny",
    "pending",
    "CONSENT_LEDGER",
    "CONSENT_STATE",
    "PENDING",
    "APPROVED",
    "DENIED",
]
