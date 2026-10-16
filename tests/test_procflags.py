"""No console window, ever (the owner saw windows flash while BuildBot worked).

* :func:`puffpai.procflags.hidden` adds ``CREATE_NO_WINDOW`` and a hidden
  start-up window, keeps the caller's own flags, and never lets a
  window-making flag through.
* Every program start under ``src/puffpai`` uses it (read from the files,
  never run). The few left out are listed below with the reason.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys

import pytest

from puffpai import procflags

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src",
                   "puffpai")

#: (file under src/puffpai, enclosing function or "*") -> why it is left out.
ALLOWED = {
    ("developer_terminal.py", "*"):
        "the Developer page terminal handles its own window on purpose",
    ("remove_cli.py", "_handoff"):
        "puffpai remove, typed by the owner, opens the removal window on purpose "
        "and says so (W.REMOVE_NEW_WINDOW)",
    ("box/assets/box_run.py", "*"):
        "runs as root inside the Linux box, where there are no windows; it cannot "
        "import puffpai",
    ("build/toolsmith_run.py", "open_in_browser"):
        "opens a working app in the owner's own browser on purpose, once (AGENTS.md "
        "section 4); that window is the point",
    ("programs.py", "_start_window"):
        "starts the owner's own desktop program (or a file's own app) when she asks; "
        "its window is the point (AGENTS.md, Your own programs)",
    ("box/driver.py", "_run_linux"):
        "the Linux side of the box (bubblewrap with a seccomp file), never Windows",
}

STARTS = {"Popen", "run", "call", "check_call", "check_output"}
HELPERS = {"hidden", "_popen_kwargs"}


# ── the helper ───────────────────────────────────────────────────────────────

@pytest.mark.skipif(os.name != "nt", reason="console windows are a Windows thing")
def test_hidden_adds_no_window_keeps_the_callers_flags_and_hides_the_window():
    suspended = 0x00000004
    group = 0x00000200
    got = procflags.hidden(suspended | group)
    assert got["creationflags"] & procflags.CREATE_NO_WINDOW
    assert got["creationflags"] & suspended and got["creationflags"] & group
    info = got["startupinfo"]
    assert isinstance(info, subprocess.STARTUPINFO)
    assert info.dwFlags & procflags.STARTF_USESHOWWINDOW
    assert info.wShowWindow == procflags.SW_HIDE
    assert procflags.hidden()["startupinfo"] is not info        # a fresh one each time


@pytest.mark.skipif(os.name != "nt", reason="console windows are a Windows thing")
def test_window_making_flags_never_get_through():
    for bad in (procflags.CREATE_NEW_CONSOLE, procflags.DETACHED_PROCESS,
                procflags.CREATE_NEW_CONSOLE | procflags.DETACHED_PROCESS | 0x200):
        flags = procflags.hidden(bad)["creationflags"]
        assert flags & procflags.CREATE_NO_WINDOW
        assert not flags & (procflags.CREATE_NEW_CONSOLE | procflags.DETACHED_PROCESS)


@pytest.mark.skipif(os.name != "nt", reason="console windows are a Windows thing")
def test_a_callers_startupinfo_is_kept_and_hidden():
    mine = subprocess.STARTUPINFO()
    mine.dwFlags = 0x100                                      # STARTF_USESTDHANDLES
    got = procflags.hidden(startupinfo=mine)
    assert got["startupinfo"] is mine and mine.dwFlags & 0x100 and mine.wShowWindow == 0


@pytest.mark.skipif(os.name != "nt", reason="console windows are a Windows thing")
def test_a_real_program_starts_hidden():
    done = subprocess.run([sys.executable, "-I", "-c", "print('ok')"], capture_output=True,
                          text=True, timeout=60, **procflags.hidden())
    assert done.returncode == 0 and done.stdout.strip() == "ok"


def test_elsewhere_it_adds_nothing(monkeypatch):
    monkeypatch.setattr(procflags, "_IS_WINDOWS", False)
    assert procflags.hidden(0x10) == {} and procflags.flags(0x10) == 0


def test_run_heres_start_flags_keep_the_group_and_hide_the_window():
    from puffpai.integrate import run_here
    kw = run_here._popen_kwargs()  # noqa: SLF001
    if os.name == "nt":
        assert kw["creationflags"] & procflags.CREATE_NO_WINDOW
        assert kw["creationflags"] & 0x200
        assert kw["startupinfo"].wShowWindow == procflags.SW_HIDE
    else:
        assert kw == {"start_new_session": True}


# ── every program start uses it ──────────────────────────────────────────────

def _helper_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
    return name in HELPERS


def _hidden_names(fn: ast.AST) -> set[str]:
    """Names in *fn* set from the helper, or updated with it."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and _helper_call(node.value):
            out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "update" and isinstance(node.func.value, ast.Name) \
                and node.args and _helper_call(node.args[0]):
            out.add(node.func.value.id)
    return out


