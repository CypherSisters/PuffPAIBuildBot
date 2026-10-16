"""The consent wording rule, as a word check.

Never "nothing leaves your computer" (or "nothing goes out", "nothing left",
"stays off the internet") without the consent qualifier. The line is
"Nothing leaves your computer without your consent." or "What leaves your
computer: only what you say yes to."

Scanned: every string in ``src/`` (Python: string literals, not comments or
docstrings), the dashboard's TS/TSX (code, strings and page text, not
comments), README.md and docs/, config/*.json, and the installers.
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

BAD = re.compile(
    r"nothing (?:else )?(?:about you )?(?:leaves|goes out|went out)"
    r"|nothing left (?:your|this) (?:computer|house|home)"
    r"|stays off the internet",
    re.IGNORECASE)
QUALIFIED = re.compile(
    r"without your consent|only what you say yes to|until you (?:switch|say|turn)"
    r"|unless you|you said yes|you say yes",
    re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n")


def offences(text: str) -> list[str]:
    """Each sentence of *text* that claims nothing leaves with no qualifier."""
    out = []
    for sentence in _SENTENCE.split(text or ""):
        if BAD.search(sentence) and not QUALIFIED.search(sentence):
            out.append(sentence.strip()[:160])
    return out


# -- Python: string literals, never comments or docstrings ------------------

def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def python_strings(source: str) -> list[str]:
    tree = ast.parse(source)
    skip = _docstring_ids(tree)
    out: list[str] = []
    in_fstring: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                in_fstring.add(id(value))
                parts.append(value.value if isinstance(value, ast.Constant)
                             and isinstance(value.value, str) else "{}")
            out.append("".join(parts))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in skip and id(node) not in in_fstring:
            out.append(node.value)
    return out


# -- TS / TSX: everything but comments ---------------------------------------

def strip_ts_comments(source: str) -> str:
    """The text with // and /* */ comments blanked; strings kept whole."""
    out: list[str] = []
    i, n = 0, len(source)
    quote = ""
    while i < n:
        ch = source[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(source[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if source.startswith("//", i):
            j = source.find("\n", i)
            i = n if j < 0 else j
            continue
        if source.startswith("/*", i):
            j = source.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _json_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _json_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _json_strings(v)]
    return []


def _files(base: Path, suffixes: tuple[str, ...]) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames
                       if d not in ("node_modules", ".next", "__pycache__", "out")]
        for name in filenames:
            if name.endswith(suffixes):
                out.append(Path(dirpath) / name)
    return sorted(out)


def _scan() -> list[str]:
    found: list[str] = []
    for path in _files(ROOT / "src", (".py",)):
        for text in python_strings(path.read_text(encoding="utf-8")):
            found += [f"{path.relative_to(ROOT)}: {s}" for s in offences(text)]
    for sub in ("app", "components"):
        base = ROOT / "dashboard" / sub
        if base.is_dir():
            for path in _files(base, (".ts", ".tsx")):
                text = strip_ts_comments(path.read_text(encoding="utf-8"))
                found += [f"{path.relative_to(ROOT)}: {s}" for s in offences(text)]
    docs = [ROOT / "README.md"] + _files(ROOT / "docs", (".md",))
    for path in docs:
        if path.is_file():
            # A wrapped Markdown line is one sentence: join the lines first.
            text = re.sub(r"(?<!\n)\n(?!\n)", " ", path.read_text(encoding="utf-8"))
            found += [f"{path.relative_to(ROOT)}: {s}" for s in offences(text)]
    for path in sorted((ROOT / "config").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for text in _json_strings(data):
            found += [f"{path.relative_to(ROOT)}: {s}" for s in offences(text)]
    for name in ("install.ps1", "install.sh", "install.cmd"):
        path = ROOT / name
        if path.is_file():
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                     if not ln.lstrip().startswith(("#", "REM ", "::"))]
            found += [f"{name}: {s}" for s in offences("\n".join(lines))]
    return found


def test_no_owner_text_claims_nothing_leaves_without_consent():
    found = _scan()
    assert not found, "Decision 7: add the consent qualifier:\n" + "\n".join(found)


# -- the checker itself catches what it must, and spares what it may --------

@pytest.mark.parametrize("text", [
    "What leaves your computer: nothing. WeddingBot stays off the internet.",
    "Nothing leaves your computer.",
    "Nothing else about you leaves this computer.",
    "Total: 5 MB. Nothing else goes out.",
    "all stopped, as always: nothing left your computer",
    "Nothing leaves this computer, ever.",
])
def test_the_check_catches_each_old_line(text):
    assert offences(text)


@pytest.mark.parametrize("text", [
    "Nothing leaves your computer without your consent.",
    "What leaves your computer: only what you say yes to.",
    "Nothing goes out until you switch it on again here.",
    "That is all this card sends about you.",
])
def test_the_check_spares_the_consent_lines(text):
    assert not offences(text)


def test_python_comments_and_docstrings_are_exempt_strings_are_not():
    src = ('"""Nothing leaves your computer."""\n'
           '# nothing leaves your computer\n'
           'def f(name):\n'
           '    """nothing goes out."""\n'
           '    return f"{name} stays off the internet."\n')
    texts = python_strings(src)
    assert [t for t in texts if offences(t)] == ["{} stays off the internet."]


def test_ts_comments_are_exempt_page_text_is_not():
    src = ('// nothing leaves your computer\n'
           '/* Nothing goes out. */\n'
           'const url = "https://example.org"; // ok\n'
           'export const P = () => <p>Nothing leaves your computer.</p>;\n')
    text = strip_ts_comments(src)
    assert "https://example.org" in text
    found = offences(text)
    assert len(found) == 1 and "<p>Nothing leaves your computer." in found[0]
