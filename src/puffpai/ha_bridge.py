"""
PuffPAI Home Assistant Bridge
==============================
Single source of truth for all Home Assistant communication.

puff puff pass

Every component (API server, tools, MCP server, dashboard) should
go through this module instead of making direct HA REST calls.

Usage:
    from puffpai.ha_bridge import get_bridge

    bridge = get_bridge()
    bridge.list_devices(domain="light")
    bridge.control_device("light.bedroom", "turn_on", brightness_pct=80)
    bridge.get_automations()
    bridge.create_automation({...})

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from puffpai.config import get_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

CONTROLLABLE_DOMAINS = frozenset([
    "light", "switch", "fan", "cover", "climate", "media_player",
    "lock", "vacuum", "camera", "scene", "automation", "script",
    "siren", "remote", "number", "select", "button",
])

SENSOR_DOMAINS = frozenset([
    "sensor", "binary_sensor", "weather", "sun", "zone", "person",
])

COLOR_TEMP_PRESETS = {
    "warm": 400,
    "neutral": 300,
    "cool": 180,
    "daylight": 153,
}


@dataclass
class DeviceInfo:
    """Normalised representation of an HA entity."""
    entity_id: str
    domain: str
    friendly_name: str
    state: str
    attributes: dict[str, Any]
    last_changed: str
    last_updated: str
    area_id: str | None = None
    area_name: str | None = None

    @property
    def is_controllable(self) -> bool:
        return self.domain in CONTROLLABLE_DOMAINS

    @property
    def is_on(self) -> bool:
        return self.state in ("on", "playing", "open", "unlocked", "heat", "cool", "auto")


@dataclass
class Room:
    """A room / area with its devices."""
    area_id: str
    name: str
    devices: list[DeviceInfo] = field(default_factory=list)


@dataclass
class AutomationInfo:
    """HA automation entity with extra metadata."""
    entity_id: str
    friendly_name: str
    state: str  # "on" or "off"
    last_triggered: str | None
    mode: str | None  # single, restart, queued, parallel


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------

class _StateCache:
    """Thread-safe TTL cache for HA state data."""

    def __init__(self, ttl: float = 5.0):
        self._ttl = ttl
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._timestamps: dict[str, float] = {}

    def get(self, key: str) -> Any | None:
        with self._lock:
            ts = self._timestamps.get(key, 0)
            if time.monotonic() - ts < self._ttl:
                return self._data.get(key)
        return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._timestamps[key] = time.monotonic()

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._data.clear()
                self._timestamps.clear()
            else:
                self._data.pop(key, None)
                self._timestamps.pop(key, None)


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class HABridge:
    """Unified Home Assistant client with caching and device registry."""

    def __init__(self, url: str | None = None, token: str | None = None):
        cfg = get_config()
        self._url = (url or cfg.ha.url).rstrip("/")
        # The token is read now: the plain copy if one is still in the
        # settings, else the vault (its locked copy while the vault is locked).
        self._token_given = bool(token)
        self._token = token or cfg.ha.token
        if not self._token and cfg.ha.enabled:
            from puffpai.security import keys
            self._token = keys.house_key("home_assistant") or ""
        self._enabled = cfg.ha.enabled
        self._cache = _StateCache(ttl=5.0)
        self._session = requests.Session()  # lan-egress: Home Assistant session on the configured LAN url
        # Marked home-network calls never use a proxy (security.lan_guard).
        self._session.trust_env = False
        self._session.headers.update({
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        })

    def reload_key(self) -> None:
        """Read the token again (a kept key was sealed again or changed), in
        place, so every holder of this bridge uses it at once. A token handed
        in by the caller is left alone. Never raises, never logs the token."""
        if self._token_given:
            return
        try:
            cfg = get_config()
            token = cfg.ha.token
            if not token and cfg.ha.enabled:
                from puffpai.security import keys
                token = keys.house_key("home_assistant") or ""
            self._token = token
            self._session.headers["Authorization"] = f"Bearer {token}"
            self._cache.invalidate()
        except Exception as exc:  # noqa: BLE001
            logger.debug("HA token not reloaded: %s", type(exc).__name__)

    # -- low-level request ----------------------------------------------------

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Any = None,
        timeout: int | None = None,
        stream: bool = False,
    ) -> requests.Response:
        """Make an authenticated request to the HA REST API.

        Raises HABridgeError on any failure.
        """
        if not self._enabled:
            raise HABridgeError("Home Assistant integration is disabled")

        url = f"{self._url}/api/{endpoint}"
        kwargs: dict[str, Any] = {
            "timeout": timeout or 10,
            "stream": stream,
        }

        try:
            if method == "GET":
                resp = self._session.get(url, **kwargs)  # lan-egress: Home Assistant on the configured url; lan_guard refuses an internet address
            elif method == "POST":
                resp = self._session.post(url, json=data, **kwargs)  # lan-egress: Home Assistant on the configured url; lan_guard refuses an internet address
            elif method == "DELETE":
                resp = self._session.delete(url, **kwargs)  # lan-egress: Home Assistant on the configured url; lan_guard refuses an internet address
            else:
                raise HABridgeError(f"Unsupported HTTP method: {method}")

            resp.raise_for_status()
            return resp

        except requests.exceptions.ConnectionError:
            raise HABridgeError(f"Cannot connect to Home Assistant at {self._url}")
        except requests.exceptions.Timeout:
            raise HABridgeError("Home Assistant request timed out")
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            body = ""
            if exc.response is not None:
                try:
                    body = exc.response.text[:200]
                except Exception:
                    pass
            raise HABridgeError(f"HA returned HTTP {status}: {body}")

    # -- connection -----------------------------------------------------------

    def is_connected(self) -> bool:
        """Check whether HA is reachable."""
        try:
            resp = self._request("")
            return resp.ok
        except HABridgeError:
            return False

    def get_config(self) -> dict[str, Any]:
        """Return the HA /api/config payload."""
        cached = self._cache.get("config")
        if cached is not None:
            return cached
        data = self._request("config").json()
        self._cache.set("config", data)
        return data

    def get_status(self) -> dict[str, Any]:
        """Connection status summary for API consumers."""
        try:
            config = self.get_config()
            return {
                "connected": True,
                "ha_url": self._url,
                "ha_version": config.get("version", "unknown"),
                "location_name": config.get("location_name", ""),
            }
        except HABridgeError as exc:
            return {
                "connected": False,
                "ha_url": self._url,
                "error": str(exc),
            }

    # -- state ----------------------------------------------------------------

    def get_all_states(self, *, force: bool = False) -> list[dict[str, Any]]:
        """Return all entity states (raw HA format). Cached for 5 s."""
        if not force:
            cached = self._cache.get("states")
            if cached is not None:
                return cached
        states = self._request("states").json()
        self._cache.set("states", states)
        return states

    def get_entity_state(self, entity_id: str) -> dict[str, Any]:
        """Return a single entity's state."""
        return self._request(f"states/{entity_id}").json()

    # -- devices --------------------------------------------------------------

    def _parse_device(self, raw: dict[str, Any], area_map: dict[str, str] | None = None) -> DeviceInfo:
        """Convert a raw HA state dict into a DeviceInfo."""
        eid = raw.get("entity_id", "")
        attrs = raw.get("attributes", {})
        domain = eid.split(".")[0] if "." in eid else "unknown"
        area_id = attrs.get("area_id")
        area_name = None
        if area_id and area_map:
            area_name = area_map.get(area_id, area_id)
        return DeviceInfo(
            entity_id=eid,
            domain=domain,
            friendly_name=attrs.get("friendly_name", eid),
            state=raw.get("state", "unknown"),
            attributes=attrs,
            last_changed=raw.get("last_changed", ""),
            last_updated=raw.get("last_updated", ""),
            area_id=area_id,
            area_name=area_name,
        )

    def list_devices(
        self,
        domain: str | None = None,
        controllable_only: bool = True,
    ) -> list[DeviceInfo]:
        """List devices, optionally filtered by domain."""
        states = self.get_all_states()
        area_map = self._get_area_map()
        devices = []
        for raw in states:
            dev = self._parse_device(raw, area_map)
            if controllable_only and dev.domain not in CONTROLLABLE_DOMAINS:
                continue
            if domain and dev.domain != domain:
                continue
            devices.append(dev)
        return devices

    def get_device(self, entity_id: str) -> DeviceInfo:
        """Get full info for a single device."""
        raw = self.get_entity_state(entity_id)
        return self._parse_device(raw, self._get_area_map())

    def find_devices(self, query: str) -> list[DeviceInfo]:
        """Search devices by partial name or entity_id match."""
        query_lower = query.lower()
        states = self.get_all_states()
        area_map = self._get_area_map()
        matches = []
        for raw in states:
            eid = raw.get("entity_id", "").lower()
            fname = raw.get("attributes", {}).get("friendly_name", "").lower()
            if query_lower in eid or query_lower in fname:
                matches.append(self._parse_device(raw, area_map))
        return matches

    # -- device control -------------------------------------------------------

    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | list[str] | None = None,
        **service_data: Any,
    ) -> list[dict[str, Any]]:
        """Call an HA service and return the resulting state array.

        Invalidates the state cache after the call.
        """
        data: dict[str, Any] = dict(service_data)
        if entity_id is not None:
            data["entity_id"] = entity_id

        resp = self._request(f"services/{domain}/{service}", method="POST", data=data)
        self._cache.invalidate("states")

        try:
            return resp.json()
        except Exception:
            return []

    def control_device(
        self,
        entity_id: str,
        action: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """High-level device control (turn_on/turn_off/toggle + params).

        Returns a result dict with ``success``, ``entity_id``, ``action``.
        Extra kwargs are passed as service_data (brightness_pct, temperature, etc.).
        """
        action = action.lower().strip()
        if action not in ("turn_on", "turn_off", "toggle"):
            raise HABridgeError(f"Unknown action '{action}'. Use turn_on, turn_off, or toggle.")

        domain = entity_id.split(".")[0]
        self.call_service(domain, action, entity_id=entity_id, **kwargs)
        return {"success": True, "entity_id": entity_id, "action": action}

    def set_light(
        self,
        entity_id: str,
        brightness_pct: int | None = None,
        color_temp: str | int | None = None,
        rgb_color: tuple[int, int, int] | None = None,
    ) -> dict[str, Any]:
        """Set light properties (brightness, colour temperature, rgb)."""
        data: dict[str, Any] = {}
        if brightness_pct is not None:
            data["brightness_pct"] = max(0, min(100, brightness_pct))
        if color_temp is not None:
            if isinstance(color_temp, str):
                data["color_temp"] = COLOR_TEMP_PRESETS.get(color_temp.lower(), 300)
            else:
                data["color_temp"] = color_temp
        if rgb_color is not None:
            data["rgb_color"] = list(rgb_color)

        self.call_service("light", "turn_on", entity_id=entity_id, **data)
        return {"success": True, "entity_id": entity_id, **data}

    def set_climate(
        self,
        entity_id: str,
        temperature: float | None = None,
        hvac_mode: str | None = None,
    ) -> dict[str, Any]:
        """Set thermostat temperature and/or mode."""
        data: dict[str, Any] = {}
        if temperature is not None:
            data["temperature"] = temperature
        if hvac_mode is not None:
            data["hvac_mode"] = hvac_mode

        self.call_service("climate", "set_temperature", entity_id=entity_id, **data)
        return {"success": True, "entity_id": entity_id, **data}

    # -- rooms / areas --------------------------------------------------------

    def _get_area_map(self) -> dict[str, str]:
        """Return {area_id: area_name} mapping. Cached."""
        cached = self._cache.get("area_map")
        if cached is not None:
            return cached
        try:
            areas = self._request("config/area_registry/list").json()
            mapping = {a["area_id"]: a["name"] for a in areas}
        except (HABridgeError, KeyError):
            mapping = {}
        self._cache.set("area_map", mapping)
        return mapping

    def get_rooms(self) -> list[Room]:
        """Return all HA areas with their devices grouped in."""
        area_map = self._get_area_map()
        states = self.get_all_states()

        rooms: dict[str, Room] = {}
        unassigned = Room(area_id="unassigned", name="Unassigned")

        for raw in states:
            dev = self._parse_device(raw, area_map)
            if not dev.is_controllable and dev.domain not in SENSOR_DOMAINS:
                continue

            area = dev.area_id
            if area and area in area_map:
                if area not in rooms:
                    rooms[area] = Room(area_id=area, name=area_map[area])
                rooms[area].devices.append(dev)
            else:
                unassigned.devices.append(dev)

        result = sorted(rooms.values(), key=lambda r: r.name)
        if unassigned.devices:
            result.append(unassigned)
        return result

    # -- automations ----------------------------------------------------------

    def get_automations(self) -> list[AutomationInfo]:
        """Return all automation.* entities."""
        states = self.get_all_states()
        automations = []
        for raw in states:
            eid = raw.get("entity_id", "")
            if not eid.startswith("automation."):
                continue
            attrs = raw.get("attributes", {})
            automations.append(AutomationInfo(
                entity_id=eid,
                friendly_name=attrs.get("friendly_name", eid),
                state=raw.get("state", "unknown"),
                last_triggered=attrs.get("last_triggered"),
                mode=attrs.get("current", None),
            ))
        return automations

    def trigger_automation(self, entity_id: str) -> dict[str, Any]:
        """Trigger an automation manually."""
        self.call_service("automation", "trigger", entity_id=entity_id)
        return {"success": True, "entity_id": entity_id, "action": "triggered"}

    def toggle_automation(self, entity_id: str, enable: bool) -> dict[str, Any]:
        """Enable or disable an automation."""
        action = "turn_on" if enable else "turn_off"
        self.call_service("automation", "toggle" if enable else "turn_off", entity_id=entity_id)
        return {"success": True, "entity_id": entity_id, "enabled": enable}

    def create_automation(self, config: dict[str, Any]) -> dict[str, Any]:
        """Create a new automation via the HA config API.

        ``config`` should follow the HA automation schema:
        {
            "alias": "My Automation",
            "description": "...",
            "trigger": [...],
            "condition": [...],
            "action": [...]
        }

        HA requires POST to config/automation/config/<unique_id>.
        """
        import uuid
        auto_id = f"puffpai_{uuid.uuid4().hex[:12]}"
        resp = self._request(
            f"config/automation/config/{auto_id}", method="POST", data=config
        )
        self._cache.invalidate("states")
        try:
            return {"success": True, "id": auto_id, **resp.json()}
        except Exception:
            return {"success": True, "id": auto_id, "message": "Automation created"}

    # -- scenes & scripts -----------------------------------------------------

    def get_scenes(self) -> list[DeviceInfo]:
        """Return all scene.* and script.* entities."""
        states = self.get_all_states()
        area_map = self._get_area_map()
        return [
            self._parse_device(raw, area_map)
            for raw in states
            if raw.get("entity_id", "").startswith(("scene.", "script."))
        ]

    def activate_scene(self, entity_id: str) -> dict[str, Any]:
        """Activate a scene or script."""
        domain = entity_id.split(".")[0]
        self.call_service(domain, "turn_on", entity_id=entity_id)
        return {"success": True, "entity_id": entity_id, "action": "activated"}

    # -- history --------------------------------------------------------------

    def get_history(
        self,
        entity_id: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[Any]:
        """Get state history for an entity."""
        params = f"filter_entity_id={entity_id}&minimal_response&no_attributes"
        endpoint = "history/period"
        if start:
            endpoint = f"history/period/{start}"
        if end:
            params += f"&end_time={end}"

        resp = self._request(f"{endpoint}?{params}")
        return resp.json()

    # -- camera ---------------------------------------------------------------

    def get_camera_image(self, entity_id: str) -> tuple[bytes, str]:
        """Return (image_bytes, content_type) for a camera entity."""
        resp = self._request(f"camera_proxy/{entity_id}", stream=True)
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        return resp.content, content_type


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class HABridgeError(Exception):
    """Raised when an HA operation fails."""
    pass


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: HABridge | None = None
_lock = threading.Lock()


def get_bridge(*, _force_reload: bool = False) -> HABridge:
    """Return the global HABridge singleton."""
    global _instance
    if _instance is None or _force_reload:
        with _lock:
            if _instance is None or _force_reload:
                _instance = HABridge()
    return _instance


def reload_key() -> None:
    """The Home Assistant token was sealed again or changed: the live bridge
    (if one was built) reads it now, no restart."""
    with _lock:
        bridge = _instance
    if bridge is not None:
        bridge.reload_key()
