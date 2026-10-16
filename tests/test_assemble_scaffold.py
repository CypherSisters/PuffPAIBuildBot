"""The assembled app scaffold: every view type renders from the example specs.

Runs tests/test_assemble_scaffold.js in node (a small DOM, no packages), and
checks the scaffold ships what it says: hls.js vendored with its license, the
world outline, and no outside address in its page.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAFFOLD = os.path.join(ROOT, "src", "puffpai", "assemble", "scaffold")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "assembled_specs")
HLS_SHA256 = "4725cc8f211d2b2c974bc92dac8ce48fe4e00d5193f8830d44f8afdee7857506"


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the views")
def test_every_view_renders_from_the_example_specs():
    proc = subprocess.run(["node", os.path.join(ROOT, "tests", "test_assemble_scaffold.js")],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passed" in proc.stdout


def test_vendored_files_are_pinned_and_licensed():
    vendor = os.path.join(SCAFFOLD, "vendor")
    with open(os.path.join(vendor, "hls-1.6.15.min.js"), "rb") as fh:
        # LF-normalised, so a checkout that turns line ends into CRLF still matches the pin.
        assert hashlib.sha256(fh.read().replace(b"\r\n", b"\n")).hexdigest() == HLS_SHA256
    for name in ("hls.js-LICENSE.txt", "nunito-OFL.txt", "fredoka-OFL.txt", "world.js"):
        assert os.path.getsize(os.path.join(vendor, name)) > 0
    with open(os.path.join(SCAFFOLD, "NOTICE.txt"), encoding="utf-8") as fh:
        notice = fh.read()
    assert HLS_SHA256 in notice and "Apache" in notice


def test_example_specs_use_only_known_view_and_setting_kinds():
    views = {"gallery", "table", "forecast", "map", "player", "chart", "list", "form", "settings", "links"}
    kinds = {"place", "text", "choice", "key"}
    names = sorted(os.listdir(FIXTURES))
    assert len(names) >= 4
    for name in names:
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
            spec = json.load(fh)["spec"]
        assert spec["schema"] == "assembled_app/v1"
        assert {v["type"] for v in spec["views"]} <= views, name
        assert {s["kind"] for s in spec.get("settings", [])} <= kinds, name
