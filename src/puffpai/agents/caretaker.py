"""Caretaker — WF-6 self-maintenance (deterministic, LLM-independent).

The thing that restarts services cannot be one of the services it restarts, and
it must keep working when the LLM stack is down. So the Caretaker is **mostly
deterministic code**: reachability probes with short timeouts, each fully
guarded, so a health sweep always returns a report even if every dependency is
unreachable. Nothing here imports the inference stack.

Health targets: Ollama, ChromaDB (local persistent path), the api_server, n8n,
and Jellyfin. Each probe returns ``{"service", "reachable", "detail"}``.

Restart/elevation actions are **clearly-marked stubs**. Actually restarting a
unit needs systemd (Linux) or the Windows SCM and elevation this process does not
have; :meth:`restart_service` therefore never executes anything — it returns a
stub result describing what a privileged Caretaker unit would do. This is the
fail-closed default: no accidental service control on a dev host.

Credentials: service-control + update-pack verification keys
(``AgentScope.CARETAKER``) — forward-looking; not exercised by the stubs.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import platform
import socket
from urllib.parse import urlparse

from puffpai.logging_config import get_logger
from puffpai.security.secrets_provider import AgentScope, SecretsProvider

from .base import BaseAgent
from .envelope import Envelope

logger = get_logger(__name__)

_PROBE_TIMEOUT = 2.0  # seconds — a health sweep must stay snappy


def _tcp_reachable(host: str, port: int, timeout: float = _PROBE_TIMEOUT) -> bool:
    """Deterministic TCP connect probe. Never raises."""
    try:
        with socket.create_connection((host, port), timeout=timeout):  # lan-egress: health probe of a service the owner configured on this computer or home network
            return True
    except Exception:
        return False


def _host_port_from_url(url: str, default_port: int) -> tuple[str, int]:
    try:
        parsed = urlparse(url if "://" in url else f"http://{url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or default_port
        return host, port
    except Exception:
        return "127.0.0.1", default_port


class CaretakerAgent(BaseAgent):
    """Deterministic watchdog / health reporter with restart stubs."""

    name = "caretaker"
    scope = AgentScope.CARETAKER

    def __init__(self, *, secrets: SecretsProvider | None = None,
                 config=None) -> None:
        super().__init__(secrets=secrets)
        self._config = config

    def _cfg(self):
        if self._config is None:
            try:
                from puffpai.config import get_config
                self._config = get_config()
            except Exception as exc:
                logger.debug("caretaker config unavailable: %s", exc)
                self._config = None
        return self._config

    # ── health checks (deterministic, no LLM) ───────────────────────
    def run_health_checks(self) -> dict:
        """Probe every service and return a report. Always succeeds."""
        checks = [
            self._check_ollama(),
            self._check_chromadb(),
            self._check_api_server(),
            self._check_n8n(),
            self._check_jellyfin(),
        ]
        healthy = sum(1 for c in checks if c["reachable"])
        return {
            "ok": True,
            "healthy": healthy,
            "total": len(checks),
            "checks": checks,
        }

    def _check_ollama(self) -> dict:
        cfg = self._cfg()
        url = None
        if cfg is not None:
            url = getattr(getattr(cfg, "ollama", None), "url", None)
        url = url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
        host, port = _host_port_from_url(url, 11434)
        return {"service": "ollama", "reachable": _tcp_reachable(host, port),
                "detail": f"{host}:{port}"}

    def _check_chromadb(self) -> dict:
        cfg = self._cfg()
        path = None
        if cfg is not None:
            paths = getattr(cfg, "paths", None)
            path = getattr(paths, "chromadb_path", None) or getattr(
                getattr(cfg, "chromadb", None), "path", None)
        reachable = bool(path) and os.path.isdir(str(path))
        return {"service": "chromadb", "reachable": reachable,
                "detail": "persistent path present" if reachable else "path missing"}

    def _check_api_server(self) -> dict:
        cfg = self._cfg()
        host, port = "127.0.0.1", 8000
        if cfg is not None:
            api = getattr(cfg, "api_server", None)
            host = getattr(api, "host", host) or host
            port = int(getattr(api, "port", port) or port)
        if host in ("0.0.0.0", ""):
            host = "127.0.0.1"
        return {"service": "api_server", "reachable": _tcp_reachable(host, port),
                "detail": f"{host}:{port}"}

    def _check_n8n(self) -> dict:
        cfg = self._cfg()
        url = getattr(getattr(cfg, "n8n", None), "url", None) if cfg else None
        url = url or "http://127.0.0.1:5678"
        host, port = _host_port_from_url(url, 5678)
        return {"service": "n8n", "reachable": _tcp_reachable(host, port),
                "detail": f"{host}:{port}"}

    def _check_jellyfin(self) -> dict:
        cfg = self._cfg()
        url = None
        if cfg is not None:
            media = getattr(cfg, "media_server", None)
            url = getattr(media, "jellyfin_url", None) or getattr(media, "url", None)
        if not url:
            return {"service": "jellyfin", "reachable": False, "detail": "not configured"}
        host, port = _host_port_from_url(url, 8096)
        return {"service": "jellyfin", "reachable": _tcp_reachable(host, port),
                "detail": f"{host}:{port}"}

    # ── restart hooks (STUBS — elevation/systemd required) ──────────
    def restart_service(self, service: str) -> dict:
        """STUB: describe the restart a privileged Caretaker unit would perform.

        This never executes a restart. Real service control requires elevation
        (systemd on Linux, the Windows SCM) that this process does not hold, and
        fail-closed means we do not attempt it from a dev/app context.
        """
        system = platform.system()
        if system == "Linux":
            planned = f"systemctl restart puffpai-{service}.service"
        elif system == "Windows":
            planned = f"Restart-Service -Name puffpai-{service}"
        else:
            planned = f"(no service manager mapping for {system})"
        logger.warning("caretaker restart_service(%s) is a stub — not executed", service)
        return {
            "ok": False,
            "stub": True,
            "service": service,
            "platform": system,
            "planned_command": planned,
            "reason": "service control requires elevation; not performed from this context",
        }

    def verify_update_pack(self, path: str) -> dict:
        """STUB: signed-update-pack verification hook (Caretaker holds the key)."""
        return {
            "ok": False,
            "stub": True,
            "path": path,
            "reason": "update-pack signature verification not yet implemented "
                      "(UPDATE_VERIFICATION_KEY reserved for CARETAKER scope)",
        }

    # ── request handling ────────────────────────────────────────────
    def handle(self, envelope: Envelope) -> dict:
        intent = envelope.intent
        p = envelope.payload or {}
        if intent in ("health", "health_check", "watchdog"):
            return self.run_health_checks()
        if intent == "restart_service":
            return self.restart_service(str(p.get("service", "")))
        if intent == "verify_update_pack":
            return self.verify_update_pack(str(p.get("path", "")))
        return {"ok": False, "error": f"caretaker: unknown intent {intent!r}"}

    def health(self) -> dict:
        base = super().health()
        base["llm_independent"] = True
        return base


__all__ = ["CaretakerAgent"]
