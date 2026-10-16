"""Drawbridge stops a page that hides encoded code and runs it (NEXT_FIXES item 15).

A project's only index.html held large encrypted blobs and an obfuscated
loader that decrypted them and wrote them into the page. These small,
synthetic fixtures (nothing here runs) cover each loader shape that must be
stopped and each normal shape that must not be: minified bundles, source
maps, inline pictures and fonts as data: URLs, and WebAssembly.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import base64
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import drawbridge  # noqa: E402
from puffpai.build import toolsmith_run as T  # noqa: E402
from puffpai.integrate import apply, classify, fetch, hidden_loader as H, records  # noqa: E402

RID = "piano-sheets-abcdefgh"
PLAN = {"license": "MIT", "mode": "static_web", "size_bytes": 600 * 1024,
        "runtime_hosts": [], "box": False}
REASON = "its page hides encoded code that it unpacks and runs"


def _b64(n: int, seed: int = 1) -> str:
    return base64.b64encode(random.Random(seed).randbytes(n)).decode()


def _hex(n: int, seed: int = 2) -> str:
    return random.Random(seed).randbytes(n).hex()


def _page(body: str, script: str) -> str:
    return (f"<!doctype html><html><head><title>Site</title></head><body>{body}"
            f"<script>{script}</script></body></html>")


# -- loaders that must be stopped -------------------------------------------------

HIDDEN_DIV_WRITE = _page(
    f'<div id="d" style="display:none">{_b64(9000)}</div>',
    "var k='p';var s=atob(document.getElementById('d').textContent);"
    "document.open();document.write(s);document.close();")

XOR_EVAL = _page(
    "", f"var b='{_b64(6000, 3)}';var r=atob(b),o='';"
    "for(var i=0;i<r.length;i++){o+=String.fromCharCode(r.charCodeAt(i)^0x5a);}eval(o);")

CUSTOM_XOR_NO_ATOB = _page(
    "", f"var _0x1a=['{_hex(3000, 4)}'];function _0x2b(s,k){{var o='';"
    "for(var i=0;i<s.length;i+=2){o+=String.fromCharCode(parseInt(s.substr(i,2),16)^k.charCodeAt(i%k.length));}"
    "return o;}document.body.innerHTML=_0x2b(_0x1a[0],'key');")

HEX_FUNCTION = _page(
    "", f"var h='{_hex(4000, 5)}';var t='';for(var i=0;i<h.length;i+=2)"
    "t+=String.fromCharCode(parseInt(h.substr(i,2),16));new Function(t)();")

MANY_BLOBS_INSERT = _page(
    "".join(f'<span hidden data-p="{_b64(300, 10 + i)}"></span>' for i in range(14)),
    "var p=[].map.call(document.querySelectorAll('[data-p]'),function(e){"
    "return decodeURIComponent(escape(atob(e.dataset.p)));}).join('');"
    "document.body.insertAdjacentHTML('beforeend',p);")

HTML_DATA_URL = _page(
    "", f"var u='data:text/html;base64,{_b64(5000, 6)}';"
    "document.write(atob(u.split(',')[1]));")

ESCAPED_SET_TIMEOUT = _page(
    "", "var c='" + "".join(f"\\x{b:02x}" for b in random.Random(7).randbytes(1400)) + "';"
    "setTimeout(unescape(c.replace(/\\\\x/g,'%')),10);")


@pytest.mark.parametrize("text", [HIDDEN_DIV_WRITE, XOR_EVAL, CUSTOM_XOR_NO_ATOB, HEX_FUNCTION,
                                  MANY_BLOBS_INSERT, HTML_DATA_URL, ESCAPED_SET_TIMEOUT],
                         ids=["hidden-div-write", "xor-eval", "custom-xor-innerHTML",
                              "hex-new-Function", "many-blobs-insertAdjacentHTML",
                              "html-data-url-write", "escapes-setTimeout"])
def test_a_page_that_unpacks_hidden_code_and_runs_it_is_found(text):
    assert H.found(text)
    assert H.in_file("index.html", text)


# -- normal shapes that must pass ----------------------------------------------------

def _bundle(extra: str = "", size: int = 300_000) -> str:
    """A vite/webpack-like minified bundle: lots of code that uses atob,
    innerHTML, new Function and eval-free helpers all over, no hidden blob."""
    chunk = ("function a(e,t){return e.innerHTML=t,e}var b=function(e){return atob(e)};"
             "const c=new Function('return this')();export{a as h,b as d};"
             "(()=>{var e={};e.r=t=>{Object.defineProperty(t,'__esModule',{value:!0})}})();")
    return extra + (chunk * (size // len(chunk) + 1))[:size]


PNG_HEAD = base64.b64encode(b"\x89PNG\r\n\x1a\n" + random.Random(8).randbytes(6000)).decode()
WOFF2_HEAD = base64.b64encode(b"wOF2" + random.Random(9).randbytes(8000)).decode()
WASM_HEAD = base64.b64encode(b"\x00asm\x01\x00\x00\x00" + random.Random(11).randbytes(9000)).decode()

NORMAL = {
    "minified-bundle": _bundle(),
    "bundle-with-inline-images-and-fonts": _bundle(
        f'const logo="data:image/png;base64,{PNG_HEAD}";'
        f'const css="@font-face{{src:url(data:font/woff2;base64,{WOFF2_HEAD})}}";'
        "document.querySelector('#x').innerHTML='<img src=\"'+logo+'\">';"),
    "inline-wasm-data-url": _bundle(
        f'var w="data:application/wasm;base64,{_b64(9000, 12)}";'
        "WebAssembly.instantiate(Uint8Array.from(atob(w.split(',')[1]),c=>c.charCodeAt(0)));"
        "el.innerHTML='ready';", size=20_000),
    "raw-wasm-base64": _page(
        "", f'var w="{WASM_HEAD}";WebAssembly.instantiate(Uint8Array.from(atob(w),'
        "c=>c.charCodeAt(0))).then(()=>{document.getElementById('s').innerHTML='ok'});"),
    "page-with-inline-picture": _page(
        f'<img src="data:image/jpeg;base64,{base64.b64encode(b"x" * 3 + random.Random(13).randbytes(7000)).decode()}">',
        "document.getElementById('o').innerHTML=decodeURIComponent('%3Cb%3Ehi%3C%2Fb%3E');"),
    "short-atob-innerHTML": _page("", "el.innerHTML=atob('PGI+aGk8L2I+');eval('1+1');"),
    "big-blob-never-run": _page(
        f'<pre id="key">{_b64(9000, 14)}</pre>', "console.log('a public key, shown only');"),
    "dashes-line": _page("<!-- " + "-" * 5000 + " -->", "el.innerHTML=atob('aGk=');"),
}

SOURCE_MAP = json.dumps({"version": 3, "sources": ["src/a.ts"],
                         "mappings": ";".join(["AAAA,SAASA,CAAC,EAAE"] * 2000),
                         "sourcesContent": ["export const a = 1;\n" * 400]})


@pytest.mark.parametrize("name", sorted(NORMAL))
def test_normal_pages_and_bundles_pass(name):
    assert not H.found(NORMAL[name]), name


def test_a_source_map_and_non_web_files_are_not_read():
    assert not H.found(SOURCE_MAP)
    assert not H.in_file("README.md", HIDDEN_DIV_WRITE)
    assert not H.in_file("assets/index.js.map", HIDDEN_DIV_WRITE)
    assert H.in_file("assets/loader.mjs", XOR_EVAL)


# -- Drawbridge's code_project verdict and Toolsmith's download check ------------------

@pytest.fixture
def _registry(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    sup.fresh_drawbridge(monkeypatch)


def _verdict(root):
    lic = fetch.read_license(str(root))
    mode, scan = classify.classify_tree(str(root))
    rec = records.blank(RID, name="Piano Sheets")
    rec["from"]["url"] = "https://github.com/someone/online-piano-sheets"
    rec["from"]["commit"] = sup.COMMIT
    rec["plan_facts"] = dict(PLAN)
    got = {"url": "https://codeload.github.com/someone/online-piano-sheets/tar.gz/" + sup.COMMIT,
           "commit": sup.COMMIT, "sha256": "ab" * 32, "bytes": 200_000,
           "final_host": "codeload.github.com", "extracted_bytes": 140_000}
    draft = apply.build_draft(rec, got=got, lic=lic, mode=mode, scan=scan,
                              description="Piano sheets in one page.", feature_lines=0,
                              archived=False, refused="")
    return drawbridge.check_addition(draft, by="test")


def test_drawbridge_blocks_the_hidden_loader_with_a_plain_reason(_registry, tmp_path):
    root = sup.static_tree(str(tmp_path / "src"))
    clean = _verdict(root)
    assert "hidden_loader" not in clean["reasons"]
    row = next(r for r in clean["results"] if r["code"] == "hidden_loader")
    assert row["result"] == "pass"
    sup.write(root, "index.html", HIDDEN_DIV_WRITE)
    v = _verdict(root)
    assert v["verdict"] == "block" and "hidden_loader" in v["reasons"], v["reasons"]
    assert drawbridge.line_for(v) == f"Piano Sheets: stopped because {REASON}. It was not added."
    # In dist/ it is still a page: still stopped.
    root2 = sup.static_tree(str(tmp_path / "src2"))
    sup.write(root2, "dist/index.html", XOR_EVAL)
    assert "hidden_loader" in _verdict(root2)["reasons"]


def test_a_normal_built_project_is_not_stopped(_registry, tmp_path):
    root = sup.static_tree(str(tmp_path / "src"))
    sup.write(root, "dist/assets/index-4f2a.js", NORMAL["bundle-with-inline-images-and-fonts"])
    sup.write(root, "dist/assets/index-4f2a.js.map", SOURCE_MAP)
    sup.write(root, "build/static/js/main.js", NORMAL["inline-wasm-data-url"])
    sup.write(root, "public/app.wasm", random.Random(15).randbytes(20_000))
    v = _verdict(root)
    assert "hidden_loader" not in v["reasons"], v["results"]
    assert classify.scan_tree(root)["hidden_loader"] == ""


def test_toolsmith_will_not_run_a_download_with_a_hidden_loader(tmp_path):
    root = sup.static_tree(str(tmp_path / "app"))
    assert T.scan_problem(root) == ""
    sup.write(root, "index.html", HIDDEN_DIV_WRITE)
    assert T.scan_problem(root) == REASON
    assert classify.scan_tree(root)["hidden_loader"] == "index.html"
    assert "—" not in REASON and H.REASON == REASON
