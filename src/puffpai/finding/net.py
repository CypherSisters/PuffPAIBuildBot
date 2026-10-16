"""The one way the find step asks a website: ``source_fetch.fetch_small``.

``fetch_small`` checks the owner's switch for the service before any
connection and writes one line on "What leaves your computer" for every
request (sent, refused or failed). On top of it this keeps a small cache
(24 hours for search answers, so asking the same words twice sends nothing)
and hands back ``(status, body, headers)``.

Tests replace :func:`_send` with recorded answers; nothing here opens a
connection itself.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SEARCH_TTL_S = 24 * 3600
SEARCH_MAX_BYTES = 2 * 1024 * 1024

#: Per thread: the requests that got no usable answer since :func:`begin`
#: (reason words only), so a search can say "that site did not answer".
_seen = threading.local()


def begin() -> None:
    _seen.failed = []


def failures() -> list[str]:
    return list(getattr(_seen, "failed", []) or [])


def _note(reason: str) -> None:
    lst = getattr(_seen, "failed", None)
    if lst is not None:
        lst.append(reason)


class Answer(dict):
    """``{"status", "body", "headers", "reason", "cached"}``."""

    @property
    def ok(self) -> bool:
        return self.get("status") == 200 and self.get("body") is not None

    def json(self) -> Any:
        import json
        try:
            return json.loads((self.get("body") or b"").decode("utf-8", errors="replace"))
        except ValueError:
            return None


def _send(url: str, *, hosts: Iterable[str], service: str, max_bytes: int,
          accept: Optional[str] = None, key_id: Optional[str] = None) -> dict:
    """One GET through ``source_fetch.fetch_small`` (the switch and the log line).
    *key_id*: a sign-up key from the vault on this request only (sent inside
    the security package; this module never holds it)."""
    from puffpai import source_fetch
    return source_fetch.fetch_small(url, allowed_hosts=set(hosts), max_bytes=max_bytes,
                                    service=service, honour_robots=False,
                                    headers={"accept": accept} if accept else None,
                                    **({"key_id": key_id} if key_id else {}))


def get(url: str, *, hosts: Iterable[str], service: str,
        max_bytes: int = SEARCH_MAX_BYTES, accept: Optional[str] = None,
        cache_s: float = SEARCH_TTL_S, key_id: Optional[str] = None) -> Answer:
    """A GET with a cache. ``cache_s=0`` always asks again. A request with
    *key_id* (her own account) is never cached: what it sees can include what
    only her account may see, so it is never kept on disk."""
    from puffpai.catalogs import searchers as S
    if key_id:
        cache_s = 0
    key = ("finding", service, url, accept or "")
    if cache_s > 0:
        cached = S.cache_read(key)
        if cached is not None and time.time() - float(cached["at"]) < cache_s \
                and cached.get("status") == 200:
            return Answer(status=200, body=cached.get("body"), headers={}, reason="ok",
                          cached=True)
    try:
        got = _send(url, hosts=hosts, service=service, max_bytes=max_bytes, accept=accept,
                    **({"key_id": key_id} if key_id else {}))
    except Exception as exc:  # noqa: BLE001 - a site that fails is "no answer"
        logger.info("finding: %s did not answer (%s)", service, type(exc).__name__)
        _note("no_answer")
        return Answer(status=None, body=None, headers={}, reason="no_answer", cached=False)
    ans = Answer(status=got.get("status"), body=got.get("body"),
                 headers=dict(got.get("headers") or {}), reason=str(got.get("reason") or ""),
                 cached=False)
    if ans.get("status") not in (200, 404):
        _note(ans.get("reason") or "no_answer")
    if cache_s > 0 and ans.ok and not got.get("truncated"):
        S.cache_write(key, body=ans["body"], status=200, etag=None, truncated=False,
                      at=time.time())
    return ans


__all__ = ["Answer", "SEARCH_MAX_BYTES", "SEARCH_TTL_S", "get"]
