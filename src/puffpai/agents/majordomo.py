"""Majordomo — WF-5 proactive & scheduled work ("agents while you sleep").

The Majordomo owns background work: the daily briefing, reminders, calendar sync,
and n8n workflow ownership. It is the only holder of the n8n API
key and calendar credentials (``AgentScope.MAJORDOMO``).

**A persistent queue.** Tasks are appended to a JSONL file under ``data/`` so a
restart does not lose scheduled work. The queue path honours ``PUFFPAI_DATA_DIR``
(same convention as the consent gate).

Running a workflow or worker that reaches n8n is ``state_changing`` and passes
Sentinel review like any other plan.

**Routines** (:mod:`puffpai.routines`) are the background work that actually
runs: the scheduler thread sends one ``run_routine`` envelope per due routine,
state-changing, so the Sentinel reviews it (and checks the owner's recorded yes
for ``routine:<id>``) before this agent composes and delivers it. The handler
re-checks the yes itself and never goes online.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from puffpai.logging_config import get_logger
from puffpai.security.secrets_provider import AgentScope, SecretsProvider

from .base import BaseAgent
from .envelope import Envelope

logger = get_logger(__name__)

_DATA_DIR = os.environ.get(
    "PUFFPAI_DATA_DIR",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    ),
)
_QUEUE_LOCK = threading.Lock()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class MajordomoAgent(BaseAgent):
    """Background/scheduled work owner + scoped task-worker spawner."""

    name = "majordomo"
    scope = AgentScope.MAJORDOMO

    def __init__(self, *, secrets: SecretsProvider | None = None,
                 n8n_client=None, queue_path: str | None = None) -> None:
        super().__init__(secrets=secrets)
        self._n8n = n8n_client
        self._queue_path = queue_path or os.path.join(_DATA_DIR, "majordomo_tasks.jsonl")

    # ── engine accessor (lazy, guarded) ─────────────────────────────
    def _n8n_client(self):
        if self._n8n is None:
            try:
                from puffpai.n8n_client import N8nClient  # type: ignore[import-not-found]
            except ImportError as exc:  # workflow engine ships in a later phase
                raise RuntimeError(
                    "The workflow engine is not available in this build (it "
                    "ships in a later phase). Inject an n8n_client to exercise "
                    "Majordomo workflow runs."
                ) from exc
            self._n8n = N8nClient()
        return self._n8n

    # ── persistent queue ────────────────────────────────────────────
    def _append_queue(self, record: dict) -> None:
        with _QUEUE_LOCK:
            os.makedirs(os.path.dirname(self._queue_path), exist_ok=True)
            with open(self._queue_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")

    def list_queue(self, limit: int = 100) -> list[dict]:
        """Read recent queued tasks (newest last)."""
        if not os.path.isfile(self._queue_path):
            return []
        out: list[dict] = []
        try:
            with open(self._queue_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return out[-limit:]

    # ── request handling ────────────────────────────────────────────
    def handle(self, envelope: Envelope) -> dict:
        intent = envelope.intent
        p = envelope.payload or {}
        try:
            if intent in ("run_workflow", "n8n"):
                # Reaching n8n is egress-class work; charge it and require the
                # budget to permit egress (Sentinel also veto-checks this).
                envelope.budget.consume(1, egress=True)
                ok, data = self._n8n_client().call(p["webhook_name"], p.get("data", {}))
                return {"ok": ok, "data": data}
            if intent in ("enqueue", "schedule"):
                self._append_queue({
                    "queued_at": _utcnow(),
                    "intent": str(p.get("task_intent", "task")),
                    "target": p.get("target"),
                    "requester": str(envelope.requester),
                    "payload_keys": sorted((p.get("task_payload") or {}).keys()),
                    "status": "queued",
                })
                return {"ok": True, "queued": True}
            if intent == "run_routine":
                # One action: compose + deliver. No egress: the routine reads
                # caches and the library only; the budget forbids going online.
                envelope.budget.consume(1)
                from puffpai import routines
                now = p.get("now_ms")
                when = (datetime.fromtimestamp(int(now) / 1000, tz=timezone.utc)
                        if isinstance(now, (int, float)) and not isinstance(now, bool)
                        else None)
                return routines.execute(str(p["routine_id"]), now=when)
            if intent in ("list_routines", "routines"):
                from puffpai import routines
                return {"ok": True, "routines": routines.list_routines()}
            if intent in ("list_queue", "queue"):
                return {"ok": True, "queue": self.list_queue(int(p.get("limit", 100)))}
            return {"ok": False, "error": f"majordomo: unknown intent {intent!r}"}
        except KeyError as exc:
            return {"ok": False, "error": f"majordomo: missing payload key {exc}"}

    def health(self) -> dict:
        base = super().health()
        base["queue_path"] = self._queue_path
        base["queued_tasks"] = len(self.list_queue(limit=10_000))
        return base


__all__ = ["MajordomoAgent"]
