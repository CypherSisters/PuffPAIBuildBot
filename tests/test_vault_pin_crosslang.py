"""The browser's vault PIN rule is never looser than the backend's.

``dashboard/app/_lib/vaultPin.ts`` decides, in the browser, whether a new PIN
is sent at all (the onboarding wizard's vault step, and the Vault page's
create and "Forgot your PIN?"). The backend's rule for a PIN chosen in setup
or in a recovery is :func:`puffpai.setup_ops.validate_vault_pin`. If the
browser accepted a PIN the backend refuses, the owner would be told it is
fine and then refused a screen later; before this test the browser's list of
common PINs was missing the vault floor's own list (``1234567890``,
``password1``...), so that happened.

This runs the REAL TypeScript file through node (type stripping, no build),
the same way tests/test_recovery_code_crosslang.py runs the recovery-code
normaliser, rather than a Python copy of it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from puffpai import setup_ops

_TS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "dashboard", "app", "_lib", "vaultPin.ts")

_CORPUS = [
    "", "a", "abc12", "abcdefg", "1234567", "583920174", "5839201746",
    "1234567890", "0987654321", "12345678", "87654321", "11111111", "aaaaaaaa",
    "password", "Password", "PASSWORD1", "password1", "passw0rd", "letmein1",
    "welcome1", "changeme", "adminadmin", "trustno1", "qwertyui", "abcd1234",
    "iloveyou", "puffpai", "PuffPAI!", "homebot1", "abcdefgh", "hgfedcba",
    "abcabcabc", "121212121212", "xyxyxyxy", "abababab",
    "correct horse battery staple", "tab\tinside", "café-latte-9",
    "Tulip-9-Harbor", "Maple-4-Lantern", "a-different-strong-pin",
    "x" * 128, "x" * 127 + "y", "Qz" * 64 + "!", "Q" + "z9" * 64,
    "7bS!k2@Lp", "only-letters-and-dashes", "UPPER-lower-123",
]


def _node_refusals(pins: list[str]) -> list[str]:
    url = "file:///" + _TS.replace("\\", "/").lstrip("/")
    script = (
        f"const m = await import({json.dumps(url)});\n"
        "const input = JSON.parse(process.argv[1]);\n"
        "console.log(JSON.stringify(input.map((p) => m.pinRefusal(p))));\n"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(pins)],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
    ).stdout
    return json.loads(out.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browser_never_accepts_what_the_backend_refuses():
    browser = _node_refusals(_CORPUS)
    looser = [p for p, why in zip(_CORPUS, browser)
              if not why and not setup_ops.validate_vault_pin(p)[0]]
    assert looser == [], f"browser accepts PINs the backend refuses: {looser!r}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browser_and_backend_agree_on_the_corpus():
    """Not required by the invariant (the browser may be stricter), but a
    stricter browser refuses PINs the owner is allowed, so say so if it
    drifts that way too."""
    browser = _node_refusals(_CORPUS)
    stricter = [p for p, why in zip(_CORPUS, browser)
                if why and setup_ops.validate_vault_pin(p)[0]]
    assert stricter == []


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_keypad_shapes_and_random_pins_agree_both_ways():
    """Keypad shapes with the
    corner key shared are refused on both sides, a random PIN refused by
    neither (7396304647) stays fine on both, and a sample of random PINs is
    judged the same way by each."""
    import random
    shapes = ["1478963250", "1236987410", "7896321450", "3698741250",
              "1452369870", "1593574862", "9632147850", "7412369850",
              "1478963250x", "a1478963250", "Q1236987410"]
    rng = random.Random(4242)
    sample = ["".join(rng.choice("0123456789") for _ in range(10))
              for _ in range(400)]
    sample += ["".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789")
                       for _ in range(8)) for _ in range(200)]
    pins = shapes + ["7396304647"] + sample
    browser = _node_refusals(pins)
    backend = [not setup_ops.validate_vault_pin(p)[0] for p in pins]
    assert [p for p, why, back in zip(pins, browser, backend)
            if bool(why) != back] == []
    assert all(browser[:len(shapes)]) and all(backend[:len(shapes)])
    assert browser[len(shapes)] == "" and not backend[len(shapes)]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_common_choice_on_either_python_list_is_refused():
    from puffpai import zone_vault
    from puffpai.security import vault
    names = sorted(set(zone_vault._COMMON_PINS) | set(vault._COMMON_SECRETS))
    browser = _node_refusals(names)
    assert [n for n, why in zip(names, browser) if not why] == []
