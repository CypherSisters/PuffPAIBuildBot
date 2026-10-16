#!/usr/bin/env python3
"""Release-build check of the locked box's base (Alpine minirootfs).

BuildBot trusts only the sha256 pinned in ``puffpai.box.driver`` at run time
(Windows has no gpg). Before a release pins or re-pins it, this tool checks,
on the release machine and with files downloaded by hand:

1. the tarball's sha256 equals ``driver.ROOTFS_SHA256``;
2. the key file's fingerprint is the pinned Alpine release key
   (``0482D84022F52DF1C4E7CD43293ACD0907D9495A``);
3. ``gpg --verify`` in a throwaway keyring reports GOODSIG and VALIDSIG for
   exactly that fingerprint.

Usage (gpg by full path; nothing is looked up on PATH):

    python tools/verify_rootfs.py --gpg /usr/bin/gpg \\
        --tarball alpine-minirootfs-3.24.2-x86_64.tar.gz \\
        --signature alpine-minirootfs-3.24.2-x86_64.tar.gz.asc --key ncopa.asc

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile

PINNED_FPR = "0482D84022F52DF1C4E7CD43293ACD0907D9495A"


def pinned_sha256() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "src"))
    from puffpai.box import driver
    return driver.ROOTFS_SHA256


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def status_says_good(status_text: str, fingerprint: str = PINNED_FPR) -> bool:
    """True only when gpg's machine status lines hold GOODSIG and a VALIDSIG
    whose primary-key fingerprint is exactly *fingerprint*, and no BADSIG,
    ERRSIG, EXPKEYSIG or REVKEYSIG."""
    good = valid = False
    for line in status_text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != "[GNUPG:]":
            continue
        tag = parts[1]
        if tag in ("BADSIG", "ERRSIG", "EXPKEYSIG", "REVKEYSIG", "EXPSIG", "NO_PUBKEY"):
            return False
        if tag == "GOODSIG":
            good = True
        if tag == "VALIDSIG":
            fprs = [p.upper() for p in parts[2:]]
            valid = fingerprint in (fprs[:1] + fprs[-1:])
    return good and valid


def verify(gpg: str, tarball: str, signature: str, key: str) -> list[str]:
    """Problems found ([] means the base may be pinned)."""
    problems = []
    if not os.path.isabs(gpg) or not os.path.isfile(gpg):
        return ["give gpg as a full path to the program"]
    if file_sha256(tarball) != pinned_sha256():
        problems.append("the tarball's sha256 is not the pinned one")
    with tempfile.TemporaryDirectory() as home:
        env = {"GNUPGHOME": home, "PATH": os.path.dirname(gpg)}
        imp = subprocess.run([gpg, "--batch", "--import", key], capture_output=True, env=env,
                             timeout=60)
        if imp.returncode != 0:
            return problems + ["the key file could not be imported"]
        fpr = subprocess.run([gpg, "--batch", "--with-colons", "--fingerprint"],
                             capture_output=True, text=True, env=env, timeout=60)
        fprs = [ln.split(":")[9] for ln in fpr.stdout.splitlines() if ln.startswith("fpr:")]
        if PINNED_FPR not in fprs:
            problems.append("the key file is not the pinned Alpine release key")
        ver = subprocess.run([gpg, "--batch", "--status-fd", "1", "--verify", signature, tarball],
                             capture_output=True, text=True, env=env, timeout=60)
        if not status_says_good(ver.stdout):
            problems.append("the signature does not verify with the pinned key")
    return problems


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="verify_rootfs.py")
    ap.add_argument("--gpg", required=True)
    ap.add_argument("--tarball", required=True)
    ap.add_argument("--signature", required=True)
    ap.add_argument("--key", required=True)
    a = ap.parse_args(argv)
    problems = verify(a.gpg, a.tarball, a.signature, a.key)
    for p in problems:
        print("NOT OK:", p)
    if not problems:
        print("OK: sha256 pinned, key pinned, signature good")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