def _starts(tree: ast.Module) -> set[str]:
    """Names this module gave to subprocess's start functions (``_run = subprocess.run``)."""
    out = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute) \
                and isinstance(node.value.value, ast.Name) \
                and node.value.value.id == "subprocess" and node.value.attr in STARTS:
            out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return out


def _is_start(call: ast.Call, aliases: set[str]) -> bool:
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        if f.value.id == "subprocess" and f.attr in STARTS:
            return True
        if f.value.id == "os" and f.attr in ("startfile", "system", "popen"):
            return True
        if f.value.id == "asyncio" and f.attr.startswith("create_subprocess"):
            return True
    return isinstance(f, ast.Name) and f.id in aliases


def _offenders() -> list[str]:
    bad = []
    for folder, _dirs, files in os.walk(SRC):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, SRC).replace(os.sep, "/")
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=rel)
            aliases = _starts(tree)

            def visit(node: ast.AST, fn_name: str, hidden: set[str]) -> None:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        visit(child, child.name, hidden | _hidden_names(child))
                        continue
                    if isinstance(child, ast.Call) and _is_start(child, aliases):
                        ok = any(k.arg is None and (_helper_call(k.value) or (
                            isinstance(k.value, ast.Name) and k.value.id in hidden))
                            for k in child.keywords)
                        if not ok and (rel, fn_name) not in ALLOWED \
                                and (rel, "*") not in ALLOWED:
                            bad.append(f"{rel}:{child.lineno} ({fn_name})")
                    visit(child, fn_name, hidden)
            visit(tree, "<module>", set())
    return bad


def test_every_program_start_hides_its_window():
    bad = _offenders()
    assert not bad, ("start these with **procflags.hidden() (keeps your flags, adds "
                     "CREATE_NO_WINDOW and SW_HIDE), or add them to ALLOWED with the "
                     "reason:\n  " + "\n  ".join(bad))


def test_the_scanner_finds_a_start_without_it(tmp_path, monkeypatch):
    pkg = tmp_path / "puffpai"
    pkg.mkdir()
    (pkg / "x.py").write_text(
        "import subprocess\nfrom puffpai import procflags\n_run = subprocess.run\n"
        "def good():\n    subprocess.run(['a'], **procflags.hidden())\n"
        "def good2():\n    kw = {}\n    kw.update(procflags.hidden())\n"
        "    subprocess.Popen(['a'], **kw)\n"
        "def bad():\n    subprocess.Popen(['a'], creationflags=0x10)\n"
        "def bad2():\n    _run(['a'])\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "SRC", str(pkg))
    assert _offenders() == ["x.py:11 (bad)", "x.py:13 (bad2)"]


def test_every_exception_is_still_there():
    """An exception left behind after its code moved is a hole for the next one."""
    for rel, fn in ALLOWED:
        with open(os.path.join(SRC, rel), encoding="utf-8") as fh:
            text = fh.read()
        assert fn == "*" or f"def {fn}(" in text, (rel, fn)
