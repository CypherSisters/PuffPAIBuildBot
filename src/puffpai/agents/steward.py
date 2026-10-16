"""Steward — WF-2 house operations (HA + media control).

The Steward is the *only* agent that holds the Home Assistant token and the
media-server API credentials (``AgentScope.STEWARD``). Concierge cannot control a
light or fetch a camera frame itself — it asks the Steward, whose state-changing
requests are veto-reviewed by Sentinel on the way in.

Composition, not rewrite: this wraps the Home Assistant bridge and the media
bridge rather than reimplementing them. Both integrations ship in a later phase;
until then, inject them (or leave them unset and the Steward reports the
capability as unavailable).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from puffpai.logging_config import get_logger
from puffpai.security.secrets_provider import AgentScope, SecretsProvider

from .base import BaseAgent
from .envelope import Envelope

logger = get_logger(__name__)


class StewardAgent(BaseAgent):
    """House + media actuator."""

    name = "steward"
    scope = AgentScope.STEWARD

    def __init__(self, *, secrets: SecretsProvider | None = None,
                 ha_bridge=None, media_bridge=None) -> None:
        super().__init__(secrets=secrets)
        self._ha = ha_bridge
        self._media = media_bridge

    # ── engine accessors (lazy, guarded) ────────────────────────────
    def _ha_bridge(self):
        if self._ha is None:
            try:
                from puffpai.ha_bridge import get_bridge  # type: ignore[import-not-found]
            except ImportError as exc:  # arrives in a later release phase
                raise RuntimeError(
                    "Home Assistant control is not available in this build "
                    "(the HA integration ships in a later phase). Inject an "
                    "ha_bridge to exercise the Steward."
                ) from exc
            self._ha = get_bridge()
        return self._ha

    def _media_bridge(self):
        if self._media is None:
            try:
                from puffpai.media_bridge import get_media_bridge  # type: ignore[import-not-found]
            except ImportError as exc:  # arrives in a later release phase
                raise RuntimeError(
                    "Media control is not available in this build (the media "
                    "integration ships in a later phase). Inject a media_bridge "
                    "to exercise the Steward."
                ) from exc
            self._media = get_media_bridge()
        return self._media

    # ── request handling ────────────────────────────────────────────
    def handle(self, envelope: Envelope) -> dict:
        intent = envelope.intent
        p = envelope.payload or {}
        try:
            # ── read-only house queries ──
            if intent in ("list_devices", "devices"):
                ha = self._ha_bridge()
                devices = ha.list_devices(domain=p.get("domain"),
                                          controllable_only=p.get("controllable_only", True))
                return {"ok": True, "devices": [self._device_dict(d) for d in devices]}
            if intent in ("device_status", "entity_state"):
                ha = self._ha_bridge()
                return {"ok": True, "state": ha.get_entity_state(p["entity_id"])}
            if intent == "camera_frame":
                ha = self._ha_bridge()
                envelope.budget.consume(1)  # a fetch is a metered action
                image, content_type = ha.get_camera_image(p["entity_id"])
                return {"ok": True, "content_type": content_type, "bytes": len(image)}

            # ── state-changing house actions (veto-reviewed by the bus) ──
            if intent in ("control_device", "control"):
                ha = self._ha_bridge()
                envelope.budget.consume(1)
                result = ha.control_device(p["entity_id"], p["action"],
                                           **p.get("kwargs", {}))
                return {"ok": True, "result": result}
            if intent == "activate_scene":
                ha = self._ha_bridge()
                envelope.budget.consume(1)
                return {"ok": True, "result": ha.activate_scene(p["entity_id"])}

            # ── media ──
            if intent == "media_status":
                return {"ok": True, "status": self._media_bridge().get_status()}
            if intent == "media_search":
                media = self._media_bridge()
                kind = p.get("kind", "music")
                query = p.get("query", "")
                fn = {"music": media.search_music, "movies": media.search_movies,
                      "books": media.search_books}.get(kind, media.search_music)
                items = fn(query, limit=p.get("limit", 20))
                return {"ok": True, "count": len(items)}
            if intent == "media_play":
                media = self._media_bridge()
                envelope.budget.consume(1)
                return {"ok": True, "result": media.play_music(p["item_id"],
                                                               p.get("session_id", ""))}

            return {"ok": False, "error": f"steward: unknown intent {intent!r}"}
        except KeyError as exc:
            return {"ok": False, "error": f"steward: missing payload key {exc}"}

    @staticmethod
    def _device_dict(device) -> dict:
        """Best-effort serialization of a DeviceInfo without assuming shape."""
        for attr in ("to_dict", "_asdict"):
            fn = getattr(device, attr, None)
            if callable(fn):
                try:
                    return fn()
                except Exception:
                    break
        return {
            "entity_id": getattr(device, "entity_id", None),
            "name": getattr(device, "name", None),
            "state": getattr(device, "state", None),
        }

    def health(self) -> dict:
        base = super().health()
        try:
            base["ha_connected"] = bool(self._ha_bridge().is_connected())
        except Exception:
            base["ha_connected"] = False
        return base


__all__ = ["StewardAgent"]
