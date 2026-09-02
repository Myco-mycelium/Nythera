#!/usr/bin/env python3
"""Allow running the Nyrqis backend as a Python module.

    python -m nyrqis_backend boot
    python -m nyrqis_backend service serve
    python -m nyrqis_backend container run /bin/sh
"""

import sys
from nyrqis_backend import main

if __name__ == "__main__":
    sys.exit(main())
