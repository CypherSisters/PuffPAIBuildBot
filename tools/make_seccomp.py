#!/usr/bin/env python3
"""Build the locked box's seccomp filter (``box.bpf``) at release or install time.

The filter's source is ``src/puffpai/box/assets/make_seccomp.py``, the same
file setup copies into the box on Windows (where it is built inside the box
with Alpine's own libseccomp). On a Linux host, run this once with the
python3 seccomp bindings installed and put the result where the box driver
looks (``$XDG_DATA_HOME/puffpai/box/box.bpf``):

    python3 tools/make_seccomp.py ~/.local/share/puffpai/box/box.bpf

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "..", "src", "puffpai", "box", "assets", "make_seccomp.py")


def main(argv: list) -> int:
    if len(argv) != 1:
        print("usage: make_seccomp.py <where to write box.bpf>", file=sys.stderr)
        return 2
    sys.argv = [SOURCE, argv[0]]
    runpy.run_path(SOURCE, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
