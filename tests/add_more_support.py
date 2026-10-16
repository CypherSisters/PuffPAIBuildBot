"""Shared helpers for the add-more tests. Not a test module.

Every helper works inside the per-test state and data roots conftest pins.
``guard_network`` makes ANY outbound attempt (a door request, the library's
own opener, a raw socket) fail the test and records it, so "zero outbound
calls" is checked at every layer a request could take.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from livedata_support import (FIXTURES, FakeResponse, fake_network,
                              set_posture)

AUTH_KEY = "0123456789abcdef" * 4
DEVICE_A = "deviceaaaa000001"
DEVICE_B = "devicebbbb000002"

#: The six test strangers (prove genericity on several, never one).
GOALS = {
    "G1": "space pictures and my smart telescope",
    "G2": "dinosaurs",
    "G3": "car recalls and maintenance",
    "G4": "vegetable garden in Ohio",
    "G5": "knitting and crochet patterns",
    "G6": "bands and albums",
}

#: Three private sentences: nothing may leave the house.
PRIVATE_SENTENCES = (
    "I want you to know that my son Jake is allergic to peanuts and has epilepsy",
    "I want you to have my divorce lawyer Susan Park number",
    "can you also tell me a joke",
)


class Outbound(list):
    """Every outbound attempt seen (should stay empty)."""


def guard_network(monkeypatch) -> Outbound:
    from puffpai import source_fetch
    from puffpai.catalogs import kiwix_opds
    from puffpai.security import egress_doors
    seen = Outbound()

    def refuse(*args, **kwargs):
        seen.append((args[:2], sorted(kwargs)[:4]))
        raise AssertionError("an outbound request was attempted")

    monkeypatch.setattr(egress_doors, "_send", refuse)
    monkeypatch.setattr(egress_doors, "_default_resolver", refuse)
    monkeypatch.setattr(kiwix_opds, "search_live", refuse)
    for name in ("get_small", "_open_chain"):
        if hasattr(source_fetch, name):
            monkeypatch.setattr(source_fetch, name, refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return seen


def built(posture: str = "enumerated", *, name: str = "Rexy",
          goal: str = "") -> None:
    """A finished bot: a build record with its internet setting, a name and,
    with *goal*, what the owner said it is for."""
    set_posture(posture)
    if goal:
        from puffpai.config import get_config
        path = Path(get_config().CONFIG_DIR, "build_manifest.json")
        record = json.loads(path.read_text("utf-8"))
        record["surfaces"] = {"goal": goal}
        path.write_text(json.dumps(record), encoding="utf-8")
    from puffpai import bot_identity
    bot_identity.set_bot_name(name)


def built_offline(name: str = "Rexy") -> None:
    """A bot that was built to stay off the internet."""
    from puffpai import build_manifest, build_posture, bot_identity
    from puffpai.config import get_config
    from puffpai.security import egress_doors
    posture = build_posture.posture_manifest_block(
        build_posture.assess_posture("offline", "island", []))
    cfg_dir = get_config().CONFIG_DIR
    Path(cfg_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg_dir, "build_manifest.json").write_text(
        json.dumps({"schema": build_manifest.SCHEMA, "posture": posture}),
        encoding="utf-8")
    egress_doors.reset_cache()
    bot_identity.set_bot_name(name)


def nhtsa_network(monkeypatch):
    """A fake internet where the recalls website answers with its sample."""
    net = fake_network(monkeypatch)
    body = (FIXTURES / "samples" / "nhtsa.json").read_bytes()
    net.on("https://api.nhtsa.gov/", FakeResponse(200, body))
    return net


def item_numbered(public: dict[str, Any], section: str) -> dict[str, Any]:
    for group in public["groups"]:
        if group["section"] == section:
            for item in group["items"]:
                if item["can_add"]:
                    return item
    raise AssertionError(f"no {section} line in {public}")


def item_by_host(public: dict[str, Any], host: str) -> dict[str, Any]:
    for group in public["groups"]:
        for item in group["items"]:
            if item["from"] == host and item["can_add"]:
                return item
    raise AssertionError(f"no line from {host}")


def owner_text(public: dict[str, Any]) -> str:
    """Every string on a public session an owner reads."""
    parts = [public.get("title", ""), public.get("searched_line", ""),
             public.get("choose_line", ""), public["online"].get("line", "")]
    parts += public.get("notes") or []
    parts += public.get("nothing_found") or []
    for group in public["groups"]:
        parts.append(group["heading"])
        for item in group["items"]:
            parts += [item["label"], item["detail"], item["size_words"]]
    return "\n".join(str(p) for p in parts if p)
