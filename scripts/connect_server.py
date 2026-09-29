"""Compatibility entry point for older Windows server shortcuts."""

import sys

from marianabot.bootstrap import main

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "--connect-server", *sys.argv[1:]]
    main()
