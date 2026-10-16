"""Each app page frames only its own app's address; other pages frame none.

Runs the REAL middleware function (``webAppFrameSrc`` in dashboard/middleware.ts)
in node: its body is plain JavaScript, lifted verbatim, so a change to it is a
change to what this test runs. Also checks that the page CSP line uses it, and
that the cross-site guard now refuses ``Sec-Fetch-Site: same-site`` (an app
address is the same site as a dashboard at ``localhost``).
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
CROSS_SITE = os.path.join(ROOT, "dashboard", "app", "_lib", "server", "crossSite.ts")

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the real function")


def _source() -> str:
    with open(MIDDLEWARE, encoding="utf-8") as fh:
        return fh.read()


def _run_frame_src(cases: list[list[str]]) -> list[str]:
    m = re.search(r"function webAppFrameSrc\(pathname: string, port: string\): string \{(.*?)\n\}",
                  _source(), re.S)
    assert m, "could not lift webAppFrameSrc out of middleware.ts; fix this extractor, do not drop the test"
    script = ("function webAppFrameSrc(pathname, port) {" + m.group(1) + "}\n"
              "const cases = JSON.parse(process.argv[1]);\n"
              "console.log(JSON.stringify(cases.map(([p, port]) => webAppFrameSrc(p, port))));\n")
    proc = subprocess.run(["node", "-e", script, json.dumps(cases)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@requires_node
def test_the_app_page_frames_exactly_its_own_address():
    got = _run_frame_src([["/dashboard/apps/bunnys-big-day-k3j7x2qa", "4271"],
                          ["/dashboard/apps/bunnys-big-day-k3j7x2qa/", "4271"],
                          ["/dashboard/apps/a-22222222", "4381"]])
    assert got == ["http://bunnys-big-day-k3j7x2qa.localhost:4271",
                   "http://bunnys-big-day-k3j7x2qa.localhost:4271/".rstrip("/"),
                   "http://a-22222222.localhost:4381"]


@requires_node
def test_every_other_page_frames_no_app_address():
    pages = ["/dashboard", "/dashboard/apps", "/dashboard/apps/calendar", "/dashboard/apps/trivia/play",
             "/dashboard/apps/bunnys-big-day-k3j7x2qa/settings", "/dashboard/outside",
             "/dashboard/apps/Bunny-k3j7x2qa", "/dashboard/apps/bunny-k3j7x2q", "/dashboard/apps/bunny-k3j7x2qa1",
             "/dashboard/apps/bunny-k3j7x2qa.evil.example", "/dashboard/apps/bunny-k3j7x2qa%2f",
             "/dashboard/apps/bunny-k3j7x2qa\n", "/x/dashboard/apps/bunny-k3j7x2qa", "/", "/pair", ""]
    assert _run_frame_src([[p, "4271"] for p in pages]) == [""] * len(pages)


@requires_node
def test_a_bad_port_gives_no_address():
    got = _run_frame_src([["/dashboard/apps/bunny-k3j7x2qa", p] for p in
                          ("", "80", "70000", "4271 http://evil.example", "4271;", "abc", "1023")])
    assert got == [""] * 7


def test_the_page_policy_uses_the_function_and_nothing_else_names_an_app():
    src = _source()
    assert "const APP_FRAME = webAppFrameSrc(pathname, process.env.PUFFPAI_WEBAPPS_PORT ?? \"4271\");" in src
    # The assembled-app frame (tests/test_assemble_frame_src.py) follows the app address.
    assert ("`frame-src 'self' ${JELLYFIN}${APP_FRAME ? \" \" + APP_FRAME : \"\"}"
            "${ASSEMBLED_FRAME ? \" \" + ASSEMBLED_FRAME : \"\"}; `") in src
    assert "frame-ancestors 'none'" in src
    assert src.count("localhost:4271") == 0 and "*.localhost" not in src


@requires_node
def test_same_site_is_now_refused_by_the_cross_site_guard():
    script = (
        "import { isCrossSiteRequest } from " + json.dumps("file:///" + CROSS_SITE.replace("\\", "/")) + ";\n"
        "const h = (o) => ({ get: (n) => o[n.toLowerCase()] ?? null });\n"
        "const self = 'http://localhost:3003/api/pai/health';\n"
        "console.log(JSON.stringify([\n"
        "  isCrossSiteRequest(h({'sec-fetch-site': 'same-site', origin: 'http://bunny-k3j7x2qa.localhost:4271'}), self),\n"
        "  isCrossSiteRequest(h({'sec-fetch-site': 'same-site'}), self),\n"
        "  isCrossSiteRequest(h({'sec-fetch-site': 'cross-site'}), self),\n"
        "  isCrossSiteRequest(h({'sec-fetch-site': 'same-origin', origin: 'http://localhost:3003'}), self),\n"
        "  isCrossSiteRequest(h({'sec-fetch-site': 'none'}), self),\n"
        "]));\n")
    proc = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                          timeout=60, cwd=os.path.dirname(CROSS_SITE))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == [True, True, True, False, False]


# ---------------------------------------------------------------------------
# the frame hears one message only, and removing an
# app loads its goodbye page (Clear-Site-Data) before leaving
# ---------------------------------------------------------------------------

APP_DIR = os.path.join(ROOT, "dashboard", "app", "dashboard", "apps", "[id]")


def _read(name: str) -> str:
    with open(os.path.join(APP_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def test_the_frame_hears_only_not_saved_from_its_own_window_and_address():
    src = _read("WebAppFrame.tsx")
    m = re.search(r"export function isNotSaved\((.*?)\n\}", src, re.S)
    assert m, "isNotSaved must stay one small function"
    body = m.group(1)
    assert "ev.origin !== origin" in body and "ev.source !== frameWindow" in body
    assert 'd.puffpai === "not-saved"' in body and "Object.keys(d).length === 1" in body
    # the words shown are fixed, never the app's
    assert "setNotSaved(true)" in src and "ev.data" not in src.split("isNotSaved(ev")[1][:200]
    assert "dangerouslySetInnerHTML" not in src and "innerHTML" not in src


def test_removing_an_app_loads_its_goodbye_page_after_the_remove():
    src = _read("AppHeader.tsx")
    assert '"/__puffpai/bye"' in src
    remove = src[src.index("const remove = useCallback"):]
    assert remove.index("checkedOrigin(id") < remove.index("/integrations/${id}/remove") \
        < remove.index("sayGoodbye(origin)")
    assert 'setAttribute("sandbox", "allow-same-origin")' in src
    assert "dangerouslySetInnerHTML" not in src and "innerHTML" not in src


def test_switching_on_is_two_taps_with_the_backends_ticket():
    src = _read("AppHeader.tsx")
    assert "needs_yes" in src and "reply.ticket" in src
    # the ticket is only ever sent back with the owner's "Switch on" / "Give it"
    assert "switchSite(asking.host, true, asking.q.ticket)" in src
    assert "giveFolder(folderQ.ticket)" in src
