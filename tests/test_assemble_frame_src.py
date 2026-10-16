"""A thing's page may frame its assembled app (127.0.0.1) and YouTube's no-cookie
player; no other page lists either, and /mcp-app-host keeps its own policy.

Runs the REAL ``assembledFrameSrc`` body lifted from dashboard/middleware.ts in
node, like tests/test_app_page_frame_src.py does for ``webAppFrameSrc``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIDDLEWARE = os.path.join(ROOT, "dashboard", "middleware.ts")

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the real function")


def _source() -> str:
    with open(MIDDLEWARE, encoding="utf-8") as fh:
        return fh.read()


def _run(paths: list[str]) -> list[str]:
    m = re.search(r"function assembledFrameSrc\(pathname: string\): string \{(.*?)\n\}", _source(), re.S)
    assert m, "could not lift assembledFrameSrc out of middleware.ts; fix this extractor, do not drop the test"
    script = ("function assembledFrameSrc(pathname) {" + m.group(1) + "}\n"
              "const cases = JSON.parse(process.argv[1]);\n"
              "console.log(JSON.stringify(cases.map((p) => assembledFrameSrc(p))));\n")
    proc = subprocess.run(["node", "-e", script, json.dumps(paths)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@requires_node
def test_a_thing_page_frames_its_app_and_the_nocookie_player():
    got = _run(["/dashboard/things/space-pictures", "/dashboard/things/a1_b-2/"])
    assert got == ["http://127.0.0.1:* https://www.youtube-nocookie.com"] * 2


@requires_node
def test_a_job_page_frames_the_same_and_nothing_wider():
    got = _run(["/dashboard/jobs/earth-pictures", "/dashboard/jobs/guest_list/"])
    assert got == ["http://127.0.0.1:* https://www.youtube-nocookie.com"] * 2
    bad = ["/dashboard/jobs", "/dashboard/jobs/x/y", "/dashboard/jobs/Bad", "/dashboard/jobs/x.evil.example",
           "/dashboard/jobsx/a", "/x/dashboard/jobs/a", "/dashboard/jobs/x%2f", "/dashboard/jobs/x\n",
           "/dashboard/jobs/x:1", "/dashboard/jobs/-x", "/dashboard/jobs/" + "a" * 102]
    assert _run(bad) == [""] * len(bad)
    # Exactly what a thing page gets, never more: same hosts, same id shape.
    things = _run(["/dashboard/things/earth-pictures"])
    assert _run(["/dashboard/jobs/earth-pictures"]) == things


def test_the_mcp_app_host_keeps_its_own_strict_policy():
    path = os.path.join(ROOT, "dashboard", "app", "mcp-app-host", "route.ts")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    for part in ("default-src 'none'", "connect-src 'none'", "frame-src 'none'",
                 "child-src 'none'", "object-src 'none'"):
        assert part in src, part


@requires_node
def test_no_other_page_lists_them():
    pages = ["/dashboard", "/dashboard/things", "/dashboard/things/x/y", "/dashboard/things/Bad",
             "/dashboard/things/x.evil.example", "/dashboard/things/x%2f", "/dashboard/things/x\n",
             "/x/dashboard/things/a", "/dashboard/apps/a-22222222", "/mcp-app-host", "/", ""]
    assert _run(pages) == [""] * len(pages)


def test_the_page_policy_uses_it_and_keeps_the_rest():
    src = _source()
    assert "const ASSEMBLED_FRAME = assembledFrameSrc(pathname);" in src
    assert '${ASSEMBLED_FRAME ? " " + ASSEMBLED_FRAME : ""}; ` +' in src
    assert "\"media-src 'self' blob:; \"" in src
    # The MCP App host document is still left alone (it sends its own, stricter policy).
    assert "pathname !== MCP_APP_HOST &&" in src
    assert "\"frame-ancestors 'none'; \"" in src
