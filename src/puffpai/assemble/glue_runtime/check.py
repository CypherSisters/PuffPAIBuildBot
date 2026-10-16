"""Drawbridge's ``glue`` checklist: read connecting code, never run it.

Toolsmith writes small Python files (one function each, returning rows) that
join found parts into an app. Every time a file is written or rewritten,
:func:`glue_rows` reads its syntax tree and Drawbridge turns the rows into
one verdict (``drawbridge.check_addition({"kind": "glue", ...})``). Every
failed row blocks: a glue file that breaks a rule never runs.

The rules, in plain words:

* one function per file (a short description, imports and plain constants
  may sit beside it; no classes, no code that runs on import);
* imports only from ``json``, ``re``, ``datetime``, ``math``,
  ``urllib.parse``, ``puff_glue`` (the helper that asks BuildBot to fetch a
  page) and the packages the app's plan names;
* never the names that run or load code or reach files: ``eval``, ``exec``,
  ``compile``, ``__import__``, ``open``, ``getattr`` and the like;
* never an attribute that starts with ``_`` (that is how ``__subclasses__``,
  ``__globals__`` and ``__builtins__`` tricks are spelled), never one of a
  list of module names reached through another module (``x.os``,
  ``x.sys``, ``x.socket`` ...), and never a module reached through an
  allowed module;
* text formatting never reaches inside a value: ``str.format`` and
  ``format_map`` only on a written-out template whose fields are plain
  names or numbers (no ``{0.x}``, no ``{0[x]}``); ``%`` never on a template
  that is built from pieces; an f-string never reaches an attribute or an
  item of a name that is not the function's own (its arguments and what it
  sets), an UPPERCASE constant or an imported module;
* never a name, an attribute, an argument or a format field whose name has
  ``token``, ``nonce`` or ``secret`` in it.

What this protects against, and what it does not: it stops the usual ways
a small file reaches the system (programs, sockets, files, loaded code) from
the file itself. It is NOT a sandbox. A planned package is outside code too,
and what it does inside is not read here. The run limits in
:mod:`puffpai.assemble.glue_runtime.runner` (its own process in a Job
Object, a 30 second limit, only 127.0.0.1, no file writes outside its own
folder, no keys from the vault) are the second line.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import re
import string
import types
from typing import Any, Iterable

KIND = "glue"
CHECKER_VERSION = "2"

MAX_BYTES = 32 * 1024
MAX_LINES = 400

#: Modules every glue file may import.
ALLOWED_MODULES = frozenset({"json", "re", "datetime", "math", "urllib.parse",
                             "puff_glue"})

#: Modules no glue file may import, whatever a plan says (a package plan can
#: never name one of these either).
DENIED_MODULES = frozenset({
    "os", "sys", "subprocess", "socket", "ctypes", "importlib", "builtins",
    "shutil", "pathlib", "io", "pickle", "marshal", "multiprocessing",
    "threading", "asyncio", "signal", "winreg", "_winapi", "msvcrt", "inspect",
    "gc", "code", "codeop", "runpy", "types", "http", "urllib", "urllib.request",
    "ssl", "select", "selectors", "tempfile", "glob", "zipimport", "pty",
    "platform", "ftplib", "smtplib", "telnetlib", "webbrowser", "posix", "nt",
    "codecs", "mmap", "resource", "fcntl", "atexit", "sysconfig", "site",
})

#: Names that run or load code, reach files or reach the interpreter.
DENIED_NAMES = frozenset({
    "eval", "exec", "compile", "__import__", "open", "input", "breakpoint",
    "globals", "locals", "vars", "getattr", "setattr", "delattr", "hasattr",
    "memoryview", "help", "exit", "quit", "__builtins__", "builtins",
    "__loader__", "__spec__", "__file__", "classmethod", "staticmethod",
    "property", "super",
})

#: Attribute names refused anywhere (module names reached through another
#: module, and the usual ways to a frame or a process).
DENIED_ATTRS = frozenset({
    "os", "sys", "subprocess", "socket", "ctypes", "importlib", "builtins",
    "shutil", "pathlib", "io", "pickle", "marshal", "urllib", "request",
    "http", "ssl", "codecs", "tempfile", "glob", "platform", "threading",
    "multiprocessing", "asyncio", "signal", "winreg", "open", "system",
    "popen", "spawn", "startfile", "modules", "f_globals", "f_locals",
    "f_back", "f_builtins", "gi_frame", "cr_frame", "ag_frame", "tb_frame",
    "func_globals", "load_module", "exec_module", "mro", "gi_code", "cr_code",
    "ag_code", "f_code", "tb_next", "co_consts", "co_names",
})

#: Words no name, attribute, argument or format field may hold: the run's
#: own fetch token and nonce, and anything called a secret.
SECRET_WORDS = ("token", "nonce", "secret")

_FN_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")
_PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,59}\Z")

#: Every code this checklist uses; every one blocks.
BLOCK_CODES = frozenset({
    "glue_syntax", "glue_too_big", "glue_top_level", "glue_one_function",
    "glue_import", "glue_name", "glue_attribute", "glue_module_attribute",
    "glue_class", "glue_shape", "glue_format", "glue_secret_name",
})


def _row(check: str, ok: bool, why: str, code: str) -> dict[str, str]:
    return {"check": check, "result": "pass" if ok else "fail",
            "why": "" if ok else why[:240], "code": code}


def clean_packages(packages: Any) -> frozenset[str]:
    """Import names a plan may add: plain names, never a denied module."""
    out = set()
    for raw in packages or ():
        name = str(raw or "").strip()
        if _PKG_RE.match(name) and name not in DENIED_MODULES \
                and name.split(".")[0] not in DENIED_MODULES:
            out.add(name)
    return frozenset(out)


def _allowed_import(name: str, packages: frozenset[str]) -> bool:
    if name in ALLOWED_MODULES:
        return True
    parts = name.split(".")
    if any(p in DENIED_MODULES or p.startswith("_") for p in parts):
        return False
    return parts[0] in packages


def _literal(node: ast.AST) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return False
    return True


def _secret_name(name: Any) -> bool:
    low = str(name or "").lower()
    return any(w in low for w in SECRET_WORDS)


def _safe_template(text: str, depth: int = 0) -> bool:
    """A format template whose every field is a plain name or number (and
    whose nested format specs are the same). Never ``{0.x}`` or ``{0[x]}``."""
    if depth > 2:
        return False
    try:
        parsed = list(string.Formatter().parse(text))
    except (ValueError, TypeError):
        return False
    for _lit, field, spec, _conv in parsed:
        if field is not None and ("." in field or "[" in field or _secret_name(field)):
            return False
        if spec and not _safe_template(spec, depth + 1):
            return False
    return True


def _builds_text(node: ast.AST) -> bool:
    """A template made at run time from pieces (joined, added, an f-string,
    a call's answer or an item of something)."""
    return isinstance(node, (ast.JoinedStr, ast.Call, ast.Subscript, ast.BinOp))


def _root_name(node: ast.AST) -> str:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _own_names(fn: ast.FunctionDef) -> set[str]:
    """The function's arguments and every name it sets."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def _format_problems(tree: ast.AST, functions: list, bound: set[str]) -> list[str]:
    out: list[str] = []
    own: set[str] = set()
    for fn in functions:
        own |= _own_names(fn)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("format", "format_map"):
            value = node.value
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                out.append(f"{node.attr} on a template that is not written out")
            elif not _safe_template(value.value):
                out.append(f"a {node.attr} template that reaches inside a value")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) \
                and _builds_text(node.left):
            out.append("% on a template that is built from pieces")
        elif isinstance(node, ast.FormattedValue):
            for inner in ast.walk(node.value):
                if isinstance(inner, (ast.Attribute, ast.Subscript)):
                    root = _root_name(inner)
                    if root and root not in own and root not in bound \
                            and not root.isupper():
                        out.append(f"an f-string reaching inside {root}")
    return out


def _secret_names(tree: ast.AST) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.arg):
            name = node.arg
        elif isinstance(node, ast.keyword):
            name = node.arg
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.alias):
            name = node.asname or node.name
        if name and _secret_name(name):
            out.append(name)
    return out


_TRUSTED_FOR_LOOKUP = ("json", "re", "datetime", "math", "urllib.parse")


def _real_module(name: str) -> types.ModuleType | None:
    """The module object for an allowed standard module or ``puff_glue``,
    to see which of its attributes are modules. Never a planned package
    (that would run outside code in BuildBot itself)."""
    if name in _TRUSTED_FOR_LOOKUP:
        return importlib.import_module(name)
    if name == "puff_glue":
        from puffpai.assemble.glue_runtime import puff_glue
        return puff_glue
    return None


def glue_rows_for_source(code: Any, packages: Iterable[str] = ()) -> list[dict[str, str]]:
    """The checklist rows for one glue file's text. Pure; never raises."""
    rows: list[dict[str, str]] = []
    if not isinstance(code, str):
        return [_row("It is a text file", False, "it is not text", "glue_shape")]
    size_ok = len(code.encode("utf-8", "replace")) <= MAX_BYTES \
        and code.count("\n") <= MAX_LINES
    rows.append(_row("It is short", size_ok,
                     f"it is longer than {MAX_LINES} lines or {MAX_BYTES // 1024} KB",
                     "glue_too_big"))
    if not size_ok:
        return rows
    try:
        tree = ast.parse(code, mode="exec")
    except (SyntaxError, ValueError, RecursionError) as exc:
        msg = getattr(exc, "msg", "") or type(exc).__name__
        line = getattr(exc, "lineno", 0) or 0
        rows.append(_row("It is Python that reads cleanly", False,
                         f"line {line}: {msg}", "glue_syntax"))
        return rows
    rows.append(_row("It is Python that reads cleanly", True, "", "glue_syntax"))
    pkgs = clean_packages(packages)

    # top level: docstring, imports, constants, ONE function
    functions, extra = [], []
    for i, node in enumerate(tree.body):
        if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.FunctionDef):
            functions.append(node)
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None \
                and _literal(node.value) and all(
                    isinstance(t, ast.Name) and t.id.isupper()
                    for t in (node.targets if isinstance(node, ast.Assign) else [node.target])):
            continue
        extra.append(getattr(node, "lineno", 0))
    rows.append(_row("Nothing runs when it is loaded", not extra,
                     f"line {extra[0] if extra else 0} runs on load (only a description, "
                     "imports, UPPERCASE constants and one function may sit at the top)",
                     "glue_top_level"))
    one = len(functions) == 1 and _FN_RE.match(functions[0].name) \
        and not functions[0].decorator_list
    rows.append(_row("It is one plain function", bool(one),
                     "a glue file holds exactly one plain function with a lowercase name "
                     f"(found {len(functions)})", "glue_one_function"))

    bad_imports, bad_names, bad_attrs, mod_attrs, classes = [], [], [], [], []
    bound_modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if not _allowed_import(a.name, pkgs):
                    bad_imports.append(a.name)
                    continue
                bound = a.asname or a.name.split(".")[0]
                bound_modules[bound] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level or not module:
                bad_imports.append("a relative import")
                continue
            for a in node.names:
                full = f"{module}.{a.name}"
                if a.name == "*" or not _allowed_import(module, pkgs)                         or a.name.startswith("_") or a.name in DENIED_ATTRS:
                    bad_imports.append(full)
                    continue
                mod = _real_module(module)
                if mod is not None and isinstance(getattr(mod, a.name, None),
                                                  types.ModuleType):
                    mod_attrs.append(full)
        elif isinstance(node, ast.Name) and node.id in DENIED_NAMES:
            bad_names.append(node.id)
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in DENIED_ATTRS:
                bad_attrs.append(node.attr)
        elif isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Global,
                               ast.Nonlocal, ast.AsyncWith, ast.AsyncFor, ast.Await)):
            classes.append(type(node).__name__)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and re.search(r"\{[^{}]*\.\s*_", node.value):
            # "{0.__class__}".format(x) reaches attributes without a dot in code.
            bad_attrs.append("(an attribute named in a format string)")

    # a module reached through an allowed module: ``json.decoder``,
    # ``urllib.parse.sys``; ``urllib`` itself only as ``urllib.parse``.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(node.value, ast.Name) and node.value.id in bound_modules:
            full = bound_modules[node.value.id]
            if full == "urllib":
                if node.attr != "parse":
                    mod_attrs.append(f"urllib.{node.attr}")
                continue
            mod = _real_module(full)
            if mod is not None and isinstance(getattr(mod, node.attr, None),
                                              types.ModuleType):
                mod_attrs.append(f"{node.value.id}.{node.attr}")
        elif isinstance(node.value, ast.Attribute) and node.value.attr == "parse"                 and isinstance(node.value.value, ast.Name)                 and bound_modules.get(node.value.value.id) == "urllib":
            mod = _real_module("urllib.parse")
            if mod is not None and isinstance(getattr(mod, node.attr, None),
                                              types.ModuleType):
                mod_attrs.append(f"urllib.parse.{node.attr}")

    def _first(items: list[str]) -> str:
        return ", ".join(sorted(set(items))[:4])

    formats = _format_problems(tree, functions, set(bound_modules))
    secrets_ = _secret_names(tree)

    rows.append(_row("It imports only what glue may use", not bad_imports,
                     f"it imports {_first(bad_imports)}", "glue_import"))
    rows.append(_row("It uses no name that runs code or opens files", not bad_names,
                     f"it uses {_first(bad_names)}", "glue_name"))
    rows.append(_row("It reaches no hidden part of Python", not bad_attrs,
                     f"it reaches {_first(bad_attrs)}", "glue_attribute"))
    rows.append(_row("It reaches no module through another module", not mod_attrs,
                     f"it reaches {_first(mod_attrs)}", "glue_module_attribute"))
    rows.append(_row("It defines no classes and waits on nothing", not classes,
                     f"it uses {_first(classes)}", "glue_class"))
    rows.append(_row("Its text formatting does not reach inside values", not formats,
                     f"it uses {_first(formats)}", "glue_format"))
    rows.append(_row("It names nothing like a token, a nonce or a secret", not secrets_,
                     f"it names {_first(secrets_)}", "glue_secret_name"))
    return rows


