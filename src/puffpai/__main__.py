"""Enable ``python -m puffpai <command>``."""

import sys

from puffpai.cli import main

if __name__ == "__main__":
    sys.exit(main())
