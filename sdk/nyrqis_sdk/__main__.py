"""Allow running the Nyrqis SDK as a module.

Usage:
    python -m nyrqis_sdk new my-app
    python -m nyrqis_sdk pkg list
"""

from .cli import main

if __name__ == "__main__":
    main()