def glue_rows(draft: Any) -> list[dict[str, str]]:
    """Drawbridge's rows for a ``{"kind": "glue", "code", "packages"}`` draft."""
    if not isinstance(draft, dict):
        return [_row("It is a glue record", False, "it is not a record", "glue_shape")]
    return glue_rows_for_source(draft.get("code"), draft.get("packages") or ())


def passes(code: str, packages: Iterable[str] = ()) -> bool:
    """True when every row passes (the same rules, without the log)."""
    return all(r["result"] == "pass" for r in glue_rows_for_source(code, packages))


def function_name(code: str) -> str:
    """The one function's name, or ""."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return ""
    fns = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    return fns[0] if len(fns) == 1 else ""


def code_sha(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8", "replace")).hexdigest()


def connect() -> list[str]:
    """Register this checklist with Drawbridge (``factory_wiring``)."""
    from puffpai import drawbridge
    if KIND in drawbridge.checker_kinds():
        return [f"drawbridge:{KIND}"]
    drawbridge.register_checker(KIND, glue_rows, block_codes=BLOCK_CODES,
                                version=CHECKER_VERSION)
    return [f"drawbridge:{KIND}"]


__all__ = ["ALLOWED_MODULES", "BLOCK_CODES", "CHECKER_VERSION", "DENIED_ATTRS",
           "DENIED_MODULES", "DENIED_NAMES", "KIND", "SECRET_WORDS", "clean_packages", "code_sha",
           "connect", "function_name", "glue_rows", "glue_rows_for_source", "passes"]
