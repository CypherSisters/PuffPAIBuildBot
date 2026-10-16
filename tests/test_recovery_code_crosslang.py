"""The browser's recovery-code normalizer must never be looser than the vault's.

There are three copies of this normalization: `security/vault.py` (the one that
actually derives the key), `zone_vault.py` (the CLI's type-back check), and
`dashboard/app/_lib/recoveryCode.ts` (the browser's). The two Python copies are
bound to each other by an existing test. The TypeScript copy was bound to
nothing, and it drifted: it used ``[^A-Z0-9]`` while Python uses the
Unicode-aware ``str.isalnum()``, so ``"...37E-with-an-accent"`` normalized to a
match in the browser and to a mismatch in the vault.

That failure mode is the expensive direction. The owner is told the code they
carefully wrote down is confirmed, and finds out otherwise months later when it
is the only thing between them and their documents.

**The invariant, and it is one-directional:** the browser may be STRICTER than
the vault, never more permissive. A false rejection costs a retype. A false
confirmation costs the vault.

These tests run the real TypeScript function through node, rather than
re-implementing it here - a re-implementation is a fourth copy and would drift
in its turn.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from puffpai.security.vault import normalize_recovery_code as vault_normalize

_TS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "dashboard", "app", "_lib", "recoveryCode.ts",
)

# Deliberately nasty: hand-written formatting the owner should be forgiven for,
# and lookalike/invisible characters they should not be silently forgiven for.
_CORPUS = [
    "THXXT-2CL37-HM7RT-C6833-VN3N6",
    "thxxt-2cl37-hm7rt-c6833-vn3n6",
    "  THXXT 2CL37 HM7RT C6833 VN3N6  ",
    "THXXT2CL37HM7RTC6833VN3N6",
    "THXXT/2CL37_HM7RT.C6833,VN3N6",
    "THXXT\n2CL37\tHM7RT C6833 VN3N6",
    "THXXT-2CL37-HM7RT-C6833-VN3NÉ",   # accented latin
    "THXXT-2CL37-HM7RT-C6833-VN3NΩ",   # greek omega
    "THXXT-2CL37-HM7RT-C6833-VN3N٥",   # arabic-indic digit
    "THXXT-2CL37-HM7RT-C6833-VN3N6​",  # zero-width space
    "‮THXXT-2CL37-HM7RT-C6833-VN3N6",  # rtl override
    "",
    "   ",
]


def _node_normalize(values: list[str]) -> list[str]:
    """Run the REAL browser function over ``values`` via node.

    The function body is plain JS inside a .ts file, so it is lifted verbatim
    rather than transpiled. If the body ever stops being plain JS this raises
    loudly instead of quietly testing nothing.
    """
    source = open(_TS_PATH, encoding="utf-8").read()
    match = re.search(
        r"export function normalizeRecoveryCode\(text: string\): string \{(.*?)\n\}",
        source, re.S)
    assert match, ("could not lift normalizeRecoveryCode out of recoveryCode.ts - "
                   "if its shape changed, fix this extractor rather than deleting "
                   "the only test binding the browser to the vault")
    body = match.group(1)

    script = (
        "function normalizeRecoveryCode(text) {" + body + "}\n"
        "const input = JSON.parse(process.argv[1]);\n"
        "console.log(JSON.stringify(input.map(normalizeRecoveryCode)));\n"
    )
    proc = subprocess.run(["node", "-e", script, json.dumps(values)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout)


requires_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to run the real browser normalizer")


@requires_node
def test_the_browser_is_never_more_permissive_than_the_vault():
    """The invariant. A browser match that the vault would reject is the bug."""
    shown = vault_normalize("THXXT-2CL37-HM7RT-C6833-VN3N6")
    browser = _node_normalize(_CORPUS)

    dangerous = []
    for typed, browser_form in zip(_CORPUS, browser):
        vault_match = vault_normalize(typed) == shown
        browser_match = browser_form == shown
        if browser_match and not vault_match:
            dangerous.append(typed)

    assert not dangerous, (
        "the browser would confirm codes the vault rejects, which tells an "
        f"owner a bad code is good: {dangerous!r}")


@requires_node
def test_ordinary_handwriting_is_forgiven_by_both():
    """Being stricter is allowed, but not so strict it punishes real people."""
    forgiving = [
        "THXXT-2CL37-HM7RT-C6833-VN3N6",
        "thxxt-2cl37-hm7rt-c6833-vn3n6",
        "  THXXT 2CL37 HM7RT C6833 VN3N6  ",
        "THXXT2CL37HM7RTC6833VN3N6",
    ]
    shown = vault_normalize("THXXT-2CL37-HM7RT-C6833-VN3N6")
    for typed, browser_form in zip(forgiving, _node_normalize(forgiving)):
        assert vault_normalize(typed) == shown, f"vault rejected {typed!r}"
        assert browser_form == shown, f"browser rejected {typed!r}"


_LOOKALIKES = [
    "THXXT-2CL37-HM7RT-C6833-VN3NÉ",   # accented latin
    "THXXT-2CL37-HM7RT-C6833-VN3NΩ",   # greek omega
    "THXXT-2CL37-HM7RT-C6833-VN3N٥",   # arabic-indic digit
]

_INVISIBLES = [
    "THXXT-2CL37-HM7RT-C6833-VN3N6​",  # zero-width space
    "‮THXXT-2CL37-HM7RT-C6833-VN3N6",  # rtl override
    "THXXT-2CL37-HM7RT-C6833-VN3N6﻿",  # BOM
]


@requires_node
def test_visible_lookalikes_are_refused_on_both_sides():
    """A Greek omega is not a letter in our alphabet.

    These change the code's identity, so accepting one would mean accepting a
    different code. Python keeps them (they ARE alphanumeric) and so the
    derivation fails; the browser must reach the same verdict.
    """
    shown = vault_normalize("THXXT-2CL37-HM7RT-C6833-VN3N6")
    for typed, browser_form in zip(_LOOKALIKES, _node_normalize(_LOOKALIKES)):
        assert vault_normalize(typed) != shown, f"vault accepted {typed!r}"
        assert browser_form != shown, f"browser accepted {typed!r}"


@requires_node
def test_invisible_formatting_is_forgiven_on_both_sides():
    """Invisible characters are punctuation, not identity.

    A zero-width space or a BOM riding along on a paste out of a document is
    not a different code - Python drops them because they are not alphanumeric,
    and the browser now does too. Getting this wrong in the strict direction
    would reject a correct code for something the owner cannot see on screen,
    which is its own cruelty.
    """
    shown = vault_normalize("THXXT-2CL37-HM7RT-C6833-VN3N6")
    for typed, browser_form in zip(_INVISIBLES, _node_normalize(_INVISIBLES)):
        assert vault_normalize(typed) == shown, f"vault rejected {typed!r}"
        assert browser_form == shown, f"browser rejected {typed!r}"


def test_the_two_python_copies_still_agree():
    """Cheap belt-and-braces; the browser is the copy that drifted, but the
    pair it is being compared against has to be sound for that to mean much."""
    from puffpai import zone_vault
    for text in _CORPUS:
        assert zone_vault._normalize_recovery_code(text) == vault_normalize(text)
