"""
PuffPAI n8n Webhook Client — Centralized HTTP layer for n8n communication.

Provides retry logic, structured error handling, and config-driven
webhook URLs so callers don't hardcode paths.

    from puffpai.n8n_client import N8nClient

    client = N8nClient()
    ok, data = client.call("weather_alerts", {"lat": 0.0, "lon": 0.0})

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from puffpai.config import get_config
from puffpai.security.lan_guard import no_proxies as _no_proxies  # marked home-network calls never use a proxy

logger = logging.getLogger(__name__)

_DEFAULT_RETRIES = 2
_RETRY_BACKOFF = 0.5  # seconds between retries


class N8nClient:
    """Thin wrapper around n8n webhook HTTP calls with retry + fallback."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int | None = None,
        max_retries: int = _DEFAULT_RETRIES,
    ):
        cfg = get_config()
        self._base_url = (base_url or cfg.n8n.url).rstrip("/")
        self._timeout = timeout or cfg.n8n.timeout
        self._max_retries = max_retries
        self._webhooks = cfg.n8n.webhooks

    # ── Public API ────────────────────────────────────────────────

    def webhook_url(self, name: str) -> str:
        """Return the full URL for a named webhook.

        Supported names mirror the shipped ``n8n_workflows/`` templates plus
        ``weather_alerts`` and ``puffpai_voice``.
        """
        # Only allow known webhook names from the N8nWebhooks dataclass
        _KNOWN = {
            "weather_alerts", "puffpai_voice",
            "daily_news", "daily_weather", "calendar_sync", "meal_planner",
            "grocery_list", "daily_briefing", "health_check", "media_now_playing",
        }
        if name not in _KNOWN:
            raise ValueError(f"Unknown webhook name: {name!r}")
        path = getattr(self._webhooks, name)
        return f"{self._base_url}{path}"

    def call(
        self,
        webhook_name: str,
        payload: dict[str, Any],
        *,
        method: str = "POST",
    ) -> tuple[bool, dict[str, Any] | str]:
        """Call an n8n webhook with retry logic.

        Returns (success: bool, data: dict | error_message: str).
        On success, data is the parsed JSON response.
        On failure, data is a human-readable error string.
        """
        url = self.webhook_url(webhook_name)
        last_error = ""

        for attempt in range(1, self._max_retries + 1):
            try:
                if method.upper() == "POST":
                    resp = requests.post(url, json=payload, timeout=self._timeout, proxies=_no_proxies())  # lan-egress: n8n webhook on the configured LAN url
                else:
                    resp = requests.get(url, params=payload, timeout=self._timeout, proxies=_no_proxies())  # lan-egress: n8n webhook on the configured LAN url

                if resp.status_code == 200:
                    try:
                        return True, resp.json()
                    except ValueError:
                        return True, {"raw": resp.text}

                last_error = f"HTTP {resp.status_code}"
                logger.warning(
                    "n8n %s returned %s (attempt %d/%d)",
                    webhook_name, resp.status_code, attempt, self._max_retries,
                )

            except requests.exceptions.ConnectionError:
                last_error = f"n8n unreachable at {self._base_url}"
                logger.warning(
                    "n8n connection failed for %s (attempt %d/%d)",
                    webhook_name, attempt, self._max_retries,
                )
            except requests.exceptions.Timeout:
                last_error = f"n8n timeout ({self._timeout}s)"
                logger.warning(
                    "n8n timeout for %s (attempt %d/%d)",
                    webhook_name, attempt, self._max_retries,
                )
            except requests.exceptions.RequestException as exc:
                last_error = str(exc)
                logger.warning(
                    "n8n request error for %s: %s (attempt %d/%d)",
                    webhook_name, exc, attempt, self._max_retries,
                )

            if attempt < self._max_retries:
                time.sleep(_RETRY_BACKOFF * attempt)

        return False, last_error

    def is_reachable(self) -> bool:
        """Quick health check — can we reach the n8n base URL?"""
        try:
            requests.get(self._base_url, timeout=3, proxies=_no_proxies())  # lan-egress: n8n reachability probe, LAN
            return True
        except requests.exceptions.RequestException:
            return False
