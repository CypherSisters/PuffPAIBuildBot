"""Work that takes a while (a search, a batch of installs), run on a thread,
with its plain lines readable while it works.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from typing import Any, Callable

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

KEEP = 40
JOB_RE = re.compile(r"^fj_[0-9a-f]{12}$")
_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}


def start(kind: str, work: Callable[[Callable[[str], None]], Any], *,
          wait: bool = False) -> str:
    """Run ``work(say)`` on a thread; returns the job id. ``wait`` runs it here
    (tests, and callers that want the answer)."""
    job_id = "fj_" + secrets.token_hex(6)
    job: dict[str, Any] = {"id": job_id, "kind": kind, "lines": [], "done": False,
                           "result": None, "error": "", "started": time.time()}
    with _LOCK:
        _JOBS[job_id] = job
        for old in sorted(_JOBS, key=lambda k: _JOBS[k]["started"])[:-KEEP]:
            if _JOBS[old]["done"]:
                _JOBS.pop(old, None)

    def say(line: str) -> None:
        if line:
            with _LOCK:
                job["lines"].append(str(line)[:400])

    def run() -> None:
        try:
            result = work(say)
        except Exception as exc:  # noqa: BLE001 - the screen says it plainly
            logger.warning("finding: %s job failed (%s)", kind, type(exc).__name__)
            result = None
            with _LOCK:
                job["error"] = "Something went wrong, so this stopped. Nothing else changed."
        with _LOCK:
            job["result"] = result
            job["done"] = True

    if wait:
        run()
    else:
        threading.Thread(target=run, name=f"finding-{kind}", daemon=True).start()
    return job_id


def status(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(str(job_id or ""))
        if job is None:
            return None
        return {"id": job["id"], "kind": job["kind"], "lines": list(job["lines"]),
                "done": job["done"], "result": job["result"], "error": job["error"]}


__all__ = ["JOB_RE", "start", "status"]
