"""
PuffPAI Home Assistant Device Watcher
======================================
Monitors Home Assistant for new entities and manages a human approval queue.

Flow:
    1. Periodically poll HA for all entities.
    2. Compare against the known/decided device list.
    3. New controllable devices land in a *pending* queue and are recorded in
       the Phase-4 consent ledger as ``pending``.
    4. A human approves or denies each one (``puffpai devices approve/deny`` or
       :meth:`approve_device` / :meth:`deny_device`). The decision is written to
       the consent ledger, which is the single source of truth the Sentinel
       agent consults before any state-changing action touches that entity.

There is **no** automatic policy in this build — no blast-radius scoring, no
network mechanism, no auto-approval of real devices. A person decides. Unknown
devices stay pending (Sentinel flags, does not act) until a human rules on them.

Usage:
    from puffpai.ha_device_watcher import get_watcher

    watcher = get_watcher()
    watcher.check_for_new_devices()        # Poll HA now
    pending = watcher.get_pending()         # Devices awaiting a human decision
    watcher.approve_device("light.desk")
    watcher.deny_device("camera.unknown")

Home Assistant is fully optional: when HA is disabled or unreachable, polling
returns an empty list and the watcher degrades cleanly.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

def _default_data_dir() -> str:
    """Resolve the data directory *now* (honours ``PUFFPAI_DATA_DIR``).

    Resolved at call time, not import time, so a test or host that sets
    ``PUFFPAI_DATA_DIR`` after import still lands in the right place.
    """
    return os.environ.get(
        "PUFFPAI_DATA_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
    )

# Domains we watch for new controllable devices.
WATCHED_DOMAINS = frozenset([
    "light", "switch", "fan", "cover", "climate", "media_player",
    "lock", "vacuum", "camera", "siren", "remote", "sensor",
    "binary_sensor", "number", "select", "button",
])

# Domains that are HA-internal helpers, not real devices — auto-recorded so we
# do not keep re-processing them, but never surfaced for approval.
AUTO_APPROVE_DOMAINS = frozenset([
    "automation", "script", "scene", "input_boolean", "input_number",
    "input_select", "input_text", "input_datetime", "input_button",
    "counter", "timer", "schedule", "zone", "person", "sun",
    "weather", "update", "tts", "stt", "wake_word", "conversation",
    "assist_satellite", "todo", "event",
])

# Entity-id prefixes for HA-internal sensors that are HA's own metrics, not
# smart-home devices.
_AUTO_APPROVE_SENSOR_PREFIXES = (
    "sensor.backup_", "sensor.sun_", "sensor.energy_", "sensor.hacs",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PendingDevice:
    """A device awaiting a human decision."""
    entity_id: str
    domain: str
    friendly_name: str
    state: str
    manufacturer: str | None = None
    model: str | None = None
    category: str = "unknown"
    discovered_at: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Persist only a small, safe subset of HA attributes.
        d["attributes"] = {
            k: v for k, v in self.attributes.items()
            if k in ("friendly_name", "device_class", "manufacturer",
                     "model", "sw_version", "hw_version", "via_device",
                     "supported_color_modes", "supported_features")
        }
        return d


@dataclass
class KnownDevice:
    """A device that has been decided on (approved/denied) or auto-recorded."""
    entity_id: str
    domain: str
    friendly_name: str
    status: str  # "approved", "denied", "auto"
    decided_at: str = ""
    category: str = "unknown"


class HADeviceWatcher:
    """Watches Home Assistant for new entities and manages the approval queue.

    Human decisions are routed to the Phase-4 consent ledger; the watcher's own
    JSON files only hold display metadata (friendly name, manufacturer, model)
    so the CLI can show something friendlier than a bare entity id.
    """

    def __init__(self, *, bridge: Any = None, ledger: Any = None,
                 data_dir: str | None = None) -> None:
        self._lock = threading.Lock()
        self._known: dict[str, KnownDevice] = {}
        self._pending: dict[str, PendingDevice] = {}
        self._bridge = bridge
        self._ledger = ledger
        # Resolve persistence paths at instantiation (not import) so setting
        # PUFFPAI_DATA_DIR after import, or injecting data_dir in a test,
        # isolates state per run.
        self._data_dir = data_dir or _default_data_dir()
        self._known_file = os.path.join(self._data_dir, "ha_known_devices.json")
        self._pending_file = os.path.join(self._data_dir, "ha_pending_devices.json")
        self._load_state()

    # ── consent ledger accessor ──────────────────────────────────────────
    def _consent(self):
        if self._ledger is None:
            from puffpai.security.consent_gate import get_ledger
            self._ledger = get_ledger()
        return self._ledger

    def _get_bridge(self):
        if self._bridge is None:
            from puffpai.ha_bridge import get_bridge
            self._bridge = get_bridge()
        return self._bridge

    # ── persistence ──────────────────────────────────────────────────────
    def _load_state(self) -> None:
        os.makedirs(self._data_dir, exist_ok=True)
        if os.path.isfile(self._known_file):
            try:
                with open(self._known_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for eid, d in data.items():
                    self._known[eid] = KnownDevice(**d)
            except (json.JSONDecodeError, TypeError, OSError):
                logger.warning("Corrupt known devices file, starting fresh")
        if os.path.isfile(self._pending_file):
            try:
                with open(self._pending_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for eid, d in data.items():
                    self._pending[eid] = PendingDevice(**d)
            except (json.JSONDecodeError, TypeError, OSError):
                logger.warning("Corrupt pending devices file, starting fresh")

    def _save_known(self) -> None:
        os.makedirs(self._data_dir, exist_ok=True)
        with open(self._known_file, "w", encoding="utf-8") as f:
            json.dump({eid: asdict(d) for eid, d in self._known.items()}, f, indent=2)

    def _save_pending(self) -> None:
        os.makedirs(self._data_dir, exist_ok=True)
        with open(self._pending_file, "w", encoding="utf-8") as f:
            json.dump({eid: d.to_dict() for eid, d in self._pending.items()}, f, indent=2)

    # ── discovery ────────────────────────────────────────────────────────
    def check_for_new_devices(self) -> list[PendingDevice]:
        """Poll HA for entities and return any newly discovered devices.

        Known (decided) devices are skipped; HA-internal helpers are recorded
        silently; every other new device lands in the pending queue and is
        registered as ``pending`` in the consent ledger. Returns [] when HA is
        disabled or unreachable.
        """
        try:
            from puffpai.ha_bridge import HABridgeError
            bridge = self._get_bridge()
            states = bridge.get_all_states(force=True)
        except Exception as exc:  # HABridgeError, ImportError, disabled HA, etc.
            logger.info("Device watcher: HA not available (%s)", exc)
            return []

        new_pending: list[PendingDevice] = []
        ledger = self._consent()

        with self._lock:
            for entity in states:
                eid = entity.get("entity_id", "")
                domain = eid.split(".")[0] if "." in eid else ""
                if not eid or eid in self._known or eid in self._pending:
                    continue

                attrs = entity.get("attributes", {}) or {}
                friendly_name = attrs.get("friendly_name", eid)

                # HA-internal helpers and unwatched domains: record and move on.
                if (domain in AUTO_APPROVE_DOMAINS
                        or eid.startswith(_AUTO_APPROVE_SENSOR_PREFIXES)
                        or domain not in WATCHED_DOMAINS):
                    self._known[eid] = KnownDevice(
                        entity_id=eid, domain=domain, friendly_name=friendly_name,
                        status="auto", decided_at=_utcnow(),
                    )
                    continue

                # A new controllable device: queue it and register as pending.
                pending = PendingDevice(
                    entity_id=eid,
                    domain=domain,
                    friendly_name=friendly_name,
                    state=entity.get("state", "unknown"),
                    manufacturer=attrs.get("manufacturer"),
                    model=attrs.get("model"),
                    category=self._guess_category(domain, friendly_name),
                    discovered_at=_utcnow(),
                    attributes=attrs,
                )
                self._pending[eid] = pending
                new_pending.append(pending)
                ledger.record(eid, source="device_watcher",
                              context=f"{domain}: {friendly_name}")

            self._save_known()
            self._save_pending()

        if new_pending:
            logger.info("Discovered %d new device(s): %s", len(new_pending),
                        ", ".join(d.friendly_name for d in new_pending))
        return new_pending

    @staticmethod
    def _guess_category(domain: str, name: str) -> str:
        """Rough category guess for display purposes only."""
        name_lower = (name or "").lower()
        for kws, cat in (
            (("washer", "washing"), "smart_washer"),
            (("dryer", "drying"), "smart_dryer"),
            (("dishwasher",), "smart_dishwasher"),
            (("fridge", "refrigerator"), "smart_fridge"),
            (("oven", "range", "stove"), "smart_oven"),
        ):
            if any(kw in name_lower for kw in kws):
                return cat
        mapping = {
            "light": "smart_light", "switch": "smart_switch",
            "camera": "camera", "climate": "thermostat", "lock": "smart_lock",
            "vacuum": "robot_vacuum", "fan": "smart_fan", "cover": "smart_cover",
            "media_player": "media_player", "siren": "siren",
            "sensor": "sensor", "binary_sensor": "sensor",
        }
        return mapping.get(domain, "unknown")

    # ── queue accessors ──────────────────────────────────────────────────
    def get_pending(self) -> list[PendingDevice]:
        with self._lock:
            return list(self._pending.values())

    def get_pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    # ── human decisions (routed to the consent ledger) ───────────────────
    def approve_device(self, entity_id: str, *, by: str = "owner") -> dict[str, Any]:
        """Approve a device — a human decision, recorded in the consent ledger."""
        return self._decide(entity_id, "approved", by)

    def deny_device(self, entity_id: str, *, by: str = "owner") -> dict[str, Any]:
        """Deny a device — a human decision, recorded in the consent ledger."""
        return self._decide(entity_id, "denied", by)

    def _decide(self, entity_id: str, status: str, by: str) -> dict[str, Any]:
        with self._lock:
            device = self._pending.pop(entity_id, None)
        # Route the decision to the consent ledger (the source of truth). It
        # accepts entity ids it has never seen, so a decision can be made even
        # without a prior sighting.
        ledger = self._consent()
        if status == "approved":
            ledger.approve(entity_id, by=by)
        else:
            ledger.deny(entity_id, by=by)

        now = _utcnow()
        known = KnownDevice(
            entity_id=entity_id,
            domain=device.domain if device else entity_id.split(".")[0],
            friendly_name=device.friendly_name if device else entity_id,
            status=status,
            decided_at=now,
            category=device.category if device else "unknown",
        )
        with self._lock:
            self._known[entity_id] = known
            self._save_known()
            self._save_pending()

        logger.info("Device %s %s by %s", entity_id, status, by)
        return {
            "success": True,
            "entity_id": entity_id,
            "status": status,
            "friendly_name": known.friendly_name,
            "decided_at": now,
        }

    def approve_all_pending(self, *, by: str = "owner") -> list[dict]:
        """Approve every pending device (bulk human decision)."""
        return [self.approve_device(eid, by=by)
                for eid in [d.entity_id for d in self.get_pending()]]

    def reset_device(self, entity_id: str) -> dict[str, Any]:
        """Forget a decided device so it re-appears as pending on the next scan."""
        with self._lock:
            known = self._known.pop(entity_id, None)
            if known is None:
                return {"success": False,
                        "error": f"Device '{entity_id}' not in known list"}
            self._save_known()
        return {"success": True, "entity_id": entity_id,
                "message": "Device reset, will re-appear on next scan"}

    # ── notification helpers ─────────────────────────────────────────────
    def get_notification_summary(self) -> dict[str, Any] | None:
        pending = self.get_pending()
        if not pending:
            return None
        return {
            "count": len(pending),
            "message": f"PuffPAI found {len(pending)} new Home Assistant device(s)",
            "devices": [{
                "entity_id": d.entity_id,
                "name": d.friendly_name,
                "type": d.category,
                "manufacturer": d.manufacturer or "Unknown",
            } for d in pending],
        }

    # ── stats ────────────────────────────────────────────────────────────
    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            approved = sum(1 for d in self._known.values() if d.status == "approved")
            denied = sum(1 for d in self._known.values() if d.status == "denied")
            auto = sum(1 for d in self._known.values() if d.status == "auto")
            return {
                "known_devices": len(self._known),
                "approved": approved,
                "denied": denied,
                "auto_recorded": auto,
                "pending": len(self._pending),
            }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: Optional[HADeviceWatcher] = None
_singleton_lock = threading.Lock()


def get_watcher(*, _force_reload: bool = False) -> HADeviceWatcher:
    """Return the global HADeviceWatcher singleton."""
    global _instance
    if _instance is None or _force_reload:
        with _singleton_lock:
            if _instance is None or _force_reload:
                _instance = HADeviceWatcher()
    return _instance


__all__ = ["HADeviceWatcher", "PendingDevice", "KnownDevice", "get_watcher",
           "WATCHED_DOMAINS", "AUTO_APPROVE_DOMAINS"]
