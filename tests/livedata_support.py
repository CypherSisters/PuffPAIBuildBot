"""Shared helpers for the live-data tests (feeds, doors, drafts, review).

Not a test module. Every helper works inside the per-test state and data roots
that ``conftest._pin_state_roots`` pins, so nothing touches a real install and
nothing opens a real socket: ``fake_network`` replaces ``requests.request`` and
the address lookup, and records every call it sees.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "livedata"

PNG_1PX = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
           b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc"
           b"\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
JPEG_HEAD = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def candidates() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "candidates.json").read_text("utf-8"))["candidates"]


def goals() -> dict[str, str]:
    return json.loads((FIXTURES / "candidates.json").read_text("utf-8"))["goals"]


def candidate(cid: str) -> dict[str, Any]:
    return next(c for c in candidates() if c["id"] == cid)


def sample_bytes(cand: dict[str, Any]) -> bytes:
    return (FIXTURES / "samples" / cand["sample"]).read_bytes()


def fixture_probe(body: bytes, *, status: int = 200,
                  content_type: str = "application/json"):
    """A stand-in for egress_doors.build_probe_get that returns *body*."""
    calls: list[dict[str, Any]] = []

    def probe(url, host, build_online, max_bytes):
        calls.append({"url": url, "host": host, "online": build_online})
        return {"status": status, "content_type": content_type,
                "bytes": len(body), "truncated": False, "body": body}
    probe.calls = calls  # type: ignore[attr-defined]
    return probe


def set_posture(run_egress: str = "enumerated", *, home: Optional[dict] = None
                ) -> None:
    """Write a build manifest (and optionally a home block) into CONFIG_DIR."""
    from puffpai import build_manifest, build_posture
    from puffpai.config import get_config
    from puffpai.security import egress_doors

    cfg_dir = get_config().CONFIG_DIR
    os.makedirs(cfg_dir, exist_ok=True)
    posture = build_posture.posture_manifest_block(
        build_posture.assess_posture("online", run_egress, []))
    Path(cfg_dir, "build_manifest.json").write_text(
        json.dumps({"schema": build_manifest.SCHEMA, "posture": posture}),
        encoding="utf-8")
    config: dict[str, Any] = {}
    if home is not None:
        config["home"] = home
    Path(cfg_dir, "puffpai_config.json").write_text(json.dumps(config),
                                                    encoding="utf-8")
    egress_doors.reset_cache()


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"",
                 content_type: str = "application/json",
                 headers: Optional[dict] = None):
        self.status_code = status
        self._body = body
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.closed = False
        self.raw = None

    def iter_content(self, chunk_size: int = 16384):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeNetwork:
    """Routes by URL prefix; records every request that reached "the wire"."""

    def __init__(self):
        self.routes: dict[str, FakeResponse] = {}
        self.calls: list[dict[str, Any]] = []
        self.addresses = ["93.184.216.34"]

    def on(self, prefix: str, response: FakeResponse) -> None:
        self.routes[prefix] = response

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url,
                           "headers": dict(kwargs.get("headers") or {}),
                           "allow_redirects": kwargs.get("allow_redirects")})
        for prefix in sorted(self.routes, key=len, reverse=True):
            if url.startswith(prefix):
                return self.routes[prefix]
        return FakeResponse(404, b"{}")

    def resolver(self, host, port):
        return list(self.addresses)


def fake_network(monkeypatch) -> FakeNetwork:
    import requests

    from puffpai.security import egress_doors
    net = FakeNetwork()
    monkeypatch.setattr(egress_doors, "_send", net.request)
    monkeypatch.setattr(egress_doors, "_default_resolver", net.resolver)
    # conftest switches sample looks off for every test; this network is fake.
    monkeypatch.delenv(egress_doors.BUILD_PROBE_ENV, raising=False)
    return net


def no_network(monkeypatch) -> None:
    """Any socket attempt fails the test."""
    import requests

    from puffpai.security import egress_doors

    def boom(*a, **k):
        raise AssertionError("a request reached the network layer")
    monkeypatch.setattr(egress_doors, "_send", boom)
    monkeypatch.setattr(egress_doors, "_default_resolver", boom)


def feed_manifest(app_id: str = "skyshots", *, card: Optional[dict] = None,
                  url: str = "https://api.example.org/v1/latest?x=1",
                  image_host: Optional[str] = "img.example.org",
                  **feed_extra) -> dict[str, Any]:
    service = {"name": "Example Data", "host": "api.example.org"}
    if image_host:
        service["image_host"] = image_host
    feed = {
        "service": service,
        "url": url,
        "refresh_minutes": 30,
        "max_age_minutes": 1440,
        "card": card or {"kind": "value", "title": "Example reading",
                         "value": "reading.level", "label": "Level",
                         "caption": "reading.when"},
        "credit": "Data: Example Data",
        "license": "CC-BY-4.0",
    }
    feed.update(feed_extra)
    return {
        "schema": "puffpai_app/v1", "id": app_id, "name": "Sky Shots",
        "blurb": "A test app.", "surfaces": ["knowledge.library"],
        "ui": {"route": f"/dashboard/apps/{app_id}", "icon": "mdiRss"},
        "feed": feed,
    }


def install(manifest: dict[str, Any]) -> None:
    """Write *manifest* into the owner's apps folder and rescan."""
    from puffpai import app_registry
    folder = Path(app_registry.user_apps_dir(), manifest["id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    app_registry.rescan()


#: Words kept out of anything a builder or owner reads.
#: The list lives in puffpai.plain_words; this is
#: it, for the source-file scan in test_livedata_plain_words.
from puffpai.plain_words import BANNED_WORDS  # noqa: E402

#: On top of the one list, in sentences a person reads: "api" said as a word.
TEXT_EXTRA = (" api ",)


def jargon_in(text: str) -> list[str]:
    """Banned words anywhere in *text*, commands included.

    A command the owner copies is still text the owner reads, so it gets no
    exemption (an earlier version skipped ``puffpai ...`` and let a raw option
    value through).
    """
    from puffpai import plain_words
    return plain_words.check(f" {text or ''} ", also=TEXT_EXTRA)
